# Changelog

Completed work, newest first. The live plan is [ROADMAP.md](ROADMAP.md).

## 2026-07-28 — Phase C: the filter dimensions

The flow row grew four columns — `region`, `species`, `assortment`,
`entity_id` — while every national number stayed exactly what it was. This is
the schema Phase D's static site filters on, landed before the frontend exists
because retrofitting it after would be materially more expensive. 638 → 1,878
rows; summing `volume` over the new columns recovers the old table.

**Species** (`spruce` / `pine` / `non-coniferous`)

- Mixed flows are split per species using the carbon model's own fixed-point
  propagation (`species_model.flow_species_shares`) — the model result made
  explicit, not new data. Pure flows (pool outflows, harvest-to-pool) keep
  their volumes bit-for-bit; the 07410 control total is still exactly
  61,445,000 m³.
- **A real bug surfaced by the new per-slice fence:** in years where an
  inventory node only withdraws, the propagation left it at its ⅓/⅓/⅓
  initialisation — withdrawn wood counted as one-third non-coniferous against
  a real share of a few percent (−5.6 % non-coniferous imbalance at
  Planingmills). Fixed: each year's fixed point now seeds the next, so
  withdrawals carry the mix the wood went in with. The carbon overlay had
  silently used those ⅓ mixes all along; inventory-withdrawal edges moved
  about −10 % carbon each, total carbon −0.07 %.
- `add_carbon` takes a direct density lookup when the frame carries species
  (identical edge totals by algebra; honest per-row values), fenced by a test
  against the mixed-density path.

**Region** (`NO-xx` counties on the harvest edge, `"NO"` elsewhere)

- SSB 03795 is now also pulled per county via the **`agg_KommFylker`**
  codelist — the whole series backcast into the 2024 county boundaries, which
  sidesteps the 2018–2024 county reforms entirely. Committed as
  `data/raw/harvest_by_fylke.csv` (like the Treindustrien CSV); the fresh
  pipeline rewrites it from the new `03795_fylke` query so both 03795 pulls
  share a vintage, and the refresh loop restores it on failure.
- The county figures sum to *less* than the harvest edges because those edges
  also carry the 11181 firewood component, which SSB only estimates
  nationally. So each `Harvested wood → pool` edge splits into exact
  per-county rows (verified bit-for-bit against the published figures, tied
  out 2012–2023 exactly; 2024 within 6 m³) plus one `"NO"` remainder row for
  the firewood estimate. `"NO"` therefore means *not regionally attributed*,
  never "rest of Norway". A negative remainder (the two 03795 pulls
  disagreeing) raises instead of clamping.
- An unknown county label raises (`dimensions.FYLKE_CODES` is a closed map) —
  same policy as a new node: a new region is a human decision.

**Assortment** — `sawtimber` / `pulpwood` / `sams` / `fuelwood` where the
source distinguishes them (07410 pool outflows, their netted trade
counterparts, the firewood stream); `all` where the distinction has no meaning.

**`entity_id`** — always `"national"`, documented in the schema docstring:
mill-level data would need a member agreement, but its arrival must be a data
change, not a rewrite. Phase E's `min_entities` guard is where it gets teeth.

**Guarantees widened, none weakened**

- `validate.assert_slices_balanced`: every (region, species) slice of every
  process node balances within 1 % over the paper window (absolute floor
  1,000 m³ per slice — same rationale as the vintage diff's volume floor).
  Wired into the refresh gate alongside the aggregate check.
- `dimensions.collapse_national` is the exact inverse of the expansion;
  origin merging is token-based so collapse → re-expand round-trips
  byte-stable (fenced by tests).
- Carbon file stays additive: columns `…origin, carbon` keep their positions,
  dimensions append after.
- 121 tests (was 106), golden fixture regenerated in the same commit.

> Roadmap lineage: the original `ROADMAP.md` (2026-06 refactor) was followed by
> `ROADMAP_NEXT.md` (improvements backlog) and `ROADMAP_CONSOLIDATION.md`
> (live/public/research reordering). On 2026-07-28 these were consolidated:
> the open plan became the single `ROADMAP.md`, and the completed items moved
> here. Old item numbers (A1, D1, …) referenced in commit messages refer to
> those superseded files.

## 2026-07-28 — Phase B: the refresh loop, and a year window that means something

The dataset now refreshes itself behind gates, and covers **2012–2024** — the
years every SSB source table covers — instead of 1988–2025.

**The refresh loop**

- **`data/processed/manifest.json`** (`circwood.manifest`): pull timestamp,
  per-table SSB `updated` date, query-file SHA-256, year coverage, row counts,
  output hashes, package version, git SHA and branch. `data_as_of` deliberately
  reports the newest *SSB revision* date, not our pull time — quoting the latter
  would overstate how fresh the statistics are.
- **`circwood.diff`** — the vintage diff, as a blocking gate. Compares per
  (source, target, year) and fails on a flow moving past a threshold (5 %), on a
  node or edge appearing or disappearing, or on a year being lost. Two
  refinements the plan did not specify but the gate is useless without: a
  volume floor (1,000 m³), or every rounding-scale edge trips it; and comparison
  only over *overlapping* years, or every refresh that picks up a newly
  published year fails by construction.
- **`circwood.refresh`** (`circwood-refresh`) — ingest → rebuild → carbon →
  manifest → mass balance → reconcile → vintage diff → golden-fixture update,
  emitting a markdown report for the PR body. **Nothing survives a failure:** the
  previous `data/processed` outputs are restored before the error propagates, so
  a failed refresh leaves the tree exactly as it found it.
- **`.github/workflows/refresh.yml`** — monthly cron plus `workflow_dispatch`;
  opens a PR via `gh` (no third-party action), uploads the report and opens an
  issue on failure. pytest runs *before* the refresh and again *after* it,
  rather than in the middle as planned — mid-run it is red by construction,
  since the golden fence compares the dataset to a fixture that has not been
  updated yet.
- **`validate.PROCESS_NODES` / `assert_window_balanced`** — the balance
  guarantee (process nodes, paper window, 1 %) now has one definition shared by
  the test-suite and the refresh gate.
- **Streamlit app states its vintage** — "Data as of …" from the manifest, and
  says so plainly when no manifest exists rather than showing nothing.

**The year window**

- The first live refresh blocked on `Firewood → Energy` moving **+808 %** in
  2025. Not drift: SSB 11181 had published 2025 and the other four tables had
  not, so the dataset carried a part-published year that reads as a collapse.
  The same defect ran the other way at the start — 1988–2011 had trade data with
  no harvest behind it, so species pools exported wood that never entered them.
- The dataset is now clipped to `constants.DATASET_MIN_YEAR/DATASET_MAX_YEAR`
  (2012–2024), fenced by `tests/test_golden.py`. `Export Boards`, whose only
  data was pre-2012, is gone from the dataset and `config/nodes.json` (32 → 31
  nodes); 1,064 → 638 rows.
- **The 2012–2022 numbers did not move.** Within the window the regenerated
  dataset is numerically identical to the committed one — every difference at
  floating-point epsilon — and the SSB 07410 control total is still exactly
  61,445,000 m³.
- Policy, rather than the two hard-coded integers it is today, is **Phase J**.

**Verified:** 104 tests green (Python 3.14); the full `circwood-refresh` chain
run against the live API end-to-end — mass balance worst node +0.60 %,
Treindustrien reconciliation 0.00 %, control total exact, vintage diff PASS.

## 2026-07-28 — Phase A: ingestion hardened for unattended runs

`ingest/ssb.py` rewritten so a scheduled run either produces a complete,
validated dataset or fails loudly. Previously a single
`except Exception: print(); continue` turned any fetch failure into a *partial*
dataset that the pipeline then built on silently.

- **Fetch split from parse**: `fetch_table(spec) -> str` is the only function
  touching the network; `parse_table(raw)`, `table_updated(raw)`,
  `normalize_spec`, and `validate_table` are pure and tested offline.
- **Failure policy made explicit**: configuration errors (unreadable query file,
  off-contract query, duplicate table id) raise `SSBQueryError` before any
  network call; runtime errors are collected per table and raised together as
  `SSBIngestError`. `allow_partial=True` is the only way to get a partial
  dataset, and it warns.
- **Retries**: `requests.Session` with `Retry(total=5, backoff_factor=1,
  status_forcelist=[429,500,502,503,504], respect_retry_after_header=True)` —
  and `allowed_methods` including POST, without which urllib3 would have retried
  nothing, since every SSB query here is a POST. Descriptive `User-Agent`
  identifying the project, contact overridable via `CIRCWOOD_CONTACT`.
- **Request contract enforced, not assumed**: every query must resolve to
  `outputFormat=json-stat2` and `lang=no`; missing settings are injected,
  conflicting ones raise. A wrong `lang` returns HTTP 200, parses cleanly and
  matches zero rows downstream. Responses are decoded as UTF-8 explicitly so a
  charset-less header cannot mangle the Norwegian labels the transforms filter
  on. The legacy v0 shape is held to the same contract.
- **Duplicate table ids raise** instead of one file silently overwriting another.
- **Raw responses cached** to `data/raw/ssb/{table_id}.json` *before* parsing
  (git-ignored; the unparseable body is the one you need to read).
- **Vintage captured** per table — SSB's own `updated` revision date, our pull
  timestamp, query-file SHA-256, row count and year coverage — returned on
  `IngestResult` and written to `data/raw/ssb/_vintages.json`. This is the input
  to the Phase B manifest.
- **Validation on load**: required columns present (per-table `TableContract`),
  `value` not all-NaN, year coverage spanning 2012–2022. Every message names the
  table id.
- **`logging` replaces `print`** in the ingestion module; the CLI gains
  `--verbose`.
- **Tests** (`tests/test_ssb_ingest.py`, 33 cases) against real json-stat2
  fixtures saved from SSB, plus mocked failure paths: partial failure raises,
  retry exhaustion raises, the wrong-lang assertion fires, duplicates raise,
  raw is cached even when parsing fails, and the repo's own five query files are
  checked against the contract offline. CI now installs the `ssb` extras so
  these run rather than skip.
- Verified: 61 tests green (Python 3.14); live ingestion of all five SSB tables
  validated; `build_sankey(use_cached_ssb=False)` completed end-to-end against
  the live API (committed dataset restored afterwards — regenerating it is
  Phase B's vintage-diff gate, not this phase).

## 2026-07-28 — Phase 0: audit cleanup and repo hygiene

- **One-time node-label migration** (decision logged in ROADMAP.md):
  `Loss` → `Loss Pulpmills`, `Timber Inventory` → `Timber inventory`,
  `planed timber inventory` → `Planed timber inventory`, applied to the
  committed datasets and every generator.
- **Fixed silently dropped Sankey flows** (~5.9 Mm³ over 2012–2022):
  `config/nodes.json` had drifted from the dataset, so `Pulpmills → Loss`
  (the mill's entire loss output), `Import planed wood → WPP` and
  `Firewood → Export Firewood` never rendered, and `Impregnated inventory`
  was mislabelled as `Boardmills_stock`. nodes.json rebuilt to match the
  dataset's 32 nodes exactly; ghost labels removed; the viz `NODE_RENAME_MAP`
  deleted.
- **Golden regression fence wired up**: the fixture (previously read by no
  test) was regenerated as an exact snapshot of the shipped dataset;
  `tests/test_golden.py` enforces snapshot equality and dataset ↔ nodes.json
  coverage.
- **`config/carbon_nodes.csv` drift fixed**: netted-away import nodes removed;
  the three `Net export *` nodes added with explicit fixed species/state so
  net-import years no longer fall back silently to spruce/dry.
- **Docs**: stale README/roadmap pointers fixed; completed `improvements.txt`
  deleted; roadmap files consolidated into `ROADMAP.md` + this changelog.
- **Repo hygiene**: PDFs (papers/reports) git-ignored — the repo is public.
- Verified: full test suite green locally (28 passed, Python 3.14) after
  disabling Windows Smart App Control, which had been blocking pandas' DLLs;
  mass balance within 1 % (worst node +0.60 %); SSB 07410 control total exact.

## 2026-06-28 — Researcher-adjustable sawmill coefficients

- App: "Sawmill & planing coefficients" controls — set the by-product
  percentages manually or derive the sawn yield from Treindustrien
  produced-volume data; the dataset is rebuilt from cached primary rows via
  `regenerate_balanced` with the chosen coefficients.
- `transform/sawing.py`: `SawmillCoefficients` dataclass +
  `resolve_coefficients` (manual vs Treindustrien-derived).
- SSB 03795 query file updated to PxWebApi v2 with explicit parameters.

## 2026-06-27 — Correctness: mass balance, reconciliation, CI

- **Mass balance closed** (sawing-diagnosis Issue 3): raw-log re-exports now
  bypass the mill nodes (species pools → export), sawn-side flows are
  mass-derived via `SAWN_YIELD`, and the imported-planed-wood double-count is
  fixed. Every process node balances within ~1 % over 2012–2022;
  Sawmills → Pulpmills ≈ 26 % of logs sawn. Added `circwood.validate`
  (balance report + `assert_balanced`), `assemble.assemble_sankey` /
  `regenerate_balanced` (offline re-derivation), an app balance panel, and
  balance tests. Shipped dataset regenerated. Trade flows collapsed into
  net-export nodes (`Net export skurlast` / `massevirke` / `sawn timber`).
- **Reconciliation** (`circwood.reconcile`): planed/impregnated throughput
  ties exactly to the Treindustrien CSV; domestic sawtimber matches the SSB
  07410 control total (61,445,000 m³, 2012–2022); guarded by tests.
- **CI**: GitHub Actions runs the suite on push/PR, Python 3.11–3.12.
- App: node selector (choose which nodes to display); web-deploy config
  (requirements.txt, Streamlit theme).
- **Species-accurate carbon model**: spruce/pine/non-coniferous mix tracked
  through mixing nodes to a fixed point; researcher-editable
  `config/carbon_densities.csv` + `config/carbon_nodes.csv`; carbon metric in
  the app. (Supersedes the initial hard-coded carbon overlay from the same
  day.)
- Sankey visual redesign (fixed layout, categorical palette, link opacity).

## 2026-06-25 — Refactor into a package

- Restructured the script collection into the installable `circwood` package
  (`ingest` / `transform` / `carbon` / `viz` / `pipeline`), a Streamlit app,
  centralized constants and paths, pytest suite locking in the sawing fix
  (year reindexing, coefficients on raw log input, mass-balanced residual per
  `Sawing_function_diagnosis.docx`), dead code removed.
- Baseline commit of the pre-refactor project state.
