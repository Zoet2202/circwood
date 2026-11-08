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
| `sitree/` | the `sitree` R package (0.1.15 on R 4.6.2 when run, 2026-11-08) and its `stand.west` example stand | `models/sitree/growth_curve_sitree.csv` (committed; compared beside the calibrated curve in `rotation_results.csv`, switchable to primary in `models/rotation/parameters.csv`) | `Rscript models/sitree/simulate_stand.R` (R; about a minute; not run in CI — the export is committed) |

The outputs are committed like every other derived file: each carries `#`
header lines naming its inputs, their vintage and its assumptions, and a test
(`tests/test_models.py`) checks that the committed file equals a fresh run, so a
change in β or in a price cannot leave a stale result behind unnoticed. The
Streamlit app shows the results under their caveats; the public site does not,
until the owner has reviewed the numbers (ROADMAP Phase L).

What the models are not: forecasts. The rotation model is a single-stand
optimisation on a calibrated growth curve; the stock projection is an
area-class model with national-average parameters. Both exist to show *how the
empirical β and today's inventory move the answers*, with every assumption in
a CSV next to the code.

The SiTree run reproduces the thesis's set-up — the package's example stand,
the call it documents, 250 years without management — and is kept as a
comparison: the stand starts already stocked (about 40 years old; its ages are
shifted by `sitree_stand_age_at_start_years`) and its 6 400 stems/ha carry the
increment and mortality functions far outside their calibration range (basal
area 186 m²/ha at 50 simulated years, 276 at 150), so only its shape is
compared, in the `growth curve: sitree example stand` rows. A comparison that
carries weight needs a stand at a plausible density; the script's header says
what it computes and the realism figures it prints.
