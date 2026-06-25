"""Assembly helpers: dedupe, mass-balance checks, and the fresh pipeline.

The fresh (SSB API) pipeline is ported here for when live ingestion is wired up,
but it is gated behind the optional ``ssb`` extras and is not run by the cached
pipeline. The dedupe and mass-balance helpers are pure and used by both the
pipeline and the test-suite.
"""

from __future__ import annotations

import pandas as pd

from circwood import config, constants as C


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
    sankey_df = pd.concat(species_frames, ignore_index=True)
    production = tre.production_wide(treindustrien_df)
    flows = create_flow_rows(
        trade_df=trade_vol, sankey_df=sankey_df,
        treindustrien_production=production, treindustrien_inventory=treindustrien_df,
    )

    inventory_rows = create_inventory_rows(tre.signed_inventory(treindustrien_df))

    sankey_df = pd.concat(
        [sankey_df, trade_vol, flows, inventory_rows, fao_boardmills_df,
         rows_EOL, rows_waste, rows_residues, rows_mortality, rows_firewood],
        ignore_index=True,
    )
    sankey_df = boardmill_process(sankey_df)
    sankey_df = pulpmills_process(sankey_df)

    # Firewood -> Energy aggregate
    df_firewood = (
        sankey_df[sankey_df["target"] == "Firewood"]
        .groupby("year", as_index=False)["volume"].sum()
    )
    df_firewood["source"], df_firewood["target"] = "Firewood", "Energy"
    df_firewood["origin"] = "SSB various tables"
    sankey_df = pd.concat([sankey_df, df_firewood], ignore_index=True)

    # WPP split
    df_wpp = (
        sankey_df[sankey_df["target"] == "Wood Product processing & Logistics"]
        .groupby("year", as_index=False)["volume"].sum()
    )
    df_wpp["source"] = "Wood Product processing & Logistics"
    df_wpp["origin"] = "SSB and Treindustrien"
    wpp_ltu = df_wpp.assign(volume=df_wpp["volume"] * C.WPP_LONG_TERM, target="Long-term USE")
    wpp_energy = df_wpp.assign(volume=df_wpp["volume"] * C.WPP_ENERGY, target="Energy")
    sankey_df = pd.concat([sankey_df, wpp_ltu, wpp_energy], ignore_index=True)

    sankey_df["year"] = pd.to_numeric(sankey_df["year"], errors="coerce")
    sankey_df = sankey_df.dropna(subset=["year", "volume"])
    sankey_df["year"] = sankey_df["year"].astype(int)
    sankey_df["volume"] = sankey_df["volume"].abs()

    sankey_df = dedupe_flows(sankey_df)

    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    sankey_df.to_csv(config.SANKEY_CSV, index=False)
    print(f"Wrote {len(sankey_df)} rows to {config.SANKEY_CSV}")
    return sankey_df
