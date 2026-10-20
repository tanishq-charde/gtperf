"""Aero engine environmental estimates: LTO-cycle NOx against the ICAO/EPA standard, and noise sources.

**NOx.** The emissions index comes from the NOx severity parameter used for aero combustors in GasTurb
(Kurzke), which needs only combustor inlet pressure, temperature and humidity:

    S_NOx = (P3 / 2965 kPa)**0.4 * exp((T3 - 826 K) / 194 K + (6.29 - 100 * WAR) / 53.2)
    EI_NOx = EI_1 * S_NOx                                                   [g NOx per kg fuel]

EI_1, the index at severity 1, describes the combustor technology: 32 g/kg is a conventional rich-burn
combustor; lean-burn designs are far lower. WAR is the water-to-air ratio (kg/kg); water vapour lowers NOx,
and the ICAO reference humidity of 0.00634 kg/kg is used for the LTO cycle. Each LTO mode is run as its own
off-design point (take-off 0.7 min at 100 % rated thrust, climb 2.2 min at 85 %, approach 4.0 min at 30 %,
taxi and idle 26 min at 7 %), so T3 and P3, and with them the index, are the engine's own at each thrust.
Dp/Foo, grams of NOx per kN of rated thrust, is compared with the standard for the rated pressure ratio (US
40 CFR 1031.60, tier 6 in production and tier 8 for new types, the ICAO CAEP/6 and CAEP/8 levels, for rated
thrust above 89 kN). `NOxCorrelation`, an index calibrated to one known point and scaled by P3 and T3, is
kept for calibrating to measured data.

**Noise.** Source levels for the two components that dominate a turbofan at take-off and approach:

- fan broadband noise, inlet and discharge, by Heidmann's method (NASA TM X-71763, 1975): peak SPL at
  1 m = 20 log(dT/0.555 K) + 10 log(m/0.4536 kg/s) + F1(tip relative Mach), with F1 = 58.5 dB (inlet) or
  60.0 dB (discharge) for a subsonic design tip and the published corrections above Mach 1. Rotor-stator
  spacing and directivity terms are taken at their reference values (0 dB), and tones are not included;
- jet mixing noise from Lighthill's eighth-power law as correlated by von Glahn (NASA TM X-68059, 1972):
  sound power W = K rho_a A (V_j - V_0)**8 / c_a**5 with K = 3e-5, for each stream, converted to an
  equivalent omnidirectional SPL at 1 m.

These are component source levels for trade studies, not certification EPNdB: that needs spectra,
directivity, flight path, atmospheric absorption, perceived-noise weighting and tone corrections.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .aero import SEA_LEVEL_STATIC

LTO_MODES = (("Take-off", 1.00, 0.7), ("Climb", 0.85, 2.2), ("Approach", 0.30, 4.0), ("Taxi/idle", 0.07, 26.0))
CO2_PER_KG_FUEL = 3.16  # kg CO2 per kg of kerosene, the factor ICAO uses


def nox_standard(rated_pr, tier=8):
    """NOx standard Dp/Foo (g/kN) for rated thrust above 89 kN (40 CFR 1031.60 tables 4 and 5)."""
    if rated_pr >= (104.7 if tier == 8 else 82.6):
        return 32 + 1.6 * rated_pr
    if rated_pr <= 30:
        return (7.88 if tier == 8 else 16.72) + 1.408 * rated_pr
    return (-9.88 if tier == 8 else -1.04) + 2.0 * rated_pr


@dataclass(frozen=True)
class NOxCorrelation:
    ei_ref: float = 30.0  # g/kg at the reference point
    p3_ref: float = 1.0
    t3_ref: float = 1.0
    pressure_exponent: float = 0.5
    t_scale: float = 180.0

    def calibrate(self, result, ei):
        s = result.stations["3"]
        return NOxCorrelation(ei, s.p, s.t, self.pressure_exponent, self.t_scale)

    def ei(self, result):
        s = result.stations["3"]
        pressure = (s.p / self.p3_ref) ** self.pressure_exponent
        return self.ei_ref * pressure * math.exp((s.t - self.t3_ref) / self.t_scale)


@dataclass(frozen=True)
class NOxSeverity:
    """EI_NOx = ei_at_severity_1 * S_NOx (see the module notes)."""

    ei_at_severity_1: float = 32.0
    war: float = 0.00634  # water-to-air ratio, kg/kg (ICAO reference humidity)

    def severity(self, p3, t3):
        return (p3 / 2965e3) ** 0.4 * math.exp((t3 - 826.0) / 194.0 + (6.29 - 100 * self.war) / 53.2)

    def ei(self, result):
        s = result.stations["3"]
        return self.ei_at_severity_1 * self.severity(s.p, s.t)


def _run_at_thrust(engine, flight, thrust, start, steps=8):
    """Operating point at a net thrust, falling back to stepping down in thrust from a converged point
    `start` (a result) when the direct solve does not converge."""
    from .aero_offdesign import MatchError

    try:
        return engine.run(flight, thrust=thrust, guess=start.info["x"])
    except MatchError:
        pass
    r = start
    for i in range(1, steps + 1):
        r = engine.run(flight, thrust=start.net_thrust + (thrust - start.net_thrust) * i / steps, guess=r.info["x"])
    return r


def lto_cycle(engine, take_off_tet, correlation=None, flight=SEA_LEVEL_STATIC):
    """LTO emissions of a TurbofanEngine rated at `take_off_tet`, with the index from `correlation`
    (default `NOxSeverity()`). Returns a dict with per-mode rows, rated thrust (kN), rated pressure ratio,
    Dp/Foo and the tier 6 and tier 8 standards."""
    rated = engine.run(flight, tet=take_off_tet)
    if correlation is None:
        correlation = NOxSeverity()
    elif isinstance(correlation, NOxCorrelation):
        correlation = correlation.calibrate(rated, correlation.ei_ref)
    foo = rated.net_thrust
    rpr = rated.stations["3"].p / rated.stations["2"].p
    rows, results, last, nox_total, fuel_total = [], [], rated, 0.0, 0.0
    for name, frac, minutes in LTO_MODES:
        r = rated if frac == 1.0 else _run_at_thrust(engine, flight, frac * foo, last)
        last = r
        results.append(r)
        ei = correlation.ei(r)
        fuel = r.fuel_flow * minutes * 60
        rows.append({"mode": name, "thrust_kN": r.net_thrust / 1e3, "fuel_kg_s": r.fuel_flow,
                     "t3_K": r.stations["3"].t, "p3_bar": r.stations["3"].p / 1e5, "ei_nox": ei,
                     "fuel_kg": fuel, "nox_g": ei * fuel, "n1": r.info["n1"], "n2": r.info["n2"],
                     "tet_K": r.info["tet"]})
        nox_total += ei * fuel
        fuel_total += fuel
    dp_foo = nox_total / (foo / 1e3)
    return {"modes": rows, "rated_thrust_kN": foo / 1e3, "rated_pressure_ratio": rpr, "dp_foo": dp_foo,
            "tier6": nox_standard(rpr, 6), "tier8": nox_standard(rpr, 8), "fuel_kg": fuel_total,
            "co2_kg": CO2_PER_KG_FUEL * fuel_total, "results": results}


# --- noise ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class FanGeometry:
    """Fan face sized at the design point: axial Mach number and hub-tip ratio set the tip radius."""

    axial_mach: float = 0.6
    hub_tip: float = 0.3


def _tip_relative_mach(engine, result, geometry):
    """Fan tip relative Mach number from the fan face flow and tip speed."""
    s2 = result.stations["2"]
    gas, t0 = s2.gas, s2.t
    g = gas.gamma(t0)
    # static conditions at the design-point fan face Mach number, then scaled by this point's corrected flow
    m_ax = geometry.axial_mach * min(result.info.get("fan_flow", 1.0), 1.15)
    t = t0 / (1 + (g - 1) / 2 * m_ax**2)
    a = math.sqrt(g * gas.R * t)
    tip = engine.fan_tip_radius
    u = result.info["n1_rpm"] * 2 * math.pi / 60 * tip
    return math.hypot(m_ax * a, u) / a


FAN_GEOMETRY = FanGeometry()


def fan_tip_radius(engine, geometry=FAN_GEOMETRY):
    """Tip radius (m) of a fan whose face passes the design flow at the given axial Mach number."""
    s2 = engine.design.stations["2"]
    gas, t0, p0 = s2.gas, s2.t, s2.p
    g = gas.gamma(t0)
    m = geometry.axial_mach
    t = t0 / (1 + (g - 1) / 2 * m * m)
    p = p0 * (t / t0) ** (g / (g - 1))
    area = s2.w / (p / (gas.R * t) * m * math.sqrt(g * gas.R * t))
    return math.sqrt(area / (math.pi * (1 - geometry.hub_tip**2)))


def _heidmann_f1(base, mtr_design, mtr, knee):
    level = base
    if mtr_design > 1.0:
        level += 20 * math.log10(mtr_design)
    if mtr > knee:
        level -= 20 * math.log10(mtr)
    return level


def noise_sources(engine, result, geometry=FAN_GEOMETRY):
    """Component source levels (dB re 20 uPa at 1 m) at one operating point: fan inlet and discharge
    broadband (Heidmann, peak) and core and bypass jet mixing noise (Lighthill/von Glahn, omnidirectional)."""
    if not hasattr(engine, "fan_tip_radius"):
        engine.fan_tip_radius = fan_tip_radius(engine, geometry)
        design = engine.design
        design.info.setdefault("n1_rpm", engine.spools.n1_rpm)
        engine.fan_mtr_design = _tip_relative_mach(engine, design, geometry)
    st = result.stations
    dt = st["13"].t - st["2"].t
    m = st["2"].w
    mtr = _tip_relative_mach(engine, result, geometry)
    base = 20 * math.log10(dt / 0.555) + 10 * math.log10(m / 0.4536)
    out = {
        "fan_tip_relative_mach": mtr,
        "fan_inlet_broadband": base + _heidmann_f1(58.5, engine.fan_mtr_design, mtr, 0.9),
        "fan_discharge_broadband": base + _heidmann_f1(60.0, engine.fan_mtr_design, mtr, 1.0),
    }
    t_amb, p_amb = result.flight.static()
    air = result.flight.air()
    rho_a = p_amb / (air.R * t_amb)
    c_a = math.sqrt(air.gamma(t_amb) * air.R * t_amb)
    v0 = result.v0
    for name, jet in (("core_jet", result.core_jet), ("bypass_jet", result.bypass_jet)):
        # an under-expanded jet is represented by its fully expanded velocity, gross thrust / flow
        dv = max(jet.gross_thrust / jet.w - v0, 1e-3)
        power = 3e-5 * rho_a * jet.area * dv**8 / c_a**5
        out[name] = 10 * math.log10(power / 1e-12) - 10 * math.log10(4 * math.pi)
    out["total"] = 10 * math.log10(sum(10 ** (v / 10) for k, v in out.items() if k != "fan_tip_relative_mach"))
    return out
