import math
from pathlib import Path

import pytest

from gtperf.cli import engine_from_datasheet
from gtperf.emissions import (
    MCPD_NEW_GT_NOX_NATURAL_GAS,
    ZELDOVICH_TA,
    DLECombustor,
    compliance_table,
    correct_to_15_o2,
    flame_temperature,
    lowest_compliant_load,
)
from gtperf.fuel import natural_gas

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture(scope="module")
def setup():
    _, _, engine = engine_from_datasheet(EXAMPLES / "sgt400_13mw.toml")
    base = engine.run()
    return engine, base, DLECombustor(natural_gas()).calibrate(base, 10.0, 5.0)


def test_unit_conversions():
    assert correct_to_15_o2(20.0, 15.0) == pytest.approx(20.0)
    assert correct_to_15_o2(10.0, 17.975) == pytest.approx(20.0, rel=1e-12)  # half the 15 % O2 margin
    e = DLECombustor(natural_gas())
    assert e.nox_ref * 46.0055 / 22.414 == pytest.approx(20.53, abs=0.01)  # 10 ppm NO2 is 20.5 mg/Nm3


def test_calibration_point_is_reproduced(setup):
    _, base, c = setup
    e = c.emissions(base)
    assert e["nox_ppm_15"] == pytest.approx(10.0) and e["co_ppm_15"] == pytest.approx(5.0)
    assert 1700 < e["flame_temperature_K"] < 1900
    assert e["nox_mg_15"] < MCPD_NEW_GT_NOX_NATURAL_GAS


def test_nox_follows_the_zeldovich_activation_temperature(setup):
    _, base, c = setup
    t = c.t_ref + 50.0
    hot = DLECombustor(natural_gas(), t_ref=c.t_ref, p3_ref=c.p3_ref, staging=False,
                       primary_fraction=c.primary_fraction)
    # find the premix fraction that gives a flame 50 K hotter, then compare with the Arrhenius factor
    from scipy.optimize import brentq
    f = brentq(lambda f: flame_temperature(c.attach(base), f) - t, 0.5, c.primary_fraction)
    ratio = hot.__class__(**{**hot.__dict__, "primary_fraction": f}).emissions(base)["nox_ppm_15"] / 10.0
    assert ratio == pytest.approx(math.exp(ZELDOVICH_TA * (1 / c.t_ref - 1 / t)), rel=1e-9)
    assert 1.6 < ratio < 2.0  # thermal NOx roughly doubles for 50 K near 1800 K


def test_staging_extends_emissions_compliant_turndown(setup):
    engine, _, c = setup
    loads = [1.0, 0.8, 0.6, 0.5, 0.4, 0.3, 0.25, 0.2]
    staged = compliance_table(engine, c, loads)
    unstaged = compliance_table(engine, DLECombustor(**{**c.__dict__, "staging": False}), loads)
    assert lowest_compliant_load(staged) <= 0.3 < lowest_compliant_load(unstaged)
    co = [r["co_mg_15"] for r in staged]
    assert co[-1] > 3 * co[0]  # below the staging stop the flame cools and CO climbs
