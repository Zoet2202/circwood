"""Mass-balance validation for the wood-flow dataset.

A material-flow Sankey should conserve volume at every *process* node: what
flows in equals what flows out (the model treats stock changes as their own
edges to inventory nodes, so they are part of the flow accounting). Boundary
nodes are exempt:

* **sources** — only emit (e.g. ``Harvested wood``, the ``Import *`` nodes);
* **sinks** — only receive (e.g. ``Energy``, ``Loss Pulpmills``, ``Export *``,
  ``Short-term USE`` ...).

``balance_report`` quantifies the imbalance per node; ``assert_balanced`` turns
it into a hard check for the process nodes.
"""

from __future__ import annotations

import pandas as pd


def balance_report(df: pd.DataFrame, by_year: bool = False) -> pd.DataFrame:
    """Per-node inflow/outflow/imbalance.

    Parameters
    ----------
    by_year : bool
        If True, report per (year, node); otherwise aggregate over all years.

    Returns
    -------
    DataFrame with columns
        [node, (year), inflow, outflow, imbalance, rel_imbalance, kind]
    where ``rel_imbalance = imbalance / inflow`` and ``kind`` is one of
    ``source`` / ``sink`` / ``process``.
    """
    keys = ["year"] if by_year else []

    inflow = (
        df.groupby(keys + ["target"])["volume"].sum()
        .rename_axis(keys + ["node"]).rename("inflow")
    )
    outflow = (
        df.groupby(keys + ["source"])["volume"].sum()
        .rename_axis(keys + ["node"]).rename("outflow")
    )
    rep = pd.concat([inflow, outflow], axis=1).fillna(0.0).reset_index()
    rep["imbalance"] = rep["outflow"] - rep["inflow"]
    rep["rel_imbalance"] = rep["imbalance"] / rep["inflow"].where(rep["inflow"] != 0)

    def kind(row):
        if row["inflow"] == 0:
            return "source"
        if row["outflow"] == 0:
            return "sink"
        return "process"

    rep["kind"] = rep.apply(kind, axis=1)
    sort_keys = (keys + ["imbalance"])
    return rep.sort_values(sort_keys, key=lambda s: s.abs() if s.name == "imbalance" else s,
                           ascending=False).reset_index(drop=True)


def process_imbalances(df: pd.DataFrame, by_year: bool = False) -> pd.DataFrame:
    """Just the process nodes (those expected to balance), worst first."""
    rep = balance_report(df, by_year=by_year)
    return rep[rep["kind"] == "process"].reset_index(drop=True)


def assert_balanced(df: pd.DataFrame, rel_tol: float = 0.01, abs_tol: float = 1.0,
                    nodes: list | None = None) -> None:
    """Raise AssertionError if any process node is out of balance.

    A node passes if ``|imbalance| <= abs_tol`` or
    ``|rel_imbalance| <= rel_tol``.
    """
    rep = process_imbalances(df)
    if nodes is not None:
        rep = rep[rep["node"].isin(nodes)]
    bad = rep[(rep["imbalance"].abs() > abs_tol)
              & (rep["rel_imbalance"].abs() > rel_tol)]
    if not bad.empty:
        lines = "\n".join(
            f"  {r.node}: in={r.inflow:,.0f} out={r.outflow:,.0f} "
            f"({r.rel_imbalance:+.1%})"
            for r in bad.itertuples()
        )
        raise AssertionError(f"{len(bad)} node(s) out of balance:\n{lines}")
