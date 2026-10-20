"""Build the GTPerf web tool into one HTML page: the page, its inputs, the baked starting results, the
calculation script and a gtperf wheel built from this checkout, so the page runs the real package in the
browser through Pyodide.

    python3 web/bake.py                       # after changing the package or the inputs
    python3 web/build.py web/dist/index.html
"""

import base64
import json
import subprocess
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent
ROOT = WEB.parent


def wheel():
    out = WEB / "wheel"
    for old in out.glob("gtperf-*.whl"):
        old.unlink()
    subprocess.run([sys.executable, "-m", "pip", "wheel", "-q", "--no-deps", "-w", str(out), str(ROOT)], check=True)
    return next(out.glob("gtperf-*.whl")).read_bytes()


def main(target):
    safe = lambda s: s.replace("</", "<\\/")  # noqa: E731  keeps embedded text from closing the script tag
    logo = base64.b64encode((ROOT / "src/gtperf/app/assets/logo.png").read_bytes()).decode()
    html = (WEB / "head.html").read_text().replace("__LOGO__", "data:image/png;base64," + logo)
    js = (WEB / "script.html").read_text()
    js = js.replace("__SPEC__", safe((WEB / "spec.json").read_text()))
    js = js.replace("__BAKED__", safe((WEB / "baked.json").read_text()))
    js = js.replace("__WHEEL__", base64.b64encode(wheel()).decode())
    js = js.replace("__COMPUTE__", safe(json.dumps((WEB / "compute.py").read_text())))
    out = Path(target)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html + js)
    print(out, len(html + js) // 1024, "KB")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else WEB / "dist" / "index.html")
