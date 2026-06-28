"""Plotly Sankey figure construction (no I/O side effects, no fig.show())."""

from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go

from circwood import config, constants as C

# Data-side node names that differ from the labels in config/nodes.json.
# Any flow whose endpoints are not in nodes.json is dropped during remap.
NODE_RENAME_MAP = {
    "Standing Wood": "Harvested wood",
    "Standing wood": "Harvested wood",
    "End-of-Life": "End-of-life",
    "Material recovery": "Material Recovery",
    "Timber Inventory": "Sawmills_stock",
    "planed timber inventory": "Planingmills_stock",
    "Impregnated inventory": "Boardmills_stock",
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
    value_scale: float = 1_000_000.0,
) -> pd.DataFrame:
    """Filter, rename, scale and aggregate flows for plotting.

    The ``volume`` column is divided by ``value_scale`` (e.g. 1e6 for m3 -> Mm3,
    or 1e9 for kg C -> Mt C). ``aggregate='avg'`` produces the annual average
    over the window; any other value keeps per-year rows.
    """
    df = df.copy()
    df = df.dropna(subset=["year", "volume"])
    if value_scale and value_scale != 1:
        df["volume"] = df["volume"].astype(float) / value_scale

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


def _to_rgba(color: str, alpha: float = 0.4) -> str:
    """Convert a #RRGGBB (or rgb/rgba) color to an rgba() string with alpha."""
    if color.startswith("#"):
        h = color.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return f"rgba({r},{g},{b},{alpha})"
    if color.startswith("rgb("):
        return color.replace("rgb(", "rgba(").replace(")", f",{alpha})")
    return color


def build_figure(
    labels, x_positions, y_positions, node_colors, df,
    min_year=C.MIN_YEAR, max_year=C.MAX_YEAR,
    value_unit="Mm³",
    label_threshold=0.5,
) -> go.Figure:
    """Build the Plotly Sankey figure from prepared + remapped flows.

    Links are coloured by their source node at low opacity so individual flows
    stay traceable through dense regions. Node positions are honoured exactly
    (``arrangement='fixed'``) so the layout is deterministic and does not drift
    between renders.
    """
    df = df.sort_values(["source_id", "target_id"]).reset_index(drop=True)
    values = df["volume"].abs().tolist()
    # Only annotate the larger flows; rely on hover for the rest.
    link_labels = [f"{v:.1f}" if v >= label_threshold else "" for v in values]
    link_colors = [_to_rgba(node_colors[s], 0.40) for s in df["source_id"]]

    link = dict(
        source=df["source_id"].tolist(),
        target=df["target_id"].tolist(),
        value=values,
        label=link_labels,
        color=link_colors,
        hovertemplate=(
            "%{source.label} → %{target.label}<br>"
            "%{value:.2f} " + value_unit + "<extra></extra>"
        ),
    )
    node = dict(
        label=labels, color=node_colors, x=x_positions, y=y_positions,
        pad=22, thickness=18,
        line=dict(color="rgba(0,0,0,0.25)", width=0.5),
        hovertemplate="%{label}<br>Total: %{value:.2f} " + value_unit + "<extra></extra>",
    )
    fig = go.Figure(go.Sankey(
        textfont=dict(color="#222", size=12, family="Arial"),
        arrangement="fixed", node=node, link=link,
        valueformat=".2f", valuesuffix=f" {value_unit}",
    ))
    fig.update_layout(
        title=dict(
            text=f"Wood flows in Norway — annual average {min_year}–{max_year} ({value_unit})",
            font=dict(size=20, family="Arial", color="#111"),
            x=0.01, xanchor="left",
        ),
        font=dict(family="Arial", size=13, color="#222"),
        paper_bgcolor="white",
        plot_bgcolor="white",
        margin=dict(l=10, r=190, t=70, b=20),
        height=760,
    )
    return fig


def make_sankey(
    df: pd.DataFrame,
    min_year: int = C.MIN_YEAR,
    max_year: int = C.MAX_YEAR,
    aggregate: str = "avg",
    nodes_to_drop=("Energy output",),
    value_scale: float = 1_000_000.0,
    value_unit: str = "Mm³",
    label_threshold: float = 0.5,
) -> go.Figure:
    """Convenience: full pipeline from a raw sankey DataFrame to a figure.

    Pass ``value_scale``/``value_unit`` to switch metric, e.g. carbon flows
    (``volume`` column already in kg C) with ``value_scale=1e9``,
    ``value_unit='Mt C'``.
    """
    labels, xs, ys, colors = load_node_params(nodes_to_drop=nodes_to_drop)
    flows = prepare_flows(df, min_year, max_year, aggregate, nodes_to_drop,
                          value_scale=value_scale)
    flows = remap_ids(flows, labels)
    return build_figure(labels, xs, ys, colors, flows, min_year, max_year,
                        value_unit=value_unit, label_threshold=label_threshold)
