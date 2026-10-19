"""Two-spool separate-exhaust turbofan at a design point.

Station numbering follows the usual aero convention (SAE AS755 style):

    0 free stream   2 fan face        13 fan bypass exit   18 bypass nozzle throat
    21 fan core exit 25 HPC inlet     3 HPC exit           4 combustor exit
    41 HPT rotor inlet (after NGV cooling air)   44 HPT rotor exit
    45 after HPT rotor cooling air / LPT duct inlet   48 LPT duct exit
    49 LPT inlet (after LPT cooling air)   5 LPT exit   8 core nozzle
    26 HPC interstage bleed for LPT cooling (only when bled mid-stage)

The LP spool is the fan (and booster) driven by the LPT; the HP spool is the
high-pressure compressor driven by the HPT. Each turbine's exit pressure is
found from its spool power balance, as in the twin-shaft industrial cycle.

Cooling air is bled from the HPC and returned in three places: NGV cooling
mixes before the HPT rotor and so does HPT work; rotor cooling mixes after the
rotor and does none; LPT cooling (from HPC exit or an interstage bleed) mixes
at the LPT inlet. A single `cooling_fraction` is all NGV cooling, which was the
original single-stream model.

Nozzles are convergent unless `ideal_nozzles` is set, in which case the jets
expand fully to ambient pressure; `Nozzle` adds convergent-divergent nozzles
and a discharge coefficient. Off-design matching is in `aero_offdesign`.
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
    area: float  # exit flow area (effective), m2
    choked: bool
    p_amb: float
    throat_area: float | None = None  # effective throat area, m2 (the exit area for a convergent nozzle)
    cd: float = 1.0  # discharge coefficient: effective / geometric throat area

    def __post_init__(self):
        if self.throat_area is None:
            self.throat_area = self.area

    @property
    def gross_thrust(self):
        return self.w * self.v + self.area * (self.p - self.p_amb)

    @property
    def geometric_throat_area(self):
        return self.throat_area / self.cd

    @property
    def area_ratio(self):
        """Exit over throat area: 1 for a convergent nozzle."""
        return self.area / self.throat_area


def nozzle(state, p_amb, efficiency=1.0, ideal=False):
    """Expand a total state through a convergent nozzle (or fully, if ideal) to ambient pressure.

    `efficiency` applies to the enthalpy drop: h0 - h = efficiency * (h0 - h_isentropic).
    """
    return Nozzle("ideal" if ideal else "convergent", efficiency).jet(state, p_amb)


@dataclass(frozen=True)
class Nozzle:
    """Exhaust nozzle geometry and losses.

    kind "convergent": the exit is the throat; above the critical pressure ratio the flow chokes and the
    excess pressure gives pressure thrust A (p_exit - p_amb). kind "cd" (convergent-divergent): a throat
    followed by a divergent section of exit/throat area ratio `area_ratio`; None sizes the exit for full
    expansion to ambient at the design point. With the area ratio fixed (always so off design), the
    jet is under- or over-expanded and the pressure thrust term carries the difference (negative when
    over-expanded). Shocks inside the divergent section and flow separation are not modelled, so strongly
    over-expanded points are optimistic about neither and pessimistic about the pressure thrust. Below
    the pressure ratio that chokes the throat, a C-D nozzle is a subsonic venturi exiting at ambient.
    kind "ideal" expands fully with no throat (the old `ideal_nozzles`).

    `efficiency` applies to the enthalpy drop, h0 - h = efficiency (h0 - h_s): the velocity coefficient
    is its square root. `cd` is the discharge coefficient (effective over geometric throat area), a
    number or a function of nozzle pressure ratio P0/p_amb. Thrust follows the effective areas, so a
    constant cd changes only the geometric area that has to be built; off design the geometric area is
    fixed and an NPR-dependent cd changes the flow the nozzle passes.
    """

    kind: str = "convergent"
    efficiency: float = 1.0
    cd: object = 1.0
    area_ratio: float | None = None

    def __post_init__(self):
        if self.kind not in ("convergent", "cd", "ideal"):
            raise ValueError(f"unknown nozzle kind {self.kind!r}")

    def discharge_coefficient(self, npr):
        return self.cd(npr) if callable(self.cd) else self.cd

    def _expansion(self, state):
        gas, h0, s0 = state.gas, state.h, state.s
        efficiency = self.efficiency

        def exit_at(p):
            t_is = gas.t_from_s(s0, p, t_guess=state.t * (p / state.p) ** 0.25)
            h = h0 - efficiency * (h0 - gas.h(t_is))
            t = gas.t_from_h(h, t_guess=t_is)
            v = math.sqrt(max(2 * (h0 - h), 0.0))
            return t, v

        def mach_minus_one(p):
            t, v = exit_at(p)
            return v / math.sqrt(_gamma(gas, t) * gas.R * t) - 1

        def flux(p):
            t, v = exit_at(p)
            return p / (gas.R * t) * v

        return exit_at, mach_minus_one, flux

    def _solve(self, state, p_amb):
        """Exit pressure, throat mass flux (kg/s per m2 of effective throat), area ratio and choking."""
        exit_at, mach_minus_one, flux = self._expansion(state)
        if self.kind == "ideal":
            return p_amb, None, None, False
        if self.kind == "convergent":
            if p_amb < state.p and mach_minus_one(p_amb) > 0:
                p_t = brentq(mach_minus_one, p_amb, state.p * 0.999, xtol=1e-6)
                return p_t, flux(p_t), 1.0, True
            return p_amb, (flux(p_amb) if p_amb < state.p else 0.0), 1.0, False
        if p_amb >= state.p:
            return p_amb, 0.0, self.area_ratio or 1.0, False
        p_star = brentq(mach_minus_one, state.p * 0.05, state.p * 0.999, xtol=1e-6)
        g_star = flux(p_star)
        ar = self.area_ratio
        if ar is None:  # full expansion: a C-D nozzle only diverges when the throat is choked
            if p_amb >= p_star:
                return p_amb, flux(p_amb), 1.0, False
            return p_amb, g_star, g_star / flux(p_amb), True
        if p_amb > p_star and ar * flux(p_amb) <= g_star:  # subsonic venturi, exit at ambient
            return p_amb, ar * flux(p_amb), ar, False
        if ar <= 1.0:
            return p_star, g_star, 1.0, True
        p_e = brentq(lambda p: flux(p) - g_star / ar, p_star * 1e-4, p_star, xtol=1e-6)  # supersonic branch
        return p_e, g_star, ar, True

    def mass_flux(self, state, p_amb):
        """Mass flow per unit geometric throat area at total state `state` exhausting to p_amb."""
        if self.kind == "ideal":
            raise ValueError("an ideal nozzle has no throat to fix off design")
        _, g, _, _ = self._solve(state, p_amb)
        return g * self.discharge_coefficient(state.p / p_amb)

    def jet(self, state, p_amb):
        if state.w == 0:
            return Jet(0.0, 0.0, state.t, p_amb, 0.0, False, p_amb)
        p_exit, g_throat, ar, choked = self._solve(state, p_amb)
        if p_exit >= state.p:
            return Jet(state.w, 0.0, state.t, state.p, math.inf, False, p_amb)
        t, v = self._expansion(state)[0](p_exit)
        rho = p_exit / (state.gas.R * t)
        area = state.w / (rho * v)
        cd = self.discharge_coefficient(state.p / p_amb)
        throat = area if self.kind != "cd" else state.w / g_throat
        return Jet(state.w, v, t, p_exit, area, choked, p_amb, throat, cd)


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
    cooling_fraction: float = 0.10  # single-stream HPT cooling: all of it NGV cooling (see ngv_cooling)
    ngv_cooling: float | None = None  # of HPC inlet flow, from HPC exit, mixed before the HPT rotor
    rotor_cooling: float | None = None  # of HPC inlet flow, from HPC exit, mixed after the HPT rotor
    lpt_cooling: float = 0.0  # of HPC inlet flow, mixed at the LPT inlet
    lpt_cooling_bleed: float = 1.0  # where it is bled: fraction of the HPC pressure ratio in log terms, 1 = exit
    hp_offtake: float = 0.0  # W, accessory and electrical power from the HP spool
    customer_bleed: float = 0.0  # fraction of HPC flow taken overboard
    mechanical_efficiency: float = 0.99
    nozzle_efficiency: float = 1.0
    ideal_nozzles: bool = False
    core_nozzle: Nozzle | None = None  # default: convergent (or ideal) with nozzle_efficiency
    bypass_nozzle: Nozzle | None = None
    fuel: object = None

    def cooling_flows(self):
        """(NGV, rotor) cooling fractions. Giving either split value overrides `cooling_fraction`."""
        if self.ngv_cooling is None and self.rotor_cooling is None:
            return self.cooling_fraction, 0.0
        return self.ngv_cooling or 0.0, self.rotor_cooling or 0.0

    def nozzles(self):
        """(core, bypass) nozzles."""
        default = Nozzle("ideal" if self.ideal_nozzles else "convergent", self.nozzle_efficiency)
        return self.core_nozzle or default, self.bypass_nozzle or default

    @property
    def hpc_bleeds_midstage(self):
        return self.lpt_cooling > 0 and self.lpt_cooling_bleed < 1

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
        hpc = Compressor(self.hpc_pr, self.hpc_eff, self.compressor_polytropic)
        w25 = st["25"].w
        ngv, rotor = self.cooling_flows()
        lpt = self.lpt_cooling
        if self.hpc_bleeds_midstage:
            pr_mid = self.hpc_pr**self.lpt_cooling_bleed
            mid, work_front = replace(hpc, pressure_ratio=pr_mid).run(st["25"])
            st["3"], work_rear = replace(hpc, pressure_ratio=self.hpc_pr / pr_mid).run(replace(mid, w=w25 * (1 - lpt)))
            hpc_work = work_front + work_rear
            st["26"] = lpt_air = replace(mid, w=w25 * lpt)
        else:
            st["3"], hpc_work = hpc.run(st["25"])
            lpt_air = replace(st["3"], w=w25 * lpt)

        ngv_air = replace(st["3"], w=w25 * ngv)
        rotor_air = replace(st["3"], w=w25 * rotor)
        to_burner = replace(st["3"], w=w25 * (1 - ngv - rotor - self.customer_bleed - lpt))
        st["31"] = to_burner
        st["4"], fuel_flow = Combustor(fuel, self.tet, self.combustor_dp, self.combustor_eff).run(to_burner)
        st["41"] = mix(st["4"], ngv_air) if ngv_air.w > 0 else st["4"]

        fan_power = fan_bypass_work + fan_core_work
        hpt_work = (hpc_work + self.hp_offtake) / self.mechanical_efficiency
        st["44"] = self._expand(Turbine(self.hpt_eff, self.turbine_polytropic), st["41"], hpt_work)
        st["45"] = mix(st["44"], rotor_air) if rotor_air.w > 0 else st["44"]
        st["48"] = Duct(self.turbine_duct_dp).run(st["45"])
        st["49"] = mix(st["48"], lpt_air) if lpt_air.w > 0 else st["48"]
        lpt_work = fan_power / self.mechanical_efficiency
        st["5"] = self._expand(Turbine(self.lpt_eff, self.turbine_polytropic), st["49"], lpt_work)
        st["8"] = Duct(self.jet_pipe_dp).run(st["5"])
        st["18"] = Duct(self.bypass_duct_dp).run(st["13"])

        _, p_amb = flight.static()
        if st["8"].p <= p_amb:
            raise ValueError("core exhaust total pressure is below ambient: the LPT cannot drive this fan")
        core_nozzle, bypass_nozzle = self.nozzles()
        core_jet = core_nozzle.jet(st["8"], p_amb)
        bypass_jet = bypass_nozzle.jet(st["18"], p_amb)
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
