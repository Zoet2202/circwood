# Circwood WP4 — Norwegian Wood-Flow Sankey

A clean, reproducible pipeline and interactive web app for the Circwood WP4
wood-flow material-flow analysis: from Norwegian standing forest through
sawmills, pulpmills, board mills and downstream use, visualised as a Sankey
diagram.

Data sources: **SSB** (Statistics Norway) tables and the **Treindustrien**
annual report. The current build runs entirely off cached/processed data — no
network access required. Live SSB API ingestion is scaffolded for later (see
[ROADMAP.md](ROADMAP.md)).

## Project layout

```
config/              # ssb_queries/*.json, nodes.json (Sankey node positions)
data/
  raw/               # source spreadsheets + cached SSB inputs
  processed/         # generated: sankey_data.csv, carbon_data.csv
src/circwood/
  config.py          # all filesystem paths
  constants.py       # all model coefficients (yields, splits, year window)
  ingest/            # ssb.py (API), treindustrien.py, trade.py
  transform/         # sawing.py, mills.py, firewood.py, inventory.py, assemble.py
  viz/sankey.py      # Plotly figure builder (pure, no I/O)
  pipeline.py        # build_sankey(use_cached_ssb=...)
app/streamlit_app.py # the web app
tests/               # pytest: locks in the sawing fix + cached-data sanity
```

## Setup

```bash
pip install -e .          # core (cached pipeline + app)
pip install -e .[dev]     # + pytest
pip install -e .[ssb]     # + pyjstat/requests (only for live SSB fetch, later)
```

Requires Python ≥ 3.10.

## Usage

**Load / build the dataset**

```python
from circwood import build_sankey
df = build_sankey(use_cached_ssb=True)   # loads data/processed/sankey_data.csv
```

CLI: `circwood-build` (or `python -m circwood.pipeline`). `--fresh` re-fetches
from the SSB API (needs the `ssb` extras + network; not yet wired for routine use).

**Run the web app**

```bash
streamlit run app/streamlit_app.py
```

Sidebar controls: **metric** (volume Mm³ or embodied carbon Mt C),
annual-average vs single-year, year range, and node toggles. A CSV download
panel exports the current window.

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

> Note: the *whole assembled* Sawmills node intentionally does not net to zero,
> because trade export rows (`Sawmills → Export *`) are additional outflows. The
> sawing *transform* is mass-balanced in isolation; the test suite checks that.

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

Volume **and carbon** pipelines + the app are working off cached data. Still to
do: live SSB API ingestion. See [ROADMAP.md](ROADMAP.md).
