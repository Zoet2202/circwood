# Changelog

Completed work, newest first. The live plan is [ROADMAP.md](ROADMAP.md).

> Roadmap lineage: the original `ROADMAP.md` (2026-06 refactor) was followed by
> `ROADMAP_NEXT.md` (improvements backlog) and `ROADMAP_CONSOLIDATION.md`
> (live/public/research reordering). On 2026-07-28 these were consolidated:
> the open plan became the single `ROADMAP.md`, and the completed items moved
> here. Old item numbers (A1, D1, …) referenced in commit messages refer to
> those superseded files.

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
