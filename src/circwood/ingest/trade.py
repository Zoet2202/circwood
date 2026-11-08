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


#: Substring of the SSB ``varenummer`` label -> trade type, applied in order
#: (later matches win, so 'skurlast' is overridden by 'Impregnert ... skurlast').
#: Shared by the national 08801 pull and the per-partner 08799 pull so both
#: classify a commodity identically — a type that only one of them knew would
#: give a destination split with nothing to split.
VARENUMMER_KEYWORDS = (
    ("Impregnert", "impregnated"),
    ("Massevirke", "pulpwood"),
    ("Skurtømmer", "saw wood"),
    ("Trepeletter", "pellets"),
    ("brensel", "firewood"),
    ("høvellast", "planed wood"),
    ("skurlast", "sawn wood"),
)

#: Trade type -> the node(s) its exported volume ends up at in the *assembled*
#: dataset. Raw sawlogs, pulpwood and sawn timber are netted against imports
#: (``assemble.NET_EXPORT_PRODUCTS``), so their gross ``Export *`` targets are
#: replaced by ``Net export *`` nodes; the rest keep their gross export node.
#: Keys are the ``TRADE_MAPPING`` types; a type absent here is never split by
#: destination.
EXPORT_NODES_BY_TYPE = {
    "saw wood": ("Net export skurlast",),
    "pulpwood": ("Net export massevirke",),
    "sawn wood": ("Net export sawn timber",),
    "pellets": ("Export pellets",),
    "firewood": ("Export Firewood",),
    "planed wood": ("Export planed wood",),
    "impregnated": ("Export Impregnation",),
}


def classify_varenummer(labels: pd.Series) -> pd.Series:
    """Map SSB ``varenummer`` labels to a trade type (``'default'`` if none)."""
    out = pd.Series("default", index=labels.index, dtype=object)
    for keyword, trade_type in VARENUMMER_KEYWORDS:
        out[labels.str.contains(keyword, case=False, regex=False)] = trade_type
    return out


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
