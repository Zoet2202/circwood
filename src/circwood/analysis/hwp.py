"""Empirical harvested-wood-product fraction β(year, species) — ROADMAP Phase F.

Forest-rotation economics assumes a fixed harvested-wood-product fraction β;
``constants.LITERATURE_BETA`` = 0.55 is the common assumed β in the rotation
literature (ROADMAP Phase F). Which product pool that value denotes is
unverified here (papers/ not checked). This module measures β off the
mass-balanced flow table and exports ``data/processed/beta_series.csv`` for an external (R) rotation
model. The rotation model itself is deliberately *not* implemented here.

Definition (production basis)
-----------------------------
``beta(y, k)`` = the share of the carbon in year *y*'s domestic roundwood
removals of species *k* that is absorbed at ``Long-term USE`` in year *y*.

* **Denominator — roundwood removals**: the ``Harvested wood → Spruce / Pine /
  Non-coniferous`` edges (SSB 03795 commercial harvest + the 11181 firewood
  estimate). ``Bark`` and ``Residues from harvest`` are excluded because both
  are fixed 10 % coefficients (``BARK_PERCENTAGE``,
  ``HARVEST_RESIDUE_PERCENTAGE``), not measurements; they are reported as
  ``bark_ratio`` / ``residue_ratio`` on the same denominator. The 10 % applies
  to commercial harvest only (SSB 03795 for Bark, SSB 07410 for Residues), not
  to the 11181 firewood estimate, so both ratios sit below 0.10 here.
* **Tracing — absorbing Markov chain, per year**. Volumes are aggregated over
  every region/species/assortment row of the year. The species pools and the
  process nodes (Sawmills, Planingmills, Boardmills, Impregnation, Pulpmills,
  Wood Product processing & Logistics, Firewood) are transient; every sink is
  absorbing. A transient node *i* sends ``F_ij / T_i`` to *j*, with
  ``T_i = max(inflow_i, outflow_i)``:

  - inflow > outflow: the unrouted residual goes to a named ``closure_gap``
    sink for that node (a data defect, gated by ``BETA_CLOSURE_TOL``);
  - outflow > inflow: the extra output is attributed to an unnamed exogenous
    source and does not dilute the domestic trace. Every real exogenous input
    is an explicit edge, so this too is an imbalance: a pool or process node
    whose excess output exceeds ``BETA_EXCESS_OUT_TOL`` of its outflow raises.

  ``B = (I − Q)⁻¹ R`` gives, for each pool, the fraction of its harvest that
  ends in each sink. Exogenous entries (``Import *``, inventory withdrawals,
  ``Net export sawn timber`` in its net-import years, ``End-of-Life``) are
  never traced from: under proportional mixing a node's outflow *fractions*
  do not depend on who fed it, so imports change nothing in the domestic
  trace (they only enter through ``T_i``). ``Long-term USE`` is absorbing; its
  ``→ End-of-Life`` edge is stock outflow (SSB 10513 waste from the standing
  stock of products), not this year's inflow, and is ignored.
* **Inventories and net-export nodes** are signed: in a year where one only
  receives it is a sink (``stock_change_share`` / an export share); where it
  only emits it is an exogenous source; where it does both it is treated as a
  transient pass-through whose net deposit goes to its own bucket.
* **Carbon basis** (Phase G): the chain is solved on *carbon* edge weights,
  recomputed from the frame's volumes with the conserving carbon model
  (:func:`circwood.carbon.species_model.add_carbon`): every species enters at
  its raw density, wood keeps its carbon through every node, and the water
  edge (``Sawmills → Shrinkage``) carries none. The fates are therefore
  fractions of *harvest-entry carbon* and sum to exactly 1 per (year,
  species); ``shrinkage_share`` is 0 by construction and is kept only for
  schema stability. ``species = "all"`` is the harvest-carbon-weighted mean of
  the three. Any ``carbon`` column on the input is ignored and recomputed, so
  β never depends on the vintage of someone else's carbon model.
  ``basis="volume"`` reproduces the pre-Phase-G trace (volume weights, water
  as a fate) and is kept as the independent cross-check against the W2
  reference tracer. ``beta_literal`` is the consumption ratio
  ``Σ carbon(* → Long-term USE, species=k) / Σ carbon(Harvested wood → pool k)``
  on the same conserved carbon; it counts imported wood in the numerator and
  labels imports by the fixed shares in ``config/carbon_nodes.csv``.

Fate buckets (:data:`FATE_BUCKETS`) partition the harvest: ``beta`` (Long-term
USE), ``pulp_share`` (Short-term USE, fed only by Pulpmills), ``energy_share``,
the export shares (roundwood / sawn / firewood), ``loss_share``,
``shrinkage_share``, ``stock_change_share`` and ``closure_gap``. ``other_share``
is everything but beta, pulp and energy.

Assumptions and limits
----------------------
* **Proportional mixing**: wood leaving a node has the fate distribution of the
  node, whatever its origin — the same assumption the species carbon model
  makes (:mod:`circwood.carbon.species_model`). Species differences therefore
  arise only at the pools (how each pool splits between sawmills, pulpmills,
  board mills, firewood and roundwood export).
* **β is only partly empirical.** Pool routing comes from SSB 07410 / 03795 /
  11181 / 08801 and sawn production from Treindustrien, but every downstream
  split is a fixed coefficient in ``constants.py`` (SAWDUST, CELLULOSE,
  SCRAPS, SHRINKAGE, BOARDMILL_LOSS, PULP_*, WPP_LONG_TERM).
  :mod:`circwood.analysis.sensitivity` varies each of them one at a time over
  the ranges in ``config/coefficient_ranges.csv``; ``ci_low``/``ci_high`` are
  the root-sum-square envelope of those deviations (``ci_method`` says so),
  read from the committed ``beta_sensitivity.csv`` at export time.
* **Shrinkage** (``SHRINKAGE``, water loss at the saw) carries no carbon
  since Phase G, so on the carbon basis its bucket is exactly 0; on the
  volume basis it is a volume fate of about 2 %.
* ``hwp_ipcc_share = beta + pulp_share + export_sawn_share`` is a **proxy**
  for the IPCC production-approach HWP inflow (sawnwood, panels, paper), not
  an IPCC-compliant estimate; whether exported roundwood should count is left
  open (``export_roundwood_share`` is kept separate).

Divergence from the literature value
------------------------------------
Measured β is about 0.17–0.23 over 2012–2023 at the dataset vintage this was
written against. The rest of the harvest carbon is reported in the fate
columns; at that vintage roughly a quarter each goes to pulp/paper, to energy
(near-immediate oxidation) and to roundwood export. The exact divergence is **computed** at export time from the exported rows and
written into the CSV header; it is never hand-typed.

Everything raises rather than returning partial data: unknown node, a null
source/target/year/volume, negative volume, a ``Harvested wood`` edge to
anything but a pool, Bark or Residues, a pool with zero harvest, no
Long-term USE inflow, a singular ``I − Q`` (a closed cycle with no exit), a
missing year, a closure gap above ``BETA_CLOSURE_TOL`` or an output excess
above ``BETA_EXCESS_OUT_TOL`` (both messages name the year and the node).
Neither gate can be switched off for the series.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from circwood import config
from circwood import constants as C
from circwood.carbon.species_model import (
    POOL_SPECIES,
    SPECIES,
    STANDING_WOOD,
    CarbonModel,
    add_carbon,
    flow_species_shares,
    load_model,
)
from circwood.manifest import read_manifest

# ---------------------------------------------------------------------------
# Node roles
# ---------------------------------------------------------------------------
LTUSE = "Long-term USE"
STUSE = "Short-term USE"
WPP = "Wood Product processing & Logistics"

#: Process nodes traced through (transient states besides the species pools).
PROCESSORS = (
    "Sawmills", "Planingmills", "Boardmills", "Impregnation", "Pulpmills",
    WPP, "Firewood",
)

#: Nodes that hold a signed residual: a sink when they only receive, an
#: exogenous source when they only emit, a pass-through when they do both.
INVENTORY_NODES = ("Timber inventory", "Planed timber inventory", "Impregnated inventory")
NET_EXPORT_NODES = ("Net export skurlast", "Net export massevirke", "Net export sawn timber")
BIDIRECTIONAL = INVENTORY_NODES + NET_EXPORT_NODES

#: Nodes whose output is never traced: imports, and End-of-Life (stock outflow
#: of Long-term USE, not this year's harvest).
EXOGENOUS_SOURCES = ("Import planed wood", "Import Boards", "Import Firewood", "End-of-Life")

#: Harvest by-products: outside the denominator, reported as ratios.
HARVEST_BYPRODUCTS = {"Bark": "bark_ratio", "Residues from harvest": "residue_ratio"}

#: The fate buckets, in export order. They partition each species' harvest.
FATE_BUCKETS: tuple[str, ...] = (
    "beta",
    "pulp_share",
    "energy_share",
    "export_roundwood_share",
    "export_sawn_share",
    "export_firewood_share",
    "loss_share",
    "shrinkage_share",
    "stock_change_share",
    "closure_gap",
)
#: The buckets that make up ``other_share``.
OTHER_BUCKETS: tuple[str, ...] = FATE_BUCKETS[3:]

_SINK_BUCKET = {
    LTUSE: "beta",
    STUSE: "pulp_share",
    "Energy": "energy_share",
    "Net export skurlast": "export_roundwood_share",
    "Net export massevirke": "export_roundwood_share",
    "Net export sawn timber": "export_sawn_share",
    "Export planed wood": "export_sawn_share",
    "Export Firewood": "export_firewood_share",
    "Loss Pulpmills": "loss_share",
    "Loss Boardmills": "loss_share",
    "Shrinkage": "shrinkage_share",
    "Timber inventory": "stock_change_share",
    "Planed timber inventory": "stock_change_share",
    "Impregnated inventory": "stock_change_share",
}

_KNOWN_NODES = (
    {STANDING_WOOD, *POOL_SPECIES, *PROCESSORS, *EXOGENOUS_SOURCES, *HARVEST_BYPRODUCTS}
    | set(_SINK_BUCKET)
)

#: Species values in the export, in row order.
SPECIES_ORDER = (*SPECIES, "all")

#: Columns of the export: the ROADMAP's interface first, in ROADMAP order, then
#: additive detail columns.
ROADMAP_COLUMNS = (
    "year", "species", "beta", "pulp_share", "energy_share", "other_share",
    "volume_basis", "ci_low", "ci_high",
)
EXTRA_COLUMNS = (
    "beta_literal",
    "export_roundwood_share", "export_sawn_share", "export_firewood_share",
    "loss_share", "shrinkage_share", "stock_change_share", "closure_gap",
    "hwp_ipcc_share", "bark_ratio", "residue_ratio",
    "harvest_carbon_kgc", "domestic_share_of_ltuse", "ci_method",
)
EXPORT_COLUMNS = ROADMAP_COLUMNS + EXTRA_COLUMNS

#: ``ci_method`` when no sensitivity results are available to the export.
CI_METHOD = "none (beta_sensitivity.csv not available)"

#: ``I − Q`` with a condition number above this is treated as singular.
_SINGULAR_COND = 1e12


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
def classify_sink(node: str) -> str:
    """Map an absorbing node to its fate bucket.

    Raises ``ValueError`` on any node not in the closed map — a new node is a
    modelling change and wants a human, never a silent "other".
    """
    try:
        return _SINK_BUCKET[node]
    except KeyError:
        raise ValueError(
            f"Unknown sink node {node!r}: not mapped to a beta fate bucket. "
            "A new node is a modelling change; add it to hwp._SINK_BUCKET deliberately."
        ) from None


# ---------------------------------------------------------------------------
# Tracing
# ---------------------------------------------------------------------------
def _check_volumes(df: pd.DataFrame, column: str = "volume") -> None:
    """Refuse rows the trace would otherwise drop or misread silently."""
    for col in ("source", "target", "year"):
        if col in df.columns and df[col].isna().any():
            raise ValueError(f"{int(df[col].isna().sum())} flow row(s) with no {col}.")
    if column not in df.columns:
        raise ValueError(f"Flow table has no {column!r} column to trace.")
    vol = pd.to_numeric(df[column], errors="raise")
    if not np.isfinite(vol.to_numpy(dtype=float)).all():
        raise ValueError(f"Non-finite {column} in flow table.")
    neg = df[vol < 0]
    if not neg.empty:
        r = neg.iloc[0]
        raise ValueError(
            f"{len(neg)} negative {column}(s) in flow table, e.g. "
            f"{r['source']} -> {r['target']} = {r[column]}"
            + (f" ({r['year']})" if "year" in neg.columns else "")
        )


#: Edge weight the chain is solved on: conserved carbon (the export) or volume
#: (the pre-Phase-G method, kept as the independent cross-check).
BASES = ("carbon", "volume")
_UNIT = {"carbon": "kg C", "volume": "m3"}


def _year_label(edges: pd.DataFrame) -> str:
    if "year" in edges.columns and edges["year"].nunique() == 1:
        return f"year {int(edges['year'].iloc[0])}: "
    return ""


def absorption_fractions(
    edges: pd.DataFrame,
    *,
    closure_tol: float | None = None,
    excess_out_tol: float | None = None,
    weights: str = "volume",
) -> pd.DataFrame:
    """Trace one year's species pools to their sinks.

    ``edges`` has columns ``source``, ``target`` and the ``weights`` column —
    ``"volume"`` (m³) or ``"carbon"`` (kg C, the Phase G export basis); any
    further columns are summed over. Returns a long frame with columns
    ``pool, sink, bucket, fraction`` — for each pool present, the fraction of
    its harvest absorbed at each sink. Closure-gap sinks are labelled
    ``"closure gap at <node>"``; a two-way inventory's net deposit is labelled
    with the inventory's own name.

    Raises ``ValueError`` on a null or negative volume, a null node, an
    unknown node, a ``Harvested wood`` edge to anything but a pool or a harvest
    by-product, a sink with outflow, a singular ``I − Q``; when
    ``closure_tol`` is given, on any pool whose closure gap exceeds it (naming
    the node with the largest gap); and when ``excess_out_tol`` is given, on any
    pool or process node whose outflow exceeds its inflow by more than that
    share of its outflow.
    """
    if weights not in BASES:
        raise ValueError(f"weights must be one of {BASES}, got {weights!r}.")
    _check_volumes(edges, weights)
    unit = _UNIT[weights]
    label = _year_label(edges)
    E = edges.groupby(["source", "target"])[weights].sum()
    E = E[E > 0]

    nodes = set(E.index.get_level_values(0)) | set(E.index.get_level_values(1))
    unknown = sorted(nodes - _KNOWN_NODES)
    if unknown:
        raise ValueError(
            f"{label}unknown node(s) {unknown}: not classified for the beta trace. "
            "A new node is a modelling change and wants a human."
        )

    inflow = E.groupby(level="target").sum()
    outflow = E.groupby(level="source").sum()

    def _in(n: str) -> float:
        return float(inflow.get(n, 0.0))

    def _out(n: str) -> float:
        return float(outflow.get(n, 0.0))

    transient = [n for n in (*POOL_SPECIES, *PROCESSORS) if n in nodes]
    transient += [n for n in BIDIRECTIONAL if _in(n) > 0 and _out(n) > 0]
    pools = [p for p in POOL_SPECIES if p in nodes]
    if not pools:
        raise ValueError(f"{label}no species pool (Spruce/Pine/Non-coniferous) in the flows.")
    ti = {n: i for i, n in enumerate(transient)}

    sink_cols: dict[str, str] = {}     # sink label -> bucket, in insertion order
    q_entries: list[tuple[int, int, float]] = []
    r_entries: list[tuple[int, str, float]] = []

    for (src, tgt), vol in E.items():
        if src == STANDING_WOOD:
            if tgt not in POOL_SPECIES and tgt not in HARVEST_BYPRODUCTS:
                raise ValueError(
                    f"{label}harvest edge {src} -> {tgt!r} bypasses the species pools; "
                    "the beta denominator has no rule for it."
                )
            continue
        if src not in ti:
            # Not traced. Sinks may not emit, except the documented cases.
            if src in _SINK_BUCKET and src not in BIDIRECTIONAL and src != LTUSE:
                raise ValueError(
                    f"{label}sink node {src!r} has an outflow ({src} -> {tgt}); "
                    "the beta trace has no rule for it."
                )
            continue
        T = max(_in(src), _out(src))
        if tgt in ti:
            q_entries.append((ti[src], ti[tgt], vol / T))
        else:
            bucket = classify_sink(tgt)
            sink_cols.setdefault(tgt, bucket)
            r_entries.append((ti[src], tgt, vol / T))

    for n in transient:
        surplus = _in(n) - _out(n)
        if surplus > 0:
            T = max(_in(n), _out(n))
            if n in BIDIRECTIONAL:
                sink, bucket = n, classify_sink(n)       # net deposit
            else:
                sink, bucket = f"closure gap at {n}", "closure_gap"
            sink_cols.setdefault(sink, bucket)
            r_entries.append((ti[n], sink, surplus / T))

    sinks = list(sink_cols)
    si = {s: j for j, s in enumerate(sinks)}
    nt = len(transient)
    Q = np.zeros((nt, nt))
    R = np.zeros((nt, len(sinks)))
    for i, j, v in q_entries:
        Q[i, j] += v
    for i, s, v in r_entries:
        R[i, si[s]] += v

    M = np.eye(nt) - Q
    cond = np.linalg.cond(M)
    if not np.isfinite(cond) or cond > _SINGULAR_COND:
        raise ValueError(
            f"{label}I - Q is singular (condition number {cond:.3g}): a closed cycle "
            "among process nodes has no exit to any sink."
        )
    B = np.linalg.solve(M, R)

    rows = []
    for p in pools:
        for s in sinks:
            rows.append((p, s, sink_cols[s], float(B[ti[p], si[s]])))
    out = pd.DataFrame(rows, columns=["pool", "sink", "bucket", "fraction"])

    for p in pools:
        total = out.loc[out["pool"] == p, "fraction"].sum()
        if not np.isclose(total, 1.0, atol=1e-9):
            raise ValueError(f"{label}fates of {p} sum to {total:.12f}, not 1.")

    if closure_tol is not None:
        gaps = out[out["bucket"] == "closure_gap"]
        for p in pools:
            g = gaps[gaps["pool"] == p]
            if g["fraction"].sum() > closure_tol:
                worst = g.loc[g["fraction"].idxmax()]
                node = worst["sink"].removeprefix("closure gap at ")
                raise ValueError(
                    f"{label}closure gap {g['fraction'].sum():.4f} of {p} harvest exceeds "
                    f"BETA_CLOSURE_TOL {closure_tol}; largest unrouted inflow at node "
                    f"{node!r} (in {_in(node):,.0f} {unit}, out {_out(node):,.0f} {unit}). "
                    "Refusing to return partial beta."
                )

    if excess_out_tol is not None:
        for n in transient:
            if n in BIDIRECTIONAL or _out(n) <= _in(n):
                continue      # a two-way inventory's net withdrawal is a real input
            excess = (_out(n) - _in(n)) / _out(n)
            if excess > excess_out_tol:
                raise ValueError(
                    f"{label}node {n!r} emits {excess:.1%} more than it receives "
                    f"(in {_in(n):,.0f} {unit}, out {_out(n):,.0f} {unit}), above "
                    f"BETA_EXCESS_OUT_TOL {excess_out_tol}: output with no recorded "
                    "input. Refusing to return partial beta."
                )
    return out


# ---------------------------------------------------------------------------
# Literal (roadmap-wording) ratio
# ---------------------------------------------------------------------------
def _with_species_and_carbon(df: pd.DataFrame, model: CarbonModel) -> pd.DataFrame:
    """Give every row one concrete species and a carbon value (kg C).

    Carbon is always recomputed from the volumes with ``model`` — the input's
    own ``carbon`` column, if any, is dropped — so β depends on the volumes
    and the current carbon model only, never on the vintage of a carbon file.
    """
    df = df.drop(columns="carbon", errors="ignore")
    if "species" in df.columns and df["species"].isin(SPECIES).all():
        base = df
    else:
        shares = flow_species_shares(df, model)
        parts = []
        for sp in SPECIES:
            part = df.copy()
            part["species"] = sp
            part["volume"] = df["volume"] * shares[sp]
            parts.append(part[part["volume"] > 0])
        base = pd.concat(parts, ignore_index=True)
    return add_carbon(base, model)


def literal_beta(df: pd.DataFrame, *, model: CarbonModel | None = None) -> pd.DataFrame:
    """The ROADMAP's literal ratio, per (year, species incl. ``"all"``).

    ``Σ carbon(* → Long-term USE, species=k) / Σ carbon(Harvested wood → pool k)``
    from the dataset's own ``carbon`` and ``species`` columns (computed with the
    carbon model if absent). This is a *consumption* ratio: imported wood is in
    the numerator, exported domestic wood is not, the numerator is at dry and
    the denominator at raw density, and imports carry the fixed species shares
    of ``config/carbon_nodes.csv``. Exported as ``beta_literal`` for
    comparison only. Columns: ``year, species, beta_literal``.
    """
    model = model or load_model()
    _check_volumes(df)
    base = _with_species_and_carbon(df.drop(columns="carbon", errors="ignore"), model)
    rows = []
    for year, g in base.groupby("year"):
        lt = g[g["target"] == LTUSE]
        harv = g[(g["source"] == STANDING_WOOD) & g["target"].isin(POOL_SPECIES)]
        for pool, sp in POOL_SPECIES.items():
            den = harv.loc[harv["target"] == pool, "carbon"].sum()
            if not den > 0:
                raise ValueError(
                    f"year {int(year)}: zero harvest for pool {pool!r} "
                    "(Harvested wood -> pool); beta_literal is undefined."
                )
            num = lt.loc[lt["species"] == sp, "carbon"].sum()
            rows.append((int(year), sp, num / den))
        rows.append((int(year), "all", lt["carbon"].sum() / harv["carbon"].sum()))
    return pd.DataFrame(rows, columns=["year", "species", "beta_literal"])


# ---------------------------------------------------------------------------
# The series
# ---------------------------------------------------------------------------
def beta_series(
    df: pd.DataFrame | None = None,
    *,
    min_year: int = C.BETA_MIN_YEAR,
    max_year: int = C.BETA_MAX_YEAR,
    closure_tol: float = C.BETA_CLOSURE_TOL,
    excess_out_tol: float = C.BETA_EXCESS_OUT_TOL,
    model: CarbonModel | None = None,
    basis: str = "carbon",
) -> pd.DataFrame:
    """Production-basis β and the full fate split per (year, species).

    ``df`` defaults to the committed ``data/processed/carbon_data.csv``; only
    its volumes are used — carbon is recomputed with ``model`` (see the
    module docstring). ``basis="carbon"`` (default, the export) traces
    conserved carbon; ``basis="volume"`` traces m³ with water as a fate, the
    pre-Phase-G method kept as the cross-check. Rows are ordered by year, then
    ``spruce, pine, non-coniferous, all``; columns are :data:`EXPORT_COLUMNS`.
    Raises ``ValueError`` — never returns a partial frame — if a year in the
    window is missing, a pool has zero harvest, a year has no Long-term USE
    inflow, or any :func:`absorption_fractions` check fails (closure gap >
    ``closure_tol`` or output excess > ``excess_out_tol`` names the year and
    node). Neither gate can be switched off here: both tolerances must be
    numbers in [0, 1).
    """
    for name, tol in (("closure_tol", closure_tol), ("excess_out_tol", excess_out_tol)):
        if isinstance(tol, bool) or not isinstance(tol, (int, float)) or not 0 <= tol < 1:
            raise ValueError(
                f"{name} must be a number in [0, 1), got {tol!r}; the beta gates "
                "cannot be disabled."
            )
    if basis not in BASES:
        raise ValueError(f"basis must be one of {BASES}, got {basis!r}.")
    model = model or load_model()
    if df is None:
        df = pd.read_csv(config.CARBON_CSV)
    _check_volumes(df)
    if min_year > max_year:
        raise ValueError(f"Empty window: min_year {min_year} > max_year {max_year}.")
    window = df[(df["year"] >= min_year) & (df["year"] <= max_year)]
    missing = sorted(set(range(min_year, max_year + 1)) - set(window["year"].astype(int)))
    if missing:
        raise ValueError(f"No flows for year(s) {missing} in the requested beta window.")

    # One species per row and conserved carbon, from the volumes and the model.
    # Propagated over *every* year of the input before windowing: the species
    # mix and density of an inventory that only withdraws in a year are
    # carried from the years before, so a sub-window run must see them to
    # match the dataset build (which always runs over all years).
    traced_all = _with_species_and_carbon(df, model)
    traced = traced_all[(traced_all["year"] >= min_year) & (traced_all["year"] <= max_year)]
    lit = (literal_beta(traced_all, model=model).set_index(["year", "species"])["beta_literal"])

    rows = []
    for year in range(min_year, max_year + 1):
        g = traced[traced["year"] == year]
        fr = absorption_fractions(
            g, closure_tol=closure_tol, excess_out_tol=excess_out_tol, weights=basis
        )
        hw = g[g["source"] == STANDING_WOOD].groupby("target")["volume"].sum()
        hw_carbon = g[g["source"] == STANDING_WOOD].groupby("target")["carbon"].sum()
        harvest_total = float(sum(hw.get(p, 0.0) for p in POOL_SPECIES))
        ratios = {col: float(hw.get(node, 0.0)) / harvest_total if harvest_total > 0 else np.nan
                  for node, col in HARVEST_BYPRODUCTS.items()}
        lt_inflow = float(g.loc[g["target"] == LTUSE, "volume"].sum())
        if not lt_inflow > 0:
            raise ValueError(
                f"year {year}: no Long-term USE inflow; domestic_share_of_ltuse is undefined."
            )

        year_rows = []
        lt_traced = 0.0
        for pool, sp in POOL_SPECIES.items():
            hv = float(hw.get(pool, 0.0))
            if hv <= 0:
                raise ValueError(
                    f"year {year}: zero harvest for pool {pool!r} "
                    "(Harvested wood -> pool); beta is undefined."
                )
            shares = (fr[fr["pool"] == pool].groupby("bucket")["fraction"].sum()
                      .reindex(list(FATE_BUCKETS), fill_value=0.0))
            lt_traced += shares["beta"] * hv
            year_rows.append({
                "year": year, "species": sp,
                **{b: float(shares[b]) for b in FATE_BUCKETS},
                "volume_basis": hv,
                "harvest_carbon_kgc": float(hw_carbon.get(pool, 0.0)),
                "domestic_share_of_ltuse": np.nan,
            })
        hc = sum(r["harvest_carbon_kgc"] for r in year_rows)
        year_rows.append({
            "year": year, "species": "all",
            **{b: sum(r[b] * r["harvest_carbon_kgc"] for r in year_rows) / hc
               for b in FATE_BUCKETS},
            "volume_basis": sum(r["volume_basis"] for r in year_rows),
            "harvest_carbon_kgc": hc,
            "domestic_share_of_ltuse": lt_traced / lt_inflow,
        })
        for r in year_rows:
            r.update(ratios)
            r["beta_literal"] = float(lit.get((year, r["species"]), np.nan))
        rows += year_rows

    out = pd.DataFrame(rows)
    out["other_share"] = out[list(OTHER_BUCKETS)].sum(axis=1)
    out["hwp_ipcc_share"] = out["beta"] + out["pulp_share"] + out["export_sawn_share"]
    out["ci_low"] = np.nan
    out["ci_high"] = np.nan
    out["ci_method"] = CI_METHOD
    out["year"] = out["year"].astype(int)
    return out[list(EXPORT_COLUMNS)].reset_index(drop=True)



# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
_FORMATS = {
    "volume_basis": "{:.3f}",
    "harvest_carbon_kgc": "{:.1f}",
}
_SHARE_FORMAT = "{:.8f}"


def _csv_cell(text: str) -> str:
    """Quote a text cell the way csv.QUOTE_MINIMAL would (commas, quotes, newlines)."""
    if any(ch in text for ch in ',"\n\r'):
        return '"' + text.replace('"', '""') + '"'
    return text


def _format_frame(frame: pd.DataFrame) -> str:
    lines = [",".join(EXPORT_COLUMNS)]
    for r in frame.itertuples(index=False):
        cells = []
        for col, val in zip(EXPORT_COLUMNS, r):
            if col in ("year",):
                cells.append(str(int(val)))
            elif col in ("species", "ci_method"):
                cells.append(_csv_cell(str(val)))
            elif pd.isna(val):
                cells.append("")
            else:
                cells.append(_FORMATS.get(col, _SHARE_FORMAT).format(float(val)))
        lines.append(",".join(cells))
    return "\n".join(lines) + "\n"


def _header(
    frame: pd.DataFrame,
    manifest_file: Path | None,
    source: str,
    *,
    closure_tol: float,
    excess_out_tol: float,
    basis: str,
    ci_note: str,
) -> list[str]:
    allr = frame[frame["species"] == "all"].set_index("year")
    y0, y1 = int(allr.index.min()), int(allr.index.max())
    mean_b = float(allr["beta"].mean())
    lit = C.LITERATURE_BETA
    paper = allr.loc[(allr.index >= C.MIN_YEAR) & (allr.index <= C.MAX_YEAR)]

    man = read_manifest(manifest_file)
    if man is None:
        vintage = "unknown (no data/processed/manifest.json)"
    else:
        per_table = ", ".join(
            f"{tid} {str(t.get('updated') or 'unknown')[:10]}"
            for tid, t in sorted(man.tables.items())
        )
        vintage = f"data as of {man.data_as_of or 'unknown'} (SSB revision dates: {per_table})"

    h = [
        "Circwood WP4 - empirical harvested-wood-product fraction beta(year, species), "
        "ROADMAP Phase F.",
        "Generated by circwood.analysis.hwp.export_beta_series; do not edit by hand. "
        "Read with pandas.read_csv(path, comment='#') or R read.csv(path, comment.char='#').",
        f"Input: {source}. Years {y0}-{y1}. Source vintage: {vintage}.",
        "Units: beta, *_share, closure_gap, hwp_ipcc_share, bark_ratio, residue_ratio, "
        "beta_literal and domestic_share_of_ltuse are dimensionless fractions; every fate "
        "column is a fraction of harvest carbon. volume_basis in m3; harvest_carbon_kgc in kg C.",
        "Denominator: roundwood removals = Harvested wood -> Spruce/Pine/Non-coniferous "
        "(SSB 03795 commercial harvest + SSB 11181 firewood estimate). Bark and Residues "
        "from harvest are excluded (fixed 10% coefficients, not measurements) and reported "
        "as bark_ratio/residue_ratio on the same denominator. The 10% applies to commercial "
        "harvest only (SSB 03795 for Bark, SSB 07410 for Residues), not to the firewood "
        "estimate, so both ratios are below 0.10 here; every species row shows the national ratio.",
        f"Carbon basis: edge weights are {'conserved carbon (kg C)' if basis == 'carbon' else 'volume (m3)'} "
        "recomputed from the flow volumes with the Phase G carbon model (config/carbon_densities.csv, "
        "config/carbon_nodes.csv): every species enters at its raw density, wood keeps its carbon "
        "through every node and the water edge Sawmills -> Shrinkage carries none. beta + pulp_share "
        "+ energy_share + other_share = 1 per row. beta_literal uses the same conserved carbon.",
        "Method: production basis. Per year, an absorbing Markov chain over the aggregated "
        "flows traces each species pool's harvest to its sinks with proportional mixing; "
        "T_i = max(inflow, outflow); unrouted inflow -> closure_gap; imports, net imports and "
        "one-way inventory withdrawals are exogenous and never traced; an inventory that both "
        "receives and emits in a year is a "
        "pass-through whose net deposit goes to stock_change_share. beta = share absorbed at "
        "Long-term USE; pulp_share = Short-term USE; energy_share = Energy (near-immediate "
        "oxidation); other_share = exports + losses + shrinkage + stock change + closure gap. "
        "species=all is the harvest-carbon-weighted mean.",
        f"Gates applied to this export: a year is refused if any species' closure_gap exceeds "
        f"{closure_tol}, or if a pool or process node emits more than it receives by over "
        f"{excess_out_tol} of its outflow.",
        "Only partly empirical: pool routing is SSB/Treindustrien data, but every downstream "
        "split is a fixed coefficient in constants.py; data/processed/beta_sensitivity.csv "
        "varies each one over config/coefficient_ranges.csv.",
        "shrinkage_share: water leaving the saw carries no carbon, so on the carbon basis this "
        "column is 0 by construction; it is kept for schema stability.",
        "beta_literal = carbon(* -> Long-term USE) / carbon(harvest) from the dataset columns: "
        "a consumption ratio including imported wood; for comparison only.",
        "hwp_ipcc_share = beta + pulp_share + export_sawn_share is a PROXY for the IPCC "
        "production-approach HWP share, not an IPCC estimate. domestic_share_of_ltuse "
        "(species=all only) = traced domestic volume / total Long-term USE inflow.",
        f"ci_low/ci_high: {ci_note}",
        f"Literature: LITERATURE_BETA = {lit:.2f}, the common assumed beta in the rotation "
        "literature (ROADMAP Phase F); which product pool it denotes is unverified (papers/ "
        f"not checked). Mean beta(all) {y0}-{y1} = {mean_b:.4f} "
        f"(min {allr['beta'].min():.4f}, max {allr['beta'].max():.4f}); divergence "
        f"{mean_b - lit:+.4f} absolute ({(mean_b - lit) / lit:+.1%}).",
    ]
    if len(paper):
        pm = float(paper["beta"].mean())
        h.append(
            f"Paper window {int(paper.index.min())}-{int(paper.index.max())}: mean beta(all) "
            f"= {pm:.4f}, divergence {pm - lit:+.4f} ({(pm - lit) / lit:+.1%}); fate means: "
            f"pulp {paper['pulp_share'].mean():.4f}, energy {paper['energy_share'].mean():.4f}, "
            f"roundwood export {paper['export_roundwood_share'].mean():.4f}."
        )
    return ["# " + line for line in h]


def apply_bounds(frame: pd.DataFrame, bounds: pd.DataFrame | None) -> tuple[pd.DataFrame, str]:
    """Fill ``ci_low`` / ``ci_high`` / ``ci_method`` from a bounds frame.

    ``bounds`` has columns ``year, species, ci_low, ci_high, ci_method`` (as
    :func:`circwood.analysis.sensitivity.beta_bounds` produces). Rows of
    ``frame`` with no matching bound keep empty CIs and :data:`CI_METHOD`.
    Returns the filled frame and a one-line note for the header.
    """
    out = frame.copy()
    if bounds is None or bounds.empty:
        return out, (
            "empty; no sensitivity results were available to this export (run "
            "circwood.analysis.sensitivity.export_sensitivity first)."
        )
    b = bounds.set_index(["year", "species"])
    idx = pd.MultiIndex.from_frame(out[["year", "species"]])
    matched = idx.isin(b.index)
    out.loc[matched, "ci_low"] = b["ci_low"].reindex(idx[matched]).to_numpy()
    out.loc[matched, "ci_high"] = b["ci_high"].reindex(idx[matched]).to_numpy()
    out.loc[matched, "ci_method"] = b["ci_method"].reindex(idx[matched]).to_numpy()
    methods = sorted(set(out.loc[matched, "ci_method"]))
    note = (
        f"{int(matched.sum())} of {len(out)} rows carry bounds; ci_method per row. "
        + " ".join(methods)
    )
    if (~matched).any():
        note += f" {int((~matched).sum())} row(s) have no bounds ({CI_METHOD})."
    return out, note


def export_beta_series(
    path: Path | str | None = None,
    *,
    manifest_file: Path | str | None = None,
    bounds: pd.DataFrame | None | str = "auto",
    **kw,
) -> Path:
    """Write :func:`beta_series` to ``path`` (default ``config.BETA_SERIES_CSV``).

    ``#``-prefixed header lines state units, carbon basis, denominator, method,
    the source vintage (from ``data/processed/manifest.json``) and the computed
    divergence from ``LITERATURE_BETA``. Output is deterministic (fixed float
    formats, LF line endings, no timestamps) so a re-run is byte-identical.
    Keyword arguments go to :func:`beta_series`; the gates cannot be disabled
    (``closure_tol=None`` or ``excess_out_tol=None`` raises) and the header
    prints the tolerances actually used.

    ``bounds`` fills ``ci_low``/``ci_high``: ``"auto"`` reads the committed
    ``data/processed/beta_sensitivity.csv`` if it exists (the Phase G
    one-at-a-time sensitivity, see :mod:`circwood.analysis.sensitivity`), a
    frame is used as given, ``None`` leaves the bounds empty.
    """
    path = Path(path) if path is not None else config.BETA_SERIES_CSV
    closure_tol = kw.setdefault("closure_tol", C.BETA_CLOSURE_TOL)
    excess_out_tol = kw.setdefault("excess_out_tol", C.BETA_EXCESS_OUT_TOL)
    if closure_tol is None or excess_out_tol is None:
        raise ValueError(
            "export_beta_series cannot disable the closure or excess-output gate "
            "(closure_tol / excess_out_tol is None)."
        )
    basis = kw.setdefault("basis", "carbon")
    source = "caller-supplied flow table" if kw.get("df") is not None else (
        "data/processed/carbon_data.csv (volumes; carbon recomputed)")
    if kw.get("df") is None:
        kw["df"] = pd.read_csv(config.CARBON_CSV)
    frame = beta_series(**kw)
    if isinstance(bounds, str):
        if bounds != "auto":
            raise ValueError("bounds must be a DataFrame, None or 'auto'.")
        from circwood.analysis.sensitivity import load_bounds

        bounds = load_bounds()
    frame, ci_note = apply_bounds(frame, bounds)
    header = _header(frame, Path(manifest_file) if manifest_file else None, source,
                     closure_tol=closure_tol, excess_out_tol=excess_out_tol,
                     basis=basis, ci_note=ci_note)
    text = "\n".join(header) + "\n" + _format_frame(frame)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


__all__ = [
    "BASES",
    "FATE_BUCKETS",
    "OTHER_BUCKETS",
    "EXPORT_COLUMNS",
    "ROADMAP_COLUMNS",
    "apply_bounds",
    "classify_sink",
    "absorption_fractions",
    "literal_beta",
    "beta_series",
    "export_beta_series",
]
