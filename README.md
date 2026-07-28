# Circwood WP4 — Norwegian Wood-Flow Sankey

A clean, reproducible pipeline and interactive web app for the Circwood WP4
wood-flow material-flow analysis: from Norwegian standing forest through
sawmills, pulpmills, board mills and downstream use, visualised as a Sankey
diagram.

Data sources: **SSB** (Statistics Norway) tables and the **Treindustrien**
annual report. The default build runs entirely off cached/processed data — no
network access required. Live SSB API ingestion works and is hardened for
unattended runs; what is still manual is *triggering* the refresh (see
[ROADMAP.md](ROADMAP.md), Phase B).

## Project layout

```
config/              # ssb_queries/*.json, nodes.json (Sankey node positions)
data/
  raw/               # source spreadsheets + cached SSB inputs
  processed/         # generated: sankey_data.csv, carbon_data.csv, manifest.json
src/circwood/
  config.py          # all filesystem paths
  constants.py       # all model coefficients (yields, splits, year window)
  ingest/            # ssb.py (API), treindustrien.py, trade.py
  transform/         # sawing.py, mills.py, firewood.py, inventory.py, assemble.py
  viz/sankey.py      # Plotly figure builder (pure, no I/O)
  pipeline.py        # build_sankey(use_cached_ssb=...)
  manifest.py        # dataset provenance / "data as of"
  diff.py            # vintage diff, the refresh gate
  refresh.py         # the refresh loop (circwood-refresh)
app/streamlit_app.py # the web app
tests/               # pytest: locks in the sawing fix + cached-data sanity
```

## Setup

```bash
pip install -e .          # core (cached pipeline + app)
pip install -e .[dev]     # + pytest
pip install -e .[ssb]     # + pyjstat/requests (live SSB fetch)
```

Requires Python ≥ 3.10.

## Usage

**Load / build the dataset**

```python
from circwood import build_sankey
df = build_sankey(use_cached_ssb=True)   # loads data/processed/sankey_data.csv
```

CLI: `circwood-build` (or `python -m circwood.pipeline`). `--fresh` re-fetches
from the SSB API and rewrites `sankey_data.csv` (needs the `ssb` extras +
network); `--verbose` logs each table's vintage and any retries. A fresh run
either produces a complete, validated dataset or raises — it never returns
partial data unless you ask it to.

The dataset covers **2012–2024**: the years every SSB source table covers
(`constants.DATASET_MIN_YEAR/DATASET_MAX_YEAR`). Outside that intersection the
rows are a partial picture rather than extra data — see the ROADMAP decision log.

**Refresh from SSB**

```bash
circwood-refresh                 # rebuild, gate, and report
circwood-refresh --report r.md   # also write the markdown report
```

Runs ingest → rebuild → `manifest.json` → mass balance → reconciliation →
**vintage diff** against the committed dataset → golden-fixture update. Any
failed gate restores the previous `data/processed` outputs and exits non-zero,
so a bad vintage never reaches a commit. `.github/workflows/refresh.yml` runs
this monthly and opens a PR. To adopt a change the gate blocked, review it and
re-run with `--accept-drift`.

**Run the web app**

```bash
streamlit run app/streamlit_app.py
```

Sidebar controls: **metric** (volume Mm³ or embodied carbon Mt C),
annual-average vs single-year, year range, and a **node selector** (choose
exactly which nodes to display — flows touching a hidden node are removed). A CSV
download panel exports the current window.

**Carbon overlay (species-accurate)**

```python
from circwood import build_carbon
df = build_carbon(write=True)   # adds a `carbon` column (kg C); writes carbon_data.csv
```

Carbon mass per flow = `volume (m³) × Σ species_share × density(species, state)`.
The model is **species-accurate downstream**: the spruce / pine / non-coniferous
mix is tracked as wood flows through the mixing nodes (Sawmills, Pulpmills, …) to
a fixed point, rather than collapsing everything to spruce. So, e.g., Sawmills
output carbon reflects the actual spruce/pine split of the logs sawn that year.
See [carbon/species_model.py](src/circwood/carbon/species_model.py).

### Editing the carbon model (for researchers)

All carbon assumptions live in two CSVs you can open and edit in Excel — no code
changes needed:

* **[config/carbon_densities.csv](config/carbon_densities.csv)** — carbon density
  (kg C/m³) for each `(species, state)`.
* **[config/carbon_nodes.csv](config/carbon_nodes.csv)** — one row per node:
  * `state` — `raw` or `dry` (moisture state of wood leaving the node),
  * `mix_mode` — `fixed` (use the given species shares; for pools and imports) or
    `propagate` (compute the species mix from the node's inflows; for mills, use
    phases, inventories),
  * `spruce_share` / `pine_share` / `nonconif_share` — species fractions, used
    only when `mix_mode = fixed`.

Edit a value, re-run the app (or `build_carbon(write=True)`), and the carbon
figures update. The app's "Species composition" panel shows the resulting mix
per node.

**Run the tests**

```bash
pytest
```

The suite is run in CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) on
every push/PR across Python 3.11–3.12. It includes reconciliation checks
([src/circwood/reconcile.py](src/circwood/reconcile.py)) that tie pipeline totals
to the Treindustrien source CSV and the SSB 07410 control total.

## The sawing-function fix

`Sawing_function_diagnosis.docx` documents three compounding bugs in the old
sawing calculation. All three are fixed in
[src/circwood/transform/sawing.py](src/circwood/transform/sawing.py) and guarded
by [tests/test_sawing.py](tests/test_sawing.py):

1. **Year misalignment** — every per-year series is reindexed onto a shared year
   index before arithmetic (no positional/integer-index alignment).
2. **Coefficient base** — `CELLULOSE`/`SAWDUST`/`SCRAPS` are applied to the raw
   log input, not a post-shrinkage "dry" volume.
3. **Mass balance** — the sawn-timber residual to WPP is derived from a
   mass-balanced `SAWN_YIELD`, with explicit handling of re-exported logs,
   imports and stock changes.

Result: **Sawmills → Pulpmills ≈ 26%** of sawmill log input (the expected chip
yield), verified for the 2012–2022 window.

### Mass balance

The dataset conserves volume at every process node. Raw-log re-exports bypass the
Sawmills node (routed from the species pools straight to Export, since they are
never sawn), the sawn-side flows are mass-derived, and imported planed wood
reaches WPP via its own edge. Over 2012–2022, Sawmills, Pulpmills, Boardmills,
Planingmills, Impregnation and WPP all balance within ~1 %; use-phase
(Long-term/Short-term USE) and inventory nodes are reservoirs by design. The
checks live in [src/circwood/validate.py](src/circwood/validate.py) and
[tests/test_balance.py](tests/test_balance.py); the app has a "Mass balance
check" panel. To re-derive a balanced dataset offline from primary rows:
`python -c "from circwood.transform.assemble import regenerate_balanced; regenerate_balanced(write=True)"`.

## Deployment (web hosting)

The app runs off the **committed** processed data (`data/processed/*.csv`) and the
config files, so it needs no live SSB access or local install when hosted.

**Streamlit Community Cloud:** point it at this repo with
`app/streamlit_app.py` as the entry point. It installs from
[requirements.txt](requirements.txt) (the app adds `src/` to the path itself, so
the package is not pip-installed there). Theme/server defaults are in
[.streamlit/config.toml](.streamlit/config.toml).

To refresh the hosted data after editing inputs or the carbon model, regenerate
and commit the processed CSVs:

```bash
python -m circwood.pipeline          # sankey_data.csv (cached mode)
python -c "from circwood import build_carbon; build_carbon(write=True)"  # carbon_data.csv
```

## Status & roadmap

Volume **and carbon** pipelines + the app work off cached data; live SSB
ingestion is hardened for unattended runs (Phase A) and the refresh loop is
automated behind gates (Phase B). Still to do: the year-window policy the first
live refresh exposed (Phase J), filter dimensions in the schema, and a public
static site. The live plan is [ROADMAP.md](ROADMAP.md); completed work is
recorded in [CHANGELOG.md](CHANGELOG.md).

The Streamlit app is the **internal exploration tool**; the public deployment
will be a static site (see Phase D of the roadmap) so it is robust enough for
Treteknisk to adopt.
