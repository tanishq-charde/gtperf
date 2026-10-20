"""Turbofan cycle trade at cruise: bypass ratio and fan pressure ratio.

Run from the repository root:  python examples/turbofan_study.py
Writes docs/figures/turbofan_trade.png.
"""

import matplotlib.pyplot as plt
import numpy as np
from parametric_study import FIG, GRID, INK, INK2, SURFACE

from gtperf.aero import Flight, Turbofan

CRUISE = Flight(10668.0, 0.78)
BPRS = [4, 7, 10, 13]
SHADES = ["#c3daf7", "#86b6ef", "#2a78d6", "#184f95"]  # sequential: higher bypass ratio = darker
FLAG = "#d03b3b"


def engine(bpr, fpr):
    return Turbofan(bpr=bpr, fan_pr=fpr, fan_core_pr=2.4, hpc_pr=17.0, tet=1600.0)


def sweep(bpr):
    rows = []
    for fpr in np.arange(1.25, 3.0, 0.02):
        try:
            r = engine(bpr, fpr).run(100.0, CRUISE)
        except ValueError:  # the LPT cannot drive a fan this hard
            break
        rows.append((fpr, r.specific_thrust, r.tsfc, r))
    return rows


def main():
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4), constrained_layout=True, gridspec_kw={"width_ratios": [1.3, 1]})
    ax = axes[0]
    best = []
    for bpr, color in zip(BPRS, SHADES):
        rows = sweep(bpr)
        x, y = [r[1] for r in rows], [r[2] for r in rows]
        ax.plot(x, y, color=color)
        k = int(np.argmin(y))
        best.append((bpr, rows[k]))
        ax.annotate(f"BPR {bpr}", (x[-1], y[-1]), xytext=(4, 0), textcoords="offset points", va="center",
                    color=INK2, fontsize=9)
    ax.plot([b[1][1] for b in best], [b[1][2] for b in best], "o--", color=INK, lw=1, ms=6, mec=SURFACE, mew=1.5,
            label="best fan pressure ratio for each BPR")
    design = engine(10, 1.5).run(100.0, CRUISE)
    ax.plot(design.specific_thrust, design.tsfc, "D", color=FLAG, ms=8, mec=SURFACE, mew=1.5,
            label="example design (BPR 10, FPR 1.50)")
    ax.set_xlabel("Specific thrust (m/s)")
    ax.set_ylabel("TSFC (kg/(N s))")
    ax.set_title("TSFC against specific thrust, each line sweeps fan pressure ratio", loc="left")
    ax.legend(frameon=False, loc="upper left", labelcolor=INK2, fontsize=9)

    ax = axes[1]
    bprs = np.arange(3, 16, 1.0)
    eff = {"thermal": [], "propulsive": [], "overall": []}
    for bpr in bprs:
        rows = sweep(bpr)
        r = min(rows, key=lambda row: row[2])[3]
        eff["thermal"].append(100 * r.thermal_efficiency)
        eff["propulsive"].append(100 * r.propulsive_efficiency)
        eff["overall"].append(100 * r.overall_efficiency)
    for (name, y), color in zip(eff.items(), ["#86b6ef", "#2a78d6", INK]):
        ax.plot(bprs, y, color=color, lw=2.5 if name == "overall" else 2)
        ax.annotate(name, (bprs[-1], y[-1]), xytext=(4, 0), textcoords="offset points", va="center", color=INK2,
                    fontsize=9)
    ax.set_xlim(3, 17.5)
    ax.set_xlabel("Bypass ratio (at its best fan pressure ratio)")
    ax.set_title("Efficiency (%): higher bypass buys propulsive efficiency", loc="left")
    for a in axes:
        a.grid(color=GRID)
    fig.suptitle("Two-spool turbofan at Mach 0.78, 35,000 ft, OPR about 41, TET 1600 K", x=0.01, ha="left",
                 color=INK, fontsize=12)
    fig.savefig(FIG / "turbofan_trade.png", dpi=150)
    plt.close(fig)
    for bpr, (fpr, st, tsfc, _) in best:
        print(f"BPR {bpr:4.1f}: best FPR {fpr:.2f}, specific thrust {st:6.1f} m/s, TSFC {tsfc:.3e} kg/(N s)")


if __name__ == "__main__":
    main()
