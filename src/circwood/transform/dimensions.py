"""Filter dimensions for the flow schema (Phase C).

The flow table used to be implicitly national and all-species: one row per
``(source, target, volume, year, origin)``. This module widens it with the four
dimensions the public site filters on, so a row is now

    (source, target, volume, year, origin, region, species, assortment, entity_id,
     destination)

and the old table is recovered exactly by summing volume over the new columns
(see :func:`collapse_national`).

The dimensions
--------------
``region``
    ISO-style region code: ``"NO-31"`` (Østfold) … ``"NO-56"`` (Finnmark) on
    the harvest front edge, ``"NO"`` everywhere else. ``"NO"`` means *not
    regionally attributed*, not "rest of Norway": SSB publishes harvest by
    county (03795 via the ``agg_KommFylker`` codelist — the 2024 county
    boundaries backcast over the whole series, immune to the 2018–2024 county
    reforms), but the ``Harvested wood -> pool`` edges also carry the
    nationally *estimated* firewood component (11181), which SSB does not
    regionalise. So each harvest edge splits into exact per-county rows (pure
    SSB 03795 figures, bit-for-bit) plus one ``"NO"`` remainder row for the
    firewood estimate; downstream flows are all ``"NO"`` because no mill-level
    or regional processing data exists publicly. Filtering by a county
    therefore answers "what does this county contribute to the national
    pools", which is the question the source data can support.
``species``
    ``spruce`` / ``pine`` / ``non-coniferous``. Rows whose wood is a mix (all
    flows downstream of the mills) are **split** into one row per species using
    the same fixed-point propagation the carbon model has always used
    (:func:`circwood.carbon.species_model.flow_species_shares`) — so the split
    is a model result made explicit, not new source data. Flows out of the
    single-species pools and the ``Harvested wood -> pool`` rows are pure and
    keep their volume bit-for-bit. Import and net-export nodes carry the shares
    configured in ``config/carbon_nodes.csv`` (imports are assumed 100 % spruce
    there — a documented assumption, see Phase G).
``assortment``
    Roundwood assortment where the source table distinguishes it:
    ``sawtimber`` (sagtømmer), ``pulpwood`` (massevirke), ``fuelwood`` (ved til
    brensel), and ``sams`` (SSB 07410 'sams sagtømmer og massevirke', a mixed
    sawtimber/pulpwood grade that feeds Boardmills). ``all`` everywhere the
    distinction has no meaning (processed goods and downstream flows).
``entity_id``
    Always ``"national"`` today. The column exists because mill-level data does
    not exist publicly and would require a member-level data agreement with
    Treindustrien — but when it arrives, it must land as a *data* change, not a
    schema rewrite of every transform, test and site filter. Confidentiality
    rules for any non-national entity_id are Phase E; ``export.bundle`` must
    refuse to ship such rows without a min-entities guard.
``destination``
    Where exported wood goes: ``"domestic"`` for every row that is not an
    export flow, an ISO 3166-1 alpha-2 partner code on the rows into the
    ``Export *`` / ``Net export *`` nodes, ``"unspecified"`` where SSB does not
    publish the partner. Data: SSB 08799, the by-country sibling of the 08801
    trade table; split by partner *share* because the netted export edges have
    no partner-level figure to be exact against. Added after Phase E, on the
    same additive terms; see :mod:`circwood.transform.destinations`.

Splitting a flow across a dimension never changes its national total: shares
sum to 1 by construction, and zero-share rows are dropped rather than written.
"""

from __future__ import annotations

import pandas as pd

from circwood import config
from circwood.carbon.species_model import (
    POOL_SPECIES,
    SPECIES,
    STANDING_WOOD,
    flow_species_shares,
)
from circwood.transform.destinations import add_destination, load_partner_exports

#: The dimension columns, in schema order (Phase C's four, then ``destination``
#: — appended, never reordered, per the additive-schema house rule).
DIMENSION_COLUMNS = ["region", "species", "assortment", "entity_id", "destination"]

#: Full flow schema (national columns first — additive, never reordered).
FLOW_COLUMNS = ["source", "target", "volume", "year", "origin"] + DIMENSION_COLUMNS

NATIONAL_REGION = "NO"
NATIONAL_ENTITY = "national"

#: Degenerate value for a dimension the source data does not distinguish.
ALL = "all"

_SPECIES_POOLS = ("Spruce", "Pine", "Non-coniferous")

#: SSB agg_KommFylker region label -> ISO 3166-2:NO code (2024 counties; the
#: codelist backcasts the whole series into these boundaries). Deliberately a
#: closed map: a label SSB adds or renames raises rather than silently minting
#: a region — the same policy as a new node, because a new region is a
#: modelling and presentation decision that wants a human.
FYLKE_CODES = {
    "Østfold": "NO-31",
    "Akershus": "NO-32",
    "Oslo - Oslove": "NO-03",
    "Innlandet": "NO-34",
    "Buskerud": "NO-33",
    "Vestfold": "NO-39",
    "Telemark": "NO-40",
    "Agder": "NO-42",
    "Rogaland": "NO-11",
    "Vestland": "NO-46",
    "Møre og Romsdal": "NO-15",
    "Trøndelag - Trööndelage": "NO-50",
    "Nordland - Nordlánnda": "NO-18",
    "Troms - Romsa - Tromssa": "NO-55",
    "Finnmark - Finnmárku - Finmarkku": "NO-56",
    "Svalbard": "NO-21",
    "Jan Mayen": "NO-22",
    # No ISO code exists for this SSB residual category.
    "Kontinentalsokkelen og uoppgitt": "NO-UNK",
}

#: 03795 sortiment label -> the species pool its volume feeds.
_SORTIMENT_POOLS = {"Gran": "Spruce", "Furu": "Pine", "Lauv": "Non-coniferous"}


def build_regional_harvest(tbl_03795_fylke: pd.DataFrame) -> pd.DataFrame:
    """Tidy the parsed 03795 county table into the committed regional frame.

    Input: the pyjstat frame for the ``03795_fylke`` query (columns ``år``,
    ``sortiment``, ``region``, ``value``). Output columns:
    ``[year, region, region_label, target, volume]`` with ``target`` the
    species-pool label, zero/missing cells dropped, and ``region`` the ISO
    code from :data:`FYLKE_CODES` (an unknown label raises).
    """
    df = tbl_03795_fylke
    sub = df[df["sortiment"].isin(_SORTIMENT_POOLS)].copy()
    sub["volume"] = pd.to_numeric(sub["value"], errors="coerce")
    sub = sub.dropna(subset=["volume"])
    sub = sub[sub["volume"] > 0]

    unknown = sorted(set(sub["region"]) - set(FYLKE_CODES))
    if unknown:
        raise ValueError(
            f"SSB 03795 (agg_KommFylker) returned region label(s) not in "
            f"dimensions.FYLKE_CODES: {unknown}. A new or renamed county is a "
            f"presentation decision — add it to the map (and to the site's "
            f"region filter) deliberately."
        )

    out = pd.DataFrame({
        "year": pd.to_numeric(sub["år"]).astype(int),
        "region": sub["region"].map(FYLKE_CODES),
        "region_label": sub["region"],
        "target": sub["sortiment"].map(_SORTIMENT_POOLS),
        "volume": sub["volume"].astype(float),
    })
    return out.sort_values(["year", "target", "region"]).reset_index(drop=True)


def load_regional_harvest() -> pd.DataFrame | None:
    """The committed county-harvest frame, or ``None`` if none is committed."""
    if not config.HARVEST_FYLKE_CSV.exists():
        return None
    return pd.read_csv(config.HARVEST_FYLKE_CSV)


def merge_origins(origins) -> str:
    """Union the ``' | '``-separated source tags of several origin strings.

    Token-based on purpose: collapsing a county row (``'SSB table 03795'``)
    with the national remainder (``'SSB table 03795 | SSB table 11181'``) must
    give back the remainder's string, not concatenate the two.
    """
    tokens: set[str] = set()
    for origin in origins:
        tokens.update(part.strip() for part in str(origin).split("|"))
    return " | ".join(sorted(tokens))


def assortment_for_edge(source: str, target: str) -> str:
    """Roundwood assortment of an edge, ``"all"`` where none is distinguished.

    Only edges whose volume comes from an assortment-split source table get a
    label: the 07410 pool outflows (sagtømmer / massevirke / sams), their
    netted trade counterparts, and the firewood stream (11181 / 03795 'ved til
    brensel'). Everything downstream of a mill is processed wood, where the
    roundwood assortment has no meaning.
    """
    if source == "Firewood" or target == "Firewood":
        return "fuelwood"
    if source in _SPECIES_POOLS:
        if target in ("Sawmills", "Net export skurlast"):
            return "sawtimber"
        if target in ("Pulpmills", "Net export massevirke"):
            return "pulpwood"
        if target == "Boardmills":
            return "sams"
    # Net-import years reverse the net-export edges (see assemble._apply_net_exports).
    if source == "Net export skurlast":
        return "sawtimber"
    if source == "Net export massevirke":
        return "pulpwood"
    return ALL


def add_dimensions(
    df: pd.DataFrame,
    model=None,
    regional: pd.DataFrame | None = None,
    partners: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Widen a national flow table with the filter dimension columns.

    ``regional`` is the county-harvest frame (see :func:`build_regional_harvest`);
    it defaults to the committed ``data/raw/harvest_by_fylke.csv`` and may be
    absent, in which case every row stays ``region="NO"``. ``partners`` is the
    partner-export frame (see :func:`destinations.build_partner_exports`),
    defaulting to the committed ``data/raw/exports_by_partner.csv``; absent,
    every row stays ``destination="domestic"``.

    Idempotent: a frame that already carries every dimension column is returned
    unchanged. Row order follows the input's national row order, with a split
    row's species in canonical order (spruce, pine, non-coniferous) and a
    regionalised harvest row's counties in code order before the national
    remainder, so the committed CSV diff stays reviewable.
    """
    if all(col in df.columns for col in DIMENSION_COLUMNS):
        return df.copy()

    shares = flow_species_shares(df, model)
    row_pos = pd.Series(range(len(df)), index=df.index)

    parts = []
    for order, sp in enumerate(SPECIES):
        keep = shares[sp] > 0
        if not keep.any():
            continue
        part = df.loc[keep].copy()
        part["volume"] = part["volume"] * shares.loc[keep, sp]
        part["species"] = sp
        # Ordering helpers; no leading underscore because itertuples() renames
        # underscore-prefixed columns to positional fields.
        part["row_order"] = row_pos.loc[keep].to_numpy()
        part["species_order"] = order
        parts.append(part)

    wide = (
        pd.concat(parts, ignore_index=True)
        .sort_values(["row_order", "species_order"], kind="stable")
        .drop(columns=["species_order"])
        .reset_index(drop=True)
    )
    wide["region"] = NATIONAL_REGION
    wide["assortment"] = [
        assortment_for_edge(s, t) for s, t in zip(wide["source"], wide["target"])
    ]
    wide["entity_id"] = NATIONAL_ENTITY

    if regional is None:
        regional = load_regional_harvest()
    wide = _apply_regional_harvest(wide, regional)
    if partners is None:
        partners = load_partner_exports()
    wide = add_destination(wide, partners)
    return wide[FLOW_COLUMNS]


def _apply_regional_harvest(
    wide: pd.DataFrame, regional: pd.DataFrame | None
) -> pd.DataFrame:
    """Split the ``Harvested wood -> pool`` rows by county.

    Each harvest edge becomes the exact per-county SSB 03795 figures plus one
    ``"NO"`` remainder row carrying what SSB only estimates nationally (the
    11181 firewood component). The split is exact by construction: county rows
    are the published figures bit-for-bit and the remainder is the national
    volume minus their sum. A materially negative remainder means the county
    and national pulls disagree on the 03795 figures themselves — a vintage
    mismatch that must fail loudly rather than clamp.
    """
    if regional is None or len(regional) == 0:
        return wide.drop(columns=["row_order"], errors="ignore")

    wide = wide.reset_index(drop=True)
    if "row_order" not in wide.columns:
        wide["row_order"] = range(len(wide))
    wide["sub_order"] = 0

    mask = (wide["source"] == STANDING_WOOD) & wide["target"].isin(POOL_SPECIES)
    pieces = [wide[~mask]]
    for r in wide[mask].itertuples():
        sub = regional[(regional["year"] == r.year) & (regional["target"] == r.target)]
        if sub.empty:
            pieces.append(wide.loc[[r.Index]])
            continue
        counties = sub.sort_values("region")
        remainder = float(r.volume) - float(counties["volume"].sum())
        if remainder < -0.5:
            raise ValueError(
                f"County harvest exceeds the national figure for "
                f"{r.target} in {r.year} by {-remainder:,.0f} m³. The 03795 "
                f"national and county (agg_KommFylker) pulls disagree — "
                f"refresh both from the same vintage before rebuilding."
            )
        piece = pd.DataFrame({
            "source": r.source,
            "target": r.target,
            "volume": counties["volume"].to_numpy(dtype=float),
            "year": r.year,
            "origin": "SSB table 03795",
            "region": counties["region"].to_numpy(),
            "species": r.species,
            "assortment": r.assortment,
            "entity_id": r.entity_id,
            "row_order": r.row_order,
            "sub_order": range(1, len(counties) + 1),
        })
        pieces.append(piece)
        if remainder > 0:
            rem = wide.loc[[r.Index]].copy()
            rem["volume"] = remainder
            rem["sub_order"] = len(counties) + 1
            pieces.append(rem)

    return (
        pd.concat(pieces, ignore_index=True)
        .sort_values(["row_order", "sub_order"], kind="stable")
        .drop(columns=["row_order", "sub_order"])
        .reset_index(drop=True)
    )


def collapse_national(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate a widened table back to the national schema.

    The exact inverse of :func:`add_dimensions` up to float summation: one row
    per (year, source, target) with volumes summed over every dimension and
    origins merged the same way ``assemble.dedupe_flows`` merges them. A frame
    without dimension columns passes through untouched (bar column order), so
    pre-Phase-C frames and fixtures keep working.
    """
    national = [c for c in df.columns if c not in DIMENSION_COLUMNS]
    collapsed = (
        df[national]
        .groupby(["year", "source", "target"], as_index=False)
        .agg(volume=("volume", "sum"), origin=("origin", merge_origins))
    )
    return collapsed[["source", "target", "volume", "year", "origin"]]
