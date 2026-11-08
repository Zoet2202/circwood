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

**What exists (2026-11-08):** every phase below is delivered. The clean
`src/circwood` package builds a tidy, mass- and carbon-balanced flow table
(3,723 rows, 31 nodes, 2012–2024, five filter dimensions) from Statistics
Norway and the Treindustrien annual report; `circwood-refresh` regenerates it
monthly behind gates (mass balance, reconciliation, a vintage diff with
attribution, a derived year window) and opens a PR; the public static site
renders it; the research layer exports β with a sensitivity envelope, an HWP
carbon stock, the forest-inventory resource, an economic valuation, and two
forward models (rotation economics with the empirical β; a stock projection)
that live under `models/` and exchange files with the pipeline. The dataset
vintage is SSB data as of 2026-10-15. What each phase delivered and why is in
the CHANGELOG; the decisions are below.

**Out of scope for the agent** — human tasks, do not attempt or stub them:
negotiating member-level data agreements, contacting Treindustrien /
Treteknisk / NMBU, publication and preprint submission, deciding what is
commercially confidential, minting the Zenodo DOI, choosing price sources and
their licensing.

**House rules:**
- Every phase ends green: `pytest` passes and `reconcile` still ties out.
- No phase may weaken an existing guarantee. Mass balance within ~1 % (volume
  and carbon) and the SSB 07410 control total (61,445,000 m³, 2012–2022) are
  regression fences.
- Changes to `sankey_data.csv` must be additive and backward-compatible.
  Adding columns is fine; renaming or reordering breaks the committed dataset.
  Any regeneration must update `tests/fixtures/golden_sankey_data.csv` in the
  same commit, so the diff is visible in review.
- PDFs (papers, reports) are never committed — the repo is public.

---

## Decision log

- **2026-11-08 · Whole-job handover (owner).** "Implement the remaining items
  in ROADMAP.md; don't stop for questions; I trust your decision in this
  session." The decisions below of this date were taken by the session under
  that handover and are listed back here; the work was left uncommitted for
  review.
- **2026-11-08 · End-of-Life unit confirmed and fixed (Phase K).** SSB 10513
  publishes 1 000 tonnes (json-stat2 `unit`), so the shipped flow was 1 000×
  too small. `EOL_UNIT_KG` and `WASTE_WOOD_DENSITY_KG_M3` (483) live in
  `constants.py`.
- **2026-11-08 · SSB 11181 is removals (Phase K).** Its label is "Avvirkning
  av vedvirke" (with bark, from consumption surveys, sold and own use), so the
  pool → Firewood edges are production and `Firewood → Energy` is the residual
  after exports; Firewood joined `validate.PROCESS_NODES`.
- **2026-11-08 · The annual average divides by the window's years** (Phase K),
  in `viz.prepare_flows` and the site alike, so the averaged diagram balances.
- **2026-11-08 · Dataset regenerated from live SSB with `--accept-drift`**
  after an offline review of the full diff: outside the two Phase K fixes
  every cell matched to 1e-15 and no SSB table had been revised since the
  committed vintage. The 08799 partner data (never committed before) came
  with it. Both nodes `End-of-Life` and `Energy` were repositioned in
  `nodes.json` to make room for the now-visible band — not a new node.
- **2026-11-08 · Carbon follows the wood (Phase G).** Carbon enters at
  harvest and imports and is conserved through every node at one effective
  density; water carries none. The `state` of a propagate node is
  documentation. β traces conserved carbon; its pinned values were re-derived
  in the same change; the volume trace stays as the cross-check.
- **2026-11-08 · Uncertainty is an assumption envelope, not a confidence
  interval (Phase G).** `ci_low`/`ci_high` are the root-sum-square of
  one-at-a-time coefficient deviations over `config/coefficient_ranges.csv`
  (indicative ranges, to be refined by the authors); the ±10 % density
  envelope is run alongside and shown to cancel in β (a uniform scaling leaves
  every ratio unchanged); the fully correlated linear bound is reported beside
  the RSS one; `ci_method` says so on every row.
- **2026-11-08 · HWP stock exported with two initialisations (Phase G):**
  cohort (the window's own products) and steady state; IPCC default
  half-lives, editable. The stock window is β's (2012–2023) for the same
  2024 reason.
- **2026-11-08 · Phase J policy.** Lost year coverage blocks a refresh; new
  coverage is proposed in the PR body, never imposed; an overdue table is an
  alert; a move no upstream change explains is its own failure class
  (`UnexplainedDriftError`); a table new to the manifest explains nothing;
  `accept_drift` is a `workflow_dispatch` button with the actor in the audit
  trail; 2 % inside the paper window, 5 % outside, until the history file has
  enough cycles to set them from.
- **2026-11-08 · Forest layer keyed on codes (Phase H);** nine NFI regions
  crosswalked to the county codes; the headline harvest/increment ratio is
  against the increment on productive forestry land (0.55), with the
  all-land ratio (0.49) alongside.
- **2026-11-08 · Economic layer prices only what SSB publishes (Phase L):**
  roundwood at 07413, energy at 08360 × 753 kWh/m³, carbon at a labelled
  assumed price. Processed products are not priced; that needs a human
  decision on sources and licensing.
- **2026-11-08 · The forward models live in `models/` (Phase L),** outside the
  package, exchanging CSV files, per the Phase F decision; the rotation
  model's default growth curve is calibrated to the thesis's two stated facts
  because its SiTree numbers do not survive in the text; the R script was run
  later the same day and its curve is compared beside the calibrated one (the
  decision below).
- **2026-11-08 · Repo-wide `ruff format` deferred** to its own commit so the
  formatter's diff never mixes with a data regeneration; `ruff check` and
  `mypy` are clean and in CI; pre-commit formats touched files only.
- **2026-11-08 · Review council findings applied** (three independent
  read-only reviews, every finding recomputed first). The social rotation
  model priced carbon uptake on a growing path and the harvest release on a
  constant one; on one path — the growing price of eq. 19, as in the CRC
  model — the social optimum is 107 years, not 125 (106 at a constant price),
  which is where the thesis found it. A derived edge is now explained only by
  its *own* upstream tables or input (`diff._DERIVED_UPSTREAM`), so the
  monthly partner table cannot excuse an edit to `WPP_LONG_TERM`. The first
  history line was re-derived with the shipped classifier and says so. The
  manifest records the price file in the vintage that wrote it; the failure
  report carries the window, diff and attribution; `_vintages.json` merges
  across layers; the sensitivity method states that the uniform density
  envelope cancels and reports the correlated linear bound. Version 0.2.0.
- **2026-11-08 · No licence is claimed until the owners choose one.**
  `CITATION.cff` and `.zenodo.json` carried `license: MIT` with no LICENSE
  file; the field is removed (a claim without the text is all-rights-reserved,
  and Zenodo would publish it). Choosing the licence is in *What remains*.
- **2026-11-08 · The SiTree run is a comparison, not the primary curve.** R
  arrived the same day; the thesis's set-up (`stand.west`, the call the
  package documents, 250 years, no management) was run with sitree 0.1.15
  and committed. The example stand starts already stocked (about 40 years
  old; its ages are shifted by that) and its 6 400 stems/ha push the growth
  functions to basal areas of 190–280 m²/ha, so its absolute volumes are not
  credible; the calibrated logistic stays primary and the SiTree curve is
  reported beside it (`growth curve: sitree example stand`), switchable in
  `models/rotation/parameters.csv`.
- **2026-11-08 · Partner countries come from SSB 08799, not FAOSTAT.** The
  owner asked for FAOSTAT trade flows to see where exported wood ends up.
  Checked live: FAOSTAT's REST API answers every endpoint with
  `401 Missing Authorization Header` (Cognito login, no documented sign-up),
  and its bilateral Forestry Trade Flows domain (FT) is discontinued (last
  update 2022-05-27), so it could not cover 2021–2024. SSB 08799 is the
  by-country sibling of the 08801 table already in the pipeline and covers
  the whole window. Shipped as the fifth dimension column, `destination`, additive.
  The netted export edges are attributed by *gross export share* — a
  modelling assumption, documented in `transform/destinations.py`.
- **2026-11-08 · β is production-basis** (owner decision, Phase F): β counts
  domestic harvest carbon reaching Long-term USE, traced by proportional
  attribution, so imports never inflate it and exports are their own fate.
  The roadmap's original literal ratio is kept as `beta_literal`.
- **2026-11-08 · β denominator and carbon basis** (CEO decisions in the
  agent run, Phase F): the denominator is roundwood removals
  (`Harvested wood →` the three pools); Bark and Residues are excluded because
  both are fixed 10 % coefficients, not measurements. A two-way inventory
  within one year is netted. (The carbon basis changed to conserved carbon
  with Phase G, above.)
- **2026-11-08 · β refuses broken years** (owner decision: a closure gap above
  `BETA_CLOSURE_TOL`, 2 %, raises; added by the CEO after review: an output
  excess above `BETA_EXCESS_OUT_TOL`, 15 %, raises). 2024 fails (Sawmills has
  no outputs), so the export covers 2012–2023; `DATASET_MAX_YEAR` is
  unchanged.
- **2026-11-08 · Economic value is in scope** (owner request): add an
  economic layer and a forward wood-stock projection (Phase L), building on
  the owner's 2023 master's thesis, with SiTree for growth and SSB 14594 for
  today's productive forest. The rotation and growth models stay outside this
  repo's package as separate models exchanging files, per the Phase F decision.
- **2026-11-08 · No stop to the dataset** (owner decision): the dataset
  window stays 2012–2024 for the current model even though Treindustrien
  ends at 2023; β exports 2012–2023 because its closure gate refuses 2024.
- **2026-11-08 · `ci_low`/`ci_high` shipped empty in Phase F** (CEO decision
  in the agent run) with `ci_method` saying why; filled by Phase G above.
- **2026-07-28 · Phase J shelved** (user decision; unshelved and delivered
  2026-11-08): the year-window/drift policy work was deferred until the
  per-table drift could be analysed. The interim protections (clipped window,
  diff gate) stood until then.
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
  window is the reviewed one-line diff Phase J now proposes.
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
- **2026-07-28 · HWP stock model**: Phase G "HWP pool as a stock" was judged
  outside the paper's scope and kept as P3; delivered 2026-11-08 as a derived
  file beside the flow table, which stays an annual flow.
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
has no path that returns silently partial data. The API Phase B built on:

- `ingest.ssb.process_query_files(dir, *, allow_partial=False, session=None,
  cache_dir=None)` → `IngestResult(datasets, vintages, failures)`;
  `ingest.ssb.load_cached_result(cache_dir)` (2026-11-08) rebuilds the same
  result offline from the raw cache.
- `IngestResult.vintages[table_id]` is a `TableVintage` carrying SSB's `updated`
  revision date, our `fetched_at`, `query_sha256`, `rows`, `year_min/max`.
- Errors: `SSBQueryError` (config, pre-network), `SSBIngestError` (aggregate
  runtime failures), `SSBParseError`, `SSBValidationError`.

Two things that looked like bugs and are not:

- **SSB 10513 is incineration-only by design.** The query selects
  `AvfallsBehand=21` ("Levert til forbrenning") because that is the current
  reality — Norwegian waste wood is effectively all incinerated — and the edge
  exists to quantify the volume that *may* be recovered. So the whole
  End-of-Life stream routes to Energy and there is no `Material recovery` node;
  the recoverable potential is read off the Energy edge. A guard raises if the
  query is ever widened without adding explicit routing.
- **`pyjstat.from_json_stat` is deprecated** upstream in favour of
  `Dataset.read()`. Migrating (or a pyjstat version bump) could silently change
  the parsed column names the transforms index by, so it is fenced:
  `tests/test_ssb_parse_golden.py` snapshots the parse output of the committed
  raw responses and pins the year-is-a-string dtype contract. Migrate behind
  that fence, regenerating the snapshot in the same commit.

---

## Phase B — Automate the refresh loop · **DONE 2026-07-28**

Delivered; details in [CHANGELOG.md](CHANGELOG.md). `circwood-refresh` runs the
whole chain and the monthly workflow opens a PR.

Deviation from the plan, deliberate: the roadmap put `pytest` between the
rebuild and the data checks. That cannot pass — `tests/test_golden.py` compares
the shipped dataset to the committed fixture, so it is red by construction from
the moment the dataset is regenerated until the fixture is updated. The workflow
runs pytest *before* the refresh (the code is sound against the committed data)
and again *after* it (the regenerated dataset and its refreshed fixture agree).

**One thing that cannot be automated, by design:** `config/nodes.json` holds
hand-placed diagram positions, so a refresh that introduces a *new node* cannot
place it. That refresh fails the post-refresh `pytest` (node-coverage fence) and
opens an issue. Correct: a new node is a modelling change and wants a human.

---

## Phase J — Year-window and vintage-drift policy · **DONE 2026-11-08**

Shelved 2026-07-28, delivered 2026-11-08; details in the CHANGELOG and in
[docs/REFRESH_POLICY.md](docs/REFRESH_POLICY.md), which is the handover
document for whoever runs the loop.

- [x] **Derive the window, then assert it** — `circwood.coverage.coverage_report`
      from `IngestResult.vintages`; lost coverage raises `WindowError`, new
      coverage is a proposal in the PR body.
- [x] **Per-table publication lag** — `TableContract.publication_lag_months`,
      six months' grace, overdue reported; Treindustrien's file reported as
      an input.
- [x] **Separate revision drift from reclassification** — `diff.attribute_changes`
      classes every breaching flow; `UnexplainedDriftError` for moves nothing
      upstream explains; the manifest records input-file hashes (schema 2).
- [x] **A route from blocked to accepted** — `accept_drift` as a
      `workflow_dispatch` input with the actor in the PR body and commit.
- [x] **Reconsider the threshold once there is history** — 2 % inside the
      paper window, 5 % outside, and `refresh_history.jsonl` to set them from;
      the first entry is the 2026-11-08 regeneration itself.
- [x] **2024 is not covered by every source — decided 2026-11-08: no stop.**

**Acceptance met:** a table publishing a new year ahead of the others does
not change the shipped dataset; widening the window is a one-line diff the
pipeline proposes; a blocked refresh has a documented, auditable route to
acceptance.

---

## Phase C — Add the filter dimensions to the schema · **DONE 2026-07-28**

Delivered; details in [CHANGELOG.md](CHANGELOG.md). Schema:
`pipeline.SANKEY_COLUMNS` — the five national columns plus `region`,
`species`, `assortment`, `entity_id` and (2026-11-08) `destination`;
`transform/dimensions.py` documents every dimension's semantics;
`add_dimensions` / `collapse_national` are exact inverses. Left open then and
still open: 07410 removals are also published regionally — the mill-input
edges could get the same treatment if anyone wants regional filtering deeper
than the harvest front.

---

## Phase D — Web artifact and static site · **DONE 2026-07-28**

Delivered; details in [CHANGELOG.md](CHANGELOG.md). Since 2026-11-08 the site
also offers CO₂e, shows the carbon envelope and a forest-resource card, and
lists every source table in its footer; the model results stay off it until
the owner has reviewed them.

---

## Phase E — Confidentiality guardrails · **DONE 2026-07-28**

Delivered; details in [CHANGELOG.md](CHANGELOG.md) and
[docs/DATA_CONFIDENTIALITY.md](docs/DATA_CONFIDENTIALITY.md). Inert today by
construction (every row is national), not by being off.

---

## Phase F — Research bridge: empirical β · **DONE 2026-11-08**

Delivered; details in [CHANGELOG.md](CHANGELOG.md). `beta_series.csv` exports
β(year, species) 2012–2023 on a production basis, with its fates, and (since
Phase G) traced on conserved carbon with a sensitivity envelope in
`ci_low`/`ci_high`. The rotation model that consumes it is `models/rotation`
(Phase L), a separate model exchanging files.

---

## Phase G — Carbon depth · **DONE 2026-11-08**

- [x] **Uncertainty bounds** — density envelope (`carbon_low`/`carbon_high`)
      plus the one-at-a-time coefficient sensitivity
      (`circwood.analysis.sensitivity`, `beta_sensitivity.csv`,
      `config/coefficient_ranges.csv`) feeding `ci_low`/`ci_high`.
- [x] **HWP pool as a stock** — `circwood.carbon.stock`, `hwp_stock.csv`,
      `config/hwp_half_lives.csv`.
- [x] **Carbon conservation at process nodes** — carbon follows the wood;
      `validate.assert_carbon_balanced`.
- [x] **One-at-a-time sensitivity of β** — as above.
- [x] **Import species mix** — documented: the fixed shares in
      `carbon_nodes.csv` cannot move the production-basis β (imports are never
      traced); they move `beta_literal` and the carbon totals, and
      `tests/test_hwp.py` pins that. Sourcing the real split remains a data
      question for the authors.
- [x] **CO₂-equivalents** — `CO2_PER_C`; a display option in the app and site.

---

## Phase K — Dataset defects found in the Phase F review · **DONE 2026-11-08**

All four fixed and the doc drift with them; see the CHANGELOG and the
decision log. The one thing that remains open from that review: the
`Impregnation` node's year-to-year imbalance (±13 % in single years, 0.2 %
over the window) comes from the Treindustrien inventory series and is a data
question, not a code defect.

---

## Phase H — Forest resource layer · **DONE 2026-11-08**

The six SSB Landsskogtakseringen tables (14589, 14590, 14591, 14593, 14594,
06289) are ingested by `transform/forest.py` into `forest_resource.csv` and
rebuilt by the refresh; the definitional boundary (productive forestry land
excludes protected forest) is in METHODS.md; the increment-vs-harvest ratio is
reported with its caveats in METHODS, the app and the site.

Deliberately not done: **SR16** (NIBIO's 16 m raster) for a map view —
modelled data with ~50 % per-pixel RMSE that would need municipality-level
aggregation and a tile pipeline; a different project.

Still useful if wanted (P3): join the superseded series (13327, 06290/06291,
14368) with a region crosswalk for a longer regional history — a human
decision on the crosswalk.

---

## Phase I — Reproducibility and publication support · **DONE 2026-11-08**

- [x] `METHODS.md`, `DATA_DICTIONARY.md` (fenced by `tests/test_docs.py`),
      `docs/REFRESH_POLICY.md`.
- [x] `requirements-lock.txt`, `Dockerfile`.
- [x] `CITATION.cff`, `.zenodo.json` — **minting the DOI is a human step:**
      connect the GitHub repository to Zenodo and publish a release.
- [x] `ruff` + pre-commit; `mypy` baseline; CI lint job.
- [x] Broader tests (trade mapping, inventory routing, firewood split, viz
      aggregation, rebuild fidelity, carbon conservation, and every new layer).

---

## Phase L — Economic value: rotation, carbon and the future wood stock · **DONE 2026-11-08**

Delivered as described in the CHANGELOG and METHODS §8–9. What a human still
owns:

- **Prices:** replace the carbon price and exchange rate in `config/prices.csv`
  with the year analysed; decide on a source for processed-product prices if
  the product stages are to be valued; check licensing before any member price
  data enters (`docs/DATA_CONFIDENTIALITY.md`).
- **Growth:** done 2026-11-08 — `models/sitree/simulate_stand.R` ran with
  sitree 0.1.15; the export is committed and compared in
  `rotation_results.csv` (decision log). What would carry weight is a
  *realistic* stand: the example stand's density is outside the growth
  functions' range, so a stand from the NFI plots or a thinned planting is
  the next step.
- **Scaling:** the stock projection's class residences and weights are
  indicative national averages; a regional or site-class version needs the
  NFI's joint distribution, which SSB does not publish.
- **Publication:** decide whether the model results go on the public site.

---

## What remains (for a human)

1. Review and commit the 2026-11-08 change set (the dataset moved; the golden
   fixture, β pins and every derived file moved with it).
2. Choose a licence: add `LICENSE` and the SPDX id to `CITATION.cff`,
   `.zenodo.json` and `pyproject.toml` (the metadata deliberately carries
   none until then).
3. Mint the Zenodo DOI; cite it in README and CITATION.cff.
4. Refine `config/coefficient_ranges.csv` and `config/prices.csv` with sourced
   ranges and prices; if the SiTree comparison is to carry weight, simulate a
   stand at a realistic density (the example stand's is outside the growth
   functions' range).
5. Decide on the public-site exposure of the model results, and whether the
   paper's authors want the corrected social-model figure (107, not 125).
6. Pick the drift thresholds from `refresh_history.jsonl` after a few cycles.

---

## Definition of done

- A scheduled job refreshes SSB data, proves it balances and reconciles,
  diffs it against the previous vintage, and opens a PR — with no human step
  until review. **Met** (and since Phase J it also derives the year window,
  attributes every move and names its own changes as such).
- A public static site renders an interactive, cross-filterable Sankey with
  year, species, region and node filters, and states its data vintage. **Met.**
- `beta_series.csv` exports a measured HWP fraction with uncertainty bounds and
  a documented carbon basis. **Met** (bounds are an assumption envelope, labelled).
- Confidentiality rules are written down and enforced in code before any
  member-level data can enter the repository. **Met.**
