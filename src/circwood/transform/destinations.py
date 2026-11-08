"""The ``destination`` dimension: where exported wood goes (SSB 08799).

The flow table says how much wood leaves Norway — the ``Export *`` and ``Net
export *`` nodes — but not where it goes. SSB table **08799** is the
by-partner-country sibling of the 08801 trade table the pipeline already uses:
the same HS commodity codes and the same ``Mengde 2`` (m³) measure, monthly,
with a ``Land`` dimension whose codes are ISO 3166-1 alpha-2. This module
turns that into one more filter dimension on the flow schema::

    destination   "domestic"      the row is not an export flow
                  "SE", "DE", …   ISO 3166-1 alpha-2 partner country
                  "unspecified"   exported, partner not published (SSB code 97)

Summing ``volume`` over ``destination`` recovers the row exactly, like every
other dimension (``dimensions.collapse_national``).

How the split is made
---------------------
An export edge's national volume for a year is split across partners by each
partner's **share** of the 08799 export volume of the same trade type and
year. Shares rather than figures, for two reasons:

* Raw sawlogs, pulpwood and sawn timber are *netted* against imports in the
  assembled dataset (``assemble.NET_EXPORT_PRODUCTS``); the netted volume has
  no partner-level counterpart in any source, so attributing it by the gross
  export mix is the only defensible reading — and it is documented here as a
  modelling assumption, not source data.
* For the gross export edges (firewood, planed wood, …) the shares applied to
  the national figure reproduce the 08799 figures exactly whenever the two
  tables agree, and degrade gracefully to a proportional split when the
  monthly table has been revised and the annual one not yet (or vice versa).
  :func:`reconcile_partner_exports` reports that disagreement instead of
  hiding it.

A year/type with export volume in the dataset but no partner rows at all
stays a single row with ``destination="unspecified"``: the volume is still
exported, we just cannot say where. Net-*import* years (the reversed
``Net export * -> mill`` edge) are ``"domestic"`` — the wood ends up in Norway.

What this is not
----------------
It is not FAOSTAT. FAOSTAT's bilateral *Forestry Trade Flows* (FT) was
discontinued in 2022 and its REST API now requires an account, so the
partner question is answered from SSB, which covers the whole dataset window
on the same pipeline. FAOSTAT's open bulk files remain a possible cross-check
of Norway's export *totals*, nothing more.
"""

from __future__ import annotations

import logging

import pandas as pd

from circwood import config
from circwood.ingest.trade import EXPORT_NODES_BY_TYPE, classify_varenummer

log = logging.getLogger(__name__)

DESTINATION_COLUMN = "destination"

#: Not an export flow: the wood stays in (or, for a net-import year, comes to) Norway.
DOMESTIC = "domestic"

#: Exported, partner not published. SSB's own residual (``Land`` code 97,
#: 'Uspesifisert'), and the fallback for an export edge with no partner data.
UNSPECIFIED = "unspecified"

#: SSB ``Land`` codes that are not a country. Mapped rather than refused: the
#: residual is part of the published total and must stay in the split.
_SSB_RESIDUAL_CODES = {"97": UNSPECIFIED}

#: Columns of the committed ``data/raw/exports_by_partner.csv``.
PARTNER_COLUMNS = ["year", "type", "destination", "destination_label", "volume"]

#: Assembled-dataset node -> trade type (the inverse of EXPORT_NODES_BY_TYPE).
_TYPE_BY_EXPORT_NODE = {
    node: trade_type
    for trade_type, nodes in EXPORT_NODES_BY_TYPE.items()
    for node in nodes
}

_M3_LABEL = "Mengde 2 (M2)"
_EXPORT_LABEL = "Eksport"


def build_partner_exports(
    chunks: list[pd.DataFrame], land_codes: dict[str, str]
) -> pd.DataFrame:
    """Tidy the parsed 08799 chunks into the committed partner-export frame.

    ``chunks`` are the pyjstat frames of the ``08799_*`` query files (columns
    ``måned, varenummer, land, import/eksport, statistikkvariabel, value``);
    ``land_codes`` maps the ``land`` *labels* back to SSB's codes (ISO alpha-2),
    as :func:`circwood.ingest.ssb.dimension_codes` returns them. Months are
    summed to years, commodities to trade types, and zero/missing cells
    dropped. Output: :data:`PARTNER_COLUMNS`, sorted for a reviewable diff.
    An unknown partner label raises — a partner is a key, not a guess.
    """
    df = pd.concat(chunks, ignore_index=True)
    df = df[(df["statistikkvariabel"] == _M3_LABEL) & (df["import/eksport"] == _EXPORT_LABEL)].copy()
    df["volume"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["volume"])
    df = df[df["volume"] != 0]

    unknown = sorted(set(df["land"]) - set(land_codes))
    if unknown:
        raise ValueError(
            f"SSB 08799 returned partner label(s) with no code in the json-stat2 "
            f"document: {unknown}. Refusing to invent a destination."
        )

    df["type"] = classify_varenummer(df["varenummer"])
    df = df[df["type"] != "default"]
    codes = df["land"].map(land_codes)
    df["destination"] = codes.map(lambda c: _SSB_RESIDUAL_CODES.get(c, c))
    df["destination_label"] = df["land"].where(
        ~codes.isin(_SSB_RESIDUAL_CODES), UNSPECIFIED
    )
    df["year"] = pd.to_numeric(df["måned"].astype(str).str[:4]).astype(int)

    out = (
        df.groupby(["year", "type", "destination", "destination_label"], as_index=False)["volume"]
        .sum()
    )
    out = out[out["volume"] > 0]
    return out.sort_values(["year", "type", "destination"]).reset_index(drop=True)[PARTNER_COLUMNS]


def load_partner_exports() -> pd.DataFrame | None:
    """The committed partner-export frame, or ``None`` if none is committed."""
    if not config.EXPORTS_PARTNER_CSV.exists():
        return None
    return pd.read_csv(config.EXPORTS_PARTNER_CSV, keep_default_na=False)


def reconcile_partner_exports(
    partners: pd.DataFrame, national_exports: pd.DataFrame, *, tolerance: float = 0.02
) -> pd.DataFrame:
    """Compare 08799 export totals (summed over partners) with the 08801 ones.

    ``national_exports`` has columns ``year, type, volume`` (gross m³ exports
    per trade type from the 08801 pull). Returns one row per (year, type) with
    both totals and their relative difference, and logs a warning for every
    pair outside ``tolerance``. Informational: the two tables are published on
    different schedules and are revised separately, so a gap is a vintage
    note for the manifest, not a failed gate — the destination split uses
    shares, so the dataset's national totals are unaffected either way.
    """
    a = partners.groupby(["year", "type"], as_index=False)["volume"].sum().rename(columns={"volume": "partner_total"})
    b = national_exports.groupby(["year", "type"], as_index=False)["volume"].sum().rename(columns={"volume": "national_total"})
    merged = a.merge(b, on=["year", "type"], how="outer").fillna(0.0)
    denom = merged["national_total"].where(merged["national_total"] != 0, merged["partner_total"])
    merged["rel_diff"] = ((merged["partner_total"] - merged["national_total"]) / denom.where(denom != 0)).fillna(0.0)
    off = merged[merged["rel_diff"].abs() > tolerance]
    for r in off.itertuples(index=False):
        log.warning(
            "08799 vs 08801 export totals differ for %s %s: %.0f vs %.0f m3 (%+.1f%%)",
            r.year, r.type, r.partner_total, r.national_total, 100 * r.rel_diff,
        )
    return merged.sort_values(["year", "type"]).reset_index(drop=True)


def destination_for_edge(source: str, target: str) -> str | None:
    """The trade type whose partners split this edge, or ``None`` (domestic).

    Only edges *into* an export node are split; the reversed net-import edge
    (``Net export * -> mill``) has the export node as its source and stays
    domestic.
    """
    return _TYPE_BY_EXPORT_NODE.get(target)


def add_destination(wide: pd.DataFrame, partners: pd.DataFrame | None) -> pd.DataFrame:
    """Add the ``destination`` column to a widened flow table.

    ``wide`` is the Phase C table (one row per region/species/assortment
    slice). Every row is ``"domestic"`` except rows into an export node, which
    are split by partner share of the same (year, trade type) in ``partners``
    (see :func:`build_partner_exports`); with no partner data for that year
    and type the row stays whole as ``"unspecified"``. With ``partners`` None
    or empty the column is added as all-domestic, so a checkout without the
    committed partner file still builds. Row order is preserved, partner rows
    in code order, so the committed CSV diff stays reviewable. Idempotent.
    """
    if DESTINATION_COLUMN in wide.columns:
        return wide.copy()

    wide = wide.reset_index(drop=True).copy()
    wide[DESTINATION_COLUMN] = DOMESTIC
    types = [destination_for_edge(s, t) for s, t in zip(wide["source"], wide["target"])]
    wide["dest_type"] = types
    mask = wide["dest_type"].notna()
    if not mask.any():
        return wide.drop(columns=["dest_type"])

    shares_by_key: dict[tuple[int, str], pd.DataFrame] = {}
    if partners is not None and len(partners):
        p = partners.copy()
        p["year"] = p["year"].astype(int)
        for key, g in p.groupby(["year", "type"]):
            total = float(g["volume"].sum())
            if total <= 0:
                continue
            shares_by_key[key] = (
                g.assign(share=g["volume"] / total)
                .sort_values("destination")[["destination", "share"]]
            )

    wide["row_order"] = range(len(wide))
    wide["sub_order"] = 0
    pieces = [wide[~mask]]
    for r in wide[mask].itertuples():
        shares = shares_by_key.get((int(r.year), r.dest_type))
        if shares is None:
            row = wide.loc[[r.Index]].copy()
            row[DESTINATION_COLUMN] = UNSPECIFIED
            pieces.append(row)
            continue
        piece = pd.concat([wide.loc[[r.Index]]] * len(shares), ignore_index=True)
        piece["volume"] = float(r.volume) * shares["share"].to_numpy()
        piece[DESTINATION_COLUMN] = shares["destination"].to_numpy()
        piece["sub_order"] = range(1, len(shares) + 1)
        pieces.append(piece[piece["volume"] > 0])

    return (
        pd.concat(pieces, ignore_index=True)
        .sort_values(["row_order", "sub_order"], kind="stable")
        .drop(columns=["row_order", "sub_order", "dest_type"])
        .reset_index(drop=True)
    )


__all__ = [
    "DESTINATION_COLUMN",
    "DOMESTIC",
    "PARTNER_COLUMNS",
    "UNSPECIFIED",
    "add_destination",
    "build_partner_exports",
    "destination_for_edge",
    "load_partner_exports",
    "reconcile_partner_exports",
]
