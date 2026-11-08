"""Phase L — the economic value layer."""

from __future__ import annotations

import pandas as pd
import pytest

from circwood import config
from circwood import constants as C
from circwood.transform.economics import (
    ELSPOT_LABEL,
    PRICES_COLUMNS,
    ROUNDWOOD_SERIES,
    VALUE_COLUMNS,
    export_economic_value,
    load_assumptions,
    load_price_tables,
    value_flows,
    value_summary,
)


def test_assumptions_file_is_complete_and_labelled():
    a = load_assumptions()
    assert set(a.index) >= {"carbon_price_eur_per_tco2", "nok_per_eur", "energy_kwh_per_m3"}
    assert a.loc["energy_kwh_per_m3", "value"] == 753
    assert (a["kind"].isin(["assumption", "derived", "literature", "statistics"])).all()
    assert a["source"].str.len().gt(10).all()


def test_assumptions_loader_validates(tmp_path):
    bad = tmp_path / "p.csv"
    bad.write_text("parameter,value,unit,kind,source\nnok_per_eur,11.5,NOK per EUR,assumption,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing parameter"):
        load_assumptions(bad)


def _prices():
    return pd.DataFrame([
        ("07413", 2020, "Gran sagtømmer", "spruce", "sawtimber", 500.0, "NOK per m3"),
        ("07413", 2020, "Gran massevirke", "spruce", "pulpwood", 300.0, "NOK per m3"),
        ("07413", 2020, "I alt", "all", "all", 400.0, "NOK per m3"),
        ("07413", 2020, "Lauvtre massevirke", "non-coniferous", "pulpwood", 250.0, "NOK per m3"),
        ("08360", 2020, ELSPOT_LABEL, "all", "all", 10.0, "øre per kWh"),
    ], columns=PRICES_COLUMNS)


def _assumptions():
    return pd.DataFrame({
        "parameter": ["carbon_price_eur_per_tco2", "nok_per_eur", "energy_kwh_per_m3"],
        "value": [100.0, 10.0, 750.0], "unit": ["x", "y", "z"],
        "kind": ["assumption", "assumption", "derived"], "source": ["s1", "s2", "s3"],
    }).set_index("parameter")


def test_value_flows_prices_each_stage_from_its_source():
    carbon = pd.DataFrame([
        ("Spruce", "Sawmills", 100.0, 2020, 19000.0),
        ("Spruce", "Firewood", 10.0, 2020, 1900.0),
        ("Non-coniferous", "Boardmills", 5.0, 2020, 1200.0),     # sams, not published -> pulpwood price
        ("Firewood", "Energy", 10.0, 2020, 1900.0),
        ("Wood Product processing & Logistics", "Long-term USE", 50.0, 2020, 10000.0),
        ("Spruce", "Sawmills", 100.0, 2021, 19000.0),           # no 2021 prices: kept, unpriced
    ], columns=["source", "target", "volume", "year", "carbon"])
    out = value_flows(carbon, _prices(), _assumptions())
    assert list(out.columns) == VALUE_COLUMNS
    v = out.set_index(["year", "stage", "item"])
    assert v.loc[(2020, "roundwood", "Sawmills"), "value_nok"] == pytest.approx(100 * 500)
    assert v.loc[(2020, "roundwood", "Firewood"), "value_nok"] == pytest.approx(10 * 400)
    assert "proxy" in v.loc[(2020, "roundwood", "Firewood"), "price_source"]
    assert v.loc[(2020, "roundwood", "Boardmills"), "value_nok"] == pytest.approx(5 * 250)
    assert v.loc[(2020, "energy", "Firewood"), "value_nok"] == pytest.approx(10 * 0.10 * 750)
    stored = v.loc[(2020, "carbon stored", "Wood Product processing & Logistics")]
    assert stored["quantity"] == pytest.approx(10000 * C.CO2_PER_C / 1000)
    assert stored["value_nok"] == pytest.approx(stored["quantity"] * 100 * 10)
    released = v.loc[(2020, "carbon released", "Firewood")]
    assert released["value_nok"] < 0
    unpriced = v.loc[(2021, "roundwood", "Sawmills")]
    assert pd.isna(unpriced["price"]) and pd.isna(unpriced["value_nok"])


def test_committed_prices_and_values_are_consistent(tmp_path):
    prices = load_price_tables()
    assert prices is not None, "data/raw/prices_ssb.csv is missing; run circwood-economics"
    assert set(prices["table"]) == {"07413", "08360"}
    rw = prices[prices["table"] == "07413"]
    assert set(rw["series"]) <= set(ROUNDWOOD_SERIES)
    assert rw["year"].min() <= C.MIN_YEAR and rw["year"].max() >= C.MAX_YEAR
    committed = config.ECONOMIC_VALUE_CSV
    assert committed.exists(), "data/processed/economic_value.csv is missing"
    fresh = export_economic_value(tmp_path / "fresh.csv")
    assert committed.read_text(encoding="utf-8").replace("\r\n", "\n") == fresh.read_text(encoding="utf-8")
    values = pd.read_csv(committed, comment="#")
    summary = value_summary(values).set_index("stage")
    # Orders of magnitude: roundwood at the pool gate is a few billion NOK a year.
    assert 2e9 < summary.loc["roundwood", "value_nok_per_year"] < 2e10
    assert summary.loc["carbon released", "value_nok_per_year"] < 0
    assert set(values["stage"]) == {"roundwood", "energy", "carbon stored", "carbon released"}
