"""Pass-off test analysis: data validation, correction to reference conditions, uncertainty and acceptance.

The correction follows the model-based approach used in performance test codes
(ASME PTC 22 / ISO 2314 style): the engine model predicts performance at the
test conditions and at the reference conditions, and the measured result is
scaled by the ratio (or shifted by the difference, for temperatures). Exhaust
flow cannot be measured directly, so it is derived from a heat balance.

Uncertainty is propagated two ways, following the GUM (JCGM 100) and its Monte
Carlo supplement (JCGM 101). Instrument calibration (systematic) errors are
shared by every test point; random errors are independent per point. Each
instrument's calibration uncertainty comes from its accuracy class or a
calibration certificate (`gtperf.instruments`); readings are first corrected by
in-date certificate errors. The exhaust rake's under-sampling of the
non-uniform exhaust field is found by Monte Carlo over the burner pattern model
(`gtperf.exhaust`), and the correction model's own uncertainty comes from the
build-to-build scatter estimated from production history (`gtperf.scatter`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .cycle import ISO, Ambient
from .exhaust import ExhaustProfileModel, cold_spot, profile_uncertainty
from .instruments import calibration_status, draw
from .maps import NEW_AND_CLEAN, Health

AMBIENT_TAGS = [f"T_amb_{i}" for i in range(1, 4)]
EXHAUST_TAGS = [f"T_exh_{i:02d}" for i in range(1, 13)]
SINGLE_TAGS = ["P_amb", "RH", "dP_inlet", "dP_exhaust", "W_fuel", "LHV", "P_elec"]
QUANTITIES = ["T_amb", "P_amb", "RH", "dP_inlet", "dP_exhaust", "W_fuel", "LHV", "P_elec", "T_exh"]
MODEL_INPUTS = ["T_amb", "P_amb", "RH", "dP_inlet", "dP_exhaust"]
RESULTS = ["power_MW", "heat_rate", "exhaust_temp_C", "exhaust_flow"]
RESULT_LABELS = {
    "power_MW": ("Corrected power", "MW"),
    "heat_rate": ("Corrected heat rate", "kJ/kWh"),
    "exhaust_temp_C": ("Corrected exhaust temperature", "°C"),
    "exhaust_flow": ("Corrected exhaust flow (heat balance)", "kg/s"),
}


# --- data validation -----------------------------------------------------

@dataclass
class ValidationLimits:
    stuck_samples: int = 60  # identical consecutive readings
    spike_sigma: float = 6.0
    ambient_t_disagreement_K: float = 0.5
    exhaust_tc_deviation_K: float = 20.0
    exhaust_tc_drift_K: float = 5.0
    # range of 1-minute averages allowed within a test point
    stability: dict = field(default_factory=lambda: {"P_elec": 1.0, "W_fuel": 1.0, "T_amb": 1.0, "T_exh": 3.0})
    exhaust_model: ExhaustProfileModel = field(default_factory=ExhaustProfileModel)
    load: float = 1.0  # load fraction of the test points, for the burner cold-spot threshold
    u_tc: float = 1.27  # thermocouple calibration standard uncertainty used in the cold-spot threshold


@dataclass
class ValidationReport:
    clean: object  # DataFrame with spikes removed
    excluded: dict  # tag -> reason
    spikes: dict  # tag -> count removed
    stability: list  # (point, quantity, range, limit, ok)
    burner: object = None  # exhaust.ColdSpot from the rake, before any thermocouple was excluded

    @property
    def stable(self):
        return all(ok for *_, ok in self.stability)


def _max_identical_run(x):
    same = np.diff(x) == 0
    best = run = 0
    for s in same:
        run = run + 1 if s else 0
        best = max(best, run)
    return best + 1 if best else 1


def validate(data, limits=None):
    """Screen a test log: remove spikes, flag stuck, disagreeing or drifting sensors, check stability."""
    lim = limits or ValidationLimits()
    df = data.copy()
    excluded, spikes = {}, {}
    tags = [c for c in df.columns if c not in ("time_s", "point")]

    for tag in tags:
        for _, idx in df.groupby("point").groups.items():
            x = df.loc[idx, tag].to_numpy()
            if _max_identical_run(x) >= lim.stuck_samples:
                excluded[tag] = f"stuck (no change for {lim.stuck_samples}+ samples)"
                break
            med = df.loc[idx, tag].rolling(31, center=True, min_periods=5).median().to_numpy()
            resid = x - med
            sigma = 1.4826 * np.nanmedian(np.abs(resid - np.nanmedian(resid)))
            bad = np.abs(resid) > lim.spike_sigma * max(sigma, 1e-12)
            if bad.any():
                df.loc[np.asarray(idx)[bad], tag] = np.nan
                spikes[tag] = spikes.get(tag, 0) + int(bad.sum())

    def group_means(group):
        valid = [t for t in group if t not in excluded]
        return valid, df.groupby("point")[valid].mean()

    valid, means = group_means(AMBIENT_TAGS)
    dev = means.sub(means.median(axis=1), axis=0).abs().max()
    for tag in valid:
        if dev[tag] > lim.ambient_t_disagreement_K:
            excluded[tag] = f"disagrees with redundant sensors by {dev[tag]:.2f} K"

    valid, means = group_means(EXHAUST_TAGS)
    # Burner screen first: a cold spot from a lean burner drags adjacent thermocouples down together and must
    # not be mistaken for (and hidden by excluding) faulty sensors.
    rake = np.array([means[t].mean() if t in valid else np.nan for t in EXHAUST_TAGS])
    burner = cold_spot(rake, lim.exhaust_model, lim.load, lim.u_tc)
    protected = set()
    if burner.flagged:
        rdev = rake - np.nanmedian(rake)
        protected = {EXHAUST_TAGS[i] for i in burner.thermocouples}
        protected |= {EXHAUST_TAGS[i] for i in range(len(EXHAUST_TAGS)) if rdev[i] < -0.5 * burner.threshold}
    dev = means.sub(means.median(axis=1), axis=0).abs().max()
    first = df.groupby("point").head(60).groupby("point")[valid].mean()
    last = df.groupby("point").tail(60).groupby("point")[valid].mean()
    change = last - first
    drift = change.sub(change.median(axis=1), axis=0).abs().max()
    for tag in valid:
        if dev[tag] > lim.exhaust_tc_deviation_K and tag not in protected:
            excluded[tag] = f"reads {dev[tag]:.1f} K from the exhaust median"
        elif drift[tag] > lim.exhaust_tc_drift_K:
            excluded[tag] = f"drifts {drift[tag]:.1f} K relative to other thermocouples"

    clean = df.drop(columns=list(excluded))
    stability = []
    minute = (clean["time_s"] - clean.groupby("point")["time_s"].transform("min")) // 60
    for p, g in clean.groupby("point"):
        series = _group_series(g)
        m = minute[g.index]
        for q, limit in lim.stability.items():
            avg = series[q].groupby(m).mean()
            rng = avg.max() - avg.min()
            if q in ("P_elec", "W_fuel"):
                rng = 100 * rng / avg.mean()
            stability.append((int(p), q, float(rng), limit, bool(rng <= limit)))
    return ValidationReport(clean, excluded, spikes, stability, burner)


def apply_corrections(data, instruments, test_date=None):
    """Correct each channel by its in-date calibration certificate errors. Returns a copy and the corrections."""
    df = data.copy()
    applied = {}
    for tag, inst in instruments.items():
        if tag in df and hasattr(inst, "correction"):
            e = inst.correction(test_date)
            if e:
                df[tag] = inst.corrected(df[tag], test_date)
                applied[tag] = e
    return df, applied


def _group_series(g):
    out = {t: g[t] for t in SINGLE_TAGS if t in g}
    out["T_amb"] = g[[t for t in AMBIENT_TAGS if t in g]].mean(axis=1)
    out["T_exh"] = g[[t for t in EXHAUST_TAGS if t in g]].mean(axis=1)
    return out


# --- averaging -------------------------------------------------------------

@dataclass
class PointMeasurement:
    point: int
    values: dict  # quantity -> mean
    u_random: dict  # quantity -> standard uncertainty of the mean
    n_samples: int


def _sem(x):
    """Standard error of the mean with an effective sample size for lag-1 autocorrelation."""
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 3:
        return float("nan")
    d = x - x.mean()
    rho = float(np.dot(d[:-1], d[1:]) / np.dot(d, d)) if np.dot(d, d) > 0 else 0.0
    rho = min(max(rho, 0.0), 0.99)
    n_eff = n * (1 - rho) / (1 + rho)
    return float(x.std(ddof=1) / math.sqrt(max(n_eff, 1.0)))


def average(report):
    points = []
    for p, g in report.clean.groupby("point"):
        series = _group_series(g)
        values = {q: float(np.nanmean(series[q])) for q in QUANTITIES}
        u = {q: _sem(series[q].to_numpy(dtype=float)) for q in QUANTITIES}
        points.append(PointMeasurement(int(p), values, u, len(g)))
    return points


def _instrument_components(inst, reading, test_date, label, scale=1.0):
    """(label, standard uncertainty, distribution) of one instrument's calibration components, in reading units."""
    if not getattr(inst, "components", ()):
        u = inst.bias * abs(reading) if inst.relative else inst.bias
        return [(label, u * scale, "normal")]
    out = []
    for comp, u, dist in inst.component_u(reading, test_date):
        u_abs = u * abs(reading) if inst.relative else u
        out.append((f"{label}: {comp.name}", u_abs * scale, dist))
    return out


def systematic_uncertainties(report, points, instruments, test_date=None, exhaust_model=None, load=1.0,
                             profile_trials=4000):
    """Calibration uncertainty of each averaged quantity (shared by all points).

    Returns (u, detail, components): `u` maps quantity -> combined standard
    uncertainty; `components` maps quantity -> [(label, u, distribution)] for the
    Monte Carlo; `detail` holds the exhaust temperature terms and the rake profile
    Monte Carlo (`profile`). The rake mean of k valid thermocouples carries each
    thermocouple's calibration error / k, plus the profile under-sampling error,
    whose expected value is returned as `detail["T_exh profile correction"]` (to be
    subtracted from the rake mean).
    """
    cols = report.clean.columns
    mean = {q: np.mean([p.values[q] for p in points]) for q in QUANTITIES}
    comps = {}
    for q in SINGLE_TAGS:
        comps[q] = _instrument_components(instruments[q], mean[q], test_date, q)
    for q, tags in (("T_amb", AMBIENT_TAGS), ("T_exh", EXHAUST_TAGS)):
        valid = [t for t in tags if t in cols]
        comps[q] = []
        for t in valid:
            comps[q] += _instrument_components(instruments[t], mean[q], test_date, t, 1 / len(valid))
    u_cal = math.sqrt(sum(c[1] ** 2 for c in comps["T_exh"]))
    mask = np.array([t in cols for t in EXHAUST_TAGS])
    profile = profile_uncertainty(exhaust_model or ExhaustProfileModel(), load, mask, n=profile_trials)
    comps["T_exh"].append(("T_exh: rake profile sampling (Monte Carlo)", profile.u, "normal"))
    u = {q: math.sqrt(sum(c[1] ** 2 for c in comps[q])) for q in QUANTITIES}
    detail = {"T_exh calibration": u_cal, "T_exh profile sampling": profile.u,
              "T_exh profile correction": profile.correction, "profile": profile}
    return u, detail, comps


# --- correction to reference conditions -------------------------------------

@dataclass
class Guarantee:
    power_MW_min: float | None = None
    heat_rate_max: float | None = None
    exhaust_temp_C_max: float | None = None


@dataclass
class Reference:
    ambient: Ambient = ISO
    inlet_loss_mbar: float = 0.0
    exhaust_loss_mbar: float = 0.0


@dataclass
class ProductionScatter:
    """Build-to-build standard deviations (k=1) of component health, as fractions.

    The correction curves come from the nominal engine, but each production engine
    differs slightly, so its true response to ambient conditions differs too. This
    is the main source of correction-method uncertainty, and it grows with the
    distance between test and reference conditions.

    The defaults are placeholder assumptions, used only when no production history
    is available. `gtperf.scatter.production_scatter` estimates these values (with
    confidence intervals) from a production pass-off history; `source` records
    which was used.
    """

    compressor_flow: float = 0.005
    compressor_eff: float = 0.004
    turbine_eff: float = 0.003
    turbine_capacity: float = 0.0
    source: str = "assumed (no production history)"

    def perturbations(self):
        out = [
            ("Correction model: compressor flow scatter", Health(compressor_flow_scale=1 + self.compressor_flow)),
            ("Correction model: compressor efficiency scatter", Health(compressor_eff_delta=self.compressor_eff)),
            ("Correction model: turbine efficiency scatter", Health(turbine_eff_delta=self.turbine_eff)),
            ("Correction model: turbine capacity scatter", Health(turbine_capacity_scale=1 + self.turbine_capacity)),
        ]
        return [(label, h) for label, h in out if h != NEW_AND_CLEAN]


class PassOffAnalysis:
    """Corrects measured point averages to reference conditions with an engine model."""

    def __init__(self, engine, reference=None, health=NEW_AND_CLEAN):
        self.engine = engine
        self.health = health
        self.reference = reference or Reference()
        r = self.reference
        self.ref = self._model_from_result(
            engine.run(r.ambient, inlet_loss=r.inlet_loss_mbar * 100, exhaust_loss=r.exhaust_loss_mbar * 100,
                       health=health)
        )
        self._cache = {}
        self._guess = None

    @staticmethod
    def _model_from_result(r):
        q_fuel = r.heat_input / 1e3  # kW
        p = r.electrical_power / 1e3
        gas = r.exhaust.gas
        dh = (gas.h(r.exhaust.t) - gas.h(r.stations["0"].t)) / 1e3
        loss_frac = (q_fuel - p - r.exhaust.w * dh) / p  # closes the model's own heat balance
        return {
            "power_MW": r.electrical_power / 1e6,
            "heat_rate": r.heat_rate,
            "exhaust_temp_C": r.exhaust.t - 273.15,
            "exhaust_flow": r.exhaust.w,
            "loss_frac": loss_frac,
            "gas": gas,
            "control": r.info.get("control", "temperature"),
        }

    def model_at(self, x):
        key = tuple(round(x[q], 12) for q in MODEL_INPUTS)
        if key not in self._cache:
            amb = Ambient(x["T_amb"] + 273.15, x["P_amb"] * 100, x["RH"] / 100)
            r = self.engine.run(amb, inlet_loss=x["dP_inlet"] * 100, exhaust_loss=x["dP_exhaust"] * 100,
                                health=self.health, guess=self._guess)
            self._guess = r.info.get("solution")
            self._cache[key] = self._model_from_result(r)
        return self._cache[key]

    def correct_point(self, x, model=None):
        m = model or self.model_at(x)
        ref = self.ref
        p_meas = x["P_elec"] / 1e3
        q_fuel = x["W_fuel"] * x["LHV"] * 1e3  # kW
        hr_meas = 3600 * q_fuel / x["P_elec"]
        gas = m["gas"]
        dh = (gas.h(x["T_exh"] + 273.15) - gas.h(x["T_amb"] + 273.15)) / 1e3
        flow_meas = (q_fuel - x["P_elec"] * (1 + m["loss_frac"])) / dh
        return {
            "power_MW": p_meas * ref["power_MW"] / m["power_MW"],
            "heat_rate": hr_meas * ref["heat_rate"] / m["heat_rate"],
            "exhaust_temp_C": x["T_exh"] + ref["exhaust_temp_C"] - m["exhaust_temp_C"],
            "exhaust_flow": flow_meas * ref["exhaust_flow"] / m["exhaust_flow"],
            "measured_power_MW": p_meas,
            "measured_heat_rate": hr_meas,
            "power_vs_model": p_meas / m["power_MW"],
            "heat_rate_vs_model": hr_meas / m["heat_rate"],
            "power_correction": ref["power_MW"] / m["power_MW"],
            "heat_rate_correction": ref["heat_rate"] / m["heat_rate"],
        }

    def correct(self, points_values, linear=None):
        """Average of per-point corrected results. `linear` optionally supplies linearised model outputs."""
        res = []
        for k, x in enumerate(points_values):
            model = linear(k, x) if linear else None
            res.append(self.correct_point(x, model))
        return {key: float(np.mean([r[key] for r in res])) for key in res[0]}, res


# --- uncertainty ------------------------------------------------------------

@dataclass
class UncertaintyResult:
    value: dict
    u: dict  # combined standard uncertainty per result
    budget: dict  # result -> list of (input, u_input, sensitivity, contribution, share)
    mc: dict | None = None  # result -> samples
    model_terms: list = field(default_factory=list)  # (label, result shifts for 1 sigma)

    def expanded(self, key, k=2.0):
        return k * self.u[key]


def _inputs(points, u_sys):
    """(label, kind, quantity, point index or None, standard uncertainty)."""
    out = [(f"{q} (systematic)", "sys", q, None, u_sys[q]) for q in QUANTITIES if u_sys[q] > 0]
    for k, p in enumerate(points):
        out += [(f"{q} (random, point {p.point})", "rand", q, k, p.u_random[q])
                for q in QUANTITIES if p.u_random[q] > 0]
    return out


def _model_scatter(analysis, base, scatter):
    """Change in each averaged result for a one-standard-deviation health shift of the correction model."""
    value, _ = analysis.correct(base)
    out = []
    for label, h in scatter.perturbations():
        shifted = PassOffAnalysis(analysis.engine, analysis.reference, health=h)
        v, _ = shifted.correct(base)
        out.append((label, {r: v[r] - value[r] for r in RESULTS}))
    return out


def gum(analysis, points, u_sys, scatter=None):
    """First-order GUM propagation with numerical sensitivity coefficients through the full correction.

    With `scatter`, the correction model's own uncertainty from build-to-build
    variation is included (sensitivity = 1, uncertainty = the result shift).
    """
    base = [dict(p.values) for p in points]
    value, _ = analysis.correct(base)
    inputs = _inputs(points, u_sys)
    model_terms = _model_scatter(analysis, base, scatter) if scatter else []
    sens = {}
    for label, kind, q, k, u in inputs:
        hi, lo = [dict(x) for x in base], [dict(x) for x in base]
        for j in range(len(base)):
            if kind == "sys" or j == k:
                hi[j][q] += u
                lo[j][q] -= u
        v_hi, _ = analysis.correct(hi)
        v_lo, _ = analysis.correct(lo)
        sens[label] = {r: (v_hi[r] - v_lo[r]) / (2 * u) for r in RESULTS}
    budget, u_c = {}, {}
    for r in RESULTS:
        rows = [(label, u, sens[label][r], abs(sens[label][r] * u)) for label, _, _, _, u in inputs]
        rows += [(label, abs(d[r]), 1.0, abs(d[r])) for label, d in model_terms]
        var = sum(c * c for *_, c in rows)
        u_c[r] = math.sqrt(var)
        budget[r] = sorted([(lab, u, s, c, c * c / var if var else 0.0) for lab, u, s, c in rows],
                           key=lambda row: -row[3])
    return UncertaintyResult(value, u_c, budget, model_terms=model_terms)


def _linearised_model(analysis, points, step=None):
    """Model outputs at each point as a first-order expansion in the model inputs."""
    step = step or {"T_amb": 0.1, "P_amb": 0.5, "RH": 1.0, "dP_inlet": 0.2, "dP_exhaust": 0.2}
    keys = ["power_MW", "heat_rate", "exhaust_temp_C", "exhaust_flow", "loss_frac"]
    lin = []
    for p in points:
        x0 = dict(p.values)
        m0 = analysis.model_at(x0)
        jac = {}
        for q in MODEL_INPUTS:
            hi, lo = dict(x0), dict(x0)
            hi[q] += step[q]
            lo[q] -= step[q]
            mh, ml = analysis.model_at(hi), analysis.model_at(lo)
            jac[q] = {kk: (mh[kk] - ml[kk]) / (2 * step[q]) for kk in keys}
        lin.append((x0, m0, jac))

    def model(k, x):
        x0, m0, jac = lin[k]
        out = dict(m0)
        for kk in keys:
            out[kk] = m0[kk] + sum(jac[q][kk] * (x[q] - x0[q]) for q in MODEL_INPUTS)
        return out

    return model


def monte_carlo(analysis, points, u_sys, trials=5000, seed=1, full_model=False, model_terms=(), components=None):
    """JCGM 101 Monte Carlo. Systematic errors are drawn once per trial and shared by all points.

    With `components` (from `systematic_uncertainties`), each instrument's
    calibration error is drawn from its own distribution (rectangular for class
    tolerances, normal for certificates) and summed per quantity; otherwise each
    quantity's systematic error is normal with standard deviation `u_sys`.

    By default the engine model is linearised around each test point (its response to
    sub-kelvin perturbations is smooth), while the measurement equations stay fully
    nonlinear. `full_model=True` re-runs the engine model for every trial.
    Correction-model scatter terms (from `gum`) are added linearly.
    """
    rng = np.random.default_rng(seed)
    linear = None if full_model else _linearised_model(analysis, points)
    samples = {r: np.empty(trials) for r in RESULTS}
    if components:
        e_all = {q: sum(draw(rng, dist, u, trials) for _, u, dist in components[q]) if components[q]
                 else np.zeros(trials) for q in QUANTITIES}
    else:
        e_all = {q: rng.normal(0, u_sys[q], trials) for q in QUANTITIES}
    for t in range(trials):
        e_sys = {q: e_all[q][t] for q in QUANTITIES}
        xs = []
        for p in points:
            xs.append({q: p.values[q] - e_sys[q] - rng.normal(0, p.u_random[q]) for q in QUANTITIES})
        v, _ = analysis.correct(xs, linear)
        z = rng.normal(0, 1, len(model_terms))
        for r in RESULTS:
            samples[r][t] = v[r] + sum(zi * d[r] for zi, (_, d) in zip(z, model_terms))
    return samples


# --- acceptance --------------------------------------------------------------

def conformity(unc, guarantee, k=2.0):
    """Compare corrected results with guarantee limits.

    PASS: the result meets the limit even after subtracting the expanded uncertainty.
    PASS (within uncertainty): the result meets the limit but the margin is smaller than U95.
    FAIL (within uncertainty) and FAIL mirror these on the other side.
    Probability of conformance is from the Monte Carlo samples when available, else normal.
    """
    checks = []
    spec = [("power_MW", guarantee.power_MW_min, +1), ("heat_rate", guarantee.heat_rate_max, -1),
            ("exhaust_temp_C", guarantee.exhaust_temp_C_max, -1)]
    for key, limit, sign in spec:
        if limit is None:
            continue
        value, u = unc.value[key], unc.u[key]
        margin = sign * (value - limit)
        U = k * u
        if unc.mc is not None:
            prob = float(np.mean(sign * (unc.mc[key] - limit) >= 0))
        else:
            prob = 0.5 * (1 + math.erf(margin / (u * math.sqrt(2))))
        if margin >= U:
            verdict = "PASS"
        elif margin >= 0:
            verdict = "PASS (within uncertainty)"
        elif margin > -U:
            verdict = "FAIL (within uncertainty)"
        else:
            verdict = "FAIL"
        checks.append({"result": key, "value": value, "limit": limit, "margin": margin, "U95": U,
                       "probability": prob, "verdict": verdict, "direction": ">=" if sign > 0 else "<="})
    return checks


@dataclass
class PassOffResult:
    report: ValidationReport
    points: list
    per_point: list
    u_sys: dict
    u_sys_detail: dict
    uncertainty: UncertaintyResult
    checks: list
    analysis: PassOffAnalysis
    warnings: list = field(default_factory=list)
    notes: list = field(default_factory=list)  # findings that do not invalidate the test
    instruments: dict = field(default_factory=dict)
    test_date: object = None
    calibration: list = field(default_factory=list)  # instruments.calibration_status rows
    corrections: dict = field(default_factory=dict)  # tag -> certificate correction applied
    scatter: ProductionScatter | None = None
    sys_components: dict = field(default_factory=dict)

    @property
    def valid(self):
        return not self.warnings


RESULT_TAGS = set(SINGLE_TAGS) | set(AMBIENT_TAGS) | set(EXHAUST_TAGS)


def analyse(data, engine, instruments, guarantee=None, reference=None, limits=None, mc_trials=5000, seed=1,
            scatter=None, speed_limit_band=0.3, test_date=None, exhaust_model=None):
    """Full pass-off chain: correct by certificates, validate, average, correct, propagate uncertainty, check.

    `test_date` (datetime.date) is checked against each instrument's calibration
    certificates: an out-of-date certificate is not used (class tolerance, no
    correction) and is flagged. For a channel that feeds the corrected results
    this makes the test invalid; for a diagnostic channel it is a note.
    `scatter` should come from `gtperf.scatter.production_scatter`; without it the
    placeholder ProductionScatter() is used and the report says so.
    `speed_limit_band` (% speed): a point whose measured gas generator speed is this
    close to the limit is treated as speed-controlled.
    """
    scatter = ProductionScatter() if scatter is None else scatter
    exhaust_model = exhaust_model or (limits.exhaust_model if limits else ExhaustProfileModel())
    corrected, applied = apply_corrections(data, instruments, test_date)
    report = validate(corrected, limits)
    points = average(report)
    analysis = PassOffAnalysis(engine, reference)
    load = float(np.mean([p.values["P_elec"] for p in points])) / 1e3 / analysis.ref["power_MW"]
    u_sys, detail, comps = systematic_uncertainties(report, points, instruments, test_date, exhaust_model, load)
    for p in points:  # expected rake-versus-mass-weighted-mean offset from the profile model
        p.values["T_exh"] -= detail["T_exh profile correction"]
    unc = gum(analysis, points, u_sys, scatter)
    if mc_trials:
        unc.mc = monte_carlo(analysis, points, u_sys, trials=mc_trials, seed=seed, model_terms=unc.model_terms,
                             components=comps)
    _, per_point = analysis.correct([p.values for p in points])
    checks = conformity(unc, guarantee or Guarantee())
    warnings, notes = [], []
    max_speed = getattr(engine, "max_gg_speed", None)
    clean = report.clean
    for p in points:
        mode = analysis.model_at(p.values)["control"]
        if max_speed and "N_gg" in clean:
            # the measured gas generator speed shows the control mode the real engine was in
            n_gg = clean.loc[clean["point"] == p.point, "N_gg"].mean()
            if n_gg >= 100 * max_speed - speed_limit_band:
                mode = "gas generator speed"
        if mode != analysis.ref["control"]:
            warnings.append(f"Point {p.point} runs on the {mode} limit but the reference condition is on the "
                            f"{analysis.ref['control']} limit. Correction across control modes is not valid; "
                            "retest at conditions in the same control mode.")
    if not report.stable:
        warnings.append("One or more test points did not meet the stability criteria.")
    calibration = calibration_status(instruments, test_date)
    if test_date is None and calibration:
        notes.append("No test date given: calibration certificates were used without checking their due dates.")
    for row in calibration:
        if row["valid"]:
            continue
        msg = (f"{row['tag']} ({row['component']}): certificate {row['certificate']} is {row['status']} "
               f"(calibrated {row['date']}, due {row['due']}, test {test_date}). The class tolerance was used "
               "instead and no certificate correction was applied.")
        (warnings if row["tag"] in RESULT_TAGS else notes).append(
            msg + ("" if row["tag"] in RESULT_TAGS else " Diagnostic channel only; results unaffected."))
    if report.burner is not None and report.burner.flagged:
        b = report.burner
        warnings.append(f"Exhaust cold spot {b.depth:.0f} K below the rake median at about {b.angle_deg:.0f}° "
                        f"(threshold {b.threshold:.0f} K), consistent with a lean or blocked burner "
                        f"{b.burner}. Inspect the combustor; the exhaust profile model does not cover a "
                        "faulty burner, so the exhaust temperature uncertainty is not valid.")
    if scatter.source.startswith("assumed"):
        notes.append("Correction-model scatter is the placeholder assumption, not estimated from production data.")
    return PassOffResult(report, points, per_point, u_sys, detail, unc, checks, analysis, warnings, notes,
                         instruments, test_date, calibration, applied, scatter, comps)
