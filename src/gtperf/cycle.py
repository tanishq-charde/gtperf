"""Single-shaft and twin-shaft simple-cycle gas turbines at a design point."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from scipy.optimize import brentq

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


@dataclass(frozen=True)
class Cooling:
    """Turbine cooling and sealing air, each stream as a fraction of compressor inlet flow.

    - `ngv`: first-stage nozzle guide vane cooling, from compressor delivery. It mixes ahead of the first
      rotor, so it is heated by the hot gas and then does work in every turbine downstream. TIT (station 4)
      is the combustor exit; the rotor inlet temperature after this air mixes in is station 41.
    - `rotor`: HP turbine rotor (or, single shaft, turbine) cooling from compressor delivery. It is
      treated as rejoining the gas path after that turbine, so it does no work there: the conservative
      textbook simplification.
    - `pt`: twin shaft only, power turbine cooling and sealing air, bled from an interstage port of the
      compressor and mixed at power turbine inlet. Bleeding it part-way through the compressor saves the
      work of compressing it to delivery pressure.

    `port` places the interstage bleed as a fraction of the compressor's pressure ratio in log terms
    (0.6 means the port pressure is p2 * PR**0.6).
    """

    ngv: float = 0.0
    rotor: float = 0.0
    pt: float = 0.0
    port: float = 0.6

    @property
    def total(self):
        return self.ngv + self.rotor + self.pt


def _cooling(cooling, cooling_fraction):
    if cooling is not None and cooling_fraction:
        raise ValueError("give either cooling or the legacy cooling_fraction, not both")
    return cooling if cooling is not None else Cooling(ngv=cooling_fraction)


def compress_with_bleeds(compressor, s2, cooling):
    """Compressor delivery and the cooling streams. Returns (delivery to the combustor, NGV air, rotor air,
    PT air, compressor power). Interstage air leaves at the port pressure and temperature, having been
    compressed only that far; the compression path is the same for all air up to the port."""
    s3, power = compressor.run(s2)
    w = s2.w
    port = None
    if cooling.pt:
        p_port = s2.p * (s3.p / s2.p) ** cooling.port
        t_port = _path_temperature(compressor, s2, s3, p_port)
        port = FlowState(w * cooling.pt, t_port, p_port, s2.gas)
        power -= port.w * (s3.h - port.h)
    ngv = replace(s3, w=w * cooling.ngv)
    rotor = replace(s3, w=w * cooling.rotor)
    pt = port if port is not None else replace(s3, w=0.0)
    delivery = replace(s3, w=w * (1 - cooling.total))
    return s3, delivery, ngv, rotor, pt, power


def _path_temperature(compressor, s2, s3, p):
    """Temperature on the compression path at pressure p. For a polytropic compressor this is exact; for an
    isentropic efficiency the same efficiency is applied to the part compression."""
    part = replace(compressor, pressure_ratio=p / s2.p)
    return part.run(s2)[0].t


@dataclass
class CycleResult:
    stations: dict
    fuel_flow: float
    fuel_lhv: float
    compressor_power: float
    turbine_power: float
    shaft_power: float
    electrical_power: float
    info: dict = field(default_factory=dict)

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

    @property
    def exhaust_energy(self):
        """Exhaust heat above ambient temperature, W (what a heat recovery boiler could use)."""
        ex = self.exhaust
        return ex.w * (ex.gas.h(ex.t) - ex.gas.h(self.stations["0"].t))

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

    Cooling air (see `Cooling`) bypasses the combustor: NGV air mixes before the rotor, so the
    turbine sees the lower rotor inlet temperature (station 41) rather than the combustor exit
    temperature (TIT, station 4); rotor air rejoins after the turbine. The legacy `cooling_fraction`
    is all NGV air. Station 31 is the combustor inlet after the cooling air is taken off.
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
    cooling: Cooling | None = None

    def __post_init__(self):
        self.cooling = _cooling(self.cooling, self.cooling_fraction)
        self.cooling_fraction = 0.0
        if self.cooling.pt:
            raise ValueError("a single-shaft engine has no power turbine cooling")

    def run(self, air_flow, ambient=ISO):
        s0 = FlowState(air_flow, ambient.t, ambient.p, ambient.air())
        s2 = self.inlet.run(replace(s0, p=s0.p - self.inlet_loss))
        s3, s31, ngv, rotor, _, comp_power = compress_with_bleeds(self.compressor, s2, self.cooling)
        s4, fuel_flow = self.combustor.run(s31)
        s41 = mix(s4, ngv)
        s49, turb_power = self.turbine.run(s41, ambient.p + self.exhaust_loss)
        s5 = mix(s49, rotor)

        shaft = turb_power * self.mechanical_efficiency - comp_power
        return CycleResult(
            stations={"0": s0, "2": s2, "3": s3, "31": s31, "4": s4, "41": s41, "49": s49, "5": s5},
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


@dataclass
class TwinShaftCycle:
    """Two-shaft engine: a gas generator (compressor driven by the HP turbine) and a free power turbine.

    Stations as SimpleCycle, plus 44 at HP turbine exit and 45 at power turbine inlet, after rotor and
    power turbine cooling air have mixed in. The HP turbine expands just far enough to drive the
    compressor; the power turbine takes the rest down to exhaust pressure.
    """

    compressor: Compressor
    combustor: Combustor
    hp_turbine: Turbine = field(default_factory=Turbine)
    power_turbine: Turbine = field(default_factory=Turbine)
    inlet: Duct = field(default_factory=Duct)
    inlet_loss: float = 0.0
    exhaust_loss: float = 0.0
    cooling_fraction: float = 0.0
    mechanical_efficiency: float = 0.99  # applied to each shaft
    generator_efficiency: float = 0.985
    cooling: Cooling | None = None

    def __post_init__(self):
        self.cooling = _cooling(self.cooling, self.cooling_fraction)
        self.cooling_fraction = 0.0

    def run(self, air_flow, ambient=ISO):
        s0 = FlowState(air_flow, ambient.t, ambient.p, ambient.air())
        s2 = self.inlet.run(replace(s0, p=s0.p - self.inlet_loss))
        s3, s31, ngv, rotor, pt, comp_power = compress_with_bleeds(self.compressor, s2, self.cooling)
        s4, fuel_flow = self.combustor.run(s31)
        s41 = mix(s4, ngv)

        p5 = ambient.p + self.exhaust_loss
        needed = comp_power / self.mechanical_efficiency

        def residual(p45):
            return self.hp_turbine.run(s41, p45)[1] - needed

        p45 = brentq(residual, p5 * 1.0001, s41.p * 0.9999, xtol=1e-6)
        s44, hpt_power = self.hp_turbine.run(s41, p45)
        s45 = mix(mix(s44, rotor), pt)
        s5, pt_power = self.power_turbine.run(s45, p5)

        shaft = pt_power * self.mechanical_efficiency
        return CycleResult(
            stations={"0": s0, "2": s2, "3": s3, "31": s31, "4": s4, "41": s41, "44": s44, "45": s45, "5": s5},
            fuel_flow=fuel_flow,
            fuel_lhv=self.combustor.fuel.lhv,
            compressor_power=comp_power,
            turbine_power=hpt_power + pt_power,
            shaft_power=shaft,
            electrical_power=shaft * self.generator_efficiency,
        )

    def size_for_power(self, electrical_power, ambient=ISO, guess_air_flow=50.0):
        per_kg = self.run(guess_air_flow, ambient).electrical_power / guess_air_flow
        return electrical_power / per_kg
