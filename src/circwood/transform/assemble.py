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


def _bypass_raw_log_reexports(primary_df: pd.DataFrame) -> pd.DataFrame:
    """Route raw-log re-exports around the Sawmills node.

    SSB 08801 maps re-exported sawlogs ('Skurtømmer' eksport) to a
    ``Sawmills -> Export Sawmills`` edge, i.e. the logs pass *through* Sawmills
    even though they are never sawn. That inflates the Sawmills inflow and means
    the 26% chip yield (applied to logs actually sawn) reads as a smaller share
    of inflow, and the node cannot balance.

    This re-routes the re-exported volume to come straight from the species
    pools (``Spruce``/``Pine``/``Non-coniferous`` -> ``Export Sawmills``),
    allocated by each species' share of the sawlog inflow, and reduces the
    corresponding species -> Sawmills rows. Idempotent: only acts on the
    ``Sawmills -> Export Sawmills`` passthrough.
    """
    df = primary_df.copy()
    reexp = df[(df["source"] == "Sawmills") & (df["target"] == "Export Sawmills")]
    if reexp.empty:
        return df

    reexp_by_year = reexp.groupby("year")["volume"].sum()
    df = df[~((df["source"] == "Sawmills") & (df["target"] == "Export Sawmills"))].copy()

    harvest_mask = df["source"].isin(SPECIES_POOLS) & (df["target"] == "Sawmills")
    new_rows = []
    for year, total in reexp_by_year.items():
        hy = df[harvest_mask & (df["year"] == year)]
        hsum = hy["volume"].sum()
        if hsum <= 0:
            continue
        for idx, row in hy.iterrows():
            cut = total * (row["volume"] / hsum)
            df.at[idx, "volume"] = row["volume"] - cut
            new_rows.append({
                "source": row["source"], "target": "Export Sawmills",
                "volume": cut, "year": year, "origin": row["origin"],
            })

    if new_rows:
        df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
    return df


def assemble_sankey(primary_df: pd.DataFrame, treindustrien_df: pd.DataFrame) -> pd.DataFrame:
    """Build the full, mass-balanced Sankey from primary rows + Treindustrien.

    ``primary_df`` holds the non-derived flows (SSB species/trade/mortality/EOL/
    firewood rows + FAO board imports). This function re-derives every process
    edge — the sawing flows, inventory routing, mill splits, and the firewood/
    WPP aggregates — so the result is mass-consistent regardless of whether the
    primary rows came fresh from SSB or were extracted from a cached dataset.
    """
    production = tre.production_wide(treindustrien_df)

    # Route raw-log re-exports around Sawmills so only sawn logs reach the node.
    primary_df = _bypass_raw_log_reexports(primary_df)

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
    return _finalize(sankey_df)


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

    # --- 06289 mortality ---
    tbl_06289 = pd.concat(ssb["06289"], ignore_index=True)
    tilvekst = tbl_06289.loc[
        (tbl_06289["statistikkvariabel"] == "Årlig tilvekst")
        & (tbl_06289["treslag"] == "I alt")
    ].copy()
    rows_mortality = pd.DataFrame({
        "source": "Standing wood", "target": "Mortality",
        "volume": tilvekst["value"] * 1000 * C.MORTALITY_FACTOR,
        "year": tilvekst["år"], "origin": "SSB table 06289",
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
        "source": "Standing wood", "target": "Residues from harvest",
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
                          rows_residues, rows_mortality, rows_firewood],
        ignore_index=True,
    )
    sankey_df = assemble_sankey(primary_df, treindustrien_df)

    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    sankey_df.to_csv(config.SANKEY_CSV, index=False)
    print(f"Wrote {len(sankey_df)} rows to {config.SANKEY_CSV}")
    return sankey_df
