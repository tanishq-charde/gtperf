"""Off-design performance by component matching, with variable geometry, part load and control limits.

An engine is built from a design-point cycle. The design point fixes each component's reference point
on its map: compressor corrected flow and pressure ratio, turbine flow capacities and isentropic enthalpy
drops. Off design, the solver finds the operating point where the compressor map, turbine flow
capacities and shaft power balances all agree. Compressor operating points are located by corrected
speed and R-line (see `maps`), so the solver stays well posed where a speed line is vertical at choke.

Variable geometry and air system (`Controls`):

- inlet guide vanes (VIGV) scheduled on gas generator corrected speed for a twin-shaft engine, which
  restores surge margin on hot days, or used at part load on a single-shaft engine to hold exhaust
  temperature up for heat recovery;
- a handling (anti-surge) bleed from compressor delivery to the exhaust, scheduled on corrected speed;
- inlet and exhaust pressure losses that scale with dynamic head (flow squared times temperature over
  pressure), from their values at the design flow, instead of staying fixed.

Control limits (`Limits`): base load is the highest output at which no limit is exceeded. The turbine
inlet temperature limit usually binds; a cold day can hit the gas generator speed limit (twin shaft),
and other limits are the exhaust or power turbine inlet temperature, the shaft (gearbox or coupling)
power, and the generator's capability at the ambient temperature. Part load is any lower output: the
twin-shaft engine lowers TIT and the gas generator slows; the single-shaft engine first closes its
VIGVs to hold exhaust temperature, then lowers TIT once the vanes reach their stop.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np
from scipy.optimize import brentq, root

from .components import Compressor, FlowState, Turbine, mix
from .cycle import ISO, CycleResult, SimpleCycle, TwinShaftCycle, compress_with_bleeds
from .maps import NEW_AND_CLEAN, TabulatedCompressorMap, ellipse_capacity, velocity_ratio_efficiency


@dataclass(frozen=True)
class Schedule:
    """Piecewise-linear schedule y(x) through ((x, y), ...) points, held flat beyond the end points."""

    points: tuple

    def __call__(self, x):
        xs, ys = zip(*self.points, strict=True)
        return float(np.interp(x, xs, ys))


@dataclass(frozen=True)
class Generator:
    """Generator capability: apparent power rating at rated power factor, derated with ambient temperature
    for an air-cooled machine. `derate` maps ambient temperature (°C) to a capability multiplier."""

    rating_mva: float
    power_factor: float = 0.85
    derate: Schedule = Schedule(((15.0, 1.0), (50.0, 1.0)))

    def capability(self, ambient_t):
        """Maximum active power, W, at ambient temperature ambient_t (K)."""
        return self.rating_mva * 1e6 * self.power_factor * self.derate(ambient_t - 273.15)


@dataclass(frozen=True)
class Limits:
    """Operating limits. `tit` defaults to the design TIT. `gg_speed` and `gg_corrected_speed` are the gas
    generator's relative mechanical and corrected speeds (twin shaft only): the mechanical limit protects
    the rotor, the corrected (aerodynamic) limit the compressor's front stages on a cold day. Temperatures
    in K, shaft power in W."""

    tit: float | None = None
    gg_speed: float = 1.03
    gg_corrected_speed: float | None = None
    exhaust_temp: float | None = None
    pt_inlet_temp: float | None = None
    shaft_power: float | None = None
    generator: Generator | None = None


@dataclass(frozen=True)
class Controls:
    """Variable geometry and air system.

    `vigv`: twin shaft, VIGV angle (degrees, negative closed) against gas generator corrected speed.
    `bleed`: handling bleed to exhaust, fraction of compressor inlet flow, against corrected speed.
    `vigv_min`: single shaft, the vane stop used at part load. `exhaust_temp_set`: single shaft part-load
    exhaust temperature set point (K); by default the base-load exhaust temperature at the same ambient.
    `scale_losses`: inlet and exhaust losses scale with dynamic head from their design-flow values.
    """

    vigv: Schedule | None = None
    bleed: Schedule | None = None
    vigv_min: float = -30.0
    exhaust_temp_set: float | None = None
    scale_losses: bool = True


def _isentropic_drop(state, p_out):
    gas = state.gas
    t_s = gas.t_from_s(state.s, p_out, t_guess=state.t * (p_out / state.p) ** 0.25)
    return state.h - gas.h(t_s)


def _capacity(state):
    """Non-dimensional flow capacity W*sqrt(R*T/gamma)/P, so gas composition changes are captured."""
    gas = state.gas
    return state.w * math.sqrt(gas.R * state.t / gas.gamma(state.t)) / state.p


def _sonic_temperature(state):
    """gamma*R*T, the square of the speed of sound: the right scaling for corrected speed."""
    return state.gas.gamma(state.t) * state.gas.R * state.t


def _turbine_eff(state_in, state_out):
    return (state_in.h - state_out.h) / _isentropic_drop(state_in, state_out.p)


def _head(state):
    """Dynamic head scaling W**2 * T / P (for a fixed area and gas)."""
    return state.w**2 * state.t / state.p


class _Engine:
    """Shared design-point referencing, gas-path evaluation and limit handling."""

    def __init__(self, cycle, air_flow, ambient=ISO, compressor_map=None, limits=None, controls=None):
        self.cycle = cycle
        self.design_ambient = ambient
        self.design = d = cycle.run(air_flow, ambient)
        s2, s3 = d.stations["2"], d.stations["3"]
        self.t2_design = s2.t
        self.a2_design = _sonic_temperature(s2)
        self.capacity2_design = _capacity(s2)
        self.pr_design = s3.p / s2.p
        self.compressor_eff_design = cycle.compressor.isentropic_efficiency(s2)
        self.compressor_map = compressor_map or TabulatedCompressorMap.load("hpc", self.pr_design)
        self.beta_design = self.compressor_map.beta_at_design()
        self.tit_design = cycle.combustor.t_out
        self.limits = limits or Limits()
        self.controls = controls or Controls()
        # loss scaling references: the design flow state at inlet and exhaust
        self.inlet_head_design = _head(replace(d.stations["0"], w=s2.w))
        self.exhaust_head_design = _head(d.exhaust)
        self.max_gg_speed = self.limits.gg_speed

    # --- gas path pieces --------------------------------------------------------------------------

    def _inlet(self, g, ambient, inlet_loss, health):
        """Ambient and compressor inlet states for relative corrected flow g, with the inlet loss scaled
        to the actual flow (a short fixed-point loop: the loss is a small fraction of the pressure)."""
        s0 = FlowState(1.0, ambient.t, ambient.p, ambient.air())
        s2 = self.cycle.inlet.run(replace(s0, p=s0.p - inlet_loss))
        for _ in range(3 if (inlet_loss and self.controls.scale_losses) else 1):
            w2 = g * health.compressor_flow_scale * self.capacity2_design / _capacity(replace(s2, w=1.0))
            if not (inlet_loss and self.controls.scale_losses):
                break
            dp = inlet_loss * _head(replace(s0, w=w2)) / self.inlet_head_design
            s2 = self.cycle.inlet.run(replace(s0, p=s0.p - dp))
        return replace(s0, w=w2), replace(s2, w=w2)

    def _exhaust_pressure(self, ambient, exhaust_loss, exhaust_state):
        if not (exhaust_loss and self.controls.scale_losses) or exhaust_state is None:
            return ambient.p + exhaust_loss
        return ambient.p + exhaust_loss * _head(exhaust_state) / self.exhaust_head_design

    def _gas_generator_front(self, n, beta, vigv, bleed, tit, ambient, inlet_loss, fuel, health):
        """Inlet, compressor with cooling and handling bleeds, combustor and NGV cooling air mixing."""
        c = self.cycle
        m = self.compressor_map
        g, pr, eff_rel = m.point(n, beta, vigv)
        s0, s2 = self._inlet(g, ambient, inlet_loss, health)
        eff = self.compressor_eff_design * eff_rel + health.compressor_eff_delta
        s3, s31, ngv, rotor, pt, comp_power = compress_with_bleeds(Compressor(pr, eff, polytropic=False), s2,
                                                                   c.cooling)
        handling = replace(s3, w=s2.w * bleed)
        s31 = replace(s31, w=s31.w - handling.w)
        combustor = replace(c.combustor, t_out=tit, fuel=fuel or c.combustor.fuel)
        s4, fuel_flow = combustor.run(s31)
        s41 = mix(s4, ngv)
        st = {"0": s0, "2": s2, "3": s3, "31": s31, "4": s4, "41": s41}
        return st, (rotor, pt, handling), comp_power, fuel_flow, combustor.fuel, g

    def _info(self, n, beta, g, vigv, bleed, mech_speed, tit, control):
        return {
            "corrected_speed": n,
            "corrected_flow": g,
            "beta": beta,
            "vigv": vigv,
            "bleed": bleed,
            "mechanical_speed": mech_speed,
            "surge_margin": self.compressor_map.surge_margin_at(n, beta, vigv),
            "tit": tit,
            "control": control,
        }

    # --- limits -----------------------------------------------------------------------------------

    def _limit_values(self, result, ambient, mech_speed):
        """Each active limit as value / limit - 1 (positive = exceeded)."""
        lim, out = self.limits, {}
        if lim.exhaust_temp:
            out["exhaust temperature"] = result.exhaust.t / lim.exhaust_temp - 1
        if lim.pt_inlet_temp and "45" in result.stations:
            out["power turbine inlet temperature"] = result.stations["45"].t / lim.pt_inlet_temp - 1
        if lim.shaft_power:
            out["shaft power"] = result.shaft_power / lim.shaft_power - 1
        if lim.generator:
            out["generator capability"] = result.electrical_power / lim.generator.capability(ambient.t) - 1
        return out

    def _solve_limited(self, evaluate, x_tit, tit_max, ambient, extra=None):
        """Base load: start at the TIT limit, then while another limit is exceeded, hold the worst one and
        let TIT fall. `evaluate(x, tit)` returns (residuals, result, mech_speed); `x_tit` solves the gas path
        at the TIT limit. Returns (x, tit, control, result, speed)."""
        x = x_tit
        _, result, speed = evaluate(x, tit_max)
        tit, control = tit_max, "temperature"
        held = []
        for _ in range(6):
            values = self._limit_values(result, ambient, speed)
            if self._speed_limited():
                values.update(self._speed_values(x, speed))
            values = {k: v for k, v in values.items() if k not in held}
            worst = max(values, key=values.get, default=None)
            if worst is None or values[worst] <= 1e-9:
                break
            held.append(worst)

            def residuals(z, name=worst):
                res, r, s = evaluate(z[:-1], z[-1] * tit_max)
                v = self._limit_values(r, ambient, s)
                if self._speed_limited():
                    v.update(self._speed_values(z[:-1], s))
                return [*res, v[name]]

            sol = root(residuals, [*x, tit / tit_max * 0.98], method="hybr", options={"xtol": 1e-11})
            _check(sol)
            x, tit, control = list(sol.x[:-1]), sol.x[-1] * tit_max, worst
            _, result, speed = evaluate(x, tit)
        return x, tit, control, result, speed

    def _speed_limited(self):
        return False

    def _part_load(self, evaluate, base, power, ambient):
        """Lower TIT until electrical output meets the demand. Returns (x, tit, result, speed)."""
        x, tit = list(base.info["solution"]), base.info["tit"]
        tit_ref = self.limits.tit or self.tit_design

        def residuals(z):
            res, r, _ = evaluate(z[:-1], z[-1] * tit_ref)
            return [*res, r.electrical_power / power - 1]

        frac = power / base.electrical_power
        sol = root(residuals, [*x, tit / tit_ref * (0.75 + 0.25 * frac)], method="hybr", options={"xtol": 1e-11})
        _check(sol)
        x, tit = list(sol.x[:-1]), sol.x[-1] * tit_ref
        _, result, speed = evaluate(x, tit)
        return x, tit, result, speed

    def _demand(self, base, power, load):
        if load is not None:
            return load * base.electrical_power
        return power


class TwinShaftEngine(_Engine):
    """Gas generator plus free power turbine at constant (synchronous) speed."""

    def __init__(self, cycle: TwinShaftCycle, air_flow, ambient=ISO, compressor_map=None, max_gg_speed=None,
                 limits=None, controls=None):
        limits = limits or Limits()
        if max_gg_speed is not None:
            limits = replace(limits, gg_speed=max_gg_speed)
        super().__init__(cycle, air_flow, ambient, compressor_map, limits, controls)
        st = self.design.stations
        self.hpt_capacity = _capacity(st["41"]) / ellipse_capacity(st["41"].p / st["44"].p)
        self.pt_capacity = _capacity(st["45"]) / ellipse_capacity(st["45"].p / st["5"].p)
        self.hpt_eff_design = _turbine_eff(st["41"], st["44"])
        self.pt_eff_design = _turbine_eff(st["45"], st["5"])
        self.hpt_dh_design = _isentropic_drop(st["41"], st["44"].p)
        self.pt_dh_design = _isentropic_drop(st["45"], st["5"].p)
        self.p45_design = st["45"].p
        self.comp_power_design = self.design.compressor_power

    def _speed_limited(self):
        return True

    def speed_limited(self, n_gg, t_inlet, band=0.003):
        """Whether measured gas generator speed (relative) at inlet temperature t_inlet (K) is within `band`
        of either speed limit. The corrected speed uses sqrt(T) scaling, close enough to flag the mode."""
        n_gg, t_inlet = np.asarray(n_gg, float), np.asarray(t_inlet, float)
        hit = n_gg >= self.max_gg_speed - band
        if self.limits.gg_corrected_speed:
            hit |= n_gg * np.sqrt(self.t2_design / t_inlet) >= self.limits.gg_corrected_speed - band
        return hit

    def _speed_values(self, x, speed):
        out = {"gas generator speed": speed / self.max_gg_speed - 1}
        if self.limits.gg_corrected_speed:
            out["gas generator corrected speed"] = x[0] / self.limits.gg_corrected_speed - 1
        return out

    def _geometry(self, n):
        c = self.controls
        return (c.vigv(n) if c.vigv else 0.0), (c.bleed(n) if c.bleed else 0.0)

    def _evaluate(self, n, beta, q45, tit, ambient, inlet_loss, exhaust_loss, fuel, health):
        c = self.cycle
        vigv, bleed = self._geometry(n)
        st, (rotor, pt, handling), comp_power, fuel_flow, fuel, g = self._gas_generator_front(
            n, beta, vigv, bleed, tit, ambient, inlet_loss, fuel, health)
        s41 = st["41"]
        gg_speed = n * math.sqrt(_sonic_temperature(st["2"]) / self.a2_design)
        p45 = q45 * self.p45_design

        dh = _isentropic_drop(s41, p45)
        eff = self.hpt_eff_design * velocity_ratio_efficiency(gg_speed / math.sqrt(dh / self.hpt_dh_design))
        s44, hpt_power = Turbine(eff + health.turbine_eff_delta + health.hpt_eff_delta).run(s41, p45)
        s45 = mix(mix(s44, rotor), pt)

        exhaust = None
        for _ in range(2 if (exhaust_loss and self.controls.scale_losses) else 1):
            p5 = self._exhaust_pressure(ambient, exhaust_loss, exhaust)
            dh = _isentropic_drop(s45, p5)
            eff = self.pt_eff_design * velocity_ratio_efficiency(1.0 / math.sqrt(dh / self.pt_dh_design))
            s49, pt_power = Turbine(eff + health.turbine_eff_delta + health.pt_eff_delta).run(s45, p5)
            exhaust = mix(s49, handling)

        residuals = [
            _capacity(s41) / (self.hpt_capacity * health.turbine_capacity_scale * ellipse_capacity(s41.p / p45)) - 1,
            (hpt_power * c.mechanical_efficiency - comp_power) / self.comp_power_design,
            _capacity(s45) / (self.pt_capacity * ellipse_capacity(p45 / p5)) - 1,
        ]
        shaft = pt_power * c.mechanical_efficiency
        stations = {**st, "44": s44, "45": s45}
        if handling.w:
            stations["49"] = s49
        stations["5"] = exhaust
        result = CycleResult(
            stations=stations,
            fuel_flow=fuel_flow,
            fuel_lhv=fuel.lhv,
            compressor_power=comp_power,
            turbine_power=hpt_power + pt_power,
            shaft_power=shaft,
            electrical_power=shaft * c.generator_efficiency,
        )
        return residuals, result, gg_speed

    def run(self, ambient=ISO, tit=None, inlet_loss=None, exhaust_loss=None, fuel=None, health=NEW_AND_CLEAN,
            guess=None, power=None, load=None):
        """Operating point: base load by default, or part load at an electrical `power` (W) or a `load`
        fraction of base load at these conditions. Returns a CycleResult whose `info` holds the matching
        variables, the variable geometry positions and the controlling limit."""
        tit_max = tit or self.limits.tit or self.tit_design
        inlet_loss = self.cycle.inlet_loss if inlet_loss is None else inlet_loss
        exhaust_loss = self.cycle.exhaust_loss if exhaust_loss is None else exhaust_loss
        args = (ambient, inlet_loss, exhaust_loss, fuel, health)

        def evaluate(x, t):
            return self._evaluate(*x, t, *args)

        x0 = list(guess[:3]) if guess else [1.0, self.beta_design, ambient.p / self.design_ambient.p]
        sol = root(lambda x: evaluate(x, tit_max)[0], x0, method="hybr", options={"xtol": 1e-10})
        _check(sol)
        x, tit_op, control, result, speed = self._solve_limited(evaluate, list(sol.x), tit_max, ambient)
        demand = self._demand(result, power, load)
        if demand is not None and demand < result.electrical_power * (1 - 1e-9):
            result.info = {"solution": tuple(x), "tit": tit_op}
            x, tit_op, result, speed = self._part_load(evaluate, result, demand, ambient)
            control = "load"
        result.info = self._info(x[0], x[1], self._g(x[0], x[1]), *self._geometry(x[0]), speed, tit_op, control)
        result.info["solution"] = tuple(x)
        return result

    def _g(self, n, beta):
        return self.compressor_map.point(n, beta, self._geometry(n)[0])[0]


class SingleShaftEngine(_Engine):
    """Single shaft at constant mechanical (synchronous) speed. At base load the VIGVs are fully open; at
    part load they close to hold the exhaust temperature set point, then TIT falls once they reach the stop."""

    def __init__(self, cycle: SimpleCycle, air_flow, ambient=ISO, compressor_map=None, limits=None,
                 controls=None):
        super().__init__(cycle, air_flow, ambient, compressor_map, limits, controls)
        st = self.design.stations
        self.turbine_capacity = _capacity(st["41"]) / ellipse_capacity(st["41"].p / st["49"].p)
        self.turbine_eff_design = _turbine_eff(st["41"], st["49"])
        self.turbine_dh_design = _isentropic_drop(st["41"], st["49"].p)

    def _corrected_speed(self, ambient):
        """Constant mechanical speed, so corrected speed only moves with inlet conditions."""
        inlet = FlowState(1.0, ambient.t, ambient.p, ambient.air())
        return math.sqrt(self.a2_design / _sonic_temperature(inlet))

    def _evaluate(self, beta, tit, vigv, ambient, inlet_loss, exhaust_loss, fuel, health):
        c = self.cycle
        n = self._corrected_speed(ambient)
        bleed = self.controls.bleed(n) if self.controls.bleed else 0.0
        st, (rotor, _, handling), comp_power, fuel_flow, fuel, g = self._gas_generator_front(
            n, beta, vigv, bleed, tit, ambient, inlet_loss, fuel, health)
        s41 = st["41"]
        exhaust = None
        for _ in range(2 if (exhaust_loss and self.controls.scale_losses) else 1):
            p5 = self._exhaust_pressure(ambient, exhaust_loss, exhaust)
            dh = _isentropic_drop(s41, p5)
            eff = self.turbine_eff_design * velocity_ratio_efficiency(1.0 / math.sqrt(dh / self.turbine_dh_design))
            s49, turb_power = Turbine(eff + health.turbine_eff_delta).run(s41, p5)
            exhaust = mix(mix(s49, rotor), handling)
        residual = _capacity(s41) / (self.turbine_capacity * health.turbine_capacity_scale
                                     * ellipse_capacity(s41.p / p5)) - 1
        shaft = turb_power * c.mechanical_efficiency - comp_power
        result = CycleResult(
            stations={**st, "49": s49, "5": exhaust},
            fuel_flow=fuel_flow,
            fuel_lhv=fuel.lhv,
            compressor_power=comp_power,
            turbine_power=turb_power,
            shaft_power=shaft,
            electrical_power=shaft * c.generator_efficiency,
        )
        return [residual], result, n, g, bleed

    def _match(self, tit, vigv, args, guess=None):
        """R-line that matches the turbine flow capacity, by bracketing (robust for one unknown)."""
        f = lambda b: self._evaluate(b, tit, vigv, *args)[0][0]  # noqa: E731
        if guess is not None:
            try:
                sol = root(lambda x: [f(x[0])], [guess], method="hybr", options={"xtol": 1e-12})
                if sol.success:
                    return float(sol.x[0])
            except (ValueError, RuntimeError):
                pass
        return brentq(f, 0.2, 4.5, xtol=1e-12)

    def run(self, ambient=ISO, tit=None, inlet_loss=None, exhaust_loss=None, fuel=None, health=NEW_AND_CLEAN,
            guess=None, power=None, load=None):
        tit_max = tit or self.limits.tit or self.tit_design
        inlet_loss = self.cycle.inlet_loss if inlet_loss is None else inlet_loss
        exhaust_loss = self.cycle.exhaust_loss if exhaust_loss is None else exhaust_loss
        args = (ambient, inlet_loss, exhaust_loss, fuel, health)

        def evaluate(x, t, vigv=0.0):
            res, r, n, _, _ = self._evaluate(x[0], t, vigv, *args)
            return res, r, 1.0

        beta = self._match(tit_max, 0.0, args, guess[0] if guess else self.beta_design)
        x, tit_op, control, result, _ = self._solve_limited(evaluate, [beta], tit_max, ambient)
        vigv = 0.0

        demand = self._demand(result, power, load)
        if demand is not None and demand < result.electrical_power * (1 - 1e-9):
            t5_set = self.controls.exhaust_temp_set or result.exhaust.t
            if self.limits.exhaust_temp:
                t5_set = min(t5_set, self.limits.exhaust_temp)

            def igv_control(z):
                res, r, _, _, _ = self._evaluate(z[0], z[1] * tit_max, z[2] * 10, *args)
                return [*res, r.electrical_power / demand - 1, r.exhaust.t / t5_set - 1]

            frac = demand / result.electrical_power
            sol = root(igv_control, [x[0], tit_op / tit_max, -1.0 * (1 - frac)], method="hybr",
                       options={"xtol": 1e-11})
            vigv = sol.x[2] * 10 if sol.success else self.controls.vigv_min - 1
            if sol.success and vigv <= 0.0 and vigv >= self.controls.vigv_min and sol.x[1] * tit_max <= tit_max:
                x, tit_op = [sol.x[0]], sol.x[1] * tit_max
                control = "load (VIGV holding exhaust temperature)"
            else:
                vigv = self.controls.vigv_min

                def tit_control(z):
                    res, r, _, _, _ = self._evaluate(z[0], z[1] * tit_max, vigv, *args)
                    return [*res, r.electrical_power / demand - 1]

                sol = root(tit_control, [x[0], 0.8], method="hybr", options={"xtol": 1e-11})
                _check(sol)
                x, tit_op = [sol.x[0]], sol.x[1] * tit_max
                control = "load (VIGV at stop)"
        _, result, n, g, bleed = self._evaluate(x[0], tit_op, vigv, *args)
        result.info = self._info(n, x[0], g, vigv, bleed, 1.0, tit_op, control)
        result.info["solution"] = tuple(x)
        return result


def _check(sol):
    if not sol.success:
        raise RuntimeError(f"off-design match failed: {sol.message}")


__all__ = ["Controls", "Generator", "Limits", "Schedule", "SingleShaftEngine", "TwinShaftEngine"]
