"""Tests for the refresh orchestrator's safety net and its PR report.

The full chain needs network and is exercised by the scheduled workflow. What is
worth pinning here is the property that makes an unattended refresh safe: a run
that fails a gate must leave the working tree exactly as it found it, so there is
never a half-updated dataset sitting there waiting to be committed — plus the
Phase J gates: lost year coverage blocks, and an unexplained move is named as
such.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from circwood import config
from circwood import pipeline as pipelinemod
from circwood import refresh as refreshmod
from circwood.coverage import WindowError
from circwood.diff import DiffReport, UnexplainedDriftError, diff_datasets
from circwood.ingest import ssb as ssbmod
from circwood.manifest import build_manifest
from circwood.refresh import RefreshResult
from circwood.transform import assemble as assemblemod

COMMITTED = pd.DataFrame({
    "source": ["Spruce", "Sawmills"],
    "target": ["Sawmills", "Pulpmills"],
    "volume": [1_000_000.0, 260_000.0],
    "year": [2020, 2020],
    "origin": ["SSB table 07410", "SSB table 07410 | Treindustrien årsrapport"],
})

DRIFTED = COMMITTED.assign(volume=[1_000_000.0, 900_000.0])


@pytest.fixture
def isolated_outputs(tmp_path, monkeypatch):
    """Point every managed output at tmp_path so tests never touch the repo."""
    processed = tmp_path / "processed"
    processed.mkdir()
    paths = {
        "sankey": processed / "sankey_data.csv",
        "carbon": processed / "carbon_data.csv",
        "golden": tmp_path / "golden_sankey_data.csv",
        "beta": processed / "beta_series.csv",
        "sensitivity": processed / "beta_sensitivity.csv",
        "stock": processed / "hwp_stock.csv",
        "forest": processed / "forest_resource.csv",
        "economic": processed / "economic_value.csv",
        "history": processed / "refresh_history.jsonl",
    }
    for key in ("sankey", "carbon", "golden"):
        COMMITTED.to_csv(paths[key], index=False)
    paths["beta"].write_text("# committed beta\nyear\n2019\n", encoding="utf-8")
    paths["sensitivity"].write_text("parameter\ncentral\n", encoding="utf-8")
    paths["stock"].write_text("# committed stock\n", encoding="utf-8")

    monkeypatch.setattr(config, "PROCESSED_DIR", processed)
    monkeypatch.setattr(config, "SANKEY_CSV", paths["sankey"])
    monkeypatch.setattr(config, "CARBON_CSV", paths["carbon"])
    monkeypatch.setattr(config, "BETA_SERIES_CSV", paths["beta"])
    monkeypatch.setattr(config, "BETA_SENSITIVITY_CSV", paths["sensitivity"])
    monkeypatch.setattr(config, "HWP_STOCK_CSV", paths["stock"])
    monkeypatch.setattr(config, "FOREST_RESOURCE_CSV", paths["forest"])
    monkeypatch.setattr(config, "ECONOMIC_VALUE_CSV", paths["economic"])
    monkeypatch.setattr(config, "REFRESH_HISTORY", paths["history"])
    monkeypatch.setattr(config, "FOREST_QUERY_DIR", tmp_path / "no-forest-queries")
    monkeypatch.setattr(config, "ECONOMIC_QUERY_DIR", tmp_path / "no-economic-queries")
    monkeypatch.setattr(refreshmod, "GOLDEN_FIXTURE", paths["golden"])
    # The stub frames are not a real flow graph, so the analysis exports are
    # stubbed here (they write markers); the failure path is pinned below.
    monkeypatch.setattr(
        refreshmod, "export_beta",
        lambda path: (path.write_text("# beta\nyear\n2020\n", encoding="utf-8"), path)[1],
    )

    def fake_analyses():
        config.BETA_SENSITIVITY_CSV.write_text("parameter\ncentral\n", encoding="utf-8")
        config.HWP_STOCK_CSV.write_text("# stock\n", encoding="utf-8")
        return {"beta sensitivity": config.BETA_SENSITIVITY_CSV, "HWP stock": config.HWP_STOCK_CSV}

    monkeypatch.setattr(refreshmod, "export_analyses", fake_analyses)
    # The stub frames carry no carbon column.
    monkeypatch.setattr("circwood.validate.assert_carbon_balanced", lambda *a, **k: None)
    return paths


@pytest.fixture
def stub_ingest(monkeypatch):
    monkeypatch.setattr(
        ssbmod, "process_query_files",
        lambda *a, **k: ssbmod.IngestResult(datasets={}, vintages={}),
    )


def _pass_the_data_gates(monkeypatch):
    monkeypatch.setattr(refreshmod, "_control_totals", lambda df: [])
    monkeypatch.setattr("circwood.validate.assert_window_balanced", lambda *a, **k: None)
    monkeypatch.setattr(
        "circwood.reconcile.treindustrien_reconciliation",
        lambda df, **kw: pd.DataFrame([{"metric": "m", "rel_diff": 0.0}]),
    )


def test_the_manifest_is_written_inside_the_configured_output_dir(
    isolated_outputs, stub_ingest, monkeypatch, tmp_path
):
    """Guards the whole isolation story: nothing may escape to the real repo."""
    monkeypatch.setattr(assemblemod, "run_fresh_pipeline",
                        lambda **kw: (COMMITTED.to_csv(config.SANKEY_CSV, index=False), COMMITTED)[1])
    monkeypatch.setattr(pipelinemod, "build_carbon", lambda **kw: COMMITTED)
    _pass_the_data_gates(monkeypatch)
    refreshmod.refresh()
    assert (config.PROCESSED_DIR / "manifest.json").exists()
    assert config.PROCESSED_DIR != tmp_path.parent  # sanity: really redirected
    manifest = json.loads((config.PROCESSED_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert "inputs" in manifest


def test_a_failed_gate_leaves_the_working_tree_untouched(
    isolated_outputs, stub_ingest, monkeypatch
):
    """The property the whole unattended loop rests on."""
    before = {name: path.read_bytes() for name, path in isolated_outputs.items() if path.exists()}

    def fake_rebuild(**kwargs):
        DRIFTED.to_csv(config.SANKEY_CSV, index=False)   # the rebuild lands ...
        return DRIFTED

    def explode(**kwargs):
        raise RuntimeError("carbon model blew up")       # ... then a gate fails

    monkeypatch.setattr(assemblemod, "run_fresh_pipeline", fake_rebuild)
    monkeypatch.setattr(pipelinemod, "build_carbon", explode)

    with pytest.raises(RuntimeError, match="carbon model blew up"):
        refreshmod.refresh()

    after = {name: path.read_bytes() for name, path in isolated_outputs.items() if path.exists()}
    assert after == before


def test_files_created_by_a_failed_run_are_removed(
    isolated_outputs, stub_ingest, monkeypatch
):
    """A baseline run that fails must not leave a stray dataset behind."""
    for path in isolated_outputs.values():
        path.unlink(missing_ok=True)

    def fake_rebuild(**kwargs):
        DRIFTED.to_csv(config.SANKEY_CSV, index=False)
        return DRIFTED

    monkeypatch.setattr(assemblemod, "run_fresh_pipeline", fake_rebuild)
    monkeypatch.setattr(
        pipelinemod, "build_carbon",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError):
        refreshmod.refresh()
    assert not isolated_outputs["sankey"].exists()


def test_lost_year_coverage_blocks_before_the_rebuild(isolated_outputs, monkeypatch):
    """Phase J: a gated table that no longer reaches DATASET_MAX_YEAR fails the run and restores."""
    short = ssbmod.TableVintage(
        table_id="10513", query_file="q.json", query_sha256="a" * 64,
        updated="2026-03-24T07:00:00Z", fetched_at="2026-11-08T00:00:00+00:00",
        rows=12, year_min=2012, year_max=2023,
    )
    monkeypatch.setattr(
        ssbmod, "process_query_files",
        lambda *a, **k: ssbmod.IngestResult(datasets={}, vintages={"10513": short}),
    )
    rebuilt = {"called": False}

    def fake_rebuild(**kwargs):
        rebuilt["called"] = True
        return COMMITTED

    monkeypatch.setattr(assemblemod, "run_fresh_pipeline", fake_rebuild)
    before = isolated_outputs["sankey"].read_bytes()
    with pytest.raises(WindowError, match="10513 ends 2023"):
        refreshmod.refresh(as_of="2026-11-08")
    assert not rebuilt["called"]
    assert isolated_outputs["sankey"].read_bytes() == before


def test_an_unexplained_drift_is_named_and_accept_drift_adopts_it(
    isolated_outputs, stub_ingest, monkeypatch
):
    """Nothing upstream changed, yet a flow moved: the serious class. accept_drift is the escape hatch."""
    def fake_rebuild(**kwargs):
        DRIFTED.to_csv(config.SANKEY_CSV, index=False)
        return DRIFTED

    monkeypatch.setattr(assemblemod, "run_fresh_pipeline", fake_rebuild)
    monkeypatch.setattr(pipelinemod, "build_carbon", lambda **kw: DRIFTED)
    _pass_the_data_gates(monkeypatch)

    with pytest.raises(UnexplainedDriftError, match="no SSB source table was revised"):
        refreshmod.refresh()

    result = refreshmod.refresh(accept_drift=True)
    assert not result.diff.ok          # the drift is still reported ...
    assert result.golden_updated       # ... but the dataset was adopted
    assert result.accepted_drift
    body = result.report()
    assert "Vintage diff: FAIL" in body
    assert "accept_drift" in body and "unexplained" in body
    # The drift history recorded the accepted move.
    lines = isolated_outputs["history"].read_text(encoding="utf-8").strip().splitlines()
    entry = json.loads(lines[-1])
    assert entry["accepted_drift"] is True and entry["flows_breaching"] == 1


def test_a_revised_table_explains_a_move(isolated_outputs, stub_ingest, monkeypatch):
    """The same move is 'ssb-revision' when the source table's SSB date changed."""
    from circwood import manifest as manifestmod

    old = build_manifest({"07410": {"updated": "2026-01-01T00:00:00Z", "query_sha256": "a" * 64}})
    manifestmod.write_manifest(old)
    new_vintage = ssbmod.TableVintage(
        table_id="07410", query_file="q.json", query_sha256="a" * 64,
        updated="2026-05-21T06:00:00Z", fetched_at="2026-11-08T00:00:00+00:00",
        rows=1, year_min=2012, year_max=2024,
    )
    monkeypatch.setattr(
        ssbmod, "process_query_files",
        lambda *a, **k: ssbmod.IngestResult(datasets={}, vintages={"07410": new_vintage}),
    )
    monkeypatch.setattr(assemblemod, "run_fresh_pipeline",
                        lambda **kw: (DRIFTED.to_csv(config.SANKEY_CSV, index=False), DRIFTED)[1])
    monkeypatch.setattr(pipelinemod, "build_carbon", lambda **kw: DRIFTED)
    _pass_the_data_gates(monkeypatch)

    # Explained, but still past the threshold: the ordinary gate blocks it.
    with pytest.raises(Exception) as excinfo:
        refreshmod.refresh(as_of="2026-11-08")
    assert not isinstance(excinfo.value, UnexplainedDriftError)
    assert "beyond tolerance" in str(excinfo.value)
    result = refreshmod.refresh(accept_drift=True, as_of="2026-11-08")
    assert result.attribution["class"].tolist() == ["ssb-revision"]
    assert result.revised_tables == ["07410"]


def test_a_failed_beta_export_restores_every_output(
    isolated_outputs, stub_ingest, monkeypatch
):
    """Phase F: the beta export runs after the diff gate and is a gate itself."""
    before = {name: path.read_bytes() for name, path in isolated_outputs.items() if path.exists()}

    def fake_rebuild(**kwargs):
        DRIFTED.to_csv(config.SANKEY_CSV, index=False)
        return DRIFTED

    def fake_carbon(**kwargs):
        DRIFTED.to_csv(config.CARBON_CSV, index=False)
        return DRIFTED

    def failing_beta(path):
        path.write_text("half-written", encoding="utf-8")   # lands, then fails
        raise ValueError("year 2023: closure gap 0.3000 exceeds BETA_CLOSURE_TOL")

    monkeypatch.setattr(assemblemod, "run_fresh_pipeline", fake_rebuild)
    monkeypatch.setattr(pipelinemod, "build_carbon", fake_carbon)
    _pass_the_data_gates(monkeypatch)
    monkeypatch.setattr(refreshmod, "export_beta", failing_beta)

    with pytest.raises(ValueError, match="closure gap"):
        refreshmod.refresh(accept_drift=True)    # past the diff gate, into the export

    after = {name: path.read_bytes() for name, path in isolated_outputs.items() if path.exists()}
    assert after == before
    assert not (config.PROCESSED_DIR / "manifest.json").exists()
    assert not isolated_outputs["history"].exists()


def test_a_successful_refresh_re_exports_beta_and_the_analyses(isolated_outputs, stub_ingest, monkeypatch):
    def fake_rebuild(**kwargs):
        COMMITTED.to_csv(config.SANKEY_CSV, index=False)
        return COMMITTED

    monkeypatch.setattr(assemblemod, "run_fresh_pipeline", fake_rebuild)
    monkeypatch.setattr(pipelinemod, "build_carbon", lambda **kw: COMMITTED)
    _pass_the_data_gates(monkeypatch)

    result = refreshmod.refresh()
    assert result.beta_series == config.PROCESSED_DIR / "beta_series.csv"
    assert isolated_outputs["beta"].read_text(encoding="utf-8") == "# beta\nyear\n2020\n"
    assert isolated_outputs["stock"].read_text(encoding="utf-8") == "# stock\n"
    body = result.report()
    assert "beta series:** re-exported" in body
    assert "HWP stock:** re-exported" in body
    assert "Year window:" in body
    # Nothing moved, so no history line was written.
    assert not isolated_outputs["history"].exists()


def test_managed_outputs_cover_everything_the_refresh_writes():
    assert set(refreshmod.MANAGED_OUTPUTS) == {
        "sankey_data.csv", "carbon_data.csv", "manifest.json", "beta_series.csv",
        "beta_sensitivity.csv", "hwp_stock.csv", "forest_resource.csv", "economic_value.csv",
        "refresh_history.jsonl",
    }
    assert set(p.name for p in refreshmod.MANAGED_INPUTS) == {
        "harvest_by_fylke.csv", "exports_by_partner.csv", "prices_ssb.csv",
    }


# ---------------------------------------------------------------------------
# The PR body
# ---------------------------------------------------------------------------
def _result(diff: DiffReport, golden_updated: bool = True) -> RefreshResult:
    manifest = build_manifest(
        {"11181": {"updated": "2026-06-29T06:00:00Z", "rows": 38,
                   "year_min": 2007, "year_max": 2025, "query_sha256": "a" * 64}},
        {"sankey_data.csv": {"year_min": 2012, "year_max": 2024}},
    )
    return RefreshResult(
        sankey=COMMITTED,
        carbon=COMMITTED,
        manifest=manifest,
        diff=diff,
        balance=pd.DataFrame([{"node": "Sawmills", "rel_imbalance": 0.001}]),
        reconciliation=pd.DataFrame([{"metric": "m", "rel_diff": 0.002}]),
        control_totals=[{
            "name": "SSB 07410 domestic sawtimber 2012–2022",
            "pipeline": 61_400_000.0, "reference": 61_445_000.0,
            "rel_diff": -0.0007, "source": "SSB 07410", "tolerance": 0.01,
        }],
        golden_updated=golden_updated,
    )


def test_report_states_the_vintage_and_the_checks():
    body = _result(diff_datasets(COMMITTED, COMMITTED)).report()
    assert "Data as of:** 2026-06-29" in body
    assert "| 11181 |" in body                      # per-table provenance
    assert "Mass balance:** pass" in body
    assert "SSB 07410 domestic sawtimber" in body
    assert "Golden fixture:** updated to match" in body
    assert "Vintage diff: PASS" in body


def test_report_carries_the_failing_diff_into_the_pr_body():
    drifted = diff_datasets(COMMITTED, DRIFTED)
    assert not drifted.ok
    body = _result(drifted).report()
    assert "Vintage diff: FAIL" in body
    assert "Sawmills → Pulpmills" in body
