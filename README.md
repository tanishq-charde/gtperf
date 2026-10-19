# gtperf

A Python toolkit for gas turbine performance: cycle modelling, site performance prediction, pass-off test validation and fleet trend analysis.

**Status:** phases 1 and 2 of 6 done (design-point cycle; off-design and site performance). Private, work in progress.

## What phase 1 does

- **Real-gas properties.** Humid air and combustion products as ideal-gas mixtures, using NASA 7-coefficient polynomials (GRI-Mech 3.0 data). Enthalpies include heats of formation, so fuel LHV and the combustor energy balance come out of the same property model. Checked against Cantera to 1 part in a million.
- **Fuels.** Gaseous hydrocarbon blends (CH4, C2H6, C3H8 with CO2/N2 inerts), complete combustion, LHV and stoichiometric fuel-air ratio computed from composition.
- **Components.** Inlet duct, compressor (isentropic or polytropic efficiency), combustor (solves fuel flow for a target exit temperature, with pressure loss and combustion efficiency), turbine, and a simple turbine cooling-air model that separates TIT (combustor exit) from rotor inlet temperature.
- **Single-shaft simple cycle.** Station-by-station results, shaft and electrical power, thermal efficiency, heat rate, exhaust flow and temperature, inlet and exhaust installation losses, and sizing of air flow for a target output.
- **Parametric studies** over pressure ratio and firing temperature.
- **CLI** driven by a TOML engine definition.

## What phase 2 adds

- **Twin-shaft cycle** (gas generator plus free power turbine), the layout of most small industrial engines, alongside the single-shaft cycle.
- **Off-design matching.** A generic analytic compressor map (speed lines from surge to choke, efficiency fall-off with speed and incidence), Stodola ellipse-law turbine flow capacity, and turbine efficiency against blade-speed ratio. A Newton solver finds the point where compressor flow, both turbine capacities and the gas generator power balance agree. Corrected flow and speed use full γRT similarity, so humidity and fuel changes are handled consistently.
- **Base-load control.** Constant TIT, with a gas generator speed limit that takes over on cold days and lowers TIT ("speed topping").
- **Calibration to a public datasheet.** Air flow, TIT and turbine efficiency are fitted to the published SGT-400 13 MW ISO power, heat rate and exhaust flow. Exhaust temperature is left out of the fit and used as an independent check.
- **Site performance prediction.** ISA altitude, ambient temperature, humidity, inlet and exhaust losses, fuel composition and component degradation, with the change from ISO broken into correction factors that multiply exactly to the site value. Output as a markdown or CSV performance sheet.
- **Correction curves** across ambient temperature, plus a `Health` object (compressor flow and efficiency, turbine efficiency and capacity) that phases 3 and 4 build on.

## Quick start

```bash
pip install -e ".[dev]"
gtperf design examples/engine_13mw.toml       # or: python -m gtperf design ...
python examples/parametric_study.py           # regenerates docs/figures/
gtperf calibrate examples/sgt400_13mw.toml
gtperf site examples/sgt400_13mw.toml examples/site_hot_desert.toml
gtperf curves examples/sgt400_13mw.toml > curves.csv
python examples/site_performance.py           # correction curves, site sheet
pytest
```

Example output for the illustrative 13 MW class engine at ISO conditions (15 °C, 1.01325 bar, 60 % RH):

```
electrical_power_MW             12.929
thermal_efficiency_pct          36.311
heat_rate_kJ_per_kWh          9914.356
exhaust_flow_kg_s               39.750
exhaust_temp_C                 527.001
```

From Python:

```python
from gtperf import SimpleCycle, Compressor, Combustor, Turbine, Ambient, natural_gas

cycle = SimpleCycle(
    Compressor(pressure_ratio=16.8, efficiency=0.90),        # polytropic
    Combustor(natural_gas(), t_out=1523.15),                 # TIT 1250 C
    Turbine(efficiency=0.89),
    cooling_fraction=0.15,
)
iso = cycle.run(air_flow=39.0)
hot = cycle.run(air_flow=39.0, ambient=Ambient(t=308.15))   # 35 C day
print(iso, hot.electrical_power / 1e6)
```

## Results

![Trade study](docs/figures/trade_study.png)

The classic simple-cycle trade-off: specific work (and so engine size for a given output) peaks at a much lower pressure ratio than thermal efficiency does, and both rise with firing temperature. Small industrial engines sit between the two optima.

![T-s diagram](docs/figures/ts_diagram.png)

### Calibration to the SGT-400 13 MW datasheet

Datasheet values from the [Siemens Energy SGT-400 product page](https://siemens-energy.com/us/en/home/products-services/product/sgt-400.html) (simple cycle, 13 MW version, ISO). Compressor polytropic efficiency 0.89, 15 % cooling air, 4 % combustor loss and 98 % generator efficiency are assumptions.

| Quantity | Datasheet | Model | Error |
|---|---:|---:|---:|
| Electrical power (MW) | 12.9 | 12.9 | fitted |
| Heat rate (kJ/kWh) | 10,294 | 10,294 | fitted |
| Exhaust flow (kg/s) | 40.0 | 40.0 | fitted |
| Exhaust temperature (°C) | 555 | 550.2 | −4.8 K (not fitted) |

Fitted values: air flow 39.2 kg/s, TIT 1280 °C, turbine isentropic efficiency 0.858. These depend on the assumptions above, so they are model parameters, not claims about the real engine.

![Correction curves](docs/figures/correction_curves.png)

Output falls by about 0.9 % per kelvin above ISO, while heat rate and exhaust temperature rise. Below about −6 °C the gas generator reaches its speed limit (shaded), so TIT is cut back and the curves change slope.

![Site correction factors](docs/figures/site_factors.png)

The full sheet for this example site is in [docs/site_hot_desert.md](docs/site_hot_desert.md).

## Verification

The tests in `tests/` check:

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
| Hot day: lower output and flow, higher heat rate and exhaust temperature, less surge margin | Physical behaviour |
| Site correction factors multiply exactly to the site/ISO ratio | Consistency |
| Calibration hits datasheet targets; exhaust temperature within 10 K | SGT-400 datasheet |

## Limitations

- The compressor map is generic and analytic, not the real machine's. Trends are right but numbers away from ISO are indicative only. No variable inlet guide vanes or bleed schedules yet, so surge margin falls further on hot days than a real engine would allow.
- The calibration fits three datasheet numbers with three unknowns. A real calibration would use test-bed data across several operating points.
- Cooling air is a single mixed stream ahead of the HP turbine, not a stage-by-stage model.
- Inlet and exhaust losses are fixed at the quoted values rather than scaling with flow squared.
- The twin-shaft humidity effect (about +5 % output from dry to saturated at 40 °C) comes from the gas generator speeding up with more water vapour in the gas. It has not been checked against OEM humidity curves.
- Part load, generator capability limits and other derates are not modelled yet.

## Roadmap

1. Design-point cycle (done)
2. Off-design matching, site performance and calibration to a public datasheet (done)
3. Pass-off test validation: correction to reference conditions (ASME PTC 22 / ISO 2314 approach) and measurement uncertainty (GUM, Monte Carlo)
4. Fleet analytics: SPC charts, gas-path fault isolation, anomaly detection on a synthetic fleet
5. Tooling and reporting: Excel/VBA front end, CI, technical report
6. Stretch: MATLAB port, two-spool aero cycle, requirements and certification note

All data in this project is synthetic or from public sources.
