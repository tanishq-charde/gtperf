"""Production SPC and in-service fleet monitoring on synthetic data.

Run from the repository root:  python examples/fleet_analysis.py
Takes a few minutes (two fleets of full off-design runs). Writes data, figures
and docs/fleet/report.md.
"""


import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from parametric_study import BLUES, INK, INK2, ROOT, SURFACE  # shared figure style

from gtperf import spc
from gtperf.cli import engine_from_datasheet
from gtperf.fleet import (
    PASSOFF_NOISE,
    BaselineModel,
    FleetFault,
    default_fleet_faults,
    default_production_events,
    estimate_health,
    in_service_fleet,
    make_gpa,
    monitor_fleet,
    naive_fleet_outliers,
    production_sequence,
    score,
)
from gtperf.gpa import HEALTH, HEALTH_LABELS, MEASUREMENTS, GasPathAnalysis, influence_matrix

OUT = ROOT / "docs" / "fleet"
FIG = OUT / "figures"
FLAG, MUTED = "#d03b3b", "#b8b7b2"
LSL_POWER = 12.40  # production acceptance limit, MW
D_COLS = [f"d_{m}" for m in MEASUREMENTS]

TEST_FAULTS = [
    FleetFault(2, 350, "engine", "turbine_eff", "HP turbine blade damage", -1.2),
    FleetFault(6, 420, "engine", "turbine_capacity", "HP turbine nozzle erosion", 2.5),
    FleetFault(9, 280, "engine", "compressor_eff", "Inlet filter failure, rapid fouling", -2.0, ramp_days=40),
    FleetFault(4, 300, "sensor", "sensor:egt", "Exhaust thermocouple drift", 10.0, ramp_days=60),
    FleetFault(11, 500, "sensor", "sensor:fuel_flow", "Fuel meter calibration error", 1.0),
]


def label(code):
    if code is None:
        return "not detected"
    if code.startswith("sensor:"):
        return f"{code.split(':')[1]} sensor"
    return HEALTH_LABELS[code].split(" (")[0].lower()


# --- production --------------------------------------------------------------

def production(engine):
    p = production_sequence(engine)
    p.to_csv(OUT / "production_passoff.csv", index=False, float_format="%.5f")
    x = p["power_MW"].to_numpy()
    c, s = spc.phase1(x, 50)
    ind = spc.individuals(x, c, s)
    shift_at = 120
    skip = {int(i) for i in ind.signals if i < shift_at - 1}  # assigned causes before the shift
    ew = spc.ewma(x, c, s, skip=skip)
    cu = spc.cusum(x, c, s, skip=skip)

    def first_after(chart):
        sig = [int(i) + 1 for i in chart.signals if i not in skip and i + 1 >= shift_at]
        return sig[0] if sig else None

    detect = {"Individuals (Shewhart)": first_after(ind), "EWMA": first_after(ew), "CUSUM": first_after(cu)}

    gpa = GasPathAnalysis(influence_matrix(engine), PASSOFF_NOISE)
    before = p[D_COLS].iloc[50:shift_at - 1].mean().to_numpy()
    after = p[D_COLS].iloc[shift_at - 1:].mean().to_numpy()
    shift_class = gpa.classify(after - before)[:3]
    outlier = gpa.classify(p[D_COLS].iloc[44].to_numpy() - before)[:3]
    cpk = (spc.cpk_lower(np.delete(x[:shift_at - 1], 44), LSL_POWER), spc.cpk_lower(x[shift_at - 1:], LSL_POWER))

    builds = p["build"].to_numpy()
    fig, axes = plt.subplots(3, 1, figsize=(10, 8), constrained_layout=True, sharex=True)
    ax = axes[0]
    ax.plot(builds, x, color=BLUES[1], lw=1, marker="o", ms=3)
    ax.plot(builds[ind.signals], x[ind.signals], "o", ms=7, color=FLAG, mec=SURFACE, mew=1.5)
    for y in (ind.ucl[0], ind.lcl[0]):
        ax.axhline(y, color=INK2, lw=1, ls="--")
    ax.axhline(c, color=INK2, lw=0.8)
    ax.axhline(LSL_POWER, color=FLAG, lw=1)
    ax.annotate("acceptance limit", (2, LSL_POWER), xytext=(0, 3), textcoords="offset points", color=INK2, fontsize=8)
    ax.set_title("Corrected pass-off power (MW), individuals chart with 3-sigma limits", loc="left")
    for a, chart, name in [(axes[1], ew, "EWMA of corrected power (MW)"),
                           (axes[2], cu, "CUSUM of corrected power (MW)")]:
        a.plot(builds, chart.statistic, color=BLUES[1])
        a.plot(builds, chart.ucl, color=INK2, lw=1, ls="--")
        a.plot(builds, chart.lcl, color=INK2, lw=1, ls="--")
        sig = [i for i in chart.signals if i not in skip]
        a.plot(builds[sig], chart.statistic[sig], "o", ms=5, color=FLAG)
        a.set_title(name, loc="left")
    for a in axes:
        a.axvline(shift_at - 0.5, color=INK2, lw=1, ls=":")
        a.axvspan(79.5, 85.5, color="#f0efec", lw=0)
    lo = axes[2].get_ylim()[0]
    axes[2].annotate("supplier change", (shift_at, lo), xytext=(4, 6), textcoords="offset points", color=INK2,
                     fontsize=8)
    axes[2].annotate("test-bed meter\nerror (shaded)", (79.5, lo), xytext=(-4, 6), textcoords="offset points",
                     color=INK2, fontsize=8, ha="right")
    axes[2].set_xlabel("Build number")
    fig.savefig(FIG / "production_spc.png", dpi=150)
    plt.close(fig)
    return {"p": p, "detect": detect, "shift_class": shift_class, "outlier": outlier, "cpk": cpk, "centre": c,
            "sigma": s, "signals": [int(i) + 1 for i in ind.signals]}


# --- in-service ----------------------------------------------------------------

def run_fleet(engine, baseline, gpa, faults, seed, thresholds=(20, 25, 30)):
    data, washes, faults = in_service_fleet(engine, faults=faults, seed=seed)
    n_eng, days = data["engine"].nunique(), int(data["day"].max()) + 2
    table = []
    results = {}
    for thr in thresholds:
        m = monitor_fleet(engine, data, washes, baseline, gpa, step_threshold=thr)
        sc, false, far = score(m.alarms, faults, n_eng, days)
        table.append({"threshold": thr, "detected": int(sc["detected_day"].notna().sum()), "faults": len(faults),
                      "isolated": int(sc["correct"].sum()), "false_per_engine_year": far})
        results[thr] = (m, sc, false, far)
    return data, washes, faults, pd.DataFrame(table), results


def engine_figure(data, mon, washes, faults, engine_ids, path, gpa):
    fig, axes = plt.subplots(len(engine_ids), 2, figsize=(11, 2.7 * len(engine_ids)), constrained_layout=True)
    health = estimate_health(mon.dz, gpa, data=data.loc[mon.dz.index])
    for row, e in zip(axes, engine_ids):
        idx = data.loc[mon.dz.index].query("engine == @e").index
        days = data.loc[idx, "day"]
        f = next(f for f in faults if f.engine == e)
        ax = row[0]
        for col, name, color in [("d_power", "power (%)", BLUES[1]), ("d_egt", "EGT (K)", BLUES[2])]:
            ax.plot(days, mon.dz.loc[idx, col], color=color, lw=0.6, alpha=0.35)
            ax.plot(days, mon.dz.loc[idx, col].ewm(alpha=0.1).mean(), color=color, lw=1.6, label=name)
        ax.legend(frameon=False, fontsize=8, loc="upper left", labelcolor=INK2, ncol=2)
        ax.set_title(f"Engine {e}: {f.description.lower()} from day {f.day}", loc="left", fontsize=10)
        ax2 = row[1]
        for j, h in enumerate(HEALTH):
            ax2.plot(days, health.loc[idx, h], lw=1.4, color=["#2a78d6", "#eb6834", "#1baf7a", "#6250d6"][j],
                     label=HEALTH_LABELS[h])
        ax2.set_title("Gas-path health estimate (EWMA-smoothed)", loc="left", fontsize=10)
        if e == engine_ids[0]:
            ax2.legend(frameon=False, fontsize=7, loc="lower left", labelcolor=INK2, ncol=2)
        for a in row:
            for w in washes.query("engine == @e")["day"]:
                a.axvline(w, color=MUTED, lw=0.8, ls=":")
            a.axvline(f.day, color=INK2, lw=1)
            for al in [al for al in mon.alarms if al.engine == e][:1]:
                a.axvline(al.day, color=FLAG, lw=1.5)
            a.axhline(0, color=INK2, lw=0.6)
        row[1].set_xlabel("Day") if e == engine_ids[-1] else None
    fig.suptitle("Delta and health trends. Black: fault starts. Red: first alarm. Dotted: compressor washes.",
                 x=0.01, ha="left", color=INK, fontsize=11)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def cycling_study(engine, seed=23):
    """The test fleet's faults on cycling units: base-load-only monitoring against base plus part load."""
    data, washes, faults = in_service_fleet(engine, faults=TEST_FAULTS, seed=seed, dispatch="cycling")
    out = {"base_share": float(data["base_load"].mean())}
    for name, part in (("base", False), ("both", True)):
        m = monitor_fleet(engine, data, washes, part_load=part)
        out[name] = score(m.alarms, faults, 12, 730)
    return out


def write_report(prod, train, test, naive, cycling):
    L = ["# Fleet performance monitoring", "",
         "Synthetic data from the calibrated SGT-400-class model. Two problems: watching production pass-off results "
         "build by build, and watching engines in service for faults.", ""]
    L += ["## 1. Production pass-off SPC", "",
          "200 engines in build order, each with build-to-build component scatter and pass-off measurement error. "
          "Three events are hidden in the data:", ""]
    L += [f"- Build {ev.first}{'' if ev.last == ev.first else ('–' + str(ev.last) if ev.last else ' onwards')}: "
          f"{ev.description}" for ev in default_production_events()]
    L += ["", f"Phase I limits from builds 1–50 (outliers removed): centre {prod['centre']:.3f} MW, "
          f"sigma {prod['sigma']:.3f} MW. Individuals chart signals at builds {prod['signals']}.", "",
          "| Chart | First signal after the supplier change (build 120) | Builds to detect |", "|---|---:|---:|"]
    for k, v in prod["detect"].items():
        L.append(f"| {k} | {v if v else 'none'} | {v - 120 if v else '–'} |")
    sc = prod["shift_class"]
    L += ["", f"Gas-path analysis of the mean delta change across build 120 points to **{label(sc[0][0])}** "
          f"(match {sc[0][1]:.3f}; next {label(sc[1][0])} {sc[1][1]:.3f}). Build 45 matches "
          f"**{label(prod['outlier'][0][0])}** ({prod['outlier'][0][1]:.3f}).", "",
          f"Process capability against the {LSL_POWER} MW acceptance limit: Cpk {prod['cpk'][0]:.2f} before the "
          f"change, {prod['cpk'][1]:.2f} after.", "",
          "The +0.6 % power metering error on builds 80–85 is too small to see in power alone (about half a sigma). "
          "It shows up in heat rate instead, because the meter reads high while fuel flow is unaffected.", "",
          "![Production SPC](figures/production_spc.png)", ""]

    L += ["## 2. In-service fleet monitoring", "",
          "Twelve engines on site for two years, with base-load snapshots every two days. Compressors foul and are "
          "washed every 100–140 days, components wear slowly, and the weather follows a UK-like seasonal "
          "cycle. Each snapshot is compared with the nominal model at the same ambient conditions.", "",
          "Two physics-based detectors run on the deltas:", "",
          "- **Step detector.** It compares the last 8 snapshots with the engine's own trend over the 20 before, "
          "with known washes modelled as steps. The statistic is chi-squared with 6 degrees of freedom when nothing "
          "has changed, using the exact prediction variance of the trend fit and the correlated error from the "
          "ambient temperature sensor. Gas-path fingerprints name the most likely cause.",
          "- **Instrument consistency monitor.** It checks whether the smoothed deltas can be explained by any "
          "combination of component changes (generalised least squares residual). A drifting sensor cannot, and "
          "removing one sensor at a time names it. Large real engine faults are refitted with the full nonlinear "
          "model before being called an instrument fault.", "",
          "Snapshots with the gas generator on its speed limit (cold days) are excluded, because the nominal "
          "baseline is on temperature control there.", ""]
    L += ["### Choosing the threshold on a training fleet", "",
          "| Step threshold | Faults detected | Correctly isolated | False alarms per engine-year |",
          "|---:|---:|---:|---:|"]
    for _, r in train[3].iterrows():
        L.append(f"| {r.threshold} | {r.detected} / {r.faults} | {r.isolated} | {r.false_per_engine_year:.2f} |")
    thr = 25
    L += ["", f"A threshold of {thr} was chosen. It sits about 3e-4 into the chi-squared tail.", "",
          "### Results on an unseen test fleet", "",
          "This fleet has different random seeds and different faults on different engines and days.", "",
          "| Engine | Fault | Detected after (days) | Isolated as | Correct |", "|---:|---|---:|---|---|"]
    m, sc_t, false_t, far_t = test[4][thr]
    for _, r in sc_t.iterrows():
        d = f"{int(r.delay_days)}" if pd.notna(r.delay_days) else "not detected"
        L.append(f"| {r.engine} | {r.fault} | {d} | {label(r.isolated_as)} | {'yes' if r.correct else 'no'} |")
    L += ["", f"False alarms: {len(false_t)} in {12 * 2} engine-years ({far_t:.2f} per engine-year)."]
    nsc, nfalse, nfar = naive
    det = int(nsc["detected_day"].notna().sum())
    L += ["", "### Compared with a naive data-driven detector", "",
          "A Hotelling T² outlier test on each engine's rolling-mean deltas against the fleet's spread in the "
          "first 90 days has no physics and no knowledge of washes.", "",
          "| Detector | Faults detected | Can name the cause | False alarms per engine-year |",
          "|---|---:|---|---:|",
          f"| Physics-based (step + consistency) | {int(sc_t['detected_day'].notna().sum())} / {len(sc_t)} | yes | "
          f"{far_t:.2f} |",
          f"| Naive T² outlier | {det} / {len(nsc)} | no | {nfar:.2f} |", "",
          "The naive detector mostly flags engines that are simply dirtier or more worn than the fleet was early "
          "on. That is real but expected, so it produces nuisance alarms, and it cannot tell a sensor fault from an "
          "engine fault.", "",
          "![Engine dashboards](figures/engine_trends.png)", ""]
    sb, sp = cycling["base"][0], cycling["both"][0]
    L += ["### Cycling units: monitoring at part load", "",
          f"The same faults on engines that follow a dispatch profile: base load on "
          f"{100 * cycling['base_share']:.0f} % "
          "of snapshots, otherwise 55–95 % load. Monitoring only base-load points throws the rest away. At part "
          "load the output is the operator's setting, so the baseline is read at the measured output, the power "
          "delta is zero by construction, and an influence matrix built at part load (power excluded) explains the "
          "other five deltas. A power meter error then appears as the engine running at a different output than "
          "the baseline assumes, and its fingerprint is the change in the other deltas per percent of output; the "
          "meter's own noise is carried into the step detector's covariance for the same reason. The two streams "
          "are monitored separately per engine and their alarms merged.", "",
          "| Engine | Fault | Base load only: detected after (days) | Base + part load | Isolated as (base + part) |",
          "|---:|---|---:|---:|---|"]
    for (_, a), (_, b) in zip(sb.iterrows(), sp.iterrows(), strict=True):
        da = f"{int(a.delay_days)}" if pd.notna(a.delay_days) else "not detected"
        db = f"{int(b.delay_days)}" if pd.notna(b.delay_days) else "not detected"
        L.append(f"| {a.engine} | {a.fault} | {da} | {db} | {label(b.isolated_as)} |")
    L += ["", f"False alarms per engine-year: {cycling['base'][2]:.2f} base load only, {cycling['both'][2]:.2f} with "
          "part load.", "",
          "The instrument consistency check runs on base-load points only: at part load five free measurements and "
          "four health parameters leave one degree of freedom, enough to see an inconsistency but not to name the "
          "sensor.", "",
          "## Limitations", "",
          "- The compressor and lumped turbine efficiency fingerprints are similar, so a small fault in one is "
          "easily confused with the other. The split-turbine study in [gpa_observability.md](../gpa_observability.md) "
          "shows a second snapshot at part load is the most effective cure with the existing instruments.",
          "- Compressor flow capacity only shows in gas generator speed on this twin-shaft engine at "
          "temperature control, so fouling flow loss is weakly observable.",
          "- Gradual sensor drifts are invisible to the step detector by design (it follows trends), which is "
          "why the consistency monitor exists.",
          "- Everything is synthetic, and the field instrument noise levels are assumptions."]
    (OUT / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    FIG.mkdir(parents=True, exist_ok=True)
    _, _, engine = engine_from_datasheet(ROOT / "examples" / "sgt400_13mw.toml")
    prod = production(engine)
    print("production done", prod["detect"])

    baseline, gpa = BaselineModel(engine), make_gpa(engine)
    train = run_fleet(engine, baseline, gpa, default_fleet_faults(), seed=11)
    print("training fleet\n", train[3])
    test = run_fleet(engine, baseline, gpa, TEST_FAULTS, seed=23, thresholds=(25,))
    data, washes, faults = test[0], test[1], test[2]
    data.to_csv(OUT / "in_service_fleet.csv", index=False, float_format="%.5f")
    washes.to_csv(OUT / "washes.csv", index=False)
    m = test[4][25][0]
    d = data[~m.excluded]
    nv = naive_fleet_outliers(d, m.dz)
    naive = score(nv, faults, 12, 730)
    engine_figure(data, m, washes, faults, [2, 9, 4], FIG / "engine_trends.png", gpa)
    cycling = cycling_study(engine)
    print("cycling fleet\n", cycling["base"][0], "\n", cycling["both"][0])
    write_report(prod, train, test, naive, cycling)
    print("test fleet\n", test[4][25][1])
    print("wrote docs/fleet/report.md")
