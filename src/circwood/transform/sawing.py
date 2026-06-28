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

from dataclasses import dataclass, replace

import pandas as pd

from circwood import constants as C


@dataclass(frozen=True)
class SawmillCoefficients:
    """Conversion percentages for the Sawmills and Planingmills nodes.

    The four sawmill by-product shares plus the implied :attr:`sawn_yield` are
    fractions of the raw sawtimber LOG INPUT and always partition it exactly
    (they sum to 1), so the Sawmills node mass-balances by construction. The
    sawn-timber yield is therefore *derived* from the four by-products rather
    than stored.

    Two ways to obtain the values:

    * **manual** — the researcher types the by-product shares (or the package
      defaults are used); the sawn yield is whatever is left over.
    * **treindustrien** — the sawn yield is measured from the Treindustrien
      produced-sawn-timber series and the by-product shares are rescaled to fill
      the remainder (see :meth:`from_treindustrien`).

    ``source`` records which of the two produced the values, for display/audit.
    """

    shrinkage: float = C.SHRINKAGE
    sawdust: float = C.SAWDUST
    cellulose: float = C.CELLULOSE
    scraps: float = C.SCRAPS
    planed_input_factor: float = C.PLANED_INPUT_FACTOR
    byproduct_planing: float = C.BYPRODUCT_PLANING
    source: str = "manual"

    @property
    def byproduct_total(self) -> float:
        """Combined share of the log input that is *not* sawn timber."""
        return self.shrinkage + self.sawdust + self.cellulose + self.scraps

    @property
    def sawn_yield(self) -> float:
        """Residual sawn-timber yield (mass balance: 1 − the four by-products)."""
        return 1.0 - self.byproduct_total

    @classmethod
    def from_treindustrien(
        cls,
        produced_sawn: pd.Series,
        initial_timber_vol: pd.Series,
        *,
        base: "SawmillCoefficients | None" = None,
    ) -> "SawmillCoefficients":
        """Derive coefficients from the Treindustrien produced-sawn series.

        The sawn yield is a single window-averaged ratio,
        ``Σ produced_sawn / Σ initial_timber_vol`` over the years where the log
        input is positive (years present in one series but not the other would
        otherwise bias the average). Treindustrien does not report the *split* of
        the non-sawn portion, so the four by-product shares from ``base`` are kept
        in the same proportion and rescaled to fill the new residual
        ``1 − sawn_yield``. The planing coefficients are not measured by
        Treindustrien and are carried over from ``base`` unchanged.

        ``produced_sawn`` and ``initial_timber_vol`` must share a year index.
        """
        base = base or cls()
        mask = initial_timber_vol > 0
        total_input = float(initial_timber_vol[mask].sum())
        total_sawn = float(produced_sawn[mask].sum())
        if total_input <= 0:
            raise ValueError(
                "Cannot derive sawn yield from Treindustrien: no positive log "
                "input in the overlapping years."
            )
        sawn_yield = total_sawn / total_input
        residual = 1.0 - sawn_yield
        scale = residual / base.byproduct_total if base.byproduct_total else 0.0
        return cls(
            shrinkage=base.shrinkage * scale,
            sawdust=base.sawdust * scale,
            cellulose=base.cellulose * scale,
            scraps=base.scraps * scale,
            planed_input_factor=base.planed_input_factor,
            byproduct_planing=base.byproduct_planing,
            source="treindustrien",
        )


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


def _initial_timber_vol(
    trade_df: pd.DataFrame,
    sankey_df: pd.DataFrame,
    treindustrien_inventory: pd.DataFrame,
    all_years,
) -> pd.Series:
    """Raw sawtimber volume entering the saw, reindexed by year.

    Domestic sawlogs (``* -> Sawmills`` in ``sankey_df``) plus imported saw wood,
    less the year-over-year timber-stock change. This is the denominator the
    by-product yields are applied to (see :func:`create_flow_rows`) and the base
    for the Treindustrien-derived sawn yield (see
    :meth:`SawmillCoefficients.from_treindustrien`).
    """
    inventory_df = sawmills_inventory_changes(treindustrien_inventory)
    inventory_timber = _yearly_series(
        inventory_df, inventory_df["Category"] == "Timber inventory",
        "VolumeChange", all_years,
    )
    import_saw_wood = _yearly_series(
        trade_df, trade_df["source"] == "Import saw wood", "volume", all_years)
    sankey_inflow = _yearly_series(
        sankey_df, sankey_df["target"] == "Sawmills", "volume", all_years)
    return pd.Series(
        sankey_inflow.to_numpy()
        - inventory_timber.to_numpy()
        + import_saw_wood.to_numpy(),
        index=all_years,
    )


def resolve_coefficients(
    source: str,
    trade_df: pd.DataFrame,
    sankey_df: pd.DataFrame,
    treindustrien_production: pd.DataFrame,
    treindustrien_inventory: pd.DataFrame,
    base: SawmillCoefficients | None = None,
) -> SawmillCoefficients:
    """Resolve the coefficients to use for one assembly run.

    ``source="manual"`` returns ``base`` (defaults if omitted) unchanged.
    ``source="treindustrien"`` derives the sawn yield from the produced-volume
    data using the same ``initial_timber_vol`` that :func:`create_flow_rows`
    will see, so the displayed and applied coefficients always agree.
    """
    base = base or SawmillCoefficients()
    if source != "treindustrien":
        return replace(base, source="manual")

    all_years = sorted(treindustrien_production["year"].unique())
    prod = treindustrien_production.set_index("year").reindex(all_years)
    initial_timber_vol = _initial_timber_vol(
        trade_df, sankey_df, treindustrien_inventory, all_years)
    return SawmillCoefficients.from_treindustrien(
        prod["Volume_sawn"], initial_timber_vol, base=base)


def create_flow_rows(
    trade_df: pd.DataFrame,
    sankey_df: pd.DataFrame,
    treindustrien_production: pd.DataFrame,
    treindustrien_inventory: pd.DataFrame,
    coefficients: SawmillCoefficients | None = None,
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
    coefficients : SawmillCoefficients, optional
        Conversion percentages for the sawmill by-products and the planing
        process. Defaults to the package constants (manual mode). Pass a
        Treindustrien-derived instance (see :func:`resolve_coefficients`) to use
        data-driven yields instead.

    Returns
    -------
    DataFrame with columns [year, volume, source, target, origin].
    """
    coef = coefficients or SawmillCoefficients()
    all_years = sorted(treindustrien_production["year"].unique())

    prod = treindustrien_production.set_index("year").reindex(all_years).reset_index()
    year = prod["year"]
    planed_vol = prod["Volume_planed"]
    imp_vol = prod["Volume_imp"]

    # --- Inventory stock changes, reindexed by year ---
    inventory_df = sawmills_inventory_changes(treindustrien_inventory)
    inventory_sawn = _yearly_series(
        inventory_df, inventory_df["Category"] == "sawn timber inventory",
        "VolumeChange", all_years,
    )

    # --- Trade series, all reindexed by year ---
    # NB: 'Export sawn wood' / 'Export planed wood' live in the TARGET column
    # (see ingest.trade.assign_target_source_trade), while 'Import *' labels
    # live in the SOURCE column.
    #
    # Raw-log re-exports ('Export Sawmills') are NOT handled here: they are
    # routed around the Sawmills node upstream (see assemble._bypass_raw_log_
    # reexports), so the logs reaching this function are only those actually
    # sawn. This keeps the by-product yields (e.g. 26% cellulose) as shares of
    # sawn-log input and lets the Sawmills node balance.
    export_sawn_wood = _yearly_series(
        trade_df, trade_df["target"] == "Export sawn wood", "volume", all_years)
    export_planed_wood = _yearly_series(
        trade_df, trade_df["target"] == "Export planed wood", "volume", all_years)

    # Volume that actually enters the saw: domestic sawlogs + imports - stock change.
    initial_timber_vol = _initial_timber_vol(
        trade_df, sankey_df, treindustrien_inventory, all_years)

    # By-products as shares of the raw INPUT volume (fix #2), using the resolved
    # (manual or Treindustrien-derived) coefficients.
    water_vol = initial_timber_vol * coef.shrinkage
    sawdust_vol = initial_timber_vol * coef.sawdust
    cellulose_vol = initial_timber_vol * coef.cellulose
    scraps_vol = initial_timber_vol * coef.scraps

    # Mass-balanced sawn-timber residual to WPP (fix #3).
    sawn_vol_implied = initial_timber_vol * coef.sawn_yield
    sawn_vol_to_planed = planed_vol.to_numpy() * coef.planed_input_factor
    sawn_vol_to_planed = pd.Series(sawn_vol_to_planed, index=all_years)
    left_over_volume = (
        sawn_vol_implied
        - sawn_vol_to_planed
        - export_sawn_wood
        - inventory_sawn
    )

    planed_byproducts = sawn_vol_to_planed * coef.byproduct_planing
    # Domestically planed wood routed to WPP, after export and impregnation.
    # NB: imported planed wood is NOT added here — it reaches WPP via its own
    # direct trade edge ('Import planed wood' -> WPP). Including it would inflate
    # the Planingmills outflow and break that node's mass balance.
    sum_planed_wood = planed_vol.to_numpy() - export_planed_wood.to_numpy()
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
