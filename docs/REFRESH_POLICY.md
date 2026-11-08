# Refresh policy

How the dataset refreshes itself, what stops it, and what a human does when it
stops. Written for whoever runs the loop after the authors (ROADMAP Phase J,
2026-11-08).

## The loop

`.github/workflows/refresh.yml` runs `circwood-refresh` on the 4th of every
month and opens a pull request when anything moved. One run:

1. **pytest** against the committed dataset (the code is sound before anything
   is touched).
2. **Ingest** every table in `config/ssb_queries/` (complete, validated,
   Norwegian json-stat2, or it raises).
3. **Year window** — derived from the fetched coverage, compared with
   `DATASET_MIN_YEAR`/`DATASET_MAX_YEAR` (see below).
4. **Rebuild** the flows and the carbon; write `manifest.json`.
5. **Gates:** mass balance (aggregate, per slice, carbon), reconciliation to
   Treindustrien and the SSB 07410 control total.
6. **Vintage diff** against the committed dataset, with attribution.
7. **Re-export** the sensitivity, β, the HWP stock, the forest layer and the
   economic layer; update the golden fixture; append the history line.
8. **pytest** again against the refreshed state; then the PR.

Nothing survives a failure: every managed output and input is restored before
the error propagates, and an issue is opened with the report.

## The year window

The dataset is clipped to `DATASET_MIN_YEAR`–`DATASET_MAX_YEAR` (2012–2024).
Each run derives the window the gated SSB tables actually cover and:

| finding | what happens | what to do |
|---|---|---|
| a gated table no longer covers the declared window | the run fails (`WindowError`) and restores | check SSB: did the table lose years, or must the window be narrowed deliberately? |
| every gated table now covers a later year | the PR body carries a **proposal**: "widen `DATASET_MAX_YEAR` to N" | check that `data/raw/treindustrien_production_data.csv` reaches that year too, then make the one-line change in `constants.py` |
| a table is behind its usual lag + 6 months | the PR body flags it **overdue** | look at SSB's release calendar; a discontinued series shows up here first |

Usual lags are on each `TableContract` (`publication_lag_months`); the
Treindustrien file is reported as an input with a nine-month lag. The owner
decided (2026-11-08) that Treindustrien ending at 2023 does not narrow the
window; β and the stock model refuse 2024 on their own gates.

## The vintage diff and its attribution

A flow that moves by more than 5 % (2 % inside the paper window 2012–2022, both
above a 1 000 m³ floor), or any node, edge or year that appears or
disappears, blocks the run. Every breaching flow is then attributed by its
`origin` to one of:

- **ssb-revision** — a source table's SSB `updated` date changed. Routine.
- **query-change** — our query file for that table changed.
- **input-change** — a committed raw input an `origin` names (the
  Treindustrien file, FAOSTAT) changed. The county, partner and price files
  are SSB fetches: their revisions show as `ssb-revision` of their own tables
  (03795 by county, the 08799 chunks, 07413 and 08360), and prices move no flow.
- **derived-from-revised** — a derived edge (`WPP → Long-term USE / Energy`,
  `Firewood → Energy`) whose *own* upstream tables or input moved
  (`diff._DERIVED_UPSTREAM`). A revision elsewhere — the partner table 08799 is
  revised monthly and moves no volume — does not count.
- **unexplained** — nothing upstream moved; this repository's code or
  configuration moved a published number. The run fails with
  `UnexplainedDriftError`, named as such in the issue.

A table that is new to the manifest explains nothing, and a missing previous
record excuses nothing.

## From blocked to accepted

The only route is a reviewed decision:

1. Read the failed run's report (the issue, or the artifact).
2. Reproduce offline if you want to look closer: the raw responses of the
   last run are cached in `data/raw/ssb/` and
   `ingest.ssb.load_cached_result()` rebuilds the ingest result without the
   network (`_vintages.json` is merged across the flow, forest and price
   ingests, so every table is there); `run_fresh_pipeline(ingested=...)` and
   `diff.diff_datasets` give you the same diff. The failed run's report
   already carries the window table, the diff and the attribution.
3. If the new numbers are right, re-run the workflow by hand
   (`workflow_dispatch`) with **`accept_drift`** ticked. When the gate would
   have blocked and the box adopted the result, the actor and run id land in
   the run log, the PR body and the commit message and the history line
   records `accepted_drift: true`; a ticked box on a clean run accepts nothing
   and leaves no trace. Never set it on a schedule.
4. If they are wrong, fix the query or the transform and let the next run
   prove it.

## The thresholds

5 % and 2 % were set before any real refresh had run. `refresh_history.jsonl`
records, for every run that passed its gates (or was accepted) and moved a
flow, how many flows moved (`flows_moved` ignores re-serialisation noise), how
far, and which tables were revised; a blocked run leaves no line — nothing of
it survives — and its evidence is the failure report. Once a few cycles exist,
set the thresholds from the observed distribution of routine SSB revisions.
