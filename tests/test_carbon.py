"""Tests for the species-accurate, carbon-conserving embodied-carbon overlay (Phase G)."""

from __future__ import annotations

import pandas as pd
import pytest

from circwood import build_sankey
from circwood import constants as C
from circwood.carbon import add_carbon, carbon_sankey, load_model, species_mix
from circwood.carbon.species_model import node_densities
from circwood.pipeline import CARBON_COLUMNS, build_carbon
from circwood.validate import PROCESS_NODES, assert_carbon_balanced


def test_model_loads_and_has_density_table():
    model = load_model()
    assert model.density("spruce", "raw") == 190
    assert model.density("pine", "dry") == 228
    assert model.density("non-coniferous", "dry") == 268
    assert model.scenario == "central"


def test_density_scenarios_bracket_central():
    low, high = load_model(scenario="low"), load_model(scenario="high")
    central = load_model()
    for key, value in central.densities.items():
        assert low.densities[key] < value < high.densities[key], key
    with pytest.raises(ValueError, match="scenario must be"):
        load_model(scenario="median")


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


def test_mixing_node_conserves_carbon_and_blends_species():
    """Sawmills output carries the carbon that came in, at the blended raw density."""
    df = pd.DataFrame({
        "source": ["Spruce", "Pine", "Sawmills"],
        "target": ["Sawmills", "Sawmills", "Pulpmills"],
        "volume": [75.0, 25.0, 100.0],
        "year": [2015, 2015, 2015],
        "origin": ["t", "t", "t"],
    })
    mix = species_mix(df).set_index("node")
    assert mix.loc["Sawmills", "spruce"] == pytest.approx(0.75)
    assert mix.loc["Sawmills", "pine"] == pytest.approx(0.25)

    out = add_carbon(df)
    saw_out = out[(out.source == "Sawmills")].iloc[0]
    # No water leaves, so the wood leaves at the density it arrived with:
    # 0.75*190 + 0.25*215 per m3 — carbon in == carbon out.
    assert saw_out["carbon"] == pytest.approx(75 * 190 + 25 * 215)
    assert out[out.target == "Sawmills"]["carbon"].sum() == pytest.approx(saw_out["carbon"])


def test_water_edge_carries_no_carbon_and_uplifts_the_wood_density():
    """Sawmills -> Shrinkage is water: 0 kg C; the remaining wood is denser, so carbon is conserved."""
    df = pd.DataFrame({
        "source": ["Harvested wood", "Spruce", "Sawmills", "Sawmills"],
        "target": ["Spruce", "Sawmills", "Shrinkage", "Wood Product processing & Logistics"],
        "volume": [100.0, 100.0, 5.0, 95.0],
        "year": [2015] * 4, "origin": ["t"] * 4,
    })
    out = add_carbon(df).set_index("target")["carbon"]
    assert out["Shrinkage"] == 0.0
    assert out["Wood Product processing & Logistics"] == pytest.approx(100 * 190)
    dens = node_densities(df).set_index(["node", "species"])["kg_c_per_m3"]
    assert dens.loc[("Sawmills", "spruce")] == pytest.approx(190 / 0.95)


def test_withdrawal_only_inventory_keeps_last_years_density():
    """An inventory that only emits carries the density its wood went in with."""
    rows = [
        ("Harvested wood", "Spruce", 100.0, 2015), ("Spruce", "Sawmills", 100.0, 2015),
        ("Sawmills", "Shrinkage", 5.0, 2015), ("Sawmills", "Timber inventory", 95.0, 2015),
        ("Harvested wood", "Spruce", 1.0, 2016), ("Spruce", "Sawmills", 1.0, 2016),
        ("Timber inventory", "Sawmills", 50.0, 2016),
        ("Sawmills", "Wood Product processing & Logistics", 51.0, 2016),
    ]
    df = pd.DataFrame(rows, columns=["source", "target", "volume", "year"]).assign(origin="t")
    dens = node_densities(df).set_index(["year", "node", "species"])["kg_c_per_m3"]
    assert dens.loc[(2016, "Timber inventory", "spruce")] == pytest.approx(190 / 0.95)
    out = add_carbon(df)
    withdrawal = out[(out.year == 2016) & (out.source == "Timber inventory")]["carbon"].iloc[0]
    assert withdrawal == pytest.approx(50 * 190 / 0.95)


def test_every_cached_flow_gets_carbon_except_water():
    df = build_sankey()
    out = add_carbon(df)
    water = out["target"] == "Shrinkage"
    assert (out.loc[~water, "carbon"] > 0).all()
    assert (out.loc[water, "carbon"] == 0).all()
    assert water.any()


def test_shipped_carbon_is_conserved_at_process_nodes():
    """The Phase G fence: carbon in == carbon out at every process node over the paper window."""
    carbon = build_carbon()
    assert_carbon_balanced(carbon)
    w = carbon[(carbon.year >= C.MIN_YEAR) & (carbon.year <= C.MAX_YEAR)]
    for node in PROCESS_NODES:
        c_in = w[w.target == node].carbon.sum()
        c_out = w[w.source == node].carbon.sum()
        v_in = w[w.target == node].volume.sum()
        v_out = w[w.source == node].volume.sum()
        # Carbon imbalance equals the wood-volume imbalance, by construction.
        assert c_out / c_in - 1 == pytest.approx(v_out / v_in - 1, abs=2e-3), node


def test_carbon_balance_fence_bites_on_a_leak():
    frame = pd.DataFrame({
        "source": ["Spruce", "Sawmills"], "target": ["Sawmills", "Pulpmills"],
        "volume": [100.0, 100.0], "year": [2015, 2015], "carbon": [19000.0, 25000.0],
    })
    with pytest.raises(AssertionError, match="Sawmills"):
        assert_carbon_balanced(frame, nodes=["Sawmills"])
    with pytest.raises(KeyError):
        assert_carbon_balanced(frame.drop(columns="carbon"))


def test_build_carbon_adds_the_envelope_additively():
    df = build_carbon()
    assert list(df.columns) == CARBON_COLUMNS
    assert list(df.columns[:6]) == ["source", "target", "volume", "year", "origin", "carbon"]
    water = df["target"] == "Shrinkage"
    assert (df.loc[~water, "carbon_low"] < df.loc[~water, "carbon"]).all()
    assert (df.loc[~water, "carbon_high"] > df.loc[~water, "carbon"]).all()
    assert (df.loc[water, ["carbon_low", "carbon_high"]] == 0).all().all()
    # The envelope is the same model at the file's low/high densities (±10 % today).
    assert df["carbon_low"].sum() / df["carbon"].sum() == pytest.approx(0.90, abs=0.005)
    assert df["carbon_high"].sum() / df["carbon"].sum() == pytest.approx(1.10, abs=0.005)


def test_carbon_sankey_preserves_schema():
    df = build_sankey()
    cs = carbon_sankey(df)
    assert list(cs.columns) == list(df.columns)
    assert len(cs) == len(df)
    assert cs["volume"].ge(0).all()
    assert (cs.loc[cs["target"] != "Shrinkage", "volume"] > 0).all()


def test_carbon_imbalance_tracks_volume_imbalance_per_species():
    """Carbon follows the wood per species, not only in aggregate.

    At every process node, year and species over the paper window the relative
    carbon imbalance equals the relative volume imbalance, divided by (1 - w)
    where the node sheds the share w of its input as water (Sawmills ->
    Shrinkage carries volume and no carbon). This is the identity behind the
    claim that a per-slice carbon check adds nothing to the per-slice volume
    check; the README and CLAUDE.md state it, so it is fenced here.
    """
    df = build_carbon()
    df = df[(df["year"] >= C.MIN_YEAR) & (df["year"] <= C.MAX_YEAR)]
    water = {"Sawmills": C.SHRINKAGE}
    worst = 0.0
    for node in PROCESS_NODES:
        for _slice, g in df[(df["source"] == node) | (df["target"] == node)].groupby(["year", "species"]):
            inflow, outflow = g[g["target"] == node], g[g["source"] == node]
            v_in, c_in = inflow["volume"].sum(), inflow["carbon"].sum()
            if v_in <= 0 or c_in <= 0:
                continue
            rel_v = (outflow["volume"].sum() - v_in) / v_in
            rel_c = (outflow["carbon"].sum() - c_in) / c_in
            worst = max(worst, abs(rel_c - rel_v / (1 - water.get(node, 0.0))))
    assert worst < 1e-9, worst


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
    # A file without envelope columns still loads centrally, but not as a scenario.
    with pytest.raises(ValueError, match="kg_c_per_m3_low"):
        load_model(densities_file=dens, nodes_file=nodes, scenario="low")


def test_envelope_must_bracket_central(tmp_path):
    dens = tmp_path / "d.csv"
    dens.write_text(
        "species,state,kg_c_per_m3,kg_c_per_m3_low,kg_c_per_m3_high\n"
        "spruce,raw,190,195,209\nspruce,dry,203,183,223\npine,raw,215,194,237\n"
        "pine,dry,228,205,251\nnon-coniferous,raw,240,216,264\nnon-coniferous,dry,268,241,295\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="bracket"):
        load_model(densities_file=dens)
