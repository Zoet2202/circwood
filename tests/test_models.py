"""The separate models under models/ (ROADMAP Phase L): rotation economics and the stock projection.

They exchange files with the pipeline and are not part of the package; these
tests pin the economics they implement (the thesis's qualitative results on the
calibrated curve) and fence the committed result files against a fresh run.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from models.forest_stock import projection
from models.rotation import rotation


@pytest.fixture(scope="module")
def params():
    return rotation.load_parameters()


@pytest.fixture(scope="module")
def curve(params):
    return rotation.GrowthCurve.logistic(params)


def _prices(params, **over):
    p_s, p_e, _ = rotation.reference_prices(params)
    alpha = params["wood_density_kg_per_m3"] * params["carbon_fraction"] * rotation.CO2_PER_C / 1000.0
    base = dict(i=params["discount_rate"], p_s=p_s, p_k=rotation.carbon_price_nok(params), p_e=p_e,
                beta=0.55, alpha=alpha, sigma=params["dead_biomass_decay_per_year"],
                energy_kwh_per_m3=params["energy_kwh_per_m3"], k=params["planting_cost_nok_per_ha"])
    base.update(over)
    return rotation.Prices(**base)


def test_default_curve_is_calibrated_to_the_thesis(curve, params):
    """Faustmann optimum near 90 years at 3 %, living volume plateauing near 135 years."""
    curves = rotation.npv_curves(curve, _prices(params))
    t_f, _ = rotation.optimum(curves, "faustmann")
    assert 85 <= t_f <= 95
    assert curve.volume[135] / curve.volume.max() > 0.97
    assert curve.volume[0] == 0 and np.all(np.diff(curve.volume) >= 0)
    assert curve.dead[135] > 0


def _faustmann_foc_gap(curve, p, t):
    """S'/S - i (1 - k / (p_S S)) / (1 - e^{-iT}): the infinite-rotation first-order condition.

    Thesis eq. 14 is the k = 0 case; a planting cost k lowers the right-hand side
    and so lengthens the optimum by about a year on this curve.
    """
    s, ds = curve.volume[t], curve.growth[t]
    return ds / s - p.i * (1 - p.k / (p.p_s * s)) / (1 - np.exp(-p.i * t))


def test_faustmann_criterion_holds_at_the_optimum(curve, params):
    """The first-order condition changes sign within one grid year of the optimum (thesis eq. 14).

    The optimum is an integer age and the growth rate a central difference, so
    the continuous crossing lies within a year of it on either side: the gap is
    positive one year before and negative one year after.
    """
    p = _prices(params)
    t_f, _ = rotation.optimum(rotation.npv_curves(curve, p), "faustmann")
    assert _faustmann_foc_gap(curve, p, t_f - 1) > 0 > _faustmann_foc_gap(curve, p, t_f + 1)
    # With no planting cost the condition is eq. 14 as written, S'/S = i / (1 - e^{-iT}),
    # and the timber price drops out of it (eq. 7.5)...
    free = _prices(params, k=0.0)
    t_free, _ = rotation.optimum(rotation.npv_curves(curve, free), "faustmann")
    assert _faustmann_foc_gap(curve, free, t_free - 1) > 0 > _faustmann_foc_gap(curve, free, t_free + 1)
    t_rich, _ = rotation.optimum(rotation.npv_curves(curve, _prices(params, k=0.0, p_s=p.p_s * 2)), "faustmann")
    assert t_rich == t_free
    # ...with a planting cost a higher price can only pull the optimum back towards that age.
    t_rich_k, _ = rotation.optimum(rotation.npv_curves(curve, _prices(params, p_s=p.p_s * 2)), "faustmann")
    assert t_free <= t_rich_k <= t_f
    # A higher interest rate shortens it.
    t_steep, _ = rotation.optimum(rotation.npv_curves(curve, _prices(params, i=0.05)), "faustmann")
    assert t_steep < t_f


def test_carbon_lengthens_the_rotation_and_beta_shortens_the_carbon_effect(curve, params):
    base = rotation.npv_curves(curve, _prices(params, beta=0.55))
    t_f, _ = rotation.optimum(base, "faustmann")
    t_vk, _ = rotation.optimum(base, "van_kooten")
    t_crc, _ = rotation.optimum(base, "crc_owner")
    assert t_vk > t_f and t_crc > t_f                       # the thesis's headline
    # Without a carbon price van Kooten is Faustmann.
    free = rotation.npv_curves(curve, _prices(params, p_k=0.0))
    assert rotation.optimum(free, "van_kooten")[0] == rotation.optimum(free, "faustmann")[0]
    # A lower beta (more of the stem released at harvest) makes harvest costlier, so later.
    low = rotation.npv_curves(curve, _prices(params, beta=0.20))
    assert rotation.optimum(low, "van_kooten")[0] >= t_vk
    # Higher timber prices pull van Kooten back towards Faustmann (thesis table 1).
    rich = rotation.npv_curves(curve, _prices(params, p_s=_prices(params).p_s * 3))
    assert rotation.optimum(rich, "van_kooten")[0] <= t_vk


def test_crc_reward_starts_at_the_faustmann_age(curve, params):
    p = _prices(params)
    curves = rotation.npv_curves(curve, p)
    t_f, _ = rotation.optimum(curves, "faustmann")
    # Below T_F the owner has no carbon reward: CRC equals Faustmann minus the decay charge.
    before = curves[curves["age_years"] <= t_f]
    assert (before["crc_owner"] <= before["faustmann_single"] + 1e-9).all()
    at = curves.set_index("age_years")
    assert at.loc[t_f + 1, "crc_owner"] - at.loc[t_f + 1, "faustmann_single"] > \
        at.loc[t_f, "crc_owner"] - at.loc[t_f, "faustmann_single"]


def test_social_energy_term_pulls_back_towards_faustmann(curve, params):
    base = rotation.npv_curves(curve, _prices(params))
    cheap = rotation.npv_curves(curve, _prices(params, p_e=0.0))
    assert rotation.optimum(base, "social")[0] <= rotation.optimum(cheap, "social")[0]
    # The thesis's finding (section 4.5): the social optimum lies back towards Faustmann
    # from the owner's carbon-rewarded age, not beyond it.
    t_f = rotation.optimum(base, "faustmann")[0]
    assert t_f <= rotation.optimum(base, "social")[0] < rotation.optimum(base, "crc_owner")[0]


def test_social_model_values_uptake_and_release_on_one_price_path(curve, params):
    """Eq. 21 on one carbon-price path.

    With the price growing at i the discounting cancels, so the carbon part of
    the social NPV is p_K * alpha * [int(S' - sigma D) dt - (1 - beta) S], undiscounted,
    at every age; with a constant price the same terms carry e^{-it}. Mixing the
    two paths (uptake growing, release constant) is the error that put the
    social optimum at 125 years.
    """
    t = curve.age
    disc = np.exp(-params["discount_rate"] * t)
    for grows in (True, False):
        p = _prices(params, carbon_price_grows_with_i=grows)
        curves = rotation.npv_curves(curve, p)
        timber = p.p_s * curve.volume * disc - p.k
        energy = p.p_e * p.energy_kwh_per_m3 * curve.volume * disc * (1 - p.beta)
        carbon_part = curves["social"].to_numpy() - timber - energy
        weight = np.ones_like(t) if grows else disc
        uptake = rotation._cumulative_integral(p.p_k * p.alpha * (curve.growth - p.sigma * curve.dead) * weight, t)
        release = p.p_k * p.alpha * (1 - p.beta) * curve.volume * weight
        assert np.allclose(carbon_part, uptake - release, rtol=1e-9, atol=1e-6)


def test_growth_curve_round_trips_through_csv(tmp_path, curve):
    path = curve.to_csv(tmp_path / "gc.csv", "test")
    again = rotation.GrowthCurve.from_csv(path)
    assert np.allclose(again.volume, curve.volume, atol=1e-3)
    assert np.allclose(again.dead, curve.dead, atol=1e-3)
    bad = tmp_path / "bad.csv"
    bad.write_text("age_years,volume_m3_ha\n0,0\n5,10\n", encoding="utf-8")
    with pytest.raises(ValueError, match="annual grid"):
        rotation.GrowthCurve.from_csv(bad)


def test_sitree_export_is_well_formed_and_labelled():
    """The committed SiTree run: annual grid to 250 years, non-negative pools, provenance and the realism check."""
    path = rotation.SITREE_CURVE_CSV
    assert path.exists(), "models/sitree/growth_curve_sitree.csv is missing; run Rscript models/sitree/simulate_stand.R"
    header = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.startswith("#")]
    assert any("SiTree" in ln and "stand.west" in ln for ln in header)
    assert any("Realism check" in ln and "basal area" in ln for ln in header)
    raw = pd.read_csv(path, comment="#")
    assert raw["age_years"].tolist() == list(range(0, 251))
    assert {"volume_m3_ha", "dead_volume_m3_ha", "volume_sd_m3_ha", "stems_per_ha", "basal_area_m2_ha"} <= set(raw.columns)
    assert (raw["volume_m3_ha"] >= 0).all() and (raw["dead_volume_m3_ha"] >= 0).all()
    assert raw["dead_volume_m3_ha"].iloc[0] == 0 and raw["dead_volume_m3_ha"].is_monotonic_increasing
    # The example stand starts mid-life (dense, already stocked) and the growth
    # functions are extrapolated far past any real stand's basal area; both facts
    # are what keeps this curve a comparison and not the primary.
    assert raw["volume_m3_ha"].iloc[0] > 100 and raw["basal_area_m2_ha"].max() > 150
    sit = rotation.GrowthCurve.from_csv(path, age_offset=40)
    assert sit.age[0] == 40 and len(sit.age) == 251 and np.allclose(np.diff(sit.age), 1.0)


SSB_STAND = "ssb-anchored spruce stand SI 14"


def test_rotation_results_compare_every_growth_curve():
    """Base and price scenarios run on the primary curve; every other curve appears once per model and beta."""
    committed = pd.read_csv(rotation.RESULTS_CSV, comment="#")
    assert "growth_curve" in committed.columns
    params = rotation.load_parameters()
    curves, primary = rotation.load_curves(params)
    assert {rotation.LOGISTIC_CURVE, rotation.SITREE_CURVE, SSB_STAND,
            "ssb-anchored spruce stand SI 11", "ssb-anchored spruce stand SI 17"} <= set(curves)
    assert primary == str(params["primary_growth_curve"]) == SSB_STAND      # decision 27
    base = committed[committed["scenario"] == "base"]
    assert (base["growth_curve"] == primary).all()
    for name in curves:
        if name == primary:
            continue
        other = committed[committed["scenario"] == f"growth curve: {name}"]
        assert (other["growth_curve"] == name).all(), name
        assert len(other) == len(base) == len(rotation.MODELS) * base["beta_source"].nunique(), name
    # On every curve carbon lengthens the rotation and the social optimum sits between Faustmann and CRC.
    for name in curves:
        rows = base if name == primary else committed[committed["scenario"] == f"growth curve: {name}"]
        t = rows[rows["beta_source"] == "empirical spruce"].set_index("model")["t_opt_years"]
        assert t["faustmann"] < t["van_kooten"] < t["crc_owner"], name
        assert t["faustmann"] <= t["social"] < t["crc_owner"], name


def test_committed_primary_growth_curve_equals_the_parameters(params):
    """models/rotation/growth_curve.csv is the primary curve as run, so it cannot drift from parameters.csv."""
    curves, primary = rotation.load_curves(params)
    committed = rotation.GrowthCurve.from_csv(rotation.GROWTH_CURVE_CSV)
    fresh = curves[primary]
    assert np.allclose(committed.age, fresh.age)
    assert np.allclose(committed.volume, fresh.volume, atol=1e-3)
    assert np.allclose(committed.dead, fresh.dead, atol=1e-3)
    first = rotation.GROWTH_CURVE_CSV.read_text(encoding="utf-8").splitlines()[0]
    assert f"primary growth curve '{primary}'" in first


def test_ssb_anchored_stands_are_exported_and_validated():
    """The three SSB-anchored exports: provenance, true stand ages, realism, and the validation table."""
    for si, start in ((11, 25), (14, 20), (17, 15)):
        path = rotation.SITREE_DIR / f"growth_curve_ssb_si{si}.csv"
        assert path.exists(), path
        header = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.startswith("#")]
        assert rotation.curve_name_from_header(path) == f"ssb-anchored spruce stand SI {si}"
        assert any("Validation against SSB" in ln for ln in header)
        assert any("Realism check" in ln for ln in header)
        raw = pd.read_csv(path, comment="#")
        assert raw["age_years"].tolist() == list(range(start, 251))
        assert (raw["volume_m3_ha"] >= 0).all() and raw["dead_volume_m3_ha"].is_monotonic_increasing
        assert raw["basal_area_m2_ha"].max() < 100                      # a dense unthinned stand, not the example stand's 280
        assert 250 < raw.loc[raw["age_years"] == 100, "volume_m3_ha"].iloc[0] < 700
    val = pd.read_csv(rotation.SITREE_DIR / "ssb_validation.csv", comment="#")
    assert val["si"].tolist() == [11, 14, 17]
    assert (val["implied_volume_m3_ha"] > 0).all() and (val["ssb_volume_high"] > val["ssb_volume_low"]).all()
    # The implied gross increment lands in or near SSB's band (an unmanaged stand against a managed landscape).
    assert ((val["implied_gross_increment_m3_ha_yr"] > 0.5 * val["ssb_increment_low"])
            & (val["implied_gross_increment_m3_ha_yr"] < 1.5 * val["ssb_increment_high"])).all()


def test_an_optimum_on_the_grid_edge_is_not_interior(params):
    """A curve that starts past its best age puts the Faustmann optimum on the first searched age."""
    sit = rotation.GrowthCurve.from_csv(rotation.SITREE_CURVE_CSV)          # ages from the simulation start
    curves = rotation.npv_curves(sit, _prices(params))
    t_f, _ = rotation.optimum(curves, "faustmann")
    assert t_f == rotation.MIN_ROTATION_AGE                               # pinned to the edge
    assert rotation.curve_name_from_header(rotation.SITREE_CURVE_CSV) == rotation.SITREE_CURVE
    committed = pd.read_csv(rotation.RESULTS_CSV, comment="#")
    edge = committed[(committed["t_opt_years"] <= rotation.MIN_ROTATION_AGE) |
                     (committed["t_opt_years"] >= params["max_age_years"])]
    assert (edge["interior_optimum"] == 0).all()


def test_committed_rotation_results_match_a_fresh_run():
    assert rotation.RESULTS_CSV.exists(), "data/processed/rotation_results.csv is missing; run the model"
    committed = pd.read_csv(rotation.RESULTS_CSV, comment="#")
    fresh = rotation.run(write=False)
    # The file rounds NPV to 0.1 NOK and prices to six significant figures.
    pd.testing.assert_frame_equal(committed, fresh, check_exact=False, rtol=1e-5, atol=0.06)
    assert {"interior_optimum", "npv_positive"} <= set(committed.columns)
    base = committed[committed["scenario"] == "base"].set_index(["beta_source", "model"])["t_opt_years"]
    assert base[("empirical spruce", "van_kooten")] > base[("empirical spruce", "faustmann")]
    assert base[("empirical spruce", "van_kooten")] > base[("thesis 0.55", "van_kooten")]
    assert set(committed["beta_source"]) >= {"thesis 0.55", "empirical spruce"}


# ---------------------------------------------------------------------------
# Stock projection
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def forest_state():
    forest = pd.read_csv(projection.FOREST_CSV, dtype={"table": str, "interval": str, "region": str})
    return projection.today(forest)


def test_starting_state_reproduces_the_inventory(forest_state):
    s = forest_state
    assert 70_000 < s["area_km2"] < 95_000
    assert sum(s["areas_km2"].values()) == pytest.approx(s["area_km2"])
    assert 8e8 < s["standing_volume_m3"] < 1e9
    assert 2e7 < s["increment_m3"] < 2.6e7


def test_projection_conserves_area_and_calibrates_to_today(forest_state):
    params = projection.load_parameters()
    out = projection.project(forest_state, params, "status quo", 12.4e6, None)
    area_cols = [f"area_{c}_km2" for c in projection.CLASSES]
    assert np.allclose(out[area_cols].sum(axis=1), forest_state["area_km2"], rtol=1e-9)
    first = out.iloc[0]
    assert first["increment_m3"] == pytest.approx(forest_state["increment_m3"])
    assert first["standing_volume_m3"] == pytest.approx(forest_state["standing_volume_m3"])
    assert first["harvest_m3"] == pytest.approx(12.4e6)
    assert (out["standing_volume_m3"] >= 0).all()


def test_deferring_harvest_raises_the_stock_and_lowers_near_term_harvest(forest_state):
    params = projection.load_parameters()
    f = projection.project(forest_state, params, "faustmann", None, 90).set_index("year")
    c = projection.project(forest_state, params, "crc", None, 120).set_index("year")
    assert c.loc[2050, "standing_volume_m3"] > f.loc[2050, "standing_volume_m3"]
    assert c.loc[2026, "harvest_m3"] < f.loc[2026, "harvest_m3"]


def test_committed_projection_matches_a_fresh_run():
    assert projection.RESULTS_CSV.exists(), "data/processed/forest_stock_projection.csv is missing; run the model"
    committed = pd.read_csv(projection.RESULTS_CSV, comment="#")
    fresh = projection.run(write=False)
    # The file rounds every quantity to one decimal.
    pd.testing.assert_frame_equal(committed, fresh, check_exact=False, rtol=1e-6, atol=0.06)
    assert set(committed["scenario"]) == set(projection.SCENARIOS)
