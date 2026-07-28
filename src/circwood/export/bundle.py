"""Public-bundle export and its confidentiality gate.

Two things live here. :func:`build_bundle` (Phase D) serialises the flow
table, node positions, dimension lookups and manifest vintage into
``site/data/circwood.json`` — the single file the static site loads. And the
gate every frame must pass before anything is serialised (Phase E):
:func:`assert_publishable`.

The gate is deliberately written *before* any member-level data exists,
because the failure mode — a member's confidential volumes readable in any
browser's devtools — is unrecoverable once shipped. The rules it enforces
are stated in ``docs/DATA_CONFIDENTIALITY.md``; the two that are code, not
prose:

1. **Per-entity rows never ship.** Anything in a public bundle is public, so
   a row with ``entity_id != "national"`` is refused outright — regardless of
   how many entities contribute to its cell. Member views require per-entity
   bundles built server-side behind authentication, never client-side
   filtering of a combined bundle.
2. **Suppression happens at build time.** A published cell derived from
   member data needs at least :data:`MIN_ENTITIES` distinct contributors;
   the refusal message reports how many cells fall short so a build-time
   aggregation step knows what it must suppress.

Today every row in the dataset is ``entity_id == "national"`` (see
``transform/dimensions.py``), so the gate passes everything — inert by
construction, not by being switched off.
"""

from __future__ import annotations

import argparse
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from circwood import config
from circwood.transform.dimensions import FYLKE_CODES, NATIONAL_ENTITY, NATIONAL_REGION

#: Minimum number of distinct entities a published cell must draw on before
#: it may leave the build as an aggregate (docs/DATA_CONFIDENTIALITY.md).
#: A disclosure-policy floor, not a model coefficient — which is why it lives
#: here at the enforcement point rather than in ``constants.py``.
MIN_ENTITIES = 5

#: Columns that carry values or provenance rather than identify a cell.
#: Everything else in a frame (source, target, year, region, species,
#: assortment, ...) keys the cell a browser could read.
_VALUE_COLUMNS = ("volume", "carbon", "origin", "entity_id")


class ConfidentialityError(ValueError):
    """A frame headed for a public bundle would expose member-level data."""


def assert_publishable(
    df: pd.DataFrame, *, min_entities: int = MIN_ENTITIES
) -> pd.DataFrame:
    """Gate a frame headed for a public bundle; return it unchanged if clean.

    Passes national frames through: a frame with no ``entity_id`` column
    (pre-Phase-C shapes) or with every row ``entity_id == "national"`` is
    publishable by definition. Any other row is member-level and raises
    :class:`ConfidentialityError` — see the module docstring for why the
    refusal is unconditional rather than a per-cell count.

    The error message counts rows, entities and under-threshold cells but
    never names an entity: this repository and its CI logs are public, so a
    suppression message that quotes an entity id would itself be a leak.
    """
    if "entity_id" not in df.columns:
        return df
    member = df[df["entity_id"] != NATIONAL_ENTITY]
    if member.empty:
        return df

    cell_key = [c for c in df.columns if c not in _VALUE_COLUMNS]
    if cell_key:
        contributors = member.groupby(cell_key, dropna=False)["entity_id"].nunique()
    else:
        contributors = pd.Series([member["entity_id"].nunique()])
    n_under = int((contributors < min_entities).sum())

    raise ConfidentialityError(
        f"Refusing to publish: {len(member)} member-level row(s) from "
        f"{member['entity_id'].nunique()} distinct entity id(s) in a frame "
        f"headed for a public bundle. Anything in the bundle is readable in "
        f"browser devtools, so per-entity rows never ship — member views need "
        f"per-entity bundles built server-side behind authentication. The "
        f"only public route is aggregation at build time, and "
        f"{n_under} of {len(contributors)} cell(s) here draw on fewer than "
        f"min_entities={min_entities} contributors, so they must be "
        f"suppressed even in aggregate. See docs/DATA_CONFIDENTIALITY.md."
    )


# ---------------------------------------------------------------------------
# Phase D — the bundle builder
# ---------------------------------------------------------------------------

#: Version of the bundle JSON layout, independent of the manifest's.
BUNDLE_SCHEMA_VERSION = 1

#: Canonical display order for the dimension lookups (subset to what the
#: dataset actually contains; the site renders filters in this order).
_SPECIES_ORDER = ("spruce", "pine", "non-coniferous")
_ASSORTMENT_ORDER = ("all", "sawtimber", "pulpwood", "sams", "fuelwood")

#: ``region="NO"`` means *not regionally attributed* (see transform/dimensions),
#: and the site must say so rather than imply "rest of Norway".
_NATIONAL_REGION_LABEL = "National (not regionally attributed)"


def _region_labels() -> dict[str, str]:
    """ISO region code -> display label, inverted from the closed county map."""
    labels = {code: label for label, code in FYLKE_CODES.items()}
    labels[NATIONAL_REGION] = _NATIONAL_REGION_LABEL
    return labels


def _ordered_levels(values: pd.Series, canonical: tuple[str, ...]) -> list[str]:
    """The distinct values of a column, canonical order first, extras appended.

    Extras sort after the canonical block so a new level (a future dataset
    change) still round-trips through the bundle instead of raising here —
    the site just renders it last.
    """
    present = set(values)
    ordered = [v for v in canonical if v in present]
    return ordered + sorted(present - set(canonical))


def _load_flow_table() -> pd.DataFrame:
    """The carbon table — the flow schema plus ``carbon`` — as the site needs it."""
    if not config.CARBON_CSV.exists():
        raise FileNotFoundError(
            f"{config.CARBON_CSV} not found. The bundle is built from the "
            f"committed carbon dataset; run build_carbon(write=True) first."
        )
    df = pd.read_csv(config.CARBON_CSV)
    df.columns = [c.lstrip("﻿").strip() for c in df.columns]
    return df


def build_bundle(
    df: pd.DataFrame | None = None,
    *,
    nodes: list[dict] | None = None,
    out_path: Path | None = None,
    write: bool = True,
) -> dict:
    """Serialise the public dataset into the static site's single data file.

    Reads the committed ``carbon_data.csv`` (the flow schema plus a ``carbon``
    column), ``config/nodes.json`` and ``data/processed/manifest.json``, and
    writes ``site/data/circwood.json`` plus a pre-compressed ``.json.gz``.
    Every frame is routed through :func:`assert_publishable` before anything
    is serialised — the Phase E gate is the only door out of this module.

    The layout is columnar with index-coded categoricals (node, region,
    species, assortment and origin columns are integer indices into the
    bundle's lookup arrays): the whole national dataset serialises to a few
    hundred kilobytes, which is why this is one JSON file and not Parquet or
    a database (roadmap, Phase D). Volumes are rounded to 0.1 m³ and carbon
    to 1 kg C — display precision; the CSVs stay the exact artifact.

    Parameters exist for the tests: ``df`` injects a flow table, ``nodes`` a
    node list, ``write=False`` skips touching the filesystem.
    """
    if df is None:
        df = _load_flow_table()
    df = assert_publishable(df)

    if nodes is None:
        with open(config.NODES_FILE, "r", encoding="utf-8") as fh:
            nodes = json.load(fh)

    labels = [n["label"] for n in nodes]
    node_index = {label: i for i, label in enumerate(labels)}
    flow_nodes = set(df["source"]) | set(df["target"])
    missing = sorted(flow_nodes - set(labels))
    ghosts = sorted(set(labels) - flow_nodes)
    if missing or ghosts:
        # The same exact-coverage policy the golden fence enforces. A missing
        # label would make the site silently drop flows (the remap_ids failure
        # mode that once hid ~5.9 Mm³); a ghost would render an orphan node.
        raise ValueError(
            f"config/nodes.json must cover the dataset's node set exactly: "
            f"missing {missing or 'none'}, ghosts {ghosts or 'none'}."
        )

    if df[["volume", "carbon"]].isna().any().any():
        raise ValueError("Flow table has NaN volume or carbon — refusing to bundle.")

    region_labels = _region_labels()
    regions = [NATIONAL_REGION] + sorted(set(df["region"]) - {NATIONAL_REGION})
    unknown_regions = [r for r in regions if r not in region_labels]
    if unknown_regions:
        raise ValueError(
            f"Region code(s) {unknown_regions} have no label in "
            f"dimensions.FYLKE_CODES — a new region is a reviewed change."
        )
    species = _ordered_levels(df["species"], _SPECIES_ORDER)
    assortments = _ordered_levels(df["assortment"], _ASSORTMENT_ORDER)
    origins = sorted(set(df["origin"]))

    def codes(col: str, levels: list[str]) -> list[int]:
        index = {v: i for i, v in enumerate(levels)}
        return [index[v] for v in df[col]]

    manifest = _read_vintage()

    bundle = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "units": {"volume": "m3", "carbon": "kg C"},
        "year_min": int(df["year"].min()),
        "year_max": int(df["year"].max()),
        # The paper's default analysis window (constants.MIN_YEAR/MAX_YEAR).
        "paper_window": _paper_window(),
        "vintage": manifest,
        "nodes": [
            {
                "label": n["label"],
                "x": n["x_position"],
                "y": n["y_position"],
                "color": n["node_color"],
            }
            for n in nodes
        ],
        "regions": [{"code": r, "label": region_labels[r]} for r in regions],
        "species": species,
        "assortments": assortments,
        "origins": origins,
        "flows": {
            "source": [node_index[s] for s in df["source"]],
            "target": [node_index[t] for t in df["target"]],
            "year": [int(y) for y in df["year"]],
            "region": codes("region", regions),
            "species": codes("species", species),
            "assortment": codes("assortment", assortments),
            "origin": codes("origin", origins),
            "volume": [round(float(v), 1) for v in df["volume"]],
            "carbon": [round(float(c)) for c in df["carbon"]],
        },
        "row_count": int(len(df)),
    }

    if write:
        write_bundle(bundle, out_path or config.BUNDLE_JSON)
    return bundle


def _paper_window() -> list[int]:
    from circwood import constants

    return [constants.MIN_YEAR, constants.MAX_YEAR]


def _read_vintage() -> dict | None:
    """The manifest, reduced to what the site captions: dates, not hashes."""
    from circwood.manifest import read_manifest

    manifest = read_manifest()
    if manifest is None:
        return None
    return {
        "data_as_of": manifest.data_as_of,
        "oldest_table_as_of": manifest.oldest_table_as_of,
        "generated_at": manifest.generated_at,
        "tables": {
            table_id: {"updated": entry.get("updated")}
            for table_id, entry in manifest.tables.items()
        },
    }


def write_bundle(bundle: dict, out_path: Path) -> list[Path]:
    """Write the bundle as JSON and pre-compressed ``.json.gz``.

    The gzip member carries ``mtime=0`` so rebuilding an unchanged bundle is
    byte-identical — a deploy workflow can diff the output meaningfully.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
    out_path.write_text(payload + "\n", encoding="utf-8")
    gz_path = out_path.with_suffix(out_path.suffix + ".gz")
    with gzip.GzipFile(gz_path, "wb", mtime=0) as fh:
        fh.write((payload + "\n").encode("utf-8"))
    return [out_path, gz_path]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the static site's data bundle from the committed dataset."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Output path (default: {config.BUNDLE_JSON}).",
    )
    args = parser.parse_args()
    out = args.out or config.BUNDLE_JSON
    bundle = build_bundle(out_path=out)
    gz = out.with_suffix(out.suffix + ".gz")
    print(
        f"Wrote {bundle['row_count']} flows, {len(bundle['nodes'])} nodes "
        f"({bundle['year_min']}-{bundle['year_max']}) to {out} "
        f"({out.stat().st_size:,} B; {gz.stat().st_size:,} B gz)."
    )
