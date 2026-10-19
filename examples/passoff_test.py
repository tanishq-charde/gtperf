"""Synthetic production pass-off test, analysed end to end.

Run from the repository root:  python examples/passoff_test.py
Writes the raw test log, report and figures to docs/passoff/.
"""

from pathlib import Path

from gtperf.cli import engine_from_datasheet
from gtperf.maps import Health
from gtperf.passoff import Guarantee, analyse
from gtperf.report import write_report
from gtperf.synthetic import Fault, TestPoint, generate_test

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "passoff"

# Acceptance limits for a production engine: nominal ISO rating minus/plus a production tolerance.
GUARANTEE = Guarantee(power_MW_min=12.65, heat_rate_max=10500.0, exhaust_temp_C_max=570.0)

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
    test.data.to_csv(OUT / "ESN-0412_test_log.csv", index=False, float_format="%.5f")

    result = analyse(test.data, engine, test.metadata["instruments"], GUARANTEE, mc_trials=5000)
    path = write_report(result, test.metadata, OUT, truth=test.truth, data=test.data)
    print(f"wrote {path.relative_to(ROOT)}")
    for c in result.checks:
        print(f"  {c['result']:<16} {c['value']:>10.3f}  ±{c['U95']:.3f}  {c['verdict']}")
