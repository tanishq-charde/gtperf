import numpy as np
import pytest

from gtperf.cli import offdesign_options
from gtperf.testbed import TwinShaftModel, fit_test_bed, predict, synthetic_campaign

TRUTH = np.array([39.5, 1560.0, 0.885, 0.86, 0.875])
START = [39.2, 1553.0, 0.89, 0.868, 0.868]
CONDITIONS = [("A", 5, 1.0), ("B", 15, 1.0), ("C", 30, 1.0), ("D", 15, 0.75), ("E", 15, 0.5), ("F", 25, 0.6)]
HELD_OUT = [("V1", 10, 0.85), ("V2", 35, 1.0)]


@pytest.fixture(scope="module")
def fitted():
    _, limits, controls = offdesign_options({}, 17.1)
    model = TwinShaftModel(17.1, engine_kw={"limits": limits, "controls": controls})
    points = synthetic_campaign(model.build(TRUTH), CONDITIONS + HELD_OUT, seed=4)
    return model, points, fit_test_bed(model, points[:6], START)


def test_multipoint_fit_recovers_hidden_parameters(fitted):
    _, _, cal = fitted
    assert np.all(np.abs((cal.x - TRUTH) / cal.std) < 3)
    assert 0.4 < cal.chi2 / cal.dof < 2.0  # the stated uncertainties explain the residuals


def test_identifiability_shows_turbine_efficiencies_are_confounded(fitted):
    """With these measurements (no inter-turbine pressure), the HP and power turbine efficiencies trade off
    almost perfectly: the fit says so through their correlation, rather than hiding it."""
    _, _, cal = fitted
    i, j = cal.names.index("hpt_eff"), cal.names.index("pt_eff")
    assert cal.correlation[i, j] < -0.9
    assert cal.std[cal.names.index("compressor_eff")] < 0.002
    assert cal.condition_number > 100


def test_held_out_points_are_predicted_within_uncertainty(fitted):
    model, points, cal = fitted
    z = predict(cal, model, points[6:])
    assert max(abs(v) for p in z.values() for v in p.values()) < 3.5
