"""Parametric studies over design-point cycle inputs."""

from __future__ import annotations

import copy

import numpy as np


def sweep(cycle, pressure_ratios, tits, ambient=None, air_flow=1.0):
    """Run `cycle` over a grid of pressure ratio and combustor exit temperature (K).

    Returns a dict of 2-D arrays indexed [tit, pressure_ratio]: thermal efficiency (-),
    specific work (J/kg) and exhaust temperature (K).
    """
    kwargs = {} if ambient is None else {"ambient": ambient}
    shape = (len(tits), len(pressure_ratios))
    out = {k: np.empty(shape) for k in ("efficiency", "specific_work", "exhaust_temp")}
    c = copy.deepcopy(cycle)
    for i, tit in enumerate(tits):
        c.combustor.t_out = tit
        for j, pr in enumerate(pressure_ratios):
            c.compressor.pressure_ratio = pr
            r = c.run(air_flow, **kwargs)
            out["efficiency"][i, j] = r.thermal_efficiency
            out["specific_work"][i, j] = r.specific_work
            out["exhaust_temp"][i, j] = r.exhaust.t
    return out
