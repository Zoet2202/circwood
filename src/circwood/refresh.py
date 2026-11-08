"""The refresh loop — regenerate the dataset from SSB and prove it is sound.

``circwood-refresh`` (or ``python -m circwood.refresh``) runs the whole chain:

1. **Ingest** fresh SSB data (Phase A: complete and validated, or it raises).
2. **Rebuild** ``sankey_data.csv`` and ``carbon_data.csv``.
3. **Write** ``data/processed/manifest.json`` — provenance and vintage.
4. **Mass balance** — every process node within tolerance.
5. **Reconcile** — Treindustrien throughput and the SSB 07410 control total.
6. **Vintage diff** — the new dataset against the committed one, as a blocking
   gate.
7. **Re-export ``beta_series.csv``** (Phase F) from the new carbon dataset and
   manifest. Its own gates (closure gap, output excess, missing year) are
   refresh gates too: if β cannot be exported, the refresh fails and restores.
   Without this step the committed β would go stale and the post-refresh
   ``pytest`` (``test_committed_beta_series_matches_fresh_export``) would fail.
   The pinned beta values in ``tests/test_hwp.py`` read a frozen input
   (``tests/fixtures/beta_input_carbon.csv``), so a refresh does not touch them.
8. **Update the golden fixture** so the regenerated dataset and its regression
   fence move together, per the house rule that they change in the same commit.

**Nothing survives a failure.** If any check fails, the previous
``data/processed`` files are restored before the error propagates, so a failed
refresh leaves the working tree exactly as it found it and there is nothing
half-updated to commit.

Ordering note (deviates from the roadmap's literal list, deliberately): the
roadmap put ``pytest`` between the rebuild and the data checks. That cannot pass
— ``tests/test_golden.py`` compares the shipped dataset to the committed
fixture, so it is red by construction the moment the dataset is regenerated and
before step 7 updates the fixture. The workflow therefore runs pytest *before*
the refresh (proving the code is sound against the committed data) and again
*after* it (proving the regenerated dataset and its refreshed fixture agree).
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from circwood import config, diff as diffmod, manifest as manifestmod
from circwood.diff import DiffReport
from circwood.manifest import Manifest

log = logging.getLogger(__name__)

GOLDEN_FIXTURE = config.ROOT_DIR / "tests" / "fixtures" / "golden_sankey_data.csv"

#: Outputs the refresh regenerates, and therefore must restore on failure.
MANAGED_OUTPUTS = (
    "sankey_data.csv", "carbon_data.csv", "manifest.json", config.BETA_SERIES_CSV.name,
)


@dataclass
class RefreshResult:
    """Everything one refresh produced, plus the evidence that it is sound."""

    sankey: pd.DataFrame
    carbon: pd.DataFrame
    manifest: Manifest
    diff: DiffReport
    balance: pd.DataFrame
    reconciliation: pd.DataFrame
    control_totals: list[dict] = field(default_factory=list)
    golden_updated: bool = False
    beta_series: Path | None = None

    def report(self) -> str:
        """Markdown for the refresh PR body."""
        y0, y1 = self.manifest.year_range
        lines = [
            "## SSB data refresh",
            "",
            f"- **Data as of:** {self.manifest.data_as_of or 'unknown'} "
            f"(oldest source table: {self.manifest.oldest_table_as_of or 'unknown'})",
            f"- **Coverage:** {y0}–{y1} · {len(self.sankey):,} flow rows",
            f"- **Built:** {self.manifest.generated_at} "
            f"from `{(self.manifest.git_sha or '?')[:8]}`",
            "",
            "### Source tables",
            "",
            "| table | SSB updated | rows | years | query sha256 |",
            "|---|---|---|---|---|",
        ]
        for table_id, t in sorted(self.manifest.tables.items()):
            lines.append(
                f"| {table_id} | {t.get('updated', '?')} | {t.get('rows', '?'):,} | "
                f"{t.get('year_min', '?')}–{t.get('year_max', '?')} | "
                f"`{str(t.get('query_sha256', ''))[:12]}` |"
            )

        worst = self.balance.head(1)
        worst_txt = (
            f"{worst.iloc[0]['node']} {worst.iloc[0]['rel_imbalance']:+.2%}"
            if not worst.empty else "n/a"
        )
        lines += [
            "",
            "### Checks",
            "",
            f"- **Mass balance:** pass (worst process node: {worst_txt})",
            f"- **Treindustrien reconciliation:** pass "
            f"(worst |rel diff| {self.reconciliation['rel_diff'].abs().max():.2%})",
        ]
        for ctrl in self.control_totals:
            lines.append(
                f"- **{ctrl['name']}:** {ctrl['pipeline']:,.0f} vs "
                f"{ctrl['reference']:,.0f} m³ ({ctrl['rel_diff']:+.2%}) — "
                f"{ctrl['source']}"
            )
        if self.beta_series is not None:
            lines.append("- **beta series:** re-exported (`data/processed/beta_series.csv`)")
        lines += [
            f"- **Golden fixture:** "
            f"{'updated to match' if self.golden_updated else 'unchanged'}",
            "",
            "### " + self.diff.summary(),
        ]
        return "\n".join(lines)


def _snapshot(paths: list[Path]) -> dict[Path, bytes | None]:
    return {p: (p.read_bytes() if p.exists() else None) for p in paths}


def _restore(snapshot: dict[Path, bytes | None]) -> None:
    for path, payload in snapshot.items():
        if payload is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(payload)


def export_beta(path: Path) -> Path:
    """Re-export the Phase F β series from the just-written carbon dataset.

    A module-level seam so tests can stub it; the real call reads
    ``config.CARBON_CSV`` and the new manifest, and raises on any β gate.
    """
    from circwood.analysis.hwp import export_beta_series

    return export_beta_series(path)


def _control_totals(sankey: pd.DataFrame) -> list[dict]:
    from circwood.reconcile import CONTROL_TOTALS, domestic_sawtimber

    ctrl = CONTROL_TOTALS["domestic_sawtimber_2012_2022"]
    got = domestic_sawtimber(sankey, ctrl["min_year"], ctrl["max_year"])
    return [{
        "name": "SSB 07410 domestic sawtimber 2012–2022",
        "pipeline": got,
        "reference": ctrl["value"],
        "rel_diff": got / ctrl["value"] - 1.0,
        "source": ctrl["source"],
        "tolerance": 0.01,
    }]


def refresh(
    *,
    threshold: float = diffmod.DEFAULT_THRESHOLD,
    min_volume: float = diffmod.DEFAULT_MIN_VOLUME,
    allow_partial: bool = False,
    update_golden: bool = True,
    accept_drift: bool = False,
    coefficient_source: str = "manual",
) -> RefreshResult:
    """Regenerate the dataset from SSB and gate it. See the module docstring.

    ``accept_drift=True`` runs the diff and reports it but does not block on it.
    It is for the one case the gate cannot decide by itself: a human has looked
    at a blocked refresh, confirmed the new numbers are right, and is adopting
    them deliberately. Never set it on a schedule — a gate that is always open
    is not a gate.
    """
    from circwood.ingest.ssb import process_query_files
    from circwood.pipeline import build_carbon
    from circwood.transform.assemble import run_fresh_pipeline
    from circwood.reconcile import treindustrien_reconciliation
    from circwood.validate import (
        PROCESS_NODES, assert_slices_balanced, assert_window_balanced,
        process_imbalances, window,
    )

    # The county-harvest CSV is an input the fresh run rewrites (same vintage
    # as the national 03795 pull), so a failed refresh must restore it too.
    managed = [config.PROCESSED_DIR / name for name in MANAGED_OUTPUTS] + [
        GOLDEN_FIXTURE, config.HARVEST_FYLKE_CSV,
    ]
    snapshot = _snapshot(managed)

    # Read the committed dataset before the rebuild overwrites it — this is the
    # "previous vintage" the diff gate compares against.
    previous = pd.read_csv(config.SANKEY_CSV) if config.SANKEY_CSV.exists() else None
    if previous is None:
        log.warning(
            "No committed dataset at %s — this run establishes the baseline and "
            "the vintage diff has nothing to gate.", config.SANKEY_CSV,
        )

    try:
        log.info("Ingesting SSB tables from %s", config.SSB_QUERY_DIR)
        ingested = process_query_files(
            str(config.SSB_QUERY_DIR), allow_partial=allow_partial
        )

        log.info("Rebuilding the Sankey dataset")
        sankey = run_fresh_pipeline(
            coefficient_source=coefficient_source, ingested=ingested
        )
        log.info("Rebuilding the carbon dataset")
        carbon = build_carbon(use_cached_ssb=True, write=True)

        outputs = {
            "sankey_data.csv": manifestmod.describe_dataset(sankey, config.SANKEY_CSV),
            "carbon_data.csv": manifestmod.describe_dataset(carbon, config.CARBON_CSV),
        }
        manifest = manifestmod.build_manifest(ingested.vintages, outputs)
        manifestmod.write_manifest(manifest)
        log.info("Wrote manifest: %s", manifest.describe())

        # The guarantee as the project actually states it: the process nodes
        # conserve volume over the paper window. Asserting it over all years
        # would fail on the committed dataset too — the source tables' year
        # coverage differs (see validate.assert_window_balanced).
        log.info("Checking mass balance")
        assert_window_balanced(sankey)
        # Phase C: the same guarantee per (region, species) slice — a dataset
        # that balances in aggregate but not per slice produces nonsense the
        # first time someone filters.
        assert_slices_balanced(sankey)
        balance = process_imbalances(window(sankey))
        balance = balance[balance["node"].isin(PROCESS_NODES)].reset_index(drop=True)

        log.info("Reconciling against source aggregates")
        reconciliation = treindustrien_reconciliation(sankey)
        worst = reconciliation["rel_diff"].abs().max()
        if worst >= 0.01:
            raise AssertionError(
                f"Treindustrien reconciliation drifted to {worst:.2%} "
                f"(tolerance 1%):\n{reconciliation.to_string()}"
            )
        controls = _control_totals(sankey)
        for ctrl in controls:
            if abs(ctrl["rel_diff"]) >= ctrl["tolerance"]:
                raise AssertionError(
                    f"{ctrl['name']}: {ctrl['pipeline']:,.0f} vs reference "
                    f"{ctrl['reference']:,.0f} m³ ({ctrl['rel_diff']:+.2%}, "
                    f"tolerance {ctrl['tolerance']:.0%})"
                )

        log.info("Diffing against the previous vintage")
        report = diffmod.diff_datasets(
            previous, sankey, threshold=threshold, min_volume=min_volume
        )
        if accept_drift:
            if not report.ok:
                log.warning(
                    "accept_drift=True: adopting a dataset the gate would have "
                    "blocked (%d flows past threshold, %d structural changes)",
                    len(report.breaching_flows), len(report.structural_changes),
                )
        else:
            diffmod.assert_within_threshold(report)

        log.info("Re-exporting the beta series")
        beta_path = export_beta(config.BETA_SERIES_CSV)

        golden_updated = False
        if update_golden and GOLDEN_FIXTURE.exists():
            new_bytes = config.SANKEY_CSV.read_bytes()
            if new_bytes != snapshot.get(GOLDEN_FIXTURE):
                # House rule: the shipped dataset and its regression fence change
                # in the same commit, so the diff is visible in review.
                GOLDEN_FIXTURE.write_bytes(new_bytes)
                golden_updated = True
                log.info("Updated golden fixture %s", GOLDEN_FIXTURE)

        return RefreshResult(
            sankey=sankey,
            carbon=carbon,
            manifest=manifest,
            diff=report,
            balance=balance,
            reconciliation=reconciliation,
            control_totals=controls,
            golden_updated=golden_updated,
            beta_series=beta_path,
        )
    except Exception:
        log.error("Refresh failed — restoring the previous data/processed outputs")
        _restore(snapshot)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh the Circwood dataset from SSB and gate the result.",
    )
    parser.add_argument(
        "--threshold", type=float, default=diffmod.DEFAULT_THRESHOLD,
        help="Relative flow move that fails the run (default %(default)s).",
    )
    parser.add_argument(
        "--min-volume", type=float, default=diffmod.DEFAULT_MIN_VOLUME,
        help="Ignore relative moves on flows below this volume, m3 "
             "(default %(default)s).",
    )
    parser.add_argument(
        "--allow-partial", action="store_true",
        help="Continue when an SSB table cannot be fetched. Produces an "
             "incomplete dataset; not for scheduled runs.",
    )
    parser.add_argument(
        "--no-golden", action="store_true",
        help="Do not update tests/fixtures/golden_sankey_data.csv.",
    )
    parser.add_argument(
        "--accept-drift", action="store_true",
        help="Report the vintage diff but do not block on it. For a human "
             "deliberately adopting a reviewed change; never for a scheduled run.",
    )
    parser.add_argument(
        "--report", type=Path, default=None,
        help="Write the markdown report here (used as the refresh PR body).",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        result = refresh(
            threshold=args.threshold,
            min_volume=args.min_volume,
            allow_partial=args.allow_partial,
            update_golden=not args.no_golden,
            accept_drift=args.accept_drift,
        )
    except Exception as exc:
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                f"## SSB data refresh — FAILED\n\n"
                f"```\n{exc}\n```\n\n"
                f"Nothing was written: the previous `data/processed` outputs were "
                f"restored before this error propagated.\n",
                encoding="utf-8",
            )
        log.error("%s", exc)
        return 1

    body = result.report()
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(body + "\n", encoding="utf-8")
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
