"""GTPerf Streamlit app (beta). Run with `gtperf gui` or `streamlit run src/gtperf/app/ui.py`.

The engine set up on the first page feeds every other section, so changing it marks their results out of date.
Each section has its own inputs, filters for what it shows, and downloads; the Reports page bundles any
combination of sections into SVG, Excel, PowerPoint, PDF and CSV files.
"""

from __future__ import annotations

import json
import sys
import tomllib

import pandas as pd
import streamlit as st

from gtperf.app import core, exports
from gtperf.maps import Health
from gtperf.site import Site

LOGO = str(exports.LOGO)
PAGES = [
    "Home",
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
USES_ENGINE = {"engine", "site", "partload", "passoff", "fleet"}

st.set_page_config(page_title="GTPerf (beta)", page_icon=LOGO, layout="wide")
st.markdown(
    f"""<style>
h1, h2, h3 {{ color: {core.NAVY}; }}
div[data-testid="stMetricValue"] {{ font-size: 1.25rem; }}
.beta {{ display:inline-block; background:{core.AMBER}; color:white; font-weight:600; font-size:0.75rem;
         padding:2px 8px; border-radius:10px; letter-spacing:0.05em; }}
.stale {{ color:{core.AMBER}; font-weight:600; }}
</style>""",
    unsafe_allow_html=True,
)

ss = st.session_state
ss.setdefault("page", "Home")
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


def store(section):
    ss.sections[section.key] = section
    ss.stale.discard(section.key)


# --- sidebar -------------------------------------------------------------------------------------------

with st.sidebar:
    st.image(LOGO, width="stretch")
    st.markdown('<span class="beta">BETA · IN PROGRESS</span>', unsafe_allow_html=True)
    st.caption(f"Engine: **{ss.datasheet.get('name', 'custom')}**")
    labels = []
    for p in PAGES:
        key = SECTION_OF.get(p)
        mark = ""
        if key in ss.sections:
            mark = " ⚠" if key in ss.stale else " ✓"
        labels.append(p + mark)
    choice = st.radio("Sections", labels, index=PAGES.index(ss.page), label_visibility="collapsed")
    ss.page = PAGES[labels.index(choice)]
    st.divider()
    st.caption("✓ calculated · ⚠ engine changed since, recalculate")
    st.caption("All results are model estimates from public data and synthetic tests.")
    if sys.platform == "emscripten":  # running in the browser from the portfolio site
        st.caption("Running in your browser: the longer studies take a little more time than on a PC.")
        st.markdown("[← Back to the project page](../#p/gtperf)")


# --- shared result display -----------------------------------------------------------------------------


def show_section(section, chart_filter=True):
    if section.key in ss.stale:
        st.markdown(
            '<p class="stale">The engine has changed since these results were calculated. '
            "Press Calculate again to update them.</p>",
            unsafe_allow_html=True,
        )
    cols = st.columns(min(4, max(1, len(section.key_results))))
    for i, (k, v) in enumerate(section.key_results):
        cols[i % len(cols)].metric(k, v)
    for n in section.notes:
        st.info(n)
    tab_charts, tab_tables, tab_inputs, tab_dl = st.tabs(["Charts", "Tables", "Inputs", "Download this section"])
    with tab_charts:
        names = list(section.figures)
        shown = (
            st.multiselect("Charts to show", names, default=names, key=f"figsel_{section.key}")
            if (chart_filter and len(names) > 1)
            else names
        )
        for name in shown:
            fig = section.figures[name]
            st.pyplot(fig, width="content" if fig.get_size_inches()[0] < 8 else "stretch")
            st.download_button(
                f"Download “{name}” (SVG)",
                exports.figure_svg(fig),
                file_name=f"{exports._slug(section.title)}_{exports._slug(name)}.svg",
                mime="image/svg+xml",
                key=f"svg_{section.key}_{name}",
            )
        if not names:
            st.caption("This section has no charts with the current options.")
    with tab_tables:
        if section.tables:
            name = st.selectbox("Table", list(section.tables), key=f"tabsel_{section.key}")
            df = section.tables[name]
            c1, c2 = st.columns([3, 1])
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
    with tab_inputs:
        st.dataframe(
            pd.DataFrame({"Input": list(section.inputs), "Value": [exports._fmt(v) for v in section.inputs.values()]}),
            width="stretch",
            hide_index=True,
        )
    with tab_dl:
        download_buttons([section], exports._slug(section.title), section.title)


def download_buttons(sections, stem, title):
    c = st.columns(5)
    if c[0].button("Prepare files", key=f"prep_{stem}", type="primary"):
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
        c[1].download_button(
            "Charts (SVG, zip)", files["svg"], f"{stem}_charts.zip", "application/zip", key=f"d1_{stem}"
        )
        c[2].download_button(
            "Workbook (XLSX)",
            files["xlsx"],
            f"{stem}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"d2_{stem}",
        )
        c[3].download_button(
            "Slides (PPTX)",
            files["pptx"],
            f"{stem}.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            key=f"d3_{stem}",
        )
        c[4].download_button("Report (PDF)", files["pdf"], f"{stem}.pdf", "application/pdf", key=f"d4_{stem}")
        st.download_button(
            "All tables (CSV, zip)", files["csv"], f"{stem}_tables.zip", "application/zip", key=f"d5_{stem}"
        )
    else:
        st.caption("Builds the charts as SVG, the tables as an Excel workbook, a PowerPoint deck and a PDF report.")


def next_button(page):
    st.button(f"Next: {page} →", on_click=go, args=(page,), key=f"next_{page}")


# --- pages ---------------------------------------------------------------------------------------------


def page_home():
    c1, c2 = st.columns([1, 2])
    c1.image(LOGO, width="stretch")
    c2.markdown("## Gas turbine performance, from datasheet to fleet")
    c2.markdown('<span class="beta">BETA · IN PROGRESS</span>', unsafe_allow_html=True)
    c2.write(
        "Enter values, press Calculate, and read or download the results. Every section uses the engine "
        "model you set up on the Engine page, so the studies stay consistent with each other."
    )
    st.markdown("#### How the sections link")
    st.graphviz_chart(f"""digraph {{ rankdir=LR; node [shape=box style="rounded,filled" fillcolor="#eef2f8"
        color="{core.NAVY}" fontname="Helvetica" fontsize=11];
        D [label="Datasheet\\n(edit or upload)"]; E [label="Engine model\\n(calibrated)"];
        S [label="Site performance"]; P [label="Part load\\n& emissions"]; T [label="Pass-off test"];
        F [label="Fleet monitoring"]; A [label="Turbofan\\n(separate aero model)"];
        R [label="Reports\\nSVG · XLSX · PPTX · PDF" fillcolor="{core.NAVY}" fontcolor=white];
        D -> E; E -> S; E -> P; E -> T; E -> F; S -> R; P -> R; T -> R; F -> R; A -> R; E -> R; }}""")
    st.markdown("#### Sections")
    rows = [
        ("Engine", "Calibrate a twin-shaft model to a datasheet; see the gas path at ISO."),
        ("Site performance", "Output, heat rate and exhaust at any site, with correction factors and curves."),
        ("Part load & emissions", "Performance against load, DLE NOx and CO, emissions-compliant turndown."),
        ("Pass-off test", "A synthetic test with sensor faults, validated and corrected, with GUM uncertainty."),
        ("Fleet monitoring", "A synthetic fleet with fouling, washes and faults; detection and diagnosis."),
        ("Turbofan", "Two-spool turbofan design point, thrust lapse, LTO NOx and noise sources."),
        ("Reports & downloads", "Bundle any sections into SVG, Excel, PowerPoint, PDF and CSV."),
    ]
    for name, text in rows:
        a, b = st.columns([1, 4])
        a.button(name, on_click=go, args=(name,), key=f"home_{name}", width="stretch")
        b.write(text)
    st.divider()
    if st.button("Run every section with default inputs", type="primary"):
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
        st.success("All sections calculated. Open any section, or Reports & downloads.")


def page_engine():
    st.header("Engine and calibration")
    d = ss.datasheet
    a = d.setdefault("assumptions", {})
    cool = a.setdefault("cooling", {})
    up = st.file_uploader("Load a datasheet (TOML, same format as examples/sgt400_13mw.toml)", type=["toml"])
    if up is not None and st.button("Use this datasheet"):
        try:
            ss.datasheet = tomllib.loads(up.getvalue().decode())
            ss.stale |= set(ss.sections) & USES_ENGINE
            st.rerun()
        except (tomllib.TOMLDecodeError, KeyError) as e:
            st.error(f"Could not read that file: {e}")
    with st.form("engine"):
        st.markdown("**Published rating (ISO)**")
        c = st.columns(3)
        name = c[0].text_input("Engine name", d.get("name", "engine"))
        power = c[1].number_input("Electrical output (MW)", 1.0, 100.0, float(d["electrical_power_MW"]), 0.1)
        hr = c[2].number_input("Heat rate (kJ/kWh)", 7000.0, 16000.0, float(d["heat_rate_kJ_per_kWh"]), 10.0)
        c = st.columns(3)
        wex = c[0].number_input("Exhaust flow (kg/s)", 2.0, 300.0, float(d["exhaust_flow_kg_s"]), 0.1)
        tex = c[1].number_input(
            "Exhaust temperature, check only (°C)", 300.0, 700.0, float(d.get("exhaust_temperature_C") or 555.0), 1.0
        )
        pr = c[2].number_input("Pressure ratio", 5.0, 40.0, float(d["pressure_ratio"]), 0.1)
        st.markdown("**Modelling assumptions**")
        c = st.columns(4)
        etac = c[0].number_input(
            "Compressor polytropic efficiency",
            0.80,
            0.95,
            float(a.get("compressor_polytropic_eff", 0.89)),
            0.005,
            format="%.3f",
        )
        dp = c[1].number_input(
            "Combustor pressure loss (fraction)", 0.01, 0.08, float(a.get("combustor_dp", 0.04)), 0.005, format="%.3f"
        )
        etag = c[2].number_input(
            "Generator efficiency", 0.94, 0.99, float(a.get("generator_efficiency", 0.98)), 0.005, format="%.3f"
        )
        etam = c[3].number_input(
            "Mechanical efficiency", 0.97, 1.0, float(a.get("mechanical_efficiency", 0.99)), 0.005, format="%.3f"
        )
        c = st.columns(4)
        ngv = c[0].number_input(
            "NGV cooling (of inlet flow)", 0.0, 0.15, float(cool.get("ngv", 0.07)), 0.005, format="%.3f"
        )
        rot = c[1].number_input("Rotor cooling", 0.0, 0.15, float(cool.get("rotor", 0.05)), 0.005, format="%.3f")
        ptc = c[2].number_input("Power turbine cooling", 0.0, 0.10, float(cool.get("pt", 0.03)), 0.005, format="%.3f")
        port = c[3].number_input(
            "PT cooling bleed port (fraction of PR, log)", 0.2, 1.0, float(cool.get("port", 0.6)), 0.05, format="%.2f"
        )
        submitted = st.form_submit_button("Calculate", type="primary")
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
    st.download_button("Download the datasheet in use (TOML)", _toml(ss.datasheet), "datasheet.toml")


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
    st.header("Site performance")
    with st.form("site"):
        c = st.columns(4)
        name = c[0].text_input("Site name", "My site")
        t = c[1].number_input("Ambient temperature (°C)", -40.0, 55.0, 30.0, 1.0)
        rh = c[2].slider("Relative humidity (%)", 0, 100, 60) / 100
        alt = c[3].number_input("Altitude (m)", 0.0, 4000.0, 0.0, 50.0)
        c = st.columns(4)
        dpi = c[0].number_input("Inlet loss (mbar)", 0.0, 50.0, 10.0, 0.5)
        dpe = c[1].number_input("Exhaust loss (mbar)", 0.0, 80.0, 25.0, 0.5)
        st.markdown("**Degradation (optional)**")
        c = st.columns(3)
        wc = c[0].number_input("Compressor flow capacity (%)", -5.0, 2.0, 0.0, 0.1)
        ec = c[1].number_input("Compressor efficiency (points)", -5.0, 1.0, 0.0, 0.1)
        et = c[2].number_input("Turbine efficiency (points)", -5.0, 1.0, 0.0, 0.1)
        st.markdown("**Correction curve options**")
        c = st.columns(2)
        rng = c[0].slider("Ambient range for curves (°C)", -40, 55, (-20, 45))
        outs = c[1].multiselect(
            "Curves to plot",
            ["Output / ISO", "Heat rate / ISO", "Exhaust flow / ISO"],
            default=["Output / ISO", "Heat rate / ISO", "Exhaust flow / ISO"],
        )
        go_ = st.form_submit_button("Calculate", type="primary")
    if go_:
        site = Site(name, alt, t, rh, dpi, dpe)
        health = Health(compressor_flow_scale=1 + wc / 100, compressor_eff_delta=ec / 100, turbine_eff_delta=et / 100)
        with st.spinner("Running the site and the correction curves…"):
            store(core.site_section(model(), site, health, rng, outs or None))
    if "site" in ss.sections:
        show_section(ss.sections["site"])
        next_button("Part load & emissions")


def page_partload():
    st.header("Part load and emissions")
    with st.form("pl"):
        c = st.columns(4)
        t = c[0].number_input("Ambient temperature (°C)", -30.0, 50.0, 15.0, 1.0)
        rh = c[1].slider("Relative humidity (%)", 0, 100, 60) / 100
        lo, hi = c[2].slider("Load range (%)", 20, 100, (30, 100), 5)
        step = c[3].select_slider("Load step (%)", [5, 10, 20], 10)
        c = st.columns(3)
        staging = c[0].toggle("DLE air staging", True)
        nox = c[1].number_input(
            "NOx limit (mg/Nm³ at 15 % O2)",
            5.0,
            200.0,
            50.0,
            5.0,
            help="EU MCPD, new gas turbine on natural gas: 50, applied above 70 % load",
        )
        co = c[2].number_input("CO limit (mg/Nm³)", 10.0, 500.0, 100.0, 10.0)
        go_ = st.form_submit_button("Calculate", type="primary")
    if go_:
        loads = [x / 100 for x in range(hi, lo - 1, -step)]
        with st.spinner("Running the load sweep…"):
            store(core.part_load_section(model(), t, rh, loads, staging, nox, co))
    if "partload" in ss.sections:
        show_section(ss.sections["partload"])
        next_button("Pass-off test")


def page_passoff():
    st.header("Pass-off test")
    st.caption(
        "A synthetic production test: the engine's health and every instrument error are hidden, so the "
        "result can be checked against the truth."
    )
    with st.form("po"):
        c = st.columns(4)
        t = c[0].number_input("Test-day temperature (°C)", -10.0, 40.0, 8.0, 0.5)
        p = c[1].number_input("Barometric pressure (mbar)", 950.0, 1050.0, 1002.0, 0.5)
        rh = c[2].slider("Relative humidity (%)", 10, 100, 78) / 100
        n = c[3].select_slider("Test points", [1, 2, 3], 2)
        faults = st.multiselect("Inject sensor faults", list(core.FAULT_CHOICES), default=list(core.FAULT_CHOICES)[:4])
        c = st.columns(3)
        wc = c[0].number_input("Hidden compressor flow change (%)", -3.0, 2.0, -0.6, 0.1)
        ec = c[1].number_input("Hidden compressor efficiency change (points)", -3.0, 1.0, -0.4, 0.1)
        seed = c[2].number_input("Random seed", 0, 9999, 4)
        st.markdown("**Guarantee**")
        c = st.columns(4)
        pmin = c[0].number_input("Output at least (MW)", 0.0, 100.0, 12.65, 0.01)
        hmax = c[1].number_input("Heat rate at most (kJ/kWh)", 7000.0, 16000.0, 10500.0, 10.0)
        tmax = c[2].number_input("Exhaust temperature at most (°C)", 300.0, 700.0, 570.0, 1.0)
        mc = c[3].select_slider("Monte Carlo trials", [0, 200, 500, 2000], 500)
        go_ = st.form_submit_button("Run the test", type="primary")
    if go_:
        with st.spinner("Generating the 1 Hz log, validating, correcting and propagating uncertainty…"):
            store(core.passoff_section(model(), t, p, rh, n, faults, pmin, hmax, tmax, wc, ec, int(seed), 300, mc))
    if "passoff" in ss.sections:
        s = ss.sections["passoff"]
        verdict = dict(s.key_results).get("Verdict")
        (st.success if verdict == "PASS" else st.error)(f"Verdict: {verdict}")
        show_section(s)
        next_button("Fleet monitoring")


def page_fleet():
    st.header("Fleet monitoring")
    with st.form("fl"):
        c = st.columns(4)
        n = c[0].slider("Engines", 2, 12, 4)
        days = c[1].select_slider("Days in service", [120, 240, 365, 540, 730], 240)
        interval = c[2].select_slider("Days between snapshots", [1, 2, 3, 5], 3)
        dispatch = c[3].radio(
            "Dispatch",
            ["base", "cycling"],
            format_func=lambda x: {"base": "Base load", "cycling": "Cycling (part load)"}[x],
        )
        st.markdown("**Injected faults** (edit, add or delete rows)")
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
        seed = st.number_input("Random seed", 0, 9999, 11)
        go_ = st.form_submit_button("Simulate and monitor", type="primary")
    if go_:
        f = faults.dropna()
        f = f[(f["Engine"] <= n) & (f["Day"] < days)]
        with st.spinner("Simulating the fleet and running the detectors (can take a minute)…"):
            store(core.fleet_section(model(), n, days, interval, dispatch, f.reset_index(drop=True), int(seed)))
            ss.fleet_args = (n, days, interval, dispatch, f.reset_index(drop=True), int(seed))
    if "fleet" in ss.sections:
        if "fleet_args" in ss:
            n_e = ss.fleet_args[0]
            sel = st.selectbox("Engine to plot", list(range(1, n_e + 1)), key="fleet_engine")
            if st.button("Redraw for this engine"):
                with st.spinner("Redrawing…"):
                    store(core.fleet_section(model(), *ss.fleet_args, engine_to_plot=sel))
        show_section(ss.sections["fleet"])
        next_button("Turbofan")


def page_turbofan():
    st.header("Turbofan")
    with st.form("tf"):
        c = st.columns(4)
        bpr = c[0].number_input("Bypass ratio", 2.0, 16.0, 10.0, 0.5)
        fpr = c[1].number_input("Fan pressure ratio (bypass)", 1.2, 2.2, 1.5, 0.05)
        fcpr = c[2].number_input("Fan root + booster pressure ratio", 1.2, 4.0, 2.4, 0.1)
        hpc = c[3].number_input("HPC pressure ratio", 5.0, 25.0, 17.0, 0.5)
        c = st.columns(4)
        tet = c[0].number_input("Cruise TET (K)", 1200.0, 1900.0, 1600.0, 10.0)
        thr = c[1].number_input("Cruise thrust (kN)", 5.0, 80.0, 22.0, 0.5)
        alt = c[2].number_input("Cruise altitude (m)", 0.0, 13000.0, 10668.0, 100.0)
        mach = c[3].number_input("Cruise Mach number", 0.0, 0.9, 0.78, 0.01)
        c = st.columns(3)
        to_tet = c[0].number_input("Take-off TET (K)", 1300.0, 2000.0, 1750.0, 10.0)
        off = c[1].toggle("Off-design: thrust lapse", True)
        env = c[2].toggle("LTO NOx and noise", True)
        go_ = st.form_submit_button("Calculate", type="primary")
    if go_:
        try:
            with st.spinner("Sizing the engine and running off-design…"):
                store(core.turbofan_section(bpr, fpr, fcpr, hpc, tet, thr, alt, mach, to_tet, off, off and env))
        except Exception as e:  # off-design can fail to converge far from the design point
            st.error(f"The model did not converge for these values: {e}")
    if "turbofan" in ss.sections:
        show_section(ss.sections["turbofan"])
        next_button("Reports & downloads")


def page_reports():
    st.header("Reports and downloads")
    if not ss.sections:
        st.info("Nothing calculated yet. Run a section, or use “Run every section” on the Home page.")
        return
    order = [k for k in ("engine", "site", "partload", "passoff", "fleet", "turbofan") if k in ss.sections]
    titles = {k: ss.sections[k].title for k in order}
    chosen = st.multiselect("Sections to include", order, default=order, format_func=titles.get)
    title = st.text_input("Report title", "Gas turbine performance results")
    stale = [titles[k] for k in chosen if k in ss.stale]
    if stale:
        st.warning("Calculated before the engine changed: " + ", ".join(stale))
    if chosen:
        download_buttons([ss.sections[k] for k in chosen], "gtperf_" + "_".join(chosen), title)
        st.markdown("#### What each file holds")
        st.markdown(
            "- **Charts (SVG)**: every chart as a vector file, one folder per section.\n"
            "- **Workbook (XLSX)**: a summary sheet of inputs and key results, then one sheet per table.\n"
            "- **Slides (PPTX)**: a cover, a key-results slide per section, a slide per chart and the main table.\n"
            "- **Report (PDF)**: inputs, key results, notes, charts and tables for each section.\n"
            "- **Tables (CSV)**: every table as a CSV file."
        )


{
    "Home": page_home,
    "Engine": page_engine,
    "Site performance": page_site,
    "Part load & emissions": page_partload,
    "Pass-off test": page_passoff,
    "Fleet monitoring": page_fleet,
    "Turbofan": page_turbofan,
    "Reports & downloads": page_reports,
}[ss.page]()
