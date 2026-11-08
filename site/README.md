# Circwood WP4 — public static site

A host-anywhere static site rendering the national wood-flow Sankey with
client-side filters (year window, metric, species, region, assortment,
destination country, node focus), click-to-cross-filter companion charts, shareable URL state and
PNG/SVG export. Deployed to GitHub Pages by `.github/workflows/site.yml` on
every merge to `main` that touches the data or the site.

## Run locally

```bash
pip install -e .          # once, from the repo root
circwood-bundle           # writes site/data/circwood.json (+ .gz) — git-ignored
python -m http.server -d site 8000
# open http://localhost:8000
```

The bundle is generated from the committed `data/processed/carbon_data.csv`
via `circwood.export.bundle.build_bundle`, which routes every frame through
the Phase E confidentiality gate (`assert_publishable`) before serialising.

## CO₂e, the carbon envelope and the forest card (Phases G and H)

The metric switch offers volume (Mm³), carbon (Mt C) and CO₂e (Mt CO₂, URL
`m=co2`); CO₂e is a display conversion only — each row's carbon times the
bundle's `units.co2_per_c` (`constants.CO2_PER_C`, 44.0095/12.0107 ≈ 3.664),
so the dataset stays in kg C and every caption, hover, export filename and the
data table follow the chosen metric. In the carbon and CO₂e metrics the node
balance table adds a low–high column on its "Total in" and "Total out" rows,
the ±10 % density envelope (`carbon_low`/`carbon_high`, from
`config/carbon_densities.csv`) aggregated by the same window-year-average rule
as the central value; the Sankey card says so in one line and the node hover
carries the same range. Below the Sankey, a "Forest resource" card renders the
bundle's `forest` block (present when `data/processed/forest_resource.csv`
exists): standing volume, annual gross increment and productive forestry land
from the National Forest Inventory (SSB 14589, 14590, 14591, latest five-year
interval), the mean roundwood harvest of the paper window, a per-species table
with harvest ÷ increment, and the development-class shares (SSB 14594) as a
compact bar. Its caption carries the comparability caveats — an inventory
interval against a calendar-year harvest, under-bark volumes against a
firewood component measured with bark — so the ratio reads as orientation, not
a fence. The page still runs against older bundles that lack `forest`,
`units.co2_per_c` or the envelope: the card, the CO₂e button and the range
column simply stay hidden.

## Why Plotly.js and not ECharts

The roadmap originally named ECharts; the shipped dataset ruled it out (see
the ROADMAP decision log): the flow table contains two-way inventory flows
(e.g. `Sawmills ⇄ Timber inventory` within a single year), which ECharts'
sankey rejects as a graph cycle, and the hand-placed node positions in
`config/nodes.json` require `arrangement: "fixed"`, which ECharts' sankey
layout cannot honour. `vendor/plotly.min.js` is committed (plotly.js-dist-min
2.35.2, MIT) so a deploy needs no CDN and no build step.
