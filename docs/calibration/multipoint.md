# Multi-point calibration to test-bed data

Synthetic campaign from a hidden engine whose parameters are known, so the fit can be checked. Each point is set by measured electrical output; measurements and their standard uncertainties:

| Measurement | Unit | Standard uncertainty |
|---|---|---:|
| W_air | kg/s | 0.50 % |
| W_fuel | kg/s | 0.35 % |
| P3 | bar | 0.30 % |
| T3 | K | 1.0 |
| T_exh | K | 2.0 |
| N_gg | - | 0.002 |
| P45 | bar | 0.30 % |

## Standard test-bed measurements

| Parameter | Fitted | Standard uncertainty | Truth | Error / u |
|---|---:|---:|---:|---:|
| air_flow | 39.532 | 0.074 | 39.5 | +0.43 |
| tit | 1560.8 | 1.4 | 1560 | +0.58 |
| compressor_eff | 0.8838 | 0.00074 | 0.885 | -1.61 |
| hpt_eff | 0.8638 | 0.007 | 0.86 | +0.54 |
| pt_eff | 0.87385 | 0.0079 | 0.875 | -0.14 |

Chi-square 37.8 on 31 degrees of freedom. Condition number of the weighted Jacobian 3,897. Correlation between HP and power turbine efficiency -0.98. Largest held-out prediction error 1.06 combined standard uncertainties.

## Adding the inter-turbine pressure P45

| Parameter | Fitted | Standard uncertainty | Truth | Error / u |
|---|---:|---:|---:|---:|
| air_flow | 39.524 | 0.074 | 39.5 | +0.32 |
| tit | 1559.7 | 1.3 | 1560 | -0.24 |
| compressor_eff | 0.8841 | 0.00074 | 0.885 | -1.21 |
| hpt_eff | 0.86155 | 0.0013 | 0.86 | +1.21 |
| pt_eff | 0.87569 | 0.0012 | 0.875 | +0.55 |

Chi-square 42.7 on 37 degrees of freedom. Condition number of the weighted Jacobian 4,338. Correlation between HP and power turbine efficiency -0.36. Largest held-out prediction error 1.96 combined standard uncertainties.

Without P45 the data fix the sum of the two turbines' work well but not how it splits, so their efficiencies are almost perfectly anti-correlated and each carries 0.7 to 0.8 points of uncertainty. Measuring the pressure between the turbines separates them and cuts that to about 0.13 points. A single datasheet point cannot reveal this at all: three numbers fitted with three unknowns always match exactly.
