"""The web tool's calculation layer (gtperf.app.core) and the script the page runs in the browser (web/compute.py)."""

import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("matplotlib")

from gtperf.app import core  # noqa: E402
from gtperf.site import Site  # noqa: E402

WEB = Path(__file__).resolve().parents[1] / "web"


@pytest.fixture(scope="module")
def compute():
    spec = importlib.util.spec_from_file_location("web_compute", WEB / "compute.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def model():
    return core.design_engine()


def inputs(study, **changes):
    spec = json.loads((WEB / "spec.json").read_text())
    p = {}
    for _, _, fields in spec[study]["groups"]:
        for f in fields:
            p[f[0]] = f[3][: f[4]] if f[2] == "chips" else (f[4] if f[2] == "sel" else f[3])
    return {**p, **changes}


def run(compute, study, design=None, **changes):
    return json.loads(compute.run(study, json.dumps(inputs(study, **changes)), json.dumps(design or {})))


def test_starting_design_reproduces_the_published_rating(model):
    iso = model.engine.run()
    assert iso.electrical_power / 1e6 == pytest.approx(12.9, rel=0.005)
    assert iso.heat_rate == pytest.approx(10294, rel=0.005)


def test_design_section_has_calculated_maps(model):
    s = core.design_section(model)
    assert {"Compressor map", "HP turbine map", "Power turbine map"} <= set(s.figures)
    assert all(not t.empty for t in s.tables.values())


def test_hot_site_loses_power(model):
    s = core.site_section(model, Site("Hot site", 0.0, 35.0, 0.4, 10.0, 25.0))
    output = dict(s.key_results)["Site output"]
    assert output.startswith("10.") and "-" in output


def test_page_script_runs_and_labels_panels(compute):
    r = run(compute, "partload")
    assert r["ok"]
    svg = r["section"]["figures"]["Part-load performance"]
    assert "(a)" in svg and "(b)" in svg


def test_edited_design_still_runs_site(compute):
    design = {**core.DESIGN_DEFAULTS, "tit_C": 1536.0}
    r = run(compute, "site", design, lo=-40, hi=60)
    assert r["ok"], r.get("error")


def test_impossible_input_gets_a_plain_message(compute):
    r = run(compute, "engine", {**core.DESIGN_DEFAULTS, "turbine_eff": 1.04})
    assert not r["ok"] and r["error"] == "Turbine efficiency is 1.04: an efficiency has to lie between 0 and 1"
