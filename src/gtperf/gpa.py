"""Linear gas-path analysis (GPA).

Measurement deltas (measured against the nominal model at the same conditions)
are related to component health deltas through an influence coefficient matrix
H, found by perturbing the engine model:

    dz = H dx + noise

Health is estimated by weighted least squares. A large residual means the deltas
are not consistent with any combination of component changes, which points at
an instrument fault. Refitting with one sensor removed at a time isolates it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .cycle import ISO
from .maps import Health

MEASUREMENTS = ["power", "fuel_flow", "egt", "p3", "t3", "n_gg"]
MEASUREMENT_UNITS = {"power": "%", "fuel_flow": "%", "egt": "K", "p3": "%", "t3": "K", "n_gg": "%"}
HEALTH = ["compressor_flow", "compressor_eff", "turbine_eff", "turbine_capacity"]
HEALTH_LABELS = {
    "compressor_flow": "Compressor flow capacity (%)",
    "compressor_eff": "Compressor efficiency (pts)",
    "turbine_eff": "Turbine efficiency (pts)",
    "turbine_capacity": "HP turbine flow capacity (%)",
}


def health_from_vector(x):
    """Health deltas in percent / points -> Health object."""
    return Health(1 + x[0] / 100, x[1] / 100, x[2] / 100, 1 + x[3] / 100)


def measurement_vector(result):
    """Gas-path measurements from a CycleResult: power, fuel flow, P3, N_gg relative; EGT and T3 absolute."""
    return np.array([
        result.electrical_power,
        result.fuel_flow,
        result.exhaust.t,
        result.stations["3"].p,
        result.stations["3"].t,
        result.info["mechanical_speed"],
    ])


def deltas(measured, expected):
    """Percent deltas for flow-like measurements, kelvin deltas for temperatures."""
    d = 100 * (measured / expected - 1)
    d[..., 2] = measured[..., 2] - expected[..., 2]
    d[..., 4] = measured[..., 4] - expected[..., 4]
    return d


def influence_matrix(engine, ambient=ISO, step=0.5, about=None):
    """H[i, j]: delta of measurement i for a 1 % (or 1 point) change in health parameter j.

    Deltas are always against the nominal engine; `about` (a health vector) moves
    the linearisation point, for nonlinear GPA.
    """
    nominal = measurement_vector(engine.run(ambient))
    x0 = np.zeros(len(HEALTH)) if about is None else np.asarray(about, float)
    H = np.zeros((len(MEASUREMENTS), len(HEALTH)))
    for j in range(len(HEALTH)):
        x = x0.copy()
        x[j] += step
        up = measurement_vector(engine.run(ambient, health=health_from_vector(x)))
        x[j] -= 2 * step
        dn = measurement_vector(engine.run(ambient, health=health_from_vector(x)))
        H[:, j] = (deltas(up, nominal) - deltas(dn, nominal)) / (2 * step)
    return H


def nonlinear_fit(engine, dz, noise_cov, x0=None, iterations=4, ambient=ISO):
    """Gauss-Newton gas-path fit with the full engine model. Returns (health, residual chi-square)."""
    nominal = measurement_vector(engine.run(ambient))
    W = np.linalg.inv(noise_cov)
    x = np.zeros(len(HEALTH)) if x0 is None else np.asarray(x0, float)
    for _ in range(iterations):
        pred = deltas(measurement_vector(engine.run(ambient, health=health_from_vector(x))), nominal)
        H = influence_matrix(engine, ambient, about=x)
        x = x + np.linalg.solve(H.T @ W @ H, H.T @ W @ (dz - pred))
    r = dz - deltas(measurement_vector(engine.run(ambient, health=health_from_vector(x))), nominal)
    return x, float(r @ W @ r)


@dataclass
class GPAResult:
    health: np.ndarray  # estimated health deltas, shape (..., n_health)
    covariance: np.ndarray  # of the estimate
    residual: np.ndarray  # dz - H x
    chi2: np.ndarray  # normalised residual sum of squares
    dof: int


class GasPathAnalysis:
    def __init__(self, H, noise_std, prior_std=None):
        """noise_std: measurement delta noise (same units as deltas). prior_std: optional health prior (ridge)."""
        self.H = np.asarray(H)
        self.noise = np.asarray(noise_std, dtype=float)
        self.prior = None if prior_std is None else np.asarray(prior_std, dtype=float)

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
        mask = np.ones(len(MEASUREMENTS), bool)
        x, cov, r, chi2 = self._solve(dz, mask)
        return GPAResult(x, cov, r, chi2, int(mask.sum() - len(HEALTH)))

    def isolate_sensor(self, dz):
        """For each snapshot, the sensor whose removal reduces the residual most, and the chi2 drop."""
        dz = np.atleast_2d(dz)
        full = self._solve(dz, np.ones(len(MEASUREMENTS), bool))[3]
        drops = []
        for k in range(len(MEASUREMENTS)):
            mask = np.ones(len(MEASUREMENTS), bool)
            mask[k] = False
            drops.append(full - self._solve(dz, mask)[3])
        drops = np.array(drops).T
        return drops.argmax(axis=1), drops.max(axis=1)

    def fingerprints(self):
        """Unit-norm measurement signature of each single fault, in noise-normalised space."""
        sig = {h: self.H[:, j] / self.noise for j, h in enumerate(HEALTH)}
        for k, m in enumerate(MEASUREMENTS):
            e = np.zeros(len(MEASUREMENTS))
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
