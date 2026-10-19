"""Extract the public NPSS component maps shipped with NASA's pyCycle into gtperf's JSON map format.

Usage: python tools/extract_npss_maps.py <path to an unpacked om-pycycle wheel>

The maps are the high-bypass turbofan (HBTF) example maps from NPSS, distributed with pyCycle
under the Apache License 2.0 (see src/gtperf/data/maps/NOTICE). Only the first alpha slice, the one each
map's design point is defined on, is kept; the files are read as data and never executed.
"""

import ast
import json
import sys
import types
from pathlib import Path

import numpy as np

MAPS = {
    "hpc": ("HPC_map.py", "HPCMap", "compressor"),
    "lpc": ("LPC_map.py", "LPCMap", "compressor"),
    "fan": ("Fan_map.py", "FanMap", "compressor"),
    "hpt": ("HPT_map.py", "HPTMap", "turbine"),
    "lpt": ("LPT_map.py", "LPTMap", "turbine"),
}
OUT = Path(__file__).resolve().parents[1] / "src" / "gtperf" / "data" / "maps"


def load(path, name):
    """Read the map's attribute assignments without executing the file: every value is a literal."""
    m = types.SimpleNamespace(defaults={})
    for node in ast.parse(path.read_text()).body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        t = node.targets[0]
        value = node.value
        if isinstance(value, ast.Call) and getattr(value.func, "attr", "") == "array":
            value = value.args[0]
        try:
            literal = ast.literal_eval(value)
        except ValueError:
            continue
        if isinstance(t, ast.Attribute) and getattr(t.value, "id", "") == name:
            setattr(m, t.attr, np.asarray(literal) if isinstance(literal, list) else literal)
        elif isinstance(t, ast.Subscript) and getattr(t.value, "attr", "") == "defaults":
            m.defaults[ast.literal_eval(t.slice)] = literal
    return m


def main(root):
    maps = Path(root) / "pycycle" / "maps"
    for key, (fname, name, kind) in MAPS.items():
        m = load(maps / fname, name)
        d = {k: (v.item() if isinstance(v, np.generic) else v) for k, v in m.defaults.items()}
        if kind == "compressor":
            data = {"kind": kind, "source": f"NPSS HBTF {key.upper()} map via pyCycle {fname}",
                    "design": {"Nc": d["NcMap"], "Rline": d["RlineMap"], "PR": d.get("PR", d.get("PRmap"))},
                    "Nc": m.NcMap.tolist(), "Rline": m.RlineMap.tolist(),
                    "Wc": np.asarray(m.WcMap)[0].tolist(), "PR": np.asarray(m.PRmap)[0].tolist(),
                    "eff": np.asarray(m.effMap)[0].tolist(), "Rline_stall": m.RlineStall}
        else:
            data = {"kind": kind, "source": f"NPSS HBTF {key.upper()} map via pyCycle {fname}",
                    "design": {"Np": d["NpMap"], "PR": d["PRmap"]},
                    "Np": m.NpMap.tolist(), "PR": m.PRmap.tolist(),
                    "Wp": np.asarray(m.WpMap)[0].tolist(), "eff": np.asarray(m.effMap)[0].tolist()}
        (OUT / f"{key}.json").write_text(json.dumps(data, indent=1))
        print(key, np.asarray(data.get("Wc", data.get("Wp"))).shape)


if __name__ == "__main__":
    main(sys.argv[1])
