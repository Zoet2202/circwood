"""The ``destination`` dimension — where exported wood goes (SSB 08799).

What these tests guarantee:

* the shipped dataset carries ``destination``: ``"domestic"`` on every
  non-export row, an ISO alpha-2 code or ``"unspecified"`` on export rows, and
  nothing else;
* splitting by destination never changes a row's volume (shares sum to one),
  so every consumer that aggregates is unaffected;
* the 08799 tidy step maps partner *labels* back to SSB's codes and refuses to
  guess at an unknown one; months sum to years, commodities to the same trade
  types the 08801 pull uses;
* the chunked 08799 query files satisfy a per-chunk contract (monthly period,
  chunk year bounds clipped to the dataset window);
* with no partner data at all the pipeline still builds (all-domestic).
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from circwood import build_sankey, config
from circwood import constants as C
from circwood.ingest import ssb
from circwood.ingest.trade import EXPORT_NODES_BY_TYPE, TRADE_MAPPING, classify_varenummer
from circwood.transform.destinations import (
    DOMESTIC,
    PARTNER_COLUMNS,
    UNSPECIFIED,
    add_destination,
    build_partner_exports,
    destination_for_edge,
    reconcile_partner_exports,
)
from circwood.transform.dimensions import add_dimensions, collapse_national

EXPORT_NODES = {n for nodes in EXPORT_NODES_BY_TYPE.values() for n in nodes}


@pytest.fixture(scope="module")
def shipped() -> pd.DataFrame:
    return build_sankey(use_cached_ssb=True)


# ---------------------------------------------------------------------------
# The shipped dataset
# ---------------------------------------------------------------------------
def test_shipped_destinations_are_well_formed(shipped):
    is_export = shipped["target"].isin(EXPORT_NODES)
    assert (shipped.loc[~is_export, "destination"] == DOMESTIC).all()
    export_dests = set(shipped.loc[is_export, "destination"])
    assert DOMESTIC not in export_dests
    for code in export_dests - {UNSPECIFIED}:
        assert len(code) == 2 and code.isupper(), code  # ISO 3166-1 alpha-2


def test_destination_split_preserves_national_totals(shipped):
    """Summing over destination (with the other dimensions) is the national table."""
    national = collapse_national(shipped)
    again = collapse_national(add_dimensions(national))
    a = national.sort_values(["year", "source", "target"]).reset_index(drop=True)
    b = again.sort_values(["year", "source", "target"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, rtol=1e-9)


def test_every_type_with_a_node_maps_back():
    """EXPORT_NODES_BY_TYPE keys are TRADE_MAPPING types; every node maps back."""
    assert set(EXPORT_NODES_BY_TYPE) <= set(TRADE_MAPPING)
    for trade_type, nodes in EXPORT_NODES_BY_TYPE.items():
        for node in nodes:
            assert destination_for_edge("anything", node) == trade_type
    # Reversed net-import edges have the export node as *source*: domestic.
    assert destination_for_edge("Net export skurlast", "Sawmills") is None


# ---------------------------------------------------------------------------
# add_destination on synthetic frames
# ---------------------------------------------------------------------------
def _wide(rows):
    df = pd.DataFrame(rows, columns=["source", "target", "volume", "year"])
    df["origin"] = "test"
    df["region"], df["species"], df["assortment"], df["entity_id"] = "NO", "spruce", "all", "national"
    return df


def _partners(rows):
    return pd.DataFrame(rows, columns=PARTNER_COLUMNS)


def test_split_by_share_and_defaults():
    wide = _wide([
        ("Spruce", "Net export skurlast", 1000.0, 2020),
        ("Sawmills", "Net export sawn timber", 500.0, 2020),   # no partner data
        ("Spruce", "Sawmills", 900.0, 2020),                   # not an export
        ("Net export skurlast", "Sawmills", 50.0, 2013),       # net-import year
    ])
    partners = _partners([
        (2020, "saw wood", "SE", "Sverige", 300.0),
        (2020, "saw wood", "DE", "Tyskland", 100.0),
    ])
    out = add_destination(wide, partners)
    skur = out[out["target"] == "Net export skurlast"].set_index("destination")["volume"]
    assert skur.to_dict() == {"DE": 250.0, "SE": 750.0}  # shares 1/4, 3/4; code order
    assert out.loc[out["target"] == "Net export sawn timber", "destination"].tolist() == [UNSPECIFIED]
    assert out.loc[out["target"] == "Sawmills", "destination"].tolist() == [DOMESTIC, DOMESTIC]
    assert out["volume"].sum() == pytest.approx(wide["volume"].sum())
    # Idempotent.
    pd.testing.assert_frame_equal(add_destination(out, partners), out)


def test_no_partner_file_means_all_domestic_but_exports_unspecified():
    wide = _wide([("Firewood", "Export Firewood", 10.0, 2020), ("Spruce", "Sawmills", 1.0, 2020)])
    out = add_destination(wide, None)
    assert out["destination"].tolist() == [UNSPECIFIED, DOMESTIC]


def test_row_order_is_preserved_with_partner_rows_in_place():
    wide = _wide([
        ("Spruce", "Sawmills", 1.0, 2020),
        ("Firewood", "Export Firewood", 10.0, 2020),
        ("Pine", "Sawmills", 2.0, 2020),
    ])
    partners = _partners([(2020, "firewood", "SE", "Sverige", 1.0), (2020, "firewood", "DK", "Danmark", 1.0)])
    out = add_destination(wide, partners)
    assert out["source"].tolist() == ["Spruce", "Firewood", "Firewood", "Pine"]
    assert out["destination"].tolist() == [DOMESTIC, "DK", "SE", DOMESTIC]


# ---------------------------------------------------------------------------
# build_partner_exports (the 08799 tidy step)
# ---------------------------------------------------------------------------
def _chunk(rows):
    return pd.DataFrame(
        rows, columns=["måned", "varenummer", "land", "import/eksport", "statistikkvariabel", "value"]
    )


LAND = {"Sverige": "SE", "Tyskland": "DE", "Uspesifisert": "97"}
SKUR = "Skurtømmer av bartrær (1988-2015) (M1=kg,  M2=m³)"
VED = "Ved til brensel, av bartrær (M1=kg, M2=m³)"


def test_build_partner_exports_sums_months_maps_codes_and_types():
    chunks = [
        _chunk([
            ("2019M01", SKUR, "Sverige", "Eksport", "Mengde 2 (M2)", 10),
            ("2019M02", SKUR, "Sverige", "Eksport", "Mengde 2 (M2)", 5),
            ("2019M01", SKUR, "Sverige", "Eksport", "Mengde 1 (M1)", 9999),  # kg, dropped
            ("2019M01", SKUR, "Tyskland", "Import", "Mengde 2 (M2)", 7),     # import, dropped
            ("2019M03", SKUR, "Uspesifisert", "Eksport", "Mengde 2 (M2)", 1),
            ("2019M03", VED, "Tyskland", "Eksport", "Mengde 2 (M2)", 0),     # zero, dropped
        ]),
        _chunk([("2020M12", VED, "Tyskland", "Eksport", "Mengde 2 (M2)", 3)]),
    ]
    out = build_partner_exports(chunks, LAND)
    assert list(out.columns) == PARTNER_COLUMNS
    recs = out.set_index(["year", "type", "destination"])["volume"].to_dict()
    assert recs == {(2019, "saw wood", "SE"): 15.0, (2019, "saw wood", UNSPECIFIED): 1.0,
                    (2020, "firewood", "DE"): 3.0}
    assert out.loc[out["destination"] == UNSPECIFIED, "destination_label"].tolist() == [UNSPECIFIED]
    assert out.loc[out["destination"] == "SE", "destination_label"].tolist() == ["Sverige"]


def test_build_partner_exports_refuses_unknown_partner_label():
    chunk = _chunk([("2019M01", SKUR, "Atlantis", "Eksport", "Mengde 2 (M2)", 10)])
    with pytest.raises(ValueError, match="Atlantis"):
        build_partner_exports([chunk], LAND)


def test_classifier_is_shared_with_the_national_pull():
    labels = pd.Series([SKUR, VED, "Trepeletter", "Noe annet"])
    assert classify_varenummer(labels).tolist() == ["saw wood", "firewood", "pellets", "default"]
    # Later keywords win, exactly as the national 08801 loop always did: an
    # 'Impregnert skurlast' label is 'sawn wood', not 'impregnated'. Preserved
    # on purpose — changing it would move committed numbers.
    assert classify_varenummer(pd.Series(["Impregnert skurlast av furu"])).tolist() == ["sawn wood"]


def test_reconcile_flags_gaps_but_does_not_raise(caplog):
    partners = _partners([(2020, "firewood", "SE", "Sverige", 100.0)])
    national = pd.DataFrame({"year": [2020, 2020], "type": ["firewood", "pellets"], "volume": [110.0, 5.0]})
    with caplog.at_level("WARNING"):
        out = reconcile_partner_exports(partners, national, tolerance=0.05)
    assert len(out) == 2
    assert out.set_index("type").loc["firewood", "rel_diff"] == pytest.approx(-10 / 110)
    assert any("firewood" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# The chunked 08799 query files and their contracts
# ---------------------------------------------------------------------------
def test_08799_chunks_cover_the_window_and_fit_the_cell_limit():
    paths = sorted(config.SSB_QUERY_DIR.glob("ssbapi_table_08799_*.json"))
    assert paths, "no 08799 chunk query files"
    national = json.loads((config.SSB_QUERY_DIR / "ssbapi_table_08801.json").read_text(encoding="utf-8"))
    codes_08801 = next(s for s in national["payload"]["selection"] if s["variableCode"] == "Varekoder")["valueCodes"]
    years = set()
    for path in paths:
        spec = ssb.load_query_spec(path)
        sel = {s["variableCode"]: s["valueCodes"] for s in spec["payload"]["selection"]}
        assert sel["ImpEks"] == ["2"] and sel["ContentsCode"] == ["Mengde2"] and sel["Land"] == ["*"]
        assert sel["Varekoder"] == codes_08801, "08799 must use the 08801 commodity list verbatim"
        months = sel["Tid"]
        assert all(m.endswith("M*") for m in months)
        years |= {int(m[:4]) for m in months}
        # 262 partner codes in SSB's Land codelist today (08799 metadata); a
        # chunk must stay under SSB's 800,000-cell ceiling (v2 /config).
        assert len(codes_08801) * 262 * 12 * len(months) <= 800_000
        contract = ssb.contract_for(ssb._table_id(spec, path.name))
        assert contract.period == "month" and contract.year_column == "måned"
    assert years >= set(range(C.DATASET_MIN_YEAR, C.DATASET_MAX_YEAR + 1))


def test_month_period_contract_validates_years():
    frame = _chunk([
        ("2017M01", SKUR, "Sverige", "Eksport", "Mengde 2 (M2)", 1),
        ("2021M12", SKUR, "Sverige", "Eksport", "Mengde 2 (M2)", 1),
    ])
    assert ssb.validate_table("08799_2017_2021", [frame]) == (2017, 2021)
    short = frame.iloc[:1]
    with pytest.raises(ssb.SSBValidationError, match="coverage ends"):
        ssb.validate_table("08799_2017_2021", [short])


def test_dimension_codes_inverts_the_json_stat_labels():
    raw = json.dumps({"dimension": {"Land": {"category": {"label": {"SE": "Sverige", "97": "Uspesifisert"}}}}})
    assert ssb.dimension_codes(raw) == {"Land": {"Sverige": "SE", "Uspesifisert": "97"}}
    dup = json.dumps({"dimension": {"Land": {"category": {"label": {"A": "x", "B": "x"}}}}})
    with pytest.raises(ssb.SSBParseError):
        ssb.dimension_codes(dup)
