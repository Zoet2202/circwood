"""Top-level orchestration: build or load the Circwood WP4 Sankey dataset."""

from __future__ import annotations

import argparse
import logging

import pandas as pd

from circwood import config

SANKEY_COLUMNS = ["source", "target", "volume", "year", "origin"]


def build_sankey(use_cached_ssb: bool = True) -> pd.DataFrame:
    """Return the wood-flow Sankey dataset.

    Parameters
    ----------
    use_cached_ssb : bool, default True
        ``True``  -> load the pre-computed ``data/processed/sankey_data.csv``.
        ``False`` -> re-fetch from the SSB API and regenerate it (needs the
        ``ssb`` extras and network access).
    """
    if use_cached_ssb:
        return load_cached_sankey()
    from circwood.transform.assemble import run_fresh_pipeline
    return run_fresh_pipeline()


def build_carbon(use_cached_ssb: bool = True, write: bool = False) -> pd.DataFrame:
    """Return the Sankey dataset with an added ``carbon`` column (kg C).

    If ``write`` is True, also (re)generate ``data/processed/carbon_data.csv``.
    """
    from circwood.carbon import add_carbon

    df = add_carbon(build_sankey(use_cached_ssb=use_cached_ssb))
    if write:
        config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        df.to_csv(config.CARBON_CSV, index=False)
        print(f"Wrote {len(df)} rows to {config.CARBON_CSV}")
    return df


def load_cached_sankey() -> pd.DataFrame:
    """Load the pre-generated Sankey dataset from disk."""
    if not config.SANKEY_CSV.exists():
        raise FileNotFoundError(
            f"Cached Sankey dataset not found at {config.SANKEY_CSV}.\n"
            f"Run `build_sankey(use_cached_ssb=False)` to regenerate it "
            f"(requires the `ssb` extras and network access)."
        )
    df = pd.read_csv(config.SANKEY_CSV)
    df.columns = [c.lstrip("﻿").strip() for c in df.columns]
    return df


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build or load the Circwood WP4 wood-flow Sankey dataset."
    )
    parser.add_argument(
        "--fresh", action="store_true",
        help="Fetch fresh data from the SSB API instead of the cached CSV.",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Log every ingestion step (table vintages, retries, failures).",
    )
    args = parser.parse_args()
    # Only the CLI configures logging; the library just emits records.
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    df = build_sankey(use_cached_ssb=not args.fresh)
    print(f"Loaded {len(df)} rows ({df['year'].min()}-{df['year'].max()}).")


if __name__ == "__main__":
    main()
