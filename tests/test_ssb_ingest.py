"""Tests for the hardened SSB ingestion layer (Phase A).

Nothing here touches the network. The parse tests run against real json-stat2
responses saved under ``tests/fixtures/ssb/``; the orchestration tests
monkeypatch :func:`circwood.ingest.ssb.fetch_table`, which is the whole point of
splitting fetch from parse.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("requests", reason="needs the optional `ssb` extras")
pytest.importorskip("pyjstat", reason="needs the optional `ssb` extras")

from circwood import config  # noqa: E402
from circwood.ingest import ssb  # noqa: E402

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ssb"
RAW_11181 = (FIXTURE_DIR / "table_11181.json").read_text(encoding="utf-8")
RAW_07410 = (FIXTURE_DIR / "table_07410.json").read_text(encoding="utf-8")


def _v2_spec(table_id: str = "11181", **params) -> dict:
    spec = {
        "url": f"https://data.ssb.no/api/pxwebapi/v2/tables/{table_id}/data",
        "payload": {"selection": []},
    }
    if params:
        spec["params"] = params
    return spec


def _write_query(directory: Path, name: str, spec: dict) -> Path:
    path = directory / name
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# parse_table / table_updated — pure, against real responses
# ---------------------------------------------------------------------------
def test_parse_table_returns_the_expected_frame():
    frames = ssb.parse_table(RAW_11181, "11181")
    assert len(frames) == 1
    df = frames[0]
    assert list(df.columns) == ["år", "virkesgruppe", "statistikkvariabel", "value"]
    # Norwegian labels survive the round-trip; the transforms filter on them.
    assert set(df["virkesgruppe"]) == {"Ved til brensel bar", "Ved til brensel lauv"}
    assert df["value"].notna().all()


def test_parse_table_handles_the_larger_removals_table():
    df = pd.concat(ssb.parse_table(RAW_07410, "07410"), ignore_index=True)
    assert {"år", "sortiment", "value"} <= set(df.columns)
    assert "Gran sagtømmer" in set(df["sortiment"])


def test_table_updated_reads_ssbs_own_revision_date():
    assert ssb.table_updated(RAW_11181, "11181") == "2026-06-29T06:00:00Z"


def test_parse_table_rejects_non_json():
    with pytest.raises(ssb.SSBParseError, match="not JSON"):
        ssb.parse_table("<html>503 Service Unavailable</html>", "11181")


def test_parse_table_rejects_json_that_is_not_json_stat():
    with pytest.raises(ssb.SSBParseError, match="not valid json-stat2"):
        ssb.parse_table(json.dumps({"error": "no such table"}), "11181")


# ---------------------------------------------------------------------------
# Request contract — the wrong-lang class of bug
# ---------------------------------------------------------------------------
def test_normalize_spec_injects_the_missing_contract_params():
    out = ssb.normalize_spec(_v2_spec(), "test.json")
    assert out["params"] == {"outputFormat": "json-stat2", "lang": "no"}


def test_normalize_spec_leaves_a_conforming_spec_alone():
    spec = _v2_spec(lang="no", outputFormat="json-stat2")
    assert ssb.normalize_spec(ssb.normalize_spec(spec)) == ssb.normalize_spec(spec)


def test_normalize_spec_rejects_a_non_norwegian_query():
    """The worst bug class: HTTP 200, clean parse, zero matching rows."""
    with pytest.raises(ssb.SSBQueryError, match="lang='en'"):
        ssb.normalize_spec(_v2_spec(lang="en"), "test.json")


def test_normalize_spec_rejects_a_non_jsonstat_output_format():
    with pytest.raises(ssb.SSBQueryError, match="outputFormat"):
        ssb.normalize_spec(_v2_spec(outputFormat="csv"), "test.json")


def test_normalize_spec_enforces_the_legacy_v0_contract():
    v0 = {"postUrl": "https://data.ssb.no/api/v0/no/table/11181", "queryObj": {}}
    assert ssb.normalize_spec(v0)["queryObj"]["response"]["format"] == "json-stat2"

    english = {"postUrl": "https://data.ssb.no/api/v0/en/table/11181", "queryObj": {}}
    with pytest.raises(ssb.SSBQueryError, match="Norwegian-language endpoint"):
        ssb.normalize_spec(english, "legacy.json")


def test_normalize_spec_rejects_a_spec_with_no_endpoint():
    with pytest.raises(ssb.SSBQueryError, match="neither"):
        ssb.normalize_spec({"payload": {}}, "test.json")


def test_shipped_query_files_satisfy_the_contract():
    """The repo's own queries must be Norwegian json-stat2 with unique ids."""
    paths = sorted(Path(config.SSB_QUERY_DIR).glob("*.json"))
    assert paths, "no query files found"
    seen: dict[str, str] = {}
    for path in paths:
        spec = ssb.load_query_spec(path)  # raises if off-contract
        assert spec["params"]["lang"] == "no"
        assert spec["params"]["outputFormat"] == "json-stat2"
        table_id = ssb._table_id(spec, path.name)
        assert table_id not in seen, f"{path.name} duplicates {seen.get(table_id)}"
        seen[table_id] = path.name
    # Every shipped query has a validation contract behind it.
    uncontracted = sorted(t for t in seen if ssb.contract_for(t) is ssb.DEFAULT_CONTRACT)
    assert not uncontracted, f"tables with no TableContract: {uncontracted}"


def test_load_query_spec_reports_an_unreadable_file(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ssb.SSBQueryError, match="cannot read query file"):
        ssb.load_query_spec(path)


# ---------------------------------------------------------------------------
# validate_table
# ---------------------------------------------------------------------------
def test_validate_table_accepts_a_real_response():
    year_min, year_max = ssb.validate_table("11181", ssb.parse_table(RAW_11181))
    assert year_min <= 2012 and year_max >= 2022


def test_validate_table_rejects_a_missing_column():
    frames = ssb.parse_table(RAW_11181)
    dropped = [f.drop(columns=["virkesgruppe"]) for f in frames]
    with pytest.raises(ssb.SSBValidationError, match="virkesgruppe"):
        ssb.validate_table("11181", dropped)


def test_validate_table_rejects_an_all_nan_value_column():
    frames = ssb.parse_table(RAW_11181)
    blanked = [f.assign(value=float("nan")) for f in frames]
    with pytest.raises(ssb.SSBValidationError, match="entirely NaN"):
        ssb.validate_table("11181", blanked)


def test_validate_table_rejects_truncated_year_coverage():
    frames = ssb.parse_table(RAW_11181)
    truncated = [f[pd.to_numeric(f["år"]) <= 2015] for f in frames]
    with pytest.raises(ssb.SSBValidationError, match="coverage ends at 2015"):
        ssb.validate_table("11181", truncated)

    late = [f[pd.to_numeric(f["år"]) >= 2015] for f in frames]
    with pytest.raises(ssb.SSBValidationError, match="coverage starts at 2015"):
        ssb.validate_table("11181", late)


def test_validate_table_rejects_an_empty_result():
    with pytest.raises(ssb.SSBValidationError, match="zero rows"):
        ssb.validate_table("11181", [pd.DataFrame(columns=["år", "virkesgruppe", "value"])])


def test_validate_table_names_the_table_in_every_message():
    frames = ssb.parse_table(RAW_11181)
    with pytest.raises(ssb.SSBValidationError, match="table 11181"):
        ssb.validate_table("11181", [f.drop(columns=["value"]) for f in frames])


# ---------------------------------------------------------------------------
# make_session — retry configuration
# ---------------------------------------------------------------------------
def test_session_retries_transient_failures_including_post():
    session = ssb.make_session()
    try:
        retry = session.get_adapter("https://data.ssb.no").max_retries
        assert retry.total == 5
        assert retry.backoff_factor == 1
        assert set(retry.status_forcelist) == {429, 500, 502, 503, 504}
        assert retry.respect_retry_after_header
        # SSB queries are POSTs; urllib3 retries only idempotent methods unless
        # POST is allowed explicitly, so this assertion is the whole feature.
        assert "POST" in retry.allowed_methods
        assert "circwood" in session.headers["User-Agent"]
    finally:
        session.close()


# ---------------------------------------------------------------------------
# process_query_files — orchestration and failure policy
# ---------------------------------------------------------------------------
@pytest.fixture
def query_dir(tmp_path):
    """A directory with two valid query files, for 11181 and 07410."""
    directory = tmp_path / "queries"
    directory.mkdir()
    _write_query(directory, "ssbapi_table_11181.json", _v2_spec("11181"))
    _write_query(directory, "ssbapi_table_07410.json", _v2_spec("07410"))
    return directory


@pytest.fixture
def fake_fetch(monkeypatch):
    """Serve saved responses instead of SSB; ``responses`` is editable per test."""
    responses = {"11181": RAW_11181, "07410": RAW_07410}

    def _fetch(spec, *, session=None, timeout=ssb.DEFAULT_TIMEOUT):
        table_id = ssb._table_id(spec, "")
        payload = responses[table_id]
        if isinstance(payload, Exception):
            raise payload
        return payload

    monkeypatch.setattr(ssb, "fetch_table", _fetch)
    return responses


def test_process_query_files_returns_validated_tables(query_dir, fake_fetch, tmp_path):
    result = ssb.process_query_files(query_dir, cache_dir=tmp_path / "cache")
    assert set(result.datasets) == {"11181", "07410"}
    assert result.complete and not result.failures
    assert list(result.datasets["11181"][0].columns)[-1] == "value"


def test_process_query_files_captures_vintage(query_dir, fake_fetch, tmp_path):
    result = ssb.process_query_files(query_dir, cache_dir=tmp_path / "cache")
    vintage = result.vintages["11181"]
    assert vintage.updated == "2026-06-29T06:00:00Z"  # SSB's revision date
    assert vintage.fetched_at.endswith("+00:00")      # our pull timestamp
    assert vintage.query_file == "ssbapi_table_11181.json"
    assert len(vintage.query_sha256) == 64
    assert vintage.year_min <= 2012 and vintage.year_max >= 2022
    assert vintage.rows > 0

    written = json.loads((tmp_path / "cache" / "_vintages.json").read_text(encoding="utf-8"))
    assert written["11181"]["updated"] == "2026-06-29T06:00:00Z"


def test_process_query_files_caches_raw_responses(query_dir, fake_fetch, tmp_path):
    cache = tmp_path / "cache"
    ssb.process_query_files(query_dir, cache_dir=cache)
    assert json.loads((cache / "11181.json").read_text(encoding="utf-8"))["updated"]
    assert (cache / "07410.json").exists()


def test_raw_response_is_cached_even_when_parsing_fails(query_dir, fake_fetch, tmp_path):
    """The unparseable body is the one you most need to look at afterwards."""
    fake_fetch["07410"] = "<html>502 Bad Gateway</html>"
    cache = tmp_path / "cache"
    with pytest.raises(ssb.SSBIngestError):
        ssb.process_query_files(query_dir, cache_dir=cache)
    assert (cache / "07410.json").read_text(encoding="utf-8").startswith("<html>")


def test_partial_failure_raises_and_names_every_broken_table(query_dir, fake_fetch, tmp_path):
    import requests

    fake_fetch["07410"] = requests.exceptions.HTTPError("500 Server Error")
    with pytest.raises(ssb.SSBIngestError) as excinfo:
        ssb.process_query_files(query_dir, cache_dir=tmp_path / "cache")
    assert set(excinfo.value.failures) == {"07410"}
    assert "500 Server Error" in str(excinfo.value)
    assert "allow_partial" in str(excinfo.value)


def test_retry_exhaustion_is_a_failure_not_a_silent_gap(query_dir, fake_fetch, tmp_path):
    import requests

    fake_fetch["11181"] = requests.exceptions.RetryError("max retries exceeded")
    fake_fetch["07410"] = requests.exceptions.ConnectionError("connection reset")
    with pytest.raises(ssb.SSBIngestError) as excinfo:
        ssb.process_query_files(query_dir, cache_dir=tmp_path / "cache")
    assert set(excinfo.value.failures) == {"11181", "07410"}


def test_validation_failure_is_reported_as_an_ingest_failure(query_dir, fake_fetch, tmp_path):
    document = json.loads(RAW_11181)
    document["value"] = [None] * len(document["value"])
    fake_fetch["11181"] = json.dumps(document)
    with pytest.raises(ssb.SSBIngestError, match="entirely NaN"):
        ssb.process_query_files(query_dir, cache_dir=tmp_path / "cache")


def test_allow_partial_opts_out_explicitly(query_dir, fake_fetch, tmp_path):
    import requests

    fake_fetch["07410"] = requests.exceptions.HTTPError("500 Server Error")
    result = ssb.process_query_files(
        query_dir, allow_partial=True, cache_dir=tmp_path / "cache"
    )
    assert set(result.datasets) == {"11181"}
    assert set(result.failures) == {"07410"}
    assert not result.complete


def test_duplicate_table_ids_raise_before_any_fetch(tmp_path, fake_fetch):
    directory = tmp_path / "queries"
    directory.mkdir()
    _write_query(directory, "a_table_11181.json", _v2_spec("11181"))
    _write_query(directory, "b_table_11181.json", _v2_spec("11181"))
    with pytest.raises(ssb.SSBQueryError, match="Duplicate SSB table id"):
        ssb.process_query_files(directory, cache_dir=tmp_path / "cache")


def test_duplicate_table_ids_are_not_excused_by_allow_partial(tmp_path, fake_fetch):
    directory = tmp_path / "queries"
    directory.mkdir()
    _write_query(directory, "a_table_11181.json", _v2_spec("11181"))
    _write_query(directory, "b_table_11181.json", _v2_spec("11181"))
    with pytest.raises(ssb.SSBQueryError):
        ssb.process_query_files(
            directory, allow_partial=True, cache_dir=tmp_path / "cache"
        )


def test_off_contract_query_file_raises_before_any_fetch(tmp_path, fake_fetch):
    directory = tmp_path / "queries"
    directory.mkdir()
    _write_query(directory, "ssbapi_table_11181.json", _v2_spec("11181", lang="en"))
    with pytest.raises(ssb.SSBQueryError, match="lang"):
        ssb.process_query_files(directory, cache_dir=tmp_path / "cache")


def test_empty_query_directory_raises(tmp_path):
    directory = tmp_path / "queries"
    directory.mkdir()
    with pytest.raises(ssb.SSBQueryError, match="No .json query files"):
        ssb.process_query_files(directory, cache_dir=tmp_path / "cache")


def test_missing_query_directory_raises(tmp_path):
    with pytest.raises(ssb.SSBQueryError, match="Query directory not found"):
        ssb.process_query_files(tmp_path / "nope", cache_dir=tmp_path / "cache")
