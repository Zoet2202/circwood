"""Sawmills (and Planingmills/Impregnation) flow construction.

This is the "sawing function" whose three bugs are documented in
``Sawing_function_diagnosis.docx``. The fixes are:

1. **Year alignment.** Every per-year series is reindexed onto a shared
   ``all_years`` index before any arithmetic, so values are aligned by *year*
   and never by positional integer index.
2. **Coefficient base.** ``SAWDUST``/``CELLULOSE``/``SCRAPS`` are applied to the
   raw log input (``initial_timber_vol``), not to a post-shrinkage dry volume.
3. **Mass balance.** The sawn-timber residual flowing to WPP is derived from a
   mass-balanced ``SAWN_YIELD`` so the Sawmills node balances, with explicit
   handling of re-exported raw logs, imports, and stock changes.

All functions are pure: they take DataFrames and return DataFrames.
"""

from __future__ import annotations

import pandas as pd

from circwood import constants as C


def sawmills_inventory_changes(treindustrien_df: pd.DataFrame) -> pd.DataFrame:
    """Compute year-over-year ``VolumeChange`` for each inventory category."""
    df = treindustrien_df.sort_values(["Category", "year"]).copy()
    inventory_df = df[
        df["Category"].str.contains("inventory", case=False, na=False)
    ].copy()
    inventory_df["VolumeChange"] = inventory_df.groupby("Category")["Volume"].diff()
    return inventory_df


def _yearly_series(df: pd.DataFrame, mask, value_col: str, all_years) -> pd.Series:
    """Sum ``value_col`` by year over ``df[mask]`` and reindex onto ``all_years``.

    This is the single helper that enforces the year-alignment fix: every series
    that feeds the sawing arithmetic is forced onto the same year index.
    """
    sub = df[mask]
    grouped = (
        sub.groupby("year", as_index=False)[value_col]
        .sum()
        .set_index("year")
        .reindex(all_years, fill_value=0)
    )
    return grouped[value_col]


def create_flow_rows(
    trade_df: pd.DataFrame,
    sankey_df: pd.DataFrame,
    treindustrien_production: pd.DataFrame,
    treindustrien_inventory: pd.DataFrame,
) -> pd.DataFrame:
    """Build all Sawmills / Planingmills / Impregnation flow rows.

    Parameters
    ----------
    trade_df : DataFrame with columns [year, source, target, volume]
        Net trade flows (from SSB 08801) already mapped to source/target labels.
    sankey_df : DataFrame with columns [source, target, volume, year]
        Partially-assembled Sankey containing the SSB 07410 ``* -> Sawmills``
        sawtimber inflows.
    treindustrien_production : DataFrame with columns
        [year, Volume_sawn, Volume_planed, Volume_imp]
    treindustrien_inventory : raw Treindustrien DataFrame [year, Category, Volume]

    Returns
    -------
    DataFrame with columns [year, volume, source, target, origin].
    """
    all_years = sorted(treindustrien_production["year"].unique())

    prod = treindustrien_production.set_index("year").reindex(all_years).reset_index()
    year = prod["year"]
    planed_vol = prod["Volume_planed"]
    imp_vol = prod["Volume_imp"]

    # --- Inventory stock changes, reindexed by year ---
    inventory_df = sawmills_inventory_changes(treindustrien_inventory)
    inventory_timber = _yearly_series(
        inventory_df, inventory_df["Category"] == "Timber inventory",
        "VolumeChange", all_years,
    )
    inventory_sawn = _yearly_series(
        inventory_df, inventory_df["Category"] == "sawn timber inventory",
        "VolumeChange", all_years,
    )

    # --- Trade series, all reindexed by year ---
    # NB: 'Export sawn wood' / 'Export planed wood' / 'Export Sawmills' live in
    # the TARGET column (see ingest.trade.assign_target_source_trade), while
    # 'Import *' labels live in the SOURCE column.
    import_saw_wood = _yearly_series(
        trade_df, trade_df["source"] == "Import saw wood", "volume", all_years)
    export_sawmills = _yearly_series(
        trade_df, trade_df["target"] == "Export Sawmills", "volume", all_years)
    export_sawn_wood = _yearly_series(
        trade_df, trade_df["target"] == "Export sawn wood", "volume", all_years)
    import_planed_wood = _yearly_series(
        trade_df, trade_df["source"] == "Import planed wood", "volume", all_years)
    export_planed_wood = _yearly_series(
        trade_df, trade_df["target"] == "Export planed wood", "volume", all_years)

    # --- Sawtimber inflow to Sawmills (SSB 07410), reindexed by year ---
    sankey_inflow = _yearly_series(
        sankey_df, sankey_df["target"] == "Sawmills", "volume", all_years)

    # Volume that actually enters the saw:
    #   domestic harvest + imports - stock change - raw logs re-exported as logs
    initial_timber_vol = (
        sankey_inflow.to_numpy()
        - inventory_timber.to_numpy()
        + import_saw_wood.to_numpy()
        - export_sawmills.to_numpy()
    )
    initial_timber_vol = pd.Series(initial_timber_vol, index=all_years)

    # By-products as shares of the raw INPUT volume (fix #2).
    water_vol = initial_timber_vol * C.SHRINKAGE
    sawdust_vol = initial_timber_vol * C.SAWDUST
    cellulose_vol = initial_timber_vol * C.CELLULOSE
    scraps_vol = initial_timber_vol * C.SCRAPS

    # Mass-balanced sawn-timber residual to WPP (fix #3).
    sawn_vol_implied = initial_timber_vol * C.SAWN_YIELD
    sawn_vol_to_planed = planed_vol.to_numpy() * C.PLANED_INPUT_FACTOR
    sawn_vol_to_planed = pd.Series(sawn_vol_to_planed, index=all_years)
    left_over_volume = (
        sawn_vol_implied
        - sawn_vol_to_planed
        - export_sawn_wood
        - inventory_sawn
    )

    planed_byproducts = sawn_vol_to_planed * C.BYPRODUCT_PLANING
    sum_planed_wood = planed_vol.to_numpy() + import_planed_wood.to_numpy() - export_planed_wood.to_numpy()
    planed_wpp = pd.Series(sum_planed_wood, index=all_years) - imp_vol.to_numpy()

    # Assemble rows. All series share the ``all_years`` index/order.
    origin = "Treindustrien årsrapport"
    edges = [
        (sawn_vol_to_planed, "Sawmills", "Planingmills"),
        (left_over_volume, "Sawmills", "Wood Product processing & Logistics"),
        (water_vol, "Sawmills", "Shrinkage"),
        (sawdust_vol, "Sawmills", "Boardmills"),
        (cellulose_vol, "Sawmills", "Pulpmills"),
        (scraps_vol, "Sawmills", "Energy"),
        (planed_byproducts, "Planingmills", "Boardmills"),
        (planed_wpp, "Planingmills", "Wood Product processing & Logistics"),
        (pd.Series(imp_vol.to_numpy(), index=all_years), "Planingmills", "Impregnation"),
        (pd.Series(imp_vol.to_numpy(), index=all_years), "Impregnation",
         "Wood Product processing & Logistics"),
    ]

    frames = [
        pd.DataFrame({
            "year": all_years,
            "volume": series.to_numpy(),
            "source": src,
            "target": tgt,
            "origin": origin,
        })
        for series, src, tgt in edges
    ]
    return pd.concat(frames, ignore_index=True)
