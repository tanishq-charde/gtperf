"""Bake the starting results into baked.json, keyed exactly as the page builds its cache keys, so the page
shows them before the calculator has loaded."""

import json
import sys
import time
from pathlib import Path

WEB = Path(__file__).resolve().parent
sys.path.insert(0, str(WEB))
import compute  # noqa: E402


def dump(o):
    return json.dumps(o, separators=(",", ":"), ensure_ascii=False)


def defaults(spec, study):
    out = {}
    for _, _, fields in spec[study]["groups"]:
        for f in fields:
            key, kind, a = f[0], f[2], f[3]
            out[key] = a[: f[4]] if kind == "chips" else (f[4] if kind == "sel" else a)
    return out


def main():
    spec = json.loads((WEB / "spec.json").read_text())
    design = defaults(spec, "engine")
    baked = {}
    for study in spec:
        p = defaults(spec, study)
        des = {} if study == "turbofan" else design
        t = time.time()
        r = json.loads(compute.run(study, dump(p), dump(des)))
        assert r["ok"], r
        baked[dump([study, p, des])] = r["section"]
        print(study, round(time.time() - t, 1), "s", r["section"]["key"][:3])
    (WEB / "baked.json").write_text(dump(baked))


if __name__ == "__main__":
    main()
