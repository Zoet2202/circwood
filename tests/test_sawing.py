"""Unit tests that lock in the three sawing-function fixes.

These build small synthetic inputs and call ``create_flow_rows`` directly, so
they verify the fix logic itself (not the cached output). See
``Sawing_function_diagnosis.docx`` for the bugs being guarded against.
"""

from __future__ import annotations

import pandas as pd
import pytest

from circwood import constants as C
from circwood.transform.sawing import (
    SawmillCoefficients,
    create_flow_rows,
    resolve_coefficients,
)


def _empty_trade() -> pd.DataFrame:
    return pd.DataFrame(columns=["year", "source", "target", "volume"])


def _zero_inventory(years) -> pd.DataFrame:
    """Inventory frame whose year-over-year change is zero for every year."""
    rows = []
    for cat in ["Timber inventory", "sawn timber inventory"]:
        for y in years:
            rows.append({"year": y, "Category": cat, "Volume": 1000.0})
    return pd.DataFrame(rows)


def _production(years) -> pd.DataFrame:
    return pd.DataFrame({
        "year": years,
        "Volume_sawn": [1.0] * len(years),
        "Volume_planed": [0.0] * len(years),
        "Volume_imp": [0.0] * len(years),
    })


def _inflow(year_to_volume: dict) -> pd.DataFrame:
    return pd.DataFrame({
        "source": "Spruce",
        "target": "Sawmills",
        "volume": list(year_to_volume.values()),
        "year": list(year_to_volume.keys()),
        "origin": "test",
    })


def test_year_alignment_no_positional_offset():
    """Cellulose for a year must use that year's inflow, not an earlier one.

    SSB inflow spans 2006-2013; production spans 2011-2013. The dominant bug was
    a 5-year positional offset between these ranges.
    """
    inflow = _inflow({
        2006: 100.0, 2007: 200.0, 2008: 300.0, 2009: 400.0, 2010: 500.0,
        2011: 1000.0, 2012: 2000.0, 2013: 3000.0,
    })
    prod_years = [2011, 2012, 2013]
    flows = create_flow_rows(
        trade_df=_empty_trade(),
        sankey_df=inflow,
        treindustrien_production=_production(prod_years),
        treindustrien_inventory=_zero_inventory(prod_years),
    )
    cellulose = flows[(flows.source == "Sawmills") & (flows.target == "Pulpmills")]
    cellulose = cellulose.set_index("year")["volume"]

    assert cellulose.loc[2011] == pytest.approx(C.CELLULOSE * 1000.0)
    assert cellulose.loc[2012] == pytest.approx(C.CELLULOSE * 2000.0)
    assert cellulose.loc[2013] == pytest.approx(C.CELLULOSE * 3000.0)


def test_cellulose_is_26pct_of_input_not_dry_volume():
    """Pulp chips must be 26% of raw input, not 26% of post-shrinkage volume."""
    years = [2011, 2012]
    inflow = _inflow({2011: 1000.0, 2012: 2000.0})
    flows = create_flow_rows(
        trade_df=_empty_trade(),
        sankey_df=inflow,
        treindustrien_production=_production(years),
        treindustrien_inventory=_zero_inventory(years),
    )
    cellulose = flows[(flows.source == "Sawmills") & (flows.target == "Pulpmills")]
    cellulose = cellulose.set_index("year")["volume"]
    assert cellulose.loc[2011] == pytest.approx(0.26 * 1000.0)
    # Guard against the dry-volume regression (0.26 * 0.95 = 0.247).
    assert cellulose.loc[2011] != pytest.approx(0.247 * 1000.0)


def test_sawmills_transform_is_mass_balanced():
    """With zero trade/inventory, total Sawmills outflow equals its input."""
    years = [2011, 2012, 2013]
    inflow_map = {2011: 1000.0, 2012: 2000.0, 2013: 3000.0}
    flows = create_flow_rows(
        trade_df=_empty_trade(),
        sankey_df=_inflow(inflow_map),
        treindustrien_production=_production(years),
        treindustrien_inventory=_zero_inventory(years),
    )
    outflow = (
        flows[flows.source == "Sawmills"].groupby("year")["volume"].sum()
    )
    for y, v in inflow_map.items():
        assert outflow.loc[y] == pytest.approx(v)


def test_byproduct_shares_sum_with_sawn_yield_to_one():
    """The defined coefficients must partition the input exactly."""
    total = C.SHRINKAGE + C.SAWDUST + C.CELLULOSE + C.SCRAPS + C.SAWN_YIELD
    assert total == pytest.approx(1.0)


# --- Sawmill/planing coefficient option (manual vs Treindustrien) ------------

def test_default_coefficients_match_constants():
    """Omitting coefficients must reproduce the package constants exactly."""
    coef = SawmillCoefficients()
    assert coef.shrinkage == C.SHRINKAGE
    assert coef.cellulose == C.CELLULOSE
    assert coef.sawn_yield == pytest.approx(C.SAWN_YIELD)
    assert coef.byproduct_total + coef.sawn_yield == pytest.approx(1.0)
    assert coef.source == "manual"


def test_manual_coefficients_change_byproduct_flows():
    """A custom cellulose share must scale the Sawmills -> Pulpmills flow."""
    years = [2011, 2012]
    inflow = _inflow({2011: 1000.0, 2012: 2000.0})
    coef = SawmillCoefficients(
        shrinkage=0.05, sawdust=0.14, cellulose=0.30, scraps=0.05)
    flows = create_flow_rows(
        trade_df=_empty_trade(),
        sankey_df=inflow,
        treindustrien_production=_production(years),
        treindustrien_inventory=_zero_inventory(years),
        coefficients=coef,
    )
    cellulose = flows[(flows.source == "Sawmills") & (flows.target == "Pulpmills")]
    cellulose = cellulose.set_index("year")["volume"]
    assert cellulose.loc[2011] == pytest.approx(0.30 * 1000.0)


def test_custom_coefficients_still_mass_balance():
    """Any coefficient choice still partitions the input (Sawmills balances)."""
    years = [2011, 2012, 2013]
    inflow_map = {2011: 1000.0, 2012: 2000.0, 2013: 3000.0}
    coef = SawmillCoefficients(
        shrinkage=0.03, sawdust=0.10, cellulose=0.30, scraps=0.07)
    flows = create_flow_rows(
        trade_df=_empty_trade(),
        sankey_df=_inflow(inflow_map),
        treindustrien_production=_production(years),
        treindustrien_inventory=_zero_inventory(years),
        coefficients=coef,
    )
    outflow = flows[flows.source == "Sawmills"].groupby("year")["volume"].sum()
    for y, v in inflow_map.items():
        assert outflow.loc[y] == pytest.approx(v)


def test_from_treindustrien_derives_yield_and_rescales_byproducts():
    """Sawn yield = Σ produced sawn / Σ input; by-products fill the remainder."""
    years = [2011, 2012]
    initial = pd.Series([1000.0, 1000.0], index=years)
    produced_sawn = pd.Series([600.0, 600.0], index=years)  # 60% yield
    coef = SawmillCoefficients.from_treindustrien(produced_sawn, initial)
    assert coef.source == "treindustrien"
    assert coef.sawn_yield == pytest.approx(0.60)
    assert coef.byproduct_total == pytest.approx(0.40)
    assert coef.byproduct_total + coef.sawn_yield == pytest.approx(1.0)
    # Relative proportions of the by-products are preserved while rescaling.
    base = SawmillCoefficients()
    assert coef.cellulose / coef.sawdust == pytest.approx(base.cellulose / base.sawdust)


def test_from_treindustrien_ignores_years_without_input():
    """Years with produced sawn but no log input must not bias the average."""
    years = [2010, 2011, 2012]
    initial = pd.Series([0.0, 1000.0, 1000.0], index=years)   # 2010 has no input
    produced_sawn = pd.Series([999.0, 500.0, 500.0], index=years)
    coef = SawmillCoefficients.from_treindustrien(produced_sawn, initial)
    assert coef.sawn_yield == pytest.approx(0.50)  # 1000 / 2000, 2010 excluded


def test_resolve_coefficients_manual_returns_base():
    """Manual mode returns the supplied base unchanged (source normalised)."""
    years = [2011, 2012]
    base = SawmillCoefficients(cellulose=0.30)
    coef = resolve_coefficients(
        "manual", _empty_trade(), _inflow({2011: 1.0, 2012: 1.0}),
        _production(years), _zero_inventory(years), base=base,
    )
    assert coef.cellulose == 0.30
    assert coef.source == "manual"
