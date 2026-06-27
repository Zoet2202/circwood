"""Embodied-carbon model for the wood-flow Sankey.

Carbon mass of a flow = ``volume (m3) × density (kg C / m3)``, where the density
is chosen from the (species, state) assigned to the flow. The model and its
coefficients come from ``data/raw/Carbon-Excel.xlsx`` (sheet ``CarbonTable``):

* Density table (kg C / m3) by species and state.
* Each flow's (species, state) is determined by its **source** node, except
  flows leaving ``Standing wood`` (the raw harvest), which take the species of
  their **target** pool.

This reproduces the spreadsheet exactly, e.g. Standing wood → Spruce uses
Spruce/Raw = 190 kg C/m3 (6,624,431 m3 → 1.2586e9 kg C).
"""

from __future__ import annotations

import pandas as pd

# Density table (kg C per m3), keyed by (species, state) in lower case.
CARBON_DENSITY: dict[tuple[str, str], float] = {
    ("spruce", "raw"): 190,
    ("spruce", "dry"): 203,
    ("pine", "raw"): 215,
    ("pine", "dry"): 228,
    ("non-coniferous", "raw"): 240,
    ("non-coniferous", "dry"): 268,
}

# (species, state) assigned by SOURCE node. Raw roundwood keeps its species at
# the "raw" state; processed/dried wood is treated as Spruce/Dry (203), matching
# the spreadsheet's convention.
_SOURCE_SPECIES_STATE: dict[str, tuple[str, str]] = {
    # Raw roundwood pools / raw-state nodes
    "Spruce": ("spruce", "raw"),
    "Pine": ("pine", "raw"),
    "Non-coniferous": ("non-coniferous", "raw"),
    "Pulpmills": ("spruce", "raw"),
    "Timber Inventory": ("spruce", "raw"),
    # Processed / dried wood -> Spruce Dry
    "Sawmills": ("spruce", "dry"),
    "Planingmills": ("spruce", "dry"),
    "Boardmills": ("spruce", "dry"),
    "Impregnation": ("spruce", "dry"),
    "Wood Product processing & Logistics": ("spruce", "dry"),
    "Long-term USE": ("spruce", "dry"),
    "End-of-Life": ("spruce", "dry"),
    "Import Boards": ("spruce", "dry"),
    "planed timber inventory": ("spruce", "dry"),
    "Impregnated inventory": ("spruce", "dry"),
    # Imports (processed -> Spruce Dry)
    "Import pulpwood": ("spruce", "dry"),
    "Import saw wood": ("spruce", "dry"),
    "Import sawn wood": ("spruce", "dry"),
    "Import planed wood": ("spruce", "dry"),
    "Import impregnated": ("spruce", "dry"),
    # Firewood
    "Firewood": ("non-coniferous", "raw"),
    "Import Firewood": ("non-coniferous", "dry"),
}

# For flows leaving 'Standing wood', assign by TARGET pool.
_STANDING_TARGET_SPECIES_STATE: dict[str, tuple[str, str]] = {
    "Spruce": ("spruce", "raw"),
    "Pine": ("pine", "raw"),
    "Non-coniferous": ("non-coniferous", "raw"),
    "Bark": ("non-coniferous", "raw"),
    "Residues from harvest": ("non-coniferous", "raw"),
    "Mortality": ("non-coniferous", "raw"),
}

STANDING_WOOD = "Standing wood"


def density_for_flow(source: str, target: str, default: float = 0.0) -> float:
    """Return the carbon density (kg C/m3) for a flow from ``source`` to ``target``."""
    if source == STANDING_WOOD:
        key = _STANDING_TARGET_SPECIES_STATE.get(target)
    else:
        key = _SOURCE_SPECIES_STATE.get(source)
    if key is None:
        return default
    return CARBON_DENSITY[key]


def add_carbon(df: pd.DataFrame, out_col: str = "carbon", default: float = 0.0) -> pd.DataFrame:
    """Return a copy of ``df`` with a carbon column (kg C) = volume × density."""
    out = df.copy()
    density = [
        density_for_flow(s, t, default)
        for s, t in zip(out["source"], out["target"])
    ]
    out[out_col] = out["volume"].astype(float) * pd.Series(density, index=out.index)
    return out


def carbon_sankey(df: pd.DataFrame, default: float = 0.0) -> pd.DataFrame:
    """Return a Sankey-shaped frame whose ``volume`` column holds carbon (kg C).

    Lets the existing viz consume carbon with no special-casing — the 'volume'
    column simply carries carbon mass instead of m3.
    """
    out = add_carbon(df, out_col="carbon", default=default)
    out["volume"] = out["carbon"]
    return out.drop(columns="carbon")
