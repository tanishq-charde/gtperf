"""Statistical process control for production performance data."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

D2 = 1.128  # moving range of two, for sigma estimation


@dataclass
class Chart:
    name: str
    statistic: np.ndarray
    centre: float
    ucl: np.ndarray
    lcl: np.ndarray
    signals: np.ndarray  # indices that signal

    @property
    def first_signal(self):
        return int(self.signals[0]) if len(self.signals) else None


def phase1(x, n_baseline, iterations=3):
    """Centre and short-term sigma (average moving range) of a baseline period.

    Points outside 3 sigma are dropped and the limits recomputed, as in a normal
    Phase I study, so one damaged engine does not widen the limits for everyone.
    """
    base = np.asarray(x[:n_baseline], float)
    keep = np.ones(len(base), bool)
    for _ in range(iterations):
        b = base[keep]
        centre, sigma = b.mean(), np.mean(np.abs(np.diff(b))) / D2
        new = np.abs(base - centre) <= 3 * sigma
        if (new == keep).all():
            break
        keep = new
    return float(centre), float(sigma)


def individuals(x, centre, sigma, L=3.0):
    x = np.asarray(x, float)
    ucl, lcl = np.full_like(x, centre + L * sigma), np.full_like(x, centre - L * sigma)
    return Chart("Individuals", x, centre, ucl, lcl, np.flatnonzero((x > ucl) | (x < lcl)))


def ewma(x, centre, sigma, lam=0.2, L=2.86, skip=()):
    """EWMA chart with exact time-varying limits. Indices in `skip` (assigned causes) are left out."""
    x = np.asarray(x, float)
    z = np.empty_like(x)
    prev = centre
    for i, xi in enumerate(x):
        if i not in skip:
            prev = lam * xi + (1 - lam) * prev
        z[i] = prev
    i = np.arange(1, len(x) + 1)
    width = L * sigma * np.sqrt(lam / (2 - lam) * (1 - (1 - lam) ** (2 * i)))
    return Chart(f"EWMA (lambda={lam})", z, centre, centre + width, centre - width,
                 np.flatnonzero((z > centre + width) | (z < centre - width)))


def cusum(x, centre, sigma, k=0.5, h=4.0, skip=()):
    """Tabular two-sided CUSUM. Statistic returned is C+ or -C-, whichever is larger, for plotting.

    Indices in `skip` (assigned causes, e.g. a damaged engine) are left out.
    """
    x = (np.asarray(x, float) - centre) / sigma
    hi = lo = 0.0
    stat, sig = np.empty_like(x), []
    for i, xi in enumerate(x):
        if i in skip:
            stat[i] = hi if hi >= lo else -lo
            continue
        hi = max(0.0, hi + xi - k)
        lo = max(0.0, lo - xi - k)
        stat[i] = hi if hi >= lo else -lo
        if hi > h or lo > h:
            sig.append(i)
    n = len(x)
    return Chart(f"CUSUM (k={k}, h={h})", stat * sigma, 0.0, np.full(n, h * sigma), np.full(n, -h * sigma),
                 np.array(sig, int))


def western_electric(x, centre, sigma):
    """Indices flagged by the four Western Electric rules, with the rule number."""
    z = (np.asarray(x, float) - centre) / sigma
    flags = {}
    for i in range(len(z)):
        if abs(z[i]) > 3:
            flags.setdefault(i, 1)
        for side in (1, -1):
            w3 = side * z[max(0, i - 2): i + 1]
            if len(w3) == 3 and np.sum(w3 > 2) >= 2:
                flags.setdefault(i, 2)
            w5 = side * z[max(0, i - 4): i + 1]
            if len(w5) == 5 and np.sum(w5 > 1) >= 4:
                flags.setdefault(i, 3)
            w8 = side * z[max(0, i - 7): i + 1]
            if len(w8) == 8 and np.all(w8 > 0):
                flags.setdefault(i, 4)
    return flags


def cpk_lower(x, lsl):
    """One-sided capability against a lower limit (e.g. minimum guaranteed power)."""
    x = np.asarray(x, float)
    return float((x.mean() - lsl) / (3 * x.std(ddof=1)))


def cpk_upper(x, usl):
    x = np.asarray(x, float)
    return float((usl - x.mean()) / (3 * x.std(ddof=1)))
