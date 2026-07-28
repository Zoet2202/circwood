"""Phase E — the confidentiality gate on the public-bundle export path.

What these tests guarantee (docs/DATA_CONFIDENTIALITY.md):

* the shipped, all-national dataset passes the gate untouched — the guard is
  inert today by construction, not by being switched off;
* any row with a non-national ``entity_id`` is refused outright, no matter how
  many entities contribute to its cell (anything in a public bundle is
  readable in devtools, so per-entity rows never ship);
* the refusal reports how many cells fall below the ``min_entities``
  disclosure floor, so a build-time aggregation step knows what it must
  suppress;
* the refusal never names an entity — this repo's CI logs are public, so a
  suppression message that quotes an id would itself be a leak.

All entity ids here are synthetic; real member data never enters the repo.
"""

from __future__ import annotations

import pandas as pd
import pytest

from circwood import build_sankey
from circwood.export.bundle import (
    MIN_ENTITIES,
    ConfidentialityError,
    assert_publishable,
)
from circwood.transform.dimensions import NATIONAL_ENTITY


def member_rows(entity_ids, year=2020):
    """A synthetic flow cell with one row per contributing entity."""
    return pd.DataFrame({
        "source": "Sawmills", "target": "Pulpmills", "volume": 1000.0,
        "year": year, "origin": "member agreement", "region": "NO",
        "species": "spruce", "assortment": "all",
        "entity_id": list(entity_ids),
    })


def test_policy_default_is_five():
    assert MIN_ENTITIES == 5


def test_shipped_dataset_is_publishable():
    """Every row is national today, so the gate passes the artifact unchanged."""
    df = build_sankey(use_cached_ssb=True)
    assert assert_publishable(df) is df


def test_frame_without_entity_column_passes():
    """Pre-Phase-C national frames carry no entity_id and are fine."""
    df = pd.DataFrame({
        "source": ["A"], "target": ["B"], "volume": [1.0],
        "year": [2020], "origin": ["t"],
    })
    assert assert_publishable(df) is df


def test_single_member_row_is_refused():
    df = member_rows(["mill-001"])
    with pytest.raises(ConfidentialityError, match="never ship"):
        assert_publishable(df)


def test_member_rows_refused_even_with_enough_contributors():
    """min_entities licenses aggregates, never the per-entity rows themselves."""
    df = member_rows([f"mill-{i:03d}" for i in range(MIN_ENTITIES + 2)])
    with pytest.raises(ConfidentialityError):
        assert_publishable(df)


def test_national_rows_do_not_excuse_member_rows():
    national = member_rows([NATIONAL_ENTITY])
    mixed = pd.concat([national, member_rows(["mill-001"])], ignore_index=True)
    with pytest.raises(ConfidentialityError, match=r"1 member-level row"):
        assert_publishable(mixed)


def test_refusal_never_names_an_entity():
    """CI logs are public: the message reports counts, never ids."""
    df = member_rows(["mill-secret-001", "mill-secret-002"])
    with pytest.raises(ConfidentialityError) as err:
        assert_publishable(df)
    assert "mill-secret" not in str(err.value)
    assert "2 distinct entity id(s)" in str(err.value)


def test_refusal_counts_under_threshold_cells():
    """One cell with 6 contributors, one with 2: only the small one is below
    the default floor, and the message says so."""
    df = pd.concat(
        [
            member_rows([f"mill-{i:03d}" for i in range(6)], year=2020),
            member_rows(["mill-900", "mill-901"], year=2021),
        ],
        ignore_index=True,
    )
    with pytest.raises(ConfidentialityError, match=r"1 of 2 cell\(s\)") as err:
        assert_publishable(df)
    assert f"min_entities={MIN_ENTITIES}" in str(err.value)


def test_min_entities_override_changes_the_cell_count_only():
    """A lower floor clears the small cell from the count — but the frame is
    still refused, because per-entity rows never ship."""
    df = member_rows(["mill-900", "mill-901"], year=2021)
    with pytest.raises(ConfidentialityError, match=r"0 of 1 cell\(s\)"):
        assert_publishable(df, min_entities=2)
