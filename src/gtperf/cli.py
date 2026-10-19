"""Command line entry point: `gtperf design engine.toml`."""

from __future__ import annotations

import argparse
import json
import tomllib

from .components import Combustor, Compressor, Turbine
from .cycle import Ambient, SimpleCycle
from .fuel import Fuel, natural_gas


def _fuel(cfg):
    return Fuel(cfg["mole_fractions"], cfg.get("name", "fuel")) if cfg else None


def engine_from_datasheet(path):
    """Calibrate a twin-shaft model to a datasheet TOML file and build its off-design engine."""
    from .calibrate import calibrate_twin_shaft
    from .offdesign import TwinShaftEngine

    with open(path, "rb") as f:
        d = tomllib.load(f)
    cal = calibrate_twin_shaft(
        d["electrical_power_MW"] * 1e6,
        d["heat_rate_kJ_per_kWh"],
        d["exhaust_flow_kg_s"],
        d["pressure_ratio"],
        exhaust_temp=d.get("exhaust_temperature_C"),
        fuel=_fuel(d.get("fuel")),
        **d.get("assumptions", {}),
    )
    return d, cal, TwinShaftEngine(cal.cycle, cal.air_flow)


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
    excel = sub.add_parser("excel", help="write the Excel site performance tool for a calibrated engine")
    excel.add_argument("datasheet")
    excel.add_argument("output", help="workbook to write, e.g. site_tool.xlsx")
    excel.add_argument("--gtperf-command", default="gtperf", help="how the workbook's RunFullModel macro calls gtperf")
    args = parser.parse_args(argv)

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
    if args.command == "excel":
        from pathlib import Path

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
