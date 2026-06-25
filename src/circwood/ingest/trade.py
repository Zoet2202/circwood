"""Trade flow mapping for SSB table 08801 (imports/exports)."""

from __future__ import annotations

import pandas as pd

# Mapping of (trade type, direction) -> (source, target).
# NB: export labels live in the TARGET column, import labels in the SOURCE
# column. The sawing transform relies on this convention.
TRADE_MAPPING = {
    "impregnated": {
        "import": ("Import impregnated", "Wood Product processing & Logistics"),
        "eksport": ("Impregnation", "Export Impregnation"),
    },
    "pulpwood": {
        "import": ("Import pulpwood", "Pulpmills"),
        "eksport": ("Pulpmills", "Export pulpwood"),
    },
    "saw wood": {
        "import": ("Import saw wood", "Sawmills"),
        "eksport": ("Sawmills", "Export Sawmills"),
    },
    "pellets": {
        "import": ("Import pellets", "Firewood"),
        "eksport": ("Sawmills", "Export pellets"),
    },
    "firewood": {
        "import": ("Import Firewood", "Firewood"),
        "eksport": ("Firewood", "Export Firewood"),
    },
    "planed wood": {
        "import": ("Import planed wood", "Wood Product processing & Logistics"),
        "eksport": ("Planingmills", "Export planed wood"),
    },
    "sawn wood": {
        "import": ("Import sawn wood", "Wood Product processing & Logistics"),
        "eksport": ("Sawmills", "Export sawn wood"),
    },
}


def _source_target(row: pd.Series) -> pd.Series:
    trade_type = str(row["type"]).strip().lower()
    direction = str(row["import/eksport"]).strip().lower()
    try:
        source, target = TRADE_MAPPING[trade_type][direction]
    except KeyError:
        source, target = None, None
    return pd.Series([source, target])


def assign_target_source_trade(trade_df: pd.DataFrame) -> pd.DataFrame:
    """Add 'source'/'target' columns from 'type' and 'import/eksport'."""
    trade_df = trade_df.copy()
    trade_df[["source", "target"]] = trade_df.apply(_source_target, axis=1)
    return trade_df
