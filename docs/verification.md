# Verification

What the tests in `tests/` check, and the reference each check is made against.

| Check | Reference |
|-------|-----------|
| Constant-cp cycle with separate air and gas properties matches the hand calculation to 1e-6 | Textbook method (Saravanamuttoo, *Gas Turbine Theory*, ch. 2) |
| Lossless cycle recovers the ideal Brayton efficiency 1 − r^−(γ−1)/γ | Analytical |
| First law closes across the whole engine to 1e-9, with cooling air | Energy balance |
| Mass and element balances through the combustor | Conservation |
| Air cp at 300, 500, 1000 K within 0.5 % | Ideal-gas air tables |
| Methane LHV 50.0 MJ/kg, stoichiometric AFR 17.2 | Published values |
| Mixture cp, h and s identical to Cantera | Cantera / GRI-Mech 3.0 |
| Expected trends: TIT, pressure ratio optima, hot day, installation losses | Physical behaviour |
| Off-design solver reproduces the design point to 1e-8 (single and twin shaft) | Consistency |
| Dry-air output scales exactly with ambient pressure; heat rate unchanged | Non-dimensional similarity |
| Cold day switches to gas generator speed control and lowers TIT | Control logic |
| Hot day: lower output and flow, higher heat rate and exhaust temperature | Physical behaviour |
| Site correction factors multiply exactly to the site/ISO ratio | Consistency |
| Calibration hits datasheet targets; exhaust temperature within 10 K | SGT-400 datasheet |
| With perfect instruments, the correction returns the model reference values | Consistency |
| Validation catches every injected fault and raises no flags on clean data | Synthetic faults |
| Corrected results fall within U95 of the hidden truth | Synthetic truth |
| GUM and Monte Carlo standard uncertainties agree within 15 % | JCGM 100 / 101 |
| Speed-limited cold-day test is flagged as not valid | Control-mode check |
| CUSUM and EWMA detect a 1-sigma shift sooner than Shewhart | SPC theory |
| Linear GPA recovers health exactly from linear deltas; isolates a sensor bias | Consistency |
| Nonlinear GPA explains a large fault that linear GPA cannot | Engine model |
| Baseline lookup matches the engine model to 0.05 % | Consistency |
| Small fleet: step fault and sensor fault both detected and correctly isolated | Synthetic truth |
| Excel correction method within 0.3 % of the full model, on and off the table grid | Full model |
| Workbook formulas (recalculated in LibreOffice) and VBA functions (run in its VBA mode) match the Python mirror to 1e-9 | Consistency |
| VBA site-file writer and CSV reader round-trip through the real CLI | Consistency |
| Constant-cp turbofan matches the hand calculation to 1e-6, including a choked core nozzle | Textbook method (Saravanamuttoo, ch. 3) |
| Lossless turbofan recovers 1 − 1/(τr·τc) at bypass ratios 0 and 5 | Analytical |
| First law closes through the turbofan; nozzles conserve total enthalpy | Energy balance |
| At the best fan pressure ratio, V_bypass/V_core = ηfan·ηLPT within 0.015 | Classic optimum (polytropic efficiencies) |
| Higher bypass ratio gives lower TSFC and specific thrust | Physical behaviour |
| ISA temperature and pressure at 0, 11 and 20 km | ISA tables |
| MATLAB port matches the Python reference values to 1e-8 | Consistency |
| Every test named in the requirements file exists, and the traceability table is current | Consistency |
| Tabulated maps pass through the design point after scaling | Consistency |
| Cold day hits the corrected-speed limit; hot day the mechanical speed limit | Control logic |
| Generator capability and exhaust temperature limits bind when set | Control logic |
| VIGVs close at low corrected speed along the schedule | Control schedule |
| Twin-shaft part load lowers TIT; single shaft holds exhaust temperature with VIGVs, then lowers TIT | Control logic |
| Inlet and exhaust losses scale with W²T/P | Fluid mechanics |
| Humidity raises twin-shaft output and lowers single-shaft output on exhaust temperature control | GER-3567H |
| Multi-point fit recovers hidden parameters; held-out points within uncertainty; turbine efficiencies flagged as confounded | Synthetic truth |
| Every instrument has a source; class tolerances convert correctly; out-of-date certificates are flagged | Standards and datasheets |
| Estimated build scatter contains the hidden truth | Synthetic truth |
| Swirl rotates the burner cold spot by the modelled angle; a lean burner is detected and tracked | Exhaust model |
| Split HP/PT health and part-load influence matrices; part-load fleet deltas are unbiased | Consistency |
| Turbofan off-design reproduces the design point, lapses thrust with altitude and Mach, and accelerates to the steady point | Consistency, physical behaviour |
| Split turbofan cooling conserves mass and energy; the CD nozzle beats the convergent one only when choked | Conservation, gas dynamics |
| NOx follows the Zeldovich activation temperature; staging extends compliant turndown | Combustion chemistry |
| NOx standard continuous and matches the tabulated values | 40 CFR 1031.60 |
| Noise sources follow known turbofan trends (jet dominates at take-off, fan at approach) | Published behaviour |
