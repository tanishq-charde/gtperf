"""Test-bed instrument list: uncertainties from accuracy classes, specifications and calibration certificates.

Each instrument's systematic (calibration) uncertainty is built from one or more
components. A component is either

* a tolerance from a standard's accuracy class or a manufacturer's specification
  (e.g. IEC 60751 class A for a Pt100: +/-(0.15 + 0.002 |t|) degC). The tolerance
  is a bound, so it is converted to a standard uncertainty with an assumed
  distribution: rectangular (divide by sqrt(3)) when the standard only promises
  that the error lies within the limits, normal with a stated coverage factor when
  the source gives one; or
* a calibration certificate for that instrument: an as-found error and an expanded
  uncertainty with its coverage factor. When a certificate is present and in date
  for the test, the reading is corrected by the as-found error and the
  certificate's uncertainty replaces the class tolerance (GUM 3.2.4: correct for
  recognised systematic effects, then carry the uncertainty of the correction).

An out-of-date certificate is not used: the class tolerance applies, no correction
is made, and the instrument is flagged.

The list lives in a TOML file (see ``gtperf/data/instruments.toml``) so that each
number carries its standard, formula and source URL with it.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from datetime import date, timedelta
from importlib import resources
from pathlib import Path

import numpy as np

DIVISORS = {"rectangular": math.sqrt(3.0), "triangular": math.sqrt(6.0)}


@dataclass(frozen=True)
class Certificate:
    """A calibration certificate for one instrument (or one element of a measurement chain).

    `as_found_error` is reading minus reference at calibration, in the instrument's
    units (for relative instruments, in percent of reading, as certificates for
    meters and flow meters state it). The instrument is used as found, so the
    analysis subtracts this error from the reading.
    """

    id: str
    as_found_error: float
    expanded_uncertainty: float
    k: float
    date: date
    interval_days: int = 365
    laboratory: str = ""

    @property
    def u(self):
        return self.expanded_uncertainty / self.k

    @property
    def due(self):
        return self.date + timedelta(days=self.interval_days)

    def status(self, test_date):
        if test_date is None:
            return "in date (test date not given)"
        if test_date < self.date:
            return "calibrated after the test"
        if test_date > self.due:
            return "out of date"
        return "in date"

    def valid_for(self, test_date):
        return self.status(test_date).startswith("in date")


@dataclass(frozen=True)
class Component:
    """One contribution to an instrument's calibration uncertainty, from a class tolerance or specification.

    The tolerance (a +/- bound, absolute, in the instrument's units) is

        sum:  fixed + of_reading |x| + of_span span
        max:  max(fixed, of_reading |x|, of_span span)

    IEC 60751 RTD classes are of the "sum" form; IEC 60584-1 thermocouple classes
    are "the greater of" a fixed value and a fraction of |t|.
    """

    name: str
    standard: str
    accuracy_class: str = ""
    formula: str = ""
    fixed: float = 0.0
    of_reading: float = 0.0
    of_span: float = 0.0
    span: float = 0.0
    combine: str = "sum"
    distribution: str = "rectangular"
    coverage_k: float = 2.0  # for distribution = "normal": the bound is an expanded uncertainty with this k
    source: str = ""
    verified: bool = True
    note: str = ""
    certificate: Certificate | None = None

    def tolerance(self, reading):
        terms = [self.fixed, self.of_reading * abs(reading), self.of_span * self.span]
        return max(terms) if self.combine == "max" else sum(terms)

    def class_u(self, reading):
        """Standard uncertainty (k=1) from the class tolerance, absolute units."""
        tol = self.tolerance(reading)
        if self.distribution == "normal":
            return tol / self.coverage_k
        return tol / DIVISORS[self.distribution]

    def uses_certificate(self, test_date):
        return self.certificate is not None and self.certificate.valid_for(test_date)

    def basis(self, test_date):
        if self.uses_certificate(test_date):
            c = self.certificate
            return f"certificate {c.id} ({c.date.isoformat()})"
        return f"{self.standard} {self.accuracy_class}".strip()


@dataclass(frozen=True)
class Instrument:
    """One measurement channel.

    `bias` and `noise` are standard uncertainties (k=1) of the systematic and random
    parts, as fractions of reading for relative instruments. `bias` is evaluated at
    the `nominal` reading; with `components`, the analysis re-evaluates it at the
    actual reading and test date. `noise` is used only to generate synthetic data:
    the analysis estimates random uncertainty from the logged samples.
    """

    unit: str
    bias: float
    noise: float
    relative: bool = False
    description: str = ""
    components: tuple = ()
    nominal: float = 0.0
    quantity: str = ""
    group: str = ""

    @property
    def sourced(self):
        return bool(self.components) and all(c.source for c in self.components)

    def _scale(self, reading):
        return abs(reading) if self.relative else 1.0

    def component_u(self, reading=None, test_date=None):
        """(component, standard uncertainty, distribution) in the instrument's convention (fraction if relative)."""
        x = self.nominal if reading is None else reading
        out = []
        for c in self.components:
            if c.uses_certificate(test_date):
                u = c.certificate.u / 100 if self.relative else c.certificate.u
                out.append((c, u, "normal"))
            else:
                out.append((c, c.class_u(x) / self._scale(x), c.distribution))
        return out

    def standard_uncertainty(self, reading=None, test_date=None):
        """Systematic standard uncertainty (k=1), fraction of reading if relative."""
        if not self.components:
            return self.bias
        return math.sqrt(sum(u * u for _, u, _ in self.component_u(reading, test_date)))

    def correction(self, test_date=None):
        """Sum of in-date certificate as-found errors (fraction of reading if relative), to subtract from readings."""
        e = sum(c.certificate.as_found_error for c in self.components if c.uses_certificate(test_date))
        return e / 100 if self.relative else e

    def corrected(self, x, test_date=None):
        e = self.correction(test_date)
        if e == 0.0:
            return x
        return x / (1 + e) if self.relative else x - e

    def draw_bias(self, rng, reading=None, test_date=None):
        """A realised calibration error for synthetic data, drawn from each component's own distribution.

        With a certificate the true error is the as-found error plus the
        certificate's own uncertainty; otherwise anywhere inside the class
        tolerance with the stated distribution.
        """
        if not self.components:
            return rng.normal(0, self.bias)
        x = self.nominal if reading is None else reading
        total = 0.0
        for c in self.components:
            if c.certificate is not None:
                e = c.certificate.as_found_error + rng.normal(0, c.certificate.u)
                total += e / 100 if self.relative else e
            else:
                total += _draw(rng, c.distribution, c.class_u(x) / self._scale(x))
        return total


def _draw(rng, distribution, u):
    if distribution == "rectangular":
        a = math.sqrt(3.0) * u
        return rng.uniform(-a, a)
    if distribution == "triangular":
        a = math.sqrt(6.0) * u
        return rng.triangular(-a, 0.0, a)
    return rng.normal(0, u)


def draw(rng, distribution, u, size=None):
    """Vectorised draw with standard deviation u from a named distribution (used by the Monte Carlo)."""
    if distribution == "rectangular":
        a = math.sqrt(3.0) * u
        return rng.uniform(-a, a, size)
    if distribution == "triangular":
        a = math.sqrt(6.0) * u
        return rng.triangular(-a, 0.0, a, size)
    return rng.normal(0, u, size)


# --- loading -----------------------------------------------------------------

def _certificate(d, default_interval):
    if d is None:
        return None
    return Certificate(str(d["id"]), float(d["as_found_error"]), float(d["expanded_uncertainty"]), float(d["k"]),
                       d["date"], int(d.get("interval_days", default_interval)), d.get("laboratory", ""))


COMPONENT_KEYS = {"name", "standard", "class", "formula", "fixed", "of_reading", "of_span", "span", "combine",
                  "distribution", "coverage_k", "source", "verified", "note", "certificate", "certificates"}


def load_instruments(path=None):
    """Read an instrument list TOML file. Returns {tag: Instrument}.

    An entry with `tags = [...]` defines several identical channels (a rake, or
    redundant sensors); per-channel certificates go under
    `[instrument.component.certificates.<tag>]`.
    """
    if path is None:
        text = resources.files("gtperf.data").joinpath("instruments.toml").read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    spec = tomllib.loads(text)
    default_interval = int(spec.get("calibration_interval_days", 365))
    out = {}
    for entry in spec["instrument"]:
        tags = entry.get("tags") or [entry["tag"]]
        for tag in tags:
            comps = []
            for c in entry.get("component", []):
                unknown = set(c) - COMPONENT_KEYS
                if unknown:
                    raise ValueError(f"{tag}: unknown component keys {sorted(unknown)}")
                cert = c.get("certificate")
                if cert is None and "certificates" in c:
                    cert = c["certificates"].get(tag)
                comps.append(Component(
                    name=c.get("name", ""), standard=c["standard"], accuracy_class=c.get("class", ""),
                    formula=c.get("formula", ""), fixed=float(c.get("fixed", 0.0)),
                    of_reading=float(c.get("of_reading", 0.0)), of_span=float(c.get("of_span", 0.0)),
                    span=float(c.get("span", 0.0)), combine=c.get("combine", "sum"),
                    distribution=c.get("distribution", "rectangular"), coverage_k=float(c.get("coverage_k", 2.0)),
                    source=c.get("source", ""), verified=bool(c.get("verified", True)), note=c.get("note", ""),
                    certificate=_certificate(cert, entry.get("calibration_interval_days", default_interval)),
                ))
            inst = Instrument(entry["unit"], 0.0, float(entry["noise"]), bool(entry.get("relative", False)),
                              entry.get("description", ""), tuple(comps), float(entry["nominal"]),
                              entry.get("quantity", ""), entry.get("group", tags[0]))
            out[tag] = Instrument(inst.unit, inst.standard_uncertainty(), inst.noise, inst.relative,
                                  inst.description, inst.components, inst.nominal, inst.quantity, inst.group)
    return out


# --- checks ------------------------------------------------------------------

def calibration_status(instruments, test_date):
    """One row per certificate: tag, component, certificate id, date, due date, status.

    Status is "in date", "out of date" (the due date is before the test) or
    "calibrated after the test" (the certificate does not describe the
    instrument as it was during the test).
    """
    rows = []
    for tag, inst in instruments.items():
        for c in inst.components:
            if c.certificate is None:
                continue
            cert = c.certificate
            rows.append({"tag": tag, "component": c.name, "certificate": cert.id, "date": cert.date,
                         "due": cert.due, "status": cert.status(test_date), "valid": cert.valid_for(test_date)})
    return rows


def out_of_date(instruments, test_date):
    """Tags whose calibration certificate is not valid on the test date."""
    return sorted({r["tag"] for r in calibration_status(instruments, test_date) if not r["valid"]})


def uncertainty_table(instruments, readings=None, test_date=None):
    """Rows for the report's uncertainty source table, one per instrument group and component.

    `u` is the standard uncertainty of one channel (RMS over the group's channels),
    in percent of reading for relative instruments.
    """
    readings = readings or {}
    groups = {}
    for tag, inst in instruments.items():
        groups.setdefault(inst.group or tag, []).append((tag, inst))
    rows = []
    for members in groups.values():
        inst = members[0][1]
        for j, comp in enumerate(inst.components):
            us, certs = [], []
            for tag, m in members:
                us.append(m.component_u(readings.get(tag, m.nominal), test_date)[j][1])
                c = m.components[j]
                if c.uses_certificate(test_date):
                    certs.append(c.certificate)
            class_text = f"{comp.standard} {comp.accuracy_class}".strip()
            if certs:
                basis = "calibration certificate" + ("s" if len(certs) > 1 else "")
                detail = "; ".join(f"{c.id}: as-found {c.as_found_error:+g}, U = {c.expanded_uncertainty:g} "
                                   f"(k = {c.k:g})" for c in certs)
                detail += f". Overrides {class_text}" + (f", {comp.formula}" if comp.formula else "")
                dist_text = "normal (certificate)"
            else:
                basis, detail, dist_text = class_text, comp.formula, comp.distribution
                if comp.distribution == "normal":
                    dist_text = f"normal (bound taken as k = {comp.coverage_k:g})"
            u = float(np.sqrt(np.mean(np.square(us))))
            rows.append({
                "tags": [t for t, _ in members], "quantity": inst.quantity, "component": comp.name, "basis": basis,
                "detail": detail, "distribution": dist_text, "u": 100 * u if inst.relative else u,
                "unit": "%" if inst.relative else inst.unit, "source": comp.source, "verified": comp.verified,
                "note": comp.note,
            })
    return rows
