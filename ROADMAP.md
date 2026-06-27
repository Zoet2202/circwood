# Circwood WP4 — Refactor Roadmap

> **This roadmap (the refactor) is essentially complete.** For what to build
> next, see [ROADMAP_NEXT.md](ROADMAP_NEXT.md).

> Goal: turn the current collection of scripts into a clean, reproducible data
> pipeline that goes **SSB API → validated wood-flow dataset → carbon dataset →
> interactive Streamlit web app** hosting the Sankey diagram, with the sawing
> bug fixed correctly and guarded by tests.

---

## 1. Where we are today

The project currently has two loosely-coupled stages plus several orphaned files.

| Area | File(s) | State |
|------|---------|-------|
| SSB → CSV pipeline | `Volume_dataset/volume.py`, `Volume_dataset/modules/functions.py` | Works; sawing logic already *partially* patched (see §2). Monolithic, hard to test. |
| Carbon overlay | `Volume_dataset/carbon.py` | Standalone script, hard-coded relative paths, incomplete species/state mapping. |
| Visualization | `Sankey_diagram/main.py` + `sankey_params.json` | Plotly Sankey via `fig.show()` (opens a browser tab). Not a hosted app. |
| Dead / duplicate code | `Volume_dataset/src.py`, `Volume_dataset/modules/__init__.py`, `MFA database to python.ipynb`, `*.pyc`, `~$*.docx` | `src.py` duplicates `functions.py`; should be removed. |
| Data inputs | `data/SSB_queries/*.json`, `data/excel_data/*.csv`, `Carbon-Excel.xlsx`, `varekoder.*` | Mixed cached/raw; provenance unclear. |
| Reference | `Sawing_function_diagnosis.docx` | Authoritative description of the sawing bug. |

**Key observation:** `functions.py`/`volume.py` already contain fix comments and
code addressing all three diagnosis issues (year reindexing, coefficients on
`initial_timber_vol`, mass-balance residual). The refactor must *preserve and
verify* those fixes, not reintroduce the bug — and lock them in with tests.

---

## 2. The sawing bug (must stay fixed)

From `Sawing_function_diagnosis.docx`, three compounding issues in
`create_flow_rows()`:

1. **Year misalignment (dominant).** SSB-07410 inflows (2006–2024) and
   Treindustrien production/inventory (2011–2023) were aligned by *positional*
   index, producing a 5-year offset. → Fix: reindex every per-year series on a
   shared `all_years` index before arithmetic.
2. **Coefficient base.** `CELLULOSE`/`SAWDUST`/`SCRAPS` were applied to the
   post-shrinkage "dry" volume (× 0.95), under-reporting by-products. → Fix:
   apply them to `initial_timber_vol` directly.
3. **Mass imbalance.** Sawmills inflow (SSB) and outflow (Treindustrien) came
   from independent sources, so the node didn't balance. → Fix: derive the
   sawn-timber residual to WPP from a mass-balanced `SAWN_YIELD`, with explicit
   handling of re-exported raw logs, imports, and stock changes.

**Acceptance target:** Sawmills → Pulpmills ≈ **26% of sawmill log input**, and
node-level mass balance (inflow = outflow ± stock change) holds within a small
tolerance for every year.

---

## 3. Target architecture

```
circwood/                         # installable package (replaces scattered scripts)
├── pyproject.toml                # deps + tooling (replaces ad-hoc imports)
├── README.md
├── ROADMAP.md
├── config/
│   ├── ssb_queries/*.json        # moved from Volume_dataset/data/SSB_queries
│   ├── nodes.json                # moved from sankey_params.json
│   └── constants.py              # SHRINKAGE, CELLULOSE, yields, factors, year window
├── src/circwood/
│   ├── ingest/                   # SSB API + Excel/CSV loaders (raw → tidy)
│   │   ├── ssb.py                # process_query_files, json-stat → DataFrame
│   │   ├── treindustrien.py      # production + inventory loaders
│   │   └── trade.py              # SSB 08801 trade mapping
│   ├── transform/                # pure, testable flow builders
│   │   ├── sawing.py             # create_flow_rows (the fixed sawing logic)
│   │   ├── boardmills.py
│   │   ├── pulpmills.py
│   │   ├── firewood.py
│   │   └── assemble.py           # builds full sankey_df, dedupe, mass-balance check
│   ├── carbon/carbon.py          # volume → carbon overlay (fixed mapping)
│   ├── pipeline.py               # build_sankey(use_cached_ssb=...) orchestrator
│   └── viz/sankey.py             # figure builder (no I/O, returns go.Figure)
├── app/streamlit_app.py          # the hosted web application
├── data/
│   ├── raw/                      # cached SSB responses + source spreadsheets
│   └── processed/                # sankey_data.csv, carbon_data.csv (generated)
└── tests/                        # pytest: sawing mass-balance, alignment, regression
```

Principles: ingestion separated from transformation separated from
presentation; transformations are pure functions over DataFrames (easy to
unit-test); all paths anchored to package root; constants centralized.

---

## 4. Work phases

> **Progress (2026-06-27):** Phases 0, 1, 2, 4, 5 and 6 are **done** — package
> restructure, dead-code removal, centralized constants, the sawing-fix test
> suite, the carbon overlay (species-accurate downstream model with editable CSV
> config and a metric selector in the app), and the Streamlit app off cached
> data. **Deferred:**
> Phase 3 (live SSB ingestion — scaffolded but not wired; the user will own this).
> Phase 7 (README) done; web deploy pending.

### Phase 0 — Baseline & safety net (do first)
- [ ] Initialize git (repo is currently not under version control) and commit the
      current state as a baseline before refactoring.
- [ ] Add `pyproject.toml` with pinned deps: `pandas`, `pyjstat`, `requests`,
      `plotly`, `streamlit`, `openpyxl`, `pytest`. Add `.gitignore` (`__pycache__`,
      `~$*`, `*.pyc`, generated CSVs).
- [ ] Capture a **golden snapshot**: run the existing pipeline in cached mode and
      save its output as a regression baseline for the current (fixed) numbers.

### Phase 1 — Clean the dead wood
- [ ] Delete `src.py` (duplicate of `functions.py`), `*.pyc`, `~$*.docx`,
      `__pycache__/`. Decide whether `MFA database to python.ipynb` is reference
      (move to `notebooks/`) or dead (remove).
- [ ] Consolidate scattered CSVs/JSON into `data/raw` vs `data/processed`.

### Phase 2 — Restructure into a package
- [ ] Create the `src/circwood` layout above; move code into focused modules.
- [ ] Extract all magic numbers (0.26, 0.05, 0.14, year windows, 86/8/6 pulp
      splits, 0.90/0.10 WPP split, carbon densities) into `config/constants.py`.
- [ ] Make `build_sankey(use_cached_ssb: bool)` the single orchestration entry
      point; everything else becomes importable, side-effect-free functions.

### Phase 3 — Harden the SSB ingestion pipeline
- [ ] Wrap SSB POST calls with retries/timeouts and clear errors; cache raw
      JSON-stat responses to `data/raw` so the pipeline is reproducible offline.
- [ ] Validate each table's expected columns/years on load (fail loud, not silent).
- [ ] Add a `--fresh` vs cached toggle (already exists in `volume.py`) and document it.

### Phase 4 — Lock in the sawing fix with tests
- [ ] Move `create_flow_rows` to `transform/sawing.py`, keeping the year-reindex,
      input-based coefficients, and mass-balance residual.
- [ ] Add `tests/test_sawing.py`:
      - year alignment (no positional offset) on mismatched year ranges,
      - Sawmills → Pulpmills ≈ 26% of input,
      - node mass balance within tolerance,
      - regression vs the Phase-0 golden snapshot.
- [ ] Add a runtime mass-balance assertion in `assemble.py`.

### Phase 5 — Fix the carbon overlay
- [ ] Rewrite `carbon.py` with package-relative paths and a complete
      species/state mapping (current map has typos: `Pulpmills_`,
      `Wood Product  & Logistics`, missing nodes).
- [ ] Output `data/processed/carbon_data.csv` and expose carbon as a selectable
      metric in the app.

### Phase 6 — Build the Streamlit web app
- [ ] `app/streamlit_app.py` hosting the Sankey, with sidebar controls:
      - year range slider (default 2012–2022),
      - aggregation: annual average vs single year,
      - metric: volume (Mm³) vs carbon,
      - optional node toggles.
- [ ] Cache the dataset load with `@st.cache_data`; reuse `viz/sankey.py` figure
      builder (no `fig.show()`).
- [ ] Add a "data provenance / download" panel (show origin tags, download CSV).

### Phase 7 — Docs, polish, deploy
- [ ] Rewrite `README.md`: setup, run pipeline, run app, data sources, the
      sawing-fix explanation.
- [ ] Add `streamlit run app/streamlit_app.py` instructions; optional deploy to
      Streamlit Community Cloud (commit `data/processed/*.csv` so the app runs
      without live SSB access).
- [ ] CI (optional): run `pytest` on push.

---

## 5. Decisions (locked in 2026-06-25)

1. **Data source:** Use the **cached** processed CSVs for now. Live SSB API
   ingestion (`use_cached_ssb=False`) will be implemented later — keep the seam
   but don't depend on network access.
2. **Metric scope:** **Volume first, carbon second.** Ship a volume-only app,
   then add the carbon overlay.
3. **Refactor style:** **Full package restructure** into `src/circwood`.
4. **Deployment:** Streamlit for **local testing now**; web hosting later — keep
   the app deploy-friendly (works off committed processed CSVs, no live SSB).

---

## 6. Definition of done

- One command builds the dataset from SSB (or cache); one command launches the app.
- Sawing bug verified fixed by automated tests (26% pulp share + mass balance).
- No duplicate/dead modules; deps pinned; paths robust to CWD.
- Streamlit app renders the Sankey with year/metric controls and data download.
