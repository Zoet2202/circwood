"""Phase H — the forest resource layer (SSB Landsskogtakseringen tables)."""

from __future__ import annotations

import pandas as pd
import pytest

from circwood import config
from circwood.ingest import ssb
from circwood.transform.dimensions import FYLKE_CODES
from circwood.transform.forest import (
    FOREST_COLUMNS,
    FOREST_TABLES,
    MEASURE_LABELS,
    NATIONAL,
    NFI_REGIONS,
    TABLE_MEASURES,
    increment_vs_harvest,
    interval_end,
    load_forest_resource,
    tidy_forest_table,
)


def test_nfi_regions_cover_every_county_exactly_once():
    counties = [c for _, (_, codes) in NFI_REGIONS.items() for c in codes]
    assert len(counties) == len(set(counties))
    iso = {v for v in FYLKE_CODES.values() if v not in ("NO-21", "NO-22", "NO-UNK")}
    assert set(counties) == iso


def test_every_measure_code_has_a_label():
    for table, codes in TABLE_MEASURES.items():
        for code in codes:
            assert code in MEASURE_LABELS, (table, code)
    # Pinned on codes: SSB's Norwegian label for HogstIV reads "VI".
    assert MEASURE_LABELS["HogstIV"].startswith("Development class IV")


def test_interval_end():
    assert interval_end("2021-2025") == 2025
    assert interval_end("2025") == 2025


def _codes():
    return {
        "14589": {
            "Region": {"Hele landet": "0", "Innlandet": "34"},
            "Tresort": {"I alt": "0000", "Gran": "1000"},
            "ContentsCode": {"Stående kubikkmasse på produktiv skogbruksmark": "Skogsareal4"},
        },
        "14594": {
            "Region": {"Hele landet": "0"},
            "ContentsCode": {"Hogstklasse VI": "HogstIV", "Hogstklasse V": "HogstV"},
        },
    }


def test_tidy_forest_table_keys_by_code_and_crosswalks_regions():
    frame = pd.DataFrame({
        "region": ["Hele landet", "Innlandet", "Innlandet"],
        "treslag": ["I alt", "Gran", "I alt"],
        "intervall (år)": ["2021-2025"] * 3,
        "statistikkvariabel": ["Stående kubikkmasse på produktiv skogbruksmark"] * 3,
        "value": [893838, 200000, 300000],
    })
    out = tidy_forest_table("14589", [frame], _codes(), {"Skogsareal4": "1 000 m³"})
    assert list(out.columns) == FOREST_COLUMNS
    assert set(out["region"]) == {NATIONAL, "34"}
    assert set(out["species"]) == {"all", "spruce"}
    assert (out["year_end"] == 2025).all() and (out["unit"] == "1 000 m³").all()
    assert out.loc[out["region"] == "34", "region_label"].iloc[0] == "Innlandet"


def test_tidy_forest_table_uses_the_code_not_the_mislabelled_text():
    frame = pd.DataFrame({
        "region": ["Hele landet", "Hele landet"], "intervall (år)": ["2021-2025"] * 2,
        "statistikkvariabel": ["Hogstklasse VI", "Hogstklasse V"], "value": [20, 44],
    })
    out = tidy_forest_table("14594", [frame], _codes()).set_index("measure")
    assert out.loc["HogstIV", "measure_label"].startswith("Development class IV")
    assert out.loc["HogstIV", "value"] == 20


def test_unknown_region_species_or_measure_raise():
    codes = _codes()
    base = pd.DataFrame({
        "region": ["Hele landet"], "treslag": ["I alt"], "intervall (år)": ["2021-2025"],
        "statistikkvariabel": ["Stående kubikkmasse på produktiv skogbruksmark"], "value": [1],
    })
    codes["14589"]["Region"]["Atlantis"] = "99"
    with pytest.raises(ValueError, match="NFI_REGIONS"):
        tidy_forest_table("14589", [base.assign(region="Atlantis")], codes)
    codes["14589"]["Tresort"]["Eik"] = "4000"
    with pytest.raises(ValueError, match="SPECIES_CODES"):
        tidy_forest_table("14589", [base.assign(treslag="Eik")], codes)
    codes["14589"]["ContentsCode"]["Noe nytt"] = "NyKode"
    with pytest.raises(ValueError, match="does not model"):
        tidy_forest_table("14589", [base.assign(statistikkvariabel="Noe nytt")], codes)


def test_increment_vs_harvest_reports_a_ratio_per_species():
    forest = pd.DataFrame([
        ("14590", "2021-2025", 2025, NATIONAL, "Norway", "Skogsareal4", "x", "all", 22659.0, "1 000 m³"),
        ("14590", "2021-2025", 2025, NATIONAL, "Norway", "AndreMark", "x", "all", 2484.0, "1 000 m³"),
        ("14590", "2021-2025", 2025, NATIONAL, "Norway", "Skogsareal4", "x", "spruce", 12565.0, "1 000 m³"),
        ("14590", "2021-2025", 2025, NATIONAL, "Norway", "AndreMark", "x", "spruce", 645.0, "1 000 m³"),
        ("14589", "2021-2025", 2025, NATIONAL, "Norway", "Skogsareal4", "x", "all", 893838.0, "1 000 m³"),
        ("14589", "2021-2025", 2025, NATIONAL, "Norway", "Skogsareal4", "x", "spruce", 414434.0, "1 000 m³"),
    ], columns=FOREST_COLUMNS)
    sankey = pd.DataFrame({
        "source": ["Harvested wood"] * 4, "target": ["Spruce", "Pine", "Spruce", "Pine"],
        "volume": [8e6, 3e6, 8e6, 3e6], "year": [2012, 2012, 2013, 2013],
    })
    out = increment_vs_harvest(forest, sankey, 2012, 2013).set_index("species")
    assert out.loc["all", "increment_total_m3"] == pytest.approx(25_143_000)
    assert out.loc["all", "harvest_mean_m3"] == pytest.approx(11e6)
    assert out.loc["all", "harvest_to_increment"] == pytest.approx(11e6 / 22_659_000)
    assert out.loc["all", "harvest_to_increment_total"] == pytest.approx(11e6 / 25_143_000)
    assert out.loc["spruce", "harvest_to_increment"] == pytest.approx(8e6 / 12_565_000)
    assert out.loc["spruce", "standing_volume_m3"] == pytest.approx(414_434_000)


def test_forest_query_files_satisfy_their_contracts():
    paths = sorted(config.FOREST_QUERY_DIR.glob("*.json"))
    ids = {ssb._table_id(ssb.load_query_spec(p), p.name) for p in paths}
    assert ids == set(FOREST_TABLES)
    for tid in ids:
        contract = ssb.contract_for(tid)
        assert contract is not ssb.DEFAULT_CONTRACT and not contract.window_gated
        assert contract.publication_lag_months is not None
    assert ssb.contract_for("14594").period == "interval"
    assert ssb.contract_for("06289").period == "year"


def test_interval_period_validates_on_the_interval_end():
    frame = pd.DataFrame({"region": ["Hele landet"], "intervall (år)": ["2021-2025"],
                          "statistikkvariabel": ["x"], "value": [1.0]})
    assert ssb.validate_table("14594", [frame]) == (2025, 2025)


def test_committed_forest_resource_is_well_formed():
    forest = load_forest_resource()
    assert forest is not None, "data/processed/forest_resource.csv is missing; run circwood-forest"
    assert list(forest.columns) == FOREST_COLUMNS
    assert set(forest["table"]) == set(FOREST_TABLES)
    assert set(forest["region"]) <= {NATIONAL} | (set(NFI_REGIONS) - {"0"})
    assert set(forest["species"]) <= {"all", "spruce", "pine", "non-coniferous"}
    national = forest[(forest["region"] == NATIONAL) & (forest["year_end"] == forest["year_end"].max())]
    # Sanity against the values the roadmap recorded from the metadata check (2021-2025).
    area = national[(national["table"] == "14591") & (national["measure"] == "ProdSkogBrukM")]["value"].iloc[0]
    assert 70_000 < area < 95_000
    vol = national[(national["table"] == "14589") & (national["measure"] == "Skogsareal4")
                   & (national["species"] == "all")]["value"].iloc[0]
    assert 800_000 < vol < 1_000_000
