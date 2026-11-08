"""Phase J — the derived year window and the publication-lag policy."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from circwood import constants as C
from circwood.coverage import (
    OK,
    OVERDUE,
    PUBLICATION_GRACE_MONTHS,
    UNGATED,
    WindowError,
    added_tables,
    assert_window,
    changed_inputs,
    changed_queries,
    coverage_report,
    expected_year_max,
    revised_tables,
)
from circwood.ingest.ssb import TableContract, contract_for


def vintage(table_id, year_min, year_max, updated="2026-05-21T06:00:00Z", sha="a" * 64):
    return {"table_id": table_id, "query_file": f"{table_id}.json", "query_sha256": sha,
            "updated": updated, "fetched_at": "2026-11-08T00:00:00+00:00", "rows": 1,
            "year_min": year_min, "year_max": year_max}


# ---------------------------------------------------------------------------
# Publication lag arithmetic
# ---------------------------------------------------------------------------
def test_expected_year_max_counts_lag_plus_grace_from_the_year_end():
    nov_2026 = date(2026, 11, 8)
    # 10513 (waste): 16 months lag + 6 grace = 22 months -> 2024 is due, 2025 is not.
    assert expected_year_max(16, nov_2026) == 2024
    # roundwood (6 months): 2025 would be due in Jan 2027 at the earliest.
    assert expected_year_max(6, nov_2026) == 2024
    assert expected_year_max(6, date(2027, 1, 15)) == 2025
    assert expected_year_max(6, date(2026, 12, 31)) == 2024
    assert expected_year_max(None, nov_2026) is None
    assert PUBLICATION_GRACE_MONTHS == 6


def test_every_shipped_contract_has_a_lag():
    for tid in ("03795", "03795_fylke", "07410", "08801", "10513", "11181", "08799_2017_2021",
                "14594", "06289", "07413"):
        assert contract_for(tid).publication_lag_months is not None, tid
    assert contract_for("10513").publication_lag_months > contract_for("07410").publication_lag_months


# ---------------------------------------------------------------------------
# The window report
# ---------------------------------------------------------------------------
def test_window_is_derived_from_gated_tables_pooled_per_base_table():
    vintages = {
        "03795": vintage("03795", 1996, 2025),
        "03795_fylke": vintage("03795_fylke", 1996, 2025),
        "07410": vintage("07410", 2012, 2025),
        "08801": vintage("08801", 1988, 2025),
        "08799_2012_2016": vintage("08799_2012_2016", 2012, 2016),
        "08799_2017_2021": vintage("08799_2017_2021", 2017, 2021),
        "08799_2022_2026": vintage("08799_2022_2026", 2022, 2025),
        "10513": vintage("10513", 2012, 2024, updated="2026-03-24T07:00:00Z"),
        "11181": vintage("11181", 2007, 2025),
        "14594": vintage("14594", 2020, 2025),  # side layer, never gated
    }
    report = coverage_report(vintages, as_of="2026-11-08")
    assert (report.derived_min, report.derived_max) == (2012, 2024)
    assert report.ok and report.proposal is None and not report.lost_coverage
    states = {t.source: t.status for t in report.tables}
    assert states["10513"] == OK           # 2024 is exactly what is due
    assert states["14594"] == UNGATED
    assert "Year window: OK" in report.summary()
    assert_window(report)


def test_new_coverage_is_proposed_not_imposed():
    vintages = {
        "07410": vintage("07410", 2012, 2025), "10513": vintage("10513", 2012, 2025),
        "11181": vintage("11181", 2007, 2025), "03795": vintage("03795", 1996, 2025),
        "08801": vintage("08801", 1988, 2025),
    }
    report = coverage_report(vintages, as_of="2026-11-08")
    assert report.derived_max == 2025 > C.DATASET_MAX_YEAR
    assert report.ok                              # never blocks
    assert "widen DATASET_MAX_YEAR" in report.proposal
    assert "Proposal" in report.summary()


def test_lost_coverage_blocks_and_names_the_table():
    vintages = {
        "07410": vintage("07410", 2012, 2025), "10513": vintage("10513", 2012, 2023),
        "11181": vintage("11181", 2007, 2025),
    }
    report = coverage_report(vintages, as_of="2026-11-08")
    assert not report.ok
    assert any("10513 ends 2023" in r for r in report.lost_coverage)
    with pytest.raises(WindowError, match="10513 ends 2023"):
        assert_window(report)
    # Lost coverage at the front, too.
    late_start = {"07410": vintage("07410", 2015, 2025), "11181": vintage("11181", 2007, 2025)}
    report = coverage_report(late_start, as_of="2026-11-08")
    assert any("start at 2015" in r for r in report.lost_coverage)


def test_overdue_is_an_alert_not_a_block():
    # 11181 (7 months lag): by Nov 2026 the year 2024 is due; 2023 is overdue.
    vintages = {"11181": vintage("11181", 2007, 2023), "07410": vintage("07410", 2012, 2025),
                "10513": vintage("10513", 2012, 2024)}
    report = coverage_report(vintages, as_of="2026-11-08")
    by = {t.source: t for t in report.tables}
    assert by["11181"].status == OVERDUE and by["11181"].expected_year_max == 2024
    assert by["07410"].status == OK
    assert "Overdue" in report.summary()
    assert not report.ok  # the window is lost as well in this example (11181 ends 2023)


def test_treindustrien_is_reported_as_an_input_never_gated():
    tre = pd.DataFrame({"year": [2011, 2023], "Category": ["x", "x"], "Volume": [1.0, 1.0]})
    vintages = {"07410": vintage("07410", 2012, 2025), "10513": vintage("10513", 2012, 2024),
                "11181": vintage("11181", 2007, 2025)}
    report = coverage_report(vintages, treindustrien_df=tre, as_of="2026-11-08")
    assert report.ok                       # the owner's decision: 2024 stays despite 2023
    assert len(report.inputs) == 1
    tre_row = report.inputs[0]
    assert tre_row.year_max == 2023 and not tre_row.window_gated
    assert tre_row.status == OVERDUE       # 2024 is nine months + grace overdue by Nov 2026
    assert "Treindustrien" in report.summary()


def test_contracts_can_be_injected():
    vintages = {"X": vintage("X", 2012, 2024)}
    gated = coverage_report(vintages, as_of="2026-11-08",
                            contracts={"X": TableContract(publication_lag_months=3)})
    assert gated.derived_max == 2024
    side = coverage_report(vintages, as_of="2026-11-08",
                           contracts={"X": TableContract(window_gated=False)})
    assert side.derived_max is None and side.ok


# ---------------------------------------------------------------------------
# Manifest comparisons for drift attribution
# ---------------------------------------------------------------------------
def test_revised_added_and_changed_helpers():
    prev = {"07410": vintage("07410", 2012, 2024, updated="2026-01-01T00:00:00Z"),
            "10513": vintage("10513", 2012, 2024)}
    cur = {"07410": vintage("07410", 2012, 2025, updated="2026-05-21T06:00:00Z"),
           "10513": vintage("10513", 2012, 2024, sha="b" * 64),
           "08799_2012_2016": vintage("08799_2012_2016", 2012, 2016)}
    assert revised_tables(prev, cur) == {"07410"}          # a new table is not a revision
    assert added_tables(prev, cur) == {"08799"}
    assert changed_queries(prev, cur) == {"10513"}
    assert revised_tables(None, cur) == set()
    assert changed_inputs({"a.csv": {"sha256": "1"}}, {"a.csv": {"sha256": "2"}, "b.csv": {"sha256": "3"}}) == {
        "a.csv", "b.csv"}
    assert changed_inputs({"a.csv": {"sha256": "1"}}, {"a.csv": {"sha256": "1"}}) == set()
