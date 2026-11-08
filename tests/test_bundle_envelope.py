"""Phase G additions to the site bundle: the carbon envelope and the CO2 factor."""

from __future__ import annotations

import pytest

from circwood import constants as C
from circwood.export.bundle import build_bundle


@pytest.fixture(scope="module")
def bundle() -> dict:
    return build_bundle(write=False)


def test_envelope_rides_along_with_the_same_length(bundle):
    flows = bundle["flows"]
    assert "carbon_low" in flows and "carbon_high" in flows
    n = bundle["row_count"]
    assert len(flows["carbon_low"]) == n == len(flows["carbon_high"])
    for lo, c, hi in zip(flows["carbon_low"], flows["carbon"], flows["carbon_high"]):
        assert lo <= c <= hi


def test_units_carry_the_co2_factor(bundle):
    assert bundle["units"]["carbon"] == "kg C"
    assert bundle["units"]["co2_per_c"] == pytest.approx(C.CO2_PER_C, abs=1e-6)
    assert 3.66 < bundle["units"]["co2_per_c"] < 3.67


def test_frames_without_envelope_still_bundle():
    from tests.test_bundle import tiny_frame, tiny_nodes

    b = build_bundle(df=tiny_frame(), nodes=tiny_nodes("A", "B"), write=False)
    assert "carbon_low" not in b["flows"]
