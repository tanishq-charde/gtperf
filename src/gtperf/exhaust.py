"""Exhaust temperature field: burner hot streaks, swirl, mixing, and what a fixed rake sees.

The exhaust plane temperature is modelled as the mass-weighted mean from the cycle
(the engine model's exhaust temperature) plus two zero-mean patterns:

Circumferential pattern
    N burners (cans) sit at equal angles. Each delivers a hot streak whose
    sector-average temperature excess at the exhaust is

        a_b = core_excess + delta_b * combustor_rise * temperature_ratio

    `core_excess` is the lobe that every can produces even with equal fuel (hot can
    cores, cooler gaps fed by dilution and cooling air). `delta_b` is the burner's
    fuel split deviation (fraction of the mean burner fuel; the deviations sum to
    zero because the control fixes total fuel). A local fuel-air ratio excess raises
    the combustor-exit temperature by about delta_b times the combustor temperature
    rise; through the expansion a temperature non-uniformity scales roughly with the
    static temperature, hence the factor T_exh/T_4 (`temperature_ratio`).

    Each streak arrives at the exhaust rotated by the swirl angle and spread by
    mixing, as a wrapped Gaussian of angular width sigma:

        theta_swirl(L) = theta_0 + kappa * L          [assumption]
        sigma(L)       = sigma_0 + sigma_1 * L        [assumption]

    L is the turbine work relative to base load, approximated by the load fraction
    (power / rated power): stage loading, and with it the flow turning that carries
    the streaks round the annulus, grows with the work extracted. Higher load also
    means more turbulence and stronger mixing, so streaks are wider and flatter. The
    kernel is normalised so a streak's sector-average excess is a_b whatever its
    width, which keeps the field's mean equal to the cycle exhaust temperature.

Radial pattern
    A mid-span peak R * (1 - (2 r - 1)^2) and a cold endwall layer from hub and tip
    cooling films, -E * (exp(-r/d) + exp(-(1-r)/d)), each with its mass-weighted mean
    removed. r is the radius fraction from hub to tip; mass flux per unit area is
    assumed uniform (density variation with temperature changes the mass-weighted
    mean by about 0.1 K and is neglected), so area weighting stands in for mass
    weighting.

A faulty burner (lean, or blocked with no fuel) has a large negative delta_b; the
other burners pick up its fuel. It leaves a cold spot that rotates with load, which
is what separates it from a bad thermocouple (fixed to one position).

The rake samples the field at fixed angles and immersions. Its mean differs from
the mass-weighted mean by an amount that depends on the unknown burner deviations,
swirl, and radial profile of the engine under test. `rake_error_samples` gives that
error by Monte Carlo over the model's priors; its standard deviation is the profile
(under-sampling) uncertainty and its mean is a correction.

The default parameter values are representative assumptions for an industrial
engine with six cans, chosen so a healthy engine shows a rake spread of 10-25 K;
they are not measured data for any engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

N_RAKE = 12
RAKE_ANGLES_DEG = 15.0 + 30.0 * np.arange(N_RAKE)
# Two-point Gauss-Legendre nodes in annulus area fraction, alternating along the rake
GAUSS_AREA = (0.5 - 0.5 / np.sqrt(3.0), 0.5 + 0.5 / np.sqrt(3.0))
RAKE_AREA_FRACTIONS = np.array([GAUSS_AREA[i % 2] for i in range(N_RAKE)])


@dataclass(frozen=True)
class BurnerFault:
    """A lean (reduced fuel) or blocked (no fuel) burner. `burner` counts from 1."""

    burner: int
    kind: str = "lean"
    fuel_fraction: float = 0.7  # fraction of nominal burner fuel for a lean burner

    @property
    def delta(self):
        return -1.0 if self.kind == "blocked" else self.fuel_fraction - 1.0


@dataclass(frozen=True)
class ExhaustProfileModel:
    """Prior description of an engine type's exhaust temperature field (see module notes)."""

    n_burners: int = 6
    burner_offset_deg: float = 0.0  # angle of burner 1
    fuel_split_std: float = 0.012  # build-to-build burner fuel split deviation (fraction)
    combustor_rise_K: float = 800.0
    temperature_ratio: float = 0.55  # T_exh / T_4
    core_excess_K: float = 20.0
    core_excess_std_K: float = 6.0
    swirl_deg_at_zero_load: float = 10.0
    swirl_deg_per_load: float = 60.0
    swirl_std_deg: float = 8.0  # engine-to-engine uncertainty of theta_0
    width_deg_at_zero_load: float = 10.0
    width_deg_per_load: float = 15.0
    radial_K: float = 10.0
    radial_std_K: float = 3.0
    endwall_K: float = 8.0
    endwall_std_K: float = 3.0
    endwall_thickness: float = 0.08  # fraction of span
    hub_tip_ratio: float = 0.6

    def swirl_deg(self, load, theta0=None):
        theta0 = self.swirl_deg_at_zero_load if theta0 is None else theta0
        return theta0 + self.swirl_deg_per_load * load

    def width_deg(self, load):
        return self.width_deg_at_zero_load + self.width_deg_per_load * load

    def burner_angles_deg(self):
        return self.burner_offset_deg + 360.0 * np.arange(self.n_burners) / self.n_burners

    def sample(self, rng, fault=None):
        """One engine's realised field, drawn from the priors (deviations are hidden truth)."""
        return ExhaustField(self, *self._draw(rng, 1, fault), fault=fault)

    def _draw(self, rng, n, fault=None):
        d = rng.normal(0, self.fuel_split_std, (n, self.n_burners))
        if fault is not None:
            d[:, fault.burner - 1] += fault.delta
        d -= d.mean(axis=1, keepdims=True)
        core = np.maximum(rng.normal(self.core_excess_K, self.core_excess_std_K, n), 0.0)
        theta0 = rng.normal(self.swirl_deg_at_zero_load, self.swirl_std_deg, n)
        radial = rng.normal(self.radial_K, self.radial_std_K, n)
        endwall = np.maximum(rng.normal(self.endwall_K, self.endwall_std_K, n), 0.0)
        if n == 1:
            return d[0], float(core[0]), float(theta0[0]), float(radial[0]), float(endwall[0])
        return d, core, theta0, radial, endwall

    # --- radial geometry ---------------------------------------------------------
    def radius_fraction(self, area_fraction):
        """Radius fraction (hub 0, tip 1) at which the annulus area inside is `area_fraction` of the total."""
        h = self.hub_tip_ratio
        r = np.sqrt(h * h + np.asarray(area_fraction) * (1 - h * h))
        return (r - h) / (1 - h)

    def radial_shapes(self, rhat):
        """Zero-mean (area-weighted) mid-span peak and endwall shapes at radius fractions rhat."""
        peak_mean, wall_mean = _shape_means(self.hub_tip_ratio, self.endwall_thickness)
        peak, wall = _shapes(np.asarray(rhat, float), self.endwall_thickness)
        return peak - peak_mean, wall - wall_mean


def _shapes(rhat, d):
    return 1 - (2 * rhat - 1) ** 2, np.exp(-rhat / d) + np.exp(-(1 - rhat) / d)


@lru_cache(maxsize=16)
def _shape_means(h, d):
    rhat = np.linspace(0, 1, 4001)
    w = h + rhat * (1 - h)  # area element proportional to radius
    peak, wall = _shapes(rhat, d)
    return float(np.trapezoid(peak * w, rhat) / np.trapezoid(w, rhat)), float(
        np.trapezoid(wall * w, rhat) / np.trapezoid(w, rhat))


def _wrapped_gauss(dtheta_rad, sigma_rad):
    out = np.zeros(np.broadcast(dtheta_rad, sigma_rad).shape)
    for k in range(-2, 3):
        out += np.exp(-0.5 * ((dtheta_rad + 2 * np.pi * k) / sigma_rad) ** 2)
    return out / (np.sqrt(2 * np.pi) * sigma_rad)


def _angular(model, theta_deg, load, deviations, core, theta0):
    """Circumferential excess (K). deviations (..., N); core, theta0 (...,); theta_deg (M,). Returns (..., M)."""
    deviations = np.asarray(deviations, float)
    core = np.asarray(core, float)[..., None]
    a = core + deviations * model.combustor_rise_K * model.temperature_ratio  # (..., N)
    centres = model.burner_angles_deg() + np.asarray(model.swirl_deg(load, theta0))[..., None]  # (..., N)
    sigma = np.radians(model.width_deg(load))
    dth = np.radians(np.asarray(theta_deg)[None, :] - centres[..., :, None])  # (..., N, M)
    g = (2 * np.pi / model.n_burners) * _wrapped_gauss(dth, sigma)
    return np.sum(a[..., :, None] * g, axis=-2) - np.mean(a, axis=-1, keepdims=True)


@dataclass
class ExhaustField:
    """One engine's exhaust temperature field (zero-mean deviations from the cycle exhaust temperature)."""

    model: ExhaustProfileModel
    deviations: np.ndarray  # burner fuel split deviations (fraction), sum zero
    core_excess_K: float
    swirl0_deg: float
    radial_K: float
    endwall_K: float
    fault: BurnerFault | None = None

    def angular(self, theta_deg, load):
        return _angular(self.model, np.atleast_1d(theta_deg), load, self.deviations, self.core_excess_K,
                        self.swirl0_deg)

    def radial(self, rhat):
        peak, wall = self.model.radial_shapes(rhat)
        return self.radial_K * peak - self.endwall_K * wall

    def deviation(self, theta_deg, rhat, load):
        """Temperature minus the mass-weighted mean (K) at angles and radius fractions (same shape)."""
        theta_deg, rhat = np.broadcast_arrays(np.asarray(theta_deg, float), np.asarray(rhat, float))
        ang = self.angular(theta_deg.ravel(), load).reshape(theta_deg.shape)
        return ang + self.radial(rhat)

    def rake(self, load, angles_deg=RAKE_ANGLES_DEG, area_fractions=RAKE_AREA_FRACTIONS):
        """True deviation from the mass-weighted mean at each rake thermocouple (K)."""
        return self.deviation(angles_deg, self.model.radius_fraction(area_fractions), load)

    def cold_spot_deg(self, load, resolution=0.05):
        """Angle of the coldest point of the circumferential pattern, degrees in [0, 360)."""
        theta = np.arange(0, 360, resolution)
        return float(theta[np.argmin(self.angular(theta, load))])

    def grid(self, load, n_theta=361, n_r=41):
        """Angles (deg), radius fractions and the deviation field on a polar grid, for plotting."""
        theta = np.linspace(0, 360, n_theta)
        rhat = np.linspace(0, 1, n_r)
        T, R = np.meshgrid(theta, rhat)
        return theta, rhat, self.deviation(T, R, load)


# --- Monte Carlo of the rake error ------------------------------------------------

def rake_error_samples(model, load, valid=None, n=4000, seed=0, angles_deg=RAKE_ANGLES_DEG,
                       area_fractions=RAKE_AREA_FRACTIONS, fault=None):
    """Rake mean minus mass-weighted mean (K) for `n` engines drawn from the model's priors.

    `valid` is a boolean mask (or index list) of the thermocouples kept by validation.
    """
    rng = np.random.default_rng(seed)
    mask = _mask(valid, len(angles_deg))
    angles = np.asarray(angles_deg, float)[mask]
    areas = np.asarray(area_fractions, float)[mask]
    d, core, theta0, radial, endwall = model._draw(rng, n, fault)
    ang = _angular(model, angles, load, d, core, theta0)  # (n, k)
    peak, wall = model.radial_shapes(model.radius_fraction(areas))
    rad = radial[:, None] * peak[None, :] - endwall[:, None] * wall[None, :]
    return (ang + rad).mean(axis=1)


@dataclass
class ProfileUncertainty:
    correction: float  # expected rake error (K), subtracted from the rake mean
    u: float  # standard uncertainty of the remaining rake error (K)
    samples: np.ndarray
    n_tc: int
    load: float


def _mask(valid, n):
    """Boolean mask from None (all), a boolean array, or a list of indices."""
    if valid is None:
        return np.ones(n, bool)
    v = np.asarray(valid)
    if v.dtype == bool:
        return v
    mask = np.zeros(n, bool)
    mask[v] = True
    return mask


def profile_uncertainty(model, load, valid=None, n=4000, seed=0):
    """Rake profile (under-sampling) correction and standard uncertainty by Monte Carlo over the model priors."""
    s = rake_error_samples(model, load, valid, n=n, seed=seed)
    k = int(_mask(valid, N_RAKE).sum())
    return ProfileUncertainty(float(s.mean()), float(s.std(ddof=1)), s, k, float(load))


# --- burner fault detection --------------------------------------------------------

def _neighbours(idx, n=N_RAKE):
    """Angular neighbours among the available thermocouple indices (a missing one is bridged)."""
    idx = sorted(idx)
    return [(idx[i], idx[(i + 1) % len(idx)]) for i in range(len(idx))]


def _pair_depth(dev, idx):
    """Depth of the coldest pair of adjacent thermocouples: max over pairs of min(-d_i, -d_j)."""
    best, pair = -np.inf, None
    for i, j in _neighbours(idx):
        v = min(-dev[i], -dev[j])
        if v > best:
            best, pair = v, (i, j)
    return best, pair


@lru_cache(maxsize=64)
def _clean_threshold(model, idx, load, u_tc, quantile, n=20000, seed=7):
    rng = np.random.default_rng(seed)
    d, core, theta0, radial, endwall = model._draw(rng, n)
    angles = RAKE_ANGLES_DEG[list(idx)]
    peak, wall = model.radial_shapes(model.radius_fraction(RAKE_AREA_FRACTIONS[list(idx)]))
    t = _angular(model, angles, load, d, core, theta0) + radial[:, None] * peak - endwall[:, None] * wall
    t = t + rng.normal(0, u_tc, t.shape)
    dev = t - np.median(t, axis=1, keepdims=True)
    pos = {k: m for m, k in enumerate(idx)}
    pairs = [(pos[i], pos[j]) for i, j in _neighbours(idx)]
    depth = np.max(np.stack([np.minimum(-dev[:, i], -dev[:, j]) for i, j in pairs], axis=1), axis=1)
    return float(np.quantile(depth, quantile))


@dataclass
class ColdSpot:
    load: float
    depth: float  # K below the rake median, coldest adjacent pair
    threshold: float
    flagged: bool
    angle_deg: float  # estimated cold-spot centre
    thermocouples: tuple  # the coldest adjacent pair (0-based indices)
    spread: float  # max - min across the rake (K)
    burner: int | None = None  # burner whose (modelled) streak is closest to the cold spot


def cold_spot(readings, model=None, load=1.0, u_tc=1.27, quantile=0.999):
    """Screen one load's rake (array of 12 point means, NaN where excluded) for a burner cold spot.

    The statistic is the depth below the rake median of the coldest *pair* of
    adjacent thermocouples. A burner streak spans more than one thermocouple
    spacing, so a cold spot drags two neighbours down together, while a single
    faulty sensor moves only one. The threshold is the `quantile` of the same
    statistic for healthy engines drawn from the profile model, including
    thermocouple calibration error `u_tc`, so the false-alarm rate is set by the
    model rather than hand-picked.
    """
    model = model or ExhaustProfileModel()
    x = np.asarray(readings, float)
    idx = tuple(int(i) for i in np.flatnonzero(~np.isnan(x)))
    dev = x - np.nanmedian(x)
    depth, pair = _pair_depth(dev, idx)
    thr = _clean_threshold(model, idx, round(float(load), 2), float(u_tc), quantile)
    i = pair[0] if dev[pair[0]] <= dev[pair[1]] else pair[1]
    angle = _vertex(dev, idx, i)
    flagged = bool(depth > thr)
    burner = None
    if flagged:
        rel = (angle - model.swirl_deg(load) - model.burner_offset_deg) / (360.0 / model.n_burners)
        burner = int(np.round(rel)) % model.n_burners + 1
    return ColdSpot(float(load), float(depth), thr, flagged, angle, pair, float(np.nanmax(x) - np.nanmin(x)), burner)


def _vertex(dev, idx, i):
    """Cold-spot centre from the coldest thermocouple and its neighbours (Gaussian, else parabolic, interpolation)."""
    order = sorted(idx)
    m = order.index(i)
    lo, hi = order[m - 1], order[(m + 1) % len(order)]
    a_lo = (RAKE_ANGLES_DEG[i] - RAKE_ANGLES_DEG[lo]) % 360
    a_hi = (RAKE_ANGLES_DEG[hi] - RAKE_ANGLES_DEG[i]) % 360
    y0, ylo, yhi = -dev[i], -dev[lo], -dev[hi]
    if abs(a_lo - a_hi) < 1e-9:
        h = a_lo
        if min(y0, ylo, yhi) > 0:
            num, den = np.log(ylo) - np.log(yhi), np.log(ylo) - 2 * np.log(y0) + np.log(yhi)
        else:
            num, den = ylo - yhi, ylo - 2 * y0 + yhi
        shift = 0.5 * h * num / den if den < 0 else 0.0
        shift = float(np.clip(shift, -h / 2, h / 2))
    else:
        shift = 0.0
    return float((RAKE_ANGLES_DEG[i] + shift) % 360)


@dataclass
class BurnerCheck:
    spots: list  # ColdSpot per load
    rotation_deg: float | None  # measured cold-spot rotation between the lowest and highest load
    modelled_rotation_deg: float | None
    tolerance_deg: float
    verdict: str

    @property
    def fault(self):
        return self.verdict.startswith("burner fault")


def burner_check(readings_by_load, model=None, u_tc=1.27, tolerance_deg=12.0):
    """Cold-spot screen at each load, and tracking of the spot's rotation with load.

    `readings_by_load` maps load fraction -> 12 rake readings (NaN where excluded).
    With two or more loads, a cold spot that rotates by the swirl model's angle
    (kappa * delta L, within `tolerance_deg`) is a combustor fault; one that stays
    put is more likely a thermocouple or rake installation problem.
    """
    model = model or ExhaustProfileModel()
    loads = sorted(readings_by_load)
    spots = [cold_spot(readings_by_load[L], model, L, u_tc) for L in loads]
    flagged = [s for s in spots if s.flagged]
    rot = modelled = None
    if not flagged:
        verdict = "no burner fault detected"
    elif len(flagged) >= 2:
        a, b = flagged[0], flagged[-1]
        rot = float((b.angle_deg - a.angle_deg + 180) % 360 - 180)
        modelled = model.swirl_deg_per_load * (b.load - a.load)
        if abs(rot - modelled) <= tolerance_deg:
            verdict = f"burner fault: cold spot near burner {b.burner} rotates with load as the swirl model predicts"
        elif abs(rot) <= tolerance_deg / 2:
            verdict = "cold spot fixed in position across loads: suspect thermocouples or rake, not a burner"
        else:
            verdict = "burner fault suspected: cold spot moves with load but not by the modelled swirl angle"
    else:
        verdict = f"burner fault suspected: cold spot near burner {flagged[0].burner} (single load, not tracked)"
    return BurnerCheck(spots, rot, modelled, tolerance_deg, verdict)


@dataclass
class RakeSurvey:
    """Rake readings from a load survey (true field plus thermocouple errors), for burner diagnostics."""

    readings: dict  # load -> 12 readings (degC)
    field: ExhaustField
    t_mean: dict = field(default_factory=dict)


def rake_survey(field_, loads, t_mean=550.0, tc_bias=None, noise_K=0.2, seed=0):
    """Synthetic rake readings at several loads. `t_mean` may be a dict load -> mass-weighted mean (degC)."""
    rng = np.random.default_rng(seed)
    bias = np.zeros(N_RAKE) if tc_bias is None else np.asarray(tc_bias, float)
    out, means = {}, {}
    for L in loads:
        tm = t_mean[L] if isinstance(t_mean, dict) else t_mean
        out[L] = tm + field_.rake(L) + bias + rng.normal(0, noise_K, N_RAKE)
        means[L] = tm
    return RakeSurvey(out, field_, means)
