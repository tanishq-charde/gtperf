import math

import pytest

from gtperf.fuel import methane, natural_gas
from gtperf.gas import DRY_AIR, IdealGasMixture, humid_air, saturation_pressure

AIR = IdealGasMixture(DRY_AIR)


@pytest.mark.parametrize("t, cp_table", [(300.0, 1005.0), (500.0, 1029.0), (1000.0, 1142.0)])
def test_dry_air_cp_matches_tables(t, cp_table):
    # Standard ideal-gas air tables (e.g. Cengel Table A-2b)
    assert AIR.cp(t) == pytest.approx(cp_table, rel=0.005)


def test_dry_air_molar_mass():
    assert AIR.mw * 1e3 == pytest.approx(28.965, abs=0.01)


def test_inverse_functions_round_trip():
    for t in (250.0, 700.0, 1600.0):
        assert AIR.t_from_h(AIR.h(t)) == pytest.approx(t, abs=1e-6)
        assert AIR.t_from_s(AIR.s(t, 5e5), 5e5) == pytest.approx(t, abs=1e-6)


def test_saturation_pressure():
    assert saturation_pressure(373.15) == pytest.approx(101325, rel=0.01)
    assert saturation_pressure(288.15) == pytest.approx(1705, rel=0.01)


def test_humid_air_is_lighter_than_dry_air():
    assert humid_air(303.15, 101325, 0.9).mw < AIR.mw


def test_methane_lhv():
    # Published LHV of methane: 50.0 MJ/kg
    assert methane().lhv / 1e6 == pytest.approx(50.0, rel=0.003)


def test_methane_stoichiometric_far():
    # 1 / 17.2 for methane in dry air
    assert methane().stoichiometric_far(AIR) == pytest.approx(1 / 17.2, rel=0.01)


def test_products_conserve_mass_and_atoms():
    fuel = natural_gas()
    far = 0.025
    prod = fuel.products(AIR, far)
    # mass: (1 + far) kg of product per kg air
    n_prod = (1 + far) / prod.mw
    for element in ("C", "H", "O", "N"):
        from gtperf.gas import SPECIES

        def atoms(mix, n):
            return sum(x * n * SPECIES[k].atoms.get(element, 0) for k, x in mix.mole_fractions.items())

        before = atoms(AIR, 1 / AIR.mw) + atoms(fuel.mix, far / fuel.mw)
        assert atoms(prod, n_prod) == pytest.approx(before, rel=1e-9)


def test_rich_mixture_rejected():
    with pytest.raises(ValueError):
        methane().products(AIR, 0.07)


def test_matches_cantera_if_available():
    ct = pytest.importorskip("cantera")
    gas = ct.Solution("gri30.yaml")
    mix = humid_air(288.15, 101325, 0.6)
    gas.TPX = 900.0, 101325.0, {("AR" if k == "Ar" else k): v for k, v in mix.mole_fractions.items()}
    assert mix.cp(900.0) == pytest.approx(gas.cp_mass, rel=1e-6)
    assert mix.h(900.0) == pytest.approx(gas.enthalpy_mass, rel=1e-6)
    # entropy differs only by the constant ideal mixing term
    mixing = -mix.R * sum(x * math.log(x) for x in mix.mole_fractions.values())
    assert mix.s(900.0, 101325.0) + mixing == pytest.approx(gas.entropy_mass, rel=1e-6)
