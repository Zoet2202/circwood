# Methods

Every assumption, coefficient and source behind the Circwood WP4 dataset and
its derived results, written to be reusable as supplementary information to
Modaresi & Landaas, *Wood extraction and utilisation in Norway 2012–2022: a
national material and carbon flow analysis*, Wood Material Science &
Engineering (2026). Where a number is quoted it was produced by the code named
beside it on the dataset vintage recorded in `data/processed/manifest.json`
(SSB data as of 2026-10-15); re-running the pipeline reproduces it. The
dataset was corrected after the paper's figures were prepared (CHANGELOG
2026-11-08, Phases K and G), so the numbers here are the corrected ones;
which published figures they supersede is for the authors to state.

Companion documents: [SANKEY_GUIDE.md](SANKEY_GUIDE.md) walks through each
source table and the edges it produces; [DATA_DICTIONARY.md](DATA_DICTIONARY.md)
lists every node, edge, column and file; [docs/REFRESH_POLICY.md](docs/REFRESH_POLICY.md)
states how the data refreshes itself and what stops it.

---

## 1. System boundary and schema

The system is the Norwegian wood chain from roundwood removals to first use,
energy and export, one calendar year at a time, 2012–2024 (the years every
Statistics Norway source table covers; `constants.DATASET_MIN_YEAR` /
`DATASET_MAX_YEAR`). Standing forest, forest growth and the fate of products
after their first use are outside the flow table; the forest-inventory layer
(§7), the product-stock model (§6) and the two forward models (§9) sit beside
it and exchange files with it.

The artifact is one tidy table, `data/processed/sankey_data.csv`: a row per
`(source, target, volume, year, origin, region, species, assortment,
entity_id, destination)`, volumes in m³ solid wood. Summing `volume` over the
five dimension columns recovers the national table exactly. `carbon_data.csv`
is the same table with `carbon`, `carbon_low` and `carbon_high` (kg C).

31 nodes: four sources (`Harvested wood`; `Import planed wood`, `Import
Boards`, `Import Firewood`), three species pools, seven process nodes, three
inventories, two use phases, `End-of-Life`, and the sinks (energy, losses,
water, export and net-export nodes). The full list is in the data dictionary.

## 2. Sources

| Source | Table | What it supplies | Unit as published | Publication lag used |
|---|---|---|---|---|
| SSB | 03795 | Roundwood removals for sale by species (`Gran`, `Furu`, `Lauv`), national and by county (`agg_KommFylker`, 2024 county boundaries backcast) | m³ | 6 months |
| SSB | 07410 | Removals by assortment and species: sawlogs, pulpwood, *sams* (mixed) | 1 000 m³ | 6 months |
| SSB | 11181 | Firewood removals (`Avvirkning av vedvirke`), coniferous and broadleaf, *with bark*, estimated from household and cabin consumption surveys and covering firewood sold and cut for own use | 1 000 m³ | 7 months |
| SSB | 08801 | Foreign trade by HS commodity code, m³ measure (`Mengde 2`) | m³ | 3 months |
| SSB | 08799 | The same trade by partner country (ISO 3166-1 alpha-2), monthly, in three five-year query chunks | m³ | 3 months |
| SSB | 10513 | Waste wood delivered to incineration (`AvfallsType` 06, `AvfallsBehand` 21) | **1 000 tonnes** | 16 months |
| Treindustrien | annual report | Sawn, planed and impregnated production; timber, sawn, planed and impregnated inventories, 2011–2023 | m³ | 9 months (a file in `data/raw/`) |
| FAOSTAT | legacy file | Board imports (`Import Boards → Boardmills`), 2005–2022; board exports pre-2012 only | m³ | — |
| SSB | 14589, 14590, 14591, 14593, 14594, 06289 | National Forest Inventory: standing volume and gross increment under bark, forest area, forest types, development-class and site-class shares, rolling five-year intervals; 06289 the national series from 1933 | 1 000 m³, km², per cent | 8 months |
| SSB | 07413 | Average roundwood prices by assortment | NOK per m³ | 6 months |
| SSB | 08360 | Wholesale electricity prices, series *Elspotmarknaden* | øre per kWh | 12 months |

Every SSB query requests `json-stat2` and `lang=no` (the transforms filter on
Norwegian category labels); the vintage of each table (SSB's own `updated`
date, our pull time, the query file's hash, rows and year span) and the hash
of every committed input file are recorded in `manifest.json` by each refresh.

## 3. From sources to balanced flows

### 3.1 Harvest and the species pools

`Harvested wood → Spruce/Pine/Non-coniferous` carries the 03795 commercial
removals plus the 11181 firewood estimate; the county rows are exact 03795
figures and the firewood component is one national remainder row per edge
(`region = "NO"` means *not regionally attributed*). The pools emit to the
mills (07410 by assortment), to firewood (11181) and to the netted roundwood
exports. Two fixed by-product edges hang off `Harvested wood`: `Bark` at
`BARK_PERCENTAGE` = 10 % of the 03795 total and `Residues from harvest` at
`HARVEST_RESIDUE_PERCENTAGE` = 10 % of the 07410 total. They are coefficients,
not measurements, and are kept out of the β denominator (§5).

### 3.2 Trade and netting

08801 commodity labels are classified by keyword into seven trade types
(`ingest/trade.py`); imports sit in the `source` column, exports in `target`.
Re-exported raw sawlogs and pulpwood are routed from the pools straight to
export (`assemble._bypass_raw_reexport`) so the mills only see wood they
process. For sawlogs, pulpwood and sawn timber the gross import/export pair is
collapsed into a single net-export node per year (`Net export skurlast`,
`Net export massevirke`, `Net export sawn timber`); the import volume is
folded into the domestic `pool → mill` edge so every node stays balanced, and
a net-import year is drawn in the reverse direction. Firewood, planed wood,
boards and pellets keep gross edges.

The `destination` dimension splits every export-node row by each partner's
share of that year's and type's 08799 exports: shares, not figures, because
the netted edges have no partner-level counterpart. `unspecified` is SSB's own
residual (`Land` 97) or an export edge with no partner data.

### 3.3 Sawing, planing, impregnation

`transform/sawing.py` applies the by-product shares to the raw log input
(domestic sawlogs + imported sawlogs − timber-stock change): `SHRINKAGE` 0.05
→ `Shrinkage` (water), `SAWDUST` 0.14 → `Boardmills`, `CELLULOSE` 0.26 →
`Pulpmills`, `SCRAPS` 0.05 → `Energy`; the residual `SAWN_YIELD` 0.50 is sawn
timber, of which Treindustrien's planed production × `PLANED_INPUT_FACTOR`
1.05 goes to `Planingmills`, the sawn exports leave, the sawn-stock change is
booked, and the rest goes to `Wood Product processing & Logistics` (WPP).
Planing emits `BYPRODUCT_PLANING` 0.05 to `Boardmills`, impregnated production
to `Impregnation`, planed exports, and the rest to WPP. The three sawing bugs
the diagnosis report found (year misalignment, coefficients applied after
shrinkage, no mass balance) are fenced by `tests/test_sawing.py`, including the
regression guard that `Sawmills → Pulpmills` equals `CELLULOSE` × raw log input
(26 %, not the 24.7 % a post-shrinkage basis gives).

The sawing step recognises both the gross trade spellings (fresh run) and the
netted ones (a rebuild from the committed table), so
`transform.assemble.regenerate_balanced` reproduces the committed national
flows exactly (`tests/test_transforms.py`).

### 3.4 Mills, use phases, firewood, end of life

`Boardmills`: `BOARDMILL_LOSS` 5 % to `Loss Boardmills`, 95 % to WPP.
`Pulpmills`: 86 % `Short-term USE` (pulp and paper), 8 % `Energy`, 6 % `Loss
Pulpmills` (`PULP_*`). WPP: `WPP_LONG_TERM` 90 % to `Long-term USE`, 10 % to
`Energy`. These splits are overridable through `transform.mills.MillCoefficients`
(the sensitivity analysis uses that).

`Firewood → Energy` is the residual of the node's inflow (11181 removals plus
imported firewood) after `Firewood → Export Firewood` (08801). Until Phase K
(2026-11-08) the whole inflow went to Energy *and* the export was added on
top, so the node emitted up to 8.5 % more than it received; it now balances
and is one of `validate.PROCESS_NODES`.

`Long-term USE → End-of-Life → Energy` carries 10513 waste wood delivered to
incineration, converted from **1 000 tonnes** (SSB's unit, confirmed from the
table's metadata) at `WASTE_WOOD_DENSITY_KG_M3` = 483 kg/m³: about 1.35 Mm³/yr
over 2012–2022. The shipped dataset before Phase K divided by 1 000 too few.
Incineration is the only treatment queried, by design: Norwegian waste wood
is effectively all incinerated and the edge exists to show the recoverable
potential; a guard raises if the query is widened without routing.

### 3.5 Assembly, clipping, guarantees

Rows are deduplicated per `(year, source, target)`, volumes taken absolute,
years clipped to the dataset window, then netted and widened with the
dimensions. Guarantees, all fenced by tests and by the refresh gates:

* every node in `validate.PROCESS_NODES` (Sawmills, Pulpmills, Boardmills,
  Planingmills, Impregnation, WPP, Firewood) conserves volume within 1 % over
  2012–2022, in aggregate and per `(region, species)` slice (1 000 m³ floor);
* the SSB 07410 control total: domestic sawtimber 2012–2022 = 61 445 000 m³,
  exactly;
* planed and impregnated throughput tie to the Treindustrien file;
* `config/nodes.json` covers the node set exactly;
* the committed CSV equals its golden fixture.

Species pools, use phases and inventories are reservoirs and are not required
to balance. In 2024 `Sawmills` receives 4.5 Mm³ and emits nothing, because the
Treindustrien series ends in 2023; the owner decided (2026-11-08) to keep 2024
in the dataset, and every derived analysis refuses the year on its own gate.

## 4. The carbon model

`carbon(row) = volume × ρ(year, source node, species)`, kg C per m³ of the
volume on that edge, with the species of each row from the dataset (mixed flows
are split by the same fixed-point propagation of the inflow mix that the
carbon model has always used).

Densities are researcher-editable in `config/carbon_densities.csv`: spruce
190 / 203, pine 215 / 228, non-coniferous 240 / 268 kg C per m³ at `raw` /
`dry` state, with `kg_c_per_m3_low` / `_high` columns at ±10 % (an indicative
envelope covering the published range of basic density × carbon fraction;
replace with measured bounds). `config/carbon_nodes.csv` gives each node its
state and whether it mixes (`propagate`) or carries fixed species shares
(pools, imports, net-export nodes), plus `carries_carbon = 0` for the water
sink.

**Carbon follows the wood (Phase G).** Carbon enters at harvest at the pool's
raw density and at imports at their configured dry density, and every
propagate node emits each species at one effective density
`ρ_N = (C_in / V_in) / (1 − w)`, where `w` is the share of the node's inflow
volume that leaves as water (`Sawmills → Shrinkage`, which carries 0 kg C).
So carbon is conserved through every balanced node, a node's carbon imbalance
equals its wood-volume imbalance, and "dry density" is derived from the
actual water loss rather than configured. An inventory that only withdraws in
a year keeps the density its wood went in with. Before this change the model
switched raw/dry density at process nodes and valued the water edge at dry
density, creating 6.6 % carbon at Sawmills, destroying 2 % at Pulpmills and
putting 0.5 Mt C on a water edge over 2012–2022. Fenced by
`validate.assert_carbon_balanced` and `tests/test_carbon.py`.

Results, 2012–2022 means (`carbon_data.csv`): carbon leaving `Harvested wood`
on all five edges 2.92 Mt C/yr (envelope 2.63–3.22), of which the roundwood
removals — β's denominator (§5) — 2.51 (2.26–2.76) and bark plus harvest
residues (fixed coefficients) 0.42; carbon entering `Long-term USE` 0.64 Mt C/yr. CO₂
equivalents are a display option (`constants.CO2_PER_C` = 44.0095/12.0107);
substitution effects are out of scope.

## 5. The empirical HWP fraction β

`circwood.analysis.hwp`, exported to `data/processed/beta_series.csv`.
β(year, species) is the share of the carbon in the year's domestic roundwood
removals (`Harvested wood → pool`, 03795 + 11181; bark and residues excluded
and reported as ratios) that is absorbed at `Long-term USE` in that year.

*Tracing.* Per year, the aggregated flows form an absorbing Markov chain with
proportional mixing at every transient node (pools, the seven process nodes,
two-way inventories netted); `B = (I − Q)⁻¹ R` gives each pool's absorption
at each sink. Imports, net imports, one-way inventory withdrawals and
`End-of-Life` are exogenous and never traced, so β is a *production-basis*
measure: imported wood never inflates it and exports are their own fates.
Since Phase G the chain is solved on **conserved carbon** recomputed from the
volumes with the carbon model, so the water edge drops out and the fates sum
to one as carbon; the pre-Phase-G volume trace is kept as `basis="volume"` and
reproduces an independent reference tracer to 1e-12.

*Gates.* A year is refused if any pool's closure gap exceeds
`BETA_CLOSURE_TOL` 2 % or any pool or process node emits more than
`BETA_EXCESS_OUT_TOL` 15 % beyond its inflow; 2024 fails (Sawmills), so the
export covers 2012–2023.

*Results.* β(all) 2012–2022 mean **0.215** (envelope 0.186–0.235), spruce
0.259, pine 0.242, non-coniferous 0.001 (that harvest is almost all firewood).
The rest of the harvest carbon: pulp and paper 0.261, energy 0.248, roundwood
export 0.223, sawn and planed export 0.013, firewood export 0.007, mill losses
0.023, stock change 0.009, closure gap 0.002. The rotation literature's
assumed 0.55 (which product pool it denotes is unverified) lies far above;
framing β against it is for the authors.

*Uncertainty (Phase G).* `circwood.analysis.sensitivity` rebuilds the national
flows with each fixed coefficient at the low and high bound of
`config/coefficient_ranges.csv` (eleven coefficients; indicative ranges to be
refined); the ±10 % density envelope is run alongside and shown to cancel (a
uniform scaling of every density leaves β unchanged, |Δβ| ≤ 3e-5). `ci_low` /
`ci_high` are the root-sum-square of the resulting deviations, which treats
the ranges as independent; the sawing by-products are shares of one split
whose high bounds all lower β, so the fully correlated bound — the plain sum,
0.156–0.254 for β(all) 2012–2022 against the RSS 0.186–0.235 — is the
conservative one, and `sensitivity.beta_bounds` reports it as
`ci_low_linear` / `ci_high_linear`. Both are an *assumption envelope*, not a
statistical confidence interval, and `ci_method` says so. The most influential
coefficient is `WPP_LONG_TERM` (β(all) 0.191–0.227 over 0.80–0.95), then the
firewood spruce/pine split (per species only), the chip yield and the scraps
share. Species-level β does not depend on densities at all (every pool's wood
enters at one density); only the harvest-carbon weighting of `all` could, and
a uniform envelope does not move it.

## 6. Carbon stored in products

`circwood.carbon.stock`, exported to `data/processed/hwp_stock.csv`:
first-order decay, `C(i+1) = e^{−k} C(i) + ((1 − e^{−k})/k) · Inflow(i)`,
`k = ln 2 / half-life`, with IPCC default half-lives (sawnwood 35 y, panels
25 y, paper 2 y; `config/hwp_half_lives.csv`). The inflow to `Long-term USE`
is split into sawnwood and panels by the panel share of WPP's inflow; the
inflow to `Short-term USE` is paper. Imports are included (a stock-in-Norway
view), unlike β.

Two initialisations: *cohort* (stock starts at zero in 2012: the carbon of
this window's products still stored) and *steady state* (starts at
inflow/k). 2012–2023: cohort inflow 15.5 Mt C, stock at end of 2023 8.6 Mt C
(sawnwood 4.5, panels 2.3, paper 1.9), decay rising to 0.81 Mt C/yr; the
steady-state stock sits near 29 Mt C with a decay of about 1.26 Mt C/yr. The
observed outflow, `Long-term USE → End-of-Life` (10513, incineration only),
averages 0.27 Mt C/yr and measures something narrower than modelled decay.

## 7. The forest resource

`transform/forest.py`, `data/processed/forest_resource.csv`: the six SSB
inventory tables, keyed on codes (the Norwegian label of development class IV
reads "VI" on SSB). Nine *takserte regioner* (the country and eight groupings
of the 2024 counties; `NFI_REGIONS` crosswalks them to the flow table's county
codes). Figures are rolling five-year intervals dated by their last year, and
*productive forestry land* excludes productive forest that is protected or set
aside, which is what makes it approximately the commercially available forest;
conflating the two is a common error.

National, interval 2021–2025: productive forestry land 82 139 km², standing
volume 894 Mm³ under bark on productive forestry land (1 025 Mm³ with other
land), gross increment 22.7 Mm³/yr (25.1 with other land). Against the
2012–2022 mean roundwood harvest of 12.4 Mm³/yr the harvest/increment ratio on
productive forestry land is 0.55 (spruce 0.63, pine 0.59, broadleaf 0.30;
0.49 against the increment on all land): different periods and bases (the
harvest's firewood component is measured with bark), so a ratio to report with
its caveats, not a fence.

## 8. The economic layer

`transform/economics.py`, `data/processed/economic_value.csv`, nominal NOK of
each year. Roundwood is valued where it leaves the pools at the 07413 average
price of its species and assortment (firewood at the all-assortment average,
a labelled proxy; broadleaf *sams* at the broadleaf pulpwood price); energy at
753 kWh/m³ (20.08 MJ/kg × 375 kg/m³ × 36 % ÷ 3.6) times the 08360 day-ahead
price; carbon entering `Long-term USE` (stored) and `Energy` (released,
negative) as t CO₂ at the carbon price of `config/prices.csv` (€94/t CO₂ at
11.5 NOK/EUR, both assumptions to be replaced for the year analysed). Processed
products are not valued: SSB publishes producer price indices, not prices per
m³, and choosing a level is a human decision on sources and licensing.
2012–2022 means: roundwood 4.7 bn NOK/yr, energy 1.3, carbon stored 2.5,
carbon released −3.8.

## 9. The forward models (`models/`)

Separate models that read and write files, never imported by the package
(ROADMAP decision, Phase F).

*Rotation economics* (`models/rotation`): the four NPV models of Landaas
(2023) — Faustmann single and infinite rotation, van Kooten with carbon, the
CRC owner model (reward only beyond the Faustmann age; dead-biomass decay at
4 % per five years charged), and the social optimum with the energy from the
share not stored — on an annual growth curve, with β where the thesis put it,
in `(1 − β)`. The carbon price grows at the discount rate (eq. 19) in both
carbon-rewarding models, uptake and release alike; van Kooten keeps the
constant price of van Kooten (1995); the `constant carbon price` scenario
holds it fixed everywhere. Three growth curves are available, every result row
names its curve (`growth_curve`), and `primary_growth_curve` in
`models/rotation/parameters.csv` says which one carries the base and price
scenarios and feeds the stock projection:

* *The SSB-anchored spruce stand* (the primary curve;
  `models/sitree/simulate_ssb_stand.R`, sitree 0.1.15 on R 4.6.2, 2026-11-08).
  SSB publishes no tree lists, so the stand is representative rather than
  observed: a pure Norway-spruce planting of 2 000 stems/ha (within the
  Norwegian regeneration norms of 180–250 plants per decare — an assumption,
  not SSB) on site index H40 14, started at total age 20 as a uniform young
  stand (dbh 5–8 cm, 4–5.5 m), grown 230 years without management with the
  functions the package documents (`dbhi.BN2009` with Korf height increment,
  `mort.B2007`, `recr.BBG2008`), stem volume under bark from `volume.norway`,
  ten replicates averaged. SSB anchors two things. The site index: H40 14
  holds 16 % of productive forestry land and, with 11 and 17 (22 % and 11 %),
  the band where most spruce stands (SSB 14594, which does not split site
  class by species), so SI 14 is the representative stand and SI 11 and 17 its
  exported neighbours. The validation: weighting each curve by SSB's
  development-class area shares (I+II 17 %, III 19 %, IV 20 %, V 44 %) gives an
  implied landscape mean — 298 m³/ha standing and 6.2 m³/ha/yr gross increment
  for SI 14 — against SSB's spruce volume and increment per hectare of spruce
  forest, 126–199 m³/ha and 3.8–6.0 m³/ha/yr (SSB 14589, 14590, 14593; the low
  end spreads the spruce volume over conifer-dominated and mixed forest too).
  The increment lands at the top of the band and the volume above it, as an
  unmanaged stand against a managed landscape should
  (`models/sitree/ssb_validation.csv`). Realism: 425 m³/ha and 1 970 stems/ha
  at age 100, a basal-area peak of 76 m²/ha, living volume peaking at
  572 m³/ha at 180 years — a dense unthinned stand within the growth
  functions' range.
* *The calibrated logistic curve* (`GrowthCurve.logistic`): a living-volume
  curve fitted to the thesis's two stated facts (Faustmann optimum near
  90 years at 3 %; plateau near 135 years), because the thesis's own SiTree
  numbers do not survive in its text. It was the primary curve until the
  SSB-anchored stand existed and remains the thesis-comparable one.
* *The thesis's example stand* (`models/sitree/simulate_stand.R`): the
  package's `stand.west` (651 Norway spruce on 1 019 m², site index 14,
  Bergen), run to the thesis's horizon with the same functions. Two things keep
  it a comparison. The stand is already stocked at the start (187 m³/ha,
  6 400 stems/ha, dominant height 13–14 m: about 40 years old on a site index
  14 height curve), so its ages are shifted by 40 years
  (`sitree_stand_age_at_start_years`; the thesis counted the start as
  planting). And at that density the increment and mortality functions are
  extrapolated far out of range (basal area 186 m²/ha at 50 simulated years,
  276 at 150; a real spruce stand rarely exceeds 70), so its absolute volumes
  — a peak of 2 426 m³/ha — are not credible; only its shape is.

Prices:
SSB 07413 spruce sawlogs and 08360 Elspot for 2023 (647 NOK/m³, 0.685
NOK/kWh), carbon 1 081 NOK/t CO₂, i = 3 %. Results on the SSB-anchored stand:
Faustmann 49 years (single rotation 54); van Kooten 89 years with the thesis's
β = 0.55 and **104 years with the empirical spruce β = 0.259** (a lower β
makes harvest costlier in carbon, so later; 101–115 over its envelope); CRC
owner 131; social 89 (116 with β = 0.55; a constant carbon price gives CRC 85
and social 90). Every NPV is positive at the 2023 prices (`npv_positive`),
Faustmann's at 15.7 kNOK/ha. On the calibrated logistic curve the same models
give 91 / 118 (108 with β = 0.55) / 124 / 107 and the Faustmann NPV is
negative (break-even about 663 NOK/m³); on the thesis's example stand
63 / 130 / 155 / 119. The thesis reported 90 / 125 / 120 and found the social
optimum pulled back towards Faustmann, as every curve here does. The ordering
is the same on every curve; the levels move with the growth curve by 30–40
years, which is why every row names its curve. At 1.5× the carbon price the
van Kooten optimum reaches 216 years and at 2× the horizon
(`interior_optimum = 0`): the model then says never harvest, a statement about
constant prices valued forever, not a rotation age.

The CRC owner model is a stylised carbon-farming unit. Under the EU's
certification framework for carbon removals and carbon farming (Regulation
(EU) 2024/3012, in force since December 2024; implementing rules in
Regulation (EU) 2025/2358) a carbon-farming activity runs for at least five
years and forest owners and managers are operators, which the model's
five-year periods match; carbon storage in products must last at least
35 years, the sawnwood half-life the HWP stock model uses. Whether a longer
rotation on an existing stand can be certified depends on the methodologies
adopted by delegated act, none of which covered it in the texts read
(2026-11-08), and whether the regulation becomes EEA-relevant for Norway was
not stated there either.

*Stock projection* (`models/forest_stock`): an area-class model over the five
development classes from today's national inventory, with indicative class
residences, per-hectare volume and increment weights and mortality calibrated
to today's totals, rolled to 2100 under three harvests: the 2012–2022 mean
(status quo), the Faustmann age and the CRC age, the latter two phased at no
more than 1.5× today's harvest. Standing volume 894 Mm³ today reaches about
1 250 Mm³ by 2100 under the status quo and about 1 330 under the CRC rotation;
the Faustmann rotation first works the mature backlog down at 18.6 Mm³/yr. A
scenario tool with every assumption in `parameters.csv`; not a forecast.

## 10. Reproducibility

`requirements-lock.txt` pins the exact versions (Python 3.12), the
`Dockerfile` reproduces the test-suite and the site bundle from the committed
dataset without network, `CITATION.cff` and `.zenodo.json` carry the citation
metadata for a release, and the refresh loop, its gates and its audit trail
are described in `docs/REFRESH_POLICY.md`. Minting the DOI (connecting the
GitHub repository to Zenodo and publishing a release) is a human step.

## 11. Constants index

Every value in `src/circwood/constants.py`, with the section that explains it.
`tests/test_docs.py` fails if a constant is added without appearing here or in
the data dictionary.

| Constant | Value | Where it is explained |
|---|---|---|
| `BARK_PERCENTAGE` | 0.1 | §3.1 |
| `BETA_CLOSURE_TOL` | 0.02 | §5 |
| `BETA_EXCESS_OUT_TOL` | 0.15 | §5 |
| `BETA_MAX_YEAR` | 2023 | §5 |
| `BETA_MIN_YEAR` | 2012 | §5 |
| `BETA_SENSITIVITY_CLOSURE_TOL` | 0.05 | §5 — looser gate for the scenario rebuilds |
| `BETA_SENSITIVITY_EXCESS_OUT_TOL` | 0.25 | §5 |
| `BOARDMILL_LOSS` | 0.05 | §3.4 |
| `BYPRODUCT_PLANING` | 0.05 | §3.3 |
| `CELLULOSE` | 0.26 | §3.3 |
| `CO2_PER_C` | 3.66419 | §4 |
| `DATASET_MAX_YEAR` | 2024 | §1 |
| `DATASET_MIN_YEAR` | 2012 | §1 — the dataset window |
| `EOL_UNIT_KG` | 1e+06 | §3.4 — SSB 10513 unit, 1 000 tonnes in kg |
| `FIREWOOD_PERCENT_SPRUCE` | 50 | §3.1, §5 (varied by the sensitivity) |
| `HARVEST_RESIDUE_PERCENTAGE` | 0.1 | §3.1 |
| `LITERATURE_BETA` | 0.55 | §5 — the assumed β the export is compared with |
| `MAX_YEAR` | 2022 | §1, §3.5 |
| `MIN_YEAR` | 2012 | §1, §3.5 — the paper's analysis window |
| `PLANED_INPUT_FACTOR` | 1.05 | §3.3 |
| `PULP_ENERGY` | 8 | §3.4 |
| `PULP_LOSS` | 6 | §3.4 |
| `PULP_SHORT_TERM` | 86 | §3.4 |
| `SAWDUST` | 0.14 | §3.3 |
| `SAWN_YIELD` | 0.5 | §3.3 (derived: 1 − the four by-products) |
| `SCRAPS` | 0.05 | §3.3 |
| `SHRINKAGE` | 0.05 | §3.3 |
| `WASTE_WOOD_DENSITY_KG_M3` | 483 | §3.4 |
| `WPP_ENERGY` | 0.1 | §3.4 (derived) |
| `WPP_LONG_TERM` | 0.9 | §3.4 |
