import math

import pytest

from gtperf.aero import Flight, Turbofan
from gtperf.aero_environment import LTO_MODES, lto_cycle, noise_sources, nox_standard
from gtperf.aero_offdesign import TurbofanEngine

SLS = Flight()
CRUISE = Flight(10668.0, 0.78)


@pytest.fixture(scope="module")
def engine():
    cycle = Turbofan(bpr=10.0, fan_pr=1.5, fan_core_pr=2.4, hpc_pr=17.0, tet=1600.0, hp_offtake=150e3)
    return TurbofanEngine(cycle, cycle.size_for_thrust(22e3, CRUISE), CRUISE)


def test_nox_standard_is_continuous_and_matches_tabulated_values():
    for tier, knee in ((8, 104.7), (6, 82.6)):
        for pr in (30.0, knee):
            assert nox_standard(pr - 1e-9, tier) == pytest.approx(nox_standard(pr + 1e-9, tier), abs=1e-6)
    assert nox_standard(40.0, 8) == pytest.approx(-9.88 + 80.0)
    assert sum(m[2] for m in LTO_MODES) == pytest.approx(32.9)


def test_lto_cycle(engine):
    lto = lto_cycle(engine, 1750.0)
    modes = lto["modes"]
    assert [m["thrust_kN"] / lto["rated_thrust_kN"] for m in modes] == pytest.approx([1.0, 0.85, 0.30, 0.07])
    assert [m["ei_nox"] for m in modes] == sorted((m["ei_nox"] for m in modes), reverse=True)
    assert lto["dp_foo"] < lto["tier8"] < lto["tier6"]
    assert lto["co2_kg"] == pytest.approx(3.16 * lto["fuel_kg"])
    # idle burns the most fuel over the cycle but makes little NOx
    assert modes[-1]["fuel_kg"] == max(m["fuel_kg"] for m in modes)
    assert modes[-1]["nox_g"] < modes[0]["nox_g"]


def test_noise_sources_follow_known_turbofan_behaviour(engine):
    """Jet noise dominates at take-off, fan noise at approach; jet power follows the eighth power."""
    take_off = engine.run(SLS, tet=1750.0)
    approach = engine.run(SLS, thrust=0.3 * take_off.net_thrust)
    to, ap = noise_sources(engine, take_off), noise_sources(engine, approach)
    assert to["core_jet"] > max(to["fan_inlet_broadband"], to["fan_discharge_broadband"])
    assert max(ap["fan_inlet_broadband"], ap["fan_discharge_broadband"]) > max(ap["core_jet"], ap["bypass_jet"])
    assert to["fan_tip_relative_mach"] > 1.0 > ap["fan_tip_relative_mach"]
    v = [j.gross_thrust / j.w for j in (take_off.core_jet, approach.core_jet)]
    area = take_off.core_jet.area / approach.core_jet.area
    expected = 80 * math.log10(v[0] / v[1]) + 10 * math.log10(area)
    assert to["core_jet"] - ap["core_jet"] == pytest.approx(expected, rel=1e-9)
