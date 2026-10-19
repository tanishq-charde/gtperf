"""Synthetic production pass-off test data.

A "true" engine (the model with hidden build-to-build health deltas) is run at the
test-day conditions, then observed through an instrument set. Each sensor gets a
fixed calibration bias (unknown to the analyst), random noise, and optionally an
injected fault, so the analysis chain can be checked against a known truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .cycle import Ambient
from .maps import NEW_AND_CLEAN

N_EXHAUST_TC = 12
N_AMBIENT_T = 3


@dataclass(frozen=True)
class Instrument:
    """Standard uncertainties (k=1). `bias` is the systematic (calibration) part, `noise` the random part.

    Relative instruments express both as fractions of reading.
    """

    unit: str
    bias: float
    noise: float
    relative: bool = False
    description: str = ""


def default_instruments():
    """A typical small-GT test-bed instrument set. Values are representative assumptions, not a standard."""
    inst = {
        "P_amb": Instrument("mbar", 0.3, 0.05, description="barometer"),
        "RH": Instrument("%", 1.5, 0.3, description="humidity probe"),
        "dP_inlet": Instrument("mbar", 0.1, 0.05, description="inlet depression"),
        "dP_exhaust": Instrument("mbar", 0.2, 0.1, description="exhaust back-pressure"),
        "W_fuel": Instrument("kg/s", 0.0020, 0.003, relative=True, description="Coriolis fuel flow meter"),
        "LHV": Instrument("MJ/kg", 0.0015, 0.0002, relative=True, description="gas chromatograph LHV"),
        "P_elec": Instrument("kW", 0.0010, 0.0015, relative=True, description="revenue-class power metering"),
        "N_gg": Instrument("%", 0.05, 0.02, description="gas generator speed (diagnostic)"),
        "P3": Instrument("bar", 0.02, 0.005, description="compressor delivery pressure (diagnostic)"),
        "T3": Instrument("degC", 1.0, 0.3, description="compressor delivery temperature (diagnostic)"),
    }
    for i in range(1, N_AMBIENT_T + 1):
        inst[f"T_amb_{i}"] = Instrument("degC", 0.15, 0.05, description="inlet air RTD")
    for i in range(1, N_EXHAUST_TC + 1):
        inst[f"T_exh_{i:02d}"] = Instrument("degC", 1.0, 0.5, description="exhaust thermocouple, type K")
    return inst


@dataclass(frozen=True)
class Fault:
    """An injected sensor fault: offset, drift (linear ramp to magnitude), stuck, or spike."""

    tag: str
    kind: str
    magnitude: float = 0.0
    points: tuple = ()  # test points affected; empty means all


@dataclass
class TestPoint:
    __test__ = False  # not a pytest test class

    t_amb_C: float
    p_amb_mbar: float
    rh: float  # 0..1
    drift_K: float = 0.0  # ambient temperature change across the point


@dataclass
class SyntheticTest:
    data: pd.DataFrame
    metadata: dict
    truth: dict = field(default_factory=dict)
    biases: dict = field(default_factory=dict)


def _ar1(rng, n, std, rho=0.95):
    e = rng.normal(0, std * np.sqrt(1 - rho**2), n)
    x = np.empty(n)
    x[0] = rng.normal(0, std)
    for i in range(1, n):
        x[i] = rho * x[i - 1] + e[i]
    return x


def _exhaust_pattern():
    """Fixed circumferential exhaust temperature profile (swirled combustor pattern), K, zero mean."""
    theta = 2 * np.pi * np.arange(N_EXHAUST_TC) / N_EXHAUST_TC
    p = 8.0 * np.sin(2 * theta + 0.4) + 3.0 * np.cos(3 * theta)
    return p - p.mean()


def generate_test(
    engine,
    points,
    health=NEW_AND_CLEAN,
    inlet_loss_mbar=5.0,
    exhaust_loss_mbar=12.0,
    duration_s=600,
    faults=(),
    instruments=None,
    seed=0,
    serial="ESN-0001",
):
    """Generate a 1 Hz pass-off test log for `engine` with hidden `health`.

    Returns a SyntheticTest whose `truth` holds what the analysis should recover:
    the engine's true performance at reference (ISO, no losses) conditions.
    """
    rng = np.random.default_rng(seed)
    inst = instruments or default_instruments()
    biases = {tag: rng.normal(0, i.bias) for tag, i in inst.items()}
    pattern = _exhaust_pattern()
    n = int(duration_s)
    frames = []

    for k, pt in enumerate(points):
        frac = np.linspace(0, 1, n)
        t_amb = pt.t_amb_C + pt.drift_K * (frac - 0.5)
        kw = dict(inlet_loss=inlet_loss_mbar * 100, exhaust_loss=exhaust_loss_mbar * 100, health=health)
        ends = [engine.run(Ambient(t + 273.15, pt.p_amb_mbar * 100, pt.rh), **kw)
                for t in (t_amb[0], t_amb[-1])]

        def lerp(get):
            a, b = get(ends[0]), get(ends[1])
            return a + (b - a) * frac

        load = _ar1(rng, n, 0.0015)  # grid and governor fluctuation
        true = {
            "P_amb": np.full(n, pt.p_amb_mbar),
            "RH": np.full(n, 100 * pt.rh),
            "dP_inlet": np.full(n, inlet_loss_mbar),
            "dP_exhaust": np.full(n, exhaust_loss_mbar),
            "W_fuel": lerp(lambda r: r.fuel_flow) * (1 + 0.9 * load),
            "LHV": np.full(n, ends[0].fuel_lhv / 1e6),
            "P_elec": lerp(lambda r: r.electrical_power / 1e3) * (1 + load),
            "N_gg": lerp(lambda r: 100 * r.info["mechanical_speed"]),
            "P3": lerp(lambda r: r.stations["3"].p / 1e5),
            "T3": lerp(lambda r: r.stations["3"].t - 273.15),
        }
        for i in range(N_AMBIENT_T):
            true[f"T_amb_{i + 1}"] = t_amb
        t_exh = lerp(lambda r: r.exhaust.t - 273.15)
        for i in range(N_EXHAUST_TC):
            true[f"T_exh_{i + 1:02d}"] = t_exh + pattern[i]

        frame = {"time_s": np.arange(n) + k * (n + 300), "point": np.full(n, k + 1)}
        for tag, i in inst.items():
            x = true[tag]
            scale = np.abs(x) if i.relative else 1.0
            frame[tag] = x + scale * (biases[tag] + rng.normal(0, i.noise, n))
        for f in faults:
            if f.points and (k + 1) not in f.points:
                continue
            x = frame[f.tag]
            if f.kind == "offset":
                x += f.magnitude
            elif f.kind == "drift":
                x += f.magnitude * frac
            elif f.kind == "stuck":
                x[:] = x[0]
            elif f.kind == "spike":
                idx = rng.choice(n, size=max(1, n // 100), replace=False)
                x[idx] += f.magnitude * rng.choice([-1, 1], size=idx.size)
            else:
                raise ValueError(f"unknown fault kind {f.kind}")
        frames.append(frame)

    data = pd.concat([pd.DataFrame(f) for f in frames], ignore_index=True)
    ref = engine.run(inlet_loss=0.0, exhaust_loss=0.0, health=health)
    truth = {
        "power_MW": ref.electrical_power / 1e6,
        "heat_rate": ref.heat_rate,
        "exhaust_temp_C": ref.exhaust.t - 273.15,
        "exhaust_flow": ref.exhaust.w,
        "health": health,
    }
    metadata = {
        "serial": serial,
        "fuel": getattr(engine.cycle.combustor.fuel, "name", "fuel"),
        "duration_s": duration_s,
        "instruments": inst,
        "faults": list(faults),
    }
    return SyntheticTest(data, metadata, truth, biases)
