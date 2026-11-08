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

## Why Plotly.js and not ECharts

The roadmap originally named ECharts; the shipped dataset ruled it out (see
the ROADMAP decision log): the flow table contains two-way inventory flows
(e.g. `Sawmills ⇄ Timber inventory` within a single year), which ECharts'
sankey rejects as a graph cycle, and the hand-placed node positions in
`config/nodes.json` require `arrangement: "fixed"`, which ECharts' sankey
layout cannot honour. `vendor/plotly.min.js` is committed (plotly.js-dist-min
2.35.2, MIT) so a deploy needs no CDN and no build step.
