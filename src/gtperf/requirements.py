"""Requirements traceability: load docs/requirements.toml and render it as a markdown table."""

from __future__ import annotations

import tomllib

METHODS = {"T": "Test", "A": "Analysis", "I": "Inspection", "D": "Demonstration"}
BEGIN, END = "<!-- requirements table: generated from docs/requirements.toml -->", "<!-- end requirements table -->"


def load(path):
    with open(path, "rb") as f:
        return tomllib.load(f)["requirement"]


def table(requirements):
    rows = ["| ID | Requirement | Verification | Acceptance | Evidence |", "|---|---|---|---|---|"]
    for r in requirements:
        tests = ", ".join(f"`{t}`" for t in r["tests"])
        evidence = r["evidence"] + (f"; {tests}" if tests else "")
        rows.append(f"| {r['id']} | {r['text']} | {METHODS[r['method']]} | {r['acceptance']} | {evidence} |")
    return "\n".join(rows)


def render_into(markdown, requirements):
    """Replace the generated table between the markers in a markdown document."""
    start, end = markdown.index(BEGIN) + len(BEGIN), markdown.index(END)
    return markdown[:start] + "\n" + table(requirements) + "\n" + markdown[end:]


def main(toml="docs/requirements.toml", markdown="docs/requirements_and_certification.md"):
    with open(markdown, encoding="utf-8") as f:
        text = f.read()
    with open(markdown, "w", encoding="utf-8") as f:
        f.write(render_into(text, load(toml)))


if __name__ == "__main__":
    main()
