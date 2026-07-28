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
Streamlit app off cached CSVs; CI on 3.11–3.12 with the `ssb` extras; SSB API
ingestion hardened for unattended runs (Phase A, 2026-07-28) — fetch split from
parse, contract-enforced queries, retries, raw-response cache, per-table vintage,
validate-on-load, and no path that returns silently partial data; and an
automated refresh loop (Phase B, 2026-07-28) — `circwood-refresh` plus a monthly
workflow that rebuilds, gates on mass balance, reconciliation and a vintage
diff, and opens a PR. The dataset covers **2012–2024**, the years every SSB
source table covers (`constants.DATASET_MIN_YEAR/MAX_YEAR`). Since Phase C
(2026-07-28) every flow row carries the four filter dimensions — `region`,
`species`, `assortment`, `entity_id` — with national totals unchanged and a
per-slice balance gate; the schema Phase D filters on already exists. Since
Phase E (2026-07-28) the public export path has its confidentiality gate:
`export.bundle.assert_publishable` refuses any member-level row, per
`docs/DATA_CONFIDENTIALITY.md` — inert today, since every row is national.

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

- **2026-07-28 · Phase J shelved** (user decision): the year-window/drift
  policy work is deferred until later in the project — it is not crucial for
  progress, and setting the per-table lag expectations and drift thresholds
  first needs a sit-down analysis of why and when the individual source tables
  drift. The interim protections stand: the dataset stays clipped to
  `DATASET_MIN/MAX_YEAR` and the vintage-diff gate still blocks. Phase C was
  pulled forward in its place.
- **2026-07-28 · Region semantics** (Phase C): counties come from SSB 03795 via
  the `agg_KommFylker` codelist — the whole series backcast into the **2024
  county boundaries** — so the region dimension is immune to the 2018–2024
  county reforms. `region="NO"` means *not regionally attributed* (the
  nationally-estimated 11181 firewood component, and everything downstream of
  the pools), never "rest of Norway". `dimensions.FYLKE_CODES` is a closed map:
  an unknown county label fails the run, like a new node.
- **2026-07-28 · Dataset year window**: the shipped dataset is clipped to the
  years *every* source table covers — 2012–2024 today
  (`constants.DATASET_MIN_YEAR/DATASET_MAX_YEAR`). Rows outside that
  intersection are a partial picture, not extra data: 1988–2011 carried trade
  with no harvest behind it, and a part-published 2025 made firewood look like
  it grew 808 %. Consequence: the `Export Boards` node, whose only data was
  pre-2012, is gone from the dataset and from `config/nodes.json`. Widening the
  window requires every table to have published the new year — see Phase J.
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

## Phase A — Harden ingestion for unattended runs · **DONE 2026-07-28**

Delivered; details in [CHANGELOG.md](CHANGELOG.md). Acceptance met:
`build_sankey(use_cached_ssb=False)` was run end-to-end against the live API and
either completes with a validated dataset or raises with a precise reason — it
has no path that returns silently partial data. The API for Phase B to build on:

- `ingest.ssb.process_query_files(dir, *, allow_partial=False, session=None,
  cache_dir=None)` → `IngestResult(datasets, vintages, failures)`.
- `IngestResult.vintages[table_id]` is a `TableVintage` carrying SSB's `updated`
  revision date, our `fetched_at`, `query_sha256`, `rows`, `year_min/max` —
  i.e. most of the manifest Phase B has to write, already collected.
- Errors: `SSBQueryError` (config, pre-network), `SSBIngestError` (aggregate
  runtime failures), `SSBParseError`, `SSBValidationError`.

**Follow-up left open** (not Phase A scope, but noted while in there):

- The committed `sankey_data.csv` is an older vintage than the live tables (a
  fresh run now spans 1988–2025). Regenerating it is Phase B's job, behind the
  vintage-diff gate.

Two things that looked like bugs and are not:

- **SSB 10513 is incineration-only by design.** The query selects
  `AvfallsBehand=21` ("Levert til forbrenning") because that is the current
  reality — Norwegian waste wood is effectively all incinerated — and the edge
  exists to quantify the volume that *may* be recovered. So the whole
  End-of-Life stream routes to Energy and there is no `Material recovery` node;
  the recoverable potential is read off the Energy edge. The dead
  `"Levert til materialgjenvinning"` branch has been replaced by a guard that
  raises if the query is ever widened without adding explicit routing.
- **`pyjstat.from_json_stat` is deprecated** upstream in favour of
  `Dataset.read()`. Migrating (or a pyjstat version bump) could silently change
  the parsed column names the transforms index by, so it is now fenced:
  `tests/test_ssb_parse_golden.py` snapshots the parse output of the committed
  raw responses and pins the year-is-a-string dtype contract. Migrate behind
  that fence, regenerating the snapshot in the same commit.

---

## Phase B — Automate the refresh loop · **DONE 2026-07-28**

Delivered; details in [CHANGELOG.md](CHANGELOG.md). `circwood-refresh` runs the
whole chain and the monthly workflow opens a PR. What Phase J below exists for
is the one thing this phase surfaced rather than solved.

Deviation from the plan, deliberate: the roadmap put `pytest` between the
rebuild and the data checks. That cannot pass — `tests/test_golden.py` compares
the shipped dataset to the committed fixture, so it is red by construction from
the moment the dataset is regenerated until the fixture is updated. The workflow
runs pytest *before* the refresh (the code is sound against the committed data)
and again *after* it (the regenerated dataset and its refreshed fixture agree).

**Acceptance met:** a from-scratch build reproduces the outputs (a missing
committed dataset is the documented baseline case, not an error); an
off-contract query file fails in `load_query_spec` before a single network call,
so nothing is written; a failed gate restores the previous outputs before the
error propagates.

**One thing that cannot be automated, by design:** `config/nodes.json` holds
hand-placed diagram positions, so a refresh that introduces a *new node* cannot
place it. That refresh fails the post-refresh `pytest` (node-coverage fence) and
opens an issue. Correct: a new node is a modelling change and wants a human.

---

## Phase J — Year-window and vintage-drift policy · **P2 · M — SHELVED 2026-07-28**

Deferred by decision (see the decision log): not crucial for progress, and the
lag/threshold design needs a real analysis of how each source table actually
drifts. Pick it back up before handing the refresh loop to Treteknisk. The
interim fix (the clipped year window plus the diff gate) remains in force.

New 2026-07-28, from what Phase B's first real run found.

**What happened.** The first live refresh blocked on six flows, the worst
`Firewood → Energy` in 2025 at **+808 %**. None of it was drift in the sense the
gate was built for. SSB table 11181 (firewood) had published 2025; 07410, 03795,
08801 and 10513 had not. The dataset was carrying years that only *some* source
tables covered, and a part-published year reads exactly like a collapse. The
same defect ran the other way at the start: 1988–2011 had trade data with no
harvest behind it, so species pools exported wood that never entered them.

**What was done as the interim fix.** The dataset is now clipped to the years
every source table covers — `constants.DATASET_MIN_YEAR/DATASET_MAX_YEAR`,
today 2012–2024 — fenced by `tests/test_golden.py`. Within that window the
regenerated dataset is numerically identical to the committed one (every
difference at floating-point epsilon; the 2012–2022 control total is still
exactly 61,445,000 m³), and the full refresh chain passes including the diff
gate. So the drift is gone, but the *policy* is two hard-coded integers that a
human has to remember to bump.

**Proposed solution:**

- [ ] **Derive the window, then assert it.** Compute the intersection window at
      ingest from `IngestResult.vintages` (which already carries `year_min` /
      `year_max` per table) and compare it against the committed constants.
      Disagreement fails the run with "10513 now covers 2025; widen
      DATASET_MAX_YEAR". Derived so it is never stale, asserted so widening the
      window stays a reviewed one-line diff rather than a silent change in
      coverage.
- [ ] **Per-table publication lag.** 10513 (waste) trails roundwood by roughly
      18 months; that is normal, not a fault. Record the expected lag per table
      alongside its `TableContract`, so "10513 has no 2025 yet" is an expected
      state and "10513 has no 2024 either" is an alert. Without this, the
      derived window silently pins the whole dataset to the slowest table
      forever and nobody notices when that table stops publishing.
- [ ] **Separate revision drift from reclassification.** Both look like a moved
      flow; only one is alarming. The manifest already stores each table's SSB
      `updated` date — if a table's `updated` is unchanged but its numbers moved,
      the change came from *our* query or transform, not from SSB, and should be
      reported as a different and more serious class of failure.
- [ ] **A route from blocked to accepted.** `circwood-refresh --accept-drift`
      exists for a human who has reviewed a blocked refresh. Wire it to a
      `workflow_dispatch` input so acceptance is a reviewed button press with an
      audit trail, not someone running the pipeline on a laptop.
- [ ] **Reconsider the threshold once there is history.** 5 % is a guess made
      before any real refresh had run. After a few cycles, set it from the
      observed distribution of routine SSB revisions — and consider a tighter
      threshold inside the paper window (2012–2022) than outside it, since those
      are the numbers that get published.

**Acceptance:** a table publishing a new year ahead of the others does not
change the shipped dataset; widening the window is a reviewed one-line diff that
the pipeline itself proposes; a blocked refresh has a documented, auditable
route to acceptance.

---

## Phase C — Add the filter dimensions to the schema · **DONE 2026-07-28**

Delivered; details in [CHANGELOG.md](CHANGELOG.md). Acceptance met: national
totals unchanged (the 07410 control total is still exactly 61,445,000 m³ and
pure flows kept their volumes bit-for-bit), `validate.assert_slices_balanced`
gates every (region, species) slice of every process node in the refresh
chain, and filtering by species or county returns a coherent sub-Sankey.
What Phase D builds on:

- Schema: `pipeline.SANKEY_COLUMNS` — the five national columns plus `region`,
  `species`, `assortment`, `entity_id`; `transform/dimensions.py` documents
  every dimension's semantics. `add_dimensions` / `collapse_national` are
  exact inverses (token-based origin merge; fenced round-trip test).
- Species: mixed flows split by the carbon model's fixed-point propagation
  (`species_model.flow_species_shares`). The per-slice fence exposed a real
  bug — inventory-withdrawal years carried a ⅓/⅓/⅓ mix — fixed by seeding
  each year's fixed point with the previous year's (withdrawals now carry the
  mix the wood went in with; total carbon moved −0.07 %).
- Region: county harvest from 03795 via `agg_KommFylker` (2024 boundaries
  backcast — reform-proof), committed as `data/raw/harvest_by_fylke.csv` and
  refreshed by the `03795_fylke` query. Harvest edges split into exact county
  rows plus a national remainder (the 11181 firewood estimate); see the
  decision log for what `region="NO"` means.

Left open, deliberately: 07410 removals are also published regionally — the
mill-input edges could get the same treatment if Phase D wants regional
filtering deeper than the harvest front. Do it the same way (separate query
id, committed CSV, exact split with a loud tie-out guard).

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

## Phase E — Confidentiality guardrails · **DONE 2026-07-28**

Delivered; details in [CHANGELOG.md](CHANGELOG.md). The rules exist before any
member-level data does, because the failure mode is unrecoverable. What Phase D
builds on:

- `docs/DATA_CONFIDENTIALITY.md` states the rules: anything shipped to the
  browser is public (member views need per-entity bundles built server-side
  behind authentication, never client-side filtering of a combined bundle);
  suppression happens at build time against `MIN_ENTITIES` (5); small peer
  groups are suppressed as groups, not cells; member data and entity ids never
  enter this public repo — not even in error messages.
- `circwood.export.bundle.assert_publishable(df, *, min_entities=5)` — the
  gate **Phase D's bundle builder must route every frame through before
  serialising**. National frames pass untouched; any `entity_id != "national"`
  row raises `ConfidentialityError`, reporting counts (rows, entities, cells
  below the floor) and never ids — fenced by `tests/test_export_guard.py`.
  Inert today by construction (every row is national), not by being off.

One deliberate strengthening over the plan: the guard refuses per-entity rows
*unconditionally*, not only when `min_entities` is unsatisfied — a public
bundle containing per-entity rows leaks in devtools no matter how many
entities contribute. `min_entities` licenses build-time *aggregates*; the
refusal message counts the cells that fall below it so an aggregation step
knows what to suppress.

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

1. ~~**A** — ingestion hardening.~~ Done 2026-07-28.
2. ~~**B** — refresh loop.~~ Done 2026-07-28.
3. ~~**C** — schema dimensions.~~ Done 2026-07-28.
4. ~~**E** — confidentiality rules.~~ Done 2026-07-28.
5. **D** — static site. The biggest visible win. **Next.**
6. **F** — β export. The research contribution.
7. **J** — year-window and drift policy. Shelved 2026-07-28; pick it up before
   handing the refresh loop over.
8. **G/H/I** as the work demands.

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
