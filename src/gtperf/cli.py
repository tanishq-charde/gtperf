"""Command line entry point: `gtperf design engine.toml`."""

from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path

from .aero import Flight
from .components import Combustor, Compressor, Turbine
from .cycle import Ambient, SimpleCycle
from .fuel import Fuel, natural_gas


def _fuel(cfg):
    return Fuel(cfg["mole_fractions"], cfg.get("name", "fuel")) if cfg else None


def _assumptions(d):
    from .cycle import Cooling

    a = dict(d.get("assumptions", {}))
    if isinstance(a.get("cooling"), dict):
        a["cooling"] = Cooling(**a["cooling"])
    return a


def offdesign_options(cfg, pressure_ratio):
    """Compressor map, limits and variable-geometry controls from an [offdesign] TOML table."""
    from .maps import CompressorMap, TabulatedCompressorMap
    from .offdesign import Controls, Generator, Limits, Schedule

    cfg = cfg or {}
    name = cfg.get("compressor_map", "hpc")
    cmap = (CompressorMap(pressure_ratio) if name == "analytic"
            else TabulatedCompressorMap.load(name, pressure_ratio, design_beta=cfg.get("map_design_beta")))
    lim = dict(cfg.get("limits", {}))
    kw = {}
    for key, scale, name_ in (("tit_C", None, "tit"), ("exhaust_temp_C", None, "exhaust_temp"),
                              ("pt_inlet_temp_C", None, "pt_inlet_temp"), ("shaft_power_MW", 1e6, "shaft_power")):
        if key in lim:
            kw[name_] = lim.pop(key) * scale if scale else lim.pop(key) + 273.15
    gen = lim.pop("generator", None)
    if gen:
        kw["generator"] = Generator(gen["rating_MVA"], gen.get("power_factor", 0.85),
                                    Schedule(tuple(map(tuple, gen["derate"]))) if "derate" in gen
                                    else Generator.__dataclass_fields__["derate"].default)
    limits = Limits(**kw, **lim)
    ctl = dict(cfg.get("controls", {}))
    for key in ("vigv", "bleed"):
        if key in ctl:
            ctl[key] = Schedule(tuple(map(tuple, ctl[key])))
    if "exhaust_temp_set_C" in ctl:
        ctl["exhaust_temp_set"] = ctl.pop("exhaust_temp_set_C") + 273.15
    return cmap, limits, Controls(**ctl)


def engine_from_datasheet(path):
    """Calibrate a twin-shaft model to a datasheet TOML file and build its off-design engine."""
    with open(path, "rb") as f:
        d = tomllib.load(f)
    return engine_from_config(d)


def engine_from_config(d):
    """As engine_from_datasheet, from an already loaded datasheet dict."""
    from .calibrate import calibrate_twin_shaft
    from .offdesign import TwinShaftEngine

    cal = calibrate_twin_shaft(
        d["electrical_power_MW"] * 1e6,
        d["heat_rate_kJ_per_kWh"],
        d["exhaust_flow_kg_s"],
        d["pressure_ratio"],
        exhaust_temp=d.get("exhaust_temperature_C"),
        fuel=_fuel(d.get("fuel")),
        **_assumptions(d),
    )
    cmap, limits, controls = offdesign_options(d.get("offdesign"), d["pressure_ratio"])
    return d, cal, TwinShaftEngine(cal.cycle, cal.air_flow, compressor_map=cmap, limits=limits, controls=controls)


def site_from_config(path):
    from .site import Site

    with open(path, "rb") as f:
        s = tomllib.load(f)
    fuel = _fuel(s.pop("fuel", None))
    return Site(fuel=fuel, **s)


def cycle_from_config(cfg):
    fuel_cfg = cfg.get("fuel")
    fuel = Fuel(fuel_cfg["mole_fractions"], fuel_cfg.get("name", "fuel")) if fuel_cfg else natural_gas()
    c, b, t, inst = cfg["compressor"], cfg["combustor"], cfg.get("turbine", {}), cfg.get("installation", {})
    cycle = SimpleCycle(
        Compressor(c["pressure_ratio"], c.get("efficiency", 0.90), c.get("polytropic", True)),
        Combustor(
            fuel,
            b["exit_temperature_C"] + 273.15,
            b.get("pressure_loss", 0.04),
            b.get("efficiency", 0.995),
        ),
        Turbine(t.get("efficiency", 0.89), t.get("polytropic", False)),
        inlet_loss=inst.get("inlet_loss_mbar", 0.0) * 100,
        exhaust_loss=inst.get("exhaust_loss_mbar", 0.0) * 100,
        cooling_fraction=t.get("cooling_fraction", 0.0),
        mechanical_efficiency=cfg.get("mechanical_efficiency", 0.99),
        generator_efficiency=cfg.get("generator_efficiency", 0.985),
    )
    a = cfg.get("ambient", {})
    ambient = Ambient(a.get("temperature_C", 15.0) + 273.15, a.get("pressure_bar", 1.01325) * 1e5, a.get("rh", 0.60))
    return cycle, ambient


def turbofan_from_config(cfg):
    from .aero import Flight, Turbofan

    c, e, loss = cfg["cycle"], cfg.get("efficiency", {}), cfg.get("losses", {})
    kw = {k: e[n] for k, n in (("fan_eff", "fan"), ("booster_eff", "booster"), ("hpc_eff", "hpc"),
                               ("hpt_eff", "hpt"), ("lpt_eff", "lpt")) if n in e}
    kw.update({k: v for k, v in loss.items() if k != "hp_offtake_kW"})
    if "hp_offtake_kW" in loss:
        kw["hp_offtake"] = loss["hp_offtake_kW"] * 1e3
    engine = Turbofan(bpr=c["bpr"], fan_pr=c["fan_pr"], fan_core_pr=c.get("fan_core_pr"), hpc_pr=c["hpc_pr"],
                      tet=c["tet_K"], **kw)
    return engine, Flight(**cfg.get("flight", {}))


OFFDESIGN_COLUMNS = ("altitude_m", "mach", "control", "net_thrust_kN", "tsfc_mg_per_Ns", "air_flow_kg_s",
                     "bypass_ratio", "tet_K", "n1", "n2", "surge_margin_fan", "surge_margin_booster",
                     "surge_margin_hpc")


def turbofan_offdesign_table(engine, points):
    """Off-design points for `gtperf turbofan --offdesign`. Each point has altitude_m, mach, optional
    isa_dt and at most one of tet_K, n1 or net_thrust_kN (default: the design TET)."""
    rows, guess = [], None
    for pt in points:
        flight = Flight(pt.get("altitude_m", 0.0), pt.get("mach", 0.0), pt.get("isa_dt", 0.0))
        setting = {k: pt[c] * f for k, c, f in (("tet", "tet_K", 1), ("n1", "n1", 1), ("thrust", "net_thrust_kN", 1e3))
                   if c in pt}
        r = engine.run(flight, guess=guess, **setting)
        guess = r.info["x"]
        s = r.summary()
        rows.append({"altitude_m": flight.altitude_m, "mach": flight.mach, "control": r.info["control"],
                     **{k: s[k] for k in ("net_thrust_kN", "tsfc_mg_per_Ns", "air_flow_kg_s", "bypass_ratio", "tet_K")},
                     **{k: r.info[k] for k in OFFDESIGN_COLUMNS[8:]}})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(prog="gtperf", description="Gas turbine performance toolkit")
    sub = parser.add_subparsers(dest="command", required=True)
    design = sub.add_parser("design", help="run a design-point cycle from a TOML file")
    design.add_argument("config")
    design.add_argument("--json", action="store_true", help="print the summary as JSON")
    cal = sub.add_parser("calibrate", help="calibrate to a datasheet TOML and compare")
    cal.add_argument("datasheet")
    site = sub.add_parser("site", help="site performance sheet for a calibrated engine")
    site.add_argument("datasheet")
    site.add_argument("site")
    site.add_argument("--csv", action="store_true", help="print the summary table as CSV")
    curves = sub.add_parser("curves", help="ambient temperature correction curves as CSV")
    curves.add_argument("datasheet")
    curves.add_argument("--from-C", type=float, default=-20.0)
    curves.add_argument("--to-C", type=float, default=45.0)
    curves.add_argument("--step", type=float, default=5.0)
    fan = sub.add_parser("turbofan", help="two-spool turbofan design point from a TOML file")
    fan.add_argument("config")
    fan.add_argument("--json", action="store_true", help="print the summary as JSON")
    fan.add_argument("--offdesign", action="store_true",
                     help="match the engine off design at the [offdesign] points (default: a few flight conditions)")
    gui = sub.add_parser("gui", help="open the GTPerf graphical interface (beta) in a browser")
    gui.add_argument("--port", type=int, default=8501)
    excel = sub.add_parser("excel", help="write the Excel site performance tool for a calibrated engine")
    excel.add_argument("datasheet")
    excel.add_argument("output", help="workbook to write, e.g. site_tool.xlsx")
    excel.add_argument("--gtperf-command", default="gtperf", help="how the workbook's RunFullModel macro calls gtperf")
    args = parser.parse_args(argv)

    if args.command == "gui":
        import subprocess
        import sys

        app = Path(__file__).resolve().parent / "app" / "ui.py"
        return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(args.port),
                                "--browser.gatherUsageStats", "false"])
    if args.command == "calibrate":
        d, c, _ = engine_from_datasheet(args.datasheet)
        print(f"{d.get('name', 'engine')}\n")
        print(c.table())
        print(f"\nFitted: air flow {c.air_flow:.2f} kg/s, TIT {c.tit - 273.15:.1f} °C, "
              f"turbine isentropic efficiency {c.turbine_efficiency:.4f}")
        return
    if args.command == "site":
        from .site import site_performance

        _, _, engine = engine_from_datasheet(args.datasheet)
        perf = site_performance(engine, site_from_config(args.site))
        print(perf.to_csv() if args.csv else perf.to_markdown())
        return
    if args.command == "turbofan":
        with open(args.config, "rb") as f:
            cfg = tomllib.load(f)
        engine, flight = turbofan_from_config(cfg)
        w = cfg.get("air_flow_kg_s") or engine.size_for_thrust(cfg["net_thrust_kN"] * 1e3, flight)
        if args.offdesign:
            from .aero_offdesign import TurbofanEngine

            od = TurbofanEngine(engine, w, flight)
            points = cfg.get("offdesign", {}).get("points") or [
                {"altitude_m": flight.altitude_m, "mach": flight.mach}, {"altitude_m": 0.0, "mach": 0.0},
                {"altitude_m": 0.0, "mach": 0.25}, {"altitude_m": 5000.0, "mach": 0.6}]
            rows = turbofan_offdesign_table(od, points)
            if args.json:
                print(json.dumps(rows, indent=2))
            else:
                print(f"{cfg.get('name', 'turbofan')}: off-design\n")
                print("".join(f"{c[:12]:>13s}" for c in OFFDESIGN_COLUMNS))
                for r in rows:
                    print("".join(f"{v:>13s}" if isinstance(v, str) else f"{v:13.4g}" for v in r.values()))
            return
        result = engine.run(w, flight)
        if args.json:
            print(json.dumps(result.summary(), indent=2))
        else:
            print(f"{cfg.get('name', 'turbofan')}\n")
            print(result)
        return
    if args.command == "excel":

        from .excel import build_workbook

        d, _, engine = engine_from_datasheet(args.datasheet)
        build_workbook(engine, args.output, name=d.get("name", "engine"), source=d.get("source", ""),
                       datasheet_path=Path(args.datasheet).resolve(), command=args.gtperf_command)
        print(f"Wrote {args.output}")
        return
    if args.command == "curves":
        import csv
        import sys

        from .site import correction_curves

        _, _, engine = engine_from_datasheet(args.datasheet)
        temps, t = [], args.from_C
        while t <= args.to_C + 1e-9:
            temps.append(t)
            t += args.step
        cc = correction_curves(engine, temps)
        w = csv.writer(sys.stdout)
        w.writerow(cc.keys())
        w.writerows(zip(*cc.values()))
        return

    with open(args.config, "rb") as f:
        cfg = tomllib.load(f)
    cycle, ambient = cycle_from_config(cfg)
    if "air_flow_kg_s" in cfg:
        air_flow = cfg["air_flow_kg_s"]
    else:
        air_flow = cycle.size_for_power(cfg["electrical_power_MW"] * 1e6, ambient)
    result = cycle.run(air_flow, ambient)
    if args.json:
        print(json.dumps(result.summary(), indent=2))
    else:
        print(f"{cfg.get('name', 'engine')}\n")
        print(result)


if __name__ == "__main__":
    main()
