"""Generic component characteristics for off-design matching.

These are analytic, non-dimensional maps scaled to the design point, so a model
can run off-design before any real map data is available. All inputs and outputs
are relative to design (design point = 1.0). They capture the trends that matter
for installed performance (flow and pressure ratio falling with corrected speed,
choked turbine flow capacity, efficiency fall-off away from design) rather than
any particular machine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

GAMMA_MAP = 1.4  # exponent used only to define the map's head coordinate
_K = (GAMMA_MAP - 1) / GAMMA_MAP


@dataclass(frozen=True)
class CompressorMap:
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

    def surge_pressure_ratio(self, n):
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
