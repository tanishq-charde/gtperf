"""Off-design performance by component matching.

An engine is built from a design-point cycle. The design point fixes each
component's reference point on its map: compressor corrected flow and pressure
ratio, turbine flow capacities and isentropic enthalpy drops. Off design, the
solver finds the operating point where the compressor map, turbine flow
capacities and shaft power balances all agree.

Control: base load at the design turbine inlet temperature (TIT). For a
twin-shaft engine the gas generator speed is also limited; on a cold day the
speed limit is reached first and TIT is lowered to hold it ("speed topping").
"""

from __future__ import annotations

import math
from dataclasses import replace

from scipy.optimize import brentq, root

from .components import Compressor, FlowState, Turbine, mix
from .cycle import ISO, CycleResult, SimpleCycle, TwinShaftCycle
from .maps import NEW_AND_CLEAN, CompressorMap, ellipse_capacity, velocity_ratio_efficiency


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


class _Engine:
    """Shared design-point referencing and gas-path evaluation."""

    def __init__(self, cycle, air_flow, ambient=ISO, compressor_map=None):
        self.cycle = cycle
        self.design_ambient = ambient
        self.design = d = cycle.run(air_flow, ambient)
        s2, s3 = d.stations["2"], d.stations["3"]
        self.t2_design = s2.t
        self.a2_design = _sonic_temperature(s2)
        self.capacity2_design = _capacity(s2)
        self.pr_design = s3.p / s2.p
        self.compressor_eff_design = cycle.compressor.isentropic_efficiency(s2)
        self.compressor_map = compressor_map or CompressorMap(self.pr_design)
        self.tit_design = cycle.combustor.t_out

    def _gas_generator_front(self, n, g, tit, ambient, inlet_loss, fuel, health):
        """Inlet, compressor, combustor and cooling air mixing. Returns states and compressor power."""
        c = self.cycle
        s0 = FlowState(1.0, ambient.t, ambient.p, ambient.air())
        s2 = c.inlet.run(replace(s0, p=s0.p - inlet_loss))
        w2 = g * health.compressor_flow_scale * self.capacity2_design / _capacity(s2)
        s0, s2 = replace(s0, w=w2), replace(s2, w=w2)
        m = self.compressor_map
        eff = self.compressor_eff_design * m.efficiency(n, g) + health.compressor_eff_delta
        s3, comp_power = Compressor(m.pressure_ratio(n, g), eff, polytropic=False).run(s2)
        cooling = replace(s3, w=s3.w * c.cooling_fraction)
        combustor = replace(c.combustor, t_out=tit, fuel=fuel or c.combustor.fuel)
        s4, fuel_flow = combustor.run(replace(s3, w=s3.w - cooling.w))
        s41 = mix(s4, cooling)
        return {"0": s0, "2": s2, "3": s3, "4": s4, "41": s41}, comp_power, fuel_flow, combustor.fuel

    def _info(self, n, g, mech_speed, tit, control):
        return {
            "corrected_speed": n,
            "corrected_flow": g,
            "mechanical_speed": mech_speed,
            "surge_margin": self.compressor_map.surge_margin(n, g),
            "tit": tit,
            "control": control,
        }


class TwinShaftEngine(_Engine):
    """Gas generator plus free power turbine at constant (synchronous) speed."""

    def __init__(self, cycle: TwinShaftCycle, air_flow, ambient=ISO, compressor_map=None, max_gg_speed=1.03):
        super().__init__(cycle, air_flow, ambient, compressor_map)
        st = self.design.stations
        self.max_gg_speed = max_gg_speed
        self.hpt_capacity = _capacity(st["41"]) / ellipse_capacity(st["41"].p / st["45"].p)
        self.pt_capacity = _capacity(st["45"]) / ellipse_capacity(st["45"].p / st["5"].p)
        self.hpt_eff_design = _turbine_eff(st["41"], st["45"])
        self.pt_eff_design = _turbine_eff(st["45"], st["5"])
        self.hpt_dh_design = _isentropic_drop(st["41"], st["45"].p)
        self.pt_dh_design = _isentropic_drop(st["45"], st["5"].p)
        self.p45_design = st["45"].p
        self.comp_power_design = self.design.compressor_power

    def _evaluate(self, n, g, q45, tit, ambient, inlet_loss, exhaust_loss, fuel, health):
        c = self.cycle
        st, comp_power, fuel_flow, fuel = self._gas_generator_front(n, g, tit, ambient, inlet_loss, fuel, health)
        s41 = st["41"]
        gg_speed = n * math.sqrt(_sonic_temperature(st["2"]) / self.a2_design)
        p45 = q45 * self.p45_design
        p5 = ambient.p + exhaust_loss

        dh = _isentropic_drop(s41, p45)
        eff = self.hpt_eff_design * velocity_ratio_efficiency(gg_speed / math.sqrt(dh / self.hpt_dh_design))
        s45, hpt_power = Turbine(eff + health.turbine_eff_delta).run(s41, p45)

        dh = _isentropic_drop(s45, p5)
        eff = self.pt_eff_design * velocity_ratio_efficiency(1.0 / math.sqrt(dh / self.pt_dh_design))
        s5, pt_power = Turbine(eff + health.turbine_eff_delta).run(s45, p5)

        residuals = [
            _capacity(s41) / (self.hpt_capacity * health.turbine_capacity_scale * ellipse_capacity(s41.p / p45)) - 1,
            (hpt_power * c.mechanical_efficiency - comp_power) / self.comp_power_design,
            _capacity(s45) / (self.pt_capacity * ellipse_capacity(p45 / p5)) - 1,
        ]
        shaft = pt_power * c.mechanical_efficiency
        result = CycleResult(
            stations={**st, "45": s45, "5": s5},
            fuel_flow=fuel_flow,
            fuel_lhv=fuel.lhv,
            compressor_power=comp_power,
            turbine_power=hpt_power + pt_power,
            shaft_power=shaft,
            electrical_power=shaft * c.generator_efficiency,
        )
        return residuals, result, gg_speed

    def run(self, ambient=ISO, tit=None, inlet_loss=None, exhaust_loss=None, fuel=None, health=NEW_AND_CLEAN,
            guess=None):
        """Base-load operating point. Returns a CycleResult whose `info` holds the matching variables."""
        tit_max = tit or self.tit_design
        inlet_loss = self.cycle.inlet_loss if inlet_loss is None else inlet_loss
        exhaust_loss = self.cycle.exhaust_loss if exhaust_loss is None else exhaust_loss
        args = (ambient, inlet_loss, exhaust_loss, fuel, health)
        x0 = list(guess) if guess else [1.0, 1.0, ambient.p / self.design_ambient.p]

        def temperature_control(x):
            return self._evaluate(*x, tit_max, *args)[0]

        sol = root(temperature_control, x0[:3], method="hybr", options={"xtol": 1e-10})
        _check(sol)
        _, result, gg_speed = self._evaluate(*sol.x, tit_max, *args)
        control, x, tit_op = "temperature", sol.x, tit_max

        if gg_speed > self.max_gg_speed:

            def speed_control(x):
                res, _, speed = self._evaluate(*x[:3], x[3] * tit_max, *args)
                return res + [speed / self.max_gg_speed - 1]

            sol = root(speed_control, [*sol.x, 0.97], method="hybr", options={"xtol": 1e-10})
            _check(sol)
            x, tit_op = sol.x[:3], sol.x[3] * tit_max
            _, result, gg_speed = self._evaluate(*x, tit_op, *args)
            control = "gas generator speed"

        result.info = self._info(x[0], x[1], gg_speed, tit_op, control)
        result.info["solution"] = tuple(x)
        return result


class SingleShaftEngine(_Engine):
    """Single shaft at constant mechanical (synchronous) speed, fixed inlet guide vanes."""

    def __init__(self, cycle: SimpleCycle, air_flow, ambient=ISO, compressor_map=None):
        super().__init__(cycle, air_flow, ambient, compressor_map)
        st = self.design.stations
        self.turbine_capacity = _capacity(st["41"]) / ellipse_capacity(st["41"].p / st["5"].p)
        self.turbine_eff_design = _turbine_eff(st["41"], st["5"])
        self.turbine_dh_design = _isentropic_drop(st["41"], st["5"].p)

    def _corrected_speed(self, ambient):
        """Constant mechanical speed, so corrected speed only moves with inlet conditions."""
        inlet = FlowState(1.0, ambient.t, ambient.p, ambient.air())
        return math.sqrt(self.a2_design / _sonic_temperature(inlet))

    def _evaluate(self, g, tit, ambient, inlet_loss, exhaust_loss, fuel, health):
        c = self.cycle
        n = self._corrected_speed(ambient)
        st, comp_power, fuel_flow, fuel = self._gas_generator_front(n, g, tit, ambient, inlet_loss, fuel, health)
        s41 = st["41"]
        p5 = ambient.p + exhaust_loss
        dh = _isentropic_drop(s41, p5)
        eff = self.turbine_eff_design * velocity_ratio_efficiency(1.0 / math.sqrt(dh / self.turbine_dh_design))
        s5, turb_power = Turbine(eff + health.turbine_eff_delta).run(s41, p5)
        residual = _capacity(s41) / (self.turbine_capacity * health.turbine_capacity_scale
                                     * ellipse_capacity(s41.p / p5)) - 1
        shaft = turb_power * c.mechanical_efficiency - comp_power
        result = CycleResult(
            stations={**st, "5": s5},
            fuel_flow=fuel_flow,
            fuel_lhv=fuel.lhv,
            compressor_power=comp_power,
            turbine_power=turb_power,
            shaft_power=shaft,
            electrical_power=shaft * c.generator_efficiency,
        )
        return residual, result, n

    def run(self, ambient=ISO, tit=None, inlet_loss=None, exhaust_loss=None, fuel=None, health=NEW_AND_CLEAN,
            guess=None):
        tit = tit or self.tit_design
        inlet_loss = self.cycle.inlet_loss if inlet_loss is None else inlet_loss
        exhaust_loss = self.cycle.exhaust_loss if exhaust_loss is None else exhaust_loss
        args = (tit, ambient, inlet_loss, exhaust_loss, fuel, health)
        m = self.compressor_map
        nf = self._corrected_speed(ambient) ** m.flow_exponent
        g = brentq(lambda g: self._evaluate(g, *args)[0], 0.8 * m.surge_flow * nf, 0.999 * m.choke_flow * nf,
                   xtol=1e-12)
        _, result, n = self._evaluate(g, *args)
        result.info = self._info(n, g, 1.0, tit, "temperature")
        result.info["solution"] = (g,)
        return result


def _check(sol):
    if not sol.success:
        raise RuntimeError(f"off-design match failed: {sol.message}")
