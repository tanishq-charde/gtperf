import json
from pathlib import Path

import numpy as np

from gtperf.cli import main
from gtperf.study import sweep
from test_cycle import make_cycle

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "engine_13mw.toml"


def test_cli_json(capsys):
    main(["design", str(EXAMPLE), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert 10 < out["electrical_power_MW"] < 16
    assert 30 < out["thermal_efficiency_pct"] < 40


def test_sweep_shapes_and_does_not_mutate_input():
    cycle = make_cycle(pr=16.0, tit=1500.0)
    res = sweep(cycle, np.array([8.0, 16.0, 24.0]), [1400.0, 1500.0])
    assert res["efficiency"].shape == (2, 3)
    assert cycle.compressor.pressure_ratio == 16.0
    assert cycle.combustor.t_out == 1500.0


DATASHEET = EXAMPLE.parent / "sgt400_13mw.toml"
SITE = EXAMPLE.parent / "site_hot_desert.toml"


def test_cli_calibrate(capsys):
    main(["calibrate", str(DATASHEET)])
    out = capsys.readouterr().out
    assert "Heat rate" in out and "Fitted" in out


def test_cli_site_csv(capsys):
    main(["site", str(DATASHEET), str(SITE), "--csv"])
    rows = capsys.readouterr().out.strip().splitlines()
    assert rows[0] == "parameter,unit,iso,site"
    assert len(rows) == 8


def test_cli_curves(capsys):
    main(["curves", str(DATASHEET), "--from-C", "0", "--to-C", "30", "--step", "10"])
    rows = capsys.readouterr().out.strip().splitlines()
    assert len(rows) == 5
