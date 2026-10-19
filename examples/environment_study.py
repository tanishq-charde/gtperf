"""Emissions and noise: DLE NOx and CO across load for the SGT-400 model, and the turbofan's LTO NOx and
noise sources.

Run from the repository root:  python examples/environment_study.py
Writes docs/environment.md and docs/figures/emissions.png.
"""

import matplotlib.pyplot as plt
import numpy as np
from parametric_study import FIG, INK2, ROOT

from gtperf.aero import Flight, Turbofan
from gtperf.aero_environment import lto_cycle, noise_sources
from gtperf.aero_offdesign import TurbofanEngine
from gtperf.cli import engine_from_datasheet
from gtperf.emissions import MCPD_NEW_GT_NOX_NATURAL_GAS, DLECombustor, compliance_table, lowest_compliant_load
from gtperf.fuel import natural_gas

BLUE, RED = "#2a78d6", "#d03b3b"
LOADS = np.round(np.arange(1.0, 0.14, -0.05), 2)
CO_LIMIT = 100.0  # mg/Nm3, a typical permit value


def industrial():
    _, _, engine = engine_from_datasheet(ROOT / "examples" / "sgt400_13mw.toml")
    staged = DLECombustor(natural_gas()).calibrate(engine.run(), 10.0, 5.0)
    unstaged = DLECombustor(**{**staged.__dict__, "staging": False})
    return {name: compliance_table(engine, c, LOADS, co_limit=CO_LIMIT)
            for name, c in (("With air staging", staged), ("Without staging", unstaged))}


def aero():
    cruise = Flight(10668.0, 0.78)
    cycle = Turbofan(bpr=10.0, fan_pr=1.5, fan_core_pr=2.4, hpc_pr=17.0, tet=1600.0, hp_offtake=150e3)
    engine = TurbofanEngine(cycle, cycle.size_for_thrust(22e3, cruise), cruise)
    lto = lto_cycle(engine, 1750.0)
    take_off = engine.run(Flight(), tet=1750.0)
    approach = engine.run(Flight(), thrust=0.3 * take_off.net_thrust)
    return lto, noise_sources(engine, take_off), noise_sources(engine, approach)


def figure(tables):
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.8))
    for (name, rows), ls in zip(tables.items(), ("-", "--")):
        load = [100 * r["load"] for r in rows]
        a1.plot(load, [r["nox_mg_15"] for r in rows], color=BLUE, ls=ls, label=name)
        a2.semilogy(load, [r["co_mg_15"] for r in rows], color=BLUE, ls=ls, label=name)
    a1.axhline(MCPD_NEW_GT_NOX_NATURAL_GAS, color=RED, lw=1.2)
    a1.axvspan(0, 70, color="#f1efe8", zorder=0)
    a1.text(15, MCPD_NEW_GT_NOX_NATURAL_GAS * 0.93, "MCPD new-plant limit (applies above 70 % load)",
            color=RED, fontsize=8, va="top")
    a2.axhline(CO_LIMIT, color=RED, lw=1.2)
    a2.text(15, CO_LIMIT * 1.15, "typical CO permit limit", color=RED, fontsize=8)
    a1.set(xlabel="Load (%)", ylabel="NOx, mg/Nm³ at 15 % O₂", ylim=(0, 60), xlim=(15, 100))
    a2.set(xlabel="Load (%)", ylabel="CO, mg/Nm³ at 15 % O₂", xlim=(15, 100), ylim=(3, 3000))
    a1.set_title("NOx against load, ISO ambient", loc="left")
    a2.set_title("CO against load", loc="left")
    a2.legend(frameon=False, fontsize=8, labelcolor=INK2)
    fig.tight_layout()
    fig.savefig(FIG / "emissions.png", dpi=150)


def main():
    tables = industrial()
    figure(tables)
    lto, to, ap = aero()
    staged, unstaged = tables["With air staging"], tables["Without staging"]
    base = staged[0]
    lines = [
        "# Emissions and noise", "",
        "## Industrial engine: DLE NOx and CO across load", "",
        f"The SGT-400 model's DLE combustor is calibrated to the published < 10 ppmvd NOx at ISO base load and an "
        f"assumed 5 ppmvd CO. The premixed flame then runs at {base['flame_temperature_K']:.0f} K. NOx is "
        f"{base['nox_mg_15']:.1f} mg/Nm³ at 15 % O₂ against the Medium Combustion Plant Directive's 50 mg/Nm³ "
        "for new gas turbines on natural gas, which applies above 70 % load.", "",
        "| Load | Output (MW) | Flame (K) | Premix air | NOx (ppmvd) | NOx (mg/Nm³) | CO (mg/Nm³) | Compliant |",
        "|---:|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    for r in staged[::2]:
        lines.append(f"| {100 * r['load']:.0f} % | {r['power_MW']:.2f} | {r['flame_temperature_K']:.0f} | "
                     f"{r['primary_fraction']:.2f} | {r['nox_ppm_15']:.1f} | {r['nox_mg_15']:.1f} | "
                     f"{r['co_mg_15']:.1f} | {'yes' if r['compliant'] else 'no'} |")
    lines += ["", f"Emissions-compliant down to {100 * lowest_compliant_load(staged):.0f} % load with air staging "
              f"(Siemens Energy publishes 30 % for the SGT-400, and the staging stop was set to match it), and only "
              f"to {100 * lowest_compliant_load(unstaged):.0f} % without, because the flame cools with TIT and CO "
              "climbs.", "", "![Emissions against load](figures/emissions.png)", "",
              "## Aero engine: LTO NOx", "",
              f"Rated thrust {lto['rated_thrust_kN']:.1f} kN at take-off TET 1750 K, rated pressure ratio "
              f"{lto['rated_pressure_ratio']:.1f}. Take-off EI calibrated to an illustrative 30 g/kg.", "",
              "| Mode | Thrust (kN) | T3 (K) | P3 (bar) | EI NOx (g/kg) | Fuel (kg) | NOx (g) |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for m in lto["modes"]:
        lines.append(f"| {m['mode']} | {m['thrust_kN']:.1f} | {m['t3_K']:.0f} | {m['p3_bar']:.1f} | "
                     f"{m['ei_nox']:.1f} | {m['fuel_kg']:.1f} | {m['nox_g']:.0f} |")
    lines += ["", f"Dp/Foo = {lto['dp_foo']:.1f} g/kN against {lto['tier8']:.1f} (CAEP/8, tier 8) and "
              f"{lto['tier6']:.1f} (CAEP/6, tier 6): a margin of {100 * (1 - lto['dp_foo'] / lto['tier8']):.0f} % "
              f"to CAEP/8. LTO fuel {lto['fuel_kg']:.0f} kg, CO2 {lto['co2_kg']:.0f} kg.", "",
              "## Aero engine: noise sources", "",
              "Source levels in dB re 20 µPa at 1 m (fan: Heidmann peak broadband; jets: Lighthill/von Glahn sound "
              "power as an omnidirectional level).", "",
              "| Source | Take-off | Approach (30 %) |", "|---|---:|---:|"]
    labels = {"fan_inlet_broadband": "Fan inlet broadband", "fan_discharge_broadband": "Fan discharge broadband",
              "core_jet": "Core jet mixing", "bypass_jet": "Bypass jet mixing", "total": "Total"}
    for k, label in labels.items():
        lines.append(f"| {label} | {to[k]:.1f} | {ap[k]:.1f} |")
    lines += ["", f"Fan tip relative Mach {to['fan_tip_relative_mach']:.2f} at take-off and "
              f"{ap['fan_tip_relative_mach']:.2f} at approach. The core jet dominates at take-off and the fan at "
              "approach, the known pattern for a high-bypass turbofan. These are source levels for trade studies; "
              "certification EPNdB would also need spectra, directivity, the flight path, atmospheric absorption, "
              "perceived-noise weighting and tone corrections."]
    (ROOT / "docs" / "environment.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[-14:]))


if __name__ == "__main__":
    main()
