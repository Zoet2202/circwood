"""Treindustrien inventory (stock-change) flow construction."""

from __future__ import annotations

import pandas as pd

# Direction of each inventory edge depends on the sign of VolumeChange:
#   change >= 0  -> stock build-up  (mill -> inventory)
#   change <  0  -> stock draw-down (inventory -> mill)
INVENTORY_MAPPING = {
    "Timber inventory": {
        "positive": ("Sawmills", "Timber Inventory"),
        "negative": ("Timber Inventory", "Sawmills"),
    },
    "sawn timber inventory": {
        "positive": ("Sawmills", "Timber Inventory"),
        "negative": ("Timber Inventory", "Sawmills"),
    },
    "planed timber inventory": {
        "positive": ("Planingmills", "planed timber inventory"),
        "negative": ("planed timber inventory", "Planingmills"),
    },
    "Impregnated inventory": {
        "positive": ("Impregnation", "Impregnated inventory"),
        "negative": ("Impregnated inventory", "Impregnation"),
    },
}


def get_source_target(row: pd.Series) -> tuple:
    """Return (source, target) for an inventory row by sign of VolumeChange."""
    cat = row["Category"]
    change = row["VolumeChange"]
    if cat in INVENTORY_MAPPING:
        branch = "positive" if change >= 0 else "negative"
        return INVENTORY_MAPPING[cat][branch]
    return (None, None)


def create_inventory_rows(treindustrien_df: pd.DataFrame) -> pd.DataFrame:
    """Build inventory stock-change flow rows from a Treindustrien frame.

    The caller is expected to have populated a signed ``VolumeChange`` column
    (year-over-year diff per Category) WITHOUT taking the absolute value, so
    routing direction is preserved. The link width is the absolute magnitude.
    """
    df = treindustrien_df.copy()
    df["source"], df["target"] = zip(*df.apply(get_source_target, axis=1))

    inventory = df[df["Category"].isin(INVENTORY_MAPPING.keys())].copy()
    inventory = inventory.dropna(subset=["source", "target", "VolumeChange"])

    return pd.DataFrame({
        "year": inventory["year"],
        "volume": inventory["VolumeChange"].abs(),
        "source": inventory["source"],
        "target": inventory["target"],
        "origin": "Treindustrien årsrapport",
    })
