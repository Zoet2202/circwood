"""Public-bundle export and its confidentiality gate.

This module is where Phase D's bundle builder lands: the function that
serialises the flow table, node positions, palette and manifest into
``site/data/circwood.json`` for the static site. What exists today is the
gate that builder must route every frame through before serialising anything
(Phase E): :func:`assert_publishable`.

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

import pandas as pd

from circwood.transform.dimensions import NATIONAL_ENTITY

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
