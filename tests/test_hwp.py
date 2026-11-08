"""Tests for the empirical HWP fraction β (Phase F, ``circwood.analysis.hwp``).

Synthetic graphs pin the tracing rules (production basis, exogenous imports,
inventories, closure gap); the committed dataset pins the golden series and
the exported ``beta_series.csv``. The must-raise tests are the "never return
partial data" guarantee.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

from circwood import config, constants as C
from circwood.analysis.hwp import (
    EXPORT_COLUMNS,
    FATE_BUCKETS,
    ROADMAP_COLUMNS,
    absorption_fractions,
    beta_series,
    classify_sink,
    export_beta_series,
    literal_beta,
)
from circwood.carbon import load_model

HW = "Harvested wood"
LT = "Long-term USE"
ST = "Short-term USE"
WPP = "Wood Product processing & Logistics"
YEAR = 2015


def _frame(edges, year=YEAR) -> pd.DataFrame:
    return pd.DataFrame(
        [(s, t, float(v), year) for s, t, v in edges],
        columns=["source", "target", "volume", "year"],
    )


def _fates(edges, pool="Spruce") -> pd.Series:
    """Bucket fractions of one pool, every bucket present."""
    fr = absorption_fractions(_frame(edges))
    return (fr[fr["pool"] == pool].groupby("bucket")["fraction"].sum()
            .reindex(list(FATE_BUCKETS), fill_value=0.0))


# Spruce harvest 100 -> Sawmills, split 50/50 to long-term use and pulp.
CHAIN = [
    (HW, "Spruce", 100), ("Spruce", "Sawmills", 100),
    ("Sawmills", WPP, 50), ("Sawmills", "Pulpmills", 50),
    (WPP, LT, 50), ("Pulpmills", ST, 50),
]
# Pine and non-coniferous pools, so beta_series has all three species.
OTHER_POOLS = [
    (HW, "Pine", 10), ("Pine", "Firewood", 10),
    (HW, "Non-coniferous", 10), ("Non-coniferous", "Firewood", 10),
    ("Firewood", "Energy", 20),
]


#: Frozen input for every test whose expected values are numbers of one dataset
#: vintage (2026-06-29): a byte copy of data/processed/carbon_data.csv at that
#: vintage. The refresh never touches it, so an SSB revision cannot break these
#: pins (the live dataset is covered by the invariant tests and by the
#: committed-vs-fresh export fence). Regenerate it only with a deliberate
#: method change, re-deriving the pinned values in the same commit.
FROZEN_CARBON_CSV = Path(__file__).parent / "fixtures" / "beta_input_carbon.csv"


@pytest.fixture(scope="module")
def frozen_flows() -> pd.DataFrame:
    return pd.read_csv(FROZEN_CARBON_CSV)


@pytest.fixture(scope="module")
def frozen_beta(frozen_flows) -> pd.DataFrame:
    return beta_series(frozen_flows)


@pytest.fixture(scope="module")
def committed_beta() -> pd.DataFrame:
    """beta on the live committed dataset: invariant checks only, no pinned numbers."""
    return beta_series()


# ---------------------------------------------------------------------------
# Passing behaviours
# ---------------------------------------------------------------------------
def test_synthetic_chain_splits_half_and_half():
    f = _fates(CHAIN)
    assert f["beta"] == pytest.approx(0.5, abs=1e-12)
    assert f["pulp_share"] == pytest.approx(0.5, abs=1e-12)
    assert f.drop(["beta", "pulp_share"]).abs().max() < 1e-12


def test_import_leaves_production_beta_unchanged_but_doubles_literal():
    base = _frame(CHAIN + OTHER_POOLS)
    with_import = _frame([
        (HW, "Spruce", 100), ("Spruce", "Sawmills", 100),
        ("Import planed wood", "Sawmills", 100),
        ("Sawmills", WPP, 100), ("Sawmills", "Pulpmills", 100),
        (WPP, LT, 100), ("Pulpmills", ST, 100),
    ] + OTHER_POOLS)
    a = beta_series(base, min_year=YEAR, max_year=YEAR).set_index("species")
    b = beta_series(with_import, min_year=YEAR, max_year=YEAR).set_index("species")
    assert b.loc["spruce", "beta"] == pytest.approx(a.loc["spruce", "beta"], abs=1e-12)
    assert b.loc["spruce", "beta"] == pytest.approx(0.5, abs=1e-12)
    assert b.loc["spruce", "beta_literal"] == pytest.approx(2 * a.loc["spruce", "beta_literal"])
    assert b.loc["all", "beta_literal"] == pytest.approx(2 * a.loc["all", "beta_literal"])
    # Half of Long-term USE is now imported wood.
    assert b.loc["all", "domestic_share_of_ltuse"] == pytest.approx(0.5)


def test_import_species_shares_do_not_touch_production_beta(tmp_path, frozen_flows):
    nodes = pd.read_csv(config.CARBON_NODES_FILE)
    imports = nodes["node"].isin(["Import planed wood", "Import Boards"])
    nodes.loc[imports, ["spruce_share", "pine_share", "nonconif_share"]] = [0, 1, 0]
    edited = tmp_path / "carbon_nodes.csv"
    nodes.to_csv(edited, index=False)
    model = load_model(nodes_file=edited)

    full = frozen_flows[frozen_flows["year"].between(2019, 2020)]
    # Strip the species/carbon columns so the literal ratio is recomputed with
    # the edited model; the production trace only ever sees volumes.
    national = full.groupby(["source", "target", "year"], as_index=False)["volume"].sum()

    ref = beta_series(full, min_year=2019, max_year=2020).set_index(["year", "species"])
    alt = beta_series(national, min_year=2019, max_year=2020,
                      model=model).set_index(["year", "species"])
    pdt.assert_series_equal(alt["beta"], ref["beta"], check_exact=False, atol=1e-12, rtol=0)
    # ...while the literal ratio, which counts imports, does move.
    assert abs(alt.loc[(2020, "pine"), "beta_literal"]
               - ref.loc[(2020, "pine"), "beta_literal"]) > 0.01


def test_shares_sum_to_one_on_committed_dataset(committed_beta):
    out = committed_beta
    assert sorted(out["year"].unique()) == list(range(C.BETA_MIN_YEAR, C.BETA_MAX_YEAR + 1))
    total = out[list(FATE_BUCKETS)].sum(axis=1)
    assert (total - 1).abs().max() <= 1e-12
    four = out[["beta", "pulp_share", "energy_share", "other_share"]].sum(axis=1)
    assert (four - 1).abs().max() <= 1e-12


def test_inventory_deposit_is_stock_change_and_withdrawal_is_exogenous():
    deposit = _fates([
        (HW, "Spruce", 100), ("Spruce", "Sawmills", 100),
        ("Sawmills", WPP, 40), ("Sawmills", "Pulpmills", 40),
        ("Sawmills", "Timber inventory", 20),
        (WPP, LT, 40), ("Pulpmills", ST, 40),
    ])
    assert deposit["stock_change_share"] == pytest.approx(0.2, abs=1e-12)
    assert deposit["beta"] == pytest.approx(0.4, abs=1e-12)

    withdrawal = _fates([
        (HW, "Spruce", 100), ("Spruce", "Sawmills", 100),
        ("Timber inventory", "Sawmills", 50),
        ("Sawmills", WPP, 75), ("Sawmills", "Pulpmills", 75),
        (WPP, LT, 75), ("Pulpmills", ST, 75),
    ])
    assert withdrawal["beta"] == pytest.approx(0.5, abs=1e-12)
    assert withdrawal["stock_change_share"] == 0.0


def test_two_way_inventory_is_netted():
    """Deposit 30 and withdrawal 10 in one year == a net deposit of 20.

    The committed dataset has the net-withdrawal variant; see the next test.
    """
    two_way = _fates([
        (HW, "Spruce", 100), ("Spruce", "Sawmills", 100),
        ("Sawmills", "Timber inventory", 30), ("Timber inventory", "Planingmills", 10),
        ("Sawmills", WPP, 35), ("Sawmills", "Pulpmills", 35),
        ("Planingmills", WPP, 10), (WPP, LT, 45), ("Pulpmills", ST, 35),
    ])
    netted = _fates([
        (HW, "Spruce", 100), ("Spruce", "Sawmills", 100),
        ("Sawmills", "Timber inventory", 20), ("Sawmills", "Planingmills", 10),
        ("Sawmills", WPP, 35), ("Sawmills", "Pulpmills", 35),
        ("Planingmills", WPP, 10), (WPP, LT, 45), ("Pulpmills", ST, 35),
    ])
    pdt.assert_series_equal(two_way, netted, check_exact=False, atol=1e-12, rtol=0)
    assert two_way["stock_change_share"] == pytest.approx(0.2, abs=1e-12)


def test_two_way_inventory_net_withdrawal_cycle_is_netted():
    """The committed shape: Sawmills <-> Timber inventory, net withdrawal (2012/2016/2022).

    Two Treindustrien categories map to the one node, with opposite signs.
    """
    two_way = _fates([
        (HW, "Spruce", 100), ("Spruce", "Sawmills", 100),
        ("Sawmills", "Timber inventory", 10), ("Timber inventory", "Sawmills", 30),
        ("Sawmills", WPP, 60), ("Sawmills", "Pulpmills", 60),
        (WPP, LT, 60), ("Pulpmills", ST, 60),
    ])
    netted = _fates([
        (HW, "Spruce", 100), ("Spruce", "Sawmills", 100),
        ("Timber inventory", "Sawmills", 20),
        ("Sawmills", WPP, 60), ("Sawmills", "Pulpmills", 60),
        (WPP, LT, 60), ("Pulpmills", ST, 60),
    ])
    pdt.assert_series_equal(two_way, netted, check_exact=False, atol=1e-12, rtol=0)
    assert two_way["beta"] == pytest.approx(0.5, abs=1e-12)   # W2's sink rule gives 60/130
    assert two_way["stock_change_share"] == 0.0


def test_net_export_sawn_timber_is_exogenous_as_source_and_export_as_sink():
    as_source = _fates(CHAIN[:-2] + [
        ("Net export sawn timber", WPP, 50), (WPP, LT, 100), ("Pulpmills", ST, 50),
    ])
    assert as_source["beta"] == pytest.approx(0.5, abs=1e-12)
    assert as_source["export_sawn_share"] == 0.0

    as_sink = _fates(CHAIN[:-2] + [
        (WPP, "Net export sawn timber", 10), (WPP, LT, 40), ("Pulpmills", ST, 50),
    ])
    assert as_sink["export_sawn_share"] == pytest.approx(0.1, abs=1e-12)
    assert as_sink["beta"] == pytest.approx(0.4, abs=1e-12)


def test_surplus_becomes_closure_gap_of_exactly_that_fraction():
    edges = [
        (HW, "Spruce", 100), ("Spruce", "Sawmills", 100),
        ("Sawmills", WPP, 40), ("Sawmills", "Pulpmills", 40),
        (WPP, LT, 40), ("Pulpmills", ST, 40),
    ]
    fr = absorption_fractions(_frame(edges))
    gap = fr[fr["bucket"] == "closure_gap"]
    assert list(gap["sink"]) == ["closure gap at Sawmills"]
    assert gap["fraction"].iloc[0] == pytest.approx(0.2, abs=1e-12)
    # A gap under the tolerance passes; over it, it raises naming year and node.
    absorption_fractions(_frame(edges), closure_tol=0.25)
    with pytest.raises(ValueError, match=r"year 2015.*'Sawmills'"):
        absorption_fractions(_frame(edges), closure_tol=0.1)


def test_all_is_harvest_carbon_weighted_mean(committed_beta):
    b = committed_beta
    for year, g in b.groupby("year"):
        sp = g[g["species"] != "all"]
        al = g[g["species"] == "all"].iloc[0]
        w = sp["harvest_carbon_kgc"]
        assert al["harvest_carbon_kgc"] == pytest.approx(w.sum())
        for col in FATE_BUCKETS:
            assert al[col] == pytest.approx((sp[col] * w).sum() / w.sum(), abs=1e-12), (year, col)


#: beta(all), production basis, from this module's output on the frozen
#: input (tests/fixtures/beta_input_carbon.csv, vintage 2026-06-29).
#: Re-derive only with a deliberate method change.
#: Cross-checked against the W2 reference tracer (w2_beta_out.csv): equal to
#: < 1e-15 in every year without a two-way inventory. 2012, 2016 and 2022 are
#: two-way years (Sawmills <-> Timber inventory) and differ from W2 (0.20274 /
#: 0.2298 / 0.2012), because the reference treated the deposit as stock change
#: instead of netting it against the same-year withdrawal (W2 spec §2b's rule).
GOLDEN_BETA_ALL = {
    2012: 0.202795, 2013: 0.213563, 2014: 0.229114, 2015: 0.220771,
    2016: 0.230170, 2017: 0.208886, 2018: 0.208191, 2019: 0.191100,
    2020: 0.172360, 2021: 0.184307, 2022: 0.204171, 2023: 0.188025,
}


def test_golden_beta_all(frozen_beta):
    got = frozen_beta[frozen_beta["species"] == "all"].set_index("year")["beta"]
    assert sorted(got.index) == sorted(GOLDEN_BETA_ALL)
    for year, expected in GOLDEN_BETA_ALL.items():
        assert got[year] == pytest.approx(expected, abs=1e-6), year
    # Independent reference: W2's tracer (w2_beta_out.csv) in years without a
    # two-way inventory, where both methods must agree to rounding.
    W2_BETA_ALL = {2015: 0.22077128198093976, 2020: 0.17235965102385667}
    for year, ref in W2_BETA_ALL.items():
        assert got[year] == pytest.approx(ref, abs=1e-12), year


#: Every fate bucket for 2015 (no two-way inventory), all species, copied at
#: full precision from W2's independent tracer (w2_beta_out.csv).
W2_2015 = {
    "spruce": dict(beta=0.2679470112167594, pulp_share=0.23468972825164744,
                   energy_share=0.11417491967455304, export_roundwood_share=0.3364996715959905,
                   export_sawn_share=0.0027162454008698845,
                   export_firewood_share=0.0011838291158604002, loss_share=0.02216657974313337,
                   shrinkage_share=0.019572916212047733, stock_change_share=0.000687646936882202,
                   closure_gap=0.00036145185225605923),
    "pine": dict(beta=0.23714651250813878, pulp_share=0.2181144804329386,
                 energy_share=0.1867466639655683, export_roundwood_share=0.3121810854303626,
                 export_sawn_share=0.002703359791018647,
                 export_firewood_share=0.0033210934503164493, loss_share=0.019255821707105426,
                 shrinkage_share=0.019480064159034173, stock_change_share=0.0006843848052127936,
                 closure_gap=0.00036653375030417404),
    "non-coniferous": dict(beta=0.000284235364937568, pulp_share=0.042007277956309465,
                           energy_share=0.8690065442492938,
                           export_roundwood_share=0.06172161811002216,
                           export_sawn_share=3.4923806519691904e-06,
                           export_firewood_share=0.023816221976800743,
                           loss_share=0.002934663630742298,
                           shrinkage_share=2.5165647352657604e-05,
                           stock_change_share=8.841339803039103e-07,
                           closure_gap=0.00019989654990918947),
    "all": dict(beta=0.22077128198093976, pulp_share=0.2020999383496498,
                energy_share=0.24371557521332443, export_roundwood_share=0.28985764796225827,
                export_sawn_share=0.0023114072633816664,
                export_firewood_share=0.005058421909257613, loss_share=0.01860609057712852,
                shrinkage_share=0.016655704482223643, stock_change_share=0.0005851577784697422,
                closure_gap=0.00033877448336658744),
}
#: beta_literal 2015 as W2 published it (4 decimals, w2_beta_output.txt).
W2_LITERAL_2015 = {"spruce": 0.3211, "pine": 0.2629, "non-coniferous": 0.0004, "all": 0.2593}


def test_golden_2015_every_column(frozen_beta, frozen_flows):
    got = frozen_beta[frozen_beta["year"] == 2015].set_index("species")
    for sp, buckets in W2_2015.items():
        for col, ref in buckets.items():
            assert got.loc[sp, col] == pytest.approx(ref, abs=1e-12), (sp, col)
        assert got.loc[sp, "hwp_ipcc_share"] == pytest.approx(
            buckets["beta"] + buckets["pulp_share"] + buckets["export_sawn_share"], abs=1e-12)
        assert got.loc[sp, "beta_literal"] == pytest.approx(W2_LITERAL_2015[sp], abs=5e-5)

    # bark_ratio / residue_ratio: by-product volume over pool harvest volume,
    # recomputed here straight from the frozen flow table.
    flows = frozen_flows
    hw = flows[(flows["year"] == 2015) & (flows["source"] == HW)].groupby("target")["volume"].sum()
    harvest = hw[["Spruce", "Pine", "Non-coniferous"]].sum()
    for col, node in (("bark_ratio", "Bark"), ("residue_ratio", "Residues from harvest")):
        assert (got[col] - hw[node] / harvest).abs().max() < 1e-12, col


def test_export_round_trips(tmp_path):
    path = export_beta_series(tmp_path / "beta.csv")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# ")
    assert "\r" not in text
    header = [ln for ln in text.splitlines() if ln.startswith("#")]
    joined = "\n".join(header)
    for needed in ("kg C", "m3", "Denominator", "Carbon basis", "Method",
                   "PROXY", "Source vintage", "LITERATURE_BETA = 0.55"):
        assert needed in joined, needed

    df = pd.read_csv(path, comment="#")
    assert tuple(df.columns[: len(ROADMAP_COLUMNS)]) == ROADMAP_COLUMNS
    assert tuple(df.columns) == EXPORT_COLUMNS
    assert df["ci_low"].isna().all() and df["ci_high"].isna().all()
    assert (df["ci_method"] == "none (Phase G pending)").all()
    assert sorted(df["year"].unique()) == list(range(C.BETA_MIN_YEAR, C.BETA_MAX_YEAR + 1))
    assert set(df["species"]) == {"spruce", "pine", "non-coniferous", "all"}

    # Deterministic: a second export is byte-identical.
    again = export_beta_series(tmp_path / "beta2.csv")
    assert again.read_bytes() == path.read_bytes()


def test_committed_beta_series_matches_fresh_export(tmp_path):
    """Regenerating the dataset without regenerating beta_series.csv fails here."""
    committed = Path(config.BETA_SERIES_CSV)
    assert committed.exists(), "data/processed/beta_series.csv is missing; run export_beta_series()"
    fresh = export_beta_series(tmp_path / "fresh.csv")

    def header(p):
        return [ln for ln in p.read_text(encoding="utf-8").replace("\r\n", "\n").splitlines()
                if ln.startswith("#")]

    assert header(committed) == header(fresh)
    pdt.assert_frame_equal(
        pd.read_csv(committed, comment="#"), pd.read_csv(fresh, comment="#"),
        check_exact=False, atol=1e-7, rtol=0,
    )


# ---------------------------------------------------------------------------
# Must raise
# ---------------------------------------------------------------------------
def test_unknown_sink_node_raises():
    with pytest.raises(ValueError, match="Unknown sink node"):
        classify_sink("Mystery sink")
    with pytest.raises(ValueError, match="Mystery sink"):
        absorption_fractions(_frame(CHAIN + [("Sawmills", "Mystery sink", 5)]))


def test_year_over_closure_tolerance_raises_naming_year_and_node(frozen_flows):
    # 2024 at the frozen vintage: Treindustrien ends in 2023, so Sawmills has no outputs.
    with pytest.raises(ValueError, match=r"year 2024.*'Sawmills'"):
        beta_series(frozen_flows, min_year=2024, max_year=2024)


def test_zero_harvest_for_a_pool_raises():
    no_pine = _frame(CHAIN + [
        (HW, "Non-coniferous", 10), ("Non-coniferous", "Firewood", 10),
        ("Firewood", "Energy", 10),
    ])
    with pytest.raises(ValueError, match="zero harvest for pool 'Pine'"):
        beta_series(no_pine, min_year=YEAR, max_year=YEAR)


def test_negative_volume_raises():
    df = _frame(CHAIN + OTHER_POOLS)
    df.loc[1, "volume"] = -1.0
    with pytest.raises(ValueError, match="negative volume"):
        absorption_fractions(df)
    with pytest.raises(ValueError, match="negative volume"):
        beta_series(df, min_year=YEAR, max_year=YEAR)


def test_closed_cycle_without_exit_raises():
    edges = [
        (HW, "Spruce", 100), ("Spruce", "Pulpmills", 100), ("Pulpmills", ST, 100),
        ("Sawmills", "Planingmills", 50), ("Planingmills", "Sawmills", 50),
    ]
    with pytest.raises(ValueError, match="singular"):
        absorption_fractions(_frame(edges))


def test_nan_volume_raises():
    df = _frame([(HW, "Spruce", 100), ("Spruce", "Sawmills", 100),
                 ("Sawmills", WPP, 100), (WPP, LT, 100)])
    df.loc[2, "volume"] = np.nan
    with pytest.raises(ValueError, match="Non-finite"):
        absorption_fractions(df)


@pytest.mark.parametrize("col", ["source", "target", "year"])
def test_null_key_column_raises(col):
    df = _frame(CHAIN + OTHER_POOLS)
    df[col] = df[col].astype(object)
    df.loc[3, col] = None
    with pytest.raises(ValueError, match=f"with no {col}"):
        beta_series(df, min_year=YEAR, max_year=YEAR)
    with pytest.raises(ValueError, match=f"with no {col}"):
        absorption_fractions(df)


def test_sink_with_outflow_raises():
    with pytest.raises(ValueError, match="has an outflow"):
        absorption_fractions(_frame([(HW, "Spruce", 100), ("Spruce", "Firewood", 100),
                                     ("Firewood", "Energy", 100), ("Energy", "Shrinkage", 5)]))


def test_bark_is_not_a_fate():
    with pytest.raises(ValueError):
        absorption_fractions(_frame([(HW, "Spruce", 100), ("Spruce", "Bark", 10),
                                     ("Spruce", "Firewood", 90), ("Firewood", "Energy", 90)]))


def test_harvest_edge_bypassing_the_pools_raises():
    for target in (LT, "Firewood"):
        edges = CHAIN + [(HW, target, 1e6)]
        if target == "Firewood":
            edges.append(("Firewood", "Energy", 1e6))
        with pytest.raises(ValueError, match="bypasses the species pools"):
            absorption_fractions(_frame(edges))


def test_output_excess_over_tolerance_raises():
    # Duplicated last-hop rows: Sawmills emits 150 from an input of 100.
    dup = CHAIN + [("Sawmills", WPP, 50), (WPP, LT, 50)]
    with pytest.raises(ValueError, match=r"year 2015.*'Sawmills' emits 33\.3%"):
        absorption_fractions(_frame(dup), excess_out_tol=C.BETA_EXCESS_OUT_TOL)
    with pytest.raises(ValueError, match="BETA_EXCESS_OUT_TOL"):
        beta_series(_frame(dup + OTHER_POOLS), min_year=YEAR, max_year=YEAR)
    # Under the tolerance it passes (5 % excess).
    ok = CHAIN + [("Sawmills", WPP, 5), (WPP, LT, 5)]
    absorption_fractions(_frame(ok), excess_out_tol=C.BETA_EXCESS_OUT_TOL)


def test_frozen_data_passes_the_excess_gate_with_margin(frozen_flows):
    # Largest output excess 2012-2023 at the frozen vintage: 0.1141 (Impregnation 2021).
    beta_series(frozen_flows, excess_out_tol=0.12)
    with pytest.raises(ValueError, match=r"year 2021.*'Impregnation'"):
        beta_series(frozen_flows, min_year=2021, max_year=2021, excess_out_tol=0.11)


def test_no_long_term_use_inflow_raises():
    df = _frame([
        (HW, "Spruce", 100), ("Spruce", "Firewood", 100),
        (HW, "Pine", 10), ("Pine", "Firewood", 10),
        (HW, "Non-coniferous", 10), ("Non-coniferous", "Firewood", 10),
        ("Firewood", "Energy", 120),
    ])
    with pytest.raises(ValueError, match="no Long-term USE inflow"):
        beta_series(df, min_year=YEAR, max_year=YEAR)


def test_literal_beta_raises_on_zero_harvest():
    df = _frame([(WPP, LT, 10), ("Spruce", "Firewood", 5)])
    with pytest.raises(ValueError, match="zero harvest for pool 'Spruce'"):
        literal_beta(df)


def test_gates_cannot_be_disabled(tmp_path):
    with pytest.raises(ValueError, match="cannot disable"):
        export_beta_series(tmp_path / "b.csv", closure_tol=None, min_year=2024, max_year=2024)
    with pytest.raises(ValueError, match="cannot disable"):
        export_beta_series(tmp_path / "b.csv", excess_out_tol=None)
    with pytest.raises(ValueError, match="cannot be disabled"):
        beta_series(closure_tol=None)
    assert not (tmp_path / "b.csv").exists()


def test_header_prints_the_tolerances_used(tmp_path, frozen_flows):
    # Frozen input: 0.015 / 0.13 sit just above that vintage's maxima (0.0144 / 0.1141).
    path = export_beta_series(tmp_path / "b.csv", df=frozen_flows,
                              closure_tol=0.015, excess_out_tol=0.13)
    header = "\n".join(ln for ln in path.read_text(encoding="utf-8").splitlines()
                       if ln.startswith("#"))
    assert "closure_gap exceeds 0.015" in header
    assert "over 0.13 of its outflow" in header


def test_missing_year_raises():
    df = pd.concat([_frame(CHAIN + OTHER_POOLS, year=2015),
                    _frame(CHAIN + OTHER_POOLS, year=2017)])
    with pytest.raises(ValueError, match=r"No flows for year\(s\) \[2016\]"):
        beta_series(df, min_year=2015, max_year=2017)


def test_empty_window_raises():
    with pytest.raises(ValueError, match="Empty window"):
        beta_series(min_year=2013, max_year=2012)


def test_no_pool_raises():
    with pytest.raises(ValueError, match="no species pool"):
        absorption_fractions(_frame([("Import planed wood", WPP, 10), (WPP, LT, 10)]))
