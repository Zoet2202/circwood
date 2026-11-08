"""The forest resource layer — ROADMAP Phase H.

Closes the front boundary of the Sankey: ``Harvested wood`` used to be an
input edge with no resource behind it. SSB publishes NIBIO's National Forest
Inventory (Landsskogtakseringen) as six tables; this module ingests them with
the same PxWeb machinery as the flow tables and writes one tidy, committed
table, ``data/processed/forest_resource.csv``.

What the inventory is, and why it cannot join the flow table on ``year``
--------------------------------------------------------------------------
Permanent 250 m² plots on a 3 × 3 km grid, each re-measured every five years,
so every figure is a *rolling five-year interval* (``2021-2025``), never a
calendar year. The tables use nine *takserte regioner* — the whole country and
eight groupings of the 2024 counties — which are coarser than the flow table's
``region`` dimension; :data:`NFI_REGIONS` is the closed crosswalk to the ISO
county codes so harvest by county can be summed up to an NFI region. The
table ``06289`` is the long national series back to 1933, where each year
stands for a multi-year inventory period.

**Productive forest** is defined by site productivity (H40 ≥ 6, roughly
≥ 1 m³/ha/yr). *Productive forestry land* (``Produktiv skogbruksmark``)
excludes productive forest that is protected or set aside, which is what makes
it approximately the commercially available forest; conflating the two is a
common error, and the output keeps SSB's measure codes so the reader can see
which one a figure is.

Tables and what they give (national 2021–2025 values from the live metadata
check of 2026-11-08 are quoted in METHODS.md):

* **14591** — forest area, km²: productive (total / forestry land / protected),
  unproductive, other wooded land. The area base.
* **14594** — share of productive forestry land by development class I–V and
  site class H40 (per cent). The harvestable stand (classes IV–V) by region —
  the starting stock Phase L projects forward. Pinned on codes, because the
  Norwegian label of ``HogstIV`` reads "Hogstklasse VI" (an SSB label error).
* **14593** — productive forestry land by forest type (spruce, pine, mixed,
  broadleaf), km².
* **14589** — standing volume under bark, 1 000 m³, by region × species.
* **14590** — annual gross increment under bark, 1 000 m³, by region × species.
* **06289** — national standing volume and increment by species, 1933–2025.

Output schema (``FOREST_COLUMNS``): one row per (table, interval, region,
measure, species) with the value in the table's own unit. Values are the
published figures; nothing is derived here except the region crosswalk and
the ``year_end`` of each interval.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from circwood import config
from circwood import constants as C

log = logging.getLogger(__name__)

#: SSB NFI region code -> (English label, ISO 3166-2:NO county codes it groups).
#: Closed map, like ``dimensions.FYLKE_CODES``: a code SSB adds or renames
#: raises rather than silently minting a region.
NFI_REGIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "0": ("Norway", ()),
    "03+31+33+32": ("Oslo, Akershus, Buskerud and Østfold", ("NO-03", "NO-32", "NO-33", "NO-31")),
    "34": ("Innlandet", ("NO-34",)),
    "39+40": ("Vestfold and Telemark", ("NO-39", "NO-40")),
    "42": ("Agder", ("NO-42",)),
    "11+46+15": ("Rogaland, Vestland and Møre og Romsdal", ("NO-11", "NO-46", "NO-15")),
    "50": ("Trøndelag", ("NO-50",)),
    "18": ("Nordland", ("NO-18",)),
    "55+56": ("Troms and Finnmark", ("NO-55", "NO-56")),
}

#: Region code used for the whole country in the output (matches the flow table's "NO").
NATIONAL = "NO"

#: Tresort code -> species label used across the project.
SPECIES_CODES = {"0000": "all", "1000": "spruce", "2000": "pine", "3000": "non-coniferous"}

#: ContentsCode -> English measure label, pinned on codes (see the module docstring).
MEASURE_LABELS: dict[str, str] = {
    # 14591 — area, km²
    "ProdSkogArTotal": "Productive forest area, total",
    "ProdSkogBrukM": "Productive forestry land (excl. protected / set aside)",
    "VernProdSkogAr": "Protected productive forest",
    "UprodSkogAreal": "Unproductive forest",
    "SkogArealTotal": "Forest area, total",
    "AnnetTreAreal": "Other wooded land",
    # 14594 — share of productive forestry land, per cent
    "HogstTot": "Development class, all",
    "HogstI": "Development class I (regeneration area)",
    "HogstII": "Development class II (young forest)",
    "HogstIII": "Development class III (young production forest)",
    "HogstIV": "Development class IV (older production forest)",
    "HogstV": "Development class V (mature forest)",
    "BonitetTot": "Site class H40, all",
    "Bonitet2": "Site class H40 26-23",
    "Bonitet3": "Site class H40 20",
    "Bonitet4": "Site class H40 17",
    "Bonitet5": "Site class H40 14",
    "Bonitet6": "Site class H40 11",
    "Bonitet8": "Site class H40 8",
    "Bonitet9": "Site class H40 6",
    # 14593 — forest type, km²
    "Skogsareal3": "Productive forestry land excl. regeneration areas, total",
    "Granskog": "Spruce forest",
    "Furu": "Pine forest",
    "Blanding": "Coniferous and mixed forest",
    "Bardominert": "Conifer-dominated mixed forest",
    "Laudominert": "Broadleaf-dominated mixed forest",
    "Lauskog": "Broadleaf forest",
    # 14589 / 14590 — volume and increment, 1 000 m³ under bark
    "Skogsareal4": "On productive forestry land",
    "AndreMark": "On other land",
    # 06289 — national series
    "Kubikkmasse": "Standing volume under bark",
    "Tilvekst": "Annual gross increment under bark",
}

#: Which measure codes belong to which table (so a reclassified table fails loudly).
TABLE_MEASURES: dict[str, tuple[str, ...]] = {
    "14591": ("ProdSkogArTotal", "ProdSkogBrukM", "VernProdSkogAr", "UprodSkogAreal",
              "SkogArealTotal", "AnnetTreAreal"),
    "14594": ("HogstTot", "HogstI", "HogstII", "HogstIII", "HogstIV", "HogstV",
              "BonitetTot", "Bonitet2", "Bonitet3", "Bonitet4", "Bonitet5", "Bonitet6",
              "Bonitet8", "Bonitet9"),
    "14593": ("Skogsareal3", "Granskog", "Furu", "Blanding", "Bardominert", "Laudominert", "Lauskog"),
    "14589": ("Skogsareal4", "AndreMark"),
    "14590": ("Skogsareal4", "AndreMark"),
    "06289": ("Kubikkmasse", "Tilvekst"),
}

FOREST_TABLES = tuple(TABLE_MEASURES)

#: Columns of ``data/processed/forest_resource.csv``.
FOREST_COLUMNS = [
    "table", "interval", "year_end", "region", "region_label", "measure",
    "measure_label", "species", "value", "unit",
]

#: Interval label -> last year ('2021-2025' -> 2025; '2025' -> 2025).
def interval_end(label: str) -> int:
    return int(str(label).strip()[-4:])


def _codes_for(dimension_codes: dict, table_id: str, dim: str) -> dict[str, str]:
    codes = dimension_codes.get(table_id, {}).get(dim)
    if not codes:
        raise ValueError(
            f"SSB {table_id}: no {dim!r} code map in the json-stat2 document; the forest "
            f"layer keys regions and measures by code, not label."
        )
    return codes


def tidy_forest_table(
    table_id: str, frames: list[pd.DataFrame], dimension_codes: dict, units: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Tidy one parsed NFI table into :data:`FOREST_COLUMNS` rows.

    ``dimension_codes`` is ``IngestResult.dimension_codes``; ``units`` maps a
    ContentsCode to its json-stat2 unit base (``"1 000 m³"``), falling back to
    the table's documented unit. Unknown region, species or measure codes raise.
    """
    df = pd.concat(frames, ignore_index=True)
    measure_codes = _codes_for(dimension_codes, table_id, "ContentsCode")
    df["measure"] = df["statistikkvariabel"].map(measure_codes)
    unknown = sorted(set(df.loc[df["measure"].isna(), "statistikkvariabel"]))
    if unknown:
        raise ValueError(f"SSB {table_id}: measure label(s) with no code: {unknown}.")
    expected = set(TABLE_MEASURES[table_id])
    unexpected = sorted(set(df["measure"]) - expected)
    if unexpected:
        raise ValueError(
            f"SSB {table_id} returned measure code(s) {unexpected} this transform does not "
            f"model; add them to forest.TABLE_MEASURES and MEASURE_LABELS deliberately."
        )

    if "region" in df.columns:
        region_codes = _codes_for(dimension_codes, table_id, "Region")
        codes = df["region"].map(region_codes)
        unknown_regions = sorted(set(df.loc[codes.isna(), "region"]) | set(codes.dropna()) - set(NFI_REGIONS))
        if unknown_regions:
            raise ValueError(
                f"SSB {table_id}: NFI region(s) not in forest.NFI_REGIONS: {unknown_regions}. "
                f"A new or renamed region is a presentation decision — add it deliberately."
            )
        df["region_code"] = codes.map(lambda c: NATIONAL if c == "0" else c)
        df["region_label"] = codes.map(lambda c: NFI_REGIONS[c][0])
    else:
        df["region_code"], df["region_label"] = NATIONAL, NFI_REGIONS["0"][0]

    if "treslag" in df.columns:
        species_codes = _codes_for(dimension_codes, table_id, "Tresort")
        sp = df["treslag"].map(species_codes)
        unknown_sp = sorted(set(sp.dropna()) - set(SPECIES_CODES)) + sorted(set(df.loc[sp.isna(), "treslag"]))
        if unknown_sp:
            raise ValueError(f"SSB {table_id}: species code(s) not in forest.SPECIES_CODES: {unknown_sp}.")
        df["species"] = sp.map(SPECIES_CODES)
    else:
        df["species"] = "all"

    period_col = "intervall (år)" if "intervall (år)" in df.columns else "år"
    df["interval"] = df[period_col].astype(str)
    df["year_end"] = df["interval"].map(interval_end)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["value"])

    units = units or {}
    out = pd.DataFrame({
        "table": table_id,
        "interval": df["interval"],
        "year_end": df["year_end"].astype(int),
        "region": df["region_code"],
        "region_label": df["region_label"],
        "measure": df["measure"],
        "measure_label": df["measure"].map(MEASURE_LABELS),
        "species": df["species"],
        "value": df["value"].astype(float),
        "unit": df["measure"].map(lambda m: units.get(m, "")),
    })
    return out.sort_values(["table", "year_end", "interval", "region", "measure", "species"]).reset_index(drop=True)


def units_from_raw(raw: str) -> dict[str, str]:
    """ContentsCode -> unit base string from a raw json-stat2 body."""
    import json

    doc = json.loads(raw)
    cat = doc.get("dimension", {}).get("ContentsCode", {}).get("category", {})
    return {code: (u or {}).get("base", "") for code, u in (cat.get("unit") or {}).items()}


def build_forest_resource(ingested, raw_dir: Path | None = None) -> pd.DataFrame:
    """Tidy every forest table of an :class:`~circwood.ingest.ssb.IngestResult`."""
    raw_dir = Path(raw_dir) if raw_dir is not None else config.RAW_SSB_DIR
    parts = []
    for table_id in FOREST_TABLES:
        if table_id not in ingested.datasets:
            raise ValueError(f"forest layer: SSB {table_id} missing from the ingest result.")
        units: dict[str, str] = {}
        raw_path = raw_dir / f"{table_id}.json"
        if raw_path.exists():
            units = units_from_raw(raw_path.read_text(encoding="utf-8"))
        parts.append(tidy_forest_table(table_id, ingested.datasets[table_id], ingested.dimension_codes, units))
    return pd.concat(parts, ignore_index=True)[FOREST_COLUMNS]


def load_forest_resource(path: Path | None = None) -> pd.DataFrame | None:
    """The committed forest table, or ``None`` if it has not been built."""
    path = Path(path) if path is not None else config.FOREST_RESOURCE_CSV
    if not path.exists():
        return None
    return pd.read_csv(path, keep_default_na=False, na_values=[""],
                       dtype={"table": str, "interval": str, "region": str, "measure": str})


def build_forest(write: bool = True, ingested=None) -> pd.DataFrame:
    """Fetch the six NFI tables from SSB (``config/ssb_queries/forest``) and tidy them.

    Needs the ``ssb`` extras and network unless ``ingested`` is supplied. Writes
    ``data/processed/forest_resource.csv`` when ``write`` is True.
    """
    if ingested is None:
        from circwood.ingest.ssb import process_query_files

        ingested = process_query_files(str(config.FOREST_QUERY_DIR))
    table = build_forest_resource(ingested)
    if write:
        config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        table.to_csv(config.FOREST_RESOURCE_CSV, index=False, encoding="utf-8", lineterminator="\n")
        log.info("Wrote %d forest rows to %s", len(table), config.FOREST_RESOURCE_CSV)
    return table


# ---------------------------------------------------------------------------
# First reading: increment against harvest
# ---------------------------------------------------------------------------
def increment_vs_harvest(
    forest: pd.DataFrame, sankey: pd.DataFrame,
    min_year: int = C.MIN_YEAR, max_year: int = C.MAX_YEAR,
) -> pd.DataFrame:
    """National gross increment (latest interval) against mean roundwood harvest.

    Returns one row per species (``all`` first) with the increment on
    productive forestry land, the total increment (other land included), the
    standing volume, the mean annual ``Harvested wood -> pool`` volume over the
    window and two ratios: ``harvest_to_increment`` against the increment on
    productive forestry land (the conventional comparison, where the
    commercial harvest comes from) and ``harvest_to_increment_total`` against
    all land. Ratios to *report with their caveats* (different periods,
    under-bark inventory figures against harvest with the firewood estimate
    measured with bark), never a fence — see METHODS.md.
    """
    inc = forest[(forest["table"] == "14590") & (forest["region"] == NATIONAL)]
    vol = forest[(forest["table"] == "14589") & (forest["region"] == NATIONAL)]
    latest = inc["year_end"].max()
    inc = inc[inc["year_end"] == latest]
    vol = vol[vol["year_end"] == latest]
    pools = {"all": None, "spruce": "Spruce", "pine": "Pine", "non-coniferous": "Non-coniferous"}
    w = sankey[(sankey["year"] >= min_year) & (sankey["year"] <= max_year)]
    n_years = max_year - min_year + 1
    harvest_rows = w[(w["source"] == "Harvested wood") & w["target"].isin([p for p in pools.values() if p])]
    rows = []
    for species, pool in pools.items():
        i = inc[inc["species"] == species]
        v = vol[vol["species"] == species]
        inc_prod = float(i.loc[i["measure"] == "Skogsareal4", "value"].sum()) * 1000
        inc_total = float(i["value"].sum()) * 1000
        standing = float(v["value"].sum()) * 1000
        h = harvest_rows if pool is None else harvest_rows[harvest_rows["target"] == pool]
        harvest = float(h["volume"].sum()) / n_years
        rows.append({
            "species": species, "interval": str(i["interval"].iloc[0]) if len(i) else "",
            "increment_productive_m3": inc_prod, "increment_total_m3": inc_total,
            "standing_volume_m3": standing, "harvest_mean_m3": harvest,
            "harvest_window": f"{min_year}-{max_year}",
            "harvest_to_increment": harvest / inc_prod if inc_prod > 0 else float("nan"),
            "harvest_to_increment_total": harvest / inc_total if inc_total > 0 else float("nan"),
        })
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch the SSB forest-inventory tables and write data/processed/forest_resource.csv."
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    table = build_forest(write=True)
    print(f"Wrote {len(table)} rows ({table['year_end'].min()}-{table['year_end'].max()}) "
          f"to {config.FOREST_RESOURCE_CSV}")


__all__ = [
    "FOREST_COLUMNS",
    "FOREST_TABLES",
    "MEASURE_LABELS",
    "NATIONAL",
    "NFI_REGIONS",
    "SPECIES_CODES",
    "TABLE_MEASURES",
    "build_forest",
    "build_forest_resource",
    "increment_vs_harvest",
    "interval_end",
    "load_forest_resource",
    "tidy_forest_table",
    "units_from_raw",
]
