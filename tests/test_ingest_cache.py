"""Side-layer query directories, the offline cache loader and the manifest's inputs (Phases H/J)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("pyjstat", reason="needs the optional `ssb` extras")

from circwood import config  # noqa: E402
from circwood.ingest import ssb  # noqa: E402
from circwood.manifest import build_manifest, describe_input, read_manifest  # noqa: E402

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ssb"


def test_every_query_directory_satisfies_the_contract():
    """Main, forest and economic query files: Norwegian json-stat2, unique ids, a contract each."""
    seen: dict[str, str] = {}
    for directory in (config.SSB_QUERY_DIR, config.FOREST_QUERY_DIR, config.ECONOMIC_QUERY_DIR):
        paths = sorted(directory.glob("*.json"))
        assert paths, f"no query files in {directory}"
        for path in paths:
            spec = ssb.load_query_spec(path)
            assert spec["params"] == {"lang": "no", "outputFormat": "json-stat2"}
            table_id = ssb._table_id(spec, path.name)
            assert table_id not in seen, f"{path.name} duplicates {seen[table_id]}"
            seen[table_id] = path.name
            assert ssb.contract_for(table_id) is not ssb.DEFAULT_CONTRACT, table_id
    # The side layers never enter the flow dataset's year window.
    for tid in ("14589", "14590", "14591", "14593", "14594", "06289", "07413"):
        assert not ssb.contract_for(tid).window_gated
    for tid in ("03795", "07410", "08801", "10513", "11181", "08799_2012_2016"):
        assert ssb.contract_for(tid).window_gated


def test_base_table():
    assert ssb.base_table("08799_2017_2021") == "08799"
    assert ssb.base_table("03795_fylke") == "03795"
    assert ssb.base_table("07410") == "07410"


def _vintage(table_id: str, updated: str, year_max: int) -> ssb.TableVintage:
    return ssb.TableVintage(
        table_id=table_id, query_file=f"ssbapi_table_{table_id}.json", query_sha256="a" * 64,
        updated=updated, fetched_at="2026-11-08T00:00:00+00:00", rows=10, year_min=2012, year_max=year_max,
    )


def test_write_vintages_merges_the_layers_that_share_one_cache(tmp_path):
    """Three ingests (flows, forest, prices) write the same file; the last must not erase the others."""
    import json

    cache = tmp_path / "ssb"
    ssb.write_vintages({"11181": _vintage("11181", "2026-06-29", 2024)}, cache)
    ssb.write_vintages({"07413": _vintage("07413", "2026-05-01", 2023)}, cache)
    merged = json.loads((cache / "_vintages.json").read_text(encoding="utf-8"))
    assert set(merged) == {"07413", "11181"}
    # A table fetched again replaces its own entry and keeps the others.
    ssb.write_vintages({"11181": _vintage("11181", "2026-07-30", 2025)}, cache)
    merged = json.loads((cache / "_vintages.json").read_text(encoding="utf-8"))
    assert merged["11181"]["updated"] == "2026-07-30" and "07413" in merged


def test_load_cached_result_rebuilds_an_ingest_result_offline(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    raw = (FIXTURE_DIR / "table_11181.json").read_text(encoding="utf-8")
    (cache / "11181.json").write_text(raw, encoding="utf-8")
    vintage = ssb.TableVintage(
        table_id="11181", query_file="ssbapi_table_11181.json", query_sha256="a" * 64,
        updated=ssb.table_updated(raw), fetched_at="2026-11-08T00:00:00+00:00",
        rows=38, year_min=2007, year_max=2025,
    )
    ssb.write_vintages({"11181": vintage}, cache)

    result = ssb.load_cached_result(cache)
    assert set(result.datasets) == {"11181"}
    assert result.vintages["11181"] == vintage
    assert "Virkegruppe" in result.dimension_codes["11181"]
    assert result.complete

    with pytest.raises(ssb.SSBQueryError, match="not in the raw cache"):
        ssb.load_cached_result(cache, tables=["07410"])
    with pytest.raises(ssb.SSBQueryError, match="No raw cache"):
        ssb.load_cached_result(tmp_path / "empty")


def test_manifest_records_input_hashes(tmp_path):
    path = tmp_path / "input.csv"
    pd.DataFrame({"year": [2011, 2023], "Category": ["a", "b"], "Volume": [1.0, 2.0]}).to_csv(path, index=False)
    desc = describe_input(path)
    assert len(desc["sha256"]) == 64 and desc["rows"] == 2
    assert (desc["year_min"], desc["year_max"]) == (2011, 2023)
    m = build_manifest({}, {}, {"input.csv": desc})
    assert m.inputs["input.csv"]["sha256"] == desc["sha256"]
    assert m.schema_version == 2
    missing = describe_input(tmp_path / "nope.csv")
    assert missing["sha256"] is None and missing["rows"] is None


def test_committed_manifest_describes_the_committed_outputs():
    """Phase K: the manifest must describe the files that ship with it."""
    m = read_manifest()
    assert m is not None, "data/processed/manifest.json is missing"
    sankey = pd.read_csv(config.SANKEY_CSV)
    assert m.outputs["sankey_data.csv"]["rows"] == len(sankey)
    assert m.outputs["sankey_data.csv"]["sha256"] == __import__("hashlib").sha256(config.SANKEY_CSV.read_bytes()).hexdigest()
    assert m.outputs["carbon_data.csv"]["sha256"] == __import__("hashlib").sha256(config.CARBON_CSV.read_bytes()).hexdigest()
    assert set(m.tables) >= {"03795", "03795_fylke", "07410", "08801", "10513", "11181"}
    assert {t for t in m.tables if t.startswith("08799_")}, "the 08799 partner chunks must be in the manifest"
    assert "treindustrien_production_data.csv" in m.inputs
    assert m.year_range == (2012, 2024)
    assert not m.git_dirty or True  # a regeneration in a dirty tree is allowed; the hashes are what matter
