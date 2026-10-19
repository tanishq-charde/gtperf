"""Synthetic production pass-off test, analysed end to end.

Run from the repository root:  python examples/passoff_test.py
Writes the raw test log, report and figures to docs/passoff/, and the exhaust
field figure to docs/figures/exhaust_profile.png.

The chain:
1. A test log is generated for an engine with hidden health deltas, hidden
   instrument errors (drawn from each instrument's class tolerance or
   certificate) and a hidden exhaust field (burner fuel split deviations, swirl).
2. A first analysis gives the pass-off measurement uncertainty of each gas-path
   measurement, from the instrument list.
3. A production history of 200 engines (hidden build scatter) is analysed to
   estimate the build-to-build scatter, subtracting that measurement variance.
4. The pass-off is analysed again with the estimated scatter for the
   correction-model uncertainty, and the report is written.
"""

from dataclasses import replace
from pathlib import Path

import numpy as np

from gtperf.cli import engine_from_datasheet
from gtperf.exhaust import BurnerFault, burner_check, rake_survey
from gtperf.fleet import BUILD_SCATTER, production_sequence
from gtperf.gpa import HEALTH, HEALTH_LABELS, MEASUREMENTS, influence_matrix
from gtperf.maps import Health
from gtperf.passoff import Guarantee, analyse
from gtperf.report import exhaust_figure, write_report
from gtperf.scatter import estimate_health_scatter, estimate_scatter, passoff_measurement_noise, production_scatter
from gtperf.synthetic import Fault, TestPoint, generate_test

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "passoff"
FIGURES = ROOT / "docs" / "figures"

# Acceptance limits for a production engine: nominal ISO rating minus/plus a production tolerance.
GUARANTEE = Guarantee(power_MW_min=12.65, heat_rate_max=10500.0, exhaust_temp_C_max=570.0)


def estimate_production_scatter(engine, noise, n=200, seed=7):
    """Build scatter from a synthetic production history whose hidden truth is fleet.BUILD_SCATTER."""
    prod = production_sequence(engine, n=n, events=[], noise=noise, seed=seed)
    H = influence_matrix(engine)
    est = estimate_health_scatter(prod[[f"d_{m}" for m in MEASUREMENTS]].to_numpy(), H, noise)
    for h, truth in zip(HEALTH, BUILD_SCATTER):
        est[h].truth = truth
    power = estimate_scatter(prod["power_MW"], noise[0] / 100 * prod["power_MW"].mean(), name="power")
    power.truth = float(np.sqrt(np.sum((H[0] * BUILD_SCATTER) ** 2)) / 100 * prod["power_MW"].mean())
    labelled = {HEALTH_LABELS[h]: est[h] for h in HEALTH}
    labelled["Corrected power (MW)"] = power
    return production_scatter(est), labelled


if __name__ == "__main__":
    _, _, engine = engine_from_datasheet(ROOT / "examples" / "sgt400_13mw.toml")

    # The engine under test is slightly worse than nominal; the analyst does not know by how much.
    hidden_health = Health(compressor_flow_scale=0.994, compressor_eff_delta=-0.004)
    test = generate_test(
        engine,
        points=[TestPoint(8.0, 1002.0, 0.78, drift_K=0.4), TestPoint(8.6, 1002.2, 0.76, drift_K=0.3),
                TestPoint(9.1, 1002.3, 0.74, drift_K=0.2)],
        health=hidden_health,
        faults=[Fault("T_exh_07", "drift", 15.0), Fault("T_exh_03", "offset", 30.0),
                Fault("T_amb_3", "stuck"), Fault("P_elec", "spike", 300.0)],
        seed=4,
        serial="ESN-0412",
    )
    OUT.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)
    test.data.to_csv(OUT / "ESN-0412_test_log.csv", index=False, float_format="%.5f")
    inst, date = test.metadata["instruments"], test.metadata["test_date"]

    first = analyse(test.data, engine, inst, GUARANTEE, mc_trials=0, test_date=date)
    noise = passoff_measurement_noise(first)
    scatter, estimates = estimate_production_scatter(engine, noise)
    print("pass-off measurement noise:", dict(zip(MEASUREMENTS, np.round(noise, 3))))
    for name, e in estimates.items():
        print(f"  {name:<34} {e.value:.3f} [{e.lower:.3f}, {e.upper:.3f}]  truth {e.truth:.3f}")

    result = analyse(test.data, engine, inst, GUARANTEE, mc_trials=5000, test_date=date, scatter=scatter)

    # Exhaust field of the engine under test, and the same engine with a lean burner, at two loads
    field = test.truth["exhaust_field"]
    lean = replace(field, fault=BurnerFault(2, "lean", 0.7))
    lean.deviations = field.deviations.copy()
    lean.deviations[1] += lean.fault.delta
    lean.deviations -= lean.deviations.mean()
    loads = [0.5, 1.0]
    exhaust_figure([field, lean], loads, FIGURES / "exhaust_profile.png",
                   titles=["ESN-0412 as tested", "Same engine, burner 2 at 70 % fuel"])
    t_exh = test.truth["exhaust_temp_C"]
    surveys = {
        "ESN-0412": burner_check(rake_survey(field, loads, t_exh, seed=1).readings),
        "Burner 2 lean (70 % fuel)": burner_check(rake_survey(lean, loads, t_exh, seed=2).readings),
    }
    for label, bc in surveys.items():
        print(f"  burner check, {label}: {bc.verdict}")

    path = write_report(result, test.metadata, OUT, truth=test.truth, data=test.data, scatter_estimates=estimates,
                        burner_surveys=surveys, exhaust_figure_path="../figures/exhaust_profile.png")
    print(f"wrote {path.relative_to(ROOT)}")
    for c in result.checks:
        print(f"  {c['result']:<16} {c['value']:>10.3f}  ±{c['U95']:.3f}  {c['verdict']}")
    print(f"  exhaust profile sampling u = {result.u_sys_detail['T_exh profile sampling']:.2f} K "
          f"(correction {result.u_sys_detail['T_exh profile correction']:+.2f} K)")
