"""Thermodynamic properties of ideal-gas mixtures.

Species properties use NASA 7-coefficient polynomials (taken from the GRI-Mech 3.0
thermo set). Enthalpies are absolute, i.e. they include the enthalpy of formation,
so combustion energy balances close without a separate heat-of-reaction term.

All quantities are SI: K, Pa, J/kg, J/(kg K).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

R_UNIVERSAL = 8.314462618  # J/(mol K)
P_REF = 101325.0  # Pa, reference pressure for standard-state entropy
T_REF = 298.15  # K, reference temperature for heating values


@dataclass(frozen=True)
class Species:
    name: str
    mw: float  # kg/mol
    atoms: dict
    t_mid: float
    high: tuple  # a1..a7 for T > t_mid
    low: tuple  # a1..a7 for T <= t_mid

    def _a(self, t):
        return self.high if t > self.t_mid else self.low

    def cp(self, t):
        """Molar heat capacity, J/(mol K)."""
        a = self._a(t)
        return R_UNIVERSAL * (a[0] + t * (a[1] + t * (a[2] + t * (a[3] + t * a[4]))))

    def h(self, t):
        """Molar absolute enthalpy, J/mol."""
        a = self._a(t)
        return R_UNIVERSAL * t * (
            a[0] + t * (a[1] / 2 + t * (a[2] / 3 + t * (a[3] / 4 + t * a[4] / 5))) + a[5] / t
        )

    def s0(self, t):
        """Molar standard-state entropy at P_REF, J/(mol K)."""
        a = self._a(t)
        return R_UNIVERSAL * (
            a[0] * math.log(t) + t * (a[1] + t * (a[2] / 2 + t * (a[3] / 3 + t * a[4] / 4))) + a[6]
        )


_ATOMIC_MASS = {"C": 12.011e-3, "H": 1.008e-3, "O": 15.999e-3, "N": 14.007e-3, "Ar": 39.948e-3}


def _species(name, atoms, t_mid, high, low):
    mw = sum(_ATOMIC_MASS[el] * n for el, n in atoms.items())
    return Species(name, mw, atoms, t_mid, tuple(high), tuple(low))


SPECIES = {
    s.name: s
    for s in [
        _species(
            "N2", {"N": 2}, 1000.0,
            [2.92664, 0.0014879768, -5.68476e-07, 1.0097038e-10, -6.753351e-15, -922.7977, 5.980528],
            [3.298677, 0.0014082404, -3.963222e-06, 5.641515e-09, -2.444854e-12, -1020.8999, 3.950372],
        ),
        _species(
            "O2", {"O": 2}, 1000.0,
            [3.28253784, 0.00148308754, -7.57966669e-07, 2.09470555e-10, -2.16717794e-14, -1088.45772, 5.45323129],
            [3.78245636, -0.00299673416, 9.84730201e-06, -9.68129509e-09, 3.24372837e-12, -1063.94356, 3.65767573],
        ),
        _species(
            "Ar", {"Ar": 1}, 1000.0,
            [2.5, 0.0, 0.0, 0.0, 0.0, -745.375, 4.366],
            [2.5, 0.0, 0.0, 0.0, 0.0, -745.375, 4.366],
        ),
        _species(
            "CO2", {"C": 1, "O": 2}, 1000.0,
            [3.85746029, 0.00441437026, -2.21481404e-06, 5.23490188e-10, -4.72084164e-14, -48759.166, 2.27163806],
            [2.35677352, 0.00898459677, -7.12356269e-06, 2.45919022e-09, -1.43699548e-13, -48371.9697, 9.90105222],
        ),
        _species(
            "H2O", {"H": 2, "O": 1}, 1000.0,
            [3.03399249, 0.00217691804, -1.64072518e-07, -9.7041987e-11, 1.68200992e-14, -30004.2971, 4.9667701],
            [4.19864056, -0.0020364341, 6.52040211e-06, -5.48797062e-09, 1.77197817e-12, -30293.7267, -0.849032208],
        ),
        _species(
            "CH4", {"C": 1, "H": 4}, 1000.0,
            [0.074851495, 0.0133909467, -5.73285809e-06, 1.22292535e-09, -1.0181523e-13, -9468.34459, 18.437318],
            [5.14987613, -0.0136709788, 4.91800599e-05, -4.84743026e-08, 1.66693956e-11, -10246.6476, -4.64130376],
        ),
        _species(
            "C2H6", {"C": 2, "H": 6}, 1000.0,
            [1.0718815, 0.0216852677, -1.00256067e-05, 2.21412001e-09, -1.9000289e-13, -11426.3932, 15.1156107],
            [4.29142492, -0.0055015427, 5.99438288e-05, -7.08466285e-08, 2.68685771e-11, -11522.2055, 2.66682316],
        ),
        _species(
            "C3H8", {"C": 3, "H": 8}, 1000.0,
            [7.5341368, 0.018872239, -6.2718491e-06, 9.1475649e-10, -4.7838069e-14, -16467.516, -17.892349],
            [0.93355381, 0.026424579, 6.1059727e-06, -2.1977499e-08, 9.5149253e-12, -13958.52, 19.201691],
        ),
    ]
}

# Dry air by mole fraction (ISO 2314 style composition, normalised).
DRY_AIR = {"N2": 0.78084, "O2": 0.20948, "Ar": 0.00934, "CO2": 0.00034}


def _normalise(x):
    total = sum(x.values())
    return {k: v / total for k, v in x.items() if v > 0}


@dataclass
class IdealGasMixture:
    """Ideal-gas mixture of fixed composition, given in mole fractions."""

    mole_fractions: dict
    _species: list = field(init=False, repr=False)

    def __post_init__(self):
        self.mole_fractions = _normalise(self.mole_fractions)
        self._species = [(SPECIES[k], x) for k, x in self.mole_fractions.items()]
        self.mw = sum(sp.mw * x for sp, x in self._species)
        self.R = R_UNIVERSAL / self.mw

    def cp(self, t):
        return sum(x * sp.cp(t) for sp, x in self._species) / self.mw

    def h(self, t):
        return sum(x * sp.h(t) for sp, x in self._species) / self.mw

    def s(self, t, p):
        """Specific entropy (excluding the constant mixing term), J/(kg K)."""
        return sum(x * sp.s0(t) for sp, x in self._species) / self.mw - self.R * math.log(p / P_REF)

    def gamma(self, t):
        cp = self.cp(t)
        return cp / (cp - self.R)

    def t_from_h(self, h, t_guess=1000.0):
        t = t_guess
        for _ in range(50):
            dt = (h - self.h(t)) / self.cp(t)
            t = max(t + dt, 0.5 * t)  # damp steps that would overshoot below zero
            if abs(dt) < 1e-9:
                return t
        raise RuntimeError("t_from_h did not converge")

    def t_from_s(self, s, p, t_guess=1000.0):
        t = t_guess
        for _ in range(50):
            dt = (s - self.s(t, p)) * t / self.cp(t)
            t = max(t + dt, 0.5 * t)  # damp steps that would overshoot below zero
            if abs(dt) < 1e-9:
                return t
        raise RuntimeError("t_from_s did not converge")

    def mass_fractions(self):
        return {k: x * SPECIES[k].mw / self.mw for k, x in self.mole_fractions.items()}


def saturation_pressure(t):
    """Saturation vapour pressure of water over liquid, Pa (Buck 1996), valid ~ -40..100 C."""
    tc = t - 273.15
    return 611.21 * math.exp((18.678 - tc / 234.5) * (tc / (257.14 + tc)))


def humid_air(t, p, rh):
    """Humid air mixture for ambient temperature t (K), pressure p (Pa) and relative humidity 0..1."""
    x_h2o = rh * saturation_pressure(t) / p
    x = {k: v * (1 - x_h2o) for k, v in _normalise(DRY_AIR).items()}
    x["H2O"] = x_h2o
    return IdealGasMixture(x)


@dataclass
class PerfectGas:
    """Calorically perfect gas (constant cp and gamma), for textbook-style hand checks.

    Enthalpy is sensible enthalpy referenced to T_REF.
    """

    cp_value: float
    gamma_value: float

    def __post_init__(self):
        self.R = self.cp_value * (1 - 1 / self.gamma_value)

    def cp(self, t):
        return self.cp_value

    def gamma(self, t):
        return self.gamma_value

    def h(self, t):
        return self.cp_value * (t - T_REF)

    def s(self, t, p):
        return self.cp_value * math.log(t / T_REF) - self.R * math.log(p / P_REF)

    def t_from_h(self, h, t_guess=None):
        return T_REF + h / self.cp_value

    def t_from_s(self, s, p, t_guess=None):
        return T_REF * math.exp((s + self.R * math.log(p / P_REF)) / self.cp_value)
