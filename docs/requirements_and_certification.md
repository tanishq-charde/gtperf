# Requirements, verification and certification

This note links each requirement on the toolkit to the evidence that verifies it, then sets out how emissions
and noise certification would constrain the two cycles modelled here. The requirements are illustrative: the
guarantees are the example contract used in the pass-off report, not Siemens Energy or Rolls-Royce figures.

## 1. Traceability

Every requirement names a verification method from the usual set: test, analysis, inspection or demonstration.
Tests are named by function, and `tests/test_requirements.py` fails if a named test is missing or if this
table drifts from [requirements.toml](requirements.toml), so the trace cannot silently go stale.

<!-- requirements table: generated from docs/requirements.toml -->
| ID | Requirement | Verification | Acceptance | Evidence |
|---|---|---|---|---|
| G-01 | Net electrical output at ISO, corrected from a pass-off test, at least 12.65 MW | Test | Corrected value above the limit with probability of conformance reported; tests in another control mode rejected | docs/passoff/report.md: 12.724 ± 0.048 MW, PASS; `test_conformity_verdicts`, `test_result_within_uncertainty_of_truth`, `test_speed_limited_test_is_flagged` |
| G-02 | Heat rate at ISO (LHV), corrected, at most 10,500 kJ/kWh | Test | As G-01 | docs/passoff/report.md: 10,317 ± 57 kJ/kWh, PASS; `test_conformity_verdicts`, `test_tight_guarantee_fails` |
| G-03 | Exhaust temperature at ISO, corrected, at most 570 °C (heat recovery boiler design limit) | Test | As G-01; exhaust rake profile under-sampling included in the uncertainty | docs/passoff/report.md: 551.2 ± 1.9 °C, PASS; `test_conformity_verdicts`, `test_validation_catches_every_injected_fault` |
| S-01 | Quoted site performance breaks into correction factors that reproduce the full model | Test | Product of factors equals site / ISO to 1e-9; Excel tool within 0.3 % of the full model | docs/site_hot_desert.md, excel/gtperf_site_tool.xlsx; `test_site_correction_factors_multiply_to_site_value`, `test_correction_method_close_to_full_model`, `test_formulas_and_vba_in_libreoffice` |
| M-01 | Expanded uncertainty (k = 2) of corrected output no more than 0.5 %, and honest | Analysis | U95 at most 0.5 % of output; repeat-test coverage of the truth between 90 and 99 % | examples/coverage_study.py: U95 0.38 %, coverage 95.5 % for output; `test_gum_and_monte_carlo_agree`, `test_result_within_uncertainty_of_truth` |
| M-02 | Offset, drift, stuck and spiking sensors are excluded before averaging, with no false exclusions on clean data | Test | Every injected fault caught; clean log raises no flag | docs/passoff/report.md, section 3; `test_validation_catches_every_injected_fault`, `test_clean_data_raises_no_flags` |
| P-01 | Gas properties agree with an independent reference implementation | Test | cp, h and s within 1e-6 of Cantera with GRI-Mech 3.0 | tests/test_gas.py; `test_matches_cantera_if_available`, `test_dry_air_cp_matches_tables` |
| P-02 | Model reproduces the public SGT-400 ISO rating and predicts the unfitted exhaust temperature | Test | Fitted values exact; exhaust temperature within 10 K | README calibration table: 550.2 °C against 555 °C; `test_calibration_hits_datasheet` |
| P-03 | Energy and mass are conserved in every cycle | Test | Closure to 1e-9 at design and off-design, industrial and aero | tests/; `test_first_law_closes`, `test_mass_and_energy_close_off_design`, `test_first_law_closes_through_the_engine`, `test_products_conserve_mass_and_atoms` |
| P-04 | The MATLAB port gives the same answers as the Python package | Test | All reference values within 1e-8 | matlab/run_tests.m; `test_matlab_port_matches_python`, `test_matlab_inputs_are_current` |
| A-01 | Turbofan design-point method matches the textbook constant-cp calculation and the ideal-cycle limit | Test | Thrust and TSFC within 1e-6 of the hand calculation; ideal efficiency 1 - 1/(tau_r tau_c) to 1e-6 | tests/test_aero.py; `test_constant_cp_turbofan_matches_hand_calculation`, `test_ideal_cycle_recovers_brayton_efficiency`, `test_optimum_fan_pressure_ratio_sets_jet_velocity_ratio` |
| F-01 | A turbine efficiency step of 1.5 points or an instrument offset is detected and correctly isolated within 60 days | Test | Detected, isolated to the right cause, delay under 60 days | docs/fleet/report.md: 5 of 5 faults on the unseen fleet; `test_monitoring_detects_and_isolates` |
| F-02 | Nuisance alarms no more than 0.1 per engine-year | Analysis | Thresholds tuned on a training fleet, scored on an unseen fleet | examples/fleet_analysis.py, docs/fleet/report.md: 0 false alarms in 24 engine-years |
<!-- end requirements table -->

The guarantee chain runs left to right in the V-model sense. A contract guarantee (G-01) becomes an
acceptance test with a stated tolerance (the U95 of M-01). That test relies on validated instruments (M-02)
and on a correction model that is itself verified (P-01 to P-03). F-02 is the only requirement verified by
analysis alone, because a false-alarm rate needs a long fleet history rather than a unit test.

## 2. Industrial engine: emissions limits

**What applies.** A 13 MW class engine has a fuel heat input of about 37 MW (12.9 MW at 35 % efficiency).
That puts a single unit inside the EU Medium Combustion Plant Directive (EU) 2015/2193, which covers plant
from 1 to 50 MW thermal input. Its limits are quoted dry at 15 % O2 for gas turbines. A larger site, or
several units totalling 50 MW or more, falls under the Industrial Emissions Directive instead. For new gas
turbines on natural gas, the MCPD NOx limit is understood to be 50 mg/Nm³. That value still needs checking
against Annex II of the Directive and the UK's own transposition before anyone relies on it.

**How it constrains the cycle.** Thermal NOx rises roughly exponentially with flame temperature, so the
limit caps flame temperature rather than TIT directly. A dry low emissions (DLE) combustor premixes fuel and
air lean so the flame stays below about 1900 K. That margin is what limits how far TIT can rise for output
and efficiency. The same lean premix makes CO and flame stability the problems at part load. That is why
emissions-compliant load range, not only output, appears in a performance guarantee.

**What the toolkit would need.** gtperf does not model NOx. The next step would be a correlation in the
combustor inlet temperature, pressure and flame temperature, calibrated to test data. Correcting a stack
measurement to 15 % O2 would then reuse the product composition the combustor already computes.

## 3. Aero engine: emissions, noise and CO2

**Emissions (ICAO Annex 16 Volume II; EASA CS-34).** Gaseous emissions are certified over the reference
landing and take-off (LTO) cycle below 3,000 ft. Each mode has a fixed time at a set thrust, as a fraction
of rated take-off thrust:

| Mode | Thrust | Time |
|---|---:|---:|
| Take-off | 100 % | 0.7 min |
| Climb | 85 % | 2.2 min |
| Approach | 30 % | 4.0 min |
| Taxi and ground idle | 7 % | 26.0 min |

NOx, CO and unburned hydrocarbons are regulated as mass per unit rated thrust over the cycle (Dp/Foo).
Since 2020, non-volatile particulate matter has also been regulated, and from 2023 it replaces the smoke
number for engines above 26.7 kN ([UK CAA summary](https://www.caa.co.uk/environmental-sustainability/uk-aviation-environmental-review/uk-aer-air-quality/air-quality-international-policy-and-regulation/)).
The NOx limit is set higher for engines of higher rated pressure ratio, because a hotter, higher-pressure
combustor inlet raises NOx while improving efficiency. The tension shows in this model's cruise design: an
overall pressure ratio of about 40 puts T3 at 775 K at cruise, and higher still at take-off.

**Noise (Annex 16 Volume I Chapter 14; EASA CS-36).** Chapter 14 is 7 EPNdB cumulative more stringent than
Chapter 4. It applies to new types submitted from 31 December 2017, or 2020 for aircraft under 55 tonnes
([ICAO](https://www.icao.int/environmental-protection/Pages/Reduction-of-Noise-at-Source.aspx)).
Jet mixing noise scales roughly with jet area times velocity to the eighth power (Lighthill), which is the
main reason bypass ratio has risen. The turbofan model shows the trade at 22 kN cruise thrust, each bypass
ratio at its best fan pressure ratio:

| Bypass ratio | Best fan PR | TSFC (mg/N/s) | Intake flow (kg/s) | Core jet (m/s) |
|---:|---:|---:|---:|---:|
| 7 | 1.93 | 15.43 | 123 | 498 |
| 10 | 1.65 | 14.60 | 161 | 460 |
| 13 | 1.50 | 14.15 | 198 | 420 |

From bypass ratio 7 to 13, fuel burn falls 8 %. Scaling jet noise intensity as flow times velocity to the
seventh power (with area proportional to flow over velocity), the core jet is about 3 dB quieter. The cost is
60 % more intake flow, so the fan is about 27 % larger in diameter, which adds weight, nacelle drag and
installation difficulty. Certification noise is measured at take-off and approach, not cruise, so these
figures show the direction and size of the trade, not a certification estimate. Fan and turbine tones,
which this model cannot predict, dominate at approach.

**CO2 (Annex 16 Volume III).** The aeroplane CO2 standard sets a fuel-efficiency metric for the aircraft. The
engine's contribution is cruise TSFC, which is exactly what the cycle trade optimises.

## 4. What certification evidence would look like

For a real product, each requirement above would trace to a controlled test procedure and a calibrated
instrument list, and each model claim to validation data. The toolkit already enforces the structure: the
guarantee, the test and its tolerance, and the automated check are linked in one file and checked on every
run. What it cannot supply is the real data behind each link: calibration certificates, test-bed
correlation, and the engine's emissions and noise test results.
