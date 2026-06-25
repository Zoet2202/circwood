"""Top-level orchestration: build or load the Circwood WP4 Sankey dataset."""

from __future__ import annotations

import argparse

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
    args = parser.parse_args()
    df = build_sankey(use_cached_ssb=not args.fresh)
    print(f"Loaded {len(df)} rows ({df['year'].min()}-{df['year'].max()}).")


if __name__ == "__main__":
    main()
