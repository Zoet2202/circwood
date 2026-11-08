"""Phase G — the HWP carbon stock (first-order decay)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from circwood import config
from circwood import constants as C
from circwood.carbon.stock import (
    CLASSES,
    METHODS,
    PANELS,
    PAPER,
    SAWNWOOD,
    STOCK_COLUMNS,
    class_inflows,
    decay_series,
    export_hwp_stock,
    hwp_stock,
    load_half_lives,
)
from circwood.pipeline import build_carbon


def test_half_lives_load_and_are_ipcc_defaults():
    hl = load_half_lives()
    assert set(hl.index) >= set(CLASSES)
    assert hl.loc[SAWNWOOD, "half_life_years"] == 35
    assert hl.loc[PANELS, "half_life_years"] == 25
    assert hl.loc[PAPER, "half_life_years"] == 2


def test_half_life_file_is_validated(tmp_path):
    bad = tmp_path / "hl.csv"
    bad.write_text("product_class,half_life_years,source\nsawnwood,35,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no half-life"):
        load_half_lives(bad)
    bad.write_text("product_class,half_life_years,source\nsawnwood,0,x\nwood-based panels,25,x\n"
                   "paper and paperboard,2,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="positive"):
        load_half_lives(bad)


def test_decay_series_halves_in_one_half_life_and_conserves_mass():
    years = list(range(2000, 2036))
    inflow = pd.Series(0.0, index=years)
    out = decay_series(inflow, half_life=35, initial=1000.0).set_index("year")
    assert out.loc[2034, "stock_end_kgc"] == pytest.approx(500.0)       # 35 years after 2000-01-01
    assert ((out["stock_start_kgc"] + out["inflow_kgc"] - out["decay_kgc"] - out["stock_end_kgc"]).abs() < 1e-9).all()
    # A constant inflow converges to Inflow / k.
    steady = decay_series(pd.Series(100.0, index=range(2000, 2500)), half_life=2, initial=0.0)
    k = np.log(2) / 2
    assert steady["stock_end_kgc"].iloc[-1] == pytest.approx(100 / k, rel=1e-6)
    with pytest.raises(ValueError, match="consecutive"):
        decay_series(pd.Series([1.0, 1.0], index=[2000, 2002]), 35, 0.0)


def test_class_inflows_split_long_term_use_by_the_panel_share_of_wpp():
    WPP = "Wood Product processing & Logistics"
    df = pd.DataFrame([
        ("Sawmills", WPP, 2015, 300.0), ("Boardmills", WPP, 2015, 100.0),
        (WPP, "Long-term USE", 2015, 360.0), ("Pulpmills", "Short-term USE", 2015, 50.0),
    ], columns=["source", "target", "year", "carbon"])
    out = class_inflows(df).set_index("product_class")["inflow_kgc"]
    assert out[PANELS] == pytest.approx(90.0)       # 25 % of 360
    assert out[SAWNWOOD] == pytest.approx(270.0)
    assert out[PAPER] == pytest.approx(50.0)


@pytest.fixture(scope="module")
def carbon() -> pd.DataFrame:
    return build_carbon()


def test_hwp_stock_on_the_committed_dataset(carbon):
    table = hwp_stock(carbon)
    assert list(table.columns) == list(STOCK_COLUMNS)
    assert set(table["product_class"]) == set(CLASSES) | {"total"}
    assert sorted(table["year"].unique()) == list(range(C.BETA_MIN_YEAR, C.BETA_MAX_YEAR + 1))
    total = table[table["product_class"] == "total"].set_index("year")
    classes = table[table["product_class"] != "total"].groupby("year")["stock_end_kgc"].sum()
    assert np.allclose(total["stock_end_kgc"], classes.reindex(total.index))
    # The cohort stock starts empty and grows every year while inflow exceeds decay.
    assert total["stock_start_kgc"].iloc[0] == 0.0
    assert (total["net_change_kgc"] > 0).all()
    assert (total["decay_kgc"] < total["inflow_kgc"]).all()
    # The observed outflow (SSB 10513) rides along on the total rows only.
    assert total["observed_eol_kgc"].notna().all()
    assert table.loc[table["product_class"] != "total", "observed_eol_kgc"].isna().all()


def test_steady_state_starts_at_inflow_over_k(carbon):
    table = hwp_stock(carbon, method="steady_state")
    first = table[(table["year"] == C.BETA_MIN_YEAR) & (table["product_class"] == SAWNWOOD)].iloc[0]
    k = np.log(2) / 35
    assert first["stock_start_kgc"] == pytest.approx(first["inflow_kgc"] / k)
    with pytest.raises(ValueError, match="method must be"):
        hwp_stock(carbon, method="magic")


def test_export_round_trips(tmp_path, carbon):
    path = export_hwp_stock(tmp_path / "stock.csv", df=carbon)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Circwood WP4 - carbon stored")
    for needed in ("IPCC", "half-life", "cohort", "steady_state", "observed_eol_kgc", "kg C"):
        assert needed in text
    df = pd.read_csv(path, comment="#")
    assert set(df["method"]) == set(METHODS)
    assert list(df.columns) == list(STOCK_COLUMNS)
    again = export_hwp_stock(tmp_path / "stock2.csv", df=carbon)
    assert again.read_bytes() == path.read_bytes()


def test_committed_stock_matches_a_fresh_export(tmp_path):
    committed = config.HWP_STOCK_CSV
    assert committed.exists(), "data/processed/hwp_stock.csv is missing; run export_hwp_stock()"
    fresh = export_hwp_stock(tmp_path / "fresh.csv")
    a = committed.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert a == fresh.read_text(encoding="utf-8")
