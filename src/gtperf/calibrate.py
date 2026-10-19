"""Calibrate a design-point model to published datasheet values."""

from __future__ import annotations

from dataclasses import dataclass

from scipy.optimize import root

from .components import Combustor, Compressor, Turbine
from .cycle import ISO, TwinShaftCycle
from .fuel import natural_gas


@dataclass
class Calibration:
    cycle: TwinShaftCycle
    air_flow: float
    tit: float
    turbine_efficiency: float
    comparison: list  # (quantity, unit, datasheet, model, error %)

    def table(self):
        lines = ["| Quantity | Datasheet | Model | Error |", "|---|---:|---:|---:|"]
        for name, unit, ref, model in self.comparison:
            lines.append(f"| {name} ({unit}) | {ref:,.1f} | {model:,.1f} | {100 * (model - ref) / ref:+.2f} % |")
        return "\n".join(lines)


def calibrate_twin_shaft(
    electrical_power,
    heat_rate,
    exhaust_flow,
    pressure_ratio,
    exhaust_temp=None,
    compressor_polytropic_eff=0.89,
    cooling_fraction=0.15,
    combustor_dp=0.04,
    mechanical_efficiency=0.99,
    generator_efficiency=0.98,
    fuel=None,
    ambient=ISO,
):
    """Find air flow, TIT and turbine efficiency that reproduce power (W), heat rate (kJ/kWh) and exhaust flow (kg/s).

    The compressor efficiency, cooling flow and losses are fixed assumptions: three
    datasheet numbers can only pin down three unknowns. Exhaust temperature, if
    given, is then a check on the result rather than an input.
    """
    fuel = fuel or natural_gas()

    def build(tit, eta_t):
        return TwinShaftCycle(
            Compressor(pressure_ratio, compressor_polytropic_eff),
            Combustor(fuel, tit, dp_frac=combustor_dp),
            Turbine(eta_t),
            Turbine(eta_t),
            cooling_fraction=cooling_fraction,
            mechanical_efficiency=mechanical_efficiency,
            generator_efficiency=generator_efficiency,
        )

    def residuals(x):
        w, tit, eta_t = x[0] * exhaust_flow, x[1] * 1500.0, x[2]
        r = build(tit, eta_t).run(w, ambient)
        return [
            r.electrical_power / electrical_power - 1,
            r.heat_rate / heat_rate - 1,
            r.exhaust.w / exhaust_flow - 1,
        ]

    sol = root(residuals, [0.98, 1.0, 0.88], method="hybr", options={"xtol": 1e-12})
    if not sol.success:
        raise RuntimeError(f"calibration failed: {sol.message}")
    w, tit, eta_t = sol.x[0] * exhaust_flow, sol.x[1] * 1500.0, sol.x[2]
    cycle = build(tit, eta_t)
    r = cycle.run(w, ambient)
    comparison = [
        ("Electrical power", "MW", electrical_power / 1e6, r.electrical_power / 1e6),
        ("Heat rate", "kJ/kWh", heat_rate, r.heat_rate),
        ("Exhaust flow", "kg/s", exhaust_flow, r.exhaust.w),
    ]
    if exhaust_temp is not None:
        comparison.append(("Exhaust temperature (check)", "°C", exhaust_temp, r.exhaust.t - 273.15))
    return Calibration(cycle, w, tit, eta_t, comparison)
