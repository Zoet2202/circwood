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

from circwood import build_sankey, config  # noqa: E402
from circwood import constants as C
from circwood.analysis.sensitivity import (  # noqa: E402
    DENSITY_PARAMETER,
    load_ranges,
    load_sensitivity,
    summary_table,
)
from circwood.carbon import add_carbon, carbon_sankey, load_model, species_mix  # noqa: E402
from circwood.carbon.stock import CLASSES as HWP_CLASSES  # noqa: E402
from circwood.reconcile import treindustrien_reconciliation  # noqa: E402
from circwood.transform.assemble import (  # noqa: E402
    effective_coefficients,
    regenerate_balanced,
)
from circwood.transform.forest import (  # noqa: E402
    MEASURE_LABELS,
    NATIONAL,
    NFI_REGIONS,
    increment_vs_harvest,
    load_forest_resource,
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
CO2_METRIC = "CO₂e (Mt CO₂)"
METRICS = {
    "Volume (Mm³)": (1_000_000.0, "Mm³"),
    "Carbon (Mt C)": (1_000_000_000.0, "Mt C"),
    CO2_METRIC: (1_000_000_000.0, "Mt CO₂"),
}
#: Multiplier on the kg C columns for each carbon-based metric. The dataset
#: stays in kg C; CO₂e is a display conversion (constants.CO2_PER_C, Phase G).
CARBON_FACTOR = {"Carbon (Mt C)": 1.0, CO2_METRIC: C.CO2_PER_C}

#: HWP product classes (stock tab). Paper reuses the pulp-and-paper blue and
#: sawnwood the long-term-use green of FATE_COLORS so the tabs agree; panels
#: take the bark brown of the harvest-carbon tile. The two stock
#: initialisations: cohort in the same green, steady state in blue and dashed.
HWP_COLORS = {
    "sawnwood": "#1f7a4d",
    "wood-based panels": "#6b4f2e",
    "paper and paperboard": "#2f6db5",
}
STOCK_METHOD_COLORS = {"cohort": "#1f7a4d", "steady_state": "#2f6db5"}

#: Development classes I–V (forest tab) are an ordered sequence from
#: regeneration to mature forest: a single-hue lightness ramp ending in SPRUCE.
DEVCLASS_COLORS = {
    "HogstI": "#dfe9d3",
    "HogstII": "#b5d1b0",
    "HogstIII": "#7fb08a",
    "HogstIV": "#468c66",
    "HogstV": "#1f5c3f",
}

#: Sensitivity tornado: a coefficient at its low / high bound (blue / orange).
BOUND_COLORS = {"low": "#2f6db5", "high": "#c4502b"}

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
    ``regenerate_balanced``.
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
    """Full frame with ``carbon``, ``carbon_low`` and ``carbon_high`` (kg C).

    The same columns as ``build_carbon()``: the central densities plus the
    Phase G density envelope of config/carbon_densities.csv, re-run on whatever
    flows the sidebar coefficients produced. A densities file without the
    envelope columns yields ``carbon`` alone, and the KPI notes omit the range.
    """
    base = _load_data(*args)
    out = add_carbon(base, load_model(scenario="central"))
    try:
        low, high = load_model(scenario="low"), load_model(scenario="high")
    except ValueError:
        return out
    out["carbon_low"] = add_carbon(base, low)["carbon"]
    out["carbon_high"] = add_carbon(base, high)["carbon"]
    return out


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


@st.cache_data(show_spinner=False)
def _hwp_stock() -> pd.DataFrame | None:
    """The Phase G HWP stock export (both methods), or None if it has not been built."""
    path = config.HWP_STOCK_CSV
    if not path.exists():
        return None
    return pd.read_csv(path, comment="#")


@st.cache_data(show_spinner=False)
def _sensitivity() -> pd.DataFrame | None:
    """The Phase G one-at-a-time sensitivity table, or None if it has not been exported."""
    return load_sensitivity()


@st.cache_data(show_spinner=False)
def _coefficient_ranges() -> tuple[pd.DataFrame | None, str]:
    """config/coefficient_ranges.csv and "", or None and the reason it would not load."""
    try:
        return load_ranges(), ""
    except (OSError, ValueError, KeyError) as exc:
        return None, str(exc)


@st.cache_data(show_spinner=False)
def _forest_resource() -> pd.DataFrame | None:
    """The Phase H forest-inventory table, or None if it has not been built."""
    return load_forest_resource()


def _read_commented(path) -> pd.DataFrame | None:
    """A committed `#`-headed CSV, or None when it has not been built."""
    if not path.exists():
        return None
    return pd.read_csv(path, comment="#")


@st.cache_data(show_spinner=False)
def _economic_value() -> pd.DataFrame | None:
    """The Phase L economic valuation, or None if it has not been built."""
    return _read_commented(config.ECONOMIC_VALUE_CSV)


@st.cache_data(show_spinner=False)
def _rotation_results() -> pd.DataFrame | None:
    """models/rotation output, or None if the model has not been run."""
    return _read_commented(config.PROCESSED_DIR / "rotation_results.csv")


@st.cache_data(show_spinner=False)
def _stock_projection() -> pd.DataFrame | None:
    """models/forest_stock output, or None if the model has not been run."""
    return _read_commented(config.PROCESSED_DIR / "forest_stock_projection.csv")


@st.cache_data(show_spinner=False)
def _sitree_curve() -> pd.DataFrame | None:
    """The SiTree export with its ages shifted as the rotation model shifts them, or None."""
    path = config.ROOT_DIR / "models" / "sitree" / "growth_curve_sitree.csv"
    curve = _read_commented(path)
    if curve is None:
        return None
    params = pd.read_csv(config.ROOT_DIR / "models" / "rotation" / "parameters.csv").set_index("parameter")["value"]
    offset = float(params.get("sitree_stand_age_at_start_years", 0.0))
    return curve.assign(age_years=curve["age_years"] + offset)


@st.cache_data(show_spinner=False)
def _growth_curve() -> pd.DataFrame | None:
    """The rotation model's growth curve (calibrated default or a SiTree export)."""
    return _read_commented(config.ROOT_DIR / "models" / "rotation" / "growth_curve.csv")


#: Stage colours for the economic layer (same family as the fate palette).
STAGE_COLORS = {
    "roundwood": "#1f7a4d",
    "energy": "#c4502b",
    "carbon stored": "#2f6db5",
    "carbon released": "#9aa19c",
}
SCENARIO_COLORS = {"status quo": "#5d6b62", "faustmann": "#c4502b", "crc": "#1f7a4d"}


def _value_chart(values: pd.DataFrame) -> go.Figure:
    """Stacked bars of value by stage and year (billion NOK; released carbon negative)."""
    by = values.groupby(["year", "stage"], as_index=False)["value_nok"].sum()
    fig = go.Figure()
    for stage, color in STAGE_COLORS.items():
        sub = by[by["stage"] == stage]
        if sub.empty:
            continue
        fig.add_bar(x=sub["year"], y=sub["value_nok"] / 1e9, name=stage,
                    marker=dict(color=color, line=dict(color=SURFACE, width=1.5)),
                    hovertemplate=f"<b>{stage}</b><br>%{{x}}: %{{y:.2f}} bn NOK<extra></extra>")
    fig.update_layout(barmode="relative", bargap=0.28, hovermode="x unified",
                      yaxis=dict(title="bn NOK (nominal)", gridcolor="#e6ebe3", zeroline=True,
                                 zerolinecolor="#cfd6cb"),
                      xaxis=dict(tickmode="linear", dtick=1, showgrid=False))
    return _chart_layout(fig, 380)


def _projection_chart(proj: pd.DataFrame, column: str, title: str) -> go.Figure:
    fig = go.Figure()
    for scenario, color in SCENARIO_COLORS.items():
        sub = proj[proj["scenario"] == scenario]
        if sub.empty:
            continue
        fig.add_scatter(x=sub["year"], y=sub[column] / 1e6, mode="lines", name=scenario,
                        line=dict(color=color, width=2.2),
                        hovertemplate=f"<b>{scenario}</b><br>%{{x}}: %{{y:.0f}} Mm³<extra></extra>")
    fig.update_layout(hovermode="x unified",
                      yaxis=dict(title=title, gridcolor="#e6ebe3", rangemode="tozero"),
                      xaxis=dict(showgrid=False))
    return _chart_layout(fig, 340)


def _growth_chart(curve: pd.DataFrame, marks: dict[str, int],
                  comparison: pd.DataFrame | None = None) -> go.Figure:
    fig = go.Figure()
    fig.add_scatter(x=curve["age_years"], y=curve["volume_m3_ha"], mode="lines", name="living volume",
                    line=dict(color=SPRUCE, width=2.4),
                    hovertemplate="age %{x}: %{y:.0f} m³/ha<extra></extra>")
    if "dead_volume_m3_ha" in curve.columns:
        fig.add_scatter(x=curve["age_years"], y=curve["dead_volume_m3_ha"], mode="lines",
                        name="dead-biomass pool", line=dict(color="#9aa19c", width=1.8, dash="dot"),
                        hovertemplate="age %{x}: %{y:.0f} m³/ha<extra></extra>")
    if comparison is not None:
        full = (comparison["volume_m3_ha_full"] if "volume_m3_ha_full" in comparison.columns
                else comparison["volume_m3_ha"])
        fig.add_scatter(x=comparison["age_years"], y=comparison["volume_m3_ha"], mode="lines",
                        name="SiTree example stand (living volume, drawn at 1/5)",
                        line=dict(color="#b07a3b", width=1.6, dash="dash"), customdata=full,
                        hovertemplate="age %{x}: %{customdata:.0f} m³/ha simulated<extra></extra>")
    for label, age in marks.items():
        fig.add_vline(x=age, line=dict(color="#cfd6cb", width=1, dash="dash"),
                      annotation_text=f"{label} {age} y", annotation_position="top left",
                      annotation_font=dict(size=11, color=INK_MUTED))
    fig.update_layout(hovermode="x unified",
                      yaxis=dict(title="m³ per ha", gridcolor="#e6ebe3", rangemode="tozero"),
                      xaxis=dict(title="stand age (years)", showgrid=False))
    return _chart_layout(fig, 320)


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


def _envelope_note(frame: pd.DataFrame, mask, n_years: int, factor: float, unit: str) -> str:
    """KPI note suffix with the carbon_low–carbon_high range, or "" without the columns."""
    if not {"carbon_low", "carbon_high"} <= set(frame.columns):
        return ""
    lo = _annual_sum(frame, mask, "carbon_low", n_years) / 1e9 * factor
    hi = _annual_sum(frame, mask, "carbon_high", n_years) / 1e9 * factor
    return f" · range {lo:,.2f}–{hi:,.2f} {unit} (density envelope)"


def _has_bounds(frame: pd.DataFrame) -> bool:
    """True when the frame carries complete ci_low / ci_high columns."""
    cols = ["ci_low", "ci_high"]
    return set(cols) <= set(frame.columns) and bool(frame[cols].notna().all().all())


def _rgba(hex_color: str, alpha: float) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha})"


def _chart_layout(fig: go.Figure, height: int, top: int = 40) -> go.Figure:
    """The app's chart chrome: fonts, paper surface, a horizontal legend above the plot."""
    fig.update_layout(
        font=dict(family=FONT_STACK, size=12, color=INK),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        height=height, margin=dict(l=10, r=10, t=top, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None),
        xaxis=dict(showgrid=False, linecolor="#cfd6cb", tickfont=dict(color=INK_MUTED)),
        yaxis=dict(gridcolor="#e6ebe3", zeroline=False, tickfont=dict(color=INK_MUTED)),
    )
    return fig


def _stock_chart(stock: pd.DataFrame) -> go.Figure:
    """Total HWP carbon stock at year end (Mt C): cohort solid, steady state dashed."""
    total = stock[stock["product_class"] == "total"]
    first = int(total["year"].min())
    names = {"cohort": f"cohort (zero stock in {first})", "steady_state": "steady state (inflow / k)"}
    fig = go.Figure()
    for method, dash in (("cohort", "solid"), ("steady_state", "dash")):
        g = total[total["method"] == method].sort_values("year")
        if g.empty:
            continue
        fig.add_scatter(
            x=g["year"], y=g["stock_end_kgc"] / 1e9, mode="lines+markers", name=names[method],
            line=dict(color=STOCK_METHOD_COLORS[method], width=2.5, dash=dash), marker=dict(size=6),
            hovertemplate=f"<b>{names[method]}</b><br>%{{x}}: %{{y:.2f}} Mt C<extra></extra>",
        )
    _chart_layout(fig, 360)
    fig.update_layout(hovermode="x unified")
    fig.update_xaxes(tickmode="linear", dtick=1)
    fig.update_yaxes(title_text="Mt C stored at year end", rangemode="tozero")
    return fig


def _stock_area_chart(stock: pd.DataFrame, method: str) -> go.Figure:
    """Stacked area: stock at year end by product class for one initialisation (Mt C)."""
    g = stock[(stock["method"] == method) & (stock["product_class"] != "total")]
    fig = go.Figure()
    for cls in HWP_CLASSES:
        s = g[g["product_class"] == cls].sort_values("year")
        if s.empty:
            continue
        fig.add_scatter(
            x=s["year"], y=s["stock_end_kgc"] / 1e9, name=cls, mode="lines", stackgroup="stock",
            line=dict(color=HWP_COLORS[cls], width=1), fillcolor=_rgba(HWP_COLORS[cls], 0.75),
            hovertemplate=f"<b>{cls}</b><br>%{{x}}: %{{y:.2f}} Mt C<extra></extra>",
        )
    _chart_layout(fig, 360)
    fig.update_layout(hovermode="x unified")
    fig.update_xaxes(tickmode="linear", dtick=1)
    fig.update_yaxes(title_text="Mt C stored at year end", rangemode="tozero")
    return fig


def _tornado_chart(summary: pd.DataFrame) -> go.Figure:
    """Horizontal bars from the central β to β with one coefficient at each of its bounds."""
    central = float(summary["beta_all_central"].iloc[0])
    s = summary.iloc[::-1]  # plotly draws the first category at the bottom: largest effect on top
    labels = [
        f"{p} ({lo:g} – {hi:g})" if pd.notna(lo) and pd.notna(hi) else str(p)
        for p, lo, hi in zip(s["parameter"], s["value_low"], s["value_high"])
    ]
    fig = go.Figure()
    for bound, column in (("low", "beta_all_low"), ("high", "beta_all_high")):
        fig.add_bar(
            y=labels, x=s[column] - central, base=central, orientation="h",
            name=f"coefficient at its {bound} bound", marker=dict(color=BOUND_COLORS[bound]),
            customdata=s[column],
            hovertemplate=f"<b>%{{y}}</b><br>{bound} bound: β = %{{customdata:.3f}}<extra></extra>",
        )
    fig.add_vline(x=central, line=dict(color=INK, width=1.5, dash="dot"),
                  annotation_text=f"central β {central:.3f}", annotation_position="top right")
    _chart_layout(fig, max(320, 30 * len(s) + 110), top=60)
    fig.update_layout(barmode="overlay", bargap=0.3, hovermode="closest")
    fig.update_xaxes(title_text="β (all species), window mean", tickformat=".2f",
                     showgrid=True, gridcolor="#e6ebe3")
    fig.update_yaxes(showgrid=False, tickfont=dict(size=11))
    return fig


def _beta_band_chart(beta_all: pd.DataFrame) -> go.Figure:
    """β (all species) per year with its ci_low–ci_high envelope, when the export carries one."""
    color = FATE_COLORS["Long-term use (β)"]
    b = beta_all.sort_values("year")
    banded = _has_bounds(b)
    fig = go.Figure()
    if banded:
        fig.add_scatter(x=b["year"], y=b["ci_high"], mode="lines", line=dict(width=0),
                        showlegend=False, hoverinfo="skip")
        fig.add_scatter(x=b["year"], y=b["ci_low"], mode="lines", line=dict(width=0),
                        fill="tonexty", fillcolor=_rgba(color, 0.18),
                        name="assumption envelope (ci_low – ci_high)", hoverinfo="skip")
    fig.add_scatter(
        x=b["year"], y=b["beta"], mode="lines+markers", name="β (all species)",
        line=dict(color=color, width=2.5), marker=dict(size=6),
        customdata=b[["ci_low", "ci_high"]].to_numpy() if banded else None,
        hovertemplate=("%{x}: β %{y:.3f} (%{customdata[0]:.3f} – %{customdata[1]:.3f})<extra></extra>"
                       if banded else "%{x}: β %{y:.3f}<extra></extra>"),
    )
    _chart_layout(fig, 340)
    fig.update_layout(hovermode="x unified")
    fig.update_xaxes(tickmode="linear", dtick=1)
    fig.update_yaxes(tickformat=".2f", rangemode="tozero")
    return fig


def _latest(forest: pd.DataFrame, table: str, region: str = NATIONAL) -> pd.DataFrame:
    """Rows of one NFI table for one region in its latest interval."""
    t = forest[(forest["table"] == table) & (forest["region"] == region)]
    return t[t["year_end"] == t["year_end"].max()]


def _interval_of(rows: pd.DataFrame) -> str:
    return str(rows["interval"].iloc[0]) if len(rows) else "—"


def _regional_table(forest: pd.DataFrame, region_order: list[str], labels: pd.Series) -> pd.DataFrame:
    """Standing volume and gross increment (Mm³) by NFI region, latest interval, all species."""

    def total(table: str, measure: str | None = None) -> pd.Series:
        t = forest[(forest["table"] == table) & (forest["species"] == "all")]
        t = t[t["year_end"] == t["year_end"].max()]
        if measure:
            t = t[t["measure"] == measure]
        return t.groupby("region")["value"].sum() * 1e-3  # 1 000 m³ -> Mm³

    return pd.DataFrame({
        "NFI region": labels,
        "standing volume (Mm³)": total("14589"),
        "standing volume, productive forestry land (Mm³)": total("14589", "Skogsareal4"),
        "gross increment (Mm³/yr)": total("14590"),
        "gross increment, productive forestry land (Mm³/yr)": total("14590", "Skogsareal4"),
    }).reindex(region_order)


def _devclass_chart(dev: pd.DataFrame, region_order: list[str], labels: pd.Series) -> go.Figure:
    """Stacked bars: share of productive forestry land by development class, per NFI region (%)."""
    x = [labels[r] for r in region_order]
    fig = go.Figure()
    for code, color in DEVCLASS_COLORS.items():
        values = dev[dev["measure"] == code].set_index("region")["value"].reindex(region_order)
        name = MEASURE_LABELS[code].removeprefix("Development class ")
        fig.add_bar(
            x=x, y=values, name=name,
            marker=dict(color=color, line=dict(color=SURFACE, width=1.5)),
            hovertemplate=f"<b>Class {name}</b><br>%{{x}}: %{{y:.0f}} %<extra></extra>",
        )
    _chart_layout(fig, 420, top=70)
    fig.update_layout(barmode="stack", bargap=0.3, hovermode="x unified",
                      legend=dict(traceorder="normal"))
    fig.update_xaxes(tickangle=-20)
    fig.update_yaxes(title_text="% of productive forestry land", range=[0, 100])
    return fig


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
            "Metric", options=list(METRICS), default=list(METRICS)[0], key="metric",
            help=("Show wood volume (m³), embodied carbon (kg C → Mt C) or its CO₂ "
                  f"equivalent (× {C.CO2_PER_C:.3f} kg CO₂ per kg C → Mt CO₂)."),
        ) or list(METRICS)[0]
        aggregate = st.segmented_control(
            "Aggregation", options=["Annual average", "Single year"],
            default="Annual average", key="aggregate",
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

    is_carbon = metric in CARBON_FACTOR
    is_co2 = metric == CO2_METRIC
    value_scale, value_unit = METRICS[metric]
    # The carbon tiles read Mt C unless the CO₂e metric is chosen.
    tile_factor, tile_unit = (C.CO2_PER_C, "Mt CO₂") if is_co2 else (1.0, "Mt C")
    window_label = _window_label(min_year, max_year)
    n_years = max_year - min_year + 1

    # ---- Headline figures for the selected window ------------------------- #
    in_window = (carbon_df["year"] >= min_year) & (carbon_df["year"] <= max_year)
    harvest = in_window & (carbon_df["source"] == HARVEST) & carbon_df["target"].isin(POOLS)
    to_ltuse = in_window & (carbon_df["target"] == LTUSE)
    per = "per year" if n_years > 1 else "in " + window_label

    harvest_m3 = _annual_sum(carbon_df, harvest, "volume", n_years) / 1e6
    harvest_c = _annual_sum(carbon_df, harvest, "carbon", n_years) / 1e9 * tile_factor
    ltuse_c = _annual_sum(carbon_df, to_ltuse, "carbon", n_years) / 1e9 * tile_factor
    harvest_range = _envelope_note(carbon_df, harvest, n_years, tile_factor, tile_unit)
    ltuse_range = _envelope_note(carbon_df, to_ltuse, n_years, tile_factor, tile_unit)

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
            if _has_bounds(sel):
                beta_note += f" · envelope {sel['ci_low'].mean():.1%}–{sel['ci_high'].mean():.1%}"
        else:
            beta_note = f"no β for {window_label} (export covers {C.BETA_MIN_YEAR}–{C.BETA_MAX_YEAR})"

    k1, k2, k3, k4 = st.columns(4)
    _kpi(k1, "Roundwood harvest", f"{harvest_m3:,.1f}", "Mm³", per, "#1f5c3f")
    _kpi(k2, "Carbon in harvest", f"{harvest_c:,.2f}", tile_unit, per + harvest_range, "#6b4f2e")
    _kpi(k3, "Carbon to long-term use", f"{ltuse_c:,.2f}", tile_unit,
         f"{per}, incl. imported wood{ltuse_range}", "#1f7a4d")
    _kpi(k4, "Empirical β", beta_value, "", beta_note, "#2f6db5")
    st.write("")

    (tab_flows, tab_fate, tab_sens, tab_stock, tab_forest, tab_econ, tab_models,
     tab_balance, tab_data) = st.tabs(
        ["Flow diagram", "Carbon fate & β", "Sensitivity", "HWP stock",
         "Forest resource", "Economics", "Models", "Mass balance", "Data & provenance"]
    )

    # ---- Flow diagram ---------------------------------------------------- #
    with tab_flows:
        st.caption(
            f"Wood-{'carbon' if is_carbon else 'volume'} flows"
            f"{' as CO₂ equivalent' if is_co2 else ''} from standing forest "
            f"to end use, {window_label}"
            f"{' (annual average)' if agg_key == 'avg' and n_years > 1 else ''}. "
            "Sources: SSB tables 03795, 07410, 08799, 08801, 10513, 11181 and "
            "Treindustrien."
        )
        if not visible:
            st.warning("Select at least one node to display.")
        else:
            plot_df = _carbon_flows(*coef_args) if is_carbon else df
            if is_co2:
                plot_df = plot_df.assign(volume=plot_df["volume"] * C.CO2_PER_C)
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

    # ---- Sensitivity ----------------------------------------------------- #
    with tab_sens:
        sens = _sensitivity()
        if sens is None:
            st.info(
                "`data/processed/beta_sensitivity.csv` is missing — export it with "
                "`python -c \"from circwood.analysis.sensitivity import export_sensitivity; "
                "export_sensitivity()\"` (Phase G)."
            )
        else:
            st.markdown("#### How much each fixed coefficient moves β")
            s_min, s_max = int(sens["year"].min()), int(sens["year"].max())
            w_min, w_max = max(min_year, s_min), min(max_year, s_max)
            if w_min > w_max:
                st.info(f"No sensitivity rows for {window_label}; the export covers {s_min}–{s_max}.")
            else:
                summary = summary_table(sens, w_min, w_max)
                central = float(summary["beta_all_central"].iloc[0])
                st.caption(
                    "One coefficient at a time is set to the low and then the high bound of "
                    "config/coefficient_ranges.csv, the national flows are rebuilt and β is "
                    f"recomputed. Bars: mean β (all species) over {_window_label(w_min, w_max)} "
                    "with the coefficient at its low (blue) and high (orange) bound; the dotted "
                    f"line is the central β, {central:.3f}. Ranked by the largest |Δβ| over every "
                    "year and species."
                )
                st.plotly_chart(_tornado_chart(summary), width="stretch")
                st.dataframe(
                    summary.style.format({
                        "value_low": "{:g}", "value_high": "{:g}",
                        "beta_all_central": "{:.3f}", "beta_all_low": "{:.3f}",
                        "beta_all_high": "{:.3f}", "max_abs_delta": "{:.4f}",
                    }, na_rep="—"),
                    width="stretch", hide_index=True,
                )

            st.markdown("##### β (all species) with its envelope")
            if beta_all is None:
                st.info("`data/processed/beta_series.csv` is missing — run the β export.")
            else:
                st.plotly_chart(_beta_band_chart(beta_all), width="stretch")
                ci_method = (
                    str(beta_all["ci_method"].dropna().iloc[0])
                    if "ci_method" in beta_all.columns and beta_all["ci_method"].notna().any()
                    else "no ci_method column in beta_series.csv"
                )
                st.caption(
                    "The band is the ci_low–ci_high envelope of beta_series.csv. The bounds are "
                    "an assumption envelope rather than a statistical confidence interval — "
                    f"ci_method: “{ci_method}”."
                )

            st.markdown("##### Coefficient ranges")
            ranges, problem = _coefficient_ranges()
            if ranges is None:
                st.warning(f"config/coefficient_ranges.csv could not be loaded: {problem}")
            else:
                st.caption(
                    "Researcher-editable in config/coefficient_ranges.csv; each central value "
                    "must equal constants.py."
                    + (" The carbon-density envelope (config/carbon_densities.csv low/high) is "
                       "varied as well and has no row here."
                       if DENSITY_PARAMETER in set(sens["parameter"]) else "")
                )
                st.dataframe(ranges, width="stretch", hide_index=True)

    # ---- HWP stock ------------------------------------------------------- #
    with tab_stock:
        stock = _hwp_stock()
        if stock is None:
            st.info(
                "`data/processed/hwp_stock.csv` is missing — export it with "
                "`python -c \"from circwood.carbon.stock import export_hwp_stock; "
                "export_hwp_stock()\"` (Phase G)."
            )
        else:
            first, last = int(stock["year"].min()), int(stock["year"].max())
            half_lives = (
                stock[stock["product_class"] != "total"]
                .drop_duplicates("product_class")
                .set_index("product_class")["half_life_years"]
            )
            st.markdown("#### Carbon stored in harvested-wood products")
            st.caption(
                "First-order decay with product-class half-lives (IPCC 2006 Guidelines / 2019 "
                "Refinement, Vol. 4 Ch. 12, Eq. 12.1) of the carbon entering Long-term USE "
                "(sawnwood and panels, split by the panel share of the WPP inflow) and "
                "Short-term USE (paper). Imports are included — a stock-in-Norway view, unlike β. "
                "Half-lives: "
                + ", ".join(f"{cls} {half_lives[cls]:g} y" for cls in HWP_CLASSES if cls in half_lives.index)
                + f" (config/hwp_half_lives.csv). The series covers {first}–{last}, the β window, "
                "whatever the sidebar year range."
            )
            left, right = st.columns(2)
            with left:
                st.markdown("##### Total stock, two initialisations")
                st.plotly_chart(_stock_chart(stock), width="stretch")
            with right:
                st.markdown("##### Cohort stock by product class")
                st.plotly_chart(_stock_area_chart(stock, "cohort"), width="stretch")
            st.caption(
                f"**Cohort**: the stock starts at zero in {first}, so the series is the carbon "
                "from this window's inflows that is still stored. **Steady state**: the stock "
                "starts at inflow / k, the level a constant pre-window inflow equal to the first "
                "year's would have reached, so the series is the total product stock under that "
                "assumption."
            )
            method = st.radio(
                "Method for the table", list(STOCK_METHOD_COLORS), horizontal=True,
                key="stock_method", format_func=lambda m: m.replace("_", " "),
            )
            tot = stock[(stock["method"] == method) & (stock["product_class"] == "total")]
            tot = tot.sort_values("year")
            table = pd.DataFrame({
                "year": tot["year"].astype(int),
                "inflow (Mt C)": tot["inflow_kgc"] / 1e9,
                "modelled decay (Mt C)": tot["decay_kgc"] / 1e9,
                "net change (Mt C)": tot["net_change_kgc"] / 1e9,
                "stock at year end (Mt C)": tot["stock_end_kgc"] / 1e9,
                "observed End-of-Life outflow (Mt C)": tot["observed_eol_kgc"] / 1e9,
            })
            st.dataframe(
                table.style.format({c: "{:,.3f}" for c in table.columns if c != "year"}),
                width="stretch", hide_index=True, height=320,
            )
            st.caption(
                "The observed outflow is Long-term USE → End-of-Life from SSB 10513 — waste wood "
                "delivered to incineration only — which measures something narrower than the "
                "modelled decay (every product leaving use, paper included), so the two need not "
                "agree."
            )

    # ---- Forest resource ------------------------------------------------- #
    with tab_forest:
        forest = _forest_resource()
        if forest is None:
            st.info(
                "`data/processed/forest_resource.csv` is missing — run `circwood-forest` "
                "(Phase H; needs the ssb extras and network) to build the forest resource layer."
            )
        else:
            vol, inc, area = _latest(forest, "14589"), _latest(forest, "14590"), _latest(forest, "14591")
            vol_all = vol[vol["species"] == "all"]
            inc_all = inc[inc["species"] == "all"]
            kcub = 1e-3  # 1 000 m³ -> Mm³
            standing = float(vol_all["value"].sum()) * kcub
            standing_prod = float(vol_all.loc[vol_all["measure"] == "Skogsareal4", "value"].sum()) * kcub
            increment = float(inc_all["value"].sum()) * kcub
            increment_prod = float(inc_all.loc[inc_all["measure"] == "Skogsareal4", "value"].sum()) * kcub
            prod_land = float(area.loc[area["measure"] == "ProdSkogBrukM", "value"].sum())
            protected = float(area.loc[area["measure"] == "VernProdSkogAr", "value"].sum())

            st.markdown("#### The resource behind Harvested wood")
            st.caption(
                "NIBIO's National Forest Inventory as published by SSB (Landsskogtakseringen, "
                "tables 14589–14594 and 06289): permanent plots re-measured every five years, so "
                "every figure is a rolling five-year interval, never a calendar year. National "
                "figures for the latest interval; volumes under bark."
            )
            f1, f2, f3, f4 = st.columns(4)
            _kpi(f1, "Standing volume", f"{standing:,.0f}", "Mm³",
                 f"{_interval_of(vol)} · {standing_prod:,.0f} Mm³ on productive forestry land",
                 "#1f5c3f")
            _kpi(f2, "Gross annual increment", f"{increment:,.1f}", "Mm³/yr",
                 f"{_interval_of(inc)} · {increment_prod:,.1f} Mm³/yr on productive forestry land",
                 "#1f7a4d")
            _kpi(f3, "Productive forestry land", f"{prod_land:,.0f}", "km²",
                 f"{_interval_of(area)} · productive forest excl. protected / set aside", "#6b4f2e")
            _kpi(f4, "Protected productive forest", f"{protected:,.0f}", "km²",
                 f"{_interval_of(area)} · not counted in productive forestry land", "#5d6b62")
            st.write("")

            st.markdown("##### Increment against harvest")
            ivh = increment_vs_harvest(forest, df, min_year, max_year)
            harvest_window = str(ivh["harvest_window"].iloc[0]).replace("-", "–")
            harvest_col = f"harvest, mean {harvest_window} (Mm³/yr)"
            show = pd.DataFrame({
                "species": ivh["species"],
                "NFI interval": ivh["interval"],
                "gross increment, productive forestry land (Mm³/yr)": ivh["increment_productive_m3"] / 1e6,
                "gross increment, all land (Mm³/yr)": ivh["increment_total_m3"] / 1e6,
                "standing volume (Mm³)": ivh["standing_volume_m3"] / 1e6,
                harvest_col: ivh["harvest_mean_m3"] / 1e6,
                "harvest / increment (all land)": ivh["harvest_to_increment"],
            })
            st.dataframe(
                show.style.format({
                    "gross increment, productive forestry land (Mm³/yr)": "{:,.1f}",
                    "gross increment, all land (Mm³/yr)": "{:,.1f}",
                    "standing volume (Mm³)": "{:,.0f}",
                    harvest_col: "{:,.1f}",
                    "harvest / increment (all land)": "{:.1%}",
                }),
                width="stretch", hide_index=True,
            )
            st.caption(
                "Orientation, not a fence. The inventory is a five-year interval while the harvest "
                "is a calendar-year mean over the sidebar window; the inventory is measured under "
                "bark on productive forestry land, which excludes protected forest, while the "
                "harvest (SSB 03795 commercial removals plus the SSB 11181 firewood estimate) has a "
                "firewood component measured with bark."
            )

            st.markdown("##### Standing volume and increment by NFI region")
            labels = forest.drop_duplicates("region").set_index("region")["region_label"]
            region_order = [
                r for r in (NATIONAL if code == "0" else code for code in NFI_REGIONS)
                if r in labels.index
            ]
            regional = _regional_table(forest, region_order, labels)
            st.dataframe(
                regional.style.format({c: "{:,.1f}" for c in regional.columns if c != "NFI region"}),
                width="stretch", hide_index=True,
            )
            st.caption(
                f"Interval {_interval_of(vol)}, all species, under bark. Regions are SSB's "
                "takserte regioner: the country and eight groupings of the 2024 counties "
                "(transform.forest.NFI_REGIONS maps them to the flow table's county codes)."
            )

            st.markdown("##### Development classes by region")
            dev = forest[(forest["table"] == "14594") & forest["measure"].isin(DEVCLASS_COLORS)]
            dev = dev[dev["year_end"] == dev["year_end"].max()]
            if dev.empty:
                st.info("No development-class rows (SSB 14594) in forest_resource.csv.")
            else:
                st.plotly_chart(_devclass_chart(dev, region_order, labels), width="stretch")
                st.caption(
                    f"SSB 14594, interval {_interval_of(dev)}: share of productive forestry land by "
                    "development class, I (regeneration) to V (mature); classes IV–V are the "
                    "harvestable stand. Development class IV's Norwegian label on SSB reads "
                    "“Hogstklasse VI”; the app keys on the code (HogstIV), not on the label."
                )

    # ---- Economics (Phase L) --------------------------------------------- #
    with tab_econ:
        values = _economic_value()
        if values is None:
            st.info("`data/processed/economic_value.csv` is missing — run `circwood-economics`.")
        else:
            st.markdown("#### What the wood is worth at each stage")
            st.caption(
                "Nominal NOK of each year. Roundwood is valued where it leaves the species pools, "
                "at SSB 07413 average prices by assortment (firewood at the all-assortment average, "
                "a proxy); energy at 753 kWh/m³ × the SSB 08360 day-ahead electricity price; carbon "
                "entering long-term use (stored) and energy (released, negative) as t CO₂ at the "
                "carbon price in config/prices.csv — an assumption, labelled as such. Processed "
                "products are not priced: SSB publishes no price per m³ for them. Nothing here is on "
                "the public site until the authors have reviewed it."
            )
            win = values[(values["year"] >= min_year) & (values["year"] <= max_year)]
            if win.empty:
                st.info(f"No valued flows in {window_label}.")
            else:
                n_val_years = max(win["year"].nunique(), 1)
                tiles = st.columns(4)
                for col, (stage, accent) in zip(tiles, STAGE_COLORS.items()):
                    total = win.loc[win["stage"] == stage, "value_nok"].sum() / n_val_years / 1e9
                    priced = int(win.loc[win["stage"] == stage, "value_nok"].notna().sum())
                    _kpi(col, stage, f"{total:,.2f}", "bn NOK/yr",
                         f"{per}; {priced} priced rows", accent)
                st.write("")
                st.plotly_chart(_value_chart(win), width="stretch")
                left, right = st.columns([3, 2])
                with left:
                    st.markdown("##### Roundwood value by species and assortment")
                    rw = (win[win["stage"] == "roundwood"]
                          .groupby(["species", "assortment"], as_index=False)
                          .agg(volume_m3=("quantity", "sum"), value_nok=("value_nok", "sum")))
                    rw["volume_m3"] /= n_val_years
                    rw["value_nok"] /= n_val_years
                    rw["nok_per_m3"] = rw["value_nok"] / rw["volume_m3"]
                    st.dataframe(
                        rw.style.format({"volume_m3": "{:,.0f}", "value_nok": "{:,.0f}",
                                         "nok_per_m3": "{:,.0f}"}),
                        width="stretch", height=300, hide_index=True,
                    )
                with right:
                    st.markdown("##### Price assumptions (config/prices.csv)")
                    try:
                        from circwood.transform.economics import load_assumptions

                        st.dataframe(load_assumptions().reset_index()[["parameter", "value", "unit", "kind"]],
                                     width="stretch", height=300, hide_index=True)
                    except (OSError, ValueError) as exc:
                        st.info(f"prices.csv could not be read: {exc}")
                unpriced = win[win["price"].isna()]
                if not unpriced.empty:
                    st.caption(f"{len(unpriced)} row(s) have no published price for their year and carry no value.")

    # ---- Models (Phase L) ------------------------------------------------ #
    with tab_models:
        st.markdown("#### Rotation economics with the empirical β")
        st.caption(
            "Separate models under models/ (they read β, the prices and the inventory and write "
            "result files; the pipeline never imports them). Landaas (2023): Faustmann, van Kooten "
            "with carbon, the CRC owner model and the social optimum, with β in (1 − β), the share of "
            "the harvested stem not stored in products. The growth curve is a logistic living-volume "
            "curve calibrated to the thesis's two stated facts (Faustmann optimum near 90 years at 3 %, "
            "plateau near 135 years) unless models/sitree has replaced it with a SiTree export. "
            "A single-stand optimisation, not a forecast."
        )
        rot = _rotation_results()
        if rot is None:
            st.info("`data/processed/rotation_results.csv` is missing — run `py -3 -m models.rotation.rotation`.")
        else:
            base = rot[rot["scenario"] == "base"]
            pivot = base.pivot(index="beta_source", columns="model", values="t_opt_years")
            order = [m for m in ("faustmann_single", "faustmann", "van_kooten", "crc_owner", "social")
                     if m in pivot.columns]
            st.markdown("##### Optimal rotation age (years) by model and β")
            st.dataframe(pivot[order], width="stretch")
            betas = base.drop_duplicates("beta_source").set_index("beta_source")["beta"]
            st.caption(
                "β: " + "; ".join(f"{k} = {v:.3f}" for k, v in betas.items())
                + ". The thesis reported 90 (Faustmann), 125 (van Kooten) and 120 (CRC) years on its "
                "simulated stand. A lower β makes harvest costlier in carbon, so the carbon models "
                "harvest later with the empirical β than with the assumed 0.55."
            )
            scen = rot[(rot["scenario"] != "base") & (rot["model"].isin(["faustmann", "van_kooten", "crc_owner", "social"]))]
            if not scen.empty:
                st.markdown("##### Scenarios (empirical β): optimal age by model")
                sp = scen.pivot_table(index="scenario", columns="model", values="t_opt_years", aggfunc="first")
                st.dataframe(sp, width="stretch")
                if "interior_optimum" in rot.columns and (rot["interior_optimum"] == 0).any():
                    st.caption(
                        "An age equal to the end of the growth curve (250 years) means the NPV was still "
                        "rising: under those prices the model says never harvest within the horizon — a "
                        "statement about constant prices valued forever, not a rotation age."
                    )
            curve = _growth_curve()
            if curve is not None:
                marks = {}
                emp = base[base["beta_source"] == "empirical spruce"].set_index("model")["t_opt_years"]
                if not emp.empty:
                    marks = {"Faustmann": int(emp.get("faustmann", 0)) or 0,
                             "van Kooten": int(emp.get("van_kooten", 0)) or 0,
                             "CRC": int(emp.get("crc_owner", 0)) or 0}
                    marks = {k: v for k, v in marks.items() if v}
                st.markdown("##### The growth curve the models run on")
                sitree = _sitree_curve()
                comparison = None
                if sitree is not None:
                    # Drawn at a fifth of its value so the two shapes share one axis: the simulated
                    # stand's absolute volumes are outside its growth functions' calibration range.
                    comparison = pd.DataFrame({"age_years": sitree["age_years"],
                                               "volume_m3_ha": sitree["volume_m3_ha"] / 5,
                                               "volume_m3_ha_full": sitree["volume_m3_ha"]})
                st.plotly_chart(_growth_chart(curve, marks, comparison), width="stretch")
                if sitree is not None:
                    st.caption(
                        "Dashed: the SiTree simulation of the package's example stand (models/sitree), ages shifted "
                        "by the stand's age at the start and drawn at a fifth of its value. Its basal area reaches "
                        "about 280 m²/ha — outside the growth functions' calibration range — so only its shape is "
                        "compared; the models' results on it are the 'growth curve: sitree example stand' rows."
                    )

        st.markdown("#### The national wood stock to 2100")
        proj = _stock_projection()
        if proj is None:
            st.info("`data/processed/forest_stock_projection.csv` is missing — run `py -3 -m models.forest_stock.projection`.")
        else:
            st.caption(
                "An area-class model over the five development classes from today's national "
                "inventory (SSB 14594, 14591, 14589, 14590), with indicative class residences and "
                "per-hectare weights calibrated to today's totals (models/forest_stock/parameters.csv). "
                "Harvest scenarios: the 2012–2022 mean every year (status quo), final felling at the "
                "Faustmann age, and at the CRC age — the last two phased at no more than 1.5× today's "
                "harvest. A scenario tool: direction and rough size, not a forecast."
            )
            a, b = st.columns(2)
            with a:
                st.plotly_chart(_projection_chart(proj, "standing_volume_m3", "standing volume, Mm³"),
                                width="stretch")
            with b:
                st.plotly_chart(_projection_chart(proj, "harvest_m3", "harvest, Mm³ per year"),
                                width="stretch")
            marks_years = [y for y in (int(proj["year"].min()), 2050, 2075, 2100) if y in set(proj["year"])]
            table = (proj[proj["year"].isin(marks_years)]
                     .pivot(index="year", columns="scenario", values="standing_volume_m3") / 1e6)
            st.dataframe(table.style.format("{:,.0f}"), width="stretch")
            st.caption("Standing volume, Mm³ under bark on productive forestry land, by scenario.")

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
        kind = "co2e" if is_co2 else ("carbon" if is_carbon else "flows")
        if is_co2:
            c_cols = [c for c in ("carbon", "carbon_low", "carbon_high") if c in base.columns]
            base = base.assign(**{c: base[c] * C.CO2_PER_C for c in c_cols}).rename(
                columns={c: c.replace("carbon", "co2e") for c in c_cols}
            )
        window = base[(base["year"] >= min_year) & (base["year"] <= max_year)]
        st.caption(
            f"{len(window):,} flow rows, {window_label}. Origins: "
            f"{', '.join(sorted(window['origin'].astype(str).unique()))}."
            + (f" The co2e columns are kg CO₂ (kg C × {C.CO2_PER_C:.3f}); the dataset "
               "itself stays in kg C." if is_co2 else "")
        )
        st.dataframe(window, width="stretch", height=340, hide_index=True)
        st.download_button(
            f"Download {'CO₂e' if is_co2 else 'carbon' if is_carbon else 'volume'} flows (CSV)",
            window.to_csv(index=False).encode("utf-8"),
            file_name=f"circwood_{kind}_{min_year}_{max_year}.csv",
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
