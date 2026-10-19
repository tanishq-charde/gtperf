"""Engine-to-engine (build) scatter estimated from production pass-off history.

Each production pass-off result is the engine's true performance plus measurement
error, so across many engines

    var(observed) = var(engine-to-engine) + var(measurement)

The measurement variance comes from the pass-off uncertainty budget (instrument
classes and certificates, random error, correction), so the build scatter is the
difference. Two estimators:

* `estimate_scatter`: one result (e.g. corrected power). The total variance has a
  chi-square confidence interval with n - 1 degrees of freedom; the measurement
  variance is treated as known, so the interval for the build scatter is the
  total-variance interval minus it. When the observed variance is not larger than
  the measurement variance the scatter is not resolved: the estimate is reported
  as zero with an upper bound only.
* `estimate_health_scatter`: component health (compressor flow and efficiency,
  turbine efficiency and capacity). The covariance of the gas-path measurement
  deltas is S = H diag(sigma^2) H' + R, with H the influence matrix and R the
  measurement covariance. sigma^2 is found by non-negative least squares on the
  distinct elements of S - R (the non-negativity is the guard against negative
  variance estimates), each weighted by its sampling standard error; the interval
  is a bootstrap over engines.

Assumptions: engines are independent draws from one build population (assigned
causes such as damaged engines or test-bed faults removed first), and every
pass-off carries an independent measurement error of the stated size. If the
test-bed calibration errors are shared by many consecutive tests, part of the
measurement variance does not appear between engines and the build scatter is
underestimated; the estimate is then a lower bound.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import stats
from scipy.optimize import nnls

from .gpa import HEALTH
from .passoff import MODEL_INPUTS, ProductionScatter


@dataclass
class ScatterEstimate:
    """Engine-to-engine standard deviation with its confidence interval."""

    name: str
    value: float
    lower: float
    upper: float
    total_std: float
    measurement_std: float
    n: int
    confidence: float
    resolved: bool
    method: str = ""

    def contains(self, x):
        return self.lower <= x <= self.upper


def estimate_scatter(values, u_meas, confidence=0.95, name=""):
    """Build scatter of one result from n production results and their measurement standard uncertainty.

    `u_meas` is a scalar or one value per engine (their mean square is used).
    """
    x = np.asarray(values, float)
    n = len(x)
    s2 = float(x.var(ddof=1))
    m2 = float(np.mean(np.square(u_meas)))
    a = 1 - confidence
    lo_tot = (n - 1) * s2 / stats.chi2.ppf(1 - a / 2, n - 1)
    hi_tot = (n - 1) * s2 / stats.chi2.ppf(a / 2, n - 1)
    var_e = s2 - m2
    resolved = var_e > 0
    return ScatterEstimate(name, math.sqrt(max(var_e, 0.0)), math.sqrt(max(lo_tot - m2, 0.0)),
                           math.sqrt(max(hi_tot - m2, 0.0)), math.sqrt(s2), math.sqrt(m2), n, confidence, resolved,
                           "total variance minus measurement variance, chi-square interval")


def _moment_fit(dz, H, R):
    """Non-negative weighted least squares for diag health variances from the delta covariance."""
    n = len(dz)
    S = np.cov(dz, rowvar=False)
    m = S.shape[0]
    rows, rhs, w = [], [], []
    for i in range(m):
        for j in range(i, m):
            rows.append(H[i] * H[j])
            rhs.append(S[i, j] - R[i, j])
            # standard error of a sample covariance (normal data)
            w.append(1.0 / math.sqrt((S[i, i] * S[j, j] + S[i, j] ** 2) / (n - 1)))
    A, b, w = np.array(rows), np.array(rhs), np.array(w)
    var, _ = nnls(A * w[:, None], b * w)
    return var


def estimate_health_scatter(dz, H, noise_std, confidence=0.95, n_boot=1000, seed=0):
    """Build scatter of each health parameter from production gas-path deltas.

    dz: (n_engines, n_measurements) deltas against nominal (as `fleet.production_sequence`).
    H: influence matrix (measurements x health), noise_std: measurement standard
    deviations in delta units. Returns {health name: ScatterEstimate} in % or points.
    """
    dz = np.asarray(dz, float)
    H = np.asarray(H, float)
    R = np.diag(np.square(noise_std))
    var = _moment_fit(dz, H, R)
    rng = np.random.default_rng(seed)
    boot = np.empty((n_boot, H.shape[1]))
    for b in range(n_boot):
        boot[b] = _moment_fit(dz[rng.integers(0, len(dz), len(dz))], H, R)
    a = 1 - confidence
    lo, hi = np.quantile(np.sqrt(boot), [a / 2, 1 - a / 2], axis=0)
    out = {}
    for k, h in enumerate(HEALTH):
        out[h] = ScatterEstimate(h, float(np.sqrt(var[k])), float(lo[k]), float(hi[k]), float("nan"), float("nan"),
                                 len(dz), confidence, bool(var[k] > 0),
                                 "delta covariance minus measurement covariance, NNLS, bootstrap interval")
    return out


def production_scatter(health_estimates, conservative=False):
    """ProductionScatter (fractions) for the pass-off correction-model uncertainty from health estimates.

    `conservative=True` uses the upper confidence bounds instead of the point estimates.
    """
    pick = (lambda e: e.upper) if conservative else (lambda e: e.value)
    e = health_estimates
    n = next(iter(e.values())).n
    kind = "upper bounds" if conservative else "point estimates"
    return ProductionScatter(pick(e["compressor_flow"]) / 100, pick(e["compressor_eff"]) / 100,
                             pick(e["turbine_eff"]) / 100, pick(e["turbine_capacity"]) / 100,
                             source=f"estimated from {n} production pass-off tests ({kind})")


def passoff_measurement_noise(result):
    """Measurement standard deviation of one pass-off, per gas-path measurement (`gpa.MEASUREMENTS` order).

    Power and exhaust temperature come straight from the pass-off GUM budget, less
    the correction-model scatter terms (those belong to the engine, not the
    measurement). Fuel flow combines the fuel meter's calibration uncertainty with
    the ambient-correction part of the power budget (the same correction applies).
    The diagnostic channels (P3, T3, N_gg) use their instrument uncertainty plus a
    simple correction term: P3 like power, T3 in proportion to inlet temperature,
    N_gg with the square root of it (corrected speed N / sqrt(T)).
    Units: % for power, fuel flow, P3 and N_gg; K for EGT and T3.
    """
    unc = result.uncertainty
    inst = result.instruments
    clean = result.report.clean
    model_var = {r: sum(d[r] ** 2 for _, d in unc.model_terms) for r in unc.u}
    p = unc.value["power_MW"]
    u_p = math.sqrt(max(unc.u["power_MW"] ** 2 - model_var["power_MW"], 0.0))
    u_egt = math.sqrt(max(unc.u["exhaust_temp_C"] ** 2 - model_var["exhaust_temp_C"], 0.0))
    corr = math.sqrt(sum(row[3] ** 2 for row in unc.budget["power_MW"] if row[0].split(" ")[0] in MODEL_INPUTS))
    corr_rel = corr / p
    td = result.test_date
    u_fuel = inst["W_fuel"].standard_uncertainty(clean["W_fuel"].mean(), td)
    p3 = clean["P3"].mean()
    u_p3 = inst["P3"].standard_uncertainty(p3, td) / p3
    t_amb = np.mean([pt.values["T_amb"] for pt in result.points]) + 273.15
    u_tamb = result.u_sys["T_amb"]
    t3 = clean["T3"].mean()
    u_t3 = math.hypot(inst["T3"].standard_uncertainty(t3, td), (t3 + 273.15) / t_amb * u_tamb)
    n_gg = clean["N_gg"].mean()
    u_ngg = math.hypot(inst["N_gg"].standard_uncertainty(n_gg, td) / n_gg, 0.5 * u_tamb / t_amb)
    return np.array([100 * u_p / p, 100 * math.hypot(u_fuel, corr_rel), u_egt, 100 * math.hypot(u_p3, corr_rel),
                     u_t3, 100 * u_ngg])
