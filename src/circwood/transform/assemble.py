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
from circwood.transform.sawing import create_flow_rows
from circwood.transform.inventory import create_inventory_rows
from circwood.transform.mills import boardmill_process, pulpmills_process

SPECIES_POOLS = ("Spruce", "Pine", "Non-coniferous")

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
    ("Sawmills", "Timber Inventory"),
    ("Timber Inventory", "Sawmills"),
    ("Planingmills", "planed timber inventory"),
    ("planed timber inventory", "Planingmills"),
    ("Impregnation", "Impregnated inventory"),
    ("Impregnated inventory", "Impregnation"),
    # mill split functions
    ("Boardmills", "Loss Boardmills"),
    ("Boardmills", "Wood Product processing & Logistics"),
    ("Pulpmills", "Short-term USE"),
    ("Pulpmills", "Energy"),
    ("Pulpmills", "Loss"),
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
        .agg(
            volume=("volume", "sum"),
            origin=("origin", lambda s: " | ".join(sorted({str(x) for x in s}))),
        )
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
    """Clean types, take magnitudes, and dedupe to one row per (year, src, tgt)."""
    sankey_df = sankey_df.copy()
    sankey_df["year"] = pd.to_numeric(sankey_df["year"], errors="coerce")
    sankey_df = sankey_df.dropna(subset=["year", "volume"])
    sankey_df["year"] = sankey_df["year"].astype(int)
    sankey_df["volume"] = sankey_df["volume"].abs()
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


def assemble_sankey(primary_df: pd.DataFrame, treindustrien_df: pd.DataFrame) -> pd.DataFrame:
    """Build the full, mass-balanced Sankey from primary rows + Treindustrien.

    ``primary_df`` holds the non-derived flows (SSB species/trade/mortality/EOL/
    firewood rows + FAO board imports). This function re-derives every process
    edge — the sawing flows, inventory routing, mill splits, and the firewood/
    WPP aggregates — so the result is mass-consistent regardless of whether the
    primary rows came fresh from SSB or were extracted from a cached dataset.
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
    flows = create_flow_rows(
        trade_df=primary_df, sankey_df=harvest_to_sawmills,
        treindustrien_production=production, treindustrien_inventory=treindustrien_df,
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
    return _apply_net_exports(_finalize(sankey_df))


def regenerate_balanced(df: pd.DataFrame | None = None, write: bool = False) -> pd.DataFrame:
    """Re-derive a mass-balanced dataset from an existing dataset's primary rows.

    Strips every derived edge (see ``DERIVED_EDGES``) from ``df`` and re-runs
    ``assemble_sankey`` on the remaining primary rows, using the local
    Treindustrien CSV. This closes the Sawmills mass-balance gap (diagnosis
    Issue 3) on the cached dataset without needing SSB API access.

    Note: this changes the sawn-side numbers (the Sawmills -> WPP band shrinks
    to its mass-balanced value, cascading to Boardmills/Pulpmills throughput).
    """
    from circwood.pipeline import load_cached_sankey

    df = df if df is not None else load_cached_sankey()
    is_derived = [(s, t) in DERIVED_EDGES for s, t in zip(df["source"], df["target"])]
    primary = df[~pd.Series(is_derived, index=df.index)].copy()

    rebuilt = assemble_sankey(primary, tre.load_treindustrien())

    if write:
        config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        rebuilt.to_csv(config.SANKEY_CSV, index=False)
        print(f"Wrote {len(rebuilt)} balanced rows to {config.SANKEY_CSV}")
    return rebuilt


# ---------------------------------------------------------------------------
# Fresh (SSB API) pipeline — deferred; requires the `ssb` extras + network.
# ---------------------------------------------------------------------------
def run_fresh_pipeline() -> pd.DataFrame:
    """Fetch fresh SSB data and rebuild the full Sankey dataset.

    Requires ``pip install -e .[ssb]`` and network access. Writes the result to
    ``config.SANKEY_CSV`` and returns it.
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
    from circwood.ingest.trade import assign_target_source_trade
    from circwood.ingest import treindustrien as tre
    from circwood.transform.sawing import create_flow_rows
    from circwood.transform.firewood import create_firewood_rows  # noqa: F401
    from circwood.transform.inventory import create_inventory_rows
    from circwood.transform.mills import boardmill_process, pulpmills_process

    treindustrien_df = tre.load_treindustrien()
    fao_boardmills_df = pd.read_csv(config.FAO_CSV)
    ssb = process_query_files(str(config.SSB_QUERY_DIR))

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
    tbl_10513 = pd.concat(ssb["10513"], ignore_index=True)
    tbl_10513 = tbl_10513[tbl_10513["value"] != 0].copy()
    tbl_10513["volume"] = tbl_10513["value"] * 1000 / 483
    tbl_10513["target"] = "Energy"
    tbl_10513.loc[
        tbl_10513["behandlingsmåte"] == "Levert til materialgjenvinning", "target"
    ] = "Material recovery"
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
    tbl_08801["type"] = "default"
    for kw, typ in [
        ("Impregnert", "impregnated"), ("Massevirke", "pulpwood"),
        ("Skurtømmer", "saw wood"), ("Trepeletter", "pellets"),
        ("brensel", "firewood"), ("høvellast", "planed wood"),
        ("skurlast", "sawn wood"),
    ]:
        tbl_08801.loc[tbl_08801["varenummer"].str.contains(kw, case=False), "type"] = typ
    tbl_08801.loc[tbl_08801["statistikkvariabel"] == "Mengde 2 (M2)", "statistikkvariabel"] = "m3"
    trade = assign_target_source_trade(tbl_08801).rename(columns={"år": "year"})
    trade = trade[trade["statistikkvariabel"] == "m3"]
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
    sankey_df = assemble_sankey(primary_df, treindustrien_df)

    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    sankey_df.to_csv(config.SANKEY_CSV, index=False)
    print(f"Wrote {len(sankey_df)} rows to {config.SANKEY_CSV}")
    return sankey_df
