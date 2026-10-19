"""Synthetic fleet data and fleet performance monitoring.

Two datasets, matching the two places performance engineers watch engines:

* a production sequence: one corrected pass-off result per engine built, for SPC;
* an in-service fleet: base-load snapshots from engines on site over two years,
  with compressor fouling, offline washes, gradual wear, and injected engine and
  instrument faults.

The monitoring pipeline compares each snapshot with the nominal model at the same
ambient conditions (a baseline lookup), detects abrupt changes against each
engine's own trend, and uses gas-path analysis to say what changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.interpolate import RegularGridInterpolator

from .cycle import ISO, Ambient
from .gpa import HEALTH, MEASUREMENTS, GasPathAnalysis, deltas, health_from_vector, measurement_vector

PASSOFF_NOISE = np.array([0.19, 0.25, 0.9, 0.3, 1.0, 0.05])  # %, %, K, %, K, % (from the phase 3 budget)
FIELD_NOISE = np.array([0.35, 0.45, 2.0, 0.35, 1.5, 0.08])
BUILD_SCATTER = np.array([0.3, 0.15, 0.12, 0.3])  # health std: %, pts, pts, %


# --- production sequence ------------------------------------------------------

@dataclass
class ProductionEvent:
    first: int
    last: int | None
    description: str
    health_shift: np.ndarray = field(default_factory=lambda: np.zeros(4))
    measurement_bias: np.ndarray = field(default_factory=lambda: np.zeros(6))


def default_production_events():
    return [
        ProductionEvent(45, 45, "Engine damaged during build (turbine efficiency -1.0 pts)",
                        health_shift=np.array([0, 0, -1.0, 0])),
        ProductionEvent(80, 85, "Test-bed power metering error (+0.6 %)",
                        measurement_bias=np.array([0.6, 0, 0, 0, 0, 0])),
        ProductionEvent(120, None, "Supplier change: compressor efficiency -0.3 pts",
                        health_shift=np.array([0, -0.3, 0, 0])),
    ]


def production_sequence(engine, n=200, events=None, seed=7):
    """Corrected (ISO) pass-off measurements for n engines in build order.

    Returns a DataFrame with the gas-path measurement deltas against nominal, plus
    corrected power, heat rate and exhaust temperature, and the true health.
    """
    rng = np.random.default_rng(seed)
    events = default_production_events() if events is None else events
    nominal = engine.run(ISO, inlet_loss=0.0, exhaust_loss=0.0)
    z0 = measurement_vector(nominal)
    rows = []
    guess = nominal.info["solution"]
    for i in range(1, n + 1):
        x = rng.normal(0, BUILD_SCATTER)
        bias = np.zeros(6)
        for ev in events:
            if ev.first <= i <= (ev.last or n):
                x = x + ev.health_shift
                bias = bias + ev.measurement_bias
        r = engine.run(ISO, inlet_loss=0.0, exhaust_loss=0.0, health=health_from_vector(x), guess=guess)
        dz = deltas(measurement_vector(r), z0) + bias + rng.normal(0, PASSOFF_NOISE)
        hr = nominal.heat_rate * (1 + dz[1] / 100) / (1 + dz[0] / 100)  # fuel heat per unit output
        rows.append({
            "build": i,
            **{f"d_{m}": dz[k] for k, m in enumerate(MEASUREMENTS)},
            "power_MW": nominal.electrical_power / 1e6 * (1 + dz[0] / 100),
            "heat_rate": hr,
            "egt_C": nominal.exhaust.t - 273.15 + dz[2],
            **{f"true_{h}": x[j] for j, h in enumerate(HEALTH)},
        })
    return pd.DataFrame(rows)


# --- in-service fleet --------------------------------------------------------

@dataclass
class FleetFault:
    engine: int
    day: int
    kind: str  # "engine" or "sensor"
    target: str  # health parameter or sensor:<measurement>
    description: str
    magnitude: float  # health delta (%/pts) for engine faults, measurement delta for sensors
    ramp_days: int = 0  # 0 = step


def default_fleet_faults():
    return [
        FleetFault(3, 400, "engine", "turbine_eff", "HP turbine blade damage", -1.5),
        FleetFault(5, 500, "engine", "turbine_capacity", "HP turbine nozzle erosion", 2.0),
        FleetFault(7, 300, "engine", "compressor_eff", "Inlet filter failure, rapid fouling", -2.0, ramp_days=40),
        FleetFault(10, 250, "sensor", "sensor:egt", "Exhaust thermocouple drift", 12.0, ramp_days=60),
        FleetFault(12, 450, "sensor", "sensor:power", "Power meter calibration error", -0.8),
    ]


def _ambient_series(rng, days):
    seasonal = 11 + 8 * np.sin(2 * np.pi * (days - 110) / 365)
    weather = np.zeros(len(days))
    for i in range(1, len(days)):
        weather[i] = 0.7 * weather[i - 1] + rng.normal(0, 2.5)
    t = np.clip(seasonal + weather, -4, 33)
    p = 1013 + np.cumsum(rng.normal(0, 2, len(days))) * 0.3
    p = np.clip(p - (p.mean() - 1013), 985, 1040)
    rh = np.clip(0.78 - 0.01 * (t - 11) + rng.normal(0, 0.07, len(days)), 0.35, 0.98)
    return t, p, rh


def in_service_fleet(engine, n_engines=12, days=730, interval=2, faults=None, seed=11):
    """Base-load snapshots for a fleet. Returns (data, maintenance log, fault list)."""
    rng = np.random.default_rng(seed)
    faults = default_fleet_faults() if faults is None else faults
    snap = np.arange(0, days, interval)
    rows, washes = [], []
    for e in range(1, n_engines + 1):
        build = rng.normal(0, BUILD_SCATTER)
        foul_rate = rng.uniform(0.6, 1.4) * np.array([-0.4, -0.5, 0, 0]) / 100  # per day
        wear_rate = np.array([0, -0.2, -0.25, 0.3]) / 365
        wash_days = list(np.cumsum(rng.integers(100, 140, size=10)))
        wash_days = [w for w in wash_days if w < days]
        washes += [{"engine": e, "day": int(w)} for w in wash_days]
        t_amb, p_amb, rh = _ambient_series(rng, snap)
        guess = None
        for k, d in enumerate(snap):
            last_wash = max([0] + [w for w in wash_days if w <= d])
            fouling = foul_rate * (d - last_wash)
            x = build + fouling + wear_rate * d
            sensor_bias = np.zeros(6)
            for f in faults:
                if f.engine != e or d < f.day:
                    continue
                frac = 1.0 if f.ramp_days == 0 else min(1.0, (d - f.day) / f.ramp_days)
                if f.kind == "engine":
                    j = HEALTH.index(f.target)
                    if f.target == "compressor_eff" and f.ramp_days:
                        # filter failure: extra fouling until the next wash
                        nxt = min([w for w in wash_days if w > f.day] + [days])
                        frac = frac if d < nxt else 0.0
                    x[j] += f.magnitude * frac
                else:
                    sensor_bias[MEASUREMENTS.index(f.target.split(":")[1])] += f.magnitude * frac
            amb = Ambient(t_amb[k] + 273.15, p_amb[k] * 100, rh[k])
            r = engine.run(amb, health=health_from_vector(x), guess=guess)
            guess = r.info["solution"]
            z = measurement_vector(r)
            noise = rng.normal(0, FIELD_NOISE)
            rel = np.array([1, 1, 0, 1, 0, 1], bool)
            z_meas = z.copy()
            z_meas[rel] *= 1 + (noise[rel] + sensor_bias[rel]) / 100
            z_meas[~rel] += noise[~rel] + sensor_bias[~rel]
            rows.append({
                "engine": e, "day": int(d), "t_amb": t_amb[k] + rng.normal(0, 0.3), "p_amb": p_amb[k],
                "rh": rh[k], **{m: z_meas[i] for i, m in enumerate(MEASUREMENTS)},
                **{f"true_{h}": x[j] for j, h in enumerate(HEALTH)},
            })
    return pd.DataFrame(rows), pd.DataFrame(washes), faults


# --- baseline model and monitoring --------------------------------------------

class BaselineModel:
    """Nominal engine measurements as a function of ambient, from a precomputed grid.

    Ambient pressure enters through non-dimensional scaling: power, fuel flow and
    P3 scale with it, temperatures and speeds do not.
    """

    def __init__(self, engine, t_range=(-15, 40), rh_grid=(0.3, 0.6, 0.95)):
        self.t = np.arange(t_range[0], t_range[1] + 1, 1.0)
        self.rh = np.array(rh_grid)
        grid = np.empty((len(self.t), len(self.rh), len(MEASUREMENTS)))
        for j, rh in enumerate(self.rh):
            guess = None
            for i, t in enumerate(self.t):
                r = engine.run(Ambient(t + 273.15, ISO.p, rh), guess=guess)
                guess = r.info["solution"]
                grid[i, j] = measurement_vector(r)
        self.interp = RegularGridInterpolator((self.t, self.rh), grid)

    def expected(self, t_amb, p_amb_mbar, rh):
        pts = np.column_stack([np.clip(t_amb, self.t[0], self.t[-1]), np.clip(rh, self.rh[0], self.rh[-1])])
        z = self.interp(pts)
        delta = np.asarray(p_amb_mbar) * 100 / ISO.p
        for k in (0, 1, 3):
            z[:, k] *= delta
        return z


def fleet_deltas(data, baseline):
    expected = baseline.expected(data["t_amb"].to_numpy(), data["p_amb"].to_numpy(), data["rh"].to_numpy())
    dz = deltas(data[MEASUREMENTS].to_numpy(), expected)
    return pd.DataFrame(dz, columns=[f"d_{m}" for m in MEASUREMENTS], index=data.index)


@dataclass
class Alarm:
    engine: int
    day: int
    statistic: float
    change: np.ndarray  # mean measurement-delta change
    classification: list  # ranked (label, score)
    source: str = ""


def _trend_design(t_prior, t_new, step_flags_prior):
    """Design matrices for a line (plus a step at a known wash) fitted to the prior window."""
    cols = [np.ones_like(t_prior), t_prior - t_prior.mean()]
    new = [np.ones_like(t_new), t_new - t_prior.mean()]
    if step_flags_prior.any() and not step_flags_prior.all():
        cols.append(step_flags_prior.astype(float))
        new.append(np.ones_like(t_new))
    return np.column_stack(cols), np.column_stack(new)


def ambient_noise_covariance(baseline, u_t_amb=0.3):
    """Delta covariance caused by ambient temperature measurement error, which moves all deltas together."""
    lo = baseline.expected([14.5], [1013.25], [0.6])
    hi = baseline.expected([15.5], [1013.25], [0.6])
    J = deltas(lo, hi)[0]  # deltas seen when the true ambient is 1 K below the reading
    return np.outer(J, J) * u_t_amb**2


def detect_changes(data, dz, washes, gpa, prior=20, recent=8, threshold=33.0, min_post_wash=5, extra_cov=None):
    """Trend-compensated change detection with GPA isolation.

    For each snapshot, the mean of the last `recent` deltas is compared with what
    the engine's own trend over the preceding `prior` snapshots predicts (washes
    in the prior window are modelled as steps). The statistic is d' S^-1 d, with S
    the covariance of that difference: measurement noise (plus `extra_cov`, e.g.
    ambient sensor error) scaled by the regression's prediction variance. It is
    chi-squared with 6 degrees of freedom when nothing has changed. Windows that
    straddle a wash are skipped, and so is any window until the prior window holds
    `min_post_wash` snapshots since the wash to anchor the new level.
    """
    R = np.diag(gpa.noise**2) + (extra_cov if extra_cov is not None else 0)
    R_inv = np.linalg.inv(R)
    alarms = []
    for e, g in data.groupby("engine"):
        days = g["day"].to_numpy(float)
        y = dz.loc[g.index].to_numpy()
        w = np.sort(washes.loc[washes["engine"] == e, "day"].to_numpy()) if len(washes) else np.array([])
        epoch = np.searchsorted(w, days, side="right")  # wash count up to each snapshot
        for i in range(prior + recent, len(days)):
            new = slice(i - recent + 1, i + 1)
            old = slice(i - recent + 1 - prior, i - recent + 1)
            if epoch[new].min() != epoch[new].max():
                continue
            flags = epoch[old] == epoch[new][0]
            if not flags.all() and flags.sum() < min_post_wash:
                continue
            A, A_new = _trend_design(days[old], days[new], flags)
            pinv = np.linalg.pinv(A)
            d = (y[new] - A_new @ (pinv @ y[old])).mean(axis=0)
            a = A_new.mean(axis=0)
            var_factor = 1 / recent + a @ np.linalg.pinv(A.T @ A) @ a
            stat = float(d @ R_inv @ d / var_factor)
            if stat > threshold and (not alarms or alarms[-1].engine != e or days[i] - alarms[-1].day > 60):
                alarms.append(Alarm(int(e), int(days[i]), stat, d, gpa.classify(d)))
    return alarms


def consistency_monitor(data, dz, gpa, lam=0.05, threshold=20.0, warmup=30, extra_cov=None, engine=None,
                        recheck=30):
    """Instrument-fault detector: are the smoothed deltas explainable by any component health change?

    EWMA-smoothed deltas are fitted with the gas-path model by generalised least
    squares. The residual chi-square (2 degrees of freedom: 6 measurements, 4
    health parameters) stays small for engine faults and wear, and grows when one
    sensor drifts. Removing one sensor at a time names the culprit.

    Linear GPA breaks down for large deviations (a badly damaged engine looks
    inconsistent too). With `engine` given, each candidate alarm is first refitted
    with the full nonlinear model; if that explains the deltas, it is not an
    instrument fault, and the engine is re-checked after `recheck` snapshots.
    """
    R = (np.diag(gpa.noise**2) + (extra_cov if extra_cov is not None else 0)) * lam / (2 - lam)
    Li = np.linalg.inv(np.linalg.cholesky(R))
    Hw = Li @ gpa.H

    def fit(zw, keep):
        x, *_ = np.linalg.lstsq(Hw[keep], zw[keep], rcond=None)
        r = zw[keep] - Hw[keep] @ x
        return float(r @ r)

    from .gpa import nonlinear_fit

    alarms, chi_all = [], pd.Series(np.nan, index=dz.index)
    for e, g in data.groupby("engine"):
        sm = dz.loc[g.index].ewm(alpha=lam).mean().to_numpy()
        days = g["day"].to_numpy()
        n_m = sm.shape[1]
        cleared_until = -1
        for i in range(warmup, len(days)):
            zw = Li @ sm[i]
            chi = fit(zw, np.ones(n_m, bool))
            chi_all.loc[g.index[i]] = chi
            if chi <= threshold or i <= cleared_until:
                continue
            if alarms and alarms[-1].engine == e and days[i] - alarms[-1].day <= 60:
                continue  # already in an alarm episode
            if engine is not None:
                _, chi_nl = nonlinear_fit(engine, sm[i], R)
                if chi_nl <= threshold:
                    cleared_until = i + recheck
                    continue
            drops = []
            for k in range(n_m):
                keep = np.ones(n_m, bool)
                keep[k] = False
                drops.append(chi - fit(zw, keep))
            ranked = sorted(((f"sensor:{MEASUREMENTS[j]}", drops[j] / chi) for j in range(n_m)),
                            key=lambda kv: -kv[1])
            alarms.append(Alarm(int(e), int(days[i]), chi, sm[i], ranked))
    return alarms, chi_all


@dataclass
class MonitoringResult:
    alarms: list  # combined, each with .source set
    step_alarms: list
    sensor_alarms: list
    chi: pd.Series
    dz: pd.DataFrame
    excluded: pd.Series  # snapshots left out (gas generator on its speed limit)


def monitor_fleet(engine, data, washes, baseline=None, gpa=None, step_threshold=25.0, sensor_threshold=20.0,
                  u_t_amb=0.3, speed_band=0.003):
    """Full in-service pipeline: baseline deltas, step detection, instrument consistency, fault isolation.

    Snapshots where the gas generator is on its speed limit are excluded: the
    nominal baseline is temperature-controlled there, so deltas would mix control
    mode with health. When the consistency monitor fires on the same engine as a
    step alarm (within 60 days), the instrument diagnosis wins.
    """
    baseline = baseline or BaselineModel(engine)
    gpa = gpa or make_gpa(engine)
    max_speed = getattr(engine, "max_gg_speed", np.inf)
    excluded = data["n_gg"] >= max_speed - speed_band
    d = data[~excluded]
    dz = fleet_deltas(d, baseline)
    cov = ambient_noise_covariance(baseline, u_t_amb)
    steps = detect_changes(d, dz, washes, gpa, threshold=step_threshold, extra_cov=cov)
    sensors, chi = consistency_monitor(d, dz, gpa, threshold=sensor_threshold, extra_cov=cov, engine=engine)
    for a in steps:
        a.source = "step"
    for a in sensors:
        a.source = "instrument"
    combined = list(sensors)
    for a in steps:
        near = [s for s in sensors if s.engine == a.engine and abs(s.day - a.day) <= 60]
        if not near:
            combined.append(a)
    combined.sort(key=lambda a: (a.engine, a.day))
    return MonitoringResult(combined, steps, sensors, chi, dz, excluded)


def naive_fleet_outliers(data, dz, baseline_days=90, threshold=30.0, recent=8):
    """Data-driven comparator: Hotelling T^2 of each engine's rolling-mean deltas against the fleet's early spread.

    It knows nothing about fouling, washes or physics, which is the point of the comparison.
    """
    rolled = dz.groupby(data["engine"]).transform(lambda s: s.rolling(recent).mean())
    early = rolled[(data["day"] < baseline_days)].dropna()
    mu = early.mean().to_numpy()
    cov_inv = np.linalg.inv(np.cov(early.to_numpy().T))
    alarms = []
    for e, g in data.groupby("engine"):
        y = rolled.loc[g.index].to_numpy()
        days = g["day"].to_numpy()
        for i in range(recent, len(days)):
            d = y[i] - mu
            t2 = d @ cov_inv @ d
            if t2 > threshold and (not alarms or alarms[-1].engine != e or days[i] - alarms[-1].day > 60):
                alarms.append(Alarm(int(e), int(days[i]), float(t2), d, []))
    return alarms


def score(alarms, faults, n_engines, days, window=150):
    """Detection delay, isolation and false alarms per engine-year.

    Repeat alarms on an engine whose fault is still present are not false alarms.
    """
    rows, matched = [], set()
    for a in alarms:
        if any(f.engine == a.engine and a.day >= f.day for f in faults):
            matched.add(id(a))
    for f in faults:
        hits = [a for a in alarms if a.engine == f.engine and f.day <= a.day <= f.day + window]
        if hits:
            a = hits[0]
            matched.add(id(a))
            top = a.classification[0][0] if a.classification else None
            rows.append({"engine": f.engine, "fault": f.description, "true": f.target, "detected_day": a.day,
                         "delay_days": a.day - f.day, "isolated_as": top, "correct": top == f.target})
        else:
            rows.append({"engine": f.engine, "fault": f.description, "true": f.target, "detected_day": None,
                         "delay_days": None, "isolated_as": None, "correct": False})
    false = [a for a in alarms if id(a) not in matched]
    return pd.DataFrame(rows), false, len(false) / (n_engines * days / 365)


def estimate_health(dz, gpa, lam=0.05, data=None):
    """EWMA-smoothed deltas per engine, then GPA health estimates. Returns a DataFrame of health deltas."""
    smooth = dz.copy()
    if data is not None:
        smooth = dz.groupby(data["engine"]).transform(lambda s: s.ewm(alpha=lam).mean())
    est = gpa.estimate(smooth.to_numpy())
    return pd.DataFrame(est.health, columns=HEALTH, index=dz.index)


def make_gpa(engine, noise=FIELD_NOISE):
    from .gpa import influence_matrix

    return GasPathAnalysis(influence_matrix(engine), noise)
