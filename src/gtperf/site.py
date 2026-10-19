"""Installed (site) performance prediction and correction curves."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from .cycle import ISO, Ambient
from .maps import NEW_AND_CLEAN


def isa_pressure(altitude_m):
    """ISA static pressure, Pa, valid in the troposphere."""
    return 101325.0 * (1 - 2.25577e-5 * altitude_m) ** 5.25588


@dataclass
class Site:
    name: str = "ISO"
    altitude_m: float = 0.0
    temperature_C: float = 15.0
    rh: float = 0.60
    inlet_loss_mbar: float = 0.0
    exhaust_loss_mbar: float = 0.0
    fuel: object = None

    def ambient(self):
        return Ambient(self.temperature_C + 273.15, isa_pressure(self.altitude_m), self.rh)


@dataclass
class SitePerformance:
    site: Site
    iso: object  # CycleResult at ISO, no losses, reference fuel
    result: object  # CycleResult at site
    factors: list = field(default_factory=list)  # (effect, power factor, heat rate factor)

    def summary_rows(self):
        r, i = self.result, self.iso
        return [
            ("Electrical output", "MW", i.electrical_power / 1e6, r.electrical_power / 1e6),
            ("Heat rate (LHV)", "kJ/kWh", i.heat_rate, r.heat_rate),
            ("Thermal efficiency (LHV)", "%", 100 * i.thermal_efficiency, 100 * r.thermal_efficiency),
            ("Fuel heat input (LHV)", "MW", i.heat_input / 1e6, r.heat_input / 1e6),
            ("Exhaust flow", "kg/s", i.exhaust.w, r.exhaust.w),
            ("Exhaust temperature", "°C", i.exhaust.t - 273.15, r.exhaust.t - 273.15),
            ("Exhaust energy above ambient", "MW", i.exhaust_energy / 1e6, r.exhaust_energy / 1e6),
        ]

    def to_markdown(self):
        s = self.site
        info = self.result.info
        lines = [
            f"## Site performance: {s.name}",
            "",
            f"Ambient {s.temperature_C:.1f} °C, {s.rh * 100:.0f} % RH, altitude {s.altitude_m:.0f} m "
            f"({isa_pressure(s.altitude_m) / 100:.1f} mbar). Inlet loss {s.inlet_loss_mbar:.1f} mbar, "
            f"exhaust loss {s.exhaust_loss_mbar:.1f} mbar.",
            "",
            "| Parameter | Unit | ISO | Site |",
            "|---|---|---:|---:|",
        ]
        lines += [f"| {n} | {u} | {a:,.2f} | {b:,.2f} |" for n, u, a, b in self.summary_rows()]
        if info:
            lines += ["", f"Control mode at site: {info['control']} limit. "
                      f"Compressor surge margin {100 * info['surge_margin']:.1f} %."]
        lines += ["", "### Correction factors (applied in order, site = ISO x product)", "",
                  "| Effect | Output | Heat rate |", "|---|---:|---:|"]
        lines += [f"| {e} | {p:.4f} | {h:.4f} |" for e, p, h in self.factors]
        return "\n".join(lines)

    def to_csv(self):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["parameter", "unit", "iso", "site"])
        w.writerows(self.summary_rows())
        return buf.getvalue()


def site_performance(engine, site, health=NEW_AND_CLEAN):
    """Predict site performance and break the change from ISO into correction factors.

    Effects are switched on one at a time in a fixed order (temperature, altitude,
    humidity, inlet loss, exhaust loss, fuel, then degradation if given), so the factors multiply exactly to the
    site value. Interactions are absorbed by whichever effect comes later.
    """
    target = site.ambient()
    steps = [
        ("Ambient temperature", dict(ambient=Ambient(target.t, ISO.p, ISO.rh))),
        ("Altitude (pressure)", dict(ambient=Ambient(target.t, target.p, ISO.rh))),
        ("Humidity", dict(ambient=target)),
        ("Inlet loss", dict(ambient=target, inlet_loss=site.inlet_loss_mbar * 100)),
        ("Exhaust loss", dict(ambient=target, inlet_loss=site.inlet_loss_mbar * 100,
                              exhaust_loss=site.exhaust_loss_mbar * 100)),
        ("Fuel", dict(ambient=target, inlet_loss=site.inlet_loss_mbar * 100,
                      exhaust_loss=site.exhaust_loss_mbar * 100, fuel=site.fuel)),
    ]
    if health != NEW_AND_CLEAN:
        steps.append(("Degradation", dict(steps[-1][1], health=health)))
    iso = engine.run(ISO, inlet_loss=0.0, exhaust_loss=0.0)
    prev, factors, guess = iso, [], iso.info.get("solution")
    for name, kwargs in steps:
        kwargs.setdefault("inlet_loss", 0.0)
        kwargs.setdefault("exhaust_loss", 0.0)
        r = engine.run(guess=guess, **kwargs)
        factors.append((name, r.electrical_power / prev.electrical_power, r.heat_rate / prev.heat_rate))
        prev, guess = r, r.info.get("solution")
    return SitePerformance(site, iso, prev, factors)


def correction_curves(engine, temperatures_C, **run_kwargs):
    """Performance relative to ISO across ambient temperature. Returns a dict of lists."""
    iso = engine.run(ISO, **run_kwargs)
    out = {k: [] for k in ("temperature_C", "power", "heat_rate", "exhaust_flow", "exhaust_temp_delta_K",
                           "tit_C", "control", "surge_margin")}
    guess = iso.info.get("solution")
    for t in temperatures_C:
        r = engine.run(Ambient(t=t + 273.15), guess=guess, **run_kwargs)
        guess = r.info.get("solution")
        out["temperature_C"].append(t)
        out["power"].append(r.electrical_power / iso.electrical_power)
        out["heat_rate"].append(r.heat_rate / iso.heat_rate)
        out["exhaust_flow"].append(r.exhaust.w / iso.exhaust.w)
        out["exhaust_temp_delta_K"].append(r.exhaust.t - iso.exhaust.t)
        out["tit_C"].append(r.info["tit"] - 273.15)
        out["control"].append(r.info["control"])
        out["surge_margin"].append(r.info["surge_margin"])
    return out
