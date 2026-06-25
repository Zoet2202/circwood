"""Boardmills and Pulpmills output-splitting transforms."""

from __future__ import annotations

import pandas as pd

from circwood import constants as C


def boardmill_process(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Split Boardmills inflows into a loss share and a panels share (to WPP)."""
    loss_factor = C.BOARDMILL_LOSS
    panels_factor = 1 - loss_factor

    filtered = dataframe.loc[dataframe["target"] == "Boardmills"].copy()
    filtered["source"] = "Boardmills"

    rows_loss = filtered.copy()
    rows_loss["volume"] = rows_loss["volume"] * loss_factor
    rows_loss["target"] = "Loss Boardmills"

    rows_panels = filtered.copy()
    rows_panels["volume"] = rows_panels["volume"] * panels_factor
    rows_panels["target"] = "Wood Product processing & Logistics"

    return pd.concat([dataframe, rows_loss, rows_panels], ignore_index=True)


def pulpmills_process(
    dataframe: pd.DataFrame,
    short_term_products: float = C.PULP_SHORT_TERM,
    energy: float = C.PULP_ENERGY,
    loss: float = C.PULP_LOSS,
) -> pd.DataFrame:
    """Split Pulpmills inflows into short-term use, energy, and loss shares."""
    short_term_ratio = short_term_products / 100
    energy_ratio = energy / 100
    loss_ratio = loss / 100

    filtered = dataframe[dataframe["target"] == "Pulpmills"].copy()
    filtered["source"] = "Pulpmills"

    rows_short_term = filtered.copy()
    rows_short_term["volume"] = rows_short_term["volume"] * short_term_ratio
    rows_short_term["target"] = "Short-term USE"

    rows_energy = filtered.copy()
    rows_energy["volume"] = rows_energy["volume"] * energy_ratio
    rows_energy["target"] = "Energy"

    rows_loss = filtered.copy()
    rows_loss["volume"] = rows_loss["volume"] * loss_ratio
    rows_loss["target"] = "Loss"

    return pd.concat(
        [dataframe, rows_short_term, rows_energy, rows_loss], ignore_index=True
    )
