"""Smoke tests for the internal Streamlit app (``app/streamlit_app.py``).

``streamlit.testing.v1.AppTest`` runs the script in-process against the
committed dataset: no browser, no server, no network. It guards the one thing
the module tests cannot — that the page renders end to end (every tab, every
cached loader, every chart) without raising — and that the metric control's
CO₂e option reaches the KPI tiles and the flow caption.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

from circwood import config  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"
TIMEOUT = 180

#: Tabs added in Phases G and H; the four older ones are asserted alongside.
TABS = {
    "Flow diagram", "Carbon fate & β", "Sensitivity", "HWP stock",
    "Forest resource", "Mass balance", "Data & provenance",
}
CO2_METRIC = "CO₂e (Mt CO₂)"

#: The derived exports the tabs read; each tab shows an st.info naming the
#: file when it is absent and nothing of the kind when it is present.
EXPORTS = {
    "beta_series.csv": config.BETA_SERIES_CSV,
    "beta_sensitivity.csv": config.BETA_SENSITIVITY_CSV,
    "hwp_stock.csv": config.HWP_STOCK_CSV,
    "forest_resource.csv": config.FOREST_RESOURCE_CSV,
}


def _run() -> AppTest:
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT)
    at.run()
    return at


def _text(elements) -> str:
    return "\n".join(str(e.value) for e in elements)


def test_app_renders_without_exception():
    at = _run()
    assert not at.exception, [e.value for e in at.exception]
    labels = {tab.label for tab in at.tabs}
    assert TABS <= labels, labels
    # Graceful degradation, both ways: a tab shows its "file is missing" notice
    # exactly when its export is absent from this checkout.
    notices = _text(at.info)
    for name, path in EXPORTS.items():
        assert (f"{name}` is missing" in notices) == (not path.exists()), (name, notices)
    # The KPI tiles read Mt C until the CO₂e metric is chosen.
    tiles = _text(at.markdown)
    assert "Carbon in harvest" in tiles and "Mt C<" in tiles
    assert "density envelope" in tiles


def test_metric_control_switches_to_co2e():
    at = _run()
    if not hasattr(at, "segmented_control"):
        pytest.skip("this Streamlit's AppTest has no segmented_control accessor")
    control = at.sidebar.segmented_control(key="metric")
    assert CO2_METRIC in control.options
    control.set_value(CO2_METRIC).run()
    assert not at.exception, [e.value for e in at.exception]
    tiles = _text(at.markdown)
    assert "Mt CO₂<" in tiles, "the carbon KPI tiles did not switch to Mt CO₂"
    assert "CO₂ equivalent" in _text(at.caption)
