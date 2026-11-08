"""Assembly helpers: dedupe, mass-balance checks, and the fresh pipeline.

The fresh (SSB API) pipeline is ported here for when live ingestion is wired up,
but it is gated behind the optional ``ssb`` extras and is not run by the cached
pipeline. The dedupe and mass-balance helpers are pure and used by both the
pipeline and the test-suite.
"""

from __future__ import annotations

import pandas as pd

from circwood import config, constants as C
from circwood.ingest import treindustrien as tre
from circwood.transform.dimensions import (
    add_dimensions,
    build_regional_harvest,
    collapse_national,
    merge_origins,
)
from circwood.transform.sawing import (
    SawmillCoefficients,
    create_flow_rows,
    resolve_coefficients,
)
from circwood.transform.inventory import create_inventory_rows
from circwood.transform.mills import boardmill_process, pulpmills_process

SPECIES_POOLS = ("Spruce", "Pine", "Non-coniferous")

# The only SSB 10513 waste-treatment category the End-of-Life transform models.
EOL_INCINERATION = "Levert til forbrenning"

# Edges produced by the assembly step (create_flow_rows, inventory routing, the
# mill split functions, and the firewood/WPP aggregates). Everything else in a
# dataset is a "primary" input row. Used to re-derive a balanced dataset from
# cached primary rows without re-fetching from SSB.
DERIVED_EDGES = {
    # create_flow_rows
    ("Sawmills", "Planingmills"),
    ("Sawmills", "Wood Product processing & Logistics"),
    ("Sawmills", "Shrinkage"),
    ("Sawmills", "Boardmills"),
    ("Sawmills", "Pulpmills"),
    ("Sawmills", "Energy"),
    ("Planingmills", "Boardmills"),
    ("Planingmills", "Wood Product processing & Logistics"),
    ("Planingmills", "Impregnation"),
    ("Impregnation", "Wood Product processing & Logistics"),
    # inventory routing
    ("Sawmills", "Timber inventory"),
    ("Timber inventory", "Sawmills"),
    ("Planingmills", "Planed timber inventory"),
    ("Planed timber inventory", "Planingmills"),
    ("Impregnation", "Impregnated inventory"),
    ("Impregnated inventory", "Impregnation"),
    # mill split functions
    ("Boardmills", "Loss Boardmills"),
    ("Boardmills", "Wood Product processing & Logistics"),
    ("Pulpmills", "Short-term USE"),
    ("Pulpmills", "Energy"),
    ("Pulpmills", "Loss Pulpmills"),
    # aggregates
    ("Firewood", "Energy"),
    ("Wood Product processing & Logistics", "Long-term USE"),
    ("Wood Product processing & Logistics", "Energy"),
}


def dedupe_flows(sankey_df: pd.DataFrame) -> pd.DataFrame:
    """Collapse to one row per (year, source, target), summing volumes.

    Multiple upstream steps emit several rows for the same edge (e.g. the
    Boardmills/Pulpmills splits, or a species pool fed by both 07410 and 11181).
    They are summable components of the same physical flow; left un-aggregated
    they inflate Sankey link widths and break mass-balance checks.
    """
    return (
        sankey_df
        .groupby(["year", "source", "target"], as_index=False)
        .agg(volume=("volume", "sum"), origin=("origin", merge_origins))
    )


def node_balance(sankey_df: pd.DataFrame, node: str) -> pd.DataFrame:
    """Return per-year inflow, outflow and imbalance for a node.

    Columns: [year, inflow, outflow, imbalance, rel_imbalance].
    Useful both as a runtime assertion and in tests.
    """
    df = sankey_df
    inflow = (
        df[df["target"] == node].groupby("year")["volume"].sum().rename("inflow")
    )
    outflow = (
        df[df["source"] == node].groupby("year")["volume"].sum().rename("outflow")
    )
    out = pd.concat([inflow, outflow], axis=1).fillna(0.0).reset_index()
    out["imbalance"] = out["outflow"] - out["inflow"]
    out["rel_imbalance"] = out["imbalance"] / out["inflow"].where(out["inflow"] != 0)
    return out


def _finalize(sankey_df: pd.DataFrame) -> pd.DataFrame:
    """Clean types, clip to the dataset window, and dedupe per (year, src, tgt)."""
    sankey_df = sankey_df.copy()
    sankey_df["year"] = pd.to_numeric(sankey_df["year"], errors="coerce")
    sankey_df = sankey_df.dropna(subset=["year", "volume"])
    sankey_df["year"] = sankey_df["year"].astype(int)
    sankey_df["volume"] = sankey_df["volume"].abs()
    # Clip to the years every source table covers. Outside that intersection the
    # dataset is not "more data", it is a partial picture: years with trade but
    # no harvest, or where one table has published the year and another has not.
    # See constants.DATASET_MIN_YEAR / DATASET_MAX_YEAR.
    sankey_df = sankey_df[
        sankey_df["year"].between(C.DATASET_MIN_YEAR, C.DATASET_MAX_YEAR)
    ]
    deduped = dedupe_flows(sankey_df)
    # Canonical column order (dedupe groups by year first).
    return deduped[["source", "target", "volume", "year", "origin"]]


# Trade flows collapsed into single net-export nodes (net = export - import),
# replacing the gross Import*/Export* edges. Raw roundwood nets at the species
# pools; sawn timber nets at the Sawmills output. (improvements items 3 / 3.5)
NET_EXPORT_PRODUCTS = (
    {
        "net_node": "Net export skurlast",
        "exports": [("Spruce", "Export Sawmills"), ("Pine", "Export Sawmills"),
                    ("Non-coniferous", "Export Sawmills")],
        "import_edge": ("Import saw wood", "Sawmills"),
        "domestic_target": "Sawmills",
    },
    {
        "net_node": "Net export massevirke",
        "exports": [("Spruce", "Export pulpwood"), ("Pine", "Export pulpwood"),
                    ("Non-coniferous", "Export pulpwood")],
        "import_edge": ("Import pulpwood", "Pulpmills"),
        "domestic_target": "Pulpmills",
    },
    {
        "net_node": "Net export sawn timber",
        "exports": [("Sawmills", "Export sawn wood")],
        "import_edge": ("Import sawn wood", "Wood Product processing & Logistics"),
        "domestic_target": "Wood Product processing & Logistics",
    },
)

NET_ORIGIN = "SSB table 08801 (net)"


def _apply_net_exports(sankey_df: pd.DataFrame) -> pd.DataFrame:
    """Collapse gross import/export pairs into single net-export nodes.

    For each product and year, ``net = gross export - gross import``. The gross
    ``Import* -> mill`` and ``source -> Export*`` edges are removed and replaced
    by a ``source -> Net export <product>`` edge carrying the net; the domestic
    ``source -> mill`` edge is bumped by the import volume so every node stays
    mass-balanced (the mill's total throughput is unchanged — the import is just
    folded into the domestic flow). Net-import years (import > export) are drawn
    as ``Net export <product> -> mill`` instead, keeping direction physical and
    volumes non-negative. Idempotent: re-running finds no gross edges to net.
    """
    vol: dict = {}
    orig: dict = {}
    for r in sankey_df.itertuples(index=False):
        k = (r.year, r.source, r.target)
        vol[k] = vol.get(k, 0.0) + float(r.volume)
        orig[k] = r.origin

    years = sorted({k[0] for k in vol})
    for cfg in NET_EXPORT_PRODUCTS:
        net_node, dom = cfg["net_node"], cfg["domestic_target"]
        imp_s, imp_t = cfg["import_edge"]
        for y in years:
            xs = {(s, t): vol.get((y, s, t), 0.0) for s, t in cfg["exports"]}
            X = sum(xs.values())
            I = vol.get((y, imp_s, imp_t), 0.0)
            if X == 0 and I == 0:
                continue
            for s, t in cfg["exports"]:
                vol.pop((y, s, t), None)
            vol.pop((y, imp_s, imp_t), None)

            if X - I >= 0 and X > 0:                  # net export
                for (s, _t), x in xs.items():
                    dk = (y, s, dom)
                    vol[dk] = vol.get(dk, 0.0) + x / X * I
                    orig.setdefault(dk, NET_ORIGIN)
                    nk = (y, s, net_node)
                    vol[nk] = vol.get(nk, 0.0) + x * (X - I) / X
                    orig[nk] = NET_ORIGIN
            else:                                     # net import (or X == 0)
                for (s, _t), x in xs.items():
                    dk = (y, s, dom)
                    vol[dk] = vol.get(dk, 0.0) + x
                    orig.setdefault(dk, NET_ORIGIN)
                mk = (y, net_node, dom)
                vol[mk] = vol.get(mk, 0.0) + (I - X)
                orig[mk] = NET_ORIGIN

    recs = [
        {"source": s, "target": t, "volume": v, "year": y, "origin": orig[(y, s, t)]}
        for (y, s, t), v in vol.items() if v != 0
    ]
    return pd.DataFrame(recs)[["source", "target", "volume", "year", "origin"]]


def _bypass_raw_reexport(primary_df: pd.DataFrame, node: str, export_target: str) -> pd.DataFrame:
    """Route raw-roundwood re-exports around a processing ``node``.

    SSB 08801 maps re-exported raw roundwood to a ``node -> export_target`` edge,
    i.e. the wood passes *through* the mill even though it is never processed
    there. That inflates the node's throughput and stops it balancing (and, for
    Sawmills, makes the 26% chip yield read as a smaller share of inflow).

    This re-routes the re-exported volume to come straight from the species pools
    (``Spruce``/``Pine``/``Non-coniferous`` -> ``export_target``), allocated by
    each species' share of the node's pool inflow, and reduces the corresponding
    ``species -> node`` rows. Species-pool balance is preserved (the pool's total
    outflow is unchanged). Idempotent: only acts on the passthrough edge.

    Examples: ``(node="Sawmills", export_target="Export Sawmills")`` for
    re-exported sawlogs ('Skurtømmer'); ``(node="Pulpmills",
    export_target="Export pulpwood")`` for re-exported pulpwood ('Massevirke').
    """
    df = primary_df.copy()
    reexp = df[(df["source"] == node) & (df["target"] == export_target)]
    if reexp.empty:
        return df

    reexp_by_year = reexp.groupby("year")["volume"].sum()
    df = df[~((df["source"] == node) & (df["target"] == export_target))].copy()

    feed_mask = df["source"].isin(SPECIES_POOLS) & (df["target"] == node)
    new_rows = []
    for year, total in reexp_by_year.items():
        fy = df[feed_mask & (df["year"] == year)]
        fsum = fy["volume"].sum()
        if fsum <= 0:
            continue
        for idx, row in fy.iterrows():
            cut = total * (row["volume"] / fsum)
            df.at[idx, "volume"] = row["volume"] - cut
            new_rows.append({
                "source": row["source"], "target": export_target,
                "volume": cut, "year": year, "origin": row["origin"],
            })

    if new_rows:
        df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
    return df


def _bypass_raw_log_reexports(primary_df: pd.DataFrame) -> pd.DataFrame:
    """Route raw-log re-exports around Sawmills (see :func:`_bypass_raw_reexport`)."""
    return _bypass_raw_reexport(primary_df, "Sawmills", "Export Sawmills")


def _prepare_sawing_inputs(primary_df: pd.DataFrame, treindustrien_df: pd.DataFrame):
    """Coerce years, route raw re-exports, and split out the sawing inputs.

    Returns ``(primary_df, harvest_to_sawmills, production)``: the cleaned primary
    rows (with raw-roundwood re-exports bypassed around the mills), the
    ``species pools -> Sawmills`` harvest inflow, and the wide Treindustrien
    production frame. Shared by :func:`assemble_sankey` and
    :func:`effective_coefficients` so the coefficients displayed and applied are
    derived from identical inputs.
    """
    production = tre.production_wide(treindustrien_df)

    # SSB rows arrive with string years (pyjstat category labels); the sawing
    # math reindexes against integer Treindustrien years, so coerce up front or
    # the string-keyed harvest inflow silently drops to zero on reindex.
    primary_df = primary_df.copy()
    primary_df["year"] = pd.to_numeric(primary_df["year"], errors="coerce")
    primary_df = primary_df.dropna(subset=["year"])
    primary_df["year"] = primary_df["year"].astype(int)

    # Route raw-roundwood re-exports around the mills so only processed wood
    # reaches them (raw sawlogs around Sawmills, raw pulpwood around Pulpmills).
    primary_df = _bypass_raw_reexport(primary_df, "Sawmills", "Export Sawmills")
    primary_df = _bypass_raw_reexport(primary_df, "Pulpmills", "Export pulpwood")

    # create_flow_rows must see ONLY the harvest logs entering the saw (species
    # pools -> Sawmills); imports/inventory are folded in via the trade frame.
    harvest_to_sawmills = primary_df[
        primary_df["source"].isin(SPECIES_POOLS) & (primary_df["target"] == "Sawmills")
    ]
    return primary_df, harvest_to_sawmills, production


def assemble_sankey(
    primary_df: pd.DataFrame,
    treindustrien_df: pd.DataFrame,
    coefficient_source: str = "manual",
    coefficients: SawmillCoefficients | None = None,
) -> pd.DataFrame:
    """Build the full, mass-balanced Sankey from primary rows + Treindustrien.

    ``primary_df`` holds the non-derived flows (SSB species/trade/mortality/EOL/
    firewood rows + FAO board imports). This function re-derives every process
    edge — the sawing flows, inventory routing, mill splits, and the firewood/
    WPP aggregates — so the result is mass-consistent regardless of whether the
    primary rows came fresh from SSB or were extracted from a cached dataset.

    ``coefficient_source`` selects how the sawmill/planing percentages are set:
    ``"manual"`` uses ``coefficients`` (or the package defaults), while
    ``"treindustrien"`` derives the sawn yield from the Treindustrien
    produced-volume data (with ``coefficients`` supplying the base by-product
    ratios and planing factors). See
    :class:`~circwood.transform.sawing.SawmillCoefficients`.
    """
    primary_df, harvest_to_sawmills, production = _prepare_sawing_inputs(
        primary_df, treindustrien_df)

    coef = resolve_coefficients(
        coefficient_source, primary_df, harvest_to_sawmills,
        production, treindustrien_df, base=coefficients,
    )
    flows = create_flow_rows(
        trade_df=primary_df, sankey_df=harvest_to_sawmills,
        treindustrien_production=production, treindustrien_inventory=treindustrien_df,
        coefficients=coef,
    )
    inventory_rows = create_inventory_rows(tre.signed_inventory(treindustrien_df))

    sankey_df = pd.concat([primary_df, flows, inventory_rows], ignore_index=True)
    sankey_df = boardmill_process(sankey_df)
    sankey_df = pulpmills_process(sankey_df)

    # Firewood -> Energy aggregate
    df_firewood = (
        sankey_df[sankey_df["target"] == "Firewood"]
        .groupby("year", as_index=False)["volume"].sum()
    )
    df_firewood["source"], df_firewood["target"] = "Firewood", "Energy"
    df_firewood["origin"] = "SSB various tables"

    # Wood Product processing & Logistics split
    df_wpp = (
        sankey_df[sankey_df["target"] == "Wood Product processing & Logistics"]
        .groupby("year", as_index=False)["volume"].sum()
    )
    df_wpp["source"] = "Wood Product processing & Logistics"
    df_wpp["origin"] = "SSB and Treindustrien"
    wpp_ltu = df_wpp.assign(volume=df_wpp["volume"] * C.WPP_LONG_TERM, target="Long-term USE")
    wpp_energy = df_wpp.assign(volume=df_wpp["volume"] * C.WPP_ENERGY, target="Energy")

    sankey_df = pd.concat([sankey_df, df_firewood, wpp_ltu, wpp_energy], ignore_index=True)
    # Phase C: widen the national table with the filter dimensions (region /
    # species / assortment / entity_id). Summing volume over them recovers the
    # national table exactly, so nothing downstream loses its guarantees.
    return add_dimensions(_apply_net_exports(_finalize(sankey_df)))


def _strip_derived(df: pd.DataFrame) -> pd.DataFrame:
    """Return only the primary (non-derived) rows of ``df`` (see ``DERIVED_EDGES``).

    Collapses the Phase C dimension columns back to the national schema first:
    the assembly math is national, and ``assemble_sankey`` re-derives the
    dimensions on its way out, so carrying split rows into it would only make
    the dedupe re-sum what the expansion just divided.
    """
    df = collapse_national(df)
    is_derived = [(s, t) in DERIVED_EDGES for s, t in zip(df["source"], df["target"])]
    return df[~pd.Series(is_derived, index=df.index)].copy()


def regenerate_balanced(
    df: pd.DataFrame | None = None,
    write: bool = False,
    coefficient_source: str = "manual",
    coefficients: SawmillCoefficients | None = None,
) -> pd.DataFrame:
    """Re-derive a mass-balanced dataset from an existing dataset's primary rows.

    Strips every derived edge (see ``DERIVED_EDGES``) from ``df`` and re-runs
    ``assemble_sankey`` on the remaining primary rows, using the local
    Treindustrien CSV. This closes the Sawmills mass-balance gap (diagnosis
    Issue 3) on the cached dataset without needing SSB API access.

    ``coefficient_source`` / ``coefficients`` are forwarded to
    :func:`assemble_sankey`, so this is also the entry point for rebuilding the
    cached dataset under researcher-chosen (manual or Treindustrien-derived)
    sawmill/planing percentages.

    Note: this changes the sawn-side numbers (the Sawmills -> WPP band shrinks
    to its mass-balanced value, cascading to Boardmills/Pulpmills throughput).
    """
    from circwood.pipeline import load_cached_sankey

    df = df if df is not None else load_cached_sankey()
    primary = _strip_derived(df)

    rebuilt = assemble_sankey(
        primary, tre.load_treindustrien(),
        coefficient_source=coefficient_source, coefficients=coefficients,
    )

    if write:
        config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        rebuilt.to_csv(config.SANKEY_CSV, index=False)
        print(f"Wrote {len(rebuilt)} balanced rows to {config.SANKEY_CSV}")
    return rebuilt


def effective_coefficients(
    coefficient_source: str = "manual",
    coefficients: SawmillCoefficients | None = None,
    df: pd.DataFrame | None = None,
) -> SawmillCoefficients:
    """Return the :class:`SawmillCoefficients` that a rebuild would actually use.

    Mirrors the primary-row extraction and re-export bypass of
    :func:`regenerate_balanced`, then resolves the coefficients against the same
    inputs :func:`assemble_sankey` sees. Lets the app preview the
    Treindustrien-derived yields without rebuilding the whole dataset.
    """
    from circwood.pipeline import load_cached_sankey

    df = df if df is not None else load_cached_sankey()
    treindustrien_df = tre.load_treindustrien()
    primary = _strip_derived(df)
    primary, harvest_to_sawmills, production = _prepare_sawing_inputs(
        primary, treindustrien_df)
    return resolve_coefficients(
        coefficient_source, primary, harvest_to_sawmills,
        production, treindustrien_df, base=coefficients,
    )


# ---------------------------------------------------------------------------
# Fresh (SSB API) pipeline — deferred; requires the `ssb` extras + network.
# ---------------------------------------------------------------------------
def run_fresh_pipeline(
    coefficient_source: str = "manual",
    coefficients: SawmillCoefficients | None = None,
    ingested=None,
) -> pd.DataFrame:
    """Fetch fresh SSB data and rebuild the full Sankey dataset.

    Requires ``pip install -e .[ssb]`` and network access. Writes the result to
    ``config.SANKEY_CSV`` and returns it. ``coefficient_source`` / ``coefficients``
    are forwarded to :func:`assemble_sankey`.

    ``ingested`` accepts an already-fetched
    :class:`~circwood.ingest.ssb.IngestResult`, so a caller that needs the
    per-table vintages (the refresh loop, which writes them to the manifest) can
    do the ingest itself instead of this function silently discarding them.
    """
    try:
        import pyjstat  # noqa: F401
        import requests  # noqa: F401
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "The fresh pipeline needs the optional SSB extras.\n"
            "Install with:  pip install -e .[ssb]\n"
            "Or use the cached pipeline (use_cached_ssb=True)."
        ) from exc

    from circwood.ingest.ssb import process_query_files
    from circwood.ingest.trade import assign_target_source_trade, classify_varenummer
    from circwood.transform.destinations import (
        build_partner_exports, reconcile_partner_exports,
    )
    from circwood.ingest import treindustrien as tre
    from circwood.transform.sawing import create_flow_rows
    from circwood.transform.firewood import create_firewood_rows  # noqa: F401
    from circwood.transform.inventory import create_inventory_rows
    from circwood.transform.mills import boardmill_process, pulpmills_process

    treindustrien_df = tre.load_treindustrien()
    fao_boardmills_df = pd.read_csv(config.FAO_CSV)
    # Raises rather than returning a partial dataset; every table below is
    # therefore present, validated, and in Norwegian.
    if ingested is None:
        ingested = process_query_files(str(config.SSB_QUERY_DIR))
    ssb = ingested.datasets

    # --- 03795 by county (agg_KommFylker) — the region dimension's data ---
    # Rewritten before assembly so add_dimensions picks up the same vintage as
    # the national 03795 pull. Tolerated as absent (e.g. a test feeding a
    # partial IngestResult): the dataset then keeps its committed regional
    # file, or stays national if none exists.
    if "03795_fylke" in ssb:
        regional = build_regional_harvest(
            pd.concat(ssb["03795_fylke"], ignore_index=True)
        )
        config.RAW_DIR.mkdir(parents=True, exist_ok=True)
        regional.to_csv(config.HARVEST_FYLKE_CSV, index=False)

    # --- 08799 exports by partner country — the destination dimension's data ---
    # Same contract as the county file above: rewritten before assembly so the
    # dimension and the national trade rows come from the same vintage;
    # tolerated as absent (partial IngestResult), in which case the committed
    # partner file, or none, is used.
    chunk_ids = sorted(tid for tid in ssb if tid.startswith("08799_"))
    partners = None
    if chunk_ids:
        land_codes: dict[str, str] = {}
        for tid in chunk_ids:
            land_codes.update(ingested.dimension_codes.get(tid, {}).get("Land", {}))
        partners = build_partner_exports(
            [pd.concat(ssb[tid], ignore_index=True) for tid in chunk_ids], land_codes
        )
        config.RAW_DIR.mkdir(parents=True, exist_ok=True)
        partners.to_csv(config.EXPORTS_PARTNER_CSV, index=False)

    # --- 11181 firewood ---
    rows_firewood = create_firewood_rows(pd.concat(ssb["11181"], ignore_index=True))

    # --- 03795 roundwood harvest by species (feeds the species pools) ---
    # 03795 'Kvantum avvirket' is already in m3 (no x1000). It supplies the
    # Harvested wood -> Spruce/Pine/Non-coniferous inflows that balance the
    # species pools, plus the harvest total used for the bark estimate.
    tbl_03795 = pd.concat(ssb["03795"], ignore_index=True)
    species_map = {"Gran": "Spruce", "Furu": "Pine", "Lauv": "Non-coniferous"}
    harvest = tbl_03795[tbl_03795["sortiment"].isin(species_map)].copy()
    rows_harvest = pd.DataFrame({
        "source": "Harvested wood",
        "target": harvest["sortiment"].map(species_map),
        "volume": harvest["value"].astype(float),
        "year": harvest["år"].astype(int),
        "origin": "SSB table 03795",
    })

    # Bark = 10% of total harvested wood (all 03795 sortiments), per year.
    harvest_total = tbl_03795.groupby("år", as_index=False)["value"].sum()
    rows_bark = pd.DataFrame({
        "source": "Harvested wood", "target": "Bark",
        "volume": harvest_total["value"].astype(float) * C.BARK_PERCENTAGE,
        "year": harvest_total["år"].astype(int), "origin": "SSB table 03795",
    })

    # --- 10513 end-of-life ---
    # Modelling decision: the query selects incineration only (AvfallsBehand=21,
    # 'Levert til forbrenning') because that is the current reality — Norwegian
    # waste wood is effectively all incinerated. The purpose of this edge is to
    # quantify the volume that *may* be recovered, so the whole End-of-Life
    # stream routes to Energy and there is no Material recovery node; the
    # recoverable potential is read off the Energy edge, not split out of it.
    tbl_10513 = pd.concat(ssb["10513"], ignore_index=True)
    tbl_10513 = tbl_10513[tbl_10513["value"] != 0].copy()
    tbl_10513["volume"] = tbl_10513["value"] * 1000 / 483
    unexpected = set(tbl_10513["behandlingsmåte"]) - {EOL_INCINERATION}
    if unexpected:
        # Widening the query without revisiting this would silently label
        # recovered wood as incinerated — the number the model exists to state.
        raise ValueError(
            f"SSB 10513 returned treatment categories this transform does not "
            f"model: {sorted(unexpected)}. The pipeline assumes incineration "
            f"only ({EOL_INCINERATION!r}); add explicit routing before widening "
            f"the AvfallsBehand selection in the query file."
        )
    tbl_10513["target"] = "Energy"
    eol_sum = tbl_10513.groupby("år", as_index=False)["volume"].sum()
    rows_EOL = pd.DataFrame({
        "year": eol_sum["år"], "volume": eol_sum["volume"],
        "source": "Long-term USE", "target": "End-of-Life",
        "origin": "SSB table 10513",
    })
    rows_waste = pd.DataFrame({
        "year": tbl_10513["år"], "volume": tbl_10513["volume"],
        "source": "End-of-Life", "target": tbl_10513["target"],
        "origin": "SSB table 10513",
    })

    # --- 07410 roundwood removals ---
    tbl_07410 = pd.concat(ssb["07410"], ignore_index=True)
    tbl_07410["value"] = tbl_07410["value"] * 1000
    sum_all = tbl_07410.groupby("år", as_index=False)["value"].sum()
    rows_residues = pd.DataFrame({
        "year": sum_all["år"], "volume": sum_all["value"] * C.HARVEST_RESIDUE_PERCENTAGE,
        "source": "Harvested wood", "target": "Residues from harvest",
        "origin": "SSB table 07410",
    })

    def sortiment_rows(name, source, target):
        sub = tbl_07410[tbl_07410["sortiment"] == name]
        return pd.DataFrame({
            "source": source, "target": target, "volume": sub["value"],
            "year": sub["år"], "origin": "SSB table 07410",
        })

    species_edges = [
        ("Gran sagtømmer", "Spruce", "Sawmills"),
        ("Gran massevirke", "Spruce", "Pulpmills"),
        ("Gran sams sagtømmer og massevirke", "Spruce", "Boardmills"),
        ("Furu sagtømmer", "Pine", "Sawmills"),
        ("Furu massevirke", "Pine", "Pulpmills"),
        ("Furu sams sagtømmer og massevirke", "Pine", "Boardmills"),
        ("Lauvtre sagtømmer", "Non-coniferous", "Sawmills"),
        ("Lauvtre massevirke", "Non-coniferous", "Pulpmills"),
        ("Lauvtre sams sagtømmer og massevirke", "Non-coniferous", "Boardmills"),
    ]
    species_frames = [sortiment_rows(*e) for e in species_edges]

    # --- 08801 trade ---
    tbl_08801 = pd.concat(ssb["08801"], ignore_index=True)
    tbl_08801 = tbl_08801.loc[tbl_08801["value"] != 0].copy()
    tbl_08801["type"] = classify_varenummer(tbl_08801["varenummer"])
    tbl_08801.loc[tbl_08801["statistikkvariabel"] == "Mengde 2 (M2)", "statistikkvariabel"] = "m3"
    trade = assign_target_source_trade(tbl_08801).rename(columns={"år": "year"})
    trade = trade[trade["statistikkvariabel"] == "m3"]
    if partners is not None:
        # Informational: the by-country and national tables are revised on
        # different schedules; a gap is logged, never fatal (shares are used).
        national_exports = trade[trade["import/eksport"].str.lower() == "eksport"]
        reconcile_partner_exports(
            partners,
            national_exports.rename(columns={"value": "volume"})[["year", "type", "volume"]]
            .assign(year=lambda d: pd.to_numeric(d["year"]).astype(int)),
        )
    trade_vol = (
        trade.groupby(["year", "source", "target"], as_index=False)["value"].sum()
        .rename(columns={"value": "volume"})
    )
    trade_vol["origin"] = "SSB table 08801"

    # --- assemble sawtimber inflow then sawing flows ---
    # Assemble all primary rows, then derive every process edge via the shared,
    # mass-balanced assembly step.
    primary_df = pd.concat(
        species_frames + [trade_vol, fao_boardmills_df, rows_EOL, rows_waste,
                          rows_residues, rows_firewood, rows_harvest, rows_bark],
        ignore_index=True,
    )
    sankey_df = assemble_sankey(
        primary_df, treindustrien_df,
        coefficient_source=coefficient_source, coefficients=coefficients,
    )

    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    sankey_df.to_csv(config.SANKEY_CSV, index=False)
    vintage = ", ".join(
        f"{tid} updated {v.updated}" for tid, v in sorted(ingested.vintages.items())
    )
    print(f"Wrote {len(sankey_df)} rows to {config.SANKEY_CSV}")
    print(f"SSB vintage: {vintage}")
    return sankey_df
