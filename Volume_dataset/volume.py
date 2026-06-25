"""
Circwood WP4 – Volume dataset pipeline.

Exposes a single entry point, ``build_sankey``, that either

1. loads the already-generated Sankey dataset (``sankey_data.csv``) — the
   recommended mode, since the paper is published and the underlying SSB
   tables for 2012–2022 do not change; **or**
2. re-runs the full SSB pipeline end-to-end, fetching fresh data from
   ``https://data.ssb.no``. Requires ``pyjstat`` + ``requests`` + network
   access, which is currently unreliable in this environment.

The boolean ``use_cached_ssb`` parameter selects between the two modes.
"""

from __future__ import annotations

###Import Packages###
import os
import sys
import json
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Path configuration
# ---------------------------------------------------------------------------
# Anchor all paths to this file's location so the script runs the same way
# regardless of the caller's current working directory (project root,
# Volume_dataset/, an IDE's run button, etc.).
SCRIPT_DIR  = Path(__file__).resolve().parent          # .../Volume_dataset
PROJECT_DIR = SCRIPT_DIR.parent                         # .../Circwood_WP4-main
DATA_DIR        = SCRIPT_DIR  / 'data'
EXCEL_DATA_DIR  = DATA_DIR    / 'excel_data'
SSB_QUERY_DIR   = DATA_DIR    / 'SSB_queries'
# Primary pipeline output, kept next to the raw inputs.
OUTPUT_CSV      = DATA_DIR    / 'sankey_data.csv'
# Secondary copy, written into the Sankey diagram folder so the visualisation
# script (Sankey_diagram/main.py) can consume the latest pipeline output
# without any extra plumbing. Previously this was `'../Sankey-diagram/...'`
# (hyphen typo + CWD-dependent) which silently wrote to the wrong place.
SANKEY_DIAGRAM_DIR  = PROJECT_DIR / 'Sankey_diagram'
SANKEY_DIAGRAM_COPY = SANKEY_DIAGRAM_DIR / 'sankey_data.csv'

# Ensure `from modules.functions import *` resolves regardless of CWD.
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from modules.functions import *  # noqa: F401,F403 – imported for pipeline use


# ---------------------------------------------------------------------------
# Constants used by the fresh pipeline
# ---------------------------------------------------------------------------
MIN_YEAR        = 2012
BARKPERCENTAGE  = 0.10
ROTFACTOR       = 1 / 15
MORTALITYFACTOR = 0.15


def _firewood_ratio(percent_spruce: float) -> tuple[float, float]:
    spruce = percent_spruce / 100.0
    pine   = 1.0 - spruce
    return spruce, pine


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def build_sankey(use_cached_ssb: bool = True) -> pd.DataFrame:
    """
    Build (or load) the Circwood WP4 wood-flow Sankey dataset.

    Parameters
    ----------
    use_cached_ssb : bool, default ``True``
        When ``True``, skip the SSB API pipeline entirely and load the
        already-computed ``sankey_data.csv`` from disk.
        Recommended for all normal use: the paper is published, the SSB
        tables for 2012–2022 do not change, and the API calls are currently
        unreliable in this environment.

        When ``False``, re-run the full SSB pipeline, fetching fresh data
        from ``https://data.ssb.no`` and regenerating ``sankey_data.csv``
        end-to-end. Requires ``pyjstat`` + ``requests`` + network access.

    Returns
    -------
    pandas.DataFrame
        The full Sankey dataset with columns
        ``['source', 'target', 'volume', 'year', 'origin']``.
    """
    if use_cached_ssb:
        return _load_cached_sankey()
    return _run_fresh_pipeline()


# ---------------------------------------------------------------------------
# Cached-mode loader
# ---------------------------------------------------------------------------
def _load_cached_sankey() -> pd.DataFrame:
    """Load the pre-generated sankey_data.csv and mirror it to Sankey_diagram/."""
    if not OUTPUT_CSV.exists():
        raise FileNotFoundError(
            f"Cached Sankey dataset not found at {OUTPUT_CSV}.\n"
            f"Run `build_sankey(use_cached_ssb=False)` to regenerate it from "
            f"the SSB API (requires network access and pyjstat / requests)."
        )

    print(f"[cache] Loading pre-computed Sankey dataset from {OUTPUT_CSV}")
    sankey_df = pd.read_csv(OUTPUT_CSV)
    print(f"[cache] Loaded {len(sankey_df)} rows.")

    # Keep the Sankey-diagram copy in sync so downstream tooling doesn't drift
    # from the primary output.
    SANKEY_DIAGRAM_DIR.mkdir(parents=True, exist_ok=True)
    sankey_df.to_csv(SANKEY_DIAGRAM_COPY, index=False)
    print(f"[cache] Mirrored to {SANKEY_DIAGRAM_COPY}")
    return sankey_df


# ---------------------------------------------------------------------------
# Fresh (API-dependent) pipeline
# ---------------------------------------------------------------------------
def _run_fresh_pipeline() -> pd.DataFrame:
    """Run the full SSB pipeline and regenerate sankey_data.csv."""
    # API-only dependencies. Imported lazily so that the cached path works
    # even when pyjstat / requests are unavailable.
    try:
        import pyjstat                              # noqa: F401
        from pyjstat import pyjstat                 # noqa: F401
        import requests                             # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "use_cached_ssb=False requires pyjstat and requests.\n"
            "Install with:  pip install pyjstat requests\n"
            "Or keep use_cached_ssb=True to use the pre-computed dataset."
        ) from exc

    # -------------------- Local CSV data --------------------
    treindustrien_df  = pd.read_csv(
        EXCEL_DATA_DIR / 'treindustrien production data.csv'
    )
    fao_boardmills_df = pd.read_csv(EXCEL_DATA_DIR / 'FAO_Data.csv')

    # -------------------- SSB API calls --------------------
    ssb_api_output = process_query_files(str(SSB_QUERY_DIR))

    # -------------------- SSB TABLE 03795 (intentionally skipped) ----------
    # SSB table 03795 reports roundwood removals by species that are numerically
    # equal (to 4 significant figures) to SSB 07410 for the overlapping years —
    # e.g. 2012 Spruce: 6,624,431 (03795) vs 6,624,000 (07410) — so emitting
    # both as `Standing Wood → species` rows double-counts the entire harvest
    # flow into the species pools. The mass-balance in `create_flow_rows`
    # downstream already relies on 07410 as the authoritative input, so we keep
    # only the 07410 pathway (`rows_gran` / `rows_furu` / `rows_lauv`) and drop
    # 03795 entirely.

    # -------------------- SSB TABLE 11181 --------------------
    tbl_11181 = pd.concat(ssb_api_output['11181'], ignore_index=True)
    tbl_11181['value'] = tbl_11181['value'] * 1000  # (1000 m3) -> (m3)
    firewood_coniferous        = tbl_11181[tbl_11181['virkesgruppe'] != 'Ved til brensel lauv']
    non_coniferous_firewood_df = tbl_11181[tbl_11181['virkesgruppe'] == 'Ved til brensel lauv']

    spruce_ratio, pine_ratio = _firewood_ratio(percent_spruce=50)

    spruce_firewood_df = firewood_coniferous.assign(
        virkesgruppe='Spruce',
        value=firewood_coniferous['value'] * spruce_ratio,
    )
    rows_firewood_spruce = pd.DataFrame({
        'source': 'Spruce', 'target': 'Firewood',
        'volume': spruce_firewood_df['value'].astype(float),
        'year':   spruce_firewood_df['år'].astype(int),
        'origin': 'SSB table 11181',
    })
    rows_firewood_spruce_sw = pd.DataFrame({
        'source': 'Standing wood', 'target': 'Spruce',
        'volume': spruce_firewood_df['value'].astype(float),
        'year':   spruce_firewood_df['år'].astype(int),
        'origin': 'SSB table 11181',
    })

    pine_firewood_df = firewood_coniferous.assign(
        virkesgruppe='Pine',
        value=firewood_coniferous['value'] * pine_ratio,
    )
    rows_firewood_pine = pd.DataFrame({
        'source': 'Pine', 'target': 'Firewood',
        'volume': pine_firewood_df['value'].astype(float),
        'year':   pine_firewood_df['år'].astype(int),
        'origin': 'SSB table 11181',
    })
    rows_firewood_pine_sw = pd.DataFrame({
        'source': 'Standing wood', 'target': 'Pine',
        'volume': pine_firewood_df['value'].astype(float),
        'year':   pine_firewood_df['år'].astype(int),
        'origin': 'SSB table 11181',
    })

    rows_non_coniferous_firewood = pd.DataFrame({
        'source': 'Non-coniferous', 'target': 'Firewood',
        'volume': non_coniferous_firewood_df['value'],
        'year':   non_coniferous_firewood_df['år'],
        'origin': 'SSB table 11181',
    })
    rows_non_coniferous_firewood_sw = pd.DataFrame({
        'source': 'Standing wood', 'target': 'Non-coniferous',
        'volume': non_coniferous_firewood_df['value'].astype(float),
        'year':   non_coniferous_firewood_df['år'].astype(int),
        'origin': 'SSB table 11181',
    })

    # -------------------- SSB TABLE 06289 --------------------
    tbl_06289 = pd.concat(ssb_api_output['06289'], ignore_index=True)
    tilvekst_df = tbl_06289.loc[
        (tbl_06289['statistikkvariabel'] == 'Årlig tilvekst')
        & (tbl_06289['treslag'] == 'I alt')
    ].copy()

    # -------------------- SSB TABLE 10513 --------------------
    tbl_10513 = pd.concat(ssb_api_output['10513'], ignore_index=True)
    tbl_10513 = tbl_10513[tbl_10513['value'] != 0]
    tbl_10513['volume'] = tbl_10513['value'] * 1000 / 483  # tonnes -> kg -> m3
    tbl_10513['source'] = 'Long-term USE'

    tbl_10513.loc[tbl_10513['behandlingsmåte'] == 'Levert til materialgjenvinning',
                  'target'] = 'Material recovery'
    tbl_10513.loc[tbl_10513['behandlingsmåte'] == 'Levert til forbrenning',
                  'target'] = 'Energy'
    tbl_10513.loc[~tbl_10513['behandlingsmåte'].isin(
        ['Levert til materialgjenvinning', 'Levert til forbrenning']
    ), 'target'] = 'Energy'

    tbl_10513_sum = tbl_10513.groupby("år", as_index=False)["volume"].sum()

    rows_EOL = pd.DataFrame({
        'year':   tbl_10513_sum['år'],
        'volume': tbl_10513_sum['volume'],
        'source': 'Long-term USE',
        'target': 'End-of-Life',
        'origin': 'SSB table 10513',
    })
    rows_waste = pd.DataFrame({
        'year':   tbl_10513['år'],
        'volume': tbl_10513['volume'],
        'source': 'End-of-Life',
        'target': tbl_10513['target'],
        'origin': 'SSB table 10513',
    })

    # Mortality (from 06289)
    val_idx = tilvekst_df.columns.get_loc('value')
    tilvekst_df.loc[:, 'mortality'] = (
        tilvekst_df.iloc[:, val_idx] * 1000 * MORTALITYFACTOR
    )
    rows_mortality = pd.DataFrame({
        'source': 'Standing wood', 'target': 'Mortality',
        'volume': tilvekst_df['mortality'],
        'year':   tilvekst_df['år'],
        'origin': 'SSB table 06289',
    })

    # -------------------- SSB TABLE 07410 --------------------
    tbl_07410 = pd.concat(ssb_api_output['07410'], ignore_index=True)
    tbl_07410['value'] = tbl_07410['value'] * 1000  # 1000m3 -> m3

    tbl_07410_sum = tbl_07410.groupby(['år'], as_index=False)['value'].sum()

    tbl_07410_sum_sans_lauv = (
        tbl_07410.groupby(['år', 'sortiment'], as_index=False)['value'].sum()
    )
    tbl_07410_sum_sans_lauv = tbl_07410_sum_sans_lauv[
        ~tbl_07410_sum_sans_lauv['sortiment'].isin([
            'Lauvtre massevirke',
            'Lauvtre sagtømmer',
            'Lauvtre sams sagtømmer og massevirke',
        ])
    ]
    tbl_07410_sum_sans_lauv = tbl_07410_sum_sans_lauv.groupby(
        'år', as_index=False
    ).sum(numeric_only=True)

    rows_bark = pd.DataFrame({
        'year':   tbl_07410_sum_sans_lauv['år'],
        'volume': tbl_07410_sum_sans_lauv['value'] * BARKPERCENTAGE,
        'source': 'Standing wood', 'target': 'Bark',
        'origin': 'SSB table 07410',
    })
    rows_residues = pd.DataFrame({
        'year':   tbl_07410_sum['år'],
        'volume': tbl_07410_sum['value'] * 0.10,
        'source': 'Standing wood', 'target': 'Residues from harvest',
        'origin': 'SSB table 07410',
    })

    df_gran_sag   = tbl_07410[tbl_07410['sortiment'] == 'Gran sagtømmer']
    df_gran_masse = tbl_07410[tbl_07410['sortiment'] == 'Gran massevirke']
    df_gran_board = tbl_07410[tbl_07410['sortiment'] == 'Gran sams sagtømmer og massevirke']
    df_furu_sag   = tbl_07410[tbl_07410['sortiment'] == 'Furu sagtømmer']
    df_furu_masse = tbl_07410[tbl_07410['sortiment'] == 'Furu massevirke']
    df_furu_board = tbl_07410[tbl_07410['sortiment'] == 'Furu sams sagtømmer og massevirke']
    df_lauv_sag   = tbl_07410[tbl_07410['sortiment'] == 'Lauvtre sagtømmer']
    df_lauv_masse = tbl_07410[tbl_07410['sortiment'] == 'Lauvtre massevirke']
    df_lauv_board = tbl_07410[tbl_07410['sortiment'] == 'Lauvtre sams sagtømmer og massevirke']

    # GRAN (spruce)
    rows_gran_sag   = pd.DataFrame({'source': 'Spruce', 'target': 'Sawmills',
                                    'volume': df_gran_sag['value'],
                                    'year':   df_gran_sag['år'],
                                    'origin': 'SSB table 07410'})
    rows_gran_masse = pd.DataFrame({'source': 'Spruce', 'target': 'Pulpmills',
                                    'volume': df_gran_masse['value'],
                                    'year':   df_gran_masse['år'],
                                    'origin': 'SSB table 07410'})
    rows_gran_board = pd.DataFrame({'source': 'Spruce', 'target': 'Boardmills',
                                    'volume': df_gran_board['value'],
                                    'year':   df_gran_board['år'],
                                    'origin': 'SSB table 07410'})
    rows_gran = pd.concat(
        [rows_gran_sag, rows_gran_masse, rows_gran_board], ignore_index=True
    )
    rows_gran = rows_gran.groupby('year', as_index=False)['volume'].sum()
    rows_gran = pd.DataFrame({'source': 'Standing wood', 'target': 'Spruce',
                              'volume': rows_gran['volume'],
                              'year':   rows_gran['year'],
                              'origin': 'SSB table 07410'})

    # FURU (pine)
    rows_furu_sag   = pd.DataFrame({'source': 'Pine', 'target': 'Sawmills',
                                    'volume': df_furu_sag['value'],
                                    'year':   df_furu_sag['år'],
                                    'origin': 'SSB table 07410'})
    rows_furu_masse = pd.DataFrame({'source': 'Pine', 'target': 'Pulpmills',
                                    'volume': df_furu_masse['value'],
                                    'year':   df_furu_masse['år'],
                                    'origin': 'SSB table 07410'})
    rows_furu_board = pd.DataFrame({'source': 'Pine', 'target': 'Boardmills',
                                    'volume': df_furu_board['value'],
                                    'year':   df_furu_board['år'],
                                    'origin': 'SSB table 07410'})
    rows_furu = pd.concat(
        [rows_furu_sag, rows_furu_masse, rows_furu_board], ignore_index=True
    )
    rows_furu = rows_furu.groupby('year', as_index=False)['volume'].sum()
    rows_furu = pd.DataFrame({'source': 'Standing wood', 'target': 'Pine',
                              'volume': rows_furu['volume'],
                              'year':   rows_furu['year'],
                              'origin': 'SSB table 07410'})

    # LAUV (non-coniferous)
    rows_lauv_sag   = pd.DataFrame({'source': 'Non-coniferous', 'target': 'Sawmills',
                                    'volume': df_lauv_sag['value'],
                                    'year':   df_lauv_sag['år'],
                                    'origin': 'SSB table 07410'})
    rows_lauv_masse = pd.DataFrame({'source': 'Non-coniferous', 'target': 'Pulpmills',
                                    'volume': df_lauv_masse['value'],
                                    'year':   df_lauv_masse['år'],
                                    'origin': 'SSB table 07410'})
    rows_lauv_board = pd.DataFrame({'source': 'Non-coniferous', 'target': 'Boardmills',
                                    'volume': df_lauv_board['value'],
                                    'year':   df_lauv_board['år'],
                                    'origin': 'SSB table 07410'})
    rows_lauv = pd.concat(
        [rows_lauv_sag, rows_lauv_masse, rows_lauv_board], ignore_index=True
    )
    rows_lauv = rows_lauv.groupby('year', as_index=False)['volume'].sum()
    rows_lauv = pd.DataFrame({'source': 'Standing wood', 'target': 'Non-coniferous',
                              'volume': rows_lauv['volume'],
                              'year':   rows_lauv['year'],
                              'origin': 'SSB table 07410'})

    # -------------------- SSB TABLE 08801 (trade) --------------------
    tbl_08801 = pd.concat(ssb_api_output['08801'], ignore_index=True)
    tbl_08801 = tbl_08801.loc[tbl_08801['value'] != 0]
    tbl_08801['type'] = 'default'

    for kw, typ in [
        ('Impregnert',   'impregnated'),
        ('Massevirke',   'pulpwood'),
        ('Skurtømmer',   'saw wood'),
        ('Trepeletter',  'pellets'),
        ('brensel',      'firewood'),
        ('høvellast',    'planed wood'),
        ('skurlast',     'sawn wood'),
    ]:
        tbl_08801.loc[
            tbl_08801['varenummer'].str.contains(kw, case=False), 'type'
        ] = typ

    tbl_08801.loc[tbl_08801['statistikkvariabel'] == 'Mengde 1 (M1)',
                  'statistikkvariabel'] = 'kg'
    tbl_08801.loc[tbl_08801['statistikkvariabel'] == 'Mengde 2 (M2)',
                  'statistikkvariabel'] = 'm3'

    trade_df = assign_target_source_trade(tbl_08801)
    trade_df = trade_df.rename(columns={'år': 'year'})
    trade_df_volume = trade_df.copy()
    trade_df_volume = trade_df_volume[trade_df_volume['statistikkvariabel'] == 'm3']
    trade_df_volume = trade_df_volume.groupby(
        ['year', 'source', 'target'], as_index=False
    )['value'].sum()
    trade_df_volume = trade_df_volume.rename(columns={'value': 'volume'})
    trade_df_volume['origin'] = 'SSB table 08801'

    # -------------------- Assemble initial sankey_df --------------------
    sankey_df = pd.DataFrame(columns=['source', 'target', 'volume', 'year', 'origin'])
    for extra in (
        rows_waste,
        rows_firewood_spruce,
        rows_firewood_pine,
        rows_non_coniferous_firewood,
        rows_gran_sag,
        rows_gran_masse,
        rows_gran_board,
        rows_furu_sag,
        rows_furu_masse,
        rows_furu_board,
        rows_lauv_sag,
        rows_lauv_masse,
        rows_lauv_board,
    ):
        sankey_df = pd.concat([sankey_df, extra], ignore_index=True)

    # -------------------- Treindustrien production --------------------
    df_planed = (
        treindustrien_df[treindustrien_df['Category'] == 'produced planed timber']
        .drop(columns='Category')
        .rename(columns={'Volume': 'Volume_planed'})
    )
    df_sawn = (
        treindustrien_df[treindustrien_df['Category'] == 'produced sawn timber']
        .drop(columns='Category')
        .rename(columns={'Volume': 'Volume_sawn'})
    )
    df_imp = (
        treindustrien_df[treindustrien_df['Category'] == 'produsert impregnated timber']
        .drop(columns='Category')
        .rename(columns={'Volume': 'Volume_imp'})
    )
    df_merged = df_sawn.merge(df_planed, on='year').merge(df_imp, on='year')
    print('Merged DataFrame:\n', df_merged, '\n')

    flows_df = create_flow_rows(
        sankey_df=sankey_df,
        treindustrien_production=df_merged,
        treindustrien_inventory=treindustrien_df,
        trade_df=trade_df_volume,
    )

    # -------------------- Treindustrien inventory (signed diffs) --------------------
    treindustrien_df = treindustrien_df.sort_values(['Category', 'year'])
    # IMPORTANT: Do NOT apply .abs() here. `get_source_target_treindustrien`
    # dispatches flows by the sign of `VolumeChange`:
    #   VolumeChange >= 0  ->  mill -> inventory  (stock build-up)
    #   VolumeChange <  0  ->  inventory -> mill  (stock draw-down)
    # Applying .abs() forces every year into the "positive" branch, systematically
    # drawing every fluctuation as a build-up and inflating the apparent stock
    # accumulation. `create_treindustrien_inventory_rows` takes the absolute
    # value for the Sankey link width (Sankey links can't be negative) AFTER
    # routing is decided.
    treindustrien_df['VolumeChange'] = (
        treindustrien_df.groupby('Category')['Volume'].diff()
    )
    treindustrien_df_inventory = create_treindustrien_inventory_rows(treindustrien_df)

    # -------------------- Concatenate everything --------------------
    sankey_df = pd.concat([sankey_df, trade_df_volume],            ignore_index=True)
    sankey_df = pd.concat([sankey_df, flows_df],                   ignore_index=True)
    sankey_df = pd.concat([sankey_df, treindustrien_df_inventory], ignore_index=True)
    sankey_df = pd.concat([sankey_df, fao_boardmills_df],          ignore_index=True)
    sankey_df = pd.concat([sankey_df, rows_EOL],                   ignore_index=True)
    sankey_df = pd.concat([sankey_df, rows_bark],                  ignore_index=True)
    sankey_df = pd.concat([sankey_df, rows_residues],              ignore_index=True)
    sankey_df = pd.concat([sankey_df, rows_gran, rows_furu, rows_lauv], ignore_index=True)
    sankey_df = pd.concat([
        sankey_df,
        rows_firewood_spruce_sw,
        rows_firewood_pine_sw,
        rows_non_coniferous_firewood_sw,
    ], ignore_index=True)
    sankey_df = pd.concat([sankey_df, rows_mortality], ignore_index=True)

    sankey_df = boardmill_process(dataframe=sankey_df)
    sankey_df = pulpmills_process(
        dataframe=sankey_df,
        short_term_products=86,
        energy=8,
        loss=6,
    )

    # -------------------- Derived aggregates --------------------
    df_firewood = (
        sankey_df[sankey_df['target'] == 'Firewood']
        .groupby('year', as_index=False)['volume']
        .sum()
    )
    df_firewood['source'] = 'Firewood'
    df_firewood['target'] = 'Energy'
    df_firewood['origin'] = 'SSB various tables'
    sankey_df = pd.concat([sankey_df, df_firewood], ignore_index=True)

    df_wpp = (
        sankey_df[sankey_df['target'] == 'Wood Product processing & Logistics']
        .groupby('year', as_index=False)['volume']
        .sum()
    )
    df_wpp['source'] = 'Wood Product processing & Logistics'
    df_wpp['origin'] = 'SSB and Treindustrien'

    df_wpp_ltu = df_wpp.copy()
    df_wpp_ltu['volume'] = df_wpp_ltu['volume'] * 0.90
    df_wpp_ltu['target'] = 'Long-term USE'

    df_wpp_energy = df_wpp.copy()
    df_wpp_energy['volume'] = df_wpp_energy['volume'] * 0.10
    df_wpp_energy['target'] = 'Energy'

    sankey_df = pd.concat([sankey_df, df_wpp_ltu, df_wpp_energy], ignore_index=True)

    sankey_df['year']   = pd.to_numeric(sankey_df['year'], errors='coerce')
    sankey_df          = sankey_df.dropna(subset=['year', 'volume'])
    sankey_df['year']   = sankey_df['year'].astype(int)
    sankey_df['volume'] = sankey_df['volume'].abs()

    # -------------------- Final dedupe pass ---------------------------------
    # Several upstream steps emit multiple rows for the same (source, target,
    # year) triplet: `rows_waste` stacks one row per behandlingsmåte from SSB
    # 10513, `boardmill_process` / `pulpmills_process` multiply every
    # `target == Boardmills/Pulpmills` input row into 2-3 output rows, and
    # any species pool (Spruce/Pine/Non-coniferous) receives rows from both
    # SSB 07410 (industrial harvest) and SSB 11181 (firewood share). These are
    # mathematically summable — they represent different components of the
    # same physical flow — but left un-aggregated they inflate link widths in
    # the Sankey and make every downstream mass-balance check look violated.
    #
    # Collapse to one row per (year, source, target) by summing volumes and
    # joining the distinct origin tags so provenance is preserved.
    sankey_df = (
        sankey_df
        .groupby(['year', 'source', 'target'], as_index=False)
        .agg(volume=('volume', 'sum'),
             origin=('origin', lambda s: ' | '.join(sorted({str(x) for x in s}))))
    )

    # -------------------- Write outputs --------------------
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    sankey_df.to_csv(OUTPUT_CSV, index=False)
    print(f"Wrote {len(sankey_df)} rows to {OUTPUT_CSV}")

    SANKEY_DIAGRAM_DIR.mkdir(parents=True, exist_ok=True)
    sankey_df.to_csv(SANKEY_DIAGRAM_COPY, index=False)
    print(f"Wrote {len(sankey_df)} rows to {SANKEY_DIAGRAM_COPY}")
    return sankey_df


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description="Build or load the Circwood WP4 wood-flow Sankey dataset."
    )
    parser.add_argument(
        '--fresh',
        action='store_true',
        help=(
            "Fetch fresh data from the SSB API instead of using the cached "
            "sankey_data.csv (requires pyjstat, requests and network access)."
        ),
    )
    args = parser.parse_args()
    build_sankey(use_cached_ssb=not args.fresh)
