"""Mass-balance tests.

`test_assembly_is_mass_conserving` proves the *transform chain* conserves
volume at every process node when fed consistent inputs — i.e. a correctly
assembled dataset balances by construction. This is the guarantee behind
fixing diagnosis Issue 3.

`test_cached_*` documents the known state of the shipped cached dataset (which
predates the Issue-3 fix): only the Sawmills node is materially out of balance.
"""

from __future__ import annotations

import pandas as pd
import pytest

from circwood import build_sankey, constants as C
from circwood.transform.sawing import create_flow_rows
from circwood.transform.mills import boardmill_process, pulpmills_process
from circwood.validate import assert_balanced, process_imbalances


def _synthetic_primary():
    """Minimal, consistent set of primary rows for one year (no trade)."""
    year = 2015
    species_edges = [
        ("Standing wood", "Spruce", 2000.0),
        ("Spruce", "Sawmills", 1200.0),
        ("Spruce", "Pulpmills", 600.0),
        ("Spruce", "Boardmills", 200.0),
    ]
    primary = pd.DataFrame(
        [{"source": s, "target": t, "volume": v, "year": year, "origin": "test"}
         for s, t, v in species_edges]
    )
    production = pd.DataFrame({
        "year": [year], "Volume_sawn": [500.0],
        "Volume_planed": [300.0], "Volume_imp": [80.0],
    })
    # Inventory with zero year-over-year change.
    inventory = pd.DataFrame([
        {"year": year, "Category": cat, "Volume": 100.0}
        for cat in ["Timber inventory", "sawn timber inventory"]
    ])
    trade = pd.DataFrame(columns=["year", "source", "target", "volume"])
    return primary, production, inventory, trade


def _assemble(primary, production, inventory, trade):
    """Run the real transform chain (mirrors the pipeline's assembly half)."""
    flows = create_flow_rows(
        trade_df=trade, sankey_df=primary,
        treindustrien_production=production, treindustrien_inventory=inventory,
    )
    df = pd.concat([primary, flows], ignore_index=True)
    df = boardmill_process(df)
    df = pulpmills_process(df)

    # Wood Product processing & Logistics split.
    wpp_in = (
        df[df["target"] == "Wood Product processing & Logistics"]
        .groupby("year", as_index=False)["volume"].sum()
    )
    ltu = wpp_in.assign(volume=wpp_in["volume"] * C.WPP_LONG_TERM,
                        source="Wood Product processing & Logistics",
                        target="Long-term USE", origin="derived")
    en = wpp_in.assign(volume=wpp_in["volume"] * C.WPP_ENERGY,
                       source="Wood Product processing & Logistics",
                       target="Energy", origin="derived")
    return pd.concat([df, ltu, en], ignore_index=True)


def test_assembly_is_mass_conserving():
    """Every process node balances within 1% when assembled correctly."""
    df = _assemble(*_synthetic_primary())
    # Sawmills, Pulpmills, Boardmills, WPP must balance tightly.
    assert_balanced(df, rel_tol=0.01,
                    nodes=["Sawmills", "Pulpmills", "Boardmills",
                           "Wood Product processing & Logistics"])


# Nodes that genuinely conserve (their split functions enforce 100% pass-through).
CONSERVING_NODES = ["Pulpmills", "Boardmills"]


def test_cached_conserving_nodes_balance():
    """Pulpmills and Boardmills balance exactly in the cached data."""
    df = build_sankey()
    window = df[(df.year >= C.MIN_YEAR) & (df.year <= C.MAX_YEAR)]
    assert_balanced(window, rel_tol=0.001, nodes=CONSERVING_NODES)


def test_cached_sawmills_is_materially_unbalanced():
    """Sawmills is the one pass-through node that is materially off.

    This is diagnosis Issue 3, still present in the shipped cached dataset (the
    sawn-side outflows come from Treindustrien, larger than the SSB log input).
    The imbalance is year-dependent, so this checks the 2012-2022 window.
    """
    df = build_sankey()
    window = df[(df.year >= C.MIN_YEAR) & (df.year <= C.MAX_YEAR)]
    rep = process_imbalances(window).set_index("node")
    assert rep.loc["Sawmills", "rel_imbalance"] > 0.1
