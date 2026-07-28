"""Golden fence on the json-stat2 -> DataFrame parse step.

``pyjstat.from_json_stat`` is deprecated upstream (it warns and points at
``Dataset.read()``), so at some point this project either migrates or takes a
pyjstat version bump. Either could silently change what
:func:`circwood.ingest.ssb.parse_table` returns — most dangerously the column
*names*, which are Norwegian dimension labels the transforms index by
('sortiment', 'virkesgruppe', 'år'), and the year column's dtype, which is
string and must stay string-shaped (``assemble`` coerces it explicitly; a
silent switch to int would make that coercion look unnecessary and invite its
removal).

The fence: parse the committed raw responses in ``fixtures/ssb/table_*.json``
and compare against the committed parsed snapshots
``fixtures/ssb/parsed_*.csv``, cell for cell.

**If this test fails**, the parse changed. That may be fine — but it is never
incidental. Verify the new output is right, then regenerate the snapshot in the
same commit so the diff is visible in review:

    py -3 -W ignore -c "import pandas as pd; from circwood.ingest import ssb; \\
      [pd.concat(ssb.parse_table(open(f'tests/fixtures/ssb/table_{t}.json', \\
      encoding='utf-8').read(), t), ignore_index=True) \\
      .to_csv(f'tests/fixtures/ssb/parsed_{t}.csv', index=False, \\
      encoding='utf-8', lineterminator='\\n') for t in ('11181', '07410')]"

Values are compared as text so the fence does not also pin pandas' numeric dtype
(which legitimately differs between pandas 2 and 3, and between a table with
NaNs and one without). The dtype *shape* that the transforms actually depend on
is asserted separately below.
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import pandas.testing as pdt
import pytest

pytest.importorskip("pyjstat", reason="needs the optional `ssb` extras")

from circwood.ingest import ssb  # noqa: E402

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ssb"
TABLES = ("11181", "07410")

# The column each table's transforms index the year by, and the dimension
# columns they filter Norwegian labels on. Names, not positions.
EXPECTED_COLUMNS = {
    "11181": ["år", "virkesgruppe", "statistikkvariabel", "value"],
    "07410": ["sortiment", "statistikkvariabel", "år", "value"],
}


def _read_csv_as_text(source) -> pd.DataFrame:
    """Read a CSV with every cell as a plain string, empties included."""
    return pd.read_csv(source, dtype=str, keep_default_na=False, encoding="utf-8")


def _parsed(table_id: str) -> pd.DataFrame:
    raw = (FIXTURE_DIR / f"table_{table_id}.json").read_text(encoding="utf-8")
    return pd.concat(ssb.parse_table(raw, table_id), ignore_index=True)


@pytest.mark.parametrize("table_id", TABLES)
def test_parse_output_matches_golden_snapshot(table_id):
    parsed = _parsed(table_id)
    got = _read_csv_as_text(io.StringIO(parsed.to_csv(index=False)))
    golden = _read_csv_as_text(FIXTURE_DIR / f"parsed_{table_id}.csv")
    pdt.assert_frame_equal(got, golden, obj=f"parsed table {table_id}")


@pytest.mark.parametrize("table_id", TABLES)
def test_parse_column_names_are_the_labels_the_transforms_index_by(table_id):
    assert list(_parsed(table_id).columns) == EXPECTED_COLUMNS[table_id]


@pytest.mark.parametrize("table_id", TABLES)
def test_value_is_numeric_and_year_is_not(table_id):
    """The dtype contract `assemble` depends on, without pinning pandas' exact dtype."""
    parsed = _parsed(table_id)
    assert pd.api.types.is_numeric_dtype(parsed["value"])
    # json-stat category labels are strings. `assemble._prepare_sawing_inputs`
    # coerces the year to int precisely because of this; if pyjstat ever starts
    # handing back integers, that coercion stops being load-bearing and someone
    # will delete it. Fail here first.
    assert pd.api.types.is_string_dtype(parsed["år"])
    assert not pd.api.types.is_numeric_dtype(parsed["år"])
