# Data dictionary

One table each for the nodes, the edges, the columns and the files of the
Circwood WP4 dataset. `tests/test_docs.py` checks that every node and every
edge of the committed dataset appears here, so a dataset change that adds or
renames one fails until this file follows. Coefficients are the names in
`src/circwood/constants.py`; provenance tags are the `origin` strings carried
by the rows.

## Nodes

| Node | Kind | What it is |
|---|---|---|
| Harvested wood | source | Roundwood removals: SSB 03795 (commercial) plus SSB 11181 (firewood, with bark). |
| Spruce | species pool | Spruce removals, balanced against the 07410 assortments, firewood and net roundwood exports. |
| Pine | species pool | As Spruce. |
| Non-coniferous | species pool | Broadleaf removals; almost entirely firewood. |
| Bark | sink | `BARK_PERCENTAGE` 10 % of the 03795 total. A coefficient, not a measurement. |
| Residues from harvest | sink | `HARVEST_RESIDUE_PERCENTAGE` 10 % of the 07410 total. Left in the forest. |
| Import planed wood | source | SSB 08801 planed-wood imports, straight to WPP. |
| Import Boards | source | FAOSTAT board imports (legacy file), to Boardmills. |
| Import Firewood | source | SSB 08801 firewood imports. |
| Sawmills | process | Sawing; by-products as shares of raw log input (`SHRINKAGE`, `SAWDUST`, `CELLULOSE`, `SCRAPS`, residual `SAWN_YIELD`). |
| Timber inventory | inventory | Treindustrien timber and sawn-timber stocks (two categories, one node); signed year-over-year change. |
| Shrinkage | water sink | Water lost at the saw (5 % of log volume). Carries no carbon. |
| Planingmills | process | Planing; Treindustrien planed production × `PLANED_INPUT_FACTOR`. |
| Planed timber inventory | inventory | Treindustrien planed-timber stock. |
| Impregnation | process | Treindustrien impregnated production. |
| Impregnated inventory | inventory | Treindustrien impregnated-timber stock. |
| Boardmills | process | Panels: `BOARDMILL_LOSS` 5 %, the rest to WPP. |
| Loss Boardmills | sink | Board-mill process loss. |
| Pulpmills | process | Pulp and paper: `PULP_SHORT_TERM` 86 %, `PULP_ENERGY` 8 %, `PULP_LOSS` 6 %. |
| Loss Pulpmills | sink | Pulp-mill process loss. |
| Wood Product processing & Logistics | process | Processed wood before use: `WPP_LONG_TERM` 90 % to Long-term USE, `WPP_ENERGY` 10 % to Energy. |
| Long-term USE | use phase (reservoir) | Long-lived products; emits only the 10513 end-of-life flow. |
| Short-term USE | use phase (reservoir) | Pulp and paper products. |
| End-of-Life | process | Waste wood delivered to incineration, SSB 10513 (1 000 t → m³ at `WASTE_WOOD_DENSITY_KG_M3` 483). |
| Energy | sink | Wood burnt for energy, from Firewood, Sawmills scraps, Pulpmills, WPP and End-of-Life. |
| Firewood | process | Firewood: removals plus imports in; exports and energy out (balances since Phase K). |
| Export Firewood | export sink | SSB 08801 firewood exports (gross). |
| Export planed wood | export sink | SSB 08801 planed-wood exports (gross). |
| Net export skurlast | net-export node | Sawlog exports net of imports; a net-import year reverses the edge. |
| Net export massevirke | net-export node | Pulpwood exports net of imports. |
| Net export sawn timber | net-export node | Sawn-timber exports net of imports; a source into WPP in 2012–2014. |

## Edges

Every `(source, target)` of the committed dataset, with the source table or
coefficient behind it and the provenance tag the rows carry.

| Edge | Derived from | Provenance tag(s) |
|---|---|---|
| Harvested wood → Spruce / Pine / Non-coniferous | 03795 by species and county + 11181 firewood (national remainder row) | `SSB table 03795`, `SSB table 03795 \| SSB table 11181` |
| Harvested wood → Bark | `BARK_PERCENTAGE` × 03795 total | `SSB table 03795` |
| Harvested wood → Residues from harvest | `HARVEST_RESIDUE_PERCENTAGE` × 07410 total | `SSB table 07410` |
| Spruce / Pine / Non-coniferous → Sawmills | 07410 sawlogs, less re-exports, plus folded net imports | `SSB table 07410` |
| Spruce / Pine / Non-coniferous → Pulpmills | 07410 pulpwood, same treatment | `SSB table 07410` |
| Spruce / Pine / Non-coniferous → Boardmills | 07410 *sams* (mixed) | `SSB table 07410` |
| Spruce / Pine / Non-coniferous → Firewood | 11181, coniferous split by `FIREWOOD_PERCENT_SPRUCE` | `SSB table 11181` |
| Spruce / Pine / Non-coniferous → Net export skurlast | 08801 sawlog exports net of imports, by pool share | `SSB table 08801 (net)` |
| Spruce / Pine / Non-coniferous → Net export massevirke | 08801 pulpwood exports net of imports | `SSB table 08801 (net)` |
| Import Firewood → Firewood | 08801 | `SSB table 08801` |
| Import planed wood → Wood Product processing & Logistics | 08801 | `SSB table 08801` |
| Import Boards → Boardmills | FAOSTAT legacy file | `FAOSTAT` |
| Net export sawn timber → Wood Product processing & Logistics | 08801, net-import years | `SSB table 08801 (net)` |
| Sawmills → Planingmills | Treindustrien planed × `PLANED_INPUT_FACTOR` | `Treindustrien årsrapport` |
| Sawmills → Wood Product processing & Logistics | sawn residual (`SAWN_YIELD`) less planing input, exports and stock change | `Treindustrien årsrapport` |
| Sawmills → Shrinkage | `SHRINKAGE` × log input (water) | `Treindustrien årsrapport` |
| Sawmills → Boardmills | `SAWDUST` × log input | `Treindustrien årsrapport` |
| Sawmills → Pulpmills | `CELLULOSE` × log input | `Treindustrien årsrapport` |
| Sawmills → Energy | `SCRAPS` × log input | `Treindustrien årsrapport` |
| Sawmills → Net export sawn timber | 08801 sawn exports net of imports | `SSB table 08801 (net)` |
| Sawmills ⇄ Timber inventory | Treindustrien stock change, signed | `Treindustrien årsrapport` |
| Planingmills → Boardmills | `BYPRODUCT_PLANING` × planing input | `Treindustrien årsrapport` |
| Planingmills → Impregnation | Treindustrien impregnated production | `Treindustrien årsrapport` |
| Planingmills → Wood Product processing & Logistics | planed less exports and impregnation | `Treindustrien årsrapport` |
| Planingmills → Export planed wood | 08801 | `SSB table 08801` |
| Planingmills ⇄ Planed timber inventory | Treindustrien stock change | `Treindustrien årsrapport` |
| Impregnation → Wood Product processing & Logistics | Treindustrien impregnated production | `Treindustrien årsrapport` |
| Impregnation ⇄ Impregnated inventory | Treindustrien stock change | `Treindustrien årsrapport` |
| Boardmills → Loss Boardmills | `BOARDMILL_LOSS` × inflow | `FAOSTAT \| SSB table 07410 \| Treindustrien årsrapport` |
| Boardmills → Wood Product processing & Logistics | (1 − `BOARDMILL_LOSS`) × inflow | `FAOSTAT \| SSB table 07410 \| Treindustrien årsrapport` |
| Pulpmills → Short-term USE / Energy / Loss Pulpmills | `PULP_SHORT_TERM` / `PULP_ENERGY` / `PULP_LOSS` × inflow | `SSB table 07410 \| SSB table 08801 \| Treindustrien årsrapport` |
| Wood Product processing & Logistics → Long-term USE / Energy | `WPP_LONG_TERM` / `WPP_ENERGY` × inflow | `SSB and Treindustrien` |
| Firewood → Energy | inflow less exports | `SSB various tables` |
| Firewood → Export Firewood | 08801 | `SSB table 08801` |
| Long-term USE → End-of-Life | 10513 | `SSB table 10513` |
| End-of-Life → Energy | 10513 (incineration only, by design) | `SSB table 10513` |

## Columns of `sankey_data.csv` / `carbon_data.csv`

| Column | Meaning |
|---|---|
| `source`, `target` | Node labels, exactly as in `config/nodes.json`. |
| `volume` | m³ solid wood (roundwood equivalent downstream). |
| `year` | Calendar year, 2012–2024. |
| `origin` | Provenance tag(s), `\|`-joined when rows were merged. |
| `carbon`, `carbon_low`, `carbon_high` | kg C, the central and envelope carbon densities (carbon file only). |
| `region` | ISO 3166-2:NO county on the harvest edges (2024 boundaries); `NO` = not regionally attributed. |
| `species` | `spruce`, `pine`, `non-coniferous`; mixed flows split by the propagated mix. |
| `assortment` | `sawtimber`, `pulpwood`, `sams`, `fuelwood` where the source distinguishes it; `all` otherwise. |
| `entity_id` | Always `national`; the column exists for member-level data under `docs/DATA_CONFIDENTIALITY.md`. |
| `destination` | `domestic`, an ISO 3166-1 alpha-2 partner code on export rows (08799 share split), or `unspecified`. |

## Files

| File | Written by | Read with |
|---|---|---|
| `data/processed/sankey_data.csv`, `carbon_data.csv` | `circwood-refresh` (`build_sankey`, `build_carbon`) | `pandas.read_csv` |
| `data/processed/manifest.json` | the refresh | `circwood.manifest.read_manifest` |
| `data/processed/beta_series.csv` | `circwood.analysis.hwp.export_beta_series` | `read_csv(path, comment="#")` |
| `data/processed/beta_sensitivity.csv` | `circwood.analysis.sensitivity.export_sensitivity` | `read_csv` |
| `data/processed/hwp_stock.csv` | `circwood.carbon.stock.export_hwp_stock` | `read_csv(path, comment="#")` |
| `data/processed/forest_resource.csv` | `circwood-forest` | `circwood.transform.forest.load_forest_resource` |
| `data/processed/economic_value.csv` | `circwood-economics` | `read_csv(path, comment="#")` |
| `data/processed/rotation_results.csv` | `py -3 -m models.rotation.rotation` | `read_csv(path, comment="#")` |
| `data/processed/forest_stock_projection.csv` | `py -3 -m models.forest_stock.projection` | `read_csv(path, comment="#")` |
| `data/processed/refresh_history.jsonl` | the refresh, one line per run that passed its gates (or was accepted) and moved a flow | JSON lines |
| `data/raw/treindustrien_production_data.csv` | hand-maintained from the Treindustrien annual report | — |
| `data/raw/harvest_by_fylke.csv`, `exports_by_partner.csv`, `prices_ssb.csv` | rewritten by a fresh run from SSB 03795 (counties), 08799, 07413 + 08360 | — |
| `models/rotation/growth_curve.csv` | `py -3 -m models.rotation.rotation` (the primary growth curve as run) | `read_csv(path, comment="#")` |
| `models/sitree/growth_curve_sitree.csv` | `Rscript models/sitree/simulate_stand.R` (sitree 0.1.15; committed) | `read_csv(path, comment="#")` |
| `config/carbon_densities.csv`, `carbon_nodes.csv`, `hwp_half_lives.csv`, `coefficient_ranges.csv`, `prices.csv`, `nodes.json`, `destination_labels.csv` | researcher-editable configuration | — |
