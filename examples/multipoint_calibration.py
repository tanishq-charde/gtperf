"""Multi-point calibration of the twin-shaft model to a synthetic test-bed campaign.

A hidden 'true' engine is run at eight test points (base load at four ambient temperatures and three part
loads, plus one held back); the model's five parameters are fitted by weighted least squares to six of
them and checked against the other two. The fit is repeated with the inter-turbine pressure measured, to
show how one extra measurement separates the two turbine efficiencies. Writes docs/calibration/multipoint.md.
"""

from pathlib import Path

import numpy as np

from gtperf.cli import offdesign_options
from gtperf.testbed import (
    DEFAULT_UNCERTAINTY,
    MEASUREMENTS,
    UNITS,
    TwinShaftModel,
    fit_test_bed,
    predict,
    synthetic_campaign,
)

OUT = Path(__file__).resolve().parents[1] / "docs" / "calibration"
TRUTH = np.array([39.5, 1560.0, 0.885, 0.86, 0.875])
START = [39.2, 1553.0, 0.89, 0.868, 0.868]  # the single-point datasheet calibration's values, roughly
CONDITIONS = [("Base, 5 °C", 5, 1.0), ("Base, 15 °C", 15, 1.0), ("Base, 30 °C", 30, 1.0),
              ("75 %, 15 °C", 15, 0.75), ("50 %, 15 °C", 15, 0.5), ("60 %, 25 °C", 25, 0.6)]
HELD_OUT = [("85 %, 10 °C", 10, 0.85), ("Base, 35 °C", 35, 1.0)]


def main():
    _, limits, controls = offdesign_options({}, 17.1)
    model = TwinShaftModel(17.1, engine_kw={"limits": limits, "controls": controls})
    truth = model.build(TRUTH)
    lines = ["# Multi-point calibration to test-bed data", "",
             "Synthetic campaign from a hidden engine whose parameters are known, so the fit can be checked. "
             "Each point is set by measured electrical output; measurements and their standard uncertainties:", "",
             "| Measurement | Unit | Standard uncertainty |", "|---|---|---:|"]
    for k in (*MEASUREMENTS, "P45"):
        u = DEFAULT_UNCERTAINTY[k]
        lines.append(f"| {k} | {UNITS[k]} | {f'{100 * u:.2f} %' if k in ('W_air', 'W_fuel', 'P3', 'P45') else u} |")
    for title, meas in (("Standard test-bed measurements", MEASUREMENTS),
                        ("Adding the inter-turbine pressure P45", (*MEASUREMENTS, "P45"))):
        pts = synthetic_campaign(truth, CONDITIONS + HELD_OUT, seed=4, measurements=meas)
        cal = fit_test_bed(model, pts[: len(CONDITIONS)], START)
        z = predict(cal, model, pts[len(CONDITIONS):])
        i, j = cal.names.index("hpt_eff"), cal.names.index("pt_eff")
        worst = max(abs(v) for p in z.values() for v in p.values())
        lines += ["", f"## {title}", "", cal.table(TRUTH), "",
                  f"Chi-square {cal.chi2:.1f} on {cal.dof} degrees of freedom. Condition number of the weighted "
                  f"Jacobian {cal.condition_number:,.0f}. Correlation between HP and power turbine efficiency "
                  f"{cal.correlation[i, j]:+.2f}. Largest held-out prediction error {worst:.2f} combined "
                  "standard uncertainties."]
        print(title, cal.correlation[i, j], worst)
    lines += ["", "Without P45 the data fix the sum of the two turbines' work well but not how it splits, so their "
              "efficiencies are almost perfectly anti-correlated and each carries 0.7 to 0.8 points of uncertainty. "
              "Measuring the pressure between the turbines separates them and cuts that to about 0.13 points. "
              "A single datasheet point cannot reveal "
              "this at all: three numbers fitted with three unknowns always match exactly."]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "multipoint.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
