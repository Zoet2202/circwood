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
* **Carbon basis**: every species enters at one raw density
  (``config/carbon_densities.csv``), so the carbon fraction of species *k*
  equals its traced volume fraction. The fates are therefore fractions of
  *harvest-entry carbon* and sum to exactly 1 per (year, species).
  ``species = "all"`` is the harvest-carbon-weighted mean of the three.
  The dataset's ``carbon`` column is **not** used for β: it switches between
  raw and dry density at process nodes (e.g. Pulpmills: dry chips in, raw out)
  and so is not conserved across a node. ``beta_literal`` does use it, because
  that is its definition (``Σ carbon(* → Long-term USE, species=k) /
  Σ carbon(Harvested wood → pool k)``); it counts imported wood in the
  numerator, mixes dry-density numerator with raw-density denominator, and
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
  SCRAPS, SHRINKAGE, BOARDMILL_LOSS, PULP_*, WPP_LONG_TERM). A sensitivity
  analysis over those belongs in Phase G; until then ``ci_low``/``ci_high``
  are empty and ``ci_method`` says so.
* **Shrinkage** (``SHRINKAGE``, documented as water loss) is its own bucket.
  Water carries no carbon, so this is a moisture/volume artefact; the
  carbon-model fix is logged for Phase G and the rotation model should
  redistribute or ignore it.
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

CI_METHOD = "none (Phase G pending)"

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
def _check_volumes(df: pd.DataFrame) -> None:
    """Refuse rows the trace would otherwise drop or misread silently."""
    for col in ("source", "target", "year"):
        if col in df.columns and df[col].isna().any():
            raise ValueError(f"{int(df[col].isna().sum())} flow row(s) with no {col}.")
    vol = pd.to_numeric(df["volume"], errors="raise")
    if not np.isfinite(vol.to_numpy(dtype=float)).all():
        raise ValueError("Non-finite volume in flow table.")
    neg = df[vol < 0]
    if not neg.empty:
        r = neg.iloc[0]
        raise ValueError(
            f"{len(neg)} negative volume(s) in flow table, e.g. "
            f"{r['source']} -> {r['target']} = {r['volume']}"
            + (f" ({r['year']})" if "year" in neg.columns else "")
        )


def _year_label(edges: pd.DataFrame) -> str:
    if "year" in edges.columns and edges["year"].nunique() == 1:
        return f"year {int(edges['year'].iloc[0])}: "
    return ""


def absorption_fractions(
    edges: pd.DataFrame,
    *,
    closure_tol: float | None = None,
    excess_out_tol: float | None = None,
) -> pd.DataFrame:
    """Trace one year's species pools to their sinks.

    ``edges`` has columns ``source``, ``target``, ``volume`` (any further
    columns are summed over). Returns a long frame with columns
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
    _check_volumes(edges)
    label = _year_label(edges)
    E = edges.groupby(["source", "target"])["volume"].sum()
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
                    f"{node!r} (in {_in(node):,.0f} m3, out {_out(node):,.0f} m3). "
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
                    f"(in {_in(n):,.0f} m3, out {_out(n):,.0f} m3), above "
                    f"BETA_EXCESS_OUT_TOL {excess_out_tol}: output with no recorded "
                    "input. Refusing to return partial beta."
                )
    return out


# ---------------------------------------------------------------------------
# Literal (roadmap-wording) ratio
# ---------------------------------------------------------------------------
def _with_species_and_carbon(df: pd.DataFrame, model: CarbonModel) -> pd.DataFrame:
    """Give every row one concrete species and a carbon value (kg C)."""
    if "species" in df.columns and df["species"].isin(SPECIES).all():
        base = df
    else:
        shares = flow_species_shares(df, model)
        parts = []
        for sp in SPECIES:
            part = df.copy()
            part["species"] = sp
            part["volume"] = df["volume"] * shares[sp]
            part = part.drop(columns="carbon", errors="ignore")
            parts.append(part[part["volume"] > 0])
        base = pd.concat(parts, ignore_index=True)
    if "carbon" not in base.columns:
        base = add_carbon(base, model)
    return base


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
    base = _with_species_and_carbon(df, model)
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
def _pool_density(model: CarbonModel, pool: str) -> float:
    rule = model.nodes.get(pool)
    state = rule.state if rule is not None else "raw"
    return model.density(POOL_SPECIES[pool], state)


def beta_series(
    df: pd.DataFrame | None = None,
    *,
    min_year: int = C.BETA_MIN_YEAR,
    max_year: int = C.BETA_MAX_YEAR,
    closure_tol: float = C.BETA_CLOSURE_TOL,
    excess_out_tol: float = C.BETA_EXCESS_OUT_TOL,
    model: CarbonModel | None = None,
) -> pd.DataFrame:
    """Production-basis β and the full fate split per (year, species).

    ``df`` defaults to the committed ``data/processed/carbon_data.csv``. Rows
    are ordered by year, then ``spruce, pine, non-coniferous, all``; columns
    are :data:`EXPORT_COLUMNS`. Raises ``ValueError`` — never returns a partial
    frame — if a year in the window is missing, a pool has zero harvest, a
    year has no Long-term USE inflow, or any :func:`absorption_fractions`
    check fails (closure gap > ``closure_tol`` or output excess >
    ``excess_out_tol`` names the year and node). Neither gate can be switched
    off here: both tolerances must be numbers in [0, 1).
    """
    for name, tol in (("closure_tol", closure_tol), ("excess_out_tol", excess_out_tol)):
        if isinstance(tol, bool) or not isinstance(tol, (int, float)) or not 0 <= tol < 1:
            raise ValueError(
                f"{name} must be a number in [0, 1), got {tol!r}; the beta gates "
                "cannot be disabled."
            )
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

    lit = literal_beta(window, model=model).set_index(["year", "species"])["beta_literal"]

    rows = []
    for year in range(min_year, max_year + 1):
        g = window[window["year"] == year]
        fr = absorption_fractions(g, closure_tol=closure_tol, excess_out_tol=excess_out_tol)
        hw = g[g["source"] == STANDING_WOOD].groupby("target")["volume"].sum()
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
                "harvest_carbon_kgc": hv * _pool_density(model, pool),
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


def _format_frame(frame: pd.DataFrame) -> str:
    lines = [",".join(EXPORT_COLUMNS)]
    for r in frame.itertuples(index=False):
        cells = []
        for col, val in zip(EXPORT_COLUMNS, r):
            if col in ("year",):
                cells.append(str(int(val)))
            elif col in ("species", "ci_method"):
                cells.append(str(val))
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
    firewood_shift: tuple[float, float],
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
        "Carbon basis: harvest-entry raw density per species (config/carbon_densities.csv) "
        "x traced volume fractions; conserving, so beta + pulp_share + energy_share + "
        "other_share = 1 per row. The dataset carbon column is not used for beta (it switches "
        "raw/dry density at process nodes and is not conserved); beta_literal uses it by definition.",
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
        "split is a fixed coefficient in constants.py (sensitivity: Phase G).",
        "shrinkage_share is a moisture/volume artefact (SHRINKAGE = water loss carries carbon "
        "in the carbon model); the fix is logged for Phase G. Redistribute or ignore it.",
        "beta_literal = carbon(* -> Long-term USE) / carbon(harvest) from the dataset columns: "
        "a consumption ratio including imported wood; for comparison only.",
        "hwp_ipcc_share = beta + pulp_share + export_sawn_share is a PROXY for the IPCC "
        "production-approach HWP share, not an IPCC estimate. domestic_share_of_ltuse "
        "(species=all only) = traced domestic volume / total Long-term USE inflow.",
        "ci_low/ci_high are empty: no uncertainty model exists yet (ci_method).",
        "Known dataset issues (ROADMAP Phase K, not fixed): (1) firewood exports are counted "
        "twice (Firewood -> Energy carries all Firewood inflow and Export Firewood is emitted on "
        "top). The trace attributes the excess output to an exogenous source, so beta, every "
        "non-firewood share and energy_share + export_firewood_share are unaffected; the split "
        "between the two is: if exports came out of the inflow, energy_share would be lower and "
        f"export_firewood_share higher by up to {firewood_shift[0]:.4f} (any species; "
        f"{firewood_shift[1]:.4f} for all) in {y0}-{y1}. (2) End-of-Life volume may be ~1000x "
        "too small (SSB 10513 unit, unconfirmed); beta does not use End-of-Life (stock "
        "outflow of Long-term USE), so it is unaffected either way.",
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


def _firewood_double_count_shift(
    df: pd.DataFrame, frame: pd.DataFrame, closure_tol: float, excess_out_tol: float,
) -> tuple[float, float]:
    """Largest energy_share shift if Export Firewood came out of Firewood's inflow.

    Re-traces each exported year with ``Firewood -> Energy`` reduced by the
    year's ``Export Firewood`` (ROADMAP Phase K defect, one reading of it).
    Returns (max over species rows, max over species=all rows); the same amount
    moves into export_firewood_share. Only reported in the header.
    """
    agg = df.groupby(["source", "target", "year"], as_index=False)["volume"].sum()
    by_species = 0.0
    by_all = 0.0
    for year, g in frame.groupby("year"):
        e = agg[agg["year"] == year].copy()
        exp = e.loc[(e["source"] == "Firewood") & (e["target"] == "Export Firewood"), "volume"].sum()
        energy = (e["source"] == "Firewood") & (e["target"] == "Energy")
        e.loc[energy, "volume"] = (e.loc[energy, "volume"] - exp).clip(lower=0.0)
        fr = absorption_fractions(e, closure_tol=closure_tol, excess_out_tol=excess_out_tol)
        rows = g.set_index("species")
        weighted = 0.0
        for pool, sp in POOL_SPECIES.items():
            corrected = fr.loc[(fr["pool"] == pool) & (fr["bucket"] == "energy_share"),
                               "fraction"].sum()
            shift = float(rows.loc[sp, "energy_share"]) - float(corrected)
            by_species = max(by_species, abs(shift))
            weighted += shift * float(rows.loc[sp, "harvest_carbon_kgc"])
        by_all = max(by_all, abs(weighted / float(rows.loc["all", "harvest_carbon_kgc"])))
    return by_species, by_all


def export_beta_series(
    path: Path | str | None = None,
    *,
    manifest_file: Path | str | None = None,
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
    """
    path = Path(path) if path is not None else config.BETA_SERIES_CSV
    closure_tol = kw.setdefault("closure_tol", C.BETA_CLOSURE_TOL)
    excess_out_tol = kw.setdefault("excess_out_tol", C.BETA_EXCESS_OUT_TOL)
    if closure_tol is None or excess_out_tol is None:
        raise ValueError(
            "export_beta_series cannot disable the closure or excess-output gate "
            "(closure_tol / excess_out_tol is None)."
        )
    source = "caller-supplied flow table" if kw.get("df") is not None else (
        "data/processed/carbon_data.csv")
    if kw.get("df") is None:
        kw["df"] = pd.read_csv(config.CARBON_CSV)
    frame = beta_series(**kw)
    shift = _firewood_double_count_shift(kw["df"], frame, closure_tol, excess_out_tol)
    header = _header(frame, Path(manifest_file) if manifest_file else None, source,
                     closure_tol=closure_tol, excess_out_tol=excess_out_tol,
                     firewood_shift=shift)
    text = "\n".join(header) + "\n" + _format_frame(frame)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


__all__ = [
    "FATE_BUCKETS",
    "OTHER_BUCKETS",
    "EXPORT_COLUMNS",
    "ROADMAP_COLUMNS",
    "classify_sink",
    "absorption_fractions",
    "literal_beta",
    "beta_series",
    "export_beta_series",
]
