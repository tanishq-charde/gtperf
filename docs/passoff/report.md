# Pass-off test report: ESN-0412

**Overall result: PASS** against the acceptance limits below, on 3 test points at base load. Fuel: natural gas.

## 1. Corrected results against acceptance limits

Corrected to reference conditions (ISO 15 °C, 1013.25 mbar, 60 % RH, no inlet or exhaust losses). U95 is the expanded uncertainty (k = 2).

| Result | Corrected | U95 | Limit | Margin | P(conform) | Verdict |
|---|---:|---:|---:|---:|---:|---|
| Corrected power (MW) | 12.724 | ±0.048 | >= 12.650 | 0.074 | 99.8 % | PASS |
| Corrected heat rate (kJ/kWh) | 10,317 | ±57 | <= 10,500 | 183 | 100.0 % | PASS |
| Corrected exhaust temperature (°C) | 551.2 | ±1.9 | <= 570.0 | 18.8 | 100.0 % | PASS |
| Corrected exhaust flow (heat balance) (kg/s) | 39.51 | ±0.36 | – | – | – | for information |

## 2. Test conditions

| Point | Samples | Ambient (°C) | Pressure (mbar) | RH (%) | Inlet loss (mbar) | Exhaust loss (mbar) |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 600 | 8.14 | 1001.8 | 77.8 | 5.2 | 12.1 |
| 2 | 600 | 8.73 | 1002.0 | 75.7 | 5.2 | 12.1 |
| 3 | 600 | 9.23 | 1002.1 | 73.7 | 5.2 | 12.1 |

## 3. Data validation

| Sensor | Excluded because |
|---|---|
| T_amb_3 | stuck (no change for 60+ samples) |
| T_exh_03 | reads 32.2 K from the exhaust median |
| T_exh_07 | drifts 13.6 K relative to other thermocouples |

Spikes removed: P_elec (18 samples).

Stability (range of 1-minute averages within each point): all within limits.

![Sensor traces](figures/sensors.png)

## 4. Measured and corrected results by point

| Point | Measured power (MW) | Measured heat rate (kJ/kWh) | Power correction | Heat rate correction | Corrected power (MW) | Corrected heat rate (kJ/kWh) |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 13.322 | 10,219 | 0.9552 | 1.0094 | 12.726 | 10,314 |
| 2 | 13.237 | 10,238 | 0.9612 | 1.0077 | 12.723 | 10,317 |
| 3 | 13.169 | 10,255 | 0.9663 | 1.0063 | 12.725 | 10,319 |

Point-to-point repeatability of corrected power: 0.02 % (max − min). This engine produced -1.36 % power against the nominal model at test conditions.

## 5. Uncertainty

Systematic (calibration) uncertainties are shared by all points; random uncertainties are the standard error of each point mean, with an effective sample size for autocorrelation. Sensitivities are found numerically through the full correction chain, including the engine model.

| Result | GUM u | Monte Carlo u | MC 95 % interval |
|---|---:|---:|---|
| Corrected power (MW) | 0.02383 | 0.02402 | 12.678 to 12.772 |
| Corrected heat rate (kJ/kWh) | 28.5 | 28.78 | 10,261 to 10,374 |
| Corrected exhaust temperature (°C) | 0.9357 | 0.94 | 549.4 to 553.1 |
| Corrected exhaust flow (heat balance) (kg/s) | 0.1795 | 0.1813 | 39.16 to 39.86 |

Largest contributors:

![Uncertainty budget](figures/uncertainty_budget.png)

![Monte Carlo distributions](figures/monte_carlo.png)

Exhaust temperature systematic uncertainty combines thermocouple calibration (0.32 K) and profile sampling (0.86 K), the second arising because excluded thermocouples leave the non-uniform exhaust profile under-sampled.

## 6. Check against the known truth (synthetic test only)

The data was generated from an engine with hidden health deltas and hidden instrument biases. A sound method should land within U95 of the truth about 95 % of the time.

| Result | True | Corrected | Error | Error / U95 |
|---|---:|---:|---:|---:|
| Corrected power (MW) | 12.700 | 12.724 | +0.024 | +0.51 |
| Corrected heat rate (kJ/kWh) | 10,352 | 10,317 | -35 | -0.62 |
| Corrected exhaust temperature (°C) | 551.9 | 551.2 | -0.6 | -0.34 |
| Corrected exhaust flow (heat balance) (kg/s) | 39.60 | 39.51 | -0.08 | -0.23 |

## Method notes

- Correction by the model-ratio method: corrected = measured × model(reference) / model(test). Exhaust temperature is corrected by difference.
- Exhaust flow comes from a heat balance on fuel heat input, electrical output and modelled mechanical and generator losses, then corrected the same way.
- Acceptance follows a guarded-band reading: PASS needs the margin to exceed U95; a smaller positive margin is reported as PASS (within uncertainty).
- Correction-model uncertainty: the correction curves describe the nominal engine, so the build-to-build scatter of compressor flow, compressor efficiency and turbine efficiency (assumed standard deviations) is propagated through the correction. This term grows with the distance from reference conditions.
- Instrument uncertainties are representative assumptions, not a calibration record.
