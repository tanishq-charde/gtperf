"""Fuels and complete-combustion product composition."""

from __future__ import annotations

from dataclasses import dataclass

from .gas import SPECIES, T_REF, IdealGasMixture, PerfectGas

COMBUSTIBLE = {"CH4", "C2H6", "C3H8"}


class Fuel:
    """Gaseous hydrocarbon fuel burned to completion (CO2 + H2O vapour).

    Composition is in mole fractions. The lower heating value is computed from the
    species enthalpies of formation, so it is consistent with the gas property model.
    """

    def __init__(self, mole_fractions, name="fuel"):
        self.name = name
        self.mix = IdealGasMixture(mole_fractions)
        self.mw = self.mix.mw
        self.lhv = self._lhv()

    def _per_mole_reaction(self):
        """Moles of O2 consumed and CO2/H2O formed per mole of fuel."""
        o2 = co2 = h2o = 0.0
        for name, x in self.mix.mole_fractions.items():
            if name not in COMBUSTIBLE:
                continue
            atoms = SPECIES[name].atoms
            c, h = atoms.get("C", 0), atoms.get("H", 0)
            o2 += x * (c + h / 4)
            co2 += x * c
            h2o += x * h / 2
        return o2, co2, h2o

    def _lhv(self):
        o2, co2, h2o = self._per_mole_reaction()
        sp = SPECIES
        h_react = self.mix.h(T_REF) * self.mw + o2 * sp["O2"].h(T_REF)
        h_prod = co2 * sp["CO2"].h(T_REF) + h2o * sp["H2O"].h(T_REF)
        return (h_react - h_prod) / self.mw  # J/kg fuel

    def stoichiometric_far(self, air):
        o2, _, _ = self._per_mole_reaction()
        mol_air_per_mol_fuel = o2 / air.mole_fractions["O2"]
        return self.mw / (mol_air_per_mol_fuel * air.mw)

    def products(self, air, far):
        """Product mixture from burning `far` kg fuel per kg of `air` to completion."""
        n_air = 1.0 / air.mw
        n_fuel = far / self.mw
        n = {k: x * n_air for k, x in air.mole_fractions.items()}
        for k, x in self.mix.mole_fractions.items():
            if k not in COMBUSTIBLE:
                n[k] = n.get(k, 0.0) + x * n_fuel
        o2, co2, h2o = self._per_mole_reaction()
        n["O2"] = n.get("O2", 0.0) - o2 * n_fuel
        n["CO2"] = n.get("CO2", 0.0) + co2 * n_fuel
        n["H2O"] = n.get("H2O", 0.0) + h2o * n_fuel
        if n["O2"] < 0:
            raise ValueError(f"fuel-air ratio {far:.4f} is richer than stoichiometric")
        return IdealGasMixture(n)

    def heat_release_term(self, t_fuel, eta):
        """Energy brought in per kg fuel for the combustor balance, J/kg.

        Absolute fuel enthalpy, less the chemical energy left unburned at efficiency eta.
        """
        return self.mix.h(t_fuel) - (1 - eta) * self.lhv


def natural_gas():
    """A typical pipeline natural gas, LHV about 48 MJ/kg."""
    return Fuel({"CH4": 0.93, "C2H6": 0.04, "C3H8": 0.01, "CO2": 0.01, "N2": 0.01}, name="natural gas")


def methane():
    return Fuel({"CH4": 1.0}, name="methane")


@dataclass
class PerfectFuel:
    """Fuel for constant-cp hand calculations: a fixed LHV and a fixed product gas."""

    lhv: float
    product_gas: PerfectGas
    name: str = "perfect fuel"

    def products(self, air, far):
        return self.product_gas

    def heat_release_term(self, t_fuel, eta):
        return eta * self.lhv
