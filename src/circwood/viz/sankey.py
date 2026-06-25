"""Plotly Sankey figure construction (no I/O side effects, no fig.show())."""

from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go

from circwood import config, constants as C

# Data-side node names that differ from the labels in config/nodes.json.
# Any flow whose endpoints are not in nodes.json is dropped during remap.
NODE_RENAME_MAP = {
    "Standing Wood": "Standing wood",
    "End-of-Life": "End-of-life",
    "Material recovery": "Material Recovery",
    "Timber Inventory": "Sawmills_stock",
    "planed timber inventory": "Planingmills_stock",
    "Impregnated inventory": "Boardmills_stock",
    "Export sawn wood": "Export Sawn",
    "Export Sawmills": "Export Sawn",
    "Export planed wood": "Export Planed",
}


def load_node_params(nodes_to_drop=(), param_file=None):
    """Load node labels/positions/colors from the params JSON."""
    with open(param_file or config.NODES_FILE, "r", encoding="utf-8") as fh:
        nodes = pd.DataFrame(json.load(fh))
    nodes = nodes[~nodes["label"].isin(nodes_to_drop)]
    return (
        nodes["label"].tolist(),
        nodes["x_position"].tolist(),
        nodes["y_position"].tolist(),
        nodes["node_color"].tolist(),
    )


def prepare_flows(
    df: pd.DataFrame,
    min_year: int = C.MIN_YEAR,
    max_year: int = C.MAX_YEAR,
    aggregate: str = "avg",
    nodes_to_drop=(),
    to_million: bool = True,
) -> pd.DataFrame:
    """Filter, rename, unit-convert and aggregate flows for plotting.

    ``aggregate='avg'`` produces the annual-average over the window; any other
    value keeps per-year rows (already summed within year).
    """
    df = df.copy()
    df = df.dropna(subset=["year", "volume"])
    if to_million:
        df["volume"] = df["volume"].astype(float) / 1_000_000.0

    df["source"] = df["source"].replace(NODE_RENAME_MAP)
    df["target"] = df["target"].replace(NODE_RENAME_MAP)

    df = df[~df["target"].isin(nodes_to_drop)]
    df = df[~df["source"].isin(nodes_to_drop)]

    df["year"] = df["year"].astype(int)
    df = df[(df["year"] >= min_year) & (df["year"] <= max_year)]

    # Sum within year first (the rename can re-merge distinct nodes), then mean.
    df = df.groupby(["source", "target", "year"], as_index=False)["volume"].sum()
    if aggregate == "avg":
        df = df.groupby(["source", "target"], as_index=False)["volume"].mean()
    return df


def remap_ids(df: pd.DataFrame, labels) -> pd.DataFrame:
    """Map source/target names to integer ids; drop flows with unknown nodes."""
    label_to_index = {label: i for i, label in enumerate(labels)}
    df = df.copy()
    df["source_id"] = df["source"].map(label_to_index)
    df["target_id"] = df["target"].map(label_to_index)
    df = df.dropna(subset=["source_id", "target_id"])
    df[["source_id", "target_id"]] = df[["source_id", "target_id"]].astype(int)
    return df


def build_figure(
    labels, x_positions, y_positions, node_colors, df,
    min_year=C.MIN_YEAR, max_year=C.MAX_YEAR,
    value_unit="Mm³",
) -> go.Figure:
    """Build the Plotly Sankey figure from prepared + remapped flows."""
    df = df.sort_values(["source_id", "target_id"]).reset_index(drop=True)
    values = df["volume"].abs().tolist()
    link_labels = [f"{v:.1f}" if v >= 0.05 else "" for v in values]

    link = dict(
        source=df["source_id"].tolist(),
        target=df["target_id"].tolist(),
        value=values,
        label=link_labels,
        hovertemplate=(
            "%{source.label} → %{target.label}<br>"
            "%{value:.2f} " + value_unit + "<extra></extra>"
        ),
    )
    node = dict(
        label=labels, color=node_colors, x=x_positions, y=y_positions,
        pad=15, thickness=20, align="right",
        hovertemplate="%{label}<br>Total: %{value:.2f} " + value_unit + "<extra></extra>",
    )
    fig = go.Figure(go.Sankey(
        textfont=dict(color="black", size=12),
        arrangement="snap", node=node, link=link,
        valueformat=".2f", valuesuffix=f" {value_unit}",
    ))
    fig.update_layout(
        title_text=f"Wood flows in Norway, annual average {min_year}-{max_year} ({value_unit})",
        font_size=10,
    )
    return fig


def make_sankey(
    df: pd.DataFrame,
    min_year: int = C.MIN_YEAR,
    max_year: int = C.MAX_YEAR,
    aggregate: str = "avg",
    nodes_to_drop=("Energy output",),
) -> go.Figure:
    """Convenience: full pipeline from a raw sankey DataFrame to a figure."""
    labels, xs, ys, colors = load_node_params(nodes_to_drop=nodes_to_drop)
    flows = prepare_flows(df, min_year, max_year, aggregate, nodes_to_drop)
    flows = remap_ids(flows, labels)
    return build_figure(labels, xs, ys, colors, flows, min_year, max_year)
