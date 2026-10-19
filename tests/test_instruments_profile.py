"""Instrument list from standards and certificates, build scatter from production data, exhaust profile model."""

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest
from test_cli_and_study import DATASHEET

from gtperf.cli import engine_from_datasheet
from gtperf.exhaust import (
    N_RAKE,
    RAKE_ANGLES_DEG,
    BurnerFault,
    ExhaustProfileModel,
    burner_check,
    cold_spot,
    profile_uncertainty,
    rake_survey,
)
from gtperf.fleet import BUILD_SCATTER, production_sequence
from gtperf.gpa import HEALTH, MEASUREMENTS, influence_matrix
from gtperf.instruments import calibration_status, load_instruments, out_of_date
from gtperf.passoff import ProductionScatter, analyse, apply_corrections
from gtperf.report import write_report
from gtperf.scatter import estimate_health_scatter, estimate_scatter, production_scatter
from gtperf.synthetic import DEFAULT_TEST_DATE, TestPoint, generate_test

LATE = date(2027, 6, 1)  # after several certificates fall due


@pytest.fixture(scope="module")
def engine():
    return engine_from_datasheet(DATASHEET)[2]


@pytest.fixture(scope="module")
def instruments():
    return load_instruments()


# --- A1: instruments ------------------------------------------------------------

def test_every_instrument_has_a_source(instruments):
    assert len(instruments) == 25
    for tag, inst in instruments.items():
        assert inst.components, tag
        for c in inst.components:
            assert c.source.startswith("https://"), (tag, c.name)
            assert c.standard and c.formula, (tag, c.name)
        assert inst.bias > 0


def test_class_tolerance_conversion(instruments):
    # IEC 60584-1 type K class 1 at 550 degC: 0.004 * 550 = 2.2 K, rectangular
    assert instruments["T_exh_01"].standard_uncertainty(550.0) == pytest.approx(2.2 / math.sqrt(3))
    # below 375 degC the fixed 1.5 K applies
    assert instruments["T3"].standard_uncertainty(300.0) == pytest.approx(1.5 / math.sqrt(3))
    # IEC 60751 class A at 15 degC without certificates (past their due date): (0.15 + 0.03) / sqrt(3)
    assert instruments["T_amb_1"].standard_uncertainty(15.0, LATE) == pytest.approx(0.18 / math.sqrt(3))


def test_certificate_overrides_class_tolerance(instruments):
    fuel = instruments["W_fuel"]
    # in date: certificate U = 0.20 % (k = 2), reading corrected by the +0.12 % as-found error
    assert fuel.standard_uncertainty(0.8, DEFAULT_TEST_DATE) == pytest.approx(0.0010)
    assert fuel.correction(DEFAULT_TEST_DATE) == pytest.approx(0.0012)
    assert fuel.corrected(1.0012, DEFAULT_TEST_DATE) == pytest.approx(1.0)
    # out of date: class tolerance 0.25 % of rate, rectangular, no correction
    assert fuel.standard_uncertainty(0.8, LATE) == pytest.approx(0.0025 / math.sqrt(3))
    assert fuel.correction(LATE) == 0.0
    rtd = instruments["T_amb_2"]
    assert rtd.standard_uncertainty(15.0, DEFAULT_TEST_DATE) == pytest.approx(0.025)
    df = pd.DataFrame({"T_amb_2": [10.0, 11.0], "W_fuel": [0.8012, 0.8012]})
    out, applied = apply_corrections(df, {k: instruments[k] for k in df}, DEFAULT_TEST_DATE)
    assert out["T_amb_2"].tolist() == pytest.approx([10.05, 11.05])
    assert out["W_fuel"].iloc[0] == pytest.approx(0.8012 / 1.0012)
    assert set(applied) == {"T_amb_2", "W_fuel"}


def test_out_of_date_calibration_is_flagged(instruments, engine):
    assert out_of_date(instruments, DEFAULT_TEST_DATE) == ["N_gg"]  # diagnostic channel, due 2026-06-30
    late = out_of_date(instruments, LATE)
    assert {"W_fuel", "P_elec", "T_amb_1", "N_gg"} <= set(late)
    rows = [r for r in calibration_status(instruments, date(2025, 1, 1)) if r["tag"] == "W_fuel"]
    assert rows[0]["status"] == "calibrated after the test"

    test = generate_test(engine, [TestPoint(10.0, 1010.0, 0.6, 0.2)], seed=3, duration_s=120, test_date=LATE)
    res = analyse(test.data, engine, test.metadata["instruments"], mc_trials=0, test_date=LATE)
    assert not res.valid
    assert any("W_fuel" in w and "out of date" in w for w in res.warnings)
    assert "W_fuel" not in res.corrections
    ok = analyse(test.data, engine, test.metadata["instruments"], mc_trials=0, test_date=DEFAULT_TEST_DATE)
    assert ok.valid and any("N_gg" in n for n in ok.notes)


# --- A2: build scatter --------------------------------------------------------------

NOISE = np.array([0.06, 0.10, 0.7, 0.04, 1.0, 0.01])


@pytest.fixture(scope="module")
def production(engine):
    return production_sequence(engine, n=200, events=[], noise=NOISE, seed=17)


def test_health_scatter_recovers_truth(engine, production):
    H = influence_matrix(engine)
    est = estimate_health_scatter(production[[f"d_{m}" for m in MEASUREMENTS]].to_numpy(), H, NOISE, n_boot=400)
    for h, truth in zip(HEALTH, BUILD_SCATTER):
        assert est[h].contains(truth), (h, est[h], truth)
        assert est[h].lower < est[h].value < est[h].upper
    sc = production_scatter(est)
    assert sc.turbine_eff == pytest.approx(est["turbine_eff"].value / 100)
    assert "estimated from 200" in sc.source


def test_power_scatter_recovers_truth(engine, production):
    H = influence_matrix(engine)
    p = production["power_MW"].mean()
    truth = np.sqrt(np.sum((H[0] * BUILD_SCATTER) ** 2)) / 100 * p
    est = estimate_scatter(production["power_MW"], NOISE[0] / 100 * p)
    assert est.resolved and est.contains(truth)
    assert est.value < est.total_std  # measurement variance was removed


def test_scatter_guard_against_negative_variance():
    rng = np.random.default_rng(0)
    x = rng.normal(0, 1.0, 50)  # pure measurement noise
    est = estimate_scatter(x, 1.5)
    assert not est.resolved
    assert est.value == 0.0 and est.lower == 0.0
    assert est.upper >= 0.0


# --- B: exhaust profile ---------------------------------------------------------------

MODEL = ExhaustProfileModel()


def test_swirl_rotates_cold_spot_by_modelled_angle():
    field = MODEL.sample(np.random.default_rng(5), BurnerFault(3, "lean", 0.7))
    a, b = field.cold_spot_deg(0.5), field.cold_spot_deg(1.0)
    modelled = MODEL.swirl_deg_per_load * 0.5
    assert (b - a) % 360 == pytest.approx(modelled, abs=1.5)
    # and the cold spot sits on the faulty burner's streak centre
    centre = (MODEL.burner_angles_deg()[2] + MODEL.swirl_deg(1.0, field.swirl0_deg)) % 360
    assert abs((b - centre + 180) % 360 - 180) < 3.0


def test_field_mean_is_the_cycle_exhaust_temperature():
    field = MODEL.sample(np.random.default_rng(2))
    theta, rhat, dev = field.grid(0.8, n_theta=721, n_r=201)
    h = MODEL.hub_tip_ratio
    w = h + rhat * (1 - h)  # area weight
    radial_mean = np.trapezoid(dev * w[:, None], rhat, axis=0) / np.trapezoid(w, rhat)
    assert np.mean(radial_mean[:-1]) == pytest.approx(0.0, abs=1e-3)


def test_profile_uncertainty_matches_rake_error_against_true_mean():
    """MC prediction from the priors versus rake errors of independently drawn engines against the exact mean."""
    valid = np.ones(N_RAKE, bool)
    valid[[2, 6]] = False
    pred = profile_uncertainty(MODEL, 1.0, valid, n=4000, seed=1)
    rng = np.random.default_rng(99)
    errors = []
    for _ in range(600):
        f = MODEL.sample(rng)
        errors.append(f.rake(1.0)[valid].mean())  # deviation from the mass-weighted mean (exactly 0)
    errors = np.array(errors)
    assert np.std(errors, ddof=1) == pytest.approx(pred.u, rel=0.12)
    assert np.mean(errors) == pytest.approx(pred.correction, abs=3 * pred.u / np.sqrt(600) + 0.02)
    assert pred.u > profile_uncertainty(MODEL, 1.0, None, n=4000).u  # losing thermocouples hurts


def _tc_bias(rng):
    return rng.uniform(-2.2, 2.2, N_RAKE)  # IEC 60584-1 class 1 at 550 degC


def test_burner_fault_detected_and_tracked():
    rng = np.random.default_rng(8)
    for burner in (1, 4):
        field = MODEL.sample(rng, BurnerFault(burner, "lean", 0.7))
        survey = rake_survey(field, [0.5, 1.0], 550.0, tc_bias=_tc_bias(rng), seed=burner)
        check = burner_check(survey.readings, MODEL)
        assert check.fault, check.verdict
        assert all(s.burner == burner for s in check.spots)
        assert check.rotation_deg == pytest.approx(check.modelled_rotation_deg, abs=8.0)
    blocked = MODEL.sample(rng, BurnerFault(2, "blocked"))
    assert cold_spot(blocked.rake(1.0) + 550.0, MODEL, 1.0).flagged


def test_clean_engines_and_sensor_faults_not_flagged():
    rng = np.random.default_rng(21)
    for i in range(40):
        field = MODEL.sample(rng)
        survey = rake_survey(field, [0.5, 1.0], 550.0, tc_bias=_tc_bias(rng), seed=i)
        assert not burner_check(survey.readings, MODEL).fault, i
    # one thermocouple reading 30 K low is a sensor fault: a single position, not a pair
    field = MODEL.sample(rng)
    bias = _tc_bias(rng)
    bias[4] -= 30.0
    survey = rake_survey(field, [0.5, 1.0], 550.0, tc_bias=bias, seed=1)
    assert not burner_check(survey.readings, MODEL).fault


def test_lean_burner_in_passoff_is_warned_not_hidden(engine):
    test = generate_test(engine, [TestPoint(12.0, 1012.0, 0.6, 0.2)], seed=6, duration_s=120,
                         burner_fault=BurnerFault(5, "lean", 0.75))
    res = analyse(test.data, engine, test.metadata["instruments"], mc_trials=0, test_date=DEFAULT_TEST_DATE)
    assert res.report.burner.flagged
    assert not res.valid and any("burner" in w for w in res.warnings)
    # the cold thermocouples are kept: they are reading a real cold spot
    cold = [f"T_exh_{i + 1:02d}" for i in res.report.burner.thermocouples]
    assert not set(cold) & set(res.report.excluded)


def test_report_lists_sources_and_profile(engine, tmp_path):
    test = generate_test(engine, [TestPoint(10.0, 1010.0, 0.6, 0.2)] * 2, seed=2, duration_s=120)
    res = analyse(test.data, engine, test.metadata["instruments"], mc_trials=200,
                  test_date=test.metadata["test_date"], scatter=ProductionScatter(0.003, 0.0015, 0.0012, 0.003, "test"))
    text = write_report(res, test.metadata, tmp_path).read_text()
    assert "Instrument uncertainty sources" in text
    assert "IEC 60584-1" in text and "https://" in text
    assert "out of date" in text  # the diagnostic speed channel
    assert "rake profile sampling" in text
    assert RAKE_ANGLES_DEG.size == N_RAKE
