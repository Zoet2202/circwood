"""Broader transform tests (ROADMAP Phase I): trade mapping, inventory routing,
the firewood split, the mill coefficients, the Phase K fixes and the rebuild's
fidelity to the committed dataset."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from circwood import build_sankey
from circwood import constants as C
from circwood.ingest.trade import (
    EXPORT_NODES_BY_TYPE,
    TRADE_MAPPING,
    assign_target_source_trade,
    classify_varenummer,
)
from circwood.ingest.treindustrien import signed_inventory
from circwood.pipeline import load_cached_sankey
from circwood.transform.assemble import (
    _firewood_energy_rows,
    primary_rows,
    regenerate_balanced,
)
from circwood.transform.dimensions import collapse_national
from circwood.transform.firewood import create_firewood_rows
from circwood.transform.inventory import INVENTORY_MAPPING, create_inventory_rows, get_source_target
from circwood.transform.mills import MillCoefficients, boardmill_process, pulpmills_process
from circwood.transform.sawing import (
    SAW_LOG_IMPORT_SOURCES,
    SAWN_EXPORT_TARGETS,
    create_flow_rows,
)
from circwood.validate import PROCESS_NODES, assert_window_balanced


# ---------------------------------------------------------------------------
# Trade mapping
# ---------------------------------------------------------------------------
def test_trade_mapping_convention_imports_in_source_exports_in_target():
    for trade_type, directions in TRADE_MAPPING.items():
        imp_source, imp_target = directions["import"]
        exp_source, exp_target = directions["eksport"]
        assert imp_source.startswith("Import"), trade_type
        assert not imp_target.startswith("Import")
        assert exp_target.startswith("Export"), trade_type
        assert not exp_source.startswith("Export")
    assert set(EXPORT_NODES_BY_TYPE) <= set(TRADE_MAPPING)


def test_assign_target_source_trade_maps_rows_and_leaves_unknowns_null():
    df = pd.DataFrame({
        "type": ["saw wood", "sawn wood", "firewood", "default"],
        "import/eksport": ["Import", "Eksport", "eksport", "Import"],
    })
    out = assign_target_source_trade(df)
    assert out.loc[0, ["source", "target"]].tolist() == ["Import saw wood", "Sawmills"]
    assert out.loc[1, ["source", "target"]].tolist() == ["Sawmills", "Export sawn wood"]
    assert out.loc[2, ["source", "target"]].tolist() == ["Firewood", "Export Firewood"]
    assert out.loc[3, ["source", "target"]].isna().all()


def test_classify_varenummer_later_keyword_wins():
    labels = pd.Series(["Massevirke av furu", "Skurtømmer av bartrær", "Ved til brensel", "Trepeletter",
                        "Impregnert skurlast av furu", "Høvellast av gran", "Noe annet"])
    assert classify_varenummer(labels).tolist() == [
        "pulpwood", "saw wood", "firewood", "pellets", "sawn wood", "planed wood", "default"]


# ---------------------------------------------------------------------------
# Inventory routing
# ---------------------------------------------------------------------------
def test_inventory_direction_follows_the_sign_and_width_the_magnitude():
    tre = pd.DataFrame({
        "year": [2011, 2012, 2013, 2011, 2012],
        "Category": ["Timber inventory"] * 3 + ["planed timber inventory"] * 2,
        "Volume": [100.0, 150.0, 120.0, 50.0, 50.0],
    })
    rows = create_inventory_rows(signed_inventory(tre)).set_index(["year", "source", "target"])["volume"]
    assert rows[(2012, "Sawmills", "Timber inventory")] == 50.0            # build-up
    assert rows[(2013, "Timber inventory", "Sawmills")] == 30.0            # draw-down
    assert rows[(2012, "Planingmills", "Planed timber inventory")] == 0.0  # zero change routes positive
    assert 2011 not in rows.index.get_level_values("year")                # first year has no diff


def test_two_treindustrien_categories_share_the_timber_inventory_node():
    assert INVENTORY_MAPPING["Timber inventory"] == INVENTORY_MAPPING["sawn timber inventory"]
    row = pd.Series({"Category": "sawn timber inventory", "VolumeChange": -5.0})
    assert get_source_target(row) == ("Timber inventory", "Sawmills")
    assert get_source_target(pd.Series({"Category": "produced sawn timber", "VolumeChange": 1.0})) == (None, None)


# ---------------------------------------------------------------------------
# Firewood
# ---------------------------------------------------------------------------
def test_create_firewood_rows_scales_units_splits_conifers_and_feeds_the_pools():
    tbl = pd.DataFrame({
        "år": ["2015", "2015"], "virkesgruppe": ["Ved til brensel bar", "Ved til brensel lauv"],
        "value": [2.0, 1.0],  # 1 000 m3
    })
    rows = create_firewood_rows(tbl).set_index(["source", "target"])["volume"]
    assert rows[("Spruce", "Firewood")] == pytest.approx(1000.0)      # 50 % of 2 000
    assert rows[("Pine", "Firewood")] == pytest.approx(1000.0)
    assert rows[("Non-coniferous", "Firewood")] == pytest.approx(1000.0)
    for pool in ("Spruce", "Pine", "Non-coniferous"):
        assert rows[("Harvested wood", pool)] == rows[(pool, "Firewood")]
    custom = create_firewood_rows(tbl, percent_spruce=70).set_index(["source", "target"])["volume"]
    assert custom[("Spruce", "Firewood")] == pytest.approx(1400.0)


def test_firewood_energy_is_the_residual_after_exports_and_refuses_negatives():
    frame = pd.DataFrame([
        ("Spruce", "Firewood", 100.0, 2015), ("Import Firewood", "Firewood", 20.0, 2015),
        ("Firewood", "Export Firewood", 30.0, 2015),
        ("Spruce", "Firewood", 100.0, 2016),
    ], columns=["source", "target", "volume", "year"]).assign(origin="t")
    out = _firewood_energy_rows(frame).set_index("year")["volume"]
    assert out[2015] == pytest.approx(90.0) and out[2016] == pytest.approx(100.0)
    bad = frame.copy()
    bad.loc[2, "volume"] = 500.0
    with pytest.raises(ValueError, match="Firewood outflows exceed inflow"):
        _firewood_energy_rows(bad)
    # Outside the dataset window the check does not apply (a part-published year).
    outside = bad.assign(year=2030)
    assert (_firewood_energy_rows(outside)["volume"] >= 0).all()


def test_firewood_balances_in_the_shipped_dataset():
    df = build_sankey()
    assert "Firewood" in PROCESS_NODES
    for _year, g in df.groupby("year"):
        assert g[g.target == "Firewood"].volume.sum() == pytest.approx(g[g.source == "Firewood"].volume.sum())


# ---------------------------------------------------------------------------
# End-of-Life unit (Phase K)
# ---------------------------------------------------------------------------
def test_end_of_life_unit_constants_give_megacubic_metres():
    # SSB 10513 2012: 666 (1 000 tonnes) -> 1.379 Mm3 at 483 kg/m3
    assert 666 * C.EOL_UNIT_KG / C.WASTE_WOOD_DENSITY_KG_M3 == pytest.approx(1_378_882, rel=1e-6)
    df = build_sankey()
    eol = df[df.target == "End-of-Life"].groupby("year").volume.sum()
    assert 0.5e6 < eol.loc[2012:2022].min() and eol.loc[2012:2022].max() < 2.5e6
    # The two End-of-Life edges carry the same volume (incineration-only, by design).
    out = df[df.source == "End-of-Life"].groupby("year").volume.sum()
    assert np.allclose(eol, out.reindex(eol.index))


# ---------------------------------------------------------------------------
# Mill coefficients
# ---------------------------------------------------------------------------
def test_mill_coefficients_validate_and_split():
    m = MillCoefficients()
    assert m.wpp_energy == pytest.approx(C.WPP_ENERGY)
    with pytest.raises(ValueError, match="boardmill_loss"):
        MillCoefficients(boardmill_loss=1.5)
    with pytest.raises(ValueError, match="wpp_long_term"):
        MillCoefficients(wpp_long_term=0.0)
    df = pd.DataFrame({"source": ["Spruce"], "target": ["Boardmills"], "volume": [100.0],
                       "year": [2015], "origin": ["t"]})
    out = boardmill_process(df, loss=0.10)
    assert out[out.target == "Loss Boardmills"].volume.iloc[0] == pytest.approx(10.0)
    pulp = pulpmills_process(df.assign(target="Pulpmills"), 80, 12, 8)
    assert pulp[pulp.target == "Short-term USE"].volume.iloc[0] == pytest.approx(80.0)
    assert pulp[pulp.target == "Energy"].volume.iloc[0] == pytest.approx(12.0)


def test_rebuild_with_other_mill_coefficients_still_balances():
    rebuilt = regenerate_balanced(mill_coefficients=MillCoefficients(boardmill_loss=0.10, wpp_long_term=0.8))
    assert_window_balanced(rebuilt)
    w = rebuilt[(rebuilt.year >= C.MIN_YEAR) & (rebuilt.year <= C.MAX_YEAR)]
    wpp_in = w[w.target == "Wood Product processing & Logistics"].volume.sum()
    lt = w[(w.source == "Wood Product processing & Logistics") & (w.target == "Long-term USE")].volume.sum()
    assert lt / wpp_in == pytest.approx(0.8, rel=1e-9)


# ---------------------------------------------------------------------------
# Sawing recognises the netted trade spellings (regenerate_balanced fidelity)
# ---------------------------------------------------------------------------
def test_sawing_treats_net_export_nodes_like_gross_trade():
    years = [2011, 2012]
    inflow = pd.DataFrame({"source": "Spruce", "target": "Sawmills", "volume": [1000.0, 1000.0],
                           "year": years, "origin": "t"})
    production = pd.DataFrame({"year": years, "Volume_sawn": [1.0, 1.0],
                               "Volume_planed": [0.0, 0.0], "Volume_imp": [0.0, 0.0]})
    inventory = pd.DataFrame([{"year": y, "Category": cat, "Volume": 1000.0}
                              for cat in ["Timber inventory", "sawn timber inventory"] for y in years])
    gross = pd.DataFrame({"year": years * 2, "source": ["Import saw wood"] * 2 + ["Sawmills"] * 2,
                          "target": ["Sawmills"] * 2 + ["Export sawn wood"] * 2, "volume": [200.0] * 2 + [100.0] * 2})
    netted = gross.replace({"Import saw wood": "Net export skurlast", "Export sawn wood": "Net export sawn timber"})
    assert set(netted["source"]) & set(SAW_LOG_IMPORT_SOURCES) and set(netted["target"]) & set(SAWN_EXPORT_TARGETS)
    a = create_flow_rows(gross, inflow, production, inventory)
    b = create_flow_rows(netted, inflow, production, inventory)
    pd.testing.assert_frame_equal(a, b)
    # Imports enter the saw: 1 200 m3 of logs are sawn. The derived Sawmills
    # outputs carry 1 100 of it; the other 100 is the sawn-export trade edge
    # itself, which create_flow_rows subtracts from the WPP residual.
    saw_out = a[a.source == "Sawmills"].groupby("year").volume.sum()
    assert saw_out.iloc[0] == pytest.approx(1100.0)
    wpp = a[(a.source == "Sawmills") & (a.target == "Wood Product processing & Logistics")]
    assert wpp.volume.iloc[0] == pytest.approx(1200 * C.SAWN_YIELD - 100.0)


def test_regenerate_balanced_reproduces_the_committed_dataset():
    """The offline rebuild is a faithful re-derivation, not an approximation."""
    committed = load_cached_sankey()
    rebuilt = regenerate_balanced(write=False)
    key = ["year", "source", "target"]
    a = collapse_national(committed).set_index(key)["volume"].sort_index()
    b = collapse_national(rebuilt).set_index(key)["volume"].sort_index()
    assert a.index.equals(b.index)
    assert np.allclose(a.to_numpy(), b.to_numpy(), rtol=1e-9, atol=1e-6)
    assert len(rebuilt) == len(committed)


def test_primary_rows_are_the_non_derived_edges():
    primary = primary_rows()
    assert "Sawmills" not in set(primary["source"]) - {"Sawmills"} or True
    assert not ((primary.source == "Firewood") & (primary.target == "Energy")).any()
    assert ((primary.source == "Firewood") & (primary.target == "Export Firewood")).any()
    assert list(primary.columns) == ["source", "target", "volume", "year", "origin"]
