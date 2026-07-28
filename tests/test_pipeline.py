"""Sanity/regression tests on the cached dataset and viz pipeline."""

from __future__ import annotations

from circwood import build_sankey, constants as C
from circwood.pipeline import SANKEY_COLUMNS
from circwood.viz.sankey import make_sankey


def test_cached_dataset_loads_with_expected_schema():
    df = build_sankey(use_cached_ssb=True)
    assert list(df.columns) == SANKEY_COLUMNS
    assert len(df) > 0
    assert df["volume"].ge(0).all()


def test_cached_pulp_share_is_about_26pct():
    """The headline result: Sawmills -> Pulpmills ~= 26% of sawmill input."""
    df = build_sankey(use_cached_ssb=True)
    window = df[(df.year >= C.MIN_YEAR) & (df.year <= C.MAX_YEAR)]
    inflow = window[window.target == "Sawmills"].volume.sum()
    pulp = window[(window.source == "Sawmills") & (window.target == "Pulpmills")].volume.sum()
    ratio = pulp / inflow
    assert 0.24 <= ratio <= 0.28, f"pulp share {ratio:.3f} outside expected band"


def test_make_sankey_builds_a_figure():
    df = build_sankey(use_cached_ssb=True)
    fig = make_sankey(df)
    assert len(fig.data) == 1
    assert len(fig.data[0].node.label) > 0
    assert len(fig.data[0].link.value) > 0
