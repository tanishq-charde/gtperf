import math

import numpy as np
import pytest

from gtperf.aero import Flight, Kerosene, Turbofan, isa, nozzle
from gtperf.components import FlowState
from gtperf.fuel import PerfectFuel
from gtperf.gas import DRY_AIR, IdealGasMixture, PerfectGas

AIR = PerfectGas(1005.0, 1.4)
GAS = PerfectGas(1148.0, 4 / 3)
CRUISE = Flight(10668.0, 0.78)


def test_isa():
    assert isa(0) == (288.15, 101325.0)
    t, p = isa(11000)
    assert t == pytest.approx(216.65) and p == pytest.approx(22632, rel=1e-4)
    assert isa(20000)[1] == pytest.approx(5474.9, rel=1e-3)


def test_kerosene_stoichiometry_and_lhv():
    fuel = Kerosene()
    air = IdealGasMixture(DRY_AIR)
    assert 1 / fuel.stoichiometric_far(air) == pytest.approx(14.7, abs=0.2)
    # burning to completion at T_REF releases exactly the LHV
    far = 0.02
    h_in = air.h(298.15) + far * fuel.heat_release_term(298.15, 1.0)
    h_out = (1 + far) * fuel.products(air, far).h(298.15)
    assert (h_in - h_out) / far == pytest.approx(fuel.lhv, rel=1e-9)


@pytest.mark.parametrize("bpr", [0.0, 5.0])
def test_ideal_cycle_recovers_brayton_efficiency(bpr):
    """All efficiencies 1, no losses, fully expanded jets: eta_th = 1 - 1/(tau_r tau_c)."""
    fuel = PerfectFuel(1e13, AIR)  # far -> 0, so the gas is unchanged by combustion
    e = Turbofan(bpr=bpr, fan_pr=1.6, hpc_pr=15.0, tet=1500.0, fan_eff=1, booster_eff=1, hpc_eff=1, hpt_eff=1,
                 lpt_eff=1, intake_recovery=1, bypass_duct_dp=0, core_duct_dp=0, combustor_dp=0, combustor_eff=1,
                 turbine_duct_dp=0, jet_pipe_dp=0, cooling_fraction=0, mechanical_efficiency=1,
                 ideal_nozzles=True, fuel=fuel)
    r = e.run(100.0, Flight(10000.0, 0.8, gas=AIR))
    g = 1.4
    tau_r = 1 + (g - 1) / 2 * 0.8**2
    tau_c = (1.6 * 15.0) ** ((g - 1) / g)
    assert r.thermal_efficiency == pytest.approx(1 - 1 / (tau_r * tau_c), rel=1e-6)


def hand_turbofan(w=215.0, bpr=5.0, opr=25.0, fpr=1.65, tit=1550.0, eta_f=0.90, eta_c=0.87, eta_t=0.90,
                  eta_m=0.99, eta_j=0.95, dp_b=0.06, lhv=43.1e6, pa=1.0e5, ta=288.0):
    """Sea-level static turbofan by the constant-cp textbook method (Saravanamuttoo, ch. 3)."""
    cpa, ga, cpg, gg, tref = 1005.0, 1.4, 1148.0, 4 / 3, 298.15
    ra, rg = cpa * (1 - 1 / ga), cpg * (1 - 1 / gg)
    wc, wb = w / (1 + bpr), w * bpr / (1 + bpr)

    def jet(m, t0, p0, cp, g, r):
        crit = (1 - (1 / eta_j) * (g - 1) / (g + 1)) ** (-g / (g - 1))
        if p0 / pa > crit:  # choked
            tc, pc = 2 * t0 / (g + 1), p0 / crit
            v = math.sqrt(g * r * tc)
            area = m / (pc / (r * tc) * v)
            return m * v + area * (pc - pa)
        t = t0 - eta_j * t0 * (1 - (pa / p0) ** ((g - 1) / g))
        return m * math.sqrt(2 * cp * (t0 - t))

    t13 = ta * (1 + (fpr ** ((ga - 1) / ga) - 1) / eta_f)
    p13 = pa * fpr
    t3 = t13 * (1 + ((opr / fpr) ** ((ga - 1) / ga) - 1) / eta_c)
    p3 = pa * opr
    f = (cpg * (tit - tref) - cpa * (t3 - tref)) / (lhv - cpg * (tit - tref))
    p4 = p3 * (1 - dp_b)
    wg = wc * (1 + f)
    t45 = tit - wc * cpa * (t3 - t13) / (eta_m * wg * cpg)
    p45 = p4 * (1 - (tit - t45) / (eta_t * tit)) ** (gg / (gg - 1))
    t5 = t45 - w * cpa * (t13 - ta) / (eta_m * wg * cpg)
    p5 = p45 * (1 - (t45 - t5) / (eta_t * t45)) ** (gg / (gg - 1))
    thrust = jet(wb, t13, p13, cpa, ga, ra) + jet(wg, t5, p5, cpg, gg, rg)
    return thrust, wc * f / thrust


def test_constant_cp_turbofan_matches_hand_calculation():
    e = Turbofan(bpr=5.0, fan_pr=1.65, hpc_pr=25.0 / 1.65, tet=1550.0, fan_eff=0.90, hpc_eff=0.87, hpt_eff=0.90,
                 lpt_eff=0.90, compressor_polytropic=False, intake_recovery=1, bypass_duct_dp=0, core_duct_dp=0,
                 combustor_dp=0.06, combustor_eff=1, turbine_duct_dp=0, jet_pipe_dp=0, cooling_fraction=0,
                 nozzle_efficiency=0.95, fuel=PerfectFuel(43.1e6, GAS))
    sls = Flight(0.0, 0.0, isa_dt=-0.15, gas=AIR)  # 288 K, 1.01325 bar
    assert sls.static() == pytest.approx((288.0, 101325.0))
    r = e.run(215.0, sls)
    thrust, sfc = hand_turbofan(pa=101325.0)
    assert r.core_jet.choked and not r.bypass_jet.choked
    assert r.net_thrust == pytest.approx(thrust, rel=1e-6)
    assert r.tsfc == pytest.approx(sfc, rel=1e-6)


def test_first_law_closes_through_the_engine():
    e = Turbofan(bpr=8.0, fan_pr=1.5, fan_core_pr=2.4, hpc_pr=16.0, tet=1650.0, mechanical_efficiency=1.0,
                 combustor_eff=1.0)
    fuel = Kerosene()
    r = e.run(200.0, CRUISE)
    s = r.stations
    h_in = s["0"].w * s["0"].h + r.fuel_flow * fuel.heat_release_term(288.15, 1.0)
    h_out = s["18"].w * s["18"].h + s["8"].w * s["8"].h
    assert h_out == pytest.approx(h_in, rel=1e-9)
    for jet, st in ((r.core_jet, s["8"]), (r.bypass_jet, s["18"])):  # nozzle conserves total enthalpy
        assert st.gas.h(jet.t) + 0.5 * jet.v**2 == pytest.approx(st.h, rel=1e-9)


def test_convergent_nozzle_chokes_at_sonic_exit():
    gas = PerfectGas(1005.0, 1.4)
    state = FlowState(10.0, 600.0, 3.0e5, gas)
    j = nozzle(state, 1.0e5)
    assert j.choked
    assert j.t == pytest.approx(2 * 600.0 / 2.4, rel=1e-6)
    assert j.p == pytest.approx(3.0e5 / 1.8929, rel=1e-4)  # critical pressure ratio 1.893 for gamma 1.4
    low = nozzle(state, 2.0e5)
    assert not low.choked and low.p == 2.0e5


def test_cruise_design_point_is_plausible():
    r = Turbofan(bpr=10.0, fan_pr=1.5, fan_core_pr=2.4, hpc_pr=17.0, tet=1600.0).run(150.0, CRUISE)
    assert 13.5e-6 < r.tsfc < 16.0e-6  # modern high-bypass cruise TSFC, kg/(N s)
    assert 0.65 < r.propulsive_efficiency < 0.85
    assert 0.45 < r.thermal_efficiency < 0.55
    assert r.core_jet.choked and r.bypass_jet.choked


def test_optimum_fan_pressure_ratio_sets_jet_velocity_ratio():
    """At the fan pressure ratio for maximum thrust, V_bypass / V_core = eta_fan * eta_lpt.

    The classic result holds for the marginal (small-stage) efficiencies, so both are polytropic here.
    """
    from scipy.optimize import minimize_scalar

    def run(fpr):
        return Turbofan(bpr=8.0, fan_pr=fpr, fan_core_pr=2.4, hpc_pr=16.0, tet=1600.0, fan_eff=0.9, lpt_eff=0.9,
                        turbine_polytropic=True, ideal_nozzles=True).run(100.0)

    def negative_thrust(fpr):
        try:
            return -run(fpr).net_thrust
        except ValueError:  # fan too hard for the LPT to drive
            return 0.0

    opt = minimize_scalar(negative_thrust, bounds=(1.3, 1.8), method="bounded",
                          options={"xatol": 1e-5})
    assert 1.35 < opt.x < 1.75
    assert run(opt.x).jet_velocity_ratio == pytest.approx(0.9 * 0.9, abs=0.015)


def test_bypass_ratio_trade():
    """Higher bypass ratio, each at its best fan pressure ratio: lower TSFC and lower specific thrust."""
    def best(bpr):
        rs = []
        for f in np.arange(1.3, 2.6, 0.05):
            try:
                rs.append(Turbofan(bpr=bpr, fan_pr=f, fan_core_pr=2.4, hpc_pr=16.0, tet=1600.0).run(100.0, CRUISE))
            except ValueError:  # the LPT cannot drive a fan this hard
                break
        return min(rs, key=lambda r: r.tsfc)

    lo, hi = best(5.0), best(12.0)
    assert hi.tsfc < lo.tsfc and hi.specific_thrust < lo.specific_thrust


def test_size_for_thrust():
    e = Turbofan(bpr=10.0, fan_pr=1.5, fan_core_pr=2.4, hpc_pr=17.0, tet=1600.0)
    w = e.size_for_thrust(25e3, CRUISE)
    assert e.run(w, CRUISE).net_thrust == pytest.approx(25e3, rel=1e-9)
