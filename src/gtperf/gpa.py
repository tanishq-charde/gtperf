"""Linear gas-path analysis (GPA).

Measurement deltas (measured against the nominal model at the same conditions)
are related to component health deltas through an influence coefficient matrix
H, found by perturbing the engine model:

    dz = H dx + noise

Health is estimated by weighted least squares. A large residual means the deltas
are not consistent with any combination of component changes, which points at
an instrument fault. Refitting with one sensor removed at a time isolates it.

The measurement and health sets are configurable. The default (six measurements, four health parameters)
is a typical industrial package; `EXTENDED_MEASUREMENTS` adds the power turbine inlet temperature and
pressure (T45, P45) and `SPLIT_HEALTH` separates the HP and power turbine efficiencies. An influence
matrix can also be built at part load (`power=` setting), where output is the setting rather than a
measurement, and stacked with base load: the same fault moves the measurements differently at the two
loads, which separates faults that look alike at one operating point.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .cycle import ISO
from .maps import Health

MEASUREMENTS = ["power", "fuel_flow", "egt", "p3", "t3", "n_gg"]
EXTENDED_MEASUREMENTS = [*MEASUREMENTS, "t45", "p45"]
TEMPERATURES = {"egt", "t3", "t45"}  # deltas in kelvin; the rest in percent
MEASUREMENT_UNITS = {m: ("K" if m in TEMPERATURES else "%") for m in EXTENDED_MEASUREMENTS}
HEALTH = ["compressor_flow", "compressor_eff", "turbine_eff", "turbine_capacity"]
SPLIT_HEALTH = ["compressor_flow", "compressor_eff", "hpt_eff", "turbine_capacity", "pt_eff"]
HEALTH_LABELS = {
    "compressor_flow": "Compressor flow capacity (%)",
    "compressor_eff": "Compressor efficiency (pts)",
    "turbine_eff": "Turbine efficiency (pts)",
    "turbine_capacity": "HP turbine flow capacity (%)",
    "hpt_eff": "HP turbine efficiency (pts)",
    "pt_eff": "Power turbine efficiency (pts)",
}
_HEALTH_FIELDS = {
    "compressor_flow": ("compressor_flow_scale", True),
    "compressor_eff": ("compressor_eff_delta", False),
    "turbine_eff": ("turbine_eff_delta", False),
    "turbine_capacity": ("turbine_capacity_scale", True),
    "hpt_eff": ("hpt_eff_delta", False),
    "pt_eff": ("pt_eff_delta", False),
}


def health_from_vector(x, health=HEALTH):
    """Health deltas in percent / points -> Health object."""
    kw = {}
    for name, v in zip(health, x, strict=True):
        field_, scale = _HEALTH_FIELDS[name]
        kw[field_] = 1 + v / 100 if scale else v / 100
    return Health(**kw)


_EXTRACT = {
    "power": lambda r: r.electrical_power,
    "fuel_flow": lambda r: r.fuel_flow,
    "egt": lambda r: r.exhaust.t,
    "p3": lambda r: r.stations["3"].p,
    "t3": lambda r: r.stations["3"].t,
    "n_gg": lambda r: r.info["mechanical_speed"],
    "t45": lambda r: r.stations["45"].t,
    "p45": lambda r: r.stations["45"].p,
}


def measurement_vector(result, measurements=MEASUREMENTS):
    """Gas-path measurements from a CycleResult (absolute values, SI)."""
    return np.array([_EXTRACT[m](result) for m in measurements])


def deltas(measured, expected, measurements=MEASUREMENTS):
    """Percent deltas for flow-like measurements, kelvin deltas for temperatures."""
    measured, expected = np.asarray(measured, float), np.asarray(expected, float)
    d = 100 * (measured / expected - 1)
    for k, m in enumerate(measurements):
        if m in TEMPERATURES:
            d[..., k] = measured[..., k] - expected[..., k]
    return d


def influence_matrix(engine, ambient=ISO, step=0.5, about=None, measurements=MEASUREMENTS, health=HEALTH,
                     **setting):
    """H[i, j]: delta of measurement i for a 1 % (or 1 point) change in health parameter j.

    Deltas are always against the nominal engine; `about` (a health vector) moves
    the linearisation point, for nonlinear GPA. `setting` (e.g. power=...) holds a
    part-load operating point; at a fixed power, the power row is zero.
    """
    nominal = measurement_vector(engine.run(ambient, **setting), measurements)
    x0 = np.zeros(len(health)) if about is None else np.asarray(about, float)
    H = np.zeros((len(measurements), len(health)))
    for j in range(len(health)):
        x = x0.copy()
        x[j] += step
        up = measurement_vector(engine.run(ambient, health=health_from_vector(x, health), **setting), measurements)
        x[j] -= 2 * step
        dn = measurement_vector(engine.run(ambient, health=health_from_vector(x, health), **setting), measurements)
        H[:, j] = (deltas(up, nominal, measurements) - deltas(dn, nominal, measurements)) / (2 * step)
    return H


def nonlinear_fit(engine, dz, noise_cov, x0=None, iterations=4, ambient=ISO, measurements=MEASUREMENTS,
                  health=HEALTH):
    """Gauss-Newton gas-path fit with the full engine model. Returns (health, residual chi-square)."""
    nominal = measurement_vector(engine.run(ambient), measurements)
    W = np.linalg.inv(noise_cov)
    x = np.zeros(len(health)) if x0 is None else np.asarray(x0, float)

    def predicted(x):
        r = engine.run(ambient, health=health_from_vector(x, health))
        return deltas(measurement_vector(r, measurements), nominal, measurements)

    for _ in range(iterations):
        H = influence_matrix(engine, ambient, about=x, measurements=measurements, health=health)
        x = x + np.linalg.solve(H.T @ W @ H, H.T @ W @ (dz - predicted(x)))
    r = dz - predicted(x)
    return x, float(r @ W @ r)


@dataclass
class GPAResult:
    health: np.ndarray  # estimated health deltas, shape (..., n_health)
    covariance: np.ndarray  # of the estimate
    residual: np.ndarray  # dz - H x
    chi2: np.ndarray  # normalised residual sum of squares
    dof: int


class GasPathAnalysis:
    def __init__(self, H, noise_std, prior_std=None, measurements=MEASUREMENTS, health=HEALTH):
        """noise_std: measurement delta noise (same units as deltas). prior_std: optional health prior (ridge)."""
        self.H = np.asarray(H)
        self.noise = np.asarray(noise_std, dtype=float)
        self.prior = None if prior_std is None else np.asarray(prior_std, dtype=float)
        self.measurements = list(measurements)
        self.health = list(health)

    def _solve(self, dz, mask):
        H = self.H[mask]
        W = np.diag(1 / self.noise[mask] ** 2)
        A = H.T @ W @ H
        if self.prior is not None:
            A = A + np.diag(1 / self.prior**2)
        cov = np.linalg.inv(A)
        x = (cov @ H.T @ W @ dz[..., mask].T).T
        r = dz[..., mask] - x @ H.T
        chi2 = np.sum((r / self.noise[mask]) ** 2, axis=-1)
        return x, cov, r, chi2

    def estimate(self, dz):
        dz = np.atleast_2d(dz)
        mask = np.ones(len(self.noise), bool)
        x, cov, r, chi2 = self._solve(dz, mask)
        return GPAResult(x, cov, r, chi2, int(mask.sum() - len(self.health)))

    def isolate_sensor(self, dz):
        """For each snapshot, the sensor whose removal reduces the residual most, and the chi2 drop."""
        dz = np.atleast_2d(dz)
        n = len(self.noise)
        full = self._solve(dz, np.ones(n, bool))[3]
        drops = []
        for k in range(n):
            mask = np.ones(n, bool)
            mask[k] = False
            drops.append(full - self._solve(dz, mask)[3])
        drops = np.array(drops).T
        return drops.argmax(axis=1), drops.max(axis=1)

    def fingerprints(self):
        """Unit-norm measurement signature of each single fault, in noise-normalised space."""
        sig = {h: self.H[:, j] / self.noise for j, h in enumerate(self.health)}
        for k, m in enumerate(self.measurements):
            e = np.zeros(len(self.noise))
            e[k] = 1 / self.noise[k]
            sig[f"sensor:{m}"] = e
        return {k: v / np.linalg.norm(v) for k, v in sig.items()}

    def classify(self, change):
        """Best-matching single fault for a change in measurement deltas (e.g. after minus before a step).

        Returns (label, score) ranked by |cosine similarity| in noise-normalised space.
        """
        v = np.asarray(change) / self.noise
        v = v / np.linalg.norm(v)
        scores = {k: float(abs(v @ f)) for k, f in self.fingerprints().items()}
        return sorted(scores.items(), key=lambda kv: -kv[1])

    def similarity(self):
        """|cosine| between every pair of health fingerprints in noise-normalised space: 1 means two faults
        are indistinguishable from these measurements, 0 that they move them in unrelated ways."""
        f = self.fingerprints()
        return {(a, b): float(abs(f[a] @ f[b])) for i, a in enumerate(self.health) for b in self.health[i + 1:]}

    def health_std(self):
        """Standard deviation of each health estimate from one snapshot of measurement noise."""
        W = np.diag(1 / self.noise**2)
        return np.sqrt(np.diag(np.linalg.inv(self.H.T @ W @ self.H)))
