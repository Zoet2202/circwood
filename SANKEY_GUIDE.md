# Circwood WP4 — Sankey Data Guide

This guide explains **how the project works end-to-end** and, for each input
dataset, **how it is ingested and what flows it produces** in the Norwegian
wood-flow Sankey diagram.

It complements the [README](README.md): the README is task-oriented (install,
run, deploy), this document is data-oriented (where every number comes from and
how it becomes an edge in the diagram).

---

## 1. The big picture

The project turns several public/industry datasets into a single tidy table of
**flows** — one row per `(source, target, volume, year, origin)` — that is then
rendered as a Plotly Sankey diagram and, optionally, re-expressed in embodied
carbon (kg C).

```
                 ┌──────────────────────────────────────────────┐
   SOURCES       │            INGEST            TRANSFORM         │      OUTPUT
                 │  (raw → tidy rows)     (derive process edges)  │
┌─────────────┐  │  ┌───────────────┐    ┌────────────────────┐  │  ┌──────────────┐
│ SSB tables  │──┼─▶│ ingest/ssb.py │──▶ │ transform/sawing   │  │  │ sankey_data  │
│ 07410 08801 │  │  │ ingest/trade  │    │ transform/mills    │──┼─▶│   .csv       │
│ 10513 11181 │  │  │ ingest/tre... │    │ transform/firewood │  │  │              │
│             │  │  └───────────────┘    │ transform/inventory│  │  │ carbon_data  │
├─────────────┤  │                       │ transform/assemble │  │  │   .csv       │
│ Treindustri.│──┼──────────────────────▶│  (mass balance)    │  │  └──────┬───────┘
│ FAO_Data    │──┼──────────────────────▶└────────────────────┘  │         │
└─────────────┘  └──────────────────────────────────────────────┘         ▼
                                                            viz/sankey.py + app
```

Two operating modes share the same transform/assembly code:

| Mode | Entry point | What it does | Needs |
|------|-------------|--------------|-------|
| **Cached** (default) | `build_sankey(use_cached_ssb=True)` | Reads the committed `data/processed/sankey_data.csv` | nothing — no network |
| **Fresh** | `build_sankey(use_cached_ssb=False)` / `circwood-build --fresh` | Re-fetches every SSB table over the API and rebuilds the dataset | `pip install -e .[ssb]` + network |

The hosted app and the test suite run in **cached** mode. The fresh pipeline
(`transform/assemble.py::run_fresh_pipeline`) is the canonical description of how
each raw dataset becomes flows, and is the code referenced throughout Section 3.

### Canonical flow schema

Every stage emits rows with these five columns:

| column | meaning |
|--------|---------|
| `source` | upstream node label |
| `target` | downstream node label |
| `volume` | flow magnitude in **m³** (becomes **kg C** in `carbon_data.csv`) |
| `year` | calendar year |
| `origin` | provenance string (which dataset/table the row came from) |

Volumes are always positive in the final dataset; direction is encoded by
`source`→`target`. The full node layout (labels, positions, colours) lives in
[`config/nodes.json`](config/nodes.json).

---

## 2. Project layout (data's-eye view)

```
config/
  ssb_queries/*.json      # one JSON-stat query per SSB table (the "API request")
  nodes.json              # Sankey node labels, x/y positions, colours
  carbon_densities.csv    # kg C/m³ per (species, state)        — editable
  carbon_nodes.csv        # per-node moisture state + species mix — editable
data/
  raw/                    # source inputs for the FRESH pipeline
    treindustrien_production_data.csv
    FAO_Data.csv
    varekoder.csv / .json # SSB commodity-code → trade-type lookups (reference)
    *.xlsx, input_data_curated.csv  # reference/source spreadsheets
  processed/              # GENERATED outputs (committed, used by the app)
    sankey_data.csv       # the volume Sankey
    carbon_data.csv       # same flows, carbon column
src/circwood/
  config.py               # all filesystem paths
  constants.py            # every coefficient (yields, splits, year window)
  ingest/                 # ssb.py, trade.py, treindustrien.py
  transform/              # sawing.py, mills.py, firewood.py, inventory.py, assemble.py
  carbon/                 # species_model.py, carbon.py
  viz/sankey.py           # Plotly figure builder (pure)
  pipeline.py             # build_sankey / build_carbon / CLI
app/streamlit_app.py      # web app
```

---

## 3. Each dataset: ingestion and use

There are **seven** input datasets. Five are SSB API tables, two are files
(Treindustrien, FAO). For each: what it is, how it's fetched/loaded, how it's
transformed, and which Sankey edges it produces.

> **Year alignment.** SSB rows arrive with *string* years (json-stat category
> labels); `assemble_sankey` coerces `year` to int up front, because the sawing
> math reindexes against integer Treindustrien years (a string/int mismatch
> silently zeroes the harvest inflow). Keep the query `Tid` windows consistent
> across tables — the model window is 2012–2022 (`constants.MIN_YEAR/MAX_YEAR`),
> so every table must cover at least those years or node balances and control
> totals break.
>
> **Two windows, not one.** `MIN_YEAR`/`MAX_YEAR` (2012–2022) is the *analysis*
> window the paper and the balance guarantee are stated over.
> `DATASET_MIN_YEAR`/`DATASET_MAX_YEAR` (2012–2024) is what the generated
> dataset *contains*: the years every source table covers. `assemble._finalize`
> clips to the latter. Rows outside it are not extra data — a year that only
> some tables have published reads as a collapse or a spike (firewood appeared
> to grow 808 % in 2025 for exactly this reason), and pre-2012 years carried
> trade with no harvest behind them.

> **SSB query format.** Each `config/ssb_queries/*.json` describes one request.
> The current files use the **PxWebApi v2** shape — `url`, `params` (query
> string: `lang`, `outputFormat`) and `payload` (the `selection` + `placement`
> body). `ingest/ssb.py` also still accepts the legacy v0 shape (`postUrl` +
> `queryObj` + `tableIdForQuery`), so a directory may mix the two during
> migration. The table id (the key the pipeline looks flows up by, e.g.
> `ingested.datasets["07410"]`) is taken from `tableIdForQuery`, or parsed from
> the v2 `url`/filename; two files resolving to the same id is an error, not a
> silent overwrite. All queries must request `json-stat2` and `lang=no` — the
> transforms below filter on Norwegian category labels — and `ingest/ssb.py` now
> *enforces* that rather than trusting it: missing settings are injected,
> conflicting ones raise `SSBQueryError` before any network call.

### 3.1 SSB 07410 — Roundwood removals (the backbone)

* **What:** commercial roundwood harvest by assortment (`sortiment`) and species,
  in 1000 m³. This is the trunk of the diagram — the wood actually cut and sold.
* **What:** commercial roundwood harvest by assortment (`sortiment`), in 1000 m³
  — the wood cut and sold, split into the mill-feeding assortments.
* **Query:** [`config/ssb_queries/ssbapi_table_07410.json`](config/ssb_queries/ssbapi_table_07410.json)
  (v2) selects nine `Treslag` (species × assortment) codes — **explicit codes, no
  `I alt`**, since the total would double-count. `Tid` covers 2012 onward to match
  the model window.
* **Ingest:** `ingest/ssb.py::process_query_files` POSTs the query (retrying
  429/5xx), caches the raw reply to `data/raw/ssb/07410.json`, parses it via
  `pyjstat`, and validates the columns and year coverage before the pipeline sees
  it. A failure raises rather than dropping the table.
* **Transform** (`assemble.run_fresh_pipeline`):
  * `value` is multiplied by 1000 (1000 m³ → m³).
  * Each `(sortiment)` is mapped to a **species pool → mill** edge:

    | SSB assortment | source | target |
    |----------------|--------|--------|
    | Gran/Furu/Lauvtre **sagtømmer** | Spruce/Pine/Non-coniferous | **Sawmills** |
    | … **massevirke** | Spruce/Pine/Non-coniferous | **Pulpmills** |
    | … **sams sagtømmer og massevirke** | Spruce/Pine/Non-coniferous | **Boardmills** |
  * A `Harvested wood → Residues from harvest` edge is added at
    `HARVEST_RESIDUE_PERCENTAGE` (10%) of total removals.
* **Used by:** the sawing transform (the `* → Sawmills` rows are its log input)
  and the reconciliation control total (pipeline sawtimber must tie to 07410).

### 3.1b SSB 03795 — Roundwood harvest by species (feeds the pools)

* **What:** total roundwood harvested (`Kvantum avvirket`) by species
  (`sortiment`: Gran/Furu/Lauv/Ved til brensel), whole country, **already in m³**
  (no ×1000).
* **Query:** [`ssbapi_table_03795.json`](config/ssb_queries/ssbapi_table_03795.json)
  (v2, `lang=no`). `ContentsCode`/`Region` resolve to a single measure and the
  national total.
* **Transform:** Gran/Furu/Lauv → **`Harvested wood → Spruce/Pine/Non-coniferous`**
  (the species-pool inflow that balances the pools against 07410's pool→mill
  outflows). The harvest total also drives **`Harvested wood → Bark`** at
  `BARK_PERCENTAGE` (10%).
* **Used by:** species-pool mass balance and the carbon harvest mix.

> **Removed: SSB 06289 and the Mortality node.** The model no longer tracks
> mortality: table 06289, the `Harvested wood → Mortality` edge, the `Mortality`
> node (`nodes.json` / `carbon_nodes.csv`) and `MORTALITY_FACTOR` have all been
> removed from the pipeline, config and diagram.

### 3.2 SSB 08801 — Foreign trade (imports & exports)

* **What:** external trade in wood commodities by `varenummer` (commodity code),
  in two quantity measures; `Mengde 2 (M2)` is the m³ measure used here.
* **Query:** [`ssbapi_table_08801.json`](config/ssb_queries/ssbapi_table_08801.json)
  — a large `Varekoder` selection over European trading partners.
* **Ingest:** JSON-stat POST → DataFrame.
* **Transform:**
  * Each commodity string is classified into a **trade type** by keyword
    (`Impregnert`→impregnated, `Massevirke`→pulpwood, `Skurtømmer`→saw wood,
    `Trepeletter`→pellets, `brensel`→firewood, `høvellast`→planed wood,
    `skurlast`→sawn wood). The human-readable code↔type lookups are mirrored in
    [`data/raw/varekoder.csv`](data/raw/varekoder.csv)/`.json` for reference.
  * `ingest/trade.py::assign_target_source_trade` maps each `(type, direction)`
    to a `(source, target)` via `TRADE_MAPPING`. **Convention:** import labels
    sit in the **source** column, export labels in the **target** column — the
    sawing transform relies on this.
  * Volumes are summed per `(year, source, target)`.
* **Edges produced** (examples): `Import saw wood → Sawmills`,
  `Sawmills → Export sawn wood`, `Import pulpwood → Pulpmills`,
  `Import planed wood → WPP`, `Firewood → Export Firewood`, etc.
* **Special handling — raw-log re-exports:** 08801 routes re-exported sawlogs
  (`Skurtømmer` eksport) as `Sawmills → Export Sawmills`, i.e. *through* the saw
  even though they're never sawn. `assemble._bypass_raw_reexport` re-routes that
  volume straight from the species pools (allocated by each species' share of
  sawlog inflow), so the Sawmills node balances and the ~26% chip yield is
  measured against logs actually sawn. The same bypass handles raw pulpwood
  re-exports around Pulpmills.
* **Net-export collapse:** for three products the gross import/export pair is
  collapsed into a single **net-export node** (`assemble._apply_net_exports`,
  the last assembly step):

  | product | net node | source |
  |---------|----------|--------|
  | sawlogs (skurlast) | `Net export skurlast` | species pools |
  | pulpwood (massevirke) | `Net export massevirke` | species pools |
  | sawn timber | `Net export sawn timber` | Sawmills |

  `net = gross export − gross import`; the import volume is folded into the
  domestic `source → mill` edge so every node stays mass-balanced (mill
  throughput is unchanged). Years where imports exceed exports are drawn as
  `Net export … → mill` so direction stays physical. The gross `Import *`/
  `Export *` edges for these three products no longer appear. *(Other products —
  planed, impregnated, firewood, boards, pellets — keep their gross edges.)*

### 3.3 SSB 10513 — Waste treatment (end-of-life)

* **What:** waste wood (`AvfallsType` = 06) by treatment method
  (`behandlingsmåte`), 1000 tonnes.
* **Query:** [`ssbapi_table_10513.json`](config/ssb_queries/ssbapi_table_10513.json).
* **Ingest:** JSON-stat POST → DataFrame; zero rows dropped.
* **Transform:** tonnes → m³ via `value × 1000 / 483` (483 kg/m³ basic density).
  Treatment method is mapped to a target: `Levert til materialgjenvinning` →
  **Material recovery**, everything else → **Energy**. Two edge sets are emitted:
  * **`Long-term USE → End-of-Life`** (total EOL per year), and
  * **`End-of-Life → Energy`** / **`End-of-Life → Material recovery`** (the split).
* **Used by:** the downstream/disposal end of the diagram.

### 3.4 SSB 11181 — Firewood consumption

* **What:** firewood (`virkesgruppe`: coniferous vs `Ved til brensel lauv`),
  1000 m³.
* **Query:** [`ssbapi_table_11181.json`](config/ssb_queries/ssbapi_table_11181.json).
* **Ingest:** JSON-stat POST → DataFrame.
* **Transform** (`transform/firewood.py::create_firewood_rows`):
  * Coniferous firewood is split spruce/pine by `FIREWOOD_PERCENT_SPRUCE` (50%);
    the `lauv` group is non-coniferous.
  * For each species it emits a **`Harvested wood → species`** edge and a
    **`species → Firewood`** edge.
* **Downstream:** in assembly, all `* → Firewood` inflow is aggregated into a
  single **`Firewood → Energy`** edge.

### 3.5 Treindustrien annual report — production & mill inventories

* **What:** sawmill-industry production (sawn/planed/impregnated timber) and
  mill stock levels, from the Treindustrien *årsrapport*. A **file**, not an API:
  [`data/raw/treindustrien_production_data.csv`](data/raw/treindustrien_production_data.csv)
  with columns `[year, Category, Volume]`.
* **Ingest** (`ingest/treindustrien.py`):
  * `load_treindustrien` reads the CSV.
  * `production_wide` pivots the three "produced …" categories into
    `[year, Volume_sawn, Volume_planed, Volume_imp]`.
  * `signed_inventory` / `sawmills_inventory_changes` compute the **signed**
    year-over-year `VolumeChange` per inventory category (sign preserved so the
    flow direction is known).
* **Transform — this is where most process edges are derived:**
  * **Sawing** (`transform/sawing.py::create_flow_rows`): given the
    `* → Sawmills` log inflow (07410), saw-wood imports/exports (08801) and the
    Treindustrien production + inventory frames, it derives every sawmill output
    by mass balance. By-product shares apply to the **raw log input**:
    `SAWDUST 0.14 → Boardmills`, `CELLULOSE 0.26 → Pulpmills`, `SCRAPS 0.05 →
    Energy`, `SHRINKAGE 0.05 → Shrinkage`; the residual `SAWN_YIELD 0.50` →
    Planingmills / WPP. Planing and impregnation edges follow.
    *(See [`Sawing_function_diagnosis.docx`](Sawing_function_diagnosis.docx) and
    the README "sawing-function fix" for the three bugs this code fixes.)*
  * **Inventory** (`transform/inventory.py::create_inventory_rows`): each signed
    `VolumeChange` becomes a stock edge whose **direction** depends on the sign
    (build-up = `mill → inventory`, draw-down = `inventory → mill`) and whose
    **width** is the absolute magnitude.
* **Used by:** sawing, planing, impregnation and inventory edges; also the
  reconciliation check that ties pipeline totals back to this CSV.

### 3.6 FAO_Data (FAOSTAT) — board-mill trade

* **What:** board (panel) production trade not well covered by SSB. A **file**,
  pre-shaped into the canonical schema:
  [`data/raw/FAO_Data.csv`](data/raw/FAO_Data.csv) already has
  `year, volume, source, target, origin` columns (e.g. `Boardmills → Export
  Boards`, origin `FAOSTAT`).
* **Ingest:** read directly with `pd.read_csv(config.FAO_CSV)`.
* **Transform:** none — it is concatenated straight into the primary rows.
* **Used by:** the Boardmills export side of the diagram.

---

## 4. Assembly: from primary rows to a balanced dataset

All the dataset rows above are concatenated into a **primary** frame, then
`transform/assemble.py::assemble_sankey` derives every *process* edge so the
result is mass-consistent regardless of source:

0. Coerce `year` to int (SSB rows carry string years; see the year-alignment note in §3).
1. `_bypass_raw_reexport` — reroute re-exported raw roundwood around the mills:
   sawlogs around Sawmills (→ `Export Sawmills`) and pulpwood around Pulpmills
   (→ `Export pulpwood`), allocated by species share so the pools stay balanced.
2. `create_flow_rows` — sawing/planing/impregnation edges (§3.5).
3. `create_inventory_rows` — mill stock-change edges (§3.5).
4. `boardmill_process` — split Boardmills inflow into `Loss Boardmills`
   (`BOARDMILL_LOSS` 5%) and `WPP` (95%).
5. `pulpmills_process` — split Pulpmills inflow into `Short-term USE` (86%),
   `Energy` (8%), `Loss` (6%).
6. Aggregates: `Firewood → Energy`, and the `WPP →` split into `Long-term USE`
   (`WPP_LONG_TERM` 90%) and `Energy` (`WPP_ENERGY` 10%).
7. `_finalize` — coerce types, take absolute volumes, and **dedupe** to one row
   per `(year, source, target)` (summing volumes, joining `origin` strings).
8. `_apply_net_exports` — collapse the gross import/export pairs for sawlogs,
   pulpwood and sawn timber into single net-export nodes (§3.2), preserving every
   node's mass balance.

Every coefficient used here lives in [`constants.py`](src/circwood/constants.py).
The default analysis window is `MIN_YEAR=2012` … `MAX_YEAR=2022`.

### Mass balance & reconciliation

* `assemble.node_balance(df, node)` returns per-year inflow/outflow/imbalance for
  any node; the app exposes this as the "Mass balance check" panel.
* `validate.py` + `tests/test_balance.py` assert the process nodes balance within
  ~1%; use-phase and inventory nodes are reservoirs by design.
* `reconcile.py` + `tests/test_reconcile.py` tie pipeline totals to the
  Treindustrien CSV and the SSB 07410 control total.
* To re-derive a balanced dataset from cached primary rows (no network):
  `regenerate_balanced(write=True)` strips the `DERIVED_EDGES` and re-runs
  assembly using the local Treindustrien CSV.

---

## 5. Carbon overlay

`carbon_data.csv` is the same flow table with `volume` (m³) converted to embodied
carbon (kg C). The model is **species-accurate**:

```
carbon(flow) = volume × Σ_species  share_species × density(species, state)
```

* **Densities** — [`config/carbon_densities.csv`](config/carbon_densities.csv):
  `kg_c_per_m3` per `(species, state)`, where state ∈ {`raw`, `dry`} (moisture).
* **Per-node rules** — [`config/carbon_nodes.csv`](config/carbon_nodes.csv): each
  node's leaving-`state` and `mix_mode`:
  * `fixed` — use the given `spruce/pine/nonconif_share` (species pools, imports).
  * `propagate` — compute the species mix from the node's **inflows**, solved to
    a fixed point so inventory cycles converge (mills, use phases, inventories).
* **Code** — `carbon/species_model.py`: `species_mix(df)` returns the mix per
  `(year, node)`; `add_carbon(df)` adds the `carbon` column. Edges leaving
  `Harvested wood` carry the target pool's species (by-products carry the year's
  harvest mix).

Both CSVs are designed to be edited in Excel — change a value, re-run
`build_carbon(write=True)` (or the app), and the carbon figures update. No code
change needed.

---

## 6. Visualisation & app

`viz/sankey.py` is pure (no I/O, no `fig.show()`):

* `load_node_params` reads labels/positions/colours from `nodes.json`.
* `prepare_flows` filters to the year window, applies `NODE_RENAME_MAP` (data-side
  names → diagram labels), scales `volume` (÷1e6 for Mm³, ÷1e9 for Mt C),
  optionally averages over the window, and drops flows touching hidden nodes.
* `remap_ids` maps node names to integer ids (flows with unknown nodes are
  dropped). `build_figure` / `make_sankey` produce the Plotly figure.

`app/streamlit_app.py` calls `build_sankey(use_cached_ssb=True)` once (cached),
then `make_sankey` for the figure and `species_mix` for the composition panel.
Sidebar controls: metric (Mm³ vs Mt C), annual-average vs single year, year
range, and a node selector. A CSV download exports the current window.

---

## 7. How to regenerate everything

```bash
# Volume dataset, from cached primary rows (no network):
python -c "from circwood.transform.assemble import regenerate_balanced; regenerate_balanced(write=True)"

# Volume dataset, fresh from the SSB API (needs .[ssb] extras + network):
circwood-build --fresh        # == build_sankey(use_cached_ssb=False)

# Carbon dataset (re-reads the carbon CSVs):
python -c "from circwood import build_carbon; build_carbon(write=True)"

# Verify balance & reconciliation:
pytest
```

Commit the regenerated `data/processed/*.csv` to refresh the hosted app.
