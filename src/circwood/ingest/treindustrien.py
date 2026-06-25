"""Loaders for the Treindustrien annual-report production/inventory CSV."""

from __future__ import annotations

import pandas as pd

from circwood import config


def load_treindustrien(path=None) -> pd.DataFrame:
    """Load the raw Treindustrien CSV [year, Category, Volume]."""
    return pd.read_csv(path or config.TREINDUSTRIEN_CSV)


def production_wide(treindustrien_df: pd.DataFrame) -> pd.DataFrame:
    """Pivot production categories into [year, Volume_sawn/planed/imp]."""
    def pick(category: str, new_name: str) -> pd.DataFrame:
        return (
            treindustrien_df[treindustrien_df["Category"] == category]
            .drop(columns="Category")
            .rename(columns={"Volume": new_name})
        )

    df_sawn = pick("produced sawn timber", "Volume_sawn")
    df_planed = pick("produced planed timber", "Volume_planed")
    df_imp = pick("produsert impregnated timber", "Volume_imp")

    return df_sawn.merge(df_planed, on="year").merge(df_imp, on="year")


def signed_inventory(treindustrien_df: pd.DataFrame) -> pd.DataFrame:
    """Return the frame with a SIGNED ``VolumeChange`` (year diff per Category).

    Direction must be preserved (do not abs() here); the inventory transform
    routes flows by the sign and takes the magnitude for the link width.
    """
    df = treindustrien_df.sort_values(["Category", "year"]).copy()
    df["VolumeChange"] = df.groupby("Category")["Volume"].diff()
    return df
