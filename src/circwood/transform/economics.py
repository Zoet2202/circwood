"""The economic value layer — ROADMAP Phase L ("value the flows").

Puts money on the flows the Sankey already quantifies, at every stage where a
public price exists, and says where it does not. Three price sources:

* **SSB 07413** — average roundwood prices by assortment (NOK per m³, annual,
  2006–), the same ``Treslag`` codes as the removals table 07410: spruce / pine /
  broadleaf × sawlogs / pulpwood / mixed (*sams*), plus the all-assortment
  average. Values the roundwood where it leaves the species pools: to the
  mills, to firewood (at the all-assortment average, a documented proxy — SSB
  publishes no firewood price), and to the netted roundwood exports.
* **SSB 08360** — wholesale electricity prices (øre per kWh, annual, 1994–2024),
  series *Elspotmarknaden* (the day-ahead market). With the thesis's energy
  yield of 753 kWh per m³ (20.08 MJ/kg dry wood × 375 kg/m³ × 36 % plant
  efficiency ÷ 3.6 MJ/kWh) it values the wood that becomes energy. The
  wholesale price is the social value of the electricity; a household price
  would include grid rent and taxes.
* **``config/prices.csv``** — what SSB does not publish: the carbon price
  (the thesis's €94 per t CO₂ of 19 May 2023, converted at an assumed EUR/NOK
  rate), the energy yield, and the carbon fraction. Every row carries its
  source and whether it is an assumption, and the output repeats that.

What is *not* valued, and why: processed products (sawn, planed, impregnated
timber, panels, pulp and paper). SSB publishes producer price *indices* for
those industries, not prices per m³; a level would need a trade-statistics
unit value or member data, which is a human decision (ROADMAP Phase L,
"needs a human"). The layer therefore values the system at its roundwood
input, at its energy output and at its carbon, and labels every number with
its basis. Prices are nominal NOK of each year.

Outputs
-------
``data/raw/prices_ssb.csv`` (committed; a fresh run regenerates it, like the
county-harvest file): one row per (table, year, series code, value, unit).

``data/processed/economic_value.csv``: one row per (year, stage, item,
species, assortment) with the quantity, its unit, the price, the price unit,
the price source and ``value_nok``. Stages: ``roundwood`` (pool outflows at
07413 prices), ``energy`` (Energy inflows at 753 kWh/m³ × elspot), ``carbon
stored`` (carbon entering Long-term USE at the carbon price, as t CO₂) and
``carbon released`` (carbon entering Energy, the same price, negative — the
social cost the thesis's social model charges for the burnt share).
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from circwood import config
from circwood import constants as C

log = logging.getLogger(__name__)

PRICE_TABLES = ("07413", "08360")

#: 07413 ``sortiment`` label -> (species, assortment) in the flow table's vocabulary.
ROUNDWOOD_SERIES = {
    "Gran sagtømmer": ("spruce", "sawtimber"),
    "Gran massevirke": ("spruce", "pulpwood"),
    "Gran sams sagtømmer og massevirke": ("spruce", "sams"),
    "Furu sagtømmer": ("pine", "sawtimber"),
    "Furu massevirke": ("pine", "pulpwood"),
    "Furu sams sagtømmer og massevirke": ("pine", "sams"),
    "Lauvtre sagtømmer": ("non-coniferous", "sawtimber"),
    "Lauvtre massevirke": ("non-coniferous", "pulpwood"),
    "Lauvtre sams sagtømmer og massevirke": ("non-coniferous", "sams"),
    "I alt": ("all", "all"),
}

#: 08360 series used as the market price of electricity.
ELSPOT_LABEL = "B.1 Elspotmarknaden"

PRICES_COLUMNS = ["table", "year", "series", "species", "assortment", "value", "unit"]
VALUE_COLUMNS = [
    "year", "stage", "item", "species", "assortment", "quantity", "quantity_unit",
    "price", "price_unit", "price_source", "value_nok",
]

#: Flow edges valued as roundwood, with the assortment price they take.
#: Firewood takes the all-assortment average (no firewood price exists).
_POOLS = {"Spruce": "spruce", "Pine": "pine", "Non-coniferous": "non-coniferous"}
_ROUNDWOOD_TARGETS = {
    "Sawmills": "sawtimber", "Net export skurlast": "sawtimber",
    "Pulpmills": "pulpwood", "Net export massevirke": "pulpwood",
    "Boardmills": "sams", "Firewood": "all",
}
ENERGY_NODE = "Energy"
LTUSE = "Long-term USE"


# ---------------------------------------------------------------------------
# Assumptions file
# ---------------------------------------------------------------------------
REQUIRED_ASSUMPTIONS = (
    "carbon_price_eur_per_tco2", "nok_per_eur", "energy_kwh_per_m3",
)


def load_assumptions(path: Path | None = None) -> pd.DataFrame:
    """``config/prices.csv`` as a frame indexed by ``parameter`` (value, unit, kind, source)."""
    path = Path(path) if path is not None else config.PRICES_FILE
    df = pd.read_csv(path)
    for col in ("parameter", "value", "unit", "kind", "source"):
        if col not in df.columns:
            raise ValueError(f"{path.name}: missing column {col!r}.")
    missing = sorted(set(REQUIRED_ASSUMPTIONS) - set(df["parameter"]))
    if missing:
        raise ValueError(f"{path.name}: missing parameter(s) {missing}.")
    if (df["value"] < 0).any():
        raise ValueError(f"{path.name}: a value is negative.")
    return df.set_index("parameter")


# ---------------------------------------------------------------------------
# SSB price tables -> the committed tidy file
# ---------------------------------------------------------------------------
def build_price_tables(ingested) -> pd.DataFrame:
    """Tidy the 07413 and 08360 frames of an ingest result into :data:`PRICES_COLUMNS`."""
    parts = []
    for table_id in PRICE_TABLES:
        if table_id not in ingested.datasets:
            raise ValueError(f"price layer: SSB {table_id} missing from the ingest result.")
    df = pd.concat(ingested.datasets["07413"], ignore_index=True)
    unknown = sorted(set(df["sortiment"]) - set(ROUNDWOOD_SERIES))
    if unknown:
        raise ValueError(f"SSB 07413 returned assortment label(s) not mapped: {unknown}.")
    rows = pd.DataFrame({
        "table": "07413",
        "year": pd.to_numeric(df["år"]).astype(int),
        "series": df["sortiment"],
        "species": df["sortiment"].map(lambda s: ROUNDWOOD_SERIES[s][0]),
        "assortment": df["sortiment"].map(lambda s: ROUNDWOOD_SERIES[s][1]),
        "value": pd.to_numeric(df["value"], errors="coerce"),
        "unit": "NOK per m3",
    })
    parts.append(rows.dropna(subset=["value"]))

    el = pd.concat(ingested.datasets["08360"], ignore_index=True)
    el = el[el["statistikkvariabel"] == ELSPOT_LABEL]
    if el.empty:
        raise ValueError(f"SSB 08360: series {ELSPOT_LABEL!r} not in the response.")
    parts.append(pd.DataFrame({
        "table": "08360",
        "year": pd.to_numeric(el["år"]).astype(int),
        "series": ELSPOT_LABEL,
        "species": "all", "assortment": "all",
        "value": pd.to_numeric(el["value"], errors="coerce"),
        "unit": "øre per kWh",
    }).dropna(subset=["value"]))
    out = pd.concat(parts, ignore_index=True)[PRICES_COLUMNS]
    return out.sort_values(["table", "year", "series"]).reset_index(drop=True)


def load_price_tables(path: Path | None = None) -> pd.DataFrame | None:
    """The committed SSB price file, or ``None`` if it has not been built."""
    path = Path(path) if path is not None else config.PRICES_SSB_CSV
    if not path.exists():
        return None
    return pd.read_csv(path, dtype={"table": str})


# ---------------------------------------------------------------------------
# Valuation
# ---------------------------------------------------------------------------
def _roundwood_price_lookup(prices: pd.DataFrame) -> dict[tuple[int, str, str], float]:
    rw = prices[prices["table"] == "07413"]
    return {(int(r.year), r.species, r.assortment): float(r.value) for r in rw.itertuples()}


def _elspot_lookup(prices: pd.DataFrame) -> dict[int, float]:
    el = prices[prices["table"] == "08360"]
    return {int(r.year): float(r.value) / 100.0 for r in el.itertuples()}  # øre -> NOK per kWh


def value_flows(
    carbon: pd.DataFrame,
    prices: pd.DataFrame,
    assumptions: pd.DataFrame,
) -> pd.DataFrame:
    """Value the flow table; see the module docstring for stages and bases.

    ``carbon`` is the carbon frame (volume in m³, carbon in kg C). Years with
    no price for a stage are reported with ``price`` empty and no value, never
    silently dropped.
    """
    rw_price = _roundwood_price_lookup(prices)
    el_price = _elspot_lookup(prices)
    kwh_per_m3 = float(assumptions.loc["energy_kwh_per_m3", "value"])
    carbon_nok = (float(assumptions.loc["carbon_price_eur_per_tco2", "value"])
                  * float(assumptions.loc["nok_per_eur", "value"]))
    carbon_source = (
        f"{assumptions.loc['carbon_price_eur_per_tco2', 'source']}; "
        f"EUR/NOK {assumptions.loc['nok_per_eur', 'value']:g} ({assumptions.loc['nok_per_eur', 'kind']})"
    )
    rows: list[dict] = []

    def add(year, stage, item, species, assortment, qty, qty_unit, price, price_unit, source):
        rows.append({
            "year": int(year), "stage": stage, "item": item, "species": species,
            "assortment": assortment, "quantity": float(qty), "quantity_unit": qty_unit,
            "price": price, "price_unit": price_unit, "price_source": source,
            "value_nok": float(qty) * price if price is not None and pd.notna(price) else float("nan"),
        })

    # Roundwood at the pool gate, by species and assortment.
    rw = carbon[carbon["source"].isin(_POOLS) & carbon["target"].isin(_ROUNDWOOD_TARGETS)]
    grouped = rw.groupby(["year", "source", "target"], as_index=False)["volume"].sum()
    for r in grouped.itertuples(index=False):
        species = _POOLS[r.source]
        assortment = _ROUNDWOOD_TARGETS[r.target]
        # The all-assortment average is published once, not per species.
        key = (int(r.year), "all", "all") if assortment == "all" else (int(r.year), species, assortment)
        price = rw_price.get(key)
        source = "SSB 07413 average price, " + (
            "all assortments (proxy: no firewood price is published)" if assortment == "all"
            else f"{species} {assortment}"
        )
        if price is None and assortment == "sams":
            # Broadleaf 'sams' is not published; fall back to the species' pulpwood price.
            price = rw_price.get((int(r.year), species, "pulpwood"))
            source = f"SSB 07413 {species} pulpwood (sams not published for this species)"
        add(r.year, "roundwood", r.target, species, assortment, r.volume, "m3",
            price, "NOK per m3", source)

    # Energy: every inflow to Energy, at 753 kWh/m3 × the day-ahead price.
    en = carbon[carbon["target"] == ENERGY_NODE].groupby(["year", "source"], as_index=False)["volume"].sum()
    for r in en.itertuples(index=False):
        nok_per_kwh = el_price.get(int(r.year))
        price = None if nok_per_kwh is None else nok_per_kwh * kwh_per_m3
        add(r.year, "energy", r.source, "all", "all", r.volume, "m3", price, "NOK per m3",
            f"SSB 08360 Elspot {nok_per_kwh:.3f} NOK/kWh × {kwh_per_m3:g} kWh/m3 "
            f"({assumptions.loc['energy_kwh_per_m3', 'source']})" if nok_per_kwh is not None
            else "SSB 08360 Elspot: no price for this year")

    # Carbon: stored (into Long-term USE) and released (into Energy), t CO2 at the carbon price.
    for stage, node, sign in (("carbon stored", LTUSE, 1.0), ("carbon released", ENERGY_NODE, -1.0)):
        sub = carbon[carbon["target"] == node].groupby(["year", "source"], as_index=False)["carbon"].sum()
        for r in sub.itertuples(index=False):
            tco2 = float(r.carbon) * C.CO2_PER_C / 1000.0
            add(r.year, stage, r.source, "all", "all", tco2, "t CO2", sign * carbon_nok,
                "NOK per t CO2", carbon_source)

    out = pd.DataFrame(rows, columns=VALUE_COLUMNS)
    return out.sort_values(["year", "stage", "item", "species", "assortment"]).reset_index(drop=True)


def value_summary(values: pd.DataFrame, min_year: int = C.MIN_YEAR, max_year: int = C.MAX_YEAR) -> pd.DataFrame:
    """Mean annual value by stage over a window (NOK per year), with the years priced."""
    w = values[(values["year"] >= min_year) & (values["year"] <= max_year)]
    out = (w.groupby("stage").agg(value_nok_per_year=("value_nok", lambda s: s.sum() / w["year"].nunique()),
                                 years_priced=("value_nok", lambda s: s.notna().sum()))
           .reset_index())
    return out


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
def export_economic_value(path: Path | str | None = None, *, carbon: pd.DataFrame | None = None) -> Path:
    """Write ``economic_value.csv`` with ``#`` header lines stating the bases. Deterministic."""
    path = Path(path) if path is not None else config.ECONOMIC_VALUE_CSV
    prices = load_price_tables()
    if prices is None:
        raise FileNotFoundError(
            f"{config.PRICES_SSB_CSV} is missing; run circwood-economics (needs the ssb extras and network)."
        )
    assumptions = load_assumptions()
    if carbon is None:
        carbon = pd.read_csv(config.CARBON_CSV)
    values = value_flows(carbon, prices, assumptions)
    summary = value_summary(values)
    by_stage = ", ".join(
        f"{r.stage} {r.value_nok_per_year / 1e9:.2f} bn NOK/yr" for r in summary.itertuples()
    )
    header = [
        "Circwood WP4 - economic value of the wood flows, ROADMAP Phase L. Generated by "
        "circwood.transform.economics.export_economic_value; do not edit by hand. Read with "
        "pandas.read_csv(path, comment='#').",
        "Nominal NOK of each year. value_nok = quantity x price computed before rounding (the file keeps "
        "quantity to 3 and price to 4 decimals); a row with an empty price has no published price for that "
        "year and is kept so the gap is visible.",
        "roundwood: species-pool outflows (m3) at SSB 07413 average prices by assortment; firewood at the "
        "all-assortment average (proxy); the netted export edges at the sawlog/pulpwood price. Imported "
        "logs folded into the mill edges are valued like domestic ones.",
        f"energy: inflows to Energy (m3) x {float(assumptions.loc['energy_kwh_per_m3', 'value']):g} kWh/m3 "
        "x SSB 08360 Elspot (day-ahead) price; the social value of the electricity, not a household tariff.",
        "carbon stored / carbon released: carbon entering Long-term USE / Energy as t CO2 (x 44.0095/12.0107) "
        f"at {float(assumptions.loc['carbon_price_eur_per_tco2', 'value']):g} EUR/t CO2 x "
        f"{float(assumptions.loc['nok_per_eur', 'value']):g} NOK/EUR (config/prices.csv; assumptions, "
        "not statistics). Released carbon is a negative value.",
        "Not valued: sawn, planed and impregnated timber, panels, pulp and paper (no published price per m3; "
        "a human decision on sources and licensing, ROADMAP Phase L).",
        f"Means {C.MIN_YEAR}-{C.MAX_YEAR}: {by_stage}.",
    ]
    lines = ["# " + h for h in header] + [",".join(VALUE_COLUMNS)]
    for r in values.itertuples(index=False):
        cells = []
        for col, val in zip(VALUE_COLUMNS, r):
            if col == "year":
                cells.append(str(int(val)))
            elif col in ("stage", "item", "species", "assortment", "quantity_unit", "price_unit", "price_source"):
                text = str(val)
                cells.append('"' + text.replace('"', '""') + '"' if any(ch in text for ch in ',"\n') else text)
            elif val is None or pd.isna(val):
                cells.append("")
            elif col == "quantity":
                cells.append(f"{float(val):.3f}")
            elif col == "price":
                cells.append(f"{float(val):.4f}")   # Elspot x 753 kWh/m3 has three decimals
            else:
                cells.append(f"{float(val):.2f}")
        lines.append(",".join(cells))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def build_economics(write: bool = True, ingested=None) -> pd.DataFrame:
    """Fetch the SSB price tables (``config/ssb_queries/economic``), tidy and value the flows."""
    if ingested is None:
        from circwood.ingest.ssb import process_query_files

        ingested = process_query_files(str(config.ECONOMIC_QUERY_DIR))
    prices = build_price_tables(ingested)
    if write:
        config.RAW_DIR.mkdir(parents=True, exist_ok=True)
        prices.to_csv(config.PRICES_SSB_CSV, index=False, encoding="utf-8", lineterminator="\n")
        export_economic_value()
        log.info("Wrote %d price rows and %s", len(prices), config.ECONOMIC_VALUE_CSV)
    return prices


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch SSB price tables and write data/raw/prices_ssb.csv and data/processed/economic_value.csv."
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    prices = build_economics(write=True)
    print(f"Wrote {len(prices)} price rows to {config.PRICES_SSB_CSV} and valued the flows into "
          f"{config.ECONOMIC_VALUE_CSV}")


__all__ = [
    "ELSPOT_LABEL",
    "PRICES_COLUMNS",
    "PRICE_TABLES",
    "ROUNDWOOD_SERIES",
    "VALUE_COLUMNS",
    "build_economics",
    "build_price_tables",
    "export_economic_value",
    "load_assumptions",
    "load_price_tables",
    "value_flows",
    "value_summary",
]
