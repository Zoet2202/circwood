"""Phase G — one-at-a-time sensitivity of β and the bounds it feeds into beta_series.csv."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from circwood import config
from circwood import constants as C
from circwood.analysis.hwp import beta_series
from circwood.analysis.sensitivity import (
    DENSITY_PARAMETER,
    KNOWN_PARAMETERS,
    SENSITIVITY_COLUMNS,
    beta_bounds,
    export_sensitivity,
    load_bounds,
    load_ranges,
    rebuild_national,
    resplit_firewood,
    sensitivity_table,
    summary_table,
)
from circwood.ingest import treindustrien as tre
from circwood.transform.assemble import primary_rows
from circwood.transform.mills import MillCoefficients
from circwood.validate import assert_window_balanced


def test_ranges_file_is_valid_and_in_step_with_constants():
    ranges = load_ranges()
    assert set(ranges["parameter"]) <= set(KNOWN_PARAMETERS)
    assert (ranges["low"] <= ranges["central"]).all() and (ranges["central"] <= ranges["high"]).all()
    row = ranges.set_index("parameter")
    assert row.loc["cellulose", "central"] == C.CELLULOSE
    assert row.loc["wpp_long_term", "central"] == C.WPP_LONG_TERM


def test_ranges_loader_refuses_drift_and_unknowns(tmp_path):
    bad = tmp_path / "r.csv"
    bad.write_text("parameter,central,low,high,unit,note\ncellulose,0.30,0.22,0.35,share,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="differs from constants.py"):
        load_ranges(bad)
    bad.write_text("parameter,central,low,high,unit,note\nmagic,1,0,2,share,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not varied"):
        load_ranges(bad)
    bad.write_text("parameter,central,low,high,unit,note\ncellulose,0.26,0.30,0.22,share,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="low <= central <= high"):
        load_ranges(bad)


def test_mill_scenarios_keep_the_pulp_split_at_100():
    from circwood.analysis.sensitivity import _mill_scenario

    m = _mill_scenario("pulp_short_term", 80.0)
    assert m.pulp_short_term == 80.0
    assert m.pulp_short_term + m.pulp_energy + m.pulp_loss == pytest.approx(100.0)
    assert m.pulp_energy / m.pulp_loss == pytest.approx(C.PULP_ENERGY / C.PULP_LOSS)
    e = _mill_scenario("pulp_energy", 12.0)
    assert e.pulp_short_term + e.pulp_energy + e.pulp_loss == pytest.approx(100.0)
    with pytest.raises(ValueError):
        MillCoefficients(pulp_short_term=90, pulp_energy=8, pulp_loss=6)


@pytest.fixture(scope="module")
def primary() -> pd.DataFrame:
    return primary_rows()


def test_resplit_firewood_moves_volume_between_pools_only(primary):
    out = resplit_firewood(primary, 70.0)
    for year in (2015, 2020):
        g_old = primary[primary["year"] == year]
        g_new = out[out["year"] == year]
        fw_old = g_old[(g_old["target"] == "Firewood") & g_old["source"].isin(["Spruce", "Pine"])]["volume"].sum()
        fw_new = g_new[(g_new["target"] == "Firewood") & g_new["source"].isin(["Spruce", "Pine"])]["volume"].sum()
        assert fw_new == pytest.approx(fw_old)                    # conifer firewood total unchanged
        spruce_fw = g_new[(g_new["source"] == "Spruce") & (g_new["target"] == "Firewood")]["volume"].sum()
        assert spruce_fw / fw_new == pytest.approx(0.70)
        assert g_new["volume"].sum() == pytest.approx(g_old["volume"].sum())  # nothing created
    # The pools' balance is untouched by the move (harvest edges moved with the firewood).
    rebuilt = rebuild_national(out, tre.load_treindustrien())
    base = rebuild_national(primary, tre.load_treindustrien())
    assert_window_balanced(rebuilt)
    def imbalance(frame, pool):
        w = frame[(frame.year >= C.MIN_YEAR) & (frame.year <= C.MAX_YEAR)]
        return w[w.source == pool].volume.sum() - w[w.target == pool].volume.sum()

    for pool in ("Spruce", "Pine"):
        assert imbalance(rebuilt, pool) == pytest.approx(imbalance(base, pool), abs=1.0)


def test_rebuild_national_default_matches_the_committed_flows(primary):
    from circwood.pipeline import load_cached_sankey
    from circwood.transform.dimensions import collapse_national

    rebuilt = rebuild_national(primary, tre.load_treindustrien())
    key = ["year", "source", "target"]
    a = collapse_national(load_cached_sankey()).set_index(key)["volume"].sort_index()
    b = rebuilt.set_index(key)["volume"].sort_index()
    assert a.index.equals(b.index)
    assert np.allclose(a.to_numpy(), b.to_numpy(), rtol=1e-9)


def test_sensitivity_table_central_equals_exported_beta_and_scenarios_move_it(primary):
    ranges = load_ranges()
    small = ranges[ranges["parameter"].isin(["wpp_long_term", "cellulose"])]
    sens = sensitivity_table(ranges=small, min_year=2015, max_year=2016)
    assert list(sens.columns) == list(SENSITIVITY_COLUMNS)
    assert set(sens["parameter"]) == {"central", "wpp_long_term", "cellulose", DENSITY_PARAMETER}
    central = sens[sens["parameter"] == "central"].set_index(["year", "species"])["beta"]
    ref = beta_series(min_year=2015, max_year=2016).set_index(["year", "species"])["beta"]
    pd.testing.assert_series_equal(central.sort_index(), ref.sort_index(), check_names=False,
                                   check_exact=False, atol=1e-12, rtol=0)
    wpp = sens[(sens["parameter"] == "wpp_long_term") & (sens["species"] == "all")].set_index("bound")
    assert (wpp.loc["low", "delta_beta"] < 0).all() and (wpp.loc["high", "delta_beta"] > 0).all()
    # A species' beta does not depend on the densities; only the "all" weighting can.
    dens = sens[(sens["parameter"] == DENSITY_PARAMETER) & (sens["species"] != "all")]
    assert dens["delta_beta"].abs().max() < 1e-12


def test_beta_bounds_is_a_root_sum_square_envelope():
    sens = pd.DataFrame([
        ("central", "central", np.nan, 2015, "all", 0.20, 0.0),
        ("a", "low", 0.1, 2015, "all", 0.17, -0.03),
        ("a", "high", 0.3, 2015, "all", 0.24, 0.04),
        ("b", "low", 1.0, 2015, "all", 0.16, -0.04),
        ("b", "high", 2.0, 2015, "all", 0.19, -0.01),   # both bounds on the low side
    ], columns=SENSITIVITY_COLUMNS)
    b = beta_bounds(sens).iloc[0]
    assert b["ci_low"] == pytest.approx(0.20 - np.sqrt(0.03 ** 2 + 0.04 ** 2))
    assert b["ci_high"] == pytest.approx(0.20 + 0.04)
    assert "RSS envelope" in b["ci_method"] and "statistical confidence interval" in b["ci_method"]
    assert "," not in b["ci_method"]   # the text lands in a CSV cell read by R users too


def test_summary_table_ranks_parameters_by_influence(primary):
    sens = sensitivity_table(ranges=load_ranges()[load_ranges()["parameter"].isin(["wpp_long_term", "sawdust"])],
                             min_year=2015, max_year=2015, include_density=False)
    summary = summary_table(sens, 2015, 2015)
    assert summary["parameter"].tolist()[0] == "wpp_long_term"
    assert (summary["max_abs_delta"].diff().dropna() <= 0).all()


def test_export_round_trips_and_load_bounds_reads_it(tmp_path, monkeypatch):
    path = tmp_path / "sens.csv"
    small = load_ranges()[load_ranges()["parameter"] == "wpp_long_term"]
    export_sensitivity(path, ranges=small, min_year=2015, max_year=2015, include_density=False)
    text = path.read_text(encoding="utf-8")
    assert "\r" not in text and text.splitlines()[0] == ",".join(SENSITIVITY_COLUMNS)
    again = tmp_path / "sens2.csv"
    export_sensitivity(again, ranges=small, min_year=2015, max_year=2015, include_density=False)
    assert again.read_bytes() == path.read_bytes()
    bounds = load_bounds(path)
    assert set(bounds.columns) == {"year", "species", "beta", "ci_low", "ci_high",
                                   "ci_low_linear", "ci_high_linear", "ci_method"}
    # The linear (fully correlated) pair is never narrower than the RSS pair.
    assert (bounds["ci_low_linear"] <= bounds["ci_low"] + 1e-12).all()
    assert (bounds["ci_high_linear"] >= bounds["ci_high"] - 1e-12).all()
    assert (bounds["ci_low"] <= bounds["beta"]).all() and (bounds["beta"] <= bounds["ci_high"]).all()
    assert load_bounds(tmp_path / "missing.csv") is None


def test_committed_sensitivity_matches_a_fresh_run(tmp_path):
    """Regenerating the dataset without re-exporting beta_sensitivity.csv fails here."""
    committed = config.BETA_SENSITIVITY_CSV
    assert committed.exists(), "data/processed/beta_sensitivity.csv is missing; run export_sensitivity()"
    fresh = export_sensitivity(tmp_path / "fresh.csv")
    pd.testing.assert_frame_equal(pd.read_csv(committed), pd.read_csv(fresh),
                                  check_exact=False, atol=1e-7, rtol=0)
