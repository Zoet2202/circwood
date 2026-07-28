"""Phase D — the bundle builder behind the static site.

What these tests guarantee:

* the bundle ties out to the committed dataset — every flow row survives with
  its volume and carbon (to display rounding), and nothing else rides along;
* node coverage is exact, both ways — a label missing from ``nodes.json``
  raises instead of silently dropping flows (the ``remap_ids`` failure mode
  that once hid ~5.9 Mm³), and a ghost label raises instead of rendering an
  orphan node;
* the builder routes through the Phase E gate: a member-level row anywhere in
  the frame refuses to serialise;
* ``entity_id`` never reaches the bundle — it is a constant today and a
  confidentiality decision tomorrow;
* the written pair (``.json`` + ``.json.gz``) round-trips and rebuilding an
  unchanged bundle is byte-identical, so deploy diffs are meaningful.
"""

from __future__ import annotations

import gzip
import json

import pandas as pd
import pytest

from circwood import config
from circwood.export.bundle import (
    ConfidentialityError,
    build_bundle,
    write_bundle,
)
from circwood.transform.dimensions import FYLKE_CODES


@pytest.fixture(scope="module")
def bundle() -> dict:
    return build_bundle(write=False)


@pytest.fixture(scope="module")
def dataset() -> pd.DataFrame:
    df = pd.read_csv(config.CARBON_CSV)
    df.columns = [c.lstrip("﻿").strip() for c in df.columns]
    return df


def tiny_frame(**overrides) -> pd.DataFrame:
    base = {
        "source": ["A"], "target": ["B"], "volume": [100.0], "year": [2020],
        "origin": ["test"], "carbon": [20.0], "region": ["NO"],
        "species": ["spruce"], "assortment": ["all"], "entity_id": ["national"],
    }
    base.update(overrides)
    return pd.DataFrame(base)


def tiny_nodes(*labels):
    return [
        {"label": lb, "x_position": 0.1 * i, "y_position": 0.5, "node_color": "#123456"}
        for i, lb in enumerate(labels)
    ]


# -- fidelity ---------------------------------------------------------------

def test_every_flow_row_survives(bundle, dataset):
    assert bundle["row_count"] == len(dataset)
    assert len(bundle["flows"]["volume"]) == len(dataset)


def test_totals_tie_out_to_display_rounding(bundle, dataset):
    # Volume is rounded to 0.1 m³ and carbon to 1 kg per row.
    assert sum(bundle["flows"]["volume"]) == pytest.approx(
        dataset["volume"].sum(), abs=0.05 * len(dataset)
    )
    assert sum(bundle["flows"]["carbon"]) == pytest.approx(
        dataset["carbon"].sum(), abs=0.5 * len(dataset)
    )


def test_indices_are_dense_and_valid(bundle):
    flows = bundle["flows"]
    n = bundle["row_count"]
    for col in ("source", "target", "year", "region", "species",
                "assortment", "origin", "volume", "carbon"):
        assert len(flows[col]) == n
    assert max(flows["source"] + flows["target"]) < len(bundle["nodes"])
    assert max(flows["region"]) < len(bundle["regions"])
    assert max(flows["species"]) < len(bundle["species"])
    assert max(flows["assortment"]) < len(bundle["assortments"])
    assert max(flows["origin"]) < len(bundle["origins"])


def test_node_coverage_matches_dataset_exactly(bundle, dataset):
    labels = {n["label"] for n in bundle["nodes"]}
    assert labels == set(dataset["source"]) | set(dataset["target"])


def test_regions_are_labelled_and_national_first(bundle):
    assert bundle["regions"][0]["code"] == "NO"
    assert "not regionally attributed" in bundle["regions"][0]["label"]
    codes = {r["code"] for r in bundle["regions"][1:]}
    assert codes <= set(FYLKE_CODES.values())


def test_vintage_carries_dates_not_hashes(bundle):
    vintage = bundle["vintage"]
    assert vintage is not None
    assert vintage["data_as_of"]
    assert "sha256" not in json.dumps(vintage)


# -- the gate and the fences ------------------------------------------------

def test_member_level_rows_refuse_to_serialise(tmp_path):
    df = tiny_frame(entity_id=["mill-001"])
    with pytest.raises(ConfidentialityError):
        build_bundle(df, nodes=tiny_nodes("A", "B"), write=False)


def test_entity_id_never_reaches_the_bundle(bundle):
    assert "entity_id" not in bundle["flows"]
    assert "entity_id" not in json.dumps(bundle["flows"])


def test_missing_node_label_raises_instead_of_dropping(tmp_path):
    with pytest.raises(ValueError, match="missing \\['B'\\]"):
        build_bundle(tiny_frame(), nodes=tiny_nodes("A"), write=False)


def test_ghost_node_label_raises(tmp_path):
    with pytest.raises(ValueError, match="ghosts \\['C'\\]"):
        build_bundle(tiny_frame(), nodes=tiny_nodes("A", "B", "C"), write=False)


def test_nan_values_refuse_to_bundle(tmp_path):
    with pytest.raises(ValueError, match="NaN"):
        build_bundle(
            tiny_frame(carbon=[float("nan")]),
            nodes=tiny_nodes("A", "B"),
            write=False,
        )


# -- the written artifact ---------------------------------------------------

def test_written_pair_round_trips_and_is_deterministic(tmp_path):
    b = build_bundle(tiny_frame(), nodes=tiny_nodes("A", "B"), write=False)
    out = tmp_path / "circwood.json"
    paths = write_bundle(b, out)
    assert [p.name for p in paths] == ["circwood.json", "circwood.json.gz"]

    text = out.read_text(encoding="utf-8")
    assert json.loads(text) == b
    assert gzip.decompress(paths[1].read_bytes()).decode("utf-8") == text

    first = (out.read_bytes(), paths[1].read_bytes())
    write_bundle(b, out)
    assert (out.read_bytes(), paths[1].read_bytes()) == first
