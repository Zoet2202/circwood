"""Phase C — the filter dimensions (region / species / assortment / entity_id).

What these tests guarantee:

* the shipped dataset carries the widened schema with the documented defaults;
* summing volume over the dimensions recovers the national table exactly, so
  no consumer that aggregates loses anything;
* flows out of a single-species pool stay pure (their volumes are the source
  numbers, bit-for-bit — the split only touches genuinely mixed flows);
* every (region, species) slice of every process node balances — the property
  that makes filtering by a dimension return a coherent sub-Sankey rather than
  nonsense;
* the carbon overlay's per-species shortcut agrees with the mixed-density path
  it replaced.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from circwood import build_sankey
from circwood.carbon.species_model import SPECIES, add_carbon
from circwood.pipeline import SANKEY_COLUMNS
from circwood.transform.dimensions import (
    ALL,
    DIMENSION_COLUMNS,
    FLOW_COLUMNS,
    FYLKE_CODES,
    NATIONAL_ENTITY,
    NATIONAL_REGION,
    add_dimensions,
    assortment_for_edge,
    collapse_national,
    load_regional_harvest,
    merge_origins,
)
from circwood.validate import assert_slices_balanced

POOLS = {"Spruce": "spruce", "Pine": "pine", "Non-coniferous": "non-coniferous"}


@pytest.fixture(scope="module")
def shipped() -> pd.DataFrame:
    return build_sankey(use_cached_ssb=True)


def test_schema_is_the_declared_one(shipped):
    assert SANKEY_COLUMNS == FLOW_COLUMNS
    assert list(shipped.columns) == FLOW_COLUMNS


def test_dimension_defaults(shipped):
    """Entity is degenerate today by design; region only varies on the front edge."""
    assert (shipped["entity_id"] == NATIONAL_ENTITY).all()
    assert shipped["species"].isin(SPECIES).all()
    valid_regions = {NATIONAL_REGION} | set(FYLKE_CODES.values())
    assert set(shipped["region"]) <= valid_regions
    # Only the Harvested wood -> pool edges are regionally attributed; every
    # downstream flow is national because no regional processing data exists.
    downstream = shipped[shipped["source"] != "Harvested wood"]
    assert (downstream["region"] == NATIONAL_REGION).all()


def test_collapse_recovers_the_national_table(shipped):
    """Aggregating over the dimensions is lossless (the additivity guarantee)."""
    national = collapse_national(shipped)
    assert list(national.columns) == ["source", "target", "volume", "year", "origin"]
    # Same edges, same years, same totals.
    key = ["year", "source", "target"]
    wide_sums = shipped.groupby(key)["volume"].sum()
    nat_sums = national.set_index(key)["volume"]
    assert np.allclose(wide_sums.reindex(nat_sums.index), nat_sums, rtol=1e-12)


def test_expand_collapse_roundtrip(shipped):
    """collapse -> re-expand reproduces the shipped dataset (same model, same split)."""
    again = add_dimensions(collapse_national(shipped))
    a = shipped.sort_values(FLOW_COLUMNS[:2] + ["year", "species"]).reset_index(drop=True)
    b = again.sort_values(FLOW_COLUMNS[:2] + ["year", "species"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, rtol=1e-9)


def test_add_dimensions_is_idempotent(shipped):
    pd.testing.assert_frame_equal(add_dimensions(shipped), shipped)


def test_pool_outflows_are_pure_species(shipped):
    """A flow out of a species pool carries that species only, volume untouched."""
    for pool, sp in POOLS.items():
        rows = shipped[shipped["source"] == pool]
        assert (rows["species"] == sp).all(), f"{pool} emitted a foreign species"
        # Pure rows are never split by species: one row per (year, target,
        # destination) — the export edges are split by partner country only.
        assert not rows.duplicated(["year", "target", "destination"]).any()
        domestic = rows[rows["destination"] == "domestic"]
        assert not domestic.duplicated(["year", "target"]).any()


def test_mixed_flows_are_split_per_species(shipped):
    """Downstream of the mills a physical edge appears once per species present."""
    saw_pulp = shipped[(shipped["source"] == "Sawmills") & (shipped["target"] == "Pulpmills")]
    per_year = saw_pulp.groupby("year")["species"].nunique()
    assert (per_year > 1).all(), "Sawmills output should be a species mix"


def test_assortment_labels(shipped):
    def labels(source, target):
        sub = shipped[(shipped["source"] == source) & (shipped["target"] == target)]
        return set(sub["assortment"])

    assert labels("Spruce", "Sawmills") == {"sawtimber"}
    assert labels("Spruce", "Pulpmills") == {"pulpwood"}
    assert labels("Spruce", "Boardmills") == {"sams"}
    assert labels("Spruce", "Net export skurlast") == {"sawtimber"}
    assert labels("Firewood", "Energy") == {"fuelwood"}
    assert labels("Import Firewood", "Firewood") == {"fuelwood"}
    # Processed wood has no roundwood assortment.
    assert labels("Sawmills", "Pulpmills") == {ALL}
    assert labels("Wood Product processing & Logistics", "Long-term USE") == {ALL}
    # The mapping function agrees with the shipped data everywhere.
    expected = [
        assortment_for_edge(s, t) for s, t in zip(shipped["source"], shipped["target"])
    ]
    assert (shipped["assortment"] == expected).all()


def test_every_slice_of_every_process_node_balances(shipped):
    """The acceptance criterion: filtering by (region, species) stays coherent."""
    assert_slices_balanced(shipped)


def test_slice_check_skips_national_frames():
    """Pre-Phase-C frames (no dimension columns) are not an error."""
    assert_slices_balanced(collapse_national(build_sankey()))


def test_regional_rows_are_the_published_county_figures(shipped):
    """County harvest rows carry SSB 03795 values bit-for-bit, plus one national
    remainder per edge for the firewood component SSB only estimates nationally."""
    regional = load_regional_harvest()
    assert regional is not None, "data/raw/harvest_by_fylke.csv is committed"

    harvest = shipped[
        (shipped["source"] == "Harvested wood") & shipped["target"].isin(POOLS)
    ]
    county = harvest[harvest["region"] != NATIONAL_REGION]
    assert (county["origin"] == "SSB table 03795").all()

    # Every dataset county row equals the committed source figure exactly.
    key = ["year", "target", "region"]
    src = regional.set_index(key)["volume"]
    got = county.set_index(key)["volume"]
    aligned = src.reindex(got.index)
    assert not aligned.isna().any()
    assert (aligned == got).all()

    # At most one national remainder row per (year, pool), and it is what makes
    # the edge total exact.
    remainder = harvest[harvest["region"] == NATIONAL_REGION]
    assert not remainder.duplicated(["year", "target"]).any()
    assert (remainder["volume"] > 0).all()


def test_negative_regional_remainder_raises():
    """County figures exceeding the national edge = vintage mismatch = loud."""
    national = pd.DataFrame([{
        "source": "Harvested wood", "target": "Spruce", "volume": 100.0,
        "year": 2015, "origin": "t",
    }])
    regional = pd.DataFrame([{
        "year": 2015, "region": "NO-34", "region_label": "Innlandet",
        "target": "Spruce", "volume": 250.0,
    }])
    with pytest.raises(ValueError, match="vintage"):
        add_dimensions(national, regional=regional)


def test_merge_origins_is_token_based():
    merged = merge_origins(["SSB table 03795", "SSB table 03795 | SSB table 11181"])
    assert merged == "SSB table 03795 | SSB table 11181"


def test_carbon_shortcut_matches_mixed_density_path(shipped):
    """Per-edge carbon is identical whether computed per species or as a blend.

    Σ_sp share·vol·ρ(sp) == vol·Σ_sp share·ρ(sp): the widened frame's direct
    density lookup and the national frame's mixed-density propagation must
    agree, or the carbon overlay changed meaning when the schema widened.
    """
    key = ["year", "source", "target"]
    wide = add_carbon(shipped).groupby(key)["carbon"].sum()
    national = add_carbon(collapse_national(shipped)).set_index(key)["carbon"]
    assert np.allclose(wide.reindex(national.index), national, rtol=1e-9)


def test_dimension_columns_constant_matches_schema():
    assert FLOW_COLUMNS == ["source", "target", "volume", "year", "origin", *DIMENSION_COLUMNS]
    # Additive schema: Phase C's four dimensions keep their positions, the
    # destination dimension appends after them.
    assert DIMENSION_COLUMNS[:4] == ["region", "species", "assortment", "entity_id"]
    assert DIMENSION_COLUMNS[4] == "destination"
