"""Tests for the embodied-carbon overlay."""

from __future__ import annotations

import pandas as pd
import pytest

from circwood import build_sankey
from circwood.carbon import add_carbon, carbon_sankey, density_for_flow


def test_density_matches_spreadsheet_model():
    # Density table (kg C / m3) from Carbon-Excel.xlsx, CarbonTable.
    assert density_for_flow("Standing wood", "Spruce") == 190
    assert density_for_flow("Standing wood", "Pine") == 215
    assert density_for_flow("Standing wood", "Non-coniferous") == 240
    assert density_for_flow("Standing wood", "Bark") == 240   # bark -> non-conif raw
    # Source-driven assignments
    assert density_for_flow("Spruce", "Sawmills") == 190      # raw roundwood
    assert density_for_flow("Sawmills", "Pulpmills") == 203   # processed -> spruce dry
    assert density_for_flow("Pulpmills", "Short-term USE") == 190
    assert density_for_flow("Import Firewood", "Firewood") == 268


def test_carbon_equals_volume_times_density():
    df = pd.DataFrame({
        "source": ["Standing wood", "Sawmills"],
        "target": ["Spruce", "Pulpmills"],
        "volume": [1000.0, 2000.0],
        "year": [2015, 2015],
        "origin": ["t", "t"],
    })
    out = add_carbon(df)
    assert out["carbon"].iloc[0] == pytest.approx(1000.0 * 190)
    assert out["carbon"].iloc[1] == pytest.approx(2000.0 * 203)


def test_every_cached_flow_gets_a_density():
    """Guards against new/renamed nodes silently dropping out of the model."""
    df = build_sankey()
    out = add_carbon(df, default=0.0)
    uncovered = out[out["carbon"] == 0]
    assert uncovered.empty, (
        "uncovered sources: " + ", ".join(sorted(uncovered["source"].unique()))
    )


def test_carbon_sankey_preserves_schema():
    df = build_sankey()
    cs = carbon_sankey(df)
    assert list(cs.columns) == ["source", "target", "volume", "year", "origin"]
    assert len(cs) == len(df)
    assert cs["volume"].gt(0).all()
