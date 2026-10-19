"""Correction curves and a site performance sheet for the calibrated twin-shaft model.

Run from the repository root:  python examples/site_performance.py
Writes docs/figures/correction_curves.png, docs/figures/site_factors.png and
docs/site_hot_desert.md.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from gtperf.cli import engine_from_datasheet, site_from_config
from gtperf.site import correction_curves, site_performance

from parametric_study import BLUES, FIG, INK, INK2, ROOT, SURFACE  # shared figure style

SPEED_SHADE = "#f0efec"


def curves_figure(engine):
    temps = list(np.arange(-20, 46, 1.0))
    cc = correction_curves(engine, temps)
    speed_limited = [t for t, c in zip(temps, cc["control"]) if c != "temperature"]
    panels = [
        ("power", 100, "Output (% of ISO)", True),
        ("heat_rate", 100, "Heat rate (% of ISO)", True),
        ("exhaust_flow", 100, "Exhaust flow (% of ISO)", True),
        ("exhaust_temp_delta_K", 1, "Exhaust temperature change (K)", False),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(10, 6.5), constrained_layout=True, sharex=True)
    for ax, (key, scale, title, pct) in zip(axes.flat, panels):
        if speed_limited:
            ax.axvspan(temps[0], max(speed_limited), color=SPEED_SHADE, lw=0)
        ax.plot(temps, np.array(cc[key]) * scale, color=BLUES[1])
        ax.axvline(15, color=INK2, lw=1, ls=":")
        ax.axhline(100 if pct else 0, color=INK2, lw=0.8)
        ax.set_title(title, loc="left")
        ax.set_xlim(temps[0], temps[-1])
    for ax in axes[1]:
        ax.set_xlabel("Ambient temperature (°C)")
    a = axes[0, 0]
    if speed_limited:
        a.annotate("gas generator\nspeed limit", (temps[0] + 1, a.get_ylim()[0]), xytext=(0, 6),
                   textcoords="offset points", color=INK2, fontsize=9)
    a.annotate("ISO 15 °C", (15, a.get_ylim()[1]), xytext=(4, -14), textcoords="offset points",
               color=INK2, fontsize=9)
    fig.suptitle("Ambient temperature correction curves, model calibrated to SGT-400 13 MW datasheet",
                 x=0.01, ha="left", color=INK, fontsize=12)
    fig.savefig(FIG / "correction_curves.png", dpi=150)
    plt.close(fig)


def factors_figure(perf):
    names = [f[0] for f in perf.factors]
    pct = [100 * (f[1] - 1) for f in perf.factors]
    fig, ax = plt.subplots(figsize=(7, 3.6), constrained_layout=True)
    y = np.arange(len(names))[::-1]
    ax.barh(y, pct, height=0.6, color=BLUES[1])
    for yi, v in zip(y, pct):
        ax.annotate(f"{v:+.1f} %", (v, yi), xytext=(-4 if v < 0 else 4, 0), textcoords="offset points",
                    ha="right" if v < 0 else "left", va="center", color=INK2, fontsize=9)
    ax.set_yticks(y, names)
    ax.axvline(0, color=INK2, lw=0.8)
    ax.grid(axis="y", visible=False)
    ax.set_xlim(min(pct) * 1.25, max(2.0, max(pct) * 1.5))
    ax.set_xlabel("Change in output from the previous step (%)")
    total = 100 * (perf.result.electrical_power / perf.iso.electrical_power - 1)
    ax.set_title(f"{perf.site.name}: output {perf.result.electrical_power / 1e6:.2f} MW "
                 f"({total:+.1f} % vs ISO)", loc="left")
    fig.savefig(FIG / "site_factors.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    FIG.mkdir(parents=True, exist_ok=True)
    _, cal, engine = engine_from_datasheet(ROOT / "examples" / "sgt400_13mw.toml")
    curves_figure(engine)
    perf = site_performance(engine, site_from_config(ROOT / "examples" / "site_hot_desert.toml"))
    factors_figure(perf)
    (ROOT / "docs" / "site_hot_desert.md").write_text(perf.to_markdown() + "\n", encoding="utf-8")
    print("wrote correction_curves.png, site_factors.png, site_hot_desert.md")
