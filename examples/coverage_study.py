"""Does the stated uncertainty mean what it says?

Repeats the synthetic pass-off test with fresh instrument errors (drawn from each
instrument's class tolerance or certificate), noise, exhaust field (burner fuel
splits, swirl, radial profile) and build-to-build health each time, and counts
how often the truth falls inside the corrected result's 95 % interval (GUM,
k = 2). A well-calibrated method should cover the truth about 95 % of the time.

The engines are drawn from the production population (fleet.BUILD_SCATTER, the
generator's hidden truth). The analysis does not know it: the correction-model
scatter is estimated once from a 200-engine production history, as in
examples/passoff_test.py.

Run from the repository root:  python examples/coverage_study.py [n_tests]
"""

import sys
from pathlib import Path

import numpy as np
from passoff_test import estimate_production_scatter

from gtperf.cli import engine_from_datasheet
from gtperf.fleet import BUILD_SCATTER
from gtperf.gpa import health_from_vector
from gtperf.passoff import RESULTS, analyse
from gtperf.scatter import passoff_measurement_noise
from gtperf.synthetic import TestPoint, generate_test

ROOT = Path(__file__).resolve().parents[1]


def estimated_scatter(engine):
    ref = generate_test(engine, [TestPoint(10.0, 1010.0, 0.6, 0.3)] * 3, seed=1, duration_s=300)
    res = analyse(ref.data, engine, ref.metadata["instruments"], mc_trials=0, test_date=ref.metadata["test_date"])
    return estimate_production_scatter(engine, passoff_measurement_noise(res))[0]


def coverage(engine, n, seed0=100, scatter=None):
    hits = {r: 0 for r in RESULTS}
    z = {r: [] for r in RESULTS}
    valid = flagged = 0
    for i in range(n):
        rng = np.random.default_rng(seed0 + i)
        health = health_from_vector(rng.normal(0, BUILD_SCATTER))
        t = rng.uniform(-5, 30)
        pts = [TestPoint(t + 0.5 * k, rng.uniform(990, 1025), rng.uniform(0.3, 0.9), 0.3) for k in range(3)]
        test = generate_test(engine, pts, health=health, seed=seed0 + i, duration_s=300)
        res = analyse(test.data, engine, test.metadata["instruments"], mc_trials=0, scatter=scatter,
                      test_date=test.metadata["test_date"])
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
    scatter = estimated_scatter(engine)
    print(f"correction-model scatter: {scatter}")
    cov, z, valid, flagged = coverage(engine, n, scatter=scatter)
    print(f"{valid} valid tests, {flagged} flagged as invalid (different control mode from reference)")
    for r in RESULTS:
        print(f"{r:<16} coverage {100 * cov[r]:5.1f} %   std of normalised error {np.std(z[r]):.2f}")
