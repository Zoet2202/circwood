# Separate models that exchange files with the pipeline

The `circwood` package is a *positive, retrospective* material- and carbon-flow
account. The models in this folder are *normative or prospective*: what a forest
owner should do, what the standing stock may become. The ROADMAP decided
(Phase F, 2026-11-08) that the two kinds stay separate and talk through
parameter files, so nothing here is imported by `src/circwood`, nothing here is
run by the refresh loop, and the package never depends on R.

| model | reads | writes | runs with |
|---|---|---|---|
| `rotation/` | `data/processed/beta_series.csv` (the empirical β and its bounds), `data/raw/prices_ssb.csv` (SSB roundwood and electricity prices), `config/prices.csv` (carbon price), `models/rotation/parameters.csv`, `models/rotation/growth_curve.csv` | `data/processed/rotation_results.csv` | `py -3 -m models.rotation.rotation` |
| `forest_stock/` | `data/processed/forest_resource.csv` (SSB forest inventory), `data/processed/sankey_data.csv` (today's harvest), `data/processed/rotation_results.csv` (the rotation ages), `models/forest_stock/parameters.csv` | `data/processed/forest_stock_projection.csv` | `py -3 -m models.forest_stock.projection` |
| `sitree/` | the `sitree` R package (0.1.15 on R 4.6.2 when run, 2026-11-08); `simulate_ssb_stand.R` reads `data/processed/forest_resource.csv` (SSB site-class and development-class areas, spruce volume and increment), `simulate_stand.R` the package's `stand.west` example stand | `models/sitree/growth_curve_ssb_si{11,14,17}.csv` and `ssb_validation.csv`; `models/sitree/growth_curve_sitree.csv` — the rotation model reads every export by its `curve_name` header and `primary_growth_curve` in `models/rotation/parameters.csv` picks the primary (SI 14) | `Rscript models/sitree/simulate_ssb_stand.R`, `Rscript models/sitree/simulate_stand.R` (R; under a minute; not run in CI — the exports are committed) |

The outputs are committed like every other derived file: each carries `#`
header lines naming its inputs, their vintage and its assumptions, and a test
(`tests/test_models.py`) checks that the committed file equals a fresh run, so a
change in β or in a price cannot leave a stale result behind unnoticed. The
Streamlit app shows the results under their caveats; the public site does not,
until the owner has reviewed the numbers (ROADMAP Phase L).

What the models are not: forecasts. The rotation model is a single-stand
optimisation on a simulated or calibrated growth curve; the stock projection is an
area-class model with national-average parameters. Both exist to show *how the
empirical β and today's inventory move the answers*, with every assumption in
a CSV next to the code.

Two SiTree runs feed the rotation model. The SSB-anchored stand is the primary
curve: SSB publishes no tree lists, so it is a representative 2 000 stems/ha
spruce planting on site index 14 (the class holding 16 % of productive
forestry land; 11 and 17 exported as neighbours), validated against SSB's
spruce volume and increment per hectare through the development-class area
shares (`sitree/ssb_validation.csv`); its two non-SSB assumptions, the planting
density and the start ages, are named in the script. The thesis's example
stand is kept as a comparison: it starts already stocked (about 40 years old;
its ages are shifted by `sitree_stand_age_at_start_years`) and its
6 400 stems/ha carry the increment and mortality functions far outside their
calibration range (basal area 276 m²/ha at 150 simulated years), so only its
shape is compared. The calibrated logistic curve, fitted to the thesis's two
stated facts, is the third; every result row names its curve.
