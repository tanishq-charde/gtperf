"""Synthetic production pass-off test data.

A "true" engine (the model with hidden build-to-build health deltas) is run at the
test-day conditions, then observed through an instrument set. Each sensor gets a
fixed calibration bias (unknown to the analyst), random noise, and optionally an
injected fault, so the analysis chain can be checked against a known truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from .cycle import Ambient
from .exhaust import ExhaustProfileModel
from .instruments import Instrument, load_instruments
from .maps import NEW_AND_CLEAN

__all__ = ["DEFAULT_TEST_DATE", "Fault", "Instrument", "SyntheticTest", "TestPoint", "default_instruments",
           "generate_test"]

N_EXHAUST_TC = 12
N_AMBIENT_T = 3
DEFAULT_TEST_DATE = date(2026, 10, 12)


def default_instruments(path=None):
    """The test-bed instrument list, with uncertainties from standards, specifications and certificates.

    Read from ``gtperf/data/instruments.toml`` unless `path` is given. See
    `gtperf.instruments` for how class tolerances and certificates become
    standard uncertainties.
    """
    return load_instruments(path)


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
    test_date=DEFAULT_TEST_DATE,
    exhaust=None,
    exhaust_model=None,
    burner_fault=None,
):
    """Generate a 1 Hz pass-off test log for `engine` with hidden `health`.

    Each instrument's hidden calibration error is drawn from its own uncertainty
    model: within the class tolerance (with its stated distribution), or the
    certificate's as-found error plus the certificate uncertainty. The exhaust rake
    reads a field from `gtperf.exhaust`: `exhaust` (an ExhaustField) if given,
    otherwise one drawn from `exhaust_model` with optional `burner_fault`.

    Returns a SyntheticTest whose `truth` holds what the analysis should recover:
    the engine's true performance at reference (ISO, no losses) conditions.
    """
    rng = np.random.default_rng(seed)
    inst = instruments or default_instruments()
    biases = {tag: i.draw_bias(rng, test_date=test_date) for tag, i in inst.items()}
    # the engine's exhaust field comes from its own random stream, so supplying one leaves the sensor noise unchanged
    if exhaust is None:
        exhaust = (exhaust_model or ExhaustProfileModel()).sample(np.random.default_rng([seed, 1]), burner_fault)
    field_ = exhaust
    rated = engine.run().electrical_power  # nominal engine at ISO: load fraction reference
    n = int(duration_s)
    frames = []
    loads = []

    for k, pt in enumerate(points):
        frac = np.linspace(0, 1, n)
        t_amb = pt.t_amb_C + pt.drift_K * (frac - 0.5)
        kw = dict(inlet_loss=inlet_loss_mbar * 100, exhaust_loss=exhaust_loss_mbar * 100, health=health)
        ends = [engine.run(Ambient(t + 273.15, pt.p_amb_mbar * 100, pt.rh), **kw)
                for t in (t_amb[0], t_amb[-1])]

        def lerp(get, ends=ends, frac=frac):
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
        t_exh = lerp(lambda r: r.exhaust.t - 273.15)  # mass-weighted mean from the cycle
        load = 0.5 * (ends[0].electrical_power + ends[1].electrical_power) / rated
        loads.append(load)
        profile = field_.rake(load)
        for i in range(N_EXHAUST_TC):
            true[f"T_exh_{i + 1:02d}"] = t_exh + profile[i]

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
        "exhaust_field": field_,
        "loads": loads,
    }
    metadata = {
        "serial": serial,
        "fuel": getattr(engine.cycle.combustor.fuel, "name", "fuel"),
        "duration_s": duration_s,
        "instruments": inst,
        "faults": list(faults),
        "test_date": test_date,
    }
    return SyntheticTest(data, metadata, truth, biases)
