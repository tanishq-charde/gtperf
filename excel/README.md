# Excel site performance tool

`gtperf_site_tool.xlsx` is a correction-curve sheet for the calibrated SGT-400 model. Type site
conditions into the blue cells of the **Site tool** sheet (five cases side by side) and it returns
output, heat rate, efficiency, fuel input and exhaust conditions. It needs no Python and no macros.

![Site tool sheet](../docs/figures/excel_site_tool.png)

| Sheet | Contents |
|---|---|
| Site tool | Inputs, ISA pressure, each correction factor, site performance |
| Tables | Corrections from the full model every 5 °C from −20 to 50 °C, as named ranges |
| Check | The same cases through the full model, with the workbook's error |
| About | Method, limits, and the settings the macro uses |

The correction method stays within 0.2 % of the full model on output and heat rate, and within 0.4 K
on exhaust temperature, for the cases tested (`tests/test_excel.py`).

## Rebuilding for another engine

```
gtperf excel examples/sgt400_13mw.toml gtperf_site_tool.xlsx
```

Any datasheet file that `gtperf calibrate` accepts works.

## VBA module

`gtperf.bas` adds worksheet functions and one macro. Import it with Alt+F11, then File > Import File,
and save the workbook as `.xlsm`.

| Name | What it does |
|---|---|
| `GT_SITE(quantity, T, altitude, RH%, inlet mbar, exhaust mbar, [deg %], [deg HR %])` | Site `power`, `heat_rate`, `efficiency`, `exhaust_flow` or `exhaust_temp` from the tables |
| `GT_SITE_POWER`, `GT_SITE_HEAT_RATE` | Shortcuts for the two quoted numbers |
| `GT_TABLE(column, T)` | One Tables column interpolated at a temperature |
| `GT_INTERP(x, xs, ys)` | Linear interpolation |
| `GT_ISA_PRESSURE(altitude)`, `GT_SAT_PRESSURE(T)`, `GT_HUMIDITY_RATIO(T, RH%, p)` | Atmosphere helpers |
| `RunFullModel` (macro) | Sends every case to `gtperf site --csv` and writes the full-model results under the sheet |

`RunFullModel` needs Windows and `gtperf` installed; set the command and datasheet path on the About
sheet. The functions and the site-file and CSV helpers the macro uses are tested by running the module
in LibreOffice's VBA compatibility mode, where they match the sheet formulas to 1e-9. The Windows shell
call itself has not been run in this repository's tests.
