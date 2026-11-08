"""Forward projection of the national wood stock — ROADMAP Phase L.

An *area-class* model: Norway's productive forestry land is split into the
five development classes the National Forest Inventory reports (I regeneration,
II young, III younger production, IV older production, V mature), today's
split and totals come from the SSB inventory tables (``forest_resource.csv``:
14594 class shares, 14591 area, 14589 standing volume, 14590 gross increment),
and the areas are rolled forward one year at a time:

* ageing: a class passes ``1 / residence`` of its area to the next class each
  year (classes I–IV), so a stand spends on average ``residence`` years in it;
* harvest: a scenario-specific volume is felled from class V (and from IV if V
  runs short) at the class's volume per hectare, and the felled area returns to
  class I;
* volume: the standing stock follows ``V_{t+1} = V_t + increment_t − harvest_t
  − mortality_t`` where the increment is the sum of area × per-hectare
  increment by class (young classes grow fastest, mature slowest), scaled so
  that today's areas reproduce today's inventory increment, and mortality is a
  fixed share of the stock.

Three harvest scenarios, from the rotation model's results
(``rotation_results.csv``):

* **status quo** — the 2012–2022 mean roundwood harvest of the flow table
  (``Harvested wood → pools``) every year;
* **faustmann** — the stand is felled at the Faustmann rotation age, so class V
  turns over once every ``T_F − age at entering V`` years;
* **crc** — the same with the CRC owner model's rotation age (longer), i.e.
  harvest is deferred and the stock builds up.

Today's class V holds far more area than a 90-year rotation would keep there,
so an uncapped Faustmann scenario would fell about five times today's harvest
in its first year. The rotation scenarios are therefore *phased*: no year may
harvest more than ``harvest_cap_multiple_of_status_quo`` times the status-quo
volume (1.5 by default), which works the backlog down over decades the way
capacity and markets would.

Per-hectare volumes by class are calibrated once from today's standing volume
(class V volume per hectare is solved so that ``Σ area_c × weight_c × v_V`` equals
the inventory total), and the class residences, weights and mortality are
*indicative national averages* in ``parameters.csv``. This is a scenario tool
that shows the direction and rough size of what a change in rotation does to
the standing stock and the harvest, with every assumption in a CSV next to the
code; it is not a forecast, and it does not replace a stand-level simulator
(SiTree) run across the inventory's plots, which would need per-plot data and
R. Regions are not modelled: the class shares are national.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PARAMETERS_CSV = HERE / "parameters.csv"
FOREST_CSV = ROOT / "data" / "processed" / "forest_resource.csv"
SANKEY_CSV = ROOT / "data" / "processed" / "sankey_data.csv"
ROTATION_CSV = ROOT / "data" / "processed" / "rotation_results.csv"
RESULTS_CSV = ROOT / "data" / "processed" / "forest_stock_projection.csv"

CLASSES = ("I", "II", "III", "IV", "V")
CLASS_CODES = {"I": "HogstI", "II": "HogstII", "III": "HogstIII", "IV": "HogstIV", "V": "HogstV"}
SCENARIOS = ("status quo", "faustmann", "crc")
RESULT_COLUMNS = [
    "scenario", "year", "standing_volume_m3", "increment_m3", "harvest_m3", "mortality_m3",
    "area_I_km2", "area_II_km2", "area_III_km2", "area_IV_km2", "area_V_km2", "rotation_age_years",
]


def load_parameters(path: Path = PARAMETERS_CSV) -> dict[str, float]:
    df = pd.read_csv(path)
    return {str(r.parameter): float(r.value) for r in df.itertuples()}


def today(forest: pd.DataFrame) -> dict:
    """National starting state from the latest inventory interval."""
    nat = forest[forest["region"] == "NO"]
    latest = int(nat.loc[nat["table"] == "14594", "year_end"].max())

    def value(table: str, measure: str, species: str = "all") -> float:
        sub = nat[(nat["table"] == table) & (nat["measure"] == measure) & (nat["species"] == species)]
        sub = sub[sub["year_end"] == sub["year_end"].max()]
        if sub.empty:
            raise ValueError(f"forest_resource.csv: no national {table} {measure} ({species}) row.")
        return float(sub["value"].iloc[0])

    area_km2 = value("14591", "ProdSkogBrukM")
    shares = {c: value("14594", CLASS_CODES[c]) / 100.0 for c in CLASSES}
    total_share = sum(shares.values())
    shares = {c: s / total_share for c, s in shares.items()}          # SSB rounds to whole percent
    return {
        "interval_end": latest,
        "area_km2": area_km2,
        "areas_km2": {c: area_km2 * shares[c] for c in CLASSES},
        "standing_volume_m3": value("14589", "Skogsareal4") * 1000.0,
        "increment_m3": value("14590", "Skogsareal4") * 1000.0,
    }


def mean_harvest(sankey: pd.DataFrame, min_year: int, max_year: int) -> float:
    w = sankey[(sankey["year"] >= min_year) & (sankey["year"] <= max_year)]
    pools = w[(w["source"] == "Harvested wood") & w["target"].isin(["Spruce", "Pine", "Non-coniferous"])]
    return float(pools["volume"].sum()) / (max_year - min_year + 1)


def rotation_ages(path: Path = ROTATION_CSV) -> dict[str, int]:
    """Faustmann and CRC rotation ages from the rotation model (empirical beta, base scenario)."""
    if not path.exists():
        return {"faustmann": 90, "crc": 120}
    r = pd.read_csv(path, comment="#")
    base = r[r["scenario"] == "base"]
    source = "empirical spruce" if (base["beta_source"] == "empirical spruce").any() else "thesis 0.55"
    base = base[base["beta_source"] == source].set_index("model")["t_opt_years"]
    return {"faustmann": int(base["faustmann"]), "crc": int(base["crc_owner"])}


def project(
    state: dict, params: dict[str, float], scenario: str, harvest_target: float | None,
    rotation_age: int | None, harvest_cap: float | None = None,
) -> pd.DataFrame:
    """One scenario, annual rows from ``start_year`` to ``end_year``.

    ``harvest_cap`` (m³/yr) phases a rotation scenario: the area felled in a
    year is limited to what that volume allows.
    """
    residence = {c: params[f"residence_years_class_{c}"] for c in CLASSES[:-1]}
    vol_w = {c: params[f"volume_weight_class_{c}"] for c in CLASSES}
    inc_w = {c: params[f"increment_weight_class_{c}"] for c in CLASSES}
    areas = {c: state["areas_km2"][c] * 100.0 for c in CLASSES}               # ha
    v_class_v = state["standing_volume_m3"] / sum(areas[c] * vol_w[c] for c in CLASSES)
    v_per_ha = {c: v_class_v * vol_w[c] for c in CLASSES}
    inc_scale = state["increment_m3"] / sum(areas[c] * inc_w[c] for c in CLASSES)
    inc_per_ha = {c: inc_scale * inc_w[c] for c in CLASSES}
    mortality = params["mortality_rate_per_year"]
    age_at_v = sum(residence.values())
    stock = state["standing_volume_m3"]

    rows = []
    for year in range(int(params["start_year"]), int(params["end_year"]) + 1):
        increment = sum(areas[c] * inc_per_ha[c] for c in CLASSES)
        died = mortality * stock
        # Harvest: a volume target, or the area reaching the rotation age.
        if rotation_age is not None:
            years_in_v = max(rotation_age - age_at_v, 1.0)
            harvest_area = areas["V"] / years_in_v
            if harvest_cap is not None:
                harvest_area = min(harvest_area, harvest_cap / v_per_ha["V"])
            harvest = harvest_area * v_per_ha["V"]
            felled = {"V": harvest_area}
        else:
            target = float(harvest_target or 0.0)
            from_v = min(target / v_per_ha["V"], areas["V"])
            remaining = target - from_v * v_per_ha["V"]
            from_iv = min(remaining / v_per_ha["IV"], areas["IV"]) if remaining > 0 else 0.0
            harvest = from_v * v_per_ha["V"] + from_iv * v_per_ha["IV"]
            felled = {"V": from_v, "IV": from_iv}
        rows.append({
            "scenario": scenario, "year": year, "standing_volume_m3": stock,
            "increment_m3": increment, "harvest_m3": harvest, "mortality_m3": died,
            **{f"area_{c}_km2": areas[c] / 100.0 for c in CLASSES},
            "rotation_age_years": rotation_age if rotation_age is not None else np.nan,
        })
        # Age the classes, then apply the felling.
        moved = {c: areas[c] / residence[c] for c in CLASSES[:-1]}
        new = dict(areas)
        for c, nxt in zip(CLASSES[:-1], CLASSES[1:]):
            new[c] -= moved[c]
            new[nxt] += moved[c]
        for c, a in felled.items():
            new[c] -= a
            new["I"] += a
        areas = {c: max(a, 0.0) for c, a in new.items()}
        stock = max(stock + increment - harvest - died, 0.0)
    return pd.DataFrame(rows, columns=RESULT_COLUMNS)


def run(write: bool = True) -> pd.DataFrame:
    params = load_parameters()
    forest = pd.read_csv(FOREST_CSV, dtype={"table": str, "interval": str, "region": str, "measure": str})
    sankey = pd.read_csv(SANKEY_CSV)
    state = today(forest)
    harvest = mean_harvest(sankey, int(params["status_quo_harvest_window_min"]),
                           int(params["status_quo_harvest_window_max"]))
    ages = rotation_ages()
    cap = harvest * params["harvest_cap_multiple_of_status_quo"]
    frames = [
        project(state, params, "status quo", harvest, None),
        project(state, params, "faustmann", None, ages["faustmann"], cap),
        project(state, params, "crc", None, ages["crc"], cap),
    ]
    results = pd.concat(frames, ignore_index=True)
    if write:
        header = [
            "Circwood WP4 - forward projection of the national wood stock (models/forest_stock/projection.py, "
            "ROADMAP Phase L). Do not edit by hand; read with pandas.read_csv(path, comment='#').",
            f"Start: SSB inventory interval ending {state['interval_end']} - productive forestry land "
            f"{state['area_km2']:,.0f} km2, standing volume {state['standing_volume_m3'] / 1e6:,.1f} Mm3, "
            f"gross increment {state['increment_m3'] / 1e6:,.1f} Mm3/yr (data/processed/forest_resource.csv, "
            "national, under bark on productive forestry land).",
            f"Scenarios: status quo = the {int(params['status_quo_harvest_window_min'])}-"
            f"{int(params['status_quo_harvest_window_max'])} mean roundwood harvest "
            f"({harvest / 1e6:,.2f} Mm3/yr, data/processed/sankey_data.csv) every year; faustmann = final felling "
            f"at {ages['faustmann']} years; crc = final felling at {ages['crc']} years (data/processed/rotation_results.csv); "
            f"both phased so no year harvests more than {params['harvest_cap_multiple_of_status_quo']:g} x the status quo "
            f"({cap / 1e6:,.1f} Mm3/yr).",
            "Area-class model with indicative national-average class residences, per-hectare volume and increment "
            "weights and mortality (models/forest_stock/parameters.csv), calibrated to today's totals. A scenario "
            "tool, not a forecast; no regional detail; no climate or price feedback.",
        ]
        RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# " + h for h in header] + [",".join(RESULT_COLUMNS)]
        for r in results.itertuples(index=False):
            cells = []
            for col, val in zip(RESULT_COLUMNS, r):
                if col == "scenario":
                    cells.append(str(val))
                elif col == "year":
                    cells.append(str(int(val)))
                elif col == "rotation_age_years":
                    cells.append("" if pd.isna(val) else str(int(val)))
                else:
                    cells.append(f"{float(val):.1f}")
            lines.append(",".join(cells))
        RESULTS_CSV.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Forward projection of the national wood stock (Phase L).")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    results = run(write=not args.no_write)
    last = results[results["year"].isin([results["year"].min(), 2050, 2100])]
    print(last.pivot(index="year", columns="scenario", values="standing_volume_m3").div(1e6).round(1).to_string())
    if not args.no_write:
        print(f"Wrote {len(results)} rows to {RESULTS_CSV}")


if __name__ == "__main__":
    main()
