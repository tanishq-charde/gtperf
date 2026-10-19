"""Markdown pass-off test report with figures."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .exhaust import RAKE_ANGLES_DEG, RAKE_AREA_FRACTIONS
from .instruments import uncertainty_table
from .passoff import AMBIENT_TAGS, EXHAUST_TAGS, RESULT_LABELS, RESULTS

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
BLUE, MUTED, FLAG = "#2a78d6", "#b8b7b2", "#d03b3b"


def _style(plt):
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.spines.top": False,
        "axes.spines.right": False, "font.size": 10, "axes.titlesize": 11, "axes.titlecolor": INK,
        "lines.linewidth": 1.5,
    })


DECIMALS = {"power_MW": 3, "heat_rate": 0, "exhaust_temp_C": 1, "exhaust_flow": 2}


def _fmt(v, nd=2):
    return f"{v:,.{nd}f}"


def sensor_figure(data, report, path):
    import matplotlib.pyplot as plt

    _style(plt)
    fig, axes = plt.subplots(2, 1, figsize=(10, 6), constrained_layout=True, sharex=True)
    t = data["time_s"] / 60
    for ax, tags, title, unit in [(axes[0], EXHAUST_TAGS, "Exhaust thermocouples", "°C"),
                                  (axes[1], AMBIENT_TAGS, "Inlet air temperature sensors", "°C")]:
        for tag in tags:
            bad = tag in report.excluded
            for _, g in data.groupby("point"):
                ax.plot(g["time_s"] / 60, g[tag], color=FLAG if bad else MUTED, lw=1.6 if bad else 0.8,
                        zorder=3 if bad else 2)
            if bad:
                g = data[data["point"] == data["point"].max()]
                ax.annotate(f"{tag}: excluded", (g["time_s"].iloc[-1] / 60, g[tag].iloc[-1]), xytext=(6, 0),
                            textcoords="offset points", color=INK2, fontsize=8, va="center")
        ax.set_title(f"{title} (red: excluded by validation)", loc="left")
        ax.set_ylabel(unit)
    axes[1].set_xlabel("Test time (min)")
    axes[1].set_xlim(t.min(), t.max() * 1.12)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def budget_figure(unc, path, top=6):
    import matplotlib.pyplot as plt

    _style(plt)
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8), constrained_layout=True)
    for ax, key in zip(axes, ["power_MW", "heat_rate"]):
        rows = unc.budget[key][:top]
        labels = [r[0].replace(" (systematic)", " (sys)").replace("random, ", "rand, ")
                  .replace("Correction model: ", "Model: ").replace(" scatter", "") for r in rows][::-1]
        share = [100 * r[4] for r in rows][::-1]
        y = np.arange(len(rows))
        ax.barh(y, share, height=0.6, color=BLUE)
        for yi, v in zip(y, share):
            ax.annotate(f"{v:.0f} %", (v, yi), xytext=(4, 0), textcoords="offset points", va="center",
                        color=INK2, fontsize=9)
        ax.set_yticks(y, labels)
        ax.set_xlim(0, 115)
        ax.grid(axis="y", visible=False)
        ax.set_xlabel("Share of variance (%)")
        name, unit = RESULT_LABELS[key]
        ax.set_title(f"{name}: u = {unc.u[key]:.3g} {unit}", loc="left")
    fig.savefig(path, dpi=150)
    plt.close(fig)


def mc_figure(unc, checks, path):
    import matplotlib.pyplot as plt

    _style(plt)
    limits = {c["result"]: c["limit"] for c in checks}
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), constrained_layout=True)
    for ax, key in zip(axes, ["power_MW", "heat_rate"]):
        s = unc.mc[key]
        lo, hi = np.percentile(s, [2.5, 97.5])
        ax.hist(s, bins=60, color=BLUE, alpha=0.85)
        ax.axvspan(lo, hi, color=BLUE, alpha=0.08, lw=0)
        ax.grid(axis="x", visible=False)
        name, unit = RESULT_LABELS[key]
        nd = 3 if abs(lo) < 1000 else 0
        ax.set_title(f"{name} ({unit}), Monte Carlo\n95 % interval {lo:,.{nd}f} to {hi:,.{nd}f}", loc="left")
        ax.set_yticks([])
        if key in limits:
            ax.axvline(limits[key], color=FLAG, lw=1.5)
            ax.annotate("guarantee", (limits[key], ax.get_ylim()[1]), xytext=(4, -12), textcoords="offset points",
                        color=INK2, fontsize=9)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def exhaust_figure(fields, loads, path, titles=None, limit_K=None):
    """Exhaust-plane temperature deviation (K from the mass-weighted mean) with the rake, one row per field.

    `fields` are ExhaustField objects, `loads` the load fractions shown as columns.
    Rake thermocouples are drawn at their angles and immersions; the cold spot of
    each field (the coldest point of its circumferential pattern) is marked.
    """
    import matplotlib.pyplot as plt

    _style(plt)
    titles = titles or [""] * len(fields)
    grids = [[f.grid(L) for L in loads] for f in fields]
    fig, axes = plt.subplots(len(fields), len(loads), figsize=(4.6 * len(loads) + 0.8, 4.3 * len(fields)),
                             subplot_kw={"projection": "polar"}, constrained_layout=True, squeeze=False)
    for row, f, gs, title in zip(axes, fields, grids, titles):
        h = f.model.hub_tip_ratio
        lim = limit_K or max(float(np.max(np.abs(g[2]))) for g in gs)
        for ax, L, (theta, rhat, dev) in zip(row, loads, gs):
            r = h + rhat * (1 - h)
            mesh = ax.pcolormesh(np.radians(theta), r, dev, cmap="RdBu_r", vmin=-lim, vmax=lim, shading="auto",
                                 rasterized=True)
            r_tc = h + f.model.radius_fraction(RAKE_AREA_FRACTIONS) * (1 - h)
            ax.plot(np.radians(RAKE_ANGLES_DEG), r_tc, "o", ms=6, color=INK, mec=SURFACE, mew=1.2, ls="none")
            for k, a in enumerate(RAKE_ANGLES_DEG):
                ax.annotate(f"{k + 1}", (np.radians(a), 1.07), fontsize=7, color=INK2, ha="center", va="center",
                            annotation_clip=False)
            cold = f.cold_spot_deg(L)
            ax.annotate("", xy=(np.radians(cold), h - 0.02), xytext=(np.radians(cold), 0.2),
                        arrowprops={"arrowstyle": "->", "color": INK2, "lw": 1.2})
            ax.set_ylim(0, 1.0)
            ax.set_yticks([])
            ax.set_xticks(np.radians(np.arange(0, 360, 60)), [f"{d}°" for d in range(0, 360, 60)], fontsize=8)
            ax.tick_params(axis="x", pad=8)
            ax.set_theta_zero_location("N")
            ax.set_theta_direction(-1)
            ax.grid(False)
            ax.spines["polar"].set_visible(False)
            ax.set_title(f"{title}\n{100 * L:.0f} % load: swirl {f.model.swirl_deg(L, f.swirl0_deg):.0f}°, "
                         f"cold spot {cold:.0f}° (arrow)", loc="left", fontsize=10)
        cb = fig.colorbar(mesh, ax=list(row), shrink=0.75, pad=0.02)
        cb.set_label("T minus mass-weighted mean (K)")
        cb.outline.set_visible(False)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def write_report(result, metadata, out_dir, truth=None, data=None, scatter_estimates=None, burner_surveys=None,
                 exhaust_figure_path=None):
    """Write report.md and its figures into out_dir. Returns the report path.

    `scatter_estimates` (optional) is a dict of `scatter.ScatterEstimate` from the
    production history, shown with the correction-model terms. `burner_surveys`
    maps a label to an `exhaust.BurnerCheck` from a rake load survey.
    `exhaust_figure_path` (relative to the report) links an exhaust field figure.
    """
    out = Path(out_dir)
    fig_dir = out / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    r, unc = result.report, result.uncertainty
    if data is not None:
        sensor_figure(data, r, fig_dir / "sensors.png")
    budget_figure(unc, fig_dir / "uncertainty_budget.png")
    if unc.mc is not None:
        mc_figure(unc, result.checks, fig_dir / "monte_carlo.png")

    verdicts = [c["verdict"] for c in result.checks]
    overall = "FAIL" if any(v.startswith("FAIL") for v in verdicts) else "PASS"
    if result.warnings:
        overall = "NOT VALID"
    L = [f"# Pass-off test report: {metadata.get('serial', 'engine')}", ""]
    for w in result.warnings:
        L += [f"> **Warning:** {w}", ""]
    date_text = f" Test date: {result.test_date}." if result.test_date else ""
    L += [f"**Overall result: {overall}** against the acceptance limits below, on "
          f"{len(result.points)} test points at base load. Fuel: {metadata.get('fuel', 'n/a')}.{date_text}", ""]
    for n in result.notes:
        L += [f"> Note: {n}", ""]

    L += ["## 1. Corrected results against acceptance limits", "",
          "Corrected to reference conditions (ISO 15 °C, 1013.25 mbar, 60 % RH, no inlet or exhaust losses). "
          "U95 is the expanded uncertainty (k = 2).", "",
          "| Result | Corrected | U95 | Limit | Margin | P(conform) | Verdict |", "|---|---:|---:|---:|---:|---:|---|"]
    for c in result.checks:
        name, unit = RESULT_LABELS[c["result"]]
        nd = DECIMALS[c["result"]]
        L.append(f"| {name} ({unit}) | {_fmt(c['value'], nd)} | ±{_fmt(c['U95'], nd)} | {c['direction']} "
                 f"{_fmt(c['limit'], nd)} | {_fmt(c['margin'], nd)} | {100 * c['probability']:.1f} % "
                 f"| {c['verdict']} |")
    checked = {c["result"] for c in result.checks}
    for key in RESULTS:
        if key not in checked:
            name, unit = RESULT_LABELS[key]
            nd = DECIMALS[key]
            L.append(f"| {name} ({unit}) | {_fmt(unc.value[key], nd)} | ±{_fmt(unc.expanded(key), nd)} | – | – | – | "
                     f"for information |")

    L += ["", "## 2. Test conditions", "",
          "| Point | Samples | Ambient (°C) | Pressure (mbar) | RH (%) | Inlet loss (mbar) | Exhaust loss (mbar) |",
          "|---:|---:|---:|---:|---:|---:|---:|"]
    for p in result.points:
        v = p.values
        L.append(f"| {p.point} | {p.n_samples} | {v['T_amb']:.2f} | {v['P_amb']:.1f} | {v['RH']:.1f} | "
                 f"{v['dP_inlet']:.1f} | {v['dP_exhaust']:.1f} |")

    L += ["", "## 3. Data validation", ""]
    if r.excluded:
        L += ["| Sensor | Excluded because |", "|---|---|"]
        L += [f"| {t} | {why} |" for t, why in r.excluded.items()]
    else:
        L.append("No sensors excluded.")
    if r.spikes:
        L += ["", "Spikes removed: " + ", ".join(f"{t} ({n} samples)" for t, n in r.spikes.items()) + "."]
    unstable = [s for s in r.stability if not s[4]]
    L += ["", "Stability (range of 1-minute averages within each point): "
          + ("all within limits." if not unstable else
             "; ".join(f"point {p} {q} {v:.2f} > {lim}" for p, q, v, lim, _ in unstable))]
    if data is not None:
        L += ["", "![Sensor traces](figures/sensors.png)"]

    L += ["", "## 4. Measured and corrected results by point", "",
          "| Point | Measured power (MW) | Measured heat rate (kJ/kWh) | Power correction | Heat rate correction "
          "| Corrected power (MW) | Corrected heat rate (kJ/kWh) |", "|---:|---:|---:|---:|---:|---:|---:|"]
    for p, c in zip(result.points, result.per_point):
        L.append(f"| {p.point} | {c['measured_power_MW']:.3f} | {c['measured_heat_rate']:,.0f} | "
                 f"{c['power_correction']:.4f} | {c['heat_rate_correction']:.4f} | {c['power_MW']:.3f} | "
                 f"{c['heat_rate']:,.0f} |")
    pw = [c["power_MW"] for c in result.per_point]
    spread = 100 * (max(pw) - min(pw)) / np.mean(pw)
    vs_model = np.mean([c["power_vs_model"] for c in result.per_point])
    L += ["", f"Point-to-point repeatability of corrected power: {spread:.2f} % (max − min). "
          f"This engine produced {100 * (vs_model - 1):+.2f} % power against the nominal model at test conditions."]

    L += ["", "## 5. Uncertainty", "",
          "Systematic (calibration) uncertainties are shared by all points; random uncertainties are the standard "
          "error of each point mean, with an effective sample size for autocorrelation. Sensitivities are found "
          "numerically through the full correction chain, including the engine model.", "",
          "| Result | GUM u | Monte Carlo u | MC 95 % interval |", "|---|---:|---:|---|"]
    for key in RESULTS:
        name, unit = RESULT_LABELS[key]
        mc = unc.mc[key] if unc.mc is not None else None
        mc_u = f"{mc.std(ddof=1):.4g}" if mc is not None else "–"
        nd = DECIMALS[key]
        mc_i = (f"{np.percentile(mc, 2.5):,.{nd}f} to {np.percentile(mc, 97.5):,.{nd}f}" if mc is not None else "–")
        L.append(f"| {name} ({unit}) | {unc.u[key]:.4g} | {mc_u} | {mc_i} |")
    L += ["", "Largest contributors:", "", "![Uncertainty budget](figures/uncertainty_budget.png)"]
    if unc.mc is not None:
        L += ["", "![Monte Carlo distributions](figures/monte_carlo.png)"]
    det = result.u_sys_detail
    prof = det.get("profile")
    k_tc = prof.n_tc if prof is not None else len([t for t in EXHAUST_TAGS if t in r.clean])
    L += ["", f"Exhaust temperature systematic uncertainty combines thermocouple calibration "
          f"({det['T_exh calibration']:.2f} K for the mean of {k_tc} thermocouples) and rake profile sampling "
          f"({det['T_exh profile sampling']:.2f} K). The second is a Monte Carlo over the exhaust field model: "
          "burner fuel split deviations, swirl angle, lobe strength and radial profile drawn from their priors, "
          f"the rake sampled at its {k_tc} valid positions, and the rake mean compared with the mass-weighted mean. "
          f"The expected offset of the rake mean, {det.get('T_exh profile correction', 0.0):+.2f} K, was subtracted "
          "as a correction."]
    if r.burner is not None:
        b = r.burner
        L += ["", f"Burner check (rake, before sensor exclusions): spread {b.spread:.1f} K; coldest adjacent pair "
              f"T_exh_{b.thermocouples[0] + 1:02d}/T_exh_{b.thermocouples[1] + 1:02d} reads {-b.depth:+.1f} K from "
              f"the median; a cold spot needs it below {-b.threshold:.1f} K (99.9th percentile for healthy "
              f"engines from the profile model). {'Cold spot detected.' if b.flagged else 'No cold spot.'}"]

    if burner_surveys:
        L += ["", "Burner diagnostic load survey: the rake is read at several loads and any cold spot is tracked. "
              "A burner cold spot rotates with load by the modelled swirl angle; a sensor fault stays put.", "",
              "| Case | Loads | Cold spot depth / threshold (K) | Cold spot angle | Rotation measured / modelled | "
              "Verdict |", "|---|---|---|---|---|---|"]
        for label, bc in burner_surveys.items():
            loads = ", ".join(f"{100 * s.load:.0f} %" for s in bc.spots)
            depth = ", ".join(f"{s.depth:.0f} / {s.threshold:.0f}" for s in bc.spots)
            ang = ", ".join(f"{s.angle_deg:.0f}°" if s.flagged else "–" for s in bc.spots)
            rot = (f"{bc.rotation_deg:+.0f}° / {bc.modelled_rotation_deg:+.0f}°" if bc.rotation_deg is not None
                   else "–")
            L.append(f"| {label} | {loads} | {depth} | {ang} | {rot} | {bc.verdict} |")
    if exhaust_figure_path:
        L += ["", f"![Exhaust temperature field and rake]({exhaust_figure_path})"]

    if result.instruments and any(getattr(i, "components", ()) for i in result.instruments.values()):
        readings = {t: float(r.clean[t].mean()) for t in r.clean.columns if t in result.instruments}
        rows = uncertainty_table(result.instruments, readings, result.test_date)
        L += ["", "### Instrument uncertainty sources", "",
              "Each calibration uncertainty comes from the instrument's accuracy class or specification, or from "
              "its calibration certificate when one is in date for the test (the reading is then corrected by the "
              "as-found error). Class tolerances are converted to standard uncertainties with the stated "
              "distribution (rectangular: tolerance / √3). u is per channel at the test reading.", "",
              "| Channels | Component | Basis | Tolerance or certificate | Distribution | u (k = 1) | Source |",
              "|---|---|---|---|---|---:|---|"]
        for row in rows:
            tags = row["tags"]
            tag_text = tags[0] if len(tags) == 1 else f"{tags[0]} … {tags[-1]} ({len(tags)})"
            mark = "" if row["verified"] else " (applicability not verified, see note)"
            src = f"[link]({row['source'].replace(' ', '%20')}){mark}" if row["source"] else "none"
            cells = [tag_text, row["component"], row["basis"], row["detail"], row["distribution"],
                     f"{row['u']:.3g} {row['unit']}", src]
            L.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
        unverified = [row for row in rows if not row["verified"]]
        for row in unverified:
            L += ["", f"Note on {row['tags'][0]}: {row['note']}"]
        if result.calibration:
            L += ["", "Calibration status on the test date:", "",
                  "| Channel | Certificate | Calibrated | Due | Status |", "|---|---|---|---|---|"]
            for c in result.calibration:
                L.append(f"| {c['tag']} ({c['component']}) | {c['certificate']} | {c['date']} | {c['due']} | "
                         f"{c['status']} |")
        if result.corrections:
            items = [f"{t} {100 * e:+.3f} %" if result.instruments[t].relative
                     else f"{t} {e:+.3f} {result.instruments[t].unit}" for t, e in result.corrections.items()]
            L += ["", "Certificate corrections applied to the readings: " + ", ".join(items)
                  + " (as-found error subtracted)."]

    sc = result.scatter
    if sc is not None:
        L += ["", "### Correction-model scatter", "",
              f"Build-to-build health scatter used for the correction-model terms: {sc.source}. Compressor flow "
              f"{100 * sc.compressor_flow:.2f} %, compressor efficiency {100 * sc.compressor_eff:.2f} pts, turbine "
              f"efficiency {100 * sc.turbine_eff:.2f} pts, turbine capacity {100 * sc.turbine_capacity:.2f} % "
              "(one standard deviation)."]
        if scatter_estimates:
            L += ["", "| Parameter | Estimate | 95 % interval | Hidden truth (synthetic) |", "|---|---:|---|---:|"]
            for name, e in scatter_estimates.items():
                tv = getattr(e, "truth", None)
                L.append(f"| {name} | {e.value:.3f} | {e.lower:.3f} to {e.upper:.3f} | "
                         f"{'–' if tv is None else f'{tv:.3f}'} |")

    if truth:
        L += ["", "## 6. Check against the known truth (synthetic test only)", "",
              "The data was generated from an engine with hidden health deltas and hidden instrument biases. "
              "A sound method should land within U95 of the truth about 95 % of the time.", "",
              "| Result | True | Corrected | Error | Error / U95 |", "|---|---:|---:|---:|---:|"]
        for key in RESULTS:
            name, unit = RESULT_LABELS[key]
            err = unc.value[key] - truth[key]
            nd = DECIMALS[key]
            L.append(f"| {name} ({unit}) | {_fmt(truth[key], nd)} | {_fmt(unc.value[key], nd)} | {err:+.{nd}f} | "
                     f"{err / unc.expanded(key):+.2f} |")

    L += ["", "## Method notes", "",
          "- Correction by the model-ratio method: corrected = measured × model(reference) / model(test). "
          "Exhaust temperature is corrected by difference.",
          "- Exhaust flow comes from a heat balance on fuel heat input, electrical output and modelled "
          "mechanical and generator losses, then corrected the same way.",
          "- Acceptance follows a guarded-band reading: PASS needs the margin to exceed U95; a smaller "
          "positive margin is reported as PASS (within uncertainty).",
          "- Correction-model uncertainty: the correction curves describe the nominal engine, so the build-to-build "
          "scatter of component health (estimated from production pass-off history when available) is propagated "
          "through the correction. This term grows with the distance from reference conditions.",
          "- Instrument uncertainties come from accuracy classes, manufacturer specifications and calibration "
          "certificates listed above. The certificates for this synthetic test bed are illustrative records.",
          "- The exhaust rake reads a modelled exhaust field (burner streaks rotated by swirl and smeared by mixing, "
          "plus a radial profile). Its under-sampling uncertainty is a Monte Carlo over that model's priors."]
    path = out / "report.md"
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    return path
