# Circwood WP4 — Roadmap

> The single live roadmap (completed work lives in [CHANGELOG.md](CHANGELOG.md)).
> Three goals:
>
> 1. **Live** — SSB data refreshes itself, safely, without a human in the loop.
> 2. **Public** — the Sankey is an interactive static site, not a local app.
> 3. **Research** — the pipeline exports an *empirical* HWP fraction (β) that
>    feeds a forest-rotation model.
>
> Legend — **P1** do next · **P2** valuable · **P3** nice-to-have.
> Effort — **S** hours · **M** a day or two · **L** several days.

---

## 0. Context for the agent

**What exists:** clean `src/circwood` package; fixed + tested sawing function;
species-accurate carbon model driven by editable CSVs; closed mass balance
(`validate.py`) and reconciliation to published aggregates (`reconcile.py`);
golden-snapshot and node-coverage regression fences (`tests/test_golden.py`);
Streamlit app off cached CSVs; CI on 3.11–3.12; SSB API ingestion written
(`ingest/ssb.py`) but **not wired into routine use**.

**Ordering constraint that matters:** Phase C (schema dimensions) must land
**before** Phase D (web layer). Retrofitting a dimension into transforms, tests
and app state after the frontend is built is materially more expensive than
adding it now while every row is national.

**Out of scope for the agent** — these are human tasks, do not attempt or stub
them: negotiating member-level data agreements, contacting Treindustrien /
Treteknisk / NMBU, publication and preprint submission, deciding what is
commercially confidential.

**House rules:**
- Every phase ends green: `pytest` passes and `reconcile` still ties out.
  (Tests run locally again since 2026-07-28 — Smart App Control was disabled —
  and in CI on every push/PR.)
- No phase may weaken an existing guarantee. Mass balance within ~1 % and the
  SSB 07410 control total (61,445,000 m³, 2012–2022) are regression fences.
- Changes to `sankey_data.csv` must be additive and backward-compatible.
  Adding columns is fine; renaming or reordering breaks the committed dataset
  (a one-time label migration was done deliberately in Phase 0 — 2026-07-28).
  Any regeneration must update `tests/fixtures/golden_sankey_data.csv` in the
  same commit, so the diff is visible in review.
- PDFs (papers, reports) are never committed — the repo is public.

---

## Decision log

- **2026-07-28 · Deployment**: the Streamlit app was never deployed and will
  not be — it stays the internal exploration tool. The public deployment is
  the Phase D static site, chosen for robustness and so Treteknisk can adopt
  it. Streamlit Community Cloud is dropped.
- **2026-07-28 · HWP stock model**: Phase G "HWP pool as a stock" is **not**
  in the paper's scope. Kept in the backlog as P3; carbon stays an annual
  flow.
- **2026-07-28 · Node labels**: dataset labels were migrated once to canonical
  names (`Loss` → `Loss Pulpmills`, `Timber Inventory` → `Timber inventory`,
  `planed timber inventory` → `Planed timber inventory`); `config/nodes.json`
  now matches the dataset exactly and the viz rename map is gone. Enforced by
  `tests/test_golden.py`.
- **2026-07-28 · Repo hygiene**: one roadmap file (this one) plus
  `CHANGELOG.md`; PDFs are git-ignored.
- **2026-06-25** (refactor era): cached CSVs are the canonical committed
  artifact; full `src/circwood` package layout; volume first, carbon second.

---

## Phase A — Harden ingestion for unattended runs · **P1 · M**

`ingest/ssb.py` works when a human watches it. It is not safe on a schedule.

- [ ] **Split fetch from parse.** `fetch_table(spec) -> str` (network) and
      `parse_table(raw: str) -> list[DataFrame]` (pure). This is what unblocks
      testing; everything else in this phase is easier afterwards.
- [ ] **Stop swallowing failures.** The current
      `except Exception: print(); continue` produces a *partial* dataset that
      the pipeline then builds on. Collect failures, raise
      `SSBIngestError` listing them at the end. Add `allow_partial: bool = False`
      as an explicit opt-out.
- [ ] **Retries.** `requests.Session` + `HTTPAdapter(max_retries=Retry(
      total=5, backoff_factor=1, status_forcelist=[429,500,502,503,504],
      respect_retry_after_header=True))`. Set a descriptive `User-Agent`
      identifying the project and a contact address.
- [ ] **Enforce the contract the docstring already claims.** Both query formats
      must resolve to `outputFormat=json-stat2` and `lang=no`; inject or assert,
      do not assume. A wrong `lang` returns HTTP 200, parses cleanly, and yields
      *zero matching rows* downstream — the worst class of bug in this codebase.
- [ ] **Collision on duplicate table ids.** `dataset_output[table_id] = dataset`
      silently discards the first of two files targeting the same table. Either
      accumulate into a list or raise.
- [ ] **Cache raw responses.** Write `result.text` to
      `data/raw/ssb/{table_id}.json` *before* parsing. Enables offline debugging,
      vintage diffing, and test fixtures.
- [ ] **Capture vintage.** json-stat2 carries a top-level `updated` timestamp
      (SSB's own last-revision date). Record it per table alongside the pull
      timestamp — see Phase B manifest.
- [ ] **Validate on load.** Expected columns present, year coverage within
      bounds, value column not all-NaN. Fail loud with the table id in the
      message.
- [ ] **`logging` not `print`** (replaces the current `print` calls in this
      module).
- [ ] **Tests:** saved json-stat2 fixture → `parse_table` assertions; mocked
      `fetch_table` for the failure paths (partial failure raises, retry
      exhaustion raises, wrong-lang assertion fires).

**Acceptance:** `build_sankey(use_cached_ssb=False)` either produces a complete,
validated dataset or raises with a precise reason. It never returns silently
partial data.

---

## Phase B — Automate the refresh loop · **P1 · M**

Today "live" means a human remembers to rebuild and commit; that is the actual
gap between this project and a maintained one.

- [ ] **`data/processed/manifest.json`**, written by the pipeline:
      pull timestamp, per-table SSB `updated` date, table ids, query-file
      hashes, year coverage, package version, git SHA.
- [ ] **Scheduled workflow** `.github/workflows/refresh.yml` — monthly cron plus
      `workflow_dispatch`. Steps: fresh ingest → `build_sankey` →
      `build_carbon(write=True)` → `pytest` → `validate.assert_balanced` →
      `reconcile` → **vintage diff** → open a PR.
- [ ] **Vintage diff as a blocking gate.** New module
      `src/circwood/diff.py`: compare the regenerated dataset against the
      committed one per (source, target, year). Fail the job when any flow moves
      more than a configurable threshold (start at 5 %) or when a
      node/edge appears or disappears. This is the check that catches SSB
      reclassifying a code — the realistic failure mode, and one that mass
      balance alone will not detect, because a wrong-but-consistent dataset
      still balances.
- [ ] **PR, not direct commit.** CSV is the canonical committed artifact
      precisely so the diff is human-readable in review.
- [ ] **Notify on failure** so a broken fetch does not silently become stale
      data.
- [ ] **Surface vintage in every UI** — Streamlit app and static site both show
      "data as of {date}" from the manifest.

**Acceptance:** deleting `data/processed/*.csv` and running the workflow
reproduces them; a deliberately corrupted query file fails the job before
anything is committed.

---

## Phase C — Add the filter dimensions to the schema · **P1 · M**

Do this **before** Phase D. Currently every flow row is implicitly national and
all-species.

- [ ] **Extend the flow row** with `region`, `species`, `assortment`,
      `entity_id`. Populate with defaults today: `region="NO"`,
      `entity_id="national"`, `species` from the existing propagation where the
      carbon model already knows it, `assortment` where the source distinguishes
      sawtimber / pulpwood / fuelwood.
- [ ] **Species is nearly free.** `carbon/species_model.py` already propagates
      the spruce/pine/non-coniferous mix to a fixed point. Expose that as flow
      dimensions rather than recomputing it in the carbon step only.
- [ ] **Region where SSB publishes it.** Roundwood harvest and removals are
      available by fylke; carry the county through rather than aggregating on
      ingest. (The 03795 query currently selects the national `vs_Landet`
      codelist; this needs the fylke codelist.)
- [ ] **Update `validate.py` to balance per (region, species) slice**, not just
      nationally. A dataset that balances in aggregate but not per slice will
      produce nonsense the first time someone filters.
- [ ] **Migrate transforms and tests** to the widened schema; keep national
      totals byte-identical where the dimension is degenerate (the golden
      snapshot in `tests/test_golden.py` is the regression fence — update the
      fixture in the same commit as the schema change).
- [ ] **Document the reason for `entity_id`** in the schema docstring: mill-level
      data does not exist publicly and would require a data agreement, but the
      column exists so its arrival is a data change, not a rewrite.

**Acceptance:** national totals unchanged; `validate` passes per-slice;
filtering by species or region returns a coherent sub-Sankey.

---

## Phase D — Web artifact and static site · **P1 · L**

The public deployment (decision 2026-07-28): robust, host-anywhere static
output that Treteknisk can adopt — not a Streamlit process.

- [ ] **Bundle builder** `src/circwood/export/bundle.py` → a single
      `site/data/circwood.json`: flow rows (long format, all dimensions),
      `config/nodes.json` positions, palette/categories, manifest. Gzip; expect
      well under 1 MB. Do **not** reach for Parquet or DuckDB-WASM at this size.
- [ ] **Static site** in `site/`. **ECharts**, not Plotly.js — its sankey has
      the event hooks needed for click-to-cross-filter; Plotly's sankey is
      awkward to wire into custom selection.
- [ ] **Filters:** year range, single-year vs annual average, metric
      (volume Mm³ / carbon Mt C), species, region, assortment, node selector.
      All client-side over the bundle — filtering is row selection plus
      aggregation plus index remapping, roughly 100 lines. Do not precompute
      figure specs; the node-selection space is 2³³.
- [ ] **Cross-filter:** clicking a node or link filters companion charts —
      a time series of the selected flow across all years, and a per-node
      inflow/outflow/balance table. This is what makes it read as a tool rather
      than a picture.
- [ ] **URL state** — filters serialised to query params so a view is
      shareable, plus 3–4 named presets ("sawmill chain", "carbon to long-term
      use").
- [ ] **PNG/SVG export** of the current view.
- [ ] **Build + deploy in the same workflow as Phase B.** GitHub Pages for the
      public/portfolio copy; keep the build output host-agnostic so an Azure
      Static Web Apps target can be added without changes.
- [ ] **Keep the Streamlit app** as the internal exploration tool. Do not
      migrate it or delete it; it is the right shape for poking at the model and
      the wrong shape for a public report.

**Acceptance:** site loads from a cold cache in under two seconds, works with
JavaScript-only hosting, and shows the data vintage.

---

## Phase E — Confidentiality guardrails · **P1 · S** (write now, enforce later)

No member-level data exists in the repo today. Write these rules **before** any
arrives, because the failure mode is unrecoverable.

- [ ] `docs/DATA_CONFIDENTIALITY.md` stating the rules plainly:
      - **Anything shipped to the browser is public.** One bundle filtered
        client-side means any member can read every competitor's volumes in
        devtools. Member views require per-entity bundles built server-side
        behind authentication — never client-side filtering of a combined
        bundle.
      - **Suppression happens at build time, not render time.** Do not emit a
        cell derived from fewer than `MIN_ENTITIES` (default 5) contributors.
      - **Re-identification via benchmark views.** If a peer group is small and
        you are in it, you can often solve for the others. Suppress the group,
        not just the cell.
- [ ] Implement `export.bundle` with a `min_entities` guard that raises when
      `entity_id != "national"` and the guard is not satisfied. Inert today;
      correct by construction later.

---

## Phase F — Research bridge: empirical β · **P1 · M**

This is the phase that makes the project a research contribution rather than a
dashboard. Forest-rotation economics assumes a fixed harvested-wood-product
fraction (β) because measured national values do not exist. This pipeline
measures it.

- [ ] **`src/circwood/analysis/hwp.py`** — derive
      `β(year, species) = carbon reaching Long-term USE / carbon in harvest`,
      straight off the mass-balanced flows.
- [ ] **Fate of the residual**, split into pulp/paper (medium-lived), energy
      (near-immediate oxidation), and other. The rotation literature typically
      assumes the entire non-HWP fraction oxidises at harvest; this dataset
      shows a large share goes to pulp. Quantifying that gap is a result, not
      a correction.
- [ ] **Export `data/processed/beta_series.csv`** with columns
      `year, species, beta, pulp_share, energy_share, other_share,
      volume_basis, ci_low, ci_high` — a clean interface for an external
      (R) rotation model. Document the units and the carbon basis in the header.
- [ ] **Do not implement the rotation model in this repo.** It is a separate
      single-stand optimisation with a different question type (normative,
      prospective) from this pipeline (positive, retrospective). Keep them as
      two models exchanging a parameter file.
- [ ] **Sanity check against the literature value** (β ≈ 0.55 is the common
      assumption) and report the divergence in the export's docstring.

---

## Phase G — Carbon depth · **P2**

- [ ] **Uncertainty bounds · M** — low/central/high scenarios over
      carbon densities and species mixes, driven from the existing editable
      CSVs. Surface as a range in the app and site. Feeds `ci_low`/`ci_high` in
      Phase F.
- [ ] **HWP pool as a stock · L — deprioritised to P3, out of paper scope
      (decision 2026-07-28)** — model carbon *stored* in long-lived
      products accumulating and decaying year over year, rather than an annual
      flow. Use first-order decay with product-class half-lives; make them
      editable in `config/` like the densities. This is the headline climate
      result and it is also what CRCF's "carbon storage in long-lasting
      products" category concerns.
- [ ] **Import species mix · S** — `config/carbon_nodes.csv` assumes imports
      (and the net-export nodes) are 100 % spruce. Source the real split or
      document the assumption and its sensitivity.
- [ ] **CO₂-equivalents · M** — C → CO₂ conversion as a display option.
      Substitution effects are out of scope unless explicitly requested.

---

## Phase H — Forest resource layer · **P2 · M**

Closes the front boundary of the Sankey: today "standing forest" is an input
edge with no resource behind it.

- [ ] **Landsskogtakseringen via the existing PxWeb ingestion** — productive
      forest area, standing volume, increment, by region, site index (bonitet)
      and cutting class (hogstklasse). Same API and same query-file format as
      the existing tables, so this is mostly configuration.
- [ ] **Note the definitional boundary in `METHODS.md`:** SSB's productive
      forest excludes areas protected or set aside for non-forestry purposes.
      That exclusion is what makes it approximately "commercially available",
      and conflating the two is a common error.
- [ ] **Optional, heavier · L** — SR16 (NIBIO, 16 m raster/vector, NLOD-licensed
      via Geonorge) for spatial context and a map view. Treat as *modelled*
      data: per-pixel attributes carry roughly 50 % relative RMSE, so aggregate
      to municipality or county before display and never present a single
      polygon as measured truth.

---

## Phase I — Reproducibility and publication support · **P2**

- [ ] **`METHODS.md` · M** — every assumption, coefficient and source;
      the sawing-fix explanation; the carbon model; the β derivation. Reusable
      as paper supplementary information.
- [ ] **Data dictionary · S** — one table: node, flow, source table,
      coefficient, provenance tag.
- [ ] **Lockfile / Dockerfile · S** — exact reproduction of published figures.
- [ ] **Zenodo release + DOI** so the dataset and code are citable.
- [ ] **Lint + pre-commit · S** — `ruff` + `ruff format`.
- [ ] **Broader tests · M** — trade mapping, inventory routing, firewood
      split, viz remap/aggregation.
- [ ] **mypy · S**.

---

## Recommended sequence

1. **A** — ingestion hardening. Everything else assumes the pipeline can be
   trusted unattended.
2. **B** — refresh loop. Turns "reproducible" into "self-maintaining".
3. **C** — schema dimensions. Must precede D.
4. **E** — write the confidentiality rules (an hour, and cheap insurance).
5. **D** — static site. The biggest visible win.
6. **F** — β export. The research contribution.
7. **G/H/I** as the work demands.

---

## Definition of done

- A scheduled job refreshes SSB data, proves it balances and reconciles,
  diffs it against the previous vintage, and opens a PR — with no human step
  until review.
- A public static site renders an interactive, cross-filterable Sankey with
  year, species, region and node filters, and states its data vintage.
- `beta_series.csv` exports a measured HWP fraction with uncertainty bounds and
  a documented carbon basis.
- Confidentiality rules are written down and enforced in code before any
  member-level data can enter the repository.
