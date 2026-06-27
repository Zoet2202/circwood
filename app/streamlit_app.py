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
from circwood.viz.sankey import make_sankey  # noqa: E402

st.set_page_config(page_title="Circwood WP4 — Wood Flows", layout="wide")

# (value_scale, value_unit) per metric.
METRICS = {
    "Volume (Mm³)": (1_000_000.0, "Mm³"),
    "Carbon (Mt C)": (1_000_000_000.0, "Mt C"),
}


@st.cache_data(show_spinner=False)
def _load_data():
    return build_sankey(use_cached_ssb=True)


@st.cache_data(show_spinner=False)
def _carbon_flows():
    """Carbon-valued Sankey frame (volume column carries kg C)."""
    return carbon_sankey(_load_data())


@st.cache_data(show_spinner=False)
def _carbon_table():
    """Full frame with an added carbon column (for the data panel)."""
    return add_carbon(_load_data())


@st.cache_data(show_spinner=False)
def _species_mix():
    return species_mix(_load_data())


def main() -> None:
    st.title("Norwegian Wood Flows — Circwood WP4")

    df = _load_data()
    data_min, data_max = int(df["year"].min()), int(df["year"].max())

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

        drop_energy_output = st.checkbox("Hide 'Energy output' node", value=True)
        nodes_to_drop = ("Energy output",) if drop_energy_output else ()

    is_carbon = metric.startswith("Carbon")
    value_scale, value_unit = METRICS[metric]
    plot_df = _carbon_flows() if is_carbon else df

    st.caption(
        f"Sankey of wood-{'carbon' if is_carbon else 'volume'} flows from "
        f"standing forest to end use. Data: SSB + Treindustrien (cached)."
    )

    fig = make_sankey(
        plot_df, min_year=min_year, max_year=max_year,
        aggregate=agg_key, nodes_to_drop=nodes_to_drop,
        value_scale=value_scale, value_unit=value_unit,
    )
    st.plotly_chart(fig, use_container_width=True)

    base = _carbon_table() if is_carbon else df
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

    if is_carbon:
        with st.expander("Species composition (downstream carbon model)"):
            st.caption(
                "Spruce / pine / non-coniferous mix tracked through the mixing "
                "nodes. Editable in config/carbon_densities.csv and "
                "config/carbon_nodes.csv."
            )
            smix = _species_mix()
            smix = smix[(smix["year"] >= min_year) & (smix["year"] <= max_year)]
            smix = (
                smix.groupby("node", as_index=False)[list(("spruce", "pine", "non-coniferous"))]
                .mean()
                .sort_values("node")
            )
            st.dataframe(smix, use_container_width=True, height=320)


if __name__ == "__main__":
    main()
