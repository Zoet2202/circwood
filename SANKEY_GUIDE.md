# Circwood WP4 — Sankey Data Guide

This guide explains **how the project works end-to-end** and, for each input
dataset, **how it is ingested and what flows it produces** in the Norwegian
wood-flow Sankey diagram.

It complements the [README](README.md) (task-oriented: install, run, deploy),
[METHODS.md](METHODS.md) (every assumption and derivation, written for the
paper) and [DATA_DICTIONARY.md](DATA_DICTIONARY.md) (every node, edge, column
and file). This document is data-oriented: where every number comes from and
how it becomes an edge in the diagram.

---

## 1. The big picture

The project turns public and industry datasets into a single tidy table of
**flows** — one row per `(source, target, volume, year, origin, region,
species, assortment, entity_id, destination)` — that is rendered as a Plotly
Sankey diagram and re-expressed in embodied carbon (kg C).

```
                 ┌──────────────────────────────────────────────┐
   SOURCES       │            INGEST            TRANSFORM         │      OUTPUT
                 │  (raw → tidy rows)     (derive process edges)  │
┌─────────────┐  │  ┌───────────────┐    ┌────────────────────┐  │  ┌──────────────┐
│ SSB tables  │──┼─▶│ ingest/ssb.py │──▶ │ transform/sawing   │  │  │ sankey_data  │
│ 03795 07410 │  │  │ ingest/trade  │    │ transform/mills    │──┼─▶│   .csv       │
│ 08801 08799 │  │  │ ingest/tre... │    │ transform/firewood │  │  │              │
│ 10513 11181 │  │  └───────────────┘    │ transform/inventory│  │  │ carbon_data  │
├─────────────┤  │                       │ transform/assemble │  │  │   .csv       │
│ Treindustri.│──┼──────────────────────▶│  (mass balance)    │  │  └──────┬───────┘
│ FAO_Data    │──┼──────────────────────▶│ transform/dimensions│ │         │
└─────────────┘  └──────────────────────────────────────────────┘         ▼
                                                   viz/sankey.py · app · site bundle
```

Two operating modes share the same transform/assembly code:

| Mode | Entry point | What it does | Needs |
|------|-------------|--------------|-------|
| **Cached** (default) | `build_sankey(use_cached_ssb=True)` | Reads the committed `data/processed/sankey_data.csv` | nothing — no network |
| **Fresh** | `circwood-refresh`, or `build_sankey(use_cached_ssb=False)` / `circwood-build --fresh` | Re-fetches every SSB table over the API and rebuilds the dataset (the refresh also gates it) | `pip install -e .[ssb]` + network |

The app, the site and the test suite run in **cached** mode. The fresh pipeline
(`transform/assemble.py::run_fresh_pipeline`) is the canonical description of
how each raw dataset becomes flows, and is the code referenced throughout
Section 3. `ingest.ssb.load_cached_result()` rebuilds a fresh run's inputs
offline from the raw-response cache (`data/raw/ssb/`, git-ignored).

### Canonical flow schema

| column | meaning |
|--------|---------|
| `source`, `target` | node labels, exactly as in `config/nodes.json` |
| `volume` | m³ solid wood (`carbon`, `carbon_low`, `carbon_high` in kg C in `carbon_data.csv`) |
| `year` | calendar year, 2012–2024 |
| `origin` | provenance string (which table or coefficient the row came from) |
| `region` | ISO county code on the harvest edges (2024 boundaries), `NO` = not regionally attributed |
| `species` | `spruce` / `pine` / `non-coniferous`; mixed flows split by the propagated mix |
| `assortment` | `sawtimber` / `pulpwood` / `sams` / `fuelwood` where the source distinguishes it, else `all` |
| `entity_id` | always `national` (the column exists for member-level data under `docs/DATA_CONFIDENTIALITY.md`) |
| `destination` | `domestic`, an ISO alpha-2 partner code on export rows (SSB 08799 shares), or `unspecified` |

Volumes are positive; direction is `source → target`. Summing `volume` over the
five dimension columns recovers the five-column national table exactly
(`transform/dimensions.py`). The node layout (labels, positions, colours) is
[`config/nodes.json`](config/nodes.json).

---

## 2. Project layout (data's-eye view)

```
config/
  ssb_queries/*.json      # one query per flow table (08799 in three chunks)
  ssb_queries/forest/     # the six forest-inventory tables (§3.7)
  ssb_queries/economic/   # prices: 07413, 08360 (§3.8)
  nodes.json              # Sankey node labels, x/y positions, colours
  carbon_densities.csv    # kg C/m³ per (species, state) + low/high   — editable
  carbon_nodes.csv        # per-node state, mix rule, carries_carbon   — editable
  hwp_half_lives.csv      # product-class half-lives for the stock      — editable
  coefficient_ranges.csv  # ranges for the β sensitivity                — editable
  prices.csv              # carbon price, energy yield (assumptions)     — editable
data/
  raw/                    # inputs of the FRESH pipeline: Treindustrien CSV,
                          # FAO_Data.csv, harvest_by_fylke.csv, exports_by_partner.csv,
                          # prices_ssb.csv (the last three rewritten by a fresh run)
  processed/              # the committed artifact and every derived file
src/circwood/             # the package (see CLAUDE.md for the module map)
models/                   # separate models exchanging files (rotation, stock)
app/streamlit_app.py      # the internal app
site/                     # the public static site
```

---

## 3. Each dataset: ingestion and use

> **Year alignment.** SSB rows arrive with *string* years (json-stat category
> labels); `assemble_sankey` coerces `year` to int up front, because the sawing
> math reindexes against integer Treindustrien years. Keep the query `Tid`
> windows consistent across tables.
>
> **Two windows, not one.** `MIN_YEAR`/`MAX_YEAR` (2012–2022) is the *analysis*
> window the paper and the balance guarantee are stated over.
> `DATASET_MIN_YEAR`/`DATASET_MAX_YEAR` (2012–2024) is what the generated
> dataset *contains*: the years every flow table covers. `assemble._finalize`
> clips to the latter; since Phase J the refresh derives that window from the
> fetched coverage and proposes a widening rather than relying on memory
> (`docs/REFRESH_POLICY.md`).
>
> **SSB query format.** Each query file is PxWebApi v2 (`url`, `params`,
> `payload`); the legacy v0 shape is still accepted. The table id is taken from
> `tableIdForQuery`, else the `url`, else the filename; two files resolving to
> the same id is an error. All queries must request `json-stat2` and `lang=no`
> — the transforms filter on Norwegian category labels — and `ingest/ssb.py`
> enforces that before any network call. Every table has a `TableContract`
> (required columns, year coverage, period type, publication lag, whether it
> takes part in the year window).

### 3.1 SSB 07410 — Roundwood removals by assortment (the backbone)

* **What:** commercial roundwood removals by assortment and species, in
  1 000 m³ — the wood cut and sold, split into the mill-feeding assortments.
* **Query:** nine explicit `Treslag` codes (species × assortment), no `I alt`.
* **Transform:** `value × 1000`; each assortment maps to a **species pool → mill**
  edge: sagtømmer → `Sawmills`, massevirke → `Pulpmills`, sams sagtømmer og
  massevirke → `Boardmills`. `Harvested wood → Residues from harvest` is added
  at `HARVEST_RESIDUE_PERCENTAGE` (10 %) of total removals.
* **Used by:** the sawing transform and the reconciliation control total
  (domestic sawtimber 2012–2022 = 61 445 000 m³).

### 3.1b SSB 03795 — Roundwood removals by species (feeds the pools)

* **What:** removals for sale by species (Gran, Furu, Lauv), national and by
  county (`agg_KommFylker`, the 2024 county boundaries backcast), in m³.
  Firewood is not in this table from 2006 on (SSB's own note).
* **Transform:** `Harvested wood → Spruce/Pine/Non-coniferous`; the county
  pull becomes `data/raw/harvest_by_fylke.csv` and the exact per-county rows of
  those edges; the harvest total drives `Harvested wood → Bark` at
  `BARK_PERCENTAGE` (10 %).

### 3.2 SSB 08801 — Foreign trade (imports & exports)

* **What:** external trade in wood commodities by `varenummer`; `Mengde 2 (M2)`
  is the m³ measure used.
* **Transform:** commodity labels are classified by keyword into seven trade
  types (`ingest/trade.py`); imports sit in the `source` column, exports in
  `target`; volumes summed per `(year, source, target)`.
* **Raw-log re-exports** are routed from the pools straight to export so the
  mills only see wood they process (`assemble._bypass_raw_reexport`).
* **Net-export collapse** for sawlogs, pulpwood and sawn timber: the gross
  import/export pair becomes one `Net export …` node per year; the import is
  folded into the domestic `pool → mill` edge; a net-import year is drawn in
  reverse. Firewood, planed wood, boards and pellets keep gross edges.

### 3.2b SSB 08799 — Exports by partner country (the `destination` dimension)

* **What:** the by-country sibling of 08801 — same codes, same m³ measure,
  monthly, `Land` codes = ISO alpha-2 (`97` = unspecified). Three five-year
  query chunks (SSB's 800 000-cell limit); `contract_for` derives a per-chunk
  monthly contract bounded by the chunk's own selection.
* **Transform** (`transform/destinations.py`): months to years, commodities to
  the same trade types as 08801, labels to codes; committed as
  `data/raw/exports_by_partner.csv`; every row into an export node is split by
  each partner's *share* of that (year, type) — shares, because the netted edges
  have no partner-level figure. 61 destinations in the current dataset.

### 3.3 SSB 10513 — Waste treatment (end of life)

* **What:** wood waste (`AvfallsType` 06) delivered to incineration
  (`AvfallsBehand` 21), in **1 000 tonnes** (SSB's unit, confirmed from the
  table's metadata).
* **Transform:** `value × EOL_UNIT_KG / WASTE_WOOD_DENSITY_KG_M3`
  (1 000 t → kg → m³ at 483 kg/m³), about 1.35 Mm³/yr over 2012–2022. Two
  edges: `Long-term USE → End-of-Life` and `End-of-Life → Energy`.
* **By design incineration only:** Norwegian waste wood is effectively all
  incinerated; the edge quantifies the recoverable potential. There is no
  `Material recovery` node, and a guard raises if the query is widened without
  explicit routing. Until Phase K (2026-11-08) the conversion divided by 1 000
  too few and the edge was invisible.

### 3.4 SSB 11181 — Firewood removals

* **What:** `Avvirkning av vedvirke` — firewood removals, coniferous and
  broadleaf, in 1 000 m³, **with bark**, estimated from household and cabin
  consumption surveys and covering firewood sold and cut for own use.
* **Transform** (`transform/firewood.py`): coniferous split spruce/pine by
  `FIREWOOD_PERCENT_SPRUCE` (50 %); each species gets `Harvested wood → species`
  and `species → Firewood`. The firewood component of the harvest edges is the
  national remainder row (`region = "NO"`) because SSB never regionalises it.
* **Downstream:** `Firewood → Energy` is the residual of the node's inflow
  (removals plus `Import Firewood`) after `Firewood → Export Firewood`, so the
  node balances and is one of `validate.PROCESS_NODES` (since Phase K; before
  that the export was added on top of an Energy edge that already carried the
  whole inflow).

### 3.5 Treindustrien annual report — production & mill inventories

* **What:** sawn, planed and impregnated production and the mill stock levels,
  2011–2023, as a hand-maintained file,
  [`data/raw/treindustrien_production_data.csv`](data/raw/treindustrien_production_data.csv).
* **Sawing** (`transform/sawing.py`): by-products as shares of the **raw log
  input** (`SHRINKAGE` 0.05 → water, `SAWDUST` 0.14 → Boardmills, `CELLULOSE`
  0.26 → Pulpmills, `SCRAPS` 0.05 → Energy), the residual `SAWN_YIELD` 0.50 to
  planing (`PLANED_INPUT_FACTOR` 1.05 × planed production), export, stock
  change and WPP. Planing: `BYPRODUCT_PLANING` 0.05 to Boardmills, impregnated
  production to Impregnation, the rest to WPP. The step recognises both the
  gross trade spellings (fresh run) and the netted ones (a rebuild), so
  `regenerate_balanced` reproduces the committed flows exactly.
* **Inventory** (`transform/inventory.py`): each signed stock change becomes an
  edge whose direction follows the sign (build-up `mill → inventory`,
  draw-down `inventory → mill`); the timber and sawn-timber categories share
  the `Timber inventory` node.
* **Used by:** the sawing, planing, impregnation and inventory edges, and the
  reconciliation that ties planed and impregnated throughput back to the file.
  The series ends at 2023, so 2024 `Sawmills` emits nothing; the owner kept
  2024 in the dataset and every derived analysis refuses it on its own gate.

### 3.6 FAO_Data (FAOSTAT) — board trade (legacy)

* **What:** board imports 2005–2022 (`Import Boards → Boardmills`) and board
  exports 2002–2004, a hand-pasted file in the canonical schema.
* **Status:** the import rows are the only ones inside the dataset window; the
  `Export Boards` node predates `DATASET_MIN_YEAR` and is gone (decision
  2026-07-28). FAOSTAT is *not* the partner-country source: its REST API now
  needs an account and its bilateral trade domain is discontinued (checked
  2026-11-08); partners come from SSB 08799 (§3.2b).

### 3.7 SSB 14589 / 14590 / 14591 / 14593 / 14594 / 06289 — the forest resource (Phase H)

* **What:** the National Forest Inventory (Landsskogtakseringen, NIBIO) as SSB
  publishes it: standing volume and gross increment under bark (1 000 m³, by
  region × species), forest area (km²), forest types, development-class and
  site-class shares (per cent), and the national series from 1933. Rolling
  five-year intervals (`2021-2025`), nine *takserte regioner*.
* **Query:** `config/ssb_queries/forest/`, processed by `circwood-forest` and
  by the refresh; contracts with `period="interval"`, not window-gated.
* **Transform** (`transform/forest.py`): one tidy table,
  `data/processed/forest_resource.csv`, keyed on codes (SSB's Norwegian label
  for development class IV reads "VI"), regions crosswalked to the county
  codes (`NFI_REGIONS`). Not a flow: it sits beside the Sankey as the resource
  behind `Harvested wood`. `increment_vs_harvest` reports the 2012–2022 harvest
  at 0.55 of the 2021–2025 increment on productive forestry land (0.49 of all
  land), with its caveats (interval vs calendar year; under bark vs a firewood
  component with bark; productive forestry land excludes protected forest).

### 3.8 SSB 07413 / 08360 — prices (Phase L)

* **What:** average roundwood prices by assortment (NOK/m³, annual) and
  wholesale electricity prices (øre/kWh, series *Elspotmarknaden*).
* **Query:** `config/ssb_queries/economic/`, processed by `circwood-economics`
  and by the refresh into `data/raw/prices_ssb.csv`.
* **Used by:** `transform/economics.py` → `economic_value.csv` (§5.3) and the
  rotation model's reference prices.

---

## 4. Assembly: from primary rows to a balanced dataset

All primary rows are concatenated, then `transform/assemble.py::assemble_sankey`
derives every *process* edge so the result is mass-consistent regardless of
source:

0. Coerce `year` to int.
1. `_bypass_raw_reexport` — route re-exported sawlogs and pulpwood around the
   mills, allocated by species share.
2. `create_flow_rows` — sawing/planing/impregnation edges (§3.5).
3. `create_inventory_rows` — mill stock-change edges.
4. `boardmill_process` — `Loss Boardmills` (`BOARDMILL_LOSS` 5 %) and WPP.
5. `pulpmills_process` — `Short-term USE` 86 %, `Energy` 8 %, `Loss Pulpmills` 6 %.
6. Aggregates: `Firewood → Energy` (inflow less exports) and the WPP split into
   `Long-term USE` (`WPP_LONG_TERM` 90 %) and `Energy` (10 %). The mill splits
   are overridable through `transform.mills.MillCoefficients`.
7. `_finalize` — types, absolute volumes, clip to the dataset window, dedupe per
   `(year, source, target)`.
8. `_apply_net_exports` — the net-export nodes (§3.2).
9. `add_dimensions` — region, species, assortment, entity_id, destination.

Every coefficient lives in [`constants.py`](src/circwood/constants.py).

### Mass balance, carbon balance, reconciliation

* `validate.assert_window_balanced` / `assert_slices_balanced`: every node in
  `PROCESS_NODES` (Sawmills, Pulpmills, Boardmills, Planingmills, Impregnation,
  WPP, Firewood) balances within 1 % over 2012–2022, in aggregate and per
  (region, species) slice. Species pools, use phases and inventories are
  reservoirs by design.
* `validate.assert_carbon_balanced`: the same nodes conserve carbon (Phase G).
* `reconcile.py`: planed and impregnated throughput tie to the Treindustrien
  file; domestic sawtimber ties to the SSB 07410 control total.
* `regenerate_balanced(write=True)` re-derives a balanced dataset from the
  committed table's primary rows without network, and reproduces it exactly.

---

## 5. Carbon and the derived analyses

### 5.1 Carbon (`carbon_data.csv`)

`carbon = volume × ρ(year, source node, species)`. Densities and node rules
are the two editable CSVs; since Phase G **carbon follows the wood**: it enters
at harvest (raw density by species) and at imports (configured density), every
propagate node emits at one effective density (carbon in over wood volume out,
so a node that loses water emits denser wood), and the water sink carries none
(`carries_carbon = 0`). Carbon is therefore conserved wherever volume is.
`carbon_low`/`carbon_high` repeat the model at the file's low/high densities.
`carbon/species_model.py` holds the propagation; `carbon/carbon.py` the
overlay. CO₂e is a display conversion (`CO2_PER_C`).

### 5.2 β, its sensitivity, the HWP stock

* `analysis/hwp.py` → `beta_series.csv`: the production-basis HWP fraction,
  traced on conserved carbon with proportional mixing; gated; 2012–2023.
* `analysis/sensitivity.py` → `beta_sensitivity.csv`: β with each fixed
  coefficient at the bounds of `config/coefficient_ranges.csv` plus the density
  envelope; the source of `ci_low`/`ci_high` (an assumption envelope).
* `carbon/stock.py` → `hwp_stock.csv`: carbon stored in products, first-order
  decay, `config/hwp_half_lives.csv`, cohort and steady-state initialisations,
  the observed 10513 outflow alongside.

### 5.3 Economic value and the models

* `transform/economics.py` → `economic_value.csv`: roundwood at the pool gate at
  07413 prices, energy at 753 kWh/m³ × the 08360 day-ahead price, carbon
  stored and released at the carbon price of `config/prices.csv`. Processed
  products are not priced (no published price per m³).
* `models/rotation` → `rotation_results.csv` and `models/forest_stock` →
  `forest_stock_projection.csv`: separate models that read β, the prices and
  the inventory; see `models/README.md` and METHODS §9.

---

## 6. Visualisation, app and site

`viz/sankey.py` is pure (no I/O): `load_node_params` reads `nodes.json`;
`prepare_flows` filters to the window, scales (÷1e6 for Mm³, ÷1e9 for Mt C),
drops hidden nodes and, in average mode, divides each edge's window total by
the number of dataset years in the window (so the averaged diagram balances —
Phase K); `remap_ids` maps labels to ids (a label missing from `nodes.json`
would drop its flows silently, which is why the node-coverage fence exists);
`build_figure` honours the fixed positions.

`app/streamlit_app.py` (internal) shows the flows in volume, carbon or CO₂e,
the carbon fate and β, the sensitivity, the HWP stock, the forest resource,
the economics and the models, mass balance and provenance, off the committed
files. `export/bundle.py` serialises the committed carbon table, the node
positions, the manifest vintage and the forest summary into
`site/data/circwood.json` through the confidentiality gate; `site/` renders it
with Plotly.js and deploys to GitHub Pages on merge to `main`.

---

## 7. How to regenerate everything

```bash
# Everything, from live SSB, behind the gates (needs .[ssb] + network):
circwood-refresh --report r.md

# Pieces, offline from the committed data:
python -c "from circwood.transform.assemble import regenerate_balanced; regenerate_balanced(write=True)"
python -c "from circwood import build_carbon; build_carbon(write=True)"
python -c "from circwood.analysis.sensitivity import export_sensitivity; export_sensitivity()"
python -c "from circwood.analysis.hwp import export_beta_series; export_beta_series()"
python -c "from circwood.carbon.stock import export_hwp_stock; export_hwp_stock()"
python -c "from circwood.transform.economics import export_economic_value; export_economic_value()"
python -m models.rotation.rotation && python -m models.forest_stock.projection

# Side layers from live SSB:
circwood-forest && circwood-economics

# Verify:
pytest
```

A regenerated dataset changes `tests/fixtures/golden_sankey_data.csv` and every
derived file in the same commit; the tests say so when one is left behind.
