"""Model constants for the Circwood WP4 wood-flow pipeline.

Every magic number that drives the flow calculations lives here so the
assumptions behind the Sankey diagram are visible and auditable in one place.
Values were previously scattered across ``volume.py`` and ``functions.py``.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Year window
# ---------------------------------------------------------------------------
# Default analysis window used by the paper and the app (annual average).
MIN_YEAR = 2012
MAX_YEAR = 2022

# ---------------------------------------------------------------------------
# Harvest / standing-wood coefficients
# ---------------------------------------------------------------------------
BARK_PERCENTAGE = 0.10        # share of roundwood that is bark
HARVEST_RESIDUE_PERCENTAGE = 0.10  # residues left at harvest, share of removals
MORTALITY_FACTOR = 0.15       # share of annual increment lost to mortality
ROT_FACTOR = 1 / 15           # annual decay rate of dead-biomass stock

# ---------------------------------------------------------------------------
# Sawing (Sawmills node) — by-product yields as shares of sawtimber LOG INPUT.
#
# These are deliberately applied to the raw input volume (``initial_timber_vol``),
# NOT to a post-shrinkage "dry" volume. Applying them to the dry volume was one
# of the three bugs documented in Sawing_function_diagnosis.docx (it silently
# scaled every by-product by (1 - SHRINKAGE) and under-reported the pulp share).
# ---------------------------------------------------------------------------
SHRINKAGE = 0.05              # water loss
SAWDUST = 0.14               # -> Boardmills
CELLULOSE = 0.26             # -> Pulpmills (the headline ~26% chip yield)
SCRAPS = 0.05                # -> Energy
BYPRODUCT_PLANING = 0.05      # planing by-product share -> Boardmills

# Residual sawn-timber yield from a sawtimber log, by mass balance.
SAWN_YIELD = 1.0 - SHRINKAGE - SAWDUST - CELLULOSE - SCRAPS  # = 0.50

# Planed-timber throughput requires slightly more sawn input than it yields.
PLANED_INPUT_FACTOR = 1.05

# ---------------------------------------------------------------------------
# Downstream processing splits
# ---------------------------------------------------------------------------
BOARDMILL_LOSS = 0.05         # Boardmills loss; remainder -> WPP

# Pulpmills output split (percentages, must sum to 100).
PULP_SHORT_TERM = 86
PULP_ENERGY = 8
PULP_LOSS = 6

# Wood Product processing & Logistics output split.
WPP_LONG_TERM = 0.90
WPP_ENERGY = 0.10

# Firewood coniferous split between spruce and pine (percent spruce).
FIREWOOD_PERCENT_SPRUCE = 50.0

__all__ = [name for name in dir() if name.isupper()]
