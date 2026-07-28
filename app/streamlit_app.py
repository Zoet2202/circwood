"""Circwood WP4 — interactive wood-flow Sankey (Streamlit).

Run locally:
    streamlit run app/streamlit_app.py

Loads the cached processed dataset (no live SSB access required), so it is safe
to deploy as-is to Streamlit Community Cloud or any host.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Make the `src` layout importable when run via `streamlit run` (no install).
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from circwood import build_sankey, constants as C  # noqa: E402
from circwood.carbon import add_carbon, carbon_sankey, species_mix  # noqa: E402
from circwood.reconcile import treindustrien_reconciliation  # noqa: E402
from circwood.transform.assemble import (  # noqa: E402
    effective_coefficients,
    regenerate_balanced,
)
from circwood.transform.sawing import SawmillCoefficients  # noqa: E402
from circwood.validate import balance_report  # noqa: E402
from circwood.viz.sankey import load_node_params, make_sankey  # noqa: E402

st.set_page_config(page_title="Circwood WP4 — Wood Flows", layout="wide")

# (value_scale, value_unit) per metric.
METRICS = {
    "Volume (Mm³)": (1_000_000.0, "Mm³"),
    "Carbon (Mt C)": (1_000_000_000.0, "Mt C"),
}


# A coefficient choice is identified by (source, shrinkage, sawdust, cellulose,
# scraps, planed_input_factor, byproduct_planing) — all primitives, so they make
# safe, hashable Streamlit cache keys. The dataclass is rebuilt inside.
_DEFAULTS = SawmillCoefficients()
_DEFAULT_ARGS = (
    "manual", _DEFAULTS.shrinkage, _DEFAULTS.sawdust, _DEFAULTS.cellulose,
    _DEFAULTS.scraps, _DEFAULTS.planed_input_factor, _DEFAULTS.byproduct_planing,
)


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


def _vintage_caption() -> str:
    """"Data as of ..." from the manifest, or an honest admission of ignorance.

    A dashboard that does not state its vintage invites the reader to assume it
    is current. When no manifest exists (a checkout that has never run a fresh
    build) say so rather than showing nothing.
    """
    from circwood.manifest import read_manifest

    manifest = read_manifest()
    if manifest is None:
        return (
            ":grey[Data vintage unknown — no `data/processed/manifest.json`. "
            "Run `circwood-refresh` to record one.]"
        )
    return f":grey[{manifest.describe()}]"


def main() -> None:
    st.title("Norwegian Wood Flows — Circwood WP4")
    st.caption(_vintage_caption())

    # Year coverage is independent of the coefficient choice, so derive the slider
    # bounds from the default dataset before the controls are drawn.
    bounds_df = _load_data(*_DEFAULT_ARGS)
    data_min, data_max = int(bounds_df["year"].min()), int(bounds_df["year"].max())

    with st.sidebar:
        st.header("Controls")
        metric = st.radio(
            "Metric",
            options=list(METRICS),
            help="Show wood volume (m³) or embodied carbon (kg C → Mt C).",
        )
        aggregate = st.radio(
            "Aggregation",
            options=["Annual average", "Single year"],
            help="Average flows over a year window, or show one year.",
        )

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

        st.divider()
        st.subheader("Nodes")
        all_nodes = _all_node_labels()
        visible = st.multiselect(
            "Nodes to display",
            options=all_nodes,
            default=list(all_nodes),
            help="Flows touching a hidden node are removed from the diagram.",
        )
        nodes_to_drop = tuple(n for n in all_nodes if n not in visible)

        st.divider()
        st.subheader("Sawmill & planing coefficients")
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
                f"→ Implied sawn-timber yield: **{1 - byproduct_total:.1%}** of log input."
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

    df = _load_data(*coef_args)

    is_carbon = metric.startswith("Carbon")
    value_scale, value_unit = METRICS[metric]
    plot_df = _carbon_flows(*coef_args) if is_carbon else df

    st.caption(
        f"Sankey of wood-{'carbon' if is_carbon else 'volume'} flows from "
        f"standing forest to end use. Data: SSB + Treindustrien (cached)."
    )

    if not visible:
        st.warning("Select at least one node to display.")
        st.stop()

    fig = make_sankey(
        plot_df, min_year=min_year, max_year=max_year,
        aggregate=agg_key, nodes_to_drop=nodes_to_drop,
        value_scale=value_scale, value_unit=value_unit,
    )
    st.plotly_chart(fig, use_container_width=True)

    base = _carbon_table(*coef_args) if is_carbon else df
    window = base[(base["year"] >= min_year) & (base["year"] <= max_year)]

    with st.expander("Data & provenance"):
        st.write(
            f"{len(window)} flow rows over {min_year}-{max_year}. "
            f"Origins: {', '.join(sorted(window['origin'].astype(str).unique()))}"
        )
        st.dataframe(window, use_container_width=True, height=300)
        st.download_button(
            f"Download {'carbon' if is_carbon else 'volume'} flows (CSV)",
            window.to_csv(index=False).encode("utf-8"),
            file_name=f"circwood_{'carbon' if is_carbon else 'flows'}_{min_year}_{max_year}.csv",
            mime="text/csv",
        )

    with st.expander("Mass balance check"):
        st.caption(
            "Per-node inflow vs outflow over the selected window (m³). Process "
            "nodes should balance within ~1 %. Use-phase nodes (Long-term/"
            "Short-term USE) and the inventory nodes are reservoirs where wood "
            "accumulates, so they are not expected to net to zero."
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
            use_container_width=True, height=320,
        )

        st.markdown("**Source reconciliation** (pipeline vs Treindustrien CSV)")
        recon = treindustrien_reconciliation(df, min_year, max_year)
        st.dataframe(
            recon.style.format({
                "pipeline": "{:,.0f}", "source": "{:,.0f}", "rel_diff": "{:+.2%}",
            }),
            use_container_width=True, hide_index=True,
        )

    if is_carbon:
        with st.expander("Species composition (downstream carbon model)"):
            st.caption(
                "Spruce / pine / non-coniferous mix tracked through the mixing "
                "nodes. Editable in config/carbon_densities.csv and "
                "config/carbon_nodes.csv."
            )
            smix = _species_mix(*coef_args)
            smix = smix[(smix["year"] >= min_year) & (smix["year"] <= max_year)]
            smix = (
                smix.groupby("node", as_index=False)[list(("spruce", "pine", "non-coniferous"))]
                .mean()
                .sort_values("node")
            )
            st.dataframe(smix, use_container_width=True, height=320)


if __name__ == "__main__":
    main()
