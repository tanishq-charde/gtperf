"""The calculated compressor and turbine maps as an Excel workbook that works them out itself.

Every map point is a live Excel formula: an Inputs sheet holds the cycle and stage design values, each
component sheet derives the design-point velocity triangles, blade speed, losses and annulus areas from
them, and a stage-by-stage sheet stacks the stages for every map point with the same equations as
`gtperf.mapgen`. Native Excel charts draw the maps from those cells, so changing an input redraws them.

Two kinds of value are not formulas, because the tool finds them by searching: the flows that end each
speed line (surge and the choke end of a compressor line, choke of a turbine line) and the turbine loss
coefficient calibrated to the design efficiency. They are written as values in green, and the sheet shows
the checks that tie them back (stall margin at surge, design efficiency from the calibrated loss).
Axial velocities from continuity are solved by Newton iterations in helper columns, started below the
root so that each step moves monotonically towards it; a residual column shows the convergence.

Each formula is also evaluated in Python while the workbook is written (`_Eval`), so the file opens with
its values and charts filled in and the same strings can be checked against `mapgen`.
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass

from .mapgen import (
    P_REF,
    POST_CHOKE_LOSS,
    T_REF,
    CompressorStages,
    TurbineStages,
    _bisect,
    _StackedCompressor,
    _StackedTurbine,
    compressor_map,
)

NEWTON_STEPS = 10
R_LINES = tuple(round(1 + 0.2 * i, 1) for i in range(11))  # surge (1) to the choke end (3) of each line
TURBINE_X = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0)  # w = w_choke (1 - (1 - x)**2)
CHOKE_EXTENSION = 6  # points past choke on each turbine line
CHART_LOWEST = 0.5  # speed lines below this are in the tables but not on the charts (as in the tool)

NAVY, IVORY, MUTED, RED, AMBER, BLUE = "#12224A", "#FAF7F0", "#8A8F9C", "#B3261E", "#C98A12", "#3B6FB6"


# --- formula evaluation -----------------------------------------------------------------------------

_REF = re.compile(r"(?:'([^']+)'!|(?<![\w.]))\$?([A-Z]{1,3})\$?(\d+)(?::\$?([A-Z]{1,3})\$?(\d+))?")
_TOKEN = re.compile(r"(?P<fn>[A-Z][A-Z0-9]*)(?=\()|(?P<name>\b[a-z]_\w+)|(?P<op>\^|<>)")
_FUNCS = {
    "SQRT": "math.sqrt", "ATAN": "math.atan", "TAN": "math.tan", "DEGREES": "math.degrees",
    "RADIANS": "math.radians", "ABS": "abs", "MAX": "_max", "MIN": "_min", "IF": "_if", "ATAN2": "_atan2",
    "OR": "_or", "AND": "_and", "LN": "math.log", "EXP": "math.exp",
}


def _col(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def _flat(args):
    for a in args:
        if isinstance(a, list):
            yield from a
        else:
            yield a


class _Eval:
    """Evaluates the workbook's formulas (the subset this module writes) as they are written."""

    def __init__(self):
        self.cells, self.names, self.compiled = {}, {}, {}
        self.env = {"math": math, "_if": lambda c, a, b: a if c else b, "_atan2": lambda x, y: math.atan2(y, x),
                    "_max": lambda *a: max(_flat(a)), "_min": lambda *a: min(_flat(a)),
                    "_or": lambda *a: any(a), "_and": lambda *a: all(a), "V": self._v, "R": self._range,
                    "N": lambda n: self._v(*self.names[n.lower()])}

    def _v(self, sheet, row, col):
        return self.cells[(sheet, row, col)]

    def _range(self, sheet, r1, c1, r2, c2):
        return [self.cells[(sheet, r, c)] for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)]

    def __call__(self, formula, sheet):
        """Value of `formula` on `sheet`. Cell references become arguments, so the compiled expression is
        shared by every row of a column."""
        args = []

        def ref(m):
            s, r, c = m[1] or sheet, int(m[3]) - 1, _col(m[2])
            args.append(self._range(s, r, c, int(m[5]) - 1, _col(m[4])) if m[4] else self.cells[(s, r, c)])
            return f"a[{len(args) - 1}]"

        key = _REF.sub(ref, formula)
        code = self.compiled.get(key)
        if code is None:
            if re.search(r"(^|[^\w.)\]])-\s*[\w.\[\]]+\^", key):  # Excel binds unary minus before ^
                raise ValueError(f"ambiguous unary minus before ^ in {formula}")
            code = self.compiled[key] = compile(self.python(key), "<formula>", "eval")
        return eval(code, self.env, {"a": args})  # noqa: S307  formulas are written by this module

    @staticmethod
    def python(key):
        def sub(m):
            if m["fn"]:
                return _FUNCS[m["fn"]]
            if m["name"]:
                return f"N({m['name']!r})"
            return "**" if m["op"] == "^" else "!="
        return _TOKEN.sub(sub, key)


# --- workbook helpers --------------------------------------------------------------------------------

def _a1(row, col, absolute=False):
    letters, c = "", col + 1
    while c:
        c, rem = divmod(c - 1, 26)
        letters = chr(65 + rem) + letters
    return f"${letters}${row + 1}" if absolute else f"{letters}{row + 1}"


class _Book:
    def __init__(self):
        import xlsxwriter

        self.buf = io.BytesIO()
        self.wb = xlsxwriter.Workbook(self.buf, {"in_memory": True})
        self.ev = _Eval()
        self.written = set()
        base = {"font_name": "Calibri", "font_size": 10}
        f = lambda **k: self.wb.add_format({**base, **k})  # noqa: E731
        self.fmt = {
            "title": f(bold=True, font_size=15, font_color=NAVY, font_name="Cambria"),
            "h2": f(bold=True, font_size=12, font_color=NAVY, font_name="Cambria"),
            "text": f(text_wrap=False),
            "wrap": f(text_wrap=True, valign="top"),
            "note": f(italic=True, font_color="#555555"),
            "head": f(bold=True, font_color="white", bg_color=NAVY, text_wrap=True, valign="vcenter", border=1),
            "eq": f(italic=True, font_color="#444444", bg_color="#EEF1F7", text_wrap=True, valign="top", border=1,
                    font_size=9),
            "label": f(border=1),
            "sym": f(border=1, italic=True, align="center"),
            "unit": f(border=1, align="center", font_color="#555555"),
            "eqtext": f(border=1, font_color="#444444", italic=True),
            "input": f(border=1, font_color="#1F4FD1", bg_color="#FFF8DC", num_format="General"),
            "search": f(border=1, font_color="#1B7F3B", bg_color="#EAF6EC", num_format="0.000000"),
            "fixed": f(border=1, font_color="#666666", bg_color="#F2F2F2"),
            "calc": f(border=1, num_format="0.000000"),
            "calc4": f(border=1, num_format="0.0000"),
            "calcg": f(border=1, num_format="General"),
            "sci": f(border=1, num_format="0.0000E+00"),
            "int": f(border=1, num_format="0"),
            "key": f(border=1, bold=True, num_format="0.0000"),
        }

    def sheet(self, name):
        ws = self.wb.add_worksheet(name)
        # the formulas use only long-standing functions, so skip XlsxWriter's per-formula scan for newer ones
        ws._prepare_formula = lambda f, expand_future_functions=False: f.lstrip("=")
        return _Sheet(self, ws, name)

    def close(self):
        self.wb.close()
        return self.buf.getvalue()


class _Sheet:
    def __init__(self, book, ws, name):
        self.book, self.ws, self.name = book, ws, name
        ws.hide_gridlines(2)

    eq_span = 1  # columns the equation text of a quantity table spans

    def merged(self, row, c0, c1, text, fmt):
        for c in range(c0, c1 + 1):
            self._claim(row, c)
        if c1 > c0:
            self.ws.merge_range(row, c0, row, c1, text, self.book.fmt[fmt])
        else:
            self.ws.write_string(row, c0, text, self.book.fmt[fmt])

    def ref(self, row, col, absolute=True, sheet=None):
        """A1 reference to (row, col) of this sheet, sheet-qualified when used from another sheet."""
        a = _a1(row, col, absolute)
        return a if sheet is None or sheet is self else f"'{self.name}'!{a}"

    def _claim(self, row, col):
        if (self.name, row, col) in self.book.written:
            raise ValueError(f"{self.name}!{_a1(row, col)} is written twice")
        self.book.written.add((self.name, row, col))

    def put(self, row, col, value, fmt="text"):
        self._claim(row, col)
        f = self.book.fmt[fmt] if isinstance(fmt, str) else fmt
        if isinstance(value, str):
            self.ws.write_string(row, col, value, f)
        else:
            self.ws.write_number(row, col, float(value), f)
            self.book.ev.cells[(self.name, row, col)] = float(value)
        return value

    def calc(self, row, col, formula, fmt="calc"):
        self._claim(row, col)
        value = self.book.ev(formula, self.name)
        if isinstance(value, complex) or (isinstance(value, float) and not math.isfinite(value)):
            raise ValueError(f"{self.name}!{_a1(row, col)} = {formula} gives {value}")
        self.book.ev.cells[(self.name, row, col)] = float(value)
        self.ws.write_formula(row, col, "=" + formula, self.book.fmt[fmt], float(value))
        return float(value)

    def name_cell(self, name, row, col):
        self.book.wb.define_name(name, f"='{self.name}'!{_a1(row, col, True)}")
        key = name.lower()
        if key in self.book.ev.names:
            raise ValueError(f"name {name} is defined twice")
        self.book.ev.names[key] = (self.name, row, col)

    def quantities(self, row, title, items, col=0):
        """A Quantity | Symbol | Value | Unit | Equation table. items: (label, name, symbol, value or
        formula, unit, equation, kind) with kind input, fixed, search or calc; returns the next free row."""
        self.put(row, col, title, "h2")
        heads = ("Quantity", "Symbol", "Value", "Unit")
        for j, h in enumerate(heads):
            self.put(row + 1, col + j, h, "head")
        self.merged(row + 1, col + 4, col + 4 + self.eq_span - 1, "Equation or source", "head")
        r = row + 2
        for label, name, symbol, value, unit, eq, kind in items:
            self.put(r, col, label, "label")
            self.put(r, col + 1, symbol, "sym")
            if kind == "calc":
                self.calc(r, col + 2, value, "calcg")
            else:
                self.put(r, col + 2, value, kind)
            self.put(r, col + 3, unit, "unit")
            self.merged(r, col + 4, col + 4 + self.eq_span - 1, eq, "eqtext")
            if name:
                self.name_cell(name, r, col + 2)
            r += 1
        return r + 1


@dataclass
class _Column:
    key: str
    head: str
    eq: str
    fmt: str = "calc"
    width: float = 11


class _Table:
    """A table with one header row and one equation row; `row(i)` gives the cell references of row i."""

    def __init__(self, sheet, top, left, columns, widths=True):
        self.sh, self.top, self.left, self.cols = sheet, top, left, columns
        self.index = {c.key: left + j for j, c in enumerate(columns)}
        if len(self.index) != len(columns):
            raise ValueError("two columns share a key")
        for j, c in enumerate(columns):
            sheet.put(top, left + j, c.head, "head")
            sheet.put(top + 1, left + j, c.eq, "eq")
            if widths:
                sheet.ws.set_column(left + j, left + j, c.width)
        sheet.ws.set_row(top, 42)
        sheet.ws.set_row(top + 1, 54)
        self.first = top + 2

    def r(self, i):
        return self.first + i

    def row(self, i, sheet=None, absolute=False):
        """References to the cells of data row i, as attributes named by column key."""
        row = self.r(i)
        return type("Row", (), {k: self.sh.ref(row, c, absolute, sheet) for k, c in self.index.items()})

    def put(self, i, key, value, fmt=None):
        return self.sh.put(self.r(i), self.index[key], value, fmt or self._fmt(key))

    def calc(self, i, key, formula, fmt=None):
        return self.sh.calc(self.r(i), self.index[key], formula, fmt or self._fmt(key))

    def _fmt(self, key):
        return next(c.fmt for c in self.cols if c.key == key)

    def span(self, key, i0, i1, sheet=None):
        c = self.index[key]
        a, b = _a1(self.r(i0), c, True), _a1(self.r(i1), c, True)
        return f"{a}:{b}" if sheet is None or sheet is self.sh else f"'{self.sh.name}'!{a}:{b}"

    def chart_ref(self, key, i0, i1):
        c = self.index[key]
        return [self.sh.name, self.r(i0), c, self.r(i1), c]

    def value(self, i, key):
        return self.sh.book.ev.cells[(self.sh.name, self.r(i), self.index[key])]


def _newton_columns(prefix, label, steps=NEWTON_STEPS):
    return [_Column(f"{prefix}{m}", f"{label} iteration {m}" if m else f"{label} start",
                    "Newton step on continuity" if m else "incompressible guess (below the root)", "calc4", 9)
            for m in range(steps + 1)]


# --- inputs ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class TurbineDesign:
    title: str
    prefix: str  # name prefix in the workbook, one letter
    pressure_ratio: float
    efficiency: float
    t_in: float
    stages: TurbineStages


def _inputs(book, name, comp_pr, comp_eta, cs: CompressorStages, turbines):
    sh = book.sheet("Inputs")
    ws = sh.ws
    ws.set_column(0, 0, 44)
    ws.set_column(1, 1, 9)
    ws.set_column(2, 2, 14)
    ws.set_column(3, 3, 10)
    ws.set_column(4, 4, 70)
    sh.put(0, 0, f"Inputs: {name}", "title")
    sh.put(1, 0, "Blue cells are inputs: change one and every sheet recalculates. Grey cells set the layout "
           "(the number of rows), so change those in GTPerf and download the workbook again.", "note")
    r = sh.quantities(3, "Reference conditions and gas properties", [
        ("Reference total temperature", "g_Tref", "T_ref", T_REF, "K", "Map corrected to ISO conditions", "input"),
        ("Reference total pressure", "g_Pref", "P_ref", P_REF, "Pa", "Map corrected to ISO conditions", "input"),
        ("Ratio of specific heats, compressor", "c_gamma", "γ", 1.4, "", "Perfect gas", "input"),
        ("Specific heat, compressor", "c_cp", "cp", 1004.5, "J/(kg K)", "Perfect gas", "input"),
        ("Ratio of specific heats, turbines", "t_gamma", "γ", 1.33, "", "Perfect gas", "input"),
        ("Specific heat, turbines", "t_cp", "cp", 1150.0, "J/(kg K)", "Perfect gas", "input"),
        ("Turbine efficiency loss past choke", "t_cpost", "c", POST_CHOKE_LOSS, "",
         "η = η_choke (1 − c x²) past choke", "input"),
    ])
    r = sh.quantities(r, "Compressor design point (from the engine cycle)", [
        ("Compressor pressure ratio", "c_PR", "π_c", comp_pr, "", "Engine design input", "input"),
        ("Compressor polytropic efficiency", "c_etap", "η_p", comp_eta, "", "Engine design input", "input"),
    ])
    r = sh.quantities(r, "Compressor stage design", [
        ("Number of stages", "c_N", "N", cs.stages, "", "Sets the number of rows in the stage tables", "fixed"),
        ("Stage loading", "c_psi", "ψ", cs.loading, "", "ψ = Δh₀ / U²", "input"),
        ("Flow coefficient", "c_phi", "φ", cs.flow_coefficient, "", "φ = Va / U", "input"),
        ("Stage reaction", "c_Rx", "Λ", cs.reaction, "", "Share of the stage static enthalpy rise in the rotor",
         "input"),
        ("Blade solidity", "c_sigma", "σ", cs.solidity, "", "Chord / pitch, used in the diffusion factor", "input"),
        ("Diffusion factor at stall", "c_DFlim", "DF_lim", cs.df_limit, "", "Lieblein limit", "input"),
        ("Incidence range", "c_irange", "i_range", cs.incidence_range, "°", "Incidence that doubles a row's loss",
         "input"),
        ("Variable vane rows", "c_vrows", "n_v", cs.variable_rows, "", "VIGV plus variable stators", "input"),
        ("VIGV angle fully closed", "c_vclosed", "θ_c", cs.vane_closed, "°", "Negative = closed", "input"),
        ("Vanes fully open above corrected speed", "c_vopen_n", "N_open", cs.vane_open_speed, "",
         "Relative corrected speed", "input"),
        ("Vanes fully closed below corrected speed", "c_vshut_n", "N_closed", cs.vane_closed_speed, "",
         "Relative corrected speed", "input"),
    ])
    for t in turbines:
        p, s = t.prefix, t.stages
        r = sh.quantities(r, f"{t.title} design point (from the engine cycle)", [
            (f"{t.title} expansion ratio", f"{p}_PR", "π_t", t.pressure_ratio, "", "Engine cycle, total to total",
             "input"),
            (f"{t.title} isentropic efficiency", f"{p}_eta", "η_t", t.efficiency, "", "Engine cycle", "input"),
            (f"{t.title} inlet total temperature", f"{p}_Tin", "T₀in", t.t_in, "K", "Engine cycle at ISO", "input"),
        ])
        r = sh.quantities(r, f"{t.title} stage design", [
            ("Number of stages", f"{p}_N", "N", s.stages, "", "Sets the number of rows in the stage tables", "fixed"),
            ("Stage loading", f"{p}_psi", "ψ", s.loading, "", "ψ = Δh₀ / U²", "input"),
            ("Flow coefficient", f"{p}_phi", "φ", s.flow_coefficient, "", "φ = Va / U", "input"),
            ("Stage reaction", f"{p}_Rx", "Λ", s.reaction, "", "Share of the stage static enthalpy drop in the rotor",
             "input"),
            ("Incidence range", f"{p}_irange", "i_range", s.incidence_range, "°", "Incidence that doubles a row's loss",
             "input"),
        ])
    return sh


# --- compressor --------------------------------------------------------------------------------------

def _compressor(book, pr, eta_p, cs: CompressorStages):
    cmap = compressor_map(pr, eta_p, cs)
    py = _StackedCompressor(pr, eta_p, cs)
    n_st = int(cs.stages)
    sh, st = book.sheet("Compressor"), book.sheet("Compressor stages")
    ws = sh.ws
    ws.set_column(0, 0, 40)
    ws.set_column(1, 30, 12.5)
    sh.eq_span = 4
    sh.put(0, 0, "Compressor map by mean-line stage stacking", "title")
    sh.put(1, 0, "Design-point triangles and stage sizing here; every map point is stacked stage by stage on the "
           "'Compressor stages' sheet. Green values are search results from GTPerf (see Read me).", "note")
    r = sh.quantities(3, "Design-point derivation", [
        ("Gas constant", "c_R", "R", "c_cp*(c_gamma-1)/c_gamma", "J/(kg K)", "R = cp (γ − 1) / γ", "calc"),
        ("Exponent (γ − 1) / γ", "c_k", "k", "(c_gamma-1)/c_gamma", "", "k = (γ − 1) / γ", "calc"),
        ("Total temperature ratio", "c_tau", "τ", "c_PR^(c_k/c_etap)", "", "τ = π^(k / η_p)", "calc"),
        ("Stage total enthalpy rise", "c_dh", "Δh₀", "c_cp*g_Tref*(c_tau-1)/c_N", "J/kg",
         "Δh₀ = cp T_ref (τ − 1) / N, equal for every stage", "calc"),
        ("Mean blade speed at design", "c_U", "U", "SQRT(c_dh/c_psi)", "m/s", "U = √(Δh₀ / ψ)", "calc"),
        ("Axial velocity at design", "c_Va", "Va", "c_phi*c_U", "m/s", "Va = φ U", "calc"),
        ("Stage inlet swirl tangent (stator exit)", "c_ta1", "tan α₁", "(1-c_Rx-c_psi/2)/c_phi", "",
         "tan α₁ = (1 − Λ − ψ/2) / φ", "calc"),
        ("Rotor relative exit tangent (fixed)", "c_tb2", "tan β₂", "(c_Rx-c_psi/2)/c_phi", "",
         "tan β₂ = (Λ − ψ/2) / φ", "calc"),
        ("Rotor inlet metal angle", "c_b1d", "β₁ (rad)", "ATAN((1-c_phi*c_ta1)/c_phi)", "rad",
         "β₁ = atan((1 − φ tan α₁) / φ)", "calc"),
        ("Stator inlet metal angle", "c_a2d", "α₂ (rad)", "ATAN((1-c_phi*c_tb2)/c_phi)", "rad",
         "α₂ = atan((1 − φ tan β₂) / φ)", "calc"),
        ("Rotor inlet relative swirl at design", "c_wt1", "Wt₁", "c_U-c_Va*c_ta1", "m/s", "Wt₁ = U − Va tan α₁",
         "calc"),
        ("Rotor inlet relative velocity", "c_w1", "W₁", "SQRT(c_Va^2+c_wt1^2)", "m/s", "W₁ = √(Va² + Wt₁²)", "calc"),
        ("Stator inlet swirl at design", "c_vt2", "Vt₂", "c_U-c_Va*c_tb2", "m/s", "Vt₂ = U − Va tan β₂", "calc"),
        ("Stator inlet velocity", "c_v2", "V₂", "SQRT(c_Va^2+c_vt2^2)", "m/s", "V₂ = √(Va² + Vt₂²)", "calc"),
        ("Design dynamic head of a stage", "c_head", "h_d", "0.5*(c_w1^2+c_v2^2)", "J/kg", "h_d = (W₁² + V₂²) / 2",
         "calc"),
    ])
    # stage sizing at design
    sh.put(r, 0, "Stage design and annulus sizing (per kg/s of design flow)", "h2")
    sd = _Table(sh, r + 1, 0, widths=False, columns=[
        _Column("j", "Stage", "j", "int", 40),
        _Column("T0", "T₀ in (K)", "previous stage T₀ out", "calc4", 9),
        _Column("P0", "P₀ in (Pa)", "previous stage P₀ out", "calcg", 12),
        _Column("T", "T static (K)", "T₀ − Va² (1 + tan²α₁) / (2 cp)", "calc4", 12),
        _Column("P", "P static (Pa)", "P₀ (T / T₀)^(1/k)", "calcg", 46),
        _Column("A", "Annulus area (m² per kg/s)", "A = 1 / (ρ Va), ρ = P / (R T)", "sci", 12),
        _Column("taus", "Stage τ", "τ_s = 1 + Δh₀ / (cp T₀)", "calc", 11),
        _Column("eta", "Stage isentropic η", "η_s = (τ_s^η_p − 1) / (τ_s − 1)", "calc", 11),
        _Column("loss", "Design loss (J/kg)", "(1 − η_s) Δh₀", "calc4", 11),
        _Column("P0o", "P₀ out (Pa)", "P₀ τ_s^(η_p / k)", "calcg", 12),
        _Column("T0o", "T₀ out (K)", "T₀ τ_s", "calc4", 10),
    ])
    for i in range(n_st):
        x, p = sd.row(i), sd.row(i - 1)
        sd.put(i, "j", i + 1)
        sd.calc(i, "T0", "g_Tref" if i == 0 else p.T0o)
        sd.calc(i, "P0", "g_Pref" if i == 0 else p.P0o)
        sd.calc(i, "T", f"{x.T0}-c_Va^2*(1+c_ta1^2)/(2*c_cp)")
        sd.calc(i, "P", f"{x.P0}*({x.T}/{x.T0})^(1/c_k)")
        sd.calc(i, "A", f"1/({x.P}/(c_R*{x.T})*c_Va)")
        sd.calc(i, "taus", f"1+c_dh/(c_cp*{x.T0})")
        sd.calc(i, "eta", f"({x.taus}^c_etap-1)/({x.taus}-1)")
        sd.calc(i, "loss", f"(1-{x.eta})*c_dh")
        sd.calc(i, "P0o", f"{x.P0}*{x.taus}^(c_etap/c_k)")
        sd.calc(i, "T0o", f"{x.T0}*{x.taus}")
    r = sd.r(n_st) + 1
    r = sh.quantities(r, "Design-point check", [
        ("Stacked design pressure ratio", "c_PRstack", "π_stack", f"{sd.row(n_st - 1).P0o}/g_Pref", "",
         "P₀ out of the last stage / P_ref (equals π_c)", "calc"),
        ("Rear third starts at stage", "", "j_rear", (2 * n_st) // 3 + 1, "", "Stalls here set off surge", "fixed"),
    ])

    # speed lines
    lines = [(n, w_s, w_e, stalled) for n, w_s, _, w_e, stalled in cmap.info["surge"]]
    sh.put(r, 0, "Speed lines", "h2")
    sl = _Table(sh, r + 1, 0, widths=False, columns=[
        _Column("n", "Relative corrected speed", "N / N_design", "calc4", 40),
        _Column("ang", "VIGV angle (°)", "θ_c × clamp((N_open − n) / (N_open − N_closed), 0, 1)", "calc4", 9),
        _Column("ws", "Surge flow (relative)", "GTPerf search: rear-row stall or peak of the line", "search", 12),
        _Column("we", "Choke-end flow (relative)", "GTPerf search: near choke, or pressure rise halved", "search", 12),
        _Column("stall", "Row in rotating stall all along", "GTPerf: dotted on the map", "fixed", 46),
    ])
    for i, (n, w_s, w_e, stalled) in enumerate(lines):
        x = sl.row(i)
        sl.put(i, "n", n, "fixed")
        sl.calc(i, "ang", f"IF(OR(c_vrows<1,c_vopen_n<=c_vshut_n),0,c_vclosed*MIN(MAX((c_vopen_n-{x.n})/"
                          f"(c_vopen_n-c_vshut_n),0),1))")
        sl.put(i, "ws", w_s)
        sl.put(i, "we", w_e)
        sl.put(i, "stall", "yes" if stalled else "no")
    r = sl.r(len(lines)) + 1

    # map points, filled in two passes around the stage table
    sh.put(r, 0, "Map points (R-line 1 = surge, 3 = choke end)", "h2")
    mp = _Table(sh, r + 1, 0, widths=False, columns=[
        _Column("n", "Relative corrected speed", "from the speed lines", "calc4", 40),
        _Column("beta", "R-line", "1 surge … 3 choke end", "calcg", 9),
        _Column("w", "Corrected flow (relative)", "w_s + (w_e − w_s)(R − 1) / 2", "calc", 12),
        _Column("ang", "VIGV angle (°)", "from the speed lines", "calc4", 12),
        _Column("pr", "Pressure ratio", "P₀ out of the last stage / P_ref", "key", 46),
        _Column("tau", "Temperature ratio", "T₀ out of the last stage / T_ref", "calc", 12),
        _Column("eta", "Isentropic efficiency", "(π^k − 1) / (τ − 1)", "key", 11),
        _Column("sm", "Stall margin, worst row", "DF_lim / max DF − 1", "calc4", 11),
        _Column("smr", "Stall margin, rear third", "DF_lim / max DF(rear) − 1", "calc4", 11),
        _Column("prs", "Pressure ratio as the engine uses it", "1 + (π − 1) s, scaled to the design point", "calc4",
                11),
        _Column("etas", "Relative efficiency as the engine uses it", "η / η at the design point", "calc4", 11),
    ])
    points = []  # (line index or None for the design point, R-line)
    for li in range(len(lines)):
        points += [(li, b) for b in R_LINES]
    points.append((None, None))
    for i, (li, b) in enumerate(points):
        if li is None:
            mp.put(i, "n", 1.0, "fixed")
            mp.put(i, "beta", "design")
            mp.put(i, "w", 1.0, "fixed")
            mp.calc(i, "ang", f"IF(OR(c_vrows<1,c_vopen_n<=c_vshut_n),0,c_vclosed*MIN(MAX((c_vopen_n-"
                              f"{mp.row(i).n})/(c_vopen_n-c_vshut_n),0),1))")
            continue
        s = sl.row(li)
        mp.calc(i, "n", sl.sh.ref(sl.r(li), sl.index["n"], False))
        mp.put(i, "beta", b, "calcg")
        mp.calc(i, "w", f"{s.ws}+({s.we}-{s.ws})*({mp.row(i).beta}-1)/2")
        mp.calc(i, "ang", s.ang)

    cols = [
        _Column("pt", "Map point", "row in the map-point table", "int", 7),
        _Column("n", "n", "relative corrected speed", "calc4", 8),
        _Column("w", "w", "relative corrected flow", "calc", 10),
        _Column("ang", "VIGV angle (°)", "schedule", "calc4", 8),
        _Column("j", "Stage j", "", "int", 6),
        _Column("U", "U (m/s)", "n U_design", "calc4", 9),
        _Column("T0", "T₀ in (K)", "previous stage T₀ out", "calc4", 9),
        _Column("P0", "P₀ in (Pa)", "previous stage P₀ out", "calcg", 11),
        _Column("ta", "tan α in", "variable row j ≤ n_v: tan(α₁ − θ (1 − (j − 1)/n_v)); else tan α₁", "calc", 10),
        _Column("tao", "tan α out", "next stage's tan α in (tan α₁ after the last)", "calc", 10),
        _Column("F", "Mass flux (kg/s m²)", "w / A_j", "calc4", 10),
        _Column("rho0", "ρ₀ (kg/m³)", "P₀ / (R T₀)", "calc", 9),
        _Column("c2", "1 + tan²α", "", "calc", 9),
        *_newton_columns("va", "Va"),
        _Column("res", "Continuity residual", "ρ₀ Va (1 − Va²(1+tan²α)/(2 cp T₀))^(1/(γ−1)) / flux − 1", "sci", 11),
        _Column("dh", "Δh₀ (J/kg)", "Euler: U (U − Va (tan α in + tan β₂))", "calc4", 10),
        _Column("wt1", "Wt₁", "U − Va tan α in", "calc4", 9),
        _Column("wt2", "Wt₂", "Va tan β₂", "calc4", 9),
        _Column("vt2", "Vt₂", "U − Wt₂", "calc4", 9),
        _Column("vt3", "Vt₃", "Va tan α out", "calc4", 9),
        _Column("w1", "W₁", "√(Va² + Wt₁²)", "calc4", 9),
        _Column("w2", "W₂", "√(Va² + Wt₂²)", "calc4", 9),
        _Column("v2", "V₂", "√(Va² + Vt₂²)", "calc4", 9),
        _Column("v3", "V₃", "√(Va² + Vt₃²)", "calc4", 9),
        _Column("ir", "Rotor incidence (°)", "atan(Wt₁/Va) − β₁", "calc4", 9),
        _Column("ist", "Stator incidence (°)", "atan(Vt₂/Va) − α₂", "calc4", 9),
        _Column("h", "Dynamic head (J/kg)", "½[W₁²(1+(i_r/i_range)²) + V₂²(1+(i_s/i_range)²)]", "calc4", 11),
        _Column("df", "Diffusion factor", "max over rotor, stator: 1 − W₂/W₁ + |ΔWt|/(2σW₁)", "calc", 9),
        _Column("ratio", "Isentropic T ratio", "1 + (Δh₀ − loss_j h / h_d) / (cp T₀)", "calc", 10),
        _Column("P0o", "P₀ out (Pa)", "P₀ ratio^(1/k)", "calcg", 11),
        _Column("T0o", "T₀ out (K)", "T₀ + Δh₀ / cp", "calc4", 9),
    ]
    st.put(0, 0, "Compressor stage stacking: one row per stage of every map point", "title")
    st.put(1, 0, "Blade-row exit angles are fixed off design, annulus areas come from the design sizing, each "
           "stage's axial velocity solves continuity at its own inlet (Newton columns).", "note")
    tb = _Table(st, 3, 0, cols)
    st.ws.freeze_panes(5, 5)
    rear = (2 * n_st) // 3
    v_last = f"va{NEWTON_STEPS}"
    for i, _ in enumerate(points):
        m = mp.row(i, st)
        for j in range(n_st):
            k = i * n_st + j
            x, p, d = tb.row(k), tb.row(k - 1), sd.row(j, st, absolute=True)
            tb.put(k, "pt", i + 1)
            tb.calc(k, "n", m.n)
            tb.calc(k, "w", m.w)
            tb.calc(k, "ang", m.ang)
            tb.put(k, "j", j + 1)
            tb.calc(k, "U", f"{x.n}*c_U")
            tb.calc(k, "T0", "g_Tref" if j == 0 else p.T0o)
            tb.calc(k, "P0", "g_Pref" if j == 0 else p.P0o)
            tb.calc(k, "ta", f"IF({x.j}<=c_vrows,TAN(c_ta1v-RADIANS({x.ang})*(1-({x.j}-1)/MAX(c_vrows,1))),c_ta1)"
                    .replace("c_ta1v", "ATAN(c_ta1)"))
            tb.calc(k, "tao", f"IF({x.j}<c_N,IF({x.j}+1<=c_vrows,TAN(ATAN(c_ta1)-RADIANS({x.ang})*(1-{x.j}/"
                              f"MAX(c_vrows,1))),c_ta1),c_ta1)")
            tb.calc(k, "F", f"{x.w}/{d.A}")
            tb.calc(k, "rho0", f"{x.P0}/(c_R*{x.T0})")
            tb.calc(k, "c2", f"1+{x.ta}^2")
            tb.calc(k, "va0", f"{x.F}/{x.rho0}")
            for s in range(1, NEWTON_STEPS + 1):
                v = getattr(x, f"va{s - 1}")
                th = f"(1-{v}^2*{x.c2}/(2*c_cp*{x.T0}))"
                tb.calc(k, f"va{s}", f"{v}-({x.rho0}*{v}*{th}^(1/(c_gamma-1))-{x.F})/({x.rho0}*{th}^(1/(c_gamma-1))"
                                    f"*(1-{v}^2*{x.c2}/((c_gamma-1)*c_cp*{x.T0}*{th})))")
            va = getattr(x, v_last)
            tb.calc(k, "res", f"{x.rho0}*{va}*(1-{va}^2*{x.c2}/(2*c_cp*{x.T0}))^(1/(c_gamma-1))/{x.F}-1")
            tb.calc(k, "dh", f"{x.U}*({x.U}-{va}*({x.ta}+c_tb2))")
            tb.calc(k, "wt1", f"{x.U}-{va}*{x.ta}")
            tb.calc(k, "wt2", f"{va}*c_tb2")
            tb.calc(k, "vt2", f"{x.U}-{x.wt2}")
            tb.calc(k, "vt3", f"{va}*{x.tao}")
            tb.calc(k, "w1", f"SQRT({va}^2+{x.wt1}^2)")
            tb.calc(k, "w2", f"SQRT({va}^2+{x.wt2}^2)")
            tb.calc(k, "v2", f"SQRT({va}^2+{x.vt2}^2)")
            tb.calc(k, "v3", f"SQRT({va}^2+{x.vt3}^2)")
            tb.calc(k, "ir", f"DEGREES(ATAN2({va},{x.wt1})-c_b1d)")
            tb.calc(k, "ist", f"DEGREES(ATAN2({va},{x.vt2})-c_a2d)")
            tb.calc(k, "h", f"0.5*({x.w1}^2*(1+({x.ir}/c_irange)^2)+{x.v2}^2*(1+({x.ist}/c_irange)^2))")
            tb.calc(k, "df", f"MAX(1-{x.w2}/{x.w1}+ABS({x.wt1}-{x.wt2})/(2*c_sigma*{x.w1}),"
                             f"1-{x.v3}/{x.v2}+ABS({x.vt2}-{x.vt3})/(2*c_sigma*{x.v2}))")
            tb.calc(k, "ratio", f"1+({x.dh}-{d.loss}*{x.h}/c_head)/(c_cp*{x.T0})")
            tb.calc(k, "P0o", f"{x.P0}*{x.ratio}^(1/c_k)")
            tb.calc(k, "T0o", f"{x.T0}+{x.dh}/c_cp")
        k0, k1 = i * n_st, i * n_st + n_st - 1
        last = tb.row(k1, sh)
        mp.calc(i, "pr", f"{last.P0o}/g_Pref")
        mp.calc(i, "tau", f"{last.T0o}/g_Tref")
        mp.calc(i, "eta", f"({mp.row(i).pr}^c_k-1)/({mp.row(i).tau}-1)")
        mp.calc(i, "sm", f"c_DFlim/MAX(MAX({tb.span('df', k0, k1, sh)}),1E-6)-1")
        mp.calc(i, "smr", f"c_DFlim/MAX(MAX({tb.span('df', k0 + rear, k1, sh)}),1E-6)-1")
    end = mp.r(len(points)) + 1
    design = mp.row(len(points) - 1)
    end = sh.quantities(end, "Map scaled to the engine design point", [
        ("Stacked pressure ratio at n = 1, w = 1", "c_PRraw", "π_d", design.pr, "",
         "the design-point row of the map points", "calc"),
        ("Pressure-ratio scale factor", "c_scale", "s", "(c_PR-1)/(c_PRraw-1)", "",
         "s = (π_c − 1) / (π_d − 1): the engine scales the map so it passes through π_c", "calc"),
        ("Stacked efficiency at n = 1, w = 1", "c_etaraw", "η_d", design.eta, "",
         "the engine uses η / η_d times its design efficiency", "calc"),
    ])
    for i in range(len(points)):
        x = mp.row(i)
        mp.calc(i, "prs", f"1+({x.pr}-1)*c_scale")
        mp.calc(i, "etas", f"{x.eta}/c_etaraw")

    # surge line table for the chart
    sh.put(end, 0, "Surge line", "h2")
    su = _Table(sh, end + 1, 0, widths=False, columns=[
        _Column("n", "Relative corrected speed", "R-line 1 of each speed line", "calc4", 40),
        _Column("w", "Corrected flow (relative)", "", "calc", 9),
        _Column("pr", "Pressure ratio", "", "calc", 12),
    ])
    for li in range(len(lines)):
        i = li * len(R_LINES)
        su.calc(li, "n", mp.sh.ref(mp.r(i), mp.index["n"], False))
        su.calc(li, "w", mp.sh.ref(mp.r(i), mp.index["w"], False))
        su.calc(li, "pr", mp.sh.ref(mp.r(i), mp.index["pr"], False))

    _compressor_charts(book, sh, mp, su, lines, len(points) - 1)
    checks = [(mp.value(i, "n"), mp.value(i, "w"), mp.value(i, "pr"), mp.value(i, "eta"), mp.value(i, "sm"),
               mp.value(i, "smr")) for i in range(len(points))]
    return {"points": checks, "python": py, "map": cmap}


def _speed_label(n):
    return f"{100 * n:.0f} %"


def _labels(count, text):
    """Custom data labels: only the last point of a series, with `text`."""
    return {"custom": [{"delete": True}] * (count - 1) + [{"value": text}], "position": "right",
            "font": {"size": 7, "color": NAVY}}


def _compressor_charts(book, sh, mp, su, lines, n_design):
    wb, nb = book.wb, len(R_LINES)
    c1 = wb.add_chart({"type": "scatter", "subtype": "straight"})
    c2 = wb.add_chart({"type": "scatter", "subtype": "straight"})
    shown = [li for li, (n, *_) in enumerate(lines) if n >= CHART_LOWEST - 1e-9]
    for li in shown:
        n, stalled = lines[li][0], lines[li][3]
        i0, i1 = li * nb, li * nb + nb - 1
        line = {"color": NAVY, "width": 1.0, "dash_type": "round_dot" if stalled else "solid"}
        label = _labels(nb, _speed_label(n)) if round(n * 100) % 10 == 0 else None
        for chart, key in ((c1, "pr"), (c2, "eta")):
            s = {"name": _speed_label(n), "categories": mp.chart_ref("w", i0, i1), "values": mp.chart_ref(key, i0, i1),
                 "line": line, "marker": {"type": "none"}}
            if label:
                s["data_labels"] = label
            chart.add_series(s)
    first = shown[0]
    c1.add_series({"name": "Surge line", "categories": su.chart_ref("w", first, len(lines) - 1),
                   "values": su.chart_ref("pr", first, len(lines) - 1), "line": {"color": RED, "width": 2.25},
                   "marker": {"type": "none"}})
    for chart, key in ((c1, "pr"), (c2, "eta")):
        chart.add_series({"name": "Design point", "categories": mp.chart_ref("w", n_design, n_design),
                          "values": mp.chart_ref(key, n_design, n_design), "line": {"none": True},
                          "marker": {"type": "circle", "size": 7, "fill": {"color": AMBER},
                                     "border": {"color": "white"}}})
    grid = {"visible": True, "line": {"color": "#E3E3E3"}}
    for chart, title, y_axis in ((c1, "Compressor map", {"name": "Pressure ratio", "min": 1}),
                                 (c2, "Compressor efficiency", {"name": "Isentropic efficiency", "min": 0.5, "max": 1,
                                                                "num_format": "0%"})):
        chart.set_title({"name": title, "name_font": {"size": 12, "bold": True, "color": NAVY}})
        chart.set_x_axis({"name": "Corrected flow (relative to design)", "min": 0, "major_gridlines": grid,
                          "num_font": {"size": 8}})
        chart.set_y_axis({**y_axis, "major_gridlines": grid, "num_font": {"size": 8}})
        chart.set_legend({"position": "bottom", "delete_series": list(range(len(shown)))})
        chart.set_size({"width": 760, "height": 500})
        chart.set_chartarea({"border": {"color": "#BBBBBB"}})
    sh.ws.insert_chart(3, 12, c1)
    sh.ws.insert_chart(30, 12, c2)


# --- turbines ----------------------------------------------------------------------------------------

def _turbine(book, t: TurbineDesign):
    p, s = t.prefix, t.stages
    py = _StackedTurbine(t.pressure_ratio, t.efficiency, s, t.t_in)
    n_st = int(s.stages)
    sh, st = book.sheet(t.title), book.sheet(f"{t.title} stages")
    ws = sh.ws
    ws.set_column(0, 0, 40)
    ws.set_column(1, 30, 12.5)
    sh.eq_span = 4
    sh.put(0, 0, f"{t.title} map by mean-line stage stacking", "title")
    sh.put(1, 0, f"Design-point triangles and throat sizing here; every map point is stacked row by row on the "
           f"'{t.title} stages' sheet. Green values are search results from GTPerf (see Read me).", "note")
    r = sh.quantities(3, "Design-point derivation", [
        ("Gas constant", f"{p}_R", "R", "t_cp*(t_gamma-1)/t_gamma", "J/(kg K)", "R = cp (γ − 1) / γ", "calc"),
        ("Exponent (γ − 1) / γ", f"{p}_k", "k", "(t_gamma-1)/t_gamma", "", "k = (γ − 1) / γ", "calc"),
        ("Total work", f"{p}_dh", "Δh₀", f"t_cp*{p}_Tin*{p}_eta*(1-{p}_PR^(-{p}_k))", "J/kg",
         "Δh₀ = cp T₀in η (1 − π^−k)", "calc"),
        ("Mean blade speed at design", f"{p}_U", "U", f"SQRT({p}_dh/{p}_N/{p}_psi)", "m/s", "U = √(Δh₀ / (N ψ))",
         "calc"),
        ("Axial velocity at design", f"{p}_Va", "Va", f"{p}_phi*{p}_U", "m/s", "Va = φ U", "calc"),
        ("Nozzle exit swirl tangent (fixed)", f"{p}_ta2", "tan α₂", f"({p}_psi/2+1-{p}_Rx)/{p}_phi", "",
         "tan α₂ = (ψ/2 + 1 − Λ) / φ", "calc"),
        ("Rotor relative exit tangent (fixed)", f"{p}_tb3", "tan β₃", f"({p}_Rx+{p}_psi/2)/{p}_phi", "",
         "tan β₃ = (Λ + ψ/2) / φ", "calc"),
        ("Stage exit swirl tangent at design", f"{p}_ta3", "tan α₃", f"(1-{p}_Rx-{p}_psi/2)/{p}_phi", "",
         "tan α₃ = (1 − Λ − ψ/2) / φ", "calc"),
        ("Rotor inlet metal angle", f"{p}_b2d", "β₂ (rad)", f"ATAN(({p}_ta2*{p}_phi-1)/{p}_phi)", "rad",
         "β₂ = atan((φ tan α₂ − 1) / φ)", "calc"),
        ("Next nozzle inlet metal angle", f"{p}_a3d", "α₃ (rad)", f"ATAN({p}_ta3)", "rad", "α₃ = atan(tan α₃)", "calc"),
        ("Blade-row loss coefficient", f"{p}_zeta", "ζ", py.zeta, "",
         "GTPerf calibration: the stage stack below gives η_t at design", "search"),
    ])
    sh.put(r, 0, "Stage design and throat sizing (per kg/s of design flow)", "h2")
    sd = _Table(sh, r + 1, 0, widths=False, columns=[
        _Column("j", "Stage", "j", "int", 40),
        _Column("T0", "T₀ in (K)", "previous stage T₀ out", "calc4", 9),
        _Column("P0", "P₀ in (Pa)", "previous stage P₀ out", "calcg", 12),
        _Column("V2", "V₂² (m²/s²)", "Va² (1 + tan²α₂)", "calcg", 12),
        _Column("T2", "T₂ (K)", "T₀ − V₂² / (2 cp)", "calc4", 46),
        _Column("P2", "P₂ (Pa)", "P₀ ((T₂ − ζ V₂²/(2cp)) / T₀)^(1/k)", "calcg", 12),
        _Column("An", "Nozzle area (m² per kg/s)", "1 / (ρ₂ Va)", "sci", 11),
        _Column("W2", "W₂² (m²/s²)", "Va² + (Va tan α₂ − U)²", "calcg", 11),
        _Column("W3", "W₃² (m²/s²)", "Va² (1 + tan²β₃)", "calcg", 11),
        _Column("T3", "T₃ (K)", "T₂ + (W₂² − W₃²) / (2 cp)", "calc4", 10),
        _Column("P3", "P₃ (Pa)", "P₂ ((T₃ − ζ W₃²/(2cp)) / T₂)^(1/k)", "calcg", 11),
        _Column("Ar", "Rotor area (m² per kg/s)", "1 / (ρ₃ Va)", "sci", 11),
        _Column("vt3", "Vt₃ (m/s)", "U − Va tan β₃", "calc4", 10),
        _Column("T0o", "T₀ out (K)", "T₃ + (Va² + Vt₃²) / (2 cp)", "calc4", 10),
        _Column("P0o", "P₀ out (Pa)", "P₃ (T₀ out / T₃)^(1/k)", "calcg", 11),
    ])
    def area(i, key, formula, sized):
        """A throat area by formula, or GTPerf's value where its design exit lies past the row's flux
        peak (supersonic), since GTPerf then sizes the row as just choked."""
        if abs(book.ev(formula, sh.name) / sized - 1) < 1e-6:
            sd.calc(i, key, formula)
        else:
            sd.put(i, key, sized, "search")

    for i in range(n_st):
        x, q = sd.row(i), sd.row(i - 1)
        sd.put(i, "j", i + 1)
        sd.calc(i, "T0", f"{p}_Tin" if i == 0 else q.T0o)
        sd.calc(i, "P0", "g_Pref" if i == 0 else q.P0o)
        sd.calc(i, "V2", f"{p}_Va^2*(1+{p}_ta2^2)")
        sd.calc(i, "T2", f"{x.T0}-{x.V2}/(2*t_cp)")
        sd.calc(i, "P2", f"{x.P0}*(({x.T2}-{p}_zeta*{x.V2}/(2*t_cp))/{x.T0})^(1/{p}_k)")
        area(i, "An", f"1/({x.P2}/({p}_R*{x.T2})*{p}_Va)", py.a_noz[i])
        sd.calc(i, "W2", f"{p}_Va^2+({p}_Va*{p}_ta2-{p}_U)^2")
        sd.calc(i, "W3", f"{p}_Va^2*(1+{p}_tb3^2)")
        sd.calc(i, "T3", f"{x.T2}+({x.W2}-{x.W3})/(2*t_cp)")
        sd.calc(i, "P3", f"{x.P2}*(({x.T3}-{p}_zeta*{x.W3}/(2*t_cp))/{x.T2})^(1/{p}_k)")
        area(i, "Ar", f"1/({x.P3}/({p}_R*{x.T3})*{p}_Va)", py.a_rot[i])
        sd.calc(i, "vt3", f"{p}_U-{p}_Va*{p}_tb3")
        sd.calc(i, "T0o", f"{x.T3}+({p}_Va^2+{x.vt3}^2)/(2*t_cp)")
        sd.calc(i, "P0o", f"{x.P3}*({x.T0o}/{x.T3})^(1/{p}_k)")
    last = sd.row(n_st - 1)
    r = sd.r(n_st) + 1
    r = sh.quantities(r, "Design-point check", [
        ("Stacked design expansion ratio", f"{p}_PRstack", "π_stack", f"g_Pref/{last.P0o}", "",
         "P_ref / P₀ out of the last stage (equals π_t)", "calc"),
        ("Stacked design efficiency", f"{p}_etastack", "η_stack",
         f"({p}_Tin-{last.T0o})/({p}_Tin*(1-{p}_PRstack^(-{p}_k)))", "", "equals η_t when ζ is calibrated", "calc"),
        ("Largest expansion ratio on the charts", f"{p}_PRmax", "π_max", f"MAX(1.6*{p}_PR,1.5)", "", "as in GTPerf",
         "calc"),
    ])

    # speed lines: the choking flow is a search result; points sit at fixed fractions of it
    lines = []
    for n in sorted({c[0] for c in _turbine_lines(t)}):
        hi = 2.0
        while py.point(n, hi) is not None and hi < 50:
            hi *= 1.5
        w_ch = _bisect(lambda w, n=n: py.point(n, w) is not None, 0.0, hi)
        xs, last_pr = [], 1.0
        for x in TURBINE_X:
            res = py.point(n, w_ch * (1 - (1 - x) ** 2) * 0.9995)
            if res and res[0] > last_pr:  # the rising part of the line, as in the tool's map
                xs.append(x)
                last_pr = res[0]
        if len(xs) >= 3 and xs[-1] == 1.0:
            lines.append((n, w_ch, xs))
    sh.put(r, 0, "Speed lines", "h2")
    sl = _Table(sh, r + 1, 0, widths=False, columns=[
        _Column("n", "Relative corrected speed", "N / N_design", "calc4", 40),
        _Column("wch", "Choking flow (relative)", "GTPerf search: largest flow every row passes", "search", 9),
        _Column("prc", "Expansion ratio at choke", "last point of the line", "calc", 12),
        _Column("etac", "Efficiency at choke", "last point of the line", "calc", 12),
    ])
    for i, (n, w_ch, _) in enumerate(lines):
        sl.put(i, "n", n, "fixed")
        sl.put(i, "wch", w_ch)
    r = sl.r(len(lines)) + 1
    sh.put(r, 0, "Map points (along each speed line, then past choke)", "h2")
    mp = _Table(sh, r + 1, 0, widths=False, columns=[
        _Column("n", "Relative corrected speed", "from the speed lines", "calc4", 40),
        _Column("x", "Position x", "w = w_choke (1 − (1 − x)²) × 0.9995", "calcg", 9),
        _Column("w", "Flow capacity (relative)", "stacked flow, constant past choke", "calc", 12),
        _Column("pr", "Expansion ratio", "P_ref / P₀ out; past choke spread to π_max", "key", 12),
        _Column("eta", "Isentropic efficiency", "(T₀in − T₀out) / (T₀in (1 − π^−k)); past choke η_ch (1 − c x²)",
                "key", 46),
        _Column("xc", "Past choke x", "(1 − π^−k) / (1 − π_ch^−k) − 1", "calc", 12),
    ])
    pts, blocks = [], []
    for li, (_, _, xs) in enumerate(lines):
        start = len(pts)
        pts += [(li, x, False) for x in xs]
        pts += [(li, m, True) for m in range(1, CHOKE_EXTENSION + 1)]
        blocks.append((start, len(pts) - 1, start + len(xs) - 1))
    stage_rows = {}
    for i, (li, x, past) in enumerate(pts):
        s_ = sl.row(li)
        mp.calc(i, "n", sl.sh.ref(sl.r(li), sl.index["n"], False))
        if not past:
            mp.put(i, "x", x, "calcg")
            mp.calc(i, "w", f"{s_.wch}*(1-(1-{mp.row(i).x})^2)*0.9995")
            stage_rows[i] = len(stage_rows)
    cols = [
        _Column("pt", "Map point", "row in the map-point table", "int", 7),
        _Column("n", "n", "relative corrected speed", "calc4", 8),
        _Column("w", "w", "relative flow capacity", "calc", 10),
        _Column("j", "Stage j", "", "int", 6),
        _Column("U", "U (m/s)", "n U_design", "calc4", 9),
        _Column("T0", "T₀ in (K)", "previous stage T₀ out", "calc4", 9),
        _Column("P0", "P₀ in (Pa)", "previous stage P₀ out", "calcg", 11),
        _Column("sw", "Inlet swirl tan α", "previous stage Vt₃ / Va₃", "calc", 9),
        _Column("zn", "Nozzle loss ζ_n", "ζ (1 + ((α_in − α₃)/i_range)²), first nozzle ζ", "calc", 9),
        _Column("Fn", "Nozzle flux (kg/s m²)", "w / A_n", "calc4", 10),
        _Column("cn", "c_n", "(1 + tan²α₂) / (2 cp)", "sci", 10),
        *_newton_columns("v", "Va₂"),
        _Column("resn", "Nozzle continuity residual", "ρ₂ Va₂ / flux − 1", "sci", 10),
        _Column("t2", "T₂ (K)", "T₀ − c_n Va₂²", "calc4", 9),
        _Column("p2", "P₂ (Pa)", "P₀ ((T₀ − (1+ζ_n) c_n Va₂²) / T₀)^(1/k)", "calcg", 11),
        _Column("wt2", "Wt₂ (m/s)", "Va₂ tan α₂ − U", "calc4", 9),
        _Column("w2", "W₂² (m²/s²)", "Va₂² + Wt₂²", "calcg", 10),
        _Column("ir", "Rotor incidence (°)", "atan(Wt₂/Va₂) − β₂", "calc4", 9),
        _Column("zr", "Rotor loss ζ_r", "ζ (1 + (i_r / i_range)²)", "calc", 9),
        _Column("Fr", "Rotor flux (kg/s m²)", "w / A_r", "calc4", 10),
        _Column("cr", "c_r", "(1 + tan²β₃) / (2 cp)", "sci", 10),
        _Column("A", "T₀,rel (K)", "T₂ + W₂² / (2 cp)", "calc4", 9),
        *_newton_columns("u", "Va₃"),
        _Column("resr", "Rotor continuity residual", "ρ₃ Va₃ / flux − 1", "sci", 10),
        _Column("t3", "T₃ (K)", "T₀,rel − c_r Va₃²", "calc4", 9),
        _Column("p3", "P₃ (Pa)", "P₂ ((T₀,rel − (1+ζ_r) c_r Va₃²) / T₂)^(1/k)", "calcg", 11),
        _Column("vt3", "Vt₃ (m/s)", "U − Va₃ tan β₃", "calc4", 9),
        _Column("T0o", "T₀ out (K)", "T₃ + (Va₃² + Vt₃²) / (2 cp)", "calc4", 9),
        _Column("P0o", "P₀ out (Pa)", "P₃ (T₀ out / T₃)^(1/k)", "calcg", 11),
        _Column("swo", "Exit swirl tan α", "Vt₃ / Va₃", "calc", 9),
    ]
    st.put(0, 0, f"{t.title} stage stacking: one row per stage of every map point", "title")
    st.put(1, 0, "Nozzle and rotor exit angles are fixed, throat areas come from the design sizing, each row's "
           "axial velocity solves continuity at its exit (Newton columns).", "note")
    tb = _Table(st, 3, 0, cols)
    st.ws.freeze_panes(5, 4)

    def newton(k, prefix, start, flux, pref, a, tref, z, c):
        x = tb.row(k)
        tb.calc(k, f"{prefix}0", start)
        for m in range(1, NEWTON_STEPS + 1):
            v = getattr(x, f"{prefix}{m - 1}")
            s_ = f"({a}-(1+{z})*{c}*{v}^2)"
            q = f"({a}-{c}*{v}^2)"
            f = f"{pref}/{p}_R*({s_}/{tref})^(1/{p}_k)*{v}/{q}"
            tb.calc(k, f"{prefix}{m}", f"{v}-({f}-{flux})/({f}*(1/{v}-2*(1+{z})*{c}*{v}/({p}_k*{s_})+2*{c}*{v}/{q}))")
        v = getattr(x, f"{prefix}{NEWTON_STEPS}")
        return v, f"{pref}/{p}_R*(({a}-(1+{z})*{c}*{v}^2)/{tref})^(1/{p}_k)*{v}/({a}-{c}*{v}^2)/{flux}-1"

    for i, k0 in stage_rows.items():
        m = mp.row(i, st)
        for j in range(n_st):
            k = k0 * n_st + j
            x, q, d = tb.row(k), tb.row(k - 1), sd.row(j, st, absolute=True)
            tb.put(k, "pt", i + 1)
            tb.calc(k, "n", m.n)
            tb.calc(k, "w", m.w)
            tb.put(k, "j", j + 1)
            tb.calc(k, "U", f"{x.n}*{p}_U")
            tb.calc(k, "T0", f"{p}_Tin" if j == 0 else q.T0o)
            tb.calc(k, "P0", "g_Pref" if j == 0 else q.P0o)
            if j == 0:
                tb.put(k, "sw", "none")
                tb.calc(k, "zn", f"{p}_zeta")
            else:
                tb.calc(k, "sw", q.swo)
                tb.calc(k, "zn", f"{p}_zeta*(1+(DEGREES(ATAN({x.sw})-{p}_a3d)/{p}_irange)^2)")
            tb.calc(k, "Fn", f"{x.w}/{d.An}")
            tb.calc(k, "cn", f"(1+{p}_ta2^2)/(2*t_cp)")
            va2, res = newton(k, "v", f"{x.Fn}*{p}_R*{x.T0}/{x.P0}", x.Fn, x.P0, x.T0, x.T0, x.zn, x.cn)
            tb.calc(k, "resn", res)
            tb.calc(k, "t2", f"{x.T0}-{x.cn}*{va2}^2")
            tb.calc(k, "p2", f"{x.P0}*(({x.T0}-(1+{x.zn})*{x.cn}*{va2}^2)/{x.T0})^(1/{p}_k)")
            tb.calc(k, "wt2", f"{va2}*{p}_ta2-{x.U}")
            tb.calc(k, "w2", f"{va2}^2+{x.wt2}^2")
            tb.calc(k, "ir", f"DEGREES(ATAN2({va2},{x.wt2})-{p}_b2d)")
            tb.calc(k, "zr", f"{p}_zeta*(1+({x.ir}/{p}_irange)^2)")
            tb.calc(k, "Fr", f"{x.w}/{d.Ar}")
            tb.calc(k, "cr", f"(1+{p}_tb3^2)/(2*t_cp)")
            tb.calc(k, "A", f"{x.t2}+{x.w2}/(2*t_cp)")
            va3, res = newton(k, "u", f"{x.Fr}*{p}_R*{x.A}/({x.p2}*({x.A}/{x.t2})^(1/{p}_k))", x.Fr, x.p2, x.A, x.t2,
                              x.zr, x.cr)
            tb.calc(k, "resr", res)
            tb.calc(k, "t3", f"{x.A}-{x.cr}*{va3}^2")
            tb.calc(k, "p3", f"{x.p2}*(({x.A}-(1+{x.zr})*{x.cr}*{va3}^2)/{x.t2})^(1/{p}_k)")
            tb.calc(k, "vt3", f"{x.U}-{va3}*{p}_tb3")
            tb.calc(k, "T0o", f"{x.t3}+({va3}^2+{x.vt3}^2)/(2*t_cp)")
            tb.calc(k, "P0o", f"{x.p3}*({x.T0o}/{x.t3})^(1/{p}_k)")
            tb.calc(k, "swo", f"{x.vt3}/{va3}")
        last = tb.row(k0 * n_st + n_st - 1, sh)
        mp.calc(i, "pr", f"g_Pref/{last.P0o}")
        mp.calc(i, "eta", f"({p}_Tin-{last.T0o})/({p}_Tin*(1-{mp.row(i).pr}^(-{p}_k)))")
    for li, (_, b1, bc) in enumerate(blocks):
        ch = mp.row(bc)
        sl.calc(li, "prc", mp.sh.ref(mp.r(bc), mp.index["pr"], False))
        sl.calc(li, "etac", mp.sh.ref(mp.r(bc), mp.index["eta"], False))
        for i in range(bc + 1, b1 + 1):
            m = i - bc
            x = mp.row(i)
            mp.put(i, "x", f"choke + {m}")
            mp.calc(i, "w", ch.w)
            mp.calc(i, "pr", f"{ch.pr}+(MAX({p}_PRmax,{ch.pr})-{ch.pr})*{m}/{CHOKE_EXTENSION}")
            mp.calc(i, "xc", f"(1-{x.pr}^(-{p}_k))/(1-{ch.pr}^(-{p}_k))-1")
            mp.calc(i, "eta", f"{ch.eta}*(1-t_cpost*{x.xc}^2)")
    _turbine_charts(book, sh, mp, lines, blocks, t)
    checks = [(mp.value(i, "n"), mp.value(i, "w"), mp.value(i, "pr"), mp.value(i, "eta")) for i in stage_rows]
    return {"points": checks, "python": py}


def _turbine_lines(t: TurbineDesign):
    from .mapgen import turbine_map

    return turbine_map(t.pressure_ratio, t.efficiency, t.stages, t.t_in).info["choke"]


def _turbine_charts(book, sh, mp, lines, blocks, t):
    wb = book.wb
    c1 = wb.add_chart({"type": "scatter", "subtype": "straight"})
    c2 = wb.add_chart({"type": "scatter", "subtype": "straight"})
    shown = 0
    for (n, _, _), (b0, b1, _) in zip(lines, blocks):
        if n < CHART_LOWEST - 1e-9:
            continue
        shown += 1
        design = abs(n - 1) < 1e-9
        line = {"color": NAVY if design else BLUE, "width": 1.75 if design else 1.0}
        for chart, key in ((c1, "w"), (c2, "eta")):
            s = {"name": _speed_label(n), "categories": mp.chart_ref("pr", b0, b1), "values": mp.chart_ref(key, b0, b1),
                 "line": line, "marker": {"type": "none"}}
            if round(n * 100) % 10 == 0:
                s["data_labels"] = _labels(b1 - b0 + 1, _speed_label(n))
            chart.add_series(s)
    grid = {"visible": True, "line": {"color": "#E3E3E3"}}
    xmax = max(1.6 * t.pressure_ratio, 1.5)
    for chart, title, ytitle, extra in ((c1, "flow capacity", "Flow capacity (relative to design)", {"min": 0}),
                                        (c2, "efficiency", "Isentropic efficiency", {"min": 0, "max": 1,
                                                                                     "num_format": "0%"})):
        chart.set_title({"name": f"{t.title}: {title}", "name_font": {"size": 12, "bold": True, "color": NAVY}})
        chart.set_x_axis({"name": "Expansion ratio (total to total)", "min": 1, "max": round(xmax + 0.05, 1),
                          "major_gridlines": grid, "num_font": {"size": 8}})
        chart.set_y_axis({"name": ytitle, "major_gridlines": grid, "num_font": {"size": 8}, **extra})
        chart.set_legend({"none": True})
        chart.set_size({"width": 760, "height": 440})
        chart.set_chartarea({"border": {"color": "#BBBBBB"}})
    sh.ws.insert_chart(3, 16, c1)
    sh.ws.insert_chart(27, 16, c2)


# --- read me -----------------------------------------------------------------------------------------

def _readme(book, name, version, turbines):
    sh = book.sheet("Read me")
    ws = sh.ws
    ws.set_column(0, 0, 26)
    ws.set_column(1, 1, 100)
    ws.activate()
    sh.put(0, 0, "GTPerf calculated maps", "title")
    rows = [
        ("Engine", name),
        ("Made with", f"GTPerf {version} (gtperf Python package), github.com/tanishq-charde/gtperf"),
        ("What this is", "The compressor and turbine maps of this engine, worked out in Excel by mean-line stage "
                         "stacking from the design values on the Inputs sheet. Every map point is a formula, and the "
                         "charts are drawn from those cells."),
        ("Sheets", "Inputs: the design values. Compressor and each turbine: design-point derivation, stage sizing, "
                   "speed lines, map points and the charts. The '… stages' sheets: one row per stage of every map "
                   "point, with the equation of each column under its heading."),
        ("Colours", "Blue on cream: inputs, change freely. Black: formulas. Green: values GTPerf found by searching "
                    "(see below). Grey: layout values (number of stages) or labels."),
        ("Searched values", "Where each speed line starts and ends (surge and the choke end of a compressor line, "
                            "the choking flow of a turbine line) and the turbine loss coefficient ζ come from "
                            "searches in GTPerf, which Excel formulas cannot do. They stay fixed when an input "
                            "changes, so after a large change download the workbook again from GTPerf. The checks "
                            "show how they tie back: the rear-third stall margin is zero at a surge point set by "
                            "rear-row stall, and the stacked design efficiency equals η_t."),
        ("Compressor model", "The design temperature rise is shared equally by N repeating stages at constant mean "
                             "blade speed. Blade-row exit angles are fixed off design, so each stage follows the "
                             "Euler work Δh₀ = U(U − Va(tan α₁ + tan β₂)). Annulus areas are sized at design; off "
                             "design each stage's axial velocity follows from continuity at its own inlet density. "
                             "Losses are the design loss of each stage scaled by the incidence-weighted dynamic "
                             "head. The variable vanes turn with speed on the schedule; later variable rows by "
                             "less. A blade row stalls when its Lieblein diffusion factor exceeds DF_lim."),
        ("Turbine model", "The design work is shared equally by N repeating stages. Nozzle and rotor exit angles "
                          "are fixed and their throats sized at design; off design each row passes the flow by "
                          "continuity at its exit, with the loss coefficient raised by incidence. Past choke the "
                          "flow capacity stays constant and the efficiency falls as η_choke (1 − c x²)."),
        ("Continuity", f"Axial velocity at a given mass flux has no closed form, so it is solved by "
                       f"{NEWTON_STEPS} Newton steps in helper columns, started from the incompressible value "
                       f"(below the root, so each step moves towards it). The residual column shows the error."),
        ("Gas", "Perfect gas: γ = 1.4, cp = 1004.5 J/(kg K) in the compressor; γ = 1.33, cp = 1150 J/(kg K) in the "
                "turbines. Maps are in relative corrected terms (1.0 = design)."),
        ("Turbines", ", ".join(t.title for t in turbines) + ": inlet temperature and expansion ratio from the "
                                                            "engine cycle at ISO."),
        ("Note", "The map points use R-lines 1 to 3 in steps of 0.2; GTPerf's own map also carries R-lines past "
                 "surge for its solver. Speed lines below 50 % are in the tables but not on the charts."),
    ]
    for i, (a, b) in enumerate(rows):
        sh.put(2 + i, 0, a, "label")
        sh.put(2 + i, 1, b, "wrap")
        ws.set_row(2 + i, 15 * max(1, math.ceil(len(b) / 110)))
    return sh


# --- entry -------------------------------------------------------------------------------------------

def map_workbook(name, compressor, turbines, version="", checks=None):
    """The maps of one engine as .xlsx bytes. compressor = (pressure ratio, polytropic efficiency,
    CompressorStages); turbines = [TurbineDesign, ...]. With a `checks` dict, the evaluated map points of each
    component are put in it for testing."""
    book = _Book()
    _readme(book, name, version, turbines)
    _inputs(book, name, compressor[0], compressor[1], compressor[2], turbines)
    out = {"Compressor": _compressor(book, *compressor)}
    for t in turbines:
        out[t.title] = _turbine(book, t)
    book.wb.set_properties({"title": f"{name}: calculated maps", "author": "GTPerf", "comments":
                            "Compressor and turbine maps by mean-line stage stacking, as live formulas"})
    book.wb.set_calc_mode("auto")
    data = book.close()
    if checks is not None:
        checks.update(out)
    return data


def engine_map_workbook(model, checks=None):
    """The map workbook of a design-mode engine (`gtperf.app.core.design_engine`)."""
    from importlib.metadata import PackageNotFoundError, version

    from .app.core import compressor_stages

    try:
        release = version("gtperf")
    except PackageNotFoundError:
        from . import __version__ as release

    d, engine = model.datasheet, model.engine
    st = engine.design.stations
    turbines = []
    for key, title, prefix, s_in, s_out, eff in (("hpt", "HP turbine", "h", st["41"], st["44"], engine.hpt_eff_design),
                                                 ("pt", "Power turbine", "p", st["45"], st["5"], engine.pt_eff_design)):
        stages = TurbineStages(int(d[f"{key}_stages"]), d[f"{key}_loading"], d[f"{key}_flow_coeff"],
                               d[f"{key}_reaction"])
        turbines.append(TurbineDesign(title, prefix, s_in.p / s_out.p, eff, s_in.t, stages))
    return map_workbook(d.get("name") or "Engine", (d["pressure_ratio"], d["compressor_polytropic_eff"],
                                                    compressor_stages(d)), turbines, release, checks)


__all__ = ["TurbineDesign", "map_workbook", "engine_map_workbook"]
