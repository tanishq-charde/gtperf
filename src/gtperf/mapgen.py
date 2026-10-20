"""Compressor and turbine maps generated from the engine's own design, by mean-line stage stacking.

No stored map is scaled: each map is calculated from the component's design pressure ratio and efficiency
plus a few stage design values, so a different design gives differently shaped speed lines, surge line,
choke and efficiency islands.

**Compressor.** The design temperature rise is split equally over `stages` repeating stages at constant mean
blade speed U, set by the stage loading psi = dh0 / U**2; the axial velocity is the flow coefficient
phi = Va / U times U, and the reaction fixes the velocity triangles. Each blade row's exit angle is held
at its design value off design (constant deviation), which gives the linear Euler characteristic
psi = 1 - phi (tan alpha1 + tan beta2) for each stage. Annulus areas are sized at the design point and then
fixed, so off design each stage's axial velocity follows from continuity at its own inlet density: this
is what moves front and rear stages apart at low speed. Losses are a design-point enthalpy loss for each
stage (from the polytropic efficiency) scaled by the blade-row dynamic heads and by 1 + (i / i_range)**2
for the incidence i of each row. A blade row stalls when its Lieblein diffusion factor exceeds `df_limit`.
Surge is either the stall of a row in the rear third, which in a multistage compressor at high speed sets off surge
directly, or the peak of the speed line (the zero-slope stability limit), whichever comes at the higher
flow. Front rows can run stalled at part speed (rotating stall) below that; the charts mark where. The VIGV and the next
`variable_rows - 1` stators turn with corrected speed on the given schedule, each later row by a smaller
angle, which is how a high pressure ratio compressor keeps its front stages out of stall at part speed;
the map therefore already contains the vane schedule. Choke is any stage inlet reaching sonic velocity; the speed line
is drawn until the efficiency has fallen to 60 % of design on the choke side.

**Turbine.** The design work is split equally over `stages` repeating stages from the stage loading
psi = dh0 / U**2, flow coefficient and reaction. The nozzle exit angle and rotor relative exit angle are
held fixed and the nozzle and rotor throat areas are sized at design; off design each row passes the flow
by continuity at its exit, with enthalpy loss coefficients calibrated to the design efficiency and raised
by incidence (rotor inlet, and each later nozzle inlet). Choke is any row reaching its maximum mass flux.
At pressure ratios above choke the flow capacity stays fixed and the extra expansion past the throat does
progressively less work (supersonic deviation towards limit loading): efficiency falls as
eta_choke (1 - 0.5 x**2), with x the extra isentropic enthalpy drop as a fraction of the drop at choke.

The gas is a perfect gas (gamma 1.4 in compressors, 1.33 in turbines). The maps are returned in the
relative form of `TabulatedCompressorMap` and `TabulatedTurbineMap`, so the matching code is unchanged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from scipy.optimize import brentq, minimize_scalar

from .maps import TabulatedCompressorMap, TabulatedTurbineMap

T_REF, P_REF = 288.15, 101325.0
COMPRESSOR_SPEEDS = tuple(round(x, 3) for x in np.arange(0.15, 1.1501, 0.05))
TURBINE_SPEEDS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1)
N_RLINES = 41
BETA_MIN = -2.0
POST_CHOKE_LOSS = 0.5  # turbine efficiency past choke: eta_choke * (1 - c x**2), x = extra isentropic drop


@dataclass(frozen=True)
class CompressorStages:
    """Stage design values of an axial compressor, and its variable stator schedule."""

    stages: int = 11
    loading: float = 0.40  # psi = dh0 / U**2
    flow_coefficient: float = 0.50  # phi = Va / U
    reaction: float = 0.50
    solidity: float = 1.5  # chord / pitch, for the diffusion factor
    df_limit: float = 0.60  # Lieblein diffusion factor at stall
    incidence_range: float = 8.0  # degrees: incidence that doubles a row's loss
    variable_rows: int = 3  # VIGV plus variable stators, front of the compressor
    vane_closed: float = -35.0  # degrees (negative = closed) of the VIGV when fully closed
    vane_open_speed: float = 0.95  # relative corrected speed above which the vanes are fully open
    vane_closed_speed: float = 0.85  # and below which they are fully closed

    def vane_angle(self, n):
        """VIGV angle (degrees) at relative corrected speed n; later variable rows close proportionally less."""
        if self.variable_rows < 1 or self.vane_open_speed <= self.vane_closed_speed:
            return 0.0
        x = (self.vane_open_speed - n) / (self.vane_open_speed - self.vane_closed_speed)
        return self.vane_closed * min(max(x, 0.0), 1.0)


@dataclass(frozen=True)
class TurbineStages:
    """Stage design values of an axial turbine."""

    stages: int = 2
    loading: float = 1.5  # psi = dh0 / U**2
    flow_coefficient: float = 0.60
    reaction: float = 0.50
    incidence_range: float = 20.0  # degrees


class _Gas:
    def __init__(self, gamma, cp):
        self.g, self.cp = gamma, cp
        self.R = cp * (gamma - 1) / gamma
        self.k = (gamma - 1) / gamma


# --- compressor -------------------------------------------------------------------------------------

class _StackedCompressor:
    def __init__(self, pressure_ratio, polytropic_eff, s: CompressorStages):
        if s.stages < 1:
            raise ValueError("a compressor needs at least one stage")
        self.s, self.gas = s, _Gas(1.4, 1004.5)
        gas, n = self.gas, int(s.stages)
        tau = pressure_ratio ** (gas.k / polytropic_eff)
        self.dh = gas.cp * T_REF * (tau - 1) / n
        self.u = math.sqrt(self.dh / s.loading)
        phi, psi, r = s.flow_coefficient, s.loading, s.reaction
        self.ta1 = (1 - r - psi / 2) / phi  # stator exit (stage inlet) swirl angle at design
        self.tb2 = (r - psi / 2) / phi  # rotor relative exit angle, fixed
        self.b1d = math.atan((1 - phi * self.ta1) / phi)
        self.a2d = math.atan((1 - phi * self.tb2) / phi)
        va = phi * self.u
        t0, p0, self.area, self.loss, self.head = T_REF, P_REF, [], [], []
        for _ in range(n):
            t = t0 - va * va * (1 + self.ta1**2) / (2 * gas.cp)
            p = p0 * (t / t0) ** (1 / gas.k)
            self.area.append(1.0 / (p / (gas.R * t) * va))  # sized for 1 kg/s
            tau_s = 1 + self.dh / (gas.cp * t0)
            eff = (tau_s ** polytropic_eff - 1) / (tau_s - 1)  # stage pr**k = tau**eta_p
            self.loss.append((1 - eff) * self.dh)
            self.head.append(self._rows(va, self.u, self.ta1, self.ta1)[0])
            p0 *= tau_s ** (polytropic_eff / gas.k)
            t0 *= tau_s
        self.pr_design = p0 / P_REF

    def _inlet_swirl(self, n):
        """tan(alpha1) at each stage inlet with the variable rows at their scheduled angle."""
        s, angle = self.s, self.s.vane_angle(n)
        a1 = math.atan(self.ta1)
        k = max(int(s.variable_rows), 1)
        return [math.tan(a1 - math.radians(angle) * (1 - j / k)) if j < s.variable_rows else self.ta1
                for j in range(int(s.stages))]

    def _rows(self, va, u, ta_in, ta_out):
        """Incidence-weighted dynamic head (rotor inlet relative plus stator inlet absolute) and the larger
        Lieblein diffusion factor of the stage's two rows, for inlet and stator exit swirl tangents."""
        wt1, wt2 = u - va * ta_in, va * self.tb2
        vt2, vt3 = u - wt2, va * ta_out
        w1, w2 = math.hypot(va, wt1), math.hypot(va, wt2)
        v2, v3 = math.hypot(va, vt2), math.hypot(va, vt3)
        i_r = math.degrees(math.atan2(wt1, va) - self.b1d)
        i_s = math.degrees(math.atan2(vt2, va) - self.a2d)
        f = lambda i: 1 + (i / self.s.incidence_range) ** 2  # noqa: E731
        sig = self.s.solidity
        df = max(1 - w2 / w1 + abs(wt1 - wt2) / (2 * sig * w1), 1 - v3 / v2 + abs(vt2 - vt3) / (2 * sig * v2))
        return 0.5 * (w1 * w1 * f(i_r) + v2 * v2 * f(i_s)), df

    def _axial_velocity(self, flux, t0, p0, ta):
        gas = self.gas
        c2 = 1 + ta * ta
        a_star = math.sqrt(2 * gas.g / (gas.g + 1) * gas.R * t0)
        va_max = a_star / math.sqrt(c2)

        def mass_flux(va):
            t = t0 - va * va * c2 / (2 * gas.cp)
            return p0 * (t / t0) ** (1 / gas.k) / (gas.R * t) * va

        if flux >= mass_flux(va_max):
            return None
        return brentq(lambda v: mass_flux(v) - flux, 0.0, va_max, xtol=1e-9)

    def point(self, n, w):
        """Pressure ratio, isentropic efficiency and stall margins (df_limit / largest diffusion factor - 1,
        negative when a row is stalled) of all rows and of the rear third, at relative speed n and relative
        corrected flow w; None when a stage chokes."""
        gas, u = self.gas, n * self.u
        swirl = self._inlet_swirl(n)
        t0, p0, df_max, df_rear = T_REF, P_REF, 0.0, 0.0
        rear = (2 * len(self.area)) // 3
        for j, (area, loss, head) in enumerate(zip(self.area, self.loss, self.head)):
            ta = swirl[j]
            va = self._axial_velocity(w / area, t0, p0, ta)
            if va is None:
                return None
            dh = u * (u - va * (ta + self.tb2))
            h, df = self._rows(va, u, ta, swirl[j + 1] if j + 1 < len(swirl) else self.ta1)
            df_max = max(df_max, df)
            if j >= rear:
                df_rear = max(df_rear, df)
            ratio = 1 + (dh - loss * h / head) / (gas.cp * t0)
            if ratio <= 0.05:
                return None
            p0 *= ratio ** (1 / gas.k)
            t0 += dh / gas.cp
        pr = p0 / P_REF
        tau = t0 / T_REF
        eff = (pr**gas.k - 1) / (tau - 1) if abs(tau - 1) > 1e-9 else 0.0
        return pr, eff, self.s.df_limit / max(df_max, 1e-6) - 1, self.s.df_limit / max(df_rear, 1e-6) - 1


def _bisect(ok, lo, hi, iters=30):
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if ok(mid) else (lo, mid)
    return lo


def _speed_line(c, n):
    """Surge flow, choke-end flow and whether a blade row is past its stall limit along the whole stable
    part of the line, for one speed."""
    hi = 0.05
    while c.point(n, hi) is not None and hi < 10:
        hi *= 1.5
    if c.point(n, 0.02) is None:
        return None
    w_choke = _bisect(lambda w: c.point(n, w) is not None, 0.02, hi) * 0.9995
    ws = 0.02 + (w_choke - 0.02) * (1 - (1 - np.linspace(0.0, 1.0, 40)) ** 2)
    pts = [c.point(n, w) for w in ws]
    prs = np.array([p[0] for p in pts])
    margins = np.array([p[2] for p in pts])
    i_peak = int(np.argmax(prs))
    w_peak = ws[i_peak]
    if i_peak == 0 or i_peak == len(ws) - 1:
        w_peak = ws[i_peak]
    else:  # refine the peak of the characteristic
        w_peak = minimize_scalar(lambda w: -c.point(n, w)[0], bounds=(ws[i_peak - 1], ws[i_peak + 1]),
                                 method="bounded", options={"xatol": 1e-7}).x
    rear = np.array([p[3] for p in pts])
    w_surge = w_peak
    clear = np.nonzero((rear >= 0) & (ws > w_peak))[0]
    if len(clear) and rear[ws >= w_peak][0] < 0:  # a rear row stalls first: that is the surge point
        i = clear[0]
        w_surge = _bisect(lambda w: c.point(n, w)[3] < 0, ws[i - 1], ws[i])
    stalled = bool(margins[ws >= w_surge].max() < 0)  # a row past its diffusion limit all along the line
    for surge in (w_surge, w_peak):  # a rear-row stall right at choke leaves no line: fall back to the peak
        pr_surge = c.point(n, surge)[0]
        # the choke end: where the line has dropped to half its pressure rise
        end_ok = lambda w, pr_surge=pr_surge: c.point(n, w)[0] - 1 > 0.5 * (pr_surge - 1)  # noqa: E731
        w_end = w_choke if end_ok(w_choke) else _bisect(end_ok, surge, w_choke)
        if w_end > surge * 1.002:
            return surge, w_end, stalled
    return None


@lru_cache(maxsize=32)
def compressor_map(pressure_ratio, polytropic_eff, stages=None, speeds=COMPRESSOR_SPEEDS):
    """A `TabulatedCompressorMap` calculated by stage stacking. R-line 1 is the surge line and R-line 3 the
    choke end of each speed line; R-lines down to -2 continue past surge. Its `info` dict holds, by speed,
    the surge flow and pressure ratio, the choke-end flow and whether a row is in rotating stall along the
    whole line; `stall_margin(n, w)` gives the diffusion-factor margin of the worst row at any point."""
    stages = stages or CompressorStages()
    c = _StackedCompressor(pressure_ratio, polytropic_eff, stages)
    rows = []
    for n in speeds:
        line = _speed_line(c, n)
        if line:
            rows.append((n, *line))
    # past design speed, a line whose surge pressure ratio jumps (the rear-row stall and the peak changing
    # places) would bend the interpolated map: the map ends at the last smooth line
    for i in range(len(rows) - 1, 0, -1):
        if rows[i][0] > 1 and c.point(rows[i][0], rows[i][1])[0] > 1.25 * c.point(rows[i - 1][0], rows[i - 1][1])[0]:
            rows = rows[:i]
    kept = [r[0] for r in rows]
    if not any(abs(n - 1) < 1e-9 for n in kept):
        raise ValueError("the compressor has no stable operating range at its own design speed: change the "
                         "stage loading, flow coefficient or number of stages")
    # R-lines below 1 continue each line past surge down to 55 % of the surge flow, so a solver that strays
    # there (or a fan at low power) stays on calculated values; the flow is a smooth function of R-line
    beta = np.linspace(BETA_MIN, 3.0, N_RLINES)
    wc, pr, eff, surge = [], [], [], []
    for n, w_s, w_end, stalled in rows:
        a = (w_end - w_s) / 2
        b = (3 * a - 0.45 * w_s) / 9
        ws = np.where(beta >= 1, w_s + a * (beta - 1), w_s + a * (beta - 1) + b * (beta - 1) ** 2)
        pts = [c.point(n, w) for w in ws]
        wc.append(list(ws))
        pr.append([p[0] for p in pts])
        eff.append([max(p[1], 0.05) for p in pts])
        surge.append((n, w_s, c.point(n, w_s)[0], w_end, stalled))
    _, w_s1, w_c1, _ = rows[kept.index(next(n for n in kept if abs(n - 1) < 1e-9))]
    beta_design = 1 + 2 * (1 - w_s1) / (w_c1 - w_s1)
    m = TabulatedCompressorMap(tuple(kept), tuple(beta), tuple(map(tuple, wc)), tuple(map(tuple, pr)),
                               tuple(map(tuple, eff)), design_pr=pressure_ratio, design_nc=1.0,
                               design_beta=float(beta_design), source="stage stacking")
    object.__setattr__(m, "info", {"surge": surge, "stages": stages, "blade_speed": c.u,
                                   "design_pr_stacked": c.pr_design,
                                   "stall_margin": lambda n, w: (c.point(n, w) or (0, 0, np.nan))[2]})
    return m


# --- turbine ----------------------------------------------------------------------------------------

class _StackedTurbine:
    def __init__(self, pressure_ratio, isentropic_eff, s: TurbineStages, t_in):
        if s.stages < 1:
            raise ValueError("a turbine needs at least one stage")
        self.s, self.gas, self.t_in = s, _Gas(1.33, 1150.0), t_in
        gas, n = self.gas, int(s.stages)
        dh_total = gas.cp * t_in * isentropic_eff * (1 - pressure_ratio ** (-gas.k))
        self.u = math.sqrt(dh_total / n / s.loading)
        phi, psi, r = s.flow_coefficient, s.loading, s.reaction
        self.ta2 = (psi / 2 + 1 - r) / phi  # nozzle exit angle, fixed
        self.tb3 = (r + psi / 2) / phi  # rotor relative exit angle (against rotation), fixed
        self.ta3 = (1 - r - psi / 2) / phi  # stage exit swirl at design
        self.b2d = math.atan((self.ta2 * phi - 1) / phi)
        self.target = isentropic_eff
        self.zeta = brentq(lambda z: self._design(z)[0] - isentropic_eff, 0.0, 2.0, xtol=1e-12)
        _, self.a_noz, self.a_rot = self._design(self.zeta, areas=True)

    def _design(self, zeta, areas=False):
        """Efficiency at design for loss coefficient `zeta`; with `areas`, also each row's throat area per
        unit design flow. A row whose design exit lies past its mass flux peak (supersonic) is sized as
        just choked, since a fixed-geometry row cannot pass more than its peak flux."""
        gas, u = self.gas, self.u
        va = self.s.flow_coefficient * u
        t0, p0, a_noz, a_rot = self.t_in, P_REF, [], []

        def area(static, tan_exit):
            t, p = static(va)
            return 1.0 / min(p / (gas.R * t) * va, 0.995 * self._peak(static, tan_exit)[1])

        for _ in range(int(self.s.stages)):
            v2sq = va * va * (1 + self.ta2**2)
            t2 = t0 - v2sq / (2 * gas.cp)
            p2 = p0 * ((t2 - zeta * v2sq / (2 * gas.cp)) / t0) ** (1 / gas.k)
            if areas:
                a_noz.append(area(self._nozzle_static(t0, p0, zeta), self.ta2))
            w2sq = va * va + (va * self.ta2 - u) ** 2
            w3sq = va * va * (1 + self.tb3**2)
            t3 = t2 + (w2sq - w3sq) / (2 * gas.cp)
            p3 = p2 * ((t3 - zeta * w3sq / (2 * gas.cp)) / t2) ** (1 / gas.k)
            if areas:
                a_rot.append(area(self._rotor_static(t2, p2, w2sq, zeta), self.tb3))
            vt3 = u - va * self.tb3
            t03 = t3 + (va * va + vt3 * vt3) / (2 * gas.cp)
            p0 = p3 * (t03 / t3) ** (1 / gas.k)
            t0 = t03
        pr = P_REF / p0
        eff = (self.t_in - t0) / (self.t_in * (1 - pr ** (-gas.k)))
        return eff, a_noz, a_rot

    def _nozzle_static(self, t0, p0, zn):
        """Nozzle exit static (t, p) against axial velocity, for inlet total t0, p0 and loss zn."""
        gas = self.gas

        def noz(va):
            v2sq = va * va * (1 + self.ta2**2)
            t2 = t0 - v2sq / (2 * gas.cp)
            ts = t2 - zn * v2sq / (2 * gas.cp)
            return (t2, p0 * (max(ts, 1.0) / t0) ** (1 / gas.k)) if t2 > 1 else (1.0, 1e-9)
        return noz

    def _rotor_static(self, t2, p2, w2sq, zr):
        """Rotor exit static (t, p) against axial velocity, for inlet static t2, p2, inlet relative
        velocity squared w2sq and loss zr."""
        gas = self.gas

        def rot(va):
            w3sq = va * va * (1 + self.tb3**2)
            t3 = t2 + (w2sq - w3sq) / (2 * gas.cp)
            ts = t3 - zr * w3sq / (2 * gas.cp)
            return (t3, p2 * (max(ts, 1.0) / t2) ** (1 / gas.k)) if t3 > 1 else (1.0, 1e-9)
        return rot

    def _peak(self, static, tan_exit):
        """Axial velocity and mass flux at a row's flux peak (its choke)."""
        f = lambda va: (lambda tp: tp[1] / (self.gas.R * tp[0]) * va)(static(va))  # noqa: E731
        a0 = math.sqrt(self.gas.g * self.gas.R * self.t_in) / math.sqrt(1 + tan_exit**2)
        # the flux rises to a single peak and then falls; scan for it first so the bounded search cannot
        # settle on the falling side
        grid = np.linspace(1e-3, 3 * a0, 61)
        i = int(np.argmax([f(v) for v in grid]))
        res = minimize_scalar(lambda v: -f(v), bounds=(grid[max(i - 1, 0)], grid[min(i + 1, 60)]),
                              method="bounded", options={"xatol": 1e-4})
        return res.x, f(res.x)

    def _row(self, flux, tan_exit, static):
        """Axial velocity through a row of fixed exit angle passing `flux` (kg/s/m2); `static(va)` gives
        the exit static (t, p). None when the row cannot pass the flow (choked)."""
        f = lambda va: (lambda tp: tp[1] / (self.gas.R * tp[0]) * va)(static(va))  # noqa: E731
        va_max, f_max = self._peak(static, tan_exit)
        if flux >= f_max:
            return None
        if flux <= f(1e-6):
            return flux / f(1e-6) * 1e-6
        return brentq(lambda v: f(v) - flux, 1e-6, va_max, xtol=1e-9)

    def point(self, n, w):
        """Pressure ratio and isentropic efficiency at relative speed n and relative flow capacity w;
        None when a row chokes."""
        gas, u, s = self.gas, n * self.u, self.s
        f = lambda i: 1 + (i / s.incidence_range) ** 2  # noqa: E731
        a3d = math.atan(self.ta3)
        t0, p0, swirl_in = self.t_in, P_REF, None
        for k, (an, ar) in enumerate(zip(self.a_noz, self.a_rot)):
            zn = self.zeta * (f(math.degrees(math.atan(swirl_in) - a3d)) if k and swirl_in is not None else 1)

            noz = self._nozzle_static(t0, p0, zn)
            va2 = self._row(w / an, self.ta2, noz)
            if va2 is None:
                return None
            t2, p2 = noz(va2)
            wt2 = va2 * self.ta2 - u
            w2sq = va2 * va2 + wt2 * wt2
            zr = self.zeta * f(math.degrees(math.atan2(wt2, va2) - self.b2d))

            rot = self._rotor_static(t2, p2, w2sq, zr)
            va3 = self._row(w / ar, self.tb3, rot)
            if va3 is None:
                return None
            t3, p3 = rot(va3)
            vt3 = u - va3 * self.tb3
            t03 = t3 + (va3 * va3 + vt3 * vt3) / (2 * gas.cp)
            p0 = p3 * (t03 / t3) ** (1 / gas.k)
            t0, swirl_in = t03, vt3 / va3
        pr = P_REF / p0
        drop = 1 - pr ** (-gas.k)
        eff = (self.t_in - t0) / (self.t_in * drop) if drop > 1e-9 else 0.0
        return pr, eff


@lru_cache(maxsize=32)
def turbine_map(pressure_ratio, isentropic_eff, stages=None, t_in=1500.0, speeds=TURBINE_SPEEDS):
    """A `TabulatedTurbineMap` (flow capacity and efficiency against relative speed and pressure ratio)
    calculated by stage stacking. Its `info` dict holds each speed line's choking pressure ratio."""
    stages = stages or TurbineStages()
    t = _StackedTurbine(pressure_ratio, isentropic_eff, stages, t_in)
    k = t.gas.k
    grid = 1 + (pressure_ratio - 1) * np.linspace(0.02, 2.5, 36)
    wp, eff, choke = [], [], []
    for n in speeds:
        hi = 2.0
        while t.point(n, hi) is not None and hi < 50:
            hi *= 1.5
        w_ch = _bisect(lambda w, n=n: t.point(n, w) is not None, 0.0, hi)
        ws = w_ch * (1 - (1 - np.linspace(0.0, 1.0, 36)) ** 2) * 0.9995
        ws[0] = w_ch * 0.01
        pts = [(w, t.point(n, w)) for w in ws]
        pts = [(w, *p) for w, p in pts if p is not None and p[0] > 1.0]
        # keep the rising part of the line, so pressure ratio is a function of flow
        line = [pts[0]]
        for p in pts[1:]:
            if p[1] > line[-1][1]:
                line.append(p)
        w_l, pr_l, e_l = (np.array(v) for v in zip(*line))
        pr_ch, e_ch, w_c = pr_l[-1], e_l[-1], w_l[-1]
        drop_ch = 1 - pr_ch ** (-k)
        w_row, e_row = [], []
        for pr in grid:
            if pr <= pr_ch:
                w_row.append(float(np.interp(pr, pr_l, w_l)))
                e_row.append(float(np.interp(pr, pr_l, e_l)))
            else:  # choked: fixed capacity, and the extra expansion past the throat does less and less work
                x = (1 - pr ** (-k)) / drop_ch - 1
                w_row.append(float(w_c))
                e_row.append(float(e_ch * (1 - POST_CHOKE_LOSS * x * x)))
        wp.append(w_row)
        eff.append([max(e, 0.02) for e in e_row])
        choke.append((n, float(pr_ch), float(w_c)))
    # a turbine's choking capacity hardly moves with speed; a line choking far below it is one where the
    # 1-D model has lost the flow (huge incidence losses choking a downstream row), so it is left out
    w_design = float(np.interp(1.0, [c[0] for c in choke], [c[2] for c in choke]))
    keep = [i for i, c in enumerate(choke) if c[2] > 0.8 * w_design]
    if not any(abs(choke[i][0] - 1.0) < 1e-9 for i in keep) or len(keep) < 4:
        raise ValueError("the turbine stage inputs give a turbine that chokes below its own design flow: "
                         "raise the turbine efficiency or change the stage loading or flow coefficient")
    speeds = [speeds[i] for i in keep]
    wp, eff, choke = [wp[i] for i in keep], [eff[i] for i in keep], [choke[i] for i in keep]
    m = TabulatedTurbineMap(tuple(speeds), tuple(grid), tuple(map(tuple, wp)), tuple(map(tuple, eff)),
                            design_pr=pressure_ratio, design_np=1.0, design_map_pr=pressure_ratio,
                            source="stage stacking")
    object.__setattr__(m, "info", {"choke": choke, "stages": stages, "blade_speed": t.u})
    return m


def polytropic_from_isentropic(pr, eff, gamma=1.4):
    """Compressor polytropic efficiency equivalent to an isentropic one at pressure ratio pr."""
    k = (gamma - 1) / gamma
    return k * math.log(pr) / math.log(1 + (pr**k - 1) / eff)
