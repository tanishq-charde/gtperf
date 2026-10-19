"""Two-spool separate-exhaust turbofan at a design point.

Station numbering follows the usual aero convention (SAE AS755 style):

    0 free stream   2 fan face        13 fan bypass exit   18 bypass nozzle throat
    21 fan core exit 25 HPC inlet     3 HPC exit           4 combustor exit
    41 HPT rotor inlet (after cooling air)  45 HPT exit / LPT inlet
    5 LPT exit      8 core nozzle throat

The LP spool is the fan (and booster) driven by the LPT; the HP spool is the
high-pressure compressor driven by the HPT. Each turbine's exit pressure is
found from its spool power balance, as in the twin-shaft industrial cycle.
Nozzles are convergent unless `ideal_nozzles` is set, in which case the jets
expand fully to ambient pressure.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from scipy.optimize import brentq

from .components import Combustor, Compressor, Duct, FlowState, Turbine, mix
from .fuel import Fuel
from .gas import SPECIES, T_REF, humid_air

G0 = 9.80665


def isa(altitude_m, dt=0.0):
    """ISA static temperature (K) and pressure (Pa) up to 20 km, with a temperature offset dt."""
    if altitude_m <= 11000.0:
        t = 288.15 - 0.0065 * altitude_m
        p = 101325.0 * (t / 288.15) ** 5.25588
    else:
        t = 216.65
        p = 22632.06 * math.exp(-(altitude_m - 11000.0) / 6341.62)
    return t + dt, p


@dataclass(frozen=True)
class Flight:
    altitude_m: float = 0.0
    mach: float = 0.0
    isa_dt: float = 0.0
    rh: float = 0.0  # dry air by default, as aero performance usually quotes
    gas: object = None  # override the air model, e.g. with a PerfectGas

    def static(self):
        return isa(self.altitude_m, self.isa_dt)

    def air(self):
        t, p = self.static()
        return self.gas if self.gas is not None else humid_air(t, p, self.rh)

    def free_stream(self, w):
        """Free-stream total state and flight velocity for mass flow w."""
        t, p = self.static()
        gas = self.air()
        v = self.mach * math.sqrt(_gamma(gas, t) * gas.R * t)
        h0 = gas.h(t) + 0.5 * v * v
        t0 = gas.t_from_h(h0, t_guess=t)
        p0 = p * math.exp((gas.s(t0, p) - gas.s(t, p)) / gas.R)  # isentropic stagnation
        return FlowState(w, t0, p0, gas), v


SEA_LEVEL_STATIC = Flight()


def _gamma(gas, t):
    return gas.gamma(t)


class Kerosene(Fuel):
    """Jet fuel as a CxHy surrogate with a specified LHV (Jet A-1: C12H23, 43.2 MJ/kg).

    Products come from the atom balance; the fuel's absolute enthalpy is set so that
    complete combustion releases exactly `lhv` at T_REF with the same species data.
    """

    def __init__(self, c=12.0, h=23.0, lhv=43.2e6, cp_liquid=2000.0, name="Jet A-1"):
        self.name = name
        self.c, self.hy = c, h
        self.mw = (c * 12.011 + h * 1.008) / 1000  # kg/mol, as in the species data
        self.lhv = lhv
        self.cp_liquid = cp_liquid
        o2, co2, h2o = self._per_mole_reaction()
        h_prod = co2 * SPECIES["CO2"].h(T_REF) + h2o * SPECIES["H2O"].h(T_REF) - o2 * SPECIES["O2"].h(T_REF)
        self._h_ref = h_prod / self.mw + lhv  # J/kg, absolute, liquid at T_REF

    def _per_mole_reaction(self):
        return self.c + self.hy / 4, self.c, self.hy / 2

    def products(self, air, far):
        from .gas import IdealGasMixture

        n = {k: x / air.mw for k, x in air.mole_fractions.items()}
        n_fuel = far / self.mw
        o2, co2, h2o = self._per_mole_reaction()
        n["O2"] = n.get("O2", 0.0) - o2 * n_fuel
        n["CO2"] = n.get("CO2", 0.0) + co2 * n_fuel
        n["H2O"] = n.get("H2O", 0.0) + h2o * n_fuel
        if n["O2"] < 0:
            raise ValueError(f"fuel-air ratio {far:.4f} is richer than stoichiometric")
        return IdealGasMixture(n)

    def heat_release_term(self, t_fuel, eta):
        return self._h_ref + self.cp_liquid * (t_fuel - T_REF) - (1 - eta) * self.lhv


@dataclass
class Jet:
    """Nozzle exit: static conditions, velocity, area and pressure thrust."""

    w: float
    v: float
    t: float
    p: float
    area: float
    choked: bool
    p_amb: float

    @property
    def gross_thrust(self):
        return self.w * self.v + self.area * (self.p - self.p_amb)


def nozzle(state, p_amb, efficiency=1.0, ideal=False):
    """Expand a total state through a convergent nozzle (or fully, if ideal) to ambient pressure.

    `efficiency` applies to the enthalpy drop: h0 - h = efficiency * (h0 - h_isentropic).
    """
    gas, h0, s0 = state.gas, state.h, state.s
    if state.w == 0:
        return Jet(0.0, 0.0, state.t, p_amb, 0.0, False, p_amb)

    def exit_at(p):
        t_is = gas.t_from_s(s0, p, t_guess=state.t * (p / state.p) ** 0.25)
        h = h0 - efficiency * (h0 - gas.h(t_is))
        t = gas.t_from_h(h, t_guess=t_is)
        v = math.sqrt(max(2 * (h0 - h), 0.0))
        return t, v

    def mach_minus_one(p):
        t, v = exit_at(p)
        return v / math.sqrt(_gamma(gas, t) * gas.R * t) - 1

    p_exit, choked = p_amb, False
    if not ideal and p_amb < state.p and mach_minus_one(p_amb) > 0:
        p_exit = brentq(mach_minus_one, p_amb, state.p * 0.999, xtol=1e-6)
        choked = True
    if p_exit >= state.p:
        return Jet(state.w, 0.0, state.t, state.p, math.inf, False, p_amb)
    t, v = exit_at(p_exit)
    rho = p_exit / (gas.R * t)
    return Jet(state.w, v, t, p_exit, state.w / (rho * v), choked, p_amb)


@dataclass
class TurbofanResult:
    stations: dict
    flight: Flight
    v0: float
    fuel_flow: float
    fuel_lhv: float
    core_jet: Jet
    bypass_jet: Jet
    fan_power: float
    hpc_power: float
    info: dict = field(default_factory=dict)

    @property
    def air_flow(self):
        return self.stations["2"].w

    @property
    def ram_drag(self):
        return self.air_flow * self.v0

    @property
    def gross_thrust(self):
        return self.core_jet.gross_thrust + self.bypass_jet.gross_thrust

    @property
    def net_thrust(self):
        return self.gross_thrust - self.ram_drag

    @property
    def specific_thrust(self):
        """Net thrust per unit total intake flow, N s/kg (m/s)."""
        return self.net_thrust / self.air_flow

    @property
    def tsfc(self):
        """Thrust-specific fuel consumption, kg/(N s). Multiply by 1e6 for mg/(N s)."""
        return self.fuel_flow / self.net_thrust

    @property
    def jet_power(self):
        """Rate of kinetic energy added to the flow, W (pressure thrust treated as equivalent jet velocity)."""
        def ke(j):
            if j.w == 0:
                return 0.0
            v_eq = j.gross_thrust / j.w
            return 0.5 * j.w * v_eq**2
        return ke(self.core_jet) + ke(self.bypass_jet) - 0.5 * self.air_flow * self.v0**2

    @property
    def thermal_efficiency(self):
        return self.jet_power / (self.fuel_flow * self.fuel_lhv)

    @property
    def propulsive_efficiency(self):
        return self.net_thrust * self.v0 / self.jet_power if self.v0 > 0 else 0.0

    @property
    def overall_efficiency(self):
        return self.net_thrust * self.v0 / (self.fuel_flow * self.fuel_lhv)

    @property
    def jet_velocity_ratio(self):
        """Bypass over core equivalent jet velocity."""
        return (self.bypass_jet.gross_thrust / self.bypass_jet.w) / (self.core_jet.gross_thrust / self.core_jet.w)

    def summary(self):
        return {
            "net_thrust_kN": self.net_thrust / 1e3,
            "tsfc_mg_per_Ns": self.tsfc * 1e6,
            "specific_thrust_m_s": self.specific_thrust,
            "air_flow_kg_s": self.air_flow,
            "fuel_flow_kg_s": self.fuel_flow,
            "bypass_ratio": self.info["bpr"],
            "overall_pressure_ratio": self.stations["3"].p / self.stations["2"].p,
            "tet_K": self.stations["4"].t,
            "thermal_efficiency_pct": 100 * self.thermal_efficiency,
            "propulsive_efficiency_pct": 100 * self.propulsive_efficiency,
            "overall_efficiency_pct": 100 * self.overall_efficiency,
            "core_jet_m_s": self.core_jet.v,
            "bypass_jet_m_s": self.bypass_jet.v,
        }

    def __str__(self):
        rows = [f"{k:28s}{v:12.4f}" for k, v in self.summary().items()]
        rows.append("")
        rows.append(f"{'station':>8s}{'W kg/s':>10s}{'T K':>10s}{'P kPa':>10s}")
        rows += [f"{k:>8s}{s.w:10.3f}{s.t:10.2f}{s.p / 1e3:10.2f}" for k, s in self.stations.items()]
        return "\n".join(rows)


@dataclass
class Turbofan:
    """Two-spool, separate-exhaust turbofan cycle.

    Pressure ratios: `fan_pr` for the bypass stream, `fan_core_pr` for the fan root
    plus booster on the core stream (defaults to `fan_pr`), then `hpc_pr`.
    Compressor efficiencies are polytropic and turbine efficiencies isentropic,
    unless the `polytropic` flags say otherwise.
    """

    bpr: float
    fan_pr: float
    hpc_pr: float
    tet: float  # combustor exit temperature, K
    fan_core_pr: float | None = None
    fan_eff: float = 0.91
    booster_eff: float = 0.90
    hpc_eff: float = 0.90
    hpt_eff: float = 0.89
    lpt_eff: float = 0.91
    compressor_polytropic: bool = True
    turbine_polytropic: bool = False
    intake_recovery: float = 0.995
    bypass_duct_dp: float = 0.015
    core_duct_dp: float = 0.01  # between booster and HPC
    combustor_dp: float = 0.04
    combustor_eff: float = 0.999
    turbine_duct_dp: float = 0.01  # between HPT and LPT
    jet_pipe_dp: float = 0.01
    cooling_fraction: float = 0.10  # of HPC flow, bled at HPC exit and mixed at the HPT rotor inlet
    hp_offtake: float = 0.0  # W, accessory and electrical power from the HP spool
    customer_bleed: float = 0.0  # fraction of HPC flow taken overboard
    mechanical_efficiency: float = 0.99
    nozzle_efficiency: float = 1.0
    ideal_nozzles: bool = False
    fuel: object = None

    def run(self, w, flight=None):
        flight = flight or SEA_LEVEL_STATIC
        fuel = self.fuel or Kerosene()
        st = {}
        free, v0 = flight.free_stream(w)
        st["0"] = free
        st["2"] = Duct(1 - self.intake_recovery).run(free)
        w_core = w / (1 + self.bpr)
        w_bypass = w - w_core

        fan = Compressor(self.fan_pr, self.fan_eff, self.compressor_polytropic)
        st["13"], fan_bypass_work = fan.run(replace(st["2"], w=w_bypass))
        core_pr = self.fan_core_pr or self.fan_pr
        core_in = replace(st["2"], w=w_core)
        # fan root and booster as one compressor at the booster efficiency when they differ
        core_eff = self.fan_eff if core_pr == self.fan_pr else self.booster_eff
        st["21"], fan_core_work = Compressor(core_pr, core_eff, self.compressor_polytropic).run(core_in)
        st["25"] = Duct(self.core_duct_dp).run(st["21"])
        st["3"], hpc_work = Compressor(self.hpc_pr, self.hpc_eff, self.compressor_polytropic).run(st["25"])

        bleed = replace(st["3"], w=st["3"].w * self.cooling_fraction)
        to_burner = replace(st["3"], w=st["3"].w * (1 - self.cooling_fraction - self.customer_bleed))
        st["31"] = to_burner
        st["4"], fuel_flow = Combustor(fuel, self.tet, self.combustor_dp, self.combustor_eff).run(to_burner)
        st["41"] = mix(st["4"], bleed) if bleed.w > 0 else st["4"]

        fan_power = fan_bypass_work + fan_core_work
        hpt_work = (hpc_work + self.hp_offtake) / self.mechanical_efficiency
        st["45"] = self._expand(Turbine(self.hpt_eff, self.turbine_polytropic), st["41"], hpt_work)
        st["48"] = Duct(self.turbine_duct_dp).run(st["45"])
        lpt_work = fan_power / self.mechanical_efficiency
        st["5"] = self._expand(Turbine(self.lpt_eff, self.turbine_polytropic), st["48"], lpt_work)
        st["8"] = Duct(self.jet_pipe_dp).run(st["5"])
        st["18"] = Duct(self.bypass_duct_dp).run(st["13"])

        _, p_amb = flight.static()
        if st["8"].p <= p_amb:
            raise ValueError("core exhaust total pressure is below ambient: the LPT cannot drive this fan")
        core_jet = nozzle(st["8"], p_amb, self.nozzle_efficiency, self.ideal_nozzles)
        bypass_jet = nozzle(st["18"], p_amb, self.nozzle_efficiency, self.ideal_nozzles)
        return TurbofanResult(st, flight, v0, fuel_flow, fuel.lhv, core_jet, bypass_jet, fan_power, hpc_work,
                              info={"bpr": self.bpr, "hpt_work": hpt_work, "lpt_work": lpt_work})

    @staticmethod
    def _expand(turbine, state, work):
        """Turbine exit state that delivers `work` (W)."""
        def residual(p_out):
            return turbine.run(state, p_out)[1] - work

        p_low = state.p * 1e-3
        if residual(p_low) < 0:
            raise ValueError("turbine cannot deliver the required work")
        p_out = brentq(residual, p_low, state.p * (1 - 1e-12), xtol=1e-6, rtol=1e-12)
        return turbine.run(state, p_out)[0]

    def size_for_thrust(self, thrust, flight=None):
        """Intake mass flow that gives the required net thrust.

        The cycle scales linearly with flow except for the fixed HP power offtake, so a few
        rescaling steps converge.
        """
        w = 100.0
        for _ in range(30):
            w_new = w * thrust / self.run(w, flight).net_thrust
            if abs(w_new - w) < 1e-10 * w:
                return w_new
            w = w_new
        raise RuntimeError("size_for_thrust did not converge")
