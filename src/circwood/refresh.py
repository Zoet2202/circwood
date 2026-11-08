"""The refresh loop — regenerate the dataset from SSB and prove it is sound.

``circwood-refresh`` (or ``python -m circwood.refresh``) runs the whole chain:

1. **Ingest** fresh SSB data (Phase A: complete and validated, or it raises).
2. **Year window** (Phase J) — derive the window every gated table covers from
   the vintages and compare it with ``DATASET_MIN_YEAR``/``DATASET_MAX_YEAR``:
   lost coverage blocks; new coverage is *proposed* in the report; a table
   that is overdue by its usual publication lag is flagged.
3. **Rebuild** ``sankey_data.csv`` and ``carbon_data.csv``.
4. **Write** ``data/processed/manifest.json`` — provenance and vintage,
   including the hashes of the committed raw inputs.
5. **Mass balance** — every process node within tolerance, in aggregate and
   per (region, species) slice — and **carbon conservation** (Phase G).
6. **Reconcile** — Treindustrien throughput and the SSB 07410 control total.
7. **Vintage diff** — the new dataset against the committed one, as a blocking
   gate, with the tighter threshold inside the paper window; every breaching
   flow is **attributed** (Phase J) to an SSB revision, a query change, an
   input-file change, or — the serious class — nothing upstream at all, which
   means this repository's code moved a published number.
8. **Re-export the analyses** — ``beta_sensitivity.csv`` and ``hwp_stock.csv``
   (Phase G), then ``beta_series.csv`` (Phase F; it reads its bounds from the
   sensitivity file). Their own gates are refresh gates too.
9. **Forest layer** (Phase H) — the six NFI tables to ``forest_resource.csv``,
   if the forest query directory exists.
10. **Economic layer** (Phase L) — the price tables to ``data/raw/prices_ssb.csv``
    and the valued flows to ``economic_value.csv``, if the economic query
    directory exists; the manifest's input hashes are then re-recorded so the
    rewritten price file belongs to this vintage, not the next one.
11. **Update the golden fixture** so the regenerated dataset and its regression
    fence move together, per the house rule that they change in the same commit,
    and append one line to ``refresh_history.jsonl`` when anything moved. A run
    a gate blocks writes no history line — nothing of it survives (below); its
    evidence is the failure report, which carries the window table, the diff
    and the attribution.

**Nothing survives a failure.** If any check fails, the previous
``data/processed`` files are restored before the error propagates, so a failed
refresh leaves the working tree exactly as it found it and there is nothing
half-updated to commit.

Ordering note (deviates from the roadmap's literal list, deliberately): the
roadmap put ``pytest`` between the rebuild and the data checks. That cannot pass
— ``tests/test_golden.py`` compares the shipped dataset to the committed
fixture, so it is red by construction the moment the dataset is regenerated and
before step 10 updates the fixture. The workflow therefore runs pytest *before*
the refresh (proving the code is sound against the committed data) and again
*after* it (proving the regenerated dataset and its refreshed fixture agree).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from circwood import config
from circwood import coverage as covmod
from circwood import diff as diffmod
from circwood import manifest as manifestmod
from circwood.coverage import WindowReport
from circwood.diff import DiffReport
from circwood.manifest import Manifest

log = logging.getLogger(__name__)

GOLDEN_FIXTURE = config.ROOT_DIR / "tests" / "fixtures" / "golden_sankey_data.csv"

#: Outputs the refresh regenerates, and therefore must restore on failure.
MANAGED_OUTPUTS = (
    "sankey_data.csv", "carbon_data.csv", "manifest.json",
    config.BETA_SERIES_CSV.name, config.BETA_SENSITIVITY_CSV.name,
    config.HWP_STOCK_CSV.name, config.FOREST_RESOURCE_CSV.name,
    config.ECONOMIC_VALUE_CSV.name, config.REFRESH_HISTORY.name,
)

#: Committed raw inputs the fresh run may rewrite (same vintage as the national
#: pulls) and whose hashes the manifest records.
MANAGED_INPUTS = (config.HARVEST_FYLKE_CSV, config.EXPORTS_PARTNER_CSV, config.PRICES_SSB_CSV)

#: Inputs recorded in the manifest (hash, rows, years) for drift attribution.
MANIFEST_INPUTS = (config.TREINDUSTRIEN_CSV, config.FAO_CSV) + MANAGED_INPUTS


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
    window: WindowReport | None = None
    attribution: pd.DataFrame | None = None
    accepted_drift: bool = False
    outputs: dict[str, Path] = field(default_factory=dict)
    revised_tables: list[str] = field(default_factory=list)

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
            f"- **Mass balance:** pass (worst process node: {worst_txt}); per-slice and carbon-conservation checks pass",
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
        for name, path in sorted(self.outputs.items()):
            lines.append(f"- **{name}:** re-exported (`{_display_path(path)}`)")
        lines += [
            f"- **Golden fixture:** "
            f"{'updated to match' if self.golden_updated else 'unchanged'}",
        ]
        if self.accepted_drift:
            lines.append(
                "- **accept_drift:** the vintage diff was reviewed and adopted deliberately; "
                "see the attribution below"
            )
        if self.window is not None:
            lines += ["", "### " + self.window.summary()]
        lines += ["", "### " + self.diff.summary()]
        if self.attribution is not None and not self.attribution.empty:
            lines += ["", _attribution_markdown(self.attribution)]
        return "\n".join(lines)


def _attribution_markdown(attribution: pd.DataFrame, limit: int = 25) -> str:
    """The attribution table for a report: unexplained moves first, then by size."""
    if attribution.empty:
        return ""
    # The serious class must not be pushed off the table by larger, explained
    # moves (the regeneration run showed 25 unexplained rows and hid its 10
    # derived ones). Rank by class, then by absolute move.
    ranked = attribution.assign(
        _rank=(attribution["class"] != diffmod.UNEXPLAINED).astype(int),
        _size=(attribution["new"].fillna(0.0) - attribution["old"].fillna(0.0)).abs(),
    ).sort_values(["_rank", "_size"], ascending=[True, False])
    lines = ["**Attribution of moves past the threshold**", "",
             "| flow | year | change | class | origin |", "|---|---|---|---|---|"]
    # dict rows, not itertuples: a column named "class" has no attribute form.
    for r in ranked.head(limit).to_dict("records"):
        rel = f"{r['rel']:+.1%}" if pd.notna(r["rel"]) else "new"
        lines.append(f"| {r['source']} → {r['target']} | {r['year']} | {rel} | {r['class']} | {r['origin']} |")
    if len(ranked) > limit:
        lines.append(f"| … | | | {len(ranked) - limit} more rows | |")
    counts = attribution["class"].value_counts().to_dict()
    lines += ["", "Classes: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + "."]
    return "\n".join(lines)


def _attach_context(exc: BaseException, *parts: str) -> None:
    """Hang a gate's evidence on its exception so the FAILED report can show it.

    The acceptance route (``docs/REFRESH_POLICY.md``) starts from the failed
    run's report, so that report must carry what a reviewer needs — the window
    table, the diff summary and the attribution — not only the message.
    """
    existing = str(getattr(exc, "report_context", "") or "")
    exc.report_context = "\n\n".join(p for p in [existing, *parts] if p)  # type: ignore[attr-defined]


def _display_path(path: Path) -> str:
    """Repo-relative POSIX path when inside the repo, else the file name."""
    try:
        return Path(path).relative_to(config.ROOT_DIR).as_posix()
    except ValueError:
        return Path(path).name


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


def export_analyses() -> dict[str, Path]:
    """Re-export the Phase G analyses that read the new carbon dataset.

    Order matters: the sensitivity file feeds the β export's bounds. A
    module-level seam so tests can stub it.
    """
    from circwood.analysis.sensitivity import export_sensitivity
    from circwood.carbon.stock import export_hwp_stock

    return {
        "beta sensitivity": export_sensitivity(config.BETA_SENSITIVITY_CSV),
        "HWP stock": export_hwp_stock(config.HWP_STOCK_CSV),
    }


def export_forest() -> Path | None:
    """Phase H: rebuild ``forest_resource.csv`` if the forest query directory exists."""
    if not config.FOREST_QUERY_DIR.is_dir():
        return None
    from circwood.transform.forest import build_forest

    build_forest(write=True)
    return config.FOREST_RESOURCE_CSV


def export_economics() -> Path | None:
    """Phase L: refetch the SSB price tables and re-value the flows, if the query directory exists."""
    if not config.ECONOMIC_QUERY_DIR.is_dir():
        return None
    from circwood.transform.economics import build_economics

    build_economics(write=True)
    return config.ECONOMIC_VALUE_CSV


def _control_totals(sankey: pd.DataFrame) -> list[dict]:
    from circwood.reconcile import CONTROL_TOTALS, domestic_sawtimber

    ctrl = CONTROL_TOTALS["domestic_sawtimber_2012_2022"]
    reference = float(ctrl["value"])
    got = domestic_sawtimber(sankey, int(ctrl["min_year"]), int(ctrl["max_year"]))
    return [{
        "name": "SSB 07410 domestic sawtimber 2012–2022",
        "pipeline": got,
        "reference": reference,
        "rel_diff": got / reference - 1.0,
        "source": ctrl["source"],
        "tolerance": 0.01,
    }]


def _origins(sankey: pd.DataFrame) -> dict[tuple[str, str, int], str]:
    """(source, target, year) -> merged origin string of the new dataset."""
    from circwood.transform.dimensions import merge_origins

    grouped = sankey.groupby(["source", "target", "year"])["origin"].agg(merge_origins).reset_index()
    return {
        (str(s), str(t), int(y)): str(o)
        for s, t, y, o in zip(grouped["source"], grouped["target"], grouped["year"], grouped["origin"])
    }


def _append_history(result: RefreshResult, path: Path) -> None:
    """One JSON line per refresh that changed something — the drift history Phase J wants.

    Only a run that passed its gates (or was adopted with ``accept_drift``)
    gets here: a blocked run leaves no line, by the nothing-survives-a-failure
    rule, and its evidence is the failure report. ``max``/``median`` are over
    moves a human would call moves (|rel| > 1e-9): a value that merely
    re-serialised differently (|rel| ~ 1e-16) must not set the median.
    """
    report = result.diff
    if report.baseline or (not report.changes and not report.added_years):
        return
    rels = [abs(c.rel) for c in report.changes
            if c.rel is not None and c.magnitude >= report.min_volume and abs(c.rel) > 1e-9]
    entry = {
        "date": datetime.now(timezone.utc).date().isoformat(),
        "data_as_of": result.manifest.data_as_of,
        "git_sha": result.manifest.git_sha,
        "common_years": list(report.common_years),
        "added_years": list(report.added_years),
        "flows_changed": len(report.changes),
        "flows_moved": len(rels),
        "flows_breaching": len(report.breaching_flows),
        "max_abs_rel_move": max(rels, default=0.0),
        "median_abs_rel_move": float(pd.Series(rels).median()) if rels else 0.0,
        "threshold": report.threshold,
        "paper_threshold": report.paper_threshold,
        "accepted_drift": result.accepted_drift,
        "classes": (result.attribution["class"].value_counts().to_dict()
                    if result.attribution is not None and not result.attribution.empty else {}),
        "revised_tables": sorted(result.revised_tables),
    }
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def refresh(
    *,
    threshold: float = diffmod.DEFAULT_THRESHOLD,
    min_volume: float = diffmod.DEFAULT_MIN_VOLUME,
    paper_threshold: float | None = diffmod.DEFAULT_PAPER_THRESHOLD,
    allow_partial: bool = False,
    update_golden: bool = True,
    accept_drift: bool = False,
    coefficient_source: str = "manual",
    as_of=None,
) -> RefreshResult:
    """Regenerate the dataset from SSB and gate it. See the module docstring.

    ``accept_drift=True`` runs the diff and reports it but does not block on it.
    It is for the one case the gate cannot decide by itself: a human has looked
    at a blocked refresh, confirmed the new numbers are right, and is adopting
    them deliberately. Never set it on a schedule — a gate that is always open
    is not a gate. ``as_of`` (a date) pins the publication-lag judgement for tests.
    """
    from circwood.ingest import treindustrien as tre
    from circwood.ingest.ssb import process_query_files
    from circwood.pipeline import build_carbon
    from circwood.reconcile import treindustrien_reconciliation
    from circwood.transform.assemble import run_fresh_pipeline
    from circwood.validate import (
        PROCESS_NODES,
        assert_carbon_balanced,
        assert_slices_balanced,
        assert_window_balanced,
        process_imbalances,
        window,
    )

    managed = [config.PROCESSED_DIR / name for name in MANAGED_OUTPUTS] + [
        GOLDEN_FIXTURE, *MANAGED_INPUTS,
    ]
    snapshot = _snapshot(managed)

    # Read the committed dataset and manifest before the rebuild overwrites them
    # — the "previous vintage" the diff gate compares against, and the record
    # of what SSB had published then (drift attribution).
    previous = pd.read_csv(config.SANKEY_CSV) if config.SANKEY_CSV.exists() else None
    previous_manifest = manifestmod.read_manifest()
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

        log.info("Checking the year window against the fetched coverage")
        treindustrien_df = tre.load_treindustrien() if config.TREINDUSTRIEN_CSV.exists() else None
        window_report = covmod.coverage_report(
            ingested.vintages, treindustrien_df=treindustrien_df, as_of=as_of,
            failures=ingested.failures,
        )
        try:
            covmod.assert_window(window_report)
        except covmod.WindowError as exc:
            _attach_context(exc, "### " + window_report.summary())
            raise
        for t in window_report.overdue:
            log.warning("Publication overdue: %s", t.describe())
        if window_report.proposal:
            log.warning("%s", window_report.proposal)

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
        inputs = {p.name: manifestmod.describe_input(p) for p in MANIFEST_INPUTS if p.exists()}
        manifest = manifestmod.build_manifest(ingested.vintages, outputs, inputs)
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
        # Phase G: carbon follows the wood, so carbon balances where volume does.
        assert_carbon_balanced(carbon)
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
            previous, sankey, threshold=threshold, min_volume=min_volume,
            paper_threshold=paper_threshold,
        )
        prev_tables = previous_manifest.tables if previous_manifest else None
        prev_inputs = previous_manifest.inputs if previous_manifest else None
        revised = covmod.revised_tables(prev_tables, manifest.tables)
        attribution = diffmod.attribute_changes(
            report, _origins(sankey),
            revised_tables=revised,
            changed_queries=covmod.changed_queries(prev_tables, manifest.tables),
            changed_inputs=covmod.changed_inputs(prev_inputs, manifest.inputs),
        )
        if accept_drift:
            if not report.ok:
                log.warning(
                    "accept_drift=True: adopting a dataset the gate would have "
                    "blocked (%d flows past threshold, %d structural changes; classes %s)",
                    len(report.breaching_flows), len(report.structural_changes),
                    attribution["class"].value_counts().to_dict() if not attribution.empty else {},
                )
        else:
            try:
                diffmod.assert_explained(attribution, report)
                diffmod.assert_within_threshold(report)
            except diffmod.VintageDiffError as exc:
                _attach_context(exc, "### " + window_report.summary(), "### " + report.summary(),
                                _attribution_markdown(attribution))
                raise

        log.info("Re-exporting the analyses (sensitivity, beta, HWP stock)")
        analysis_paths = export_analyses()
        beta_path = export_beta(config.BETA_SERIES_CSV)

        log.info("Rebuilding the forest resource layer")
        forest_path = export_forest()
        if forest_path is not None:
            analysis_paths["forest resource"] = forest_path

        log.info("Re-valuing the flows (economic layer)")
        econ_path = export_economics()
        if econ_path is not None:
            analysis_paths["economic value"] = econ_path
            # The price file was just rewritten: record its hash in this vintage,
            # not the next one (the attribution would otherwise read it as a
            # changed input one run late, and the committed manifest would never
            # carry the file that produced the committed valuation).
            inputs = {p.name: manifestmod.describe_input(p) for p in MANIFEST_INPUTS if p.exists()}
            manifest = replace(manifest, inputs=inputs)
            manifestmod.write_manifest(manifest)

        golden_updated = False
        if update_golden and GOLDEN_FIXTURE.exists():
            new_bytes = config.SANKEY_CSV.read_bytes()
            if new_bytes != snapshot.get(GOLDEN_FIXTURE):
                # House rule: the shipped dataset and its regression fence change
                # in the same commit, so the diff is visible in review.
                GOLDEN_FIXTURE.write_bytes(new_bytes)
                golden_updated = True
                log.info("Updated golden fixture %s", GOLDEN_FIXTURE)

        result = RefreshResult(
            sankey=sankey,
            carbon=carbon,
            manifest=manifest,
            diff=report,
            balance=balance,
            reconciliation=reconciliation,
            control_totals=controls,
            golden_updated=golden_updated,
            beta_series=beta_path,
            window=window_report,
            attribution=attribution,
            accepted_drift=accept_drift and not report.ok,
            outputs=analysis_paths,
            revised_tables=sorted(revised),
        )
        _append_history(result, config.REFRESH_HISTORY)
        return result
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
        "--paper-threshold", type=float, default=diffmod.DEFAULT_PAPER_THRESHOLD,
        help="Tighter relative move that fails the run inside the paper window "
             "2012-2022 (default %(default)s; pass a negative value to disable).",
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
            paper_threshold=None if args.paper_threshold < 0 else args.paper_threshold,
            allow_partial=args.allow_partial,
            update_golden=not args.no_golden,
            accept_drift=args.accept_drift,
        )
    except Exception as exc:
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            kind = type(exc).__name__
            context = str(getattr(exc, "report_context", "") or "")
            args.report.write_text(
                f"## SSB data refresh — FAILED ({kind})\n\n"
                f"```\n{exc}\n```\n\n"
                f"Nothing was written: the previous `data/processed` outputs were "
                f"restored before this error propagated.\n"
                + (f"\n{context}\n" if context else ""),
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
