"""Design-point trade study and T-s diagram for the example engine.

Run from the repository root:  python examples/parametric_study.py
Writes PNGs to docs/figures/.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from gtperf.cli import cycle_from_config
from gtperf.study import sweep

import tomllib

ROOT = Path(__file__).resolve().parents[1]
FIG = ROOT / "docs" / "figures"
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
BLUES = ["#86b6ef", "#2a78d6", "#184f95"]  # sequential: hotter = darker

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.spines.top": False,
    "axes.spines.right": False, "font.size": 10, "axes.titlesize": 11, "axes.titlecolor": INK,
    "lines.linewidth": 2,
})


def load():
    with open(ROOT / "examples" / "engine_13mw.toml", "rb") as f:
        return cycle_from_config(tomllib.load(f))


def trade_study(cycle, ambient):
    prs = np.linspace(4, 40, 73)
    tits_c = [1150, 1250, 1350]
    res = sweep(cycle, prs, [t + 273.15 for t in tits_c], ambient=ambient)
    design_pr = 16.8

    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    panels = [("efficiency", 100, "Thermal efficiency (%)"), ("specific_work", 1e-3, "Specific work (kJ/kg)")]
    for ax, (key, scale, label) in zip(axes, panels):
        for i, (tit, color) in enumerate(zip(tits_c, BLUES)):
            y = res[key][i] * scale
            ax.plot(prs, y, color=color, label=f"TIT {tit} °C")
            k = int(np.argmax(y))
            ax.plot(prs[k], y[k], "o", ms=8, color=color, mec=SURFACE, mew=2)
            ax.annotate(f"{tit} °C", (prs[-1], y[-1]), xytext=(4, 0), textcoords="offset points",
                        va="center", color=INK2, fontsize=9)
        ax.axvline(design_pr, color=INK2, lw=1, ls=":")
        ax.set_xlabel("Compressor pressure ratio")
        ax.set_title(label, loc="left")
        ax.set_xlim(4, 46)
    axes[0].annotate("design point", (design_pr, axes[0].get_ylim()[0]), xytext=(4, 6),
                     textcoords="offset points", color=INK2, fontsize=9)
    axes[0].legend(frameon=False, loc="lower right", labelcolor=INK2)
    fig.suptitle("Simple cycle trade study: dots mark each optimum", x=0.01, ha="left", color=INK, fontsize=12)
    fig.savefig(FIG / "trade_study.png", dpi=150)
    plt.close(fig)


def ts_diagram(cycle, ambient):
    r = cycle.run(39.0, ambient)
    st = r.stations
    fig, ax = plt.subplots(figsize=(6, 4.5), constrained_layout=True)

    s_ref = st["2"].s

    def heat_addition(a, b, n=40):
        """Isobaric-ish heat addition a -> b; entropy blended so both end points are exact."""
        frac = np.linspace(0, 1, n)
        ts = a.t + frac * (b.t - a.t)
        ps = a.p + frac * (b.p - a.p)
        s = np.array([a.gas.s(t, p) for t, p in zip(ts, ps)])
        s += frac * (b.s - a.gas.s(b.t, b.p))
        return s - s_ref, ts

    lines = [
        ([st["2"].s - s_ref, st["3"].s - s_ref], [st["2"].t, st["3"].t]),
        heat_addition(st["3"], st["41"]),
        ([st["41"].s - s_ref, st["5"].s - s_ref], [st["41"].t, st["5"].t]),
    ]
    for s, t in lines:
        ax.plot(s, np.array(t) - 273.15, color=BLUES[1])
    ax.plot([st["5"].s - s_ref, 0], [st["5"].t - 273.15, st["2"].t - 273.15], color=INK2, lw=1, ls="--")
    labels = {"2": "2 compressor inlet", "3": "3 compressor delivery", "41": "41 rotor inlet", "5": "5 exhaust"}
    for k, text in labels.items():
        x, y = st[k].s - s_ref, st[k].t - 273.15
        ax.plot(x, y, "o", ms=8, color=BLUES[1], mec=SURFACE, mew=2)
        ax.annotate(text, (x, y), xytext=(8, -3), textcoords="offset points", color=INK2, fontsize=9)
    tit = st["4"].t - 273.15
    ax.axhline(tit, color=INK2, lw=1, ls=":")
    ax.annotate(f"TIT {tit:.0f} °C (combustor exit, before cooling air mixes in)", (0, tit),
                xytext=(0, 4), textcoords="offset points", color=INK2, fontsize=9)
    ax.annotate("dashed: heat rejected to atmosphere", (0.45, 0.12), xycoords="axes fraction", color=INK2, fontsize=8)
    ax.set_xlabel("Specific entropy relative to station 2 (J/kg K)")
    ax.set_ylabel("Temperature (°C)")
    ax.set_title("Example engine at ISO conditions, T-s diagram", loc="left")
    ax.set_xlim(right=ax.get_xlim()[1] + 350)
    ax.set_ylim(top=tit + 120)
    fig.savefig(FIG / "ts_diagram.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    FIG.mkdir(parents=True, exist_ok=True)
    cycle, ambient = load()
    trade_study(cycle, ambient)
    ts_diagram(cycle, ambient)
    print("wrote", *sorted(p.name for p in FIG.glob("*.png")))
