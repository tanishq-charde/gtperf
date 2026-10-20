"""Calculations behind each GUI section, independent of the user interface.

Every study returns a `Section`: what was asked (inputs), the headline numbers (key results), tables as
DataFrames and figures as matplotlib Figures. The exporters and the GUI only read sections, so a study added
here appears in every download format without further work.
"""

from __future__ import annotations

import copy
import math
import tomllib
from dataclasses import dataclass, field, replace
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
MUTED = "#8a8679"
POINT_COLOURS = (AMBER, GREEN, "#2a5fa8", RED, "#7a4fa3", "#3a8a8a", "#a0522d")
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


SUPERSCRIPT = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")


def sci(x, digits=3):
    """A small number in scientific notation for display, e.g. 1.527 × 10⁻⁵."""
    m, e = f"{x:.{digits}e}".split("e")
    return f"{m} × 10{str(int(e)).translate(SUPERSCRIPT)}"


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
    # stage design, from which the compressor and turbine maps are calculated
    "comp_stages": 11,
    "comp_loading": 0.40,
    "comp_flow_coeff": 0.50,
    "comp_reaction": 0.50,
    "comp_solidity": 1.5,
    "comp_df_limit": 0.60,
    "vane_rows": 3,
    "vane_closed": -35.0,
    "vane_open_speed": 0.95,
    "vane_closed_speed": 0.85,
    "hpt_stages": 2,
    "hpt_loading": 1.5,
    "hpt_flow_coeff": 0.60,
    "hpt_reaction": 0.50,
    "pt_stages": 2,
    "pt_loading": 1.3,
    "pt_flow_coeff": 0.60,
    "pt_reaction": 0.50,
}


def compressor_stages(d):
    from ..mapgen import CompressorStages

    return CompressorStages(int(d["comp_stages"]), d["comp_loading"], d["comp_flow_coeff"], d["comp_reaction"],
                            d["comp_solidity"], d["comp_df_limit"], variable_rows=int(d["vane_rows"]),
                            vane_closed=d["vane_closed"], vane_open_speed=d["vane_open_speed"],
                            vane_closed_speed=d["vane_closed_speed"])


def _check_physical(d):
    """Inputs have no limits, but some values cannot describe any engine; say which in plain words."""
    for key, label in (("compressor_polytropic_eff", "Compressor polytropic efficiency"),
                       ("turbine_eff", "Turbine efficiency"), ("mechanical_efficiency", "Mechanical efficiency"),
                       ("generator_efficiency", "Generator efficiency")):
        if not 0 < d[key] < 1:
            raise ValueError(f"{label} is {d[key]:g}: an efficiency has to lie between 0 and 1")
    if d["vane_open_speed"] > 1:
        raise ValueError(f"The vanes are fully open only above {d['vane_open_speed']:g} of design speed, so they "
                         "would be part closed at the design point: set that speed to 1.0 or less")
    if d["vane_rows"] and d["vane_closed_speed"] >= d["vane_open_speed"]:
        raise ValueError("The vanes' fully closed speed has to be below their fully open speed")


def design_engine(design=None):
    """An EngineModel built forwards from design values (missing keys take DESIGN_DEFAULTS)."""
    from ..components import Combustor, Compressor, Turbine
    from ..cycle import Cooling, TwinShaftCycle
    from ..fuel import natural_gas
    from ..offdesign import Generator, Limits, TwinShaftEngine

    d = {**DESIGN_DEFAULTS, **(design or {})}
    _check_physical(d)
    cycle = TwinShaftCycle(
        Compressor(d["pressure_ratio"], d["compressor_polytropic_eff"]),
        Combustor(natural_gas(), d["tit_C"] + 273.15, dp_frac=d["combustor_dp"]),
        Turbine(d["turbine_eff"]),
        Turbine(d["turbine_eff"]),
        cooling=Cooling(ngv=d["ngv"], rotor=d["rotor"], pt=d["pt"], port=d["port"]),
        mechanical_efficiency=d["mechanical_efficiency"],
        generator_efficiency=d["generator_efficiency"],
    )
    from ..mapgen import TurbineStages, compressor_map, turbine_map

    sgt = load_datasheet()  # the handling bleed schedule and generator derate are generic; the rest is `d`
    _, limits, controls = offdesign_options(sgt.get("offdesign"), d["pressure_ratio"])
    cmap = compressor_map(d["pressure_ratio"], d["compressor_polytropic_eff"], compressor_stages(d))
    limits = Limits(
        gg_speed=d["gg_speed_limit"],
        # the engine cannot run faster (corrected) than the top speed line the compressor map reaches
        gg_corrected_speed=min(d["gg_speed_limit"], float(cmap.nc[-1]) / cmap.design_nc),
        generator=Generator(d["generator_MVA"], d["power_factor"], limits.generator.derate),
    )
    controls = replace(controls, vigv=None)  # the vane schedule is part of the calculated map
    engine = TwinShaftEngine(cycle, d["air_flow_kg_s"], compressor_map=cmap, limits=limits, controls=controls)
    st = engine.design.stations
    for key, s_in, s_out, eff in (("hpt", st["41"], st["44"], engine.hpt_eff_design),
                                  ("pt", st["45"], st["5"], engine.pt_eff_design)):
        stages = TurbineStages(int(d[f"{key}_stages"]), d[f"{key}_loading"], d[f"{key}_flow_coeff"],
                               d[f"{key}_reaction"])
        setattr(engine, f"{key}_map", turbine_map(s_in.p / s_out.p, eff, stages, s_in.t))
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
        "comp_stages": "Compressor stages",
        "comp_loading": "Compressor stage loading",
        "comp_flow_coeff": "Compressor flow coefficient",
        "comp_reaction": "Compressor stage reaction",
        "comp_solidity": "Compressor blade solidity",
        "comp_df_limit": "Compressor diffusion factor at stall",
        "vane_rows": "Variable vane rows",
        "vane_closed": "VIGV angle fully closed (°)",
        "vane_open_speed": "Vanes fully open above corrected speed",
        "vane_closed_speed": "Vanes fully closed below corrected speed",
        "hpt_stages": "HP turbine stages",
        "hpt_loading": "HP turbine stage loading",
        "hpt_flow_coeff": "HP turbine flow coefficient",
        "hpt_reaction": "HP turbine stage reaction",
        "pt_stages": "Power turbine stages",
        "pt_loading": "Power turbine stage loading",
        "pt_flow_coeff": "Power turbine flow coefficient",
        "pt_reaction": "Power turbine stage reaction",
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
    figures = {"ISO gas path": fig}
    tables = {"ISO performance": perf, "ISO stations": stations}
    notes = [
        "Design mode: the engine is calculated forwards from the values on the left. They start at the "
        "values that reproduce the published SGT-400 13 MW rating (12.9 MW, 10,294 kJ/kWh).",
    ]
    if getattr(engine.compressor_map, "info", None):
        figures.update(_engine_maps(engine, d, iso, notes))
        tables["Calculated compressor map"] = _map_table(engine.compressor_map)
    return Section("engine", "Engine design", inputs, key, tables, figures, notes)


def _map_table(cmap):
    return pd.DataFrame(
        [
            {"Corrected speed (%)": 100 * n, "Surge flow (relative)": w_s, "Surge pressure ratio": pr_s,
             "Choke-end flow (relative)": w_e, "Blade row in rotating stall": "yes" if st else "no"}
            for n, w_s, pr_s, w_e, st in cmap.info["surge"]
        ]
    )


def _turbine_point(engine, result):
    """Corrected speeds and pressure ratios of the HP and power turbines at an operating point."""
    from ..offdesign import _sonic_temperature

    st = result.stations
    s41, s44, s45 = st["41"], st["44"], st["45"]
    pt_out = st.get("49", st["5"])
    n_hpt = result.info["mechanical_speed"] / math.sqrt(_sonic_temperature(s41) / engine.a41_design)
    n_pt = 1.0 / math.sqrt(_sonic_temperature(s45) / engine.a45_design)
    return (n_hpt, s41.p / s44.p), (n_pt, s45.p / pt_out.p)


def _engine_maps(engine, d, iso, notes):
    """Compressor, HP turbine and power turbine maps calculated from the design, with base-load points
    from -20 to 45 °C and the ISO part-load line."""
    cmap = engine.compressor_map
    ambient_runs, load_runs = [], []
    for t in (-20, -10, 0, 15, 30, 45):
        try:
            ambient_runs.append((t, engine.run(Ambient(t + 273.15, ISO.p, 0.6))))
        except RuntimeError:
            pass
    for load in (0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3):
        try:
            load_runs.append((load, engine.run(ISO, load=load)))
        except RuntimeError:
            pass
    c = d
    title = (f"Compressor map from stage stacking\nPR {c['pressure_ratio']:g}, {int(c['comp_stages'])} stages, "
             f"loading {c['comp_loading']:g}, flow coefficient {c['comp_flow_coeff']:g}, "
             f"{int(c['vane_rows'])} variable rows")
    pts = [(f"Base load {t} °C", r.info["corrected_speed"], r.info["beta"], "o") for t, r in ambient_runs
           if t in (-20, 15, 45)]
    fig_c = compressor_map_figure(cmap, title, pts,
                                  line=[(r.info["corrected_speed"], r.info["beta"]) for _, r in load_runs[::-1]]
                                  + [(iso.info["corrected_speed"], iso.info["beta"])])
    hpt_pts, pt_pts = [], []
    for label, r, marker in [("ISO base load", iso, "o")] + [(f"{t} °C", r, "s") for t, r in ambient_runs
                                                                if t in (-20, 45)] + \
            [(f"{int(100 * f)} % load", r, "^") for f, r in load_runs if f in (0.6, 0.3)]:
        (nh, ph), (np_, pp) = _turbine_point(engine, r)
        hpt_pts.append((label, nh, ph, marker))
        pt_pts.append((label, np_, pp, marker))
    fig_h = turbine_map_figure(engine.hpt_map, f"HP turbine ({int(c['hpt_stages'])} stages, loading "
                                                f"{c['hpt_loading']:g})", hpt_pts)
    fig_p = turbine_map_figure(engine.pt_map, f"Power turbine ({int(c['pt_stages'])} stages, loading "
                                               f"{c['pt_loading']:g})", pt_pts)
    stalled = [r[0] for r in cmap.info["surge"] if r[4]]
    notes.append(
        "The compressor and turbine maps are calculated from your design by stage stacking, not scaled from "
        "a stored map: each stage's velocity triangles, losses and blade-row stall are worked out from the "
        "stage design values, with the variable vanes on their schedule. The design point sits on the 100 % "
        f"speed line with {100 * iso.info['surge_margin']:.1f} % surge margin."
    )
    if stalled:
        notes.append(
            f"Below {100 * (max(stalled) + 0.05):.0f} % corrected speed a blade row is past its diffusion limit "
            "along the whole speed line (dotted lines on the map): rotating stall territory, which more variable "
            "stator rows or a lower stage loading would clear."
        )
    return {"Compressor map": fig_c, "HP turbine map": fig_h, "Power turbine map": fig_p}


def compressor_map_figure(cmap, title, points=(), line=None):
    """Speed lines, surge line and efficiency contours of a calculated compressor map, with operating points
    [(label, n, beta, marker)] and an optional operating line [(n, beta), ...]."""
    fig, ax = figure(7.5, 4.8)
    beta = np.asarray(cmap.beta)
    on = beta >= 1.0
    wc, pr, eff = (np.asarray(a)[:, on] for a in (cmap.wc, cmap.pr, cmap.eff))
    speeds = np.asarray(cmap.nc)
    lowest = min([0.5] + [n - 0.05 for _, n, _, _ in points] + [n - 0.05 for n, _ in (line or [])])
    show = speeds >= lowest - 1e-9
    surge = {round(r[0], 3): r for r in cmap.info["surge"]}
    levels = [lv for lv in np.arange(0.60, 0.95, 0.02) if eff[show].min() < lv < eff[show].max()]
    if levels:  # contours on the map's own (speed, R-line) grid
        cs = ax.contour(wc[show], pr[show], eff[show], levels=levels, colors=[MUTED], linewidths=0.6)
        ax.clabel(cs, fmt=lambda v: f"{100 * v:.0f} %", fontsize=6)
    for i in np.nonzero(show)[0]:
        stalled = surge.get(round(speeds[i], 3), (0, 0, 0, 0, False))[4]
        ax.plot(wc[i], pr[i], color=NAVY, lw=1.0, ls=":" if stalled else "-")
        if round(speeds[i] * 100) % 10 == 0:
            ax.annotate(f"{speeds[i]:.0%}".replace("%", " %"), (wc[i][-1], pr[i][-1]), fontsize=6, color=NAVY,
                        xytext=(3, -3), textcoords="offset points")
    ax.plot(wc[show, 0], pr[show, 0], color=RED, lw=1.8, label="Surge line")
    if line:
        pts = [cmap.point(n, b)[:2] for n, b in line]
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=AMBER, lw=1.6, label="Operating line")
    for (label, n, b, marker), colour in zip(points, POINT_COLOURS):
        g, p = cmap.point(n, b)[:2]
        ax.plot(g, p, marker, color=colour, ms=7, mec="white", label=label)
    style(ax, title, "Corrected flow (relative to design)", "Pressure ratio")
    legend(ax, loc="upper left")
    return fig


def turbine_map_figure(tmap, title, points=()):
    """Flow capacity and efficiency against pressure ratio on each speed line of a calculated turbine map,
    with operating points [(label, n, pr, marker)]."""
    fig, (ax1, ax2) = figure(10, 4, 2)
    prs = np.asarray(tmap.pr)
    keep = prs <= max(1.6 * tmap.design_pr, 1.5)
    cap_ref = tmap._eval(tmap._splines[0], 1.0, tmap.design_pr)
    lowest = min([0.5] + [n - 0.1 for _, n, _, _ in points])
    for j, n in enumerate(tmap.np_):
        if n < lowest - 1e-9:
            continue
        shade = NAVY if abs(n - 1) < 1e-9 else BLUE
        lw = 1.6 if abs(n - 1) < 1e-9 else 0.9
        ax1.plot(prs[keep], np.asarray(tmap.wp[j])[keep] / cap_ref, color=shade, lw=lw)
        ax2.plot(prs[keep], np.asarray(tmap.eff[j])[keep], color=shade, lw=lw)
        if round(n * 100) % 10 == 0:
            for ax, y in ((ax1, np.asarray(tmap.wp[j])[keep][-1] / cap_ref), (ax2, np.asarray(tmap.eff[j])[keep][-1])):
                ax.annotate(f"{n:.0%}".replace("%", " %"), (prs[keep][-1], y), fontsize=6, color=NAVY,
                            xytext=(3, 0), textcoords="offset points")
    eff_ref = tmap._eval(tmap._splines[1], 1.0, tmap.design_pr)
    for (label, n, pr, marker), colour in zip(points, POINT_COLOURS):
        ax1.plot(pr, tmap.capacity(n, pr), marker, color=colour, ms=7, mec="white", label=label)
        ax2.plot(pr, tmap.efficiency(n, pr) * eff_ref, marker, color=colour, ms=7, mec="white", label=label)
    ax2.set_ylim(0, None)
    style(ax1, f"{title}: flow capacity", "Pressure ratio", "Flow capacity (relative to design)")
    style(ax2, f"{title}: efficiency", "Pressure ratio", "Isentropic efficiency")
    if points:
        legend(ax2, loc="lower right")
    return fig


def _stage_label(s):
    return f"{s.stages} stages, loading {s.loading:g}, flow coefficient {s.flow_coefficient:g}"


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
        for w in (washes.loc[washes["engine"] == e_plot, "day"] if len(washes) else []):
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
    detected = int(sc["detected_day"].notna().sum()) if len(sc) else 0
    correct = int(sc["correct"].sum()) if len(sc) else 0
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


def _lto_figures(lto, take_off_tet, ei1, bpr, fan_pr, hpc_pr):
    modes = lto["modes"]
    names = [m["mode"] for m in modes]
    x = np.arange(len(modes))
    fig1, (ax1, ax2) = figure(10, 4, 2)
    ax1.bar(x, [m["ei_nox"] for m in modes], 0.6, color=NAVY)
    for i, m in enumerate(modes):
        ax1.annotate(f"{m['ei_nox']:.1f}", (i, m["ei_nox"]), ha="center", fontsize=7, xytext=(0, 2),
                     textcoords="offset points")
    ax1.set_xticks(x, names)
    style(ax1, f"EI NOx by mode, take-off TET {take_off_tet:.0f} K\n"
               f"index at severity 1: {ei1:g} g/kg, HPC PR {hpc_pr:g}", None, "EI NOx (g/kg fuel)")
    ax2.bar(x, [m["nox_g"] / 1e3 for m in modes], 0.6, color=RED)
    for i, m in enumerate(modes):
        ax2.annotate(f"{m['nox_g'] / 1e3:.2f} kg\n({m['fuel_kg']:.0f} kg fuel)", (i, m["nox_g"] / 1e3), ha="center",
                     fontsize=7, xytext=(0, 2), textcoords="offset points")
    ax2.set_xticks(x, names)
    ax2.set_ylim(0, 1.25 * max(m["nox_g"] for m in modes) / 1e3)
    style(ax2, f"NOx mass over the LTO cycle\nDp/Foo {lto['dp_foo']:.1f} g/kN, rated thrust "
               f"{lto['rated_thrust_kN']:.1f} kN", None, "NOx (kg)")
    fig2, ax = figure(7.5, 4)
    rpr = lto["rated_pressure_ratio"]
    prs = np.linspace(10, max(60, rpr + 5), 100)
    from ..aero_environment import nox_standard

    ax.plot(prs, [nox_standard(p, 6) for p in prs], color=AMBER, lw=1.6, label="CAEP/6 (tier 6)")
    ax.plot(prs, [nox_standard(p, 8) for p in prs], color=RED, lw=1.6, label="CAEP/8 (tier 8)")
    ax.plot(rpr, lto["dp_foo"], "o", color=NAVY, ms=9, mec="white", label="This engine")
    style(ax, f"LTO NOx against the standard\nrated PR {rpr:.1f}, Dp/Foo {lto['dp_foo']:.1f} g/kN "
              f"({100 * lto['dp_foo'] / lto['tier8']:.0f} % of CAEP/8), BPR {bpr:g}, FPR {fan_pr:g}",
          "Rated overall pressure ratio", "Dp/Foo (g/kN)")
    legend(ax, loc="upper left")
    return {"LTO NOx by mode": fig1, "LTO NOx against the standard": fig2}


def _turbofan_maps(engine, mode_pts, sd):
    markers = {"Take-off": "o", "Climb": "s", "Approach": "^", "Taxi/idle": "v"}
    pts = [(name, r.info, markers.get(name, "o")) for name, r in mode_pts]
    fan = compressor_map_figure(
        engine.fan_map,
        f"Fan map from stage stacking\nFPR {engine.cycle.fan_pr:g}, {int(sd['fan_stages'])} stage, "
        f"loading {sd['fan_loading']:g}, flow coefficient {sd['fan_flow_coeff']:g}",
        [("Cruise design", 1.0, engine.x_design["beta_fan"], "D")]
        + [(n, i["n1_corrected"], i["x"]["beta_fan"], m) for n, i, m in pts])
    hpc = compressor_map_figure(
        engine.hpc_map,
        f"HPC map from stage stacking\nPR {engine.cycle.hpc_pr:g}, {int(sd['hpc_stages'])} stages, "
        f"loading {sd['hpc_loading']:g}, {int(sd['hpc_vane_rows'])} variable rows",
        [("Cruise design", 1.0, engine.x_design["beta_hpc"], "D")]
        + [(n, i["n2_corrected"], i["x"]["beta_hpc"], m) for n, i, m in pts])
    hpt = turbine_map_figure(
        engine.hpt_map, f"HPT ({int(sd['hpt_stages'])} stages, loading {sd['hpt_loading']:g})",
        [("Cruise design", 1.0, engine.pr_hpt_design, "D")]
        + [(n, i["hpt_corrected_speed"], i["hpt_pr"], m) for n, i, m in pts])
    lpt = turbine_map_figure(
        engine.lpt_map, f"LPT ({int(sd['lpt_stages'])} stages, loading {sd['lpt_loading']:g})",
        [("Cruise design", 1.0, engine.pr_lpt_design, "D")]
        + [(n, i["lpt_corrected_speed"], i["lpt_pr"], m) for n, i, m in pts])
    return {"Fan map": fan, "HPC map": hpc, "HPT map": hpt, "LPT map": lpt}


# Stage design of the turbofan's components, from which its five maps are calculated, and the combustor's
# NOx index at severity 1 (32 g/kg: a conventional rich-burn combustor)
TURBOFAN_STAGE_DEFAULTS = {
    "fan_stages": 1, "fan_loading": 0.40, "fan_flow_coeff": 0.50,
    "booster_stages": 3, "booster_loading": 0.35, "booster_flow_coeff": 0.50,
    "hpc_stages": 10, "hpc_loading": 0.40, "hpc_flow_coeff": 0.50,
    "hpc_vane_rows": 4, "hpc_vane_closed": -40.0, "hpc_vane_open_speed": 0.95, "hpc_vane_closed_speed": 0.65,
    "hpt_stages": 2, "hpt_loading": 1.5, "hpt_flow_coeff": 0.60,
    "lpt_stages": 5, "lpt_loading": 2.5, "lpt_flow_coeff": 0.80,
    "ei_nox": 32.0,
}


def _stage_input_label(key):
    if key == "ei_nox":
        return "NOx index at severity 1 (g/kg)"
    words = key.replace("flow_coeff", "flow coefficient").replace("vane_", "vanes ").split("_")
    return " ".join(w.upper() if w in ("hpc", "hpt", "lpt") else w for w in words).capitalize().replace(
        "Hpc", "HPC").replace("Hpt", "HPT").replace("Lpt", "LPT")


def turbofan_stage_designs(sd):
    from ..mapgen import CompressorStages, TurbineStages

    def comp(k, **kw):
        return CompressorStages(int(sd[f"{k}_stages"]), sd[f"{k}_loading"], sd[f"{k}_flow_coeff"], **kw)

    return {
        "fan": comp("fan", variable_rows=0),
        "booster": comp("booster", variable_rows=0),
        "hpc": comp("hpc", variable_rows=int(sd["hpc_vane_rows"]), vane_closed=sd["hpc_vane_closed"],
                    vane_open_speed=sd["hpc_vane_open_speed"], vane_closed_speed=sd["hpc_vane_closed_speed"]),
        "hpt": TurbineStages(int(sd["hpt_stages"]), sd["hpt_loading"], sd["hpt_flow_coeff"]),
        "lpt": TurbineStages(int(sd["lpt_stages"]), sd["lpt_loading"], sd["lpt_flow_coeff"]),
    }


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
    stage_design=None,
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
            {"Quantity": "TSFC", "Value": r.tsfc, "Unit": "kg/(N s)"},
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
        ("Cruise TSFC", f"{sci(r.tsfc)} kg/(N s)"),
        ("Intake flow", f"{r.air_flow:.1f} kg/s"),
        ("Overall efficiency", f"{100 * r.overall_efficiency:.1f} %"),
    ]
    tables = {"Design point": design, "Stations": stations}
    figures = {}
    notes = []
    if offdesign:
        from ..aero_offdesign import MatchError, TurbofanEngine

        sd = {**TURBOFAN_STAGE_DEFAULTS, **(stage_design or {})}
        inputs.update({_stage_input_label(k): v for k, v in sd.items()})
        engine = TurbofanEngine(cycle, w, cruise, stage_designs=turbofan_stage_designs(sd))
        sls = Flight()
        take_off = engine.run(sls, tet=take_off_tet)
        key.append(("Take-off thrust (sea level static)", f"{take_off.net_thrust / 1e3:.1f} kN"))
        alts = np.linspace(0.0, 12000.0, 9)
        lapse = []
        fig, ax = figure(7.5, 4)
        start = take_off.info["x"]  # each curve starts from the last one's sea-level point
        gaps = []
        for m_, c in zip([0.2, 0.4, 0.6, 0.8], ["#c3daf7", "#86b6ef", BLUE, NAVY]):
            guess, th = start, []
            for h in alts:
                try:
                    if guess is None:
                        raise MatchError("no match at a lower altitude")
                    rr = engine.run(Flight(h, m_), tet=take_off_tet, guess=guess, walk=h == alts[0])
                except MatchError:  # this TET cannot hold a match here (or higher): leave a gap in the curve
                    guess = None
                    gaps.append((m_, h))
                    th.append(np.nan)
                    lapse.append({"Altitude (m)": h, "Mach": m_, "Net thrust (kN)": np.nan, "TSFC (kg/(N s))": np.nan})
                    continue
                guess = rr.info["x"]
                if h == alts[0]:
                    start = guess
                th.append(rr.net_thrust / 1e3)
                lapse.append(
                    {
                        "Altitude (m)": h,
                        "Mach": m_,
                        "Net thrust (kN)": rr.net_thrust / 1e3,
                        "TSFC (kg/(N s))": rr.tsfc,
                    }
                )
            ax.plot(alts / 1e3, th, color=c, lw=2, label=f"Mach {m_}")
        style(ax, f"Thrust lapse at TET {take_off_tet:.0f} K\nBPR {bpr:g}, FPR {fan_pr:g}, HPC PR {hpc_pr:g}, "
                 f"cruise TET {tet_cruise:.0f} K",
              "Altitude (km)", "Net thrust (kN)")
        legend(ax)
        figures["Thrust lapse"] = fig
        tables["Thrust lapse"] = pd.DataFrame(lapse)
        if gaps:
            first = {}
            for m_, h in gaps:
                first.setdefault(m_, h)
            notes.append(
                f"Thrust lapse at TET {take_off_tet:.0f} K stops where the engine has no match inside its calculated "
                "maps (a spool's corrected speed runs past the top speed line): "
                + ", ".join(f"Mach {m_} above {h / 1e3:g} km" for m_, h in first.items())
                + ". A real engine would be held there by its spool speed limits."
            )
        if environment:
            from ..aero_environment import NOxSeverity, lto_cycle, noise_sources

            try:
                lto = lto_cycle(engine, take_off_tet, NOxSeverity(sd["ei_nox"]))
            except (MatchError, RuntimeError, ValueError) as e:
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
                    "n1": "N1 (relative)",
                    "n2": "N2 (relative)",
                    "tet_K": "TET (K)",
                }
            )
            key += [
                ("LTO NOx Dp/Foo", f"{lto['dp_foo']:.1f} g/kN (CAEP/8 limit {lto['tier8']:.1f})"),
                ("Take-off EI NOx", f"{lto['modes'][0]['ei_nox']:.1f} g/kg"),
                ("LTO fuel / CO2", f"{lto['fuel_kg']:.0f} kg / {lto['co2_kg']:.0f} kg"),
            ]
            figures.update(_lto_figures(lto, take_off_tet, sd["ei_nox"], bpr, fan_pr, hpc_pr))
            notes.append(
                f"LTO NOx is calculated at each mode's own off-design point: the emissions index comes from the "
                f"NOx severity parameter (P3, T3 and humidity) times {sd['ei_nox']:g} g/kg, the index at "
                "severity 1 for your combustor technology (32 g/kg is a conventional rich-burn combustor)."
            )
            if lto["rated_thrust_kN"] <= 89:
                notes.append(
                    f"Rated thrust is {lto['rated_thrust_kN']:.1f} kN: the CAEP limits shown are the ones for "
                    "engines above 89 kN; smaller engines have a thrust-dependent allowance on top."
                )
            mode_pts = [(m["mode"], r) for m, r in zip(lto["modes"], lto["results"])]
            past = [m for m, r in mode_pts if r.info["surge_margin_fan"] < 0 or r.info["surge_margin_hpc"] < 0]
            if past:
                notes.append(
                    f"At {', '.join(past).lower()} a compressor works past its calculated surge line (see the fan "
                    "and HPC maps): with these stage values the design would need more surge margin there, for "
                    "example a variable-area bypass nozzle or handling bleed."
                )
        else:
            mode_pts = [("Take-off", take_off)]
        figures.update(_turbofan_maps(engine, mode_pts, sd))
        if environment:
            try:
                approach = (lto["results"][2] if lto is not None
                            else engine.run(sls, thrust=0.3 * take_off.net_thrust, guess=take_off.info["x"]))
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
            notes.append("Noise is component source levels, not certification EPNdB.")
    return Section("turbofan", "Turbofan", inputs, key, tables, figures, notes)
