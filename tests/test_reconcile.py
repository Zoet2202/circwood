"""Reconciliation tests: pipeline totals vs source/control aggregates (A3)."""

from __future__ import annotations

from circwood import build_sankey
from circwood.reconcile import (
    CONTROL_TOTALS,
    annual_roundwood_harvest,
    domestic_sawtimber,
    treindustrien_reconciliation,
)


def test_treindustrien_throughput_reconciles():
    """Planed/impregnated throughput matches the Treindustrien source CSV."""
    df = build_sankey()
    rep = treindustrien_reconciliation(df)
    assert (rep["rel_diff"].abs() < 0.01).all(), rep.to_string()


def test_domestic_sawtimber_matches_ssb_control_total():
    """Domestic sawtimber harvest matches the SSB 07410 control total."""
    df = build_sankey()
    ctrl = CONTROL_TOTALS["domestic_sawtimber_2012_2022"]
    got = domestic_sawtimber(df, ctrl["min_year"], ctrl["max_year"])
    assert abs(got / ctrl["value"] - 1.0) < 0.01, f"{got:,.0f} vs {ctrl['value']:,.0f}"


def test_annual_roundwood_harvest_is_plausible():
    """Mean annual roundwood harvest is in the realistic 9-14 Mm3/yr range."""
    df = build_sankey()
    mean_harvest = annual_roundwood_harvest(df, 2012, 2022)
    assert 9e6 <= mean_harvest <= 14e6, f"{mean_harvest:,.0f} m3/yr"
