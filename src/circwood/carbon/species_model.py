"""Species-accurate embodied-carbon model.

Instead of collapsing every processed flow to a single species, this tracks the
spruce / pine / non-coniferous composition of wood as it flows through the
mixing nodes (Sawmills, Pulpmills, ...) and applies a species-weighted carbon
density.

Carbon of a flow = ``volume × Σ_species share_species × density(species, state)``.

How the species share of a flow is determined:

* Flows leaving ``Harvested wood`` carry the target pool's species (Spruce/Pine/
  Non-coniferous); the harvest by-products (Bark, Residues) carry the year's
  overall harvest mix.
* Flows leaving a **fixed** node (the species pools and imports) carry that
  node's configured shares.
* Flows leaving a **propagate** node (the mills, use phases, inventories) carry
  the species mix of everything flowing into that node — solved to a fixed
  point so inventory cycles converge.

Everything is configured in two researcher-editable CSVs (see
``config/carbon_densities.csv`` and ``config/carbon_nodes.csv``); nothing here is
hard-coded except the species names and the algorithm itself.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from circwood import config

SPECIES = ("spruce", "pine", "non-coniferous")
STANDING_WOOD = "Harvested wood"
# Pool node label -> its single species.
POOL_SPECIES = {"Spruce": "spruce", "Pine": "pine", "Non-coniferous": "non-coniferous"}

_TOL = 1e-9
_MAX_ITER = 200


@dataclass(frozen=True)
class NodeRule:
    state: str          # 'raw' or 'dry' — moisture state of wood leaving the node
    mode: str           # 'fixed' or 'propagate'
    shares: dict        # species->fraction (only used when mode == 'fixed')


@dataclass
class CarbonModel:
    """Loaded, validated carbon configuration."""

    densities: dict           # (species, state) -> kg C / m3
    nodes: dict               # node label -> NodeRule

    def density(self, species: str, state: str) -> float:
        return self.densities[(species, state)]


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
def load_model(
    densities_file=None, nodes_file=None
) -> CarbonModel:
    """Load and validate the carbon model from the two config CSVs."""
    dens_df = pd.read_csv(densities_file or config.CARBON_DENSITIES_FILE)
    densities = {
        (str(r.species).strip().lower(), str(r.state).strip().lower()): float(r.kg_c_per_m3)
        for r in dens_df.itertuples()
    }

    nodes_df = pd.read_csv(nodes_file or config.CARBON_NODES_FILE)
    nodes: dict[str, NodeRule] = {}
    for r in nodes_df.itertuples():
        node = str(r.node).strip()
        state = str(r.state).strip().lower()
        mode = str(r.mix_mode).strip().lower()
        shares = {}
        if mode == "fixed":
            raw = {
                "spruce": _num(r.spruce_share),
                "pine": _num(r.pine_share),
                "non-coniferous": _num(r.nonconif_share),
            }
            total = sum(raw.values())
            if total <= 0:
                raise ValueError(f"Fixed node '{node}' has zero species shares.")
            shares = {k: v / total for k, v in raw.items()}
        elif mode != "propagate":
            raise ValueError(f"Node '{node}': mix_mode must be 'fixed' or 'propagate'.")
        nodes[node] = NodeRule(state=state, mode=mode, shares=shares)

    # Validate density coverage for every state referenced by a node.
    for node, rule in nodes.items():
        for sp in SPECIES:
            if (sp, rule.state) not in densities:
                raise ValueError(
                    f"No density for ({sp}, {rule.state}) needed by node '{node}'."
                )
    return CarbonModel(densities=densities, nodes=nodes)


def _num(x) -> float:
    return 0.0 if pd.isna(x) else float(x)


# ---------------------------------------------------------------------------
# Species propagation
# ---------------------------------------------------------------------------
def _harvest_mix_for_year(edges: pd.DataFrame) -> dict:
    """Species split of the year's harvest, from Harvested wood -> pool volumes."""
    sub = edges[(edges["source"] == STANDING_WOOD) & (edges["target"].isin(POOL_SPECIES))]
    acc = {sp: 0.0 for sp in SPECIES}
    for r in sub.itertuples():
        acc[POOL_SPECIES[r.target]] += float(r.volume)
    total = sum(acc.values())
    if total <= 0:
        return {"spruce": 1.0, "pine": 0.0, "non-coniferous": 0.0}
    return {k: v / total for k, v in acc.items()}


def _edge_species(source, target, model, mix_cache, harvest) -> dict:
    """Species composition of the wood carried by a single flow."""
    if source == STANDING_WOOD:
        if target in POOL_SPECIES:
            return {sp: (1.0 if sp == POOL_SPECIES[target] else 0.0) for sp in SPECIES}
        return harvest
    rule = model.nodes.get(source)
    if rule is not None and rule.mode == "fixed":
        return rule.shares
    # propagate node (or unknown source): use current estimate
    return mix_cache.get(source, {"spruce": 1.0, "pine": 0.0, "non-coniferous": 0.0})


def species_mix(df: pd.DataFrame, model: CarbonModel | None = None) -> pd.DataFrame:
    """Return species composition per (year, node).

    Columns: ['year', 'node', 'spruce', 'pine', 'non-coniferous'].

    Years are solved in ascending order and each year's fixed point seeds the
    next. That seeding is what a propagate node with **no inflow this year**
    keeps — which is exactly the inventory-withdrawal case: wood flowing out of
    ``Timber inventory`` in a withdrawal year was deposited in earlier years,
    so it carries the mix it went in with (the first year falls back to the
    harvest mix). The former ⅓/⅓/⅓ initialisation counted withdrawn wood as
    one-third non-coniferous against a real share of a few percent, which the
    per-slice balance fence (Phase C) exposed as a −5.6 % non-coniferous
    imbalance at Planingmills. Nodes with inflow converge to the same fixed
    point regardless of the seed.
    """
    model = model or load_model()
    propagate_nodes = [n for n, r in model.nodes.items() if r.mode == "propagate"]

    rows = []
    prev_mix: dict = {}
    for year, edges in df.groupby("year"):
        harvest = _harvest_mix_for_year(edges)
        # Pre-group inflows per node for speed.
        inflows = {
            node: list(zip(g["source"], g["volume"]))
            for node, g in edges.groupby("target")
        }
        mix = {n: dict(prev_mix.get(n, harvest)) for n in propagate_nodes}

        for _ in range(_MAX_ITER):
            max_delta = 0.0
            for node in propagate_nodes:
                acc = {sp: 0.0 for sp in SPECIES}
                total = 0.0
                for src, vol in inflows.get(node, []):
                    es = _edge_species(src, node, model, mix, harvest)
                    v = float(vol)
                    for sp in SPECIES:
                        acc[sp] += es.get(sp, 0.0) * v
                    total += v
                if total > 0:
                    new = {sp: acc[sp] / total for sp in SPECIES}
                    max_delta = max(max_delta, max(abs(new[sp] - mix[node][sp]) for sp in SPECIES))
                    mix[node] = new
            if max_delta < _TOL:
                break
        prev_mix = mix

        # Emit mixes for all configured nodes for this year.
        for node, rule in model.nodes.items():
            m = rule.shares if rule.mode == "fixed" else mix.get(node)
            if m is None:
                continue
            rows.append({"year": year, "node": node, **{sp: m[sp] for sp in SPECIES}})

    return pd.DataFrame(rows)


def flow_species_shares(df: pd.DataFrame, model: CarbonModel | None = None) -> pd.DataFrame:
    """Species composition of every flow row, index-aligned with ``df``.

    Returns a frame with columns ``['spruce', 'pine', 'non-coniferous']`` and
    the same index as ``df``; each row sums to 1. This is the propagation the
    carbon overlay has always used, exposed per-flow so the schema can carry
    species as a real dimension (Phase C) instead of recomputing the mix inside
    the carbon step only.
    """
    model = model or load_model()

    mixframe = species_mix(df, model)
    mix_by_year: dict = {}
    for _, mr in mixframe.iterrows():
        mix_by_year.setdefault(mr["year"], {})[mr["node"]] = {sp: mr[sp] for sp in SPECIES}

    shares = {sp: pd.Series(0.0, index=df.index) for sp in SPECIES}
    for year, edges in df.groupby("year"):
        harvest = _harvest_mix_for_year(edges)
        cache = mix_by_year.get(year, {})
        for r in edges.itertuples():
            es = _edge_species(r.source, r.target, model, cache, harvest)
            for sp in SPECIES:
                shares[sp].at[r.Index] = es.get(sp, 0.0)
    return pd.DataFrame(shares)


# ---------------------------------------------------------------------------
# Carbon computation
# ---------------------------------------------------------------------------
def _state_for_row(source: str, target: str, model: CarbonModel) -> str:
    """Moisture state of the wood on an edge (from the emitting node's rule)."""
    if source == STANDING_WOOD:
        rule = model.nodes.get(target)
        return rule.state if rule else "raw"
    rule = model.nodes.get(source)
    return rule.state if rule else "dry"


def add_carbon(df: pd.DataFrame, model: CarbonModel | None = None,
               out_col: str = "carbon") -> pd.DataFrame:
    """Return a copy of ``df`` with a species-accurate carbon column (kg C)."""
    model = model or load_model()
    out = df.copy()

    # A Phase C frame already carries one concrete species per row (the same
    # propagation, applied when the dataset was widened), so the density lookup
    # is direct. Per-edge totals are identical to the mixed-density path —
    # Σ_sp share·vol·ρ(sp) == vol·Σ_sp share·ρ(sp) — but each row's carbon now
    # reflects its own species rather than the source node's average.
    if "species" in out.columns and out["species"].isin(SPECIES).all():
        out[out_col] = [
            float(r.volume)
            * model.density(r.species, _state_for_row(r.source, r.target, model))
            for r in out.itertuples()
        ]
        return out

    # Solve species mixes once, indexed by (year, node).
    mixframe = species_mix(out, model)
    mix_by_year: dict = {}
    for _, mr in mixframe.iterrows():
        mix_by_year.setdefault(mr["year"], {})[mr["node"]] = {sp: mr[sp] for sp in SPECIES}

    carbon = pd.Series(0.0, index=out.index)
    for year, edges in out.groupby("year"):
        harvest = _harvest_mix_for_year(edges)
        cache = mix_by_year.get(year, {})
        for r in edges.itertuples():
            es = _edge_species(r.source, r.target, model, cache, harvest)
            state = _state_for_row(r.source, r.target, model)
            dens = sum(es.get(sp, 0.0) * model.density(sp, state) for sp in SPECIES)
            carbon.at[r.Index] = float(r.volume) * dens

    out[out_col] = carbon
    return out
