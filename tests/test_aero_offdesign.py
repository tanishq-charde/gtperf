import json
from dataclasses import replace

import numpy as np
import pytest

from gtperf.aero import Flight, Kerosene, Nozzle, Turbofan
from gtperf.aero_offdesign import TurbofanEngine
from gtperf.components import FlowState
from gtperf.gas import PerfectGas

CRUISE = Flight(10668.0, 0.78)
SLS = Flight()
CYCLE = Turbofan(bpr=10.0, fan_pr=1.5, fan_core_pr=2.4, hpc_pr=17.0, tet=1600.0, hp_offtake=150e3)
TAKE_OFF_TET = 1750.0


@pytest.fixture(scope="module")
def engine():
    return TurbofanEngine(CYCLE, 150.0, CRUISE)


@pytest.fixture(scope="module")
def take_off(engine):
    return engine.run(SLS, tet=TAKE_OFF_TET)


def assert_reproduces_design(engine, tol=1e-8):
    r, d = engine.run(), engine.design
    assert r.net_thrust == pytest.approx(d.net_thrust, rel=tol)
    assert r.tsfc == pytest.approx(d.tsfc, rel=tol)
    assert set(r.stations) == set(d.stations)
    for k, s in d.stations.items():
        assert (r.stations[k].w, r.stations[k].t, r.stations[k].p) == pytest.approx((s.w, s.t, s.p), rel=tol), k
    assert r.info["n1"] == pytest.approx(1, abs=tol) and r.info["n2"] == pytest.approx(1, abs=tol)
    assert r.info["bpr"] == pytest.approx(engine.cycle.bpr, rel=tol)


def test_design_point_is_reproduced(engine):
    assert_reproduces_design(engine)


def test_design_point_is_reproduced_with_split_cooling_and_cd_nozzle():
    cycle = Turbofan(bpr=6.0, fan_pr=1.7, fan_core_pr=2.2, hpc_pr=14.0, tet=1650.0, ngv_cooling=0.06,
                     rotor_cooling=0.05, lpt_cooling=0.02, lpt_cooling_bleed=0.6,
                     core_nozzle=Nozzle("cd", efficiency=0.98, cd=0.97), bypass_nozzle=Nozzle(cd=0.98))
    assert_reproduces_design(TurbofanEngine(cycle, 120.0, CRUISE))


def test_design_point_is_reproduced_without_booster():
    assert_reproduces_design(TurbofanEngine(Turbofan(bpr=5.0, fan_pr=1.7, hpc_pr=20.0, tet=1600.0), 100.0, SLS))


def test_take_off_versus_cruise(engine, take_off):
    cruise = engine.design
    assert take_off.net_thrust > 3.5 * cruise.net_thrust
    assert 7.0e-6 < take_off.tsfc < 9.0e-6 < cruise.tsfc  # ram drag makes cruise TSFC about twice SLS
    assert take_off.air_flow > 2 * cruise.air_flow
    assert not take_off.bypass_jet.choked and cruise.bypass_jet.choked
    assert take_off.info["n1_corrected"] < 1 and take_off.info["n2"] > 1  # hot day-like corrected speeds at SLS
    for k in ("surge_margin_fan", "surge_margin_booster", "surge_margin_hpc"):
        assert take_off.info[k] > 0
    # part-power TSFC bucket at cruise: best near two-thirds thrust, worse at high and low throttle
    tsfc = {t: engine.run(CRUISE, tet=t).tsfc for t in (1200.0, 1400.0, 1700.0)}
    assert tsfc[1400.0] < cruise.tsfc < tsfc[1700.0]
    assert tsfc[1400.0] < tsfc[1200.0]


def test_thrust_lapse_with_altitude_and_mach(engine, take_off):
    climb = [engine.run(Flight(h, 0.6), tet=1700.0) for h in (0.0, 5000.0, 10000.0)]
    thrust = [r.net_thrust for r in climb]
    assert thrust[0] > thrust[1] > thrust[2]
    assert 0.25 < thrust[2] / thrust[0] < 0.6
    rolling = engine.run(Flight(0.0, 0.25), tet=TAKE_OFF_TET)
    assert rolling.net_thrust < take_off.net_thrust and rolling.air_flow > take_off.air_flow


def test_thrust_and_speed_controls(engine, take_off):
    r = engine.run(CRUISE, thrust=15e3)
    assert r.net_thrust == pytest.approx(15e3, rel=1e-8) and r.info["control"] == "thrust"
    r = engine.run(SLS, n1=take_off.info["n1"], guess=take_off.info["x"])
    assert r.info["tet"] == pytest.approx(TAKE_OFF_TET, rel=1e-7)
    r = engine.run(SLS, fuel_flow=take_off.fuel_flow)
    assert r.net_thrust == pytest.approx(take_off.net_thrust, rel=1e-7)
    with pytest.raises(ValueError):
        engine.run(SLS, tet=1700.0, n1=0.9)


def test_idle_to_take_off_acceleration_reaches_the_steady_point(engine, take_off):
    idle = engine.run(SLS, n1=0.36)
    assert 0.04 < idle.net_thrust / take_off.net_thrust < 0.12

    def fuel(t):  # 3 s ramp, a stand-in for an acceleration schedule
        return idle.fuel_flow + (take_off.fuel_flow - idle.fuel_flow) * min(t / 3.0, 1.0)

    tr = engine.simulate(fuel, 8.0, idle, kind="fuel", dt=0.1)
    assert tr.n1[-1] == pytest.approx(take_off.info["n1"], rel=1e-5)
    assert tr.n2[-1] == pytest.approx(take_off.info["n2"], rel=1e-5)
    assert tr.net_thrust[-1] == pytest.approx(take_off.net_thrust, rel=1e-5)
    assert tr.tet[-1] == pytest.approx(TAKE_OFF_TET, rel=1e-5)
    assert np.all(np.diff(tr.n2) > -1e-9)  # monotonic acceleration
    assert 2.0 < tr.time_to(0.95) < 6.0
    # fuel arrives before the HP spool speeds up: TET overshoots its steady value at the same N2 and
    # the HPC moves towards surge
    sm = tr.surge_margin_hpc
    assert 0 < sm.min() < 0.5 * sm[0]
    assert tr.tet.max() > 1.3 * idle.info["tet"]


def test_cd_nozzle_beats_convergent_only_when_choked():
    gas = PerfectGas(1148.0, 4 / 3)
    hot = FlowState(50.0, 900.0, 4.0e5, gas)
    conv, cd = Nozzle("convergent", 0.98), Nozzle("cd", 0.98)
    jc, jd = conv.jet(hot, 1.0e5), cd.jet(hot, 1.0e5)
    assert jc.choked and jd.choked and jd.area_ratio > 1.2
    assert jd.p == pytest.approx(1.0e5) and jd.gross_thrust > 1.01 * jc.gross_thrust
    assert jd.throat_area == pytest.approx(jc.throat_area, rel=1e-6)  # same throat, so same flow capacity
    cool = FlowState(50.0, 300.0, 1.5e5, gas)  # below the critical pressure ratio
    assert cd.jet(cool, 1.0e5).gross_thrust == pytest.approx(conv.jet(cool, 1.0e5).gross_thrust, rel=1e-12)
    # fixed area ratio away from design: under- and over-expansion both lose thrust to full expansion
    fixed = Nozzle("cd", 0.98, area_ratio=jd.area_ratio)
    for p_amb in (0.6e5, 1.6e5):
        assert fixed.jet(hot, p_amb).gross_thrust < cd.jet(hot, p_amb).gross_thrust
    assert fixed.jet(hot, 1.6e5).p < 1.6e5  # over-expanded: negative pressure thrust
    # discharge coefficient: same thrust, larger geometric throat
    j = Nozzle("cd", 0.98, cd=0.95).jet(hot, 1.0e5)
    assert j.gross_thrust == pytest.approx(jd.gross_thrust, rel=1e-12)
    assert j.geometric_throat_area == pytest.approx(jd.throat_area / 0.95, rel=1e-12)


def test_cd_nozzle_in_the_engine():
    low_bypass = dict(bpr=1.0, fan_pr=3.0, hpc_pr=8.0, tet=1650.0)
    conv = Turbofan(**low_bypass).run(80.0, CRUISE)
    cd = Turbofan(**low_bypass, core_nozzle=Nozzle("cd"), bypass_nozzle=Nozzle("cd")).run(80.0, CRUISE)
    assert conv.core_jet.choked and cd.net_thrust > 1.01 * conv.net_thrust
    hb = Turbofan(bpr=10.0, fan_pr=1.4, hpc_pr=17.0, tet=1500.0)
    sls = [replace_nozzle(hb, kind).run(100.0, SLS) for kind in ("convergent", "cd")]
    assert not sls[0].bypass_jet.choked
    assert sls[1].bypass_jet.gross_thrust == pytest.approx(sls[0].bypass_jet.gross_thrust, rel=1e-12)
    # off design with fixed C-D area ratio: runs and still reproduces its design point
    engine = TurbofanEngine(Turbofan(**low_bypass, core_nozzle=Nozzle("cd")), 80.0, CRUISE)
    assert_reproduces_design(engine)
    r = engine.run(Flight(5000.0, 0.6), tet=1550.0)
    assert r.core_jet.p != pytest.approx(r.core_jet.p_amb, rel=1e-3)  # no longer fully expanded


def replace_nozzle(cycle, kind):
    return replace(cycle, bypass_nozzle=Nozzle(kind))


SPLIT = dict(bpr=8.0, fan_pr=1.5, fan_core_pr=2.4, hpc_pr=16.0, tet=1650.0, mechanical_efficiency=1.0,
             combustor_eff=1.0, ngv_cooling=0.07, rotor_cooling=0.05, lpt_cooling=0.03, lpt_cooling_bleed=0.5)


def first_law(r):
    s = r.stations
    h_in = s["0"].w * s["0"].h + r.fuel_flow * Kerosene().heat_release_term(288.15, 1.0)
    h_out = s["18"].w * s["18"].h + s["8"].w * s["8"].h
    return h_in, h_out


def test_split_cooling_conserves_mass_and_energy():
    r = Turbofan(**SPLIT).run(200.0, CRUISE)
    s = r.stations
    assert s["8"].w + s["18"].w == pytest.approx(s["0"].w + r.fuel_flow, rel=1e-12)
    w25 = s["25"].w
    assert s["26"].w == pytest.approx(0.03 * w25) and s["26"].p < s["3"].p
    assert s["41"].w - s["4"].w == pytest.approx(0.07 * w25, rel=1e-12)
    assert s["45"].w - s["44"].w == pytest.approx(0.05 * w25, rel=1e-12)
    assert s["49"].w - s["48"].w == pytest.approx(0.03 * w25, rel=1e-12)
    h_in, h_out = first_law(r)
    assert h_out == pytest.approx(h_in, rel=1e-9)
    # off design, the matched point closes the first law too
    engine = TurbofanEngine(Turbofan(**SPLIT, hp_offtake=0.0), 200.0, CRUISE)
    h_in, h_out = first_law(engine.run(Flight(3000.0, 0.4), tet=1500.0))
    assert h_out == pytest.approx(h_in, rel=1e-9)


def test_single_cooling_fraction_keeps_the_old_model():
    base = dict(bpr=10.0, fan_pr=1.5, fan_core_pr=2.4, hpc_pr=17.0, tet=1600.0)
    old = Turbofan(**base, cooling_fraction=0.12).run(150.0, CRUISE)
    new = Turbofan(**base, ngv_cooling=0.12, rotor_cooling=0.0).run(150.0, CRUISE)
    assert new.net_thrust == old.net_thrust and new.tsfc == old.tsfc
    # moving part of it behind the rotor costs HPT work, so it raises TSFC
    split = Turbofan(**base, ngv_cooling=0.06, rotor_cooling=0.06).run(150.0, CRUISE)
    assert split.tsfc > old.tsfc


def test_cli_offdesign(capsys, tmp_path):
    from pathlib import Path

    from gtperf.cli import main

    config = Path(__file__).resolve().parents[1] / "examples" / "turbofan_cruise.toml"
    main(["turbofan", str(config), "--offdesign", "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["net_thrust_kN"] == pytest.approx(22.0, rel=1e-6)  # the design point first
    assert all(r["net_thrust_kN"] > 0 for r in rows)
