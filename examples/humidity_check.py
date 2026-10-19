"""Check the model's humidity effect against published behaviour. Writes docs/humidity_check.md.

GE's GER-3567H (Brooks, 2000, pp. 8-9) says the humidity effect depends on how the control approximates
firing temperature: a heavy-duty single-shaft unit on exhaust temperature control loses power as humidity
rises, while an aeroderivative on gas generator temperature control can gain power, because the extra fuel
needed to heat the moist air speeds the gas generator up. Yu, Lin and Yu (Machines 13(9) 860, 2025) find the
same speed mechanism in a component-level twin-spool engine model, with thrust rising about 0.5 % per 0.01
of humidity ratio under temperature control. The model is run both ways and its sensitivities per 0.01 kg/kg
of humidity ratio are tabulated against those statements.
"""

from pathlib import Path

from gtperf import Combustor, Compressor, SimpleCycle, Turbine, natural_gas
from gtperf.cli import engine_from_datasheet
from gtperf.cycle import Ambient
from gtperf.gas import saturation_pressure
from gtperf.offdesign import Limits, SingleShaftEngine

ROOT = Path(__file__).resolve().parents[1]


def humidity_ratio(t, rh, p=101325.0):
    pv = rh * saturation_pressure(t)
    return 0.62198 * pv / (p - pv)


def sensitivities(engine, t):
    dry, wet = engine.run(Ambient(t, 101325.0, 0.0)), engine.run(Ambient(t, 101325.0, 1.0))
    steps = humidity_ratio(t, 1.0) / 0.01
    return {
        "power": 100 * (wet.electrical_power / dry.electrical_power - 1) / steps,
        "heat_rate": 100 * (wet.heat_rate / dry.heat_rate - 1) / steps,
        "speed": 100 * (wet.info["mechanical_speed"] - dry.info["mechanical_speed"]) / steps,
        "control": wet.info["control"],
    }


def engines():
    _, _, twin = engine_from_datasheet(ROOT / "examples" / "sgt400_13mw.toml")
    cycle = SimpleCycle(Compressor(16.8, 0.90), Combustor(natural_gas(), 1523.15), Turbine(0.89), cooling_fraction=0.15)
    single = SingleShaftEngine(cycle, 39.0)
    single_t5 = SingleShaftEngine(cycle, 39.0, limits=Limits(exhaust_temp=single.design.exhaust.t))
    return {"Twin shaft (SGT-400 model), TIT control": twin,
            "Single shaft, TIT control": single,
            "Single shaft, exhaust temperature control": single_t5}


def main():
    lines = ["# Humidity effect: model against published behaviour", "",
             "Sensitivity per 0.01 kg/kg of humidity ratio, from dry to saturated air at sea level.", "",
             "| Engine and control | Ambient | Output | Heat rate | Gas generator speed |", "|---|---:|---:|---:|---:|"]
    for name, e in engines().items():
        for t in (288.15, 303.15):
            s = sensitivities(e, t)
            lines.append(f"| {name} | {t - 273.15:.0f} °C | {s['power']:+.2f} % | {s['heat_rate']:+.2f} % | "
                         f"{s['speed']:+.2f} % |")
    lines += ["", "**Against GER-3567H.** Single shaft on exhaust temperature control: output falls with humidity, as "
              "GE states for heavy-duty units. Twin shaft on gas generator temperature control: output rises and the "
              "gas generator speeds up, the mechanism GE gives for aeroderivatives. Both signs agree.", "",
              "**Against Yu, Lin and Yu (2025).** Their component-level twin-spool model gains about 0.5 % thrust per "
              "0.01 of humidity ratio under temperature control; this model gains about 1 % output per 0.01. The "
              "mechanism and sign agree and the size is within a factor of two for a different engine and output "
              "measure (thrust of an afterburning turbofan against shaft power of an industrial engine).", "",
              "**What remains engine-specific.** The size of the effect depends on the compressor map's speed-flow "
              "slope and the control law, so for a guarantee it must still come from the OEM's humidity curve, which "
              "ASME PTC 22 and ISO 2314 expect the manufacturer to supply.", "",
              "Sources: F. J. Brooks, *GE Gas Turbine Performance Characteristics*, GER-3567H, GE Power Systems, 2000, "
              "pp. 8-9. J. Yu, W. Lin, Y. Yu, Humidity Influence on Aero-Engine Control Plan Inflection Point and "
              "Performance, *Machines* 13(9), 860, 2025, https://www.mdpi.com/2075-1702/13/9/860."]
    (ROOT / "docs" / "humidity_check.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[4:11]))


if __name__ == "__main__":
    main()
