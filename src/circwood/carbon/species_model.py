"""Species-accurate, carbon-conserving embodied-carbon model.

Instead of collapsing every processed flow to a single species, this tracks the
spruce / pine / non-coniferous composition of wood as it flows through the
mixing nodes (Sawmills, Pulpmills, ...) and applies a species-weighted carbon
density.

Carbon of a flow = ``volume × Σ_species share_species × ρ(year, source, species)``

where ρ is an *effective* carbon density (kg C per m³ of the volume on that
edge). How the species share and the density of a flow are determined:

* Flows leaving ``Harvested wood`` carry the target pool's species (Spruce/Pine/
  Non-coniferous) at that pool's configured density; the harvest by-products
  (Bark, Residues) carry the year's overall harvest mix.
* Flows leaving a **fixed** node (the species pools, imports, the net-export
  nodes in a net-import year) carry that node's configured shares and the
  density of its configured moisture ``state`` (``config/carbon_densities.csv``).
* Flows leaving a **propagate** node (the mills, use phases, inventories) carry
  the species mix of everything flowing into that node — solved to a fixed
  point so inventory cycles converge — and, since Phase G, a density that
  **conserves carbon through the node**: the carbon that came in, per species,
  divided by the wood volume that goes out. Water that leaves a node as volume
  (the ``Sawmills -> Shrinkage`` edge: 5 % of log volume) carries no carbon,
  so the wood leaving is correspondingly denser. In effect "dry density =
  raw density / (1 − shrinkage)", derived per node and year from the actual
  water edge rather than configured, which is why the ``state`` of a
  propagate node is now documentation only (it is still used as the fallback
  density of an inventory that only withdraws in the first year).

Carbon is therefore conserved at every balanced node: a node's carbon
imbalance equals its wood-volume imbalance, in relative terms, and nothing
else (``validate.assert_carbon_balanced`` fences this). Before Phase G the
density switched between raw and dry at process nodes and the water edge was
valued at dry density, which created carbon at Sawmills (+6.6 %), destroyed
it at Pulpmills (−2.0 %) and put 0.5 Mt C on a water edge over 2012–2022.

Everything is configured in two researcher-editable CSVs (see
``config/carbon_densities.csv`` and ``config/carbon_nodes.csv``); nothing here is
hard-coded except the species names and the algorithm itself. The density
file may carry ``kg_c_per_m3_low`` / ``kg_c_per_m3_high`` columns for the
uncertainty scenarios (``load_model(scenario=...)``); a node row may say
``carries_carbon = 0`` for a sink that is not wood (water).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from circwood import config

SPECIES = ("spruce", "pine", "non-coniferous")
STANDING_WOOD = "Harvested wood"
# Pool node label -> its single species.
POOL_SPECIES = {"Spruce": "spruce", "Pine": "pine", "Non-coniferous": "non-coniferous"}

#: Density scenarios the density CSV may carry (column suffixes).
SCENARIOS = ("central", "low", "high")
_DENSITY_COLUMNS = {
    "central": "kg_c_per_m3",
    "low": "kg_c_per_m3_low",
    "high": "kg_c_per_m3_high",
}

_TOL = 1e-9
_MAX_ITER = 200


@dataclass(frozen=True)
class NodeRule:
    state: str          # 'raw' or 'dry' — moisture state of wood leaving the node
    mode: str           # 'fixed' or 'propagate'
    shares: dict        # species->fraction (only used when mode == 'fixed')
    carries_carbon: bool = True  # False for a water/loss sink: edges into it carry 0 kg C


@dataclass
class CarbonModel:
    """Loaded, validated carbon configuration."""

    densities: dict           # (species, state) -> kg C / m3
    nodes: dict               # node label -> NodeRule
    scenario: str = "central"

    def density(self, species: str, state: str) -> float:
        return self.densities[(species, state)]

    def carries_carbon(self, node: str) -> bool:
        """Whether wood arriving at ``node`` still carries its carbon (default yes)."""
        rule = self.nodes.get(node)
        return True if rule is None else rule.carries_carbon


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
def load_model(
    densities_file=None, nodes_file=None, scenario: str = "central"
) -> CarbonModel:
    """Load and validate the carbon model from the two config CSVs.

    ``scenario`` picks the density column: ``"central"`` (``kg_c_per_m3``) or
    the ``"low"`` / ``"high"`` envelope columns, which must exist in the file
    when asked for. Where both bounds exist they must bracket the central
    value; a file without them still loads for the central scenario.
    """
    if scenario not in SCENARIOS:
        raise ValueError(f"scenario must be one of {SCENARIOS}, got {scenario!r}.")
    dens_df = pd.read_csv(densities_file or config.CARBON_DENSITIES_FILE)
    column = _DENSITY_COLUMNS[scenario]
    if column not in dens_df.columns:
        raise ValueError(
            f"config/carbon_densities.csv has no {column!r} column, so the "
            f"{scenario!r} density scenario is not defined."
        )
    for low_col, high_col in ((_DENSITY_COLUMNS["low"], _DENSITY_COLUMNS["high"]),):
        if low_col in dens_df.columns and high_col in dens_df.columns:
            bad = dens_df[
                (dens_df[low_col] > dens_df["kg_c_per_m3"])
                | (dens_df[high_col] < dens_df["kg_c_per_m3"])
            ]
            if not bad.empty:
                raise ValueError(
                    "carbon_densities.csv: low/high must bracket the central density; "
                    f"offending rows: {bad[['species', 'state']].to_dict('records')}"
                )
    densities = {
        (str(r.species).strip().lower(), str(r.state).strip().lower()): float(getattr(r, column))
        for r in dens_df.itertuples()
    }
    if any(v < 0 for v in densities.values()):
        raise ValueError("carbon_densities.csv: a density is negative.")

    nodes_df = pd.read_csv(nodes_file or config.CARBON_NODES_FILE)
    has_carries = "carries_carbon" in nodes_df.columns
    nodes: dict[str, NodeRule] = {}
    for r in nodes_df.itertuples():
        node = str(r.node).strip()
        state = str(r.state).strip().lower()
        mode = str(r.mix_mode).strip().lower()
        carries = True
        if has_carries and not pd.isna(r.carries_carbon):
            carries = bool(int(r.carries_carbon))
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
        nodes[node] = NodeRule(state=state, mode=mode, shares=shares, carries_carbon=carries)

    # Validate density coverage for every state referenced by a node.
    for node, rule in nodes.items():
        for sp in SPECIES:
            if (sp, rule.state) not in densities:
                raise ValueError(
                    f"No density for ({sp}, {rule.state}) needed by node '{node}'."
                )
    return CarbonModel(densities=densities, nodes=nodes, scenario=scenario)


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
    mix_by_year = _mix_lookup(mixframe)

    shares = {sp: pd.Series(0.0, index=df.index) for sp in SPECIES}
    for year, edges in df.groupby("year"):
        harvest = _harvest_mix_for_year(edges)
        cache = mix_by_year.get(year, {})
        for r in edges.itertuples():
            es = _edge_species(r.source, r.target, model, cache, harvest)
            for sp in SPECIES:
                shares[sp].at[r.Index] = es.get(sp, 0.0)
    return pd.DataFrame(shares)


def _mix_lookup(mixframe: pd.DataFrame) -> dict:
    """``{year: {node: {species: share}}}`` from a ``species_mix`` frame."""
    out: dict = {}
    for _, mr in mixframe.iterrows():
        out.setdefault(mr["year"], {})[mr["node"]] = {sp: mr[sp] for sp in SPECIES}
    return out


# ---------------------------------------------------------------------------
# Carbon: effective densities that conserve carbon through every node
# ---------------------------------------------------------------------------
def _state_for_row(source: str, target: str, model: CarbonModel) -> str:
    """Configured moisture state of the wood on an edge (from the emitting node)."""
    if source == STANDING_WOOD:
        rule = model.nodes.get(target)
        return rule.state if rule else "raw"
    rule = model.nodes.get(source)
    return rule.state if rule else "dry"


def _configured_density(node: str, model: CarbonModel) -> dict:
    """Density per species of wood leaving ``node`` at its configured state."""
    rule = model.nodes.get(node)
    state = rule.state if rule else "dry"
    return {sp: model.density(sp, state) for sp in SPECIES}


def node_densities(df: pd.DataFrame, model: CarbonModel | None = None) -> pd.DataFrame:
    """Effective carbon density per (year, node, species), kg C per m³ leaving the node.

    Columns: ``['year', 'node', 'species', 'kg_c_per_m3']``, one row per
    configured node, species and year in ``df``.

    * **fixed** nodes: the configured density of the node's state (the species
      pools at raw density, the imports at dry density).
    * **propagate** nodes: carbon in, per species, over the wood volume out.
      Per species *s* with inflow share ``m_s``:
      ``ρ_N[s] = (C_in[s] / V_in[s]) / (1 − w)`` where ``w`` is the share of
      the node's inflow volume that leaves as water (edges into nodes with
      ``carries_carbon = 0``, e.g. ``Shrinkage``). With no water edge the wood
      leaves at the density it arrived with; with one it leaves denser, so
      carbon is conserved while volume is lost. A node with no inflow in a
      year (an inventory that only withdraws) keeps the previous year's
      density, and in the first year falls back to its configured state.

    Solved to a fixed point per year (the inventories feed back into the
    mills), years ascending, each year seeding the next — the same scheme
    :func:`species_mix` uses for the shares.
    """
    model = model or load_model()
    propagate_nodes = [n for n, r in model.nodes.items() if r.mode == "propagate"]
    mix_by_year = _mix_lookup(species_mix(df, model))

    rows = []
    prev_rho: dict = {}
    for year, edges in df.groupby("year"):
        harvest = _harvest_mix_for_year(edges)
        cache = mix_by_year.get(year, {})
        inflows = {
            node: list(zip(g["source"], g["volume"]))
            for node, g in edges.groupby("target")
        }
        # Volume leaving each node as water (into non-carrying sinks).
        water_out = {n: 0.0 for n in propagate_nodes}
        for r in edges.itertuples():
            if r.source in water_out and not model.carries_carbon(r.target):
                water_out[r.source] += float(r.volume)

        rho = {n: dict(prev_rho.get(n, _configured_density(n, model))) for n in propagate_nodes}

        for _ in range(_MAX_ITER):
            max_delta = 0.0
            for node in propagate_nodes:
                c_in = {sp: 0.0 for sp in SPECIES}
                v_in = {sp: 0.0 for sp in SPECIES}
                total = 0.0
                for src, vol in inflows.get(node, []):
                    v = float(vol)
                    es = _edge_species(src, node, model, cache, harvest)
                    dens = _source_density(src, node, model, rho, harvest)
                    for sp in SPECIES:
                        v_in[sp] += es.get(sp, 0.0) * v
                        c_in[sp] += es.get(sp, 0.0) * v * dens[sp]
                    total += v
                if total <= 0:
                    continue
                wood_share = 1.0 - water_out[node] / total
                if wood_share <= 0:
                    raise ValueError(
                        f"year {year}: node {node!r} loses its whole inflow as water "
                        f"({water_out[node]:,.0f} of {total:,.0f} m3); no wood leaves."
                    )
                new = {}
                for sp in SPECIES:
                    if v_in[sp] > 0:
                        new[sp] = (c_in[sp] / v_in[sp]) / wood_share
                    else:
                        # Species absent from this year's inflow: keep the carried
                        # value so a zero-volume row cannot divide by zero.
                        new[sp] = rho[node][sp]
                max_delta = max(max_delta, max(abs(new[sp] - rho[node][sp]) for sp in SPECIES))
                rho[node] = new
            if max_delta < _TOL:
                break
        prev_rho = rho

        for node, rule in model.nodes.items():
            dens = rho.get(node) if rule.mode == "propagate" else _configured_density(node, model)
            if dens is None:
                continue
            for sp in SPECIES:
                rows.append({"year": year, "node": node, "species": sp, "kg_c_per_m3": dens[sp]})

    return pd.DataFrame(rows, columns=["year", "node", "species", "kg_c_per_m3"])


def _source_density(source, target, model, rho, harvest) -> dict:
    """Density per species of the wood on an edge, given the current ρ estimates."""
    if source == STANDING_WOOD:
        # Harvest edges carry the *target* pool's density (raw) — or, for the
        # by-products (Bark, Residues), that target's configured density.
        return _configured_density(target, model)
    rule = model.nodes.get(source)
    if rule is not None and rule.mode == "propagate":
        return rho.get(source, _configured_density(source, model))
    return _configured_density(source, model)


def add_carbon(df: pd.DataFrame, model: CarbonModel | None = None,
               out_col: str = "carbon") -> pd.DataFrame:
    """Return a copy of ``df`` with a species-accurate, carbon-conserving column (kg C).

    Carbon of a row = ``volume × ρ(year, source, species)`` with the effective
    densities of :func:`node_densities`; a row into a node that does not carry
    carbon (water) gets 0. A Phase C frame already carries one concrete
    species per row, so the lookup is direct; a national frame uses the
    species shares of the emitting node. Per-edge totals are identical either
    way (Σ_sp share·vol·ρ(sp) == vol·Σ_sp share·ρ(sp)).
    """
    model = model or load_model()
    out = df.copy()
    dens = node_densities(out, model)
    rho_by_year: dict = {}
    for r in dens.itertuples(index=False):
        rho_by_year.setdefault(r.year, {}).setdefault(r.node, {})[r.species] = r.kg_c_per_m3

    def edge_density(year, source, target) -> dict:
        if not model.carries_carbon(target):
            return {sp: 0.0 for sp in SPECIES}
        return _source_density(source, target, model, rho_by_year.get(year, {}), None)

    if "species" in out.columns and out["species"].isin(SPECIES).all():
        out[out_col] = [
            float(r.volume) * edge_density(r.year, r.source, r.target)[r.species]
            for r in out.itertuples()
        ]
        return out

    carbon = pd.Series(0.0, index=out.index)
    mix_by_year = _mix_lookup(species_mix(out, model))
    for year, edges in out.groupby("year"):
        harvest = _harvest_mix_for_year(edges)
        cache = mix_by_year.get(year, {})
        for r in edges.itertuples():
            es = _edge_species(r.source, r.target, model, cache, harvest)
            dens_sp = edge_density(year, r.source, r.target)
            carbon.at[r.Index] = float(r.volume) * sum(
                es.get(sp, 0.0) * dens_sp[sp] for sp in SPECIES
            )

    out[out_col] = carbon
    return out
