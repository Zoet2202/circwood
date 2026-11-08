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
Since Phase D (2026-07-28) the public deployment exists: `circwood-bundle`
builds `site/data/circwood.json` through that gate, and `site/` is a static,
host-anywhere Plotly.js site (see the decision log for why not ECharts)
deployed to GitHub Pages on merge to `main`.

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

- **2026-11-08 · Partner countries come from SSB 08799, not FAOSTAT.** The
  owner asked for FAOSTAT trade flows to see where exported wood ends up.
  Checked live: FAOSTAT's REST API answers every endpoint with
  `401 Missing Authorization Header` (Cognito login, no documented sign-up),
  and its bilateral Forestry Trade Flows domain (FT) is discontinued (last
  update 2022-05-27), so it could not cover 2021–2024. SSB 08799 is the
  by-country sibling of the 08801 table already in the pipeline and covers
  the whole window. Shipped as a sixth dimension, `destination`, additive.
  The netted export edges are attributed by *gross export share* — a
  modelling assumption, documented in `transform/destinations.py`.
- **2026-11-08 · β is production-basis** (owner decision, Phase F): β counts
  domestic harvest carbon reaching Long-term USE, traced by proportional
  attribution, so imports never inflate it and exports are their own fate.
  The roadmap's original literal ratio is kept as `beta_literal`.
- **2026-11-08 · β denominator and carbon basis** (CEO decisions in the
  agent run, Phase F): the denominator is roundwood removals
  (`Harvested wood →` the three pools); Bark and Residues are excluded because
  both are fixed 10 % coefficients, not measurements. β uses harvest-entry raw
  density × traced fractions, not the dataset's `carbon` column, which is not
  conserved (Phase G). A two-way inventory within one year is netted.
- **2026-11-08 · β refuses broken years** (owner decision: a closure gap above
  `BETA_CLOSURE_TOL`, 2 %, raises; added by the CEO after review: an output
  excess above `BETA_EXCESS_OUT_TOL`, 15 %, raises). 2024 fails (Sawmills has
  no outputs), so the export covers 2012–2023; `DATASET_MAX_YEAR` is
  unchanged and the question is logged under Phase J.
- **2026-11-08 · Shrinkage carbon documented, not fixed** (owner decision):
  `carbon_data.csv` stays unchanged; the fix is a Phase G item.
- **2026-11-08 · `ci_low`/`ci_high` ship empty** (CEO decision in the agent
  run, Phase F) with `ci_method` saying why:
  no uncertainty model exists, and definitional variants of β are not a
  confidence interval.
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
- **2026-07-28 · Plotly.js, not ECharts, for the static site** (Phase D,
  reversing this roadmap's earlier ECharts choice on evidence): the shipped
  dataset contains two-way inventory flows — `Sawmills ⇄ Timber inventory`,
  `Planingmills ⇄ Planed timber inventory`, `Impregnation ⇄ Impregnated
  inventory`, including both directions *within the same year* (2012, 2016,
  2022) — and ECharts' sankey topologically sorts its graph and throws on any
  cycle, so it cannot render this dataset at all without netting the two
  directions, which would misstate the gross deposits/withdrawals the paper's
  diagram shows. ECharts also cannot honour the hand-placed `nodes.json`
  positions (its sankey computes its own layout), while Plotly's
  `arrangement: "fixed"` reproduces the published figure exactly — the
  internal app already proves it renders this data. The event-hook advantage
  that motivated ECharts turned out minor: `plotly_click` distinguishes node
  and link clicks well enough for cross-filtering. Cost accepted: a ~1.1 MB
  gzipped vendored library (`site/vendor/plotly.min.js`, committed so deploys
  need no CDN and no build step).
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

- [ ] **2024 is not covered by every source (owner question, 2026-11-08).**
      Treindustrien's production data ends at 2023, so in 2024 `Sawmills`
      takes in 4,519,533 m³ and sends out nothing, which contradicts the
      "intersection of coverage" behind `DATASET_MAX_YEAR = 2024`. The fences
      miss it because the balance guarantee covers 2012–2022 only. β refuses
      2024 on its closure-gap gate and exports 2012–2023
      (`constants.BETA_MAX_YEAR`). Whether to narrow the dataset window is the
      owner's call.

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

## Phase D — Web artifact and static site · **DONE 2026-07-28**

Delivered; details in [CHANGELOG.md](CHANGELOG.md). The public deployment:
robust, host-anywhere static output in `site/`, deployed to GitHub Pages by
`.github/workflows/site.yml` on every merge to `main` that touches the data or
the site.

- **Bundle builder** — `export.bundle.build_bundle` (`circwood-bundle`)
  serialises `carbon_data.csv` (volume + carbon in one table), the
  `nodes.json` positions and the manifest vintage into
  `site/data/circwood.json` (+ `.json.gz`), columnar with index-coded
  categoricals: 69 KB / 17 KB gz for the full dataset. Every frame goes
  through `assert_publishable` first, and `entity_id` never reaches the
  bundle. Node coverage is checked exactly, both ways, like the golden fence.
- **One deviation, forced by the data: Plotly.js, not ECharts** — see the
  decision log entry. Cycles and fixed positions, not preference.
- Filters (year range / single year vs average, metric, species, region,
  assortment, node focus) are all client-side; click-to-cross-filter renders
  a per-year time series and an inflow/outflow/balance table; URL query-param
  state plus four presets; PNG/SVG export; light/dark theming; a data-table
  fallback for everything the charts show.
- The Streamlit app stays, untouched, as the internal tool.

**Acceptance met:** total cold-cache payload ≈ 1.4 MB gzipped (dominated by
the vendored plotly.min.js), well under two seconds on ordinary broadband;
plain static hosting with no build step; the footer states the data vintage
from the manifest ("Data as of …, oldest source table …").

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

## Phase F — Research bridge: empirical β · **DONE 2026-11-08**

This is the phase that makes the project a research contribution rather than a
dashboard. Forest-rotation economics assumes a fixed harvested-wood-product
fraction (β) because measured national values do not exist. This pipeline
measures it. Details in CHANGELOG (2026-11-08).

- [x] **`src/circwood/analysis/hwp.py`** — derives β(year, species) on a
      **production basis** (decision 2026-11-08): the share of *domestic
      harvest* carbon that reaches Long-term USE, traced through the
      mass-balanced flows by proportional (absorbing-Markov) attribution.
      The literal wording first written here — carbon reaching Long-term USE /
      carbon in harvest — counts imported wood in the numerator and drops
      exported harvest; it is kept as the extra column `beta_literal`.
- [x] **Fate of the residual**, split into pulp/paper (`pulp_share`), energy
      (`energy_share`, near-immediate oxidation) and other (exports, losses,
      shrinkage, stock change, closure gap — each also its own column). Shares
      sum to 1 per row.
- [x] **Export `data/processed/beta_series.csv`** with the columns
      `year, species, beta, pulp_share, energy_share, other_share,
      volume_basis, ci_low, ci_high` first, then the extra columns; units,
      carbon basis, denominator and method in `#` header lines. Re-exported by
      `circwood-refresh`. `ci_low`/`ci_high` are empty until Phase G.
- [x] **Do not implement the rotation model in this repo.** It is a separate
      single-stand optimisation with a different question type (normative,
      prospective) from this pipeline (positive, retrospective). Keep them as
      two models exchanging a parameter file.
- [x] **Sanity check against the literature value** (β ≈ 0.55 is the common
      assumption): the divergence is computed into the export header; the
      module docstring gives the range. Which product pool the 0.55 denotes
      has not been checked against the sources; framing β against it is for
      the authors.

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
- [ ] **Carbon conservation at process nodes · M** (found in the Phase F
      review, 2026-11-08; documented, not fixed, by owner decision). The
      `carbon` column switches raw/dry density at process nodes, so carbon is
      not conserved (Sawmills, Pulpmills, Boardmills); `Timber inventory`
      values sawn-timber deposits at raw density; and the `Shrinkage` edge —
      water loss per `constants.SHRINKAGE` — carries about 0.556 Mt C over
      2012–2024. β does not use the `carbon` column for this reason. Fixing it
      changes `carbon_data.csv`, so it wants owner review. Add a
      carbon-conservation fence on `PROCESS_NODES` with the fix.
- [ ] **One-at-a-time sensitivity of β · S** to the fixed downstream
      coefficients (sawing splits, `BOARDMILL_LOSS`, the pulp split,
      `WPP_LONG_TERM`) — β is only partly empirical while these are point
      values.
- [ ] **Import species mix · S** — `config/carbon_nodes.csv` assumes imports
      (and the net-export nodes) are 100 % spruce. Source the real split or
      document the assumption and its sensitivity.
- [ ] **CO₂-equivalents · M** — C → CO₂ conversion as a display option.
      Substitution effects are out of scope unless explicitly requested.

---

## Phase K — Dataset defects found in the Phase F review · **P1 · S–M**

Found by the 2026-11-07/08 project review. Each changes the committed dataset
or the published site, so each wants an owner decision and a golden-fixture
update in the same commit. None is fixed yet.

- [ ] **Firewood exports counted twice** (confirmed). `Firewood → Energy` is
      set to all Firewood inflow and `Firewood → Export Firewood` (08801) is
      emitted on top: in 2020 Firewood takes in 2,006,304 m³ and sends out
      2,085,882 m³. Firewood is not in `validate.PROCESS_NODES`, and β's
      output-excess gate tolerates it (largest 7.8 %, 2023, under 15 %), so
      no gate fails on it. β is unaffected (the trace treats the excess as exogenous);
      only the split between β's `energy_share` and `export_firewood_share`
      would move, by at most 0.0056 in 2012–2023. A
      human decides whether 11181 is domestic consumption (then exports come
      out of the inflow) or production. (`transform/assemble.py`,
      `ingest/trade.py`)
- [ ] **End-of-Life volume may be ~1000× too small** (unconfirmed — the raw
      10513 response is not on disk). `assemble.py` converts with
      `value * 1000 / 483`; if the unit is 1000 t, that is 10³ short. Check
      the SSB unit first; also move the 483 kg/m³ into `constants.py`.
- [ ] **The site's "annual average" is not balanced.** `prepare_flows`
      averages each edge over only the years it has data (WPP −6.7 % over
      2012–2022). Average over the whole window instead, or label it.
- [ ] **`manifest.json` is stale** — 638 rows recorded against 1,878 shipped,
      an old branch, `git_dirty: true`. Regenerate it with the next dataset
      build.
- [ ] **Doc drift** — status and install notes in README.md, the End-of-Life
      routing and schema sections of `SANKEY_GUIDE.md`, and a missing Phase E
      heading in CHANGELOG.md.

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
5. ~~**D** — static site.~~ Done 2026-07-28.
6. ~~**F** — β export.~~ Done 2026-11-08.
7. **K** — dataset defects from the Phase F review. **Next** (owner decisions
   first).
8. **J** — year-window and drift policy. Shelved 2026-07-28; pick it up before
   handing the refresh loop over.
9. **G/H/I** as the work demands.

---

## Definition of done

- A scheduled job refreshes SSB data, proves it balances and reconciles,
  diffs it against the previous vintage, and opens a PR — with no human step
  until review.
- A public static site renders an interactive, cross-filterable Sankey with
  year, species, region and node filters, and states its data vintage.
- `beta_series.csv` exports a measured HWP fraction with uncertainty bounds and
  a documented carbon basis. (Done except the bounds, which wait on Phase G.)
- Confidentiality rules are written down and enforced in code before any
  member-level data can enter the repository.
