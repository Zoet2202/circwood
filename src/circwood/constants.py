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

# Years the generated dataset covers. Narrower than what SSB will hand over:
# the source tables' coverage differs (trade 08801 reaches back to 1988, harvest
# 07410 starts in 2012, waste 10513 currently ends in 2024), and rows outside
# the *intersection* are the ones that make the dataset misleading — a year with
# trade but no harvest shows exports flowing out of species pools nothing flows
# into, and a year where one table has published and another has not shows a
# partial picture that reads as a real collapse or spike.
#
# So the dataset is clipped to the range every source table covers. Widen
# DATASET_MAX_YEAR only when *all* tables have published the new year; that is a
# deliberate act, and the vintage diff (Phase B) is what makes it visible.
DATASET_MIN_YEAR = 2012
DATASET_MAX_YEAR = 2024

# ---------------------------------------------------------------------------
# Harvest / standing-wood coefficients
# ---------------------------------------------------------------------------
BARK_PERCENTAGE = 0.10        # share of roundwood that is bark
HARVEST_RESIDUE_PERCENTAGE = 0.10  # residues left at harvest, share of removals

# ---------------------------------------------------------------------------
# Sawing (Sawmills node) — by-product yields as shares of sawtimber LOG INPUT.
#
# These are deliberately applied to the raw input volume (``initial_timber_vol``),
# NOT to a post-shrinkage "dry" volume. Applying them to the dry volume was one
# of the three bugs documented in Sawing_function_diagnosis.docx (it silently
# scaled every by-product by (1 - SHRINKAGE) and under-reported the pulp share).
#
# These values are the *manual defaults*. A researcher can override them, or have
# the sawn yield derived from the Treindustrien produced-volume dataset, via
# ``circwood.transform.sawing.SawmillCoefficients`` (exposed in the Streamlit
# app's "Sawmill & planing coefficients" control).
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

# ---------------------------------------------------------------------------
# End-of-Life (SSB 10513, waste wood delivered to incineration)
# ---------------------------------------------------------------------------
# SSB 10513 publishes waste in *1 000 tonnes* (json-stat2 unit "1 000 tonn",
# confirmed live 2026-11-08). Multiply by this to get kilograms. The shipped
# dataset carried this edge 1 000x too small until Phase K because the old
# conversion treated the unit as tonnes.
EOL_UNIT_KG = 1_000_000.0
# Basic density used to turn waste-wood mass into roundwood-equivalent m3.
# Mixed waste wood (sawn products, panels, pallets) at roughly 480 kg dry
# matter per m3; the value the paper's reference spreadsheet used.
WASTE_WOOD_DENSITY_KG_M3 = 483.0

# ---------------------------------------------------------------------------
# Carbon display conversions (Phase G)
# ---------------------------------------------------------------------------
# kg CO2 per kg C: molar masses 44.0095 / 12.0107. A display option only; the
# dataset stays in kg C.
CO2_PER_C = 44.0095 / 12.0107

# ---------------------------------------------------------------------------
# Empirical HWP fraction beta (Phase F, ``circwood.analysis.hwp``)
# ---------------------------------------------------------------------------
# The common assumed beta in the rotation literature (ROADMAP Phase F). Which
# product pool it denotes is unverified (papers/ not checked). Only used to
# report the divergence of the measured beta; it never enters a calculation.
LITERATURE_BETA = 0.55

# Largest closure gap (share of harvest carbon) a year may carry before
# ``beta_series`` refuses it. A closure gap is wood that enters a process node
# and is not routed anywhere (inflow > outflow). Over 2012-2023 the largest
# per-species gap is ~0.014 (2020); 2024 reaches ~0.37 and is refused.
BETA_CLOSURE_TOL = 0.02

# Largest share of a pool's or process node's outflow that may exceed its
# inflow (output with no recorded input; every real import or withdrawal is an
# explicit edge). Largest in the data 2012-2023: 0.1141 (Impregnation 2021).
BETA_EXCESS_OUT_TOL = 0.15

# Years exported to data/processed/beta_series.csv. Narrower than the dataset
# window on purpose: in 2024 Sawmills receives 4,519,533 m3 and emits 0, because
# the Treindustrien production series (data/raw/treindustrien_production_data.csv)
# ends in 2023. Long-term USE collapses to ~0.5 Mm3 and the all-species closure
# gap reaches ~0.32, so 2024 is a coverage break, not a measurement. DATASET_MAX_YEAR is
# deliberately left unchanged (narrowing it is a human decision).
BETA_MIN_YEAR = DATASET_MIN_YEAR
BETA_MAX_YEAR = 2023

# Gates for the one-at-a-time sensitivity scenarios (Phase G,
# ``circwood.analysis.sensitivity``). A coefficient pushed to its bound widens
# an existing data imbalance (Planingmills 2020 sits at a 9 % volume gap), so
# a scenario may cross the export gates while the central run passes them;
# the scenarios therefore run under these looser gates. The central run keeps
# the export gates, so it equals the exported beta.
BETA_SENSITIVITY_CLOSURE_TOL = 0.05
BETA_SENSITIVITY_EXCESS_OUT_TOL = 0.25

__all__ = [name for name in dir() if name.isupper()]
