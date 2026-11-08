"""Circwood WP4 — interactive wood- and carbon-flow explorer (Streamlit).

Run locally:
    streamlit run app/streamlit_app.py

The internal exploration tool (the public deployment is the static site in
``site/``). Loads the cached processed dataset, so it needs no live SSB access.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Make the `src` layout importable when run via `streamlit run` (no install).
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from circwood import build_sankey, config, constants as C  # noqa: E402
from circwood.carbon import add_carbon, carbon_sankey, species_mix  # noqa: E402
from circwood.reconcile import treindustrien_reconciliation  # noqa: E402
from circwood.transform.assemble import (  # noqa: E402
    effective_coefficients,
    regenerate_balanced,
)
from circwood.transform.sawing import SawmillCoefficients  # noqa: E402
from circwood.validate import balance_report  # noqa: E402
from circwood.viz.sankey import load_node_params, make_sankey  # noqa: E402

st.set_page_config(
    page_title="Circwood · Norwegian Wood & Carbon Flows",
    page_icon="🌲",
    layout="wide",
)

# --------------------------------------------------------------------------- #
# Look and feel
# --------------------------------------------------------------------------- #

#: Design tokens. Ink and surfaces match .streamlit/config.toml.
INK = "#1d2a22"
INK_MUTED = "#5d6b62"
SURFACE = "#fbfaf6"
SPRUCE = "#1f5c3f"

#: Carbon-fate colours, validated with the dataviz palette checker (light mode:
#: lightness band, chroma floor, CVD and normal-vision separation, contrast all
#: pass). "Other" is a neutral by convention: it is a remainder, not an identity.
FATE_COLORS = {
    "Long-term use (β)": "#1f7a4d",
    "Pulp & paper": "#2f6db5",
    "Energy": "#c4502b",
    "Other": "#9aa19c",
}

FONT_STACK = "Inter, 'Segoe UI', system-ui, sans-serif"

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,600;8..60,700&display=swap');

html, body, [class*="css"], .stMarkdown, .stText, .stCaption {
    font-family: Inter, 'Segoe UI', system-ui, sans-serif;
}
h1, h2, h3 { font-family: 'Source Serif 4', Georgia, serif !important; letter-spacing: -0.01em; }
.block-container { padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1400px; }

/* Hero band: a deep spruce canopy with a faint contour-line texture. */
.cw-hero {
    position: relative; overflow: hidden;
    background:
        radial-gradient(circle at 85% -20%, rgba(255,255,255,0.10) 0 28%, transparent 29%),
        radial-gradient(circle at 85% -20%, rgba(255,255,255,0.06) 0 40%, transparent 41%),
        radial-gradient(circle at 85% -20%, rgba(255,255,255,0.04) 0 52%, transparent 53%),
        linear-gradient(120deg, #133d2a 0%, #1f5c3f 55%, #2e7350 100%);
    color: #f3f6f1; border-radius: 14px; padding: 1.6rem 2rem 1.5rem; margin-bottom: 1.2rem;
}
.cw-hero .eyebrow {
    text-transform: uppercase; letter-spacing: 0.14em; font-size: 0.72rem;
    font-weight: 600; color: #b9d8c3; margin-bottom: 0.35rem;
}
.cw-hero h1 {
    color: #ffffff !important; font-size: 2.05rem !important; line-height: 1.15;
    margin: 0 0 0.45rem 0 !important; padding: 0 !important;
}
.cw-hero p { color: #d9e7dc; font-size: 0.98rem; max-width: 62rem; margin: 0; }
.cw-pill {
    display: inline-block; margin-top: 0.9rem; padding: 0.22rem 0.7rem;
    border-radius: 999px; background: rgba(255,255,255,0.12);
    border: 1px solid rgba(255,255,255,0.22); font-size: 0.78rem; color: #eef4ef;
}

/* KPI tiles. */
.cw-kpi {
    background: #ffffff; border: 1px solid #e0e5dc; border-radius: 12px;
    padding: 0.95rem 1.1rem 0.9rem; height: 100%;
    border-top: 3px solid var(--accent, #1f5c3f);
}
.cw-kpi .label {
    font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.08em;
    color: #5d6b62; font-weight: 600;
}
.cw-kpi .value {
    font-family: 'Source Serif 4', Georgia, serif; font-size: 1.85rem;
    font-weight: 700; color: #1d2a22; line-height: 1.2; margin-top: 0.25rem;
}
.cw-kpi .unit { font-size: 0.95rem; font-weight: 600; color: #5d6b62; margin-left: 0.2rem; }
.cw-kpi .note { font-size: 0.78rem; color: #5d6b62; margin-top: 0.2rem; }

/* Sidebar. */
section[data-testid="stSidebar"] { border-right: 1px solid #dfe5da; }
.cw-brand { display: flex; align-items: center; gap: 0.6rem; margin: 0.2rem 0 1rem; }
.cw-brand .mark {
    width: 34px; height: 34px; border-radius: 9px; background: #1f5c3f;
    display: flex; align-items: center; justify-content: center;
}
.cw-brand .name { font-family: 'Source Serif 4', Georgia, serif; font-weight: 700; font-size: 1.15rem; color: #1d2a22; }
.cw-brand .sub { font-size: 0.72rem; color: #5d6b62; letter-spacing: 0.04em; }
.cw-section {
    font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.12em;
    font-weight: 600; color: #5d6b62; margin: 1.1rem 0 0.35rem;
}

/* Tabs. */
.stTabs [data-baseweb="tab-list"] { gap: 0.25rem; border-bottom: 1px solid #dfe5da; }
.stTabs [data-baseweb="tab"] { font-weight: 500; padding: 0.55rem 1rem; }

/* Footer. */
.cw-footer {
    margin-top: 2.2rem; padding-top: 1rem; border-top: 1px solid #dfe5da;
    font-size: 0.8rem; color: #5d6b62;
}
.cw-footer b { color: #1d2a22; }
</style>
"""

#: A small spruce glyph for the sidebar brand mark (inline SVG, no asset files).
TREE_SVG = (
    '<svg width="20" height="22" viewBox="0 0 20 22" aria-hidden="true">'
    '<path d="M10 1 L15 8 H12.5 L17 14 H13.5 L18.5 19 H1.5 L6.5 14 H3 L7.5 8 H5 Z" '
    'fill="#e6f1e8"/><rect x="8.8" y="18.5" width="2.4" height="3" fill="#e6f1e8"/></svg>'
)

# (value_scale, value_unit) per metric.
METRICS = {
    "Volume (Mm³)": (1_000_000.0, "Mm³"),
    "Carbon (Mt C)": (1_000_000_000.0, "Mt C"),
}

POOLS = ("Spruce", "Pine", "Non-coniferous")
HARVEST = "Harvested wood"
LTUSE = "Long-term USE"


# A coefficient choice is identified by (source, shrinkage, sawdust, cellulose,
# scraps, planed_input_factor, byproduct_planing) — all primitives, so they make
# safe, hashable Streamlit cache keys. The dataclass is rebuilt inside.
_DEFAULTS = SawmillCoefficients()
_DEFAULT_ARGS = (
    "manual", _DEFAULTS.shrinkage, _DEFAULTS.sawdust, _DEFAULTS.cellulose,
    _DEFAULTS.scraps, _DEFAULTS.planed_input_factor, _DEFAULTS.byproduct_planing,
)


# --------------------------------------------------------------------------- #
# Data (cached)
# --------------------------------------------------------------------------- #

@st.cache_data(show_spinner="Rebuilding flows…")
def _load_data(source, shrinkage, sawdust, cellulose, scraps,
               planed_input_factor, byproduct_planing):
    """Sankey dataset for the chosen sawmill/planing coefficients.

    The default-manual case loads the shipped cached dataset as-is; any other
    choice rebuilds the flows from the cached primary rows via
    ``regenerate_balanced`` (the published Sawmills imbalance noted in the mass-
    balance panel applies to rebuilt datasets).
    """
    args = (source, shrinkage, sawdust, cellulose, scraps,
            planed_input_factor, byproduct_planing)
    if args == _DEFAULT_ARGS:
        return build_sankey(use_cached_ssb=True)
    base = SawmillCoefficients(
        shrinkage, sawdust, cellulose, scraps,
        planed_input_factor, byproduct_planing,
    )
    return regenerate_balanced(coefficient_source=source, coefficients=base)


@st.cache_data(show_spinner=False)
def _carbon_flows(*args):
    """Carbon-valued Sankey frame (volume column carries kg C)."""
    return carbon_sankey(_load_data(*args))


@st.cache_data(show_spinner=False)
def _carbon_table(*args):
    """Full frame with an added carbon column (for the data panel)."""
    return add_carbon(_load_data(*args))


@st.cache_data(show_spinner=False)
def _species_mix(*args):
    return species_mix(_load_data(*args))


@st.cache_data(show_spinner=False)
def _effective_coef(source, shrinkage, sawdust, cellulose, scraps,
                    planed_input_factor, byproduct_planing):
    """The coefficients a rebuild would actually use (for display)."""
    base = SawmillCoefficients(
        shrinkage, sawdust, cellulose, scraps,
        planed_input_factor, byproduct_planing,
    )
    return effective_coefficients(source, base)


@st.cache_data(show_spinner=False)
def _all_node_labels():
    """All node labels defined in config/nodes.json, in file order."""
    labels, _x, _y, _c = load_node_params(nodes_to_drop=())
    return labels


@st.cache_data(show_spinner=False)
def _beta_series() -> pd.DataFrame | None:
    """The committed Phase F β export, or None if it has not been built."""
    path = config.BETA_SERIES_CSV
    if not path.exists():
        return None
    return pd.read_csv(path, comment="#")


def _vintage_text() -> str:
    """"Data as of ..." from the manifest, or an honest admission of ignorance.

    A dashboard that does not state its vintage invites the reader to assume it
    is current. When no manifest exists (a checkout that has never run a fresh
    build) say so rather than showing nothing.
    """
    from circwood.manifest import read_manifest

    manifest = read_manifest()
    if manifest is None:
        return "Data vintage unknown — run circwood-refresh to record one"
    return manifest.describe()


# --------------------------------------------------------------------------- #
# Presentation helpers
# --------------------------------------------------------------------------- #

def _hero(vintage: str) -> None:
    st.markdown(
        f"""
        <div class="cw-hero">
          <div class="eyebrow">Circwood WP4 · Norwegian Institute of Wood Technology</div>
          <h1>Norwegian Wood &amp; Carbon Flows</h1>
          <p>How wood harvested in Norwegian forests moves through sawmills, pulp
          and board mills to long-lived products, paper, energy and export, in
          cubic metres and in tonnes of carbon.</p>
          <span class="cw-pill">{vintage}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _kpi(col, label: str, value: str, unit: str, note: str, accent: str) -> None:
    col.markdown(
        f"""
        <div class="cw-kpi" style="--accent:{accent}">
          <div class="label">{label}</div>
          <div class="value">{value}<span class="unit">{unit}</span></div>
          <div class="note">{note}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _section(text: str) -> None:
    st.markdown(f'<div class="cw-section">{text}</div>', unsafe_allow_html=True)


def _window_label(min_year: int, max_year: int) -> str:
    return str(min_year) if min_year == max_year else f"{min_year}–{max_year}"


def _style_sankey(fig: go.Figure) -> go.Figure:
    """Restyle the shared figure for the app without touching viz/sankey.py."""
    fig.update_layout(
        font=dict(family=FONT_STACK, size=13, color=INK),
        title=dict(font=dict(family="'Source Serif 4', Georgia, serif", size=18, color=INK)),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        margin=dict(l=10, r=10, t=60, b=10),
    )
    fig.update_traces(textfont=dict(family=FONT_STACK, size=12, color=INK))
    return fig


def _fate_chart(beta_all: pd.DataFrame) -> go.Figure:
    """Stacked bars: where each year's harvest carbon ends up (shares sum to 1)."""
    parts = {
        "Long-term use (β)": beta_all["beta"],
        "Pulp & paper": beta_all["pulp_share"],
        "Energy": beta_all["energy_share"],
        "Other": beta_all["other_share"],
    }
    fig = go.Figure()
    for name, values in parts.items():
        fig.add_bar(
            x=beta_all["year"], y=values, name=name,
            marker=dict(color=FATE_COLORS[name], line=dict(color=SURFACE, width=2)),
            hovertemplate=f"<b>{name}</b><br>%{{x}}: %{{y:.1%}}<extra></extra>",
        )
    fig.update_layout(
        barmode="stack", bargap=0.28,
        font=dict(family=FONT_STACK, size=12, color=INK),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        height=380, margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None,
                    traceorder="normal"),
        hovermode="x unified",
        xaxis=dict(tickmode="linear", dtick=1, showgrid=False,
                   linecolor="#cfd6cb", tickfont=dict(color=INK_MUTED)),
        yaxis=dict(tickformat=".0%", range=[0, 1], gridcolor="#e6ebe3",
                   zeroline=False, tickfont=dict(color=INK_MUTED)),
    )
    return fig


def _annual_sum(frame: pd.DataFrame, mask, column: str, n_years: int) -> float:
    return float(frame.loc[mask, column].sum()) / max(n_years, 1)


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #

def main() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    _hero(_vintage_text())

    # Year coverage is independent of the coefficient choice, so derive the slider
    # bounds from the default dataset before the controls are drawn.
    bounds_df = _load_data(*_DEFAULT_ARGS)
    data_min, data_max = int(bounds_df["year"].min()), int(bounds_df["year"].max())

    with st.sidebar:
        st.markdown(
            f"""
            <div class="cw-brand">
              <div class="mark">{TREE_SVG}</div>
              <div><div class="name">Circwood</div>
              <div class="sub">Wood &amp; carbon flow explorer</div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        _section("View")
        metric = st.segmented_control(
            "Metric", options=list(METRICS), default=list(METRICS)[0],
            help="Show wood volume (m³) or embodied carbon (kg C → Mt C).",
        ) or list(METRICS)[0]
        aggregate = st.segmented_control(
            "Aggregation", options=["Annual average", "Single year"],
            default="Annual average",
            help="Average flows over a year window, or show one year.",
        ) or "Annual average"

        if aggregate == "Single year":
            year = st.slider("Year", data_min, data_max, min(C.MAX_YEAR, data_max))
            min_year = max_year = year
            agg_key = "year"
        else:
            min_year, max_year = st.slider(
                "Year range", data_min, data_max,
                (max(C.MIN_YEAR, data_min), min(C.MAX_YEAR, data_max)),
            )
            agg_key = "avg"

        _section("Nodes")
        all_nodes = _all_node_labels()
        visible = st.multiselect(
            "Nodes to display",
            options=all_nodes,
            default=list(all_nodes),
            help="Flows touching a hidden node are removed from the diagram.",
            label_visibility="collapsed",
        )
        nodes_to_drop = tuple(n for n in all_nodes if n not in visible)

        _section("Model assumptions")
        with st.expander("Sawmill & planing coefficients", expanded=False):
            coef_source_label = st.radio(
                "Coefficient source",
                options=["Manual (set percentages)", "Calculate from Treindustrien"],
                help=(
                    "Manual: type the sawmill by-product and planing percentages. "
                    "Treindustrien: derive the sawn-timber yield from the produced-"
                    "volume dataset (single window-averaged value)."
                ),
            )
            source_key = (
                "treindustrien" if coef_source_label.startswith("Calculate") else "manual"
            )

            if source_key == "manual":
                shrinkage = st.number_input(
                    "Shrinkage (water) share", 0.0, 0.95, float(_DEFAULTS.shrinkage),
                    0.01, format="%.3f",
                    help="Water lost at the saw, as a share of raw log input.")
                sawdust = st.number_input(
                    "Sawdust share → Boardmills", 0.0, 0.95, float(_DEFAULTS.sawdust),
                    0.01, format="%.3f")
                cellulose = st.number_input(
                    "Cellulose (chips) share → Pulpmills", 0.0, 0.95,
                    float(_DEFAULTS.cellulose), 0.01, format="%.3f")
                scraps = st.number_input(
                    "Scraps share → Energy", 0.0, 0.95, float(_DEFAULTS.scraps),
                    0.01, format="%.3f")
                planed_input_factor = st.number_input(
                    "Planing input factor (sawn per planed)", 1.0, 2.0,
                    float(_DEFAULTS.planed_input_factor), 0.01, format="%.3f")
                byproduct_planing = st.number_input(
                    "Planing by-product share → Boardmills", 0.0, 0.95,
                    float(_DEFAULTS.byproduct_planing), 0.01, format="%.3f")
                byproduct_total = shrinkage + sawdust + cellulose + scraps
                if byproduct_total >= 1.0:
                    st.error(
                        f"By-product shares sum to {byproduct_total:.0%} (≥ 100%); the "
                        "implied sawn yield would be ≤ 0. Lower them."
                    )
                    st.stop()
                st.caption(
                    f"Implied sawn-timber yield: **{1 - byproduct_total:.1%}** of log input."
                )
            else:
                # Treindustrien-derived: the four by-product shares are rescaled from
                # the defaults to fill the data-measured sawn yield; the planing
                # factors are not reported by Treindustrien, so they keep the defaults.
                shrinkage, sawdust, cellulose, scraps = (
                    _DEFAULTS.shrinkage, _DEFAULTS.sawdust,
                    _DEFAULTS.cellulose, _DEFAULTS.scraps,
                )
                planed_input_factor = _DEFAULTS.planed_input_factor
                byproduct_planing = _DEFAULTS.byproduct_planing
                eff = _effective_coef(
                    source_key, shrinkage, sawdust, cellulose, scraps,
                    planed_input_factor, byproduct_planing,
                )
                st.caption(
                    "Derived from Treindustrien produced sawn timber:\n"
                    f"- **Sawn yield {eff.sawn_yield:.1%}** of log input\n"
                    f"- Shrinkage {eff.shrinkage:.1%} · Sawdust {eff.sawdust:.1%} · "
                    f"Cellulose {eff.cellulose:.1%} · Scraps {eff.scraps:.1%}\n\n"
                    f"Planing input factor stays at {planed_input_factor:.2f} "
                    "(planing conversion is not reported by Treindustrien)."
                )

        coef_args = (
            source_key, shrinkage, sawdust, cellulose, scraps,
            planed_input_factor, byproduct_planing,
        )
        customised = coef_args != _DEFAULT_ARGS
        if customised:
            st.caption(":orange[Custom coefficients — flows rebuilt from primary rows.]")

    df = _load_data(*coef_args)
    carbon_df = _carbon_table(*coef_args)

    is_carbon = metric.startswith("Carbon")
    value_scale, value_unit = METRICS[metric]
    window_label = _window_label(min_year, max_year)
    n_years = max_year - min_year + 1

    # ---- Headline figures for the selected window ------------------------- #
    in_window = (carbon_df["year"] >= min_year) & (carbon_df["year"] <= max_year)
    harvest = in_window & (carbon_df["source"] == HARVEST) & carbon_df["target"].isin(POOLS)
    to_ltuse = in_window & (carbon_df["target"] == LTUSE)
    per = "per year" if n_years > 1 else "in " + window_label

    harvest_m3 = _annual_sum(carbon_df, harvest, "volume", n_years) / 1e6
    harvest_c = _annual_sum(carbon_df, harvest, "carbon", n_years) / 1e9
    ltuse_c = _annual_sum(carbon_df, to_ltuse, "carbon", n_years) / 1e9

    beta = _beta_series()
    beta_all = None
    beta_note = "β export not built"
    beta_value = "—"
    if beta is not None:
        beta_all = beta[beta["species"] == "all"].sort_values("year")
        sel = beta_all[(beta_all["year"] >= min_year) & (beta_all["year"] <= max_year)]
        if len(sel):
            beta_value = f"{sel['beta'].mean():.1%}"
            covered = _window_label(int(sel["year"].min()), int(sel["year"].max()))
            beta_note = (f"of harvest carbon, {covered} mean" if len(sel) > 1
                         else f"of harvest carbon in {covered}")
        else:
            beta_note = f"no β for {window_label} (export covers {C.BETA_MIN_YEAR}–{C.BETA_MAX_YEAR})"

    k1, k2, k3, k4 = st.columns(4)
    _kpi(k1, "Roundwood harvest", f"{harvest_m3:,.1f}", "Mm³", per, "#1f5c3f")
    _kpi(k2, "Carbon in harvest", f"{harvest_c:,.2f}", "Mt C", per, "#6b4f2e")
    _kpi(k3, "Carbon to long-term use", f"{ltuse_c:,.2f}", "Mt C",
         f"{per}, incl. imported wood", "#1f7a4d")
    _kpi(k4, "Empirical β", beta_value, "", beta_note, "#2f6db5")
    st.write("")

    tab_flows, tab_fate, tab_balance, tab_data = st.tabs(
        ["Flow diagram", "Carbon fate & β", "Mass balance", "Data & provenance"]
    )

    # ---- Flow diagram ---------------------------------------------------- #
    with tab_flows:
        st.caption(
            f"Wood-{'carbon' if is_carbon else 'volume'} flows from standing forest "
            f"to end use, {window_label}"
            f"{' (annual average)' if agg_key == 'avg' and n_years > 1 else ''}. "
            "Sources: SSB tables 03795, 07410, 08799, 08801, 10513, 11181 and "
            "Treindustrien."
        )
        if not visible:
            st.warning("Select at least one node to display.")
        else:
            plot_df = _carbon_flows(*coef_args) if is_carbon else df
            fig = make_sankey(
                plot_df, min_year=min_year, max_year=max_year,
                aggregate=agg_key, nodes_to_drop=nodes_to_drop,
                value_scale=value_scale, value_unit=value_unit,
            )
            st.plotly_chart(_style_sankey(fig), width="stretch")

    # ---- Carbon fate & β ------------------------------------------------- #
    with tab_fate:
        if beta_all is None:
            st.info("`data/processed/beta_series.csv` is missing — run the β export.")
        else:
            st.markdown("#### Where the carbon in each year's harvest ends up")
            st.caption(
                "Production basis: domestic harvest carbon traced through the flows "
                "with proportional mixing; imports are never counted. β is the share "
                "reaching long-term use. Other = exports, process losses, shrinkage, "
                "stock change and closure gap. "
                f"Literature value for comparison: β ≈ {C.LITERATURE_BETA:.2f}."
            )
            if customised:
                st.caption(
                    ":orange[β is the committed export, computed with the default "
                    "coefficients; it does not follow the sidebar settings.]"
                )
            st.plotly_chart(_fate_chart(beta_all), width="stretch")

            by_species = (
                beta[beta["species"] != "all"]
                .pivot(index="year", columns="species", values="beta")
                .reindex(columns=["spruce", "pine", "non-coniferous"])
            )
            by_species["all species"] = beta_all.set_index("year")["beta"]
            left, right = st.columns([3, 2])
            with left:
                st.markdown("##### β by species")
                st.dataframe(
                    by_species.style.format("{:.1%}"),
                    width="stretch", height=300,
                )
            with right:
                st.markdown("##### Fate shares (all species)")
                st.dataframe(
                    beta_all.set_index("year")[
                        ["beta", "pulp_share", "energy_share", "other_share"]
                    ].rename(columns={
                        "beta": "long-term", "pulp_share": "pulp",
                        "energy_share": "energy", "other_share": "other",
                    }).style.format("{:.1%}"),
                    width="stretch", height=300,
                )
            st.caption(
                f"The β export covers {C.BETA_MIN_YEAR}–{C.BETA_MAX_YEAR}: 2024 is "
                "refused because sawmill output data (Treindustrien) ends at 2023. "
                "Method and caveats: header of data/processed/beta_series.csv."
            )

    # ---- Mass balance ---------------------------------------------------- #
    with tab_balance:
        st.caption(
            f"Per-node inflow vs outflow, {window_label} (m³). Process nodes should "
            "balance within ~1 %. Use-phase nodes (Long-term/Short-term USE) and the "
            "inventory nodes are reservoirs where wood accumulates, so they are not "
            "expected to net to zero."
        )
        vol_window = df[(df["year"] >= min_year) & (df["year"] <= max_year)]
        rep = balance_report(vol_window)
        rep = rep[rep["kind"] == "process"][
            ["node", "inflow", "outflow", "imbalance", "rel_imbalance"]
        ]
        st.dataframe(
            rep.style.format({
                "inflow": "{:,.0f}", "outflow": "{:,.0f}",
                "imbalance": "{:+,.0f}", "rel_imbalance": "{:+.1%}",
            }),
            width="stretch", height=320, hide_index=True,
        )

        st.markdown("##### Source reconciliation")
        st.caption("Pipeline sawn-timber throughput against the Treindustrien source.")
        recon = treindustrien_reconciliation(df, min_year, max_year)
        st.dataframe(
            recon.style.format({
                "pipeline": "{:,.0f}", "source": "{:,.0f}", "rel_diff": "{:+.2%}",
            }),
            width="stretch", hide_index=True,
        )

    # ---- Data & provenance ----------------------------------------------- #
    with tab_data:
        base = carbon_df if is_carbon else df
        window = base[(base["year"] >= min_year) & (base["year"] <= max_year)]
        st.caption(
            f"{len(window):,} flow rows, {window_label}. Origins: "
            f"{', '.join(sorted(window['origin'].astype(str).unique()))}."
        )
        st.dataframe(window, width="stretch", height=340, hide_index=True)
        st.download_button(
            f"Download {'carbon' if is_carbon else 'volume'} flows (CSV)",
            window.to_csv(index=False).encode("utf-8"),
            file_name=f"circwood_{'carbon' if is_carbon else 'flows'}_{min_year}_{max_year}.csv",
            mime="text/csv",
        )

        st.markdown("##### Species composition (downstream carbon model)")
        st.caption(
            "Spruce / pine / non-coniferous mix tracked through the mixing nodes, "
            "averaged over the window. Editable in config/carbon_densities.csv and "
            "config/carbon_nodes.csv."
        )
        smix = _species_mix(*coef_args)
        smix = smix[(smix["year"] >= min_year) & (smix["year"] <= max_year)]
        smix = (
            smix.groupby("node", as_index=False)[["spruce", "pine", "non-coniferous"]]
            .mean()
            .sort_values("node")
        )
        st.dataframe(
            smix.style.format({"spruce": "{:.1%}", "pine": "{:.1%}",
                               "non-coniferous": "{:.1%}"}),
            width="stretch", height=320, hide_index=True,
        )

    st.markdown(
        """
        <div class="cw-footer">
          <b>Circwood WP4</b> · Modaresi &amp; Landaas, “Wood extraction and utilisation
          in Norway 2012–2022: a national material and carbon flow analysis”,
          <i>Wood Material Science &amp; Engineering</i> (2026). Norwegian Institute of
          Wood Technology. Data: Statistics Norway (SSB), Treindustrien.
          Internal exploration tool — the public site is published separately.
        </div>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
