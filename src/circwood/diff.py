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

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

#: Relative move that trips the gate.
DEFAULT_THRESHOLD = 0.05

#: Flows smaller than this (m³, on both sides) are too small for a relative
#: change to be meaningful. ~0.01 % of a typical annual harvest.
DEFAULT_MIN_VOLUME = 1_000.0

KEY = ["source", "target", "year"]


class VintageDiffError(AssertionError):
    """A regenerated dataset differs from the committed one beyond tolerance."""


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
    @property
    def breaching_flows(self) -> list[FlowChange]:
        """Flow moves past the threshold that are big enough to matter."""
        out = [
            c
            for c in self.changes
            if c.magnitude >= self.min_volume
            and (c.rel is None or abs(c.rel) > self.threshold)
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
        lines.append(
            f"- Compared years: {span} "
            f"(threshold {self.threshold:.0%}, floor {self.min_volume:,.0f} m³)"
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
) -> DiffReport:
    """Compare two vintages of the flow dataset.

    ``old`` may be ``None`` (or empty) — the first build has nothing to compare
    against, which is reported as a baseline rather than treated as a pass or a
    failure.
    """
    if old is None or old.empty:
        return DiffReport(threshold=threshold, min_volume=min_volume, baseline=True)

    old_years = set(old["year"].astype(int))
    new_years = set(new["year"].astype(int))
    common = sorted(old_years & new_years)

    report = DiffReport(
        threshold=threshold,
        min_volume=min_volume,
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
) -> DiffReport:
    """Diff ``new`` against the dataset committed at ``committed_path``.

    A missing file is the baseline case, not an error — that is what makes
    "delete ``data/processed/*.csv`` and re-run" a supported way to rebuild.
    """
    from circwood import config

    path = Path(committed_path) if committed_path is not None else config.SANKEY_CSV
    old = pd.read_csv(path) if path.exists() else None
    return diff_datasets(old, new, threshold=threshold, min_volume=min_volume)


__all__ = [
    "DEFAULT_MIN_VOLUME",
    "DEFAULT_THRESHOLD",
    "DiffReport",
    "FlowChange",
    "VintageDiffError",
    "assert_within_threshold",
    "diff_committed",
    "diff_datasets",
]
