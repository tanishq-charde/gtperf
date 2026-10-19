"""Multi-point model calibration to test-bed data, with parameter uncertainty and identifiability.

A datasheet gives one operating point, so it can pin down only as many model parameters as it has
numbers. A test-bed campaign gives many points across load and ambient, each with several measurements,
and the calibration becomes a weighted least-squares problem:

    minimise  sum over points and measurements of ((model - measured) / u)**2  +  prior terms

where u is each measurement's standard uncertainty. The result carries what a single-point fit cannot:

- the parameter covariance (J^T J)^-1 from the weighted Jacobian at the solution, so every fitted value has
  an uncertainty and the correlations show which parameters the data cannot separate;
- the singular values of the weighted Jacobian, whose spread (condition number) measures identifiability;
- the chi-square of the fit against its degrees of freedom, a check that the stated uncertainties and the
  model structure are consistent with the data;
- prediction errors at held-out validation points.

Each test point is set by measured electrical output at measured ambient conditions, as on a real test
bed, and compared on bellmouth air flow, fuel flow, compressor delivery pressure and temperature, exhaust
temperature and gas generator speed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import least_squares

from .components import Combustor, Compressor, Turbine
from .cycle import ISO, Ambient, Cooling, TwinShaftCycle
from .fuel import natural_gas
from .offdesign import TwinShaftEngine

MEASUREMENTS = ("W_air", "W_fuel", "P3", "T3", "T_exh", "N_gg")
OPTIONAL = ("P45",)  # inter-turbine pressure, fitted on development engines
UNITS = {"W_air": "kg/s", "W_fuel": "kg/s", "P3": "bar", "T3": "K", "T_exh": "K", "N_gg": "-", "P45": "bar"}
# Representative test-bed standard uncertainties (k = 1): relative for flows and pressures, absolute otherwise
DEFAULT_UNCERTAINTY = {"W_air": 0.005, "W_fuel": 0.0035, "P3": 0.003, "T3": 1.0, "T_exh": 2.0, "N_gg": 0.002,
                       "P45": 0.003}
RELATIVE = {"W_air", "W_fuel", "P3", "P45"}


@dataclass
class TestPoint:
    """One stabilised test-bed point: ambient, the electrical output it was set to, and the measurements."""

    name: str
    ambient: Ambient
    power: float  # W, the setting
    measured: dict  # measurement name -> value (SI, N_gg relative to design)
    uncertainty: dict = field(default_factory=lambda: dict(DEFAULT_UNCERTAINTY))

    def sigma(self, key):
        u = self.uncertainty[key]
        return u * abs(self.measured[key]) if key in RELATIVE else u


def observe(result):
    """The measurement set from a model result."""
    st = result.stations
    return {"W_air": result.air_flow, "W_fuel": result.fuel_flow, "P3": st["3"].p / 1e5, "T3": st["3"].t,
            "T_exh": result.exhaust.t, "N_gg": result.info["mechanical_speed"], "P45": st["45"].p / 1e5}


@dataclass(frozen=True)
class TwinShaftModel:
    """Parameterised twin-shaft engine for calibration. Fixed assumptions go in the fields; the fitted
    parameters are design air flow, TIT and the three component efficiencies."""

    pressure_ratio: float
    cooling: Cooling = Cooling(ngv=0.07, rotor=0.05, pt=0.03)
    combustor_dp: float = 0.04
    mechanical_efficiency: float = 0.99
    generator_efficiency: float = 0.98
    fuel: object = None
    engine_kw: dict = field(default_factory=dict)

    PARAMETERS = ("air_flow", "tit", "compressor_eff", "hpt_eff", "pt_eff")

    def build(self, x):
        w, tit, eta_c, eta_hpt, eta_pt = x
        cycle = TwinShaftCycle(
            Compressor(self.pressure_ratio, eta_c),
            Combustor(self.fuel or natural_gas(), tit, dp_frac=self.combustor_dp),
            Turbine(eta_hpt),
            Turbine(eta_pt),
            cooling=self.cooling,
            mechanical_efficiency=self.mechanical_efficiency,
            generator_efficiency=self.generator_efficiency,
        )
        return TwinShaftEngine(cycle, w, ISO, **self.engine_kw)


@dataclass
class MultiPointCalibration:
    names: tuple
    x: np.ndarray
    covariance: np.ndarray
    singular_values: np.ndarray
    chi2: float
    dof: int
    residuals: dict  # point name -> {measurement: normalised residual}
    engine: object

    @property
    def std(self):
        return np.sqrt(np.diag(self.covariance))

    @property
    def correlation(self):
        s = self.std
        return self.covariance / np.outer(s, s)

    @property
    def condition_number(self):
        return self.singular_values[0] / self.singular_values[-1]

    def table(self, truth=None):
        head = "| Parameter | Fitted | Standard uncertainty |" + (" Truth | Error / u |" if truth is not None else "")
        rows = [head, "|---|---:|---:|" + ("---:|---:|" if truth is not None else "")]
        for i, n in enumerate(self.names):
            row = f"| {n} | {self.x[i]:.5g} | {self.std[i]:.2g} |"
            if truth is not None:
                row += f" {truth[i]:.5g} | {(self.x[i] - truth[i]) / self.std[i]:+.2f} |"
            rows.append(row)
        return "\n".join(rows)


def _residuals(model, x, points, guesses):
    engine = model.build(x)
    out = []
    for p in points:
        r = engine.run(p.ambient, power=p.power, guess=guesses.get(p.name))
        guesses[p.name] = r.info["solution"]
        obs = observe(r)
        out.extend((obs[k] - p.measured[k]) / p.sigma(k) for k in p.measured)
    return np.array(out)


def fit_test_bed(model, points, x0, scale=None, prior=None):
    """Weighted least-squares calibration of model parameters to test points.

    `scale` sets the parameters' typical magnitudes for the optimiser. `prior` optionally maps a parameter
    index to (mean, standard deviation), added as a residual: the way to keep a parameter the data cannot
    see at a sensible value without pretending it was measured.
    """
    x0 = np.asarray(x0, float)
    scale = np.asarray(scale if scale is not None else np.abs(x0), float)
    prior = prior or {}
    guesses = {}

    def f(z):
        x = z * scale
        res = _residuals(model, x, points, guesses)
        extra = [(x[i] - m) / s for i, (m, s) in prior.items()]
        return np.concatenate([res, extra])

    sol = least_squares(f, x0 / scale, method="lm", x_scale="jac", xtol=1e-10, ftol=1e-12)
    x = sol.x * scale
    J = sol.jac / scale  # d(residual)/dx
    _, sv, _ = np.linalg.svd(J, full_matrices=False)
    cov = np.linalg.inv(J.T @ J)
    engine = model.build(x)
    per_point = {}
    for p in points:
        obs = observe(engine.run(p.ambient, power=p.power))
        per_point[p.name] = {k: (obs[k] - p.measured[k]) / p.sigma(k) for k in p.measured}
    n = sum(len(v) for v in per_point.values()) + len(prior)
    return MultiPointCalibration(tuple(model.PARAMETERS), x, cov, sv, float(np.sum(sol.fun**2)), n - len(x),
                                 per_point, engine)


def predict(calibration, model, points, step=1e-4):
    """Prediction errors of a calibrated model at (held-out) points, normalised by the combined uncertainty
    of the measurement and of the prediction (parameter covariance propagated through the model)."""
    x = calibration.x
    base = [observe(calibration.engine.run(p.ambient, power=p.power)) for p in points]
    sens = []
    for i in range(len(x)):
        xi = x.copy()
        xi[i] += step * abs(x[i])
        eng = model.build(xi)
        sens.append([observe(eng.run(p.ambient, power=p.power)) for p in points])
    out = {}
    for j, p in enumerate(points):
        out[p.name] = {}
        for k in p.measured:
            g = np.array([(sens[i][j][k] - base[j][k]) / (step * abs(x[i])) for i in range(len(x))])
            u_pred2 = g @ calibration.covariance @ g
            out[p.name][k] = (base[j][k] - p.measured[k]) / np.sqrt(p.sigma(k) ** 2 + u_pred2)
    return out


def synthetic_campaign(truth_engine, conditions, uncertainty=None, seed=0, measurements=MEASUREMENTS):
    """Test points from a hidden 'true' engine. `conditions` is a list of (name, ambient temperature °C,
    load fraction of base load). Measurements get random errors at their stated uncertainties."""
    rng = np.random.default_rng(seed)
    uncertainty = uncertainty or dict(DEFAULT_UNCERTAINTY)
    points = []
    for name, t_c, load in conditions:
        amb = Ambient(t_c + 273.15, ISO.p, ISO.rh)
        r = truth_engine.run(amb, load=load)
        obs = observe(r)
        p = TestPoint(name, amb, r.electrical_power, {}, dict(uncertainty))
        for k in measurements:
            p.measured[k] = obs[k]  # sigma() of a relative measurement needs a value to scale
            p.measured[k] = obs[k] + rng.normal(0.0, p.sigma(k))
        points.append(p)
    return points
