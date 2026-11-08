"""Year-window coverage and publication-lag policy — ROADMAP Phase J.

The dataset is clipped to ``constants.DATASET_MIN_YEAR``/``DATASET_MAX_YEAR``,
"the years every SSB source table covers". Until Phase J those were two
integers a human had to remember to bump. This module *derives* the window
from what each fetched table actually covers and compares it with the
declared one, and it knows how long each table normally takes to publish a
year, so the refresh can tell three states apart:

* **expected** — a table has not published the new year yet, and it is not
  due (10513 waste trails roundwood by over a year; that is normal);
* **overdue** — a table has not published a year that, by its usual lag plus a
  grace period, should be out (an alert: a series may have stopped);
* **proposal** — every window-gated table now covers a year beyond
  ``DATASET_MAX_YEAR``: widening the window is a reviewed one-line diff the
  pipeline proposes in the refresh report.

Only *lost* coverage blocks a refresh (a table that no longer covers the
declared window would make the clipped dataset silently partial — the defect
the window exists to prevent). New coverage never changes the shipped dataset
by itself: the clip uses the declared constants, so widening stays a human
decision, and this module makes sure it is a visible one.

The owner decided (2026-11-08) that the Treindustrien series ending at 2023
does not narrow the 2012–2024 window; its coverage is therefore *reported*
here as an input, with its own lag, not gated.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from circwood import constants as C
from circwood.ingest.ssb import TableContract, base_table, contract_for

#: Months a table may run late past its usual lag before it is called overdue.
#: A policy value, not a model coefficient — which is why it lives here.
PUBLICATION_GRACE_MONTHS = 6

#: Expected publication lag of the Treindustrien annual report (months after
#: year end), for the input coverage row. The report is a file in this repo,
#: so "overdue" means someone has to update data/raw/treindustrien_production_data.csv.
TREINDUSTRIEN_LAG_MONTHS = 9

OK = "ok"
OVERDUE = "overdue"
UNGATED = "side layer"


class WindowError(AssertionError):
    """A source table no longer covers the declared dataset window."""


def expected_year_max(lag_months: int | None, as_of: date, grace_months: int = PUBLICATION_GRACE_MONTHS) -> int | None:
    """The latest year a table should have published by ``as_of``.

    Year ``t`` is due once ``as_of`` is at least ``lag_months + grace_months``
    past the end of ``t``. ``None`` when no lag is known for the table.
    """
    if lag_months is None:
        return None
    months = as_of.year * 12 + (as_of.month - 1) - (int(lag_months) + int(grace_months))
    shifted_year = months // 12
    # Year t is due when as_of - lag - grace >= 1 Jan (t + 1), i.e. t <= shifted_year - 1.
    return shifted_year - 1


@dataclass(frozen=True)
class SourceCoverage:
    """What one source covers and whether that is where it should be."""

    source: str
    base: str
    year_min: int | None
    year_max: int | None
    updated: str | None
    lag_months: int | None
    expected_year_max: int | None
    status: str
    window_gated: bool

    def describe(self) -> str:
        span = f"{self.year_min}–{self.year_max}" if self.year_min is not None else "no years"
        exp = f"expected >= {self.expected_year_max}" if self.expected_year_max is not None else "no lag on record"
        return f"{self.source}: {span} ({exp}) — {self.status}"


@dataclass
class WindowReport:
    """The declared window against the derived one, with every source's state."""

    as_of: str
    declared_min: int = C.DATASET_MIN_YEAR
    declared_max: int = C.DATASET_MAX_YEAR
    derived_min: int | None = None
    derived_max: int | None = None
    tables: list[SourceCoverage] = field(default_factory=list)
    inputs: list[SourceCoverage] = field(default_factory=list)
    #: ``"table_id: error"`` for tables an ``allow_partial`` ingest dropped. The
    #: window is derived from the tables that *survived*, so it is not a
    #: verified window while this is non-empty; the summary says so.
    failed_tables: list[str] = field(default_factory=list)

    @property
    def lost_coverage(self) -> list[str]:
        """Reasons the declared window is no longer covered (blocking)."""
        reasons = []
        if self.derived_min is not None and self.derived_min > self.declared_min:
            reasons.append(
                f"the gated tables now start at {self.derived_min}, after "
                f"DATASET_MIN_YEAR {self.declared_min}"
            )
        if self.derived_max is not None and self.derived_max < self.declared_max:
            short = [t for t in self.tables if t.window_gated and t.year_max is not None
                     and t.year_max < self.declared_max]
            names = ", ".join(f"{t.source} ends {t.year_max}" for t in short) or "unknown table"
            reasons.append(
                f"the gated tables now end at {self.derived_max}, before "
                f"DATASET_MAX_YEAR {self.declared_max} ({names})"
            )
        return reasons

    @property
    def proposal(self) -> str | None:
        """A widening the pipeline proposes, or ``None``."""
        if self.derived_max is not None and self.derived_max > self.declared_max:
            return (
                f"Every window-gated SSB table now covers {self.derived_max}: widen "
                f"DATASET_MAX_YEAR from {self.declared_max} to {self.derived_max} in "
                f"src/circwood/constants.py (one-line diff, reviewed), after checking "
                f"that data/raw/treindustrien_production_data.csv reaches it too."
            )
        return None

    @property
    def overdue(self) -> list[SourceCoverage]:
        return [t for t in self.tables + self.inputs if t.status == OVERDUE]

    @property
    def ok(self) -> bool:
        return not self.lost_coverage

    def summary(self) -> str:
        """Markdown for the refresh report."""
        state = "OK" if self.ok else "COVERAGE LOST"
        if self.failed_tables and self.ok:
            state = f"OK FOR THE TABLES FETCHED — {len(self.failed_tables)} not fetched, window unverified"
        lines = [
            f"**Year window: {state}** — declared "
            f"{self.declared_min}–{self.declared_max}, derived from the gated tables "
            f"{self.derived_min}–{self.derived_max} (as of {self.as_of})",
            "",
            "| source | years | SSB updated | usual lag | expected >= | state |",
            "|---|---|---|---|---|---|",
        ]
        for t in self.tables + self.inputs:
            span = f"{t.year_min}–{t.year_max}" if t.year_min is not None else "—"
            lines.append(
                f"| {t.source} | {span} | {str(t.updated or '—')[:10]} | "
                f"{t.lag_months if t.lag_months is not None else '—'} mo | "
                f"{t.expected_year_max if t.expected_year_max is not None else '—'} | {t.status} |"
            )
        for reason in self.lost_coverage:
            lines.append(f"- **Blocking:** {reason}")
        for failed in self.failed_tables:
            lines.append(f"- **Not fetched (allow_partial):** {failed} — its coverage is unknown and "
                         "the dataset built from the rest is partial.")
        if self.proposal:
            lines.append(f"- **Proposal:** {self.proposal}")
        for t in self.overdue:
            lines.append(
                f"- **Overdue:** {t.source} ends {t.year_max} but should have published "
                f"{t.expected_year_max} by now (usual lag {t.lag_months} months plus "
                f"{PUBLICATION_GRACE_MONTHS} months grace)."
            )
        return "\n".join(lines)


def _status(year_max: int | None, expected: int | None, gated: bool) -> str:
    if year_max is not None and expected is not None and year_max < expected:
        return OVERDUE
    return OK if gated else UNGATED


def _as_of(as_of: date | datetime | str | None) -> date:
    if as_of is None:
        return datetime.now(timezone.utc).date()
    if isinstance(as_of, datetime):
        return as_of.date()
    if isinstance(as_of, date):
        return as_of
    return date.fromisoformat(str(as_of)[:10])


def coverage_report(
    vintages: Mapping[str, Any],
    *,
    treindustrien_df: pd.DataFrame | None = None,
    as_of: date | datetime | str | None = None,
    contracts: Mapping[str, TableContract] | None = None,
    failures: Mapping[str, str] | None = None,
) -> WindowReport:
    """Derive the window from ``IngestResult.vintages`` and judge every source.

    ``failures`` is ``IngestResult.failures``: tables an ``allow_partial`` run
    could not fetch. They have no vintage, so they cannot shape the derived
    window; they are listed so the report never says "OK" about a window
    derived from fewer tables than the pipeline needs.

    ``vintages`` holds ``TableVintage`` objects or their dicts (as the manifest
    stores them). Chunked ids (``08799_2012_2016``) and alternate selections
    (``03795_fylke``) are pooled per base table before the intersection, so a
    table split across query files counts once. ``treindustrien_df`` adds the
    Treindustrien input row (reported, never gated).
    """
    today = _as_of(as_of)
    rows: list[SourceCoverage] = []
    by_base: dict[str, dict[str, Any]] = {}

    for table_id, v in sorted(vintages.items()):
        d = v.to_dict() if hasattr(v, "to_dict") else dict(v)
        contract = (contracts or {}).get(table_id) or contract_for(table_id)
        y0, y1 = d.get("year_min"), d.get("year_max")
        expected = expected_year_max(contract.publication_lag_months, today)
        if expected is not None and contract.selection_max_year is not None:
            # A chunked query (08799_2012_2016) is bounded by its own time
            # selection; it can never publish beyond it, so it is judged
            # against that bound, not the calendar.
            expected = min(expected, contract.selection_max_year)
        rows.append(SourceCoverage(
            source=table_id, base=base_table(table_id),
            year_min=None if y0 is None else int(y0),
            year_max=None if y1 is None else int(y1),
            updated=d.get("updated"), lag_months=contract.publication_lag_months,
            expected_year_max=expected,
            status=_status(None if y1 is None else int(y1), expected, contract.window_gated),
            window_gated=contract.window_gated,
        ))
        if contract.window_gated and y0 is not None and y1 is not None:
            b = by_base.setdefault(base_table(table_id), {"min": int(y0), "max": int(y1)})
            b["min"] = min(b["min"], int(y0))
            b["max"] = max(b["max"], int(y1))

    derived_min = max(b["min"] for b in by_base.values()) if by_base else None
    derived_max = min(b["max"] for b in by_base.values()) if by_base else None

    inputs: list[SourceCoverage] = []
    if treindustrien_df is not None and len(treindustrien_df):
        years = pd.to_numeric(treindustrien_df["year"], errors="coerce").dropna()
        y0, y1 = int(years.min()), int(years.max())
        expected = expected_year_max(TREINDUSTRIEN_LAG_MONTHS, today)
        inputs.append(SourceCoverage(
            source="Treindustrien årsrapport (data/raw/treindustrien_production_data.csv)",
            base="treindustrien", year_min=y0, year_max=y1, updated=None,
            lag_months=TREINDUSTRIEN_LAG_MONTHS, expected_year_max=expected,
            status=_status(y1, expected, False) if y1 < (expected or y1) else UNGATED,
            window_gated=False,
        ))

    return WindowReport(
        as_of=today.isoformat(), derived_min=derived_min, derived_max=derived_max,
        tables=rows, inputs=inputs,
        failed_tables=[f"{t}: {err}" for t, err in sorted((failures or {}).items())],
    )


def assert_window(report: WindowReport) -> None:
    """Raise :class:`WindowError` when a gated table no longer covers the declared window."""
    if report.ok:
        return
    raise WindowError(
        "The declared dataset window is no longer covered by every source table:\n"
        + "\n".join(f"  - {r}" for r in report.lost_coverage)
        + "\nA clipped dataset built now would be partial for the uncovered years. "
        "Either the table lost coverage upstream (check SSB) or DATASET_MIN_YEAR/"
        "DATASET_MAX_YEAR must be narrowed deliberately."
    )


def revised_tables(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> set[str]:
    """Base tables present in both manifests whose SSB ``updated`` date differs.

    A table that is *new* to the manifest is not a revision (see
    :func:`added_tables`): it cannot explain a move in an edge that existed
    before it did, so counting it would let an unrelated change through. With
    no previous manifest nothing is known, and nothing is revised.
    """
    if not previous:
        return set()
    out = set()
    for table_id, cur in current.items():
        c = _as_dict(cur)
        p = _as_dict(previous.get(table_id))
        if p is not None and c is not None and p.get("updated") != c.get("updated"):
            out.add(base_table(table_id))
    return out


def _as_dict(entry: Any) -> dict[str, Any] | None:
    """A manifest table entry (``TableVintage`` or dict) as a dict; ``None`` stays ``None``."""
    if entry is None:
        return None
    return entry.to_dict() if hasattr(entry, "to_dict") else dict(entry)


def added_tables(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> set[str]:
    """Base tables in the current manifest that the previous one did not have."""
    prev = {base_table(t) for t in (previous or {})}
    return {base_table(t) for t in current} - prev


def changed_queries(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> set[str]:
    """Base tables whose query-file hash differs between two manifests' ``tables``."""
    if not previous:
        return set()
    out = set()
    for table_id, cur in current.items():
        c = _as_dict(cur)
        p = _as_dict(previous.get(table_id))
        if p is not None and c is not None and p.get("query_sha256") != c.get("query_sha256"):
            out.add(base_table(table_id))
    return out


def changed_inputs(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> set[str]:
    """Input files whose hash differs between two manifests' ``inputs``.

    With no previous record (no manifest, or one written before inputs were
    recorded) nothing is *known* to have changed, so nothing is excused — the
    same rule as :func:`revised_tables`. An input the previous manifest
    recorded and the current one lacks, or vice versa, counts as changed.
    """
    if not previous:
        return set()
    out = set()
    for name, cur in (current or {}).items():
        prev = previous.get(name)
        if prev is None or prev.get("sha256") != cur.get("sha256"):
            out.add(name)
    return out


def tables_in(ids: Iterable[str]) -> set[str]:
    """Base tables of a collection of query ids."""
    return {base_table(i) for i in ids}


__all__ = [
    "OK",
    "OVERDUE",
    "PUBLICATION_GRACE_MONTHS",
    "TREINDUSTRIEN_LAG_MONTHS",
    "UNGATED",
    "SourceCoverage",
    "WindowError",
    "WindowReport",
    "added_tables",
    "assert_window",
    "changed_inputs",
    "changed_queries",
    "coverage_report",
    "expected_year_max",
    "revised_tables",
    "tables_in",
]
