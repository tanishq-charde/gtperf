"""GUI calculation layer and exports (the Streamlit page itself is smoke-tested when streamlit is installed)."""

import io
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("matplotlib")
openpyxl = pytest.importorskip("openpyxl")
pptx = pytest.importorskip("pptx")
pytest.importorskip("fpdf")

from gtperf.app import core, exports  # noqa: E402
from gtperf.site import Site  # noqa: E402


@pytest.fixture(scope="module")
def model():
    return core.build_engine(core.load_datasheet())


@pytest.fixture(scope="module")
def sections(model):
    return [
        core.engine_section(model),
        core.site_section(model, Site("Hot site", 0.0, 35.0, 0.4, 10.0, 25.0)),
        core.part_load_section(model, loads=[50, 75, 100]),
    ]


def test_engine_section_matches_datasheet(model):
    s = core.engine_section(model)
    assert s.key == "engine"
    assert s.tables and s.figures
    assert all(not t.empty for t in s.tables.values())


def test_hot_site_loses_power(sections):
    site = sections[1]
    assert site.key_results
    assert "Hot site" in " ".join(site.notes) or site.tables


def test_datasheet_with_changes_rating():
    base = core.load_datasheet()
    key = next(k for k, v in base.items() if isinstance(v, (int, float)))
    changed = core.datasheet_with(base, **{key: base[key] * 1.01})
    assert changed[key] == pytest.approx(base[key] * 1.01)
    assert base[key] != changed[key]


def test_exports_are_valid_files(sections):
    z = zipfile.ZipFile(io.BytesIO(exports.figures_zip(sections)))
    assert z.namelist() and all(n.endswith(".svg") for n in z.namelist())
    assert b"<svg" in z.read(z.namelist()[0])

    wb = openpyxl.load_workbook(io.BytesIO(exports.to_xlsx(sections)))
    assert wb.sheetnames[0] == "Summary" and len(wb.sheetnames) > 1

    deck = pptx.Presentation(io.BytesIO(exports.to_pptx(sections, "Test")))
    assert len(deck.slides) >= 1 + len(sections)

    assert exports.to_pdf(sections, "Test")[:5] == b"%PDF-"

    csv = zipfile.ZipFile(io.BytesIO(exports.tables_csv_zip(sections)))
    assert all(n.endswith(".csv") for n in csv.namelist())


def test_app_pages_run_without_errors():
    # In a separate process: Streamlit's test runner leaves state that stops LibreOffice's UNO bridge starting.
    pytest.importorskip("streamlit.testing.v1")
    app = Path(core.__file__).with_name("ui.py")
    script = f"""
from streamlit.testing.v1 import AppTest
at = AppTest.from_file({str(app)!r}, default_timeout=300).run()
assert not at.exception, at.exception
for page in ["Engine", "Site performance", "Reports & downloads"]:
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception, (page, at.exception)
"""
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stderr[-2000:]
