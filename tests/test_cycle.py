import pytest

from gtperf import (
    Ambient,
    Combustor,
    Compressor,
    Duct,
    PerfectFuel,
    PerfectGas,
    SimpleCycle,
    Turbine,
    natural_gas,
)
from gtperf.gas import T_REF

P0 = 101325.0


def test_perfect_gas_cycle_matches_hand_calculation():
    """Classic textbook method: separate constant cp for air and combustion gas."""
    air = PerfectGas(1005.0, 1.4)
    gas = PerfectGas(1148.0, 4 / 3)
    lhv = 43.1e6
    pr, eta_c, eta_t, eta_b, eta_m, dp_b, tit = 12.0, 0.86, 0.89, 0.98, 0.99, 0.06, 1350.0

    cycle = SimpleCycle(
        Compressor(pr, eta_c, polytropic=False),
        Combustor(PerfectFuel(lhv, gas), tit, dp_frac=dp_b, efficiency=eta_b),
        Turbine(eta_t),
        mechanical_efficiency=eta_m,
        generator_efficiency=1.0,
    )
    r = cycle.run(1.0, Ambient(t=288.0, p=P0, gas=air))

    t3 = 288.0 * (1 + (pr ** (0.4 / 1.4) - 1) / eta_c)
    far = (1148 * (tit - T_REF) - 1005 * (t3 - T_REF)) / (eta_b * lhv - 1148 * (tit - T_REF))
    p4 = P0 * pr * (1 - dp_b)
    t5 = tit * (1 - eta_t * (1 - (P0 / p4) ** 0.25))
    work = (1 + far) * 1148 * (tit - t5) * eta_m - 1005 * (t3 - 288.0)

    assert r.stations["3"].t == pytest.approx(t3, rel=1e-9)
    assert r.fuel_flow == pytest.approx(far, rel=1e-6)
    assert r.exhaust.t == pytest.approx(t5, rel=1e-9)
    assert r.specific_work == pytest.approx(work, rel=1e-6)
    assert r.thermal_efficiency == pytest.approx(work / (far * lhv), rel=1e-6)


@pytest.mark.parametrize("pr", [4.0, 10.0, 30.0])
def test_ideal_brayton_limit(pr):
    """Lossless cycle with negligible fuel mass recovers eta = 1 - r^-((g-1)/g)."""
    air = PerfectGas(1005.0, 1.4)
    cycle = SimpleCycle(
        Compressor(pr, 1.0, polytropic=False),
        Combustor(PerfectFuel(1e13, air), 1400.0, dp_frac=0.0, efficiency=1.0),
        Turbine(1.0),
        mechanical_efficiency=1.0,
        generator_efficiency=1.0,
    )
    r = cycle.run(1.0, Ambient(t=288.15, p=P0, gas=air))
    assert r.thermal_efficiency == pytest.approx(1 - pr ** (-0.4 / 1.4), rel=1e-5)


def make_cycle(pr=16.0, tit=1500.0, **kw):
    return SimpleCycle(
        Compressor(pr, 0.90), Combustor(natural_gas(), tit, efficiency=kw.pop("eta_b", 0.995)), Turbine(0.89), **kw
    )


def test_first_law_closes():
    cycle = make_cycle(eta_b=1.0, cooling_fraction=0.1, mechanical_efficiency=1.0)
    r = cycle.run(40.0)
    fuel = cycle.combustor.fuel
    energy_in = r.stations["0"].w * r.stations["0"].h + r.fuel_flow * fuel.mix.h(cycle.combustor.t_fuel)
    energy_out = r.exhaust.w * r.exhaust.h + r.shaft_power
    assert energy_out == pytest.approx(energy_in, rel=1e-9)


def test_mass_balance():
    r = make_cycle(cooling_fraction=0.12).run(40.0)
    assert r.exhaust.w == pytest.approx(40.0 + r.fuel_flow, rel=1e-12)


def test_polytropic_efficiency_exceeds_isentropic_for_compressor():
    cycle = make_cycle(pr=16.0)
    r = cycle.run(1.0)
    assert cycle.compressor.isentropic_efficiency(r.stations["2"]) < 0.90


def test_efficiency_rises_with_firing_temperature():
    effs = [make_cycle(tit=t).run(1.0).thermal_efficiency for t in (1300.0, 1450.0, 1600.0)]
    assert effs == sorted(effs)


def test_specific_work_peaks_at_lower_pressure_ratio_than_efficiency():
    prs = [4 + 0.5 * i for i in range(80)]
    results = [make_cycle(pr=pr, tit=1400.0).run(1.0) for pr in prs]
    pr_work = prs[max(range(len(prs)), key=lambda i: results[i].specific_work)]
    pr_eff = prs[max(range(len(prs)), key=lambda i: results[i].thermal_efficiency)]
    assert pr_work < pr_eff


def test_hot_day_reduces_output():
    cycle = make_cycle()
    iso = cycle.run(40.0)
    hot = cycle.run(40.0, Ambient(t=308.15))
    # Same mass flow here; with a real map the flow would fall too (phase 2)
    assert hot.electrical_power < iso.electrical_power


def test_installation_losses_reduce_output():
    base = make_cycle().run(40.0)
    lossy = make_cycle(inlet_loss=1000.0, exhaust_loss=2500.0).run(40.0)
    assert lossy.electrical_power < base.electrical_power
    assert lossy.heat_rate > base.heat_rate


def test_cooling_air_lowers_rotor_inlet_temperature():
    r = make_cycle(cooling_fraction=0.15).run(40.0)
    assert r.stations["41"].t < r.stations["4"].t


def test_duct_loss():
    cycle = make_cycle(inlet=Duct(0.01))
    r = cycle.run(10.0)
    assert r.stations["2"].p == pytest.approx(0.99 * P0)


def test_size_for_power():
    cycle = make_cycle()
    w = cycle.size_for_power(13e6)
    assert cycle.run(w).electrical_power == pytest.approx(13e6, rel=1e-9)
