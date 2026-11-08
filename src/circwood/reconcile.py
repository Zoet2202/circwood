"""Reconcile pipeline outputs against their source aggregates.

Cheap regression guards that catch a future change silently mangling totals.
Two kinds of check:

* **Source reconciliation** — pipeline throughput vs the authoritative input
  files we have offline (the Treindustrien production CSV).
* **Control totals** — pipeline aggregates vs documented published figures
  (SSB table totals), with the reference values recorded in ``CONTROL_TOTALS``.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from circwood import constants as C
from circwood.ingest.treindustrien import load_treindustrien, production_wide

SPECIES_POOLS = ("Spruce", "Pine", "Non-coniferous")

# Documented published reference totals (m3), with their window and source.
CONTROL_TOTALS: dict[str, dict[str, Any]] = {
    # Domestic sawtimber input, SSB table 07410, 2012-2022 (per
    # Sawing_function_diagnosis.docx). Equals species -> (Sawmills + raw-log
    # re-export), since the re-export bypass only moves volume between those two
    # species-sourced targets.
    "domestic_sawtimber_2012_2022": {
        "value": 61_445_000.0, "min_year": 2012, "max_year": 2022,
        "source": "SSB 07410 (diagnosis report)",
    },
}


def _window(df: pd.DataFrame, min_year: int, max_year: int) -> pd.DataFrame:
    return df[(df["year"] >= min_year) & (df["year"] <= max_year)]


def treindustrien_reconciliation(
    df: pd.DataFrame, min_year: int = C.MIN_YEAR, max_year: int = C.MAX_YEAR
) -> pd.DataFrame:
    """Compare pipeline mill throughput to the Treindustrien source CSV.

    Returns a tidy frame [metric, pipeline, source, rel_diff].
    """
    w = _window(df, min_year, max_year)
    tre = production_wide(load_treindustrien())
    tw = _window(tre, min_year, max_year)

    checks = []

    saw_plan = w[(w.source == "Sawmills") & (w.target == "Planingmills")].volume.sum()
    checks.append(("Sawmills->Planingmills vs planed x1.05",
                   saw_plan, tw["Volume_planed"].sum() * C.PLANED_INPUT_FACTOR))

    plan_imp = w[(w.source == "Planingmills") & (w.target == "Impregnation")].volume.sum()
    checks.append(("Planingmills->Impregnation vs impregnated",
                   plan_imp, tw["Volume_imp"].sum()))

    out = pd.DataFrame(checks, columns=["metric", "pipeline", "source"])
    out["rel_diff"] = out["pipeline"] / out["source"] - 1.0
    return out


def domestic_sawtimber(df: pd.DataFrame, min_year: int, max_year: int) -> float:
    """Total domestic sawtimber harvest reaching the sawmill (incl. net re-export).

    Raw-sawlog re-exports are now netted into ``Net export skurlast`` (the species
    pool's outflow splits between ``Sawmills`` and that net node), so the harvest
    total is the sum of both.
    """
    w = _window(df, min_year, max_year)
    return w[
        w.source.isin(SPECIES_POOLS) & w.target.isin(["Sawmills", "Net export skurlast"])
    ].volume.sum()


def annual_roundwood_harvest(df: pd.DataFrame, min_year: int, max_year: int) -> float:
    """Mean annual roundwood harvest (Harvested wood -> species pools), m3/yr."""
    w = _window(df, min_year, max_year)
    total = w[(w.source == "Harvested wood") & w.target.isin(SPECIES_POOLS)].volume.sum()
    return total / (max_year - min_year + 1)
