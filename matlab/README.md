# MATLAB port of the cycle core

Plain MATLAB functions (no toolboxes) that reproduce the Python package's cycle core:

| Function | Port of |
|---|---|
| `gt_mixture`, `gt_props`, `gt_t_from_h`, `gt_t_from_s`, `gt_humid_air` | `gtperf.gas`: NASA polynomial ideal-gas mixtures |
| `gt_fuel`, `gt_natural_gas`, `gt_kerosene`, `gt_products`, `gt_stoich_far` | `gtperf.fuel`, `gtperf.aero.Kerosene` |
| `gt_compressor`, `gt_combustor`, `gt_turbine`, `gt_mix` | `gtperf.components` |
| `gt_twin_shaft_cycle`, `gt_sgt400` | `gtperf.cycle.TwinShaftCycle` with the SGT-400 calibration |
| `gt_turbofan`, `gt_nozzle`, `gt_isa` | `gtperf.aero` two-spool turbofan |

`gt_species_data.m` and `reference_values.csv` are generated from the Python package by
`python matlab/make_reference.py`, so both implementations use the same coefficients.

## Checking it

```matlab
cd matlab
run_tests
```

`run_tests` compares gas properties, the natural gas LHV, the SGT-400 twin-shaft cycle at ISO and on a
35 °C day, and the cruise turbofan against the Python results. Every value agrees to 1e-8 or better (gas
properties and the industrial cycle to about 1e-14; the turbofan to about 1e-11, set by the turbine
pressure solver's tolerance). The check runs in GNU Octave in this repository's tests
(`tests/test_matlab.py`); it uses nothing outside base MATLAB.

## Example

```matlab
c = gt_sgt400(1553.178, 0.85782);                 % fitted TIT (K) and turbine efficiency
r = gt_twin_shaft_cycle(c, 39.2225, struct('t', 288.15, 'p', 101325, 'rh', 0.6));
fprintf('%.2f MW, heat rate %.0f kJ/kWh\n', r.electrical_power / 1e6, r.heat_rate);

fan = struct('bpr', 10, 'fan_pr', 1.5, 'fan_core_pr', 2.4, 'hpc_pr', 17, 'tet', 1600);
r = gt_turbofan(fan, 170, struct('altitude_m', 10668, 'mach', 0.78));
fprintf('%.1f kN, TSFC %.3e kg/(N s)\n', r.net_thrust / 1e3, r.tsfc);
```
