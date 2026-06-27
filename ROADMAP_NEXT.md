# Circwood WP4 — Improvements Roadmap (next horizon)

The refactor in [ROADMAP.md](ROADMAP.md) is done: clean `circwood` package,
fixed + tested sawing function, species-accurate editable carbon model, and a
Streamlit app (metric toggle, year controls, node selector) that deploys off
cached data. This document is the forward-looking backlog: what to build next,
prioritised, with the *why* and rough effort for each.

Legend — **P1** do next / high value · **P2** valuable · **P3** nice-to-have.
Effort — **S** hours · **M** a day or two · **L** several days.

---

## A. Data correctness & trust (highest leverage)

These determine whether the numbers can be defended in a paper.

- **A1 · Close the mass balance** — ✅ **DONE**
  Closed diagnosis Issue 3: raw-log re-exports now bypass the Sawmills node
  (routed from the species pools straight to Export), the sawn-side flows are
  mass-derived via ``SAWN_YIELD``, and the imported-planed-wood double-count is
  fixed. Every process node (Sawmills, Pulpmills, Boardmills, Planingmills,
  Impregnation, WPP) balances within ~1 % over 2012–2022; use-phase and
  inventory nodes are treated as reservoirs by design. Added
  ``circwood.validate`` (balance report + ``assert_balanced``), a "Mass balance
  check" panel in the app, ``assemble.assemble_sankey`` / ``regenerate_balanced``
  (offline re-derivation), and balance tests. The shipped dataset was
  regenerated; pulp ≈ 26 % of logs actually sawn.
- **A2 · Live SSB ingestion (Phase 3)** — **P1 · M**
  Wire up `build_sankey(use_cached_ssb=False)`: retries/timeouts, cache raw
  JSON-stat to `data/raw/`, and validate each table's columns/year coverage on
  load (fail loud). Record the data vintage (pull date) alongside the output.
- **A3 · Validate against published SSB/Treindustrien aggregates** — **P1 · S**
  Assert pipeline totals (harvest, sawn timber, pulp) match the source tables'
  own totals within tolerance. Cheap, high-confidence regression guard.
- **A4 · Replace placeholder import species mix** — **P2 · S**
  `config/carbon_nodes.csv` currently assumes imports are 100 % spruce. Source
  the real species split (or document the assumption and its sensitivity).
- **A5 · Data dictionary & provenance** — **P2 · S**
  One table documenting every node, every flow, its source table, and its
  coefficient. Makes the model reviewable and paper-ready.

## B. Carbon model depth

- **B1 · Uncertainty / scenario bounds** — **P1 · M**
  Let researchers run low/central/high density and species-mix assumptions and
  see the carbon range. Builds directly on the editable-CSV model. *Turns a
  point estimate into a defensible range.*
- **B2 · Carbon stock & flux over time (HWP pool)** — **P2 · L**
  Today carbon is an annual *flow*. Model the carbon *stored* in long-term
  products as a stock that accumulates and decays year over year — the headline
  result for climate impact of wood use.
- **B3 · CO₂-equivalents & substitution** — **P3 · M**
  Convert C → CO₂e and optionally model substitution benefits vs alternative
  materials/energy. Only if the paper's scope needs it.

## C. App & visualisation

- **C1 · Time-series view** — **P1 · M**
  The Sankey is a single-year/average snapshot. Add line charts of key flows
  (e.g. Sawmills→Pulpmills, total harvest, carbon in long-term use) across all
  years. Highest-value app feature after node selection.
- **C2 · Selection presets & shareable URLs** — **P2 · S**
  Persist the chosen nodes/metric/years in URL query params so a view can be
  bookmarked/shared; offer a few named presets (e.g. "Sawmill chain only").
- **C3 · Group the node selector by category** — **P3 · S**
  Forest / mills / end-use grouping (the same palette categories) to make the
  33-node list easier to navigate.
- **C4 · Figure export & per-node totals** — **P2 · S**
  Download the diagram as PNG/SVG (kaleido is already installed) and show a
  per-node inflow/outflow/balance table on hover or in a panel.
- **C5 · Two-year / scenario comparison** — **P3 · M**
  Render two Sankeys side by side (e.g. 2012 vs 2022, or central vs high-carbon).

## D. Engineering quality

- **D1 · CI pipeline (GitHub Actions)** — **P1 · S**
  Run `pytest` (and lint) on every push/PR. Cheap insurance now that there's a
  real test suite.
- **D2 · Lint + format + pre-commit** — **P2 · S**
  `ruff` + `black` (or `ruff format`) with a pre-commit hook for consistency.
- **D3 · Broaden test coverage** — **P2 · M**
  Add tests for trade mapping, inventory routing, firewood split, the viz
  remap/aggregation, and the fresh pipeline with a mocked SSB response.
- **D4 · Replace `print` with logging** — **P3 · S**
  Use the `logging` module so verbosity is controllable in the app/CLI.
- **D5 · Type-check with mypy** — **P3 · S**
  The code is largely type-hinted; add mypy to catch interface drift.

## E. Reproducibility & deployment

- **E1 · Deploy to Streamlit Community Cloud** — **P1 · S**
  Everything is ready (`requirements.txt`, committed data). Actually publish it
  and confirm the hosted build. *Turns "deployable" into "deployed".*
- **E2 · METHODS.md for the paper** — **P2 · M**
  Narrative methods doc: every assumption, coefficient, and data source, with
  the sawing-fix and carbon-model explanations. Reusable as paper SI.
- **E3 · Dockerfile / pinned lockfile** — **P3 · S**
  Fully pinned environment for exact reproducibility of published figures.

---

## Recommended near-term sequence

1. ✅ ~~A1 close the mass balance~~ — **done.**
2. **D1 CI** + **A3 aggregate validation** — lock in correctness cheaply (½ day).
3. **A2 live SSB ingestion** — make the pipeline self-refreshing.
4. **C1 time-series view** + **E1 deploy** — biggest user-visible wins.
5. **B1 carbon uncertainty** — turn the carbon result into a defensible range.

## Open questions for you

1. Is this for a **publication** (then A1/A3/A5/E2 matter most) or a **live
   dashboard** (then C1/C2/E1 matter most)?
2. Should carbon stay a **yearly flow**, or do you want the **stock-over-time**
   model (B2)? That's the biggest single analytical decision.
3. Do you want me to start on the **mass-balance reconciliation (A1)** — it's the
   most important correctness item still open?
