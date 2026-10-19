import math
import tomllib
from pathlib import Path

import pytest

from gtperf import Ambient, Combustor, Compressor, SimpleCycle, Turbine, natural_gas
from gtperf.calibrate import calibrate_twin_shaft
from gtperf.cycle import TwinShaftCycle
from gtperf.fuel import Fuel
from gtperf.maps import CompressorMap, Health, TabulatedCompressorMap, ellipse_capacity
from gtperf.offdesign import Controls, Generator, Limits, Schedule, SingleShaftEngine, TwinShaftEngine
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


def test_tabulated_map_is_scaled_to_the_design_point():
    m = TabulatedCompressorMap.load("hpc", 17.1)
    g, pr, eff = m.point(1.0, m.design_beta)
    assert (g, pr, eff) == pytest.approx((1.0, 17.1, 1.0), rel=1e-12)
    assert m.surge_margin_at(1.0, m.design_beta) > 0.1
    # along a speed line, flow rises and pressure ratio falls from surge (R-line 1) to choke (R-line 3)
    pts = [m.point(0.95, b) for b in (1.0, 1.5, 2.0, 2.5, 3.0)]
    assert [p[0] for p in pts] == sorted(p[0] for p in pts)
    assert [p[1] for p in pts] == sorted((p[1] for p in pts), reverse=True)
    # closing the vanes cuts flow and pressure ratio
    closed = m.point(1.0, m.design_beta, vigv=-20.0)
    assert closed[0] < 0.85 and closed[1] < 17.1


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
    assert hot.info["corrected_speed"] < iso.info["corrected_speed"]


def test_twin_shaft_cold_day_is_corrected_speed_limited(twin):
    engine = TwinShaftEngine(twin.cycle, 39.0, limits=Limits(gg_corrected_speed=1.02))
    r = engine.run(Ambient(t=253.15))
    assert r.info["control"] == "gas generator corrected speed"
    assert r.info["corrected_speed"] == pytest.approx(1.02, rel=1e-8)
    assert r.info["tit"] < engine.tit_design


def test_twin_shaft_hot_day_is_mechanical_speed_limited(twin):
    """With this map the gas generator speeds up on a hot day to hold TIT, so the rotor limit binds there."""
    engine = TwinShaftEngine(twin.cycle, 39.0, limits=Limits(gg_speed=1.01))
    r = engine.run(Ambient(t=318.15))
    assert r.info["control"] == "gas generator speed"
    assert r.info["mechanical_speed"] == pytest.approx(1.01, rel=1e-8)
    assert r.info["tit"] < engine.tit_design


def test_generator_and_exhaust_temperature_limits(twin, single):
    gen = Generator(15.0, 0.8, Schedule(((-20.0, 1.1), (15.0, 1.0), (50.0, 0.9))))
    engine = TwinShaftEngine(twin.cycle, 39.0, limits=Limits(generator=gen))
    r = engine.run(Ambient(t=268.15))
    assert r.info["control"] == "generator capability"
    assert r.electrical_power == pytest.approx(gen.capability(268.15), rel=1e-8)
    limit = single.design.exhaust.t + 5.0
    hot = SingleShaftEngine(single.cycle, 39.0, limits=Limits(exhaust_temp=limit)).run(Ambient(t=318.15))
    assert hot.info["control"] == "exhaust temperature"
    assert hot.exhaust.t == pytest.approx(limit, rel=1e-8)


def test_vigv_schedule_closes_vanes_at_low_corrected_speed(twin):
    """Closing the vanes on a hot day lowers flow and output and moves the working line away from surge."""
    engine = TwinShaftEngine(twin.cycle, 39.0, controls=Controls(vigv=Schedule(((0.95, -20.0), (1.0, 0.0)))))
    fixed, vaned = twin.run(Ambient(t=318.15)), engine.run(Ambient(t=318.15))
    assert vaned.info["vigv"] < -5
    assert vaned.air_flow < fixed.air_flow and vaned.electrical_power < fixed.electrical_power
    assert engine.run().info["vigv"] == 0.0


def test_twin_shaft_part_load(twin):
    base = twin.run()
    part = twin.run(load=0.5)
    assert part.electrical_power == pytest.approx(0.5 * base.electrical_power, rel=1e-8)
    assert part.info["control"] == "load"
    assert part.info["tit"] < base.info["tit"] and part.info["mechanical_speed"] < 1.0
    assert part.thermal_efficiency < base.thermal_efficiency
    assert twin.run(power=2 * base.electrical_power).electrical_power == pytest.approx(base.electrical_power)


def test_single_shaft_part_load_holds_exhaust_temperature_then_lowers_tit(single):
    base = single.run()
    igv = single.run(load=0.8)
    assert igv.exhaust.t == pytest.approx(base.exhaust.t, rel=1e-8)
    assert single.controls.vigv_min < igv.info["vigv"] < 0 and igv.air_flow < base.air_flow
    low = single.run(load=0.4)
    assert low.info["vigv"] == single.controls.vigv_min and low.exhaust.t < base.exhaust.t
    assert low.electrical_power == pytest.approx(0.4 * base.electrical_power, rel=1e-8)


def test_losses_scale_with_dynamic_head(twin):
    """An inlet loss given at design flow falls with the square of flow on a hot day."""
    dp_ref = 1000.0
    hot = Ambient(t=318.15)
    r = twin.run(hot, inlet_loss=dp_ref, exhaust_loss=0.0)
    s0, s2 = r.stations["0"], r.stations["2"]
    d0 = twin.design.stations["0"]
    expected = dp_ref * (s2.w / d0.w) ** 2 * (s0.t / d0.t) * (d0.p / s0.p)
    assert s0.p - s2.p == pytest.approx(expected, rel=1e-6)
    assert expected < 0.9 * dp_ref


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
