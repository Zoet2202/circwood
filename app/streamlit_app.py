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
from circwood.viz.sankey import make_sankey  # noqa: E402

st.set_page_config(page_title="Circwood WP4 — Wood Flows", layout="wide")


@st.cache_data(show_spinner=False)
def _load_data():
    return build_sankey(use_cached_ssb=True)


def main() -> None:
    st.title("Norwegian Wood Flows — Circwood WP4")
    st.caption(
        "Sankey of wood-volume flows from standing forest to end use. "
        "Data: SSB + Treindustrien (cached)."
    )

    df = _load_data()
    data_min, data_max = int(df["year"].min()), int(df["year"].max())

    with st.sidebar:
        st.header("Controls")
        aggregate = st.radio(
            "Aggregation",
            options=["Annual average", "Single year"],
            help="Average flows over a year window, or show one year.",
        )

        if aggregate == "Single year":
            year = st.slider(
                "Year", data_min, data_max,
                min(C.MAX_YEAR, data_max),
            )
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

    fig = make_sankey(
        df, min_year=min_year, max_year=max_year,
        aggregate=agg_key, nodes_to_drop=nodes_to_drop,
    )
    fig.update_layout(height=720)
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("Data & provenance"):
        window = df[(df["year"] >= min_year) & (df["year"] <= max_year)]
        st.write(
            f"{len(window)} flow rows over {min_year}-{max_year}. "
            f"Origins: {', '.join(sorted(window['origin'].astype(str).unique()))}"
        )
        st.dataframe(window, use_container_width=True, height=300)
        st.download_button(
            "Download flows (CSV)",
            window.to_csv(index=False).encode("utf-8"),
            file_name=f"circwood_flows_{min_year}_{max_year}.csv",
            mime="text/csv",
        )


if __name__ == "__main__":
    main()
