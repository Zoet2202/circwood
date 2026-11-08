# Circwood WP4 — Norwegian wood material- and carbon-flow analysis

A reproducible pipeline, dataset and interactive site for the Circwood WP4
material-flow analysis of Norwegian wood: from roundwood removals through
sawmills, pulp mills and board mills to use, energy and export, 2012–2024, in
cubic metres and in carbon. It is the code and data behind Modaresi & Landaas,
*Wood extraction and utilisation in Norway 2012–2022: a national material and
carbon flow analysis*, Wood Material Science & Engineering (2026), Norwegian
Institute of Wood Technology (NTI).

The dataset has been corrected since the paper's figures were prepared
(CHANGELOG 2026-11-08, Phases K and G: the End-of-Life unit, the firewood
residual, carbon conservation and β on the carbon basis), so this repository
reproduces the corrected numbers; the commit before that date (`f2006a7`)
reproduces the earlier ones. Which published figures are affected is for the
authors to state.

Data: **Statistics Norway (SSB)** tables (removals, trade, trade by partner
country, waste, firewood, the National Forest Inventory, prices) and the
**Treindustrien** annual report. The default build runs entirely off the
committed data — no network access required. A gated, scheduled refresh
regenerates everything from the SSB API and opens a pull request.

## What is in the box

| | File | What |
|---|---|---|
| The dataset | `data/processed/sankey_data.csv` | 3,723 flow rows, 31 nodes, 2012–2024, with region, species, assortment and destination dimensions |
| Carbon | `data/processed/carbon_data.csv` | the same rows in kg C, with a low/high density envelope; carbon is conserved through every process node, exactly as volume is |
| Provenance | `data/processed/manifest.json` | SSB revision date of every table, query hashes, input hashes, output hashes |
| β | `data/processed/beta_series.csv` | the empirical harvested-wood-product fraction by year and species, with a sensitivity envelope |
| Sensitivity | `data/processed/beta_sensitivity.csv` | β under each fixed coefficient at its low and high bound |
| HWP stock | `data/processed/hwp_stock.csv` | carbon stored in products, first-order decay with IPCC half-lives |
| Forest | `data/processed/forest_resource.csv` | SSB forest inventory: area, standing volume, increment, development classes, by region |
| Value | `data/processed/economic_value.csv` | roundwood, energy and carbon valued at SSB prices and a labelled carbon price |
| Models | `data/processed/rotation_results.csv`, `forest_stock_projection.csv` | rotation economics with the empirical β; the national stock to 2100 under three harvests (`models/`) |
| The site | `site/` | the public Sankey, Plotly.js, no build step, deployed to GitHub Pages |

Every number is traceable: [METHODS.md](METHODS.md) states each assumption,
coefficient and derivation; [DATA_DICTIONARY.md](DATA_DICTIONARY.md) lists
every node, edge, column and file; [SANKEY_GUIDE.md](SANKEY_GUIDE.md) walks
through each source table and the edges it produces.

## Project layout

```
config/              SSB query files (flows, forest/, economic/), nodes.json,
                     the editable model CSVs (carbon densities and nodes,
                     half-lives, sensitivity ranges, prices)
data/raw/            Treindustrien CSV, county harvest, partner exports, prices
data/processed/      the committed artifact and every derived file
src/circwood/        config, constants, ingest/, transform/, carbon/, analysis/,
                     viz/, export/, pipeline, manifest, validate, reconcile,
                     coverage, diff, refresh
models/              separate models exchanging files (rotation, forest stock, SiTree)
app/streamlit_app.py the internal exploration app
site/                the public static site
tests/               the fences and unit tests
docs/                DATA_CONFIDENTIALITY.md, REFRESH_POLICY.md
```

## Setup

```bash
pip install -e .          # core (cached pipeline + app)
pip install -e .[dev]     # + pytest, ruff, mypy
pip install -e .[ssb]     # + pyjstat/requests (live SSB fetch)
```

R (≥ 4.1) with the `sitree` package is needed only to regenerate
`models/sitree/growth_curve_sitree.csv`; the export is committed, so neither
CI nor the Docker image needs R.

Requires Python ≥ 3.10 (CI: 3.11 and 3.12). `requirements-lock.txt` pins the
exact versions used for the published figures; the `Dockerfile` reproduces the
test-suite and the site bundle offline from the committed dataset.

## Usage

**Load the dataset**

```python
from circwood import build_sankey, build_carbon
df = build_sankey()          # data/processed/sankey_data.csv
cdf = build_carbon()         # + carbon, carbon_low, carbon_high (kg C)
```

**Refresh from SSB** (needs the `ssb` extras and network)

```bash
circwood-refresh --report r.md        # rebuild, gate, report
circwood-forest                       # the forest-inventory layer only
circwood-economics                    # the price tables and the valuation only
```

The refresh ingests every table, derives the year window every table covers,
rebuilds the flows and the carbon, writes the manifest, checks mass balance
(volume and carbon, in aggregate and per slice), reconciles to Treindustrien
and the SSB 07410 control total, diffs the result against the committed
dataset and attributes every move to an SSB revision or to this repository's
own change, re-exports β, its sensitivity, the HWP stock, the forest and
economic layers, and updates the golden fixture. Any failed gate restores the
previous outputs and exits non-zero. `.github/workflows/refresh.yml` runs it
monthly and opens a PR; a blocked refresh is adopted only by a human through
the workflow's `accept_drift` input. The policy is in
[docs/REFRESH_POLICY.md](docs/REFRESH_POLICY.md).

**The derived analyses**

```python
from circwood.analysis.hwp import beta_series, export_beta_series
from circwood.analysis.sensitivity import export_sensitivity, summary_table
from circwood.carbon.stock import hwp_stock, export_hwp_stock
from circwood.transform.forest import load_forest_resource, increment_vs_harvest
```

β is production-basis: the share of each year's *domestic* roundwood carbon
that reaches long-lived products, traced through the balanced flows with
proportional mixing on conserved carbon. Over 2012–2022 it averages 0.215
(spruce 0.259, pine 0.242), against the 0.55 the rotation literature assumes
(whose product-pool definition is unverified, METHODS §5).
Its `ci_low`/`ci_high` are the root-sum-square of one-at-a-time coefficient
deviations over `config/coefficient_ranges.csv`: an assumption envelope, not a
confidence interval, and labelled as such.

**The models** (`models/`, separate from the package; see `models/README.md`)

```bash
py -3 -m models.rotation.rotation          # Faustmann / van Kooten / CRC / social, with the empirical β
py -3 -m models.forest_stock.projection    # the national stock to 2100 under three harvests
Rscript models/sitree/simulate_stand.R     # optional: regenerate the SiTree export (R >= 4.1 + sitree; committed)
```

**The app and the site**

```bash
streamlit run app/streamlit_app.py            # internal: flows, carbon fate & β, forest, HWP stock,
                                              # sensitivity, economics, models, balance, provenance
circwood-bundle && python -m http.server -d site 8000   # the public site, locally
```

The site filters by year window or single year, metric (volume, carbon,
CO₂e), species, region, assortment and destination country, cross-filters on
click, states its data vintage, shows the carbon envelope and the forest
resource, and exports PNG/SVG. It deploys to GitHub Pages on merge to `main`
(`.github/workflows/site.yml`); the model results stay off it until the authors
have reviewed them.

## Editing the model (for researchers)

All model assumptions are CSVs you can open in Excel; re-run the relevant
export and the figures update, no code change needed:

* `config/carbon_densities.csv` — kg C/m³ per species and state, with
  `kg_c_per_m3_low/high` for the envelope.
* `config/carbon_nodes.csv` — each node's state, mixing rule, fixed species
  shares, and whether it carries carbon (the water sink does not).
* `config/hwp_half_lives.csv` — product-class half-lives for the stock.
* `config/coefficient_ranges.csv` — the ranges the sensitivity analysis varies.
* `config/prices.csv` — the carbon price and energy yield (assumptions, with sources).
* `src/circwood/constants.py` — every fixed coefficient (sawing splits, mill
  splits, bark and residue shares, the year windows, the β gates).

## Tests

```bash
pytest                                    # on Windows: py -3 -m pytest
ruff check src tests app models && mypy   # lint and types, as in CI
```

The suite holds the project's guarantees as fences: the committed dataset
equals its golden fixture; `config/nodes.json` covers the node set exactly;
every process node balances within 1 % over 2012–2022 in volume (in aggregate
and per (region, species) slice) and in carbon (in aggregate; per species the
carbon imbalance equals the volume imbalance by construction, which
`tests/test_carbon.py` fences); domestic sawtimber ties to SSB 07410's
61,445,000 m³; every derived file equals a fresh export; every node, edge and
constant is documented. The sawing fix the diagnosis report asked for (year
alignment, coefficients on raw input, mass balance) is pinned in
`tests/test_sawing.py`, with the regression guard that `Sawmills → Pulpmills`
equals `CELLULOSE` × raw log input (26 %, not the 24.7 % a post-shrinkage
basis gives).

## Citing

See `CITATION.cff` and `.zenodo.json`. Please cite both the software release
and the paper.
