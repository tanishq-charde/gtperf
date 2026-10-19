import numpy as np
import pytest
from test_cli_and_study import DATASHEET

from gtperf.cli import engine_from_datasheet
from gtperf.exhaust import ExhaustField, ExhaustProfileModel
from gtperf.maps import Health
from gtperf.passoff import RESULTS, Guarantee, PassOffAnalysis, analyse, average, validate
from gtperf.report import write_report
from gtperf.synthetic import Fault, TestPoint, default_instruments, generate_test

POINTS = [TestPoint(8.0, 1002.0, 0.78, 0.4), TestPoint(8.6, 1002.2, 0.76, 0.3)]


@pytest.fixture(scope="module")
def engine():
    return engine_from_datasheet(DATASHEET)[2]


@pytest.fixture(scope="module")
def faulty(engine):
    faults = [Fault("T_exh_07", "drift", 15.0), Fault("T_exh_03", "offset", 30.0), Fault("T_amb_3", "stuck"),
              Fault("P_elec", "spike", 300.0)]
    return generate_test(engine, POINTS, health=Health(0.994, -0.004), faults=faults, seed=4, duration_s=300)


def test_synthetic_log_shape(faulty):
    d = faulty.data
    assert len(d) == 600
    assert set(default_instruments()) <= set(d.columns)
    assert d["point"].unique().tolist() == [1, 2]


def test_validation_catches_every_injected_fault(faulty):
    rep = validate(faulty.data)
    assert set(rep.excluded) == {"T_exh_07", "T_exh_03", "T_amb_3"}
    assert "stuck" in rep.excluded["T_amb_3"]
    assert rep.spikes.get("P_elec", 0) >= 3
    assert rep.stable


def test_clean_data_raises_no_flags(engine):
    clean = generate_test(engine, POINTS, seed=11, duration_s=300)
    rep = validate(clean.data)
    assert rep.excluded == {}
    assert sum(rep.spikes.values()) <= 2  # the odd noise sample may trip the spike filter


def test_perfect_instruments_recover_truth_exactly(engine):
    """With no bias, negligible noise and a nominal engine, correction must return the model's reference values."""
    # a whisper of noise so constant channels are not mistaken for stuck sensors
    inst = {k: type(v)(v.unit, 0.0, 1e-9 if v.relative else 1e-6, v.relative) for k, v in default_instruments().items()}
    flat = ExhaustField(ExhaustProfileModel(), np.zeros(6), 0.0, 0.0, 0.0, 0.0)  # uniform exhaust plane
    test = generate_test(engine, POINTS, instruments=inst, seed=1, duration_s=120, exhaust=flat)
    rep = validate(test.data)
    pts = average(rep)
    value, _ = PassOffAnalysis(engine).correct([p.values for p in pts])
    # residual from the simulated grid/load fluctuation, which the 2-minute means do not fully average out
    assert value["power_MW"] == pytest.approx(test.truth["power_MW"], rel=1.5e-3)
    assert value["heat_rate"] == pytest.approx(test.truth["heat_rate"], rel=5e-4)
    assert value["exhaust_temp_C"] == pytest.approx(test.truth["exhaust_temp_C"], abs=0.05)
    assert value["exhaust_flow"] == pytest.approx(test.truth["exhaust_flow"], rel=2e-3)


@pytest.fixture(scope="module")
def result(engine, faulty):
    return analyse(faulty.data, engine, faulty.metadata["instruments"], Guarantee(12.65, 10500.0, 570.0),
                   mc_trials=400)


def test_result_within_uncertainty_of_truth(result, faulty):
    for r in RESULTS:
        assert abs(result.uncertainty.value[r] - faulty.truth[r]) <= result.uncertainty.expanded(r)


def test_gum_and_monte_carlo_agree(result):
    for r in RESULTS:
        assert np.std(result.uncertainty.mc[r], ddof=1) == pytest.approx(result.uncertainty.u[r], rel=0.15)


def test_budget_shares_sum_to_one(result):
    for r in RESULTS:
        assert sum(row[4] for row in result.uncertainty.budget[r]) == pytest.approx(1.0)
    labels = [row[0] for row in result.uncertainty.budget["power_MW"]]
    assert any(lab.startswith("Correction model") for lab in labels)


def test_conformity_verdicts(result):
    verdicts = {c["result"]: c["verdict"] for c in result.checks}
    assert verdicts == {"power_MW": "PASS", "heat_rate": "PASS", "exhaust_temp_C": "PASS"}
    assert result.valid


def test_tight_guarantee_fails(engine, faulty):
    res = analyse(faulty.data, engine, faulty.metadata["instruments"], Guarantee(power_MW_min=12.9), mc_trials=0)
    assert res.checks[0]["verdict"] == "FAIL"
    assert res.checks[0]["probability"] < 0.01


def test_speed_limited_test_is_flagged(engine):
    cold = generate_test(engine, [TestPoint(-15.0, 1010.0, 0.5)], seed=3, duration_s=120)
    res = analyse(cold.data, engine, cold.metadata["instruments"], mc_trials=0)
    assert not res.valid
    assert "control modes" in res.warnings[0]


def test_report_written(result, faulty, tmp_path):
    path = write_report(result, faulty.metadata, tmp_path, truth=faulty.truth, data=faulty.data)
    text = path.read_text()
    assert "Overall result: PASS" in text
    assert "T_exh_03" in text
    for fig in ("sensors.png", "uncertainty_budget.png", "monte_carlo.png"):
        assert (tmp_path / "figures" / fig).exists()
