"""NOx and CO from a lean-premixed (DLE) combustor, and stack emissions against limits.

Thermal NO forms through the Zeldovich mechanism, whose rate-limiting step N2 + O -> NO + N has an
activation temperature of about 38,000 K. In a lean premixed flame almost all NO is thermal, so NOx
corrected to 15 % O2 follows the flame temperature very steeply:

    NOx = NOx_ref * exp(Ta * (1/T_ref - 1/T_flame)) * (P3 / P3_ref)**n

with T_flame the adiabatic temperature of the premixed zone and n about 0.5 (thermal NO scales with the
square root of pressure through the equilibrium O atom concentration). CO does the opposite: below a
flame temperature the burnout of CO slows and CO rises roughly exponentially as the flame cools,

    CO = CO_ref * exp(k_co * (T_ref - T_flame))

The premixed zone takes `primary_fraction` of the combustor air; the rest is liner cooling and
dilution. At part load a DLE system keeps the flame hot by staging air or fuel; here that is an air
staging that reduces the premix air fraction down to `min_primary_fraction` to hold the base-load flame
temperature. Below the load where it reaches that stop, the flame cools and CO climbs, which sets the
emissions-compliant turndown.

Reference values are calibrated at one point (usually ISO base load) to the engine's guaranteed or
measured emissions. The activation temperature is physics; the CO slope and the staging range are
assumptions to be replaced by rig data.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from scipy.optimize import brentq

ZELDOVICH_TA = 38370.0  # K, activation temperature of N2 + O -> NO + N
MOLAR_VOLUME = 22.414  # Nm3/kmol at 0 °C, 1.01325 bar
MW_NO2, MW_CO = 46.0055, 28.0101
O2_AIR = 20.95  # % by volume, dry air


def flame_temperature(result, fraction):
    """Adiabatic temperature when all the fuel burns with `fraction` of the combustor inlet air (station 31)."""
    s31, s4 = result.stations["31"], result.stations["4"]
    fuel = _fuel_of(result)
    far = result.fuel_flow / (s31.w * fraction)
    q = fuel.heat_release_term(298.15, 1.0)
    products = fuel.products(s31.gas, far)
    h = (s31.h + far * q) / (1 + far)
    return products.t_from_h(h, t_guess=s4.t + 300.0)


def _fuel_of(result):
    fuel = result.info.get("fuel")
    if fuel is None:
        raise ValueError("result has no fuel; pass the fuel with result.info['fuel'] (see DLECombustor.attach)")
    return fuel


def dry_o2_percent(gas):
    x = gas.mole_fractions
    return 100 * x.get("O2", 0.0) / (1 - x.get("H2O", 0.0))


@dataclass(frozen=True)
class DLECombustor:
    """Correlation-based DLE combustor emissions model (see module docstring)."""

    fuel: object
    primary_fraction: float = 0.75  # gives a base-load flame near 1800 K, typical of single-digit-ppm DLE
    min_primary_fraction: float = 0.55  # staging stop; this value puts turndown near the SGT-400's published 30 %
    nox_ref: float = 10.0  # ppmvd at 15 % O2
    co_ref: float = 5.0  # ppmvd at 15 % O2
    t_ref: float = 1800.0  # K, flame temperature at the reference point
    p3_ref: float = 16.5e5  # Pa
    pressure_exponent: float = 0.5
    co_slope: float = 0.02  # 1/K: about a factor of 7 per 100 K of flame cooling
    staging: bool = True

    def calibrate(self, result, nox_ppm, co_ppm):
        """Set the reference point to `result` with its measured or guaranteed NOx and CO."""
        r = self.attach(result)
        return replace(self, nox_ref=nox_ppm, co_ref=co_ppm, t_ref=flame_temperature(r, self.primary_fraction),
                       p3_ref=r.stations["3"].p)

    def attach(self, result):
        result.info.setdefault("fuel", self.fuel)
        return result

    def primary_zone(self, result):
        """Premix air fraction and flame temperature, with air staging holding the reference flame temperature
        down to the staging stop."""
        r = self.attach(result)
        tf = flame_temperature(r, self.primary_fraction)
        if not self.staging or tf >= self.t_ref:
            return self.primary_fraction, tf
        t_min = flame_temperature(r, self.min_primary_fraction)
        if t_min <= self.t_ref:
            return self.min_primary_fraction, t_min
        f = brentq(lambda f: flame_temperature(r, f) - self.t_ref, self.min_primary_fraction, self.primary_fraction,
                   xtol=1e-10)
        return f, self.t_ref

    def emissions(self, result):
        """NOx and CO in ppmvd and mg/Nm3 at 15 % O2, dry exhaust O2, and mass flows."""
        frac, tf = self.primary_zone(result)
        p3 = result.stations["3"].p
        pressure = (p3 / self.p3_ref) ** self.pressure_exponent
        nox = self.nox_ref * _exp(ZELDOVICH_TA * (1 / self.t_ref - 1 / tf)) * pressure
        co = self.co_ref * _exp(self.co_slope * (self.t_ref - tf))
        ex = result.exhaust
        o2 = dry_o2_percent(ex.gas)
        # mass flow from ppm at 15 % O2: dry flue volume at 15 % O2 that carries the same pollutant mass
        x = ex.gas.mole_fractions
        dry_kmol = ex.w / (ex.gas.mw * 1000) * (1 - x.get("H2O", 0.0))  # kmol/s of dry gas (mw in kg/mol)
        v15 = dry_kmol * MOLAR_VOLUME * (O2_AIR - o2) / (O2_AIR - 15.0)  # Nm3/s at 15 % O2
        mg_nox, mg_co = nox * MW_NO2 / MOLAR_VOLUME, co * MW_CO / MOLAR_VOLUME
        return {
            "flame_temperature_K": tf,
            "primary_fraction": frac,
            "nox_ppm_15": nox,
            "co_ppm_15": co,
            "nox_mg_15": mg_nox,
            "co_mg_15": mg_co,
            "o2_dry_pct": o2,
            "nox_kg_h": mg_nox * v15 * 3600 / 1e6,
            "co_kg_h": mg_co * v15 * 3600 / 1e6,
        }


def _exp(x):
    import math

    return math.exp(min(x, 50.0))


def correct_to_15_o2(ppm_measured, o2_dry_measured):
    """Correct a dry concentration measured at o2_dry_measured % O2 to 15 % O2."""
    return ppm_measured * (O2_AIR - 15.0) / (O2_AIR - o2_dry_measured)


# EU Medium Combustion Plant Directive (EU) 2015/2193, Annex II Part 2 Table 2, new gas turbines:
# NOx 50 mg/Nm3 on natural gas, 75 on other gaseous fuels and gas oil, dry at 15 % O2, applying above
# 70 % load only (footnote 34). Existing plants (Part 1 Table 3): 150 mg/Nm3 on natural gas.
MCPD_NEW_GT_NOX_NATURAL_GAS = 50.0
MCPD_LOAD_THRESHOLD = 0.70


def compliance_table(engine, combustor, loads, ambient=None, nox_limit=MCPD_NEW_GT_NOX_NATURAL_GAS,
                     co_limit=100.0):
    """Emissions across load at one ambient. NOx is judged only where the limit applies (above 70 % load for
    the MCPD); CO against `co_limit` (mg/Nm3, a typical permit value; the MCPD sets no CO limit)."""
    rows = []
    kw = {} if ambient is None else {"ambient": ambient}
    for load in loads:
        r = engine.run(load=load, **kw)
        e = combustor.emissions(r)
        nox_ok = load <= MCPD_LOAD_THRESHOLD or e["nox_mg_15"] <= nox_limit
        rows.append({"load": load, "power_MW": r.electrical_power / 1e6, **e,
                     "compliant": bool(nox_ok and e["co_mg_15"] <= co_limit)})
    return rows


def lowest_compliant_load(rows):
    """Lowest load from which every higher tabulated load is compliant."""
    lowest = None
    for row in sorted(rows, key=lambda r: -r["load"]):
        if not row["compliant"]:
            break
        lowest = row["load"]
    return lowest


__all__ = ["DLECombustor", "compliance_table", "correct_to_15_o2", "flame_temperature",
           "lowest_compliant_load"]
