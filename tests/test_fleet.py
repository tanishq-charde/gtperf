import numpy as np
import pytest
from test_cli_and_study import DATASHEET

from gtperf import Ambient, spc
from gtperf.cli import engine_from_datasheet
from gtperf.fleet import (
    BaselineModel,
    FleetFault,
    fleet_deltas,
    in_service_fleet,
    make_gpa,
    monitor_fleet,
    production_sequence,
    score,
)
from gtperf.gpa import (
    HEALTH,
    MEASUREMENTS,
    GasPathAnalysis,
    deltas,
    health_from_vector,
    influence_matrix,
    measurement_vector,
    nonlinear_fit,
)


@pytest.fixture(scope="module")
def engine():
    return engine_from_datasheet(DATASHEET)[2]


@pytest.fixture(scope="module")
def H(engine):
    return influence_matrix(engine)


# --- SPC ---------------------------------------------------------------------

def test_cusum_and_ewma_beat_shewhart_on_small_shift():
    rng = np.random.default_rng(0)
    x = np.r_[rng.normal(0, 1, 100), rng.normal(-1.0, 1, 100)]
    c, s = spc.phase1(x, 50)
    first = {name: next(i for i in ch.signals if i >= 100)
             for name, ch in [("shewhart", spc.individuals(x, c, s)), ("ewma", spc.ewma(x, c, s)),
                              ("cusum", spc.cusum(x, c, s))]}
    assert first["cusum"] < first["shewhart"] and first["ewma"] < first["shewhart"]


def test_phase1_ignores_an_outlier():
    rng = np.random.default_rng(1)
    x = rng.normal(10, 1, 50)
    c0, s0 = spc.phase1(x, 50)
    x[20] = 30
    c1, s1 = spc.phase1(x, 50)
    assert c1 == pytest.approx(c0, abs=0.1) and s1 == pytest.approx(s0, rel=0.25)


def test_western_electric_run_rule():
    x = np.r_[np.zeros(10), np.full(8, 0.5)]
    flags = spc.western_electric(x, 0.0, 1.0)
    assert flags.get(17) == 4


def test_cpk():
    x = np.array([9.0, 10.0, 11.0] * 10)
    assert spc.cpk_lower(x, 7.0) == pytest.approx((10 - 7) / (3 * x.std(ddof=1)))


# --- gas-path analysis ---------------------------------------------------------

def test_influence_matrix_signs(H):
    assert H.shape == (len(MEASUREMENTS), len(HEALTH))
    j = HEALTH.index("turbine_eff")
    assert H[MEASUREMENTS.index("power"), j] > 0  # better turbine, more power
    assert H[MEASUREMENTS.index("egt"), j] < 0  # and a cooler exhaust
    assert np.linalg.matrix_rank(H) == len(HEALTH)


def test_linear_gpa_recovers_health_exactly_from_linear_deltas(H):
    gpa = GasPathAnalysis(H, np.ones(6))
    x = np.array([-0.5, -0.3, 0.2, 0.4])
    est = gpa.estimate(H @ x)
    np.testing.assert_allclose(est.health[0], x, atol=1e-10)
    assert est.chi2[0] == pytest.approx(0, abs=1e-12)


def test_gpa_recovers_small_fault_from_engine_model(engine, H):
    x = np.array([0.0, 0.0, -0.5, 0.0])
    dz = deltas(measurement_vector(engine.run(health=health_from_vector(x))), measurement_vector(engine.run()))
    est = GasPathAnalysis(H, np.array([0.35, 0.45, 2.0, 0.35, 1.5, 0.08])).estimate(dz)
    assert est.health[0][2] == pytest.approx(-0.5, abs=0.05)


def test_sensor_fault_isolated_by_residual(H):
    gpa = GasPathAnalysis(H, np.array([0.35, 0.45, 2.0, 0.35, 1.5, 0.08]))
    dz = H @ np.array([0, -0.4, 0, 0]) + np.array([0, 0, 15.0, 0, 0, 0])  # engine fouling plus an EGT bias
    k, drop = gpa.isolate_sensor(dz)
    assert MEASUREMENTS[k[0]] == "egt"
    assert gpa.classify(np.array([0, 0, 10.0, 0, 0, 0]))[0][0] == "sensor:egt"


def test_nonlinear_fit_explains_a_large_fault(engine, H):
    x = np.array([0.0, 0.0, -2.5, 1.5])
    noise = np.array([0.35, 0.45, 2.0, 0.35, 1.5, 0.08]) / 5
    dz = deltas(measurement_vector(engine.run(health=health_from_vector(x))), measurement_vector(engine.run()))
    lin = GasPathAnalysis(H, noise).estimate(dz)
    x_nl, chi_nl = nonlinear_fit(engine, dz, np.diag(noise**2))
    assert chi_nl < 0.01 * lin.chi2[0]
    np.testing.assert_allclose(x_nl, x, atol=0.02)


# --- fleet ---------------------------------------------------------------------

def test_production_sequence(engine):
    p = production_sequence(engine, n=30, events=[])
    assert len(p) == 30
    assert p["power_MW"].mean() == pytest.approx(12.9, rel=0.02)


def test_baseline_model_matches_engine(engine):
    base = BaselineModel(engine, t_range=(0, 30))
    for t, p, rh in [(7.3, 1003.0, 0.8), (22.6, 1021.0, 0.45)]:
        z = measurement_vector(engine.run(Ambient(t + 273.15, p * 100, rh)))
        dz = deltas(z, base.expected([t], [p], [rh])[0])
        assert np.abs(dz).max() < 0.05


@pytest.fixture(scope="module")
def small_fleet(engine):
    faults = [FleetFault(1, 120, "engine", "turbine_eff", "blade damage", -1.5),
              FleetFault(2, 100, "sensor", "sensor:egt", "thermocouple offset", 12.0)]
    return in_service_fleet(engine, n_engines=3, days=240, interval=3, faults=faults, seed=5)


def test_fleet_shape_and_truth(small_fleet):
    data, washes, faults = small_fleet
    assert data["engine"].nunique() == 3
    after = data.query("engine == 1 and day >= 120")["true_turbine_eff"]
    before = data.query("engine == 1 and day < 120")["true_turbine_eff"]
    assert after.mean() < before.mean() - 1.0


def test_monitoring_detects_and_isolates(engine, small_fleet):
    data, washes, faults = small_fleet
    m = monitor_fleet(engine, data, washes, BaselineModel(engine, t_range=(-10, 35)), make_gpa(engine))
    sc, false, far = score(m.alarms, faults, 3, 240)
    assert sc["detected_day"].notna().all()
    assert sc["correct"].all()
    assert (sc["delay_days"] < 60).all()


def test_fleet_deltas_zero_for_nominal_engine(engine):
    import pandas as pd

    base = BaselineModel(engine, t_range=(0, 30))
    rows = []
    for t in (5.0, 15.0, 25.0):
        z = measurement_vector(engine.run(Ambient(t + 273.15, 101000.0, 0.7)))
        rows.append({"t_amb": t, "p_amb": 1010.0, "rh": 0.7, **dict(zip(MEASUREMENTS, z))})
    dz = fleet_deltas(pd.DataFrame(rows), base)
    assert dz.abs().to_numpy().max() < 0.05
