"""Calculations behind each GUI section, independent of the user interface.

Every study returns a `Section`: what was asked (inputs), the headline numbers (key results), tables as
DataFrames and figures as matplotlib Figures. The exporters and the GUI only read sections, so a study added
here appears in every download format without further work.
"""

from __future__ import annotations

import copy
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.category import StrCategoryFormatter  # noqa: E402
from matplotlib.text import Text  # noqa: E402
from matplotlib.ticker import FixedFormatter, FuncFormatter  # noqa: E402

from ..cli import engine_from_config, offdesign_options  # noqa: E402
from ..cycle import ISO, Ambient  # noqa: E402
from ..maps import NEW_AND_CLEAN, Health  # noqa: E402
from ..site import correction_curves, isa_pressure, site_performance  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
# the SGT-400 example datasheet; a copy ships with the package for installs without the repository
DEFAULT_DATASHEET = ROOT / "examples" / "sgt400_13mw.toml"
if not DEFAULT_DATASHEET.exists():
    DEFAULT_DATASHEET = Path(__file__).resolve().parents[1] / "data" / "sgt400_13mw.toml"

# Brand colours: the logo's navy, one accent, and status colours
NAVY, BLUE, RED, GREEN, AMBER = "#12224a", "#2a5fa8", "#b0302d", "#2f7d4f", "#b07a10"
INK, INK2, GRID, SURFACE = "#000000", "#333333", "#d9d6cc", "none"
SERIES = [NAVY, BLUE, "#7d9cc9", AMBER, RED, GREEN]

# Charts look like LaTeX (pgfplots) figures: Computer Modern text, a full box, inward ticks on all sides.
plt.rcParams.update(
    {
        "font.family": "serif",
        "font.serif": ["cmr10"],
        "mathtext.fontset": "cm",
        "axes.formatter.use_mathtext": True,
        "axes.unicode_minus": True,
        "font.size": 10,
        "axes.linewidth": 0.8,
        "axes.edgecolor": INK,
        "axes.labelcolor": INK,
        "xtick.color": INK,
        "ytick.color": INK,
        "xtick.direction": "in",
        "ytick.direction": "in",
        "xtick.top": True,
        "ytick.right": True,
        "xtick.minor.visible": True,
        "ytick.minor.visible": True,
        "xtick.major.size": 4,
        "ytick.major.size": 4,
        "xtick.minor.size": 2,
        "ytick.minor.size": 2,
        "lines.linewidth": 1.3,
        "lines.markersize": 4,
        "legend.frameon": True,
        "legend.fancybox": False,
        "legend.edgecolor": INK,
        "legend.framealpha": 1.0,
        "patch.linewidth": 0.6,
        "savefig.transparent": False,
    }
)
# cmr10 maps code points above ASCII, and a few within it, to other glyphs, so those characters are written in math mode
_TEX = {
    "°": r"^\circ",
    "±": r"\pm",
    "−": "-",
    "·": r"\cdot",
    "×": r"\times",
    "≥": r"\geq",
    "≤": r"\leq",
    "→": r"\rightarrow",
    "≈": r"\approx",
    "Δ": r"\Delta",
    "η": r"\eta",
    "²": "^2",
    "³": "^3",
    "₂": "_2",
    "₄": "_4",
    "_": r"\_",  # OT1 encoding puts accents and other glyphs at these ASCII positions
    "<": "<",
    ">": ">",
    "|": "|",
    "{": r"\{",
    "}": r"\}",
}


def tex(text):
    """Make a label safe for Computer Modern: non-ASCII characters go through math mode."""
    if not text or "$" in text or (text.isascii() and not any(c in _TEX for c in text)):
        return text
    out = []
    for c in text:
        if c.isascii() and c not in _TEX:
            out.append(c)
        else:
            out.append("$" + _TEX.get(c, r"\mathrm{" + c + "}") + "$")
    return "".join(out).replace("$$", "")


def latexify(fig):
    for t in fig.findobj(Text):
        t.set_text(tex(t.get_text()))
    for ax in fig.axes:
        for axis in (ax.xaxis, ax.yaxis):
            if isinstance(axis.get_major_formatter(), (StrCategoryFormatter, FixedFormatter, FuncFormatter)):
                f = axis.get_major_formatter()
                axis.set_major_formatter(FuncFormatter(lambda x, pos, f=f: tex(f(x, pos))))
                axis.set_minor_locator(plt.NullLocator())
    return fig


@dataclass
class Section:
    key: str
    title: str
    inputs: dict = field(default_factory=dict)  # label -> value (with unit in the label)
    key_results: list = field(default_factory=list)  # (label, value text)
    tables: dict = field(default_factory=dict)  # name -> DataFrame
    figures: dict = field(default_factory=dict)  # name -> matplotlib Figure
    notes: list = field(default_factory=list)

    def __post_init__(self):
        for fig in self.figures.values():
            latexify(fig)


def style(ax, title=None, xlabel=None, ylabel=None):
    ax.set_facecolor(SURFACE)
    ax.grid(False)
    if title:
        ax.set_title(title, fontsize=11, color=INK)
    if xlabel:
        ax.set_xlabel(xlabel)
    if ylabel:
        ax.set_ylabel(ylabel)


def figure(w=7.0, h=4.0, ncols=1):
    fig, axes = plt.subplots(1, ncols, figsize=(w, h), constrained_layout=True)
    fig.patch.set_facecolor("white")
    return fig, axes


def legend(ax, **kw):
    ax.legend(fontsize=8, **kw)


# --- engine ----------------------------------------------------------------------------------------


def load_datasheet(path=DEFAULT_DATASHEET):
    with open(path, "rb") as f:
        return tomllib.load(f)


def datasheet_with(base, **changes):
    """A copy of a datasheet dict with top-level values and assumptions changed by key."""
    d = copy.deepcopy(base)
    for k, v in changes.items():
        if k in d.get("assumptions", {}):
            d["assumptions"][k] = v
        elif k in d.get("assumptions", {}).get("cooling", {}):
            d["assumptions"]["cooling"][k] = v
        else:
            d[k] = v
    return d


@dataclass
class EngineModel:
    datasheet: dict
    calibration: object
    engine: object


def build_engine(datasheet):
    d, cal, engine = engine_from_config(datasheet)
    return EngineModel(d, cal, engine)


# Design mode: the engine is built forwards from its design values instead of fitted to a datasheet. The
# defaults are the values fitted to the published SGT-400 rating, so they reproduce it (12.9 MW, 10,294 kJ/kWh).
DESIGN_DEFAULTS = {
    "name": "My engine (SGT-400 starting values)",
    "air_flow_kg_s": 39.22,
    "pressure_ratio": 17.1,
    "tit_C": 1280.0,
    "compressor_polytropic_eff": 0.89,
    "turbine_eff": 0.8685,
    "combustor_dp": 0.04,
    "mechanical_efficiency": 0.99,
    "generator_efficiency": 0.98,
    "ngv": 0.07,
    "rotor": 0.05,
    "pt": 0.03,
    "port": 0.6,
    "gg_speed_limit": 1.03,
    "generator_MVA": 20.0,
    "power_factor": 0.8,
}


def design_engine(design=None):
    """An EngineModel built forwards from design values (missing keys take DESIGN_DEFAULTS)."""
    from ..components import Combustor, Compressor, Turbine
    from ..cycle import Cooling, TwinShaftCycle
    from ..fuel import natural_gas
    from ..offdesign import Generator, Limits, TwinShaftEngine

    d = {**DESIGN_DEFAULTS, **(design or {})}
    cycle = TwinShaftCycle(
        Compressor(d["pressure_ratio"], d["compressor_polytropic_eff"]),
        Combustor(natural_gas(), d["tit_C"] + 273.15, dp_frac=d["combustor_dp"]),
        Turbine(d["turbine_eff"]),
        Turbine(d["turbine_eff"]),
        cooling=Cooling(ngv=d["ngv"], rotor=d["rotor"], pt=d["pt"], port=d["port"]),
        mechanical_efficiency=d["mechanical_efficiency"],
        generator_efficiency=d["generator_efficiency"],
    )
    sgt = load_datasheet()  # map choice and the VIGV and bleed schedules are generic; limits come from `d`
    cmap, limits, controls = offdesign_options(sgt.get("offdesign"), d["pressure_ratio"])
    limits = Limits(
        gg_speed=d["gg_speed_limit"],
        gg_corrected_speed=d["gg_speed_limit"],
        generator=Generator(d["generator_MVA"], d["power_factor"], limits.generator.derate),
    )
    engine = TwinShaftEngine(cycle, d["air_flow_kg_s"], compressor_map=cmap, limits=limits, controls=controls)
    return EngineModel(d, None, engine)


def design_section(model):
    d, engine = model.datasheet, model.engine
    iso = engine.run(ISO)
    labels = {
        "name": "Engine name",
        "air_flow_kg_s": "Air flow (kg/s)",
        "pressure_ratio": "Pressure ratio",
        "tit_C": "Turbine entry temperature (°C)",
        "compressor_polytropic_eff": "Compressor polytropic efficiency",
        "turbine_eff": "Turbine isentropic efficiency",
        "combustor_dp": "Combustor pressure loss (fraction)",
        "mechanical_efficiency": "Mechanical efficiency",
        "generator_efficiency": "Generator efficiency",
        "ngv": "NGV cooling air (fraction of inlet flow)",
        "rotor": "HP rotor cooling air (fraction)",
        "pt": "Power turbine cooling air (fraction)",
        "port": "Interstage bleed position",
        "gg_speed_limit": "Gas generator speed limit (relative)",
        "generator_MVA": "Generator rating (MVA)",
        "power_factor": "Power factor",
    }
    inputs = {labels[k]: d[k] for k in labels}
    perf = pd.DataFrame(
        [
            {"Quantity": "Electrical output", "Unit": "MW", "Value": iso.electrical_power / 1e6},
            {"Quantity": "Heat rate (LHV)", "Unit": "kJ/kWh", "Value": iso.heat_rate},
            {"Quantity": "Thermal efficiency (LHV)", "Unit": "%", "Value": 100 * iso.thermal_efficiency},
            {"Quantity": "Fuel heat input (LHV)", "Unit": "MW", "Value": iso.heat_input / 1e6},
            {"Quantity": "Exhaust flow", "Unit": "kg/s", "Value": iso.exhaust.w},
            {"Quantity": "Exhaust temperature", "Unit": "°C", "Value": iso.exhaust.t - 273.15},
            {"Quantity": "Specific power", "Unit": "kJ/kg", "Value": iso.electrical_power / 1e3 / d["air_flow_kg_s"]},
            {"Quantity": "Surge margin", "Unit": "%", "Value": 100 * iso.info["surge_margin"]},
        ]
    )
    stations = station_table(iso)
    key = [
        ("ISO output", f"{iso.electrical_power / 1e6:.2f} MW"),
        ("ISO heat rate", f"{iso.heat_rate:,.0f} kJ/kWh"),
        ("ISO thermal efficiency", f"{100 * iso.thermal_efficiency:.1f} %"),
        ("Exhaust temperature", f"{iso.exhaust.t - 273.15:.1f} °C"),
        ("Exhaust flow", f"{iso.exhaust.w:.2f} kg/s"),
        ("Specific power", f"{iso.electrical_power / 1e3 / d['air_flow_kg_s']:.0f} kJ/kg"),
        ("Surge margin at ISO", f"{100 * iso.info['surge_margin']:.1f} %"),
    ]
    gas_path = _gas_path_label(engine)
    fig, (ax1, ax2) = figure(10, 3.8, 2)
    s = stations[stations["Station"] != "0"]
    x = np.arange(len(s))
    ax1.plot(x, s["Temperature (°C)"], "o-", color=NAVY)
    ax1.set_xticks(x, s["Station"])
    style(ax1, f"Gas-path temperature at ISO\n{gas_path}", "Station", "Temperature (°C)")
    ax2.plot(x, s["Pressure (bar)"], "o-", color=BLUE)
    ax2.set_xticks(x, s["Station"])
    style(ax2, f"Gas-path pressure at ISO\n{gas_path}", "Station", "Pressure (bar)")
    return Section(
        "engine",
        "Engine design",
        inputs,
        key,
        {"ISO performance": perf, "ISO stations": stations},
        {"ISO gas path": fig},
        [
            "Design mode: the engine is calculated forwards from the values on the left. They start at the "
            "values that reproduce the published SGT-400 13 MW rating (12.9 MW, 10,294 kJ/kWh).",
            "The compressor map is a generic public map scaled to the pressure ratio, so off-design studies "
            "follow your design.",
        ],
    )


STATION_NAMES = {
    "0": "Ambient",
    "2": "Compressor inlet",
    "3": "Compressor exit",
    "31": "Combustor inlet",
    "4": "Combustor exit",
    "41": "HPT rotor inlet",
    "44": "HPT exit",
    "45": "PT inlet",
    "5": "Exhaust",
}


def _gas_path_label(engine):
    c = engine.cycle
    return (f"PR {c.compressor.pressure_ratio:.3g}, TIT {c.combustor.t_out - 273.15:.0f} °C, "
            f"air flow {engine.design.stations['2'].w:.4g} kg/s")


def station_table(result):
    rows = []
    for k, s in result.stations.items():
        rows.append(
            {
                "Station": k,
                "Name": STATION_NAMES.get(k, ""),
                "Temperature (°C)": s.t - 273.15,
                "Pressure (bar)": s.p / 1e5,
                "Mass flow (kg/s)": s.w,
            }
        )
    return pd.DataFrame(rows)


def engine_section(model):
    d, cal, engine = model.datasheet, model.calibration, model.engine
    iso = engine.run(ISO)
    a = d.get("assumptions", {})
    inputs = {
        "Engine": d.get("name", "engine"),
        "Electrical output (MW)": d["electrical_power_MW"],
        "Heat rate (kJ/kWh)": d["heat_rate_kJ_per_kWh"],
        "Exhaust flow (kg/s)": d["exhaust_flow_kg_s"],
        "Exhaust temperature, check only (°C)": d.get("exhaust_temperature_C"),
        "Pressure ratio": d["pressure_ratio"],
        "Compressor polytropic efficiency": a.get("compressor_polytropic_eff"),
        "Combustor pressure loss": a.get("combustor_dp"),
        "Generator efficiency": a.get("generator_efficiency"),
    }
    comp = pd.DataFrame(
        [
            {"Quantity": n, "Unit": u, "Datasheet": ref, "Model": m, "Error (%)": 100 * (m - ref) / ref}
            for n, u, ref, m in cal.comparison
        ]
    )
    stations = station_table(iso)
    key = [
        ("Fitted air flow", f"{cal.air_flow:.2f} kg/s"),
        ("Fitted TIT", f"{cal.tit - 273.15:.0f} °C"),
        ("Fitted turbine efficiency", f"{cal.turbine_efficiency:.4f}"),
        ("ISO output", f"{iso.electrical_power / 1e6:.2f} MW"),
        ("ISO heat rate", f"{iso.heat_rate:,.0f} kJ/kWh"),
        ("ISO thermal efficiency", f"{100 * iso.thermal_efficiency:.1f} %"),
        ("Surge margin at ISO", f"{100 * iso.info['surge_margin']:.1f} %"),
    ]
    gas_path = _gas_path_label(engine)
    fig, (ax1, ax2) = figure(10, 3.8, 2)
    s = stations[stations["Station"] != "0"]
    x = np.arange(len(s))
    ax1.plot(x, s["Temperature (°C)"], "o-", color=NAVY)
    ax1.set_xticks(x, s["Station"])
    style(ax1, f"Gas-path temperature at ISO\n{gas_path}", "Station", "Temperature (°C)")
    ax2.plot(x, s["Pressure (bar)"], "o-", color=BLUE)
    ax2.set_xticks(x, s["Station"])
    style(ax2, f"Gas-path pressure at ISO\n{gas_path}", "Station", "Pressure (bar)")
    return Section(
        "engine",
        "Engine and calibration",
        inputs,
        key,
        {"Calibration": comp, "ISO stations": stations},
        {"ISO gas path": fig},
        [
            "Air flow, TIT and turbine efficiency are fitted to the datasheet output, heat rate and exhaust "
            "flow. Exhaust temperature is not fitted: it is an independent check."
        ],
    )


# --- site performance ------------------------------------------------------------------------------


def site_section(model, site, health=NEW_AND_CLEAN, curve_range=(-20.0, 45.0), curve_outputs=None):
    engine = model.engine
    sp = site_performance(engine, site, health)
    summary = pd.DataFrame(
        [
            {"Parameter": n, "Unit": u, "ISO": a, "Site": b, "Change (%)": 100 * (b / a - 1)}
            for n, u, a, b in sp.summary_rows()
        ]
    )
    factors = pd.DataFrame([{"Effect": e, "Output factor": p, "Heat rate factor": h} for e, p, h in sp.factors])
    temps = np.arange(curve_range[0], curve_range[1] + 0.1, 2.5)
    cc = correction_curves(engine, temps)
    curves = pd.DataFrame(
        {
            "Ambient (°C)": cc["temperature_C"],
            "Output / ISO": cc["power"],
            "Heat rate / ISO": cc["heat_rate"],
            "Exhaust flow / ISO": cc["exhaust_flow"],
            "Exhaust temperature change (K)": cc["exhaust_temp_delta_K"],
            "TIT (°C)": cc["tit_C"],
            "Control": cc["control"],
            "Surge margin (%)": 100 * np.array(cc["surge_margin"]),
        }
    )
    r = sp.result
    inputs = {
        "Site": site.name,
        "Ambient temperature (°C)": site.temperature_C,
        "Relative humidity (%)": 100 * site.rh,
        "Altitude (m)": site.altitude_m,
        "Ambient pressure (mbar)": isa_pressure(site.altitude_m) / 100,
        "Inlet loss (mbar)": site.inlet_loss_mbar,
        "Exhaust loss (mbar)": site.exhaust_loss_mbar,
        "Compressor flow capacity (%)": 100 * (health.compressor_flow_scale - 1),
        "Compressor efficiency (pts)": 100 * health.compressor_eff_delta,
        "Turbine efficiency (pts)": 100 * health.turbine_eff_delta,
    }
    key = [
        (
            "Site output",
            f"{r.electrical_power / 1e6:.2f} MW ({100 * (r.electrical_power / sp.iso.electrical_power - 1):+.1f} %)",
        ),
        ("Site heat rate", f"{r.heat_rate:,.0f} kJ/kWh ({100 * (r.heat_rate / sp.iso.heat_rate - 1):+.1f} %)"),
        ("Exhaust temperature", f"{r.exhaust.t - 273.15:.1f} °C"),
        ("Exhaust flow", f"{r.exhaust.w:.2f} kg/s"),
        ("Control limit at site", r.info.get("control", "")),
        ("Surge margin", f"{100 * r.info.get('surge_margin', 0):.1f} %"),
    ]

    fig1, ax = figure(7.5, 4)
    names = [e for e, _, _ in sp.factors]
    vals = [100 * (p - 1) for _, p, _ in sp.factors]
    ax.barh(names[::-1], vals[::-1], color=[NAVY if v >= 0 else RED for v in vals[::-1]])
    ax.axvline(0, color=INK2, lw=0.8)
    for i, v in enumerate(vals[::-1]):
        ax.annotate(
            f"{v:+.2f} %",
            (max(v, 0), i),
            xytext=(4, 0),
            textcoords="offset points",
            ha="left",
            va="center",
            fontsize=8,
            color=INK2,
        )
    lo = min(min(vals), 0)
    ax.set_xlim(lo * 1.1 - 0.5, max(max(vals), 0) + max(2.5, 0.25 * abs(lo)))
    style(ax, f"What moves output from ISO to site\n{site.name}: {site.temperature_C:g} °C, {site.altitude_m:g} m, "
             f"{100 * site.rh:.0f} % RH", "Change in output (%)")
    ax.grid(axis="y", visible=False)

    outputs = curve_outputs or ["Output / ISO", "Heat rate / ISO", "Exhaust flow / ISO"]
    fig2, ax = figure(7.5, 4)
    for col, c in zip(outputs, SERIES):
        ax.plot(curves["Ambient (°C)"], curves[col], color=c, lw=2, label=col)
    limited = curves["Control"] != "temperature"
    for t in curves.loc[limited, "Ambient (°C)"]:
        ax.axvspan(t - 1.25, t + 1.25, color=AMBER, alpha=0.12, lw=0)
    ax.axvline(site.temperature_C, color=RED, lw=1, ls="--", label="site")
    style(ax, f"Correction curves, {curve_range[0]:g} to {curve_range[1]:g} °C (site at {site.temperature_C:g} °C)\n"
             "shaded: speed or generator limited", "Ambient temperature (°C)", "Value")
    legend(ax)
    return Section(
        "site",
        "Site performance",
        inputs,
        key,
        {"Site vs ISO": summary, "Correction factors": factors, "Correction curves": curves},
        {"Correction factors": fig1, "Correction curves": fig2},
        ["Correction factors are applied one effect at a time, so they multiply exactly to the site value."],
    )


# --- part load and emissions --------------------------------------------------------------------------


def part_load_section(model, t_amb_C=15.0, rh=0.6, loads=None, staging=True, nox_limit=50.0, co_limit=100.0):
    from ..cli import _fuel
    from ..emissions import MCPD_LOAD_THRESHOLD, DLECombustor, compliance_table, lowest_compliant_load
    from ..fuel import natural_gas

    engine = model.engine
    loads = loads if loads is not None else [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3]
    amb = Ambient(t_amb_C + 273.15, ISO.p, rh)
    fuel = _fuel(model.datasheet.get("fuel")) or natural_gas()
    comb = DLECombustor(fuel, staging=staging).calibrate(engine.run(ISO), 10.0, 5.0)
    rows = compliance_table(engine, comb, loads, ambient=amb, nox_limit=nox_limit, co_limit=co_limit)
    perf = []
    for load in loads:
        r = engine.run(amb, load=load)
        perf.append(
            {
                "Load (%)": 100 * load,
                "Output (MW)": r.electrical_power / 1e6,
                "Heat rate (kJ/kWh)": r.heat_rate,
                "Efficiency (%)": 100 * r.thermal_efficiency,
                "TIT (°C)": r.info["tit"] - 273.15,
                "Exhaust temperature (°C)": r.exhaust.t - 273.15,
                "Exhaust flow (kg/s)": r.exhaust.w,
                "Gas generator speed (%)": 100 * r.info["mechanical_speed"],
                "VIGV (deg)": r.info["vigv"],
                "Control": r.info["control"],
            }
        )
    perf = pd.DataFrame(perf)
    em = pd.DataFrame(
        [
            {
                "Load (%)": 100 * x["load"],
                "Flame temperature (K)": x["flame_temperature_K"],
                "Premix air fraction": x["primary_fraction"],
                "NOx (ppmvd @15 % O2)": x["nox_ppm_15"],
                "NOx (mg/Nm³)": x["nox_mg_15"],
                "CO (mg/Nm³)": x["co_mg_15"],
                "NOx (kg/h)": x["nox_kg_h"],
                "Compliant": "yes" if x["compliant"] else "no",
            }
            for x in rows
        ]
    )
    lowest = lowest_compliant_load(rows)
    inputs = {
        "Ambient temperature (°C)": t_amb_C,
        "Relative humidity (%)": 100 * rh,
        "Loads (%)": ", ".join(f"{100 * x:.0f}" for x in loads),
        "Air staging": "on" if staging else "off",
        "NOx limit (mg/Nm³ @15 % O2)": nox_limit,
        "CO limit (mg/Nm³)": co_limit,
    }
    key = [
        ("Base-load output", f"{perf['Output (MW)'].iloc[0]:.2f} MW"),
        ("Base-load NOx", f"{em['NOx (mg/Nm³)'].iloc[0]:.1f} mg/Nm³ against {nox_limit:g}"),
        ("Emissions-compliant down to", f"{100 * lowest:.0f} % load" if lowest else "not compliant at the top load"),
        ("Heat rate at lowest load", f"{perf['Heat rate (kJ/kWh)'].iloc[-1]:,.0f} kJ/kWh"),
    ]
    fig1, (a1, a2) = figure(10, 3.8, 2)
    a1.plot(perf["Load (%)"], perf["Heat rate (kJ/kWh)"], "o-", color=NAVY)
    conditions = f"{t_amb_C:g} °C, {100 * rh:.0f} % RH"
    style(a1, f"Heat rate against load\n{conditions}", "Load (%)", "Heat rate (kJ/kWh)")
    a2.plot(perf["Load (%)"], perf["TIT (°C)"], "o-", color=RED, label="TIT")
    a2.plot(perf["Load (%)"], perf["Exhaust temperature (°C)"], "o-", color=BLUE, label="Exhaust")
    style(a2, f"Temperatures against load\n{conditions}", "Load (%)", "Temperature (°C)")
    legend(a2)
    fig2, ax = figure(7.5, 4)
    ax.semilogy(em["Load (%)"], em["NOx (mg/Nm³)"], "o-", color=NAVY, label="NOx")
    ax.semilogy(em["Load (%)"], em["CO (mg/Nm³)"], "s-", color=AMBER, label="CO")
    ax.axhline(nox_limit, color=NAVY, ls="--", lw=1, label=f"NOx limit {nox_limit:g}")
    ax.axhline(co_limit, color=AMBER, ls="--", lw=1, label=f"CO limit {co_limit:g}")
    ax.axvspan(0, 100 * MCPD_LOAD_THRESHOLD, color=GRID, alpha=0.4, lw=0)
    ax.set_xlim(min(em["Load (%)"]) - 3, 103)
    style(ax, f"Emissions against load, {conditions}, air staging {'on' if staging else 'off'}\n"
             f"limits NOx {nox_limit:g}, CO {co_limit:g} mg/Nm³ (grey: NOx limit not applied)",
          "Load (%)", "mg/Nm³ dry at 15 % O2")
    legend(ax)
    return Section(
        "partload",
        "Part load and emissions",
        inputs,
        key,
        {"Part-load performance": perf, "Emissions": em},
        {"Part-load performance": fig1, "Emissions against load": fig2},
        [
            "DLE combustor calibrated to 10 ppmvd NOx and 5 ppmvd CO at ISO base load. The MCPD NOx limit "
            "for new gas turbines applies above 70 % load."
        ],
    )


# --- pass-off test -------------------------------------------------------------------------------------

FAULT_CHOICES = {
    "Exhaust thermocouple 7 drifts +15 K": ("T_exh_07", "drift", 15.0),
    "Exhaust thermocouple 3 reads 30 K high": ("T_exh_03", "offset", 30.0),
    "Inlet RTD 3 stuck": ("T_amb_3", "stuck", 0.0),
    "Power meter spikes": ("P_elec", "spike", 300.0),
    "Fuel flow meter reads 1 % high": ("W_fuel", "offset", 0.01),
}


def passoff_section(
    model,
    t_amb_C=8.0,
    p_amb_mbar=1002.0,
    rh=0.78,
    n_points=2,
    faults=(),
    power_min=12.65,
    heat_rate_max=10500.0,
    exhaust_max=570.0,
    compressor_flow_pct=-0.6,
    compressor_eff_pts=-0.4,
    seed=4,
    duration_s=300,
    mc_trials=500,
):
    from ..passoff import Guarantee, analyse
    from ..synthetic import Fault, TestPoint, generate_test

    engine = model.engine
    points = [TestPoint(t_amb_C + 0.5 * i, p_amb_mbar + 0.2 * i, rh - 0.02 * i, drift_K=0.3) for i in range(n_points)]
    flts = [Fault(*FAULT_CHOICES[f]) for f in faults]
    health = Health(compressor_flow_scale=1 + compressor_flow_pct / 100, compressor_eff_delta=compressor_eff_pts / 100)
    test = generate_test(engine, points, health=health, faults=flts, seed=seed, duration_s=duration_s, serial="ESN-GUI")
    guarantee = Guarantee(power_min, heat_rate_max, exhaust_max)
    res = analyse(
        test.data,
        engine,
        test.metadata["instruments"],
        guarantee,
        mc_trials=mc_trials,
        test_date=test.metadata["test_date"],
    )
    unc = res.uncertainty
    names = {
        "power_MW": "Corrected power (MW)",
        "heat_rate": "Corrected heat rate (kJ/kWh)",
        "exhaust_temp_C": "Corrected exhaust temperature (°C)",
        "exhaust_flow": "Corrected exhaust flow (kg/s)",
    }
    results = pd.DataFrame(
        [
            {
                "Result": names[k],
                "Value": unc.value[k],
                "U95": unc.expanded(k),
                "Hidden truth": test.truth[k],
                "Error / U95": (unc.value[k] - test.truth[k]) / unc.expanded(k),
            }
            for k in names
        ]
    )
    checks = pd.DataFrame(
        [
            {
                "Result": names[c["result"]],
                "Value": c["value"],
                "Limit": f"{c['direction']} {c['limit']:g}",
                "Margin": c["margin"],
                "U95": c["U95"],
                "Probability of conformance (%)": 100 * c["probability"],
                "Verdict": c["verdict"],
            }
            for c in res.checks
        ]
    )
    excluded = pd.DataFrame(
        [{"Sensor": k, "Reason": v} for k, v in res.report.excluded.items()] or [{"Sensor": "none", "Reason": ""}]
    )
    budget = pd.DataFrame(
        [{"Input": b[0], "Contribution": b[3], "Share (%)": 100 * b[4]} for b in unc.budget["power_MW"]]
    ).sort_values("Share (%)", ascending=False)
    verdicts = [c["verdict"] for c in res.checks]
    overall = "FAIL" if any(v.startswith("FAIL") for v in verdicts) else "PASS"
    if res.warnings:
        overall = "NOT VALID"
    inputs = {
        "Ambient temperature (°C)": t_amb_C,
        "Ambient pressure (mbar)": p_amb_mbar,
        "Relative humidity (%)": 100 * rh,
        "Test points": n_points,
        "Injected sensor faults": ", ".join(faults) or "none",
        "Hidden compressor flow change (%)": compressor_flow_pct,
        "Hidden compressor efficiency change (pts)": compressor_eff_pts,
        "Guarantee: output at least (MW)": power_min,
        "Guarantee: heat rate at most (kJ/kWh)": heat_rate_max,
        "Guarantee: exhaust temperature at most (°C)": exhaust_max,
        "Random seed": seed,
    }
    key = [("Verdict", overall)] + [
        (names[c["result"]], f"{c['value']:,.3f} ± {c['U95']:,.3f}  {c['verdict']}") for c in res.checks
    ]
    key.append(("Sensors excluded by validation", str(len(res.report.excluded))))
    top = budget.head(8)
    fig1, ax = figure(7.5, 4)
    ax.barh(top["Input"][::-1], top["Share (%)"][::-1], color=NAVY)
    test_day = (f"{n_points} test point{'s' if n_points != 1 else ''} at {t_amb_C:g} °C, {p_amb_mbar:g} mbar, "
                f"{100 * rh:.0f} % RH")
    style(ax, f"Uncertainty budget: corrected power\n{test_day}", "Share of variance (%)")
    ax.grid(axis="y", visible=False)
    fig2, ax = figure(7.5, 4)
    y = np.arange(len(results))
    ax.errorbar(results["Error / U95"], y, xerr=1.0, fmt="o", color=NAVY, ecolor=BLUE, capsize=4)
    ax.axvline(0, color=INK2, lw=0.8)
    ax.set_yticks(y, results["Result"])
    ax.set_xlim(-2.5, 2.5)
    style(ax, f"Corrected result against hidden truth (bar = U95)\nhidden health: flow {compressor_flow_pct:+g} %, "
             f"efficiency {compressor_eff_pts:+g} points", "Error / U95")
    tags = [c for c in test.data.columns if c.startswith("T_exh_")]
    fig3, ax = figure(7.5, 4)
    for i, tag in enumerate(tags):
        excluded_tag = tag in res.report.excluded
        ax.plot(
            test.data["time_s"],
            test.data[tag],
            lw=1.4 if excluded_tag else 0.6,
            color=RED if excluded_tag else SERIES[i % 3],
            alpha=1 if excluded_tag else 0.6,
        )
    style(ax, f"Exhaust thermocouples (red: excluded by validation)\n{test_day}, {len(faults)} sensor faults injected",
          "Time (s)", "Temperature (°C)")
    warnings = list(res.warnings) + list(res.notes)
    return Section(
        "passoff",
        "Pass-off test",
        inputs,
        key,
        {
            "Acceptance": checks,
            "Results vs truth": results,
            "Power uncertainty budget": budget,
            "Excluded sensors": excluded,
            "Test log (1 Hz)": test.data,
        },
        {"Uncertainty budget": fig1, "Results vs truth": fig2, "Exhaust thermocouples": fig3},
        warnings
        + [
            "Synthetic test: the engine's health and every instrument error are hidden random "
            "draws, so the corrected results can be checked against the truth."
        ],
    )


# --- fleet monitoring ----------------------------------------------------------------------------------

FLEET_FAULT_TYPES = {
    "HP turbine blade damage": ("engine", "turbine_eff", -1.5, 0),
    "HP turbine nozzle erosion": ("engine", "turbine_capacity", 2.0, 0),
    "Inlet filter failure": ("engine", "compressor_eff", -2.0, 40),
    "Exhaust thermocouple drift": ("sensor", "sensor:egt", 12.0, 60),
    "Power meter error": ("sensor", "sensor:power", -0.8, 0),
    "Fuel meter error": ("sensor", "sensor:fuel_flow", 1.0, 0),
}


def default_fleet_faults(n_engines, days):
    names = list(FLEET_FAULT_TYPES)[:5]
    return pd.DataFrame(
        [
            {"Engine": 1 + (2 * i) % n_engines, "Day": int(days * (0.4 + 0.1 * i)), "Fault": n}
            for i, n in enumerate(names)
        ]
    )


def fleet_section(model, n_engines=6, days=365, interval=3, dispatch="base", faults=None, seed=11, engine_to_plot=None):
    from ..fleet import FleetFault, in_service_fleet, monitor_fleet, score

    engine = model.engine
    faults = default_fleet_faults(n_engines, days) if faults is None else faults
    ff = []
    for _, row in faults.iterrows():
        kind, target, mag, ramp = FLEET_FAULT_TYPES[row["Fault"]]
        ff.append(FleetFault(int(row["Engine"]), int(row["Day"]), kind, target, row["Fault"], mag, ramp))
    data, washes, ff = in_service_fleet(
        engine,
        n_engines=n_engines,
        days=days,
        interval=interval,
        faults=ff,
        seed=seed,
        dispatch="cycling" if dispatch == "cycling" else None,
    )
    m = monitor_fleet(engine, data, washes)
    sc, false, rate = score(m.alarms, ff, n_engines, days)
    alarms = pd.DataFrame(
        [
            {
                "Engine": a.engine,
                "Day": a.day,
                "Source": a.source,
                "Statistic": a.statistic,
                "Most likely cause": a.classification[0][0] if a.classification else "",
                "Match": a.classification[0][1] if a.classification else np.nan,
            }
            for a in m.alarms
        ]
        or [
            {
                "Engine": None,
                "Day": None,
                "Source": "no alarms",
                "Statistic": None,
                "Most likely cause": "",
                "Match": None,
            }
        ]
    )
    scored = sc.rename(
        columns={
            "engine": "Engine",
            "fault": "Fault",
            "true": "True cause",
            "detected_day": "Detected day",
            "delay_days": "Delay (days)",
            "isolated_as": "Isolated as",
            "correct": "Correct",
        }
    )
    e_plot = engine_to_plot or (int(faults["Engine"].iloc[0]) if len(faults) else 1)
    sel = data["engine"] == e_plot
    dz = m.dz.loc[m.dz.index.intersection(data.index[sel])]
    days_sel = data.loc[dz.index, "day"]
    fig, axes = plt.subplots(3, 1, figsize=(8, 6.5), sharex=True, constrained_layout=True)
    fig.patch.set_facecolor("white")
    for ax, col, label, c in zip(
        axes,
        ["d_power", "d_egt", "d_fuel_flow"],
        ["Output delta (%)", "EGT delta (K)", "Fuel flow delta (%)"],
        [NAVY, RED, BLUE],
    ):
        ax.plot(days_sel, dz[col], ".", ms=3, color=c, alpha=0.6)
        ax.plot(days_sel, dz[col].rolling(10, min_periods=1).mean(), color=c, lw=1.5)
        for w in washes.loc[washes["engine"] == e_plot, "day"]:
            ax.axvline(w, color=GREEN, lw=0.8, ls=":")
        for f in ff:
            if f.engine == e_plot:
                ax.axvline(f.day, color=AMBER, lw=1.2)
        for a in m.alarms:
            if a.engine == e_plot:
                ax.axvline(a.day, color=RED, lw=1.2, ls="--")
        style(ax, ylabel=label)
    axes[0].set_title(
        f"Engine {e_plot} of {n_engines}: deltas against the baseline, {days} days, "
        f"{'base load' if dispatch == 'base' else 'cycling'}\n(green: wash, amber: fault, red: alarm)",
        fontsize=11,
    )
    axes[-1].set_xlabel("Day", color=INK2)
    detected = int(sc["detected_day"].notna().sum())
    correct = int(sc["correct"].sum())
    inputs = {
        "Engines": n_engines,
        "Days": days,
        "Snapshot interval (days)": interval,
        "Dispatch": "cycling (part load)" if dispatch == "cycling" else "base load",
        "Random seed": seed,
        "Injected faults": "; ".join(f"engine {r.Engine} day {r.Day}: {r.Fault}" for r in faults.itertuples()),
    }
    key = [
        ("Faults detected", f"{detected} of {len(ff)}"),
        ("Correctly isolated", f"{correct} of {len(ff)}"),
        ("False alarms", f"{len(false)} ({rate:.2f} per engine-year)"),
        ("Snapshots", f"{len(data):,}"),
    ]
    return Section(
        "fleet",
        "Fleet monitoring",
        inputs,
        key,
        {"Fault detection": scored, "Alarms": alarms, "Fleet data": data},
        {"Engine trends": fig},
        ["Synthetic fleet: fouling, washes, wear and the faults above are injected."],
    )


# --- turbofan --------------------------------------------------------------------------------------------


def turbofan_section(
    bpr=10.0,
    fan_pr=1.5,
    fan_core_pr=2.4,
    hpc_pr=17.0,
    tet_cruise=1600.0,
    thrust_kN=22.0,
    altitude_m=10668.0,
    mach=0.78,
    take_off_tet=1750.0,
    offdesign=True,
    environment=True,
):
    from ..aero import Flight, Turbofan

    cruise = Flight(altitude_m, mach)
    cycle = Turbofan(bpr=bpr, fan_pr=fan_pr, fan_core_pr=fan_core_pr, hpc_pr=hpc_pr, tet=tet_cruise, hp_offtake=150e3)
    w = cycle.size_for_thrust(thrust_kN * 1e3, cruise)
    r = cycle.run(w, cruise)
    stations = pd.DataFrame(
        [
            {"Station": k, "Temperature (K)": s.t, "Pressure (bar)": s.p / 1e5, "Mass flow (kg/s)": s.w}
            for k, s in r.stations.items()
        ]
    )
    design = pd.DataFrame(
        [
            {"Quantity": "Net thrust", "Value": r.net_thrust / 1e3, "Unit": "kN"},
            {"Quantity": "TSFC", "Value": r.tsfc * 1e6, "Unit": "mg/(N s)"},
            {"Quantity": "Intake flow", "Value": r.air_flow, "Unit": "kg/s"},
            {"Quantity": "Specific thrust", "Value": r.specific_thrust, "Unit": "m/s"},
            {"Quantity": "Thermal efficiency", "Value": 100 * r.thermal_efficiency, "Unit": "%"},
            {"Quantity": "Propulsive efficiency", "Value": 100 * r.propulsive_efficiency, "Unit": "%"},
            {"Quantity": "Overall efficiency", "Value": 100 * r.overall_efficiency, "Unit": "%"},
            {"Quantity": "Core jet velocity", "Value": r.core_jet.v, "Unit": "m/s"},
            {"Quantity": "Bypass jet velocity", "Value": r.bypass_jet.v, "Unit": "m/s"},
        ]
    )
    inputs = {
        "Bypass ratio": bpr,
        "Fan pressure ratio (bypass)": fan_pr,
        "Fan root + booster pressure ratio": fan_core_pr,
        "HPC pressure ratio": hpc_pr,
        "Cruise TET (K)": tet_cruise,
        "Cruise thrust (kN)": thrust_kN,
        "Altitude (m)": altitude_m,
        "Mach number": mach,
        "Take-off TET (K)": take_off_tet,
    }
    key = [
        ("Cruise TSFC", f"{r.tsfc * 1e6:.2f} mg/(N s)"),
        ("Intake flow", f"{r.air_flow:.1f} kg/s"),
        ("Overall efficiency", f"{100 * r.overall_efficiency:.1f} %"),
    ]
    tables = {"Design point": design, "Stations": stations}
    figures = {}
    notes = []
    if offdesign:
        from ..aero_offdesign import MatchError, TurbofanEngine

        engine = TurbofanEngine(cycle, w, cruise)
        sls = Flight()
        take_off = engine.run(sls, tet=take_off_tet)
        key.append(("Take-off thrust (sea level static)", f"{take_off.net_thrust / 1e3:.1f} kN"))
        alts = np.linspace(0.0, 12000.0, 9)
        lapse = []
        fig, ax = figure(7.5, 4)
        for m_, c in zip([0.2, 0.4, 0.6, 0.8], ["#c3daf7", "#86b6ef", BLUE, NAVY]):
            guess, th = None, []
            for h in alts:
                try:
                    rr = engine.run(Flight(h, m_), tet=take_off_tet, guess=guess)
                except MatchError:  # this TET cannot hold a match here: leave a gap in the curve
                    guess = None
                    th.append(np.nan)
                    lapse.append({"Altitude (m)": h, "Mach": m_, "Net thrust (kN)": np.nan, "TSFC (mg/N/s)": np.nan})
                    continue
                guess = rr.info["x"]
                th.append(rr.net_thrust / 1e3)
                lapse.append(
                    {
                        "Altitude (m)": h,
                        "Mach": m_,
                        "Net thrust (kN)": rr.net_thrust / 1e3,
                        "TSFC (mg/N/s)": rr.tsfc * 1e6,
                    }
                )
            ax.plot(alts / 1e3, th, color=c, lw=2, label=f"Mach {m_}")
        style(ax, f"Thrust lapse at TET {take_off_tet:.0f} K\nBPR {bpr:g}, FPR {fan_pr:g}, HPC PR {hpc_pr:g}, "
                 f"cruise TET {tet_cruise:.0f} K",
              "Altitude (km)", "Net thrust (kN)")
        legend(ax)
        figures["Thrust lapse"] = fig
        tables["Thrust lapse"] = pd.DataFrame(lapse)
        if environment:
            from ..aero_environment import lto_cycle, noise_sources

            try:
                lto = lto_cycle(engine, take_off_tet)
            except MatchError as e:
                lto = None
                notes.append(f"LTO NOx was not calculated: the engine has no match at these values ({e}).")
        if environment and lto is not None:
            tables["LTO NOx"] = pd.DataFrame(lto["modes"]).rename(
                columns={
                    "mode": "Mode",
                    "thrust_kN": "Thrust (kN)",
                    "fuel_kg_s": "Fuel flow (kg/s)",
                    "t3_K": "T3 (K)",
                    "p3_bar": "P3 (bar)",
                    "ei_nox": "EI NOx (g/kg)",
                    "fuel_kg": "Fuel (kg)",
                    "nox_g": "NOx (g)",
                }
            )
            key += [
                ("LTO NOx Dp/Foo", f"{lto['dp_foo']:.1f} g/kN (CAEP/8 limit {lto['tier8']:.1f})"),
                ("LTO fuel / CO2", f"{lto['fuel_kg']:.0f} kg / {lto['co2_kg']:.0f} kg"),
            ]
        if environment:
            try:
                approach = engine.run(sls, thrust=0.3 * take_off.net_thrust)
            except MatchError as e:
                approach = None
                notes.append(f"Noise sources were not calculated: no approach match at these values ({e}).")
        if environment and approach is not None:
            n_to, n_ap = noise_sources(engine, take_off), noise_sources(engine, approach)
            src = ["fan_inlet_broadband", "fan_discharge_broadband", "core_jet", "bypass_jet", "total"]
            noise = pd.DataFrame(
                [
                    {"Source": s.replace("_", " "), "Take-off (dB at 1 m)": n_to[s], "Approach (dB at 1 m)": n_ap[s]}
                    for s in src
                ]
            )
            tables["Noise sources"] = noise
            fig, ax = figure(7.5, 4)
            x = np.arange(len(src) - 1)
            ax.bar(x - 0.2, noise["Take-off (dB at 1 m)"][:-1], 0.4, color=NAVY, label="Take-off")
            ax.bar(x + 0.2, noise["Approach (dB at 1 m)"][:-1], 0.4, color=BLUE, label="Approach")
            ax.set_xticks(x, [s.replace(" broadband", "") for s in noise["Source"][:-1]])
            ax.set_ylim(90, max(noise["Take-off (dB at 1 m)"]) + 5)
            style(ax, f"Noise source levels, take-off TET {take_off_tet:.0f} K\nBPR {bpr:g}, FPR {fan_pr:g}, "
                     f"cruise TET {tet_cruise:.0f} K",
                  None, "dB re 20 µPa at 1 m")
            legend(ax)
            figures["Noise sources"] = fig
            notes.append(
                "NOx emissions index at take-off is an illustrative 30 g/kg; noise is source levels, "
                "not certification EPNdB."
            )
    return Section("turbofan", "Turbofan", inputs, key, tables, figures, notes)
