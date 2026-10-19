import math
import tomllib
from pathlib import Path

import pytest

from gtperf import Ambient, Combustor, Compressor, SimpleCycle, Turbine, natural_gas
from gtperf.calibrate import calibrate_twin_shaft
from gtperf.cycle import TwinShaftCycle
from gtperf.fuel import Fuel
from gtperf.maps import CompressorMap, Health, ellipse_capacity
from gtperf.offdesign import SingleShaftEngine, TwinShaftEngine
from gtperf.site import Site, correction_curves, isa_pressure, site_performance

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture(scope="module")
def twin():
    cycle = TwinShaftCycle(
        Compressor(17.1, 0.89), Combustor(natural_gas(), 1550.0), Turbine(0.86), Turbine(0.86), cooling_fraction=0.15
    )
    return TwinShaftEngine(cycle, 39.0)


@pytest.fixture(scope="module")
def single():
    cycle = SimpleCycle(Compressor(16.8, 0.90), Combustor(natural_gas(), 1523.15), Turbine(0.89), cooling_fraction=0.15)
    return SingleShaftEngine(cycle, 39.0)


# --- maps ---------------------------------------------------------------

def test_compressor_map_passes_through_design():
    m = CompressorMap(17.1)
    assert m.pressure_ratio(1.0, 1.0) == pytest.approx(17.1, rel=1e-12)
    assert m.efficiency(1.0, 1.0) == pytest.approx(1.0)
    assert m.surge_margin(1.0, 1.0) > 0.1


def test_speed_line_pressure_ratio_falls_towards_choke():
    m = CompressorMap(17.1)
    prs = [m.pressure_ratio(0.95, g) for g in (0.88, 0.90, 0.92, 0.94)]
    assert prs == sorted(prs, reverse=True)


def test_ellipse_law_limits():
    assert ellipse_capacity(1.0001) < 0.02
    assert ellipse_capacity(20.0) == pytest.approx(1.0, abs=0.002)


# --- matching -----------------------------------------------------------

@pytest.mark.parametrize("engine_name", ["twin", "single"])
def test_offdesign_reproduces_design_point(engine_name, request):
    engine = request.getfixturevalue(engine_name)
    r = engine.run()
    assert r.electrical_power == pytest.approx(engine.design.electrical_power, rel=1e-8)
    assert r.heat_rate == pytest.approx(engine.design.heat_rate, rel=1e-8)
    assert r.exhaust.t == pytest.approx(engine.design.exhaust.t, rel=1e-8)


@pytest.mark.parametrize("engine_name", ["twin", "single"])
def test_hot_day_trends(engine_name, request):
    engine = request.getfixturevalue(engine_name)
    iso, hot = engine.run(), engine.run(Ambient(t=313.15))
    assert hot.electrical_power < 0.85 * iso.electrical_power
    assert hot.heat_rate > iso.heat_rate
    assert hot.exhaust.w < iso.exhaust.w
    assert hot.exhaust.t > iso.exhaust.t
    assert hot.info["surge_margin"] < iso.info["surge_margin"]


def test_twin_shaft_cold_day_is_speed_limited(twin):
    r = twin.run(Ambient(t=253.15))
    assert r.info["control"] == "gas generator speed"
    assert r.info["mechanical_speed"] == pytest.approx(twin.max_gg_speed, rel=1e-8)
    assert r.info["tit"] < twin.tit_design


def test_altitude_scales_power_with_pressure(twin):
    """Non-dimensional similarity: dry-air output scales with ambient pressure, heat rate unchanged."""
    p = isa_pressure(1000.0)
    sea, high = twin.run(Ambient(rh=0.0)), twin.run(Ambient(p=p, rh=0.0))
    assert high.electrical_power / sea.electrical_power == pytest.approx(p / 101325.0, rel=1e-6)
    assert high.heat_rate == pytest.approx(sea.heat_rate, rel=1e-6)


def test_installation_losses_and_degradation_cost_output(twin):
    base = twin.run()
    assert twin.run(inlet_loss=1000.0).electrical_power < base.electrical_power
    assert twin.run(exhaust_loss=1000.0).electrical_power < base.electrical_power
    worn = twin.run(health=Health(compressor_flow_scale=0.98, compressor_eff_delta=-0.01))
    assert worn.electrical_power < base.electrical_power
    assert worn.heat_rate > base.heat_rate


def test_mass_and_energy_close_off_design(twin):
    r = twin.run(Ambient(t=303.15), inlet_loss=800.0, exhaust_loss=1500.0)
    assert r.exhaust.w == pytest.approx(r.air_flow + r.fuel_flow, rel=1e-12)
    assert r.info["corrected_flow"] < 1.0 and r.info["corrected_speed"] < 1.0


# --- calibration and site ----------------------------------------------

@pytest.fixture(scope="module")
def calibrated():
    with open(EXAMPLES / "sgt400_13mw.toml", "rb") as f:
        d = tomllib.load(f)
    cal = calibrate_twin_shaft(
        d["electrical_power_MW"] * 1e6, d["heat_rate_kJ_per_kWh"], d["exhaust_flow_kg_s"], d["pressure_ratio"],
        exhaust_temp=d["exhaust_temperature_C"], **d["assumptions"],
    )
    return d, cal


def test_calibration_hits_datasheet(calibrated):
    d, cal = calibrated
    r = cal.cycle.run(cal.air_flow)
    assert r.electrical_power / 1e6 == pytest.approx(d["electrical_power_MW"], rel=1e-8)
    assert r.heat_rate == pytest.approx(d["heat_rate_kJ_per_kWh"], rel=1e-8)
    assert r.exhaust.w == pytest.approx(d["exhaust_flow_kg_s"], rel=1e-8)
    # exhaust temperature is not fitted: it is an independent check
    assert r.exhaust.t - 273.15 == pytest.approx(d["exhaust_temperature_C"], abs=10)
    assert 0.8 < cal.turbine_efficiency < 0.92
    assert 1150 < cal.tit - 273.15 < 1400


def test_site_correction_factors_multiply_to_site_value(twin):
    site = Site("test", altitude_m=300, temperature_C=35, rh=0.3, inlet_loss_mbar=8, exhaust_loss_mbar=20,
                fuel=Fuel({"CH4": 0.9, "C2H6": 0.05, "N2": 0.05}))
    perf = site_performance(twin, site, health=Health(0.99, -0.005))
    power = math.prod(f[1] for f in perf.factors)
    heat_rate = math.prod(f[2] for f in perf.factors)
    assert perf.result.electrical_power / perf.iso.electrical_power == pytest.approx(power, rel=1e-9)
    assert perf.result.heat_rate / perf.iso.heat_rate == pytest.approx(heat_rate, rel=1e-9)
    assert [f[0] for f in perf.factors][-1] == "Degradation"
    assert "Site performance: test" in perf.to_markdown()


def test_correction_curves_are_monotonic(single):
    cc = correction_curves(single, [-10, 0, 10, 20, 30, 40])
    assert cc["power"] == sorted(cc["power"], reverse=True)
    assert cc["heat_rate"] == sorted(cc["heat_rate"])
