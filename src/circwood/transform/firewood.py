"""Firewood flow construction from SSB table 11181."""

from __future__ import annotations

import pandas as pd

from circwood import constants as C


def make_flow(
    df: pd.DataFrame,
    source: str,
    target: str,
    *,
    value_col: str = "value",
    year_col: str = "år",
    origin: str = "SSB table 11181",
) -> pd.DataFrame:
    """Build a [source, target, volume, year, origin] flow frame from ``df``."""
    return pd.DataFrame({
        "source": source,
        "target": target,
        "volume": df[value_col].astype(float),
        "year": df[year_col].astype(int),
        "origin": origin,
    })


def create_firewood_rows(
    tbl_11181: pd.DataFrame, percent_spruce: float = C.FIREWOOD_PERCENT_SPRUCE
) -> pd.DataFrame:
    """Split firewood into Spruce/Pine/Non-coniferous flows (+ standing-wood edges).

    ``tbl_11181`` must have columns ['value' (1000 m3), 'år', 'virkesgruppe'].
    """
    df = tbl_11181.copy()
    df["value"] = df["value"] * 1000  # 1000 m3 -> m3

    conifer = df[df["virkesgruppe"] != "Ved til brensel lauv"]
    non_conifer = df[df["virkesgruppe"] == "Ved til brensel lauv"]

    spruce_ratio = percent_spruce / 100.0
    pine_ratio = 1 - spruce_ratio

    flows = []
    for species, ratio in [("Spruce", spruce_ratio), ("Pine", pine_ratio)]:
        sub = conifer.assign(value=conifer["value"] * ratio)
        flows.append(make_flow(sub, species, "Firewood"))
        flows.append(make_flow(sub, "Standing wood", species))

    flows.append(make_flow(non_conifer, "Non-coniferous", "Firewood"))
    flows.append(make_flow(non_conifer, "Standing wood", "Non-coniferous"))

    return pd.concat(flows, ignore_index=True)
