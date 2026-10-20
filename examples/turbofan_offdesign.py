"""Turbofan off-design: working lines on the fan and HPC maps, thrust lapse and an idle to take-off
acceleration.

Run from the repository root:  python examples/turbofan_offdesign.py
Writes docs/figures/turbofan_offdesign.png.
"""

import matplotlib.pyplot as plt
import numpy as np
from parametric_study import FIG, GRID, INK, INK2, SURFACE

from gtperf.aero import Flight, Turbofan
from gtperf.aero_offdesign import TurbofanEngine

CRUISE = Flight(10668.0, 0.78)
SLS = Flight()
TAKE_OFF_TET = 1750.0
IDLE_N1 = 0.36
BLUE, RED = "#2a78d6", "#d03b3b"
MACH_SHADES = ["#c3daf7", "#86b6ef", "#2a78d6", "#184f95"]  # sequential: faster = darker


def build():
    cycle = Turbofan(bpr=10.0, fan_pr=1.5, fan_core_pr=2.4, hpc_pr=17.0, tet=1600.0, hp_offtake=150e3)
    return TurbofanEngine(cycle, cycle.size_for_thrust(22e3, CRUISE), CRUISE)


def steady_line(engine, flight, settings, key):
    rows, guess = [], None
    for v in settings:
        r = engine.run(flight, guess=guess, **{key: v})
        guess = r.info["x"]
        rows.append(r)
    return rows


def draw_map(ax, cmap, speeds, title):
    betas = np.linspace(1.0, 3.0, 21)
    for n in speeds:
        g, pr = zip(*(cmap.point(n, b)[:2] for b in betas))
        ax.plot(g, pr, color=GRID, lw=1.2, zorder=1)
        ax.annotate(f"{n:.2f}", (g[-1], pr[-1]), xytext=(2, -2), textcoords="offset points", fontsize=7, color=INK2)
    surge = [cmap.point(n, 1.0)[:2] for n in np.linspace(speeds[0], speeds[-1], 30)]
    ax.plot(*zip(*surge), color=INK2, lw=1.5, ls="--", label="surge line (R-line 1)")
    ax.set_xlabel("Relative corrected flow")
    ax.set_ylabel("Pressure ratio")
    ax.set_title(title, loc="left")


def main():
    engine = build()
    take_off = engine.run(SLS, tet=TAKE_OFF_TET)
    idle = engine.run(SLS, n1=IDLE_N1)
    sls_line = steady_line(engine, SLS, np.linspace(IDLE_N1, take_off.info["n1"], 14), "n1")
    cruise_line = steady_line(engine, CRUISE, np.linspace(1700.0, 1250.0, 10), "tet")

    def ramp(t):  # fuel ramp over 3 s, a stand-in for the acceleration schedule
        return idle.fuel_flow + (take_off.fuel_flow - idle.fuel_flow) * min(t / 3.0, 1.0)

    def step(t):  # TET demanded in 0.5 s: no acceleration schedule at all
        return idle.info["tet"] + (TAKE_OFF_TET - idle.info["tet"]) * min(t / 0.5, 1.0)

    accel = engine.simulate(ramp, 8.0, idle, kind="fuel", dt=0.05)
    hard = engine.simulate(step, 8.0, idle, kind="tet", dt=0.05)

    fig, axes = plt.subplots(2, 3, figsize=(15, 8.6), constrained_layout=True)
    ax = axes[0, 0]
    draw_map(ax, engine.fan_map, [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.05], "Fan map: working lines")
    for rows, color, label in ((sls_line, INK, "sea-level static, idle to take-off"),
                               (cruise_line, BLUE, "35,000 ft Mach 0.78, TET 1250-1700 K")):
        ax.plot([r.info["fan_flow"] for r in rows], [r.info["fan_pr"] for r in rows], "o-", color=color, ms=4,
                label=label)
    ax.plot(1, engine.cycle.fan_pr, "D", color=RED, ms=8, mec=SURFACE, mew=1.5, label="design point (cruise)")
    ax.legend(frameon=False, fontsize=8, labelcolor=INK2, loc="upper left")

    ax = axes[0, 1]
    draw_map(ax, engine.hpc_map, [0.6, 0.7, 0.8, 0.9, 1.0, 1.05], "HPC map: steady and transient working lines")
    ax.plot([r.info["hpc_flow"] for r in sls_line], [r.info["hpc_pr"] for r in sls_line], "o-", color=INK, ms=4,
            label="steady, sea-level static")
    ax.plot(accel.hpc_flow, accel.hpc_pr, color=BLUE, label="acceleration, 3 s fuel ramp")
    ax.plot(hard.hpc_flow, hard.hpc_pr, color=RED, label="acceleration, TET step (no schedule)")
    ax.legend(frameon=False, fontsize=8, labelcolor=INK2, loc="upper left")

    ax = axes[0, 2]
    alts = np.linspace(0.0, 12000.0, 13)
    for mach, color in zip([0.2, 0.4, 0.6, 0.8], MACH_SHADES):
        thrust = [r.net_thrust for r in steady_line_flights(engine, alts, mach)]
        ax.plot(alts / 1e3, np.array(thrust) / take_off.net_thrust, color=color, label=f"Mach {mach}")
    ax.legend(frameon=False, fontsize=8, labelcolor=INK2, loc="upper right")
    ax.set_xlabel("Altitude (km)")
    ax.set_ylabel("Net thrust / sea-level static take-off")
    ax.set_title("Thrust lapse at TET 1700 K", loc="left")

    t95 = accel.time_to(0.95)
    ax = axes[1, 0]
    for y, label, style in ((accel.n1, "N1", "-"), (accel.n2, "N2", "--"),
                            (accel.net_thrust / take_off.net_thrust, "thrust / take-off", "-")):
        ax.plot(accel.t, y, style, color=BLUE if label != "thrust / take-off" else INK, label=label)
    ax.axvline(t95, color=INK2, lw=1, ls=":")
    ax.annotate(f"95 % thrust at {t95:.2f} s", (t95, 0.55), xytext=(5, 0), textcoords="offset points", color=INK2,
                fontsize=9)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Relative to design speed / take-off thrust")
    ax.set_title("Idle to take-off, 3 s fuel ramp", loc="left")
    ax.legend(frameon=False, fontsize=8, labelcolor=INK2, loc="center right")

    ax = axes[1, 1]
    for tr, color, label in ((accel, BLUE, "3 s fuel ramp"), (hard, RED, "TET step")):
        ax.plot(tr.t, 100 * tr.surge_margin_hpc, color=color, label=label)
        k = int(np.argmin(tr.surge_margin_hpc))
        ax.plot(tr.t[k], 100 * tr.surge_margin_hpc[k], "o", color=color, ms=7, mec=SURFACE, mew=1.5)
        ax.annotate(f"min {100 * tr.surge_margin_hpc[k]:.1f} %", (tr.t[k], 100 * tr.surge_margin_hpc[k]),
                    xytext=(8, -4), textcoords="offset points", color=INK2, fontsize=9)
    ax.axhline(0, color=INK2, lw=1)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("HPC surge margin (%)")
    ax.set_title("HPC surge margin excursion", loc="left")
    ax.legend(frameon=False, fontsize=8, labelcolor=INK2, loc="center right")

    ax = axes[1, 2]
    for tr, color, label in ((accel, BLUE, "3 s fuel ramp"), (hard, RED, "TET step")):
        ax.plot(tr.t, tr.tet, color=color, label=label)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("TET (K)")
    ax.set_title("Turbine entry temperature", loc="left")
    ax.legend(frameon=False, fontsize=8, labelcolor=INK2, loc="lower right")

    fig.suptitle("Two-spool turbofan off design (cruise design point, NPSS public maps, assumed spool inertias "
                 f"{engine.spools.lp_inertia:g} and {engine.spools.hp_inertia:g} kg m2)", x=0.01, ha="left",
                 color=INK, fontsize=12)
    fig.savefig(FIG / "turbofan_offdesign.png", dpi=150)
    plt.close(fig)

    cruise = engine.design
    print(f"design reproduction: thrust error {engine.run().net_thrust / cruise.net_thrust - 1:.1e}")
    print(f"cruise: {cruise.net_thrust / 1e3:.2f} kN, TSFC {cruise.tsfc:.3e} kg/(N s)")
    print(f"take-off (SLS, TET {TAKE_OFF_TET:.0f} K): {take_off.net_thrust / 1e3:.1f} kN, "
          f"TSFC {take_off.tsfc:.3e} kg/(N s), N1 {take_off.info['n1']:.3f}, N2 {take_off.info['n2']:.3f}")
    print(f"idle (N1 {IDLE_N1}): {idle.net_thrust / 1e3:.1f} kN, N2 {idle.info['n2']:.3f}")
    print(f"acceleration idle to 95 % take-off thrust: {t95:.2f} s (fuel ramp), {hard.time_to(0.95):.2f} s (TET step)")
    print(f"minimum HPC surge margin: {100 * accel.surge_margin_hpc.min():.1f} % (fuel ramp), "
          f"{100 * hard.surge_margin_hpc.min():.1f} % (TET step), steady {100 * idle.info['surge_margin_hpc']:.1f} %"
          " at idle")


def steady_line_flights(engine, alts, mach):
    rows, guess = [], None
    for h in alts:
        r = engine.run(Flight(h, mach), tet=1700.0, guess=guess)
        guess = r.info["x"]
        rows.append(r)
    return rows


if __name__ == "__main__":
    main()
