"""Embodied-carbon overlay for the wood-flow Sankey.

Carbon mass of a flow = ``volume (m3) × Σ_species share × density(species, state)``.

The model is **species-accurate**: the spruce / pine / non-coniferous mix is
tracked as wood flows through the mixing nodes (Sawmills, Pulpmills, ...) rather
than collapsed to a single species. All coefficients and per-node assumptions
are researcher-editable in ``config/carbon_densities.csv`` and
``config/carbon_nodes.csv`` — see :mod:`circwood.carbon.species_model`.
"""

from __future__ import annotations

import pandas as pd

from circwood.carbon.species_model import (
    add_carbon,
    load_model,
    species_mix,
)


def carbon_sankey(df: pd.DataFrame) -> pd.DataFrame:
    """Return a Sankey-shaped frame whose ``volume`` column holds carbon (kg C).

    Lets the existing viz consume carbon with no special-casing — the 'volume'
    column simply carries carbon mass instead of m3.
    """
    out = add_carbon(df, out_col="carbon")
    out["volume"] = out["carbon"]
    return out.drop(columns="carbon")


__all__ = ["add_carbon", "carbon_sankey", "species_mix", "load_model"]
