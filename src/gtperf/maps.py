"""Component characteristics for off-design matching.

Two kinds of compressor map share one interface:

- `TabulatedCompressorMap`, real map data on speed lines and R-lines (beta lines), such as the public
  NPSS maps in `gtperf/data/maps`, scaled to the engine's design point in the usual way;
- `CompressorMap`, an analytic characteristic for quick studies when no map is available.

Both are used through relative coordinates: corrected speed n and corrected flow g are 1.0 at the
design point, efficiency is relative to design, and the pressure ratio is absolute. Operating points
are located by speed and R-line, because a speed line can be vertical at choke, where flow alone does
not fix the point. R-line 1 is the surge line and R-line 3 the choke end.

Variable inlet guide vanes enter through `VaneEffect`: closing the vanes (negative angle) shifts each
speed line to lower flow and pressure ratio and costs some efficiency.

`TabulatedTurbineMap` gives turbine flow capacity and efficiency against speed and pressure ratio. The
industrial engines default to the Stodola ellipse law and a blade-speed-ratio efficiency, which are
physical laws rather than fitted maps; a tabulated turbine map can replace either.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from functools import cached_property
from importlib import resources

import numpy as np
from scipy.interpolate import RectBivariateSpline
from scipy.optimize import brentq

GAMMA_MAP = 1.4  # exponent used only to define the map's head coordinate
_K = (GAMMA_MAP - 1) / GAMMA_MAP


BETA_SURGE, BETA_CHOKE = 1.0, 3.0


@dataclass(frozen=True)
class VaneEffect:
    """Effect of variable inlet guide vane angle (degrees, negative = closed) on a compressor map.

    Corrected flow and pressure-ratio head at a given speed and R-line scale linearly with angle and
    efficiency falls with its square. The defaults are representative of a single row of industrial
    VIGVs (about 27 % less flow at 30 degrees closed); real values come from rig data.
    """

    flow_per_deg: float = 0.009
    head_per_deg: float = 0.004
    eff_per_deg2: float = 4e-5

    def factors(self, angle):
        return 1 + self.flow_per_deg * angle, 1 + self.head_per_deg * angle, self.eff_per_deg2 * angle * angle


class _MapBase:
    """Shared R-line interface. Subclasses provide `_raw(n, beta)` -> (g, pr, eff_rel) with no vanes."""

    vanes: VaneEffect

    def point(self, n, beta, vigv=0.0):
        """Relative corrected flow, pressure ratio and relative efficiency at speed n and R-line beta."""
        g, pr, eff = self._raw(n, beta)
        fw, fh, de = self.vanes.factors(vigv)
        h = (pr**_K - 1) * fh
        return g * fw, (1 + max(h, 1e-6)) ** (1 / _K), eff - de

    def surge_pressure_ratio(self, n, vigv=0.0):
        return self.point(n, BETA_SURGE, vigv)[1]

    def surge_margin_at(self, n, beta, vigv=0.0):
        """Constant-speed surge margin, (PR_surge - PR) / PR."""
        pr = self.point(n, beta, vigv)[1]
        return (self.surge_pressure_ratio(n, vigv) - pr) / pr


@dataclass(frozen=True)
class CompressorMap(_MapBase):
    """Axial compressor characteristic in relative corrected speed n and flow g.

    Each speed line is a curve of head h = PR**k - 1 against corrected flow, peaking
    at the surge line and falling to zero at choke:

        h / h_design = peak_head * n**2 * (1 - ((g - g_surge) / (g_choke - g_surge))**shape)

    with g_surge = surge_flow * n**flow_exponent and g_choke = choke_flow * n**flow_exponent.
    Isentropic efficiency peaks on the design flow-to-speed ratio and falls off
    parabolically in speed and in incidence.
    """

    design_pr: float
    surge_flow: float = 0.95
    choke_flow: float = 1.04
    flow_exponent: float = 1.5
    shape: float = 4.0
    eff_speed_coeff: float = 0.5
    eff_incidence_coeff: float = 3.0
    vanes: VaneEffect = field(default_factory=VaneEffect)
    design_beta: float = 2.0  # only fixes how R-lines are laid out; the design point is always g = 1

    @property
    def _x_design(self):
        return (1 - self.surge_flow) / (self.choke_flow - self.surge_flow)

    @property
    def peak_head(self):
        return 1 / (1 - self._x_design**self.shape)

    def _x(self, n, g):
        nf = n**self.flow_exponent
        return (g / nf - self.surge_flow) / (self.choke_flow - self.surge_flow)

    def pressure_ratio(self, n, g):
        x = max(self._x(n, g), 0.0)  # flat left of surge, keeps solvers smooth
        h_rel = self.peak_head * n**2 * (1 - x**self.shape)
        h = max(h_rel * (self.design_pr**_K - 1), 1e-6)
        return (1 + h) ** (1 / _K)

    def surge_pressure_ratio(self, n, vigv=0.0):
        if vigv:
            return super().surge_pressure_ratio(n, vigv)
        h = self.peak_head * n**2 * (self.design_pr**_K - 1)
        return (1 + h) ** (1 / _K)

    def surge_margin(self, n, g):
        """Constant-speed surge margin, (PR_surge - PR) / PR."""
        pr = self.pressure_ratio(n, g)
        return (self.surge_pressure_ratio(n) - pr) / pr

    def efficiency(self, n, g):
        """Isentropic efficiency relative to design."""
        incidence = g / n**self.flow_exponent - 1
        return (1 - self.eff_speed_coeff * (1 - n) ** 2) * (1 - self.eff_incidence_coeff * incidence**2)


    def _g_from_beta(self, n, beta):
        """R-lines are spaced evenly in flow between the surge line (beta 1) and choke (beta 3),
        with the design flow on R-line `design_beta` at design speed."""
        nf = n**self.flow_exponent
        x = (beta - BETA_SURGE) / (BETA_CHOKE - BETA_SURGE)
        xd = (self.design_beta - BETA_SURGE) / (BETA_CHOKE - BETA_SURGE)
        lo, hi = self._beta_flow_limits()
        g_design = lo + xd * (hi - lo)
        return nf * (lo + x * (hi - lo)) / g_design

    def _beta_flow_limits(self):
        return self.surge_flow, self.choke_flow

    def _raw(self, n, beta):
        g = self._g_from_beta(n, beta)
        return g, self.pressure_ratio(n, g), self.efficiency(n, g)

    def beta_at_design(self):
        """R-line of the design point (g = 1 at n = 1)."""
        return brentq(lambda b: self._g_from_beta(1.0, b) - 1.0, 0.0, 4.0)


@dataclass(frozen=True)
class TabulatedCompressorMap(_MapBase):
    """Compressor map on speed lines and R-lines, scaled so that the map's design point gives the
    engine's design pressure ratio, with relative flow, speed and efficiency of 1.0 there.

    Pressure ratio scales as (PR - 1) in proportion, the usual map-scaling rule. Away from the tabulated
    R-line range the map is extended linearly in R-line, so solvers stay smooth past surge or choke;
    speeds outside the table are extrapolated by the spline and should be treated with care.
    """

    nc: tuple
    beta: tuple
    wc: tuple
    pr: tuple
    eff: tuple
    design_pr: float
    design_nc: float
    design_beta: float
    source: str = ""
    vanes: VaneEffect = field(default_factory=VaneEffect)

    @classmethod
    def load(cls, name, design_pr, design_beta=None, design_nc=None, **kw):
        """Load a packaged map ('hpc', 'lpc' or 'fan') or a JSON file path, scaled to design_pr."""
        if name.endswith(".json"):
            with open(name, encoding="utf-8") as f:
                d = json.load(f)
        else:
            d = json.loads(resources.files("gtperf.data.maps").joinpath(f"{name}.json").read_text())
        if d["kind"] != "compressor":
            raise ValueError(f"{name} is not a compressor map")
        as_tuple = lambda a: tuple(map(tuple, a))  # noqa: E731
        return cls(tuple(d["Nc"]), tuple(d["Rline"]), as_tuple(d["Wc"]), as_tuple(d["PR"]), as_tuple(d["eff"]),
                   design_pr=design_pr, design_nc=design_nc or d["design"]["Nc"],
                   design_beta=design_beta or d["design"]["Rline"], source=d.get("source", name), **kw)

    @cached_property
    def _splines(self):
        x, y = np.asarray(self.nc), np.asarray(self.beta)
        return tuple(RectBivariateSpline(x, y, np.asarray(z), kx=3, ky=3) for z in (self.wc, self.pr, self.eff))

    @cached_property
    def _design(self):
        return tuple(float(s(self.design_nc, self.design_beta)[0, 0]) for s in self._splines)

    def _eval(self, spline, nc, beta):
        lo, hi = self.beta[0], self.beta[-1]
        b = min(max(beta, lo), hi)
        v = float(spline(nc, b)[0, 0])
        if b != beta:
            v += float(spline(nc, b, dy=1)[0, 0]) * (beta - b)
        return v

    def _raw(self, n, beta):
        nc = n * self.design_nc
        w_d, pr_d, eff_d = self._design
        wc, pr, eff = (self._eval(s, nc, beta) for s in self._splines)
        scale = (self.design_pr - 1) / (pr_d - 1)
        return wc / w_d, 1 + (pr - 1) * scale, eff / eff_d

    def beta_at_design(self):
        return self.design_beta


@dataclass(frozen=True)
class TabulatedTurbineMap:
    """Turbine map: flow capacity and efficiency against speed (percent) and pressure ratio, both
    relative to the map's design point, with pressure ratio scaled as (PR - 1) in proportion."""

    np_: tuple
    pr: tuple
    wp: tuple
    eff: tuple
    design_pr: float
    design_np: float
    design_map_pr: float
    source: str = ""

    @classmethod
    def load(cls, name, design_pr, **kw):
        if name.endswith(".json"):
            with open(name, encoding="utf-8") as f:
                d = json.load(f)
        else:
            d = json.loads(resources.files("gtperf.data.maps").joinpath(f"{name}.json").read_text())
        if d["kind"] != "turbine":
            raise ValueError(f"{name} is not a turbine map")
        as_tuple = lambda a: tuple(map(tuple, a))  # noqa: E731
        return cls(tuple(d["Np"]), tuple(d["PR"]), as_tuple(d["Wp"]), as_tuple(d["eff"]), design_pr=design_pr,
                   design_np=d["design"]["Np"], design_map_pr=d["design"]["PR"], source=d.get("source", name), **kw)

    @cached_property
    def _splines(self):
        x, y = np.asarray(self.np_), np.asarray(self.pr)
        return tuple(RectBivariateSpline(x, y, np.asarray(z), kx=3, ky=3) for z in (self.wp, self.eff))

    def _map_pr(self, pr):
        return 1 + (pr - 1) * (self.design_map_pr - 1) / (self.design_pr - 1)

    def _eval(self, spline, n, pr):
        x = min(max(n * self.design_np, self.np_[0]), self.np_[-1])
        y = self._map_pr(pr)
        yc = min(max(y, self.pr[0]), self.pr[-1])
        v = float(spline(x, yc)[0, 0])
        if yc != y:
            v += float(spline(x, yc, dy=1)[0, 0]) * (y - yc)
        return v

    def capacity(self, n, pr):
        """Flow capacity relative to design."""
        return self._eval(self._splines[0], n, pr) / self._eval(self._splines[0], 1.0, self.design_pr)

    def efficiency(self, n, pr):
        """Isentropic efficiency relative to design."""
        return self._eval(self._splines[1], n, pr) / self._eval(self._splines[1], 1.0, self.design_pr)


def ellipse_capacity(pr):
    """Stodola ellipse law: flow capacity W*sqrt(T)/P is proportional to sqrt(1 - 1/PR**2)."""
    return math.sqrt(max(1 - 1 / pr**2, 1e-12))


def velocity_ratio_efficiency(r):
    """Relative turbine efficiency against blade-speed ratio U/C relative to design: 2r - r**2."""
    return 2 * r - r * r


@dataclass(frozen=True)
class Health:
    """Component health deltas, used for degradation and gas-path studies.

    flow_scale multiplies compressor corrected flow (fouling < 1). The efficiency
    deltas are added to isentropic efficiencies (e.g. -0.01 for one point).
    """

    compressor_flow_scale: float = 1.0
    compressor_eff_delta: float = 0.0
    turbine_eff_delta: float = 0.0
    turbine_capacity_scale: float = 1.0


NEW_AND_CLEAN = Health()
