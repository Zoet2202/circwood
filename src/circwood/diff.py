"""Vintage diff — compare a regenerated dataset against the committed one.

The check that catches SSB reclassifying a code. Mass balance will not: a
dataset built from the wrong code is wrong *consistently*, so it balances
perfectly and reconciles to whatever total it was built from. Only comparing
against the previous vintage shows that a flow moved.

What counts as alarming
-----------------------
The diff is deliberately asymmetric about time. A refresh that picks up a newly
published year adds a row for every edge in that year — expected, and not
evidence of anything. So:

* Flow comparison happens **only on years present in both** datasets. New years
  are reported (``added_years``) and never block.
* A year that *disappears* blocks. Losing coverage is always a defect.
* Structural changes — a node or an edge present in one dataset's overlapping
  years and not the other — block.
* A flow that moves by more than ``threshold`` blocks, provided either side
  clears ``min_volume``. Without that floor the gate is noise: a 3 m³ rounding
  edge doubling is a 100 % move and means nothing at Mm³ scale.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from circwood import constants as C

#: Relative move that trips the gate.
DEFAULT_THRESHOLD = 0.05

#: Tighter move that trips the gate *inside the paper window* (Phase J). The
#: 2012–2022 figures are published; SSB rarely revises final years, so a move
#: there is more suspicious than one in the still-provisional recent years.
#: ``None`` disables the distinction.
DEFAULT_PAPER_THRESHOLD = 0.02

#: Flows smaller than this (m³, on both sides) are too small for a relative
#: change to be meaningful. ~0.01 % of a typical annual harvest.
DEFAULT_MIN_VOLUME = 1_000.0

KEY = ["source", "target", "year"]


class VintageDiffError(AssertionError):
    """A regenerated dataset differs from the committed one beyond tolerance."""


class UnexplainedDriftError(VintageDiffError):
    """Flows moved although no source table was revised, no query and no input file changed.

    The more serious class (Phase J): the change came from *this repository's*
    code or configuration, not from Statistics Norway, and a dataset built
    from a changed transform balances and reconciles just as well as the old
    one — only the vintage diff sees it.
    """


@dataclass(frozen=True)
class FlowChange:
    source: str
    target: str
    year: int
    old: float
    new: float
    kind: str  # "moved" | "added" | "removed"

    @property
    def delta(self) -> float:
        return self.new - self.old

    @property
    def rel(self) -> float | None:
        """Relative move against the old value; ``None`` for a new edge."""
        if self.old == 0:
            return None
        return self.new / self.old - 1.0

    @property
    def magnitude(self) -> float:
        return max(abs(self.old), abs(self.new))

    def describe(self) -> str:
        rel = f"{self.rel:+.1%}" if self.rel is not None else "new"
        return (
            f"{self.source} -> {self.target} ({self.year}): "
            f"{self.old:,.0f} -> {self.new:,.0f} m3 ({rel})"
        )


@dataclass
class DiffReport:
    """The outcome of comparing two vintages of the dataset."""

    threshold: float = DEFAULT_THRESHOLD
    min_volume: float = DEFAULT_MIN_VOLUME
    paper_threshold: float | None = DEFAULT_PAPER_THRESHOLD
    paper_window: tuple[int, int] = (C.MIN_YEAR, C.MAX_YEAR)
    baseline: bool = False  # no previous dataset to compare against
    common_years: tuple[int, ...] = ()
    added_years: tuple[int, ...] = ()
    removed_years: tuple[int, ...] = ()
    added_nodes: tuple[str, ...] = ()
    removed_nodes: tuple[str, ...] = ()
    added_edges: tuple[tuple[str, str], ...] = ()
    removed_edges: tuple[tuple[str, str], ...] = ()
    changes: list[FlowChange] = field(default_factory=list)

    # -- verdict ------------------------------------------------------------
    def threshold_for(self, year: int) -> float:
        """The relative threshold that applies to ``year`` (tighter inside the paper window)."""
        lo, hi = self.paper_window
        if self.paper_threshold is not None and lo <= int(year) <= hi:
            return min(self.paper_threshold, self.threshold)
        return self.threshold

    @property
    def breaching_flows(self) -> list[FlowChange]:
        """Flow moves past the (year-dependent) threshold that are big enough to matter."""
        out = [
            c
            for c in self.changes
            if c.magnitude >= self.min_volume
            and (c.rel is None or abs(c.rel) > self.threshold_for(c.year))
        ]
        return sorted(out, key=lambda c: abs(c.delta), reverse=True)

    @property
    def structural_changes(self) -> list[str]:
        reasons = []
        for year in self.removed_years:
            reasons.append(f"year {year} disappeared from the dataset")
        for node in self.added_nodes:
            reasons.append(f"new node {node!r}")
        for node in self.removed_nodes:
            reasons.append(f"node {node!r} disappeared")
        for s, t in self.added_edges:
            reasons.append(f"new edge {s} -> {t}")
        for s, t in self.removed_edges:
            reasons.append(f"edge {s} -> {t} disappeared")
        return reasons

    @property
    def ok(self) -> bool:
        if self.baseline:
            return True
        return not self.structural_changes and not self.breaching_flows

    # -- presentation -------------------------------------------------------
    def to_frame(self) -> pd.DataFrame:
        """Every detected flow change, largest absolute move first."""
        rows = [
            {
                "source": c.source, "target": c.target, "year": c.year,
                "old": c.old, "new": c.new, "delta": c.delta,
                "rel": c.rel, "kind": c.kind,
            }
            for c in self.changes
        ]
        df = pd.DataFrame(rows, columns=["source", "target", "year", "old", "new",
                                         "delta", "rel", "kind"])
        if df.empty:
            return df
        return df.reindex(df["delta"].abs().sort_values(ascending=False).index)

    def summary(self, limit: int = 15) -> str:
        """A markdown report — goes straight into the refresh PR body."""
        if self.baseline:
            return (
                "**Vintage diff: baseline.** No committed dataset to compare "
                "against, so nothing was gated. The dataset generated by this "
                "run becomes the baseline for the next refresh."
            )

        lines: list[str] = []
        verdict = "PASS" if self.ok else "FAIL"
        lines.append(f"**Vintage diff: {verdict}**")
        lines.append("")
        span = (
            f"{min(self.common_years)}–{max(self.common_years)}"
            if self.common_years else "none"
        )
        paper = (
            f", {min(self.paper_threshold, self.threshold):.0%} inside the paper window "
            f"{self.paper_window[0]}–{self.paper_window[1]}"
            if self.paper_threshold is not None else ""
        )
        lines.append(
            f"- Compared years: {span} "
            f"(threshold {self.threshold:.0%}{paper}, floor {self.min_volume:,.0f} m³)"
        )
        if self.added_years:
            lines.append(
                f"- New years, not gated: {', '.join(str(y) for y in self.added_years)}"
            )
        lines.append(f"- Flows changed: {len(self.changes)}")
        lines.append(f"- Flows past threshold: {len(self.breaching_flows)}")

        if self.structural_changes:
            lines.append("")
            lines.append("**Structural changes (blocking):**")
            lines.extend(f"- {reason}" for reason in self.structural_changes)

        breaching = self.breaching_flows
        if breaching:
            lines.append("")
            lines.append(f"**Largest moves past threshold** (top {min(limit, len(breaching))}):")
            lines.append("")
            lines.append("| flow | year | old (m³) | new (m³) | change |")
            lines.append("|---|---|---|---|---|")
            for c in breaching[:limit]:
                rel = f"{c.rel:+.1%}" if c.rel is not None else "new"
                lines.append(
                    f"| {c.source} → {c.target} | {c.year} | "
                    f"{c.old:,.0f} | {c.new:,.0f} | {rel} |"
                )
        elif not self.structural_changes:
            lines.append("")
            lines.append("No flow moved beyond the threshold.")
        return "\n".join(lines)


def _keyed(df: pd.DataFrame) -> pd.Series:
    """Volume per (source, target, year), summed and indexed by the key."""
    return (
        df.groupby(KEY, as_index=True)["volume"].sum().astype(float)
    )


def diff_datasets(
    old: pd.DataFrame | None,
    new: pd.DataFrame,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    min_volume: float = DEFAULT_MIN_VOLUME,
    paper_threshold: float | None = DEFAULT_PAPER_THRESHOLD,
) -> DiffReport:
    """Compare two vintages of the flow dataset.

    ``old`` may be ``None`` (or empty) — the first build has nothing to compare
    against, which is reported as a baseline rather than treated as a pass or a
    failure. ``paper_threshold`` is the tighter threshold applied to years
    inside the paper window (``None`` to use ``threshold`` everywhere).
    """
    if old is None or old.empty:
        return DiffReport(threshold=threshold, min_volume=min_volume,
                          paper_threshold=paper_threshold, baseline=True)

    old_years = set(old["year"].astype(int))
    new_years = set(new["year"].astype(int))
    common = sorted(old_years & new_years)

    report = DiffReport(
        threshold=threshold,
        min_volume=min_volume,
        paper_threshold=paper_threshold,
        common_years=tuple(common),
        added_years=tuple(sorted(new_years - old_years)),
        removed_years=tuple(sorted(old_years - new_years)),
    )
    if not common:
        return report

    old_w = old[old["year"].astype(int).isin(common)]
    new_w = new[new["year"].astype(int).isin(common)]

    report.added_nodes = tuple(sorted(_nodes(new_w) - _nodes(old_w)))
    report.removed_nodes = tuple(sorted(_nodes(old_w) - _nodes(new_w)))
    report.added_edges = tuple(sorted(_edges(new_w) - _edges(old_w)))
    report.removed_edges = tuple(sorted(_edges(old_w) - _edges(new_w)))

    old_v, new_v = _keyed(old_w), _keyed(new_w)
    joined = pd.concat([old_v.rename("old"), new_v.rename("new")], axis=1).fillna(0.0)
    moved = joined[joined["old"] != joined["new"]]

    for (source, target, year), row in moved.iterrows():
        old_val, new_val = float(row["old"]), float(row["new"])
        kind = "added" if old_val == 0 else "removed" if new_val == 0 else "moved"
        report.changes.append(
            FlowChange(str(source), str(target), int(year), old_val, new_val, kind)
        )
    report.changes.sort(key=lambda c: abs(c.delta), reverse=True)
    return report


def _nodes(df: pd.DataFrame) -> set[str]:
    return set(df["source"]) | set(df["target"])


def _edges(df: pd.DataFrame) -> set[tuple[str, str]]:
    return set(zip(df["source"], df["target"]))


def assert_within_threshold(report: DiffReport) -> None:
    """Raise :class:`VintageDiffError` unless the diff is acceptable.

    This is the blocking gate: it runs *before* anything is committed, so a
    reclassified SSB code produces a failed job rather than a quietly wrong
    dataset in the repository.
    """
    if report.ok:
        return
    reasons = list(report.structural_changes)
    reasons += [c.describe() for c in report.breaching_flows[:20]]
    extra = len(report.breaching_flows) - 20
    if extra > 0:
        reasons.append(f"... and {extra} more flows past the threshold")
    detail = "\n".join(f"  - {r}" for r in reasons)
    raise VintageDiffError(
        f"Regenerated dataset differs from the committed one beyond tolerance "
        f"(threshold {report.threshold:.0%}, floor {report.min_volume:,.0f} m³):\n"
        f"{detail}\n"
        f"Mass balance would not catch this: a dataset built from a reclassified "
        f"SSB code balances perfectly and is still wrong. Review the change, then "
        f"either accept it deliberately or fix the query."
    )


def diff_committed(
    new: pd.DataFrame,
    committed_path: str | Path | None = None,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    min_volume: float = DEFAULT_MIN_VOLUME,
    paper_threshold: float | None = DEFAULT_PAPER_THRESHOLD,
) -> DiffReport:
    """Diff ``new`` against the dataset committed at ``committed_path``.

    A missing file is the baseline case, not an error — that is what makes
    "delete ``data/processed/*.csv`` and re-run" a supported way to rebuild.
    """
    from circwood import config

    path = Path(committed_path) if committed_path is not None else config.SANKEY_CSV
    old = pd.read_csv(path) if path.exists() else None
    return diff_datasets(old, new, threshold=threshold, min_volume=min_volume,
                         paper_threshold=paper_threshold)


# ---------------------------------------------------------------------------
# Phase J — revision drift vs. reclassification
# ---------------------------------------------------------------------------
#: Classes a breaching flow is attributed to, most to least reassuring.
SSB_REVISION = "ssb-revision"         # a source table's SSB `updated` date moved
QUERY_CHANGE = "query-change"         # our query file for a source table changed
INPUT_CHANGE = "input-change"         # a committed raw input file changed
DERIVED = "derived-from-revised"      # a derived edge whose own upstream table or input changed
UNEXPLAINED = "unexplained"           # nothing upstream changed: our code or config did

#: Origin tokens that do not name a single table (derived edges), mapped to
#: the upstream sources whose change can legitimately move them: the base
#: tables and the committed input files the edge is computed from. A derived
#: edge is ``derived-from-revised`` only when one of *its own* upstreams moved,
#: not when any table in the manifest did — the partner table 08799 is
#: revised monthly and moves no volume, so "anything upstream" would excuse
#: every derived move on every scheduled run and leave ``UnexplainedDriftError``
#: unreachable for the β-defining edges.
_DERIVED_UPSTREAM: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    # Firewood -> Energy: the residual of the 11181 removals after the 08801 exports.
    "SSB various tables": (frozenset({"11181", "08801"}), frozenset()),
    # WPP -> Long-term USE / Energy: the sawn chain — removals (03795, 07410),
    # trade (08801) and the Treindustrien production file.
    "SSB and Treindustrien": (frozenset({"03795", "07410", "08801"}),
                              frozenset({"treindustrien_production_data.csv"})),
}
_GENERIC_ORIGINS = frozenset(_DERIVED_UPSTREAM)

#: Origin token -> committed input file name (``manifest.inputs`` key).
_INPUT_TOKENS = {
    "Treindustrien årsrapport": "treindustrien_production_data.csv",
    "FAOSTAT": "FAO_Data.csv",
}


def origin_tables(origin: str) -> set[str]:
    """SSB table ids named in an origin string (``'SSB table 08801 (net)'`` -> ``{'08801'}``)."""
    tables: set[str] = set()
    for token in str(origin).split("|"):
        token = token.strip()
        if token.startswith("SSB table "):
            tables.add(token[len("SSB table "):].split()[0])
    return tables


def origin_inputs(origin: str) -> set[str]:
    """Committed raw input files named in an origin string."""
    return {
        _INPUT_TOKENS[t.strip()]
        for t in str(origin).split("|") if t.strip() in _INPUT_TOKENS
    }


def classify_change(
    origin: str,
    *,
    revised_tables: Iterable[str],
    changed_queries: Iterable[str],
    changed_inputs: Iterable[str],
) -> str:
    """Attribute one moved flow to what changed upstream of it (see the class constants)."""
    revised, queries, inputs = set(revised_tables), set(changed_queries), set(changed_inputs)
    tables = origin_tables(origin)
    files = origin_inputs(origin)
    if tables & revised:
        return SSB_REVISION
    if tables & queries:
        return QUERY_CHANGE
    if files & inputs:
        return INPUT_CHANGE
    tokens = {t.strip() for t in str(origin).split("|")}
    for token in tokens & _GENERIC_ORIGINS:
        up_tables, up_inputs = _DERIVED_UPSTREAM[token]
        if (up_tables & (revised | queries)) or (up_inputs & inputs):
            return DERIVED
    return UNEXPLAINED


def attribute_changes(
    report: DiffReport,
    origins: Mapping[tuple[str, str, int], str],
    *,
    revised_tables: Iterable[str] = (),
    changed_queries: Iterable[str] = (),
    changed_inputs: Iterable[str] = (),
) -> pd.DataFrame:
    """Classify every breaching flow of ``report``.

    ``origins`` maps ``(source, target, year)`` to the new dataset's origin
    string. Returns a frame with the flow, its move and a ``class`` column.
    Mass balance cannot tell an SSB revision from a changed transform; this
    can, because the manifest records each table's SSB revision date, the
    query file hash and the committed input hashes.
    """
    rows = []
    for c in report.breaching_flows:
        origin = origins.get((c.source, c.target, int(c.year)), "")
        rows.append({
            "source": c.source, "target": c.target, "year": int(c.year),
            "old": c.old, "new": c.new, "rel": c.rel, "kind": c.kind, "origin": origin,
            "class": classify_change(
                origin, revised_tables=revised_tables,
                changed_queries=changed_queries, changed_inputs=changed_inputs,
            ),
        })
    return pd.DataFrame(rows, columns=["source", "target", "year", "old", "new", "rel",
                                       "kind", "origin", "class"])


def assert_explained(attribution: pd.DataFrame, report: DiffReport) -> None:
    """Raise :class:`UnexplainedDriftError` if any breaching flow has no upstream cause.

    Called after :func:`assert_within_threshold` would already have raised; it
    exists so the *kind* of failure is named — an unexplained move is a repo
    defect, not an SSB event, and the issue the workflow opens should say so.
    """
    if attribution.empty:
        return
    bad = attribution[attribution["class"] == UNEXPLAINED]
    if bad.empty:
        return
    lines = "\n".join(
        f"  - {r.source} -> {r.target} ({r.year}): {r.old:,.0f} -> {r.new:,.0f} m3 "
        f"[{r.origin or 'no origin'}]"
        for r in bad.head(20).itertuples()
    )
    raise UnexplainedDriftError(
        f"{len(bad)} flow(s) moved past the threshold although no SSB source table was "
        f"revised, no query file changed and no committed input changed:\n{lines}\n"
        f"The change came from this repository's code or configuration. Review the "
        f"transform or constants change and adopt it deliberately with accept_drift."
    )


__all__ = [
    "DEFAULT_MIN_VOLUME",
    "DEFAULT_PAPER_THRESHOLD",
    "DEFAULT_THRESHOLD",
    "DERIVED",
    "INPUT_CHANGE",
    "QUERY_CHANGE",
    "SSB_REVISION",
    "UNEXPLAINED",
    "DiffReport",
    "FlowChange",
    "UnexplainedDriftError",
    "VintageDiffError",
    "assert_explained",
    "assert_within_threshold",
    "attribute_changes",
    "classify_change",
    "diff_committed",
    "diff_datasets",
    "origin_inputs",
    "origin_tables",
]
