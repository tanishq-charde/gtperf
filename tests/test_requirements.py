import ast
from pathlib import Path

from gtperf.requirements import BEGIN, END, METHODS, load, render_into

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = load(ROOT / "docs" / "requirements.toml")
NOTE = ROOT / "docs" / "requirements_and_certification.md"


def defined_tests():
    names = set()
    for path in (ROOT / "tests").glob("test_*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                names.add(node.name)
    return names


def test_requirements_are_well_formed():
    ids = [r["id"] for r in REQUIREMENTS]
    assert len(ids) == len(set(ids))
    for r in REQUIREMENTS:
        assert r["method"] in METHODS
        assert r["text"] and r["acceptance"] and r["evidence"] and r["source"]
        assert r["tests"] or r["method"] != "T", f"{r['id']} is verified by test but names none"


def test_every_traced_test_exists():
    missing = {t for r in REQUIREMENTS for t in r["tests"]} - defined_tests()
    assert not missing


def test_traceability_table_is_current():
    text = NOTE.read_text(encoding="utf-8")
    assert BEGIN in text and END in text
    assert render_into(text, REQUIREMENTS) == text, "run: python -m gtperf.requirements"
