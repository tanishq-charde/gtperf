"""Command line entry point: `gtperf design engine.toml`."""

from __future__ import annotations

import argparse
import json
import tomllib

from .components import Combustor, Compressor, Turbine
from .cycle import Ambient, SimpleCycle
from .fuel import Fuel, natural_gas


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
    args = parser.parse_args(argv)

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
