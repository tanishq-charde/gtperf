# Changelog

GTPerf was built in phases; each section records what that phase or release added.

## Unreleased

- **Map spreadsheet (XLSX).** On Engine design (compressor, HP turbine, power turbine) and Turbofan (fan, booster, HPC, HPT, LPT), the calculated maps download as an Excel workbook that works them out itself: the design inputs in a table, the design-point velocity triangles and stage sizing, every stage of every map point as a live formula (axial velocities by Newton iterations in helper columns), and native Excel charts drawn from those cells. Change an input and the maps recalculate. The formulas reproduce `gtperf.mapgen` to 1 part in 10¹⁰ and are checked in LibreOffice (`gtperf.mapxlsx`, tests/test_mapxlsx.py).

## Phase 1

- **Real-gas properties.** Humid air and combustion products as ideal-gas mixtures, using NASA 7-coefficient polynomials (GRI-Mech 3.0 data). Enthalpies include heats of formation, so fuel LHV and the combustor energy balance come out of the same property model. Checked against Cantera to 1 part in a million.
- **Fuels.** Gaseous hydrocarbon blends (CH4, C2H6, C3H8 with CO2/N2 inerts), complete combustion, LHV and stoichiometric fuel-air ratio computed from composition.
- **Components.** Inlet duct, compressor (isentropic or polytropic efficiency), combustor (solves fuel flow for a target exit temperature, with pressure loss and combustion efficiency), turbine, and a simple turbine cooling-air model that separates TIT (combustor exit) from rotor inlet temperature.
- **Single-shaft simple cycle.** Station-by-station results, shaft and electrical power, thermal efficiency, heat rate, exhaust flow and temperature, inlet and exhaust installation losses, and sizing of air flow for a target output.
- **Parametric studies** over pressure ratio and firing temperature.
- **CLI** driven by a TOML engine definition.

## Phase 2

- **Twin-shaft cycle** (gas generator plus free power turbine), the layout of most small industrial engines, alongside the single-shaft cycle.
- **Off-design matching.** A generic analytic compressor map (speed lines from surge to choke, efficiency fall-off with speed and incidence), Stodola ellipse-law turbine flow capacity, and turbine efficiency against blade-speed ratio. A Newton solver finds the point where compressor flow, both turbine capacities and the gas generator power balance agree. Corrected flow and speed use full γRT similarity, so humidity and fuel changes are handled consistently.
- **Base-load control.** Constant TIT, with a gas generator speed limit that takes over on cold days and lowers TIT ("speed topping").
- **Calibration to a public datasheet.** Air flow, TIT and turbine efficiency are fitted to the published SGT-400 13 MW ISO power, heat rate and exhaust flow. Exhaust temperature is left out of the fit and used as an independent check.
- **Site performance prediction.** ISA altitude, ambient temperature, humidity, inlet and exhaust losses, fuel composition and component degradation, with the change from ISO broken into correction factors that multiply exactly to the site value. Output as a markdown or CSV performance sheet.
- **Correction curves** across ambient temperature, plus a `Health` object (compressor flow and efficiency, turbine efficiency and capacity) that phases 3 and 4 build on.

## Phase 3

- **Synthetic test-bed data.** A production engine with hidden build-to-build health deltas is "tested" at whatever the weather is on the day. A representative instrument set (redundant inlet RTDs, 12 exhaust thermocouples, Coriolis fuel flow, gas chromatograph LHV, power metering, barometer, humidity, inlet and exhaust pressure losses) logs at 1 Hz. Every sensor gets a hidden calibration bias and noise, and faults can be injected: offset, drift, stuck and spikes.
- **Data validation.** Spike removal (rolling median and MAD), stuck-sensor detection, redundant-sensor disagreement, exhaust thermocouple deviation and drift checks against the rest of the rake, and stability criteria on 1-minute averages.
- **Correction to reference conditions** by the model-ratio method used in performance test codes (ASME PTC 22 / ISO 2314 style). Exhaust flow is derived from a heat balance, since it cannot be measured directly.
- **Measurement uncertainty.** GUM first-order propagation with numerical sensitivities through the whole correction chain, plus a JCGM 101 Monte Carlo. Calibration errors are shared by all test points and random errors are independent per point, with an autocorrelation-corrected standard error. The budget also includes exhaust profile under-sampling when thermocouples are excluded, and correction-model uncertainty from production scatter.
- **Acceptance** against guarantee limits with guarded-band verdicts and probability of conformance. A test point is flagged as not valid when the engine ran in a different control mode from the reference (detected from measured gas generator speed).
- **Auto-generated test report** in markdown with figures.

## Phase 4

- **Production SPC.** 200 synthetic pass-off results in build order with hidden events: a damaged engine, a test-bed metering error and a supplier change. The toolkit runs individuals, EWMA and CUSUM charts, Western Electric rules, a robust Phase I and Cpk against the acceptance limit.
- **Gas-path analysis.** An influence coefficient matrix taken from the engine model, weighted least-squares health estimation, fault fingerprints for components and sensors, sensor isolation by residual, and a nonlinear Gauss-Newton refit for large deviations.
- **In-service fleet monitoring.** A synthetic fleet of 12 engines over 2 years, with compressor fouling and washes, wear, UK-like weather and five injected engine and instrument faults. Each snapshot is compared with the nominal model through a baseline lookup. Two physics-based detectors run on the results: a trend-compensated step detector with an exact chi-squared statistic, and an instrument consistency monitor. Thresholds are tuned on one fleet and scored on an unseen one, against a naive data-driven detector.

## Phase 5

- **Excel site performance tool.** `gtperf excel` writes a workbook for a non-specialist: five site cases side by side, input range checks, every correction factor visible, and the results. It uses plain formulas over correction tables generated from the full model, so it needs no Python and no macros, and stays within 0.2 % of the full model. See [excel/README.md](excel/README.md).
- **VBA module.** [excel/gtperf.bas](excel/gtperf.bas) adds worksheet functions (`GT_SITE`, `GT_ISA_PRESSURE` and others) and a `RunFullModel` macro that sends each case to the Python model.
- **Automated checks.** Ruff lint, 80 tests with 95 % line and branch coverage (`make check`), and a GitHub Actions workflow ready for when the repository is published. The workbook formulas and the VBA functions are tested by recalculating and running them in headless LibreOffice.
- **Technical report and slide deck**, kept as private documents outside the repository.

![Excel site tool](docs/figures/excel_site_tool.png)

## Phase 6

- **Two-spool turbofan design point.** Separate-exhaust turbofan with SAE station numbering, ISA atmosphere, a kerosene surrogate burned through the same real-gas property model, convergent nozzles that choke, and LP and HP spool power balances. `gtperf turbofan` reports thrust, TSFC, the thermal, propulsive and overall efficiencies and a station table, and sizes the engine to a thrust requirement. It is checked against the textbook constant-cp hand calculation to 1e-6, the ideal Brayton limit, first-law closure, and the classic optimum fan pressure ratio result V_bypass/V_core = ηfan·ηLPT.
- **MATLAB port.** [matlab/](matlab/) holds the property model, fuels, components, the SGT-400 twin-shaft cycle and the turbofan as MATLAB functions. `make matlab` runs them in Octave against reference values written by the Python package; they agree to about 1e-11.
- **Requirements and certification note.** [docs/requirements_and_certification.md](docs/requirements_and_certification.md) traces each guarantee and model requirement to its verification method and evidence, with a test that fails if the trace goes stale. It then sets out how emissions limits (Medium Combustion Plant Directive; ICAO Annex 16 LTO cycle) and Chapter 14 noise constrain the two cycles, with the bypass ratio trade computed from the model.

![Turbofan trade](docs/figures/turbofan_trade.png)

## v1.2

v1.1 listed twelve limitations. Each one is now modelled, checked or measured, and what remains is stated in the README's Limitations.

- **Real component maps, guide vanes and bleed.** The public NPSS HBTF compressor and turbine maps (from NASA pyCycle, Apache 2.0, see [NOTICE](src/gtperf/data/maps/NOTICE)) replace the analytic map, read by R-line and scaled to the design point. VIGV angle shifts flow, head and efficiency on the map, and VIGV and handling-bleed schedules follow corrected speed. With the real map the gas generator slows on cold days, so a corrected-speed limit now joins the mechanical one.
- **Split cooling.** NGV, rotor and power turbine cooling streams, with the power turbine air taken from an interstage port, so it costs less compressor work than delivery air.
- **Losses that scale with flow.** Inlet and exhaust losses scale with dynamic head, W²T/P, from their design values.
- **Part load and limits.** `power=` or `load=` sets any operating point. The twin shaft lowers TIT; the single shaft closes its VIGVs to hold exhaust temperature, then lowers TIT at the vane stop. Base load is the first limit reached among TIT, both gas generator speed limits, exhaust and PT inlet temperature, shaft power and a generator capability curve with ambient derate.
- **Multi-point calibration.** [testbed.py](src/gtperf/testbed.py) fits model parameters to a whole test campaign by weighted least squares, with parameter covariance, condition number, chi-square and held-out prediction. It shows that the HP and power turbine efficiencies cannot be separated without the inter-turbine pressure (correlation −0.98, falling to −0.36 with P45). See [docs/calibration/multipoint.md](docs/calibration/multipoint.md).
- **Humidity check.** The humidity effect is checked against GE's GER-3567H (a single shaft on exhaust temperature control loses power with humidity; an aeroderivative gains it as the gas generator speeds up) and against Yu, Lin and Yu (2025). See [docs/humidity_check.md](docs/humidity_check.md).
- **Sourced pass-off uncertainties and estimated scatter.** Every instrument's uncertainty comes from its standard class or datasheet ([instruments.toml](src/gtperf/data/instruments.toml)) or from a calibration certificate, with due dates checked. Production scatter is estimated from the build data with confidence intervals instead of being assumed.
- **Exhaust profile that moves.** The exhaust temperature field has burner-by-burner structure whose swirl rotates with load, so the rake profile uncertainty is a Monte Carlo over that model, and a weak burner is detected and tracked across load.
- **Fault isolation.** HP and power turbine health are separate parameters, the GPA takes any measurement set (T45 and P45 added), and influence matrices can be built at part load. [docs/gpa_observability.md](docs/gpa_observability.md) measures which faults the measurements can tell apart and shows a second snapshot at part load is the most effective cure.
- **Part-load fleet monitoring.** Cycling units follow a dispatch profile; part-load snapshots are compared with a baseline read at the measured output and analysed with a part-load influence matrix, with a power meter fingerprint for the setting. See section 2 of [docs/fleet/report.md](docs/fleet/report.md).
- **Turbofan off-design and transients.** Fan, booster, HPC and turbine maps, working lines, thrust lapse with altitude and Mach number, thrust, N1, TET and fuel-flow settings, an idle to take-off acceleration with spool inertias, split cooling and a convergent-divergent nozzle option.
- **Emissions and noise.** A DLE combustor model (Zeldovich NOx, CO burnout, air staging) calibrated to the SGT-400's published emissions, checked against the EU Medium Combustion Plant Directive across load; LTO-cycle NOx (Dp/Foo) for the turbofan against the CAEP/6 and CAEP/8 standards; and fan broadband (Heidmann) and jet mixing (Lighthill and von Glahn) source levels. See [docs/environment.md](docs/environment.md).

## v1.3

- **Design mode.** An engine is built forwards from its design values (air flow, pressure ratio, TIT, component efficiencies, cooling flows, shaft and generator limits) instead of being fitted to a datasheet. The starting values reproduce the published SGT-400 13 MW rating (12.89 MW and 10,299 kJ/kWh against 12.9 MW and 10,294 kJ/kWh), and any of them can be changed.
- **Maps calculated by stage stacking.** [mapgen.py](src/gtperf/mapgen.py) generates the compressor and turbine maps from blade-stage inputs instead of scaling a stored map. The design work is divided over the stages; stage loading ψ and flow coefficient φ fix each stage's blade speed and velocity triangles; blade angles and annulus areas are then frozen. Off design each stage is solved in turn with incidence losses. A compressor row stalls at a set Lieblein diffusion factor, the surge line is the peak pressure rise or rear-stage stall, and the variable vane schedule is part of the map. A turbine row chokes at its peak mass flux, and a row whose design exit is supersonic is sized as just choked. The twin shaft and all five turbofan components (fan, booster, HPC, HPT, LPT) run on these maps.
- **LTO NOx from the operating points.** The four ICAO landing and take-off modes are matched as real operating points on the turbofan's maps, and EI NOx comes from a severity correlation on combustor inlet pressure, temperature and humidity, giving Dp/Foo against CAEP/6 and CAEP/8.
- **Robust matching.** The limit search starts from the corrected speed limit when the TIT-limited point lies past it, steps onto limits it overshoots, and treats a failed evaluation as a failed step, so edited designs, cold days, wide correction-curve ranges and cycling fleets converge. Inputs that cannot describe an engine (an efficiency outside 0 to 1, vanes still part closed at design speed) get a plain message.
- **The GTPerf web tool** replaces the Streamlit interface and its PDF, PowerPoint and Excel exports.
