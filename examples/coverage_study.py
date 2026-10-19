"""Does the stated uncertainty mean what it says?

Repeats the synthetic pass-off test with fresh instrument biases, noise and
build-to-build health each time, and counts how often the truth falls inside
the corrected result's 95 % interval (GUM, k = 2). A well-calibrated method
should cover the truth about 95 % of the time.

Run from the repository root:  python examples/coverage_study.py [n_tests]
"""

import sys
from pathlib import Path

import numpy as np

from gtperf.cli import engine_from_datasheet
from gtperf.maps import Health
from gtperf.passoff import RESULTS, analyse
from gtperf.synthetic import TestPoint, generate_test

ROOT = Path(__file__).resolve().parents[1]


def coverage(engine, n, seed0=100):
    hits = {r: 0 for r in RESULTS}
    z = {r: [] for r in RESULTS}
    valid = flagged = 0
    for i in range(n):
        rng = np.random.default_rng(seed0 + i)
        health = Health(1 + rng.normal(0, 0.005), rng.normal(0, 0.004), rng.normal(0, 0.003))
        t = rng.uniform(-5, 30)
        pts = [TestPoint(t + 0.5 * k, rng.uniform(990, 1025), rng.uniform(0.3, 0.9), 0.3) for k in range(3)]
        test = generate_test(engine, pts, health=health, seed=seed0 + i, duration_s=300)
        res = analyse(test.data, engine, test.metadata["instruments"], mc_trials=0)
        if not res.valid:
            flagged += 1  # the method itself refuses these (e.g. cold day in speed control)
            continue
        valid += 1
        for r in RESULTS:
            err = res.uncertainty.value[r] - test.truth[r]
            z[r].append(err / res.uncertainty.u[r])
            hits[r] += abs(err) <= res.uncertainty.expanded(r)
    return {r: hits[r] / valid for r in RESULTS}, z, valid, flagged


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    _, _, engine = engine_from_datasheet(ROOT / "examples" / "sgt400_13mw.toml")
    cov, z, valid, flagged = coverage(engine, n)
    print(f"{valid} valid tests, {flagged} flagged as invalid (different control mode from reference)")
    for r in RESULTS:
        print(f"{r:<16} coverage {100 * cov[r]:5.1f} %   std of normalised error {np.std(z[r]):.2f}")
