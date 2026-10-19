import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

MATLAB = Path(__file__).resolve().parents[1] / "matlab"


def _make_reference():
    spec = importlib.util.spec_from_file_location("make_reference", MATLAB / "make_reference.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_matlab_inputs_are_current():
    """The species table and reference values in matlab/ match what the Python package produces now."""
    ref = _make_reference()
    assert (MATLAB / "gt_species_data.m").read_text() == ref.species_m()
    assert (MATLAB / "reference_values.csv").read_text() == ref.reference_csv()


@pytest.mark.skipif(not shutil.which("octave"), reason="GNU Octave not installed")
def test_matlab_port_matches_python():
    out = subprocess.run(["octave", "--no-gui", "--quiet", "--eval", "run_tests"], cwd=MATLAB, capture_output=True,
                         text=True, timeout=300)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "All MATLAB checks passed" in out.stdout
