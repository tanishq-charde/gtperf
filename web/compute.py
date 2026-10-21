"""Runs one GTPerf study from the design page's inputs and returns it as JSON (key results, notes, tables,
charts as SVG). The same file runs natively (to bake the starting results into the page) and in Pyodide."""

import base64
import io
import json
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from gtperf.app import core  # noqa: E402
from gtperf.maps import Health  # noqa: E402
from gtperf.site import Site  # noqa: E402

STATE = {"design": None, "model": None}


def model(design):
    if STATE["model"] is None or STATE["design"] != design:
        STATE["model"], STATE["design"] = core.design_engine(design), dict(design)
    return STATE["model"]


def _clean(v):
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and math.isnan(v):
        return None
    return v if isinstance(v, (int, float, str, bool, type(None))) else str(v)


def letter(fig):
    """Label the panels of a multi-panel figure (a), (b), (c), as the captions refer to them."""
    axes = [a for a in fig.axes if a.get_label() != "<colorbar>"]
    if len(axes) < 2:
        return
    titled = all(ax.get_title() for ax in axes)  # side-by-side panels with their own titles
    for i, ax in enumerate(axes):
        tag = f"({chr(97 + i)})"
        if titled:
            ax.set_title(f"{tag} {ax.get_title()}", fontsize=ax.title.get_fontsize())
        else:  # stacked panels under one title: tag each at its top left
            ax.set_title(tag, loc="left", fontsize=10)


def pack(s):
    figs = {}
    for name, f in s.figures.items():
        letter(f)
        b = io.StringIO()
        f.savefig(b, format="svg", bbox_inches="tight")
        plt.close(f)
        txt = b.getvalue()
        figs[name] = txt[txt.find("<svg"):]
    tabs = {
        n: {"cols": [str(c) for c in df.columns],
            "rows": [[_clean(v) for v in r] for r in df.head(60).itertuples(index=False)]}
        for n, df in s.tables.items()
    }
    return {
        "title": s.title,
        "inputs": {k: _clean(v) for k, v in s.inputs.items()},
        "key": [[a, str(b)] for a, b in s.key_results],
        "notes": list(s.notes),
        "tables": tabs,
        "figures": figs,
    }


def section(study, p, design):
    if study == "engine":
        return core.design_section(model(design))
    if study == "site":
        site = Site(p["name"], p["alt"], p["t"], p["rh"] / 100, p["dpi"], p["dpe"])
        health = Health(
            compressor_flow_scale=1 + p["wc"] / 100,
            compressor_eff_delta=p["ec"] / 100,
            turbine_eff_delta=p["et"] / 100,
        )
        lo, hi = p["lo"], p["hi"]
        return core.site_section(model(design), site, health, (min(lo, hi - 5), hi), p["curves"] or None)
    if study == "partload":
        loads = [x / 100 for x in range(100, int(p["lo"]) - 1, -int(p["step"]))]
        return core.part_load_section(model(design), p["t"], p["rh"] / 100, loads, p["staging"] == "On",
                                      p["nox"], p["co"])
    if study == "passoff":
        return core.passoff_section(
            model(design), p["t"], p["p"], p["rh"] / 100, int(p["n"]), p["faults"], p["pmin"], p["hmax"], p["tmax"],
            p["wc"], p["ec"], int(p["seed"]), 300, int(p["mc"]),
        )
    if study == "fleet":
        n, days = int(p["n"]), int(p["days"])
        faults = core.default_fleet_faults(n, days)
        chosen = [f for f in core.FLEET_FAULT_TYPES if f in p["faults"]]
        faults = pd.DataFrame(
            [{"Engine": 1 + (2 * i) % n, "Day": int(days * (0.4 + 0.1 * i)), "Fault": f} for i, f in enumerate(chosen)]
        ) if chosen != list(faults["Fault"]) else faults
        dispatch = "cycling" if p["dispatch"].startswith("Cycling") else "base"
        plot = min(int(p["plot"]), n)
        return core.fleet_section(model(design), n, days, int(p["interval"]), dispatch, faults, int(p["seed"]), plot)
    if study == "turbofan":
        off = p["lapse"] == "Calculate"
        return core.turbofan_section(
            p["bpr"], p["fpr"], p["fcpr"], p["hpc"], p["tet"], p["thr"], p["alt"], p["mach"], p["to_tet"],
            off, off and p["env"] == "Calculate", {k: p[k] for k in core.TURBOFAN_STAGE_DEFAULTS if k in p},
        )
    raise ValueError(study)


def run(study, params_json, design_json):
    try:
        s = section(study, json.loads(params_json), json.loads(design_json))
        return json.dumps({"ok": True, "section": pack(s)})
    except Exception as e:  # shown on the page: a design far from sensible may not converge
        return json.dumps({"ok": False, "error": explain(e)})


def map_xlsx(study, params_json, design_json):
    """A study's calculated maps as an Excel workbook of live formulas (base64), for the download."""
    try:
        from gtperf.mapxlsx import engine_map_workbook, turbofan_map_workbook

        if study == "turbofan":
            p = json.loads(params_json)
            stages = {k: p[k] for k in core.TURBOFAN_STAGE_DEFAULTS if k in p}
            engine = core.turbofan_engine(p["bpr"], p["fpr"], p["fcpr"], p["hpc"], p["tet"], p["thr"], p["alt"],
                                          p["mach"], stages)
            data = turbofan_map_workbook(engine, stages, take_off_tet=p["to_tet"])
        else:
            data = engine_map_workbook(model(json.loads(design_json)))
        return json.dumps({"ok": True, "b64": base64.b64encode(data).decode()})
    except Exception as e:
        return json.dumps({"ok": False, "error": explain(e)})


SOLVER = ("did not converge", "match failed", "f(a) and f(b)", "not making good progress", "math domain")


def explain(e):
    """The reason in plain words: the model's own messages as they are, solver failures as what they mean."""
    msg = str(e)
    if any(s in msg for s in SOLVER) or isinstance(e, (ZeroDivisionError, OverflowError, IndexError, KeyError)):
        return ("The engine model could not find a matched running point for these values (the solver stopped "
                f"with: {msg.splitlines()[0][:80]})")
    return msg[:1].upper() + msg[1:]
