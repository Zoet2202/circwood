"""Tests for the vintage-diff gate (Phase B).

The gate exists to catch what mass balance cannot: a dataset built from a
reclassified SSB code is wrong *consistently*, so it balances and reconciles.
Only comparing against the previous vintage reveals it.
"""

from __future__ import annotations

import pandas as pd
import pytest

from circwood import diff as diffmod
from circwood.diff import (
    VintageDiffError,
    assert_within_threshold,
    diff_committed,
    diff_datasets,
)


def frame(rows) -> pd.DataFrame:
    """rows: (source, target, year, volume)."""
    return pd.DataFrame(
        [{"source": s, "target": t, "year": y, "volume": v, "origin": "test"}
         for s, t, y, v in rows]
    )


BASE = frame([
    ("Spruce", "Sawmills", 2020, 1_000_000.0),
    ("Spruce", "Sawmills", 2021, 1_100_000.0),
    ("Sawmills", "Pulpmills", 2020, 260_000.0),
    ("Sawmills", "Pulpmills", 2021, 286_000.0),
])


# ---------------------------------------------------------------------------
# The quiet cases
# ---------------------------------------------------------------------------
def test_identical_datasets_pass_with_no_changes():
    report = diff_datasets(BASE, BASE.copy())
    assert report.ok and not report.changes
    assert_within_threshold(report)  # does not raise


def test_a_small_revision_passes():
    """SSB revising a figure by 1% is routine, not an alarm."""
    revised = BASE.copy()
    revised.loc[0, "volume"] = 1_010_000.0
    report = diff_datasets(BASE, revised)
    assert len(report.changes) == 1
    assert report.ok
    assert not report.breaching_flows


def test_tiny_flows_do_not_trip_the_gate():
    """A 3 m3 edge doubling is +100% and means nothing at Mm3 scale."""
    old = frame([("A", "B", 2020, 3.0), ("Spruce", "Sawmills", 2020, 1_000_000.0)])
    new = frame([("A", "B", 2020, 6.0), ("Spruce", "Sawmills", 2020, 1_000_000.0)])
    report = diff_datasets(old, new)
    assert len(report.changes) == 1       # the move is still reported ...
    assert not report.breaching_flows     # ... but does not block
    assert report.ok


# ---------------------------------------------------------------------------
# The cases the gate exists for
# ---------------------------------------------------------------------------
def test_a_flow_past_the_threshold_blocks():
    reclassified = BASE.copy()
    reclassified.loc[2, "volume"] = 400_000.0  # 260k -> 400k, +54%
    report = diff_datasets(BASE, reclassified)
    assert not report.ok
    assert len(report.breaching_flows) == 1
    with pytest.raises(VintageDiffError, match="Sawmills -> Pulpmills"):
        assert_within_threshold(report)


def test_the_error_explains_why_mass_balance_would_not_catch_it():
    changed = BASE.copy()
    changed.loc[2, "volume"] = 400_000.0
    with pytest.raises(VintageDiffError, match="balances perfectly and is still wrong"):
        assert_within_threshold(diff_datasets(BASE, changed))


def test_a_new_edge_blocks():
    extended = pd.concat([BASE, frame([("Sawmills", "Energy", 2020, 50_000.0)])],
                         ignore_index=True)
    report = diff_datasets(BASE, extended)
    assert not report.ok
    assert ("Sawmills", "Energy") in report.added_edges
    with pytest.raises(VintageDiffError, match="new edge Sawmills -> Energy"):
        assert_within_threshold(report)


def test_a_disappearing_node_blocks():
    reduced = BASE[BASE["target"] != "Pulpmills"]
    report = diff_datasets(BASE, reduced)
    assert not report.ok
    assert "Pulpmills" in report.removed_nodes
    with pytest.raises(VintageDiffError, match="Pulpmills"):
        assert_within_threshold(report)


def test_a_disappearing_year_blocks():
    reduced = BASE[BASE["year"] != 2021]
    report = diff_datasets(BASE, reduced)
    assert report.removed_years == (2021,)
    assert not report.ok
    with pytest.raises(VintageDiffError, match="year 2021 disappeared"):
        assert_within_threshold(report)


def test_the_threshold_is_configurable():
    revised = BASE.copy()
    revised.loc[0, "volume"] = 1_030_000.0  # +3%
    assert diff_datasets(BASE, revised, threshold=0.05).ok
    assert not diff_datasets(BASE, revised, threshold=0.01).ok


# ---------------------------------------------------------------------------
# Time asymmetry — the thing that would otherwise make the gate useless
# ---------------------------------------------------------------------------
def test_a_newly_published_year_is_reported_but_never_blocks():
    """Otherwise every refresh that picks up a new year fails by construction."""
    extended = pd.concat([BASE, frame([
        ("Spruce", "Sawmills", 2022, 1_200_000.0),
        ("Sawmills", "Pulpmills", 2022, 312_000.0),
    ])], ignore_index=True)
    report = diff_datasets(BASE, extended)
    assert report.added_years == (2022,)
    assert report.common_years == (2020, 2021)
    assert not report.changes
    assert report.ok


def test_a_new_year_does_not_register_as_a_new_edge():
    """Structural comparison happens inside the overlap, not across it."""
    extended = pd.concat([BASE, frame([("Boardmills", "Energy", 2022, 90_000.0)])],
                         ignore_index=True)
    report = diff_datasets(BASE, extended)
    assert report.added_edges == ()
    assert report.ok


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------
def test_no_previous_dataset_is_a_baseline_not_a_failure():
    report = diff_datasets(None, BASE)
    assert report.baseline and report.ok
    assert "baseline" in report.summary().lower()
    assert_within_threshold(report)


def test_diff_committed_treats_a_missing_file_as_baseline(tmp_path):
    report = diff_committed(BASE, tmp_path / "gone.csv")
    assert report.baseline


def test_diff_committed_reads_the_committed_dataset(tmp_path):
    path = tmp_path / "sankey.csv"
    BASE.to_csv(path, index=False)
    changed = BASE.copy()
    changed.loc[2, "volume"] = 400_000.0
    report = diff_committed(changed, path)
    assert not report.baseline and not report.ok


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def test_summary_is_markdown_naming_the_moved_flow():
    changed = BASE.copy()
    changed.loc[2, "volume"] = 400_000.0
    summary = diff_datasets(BASE, changed).summary()
    assert "FAIL" in summary
    assert "Sawmills → Pulpmills" in summary
    assert "|---|" in summary  # a markdown table, for the PR body


def test_summary_of_a_clean_diff_says_so():
    assert "No flow moved beyond the threshold" in diff_datasets(BASE, BASE).summary()


def test_to_frame_is_sorted_by_absolute_move():
    changed = BASE.copy()
    changed.loc[0, "volume"] = 1_050_000.0   # +50,000
    changed.loc[2, "volume"] = 250_000.0     # -10,000
    df = diff_datasets(BASE, changed).to_frame()
    assert list(df["delta"].abs()) == sorted(df["delta"].abs(), reverse=True)


def test_defaults_match_the_documented_gate():
    assert diffmod.DEFAULT_THRESHOLD == 0.05
    assert diffmod.DEFAULT_MIN_VOLUME == 1_000.0
