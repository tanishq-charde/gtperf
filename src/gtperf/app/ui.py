"""GTPerf Streamlit app (beta). Run with `gtperf gui` or `streamlit run src/gtperf/app/ui.py`.

The engine set up on the first page feeds every other section, so changing it marks their results out of date.
Each section has its own inputs, filters for what it shows, and downloads; the Reports page bundles any
combination of sections into SVG, Excel, PowerPoint, PDF and CSV files.
"""

from __future__ import annotations

import base64
import html
import json
import re
import sys
import tomllib

import pandas as pd
import streamlit as st

from gtperf.app import core, exports
from gtperf.maps import Health
from gtperf.site import Site

LOGO = str(exports.LOGO)
PAGES = [
    "Overview",
    "Engine",
    "Site performance",
    "Part load & emissions",
    "Pass-off test",
    "Fleet monitoring",
    "Turbofan",
    "Reports & downloads",
]
SECTION_OF = {
    "Engine": "engine",
    "Site performance": "site",
    "Part load & emissions": "partload",
    "Pass-off test": "passoff",
    "Fleet monitoring": "fleet",
    "Turbofan": "turbofan",
}
NUMBER = {"engine": 1, "site": 2, "partload": 3, "passoff": 4, "fleet": 5, "turbofan": 6}
USES_ENGINE = {"engine", "site", "partload", "passoff", "fleet"}
PROJECT_PAGE = "https://tanishq-charde.github.io/#p/gtperf"
CODE = "https://github.com/tanishq-charde/gtperf"

st.set_page_config(page_title="GTPerf (beta)", page_icon=LOGO, layout="wide", initial_sidebar_state="collapsed")
# The look follows the engine design tool on the portfolio site: ivory paper, navy ink, serif headings,
# input groups as collapsible lists on the left and results on the right.
st.markdown(f"<style>{(exports.ASSETS / 'style.css').read_text()}</style>", unsafe_allow_html=True)

ss = st.session_state
ss.setdefault("page", "Overview")
ss.setdefault("sections", {})
ss.setdefault("stale", set())
if "datasheet" not in ss:
    ss.datasheet = core.load_datasheet()


@st.cache_resource(show_spinner="Calibrating the engine model…")
def _model(datasheet_json):
    return core.build_engine(json.loads(datasheet_json))


def model():
    return _model(json.dumps(ss.datasheet, sort_keys=True))


def go(page):
    ss.page = page
    ss.study = page


def store(section):
    ss.sections[section.key] = section
    ss.stale.discard(section.key)


@st.cache_data
def _logo_uri():
    return "data:image/png;base64," + base64.b64encode(exports.LOGO.read_bytes()).decode()


# --- top bar, title and study list ---------------------------------------------------------------------

st.markdown(
    f"""<div class="topbar"><img src="{_logo_uri()}" alt="GTPerf">
<nav><a href="{PROJECT_PAGE}" target="_top">Project page</a><a href="{CODE}" target="_blank">Code</a></nav></div>""",
    unsafe_allow_html=True,
)


def status(page):
    key = SECTION_OF.get(page)
    if key in ss.sections:
        return f"{page}  ⚠ recalculate" if key in ss.stale else f"{page}  ✓"
    return page


def hero(title, lede):
    st.markdown(
        f'<p><a class="backbtn" href="{PROJECT_PAGE}" target="_top">← Back to the GTPerf project</a></p>',
        unsafe_allow_html=True,
    )
    st.markdown(f"# {title}")
    st.markdown(
        f'<p class="lede">{lede}</p><p><span class="beta">BETA · IN PROGRESS</span> &nbsp; '
        f'<span class="engine-tag">Engine in use: <b>{ss.datasheet.get("name", "custom")}</b></span></p>',
        unsafe_allow_html=True,
    )
    with st.container(key="studypick"):
        ss.setdefault("study", ss.page)
        st.selectbox("Study:", PAGES, key="study", format_func=status, on_change=lambda: go(ss.study))
    if sys.platform == "emscripten":  # running in the browser from the portfolio site
        st.caption("Running in your browser: the longer studies take a little more time than on a PC.")
    st.divider()


# --- shared result display -----------------------------------------------------------------------------

_NUM = re.compile(r"^([+\-−]?[\d,]*\.?\d+)\s*(.*)$")


def stats(key_results):
    cells = []
    for label, value in key_results:
        value = re.sub(r"\s+(PASS|FAIL)$", "", str(value))  # the pass/fail rows below carry the verdicts
        m = _NUM.match(value)
        v = f'{m.group(1)}<span class="u">{html.escape(m.group(2))}</span>' if m else html.escape(str(value))
        cls = "v" if m else "v word"
        cells.append(f'<div class="stat"><div class="{cls}">{v}</div><div class="l">{html.escape(label)}</div></div>')
    st.markdown(f'<div class="stats">{"".join(cells)}</div>', unsafe_allow_html=True)


def checks(df):
    rows = []
    for _, r in df.iterrows():
        ok = str(r["Verdict"]).upper().startswith("PASS")
        tag = '<span class="tag ok">✓ Pass</span>' if ok else '<span class="tag bad">✗ Fail</span>'
        limit = exports._fmt(r["Limit"]).replace(">=", "≥").replace("<=", "≤")
        rows.append(
            f'<div class="check">{tag}<span>{html.escape(str(r["Result"]))}</span>'
            f'<span class="need">limit {html.escape(limit)}</span></div>'
        )
    st.markdown(f'<div class="checks">{"".join(rows)}</div>', unsafe_allow_html=True)


def show_section(section):
    st.markdown(f"## {NUMBER.get(section.key, '')}. {section.title}")
    if section.key in ss.stale:
        st.markdown(
            '<p class="stale">The engine has changed since these results were calculated. '
            "Press Calculate again to update them.</p>",
            unsafe_allow_html=True,
        )
    stats(section.key_results)
    if "Acceptance" in section.tables and "Verdict" in section.tables["Acceptance"]:
        checks(section.tables["Acceptance"])
    for n in section.notes:
        st.markdown(f'<p class="note">{html.escape(n)}</p>', unsafe_allow_html=True)

    names = list(section.figures)
    if names:
        shown = names
        if len(names) > 1:
            pick = st.selectbox("Charts to show", ["All charts"] + names, key=f"figsel_{section.key}")
            shown = names if pick == "All charts" else [pick]
        for name in shown:
            fig = section.figures[name]
            st.pyplot(fig, width="stretch")
            st.download_button(
                f"Download “{name}” (SVG)",
                exports.figure_svg(fig),
                file_name=f"{exports._slug(section.title)}_{exports._slug(name)}.svg",
                mime="image/svg+xml",
                key=f"svg_{section.key}_{name}",
            )

    if section.tables:
        with st.expander("Tables", expanded=True):
            name = st.selectbox("Table", list(section.tables), key=f"tabsel_{section.key}")
            df = section.tables[name]
            c1, c2 = st.columns([3, 2])
            columns = c1.multiselect(
                "Columns", list(df.columns), default=list(df.columns), key=f"cols_{section.key}_{name}"
            )
            query = c2.text_input("Filter rows (text match)", key=f"q_{section.key}_{name}")
            view = df[columns] if columns else df
            if query:
                mask = view.astype(str).apply(lambda col: col.str.contains(query, case=False, regex=False)).any(axis=1)
                view = view[mask]
            st.dataframe(view, width="stretch", hide_index=True)
            st.download_button(
                "Download this table (CSV)",
                view.to_csv(index=False),
                file_name=f"{exports._slug(section.title)}_{exports._slug(name)}.csv",
                mime="text/csv",
                key=f"csv_{section.key}_{name}",
            )
    with st.expander("Inputs used"):
        st.dataframe(
            pd.DataFrame({"Input": list(section.inputs), "Value": [exports._fmt(v) for v in section.inputs.values()]}),
            width="stretch",
            hide_index=True,
        )
    with st.expander("Download this study"):
        download_buttons([section], exports._slug(section.title), section.title)


def download_buttons(sections, stem, title):
    if st.button("Prepare files", key=f"prep_{stem}", type="primary"):
        with st.spinner("Building files…"):
            ss[f"files_{stem}"] = {
                "svg": exports.figures_zip(sections),
                "xlsx": exports.to_xlsx(sections),
                "pptx": exports.to_pptx(sections, title),
                "pdf": exports.to_pdf(sections, title),
                "csv": exports.tables_csv_zip(sections),
            }
    files = ss.get(f"files_{stem}")
    if files:
        c = st.columns(3)
        c[0].download_button(
            "Charts (SVG, zip)", files["svg"], f"{stem}_charts.zip", "application/zip", key=f"d1_{stem}"
        )
        c[1].download_button(
            "Workbook (XLSX)",
            files["xlsx"],
            f"{stem}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"d2_{stem}",
        )
        c[2].download_button(
            "Slides (PPTX)",
            files["pptx"],
            f"{stem}.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            key=f"d3_{stem}",
        )
        c = st.columns(3)
        c[0].download_button("Report (PDF)", files["pdf"], f"{stem}.pdf", "application/pdf", key=f"d4_{stem}")
        c[1].download_button(
            "All tables (CSV, zip)", files["csv"], f"{stem}_tables.zip", "application/zip", key=f"d5_{stem}"
        )
    else:
        st.caption("Builds the charts as SVG, the tables as an Excel workbook, a PowerPoint deck and a PDF report.")


def next_button(page):
    st.button(f"Next: {page} →", on_click=go, args=(page,), key=f"next_{page}")


def layout():
    """The design tool's two columns: input lists on the left, results on the right."""
    left, right = st.columns([1, 2.3], gap="large")
    return left.container(key="inputs"), right


def empty(text="Set the inputs on the left and press Calculate."):
    st.markdown(f'<p class="note">{text}</p>', unsafe_allow_html=True)


# --- pages ---------------------------------------------------------------------------------------------

STUDIES = [
    ("Engine", "Calibrate a twin-shaft model to a datasheet and see the gas path at ISO."),
    ("Site performance", "Output, heat rate and exhaust at any site, with correction factors and curves."),
    ("Part load & emissions", "Performance against load, DLE NOx and CO, and emissions-compliant turndown."),
    ("Pass-off test", "A synthetic test with sensor faults, validated and corrected, with GUM uncertainty."),
    ("Fleet monitoring", "A synthetic fleet with fouling, washes and faults, with detection and diagnosis."),
    ("Turbofan", "Two-spool turbofan design point, thrust lapse, LTO NOx and noise sources."),
    ("Reports & downloads", "Bundle any studies into SVG, Excel, PowerPoint, PDF and CSV files."),
]


def page_overview():
    hero(
        "Gas turbine performance tool",
        "Calibrate an engine to its datasheet, then predict site output, check emissions at part load, validate "
        "a pass-off test and watch a fleet for faults. Every study uses the engine you set up, and every result "
        "downloads as SVG, Excel, PowerPoint or PDF.",
    )
    left, right = layout()
    with left:
        with st.expander("Studies", expanded=True):
            for name, text in STUDIES:
                st.button(name, on_click=go, args=(name,), key=f"home_{name}", width="stretch", help=text)
        with st.expander("Quick start", expanded=True):
            st.caption("Runs every study with its default inputs, so you can look around and download at once.")
            run = st.button("Run every study", type="primary", width="stretch")
    with right:
        st.markdown("## How the studies link")
        st.graphviz_chart("""digraph { rankdir=LR; bgcolor="transparent";
            node [shape=box style="filled" fillcolor="#FFFFFF" color="#12224A" fontname="Times" fontsize=12];
            edge [color="#12224A"];
            D [label="Datasheet\\n(edit or upload)"]; E [label="Engine model\\n(calibrated)"];
            S [label="Site performance"]; P [label="Part load\\n& emissions"]; T [label="Pass-off test"];
            F [label="Fleet monitoring"]; A [label="Turbofan\\n(separate aero model)"];
            R [label="Reports\\nSVG · XLSX · PPTX · PDF" fillcolor="#12224A" fontcolor="#FBF8EF"];
            D -> E; E -> S; E -> P; E -> T; E -> F; S -> R; P -> R; T -> R; F -> R; A -> R; E -> R; }""")
        if run:
            m = model()
            prog = st.progress(0.0, "Engine…")
            store(core.engine_section(m))
            prog.progress(0.15, "Site performance…")
            store(core.site_section(m, Site("Hot site", 0.0, 35.0, 0.4, 10.0, 25.0)))
            prog.progress(0.3, "Part load and emissions…")
            store(core.part_load_section(m))
            prog.progress(0.45, "Pass-off test…")
            store(core.passoff_section(m, faults=list(core.FAULT_CHOICES)[:4]))
            prog.progress(0.6, "Fleet monitoring…")
            store(core.fleet_section(m, n_engines=4, days=240))
            prog.progress(0.8, "Turbofan…")
            store(core.turbofan_section())
            prog.progress(1.0, "Done")
            st.rerun()
        if ss.sections:
            st.markdown("## Calculated so far")
            for name, _ in STUDIES[:-1]:
                key = SECTION_OF[name]
                if key in ss.sections:
                    stats(ss.sections[key].key_results[:4])
                    st.button(f"Open {name} →", on_click=go, args=(name,), key=f"open_{name}")


def page_engine():
    hero(
        "Engine and calibration",
        "Enter the published ISO rating and the modelling assumptions. The model fits air flow, turbine entry "
        "temperature and turbine efficiency to the datasheet; every other study uses this engine.",
    )
    d = ss.datasheet
    a = d.setdefault("assumptions", {})
    cool = a.setdefault("cooling", {})
    left, right = layout()
    with left:
        with st.form("engine"):
            with st.expander("Published rating (ISO)", expanded=True):
                name = st.text_input("Engine name", d.get("name", "engine"))
                power = st.number_input("Electrical output (MW)", 1.0, 100.0, float(d["electrical_power_MW"]), 0.1)
                hr = st.number_input("Heat rate (kJ/kWh)", 7000.0, 16000.0, float(d["heat_rate_kJ_per_kWh"]), 10.0)
                wex = st.number_input("Exhaust flow (kg/s)", 2.0, 300.0, float(d["exhaust_flow_kg_s"]), 0.1)
                tex = st.number_input(
                    "Exhaust temperature, check only (°C)",
                    300.0,
                    700.0,
                    float(d.get("exhaust_temperature_C") or 555.0),
                    1.0,
                )
                pr = st.number_input("Pressure ratio", 5.0, 40.0, float(d["pressure_ratio"]), 0.1)
            with st.expander("Efficiencies and losses"):
                etac = st.number_input(
                    "Compressor polytropic efficiency",
                    0.80,
                    0.95,
                    float(a.get("compressor_polytropic_eff", 0.89)),
                    0.005,
                    format="%.3f",
                )
                dp = st.number_input(
                    "Combustor pressure loss (fraction)",
                    0.01,
                    0.08,
                    float(a.get("combustor_dp", 0.04)),
                    0.005,
                    format="%.3f",
                )
                etag = st.number_input(
                    "Generator efficiency", 0.94, 0.99, float(a.get("generator_efficiency", 0.98)), 0.005, format="%.3f"
                )
                etam = st.number_input(
                    "Mechanical efficiency",
                    0.97,
                    1.0,
                    float(a.get("mechanical_efficiency", 0.99)),
                    0.005,
                    format="%.3f",
                )
            with st.expander("Turbine cooling"):
                ngv = st.number_input(
                    "NGV cooling (of inlet flow)", 0.0, 0.15, float(cool.get("ngv", 0.07)), 0.005, format="%.3f"
                )
                rot = st.number_input("Rotor cooling", 0.0, 0.15, float(cool.get("rotor", 0.05)), 0.005, format="%.3f")
                ptc = st.number_input(
                    "Power turbine cooling", 0.0, 0.10, float(cool.get("pt", 0.03)), 0.005, format="%.3f"
                )
                port = st.number_input(
                    "PT cooling port (fraction of PR, log)", 0.2, 1.0, float(cool.get("port", 0.6)), 0.05, format="%.2f"
                )
            submitted = st.form_submit_button("Calculate", type="primary", width="stretch")
        with st.expander("Load or save a datasheet"):
            up = st.file_uploader("Datasheet (TOML)", type=["toml"])
            if up is not None and st.button("Use this datasheet"):
                try:
                    ss.datasheet = tomllib.loads(up.getvalue().decode())
                    ss.stale |= set(ss.sections) & USES_ENGINE
                    st.rerun()
                except (tomllib.TOMLDecodeError, KeyError) as e:
                    st.error(f"Could not read that file: {e}")
            st.download_button("Download the datasheet in use", _toml(ss.datasheet), "datasheet.toml")
    with right:
        if submitted:
            new = core.datasheet_with(
                d,
                name=name,
                electrical_power_MW=power,
                heat_rate_kJ_per_kWh=hr,
                exhaust_flow_kg_s=wex,
                exhaust_temperature_C=tex,
                pressure_ratio=pr,
                compressor_polytropic_eff=etac,
                combustor_dp=dp,
                generator_efficiency=etag,
                mechanical_efficiency=etam,
                ngv=ngv,
                rotor=rot,
                pt=ptc,
                port=port,
            )
            changed = new != ss.datasheet
            ss.datasheet = new
            try:
                with st.spinner("Calibrating…"):
                    store(core.engine_section(model()))
                if changed:
                    ss.stale |= (set(ss.sections) & USES_ENGINE) - {"engine"}
            except Exception as e:  # calibration can fail for impossible combinations
                st.error(f"The model could not match these values: {e}")
        if "engine" in ss.sections:
            show_section(ss.sections["engine"])
            next_button("Site performance")
        else:
            empty()


def _toml(d, prefix=""):
    lines, tables = [], []
    for k, v in d.items():
        if isinstance(v, dict):
            tables.append((k, v))
        else:
            lines.append(f"{k} = {json.dumps(v)}")
    out = "\n".join(lines) + "\n"
    for k, v in tables:
        name = f"{prefix}{k}"
        out += f"\n[{name}]\n" + _toml(v, name + ".")
    return out


def page_site():
    hero(
        "Site performance",
        "Predict output, heat rate and exhaust conditions at your site, see which effect costs how much, and "
        "read the correction curves with the control limit that applies at each ambient.",
    )
    left, right = layout()
    with left:
        with st.form("site"):
            with st.expander("Site", expanded=True):
                name = st.text_input("Site name", "My site")
                t = st.number_input("Ambient temperature (°C)", -40.0, 55.0, 30.0, 1.0)
                rh = st.number_input("Relative humidity (%)", 0, 100, 60, 5) / 100
                alt = st.number_input("Altitude (m)", 0.0, 4000.0, 0.0, 50.0)
                dpi = st.number_input("Inlet loss (mbar)", 0.0, 50.0, 10.0, 0.5)
                dpe = st.number_input("Exhaust loss (mbar)", 0.0, 80.0, 25.0, 0.5)
            with st.expander("Degradation"):
                wc = st.number_input("Compressor flow capacity (%)", -5.0, 2.0, 0.0, 0.1)
                ec = st.number_input("Compressor efficiency (points)", -5.0, 1.0, 0.0, 0.1)
                et = st.number_input("Turbine efficiency (points)", -5.0, 1.0, 0.0, 0.1)
            with st.expander("Correction curves"):
                lo = st.number_input("Lowest ambient (°C)", -40, 40, -20, 5)
                hi = st.number_input("Highest ambient (°C)", -20, 55, 45, 5)
                outs = st.multiselect(
                    "Curves to plot",
                    ["Output / ISO", "Heat rate / ISO", "Exhaust flow / ISO"],
                    default=["Output / ISO", "Heat rate / ISO", "Exhaust flow / ISO"],
                )
            go_ = st.form_submit_button("Calculate", type="primary", width="stretch")
    with right:
        if go_:
            site = Site(name, alt, t, rh, dpi, dpe)
            health = Health(
                compressor_flow_scale=1 + wc / 100, compressor_eff_delta=ec / 100, turbine_eff_delta=et / 100
            )
            with st.spinner("Running the site and the correction curves…"):
                store(core.site_section(model(), site, health, (min(lo, hi - 5), hi), outs or None))
        if "site" in ss.sections:
            show_section(ss.sections["site"])
            next_button("Part load & emissions")
        else:
            empty()


def page_partload():
    hero(
        "Part load and emissions",
        "Sweep the load range at any ambient and check NOx and CO against the limits, to find how far the "
        "engine can turn down and stay compliant.",
    )
    left, right = layout()
    with left:
        with st.form("pl"):
            with st.expander("Conditions", expanded=True):
                t = st.number_input("Ambient temperature (°C)", -30.0, 50.0, 15.0, 1.0)
                rh = st.number_input("Relative humidity (%)", 0, 100, 60, 5) / 100
            with st.expander("Load sweep", expanded=True):
                lo = st.selectbox("Lowest load (%)", [20, 30, 40, 50, 60], index=1)
                step = st.selectbox("Load step (%)", [5, 10, 20], index=1)
                staging = st.selectbox("DLE air staging", ["On", "Off"]) == "On"
            with st.expander("Limits"):
                nox = st.number_input(
                    "NOx limit (mg/Nm³ at 15 % O2)",
                    5.0,
                    200.0,
                    50.0,
                    5.0,
                    help="EU MCPD, new gas turbine on natural gas: 50, applied above 70 % load",
                )
                co = st.number_input("CO limit (mg/Nm³)", 10.0, 500.0, 100.0, 10.0)
            go_ = st.form_submit_button("Calculate", type="primary", width="stretch")
    with right:
        if go_:
            loads = [x / 100 for x in range(100, lo - 1, -step)]
            with st.spinner("Running the load sweep…"):
                store(core.part_load_section(model(), t, rh, loads, staging, nox, co))
        if "partload" in ss.sections:
            show_section(ss.sections["partload"])
            next_button("Pass-off test")
        else:
            empty()


def page_passoff():
    hero(
        "Pass-off test",
        "A synthetic production test: the engine's health and every instrument error are hidden, so the "
        "validated, corrected result can be checked against the truth.",
    )
    left, right = layout()
    with left:
        with st.form("po"):
            with st.expander("Test conditions", expanded=True):
                t = st.number_input("Test-day temperature (°C)", -10.0, 40.0, 8.0, 0.5)
                p = st.number_input("Barometric pressure (mbar)", 950.0, 1050.0, 1002.0, 0.5)
                rh = st.number_input("Relative humidity (%)", 10, 100, 78, 1) / 100
                n = st.selectbox("Test points", [1, 2, 3], index=1)
            with st.expander("Sensor faults to inject"):
                faults = st.multiselect(
                    "Faults",
                    list(core.FAULT_CHOICES),
                    default=list(core.FAULT_CHOICES)[:4],
                    label_visibility="collapsed",
                )
            with st.expander("Hidden engine health"):
                wc = st.number_input("Compressor flow change (%)", -3.0, 2.0, -0.6, 0.1)
                ec = st.number_input("Compressor efficiency change (points)", -3.0, 1.0, -0.4, 0.1)
                seed = st.number_input("Random seed", 0, 9999, 4)
            with st.expander("Guarantee"):
                pmin = st.number_input("Output at least (MW)", 0.0, 100.0, 12.65, 0.01)
                hmax = st.number_input("Heat rate at most (kJ/kWh)", 7000.0, 16000.0, 10500.0, 10.0)
                tmax = st.number_input("Exhaust temperature at most (°C)", 300.0, 700.0, 570.0, 1.0)
                mc = st.selectbox("Monte Carlo trials", [0, 200, 500, 2000], index=2)
            go_ = st.form_submit_button("Run the test", type="primary", width="stretch")
    with right:
        if go_:
            with st.spinner("Generating the 1 Hz log, validating, correcting and propagating uncertainty…"):
                store(core.passoff_section(model(), t, p, rh, n, faults, pmin, hmax, tmax, wc, ec, int(seed), 300, mc))
        if "passoff" in ss.sections:
            s = ss.sections["passoff"]
            show_section(s)
            next_button("Fleet monitoring")
        else:
            empty("Set the test conditions on the left and press Run the test.")


def page_fleet():
    hero(
        "Fleet monitoring",
        "Simulate a fleet in service with fouling, washes and injected faults, then see which faults the "
        "monitoring finds, how fast, and whether it names the right component.",
    )
    left, right = layout()
    with left:
        with st.form("fl"):
            with st.expander("Fleet", expanded=True):
                n = st.selectbox("Engines", list(range(2, 13)), index=2)
                days = st.selectbox("Days in service", [120, 240, 365, 540, 730], index=1)
                interval = st.selectbox("Days between snapshots", [1, 2, 3, 5], index=2)
                dispatch = st.selectbox(
                    "Dispatch",
                    ["base", "cycling"],
                    format_func=lambda x: {"base": "Base load", "cycling": "Cycling (part load)"}[x],
                )
                seed = st.number_input("Random seed", 0, 9999, 11)
            with st.expander("Injected faults"):
                st.caption("Edit, add or delete rows.")
                faults = st.data_editor(
                    core.default_fleet_faults(n, days),
                    num_rows="dynamic",
                    width="stretch",
                    column_config={
                        "Fault": st.column_config.SelectboxColumn(options=list(core.FLEET_FAULT_TYPES), required=True),
                        "Engine": st.column_config.NumberColumn(min_value=1, max_value=12, step=1),
                        "Day": st.column_config.NumberColumn(min_value=1, max_value=730, step=1),
                    },
                    key=f"faults_{n}_{days}",
                )
            go_ = st.form_submit_button("Simulate and monitor", type="primary", width="stretch")
        if "fleet_args" in ss:
            with st.expander("Engine to plot", expanded=True):
                sel = st.selectbox("Engine", list(range(1, ss.fleet_args[0] + 1)), key="fleet_engine")
                if st.button("Redraw", width="stretch"):
                    with st.spinner("Redrawing…"):
                        store(core.fleet_section(model(), *ss.fleet_args, engine_to_plot=sel))
    with right:
        if go_:
            f = faults.dropna()
            f = f[(f["Engine"] <= n) & (f["Day"] < days)]
            with st.spinner("Simulating the fleet and running the detectors (can take a minute)…"):
                store(core.fleet_section(model(), n, days, interval, dispatch, f.reset_index(drop=True), int(seed)))
                ss.fleet_args = (n, days, interval, dispatch, f.reset_index(drop=True), int(seed))
        if "fleet" in ss.sections:
            show_section(ss.sections["fleet"])
            next_button("Turbofan")
        else:
            empty("Set up the fleet on the left and press Simulate and monitor.")


def page_turbofan():
    hero(
        "Turbofan",
        "Size a two-spool turbofan at cruise, then see its thrust lapse with altitude and Mach number, its LTO "
        "NOx against the CAEP standards, and its fan and jet noise sources.",
    )
    left, right = layout()
    with left:
        with st.form("tf"):
            with st.expander("Cycle", expanded=True):
                bpr = st.number_input("Bypass ratio", 2.0, 16.0, 10.0, 0.5)
                fpr = st.number_input("Fan pressure ratio (bypass)", 1.2, 2.2, 1.5, 0.05)
                fcpr = st.number_input("Fan root + booster pressure ratio", 1.2, 4.0, 2.4, 0.1)
                hpc = st.number_input("HPC pressure ratio", 5.0, 25.0, 17.0, 0.5)
                tet = st.number_input("Cruise TET (K)", 1200.0, 1900.0, 1600.0, 10.0)
            with st.expander("Cruise point", expanded=True):
                thr = st.number_input("Cruise thrust (kN)", 5.0, 80.0, 22.0, 0.5)
                alt = st.number_input("Cruise altitude (m)", 0.0, 13000.0, 10668.0, 100.0)
                mach = st.number_input("Cruise Mach number", 0.0, 0.9, 0.78, 0.01)
            with st.expander("Off-design and environment"):
                to_tet = st.number_input("Take-off TET (K)", 1300.0, 2000.0, 1750.0, 10.0)
                off = st.selectbox("Thrust lapse", ["Calculate", "Skip"]) == "Calculate"
                env = st.selectbox("LTO NOx and noise", ["Calculate", "Skip"]) == "Calculate"
            go_ = st.form_submit_button("Calculate", type="primary", width="stretch")
    with right:
        if go_:
            try:
                with st.spinner("Sizing the engine and running off-design…"):
                    store(core.turbofan_section(bpr, fpr, fcpr, hpc, tet, thr, alt, mach, to_tet, off, off and env))
            except Exception as e:  # off-design can fail to converge far from the design point
                st.error(f"The model did not converge for these values: {e}")
        if "turbofan" in ss.sections:
            show_section(ss.sections["turbofan"])
            next_button("Reports & downloads")
        else:
            empty()


def page_reports():
    hero(
        "Reports and downloads",
        "Bundle any of the studies you have calculated into one set of files: vector charts, an Excel "
        "workbook, a PowerPoint deck and a PDF report.",
    )
    if not ss.sections:
        empty("Nothing calculated yet. Run a study, or use Run every study on the Overview.")
        return
    order = [k for k in ("engine", "site", "partload", "passoff", "fleet", "turbofan") if k in ss.sections]
    titles = {k: ss.sections[k].title for k in order}
    left, right = layout()
    with left:
        with st.expander("Contents", expanded=True):
            chosen = st.multiselect("Studies to include", order, default=order, format_func=titles.get)
            title = st.text_input("Report title", "Gas turbine performance results")
    with right:
        stale = [titles[k] for k in chosen if k in ss.stale]
        if stale:
            st.markdown(
                f'<p class="stale">Calculated before the engine changed: {html.escape(", ".join(stale))}</p>',
                unsafe_allow_html=True,
            )
        if chosen:
            st.markdown("## Files")
            download_buttons([ss.sections[k] for k in chosen], "gtperf_" + "_".join(chosen), title)
            st.markdown("## What each file holds")
            st.markdown(
                "- **Charts (SVG)**: every chart as a vector file, one folder per study.\n"
                "- **Workbook (XLSX)**: a summary sheet of inputs and key results, then one sheet per table.\n"
                "- **Slides (PPTX)**: a cover, a key-results slide per study, a slide per chart and the main table.\n"
                "- **Report (PDF)**: inputs, key results, notes, charts and tables for each study.\n"
                "- **Tables (CSV)**: every table as a CSV file."
            )


{
    "Overview": page_overview,
    "Engine": page_engine,
    "Site performance": page_site,
    "Part load & emissions": page_partload,
    "Pass-off test": page_passoff,
    "Fleet monitoring": page_fleet,
    "Turbofan": page_turbofan,
    "Reports & downloads": page_reports,
}[ss.page]()
