"""Off-design matching and spool transients of the two-spool separate-exhaust turbofan.

`TurbofanEngine` is built from a `Turbofan` design point. The design point fixes each component's
reference: compressor corrected flows and pressure ratios on their maps, turbine flow capacities,
isentropic efficiencies and pressure ratios, and the nozzle throat (and exit) areas. Off design those
geometric quantities stay fixed and the solver finds the operating point where they all agree.

Components:

- fan: the NPSS HBTF fan map scaled to the bypass pressure ratio, on total fan face corrected flow.
  The fan root is assumed to give the core stream the same pressure ratio and efficiency as the tip;
- booster (only when `fan_core_pr` differs from `fan_pr`): the NPSS LPC map on the core flow at fan
  exit conditions. Fan root plus booster run as one core-side compression whose efficiency is the
  design value times the log-pressure-ratio weighted map efficiencies, which reproduces the design
  model's single core compressor exactly;
- HPC: the NPSS HPC map. Map efficiencies are isentropic, so off design every compressor runs with an
  isentropic efficiency (design value times map relative efficiency);
- HPT and LPT: efficiency from the tabulated NPSS turbine maps against corrected speed and pressure
  ratio. Flow capacity uses the Stodola ellipse law times the map's (small) speed effect, because the
  NPSS turbine maps are tabulated only over scaled pressure ratios 3 to 8 and are flat (choked) across
  that range: extrapolated to the low pressure ratios of idle, especially for the LPT, they would keep
  the turbines choked, whereas a real turbine unchokes as the ellipse law describes;
- nozzles: throat (and C-D exit) areas fixed at design; each must pass the flow arriving at it.

With `stage_designs`, all five maps are instead calculated from this engine's own design by stage stacking
(`gtperf.mapgen`), and turbine flow capacity comes from those maps directly.

Unknowns (all relative to design): LP and HP mechanical speeds, fan, booster and HPC R-lines, bypass
ratio, HPT and LPT pressure ratios, and TET when the power setting is not TET itself. Residuals:
booster and HPC corrected flow against their maps, HPT and LPT flow capacities, core and bypass nozzle
flow capacities, and the two spool power balances (steady state only). Power setting: TET, LP spool
speed N1, net thrust or fuel flow.

Corrected flow is the non-dimensional capacity W*sqrt(R*T/gamma)/P and corrected speed uses gamma*R*T,
so changes in gas properties are carried, as in `offdesign`.

`simulate` integrates the two spool speeds through time from shaft inertias,
dN/dt = (turbine power * eta_m - compressor power - offtake) / (I * N * (2 pi / 60)**2),
with the gas path solved quasi-steadily at every step: there are no volume (mass storage) or heat
soakage dynamics, which shape the first fraction of a second of a real transient.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np
from scipy.optimize import root

from .aero import SEA_LEVEL_STATIC, Flight, Kerosene, Turbofan, TurbofanResult
from .components import Combustor, Compressor, Duct, Turbine, mix
from .maps import TabulatedCompressorMap, TabulatedTurbineMap, ellipse_capacity

# Design R-lines for the fan and booster maps when the design point is at cruise. The NPSS maps put the
# design on R-line 2.2 and 2.15, which leaves the fan about 6 % constant-speed surge margin at cruise; at
# take-off the bypass nozzle unchokes and the fan working line moves towards surge, past it on those
# R-lines. Moving the cruise point towards choke keeps a take-off margin, as fan design practice does.
FAN_DESIGN_RLINE = 2.6
BOOSTER_DESIGN_RLINE = 2.4

VARIABLES = ("n1", "n2", "beta_fan", "bpr", "beta_booster", "beta_hpc", "pr_hpt", "pr_lpt", "tet")


def _capacity(state):
    gas = state.gas
    return state.w * math.sqrt(gas.R * state.t / gas.gamma(state.t)) / state.p


def _sonic(state):
    return state.gas.gamma(state.t) * state.gas.R * state.t


def _isentropic_drop(state, p_out):
    gas = state.gas
    t_s = gas.t_from_s(state.s, p_out, t_guess=state.t * (p_out / state.p) ** 0.25)
    return state.h - gas.h(t_s)


def _compressor_eff(s_in, s_out):
    """Isentropic efficiency of a compression from s_in to s_out."""
    return -_isentropic_drop(s_in, s_out.p) / (s_out.h - s_in.h)


def _turbine_eff(s_in, s_out):
    return (s_in.h - s_out.h) / _isentropic_drop(s_in, s_out.p)


@dataclass(frozen=True)
class Spools:
    """Mechanical speeds at the design point and polar moments of inertia of the two spools.

    The defaults are assumptions, not data, for an engine of about 100 to 120 kN take-off thrust with a
    fan near 1.9 m: LP spool (fan, booster, LPT) 50 kg m2, HP spool (HPC, HPT) 3 kg m2, design (cruise)
    speeds 3800 and 16500 rpm. Inertia scales roughly with flow**1.5 to flow**2 for similar engines.
    """

    n1_rpm: float = 3800.0
    n2_rpm: float = 16500.0
    lp_inertia: float = 50.0  # kg m2
    hp_inertia: float = 3.0  # kg m2


@dataclass
class Transient:
    """Time histories from `TurbofanEngine.simulate`. Speeds are relative to design mechanical speed."""

    t: np.ndarray
    n1: np.ndarray
    n2: np.ndarray
    tet: np.ndarray
    fuel_flow: np.ndarray
    net_thrust: np.ndarray
    surge_margin_fan: np.ndarray
    surge_margin_hpc: np.ndarray
    surge_margin_booster: np.ndarray
    hpc_flow: np.ndarray  # relative corrected flow on the HPC map
    hpc_pr: np.ndarray
    fan_flow: np.ndarray
    fan_pr: np.ndarray
    results: list = field(default_factory=list, repr=False)

    def time_to(self, fraction=0.95):
        """Time for net thrust to cover `fraction` of its change from start to end."""
        f0, f1 = self.net_thrust[0], self.net_thrust[-1]
        target = f0 + fraction * (f1 - f0)
        above = (self.net_thrust - target) * np.sign(f1 - f0) >= 0
        return float(self.t[np.argmax(above)])


class MatchError(RuntimeError):
    pass


class TurbofanEngine:
    """Off-design turbofan built from a design-point `Turbofan` cycle, flight condition and air flow."""

    def __init__(self, cycle: Turbofan, air_flow, flight=SEA_LEVEL_STATIC, fan_map=None, booster_map=None,
                 hpc_map=None, hpt_map=None, lpt_map=None, spools=None, stage_designs=None):
        if cycle.ideal_nozzles or "ideal" in (n.kind for n in cycle.nozzles()):
            raise ValueError("off-design matching needs real nozzle throats, not ideal_nozzles")
        self.cycle = cycle
        self.design_flight = flight
        self.design = d = cycle.run(air_flow, flight)
        self.fuel = cycle.fuel or Kerosene()
        self.spools = spools or Spools()
        st = d.stations
        core_pr = cycle.fan_core_pr or cycle.fan_pr
        self.has_booster = core_pr != cycle.fan_pr
        self.pr_hpt_design = st["41"].p / st["44"].p
        self.pr_lpt_design = st["49"].p / st["5"].p
        self.generated_maps = bool(stage_designs)
        if stage_designs:  # maps calculated from this design by stage stacking (gtperf.mapgen)
            from .mapgen import compressor_map, polytropic_from_isentropic, turbine_map

            def poly(e, pr):
                return e if cycle.compressor_polytropic else polytropic_from_isentropic(pr, e)

            sd = stage_designs
            fan_map = compressor_map(cycle.fan_pr, poly(cycle.fan_eff, cycle.fan_pr), sd["fan"])
            if self.has_booster:
                b_pr = core_pr / cycle.fan_pr
                booster_map = compressor_map(b_pr, poly(cycle.booster_eff, b_pr), sd["booster"])
            hpc_map = compressor_map(cycle.hpc_pr, poly(cycle.hpc_eff, cycle.hpc_pr), sd["hpc"])
            hpt_map = turbine_map(self.pr_hpt_design, _turbine_eff(st["41"], st["44"]), sd["hpt"], st["41"].t)
            lpt_map = turbine_map(self.pr_lpt_design, _turbine_eff(st["49"], st["5"]), sd["lpt"], st["49"].t)
        self.fan_map = fan_map or TabulatedCompressorMap.load("fan", cycle.fan_pr, design_beta=FAN_DESIGN_RLINE)
        self.booster_map = booster_map or (TabulatedCompressorMap.load("lpc", core_pr / cycle.fan_pr,
                                                                       design_beta=BOOSTER_DESIGN_RLINE)
                                           if self.has_booster else None)
        self.hpc_map = hpc_map or TabulatedCompressorMap.load("hpc", cycle.hpc_pr)
        self.hpt_map = hpt_map or TabulatedTurbineMap.load("hpt", self.pr_hpt_design)
        self.lpt_map = lpt_map or TabulatedTurbineMap.load("lpt", self.pr_lpt_design)

        w_core = st["21"].w
        self.ref = {
            "a2": _sonic(st["2"]), "cap2": _capacity(st["2"]),
            "a13": _sonic(st["13"]), "cap_booster": _capacity(replace(st["13"], w=w_core)),
            "a25": _sonic(st["25"]), "cap25": _capacity(st["25"]),
            "a41": _sonic(st["41"]), "cap41": _capacity(st["41"]),
            "a49": _sonic(st["49"]), "cap49": _capacity(st["49"]),
        }
        self.eff = {
            "fan": _compressor_eff(st["2"], st["13"]),
            "core": _compressor_eff(st["2"], st["21"]),
            "hpc": _compressor_eff(st["25"], st["3"]),
            "hpt": _turbine_eff(st["41"], st["44"]),
            "lpt": _turbine_eff(st["49"], st["5"]),
        }
        if cycle.hpc_bleeds_midstage:
            self.eff["hpc_front"] = _compressor_eff(st["25"], st["26"])

        _, p_amb = flight.static()
        core, bypass = cycle.nozzles()
        self.nozzles = {}
        for name, nz, jet, s in (("core", core, d.core_jet, st["8"]), ("bypass", bypass, d.bypass_jet, st["18"])):
            fixed = replace(nz, area_ratio=jet.area_ratio) if nz.kind == "cd" else nz
            self.nozzles[name] = (fixed, s.w / fixed.mass_flux(s, p_amb))  # geometric throat area
        self.hpc_power_design = d.hpc_power
        self.fan_power_design = d.fan_power
        self.thrust_design = d.net_thrust
        self.fuel_design = d.fuel_flow
        self.x_design = {"n1": 1.0, "n2": 1.0, "beta_fan": self.fan_map.beta_at_design(), "bpr": 1.0,
                         "beta_booster": self.booster_map.beta_at_design() if self.has_booster else 0.0,
                         "beta_hpc": self.hpc_map.beta_at_design(), "pr_hpt": 1.0, "pr_lpt": 1.0, "tet": 1.0}

    # --- gas path ---------------------------------------------------------------------------------

    def _turbine(self, tmap, s_in, n_mech, a_ref, pr, eff_design, cap_ref, pr_design):
        n = n_mech / math.sqrt(_sonic(s_in) / a_ref)
        eff = eff_design * tmap.efficiency(n, pr)
        out, power = Turbine(eff).run(s_in, s_in.p / pr)
        if self.generated_maps:
            cap = tmap.capacity(n, pr)
        else:
            cap = ellipse_capacity(pr) / ellipse_capacity(pr_design) * tmap.capacity(n, tmap.design_pr)
        return out, power, _capacity(s_in) / (cap_ref * cap) - 1, n

    def evaluate(self, x, flight, inlet=None):
        """Gas path for the named variables in `x` (see VARIABLES). Returns (residuals dict, result)."""
        c, ref, eff = self.cycle, self.ref, self.eff
        if inlet is None:
            inlet = self._inlet_states(flight)
        free, s2_unit, v0, p_amb = inlet
        n1, n2 = x["n1"], x["n2"]
        res = {}

        n_fan = n1 / math.sqrt(_sonic(s2_unit) / ref["a2"])
        g_fan, pr_fan, e_fan = self.fan_map.point(n_fan, x["beta_fan"])
        w2 = g_fan * ref["cap2"] / _capacity(s2_unit)
        s2 = replace(s2_unit, w=w2)
        bpr = x["bpr"] * c.bpr
        w_core = w2 / (1 + bpr)
        st = {"0": replace(free, w=w2), "2": s2}
        st["13"], fan_bypass_power = Compressor(pr_fan, eff["fan"] * e_fan, False).run(replace(s2, w=w2 - w_core))

        if self.has_booster:
            n_b = n1 / math.sqrt(_sonic(st["13"]) / ref["a13"])
            g_b, pr_b, e_b = self.booster_map.point(n_b, x["beta_booster"])
            res["booster"] = _capacity(replace(st["13"], w=w_core)) / ref["cap_booster"] / g_b - 1
            lf, lb = math.log(pr_fan), math.log(pr_b)
            core_eff = eff["core"] * (lf * e_fan + lb * e_b) / (lf + lb)
            core_pr = pr_fan * pr_b
        else:
            n_b = g_b = pr_b = None
            core_eff, core_pr = eff["core"] * e_fan, pr_fan
        st["21"], fan_core_power = Compressor(core_pr, core_eff, False).run(replace(s2, w=w_core))
        st["25"] = s25 = Duct(c.core_duct_dp).run(st["21"])

        n_hpc = n2 / math.sqrt(_sonic(s25) / ref["a25"])
        g_h, pr_h, e_h = self.hpc_map.point(n_hpc, x["beta_hpc"])
        res["hpc"] = _capacity(s25) / ref["cap25"] / g_h - 1
        w25 = s25.w
        ngv, rotor = c.cooling_flows()
        lpt = c.lpt_cooling
        if c.hpc_bleeds_midstage:
            mid, _ = Compressor(pr_h**c.lpt_cooling_bleed, eff["hpc_front"] * e_h, False).run(s25)
            s3, _ = Compressor(pr_h, eff["hpc"] * e_h, False).run(s25)
            st["3"] = replace(s3, w=w25 * (1 - lpt))
            hpc_power = w25 * (mid.h - s25.h) + st["3"].w * (s3.h - mid.h)
            st["26"] = lpt_air = replace(mid, w=w25 * lpt)
        else:
            st["3"], hpc_power = Compressor(pr_h, eff["hpc"] * e_h, False).run(s25)
            lpt_air = replace(st["3"], w=w25 * lpt)
        ngv_air = replace(st["3"], w=w25 * ngv)
        rotor_air = replace(st["3"], w=w25 * rotor)
        st["31"] = replace(st["3"], w=w25 * (1 - ngv - rotor - c.customer_bleed - lpt))
        tet = x["tet"] * c.tet
        st["4"], fuel_flow = Combustor(self.fuel, tet, c.combustor_dp, c.combustor_eff).run(st["31"])
        st["41"] = mix(st["4"], ngv_air) if ngv_air.w > 0 else st["4"]

        st["44"], hpt_power, res["hpt"], n_hpt = self._turbine(
            self.hpt_map, st["41"], n2, ref["a41"], x["pr_hpt"] * self.pr_hpt_design, eff["hpt"], ref["cap41"],
            self.pr_hpt_design)
        st["45"] = mix(st["44"], rotor_air) if rotor_air.w > 0 else st["44"]
        st["48"] = Duct(c.turbine_duct_dp).run(st["45"])
        st["49"] = mix(st["48"], lpt_air) if lpt_air.w > 0 else st["48"]
        st["5"], lpt_power, res["lpt"], n_lpt = self._turbine(
            self.lpt_map, st["49"], n1, ref["a49"], x["pr_lpt"] * self.pr_lpt_design, eff["lpt"], ref["cap49"],
            self.pr_lpt_design)
        st["8"] = Duct(c.jet_pipe_dp).run(st["5"])
        st["18"] = Duct(c.bypass_duct_dp).run(st["13"])
        for name, s in (("core", st["8"]), ("bypass", st["18"])):
            nz, area = self.nozzles[name]
            res[f"{name}_nozzle"] = s.w / (area * nz.mass_flux(s, p_amb)) - 1 if s.p > p_amb else s.p / p_amb - 2

        fan_power = fan_bypass_power + fan_core_power
        hp_surplus = hpt_power * c.mechanical_efficiency - hpc_power - c.hp_offtake
        lp_surplus = lpt_power * c.mechanical_efficiency - fan_power
        res["hp_power"] = hp_surplus / self.hpc_power_design
        res["lp_power"] = lp_surplus / self.fan_power_design

        core_jet = self.nozzles["core"][0].jet(st["8"], p_amb) if st["8"].p > p_amb else None
        bypass_jet = self.nozzles["bypass"][0].jet(st["18"], p_amb) if st["18"].p > p_amb else None
        info = {
            "bpr": bpr, "control": None,
            "n1": n1, "n2": n2, "n1_rpm": n1 * self.spools.n1_rpm, "n2_rpm": n2 * self.spools.n2_rpm,
            "n1_corrected": n_fan, "n2_corrected": n_hpc, "booster_corrected_speed": n_b,
            "hpt_corrected_speed": n_hpt, "lpt_corrected_speed": n_lpt,
            "fan_flow": g_fan, "fan_pr": pr_fan, "booster_flow": g_b, "booster_pr": pr_b, "hpc_flow": g_h,
            "hpc_pr": pr_h, "hpt_pr": x["pr_hpt"] * self.pr_hpt_design, "lpt_pr": x["pr_lpt"] * self.pr_lpt_design,
            "surge_margin_fan": self.fan_map.surge_margin_at(n_fan, x["beta_fan"]),
            "surge_margin_booster": (self.booster_map.surge_margin_at(n_b, x["beta_booster"])
                                     if self.has_booster else None),
            "surge_margin_hpc": self.hpc_map.surge_margin_at(n_hpc, x["beta_hpc"]),
            "tet": tet, "hpt_power": hpt_power, "lpt_power": lpt_power,
            "hp_surplus": hp_surplus, "lp_surplus": lp_surplus, "x": dict(x),
        }
        result = TurbofanResult(st, flight, v0, fuel_flow, self.fuel.lhv, core_jet, bypass_jet, fan_power,
                                hpc_power, info)
        return res, result

    # --- solving ----------------------------------------------------------------------------------

    def _free_variables(self, fixed):
        return [v for v in VARIABLES if v not in fixed and (v != "beta_booster" or self.has_booster)]

    def _residual_names(self, steady, control):
        names = (["booster"] if self.has_booster else []) + ["hpc", "hpt", "lpt", "core_nozzle", "bypass_nozzle"]
        if steady:
            names += ["hp_power", "lp_power"]
        if control in ("thrust", "fuel"):
            names.append(control)
        return names

    def _system(self, flight, fixed, control=None, target=None, steady=True):
        """Residual function over the free variables, plus a function giving the full result."""
        inlet = self._inlet_states(flight)
        free = self._free_variables(fixed)
        names = self._residual_names(steady, control)
        if len(free) != len(names):
            raise ValueError(f"{len(free)} unknowns {free} for {len(names)} residuals {names}")

        def full(z):
            x = dict(fixed)
            x.update(zip(free, z))
            if not self.has_booster:
                x["beta_booster"] = 0.0
            res, result = self.evaluate(x, flight, inlet)
            if control == "thrust":
                res["thrust"] = (result.net_thrust - target) / self.thrust_design
            elif control == "fuel":
                res["fuel"] = result.fuel_flow / target - 1
            return [res[k] for k in names], result

        def f(z):
            try:
                with np.errstate(all="ignore"):
                    r = np.array(full(z)[0], dtype=float)
            except (ValueError, RuntimeError, ZeroDivisionError, OverflowError, TypeError, AttributeError):
                r = None  # TypeError: complex numbers from negative pressure ratios; AttributeError: no jet
            if r is None or not np.all(np.isfinite(r)):
                return np.full(len(z), 10.0)  # outside the physical domain: push the solver back
            return r

        return free, f, full

    def _inlet_states(self, flight):
        free, v0 = flight.free_stream(1.0)
        s2 = Duct(1 - self.cycle.intake_recovery).run(free)
        return free, s2, v0, flight.static()[1]

    def _solve(self, flight, fixed, guess, control=None, target=None):
        free, f, full = self._system(flight, fixed, control, target)
        z0 = [guess[v] for v in free]
        sol = root(f, z0, method="hybr", options={"xtol": 1e-12})
        if not sol.success or np.max(np.abs(f(sol.x))) > 1e-8:
            raise MatchError(f"turbofan off-design match failed: {sol.message}")
        x = dict(fixed)
        x.update(zip(free, sol.x))
        if not self.has_booster:
            x["beta_booster"] = 0.0
        return x

    def _setting(self, control, value):
        """Fixed variables, residual control and target for a power setting."""
        if control == "tet":
            return {"tet": value / self.cycle.tet}, None, None
        if control == "n1":
            return {"n1": value}, None, None
        if control in ("thrust", "fuel"):
            return {}, control, value
        raise ValueError(f"unknown control {control!r}")

    def run(self, flight=None, tet=None, n1=None, thrust=None, fuel_flow=None, guess=None, walk=True):
        """Steady operating point. Give at most one power setting: TET (K), N1 (mechanical speed relative
        to design), net thrust (N) or fuel flow (kg/s); the default is the design TET.

        `guess` is the `info["x"]` of a nearby converged point. Without it, or when it fails, the solver
        walks from the design point: first the flight condition at design TET, then the power setting.
        With `walk=False` a failed solve from `guess` raises MatchError instead of walking from design.
        Returns a TurbofanResult whose `info` holds speeds, surge margins and matching variables.
        """
        flight = flight or self.design_flight
        given = {k: v for k, v in (("tet", tet), ("n1", n1), ("thrust", thrust), ("fuel", fuel_flow)) if v is not None}
        if len(given) > 1:
            raise ValueError("give only one power setting")
        control, value = next(iter(given.items())) if given else ("tet", self.cycle.tet)
        fixed, rc, target = self._setting(control, value)
        try:
            x = self._solve(flight, fixed, guess or self.x_design, rc, target)
        except MatchError:
            if not walk:
                raise
            x = self._walk(flight, control, value)
        return self._result(x, flight, control)

    def _result(self, x, flight, control):
        _, result = self.evaluate(x, flight)
        result.info["control"] = control
        return result

    def _walk(self, flight, control, value):
        """Continuation from the design point: flight condition first, then power setting."""
        d = self.design_flight
        x = dict(self.x_design)

        def flight_at(s):
            return Flight(d.altitude_m + s * (flight.altitude_m - d.altitude_m), d.mach + s * (flight.mach - d.mach),
                          d.isa_dt + s * (flight.isa_dt - d.isa_dt), flight.rh, flight.gas)

        tet_fixed = {"tet": x["tet"]}
        x = _march(lambda s, g: self._solve(flight_at(s), tet_fixed, g), x)
        _, here = self.evaluate(x, flight)
        start = {"tet": here.info["tet"], "n1": x["n1"], "thrust": here.net_thrust, "fuel": here.fuel_flow}[control]

        def at(s, g):
            fixed, rc, target = self._setting(control, start + s * (value - start))
            return self._solve(flight, fixed, g, rc, target)

        return _march(at, x)

    # --- transients -------------------------------------------------------------------------------

    def simulate(self, schedule, t_end, start, kind="tet", flight=None, dt=0.04):
        """Spool-speed transient from a steady starting point.

        schedule(t) gives TET (K) when kind is "tet", or fuel flow (kg/s) when kind is "fuel". `start` is a
        steady TurbofanResult from `run` (for example idle). Spool speeds are integrated by Heun's method
        with step `dt`; the gas path is re-matched quasi-steadily at each stage (no volume dynamics).
        """
        flight = flight or start.flight
        sp = self.spools
        k1 = 1 / (sp.lp_inertia * (sp.n1_rpm * 2 * math.pi / 60) ** 2)  # dn/dt = surplus * k / n, n relative
        k2 = 1 / (sp.hp_inertia * (sp.n2_rpm * 2 * math.pi / 60) ** 2)
        x = dict(start.info["x"])
        jac = {}

        def gas_path(t, n1, n2, x_guess):
            fixed = {"n1": n1, "n2": n2}
            if kind == "tet":
                fixed["tet"] = schedule(t) / self.cycle.tet
                control, target = None, None
            elif kind == "fuel":
                control, target = "fuel", schedule(t)
            else:
                raise ValueError(f"unknown schedule kind {kind!r}")
            free, f, full = self._system(flight, fixed, control, target, steady=False)
            z = _newton(f, np.array([x_guess[v] for v in free]), jac)
            res, result = full(z)
            result.info["x"].update(zip(free, z))
            result.info["control"] = kind
            return result

        def rates(r):
            return np.array([r.info["lp_surplus"] * k1 / r.info["n1"], r.info["hp_surplus"] * k2 / r.info["n2"]])

        times = np.arange(0.0, t_end + 0.5 * dt, dt)
        y = np.array([x["n1"], x["n2"]])
        rows = []
        for i, t in enumerate(times):
            r = gas_path(t, *y, x)
            rows.append(r)
            if i == len(times) - 1:
                break
            x = r.info["x"]
            dy1 = rates(r)
            r2 = gas_path(t + dt, *(y + dt * dy1), x)
            y = y + 0.5 * dt * (dy1 + rates(r2))

        def col(key):
            return np.array([r.info[key] if r.info[key] is not None else np.nan for r in rows])

        return Transient(times, col("n1"), col("n2"), col("tet"), np.array([r.fuel_flow for r in rows]),
                         np.array([r.net_thrust for r in rows]), col("surge_margin_fan"), col("surge_margin_hpc"),
                         col("surge_margin_booster"), col("hpc_flow"), col("hpc_pr"), col("fan_flow"),
                         col("fan_pr"), rows)


def _march(solve_at, x, s_min=1 / 256):
    """Advance s from 0 to 1 with solve_at(s, guess) -> x, halving the step on failure."""
    s, ds = 0.0, 0.25
    while s < 1.0:
        step = min(ds, 1.0 - s)
        try:
            x = solve_at(s + step, x)
        except MatchError:
            ds = step / 2
            if ds < s_min:
                raise
            continue
        s += step
        ds = min(2 * step, 0.5)
    return x


def _newton(f, z, jac, tol=1e-10, max_iter=30):
    """Newton's method that keeps its finite-difference Jacobian between calls (in `jac`) and refreshes it
    only when convergence stalls: transient steps change the solution little, so this saves most of the
    function evaluations."""
    r = f(z)
    for _ in range(max_iter):
        if np.max(np.abs(r)) < tol:
            return z
        if "J" not in jac or jac["J"].shape[0] != len(z):
            jac["J"] = _fd_jacobian(f, z, r)
        step = np.linalg.solve(jac["J"], -r)
        z_new = z + step
        r_new = f(z_new)
        if np.max(np.abs(r_new)) > 0.5 * np.max(np.abs(r)):
            jac["J"] = _fd_jacobian(f, z, r)
            step = np.linalg.solve(jac["J"], -r)
            lam = 1.0
            while True:
                z_new = z + lam * step
                r_new = f(z_new)
                if np.max(np.abs(r_new)) < np.max(np.abs(r)) or lam < 1e-3:
                    break
                lam /= 2
        z, r = z_new, r_new
    if np.max(np.abs(r)) < 1e-7:
        return z
    raise MatchError("transient gas-path match did not converge")


def _fd_jacobian(f, z, r, h=1e-7):
    J = np.empty((len(r), len(z)))
    for j in range(len(z)):
        dz = h * max(1.0, abs(z[j]))
        zp = z.copy()
        zp[j] += dz
        J[:, j] = (f(zp) - r) / dz
    return J


