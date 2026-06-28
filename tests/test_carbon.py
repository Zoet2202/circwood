"""Tests for the species-accurate embodied-carbon overlay."""

from __future__ import annotations

import pandas as pd
import pytest

from circwood import build_sankey
from circwood.carbon import add_carbon, carbon_sankey, load_model, species_mix


def test_model_loads_and_has_density_table():
    model = load_model()
    assert model.density("spruce", "raw") == 190
    assert model.density("pine", "dry") == 228
    assert model.density("non-coniferous", "dry") == 268


def test_species_mix_sums_to_one_everywhere():
    df = build_sankey()
    mix = species_mix(df)
    totals = mix[["spruce", "pine", "non-coniferous"]].sum(axis=1)
    assert ((totals - 1.0).abs() < 1e-6).all()


def test_pure_pool_source_keeps_its_species():
    """A flow straight out of a species pool uses that species only."""
    df = pd.DataFrame({
        "source": ["Harvested wood", "Spruce"],
        "target": ["Spruce", "Sawmills"],
        "volume": [1000.0, 1000.0],
        "year": [2015, 2015],
        "origin": ["t", "t"],
    })
    out = add_carbon(df)
    # Spruce -> Sawmills: spruce raw density = 190
    spruce_row = out[(out.source == "Spruce")].iloc[0]
    assert spruce_row["carbon"] == pytest.approx(1000.0 * 190)


def test_mixing_node_blends_input_species():
    """Sawmills output carbon reflects the species mix of its inputs."""
    df = pd.DataFrame({
        "source": ["Spruce", "Pine", "Sawmills"],
        "target": ["Sawmills", "Sawmills", "Pulpmills"],
        "volume": [75.0, 25.0, 50.0],
        "year": [2015, 2015, 2015],
        "origin": ["t", "t", "t"],
    })
    mix = species_mix(df).set_index("node")
    assert mix.loc["Sawmills", "spruce"] == pytest.approx(0.75)
    assert mix.loc["Sawmills", "pine"] == pytest.approx(0.25)

    out = add_carbon(df)
    saw_out = out[(out.source == "Sawmills")].iloc[0]
    # Sawmills state = dry; density = 0.75*203 + 0.25*228
    expected = 50.0 * (0.75 * 203 + 0.25 * 228)
    assert saw_out["carbon"] == pytest.approx(expected)


def test_every_cached_flow_gets_carbon():
    df = build_sankey()
    out = add_carbon(df)
    assert (out["carbon"] > 0).all()


def test_carbon_sankey_preserves_schema():
    df = build_sankey()
    cs = carbon_sankey(df)
    assert list(cs.columns) == ["source", "target", "volume", "year", "origin"]
    assert len(cs) == len(df)
    assert cs["volume"].gt(0).all()


def test_config_is_editable(tmp_path):
    """Editing the density CSV changes the result (researcher workflow)."""
    dens = tmp_path / "d.csv"
    dens.write_text(
        "species,state,kg_c_per_m3\n"
        "spruce,raw,100\nspruce,dry,100\npine,raw,100\n"
        "pine,dry,100\nnon-coniferous,raw,100\nnon-coniferous,dry,100\n",
        encoding="utf-8",
    )
    nodes = tmp_path / "n.csv"
    nodes.write_text(
        "node,state,mix_mode,spruce_share,pine_share,nonconif_share\n"
        "Spruce,raw,fixed,1,0,0\nSawmills,dry,propagate,,,\n",
        encoding="utf-8",
    )
    model = load_model(densities_file=dens, nodes_file=nodes)
    df = pd.DataFrame({
        "source": ["Spruce"], "target": ["Sawmills"], "volume": [1000.0],
        "year": [2015], "origin": ["t"],
    })
    out = add_carbon(df, model=model)
    assert out["carbon"].iloc[0] == pytest.approx(1000.0 * 100)
