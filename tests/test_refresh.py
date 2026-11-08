"""Tests for the refresh orchestrator's safety net and its PR report.

The full chain needs network and is exercised by the scheduled workflow. What is
worth pinning here is the property that makes an unattended refresh safe: a run
that fails a gate must leave the working tree exactly as it found it, so there is
never a half-updated dataset sitting there waiting to be committed.
"""

from __future__ import annotations

import pandas as pd
import pytest

from circwood import config, pipeline as pipelinemod, refresh as refreshmod
from circwood.diff import DiffReport, diff_datasets
from circwood.ingest import ssb as ssbmod
from circwood.manifest import build_manifest
from circwood.refresh import RefreshResult
from circwood.transform import assemble as assemblemod

COMMITTED = pd.DataFrame({
    "source": ["Spruce", "Sawmills"],
    "target": ["Sawmills", "Pulpmills"],
    "volume": [1_000_000.0, 260_000.0],
    "year": [2020, 2020],
    "origin": ["committed", "committed"],
})

DRIFTED = COMMITTED.assign(volume=[1_000_000.0, 900_000.0])


@pytest.fixture
def isolated_outputs(tmp_path, monkeypatch):
    """Point every managed output at tmp_path so tests never touch the repo."""
    processed = tmp_path / "processed"
    processed.mkdir()
    sankey = processed / "sankey_data.csv"
    carbon = processed / "carbon_data.csv"
    golden = tmp_path / "golden_sankey_data.csv"
    beta = processed / "beta_series.csv"

    COMMITTED.to_csv(sankey, index=False)
    COMMITTED.to_csv(carbon, index=False)
    COMMITTED.to_csv(golden, index=False)
    beta.write_text("# committed beta\nyear\n2019\n", encoding="utf-8")

    monkeypatch.setattr(config, "PROCESSED_DIR", processed)
    monkeypatch.setattr(config, "SANKEY_CSV", sankey)
    monkeypatch.setattr(config, "CARBON_CSV", carbon)
    monkeypatch.setattr(config, "BETA_SERIES_CSV", beta)
    monkeypatch.setattr(refreshmod, "GOLDEN_FIXTURE", golden)
    # The stub frames are not a real flow graph, so the beta export is stubbed
    # here (it writes a marker); the failure path is pinned below.
    monkeypatch.setattr(
        refreshmod, "export_beta",
        lambda path: (path.write_text("# beta\nyear\n2020\n", encoding="utf-8"), path)[1],
    )
    return {"sankey": sankey, "carbon": carbon, "golden": golden, "beta": beta}


def test_the_manifest_is_written_inside_the_configured_output_dir(
    isolated_outputs, stub_ingest, monkeypatch, tmp_path
):
    """Guards the whole isolation story: nothing may escape to the real repo."""
    monkeypatch.setattr(assemblemod, "run_fresh_pipeline",
                        lambda **kw: (COMMITTED.to_csv(config.SANKEY_CSV, index=False), COMMITTED)[1])
    monkeypatch.setattr(pipelinemod, "build_carbon", lambda **kw: COMMITTED)
    monkeypatch.setattr(refreshmod, "_control_totals", lambda df: [])
    monkeypatch.setattr("circwood.validate.assert_window_balanced", lambda *a, **k: None)
    monkeypatch.setattr(
        "circwood.reconcile.treindustrien_reconciliation",
        lambda df, **kw: pd.DataFrame([{"metric": "m", "rel_diff": 0.0}]),
    )
    refreshmod.refresh()
    assert (config.PROCESSED_DIR / "manifest.json").exists()
    assert config.PROCESSED_DIR != tmp_path.parent  # sanity: really redirected


@pytest.fixture
def stub_ingest(monkeypatch):
    monkeypatch.setattr(
        ssbmod, "process_query_files",
        lambda *a, **k: ssbmod.IngestResult(datasets={}, vintages={}),
    )


def test_a_failed_gate_leaves_the_working_tree_untouched(
    isolated_outputs, stub_ingest, monkeypatch
):
    """The property the whole unattended loop rests on."""
    before = {name: path.read_bytes() for name, path in isolated_outputs.items()}

    def fake_rebuild(**kwargs):
        DRIFTED.to_csv(config.SANKEY_CSV, index=False)   # the rebuild lands ...
        return DRIFTED

    def explode(**kwargs):
        raise RuntimeError("carbon model blew up")       # ... then a gate fails

    monkeypatch.setattr(assemblemod, "run_fresh_pipeline", fake_rebuild)
    monkeypatch.setattr(pipelinemod, "build_carbon", explode)

    with pytest.raises(RuntimeError, match="carbon model blew up"):
        refreshmod.refresh()

    after = {name: path.read_bytes() for name, path in isolated_outputs.items()}
    assert after == before


def test_files_created_by_a_failed_run_are_removed(
    isolated_outputs, stub_ingest, monkeypatch
):
    """A baseline run that fails must not leave a stray dataset behind."""
    for path in isolated_outputs.values():
        path.unlink()

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


def test_accept_drift_adopts_a_dataset_the_gate_would_block(
    isolated_outputs, stub_ingest, monkeypatch
):
    """The escape hatch for a human who has reviewed a blocked refresh."""
    def fake_rebuild(**kwargs):
        DRIFTED.to_csv(config.SANKEY_CSV, index=False)
        return DRIFTED

    monkeypatch.setattr(assemblemod, "run_fresh_pipeline", fake_rebuild)
    monkeypatch.setattr(pipelinemod, "build_carbon", lambda **kw: DRIFTED)
    monkeypatch.setattr(refreshmod, "_control_totals", lambda df: [])
    monkeypatch.setattr("circwood.validate.assert_window_balanced", lambda *a, **k: None)
    monkeypatch.setattr(
        "circwood.reconcile.treindustrien_reconciliation",
        lambda df, **kw: pd.DataFrame([{"metric": "m", "rel_diff": 0.0}]),
    )

    with pytest.raises(Exception, match="beyond tolerance"):
        refreshmod.refresh()

    result = refreshmod.refresh(accept_drift=True)
    assert not result.diff.ok          # the drift is still reported ...
    assert result.golden_updated       # ... but the dataset was adopted
    assert "Vintage diff: FAIL" in result.report()


def _pass_the_data_gates(monkeypatch):
    monkeypatch.setattr(refreshmod, "_control_totals", lambda df: [])
    monkeypatch.setattr("circwood.validate.assert_window_balanced", lambda *a, **k: None)
    monkeypatch.setattr(
        "circwood.reconcile.treindustrien_reconciliation",
        lambda df, **kw: pd.DataFrame([{"metric": "m", "rel_diff": 0.0}]),
    )


def test_a_failed_beta_export_restores_every_output(
    isolated_outputs, stub_ingest, monkeypatch
):
    """Phase F: the beta export runs after the diff gate and is a gate itself."""
    before = {name: path.read_bytes() for name, path in isolated_outputs.items()}

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

    after = {name: path.read_bytes() for name, path in isolated_outputs.items()}
    assert after == before
    assert not (config.PROCESSED_DIR / "manifest.json").exists()


def test_a_successful_refresh_re_exports_beta(isolated_outputs, stub_ingest, monkeypatch):
    def fake_rebuild(**kwargs):
        COMMITTED.to_csv(config.SANKEY_CSV, index=False)
        return COMMITTED

    monkeypatch.setattr(assemblemod, "run_fresh_pipeline", fake_rebuild)
    monkeypatch.setattr(pipelinemod, "build_carbon", lambda **kw: COMMITTED)
    _pass_the_data_gates(monkeypatch)

    result = refreshmod.refresh()
    assert result.beta_series == config.PROCESSED_DIR / "beta_series.csv"
    assert isolated_outputs["beta"].read_text(encoding="utf-8") == "# beta\nyear\n2020\n"
    assert "beta series:** re-exported" in result.report()


def test_managed_outputs_cover_everything_the_refresh_writes():
    assert set(refreshmod.MANAGED_OUTPUTS) == {
        "sankey_data.csv", "carbon_data.csv", "manifest.json", "beta_series.csv",
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
