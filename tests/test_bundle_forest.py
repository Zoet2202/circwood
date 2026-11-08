"""Phase H additions to the site bundle: the national forest-resource block.

What these tests guarantee:

* the block is present on the committed data with the documented keys, every
  figure finite (no ``NaN`` can reach a browser's ``response.json()``), and a
  rebuild is byte-identical — the deploy diff stays meaningful;
* its national figures are the committed ``forest_resource.csv`` rows, unit
  conversion included (SSB's 1 000 m³ -> m³, km² to 0.1, per cent to 0.1);
* ``harvest_vs_increment`` is exactly ``forest.increment_vs_harvest`` over the
  paper window, and the national ratio is plausible (0.2–0.8);
* without the forest file the key is simply absent — the bundle never fails
  for a missing side layer.
"""

from __future__ import annotations

import json
import math

import pandas as pd
import pytest
from tests.test_bundle import tiny_frame, tiny_nodes

from circwood import config
from circwood import constants as C
from circwood.export.bundle import build_bundle
from circwood.transform.forest import (
    MEASURE_LABELS,
    NATIONAL,
    increment_vs_harvest,
    load_forest_resource,
)

SPECIES = {"all", "spruce", "pine", "non-coniferous"}
KEYS = {
    "development_classes", "harvest_vs_increment", "increment_m3_per_year",
    "increment_total_m3_per_year", "interval", "intervals",
    "productive_forestry_land_km2", "protected_productive_km2",
    "standing_volume_m3", "year_end",
}


@pytest.fixture(scope="module")
def bundle() -> dict:
    return build_bundle(write=False)


@pytest.fixture(scope="module")
def forest() -> pd.DataFrame:
    table = load_forest_resource()
    assert table is not None, "data/processed/forest_resource.csv is missing; run circwood-forest"
    return table


def _national_latest(forest: pd.DataFrame, table_id: str) -> pd.DataFrame:
    rows = forest[(forest["region"] == NATIONAL) & (forest["table"] == table_id)]
    return rows[rows["year_end"] == rows["year_end"].max()]


def _value(rows: pd.DataFrame, measure: str, species: str = "all") -> float:
    hit = rows[(rows["measure"] == measure) & (rows["species"] == species)]
    assert len(hit) == 1, (measure, species, len(hit))
    return float(hit["value"].iloc[0])


def _numbers(obj):
    """Every number in a nested JSON-like structure."""
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _numbers(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _numbers(v)


# -- shape ------------------------------------------------------------------

def test_forest_block_is_present_with_the_documented_keys(bundle):
    f = bundle["forest"]
    assert set(f) == KEYS
    assert list(f) == sorted(f), "keys are sorted so the bundle is deterministic"
    for key in ("standing_volume_m3", "increment_m3_per_year", "increment_total_m3_per_year"):
        assert set(f[key]) == SPECIES
        assert list(f[key]) == sorted(f[key])
    assert [d["code"] for d in f["development_classes"]] == [
        "HogstI", "HogstII", "HogstIII", "HogstIV", "HogstV"
    ]
    assert [r["species"] for r in f["harvest_vs_increment"]] == [
        "all", "spruce", "pine", "non-coniferous"
    ]
    assert f["interval"].endswith(str(f["year_end"]))
    assert set(f["intervals"]) == {"14589", "14590", "14591", "14594"}
    assert f["intervals"]["14590"] == f["interval"] == f["intervals"]["14589"]


def test_forest_block_is_deterministic_and_json_clean(bundle):
    again = build_bundle(write=False)["forest"]
    assert json.dumps(again) == json.dumps(bundle["forest"])
    # allow_nan=False raises on NaN/Infinity — the tokens JSON does not have.
    json.dumps(bundle["forest"], allow_nan=False)
    numbers = list(_numbers(bundle["forest"]))
    assert numbers and all(math.isfinite(n) for n in numbers)
    # m³ figures are whole numbers; km² and per cent carry one decimal at most.
    for key in ("standing_volume_m3", "increment_m3_per_year", "increment_total_m3_per_year"):
        assert all(isinstance(v, int) for v in bundle["forest"][key].values())
    for v in (bundle["forest"]["productive_forestry_land_km2"], bundle["forest"]["protected_productive_km2"]):
        assert v == round(v, 1)


# -- fidelity to forest_resource.csv ------------------------------------------

def test_national_figures_agree_with_forest_resource_csv(bundle, forest):
    f = bundle["forest"]
    inc = _national_latest(forest, "14590")
    assert f["interval"] == inc["interval"].iloc[0]
    assert f["year_end"] == int(inc["year_end"].iloc[0])
    vol = forest[(forest["region"] == NATIONAL) & (forest["table"] == "14589")
                 & (forest["year_end"] == f["year_end"])]
    for sp in SPECIES:
        assert f["standing_volume_m3"][sp] == round(_value(vol, "Skogsareal4", sp) * 1000)
        assert f["increment_m3_per_year"][sp] == round(_value(inc, "Skogsareal4", sp) * 1000)
        both = _value(inc, "Skogsareal4", sp) + _value(inc, "AndreMark", sp)
        assert f["increment_total_m3_per_year"][sp] == round(both * 1000)
    # Species add up to "all" (SSB publishes rounded thousands, hence the slack).
    parts = sum(f["standing_volume_m3"][sp] for sp in SPECIES - {"all"})
    assert parts == pytest.approx(f["standing_volume_m3"]["all"], abs=5_000)

    area = _national_latest(forest, "14591")
    assert f["productive_forestry_land_km2"] == round(_value(area, "ProdSkogBrukM"), 1)
    assert f["protected_productive_km2"] == round(_value(area, "VernProdSkogAr"), 1)
    assert f["intervals"]["14591"] == area["interval"].iloc[0]

    classes = _national_latest(forest, "14594")
    for d in f["development_classes"]:
        assert d["percent"] == round(_value(classes, d["code"]), 1)
        assert d["label"] == MEASURE_LABELS[d["code"]]
        assert d["label"].startswith("Development class")
    assert sum(d["percent"] for d in f["development_classes"]) == pytest.approx(100, abs=3)
    assert f["intervals"]["14594"] == classes["interval"].iloc[0]

    # Sanity against the magnitudes the roadmap recorded (2021–2025).
    assert 70_000 < f["productive_forestry_land_km2"] < 95_000
    assert 800e6 < f["standing_volume_m3"]["all"] < 1000e6
    assert 15e6 < f["increment_m3_per_year"]["all"] < 35e6


def test_harvest_vs_increment_is_the_module_s_reading(bundle, forest):
    rows = {r["species"]: r for r in bundle["forest"]["harvest_vs_increment"]}
    flows = pd.read_csv(config.CARBON_CSV)
    flows.columns = [c.lstrip("﻿").strip() for c in flows.columns]
    expected = increment_vs_harvest(forest, flows).set_index("species")
    for sp, r in rows.items():
        e = expected.loc[sp]
        assert r["interval"] == e["interval"]
        assert r["harvest_window"] == f"{C.MIN_YEAR}-{C.MAX_YEAR}"
        for col in ("increment_productive_m3", "increment_total_m3", "standing_volume_m3", "harvest_mean_m3"):
            assert r[col] == round(e[col]), (sp, col)
        assert r["harvest_to_increment"] == pytest.approx(e["harvest_to_increment"], abs=5e-5)
        assert list(r) == sorted(r)
    # The block's own species dicts are the productive-land figures the rows carry.
    f = bundle["forest"]
    for sp in SPECIES:
        assert rows[sp]["increment_productive_m3"] == f["increment_m3_per_year"][sp]
        assert rows[sp]["increment_total_m3"] == f["increment_total_m3_per_year"][sp]
    # Norway harvests roughly half its gross increment; anything outside this
    # band is a unit or window error, not a forestry result.
    assert 0.2 < rows["all"]["harvest_to_increment"] < 0.8
    assert rows["all"]["harvest_mean_m3"] > 0


# -- the side layer is optional -------------------------------------------------

def test_forest_block_is_omitted_when_the_file_is_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "FOREST_RESOURCE_CSV", tmp_path / "forest_resource.csv")
    assert not config.FOREST_RESOURCE_CSV.exists()
    b = build_bundle(df=tiny_frame(), nodes=tiny_nodes("A", "B"), write=False)
    assert "forest" not in b
    assert "forest" not in json.dumps(b)


def test_forest_block_rides_along_with_a_frame_that_has_no_harvest():
    """A frame without Harvested wood edges still bundles: harvest 0, ratio 0, no NaN."""
    b = build_bundle(df=tiny_frame(), nodes=tiny_nodes("A", "B"), write=False)
    rows = {r["species"]: r for r in b["forest"]["harvest_vs_increment"]}
    assert rows["all"]["harvest_mean_m3"] == 0
    assert rows["all"]["harvest_to_increment"] == 0
    json.dumps(b, allow_nan=False)
