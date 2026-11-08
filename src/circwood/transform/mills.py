"""Boardmills, Pulpmills and WPP output-splitting transforms and their coefficients."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from circwood import constants as C


@dataclass(frozen=True)
class MillCoefficients:
    """The fixed downstream splits, overridable for a rebuild or a sensitivity run.

    Defaults are the package constants. The pulp shares are percentages that
    must sum to 100 (as in ``constants.py``); the WPP energy share is derived
    so the WPP split always partitions its inflow. Validated on construction so
    a sensitivity scenario cannot silently create or destroy volume.
    """

    boardmill_loss: float = C.BOARDMILL_LOSS
    pulp_short_term: float = C.PULP_SHORT_TERM
    pulp_energy: float = C.PULP_ENERGY
    pulp_loss: float = C.PULP_LOSS
    wpp_long_term: float = C.WPP_LONG_TERM

    def __post_init__(self) -> None:
        if not 0 <= self.boardmill_loss < 1:
            raise ValueError(f"boardmill_loss must be in [0, 1), got {self.boardmill_loss}.")
        if not 0 < self.wpp_long_term <= 1:
            raise ValueError(f"wpp_long_term must be in (0, 1], got {self.wpp_long_term}.")
        total = self.pulp_short_term + self.pulp_energy + self.pulp_loss
        if abs(total - 100.0) > 1e-9 or min(self.pulp_short_term, self.pulp_energy, self.pulp_loss) < 0:
            raise ValueError(f"pulp shares must be non-negative percentages summing to 100, got {total}.")

    @property
    def wpp_energy(self) -> float:
        return 1.0 - self.wpp_long_term


def boardmill_process(
    dataframe: pd.DataFrame, loss: float = C.BOARDMILL_LOSS
) -> pd.DataFrame:
    """Split Boardmills inflows into a loss share and a panels share (to WPP)."""
    loss_factor = loss
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
    rows_loss["target"] = "Loss Pulpmills"

    return pd.concat(
        [dataframe, rows_short_term, rows_energy, rows_loss], ignore_index=True
    )
