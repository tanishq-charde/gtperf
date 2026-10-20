"""Maps calculated by stage stacking reproduce their design point and behave like real characteristics."""

import math

import pytest

from gtperf.aero_environment import NOxSeverity
from gtperf.app import core
from gtperf.cycle import ISO
from gtperf.mapgen import CompressorStages, TurbineStages, compressor_map, turbine_map


def test_compressor_map_design_point_and_surge():
    m = compressor_map(17.1, 0.89, CompressorStages())
    g, pr, eff = m.point(1.0, m.beta_at_design())
    assert (g, pr, eff) == pytest.approx((1.0, 17.1, 1.0), abs=1e-6)
    assert m.info["design_pr_stacked"] == pytest.approx(17.1, rel=1e-9)
    assert 0.1 < m.surge_margin_at(1.0, m.beta_at_design()) < 0.4
    surge = {round(n, 2): pr_s for n, _, pr_s, _, _ in m.info["surge"]}
    assert surge[0.9] < surge[0.95] < surge[1.0]  # the surge line rises with speed


def test_more_loading_means_less_surge_margin():
    light = compressor_map(17.1, 0.89, CompressorStages(loading=0.40))
    heavy = compressor_map(17.1, 0.89, CompressorStages(stages=8, loading=0.5))
    assert heavy.surge_margin_at(1.0, heavy.beta_at_design()) < light.surge_margin_at(1.0, light.beta_at_design())


def test_turbine_map_capacity_and_efficiency():
    t = turbine_map(4.0, 0.88, TurbineStages(2, 1.5, 0.6, 0.5), 1550.0)
    assert t.capacity(1.0, 4.0) == pytest.approx(1.0, abs=1e-6)
    assert t.efficiency(1.0, 4.0) == pytest.approx(1.0, abs=1e-6)
    assert t.capacity(1.0, 1.5) < t.capacity(1.0, 2.5) < t.capacity(1.0, 4.0)  # unchoking at low PR
    assert t.efficiency(0.6, 4.0) < t.efficiency(1.0, 4.0)  # off-design blade-speed ratio costs efficiency


def test_design_engine_runs_on_its_own_maps():
    model = core.design_engine({})
    assert model.engine.compressor_map.source == "stage stacking"
    iso = model.engine.run(ISO)
    assert iso.electrical_power / 1e6 == pytest.approx(12.89, abs=0.02)
    low = model.engine.run(ISO, load=0.3)
    assert low.electrical_power == pytest.approx(0.3 * iso.electrical_power, rel=1e-6)


def test_nox_severity():
    def at(p, t):
        return type("R", (), {"stations": {"3": type("S", (), {"p": p, "t": t})()}})

    nox = NOxSeverity(32.0)
    assert nox.ei(at(2965e3, 826.0)) == pytest.approx(32.0 * math.exp((6.29 - 0.634) / 53.2))
    assert nox.ei(at(2965e3, 826.0 + 194.0)) / nox.ei(at(2965e3, 826.0)) == pytest.approx(math.e)
    assert NOxSeverity(32.0, war=0.02).ei(at(2965e3, 826.0)) < nox.ei(at(2965e3, 826.0))
