"""Single-shaft simple-cycle gas turbine at a design point."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .components import Combustor, Compressor, Duct, FlowState, Turbine, mix
from .gas import humid_air


@dataclass(frozen=True)
class Ambient:
    t: float = 288.15  # K
    p: float = 101325.0  # Pa
    rh: float = 0.60
    gas: object = None  # override the humid-air model, e.g. with a PerfectGas

    def air(self):
        return self.gas if self.gas is not None else humid_air(self.t, self.p, self.rh)


ISO = Ambient()


@dataclass
class CycleResult:
    stations: dict
    fuel_flow: float
    fuel_lhv: float
    compressor_power: float
    turbine_power: float
    shaft_power: float
    electrical_power: float

    @property
    def air_flow(self):
        return self.stations["2"].w

    @property
    def heat_input(self):
        return self.fuel_flow * self.fuel_lhv

    @property
    def thermal_efficiency(self):
        """Electrical output over fuel heat input (LHV basis)."""
        return self.electrical_power / self.heat_input

    @property
    def heat_rate(self):
        """kJ of fuel (LHV) per kWh of electrical output."""
        return 3600.0 / self.thermal_efficiency

    @property
    def specific_work(self):
        """Shaft power per unit compressor inlet flow, J/kg."""
        return self.shaft_power / self.air_flow

    @property
    def exhaust(self):
        return self.stations["5"]

    def summary(self):
        return {
            "electrical_power_MW": self.electrical_power / 1e6,
            "shaft_power_MW": self.shaft_power / 1e6,
            "thermal_efficiency_pct": 100 * self.thermal_efficiency,
            "heat_rate_kJ_per_kWh": self.heat_rate,
            "specific_work_kJ_per_kg": self.specific_work / 1e3,
            "air_flow_kg_s": self.air_flow,
            "fuel_flow_kg_s": self.fuel_flow,
            "exhaust_flow_kg_s": self.exhaust.w,
            "exhaust_temp_C": self.exhaust.t - 273.15,
        }

    def station_table(self):
        lines = [f"{'stn':>4} {'W kg/s':>9} {'T C':>9} {'P bar':>9}"]
        for name, st in self.stations.items():
            lines.append(f"{name:>4} {st.w:9.3f} {st.t - 273.15:9.2f} {st.p / 1e5:9.4f}")
        return "\n".join(lines)

    def __str__(self):
        rows = [f"{k:<26}{v:12.3f}" for k, v in self.summary().items()]
        return "\n".join(rows) + "\n\n" + self.station_table()


@dataclass
class SimpleCycle:
    """Single-shaft simple cycle with optional turbine cooling air.

    Stations (industrial convention): 0 ambient, 2 compressor inlet, 3 compressor
    delivery, 4 combustor exit, 41 rotor inlet after cooling air mixes in, 5 exhaust.

    The cooling air is a simplified model: `cooling_fraction` of compressor delivery
    bypasses the combustor and mixes before the turbine, so the turbine sees the
    lower rotor inlet temperature (station 41) rather than the combustor exit
    temperature (TIT, station 4).
    """

    compressor: Compressor
    combustor: Combustor
    turbine: Turbine = field(default_factory=Turbine)
    inlet: Duct = field(default_factory=Duct)
    inlet_loss: float = 0.0  # Pa, inlet filter and ducting
    exhaust_loss: float = 0.0  # Pa, back-pressure above ambient
    cooling_fraction: float = 0.0
    mechanical_efficiency: float = 0.99
    generator_efficiency: float = 0.985

    def run(self, air_flow, ambient=ISO):
        s0 = FlowState(air_flow, ambient.t, ambient.p, ambient.air())
        s2 = self.inlet.run(replace(s0, p=s0.p - self.inlet_loss))
        s3, comp_power = self.compressor.run(s2)

        cooling = replace(s3, w=s3.w * self.cooling_fraction)
        s4, fuel_flow = self.combustor.run(replace(s3, w=s3.w - cooling.w))
        s41 = mix(s4, cooling)

        s5, turb_power = self.turbine.run(s41, ambient.p + self.exhaust_loss)

        shaft = turb_power * self.mechanical_efficiency - comp_power
        return CycleResult(
            stations={"0": s0, "2": s2, "3": s3, "4": s4, "41": s41, "5": s5},
            fuel_flow=fuel_flow,
            fuel_lhv=self.combustor.fuel.lhv,
            compressor_power=comp_power,
            turbine_power=turb_power,
            shaft_power=shaft,
            electrical_power=shaft * self.generator_efficiency,
        )

    def size_for_power(self, electrical_power, ambient=ISO, guess_air_flow=50.0):
        """Air flow that gives the target electrical output (W). Output scales linearly with flow."""
        per_kg = self.run(guess_air_flow, ambient).electrical_power / guess_air_flow
        return electrical_power / per_kg
