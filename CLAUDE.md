# CLAUDE.md

Guidance for Claude Code working in this repository. Read this first; then
[ROADMAP.md](ROADMAP.md) for what to do next and [SANKEY_GUIDE.md](SANKEY_GUIDE.md)
for where each number comes from.

---

## WHAT this is

**Circwood WP4** is a reproducible material- and carbon-flow analysis (MFA) of
Norwegian wood: from standing forest through sawmills, pulpmills, board mills
and downstream use, published as a Sankey diagram. It is the code and data
behind a peer-reviewed paper — Modaresi & Landaas, *"Wood extraction and
utilisation in Norway 2012–2022: a national material and carbon flow analysis"*,
Wood Material Science & Engineering (2026), Norwegian Institute of Wood
Technology (NTI).

Three concurrent goals, in the roadmap's words: **Live** (SSB data refreshes
itself safely), **Public** (an interactive static site, not a local app), and
**Research** (export an *empirical* HWP fraction β that feeds a forest-rotation
model — see `papers/` for the thesis that motivates it).

**The artifact is a tidy flow table.** `data/processed/sankey_data.csv`:
one row per `(source, target, volume, year, origin)` — currently **638 rows,
31 nodes, 2012–2024**. `carbon_data.csv` is the same table plus a `carbon`
column (kg C). Both are **committed** and are the canonical output; everything
else either produces them or reads them.

Sources: SSB (Statistics Norway) PxWeb tables **03795, 07410, 08801, 10513,
11181**, the Treindustrien annual report, and FAOSTAT.

```
config/              ssb_queries/*.json, nodes.json (hand-placed positions),
                     carbon_densities.csv, carbon_nodes.csv
data/raw/            source spreadsheets; ssb/ is a git-ignored response cache
data/processed/      sankey_data.csv, carbon_data.csv, manifest.json  ← the artifact
src/circwood/
  config.py          every filesystem path
  constants.py       every model coefficient and the year window
  ingest/            ssb.py (API), treindustrien.py, trade.py
  transform/         sawing.py, mills.py, firewood.py, inventory.py, assemble.py
  carbon/            species_model.py (species propagation), carbon.py
  viz/sankey.py      Plotly figure builder — pure, no I/O
  pipeline.py        build_sankey() / build_carbon()      → circwood-build
  manifest.py        provenance / "data as of"
  validate.py        mass balance;  reconcile.py  source + control totals
  diff.py            vintage diff — the refresh gate
  refresh.py         the whole gated chain              → circwood-refresh
app/streamlit_app.py internal exploration tool (NOT the public deployment)
tests/               106 tests, incl. the golden + node-coverage fences
papers/              PDFs, git-ignored — read for context, never commit
```

---

## WHY it is built this way

These are decisions with reasons, not preferences. Don't undo them casually;
the reasoning is preserved in the ROADMAP decision log and CHANGELOG.

- **The committed CSV is the canonical artifact.** The app, the tests and any
  host read it, so no consumer needs network access or the `ssb` extras — and
  every data change arrives as a reviewable diff rather than a silent re-fetch.
- **All coefficients live in `constants.py`; all paths in `config.py`.** The
  assumptions behind the diagram must be auditable in one place for the paper.
- **The carbon model is two editable CSVs**, not code. Researchers change
  densities and species mixes in Excel and re-run; that is the intended workflow.
- **Carbon is species-accurate downstream.** The spruce/pine/non-coniferous mix
  propagates through mixing nodes to a fixed point instead of collapsing to
  spruce, because collapsing misstates the carbon of everything after Sawmills.
- **The dataset is clipped to the intersection of every source table's
  coverage** (`DATASET_MIN_YEAR/MAX_YEAR` = 2012–2024). Rows outside it are a
  *partial picture, not extra data*: 1988–2011 carried trade with no harvest
  behind it, and a part-published 2025 made firewood look like it grew 808 %.
- **Everything is gated, and nothing survives a failure.** A fresh run either
  produces a complete validated dataset or raises — there is no path that
  returns silently partial data. `refresh()` restores the previous
  `data/processed` outputs before any error propagates.
- **Mass balance alone is not enough**, hence `diff.py`. A dataset built from a
  reclassified SSB code is wrong *consistently*: it balances perfectly and
  reconciles to whatever total it was built from. Only comparing against the
  previous vintage reveals that a flow moved.
- **Streamlit is the internal tool and will not be deployed** (decision
  2026-07-28). The public deployment is the Phase D static site (ECharts),
  chosen for robustness so Treteknisk can adopt it. Keep the app; don't migrate
  or delete it.
- **PDFs are never committed — the repo is public.**

---

## HOW to work here

### Setup and commands

```bash
pip install -e .[dev]      # core + pytest;  add ,ssb for live SSB fetch
pytest                     # on Windows here: py -3 -m pytest
```

| Task | Command |
|---|---|
| Load the dataset | `python -c "from circwood import build_sankey; build_sankey()"` |
| Rebuild cached | `circwood-build` (`--fresh` re-fetches SSB; `--verbose` logs vintages) |
| Rebuild carbon | `python -c "from circwood import build_carbon; build_carbon(write=True)"` |
| Refresh from SSB | `circwood-refresh [--report r.md] [--accept-drift]` |
| Re-derive balanced | `python -c "from circwood.transform.assemble import regenerate_balanced; regenerate_balanced(write=True)"` |
| Run the app | `streamlit run app/streamlit_app.py` |

Requires Python ≥ 3.10; CI runs 3.11–3.12 with the `ssb` extras installed so
ingestion tests actually run (they are fully mocked — CI never calls SSB).

### The refresh chain (`src/circwood/refresh.py`)

ingest → rebuild sankey → rebuild carbon → write `manifest.json` → mass balance
→ reconcile → **vintage diff** → update golden fixture. Any failed gate restores
the previous outputs and exits non-zero. `.github/workflows/refresh.yml` runs it
monthly and opens a PR; pytest runs *before* and *after* the refresh, never in
the middle (mid-run the golden fence is red by construction).

### House rules — every phase ends green

- `pytest` passes and `reconcile` still ties out.
- **No change may weaken an existing guarantee.** The regression fences are: mass
  balance within ~1 % on `validate.PROCESS_NODES` over the paper window
  (2012–2022), and the SSB 07410 control total **61,445,000 m³**.
- **Changes to `sankey_data.csv` must be additive.** Adding columns is fine;
  renaming or reordering breaks the committed dataset.
- **Regenerating the dataset means updating `tests/fixtures/golden_sankey_data.csv`
  in the same commit**, so the diff is visible in review.
- `config/nodes.json` must cover the dataset's node set *exactly* — no missing
  labels, no ghosts (`tests/test_golden.py`). `viz.remap_ids` silently drops
  flows whose endpoint has no entry; this once hid ~5.9 Mm³.

### Things that look like bugs and are not

- **SSB 10513 is incineration-only by design** (`AvfallsBehand=21`). Norwegian
  waste wood is effectively all incinerated; the edge exists to quantify the
  recoverable potential, read off the Energy edge. A guard raises if the query
  is widened without adding explicit routing.
- **SSB queries must request `json-stat2` and `lang=no`.** The transforms filter
  on Norwegian labels ('Gran', 'Skurtømmer', …), so `lang=en` returns HTTP 200,
  parses cleanly, and yields zero rows. `normalize_spec` enforces this
  pre-network.
- **`pyjstat.from_json_stat` is deprecated upstream** and the warnings are
  expected. Migrating could silently change parsed column names, so it is fenced
  by `tests/test_ssb_parse_golden.py` — migrate behind that fence, regenerating
  the snapshot in the same commit.
- **Sawing by-product coefficients apply to the raw log input**, not a
  post-shrinkage volume. That was one of the three bugs in
  `Sawing_function_diagnosis.docx`; `tests/test_sawing.py` guards all three.
  Expected result: Sawmills → Pulpmills ≈ 26 % of log input.
- Species pools, use phases and inventory nodes are **legitimately unbalanced** —
  reservoirs and differing table coverage. Only `PROCESS_NODES` must balance.

### What needs a human — do not attempt or stub

- Negotiating member-level data agreements; contacting Treindustrien /
  Treteknisk / NMBU; publication and preprint submission; deciding what is
  commercially confidential.
- **Placing a new node.** `config/nodes.json` holds hand-placed diagram
  positions, so a refresh that introduces a new node fails the node-coverage
  fence and opens an issue. A new node is a modelling change and wants a human.
- **Widening `DATASET_MAX_YEAR`** or running `--accept-drift`: both are
  deliberate, reviewed acts.

### Where to start

`ROADMAP.md` is the single live plan; completed work is in `CHANGELOG.md`.
Phases A and B are done. **Phase J (year-window and drift policy) is next**,
then C (schema dimensions), which must land **before** D (the static site) —
retrofitting a dimension after the frontend exists is materially more expensive.
