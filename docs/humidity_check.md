# Humidity effect: model against published behaviour

Sensitivity per 0.01 kg/kg of humidity ratio, from dry to saturated air at sea level.

| Engine and control | Ambient | Output | Heat rate | Gas generator speed |
|---|---:|---:|---:|---:|
| Twin shaft (SGT-400 model), TIT control | 15 °C | +0.97 % | +0.10 % | +0.32 % |
| Twin shaft (SGT-400 model), TIT control | 30 °C | +0.95 % | +0.08 % | +0.31 % |
| Single shaft, TIT control | 15 °C | -0.18 % | +0.23 % | +0.00 % |
| Single shaft, TIT control | 30 °C | -0.26 % | +0.28 % | +0.00 % |
| Single shaft, exhaust temperature control | 15 °C | -0.60 % | +0.30 % | +0.00 % |
| Single shaft, exhaust temperature control | 30 °C | -1.23 % | +0.48 % | +0.00 % |

**Against GER-3567H.** Single shaft on exhaust temperature control: output falls with humidity, as GE states for heavy-duty units. Twin shaft on gas generator temperature control: output rises and the gas generator speeds up, the mechanism GE gives for aeroderivatives. Both signs agree.

**Against Yu, Lin and Yu (2025).** Their component-level twin-spool model gains about 0.5 % thrust per 0.01 of humidity ratio under temperature control; this model gains about 1 % output per 0.01. The mechanism and sign agree and the size is within a factor of two for a different engine and output measure (thrust of an afterburning turbofan against shaft power of an industrial engine).

**What remains engine-specific.** The size of the effect depends on the compressor map's speed-flow slope and the control law, so for a guarantee it must still come from the OEM's humidity curve, which ASME PTC 22 and ISO 2314 expect the manufacturer to supply.

Sources: F. J. Brooks, *GE Gas Turbine Performance Characteristics*, GER-3567H, GE Power Systems, 2000, pp. 8-9. J. Yu, W. Lin, Y. Yu, Humidity Influence on Aero-Engine Control Plan Inflection Point and Performance, *Machines* 13(9), 860, 2025, https://www.mdpi.com/2075-1702/13/9/860.
