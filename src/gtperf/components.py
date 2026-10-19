"""Gas turbine components operating on a one-dimensional flow state."""

from __future__ import annotations

from dataclasses import dataclass, replace

from scipy.optimize import brentq

from .gas import IdealGasMixture

POLYTROPIC_STEPS = 50


@dataclass(frozen=True)
class FlowState:
    """Total conditions of a gas stream: mass flow (kg/s), T (K), P (Pa) and its gas model."""

    w: float
    t: float
    p: float
    gas: object

    @property
    def h(self):
        return self.gas.h(self.t)

    @property
    def s(self):
        return self.gas.s(self.t, self.p)


def _isentropic_t(gas, t_in, p_in, p_out):
    return gas.t_from_s(gas.s(t_in, p_in), p_out, t_guess=t_in * (p_out / p_in) ** 0.28)


def _compress_or_expand(state, p_out, eta, polytropic, compress):
    """Change pressure with an isentropic or polytropic efficiency. Returns exit temperature."""
    gas = state.gas

    def step(t, p1, p2, eta_step):
        h1 = gas.h(t)
        dh_s = gas.h(_isentropic_t(gas, t, p1, p2)) - h1
        dh = dh_s / eta_step if compress else dh_s * eta_step
        return gas.t_from_h(h1 + dh, t_guess=t)

    if not polytropic:
        return step(state.t, state.p, p_out, eta)
    ratio = (p_out / state.p) ** (1 / POLYTROPIC_STEPS)
    t, p = state.t, state.p
    for _ in range(POLYTROPIC_STEPS):
        t = step(t, p, p * ratio, eta)
        p *= ratio
    return t


@dataclass
class Duct:
    """Total pressure loss, as a fraction of inlet pressure."""

    dp_frac: float = 0.0

    def run(self, state):
        return replace(state, p=state.p * (1 - self.dp_frac))


@dataclass
class Compressor:
    pressure_ratio: float
    efficiency: float = 0.90
    polytropic: bool = True

    def run(self, state):
        p_out = state.p * self.pressure_ratio
        t_out = _compress_or_expand(state, p_out, self.efficiency, self.polytropic, compress=True)
        out = replace(state, t=t_out, p=p_out)
        return out, state.w * (out.h - state.h)

    def isentropic_efficiency(self, state):
        out, _ = self.run(state)
        t_s = _isentropic_t(state.gas, state.t, state.p, out.p)
        return (state.gas.h(t_s) - state.h) / (out.h - state.h)


@dataclass
class Combustor:
    """Burns fuel to reach exit temperature t_out, with a pressure loss and combustion efficiency."""

    fuel: object
    t_out: float
    dp_frac: float = 0.04
    efficiency: float = 0.995
    t_fuel: float = 288.15

    def run(self, state):
        q_fuel = self.fuel.heat_release_term(self.t_fuel, self.efficiency)

        def residual(far):
            products = self.fuel.products(state.gas, far)
            return state.h + far * q_fuel - (1 + far) * products.h(self.t_out)

        far_max = 0.06
        if hasattr(self.fuel, "stoichiometric_far"):
            far_max = 0.999 * self.fuel.stoichiometric_far(state.gas)
        far = brentq(residual, 0.0, far_max, xtol=1e-15, rtol=1e-14)
        products = self.fuel.products(state.gas, far)
        out = FlowState(state.w * (1 + far), self.t_out, state.p * (1 - self.dp_frac), products)
        return out, state.w * far


@dataclass
class Turbine:
    efficiency: float = 0.89
    polytropic: bool = False

    def run(self, state, p_out):
        t_out = _compress_or_expand(state, p_out, self.efficiency, self.polytropic, compress=False)
        out = replace(state, t=t_out, p=p_out)
        return out, state.w * (state.h - out.h)


def mix(a, b):
    """Adiabatically mix two streams at the pressure of stream a."""
    if a.w == 0 or b.w == 0:
        return a if b.w == 0 else b
    if not (isinstance(a.gas, IdealGasMixture) and isinstance(b.gas, IdealGasMixture)):
        raise NotImplementedError("stream mixing needs IdealGasMixture gases")
    moles = {}
    for st in (a, b):
        n = st.w / st.gas.mw
        for k, x in st.gas.mole_fractions.items():
            moles[k] = moles.get(k, 0.0) + x * n
    gas = IdealGasMixture(moles)
    w = a.w + b.w
    h = (a.w * a.h + b.w * b.h) / w
    return FlowState(w, gas.t_from_h(h, t_guess=a.t), a.p, gas)
