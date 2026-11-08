"""Rotation economics with an empirical β — ROADMAP Phase L.

Re-runs the comparison of Landaas (2023), *En analyse av hvordan Carbon
Removal Certification (CRC) påvirker optimal rotasjonstid i skog* (NTNU), with
the harvested-wood-product fraction β measured by this pipeline instead of the
0.55 the thesis had to assume. Four net-present-value models of one spruce
stand, maximised over the rotation age ``T`` (years):

* **Faustmann** (thesis eq. 5 and 13): ``p_S S(T) e^{-iT} - k``, single
  rotation, and its infinite-rotation form divided by ``1 - e^{-iT}``.
* **van Kooten et al. (1995)** (eq. 17): the infinite-rotation Faustmann value
  plus the discounted value of carbon taken up during growth,
  ``∫₀ᵀ p_K α S'(t) e^{-it} dt``, minus the carbon released at harvest from
  the share not stored in products, ``p_K α (1 - β) S(T) e^{-iT}``.
* **CRC owner** (eq. 19): the owner sells the timber and is rewarded only for
  carbon taken up *after* the Faustmann age ``T_F`` (``G(T) = 1`` for
  ``T > T_F``), and is charged for the carbon the stand's dead biomass gives
  off as it decays (rate ``σ``). The thesis lets the carbon price grow at the
  discount rate, ``p_K(t) = p_K e^{it}``; a parameter switches that off.
* **Social optimum** (eq. 21): timber value, the value of net carbon uptake
  (growth minus decay of dead biomass), the value of the electricity from
  burning the share not stored, ``p_E E S(T) (1 - β)``, minus the carbon that
  burning releases.

``β`` enters exactly where the thesis put it: in ``(1 - β)``, the share of the
harvested stem that is not stored in long-lived products. The empirical value
is the spruce ``beta`` of ``data/processed/beta_series.csv`` averaged over the
paper window, with its ``ci_low``/``ci_high`` envelope; the thesis's 0.55 is
run alongside for comparison.

The growth curve
----------------
The thesis simulated its stand in SiTree (NIBIO) over 250 years without
harvest: living volume rises and then plateaus at about 135 years, where
growth equals mortality, and the Faustmann optimum at 3 % falls at 90 years.
No numbers from that run survive in the thesis, so the default curve here is a
logistic living-volume curve *calibrated to those two facts* (see
``parameters.csv``) with a constant mortality rate feeding a dead-biomass pool
that decays at ``σ``. ``models/sitree/simulate_stand.R`` can replace it with a
real SiTree export in the same CSV shape (``growth_curve.csv``). Everything
downstream reads the CSV, so the economics never depend on which curve
produced it.

Units: NOK per hectare; carbon as t CO₂ (α = density × carbon fraction ×
44/12 per m³); the discount rate per year; ``T`` on an annual grid.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PARAMETERS_CSV = HERE / "parameters.csv"
GROWTH_CURVE_CSV = HERE / "growth_curve.csv"                     # the primary curve, written by run()
SITREE_DIR = ROOT / "models" / "sitree"                           # every growth_curve_*.csv there is a candidate curve
SITREE_CURVE_CSV = SITREE_DIR / "growth_curve_sitree.csv"         # the thesis's example stand (simulate_stand.R)
BETA_SERIES_CSV = ROOT / "data" / "processed" / "beta_series.csv"
PRICES_SSB_CSV = ROOT / "data" / "raw" / "prices_ssb.csv"
PRICES_CONFIG_CSV = ROOT / "config" / "prices.csv"
RESULTS_CSV = ROOT / "data" / "processed" / "rotation_results.csv"

CO2_PER_C = 44.0095 / 12.0107
MODELS = ("faustmann_single", "faustmann", "van_kooten", "crc_owner", "social")
#: A stand younger than this is not a rotation; the search for every optimum starts here.
MIN_ROTATION_AGE = 20
LOGISTIC_CURVE = "calibrated logistic"
SITREE_CURVE = "sitree example stand"
RESULT_COLUMNS = [
    "scenario", "model", "beta_source", "beta", "discount_rate", "timber_price_nok_m3",
    "carbon_price_nok_tco2", "electricity_price_nok_kwh", "carbon_price_grows_with_i",
    "t_opt_years", "npv_nok_ha", "delta_t_vs_faustmann_years", "interior_optimum", "npv_positive",
    "growth_curve",
]


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------
def load_parameters(path: Path = PARAMETERS_CSV) -> dict[str, Any]:
    """parameters.csv as a dict: numbers as floats, anything else (a curve name) as a stripped string."""
    df = pd.read_csv(path)
    out: dict[str, Any] = {}
    for r in df.itertuples():
        try:
            out[str(r.parameter)] = float(r.value)
        except (TypeError, ValueError):
            out[str(r.parameter)] = str(r.value).strip()
    return out


def curve_name_from_header(path: Path) -> str | None:
    """The ``# curve_name: ...`` line a SiTree export carries, or None."""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.startswith("#"):
                break
            body = line[1:].strip()
            if body.lower().startswith("curve_name:"):
                return body.split(":", 1)[1].strip()
    return None


def load_beta(path: Path = BETA_SERIES_CSV, min_year: int = 2012, max_year: int = 2022) -> dict[str, float]:
    """Empirical spruce β (mean over the paper window) with its envelope; empty if the file is absent."""
    if not path.exists():
        return {}
    b = pd.read_csv(path, comment="#")
    s = b[(b["species"] == "spruce") & (b["year"] >= min_year) & (b["year"] <= max_year)]
    if s.empty:
        return {}
    out = {"empirical spruce": float(s["beta"].mean())}
    if s["ci_low"].notna().any():
        out["empirical spruce, low"] = float(s["ci_low"].mean())
        out["empirical spruce, high"] = float(s["ci_high"].mean())
    return out


def reference_prices(params: dict[str, float]) -> tuple[float, float, str]:
    """(timber NOK/m³, electricity NOK/kWh, source note) for the reference year, from SSB if available."""
    year = int(params["price_reference_year"])
    p_s, p_e = params["timber_price_nok_per_m3"], params["electricity_price_nok_per_kwh"]
    note = "parameters.csv fallbacks"
    if PRICES_SSB_CSV.exists():
        prices = pd.read_csv(PRICES_SSB_CSV, dtype={"table": str})
        saw = prices[(prices["table"] == "07413") & (prices["species"] == "spruce")
                     & (prices["assortment"] == "sawtimber") & (prices["year"] == year)]
        el = prices[(prices["table"] == "08360") & (prices["year"] == year)]
        if not saw.empty and not el.empty:
            p_s = float(saw["value"].iloc[0])
            p_e = float(el["value"].iloc[0]) / 100.0
            note = f"SSB 07413 Gran sagtømmer and SSB 08360 Elspot, {year}"
    return p_s, p_e, note


def carbon_price_nok(params: dict[str, float]) -> float:
    eur, rate = params["carbon_price_eur_per_tco2"], params["nok_per_eur"]
    if PRICES_CONFIG_CSV.exists():
        cfg = pd.read_csv(PRICES_CONFIG_CSV).set_index("parameter")["value"]
        eur = float(cfg.get("carbon_price_eur_per_tco2", eur))
        rate = float(cfg.get("nok_per_eur", rate))
    return eur * rate


# ---------------------------------------------------------------------------
# Growth
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GrowthCurve:
    """Annual stand trajectory per hectare: living volume, its derivative, dead volume."""

    age: np.ndarray            # years, 0..max
    volume: np.ndarray         # living volume, m³/ha
    growth: np.ndarray         # dS/dt, m³/ha/yr
    dead: np.ndarray           # dead-biomass pool as volume equivalent, m³/ha

    @classmethod
    def logistic(cls, params: dict[str, float]) -> GrowthCurve:
        """The calibrated default: logistic living volume, constant mortality rate, decaying dead pool."""
        max_age = int(params["max_age_years"])
        a, r, t0 = (params["growth_curve_max_volume_m3_ha"], params["growth_curve_rate_per_year"],
                    params["growth_curve_midpoint_years"])
        m, sigma = params["mortality_rate_per_year"], params["dead_biomass_decay_per_year"]
        age = np.arange(0, max_age + 1, dtype=float)
        volume = a / (1.0 + np.exp(-r * (age - t0)))
        volume -= volume[0]                                  # planted at age 0 with no volume
        growth = np.gradient(volume, age)
        dead = np.zeros_like(volume)
        for t in range(1, len(age)):
            dead[t] = dead[t - 1] * (1 - sigma) + m * volume[t - 1]
        return cls(age, volume, growth, dead)

    @classmethod
    def from_csv(cls, path: Path, age_offset: float = 0.0) -> GrowthCurve:
        """A SiTree (or other) export: ``age_years, volume_m3_ha[, dead_volume_m3_ha]`` on an annual grid.

        ``age_offset`` is added to ``age_years``: the SiTree export counts years
        from the start of the simulation, and the example stand is already some
        40 years old then (``parameters.csv``, ``sitree_stand_age_at_start_years``).
        """
        df = pd.read_csv(path, comment="#").sort_values("age_years")
        age = df["age_years"].to_numpy(dtype=float) + float(age_offset)
        if not np.allclose(np.diff(age), 1.0):
            raise ValueError(f"{path.name}: age_years must be an annual grid.")
        volume = df["volume_m3_ha"].to_numpy(dtype=float)
        dead = (df["dead_volume_m3_ha"].to_numpy(dtype=float) if "dead_volume_m3_ha" in df.columns
                else np.zeros_like(volume))
        return cls(age, volume, np.gradient(volume, age), dead)

    def to_csv(self, path: Path, note: str) -> Path:
        lines = [f"# {note}", "age_years,volume_m3_ha,growth_m3_ha_yr,dead_volume_m3_ha"]
        for t, v, g, d in zip(self.age, self.volume, self.growth, self.dead):
            lines.append(f"{int(t)},{v:.4f},{g:.5f},{d:.4f}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        return path


# ---------------------------------------------------------------------------
# Net present values per hectare, on the annual age grid
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Prices:
    i: float                 # discount rate per year
    p_s: float               # timber, NOK/m³
    p_k: float               # carbon, NOK/t CO₂
    p_e: float               # electricity, NOK/kWh
    beta: float              # HWP share of the harvested stem
    alpha: float             # t CO₂ per m³ of stem volume
    sigma: float             # decay of dead biomass per year
    energy_kwh_per_m3: float
    k: float                 # planting cost, NOK/ha
    carbon_price_grows_with_i: bool = True


def _cumulative_integral(values: np.ndarray, age: np.ndarray) -> np.ndarray:
    """∫₀ᵗ values dt on the grid (trapezoid), one entry per age."""
    out = np.zeros_like(values)
    out[1:] = np.cumsum(0.5 * (values[1:] + values[:-1]) * np.diff(age))
    return out


def npv_curves(curve: GrowthCurve, p: Prices, t_faustmann: int | None = None) -> pd.DataFrame:
    """NPV per hectare of every model at every rotation age (one row per age)."""
    t = curve.age
    disc = np.exp(-p.i * t)
    s, ds, d = curve.volume, curve.growth, curve.dead
    # Carbon price path: constant, or growing at the discount rate (thesis eq. 19).
    # It applies to the CRC owner and social models, uptake and release alike;
    # van Kooten (eqs. 15-17) keeps the constant price of van Kooten (1995).
    p_k_t = p.p_k * (np.exp(p.i * t) if p.carbon_price_grows_with_i else np.ones_like(t))

    timber = p.p_s * s * disc - p.k
    with np.errstate(divide="ignore", invalid="ignore"):
        infinite = np.where(t > 0, 1.0 / (1.0 - disc), np.nan)
    faustmann_single = timber
    faustmann = timber * infinite

    uptake_pv = _cumulative_integral(p.p_k * p.alpha * ds * disc, t)          # eq. 15 (constant p_K)
    release_at_harvest = p.p_k * p.alpha * (1 - p.beta) * s * disc              # eq. 16
    van_kooten = (uptake_pv + timber - release_at_harvest) * infinite            # eq. 17

    t_f = t_faustmann if t_faustmann is not None else int(np.nanargmax(faustmann))
    reward_density = p_k_t * p.alpha * ds * disc                                # carbon taken up, valued
    cum_reward = _cumulative_integral(reward_density, t)
    reward = np.where(t > t_f, cum_reward - cum_reward[t_f], 0.0)                # only beyond T_F
    decay_penalty = _cumulative_integral(p_k_t * p.sigma * p.alpha * d * disc, t)
    crc_owner = timber + reward - decay_penalty                                  # eq. 19

    # Social optimum (eq. 21) on ONE carbon-price path: the uptake integral and
    # the release at harvest are both valued on p_k_t. Valuing the uptake on a
    # growing price and the release on the constant one (as this did until
    # 2026-11-08) pushed the optimum to 125 years; on one path it is 106-107,
    # which is also where the thesis found it (pulled back towards Faustmann).
    net_uptake_pv = _cumulative_integral(p_k_t * p.alpha * (ds - p.sigma * d) * disc, t)
    energy_value = p.p_e * p.energy_kwh_per_m3 * s * disc * (1 - p.beta)
    release_social = p_k_t * p.alpha * (1 - p.beta) * s * disc
    social = timber + net_uptake_pv + energy_value - release_social              # eq. 21

    return pd.DataFrame({
        "age_years": t.astype(int), "faustmann_single": faustmann_single, "faustmann": faustmann,
        "van_kooten": van_kooten, "crc_owner": crc_owner, "social": social,
    })


def optimum(curves: pd.DataFrame, model: str, min_age: int = MIN_ROTATION_AGE) -> tuple[int, float]:
    """(T*, NPV at T*) of one model, searched from ``min_age`` (a stand younger than that is not a rotation)."""
    sub = curves[curves["age_years"] >= min_age]
    idx = sub[model].idxmax()
    return int(curves.loc[idx, "age_years"]), float(curves.loc[idx, model])


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
def load_curves(params: dict[str, Any]) -> tuple[dict[str, GrowthCurve], str]:
    """Every growth curve the model can run on, and the name of the primary one.

    The calibrated logistic curve is always there. Every SiTree export in
    ``models/sitree/growth_curve_*.csv`` joins, named by its ``curve_name``
    header line: the thesis's example stand (its ages shifted by the stand's
    age at the simulation start, ``sitree_stand_age_at_start_years``) and the
    SSB-anchored stands (true stand ages, no shift). ``primary_growth_curve``
    in parameters.csv names the curve that carries the base and price scenarios
    and feeds the stock projection; every other curve is reported under the
    scenario ``growth curve: <name>``. An unknown name falls back to the
    calibrated curve, with a warning.
    """
    curves = {LOGISTIC_CURVE: GrowthCurve.logistic(params)}
    for path in sorted(SITREE_DIR.glob("growth_curve_*.csv")):
        name = curve_name_from_header(path) or path.stem.replace("growth_curve_", "").replace("_", " ")
        offset = float(params.get("sitree_stand_age_at_start_years", 0.0)) if name == SITREE_CURVE else 0.0
        curves[name] = GrowthCurve.from_csv(path, age_offset=offset)
    wanted = str(params.get("primary_growth_curve", LOGISTIC_CURVE)).strip()
    if wanted not in curves:
        print(f"warning: primary_growth_curve '{wanted}' is not available; using '{LOGISTIC_CURVE}'", file=sys.stderr)
        wanted = LOGISTIC_CURVE
    return curves, wanted


def run(write: bool = True) -> pd.DataFrame:
    params = load_parameters()
    curves_by_name, primary_name = load_curves(params)
    curve = curves_by_name[primary_name]
    others = {n: c for n, c in curves_by_name.items() if n != primary_name}
    offset = float(params.get("sitree_stand_age_at_start_years", 0.0))
    curve_note = (
        f"primary curve '{primary_name}' (parameters.csv primary_growth_curve); also run, as scenarios "
        f"'growth curve: <name>': {', '.join(repr(n) for n in others) or 'none'}. SiTree exports are read from "
        f"models/sitree/growth_curve_*.csv by their curve_name header"
        + (f"; the example stand's ages are shifted by {offset:g} years, its age at the simulation start"
           if SITREE_CURVE in curves_by_name else "")
    )
    p_s, p_e, price_note = reference_prices(params)
    p_k = carbon_price_nok(params)
    alpha = params["wood_density_kg_per_m3"] * params["carbon_fraction"] * CO2_PER_C / 1000.0
    base = dict(
        i=params["discount_rate"], p_s=p_s, p_k=p_k, p_e=p_e, alpha=alpha,
        sigma=params["dead_biomass_decay_per_year"], energy_kwh_per_m3=params["energy_kwh_per_m3"],
        k=params["planting_cost_nok_per_ha"],
        carbon_price_grows_with_i=bool(int(params["carbon_price_grows_with_i"])),
    )
    betas = {"thesis 0.55": params["literature_beta"], **load_beta()}
    if "empirical spruce" not in betas:
        print("warning: beta_series.csv not found; only the thesis beta is run", file=sys.stderr)

    rows = []

    def add(scenario: str, beta_source: str, beta: float, *, on: GrowthCurve = curve,
            curve_name: str = primary_name, **overrides):
        p = Prices(**{**base, **overrides, "beta": beta})
        curves = npv_curves(on, p)
        t_f, _ = optimum(curves, "faustmann")
        max_age = int(on.age.max())
        # An optimum on either edge of the searched grid is not an optimum: the
        # upper edge says "never harvest within the horizon", the lower edge
        # says the curve was already past its best age when the search began.
        min_age = max(MIN_ROTATION_AGE, int(on.age.min()))
        for model in MODELS:
            t_opt, npv = optimum(curves, model)
            interior = min_age < t_opt < max_age
            if model == "crc_owner" and t_f >= max_age:
                # No Faustmann age inside the horizon: the CRC reward window is
                # empty and the "optimum" is only the decay charge's argmax.
                interior = False
            rows.append({
                "scenario": scenario, "model": model, "beta_source": beta_source, "beta": beta,
                "discount_rate": p.i, "timber_price_nok_m3": p.p_s, "carbon_price_nok_tco2": p.p_k,
                "electricity_price_nok_kwh": p.p_e,
                "carbon_price_grows_with_i": int(p.carbon_price_grows_with_i),
                "t_opt_years": t_opt, "npv_nok_ha": npv, "delta_t_vs_faustmann_years": t_opt - t_f,
                # NPV still rising at the end of the curve: "never harvest" within the
                # horizon (the thesis's question "burde skogen bare stå?"), not an optimum.
                "interior_optimum": int(interior),
                # A negative NPV at the optimum means planting does not pay at these
                # prices: the age is then the argmax of a loss, not a rotation to recommend.
                "npv_positive": int(npv > 0),
                "growth_curve": curve_name,
            })

    for source, beta in betas.items():
        add("base", source, beta)
    empirical = betas.get("empirical spruce", params["literature_beta"])
    label = "empirical spruce" if "empirical spruce" in betas else "thesis 0.55"
    for mult in (0.0, 0.5, 1.5, 2.0):
        add(f"carbon price x{mult:g}", label, empirical, p_k=p_k * mult)
    for mult in (0.75, 1.25):
        add(f"timber price x{mult:g}", label, empirical, p_s=p_s * mult)
    for mult in (0.5, 2.0):
        add(f"electricity price x{mult:g}", label, empirical, p_e=p_e * mult)
    for i in (0.02, 0.04):
        add(f"discount rate {i:g}", label, empirical, i=i)
    add("constant carbon price", label, empirical, carbon_price_grows_with_i=False)
    # The other growth curve(s), every beta: the comparison the SiTree run exists for.
    for name, other in others.items():
        for source, beta in betas.items():
            add(f"growth curve: {name}", source, beta, on=other, curve_name=name)

    results = pd.DataFrame(rows, columns=RESULT_COLUMNS)
    # The timber price at which a bare-land Faustmann rotation just breaks even:
    # p_S* = k / max_T(S_T e^{-iT}). Below it every Faustmann NPV is negative.
    break_even = base["k"] / float(np.max(curve.volume * np.exp(-base["i"] * curve.age)))
    if write:
        header = [
            "Circwood WP4 - rotation economics with the empirical beta (models/rotation/rotation.py, ROADMAP Phase L). "
            "Do not edit by hand; read with pandas.read_csv(path, comment='#').",
            "Models follow Landaas (2023), NTNU: faustmann_single (eq. 5), faustmann (eq. 13, infinite rotations), "
            "van_kooten (eq. 17), crc_owner (eq. 19, reward only beyond the Faustmann age, dead-biomass decay charged), "
            "social (eq. 21, with the energy from the share not stored). NPV in NOK per hectare; T on an annual grid.",
            f"Growth: {curve_note}; max living volume {curve.volume.max():.0f} m3/ha on the primary curve. Rows with "
            f"scenario 'growth curve: ...' run the same models on the other curve; the growth_curve column names the "
            f"curve of every row. Prices: {price_note}; carbon "
            f"{p_k:.0f} NOK/t CO2 (config/prices.csv); alpha {alpha:.4f} t CO2/m3; sigma {base['sigma']:g}/yr; "
            f"planting {base['k']:.0f} NOK/ha; i {base['i']:g}.",
            "beta: 'thesis 0.55' is the share Landaas (2023) assumed; 'empirical spruce' is the mean spruce beta of "
            "data/processed/beta_series.csv over 2012-2022, with its ci_low/ci_high envelope (an assumption envelope, "
            "see that file's header).",
            "Carbon price path: carbon_price_grows_with_i = 1 values carbon at p_K e^(it) (thesis eq. 19) in the "
            "crc_owner and social models - uptake and release alike; the 'constant carbon price' scenario holds p_K "
            "fixed. van_kooten always uses the constant price of van Kooten (1995).",
            "interior_optimum = 0 means the optimum sits on an edge of the searched ages: at the end of the growth "
            "curve (250 years) the NPV was still rising and the model says never harvest within the horizon - a "
            "statement about its assumptions (constant prices, carbon valued forever), not a rotation age; at the "
            "first searched age (20 years, or where the curve starts) the stand was already past its best age.",
            "The 'sitree example stand' curve's absolute volumes lie outside the calibration range of its increment and "
            "mortality functions (basal area to ~280 m2/ha; see the export's header): it is compared for its shape, and "
            "its NPV levels are not comparable with the other curves'. The 'ssb-anchored spruce stand SI 11/14/17' "
            "curves are planted stands at a realistic density validated against SSB's landscape means (models/sitree/"
            "ssb_validation.csv).",
            f"npv_positive = 0 means the NPV at the optimum is negative: planting does not pay at these prices and the "
            f"age is the argmax of a loss. The Faustmann rows need a timber price above about {break_even:.0f} NOK/m3 "
            f"to break even on this curve (planting {base['k']:.0f} NOK/ha; {p_s:.0f} NOK/m3 used).",
            "Not a forecast: a single-stand optimisation on a calibrated curve. Replace growth_curve.csv with a SiTree "
            "export (models/sitree) to use simulated growth.",
        ]
        RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# " + h for h in header] + [",".join(RESULT_COLUMNS)]
        for r in results.itertuples(index=False):
            cells = []
            for col, val in zip(RESULT_COLUMNS, r):
                if col in ("scenario", "model", "beta_source", "growth_curve"):
                    text = str(val)
                    cells.append('"' + text + '"' if "," in text else text)
                elif col in ("t_opt_years", "delta_t_vs_faustmann_years", "carbon_price_grows_with_i",
                             "interior_optimum", "npv_positive"):
                    cells.append(str(int(val)))
                elif col == "npv_nok_ha":
                    cells.append(f"{float(val):.1f}")
                else:
                    cells.append(f"{float(val):.6g}")
            lines.append(",".join(cells))
        RESULTS_CSV.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        # The primary curve, as run, for the app and for the reader (always rewritten,
        # so the committed file cannot drift from the parameters that define it).
        curve.to_csv(GROWTH_CURVE_CSV, (
            f"primary growth curve '{primary_name}' written by models/rotation/rotation.py (parameters.csv "
            "primary_growth_curve); the SiTree exports live in models/sitree/growth_curve_*.csv and every other "
            "curve is compared in data/processed/rotation_results.csv under the scenario 'growth curve: <name>'"
        ))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Rotation economics with the empirical beta (Phase L).")
    parser.add_argument("--no-write", action="store_true", help="compute without writing the results file")
    args = parser.parse_args()
    results = run(write=not args.no_write)
    base = results[results["scenario"] == "base"].pivot(index="beta_source", columns="model", values="t_opt_years")
    print(base.to_string())
    other = results[results["scenario"].str.startswith("growth curve: ")]
    if not other.empty:
        print(other.pivot_table(index=["scenario", "beta_source"], columns="model", values="t_opt_years",
                                aggfunc="first").to_string())
    if not args.no_write:
        print(f"Wrote {len(results)} rows to {RESULTS_CSV}")


if __name__ == "__main__":
    main()
