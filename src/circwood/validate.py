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

from circwood import constants as C

#: The nodes expected to conserve volume, and the only ones the balance
#: guarantee covers. Everything else is legitimately unbalanced over a window:
#:
#: * **species pools** (Spruce/Pine/Non-coniferous) and **Harvested wood** are
#:   fed by tables whose year coverage differs from the trade table's, so
#:   outside the analysis window they emit more than they receive;
#: * **use phases** (Long-term/Short-term USE) accumulate wood — that is the
#:   point of them;
#: * **inventory** nodes hold net stock changes, and **net export** nodes hold a
#:   signed residual.
#:
#: Kept here rather than in the test-suite so the scheduled refresh and the
#: regression test check the same thing.
PROCESS_NODES = [
    "Sawmills", "Pulpmills", "Boardmills", "Planingmills", "Impregnation",
    "Wood Product processing & Logistics",
]


#: The dimension slices the per-slice balance guarantee covers (Phase C). A
#: dataset that balances in aggregate but not per slice produces nonsense the
#: first time someone filters, so the guarantee is stated over every
#: (region, species) combination, not just the national total.
SLICE_DIMS = ["region", "species"]


def balance_report(df: pd.DataFrame, by_year: bool = False,
                   dims: list | None = None) -> pd.DataFrame:
    """Per-node inflow/outflow/imbalance.

    Parameters
    ----------
    by_year : bool
        If True, report per (year, node); otherwise aggregate over all years.
    dims : list, optional
        Extra dimension columns (e.g. ``["region", "species"]``) to slice by:
        the report then has one row per (dims..., node) and each slice is
        balanced independently. The columns must exist in ``df``.

    Returns
    -------
    DataFrame with columns
        [node, (year), (dims...), inflow, outflow, imbalance, rel_imbalance, kind]
    where ``rel_imbalance = imbalance / inflow`` and ``kind`` is one of
    ``source`` / ``sink`` / ``process``.
    """
    dims = list(dims or [])
    missing = [d for d in dims if d not in df.columns]
    if missing:
        raise KeyError(f"balance_report dims not in dataframe: {missing}")
    keys = (["year"] if by_year else []) + dims

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


def process_imbalances(df: pd.DataFrame, by_year: bool = False,
                       dims: list | None = None) -> pd.DataFrame:
    """Just the process nodes (those expected to balance), worst first."""
    rep = balance_report(df, by_year=by_year, dims=dims)
    return rep[rep["kind"] == "process"].reset_index(drop=True)


def assert_balanced(df: pd.DataFrame, rel_tol: float = 0.01, abs_tol: float = 1.0,
                    nodes: list | None = None, dims: list | None = None) -> None:
    """Raise AssertionError if any process node (slice) is out of balance.

    A node passes if ``|imbalance| <= abs_tol`` or
    ``|rel_imbalance| <= rel_tol``. With ``dims``, each (dims..., node) slice
    must pass independently.
    """
    dims = list(dims or [])
    rep = process_imbalances(df, dims=dims)
    if nodes is not None:
        rep = rep[rep["node"].isin(nodes)]
    bad = rep[(rep["imbalance"].abs() > abs_tol)
              & (rep["rel_imbalance"].abs() > rel_tol)]
    if not bad.empty:
        lines = "\n".join(
            "  " + " / ".join(str(getattr(r, d)) for d in dims + ["node"])
            + f": in={r.inflow:,.0f} out={r.outflow:,.0f} ({r.rel_imbalance:+.1%})"
            for r in bad.itertuples()
        )
        raise AssertionError(
            f"{len(bad)} node{' slice' if dims else ''}(s) out of balance:\n{lines}"
        )


def window(df: pd.DataFrame, min_year: int = C.MIN_YEAR,
           max_year: int = C.MAX_YEAR) -> pd.DataFrame:
    """Restrict to the analysis window the balance guarantee is stated over."""
    return df[(df["year"] >= min_year) & (df["year"] <= max_year)]


def assert_window_balanced(
    df: pd.DataFrame,
    rel_tol: float = 0.01,
    min_year: int = C.MIN_YEAR,
    max_year: int = C.MAX_YEAR,
    nodes: list | None = None,
) -> None:
    """The project's actual balance guarantee, in one call.

    Every node in :data:`PROCESS_NODES` conserves volume within ``rel_tol`` over
    the paper window. Note the two qualifiers: asserting balance over *all* years
    or over *all* nodes is a different and much stronger claim that the dataset
    has never made — the source tables' year coverage differs (trade goes back to
    1988, harvest to 2012), so the species pools cannot balance outside the
    window.
    """
    assert_balanced(
        window(df, min_year, max_year),
        rel_tol=rel_tol,
        nodes=list(PROCESS_NODES) if nodes is None else nodes,
    )


#: Absolute floor for the per-slice balance check, m³. Same rationale as
#: ``diff.DEFAULT_MIN_VOLUME``: on a diagram measured in Mm³, a sub-1,000 m³
#: imbalance on a thin slice is numerical noise, not a defect — per-year node
#: imbalances that cancel over the window stop cancelling exactly once they
#: are weighted by a species mix that drifts year to year. Node *totals* keep
#: their tight 1 m³ floor; this floor applies to slices only.
SLICE_ABS_TOL = 1_000.0


def assert_slices_balanced(
    df: pd.DataFrame,
    rel_tol: float = 0.01,
    abs_tol: float = SLICE_ABS_TOL,
    min_year: int = C.MIN_YEAR,
    max_year: int = C.MAX_YEAR,
    dims: list | None = None,
) -> None:
    """Per-slice form of the balance guarantee (Phase C).

    Every (region, species) slice of every :data:`PROCESS_NODES` node conserves
    volume within ``rel_tol`` (or :data:`SLICE_ABS_TOL` m³ absolute) over the
    paper window — not just the national aggregate. The species split is the
    carbon model's fixed-point propagation, whose defining property is that a
    node's outflow mix equals its inflow mix, so a slice's relative imbalance
    tracks the node's aggregate one; this assertion is what keeps that
    property true as transforms change.

    Skips silently on a pre-Phase-C frame (no dimension columns) so callers can
    run both guarantees unconditionally.
    """
    dims = [d for d in (dims if dims is not None else SLICE_DIMS) if d in df.columns]
    if not dims:
        return
    assert_balanced(
        window(df, min_year, max_year),
        rel_tol=rel_tol,
        abs_tol=abs_tol,
        nodes=list(PROCESS_NODES),
        dims=dims,
    )
