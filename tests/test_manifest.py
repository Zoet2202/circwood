"""Tests for the dataset manifest and the refresh orchestrator's safety net."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from circwood import manifest as manifestmod
from circwood.manifest import Manifest, build_manifest, read_manifest, write_manifest

VINTAGES = {
    "11181": {
        "table_id": "11181", "query_file": "ssbapi_table_11181.json",
        "query_sha256": "a" * 64, "updated": "2026-06-29T06:00:00Z",
        "fetched_at": "2026-07-28T12:00:00+00:00", "rows": 38,
        "year_min": 2007, "year_max": 2025,
    },
    "10513": {
        "table_id": "10513", "query_file": "ssbapi_table_10513.json",
        "query_sha256": "b" * 64, "updated": "2026-03-24T07:00:00Z",
        "fetched_at": "2026-07-28T12:00:00+00:00", "rows": 13,
        "year_min": 2012, "year_max": 2024,
    },
}

SANKEY = pd.DataFrame({
    "source": ["Spruce", "Sawmills"],
    "target": ["Sawmills", "Pulpmills"],
    "volume": [1_000_000.0, 260_000.0],
    "year": [2020, 2020],
    "origin": ["test", "test"],
})


def test_build_manifest_records_every_table():
    m = build_manifest(VINTAGES)
    assert set(m.tables) == {"10513", "11181"}
    assert m.tables["11181"]["query_sha256"] == "a" * 64
    assert m.package_version
    assert m.schema_version == manifestmod.SCHEMA_VERSION


def test_data_as_of_is_the_newest_ssb_revision_not_our_pull_time():
    """"Data as of" must describe the statistic's age, not our copy's."""
    m = build_manifest(VINTAGES)
    assert m.data_as_of == "2026-06-29"      # newest SSB `updated`
    assert m.oldest_table_as_of == "2026-03-24"
    assert m.generated_at[:4] != "2026-06"   # not derived from generated_at


def test_data_as_of_is_none_without_tables():
    assert build_manifest({}).data_as_of is None


def test_manifest_accepts_tablevintage_objects():
    ssb = pytest.importorskip("circwood.ingest.ssb", reason="needs the `ssb` extras")
    vintage = ssb.TableVintage(
        table_id="11181", query_file="q.json", query_sha256="c" * 64,
        updated="2026-06-29T06:00:00Z", fetched_at="2026-07-28T12:00:00+00:00",
        rows=38, year_min=2007, year_max=2025,
    )
    m = build_manifest({"11181": vintage})
    assert m.tables["11181"]["updated"] == "2026-06-29T06:00:00Z"


def test_describe_dataset_summarises_the_csv(tmp_path):
    path = tmp_path / "sankey.csv"
    SANKEY.to_csv(path, index=False)
    summary = manifestmod.describe_dataset(SANKEY, path)
    assert summary["rows"] == 2
    assert summary["year_min"] == 2020 and summary["year_max"] == 2020
    assert summary["nodes"] == 3
    assert len(summary["sha256"]) == 64


def test_manifest_round_trips_through_disk(tmp_path):
    path = tmp_path / "manifest.json"
    original = build_manifest(VINTAGES, {"sankey_data.csv": {"rows": 2, "year_min": 2020, "year_max": 2020}})
    write_manifest(original, path)
    loaded = read_manifest(path)
    assert loaded == original
    assert loaded.year_range == (2020, 2020)
    # Readable in review: real JSON, Norwegian characters unescaped.
    assert json.loads(path.read_text(encoding="utf-8"))["tables"]["11181"]["rows"] == 38


def test_manifest_path_follows_the_current_config(tmp_path, monkeypatch):
    """Resolved at call time, not bound at import.

    A module-level constant would make the refresh loop snapshot one path for
    restore-on-failure and write to another — identical in production, divergent
    under any redirection, and only noticeable after something was overwritten.
    """
    monkeypatch.setattr(manifestmod.config, "PROCESSED_DIR", tmp_path)
    assert manifestmod.manifest_path() == tmp_path / "manifest.json"

    write_manifest(build_manifest(VINTAGES))
    assert (tmp_path / "manifest.json").exists()
    assert read_manifest().data_as_of == "2026-06-29"


def test_read_manifest_returns_none_when_absent(tmp_path):
    """A checkout that never ran a fresh build is normal, not an error."""
    assert read_manifest(tmp_path / "nope.json") is None


def test_read_manifest_returns_none_on_corruption(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{ truncated", encoding="utf-8")
    assert read_manifest(path) is None


def test_describe_is_a_one_line_caption():
    m = build_manifest(VINTAGES, {"sankey_data.csv": {"year_min": 2012, "year_max": 2024}})
    caption = m.describe()
    assert caption.startswith("Data as of 2026-06-29")
    assert "2012–2024" in caption
    assert "\n" not in caption


def test_from_dict_ignores_unknown_keys():
    """A manifest written by a newer schema version must still be readable."""
    payload = build_manifest(VINTAGES).to_dict() | {"some_future_field": 1}
    assert Manifest.from_dict(payload).data_as_of == "2026-06-29"
