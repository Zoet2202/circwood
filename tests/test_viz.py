"""The viz layer: averaging that balances (Phase K), id remapping, figure construction."""

from __future__ import annotations

import pandas as pd
import pytest

from circwood import build_sankey
from circwood import constants as C
from circwood.validate import PROCESS_NODES
from circwood.viz.sankey import build_figure, load_node_params, prepare_flows, remap_ids


def _flows(rows):
    return pd.DataFrame(rows, columns=["source", "target", "volume", "year"]).assign(origin="t")


def test_average_divides_by_the_window_years_not_the_years_an_edge_has():
    df = _flows([("A", "B", 100.0, 2012), ("A", "B", 300.0, 2013), ("B", "C", 50.0, 2013)])
    avg = prepare_flows(df, 2012, 2013, aggregate="avg", value_scale=1.0).set_index(["source", "target"])["volume"]
    assert avg[("A", "B")] == pytest.approx(200.0)
    assert avg[("B", "C")] == pytest.approx(25.0)   # present one year of two: half, not 50
    per_year = prepare_flows(df, 2012, 2013, aggregate="year", value_scale=1.0)
    assert len(per_year) == 3 and set(per_year["year"]) == {2012, 2013}


def test_window_count_is_taken_before_nodes_are_dropped():
    df = _flows([("A", "B", 100.0, 2012), ("A", "B", 100.0, 2013), ("X", "Y", 1.0, 2013)])
    avg = prepare_flows(df, 2012, 2013, aggregate="avg", nodes_to_drop=("A",), value_scale=1.0)
    assert avg.set_index(["source", "target"])["volume"][("X", "Y")] == pytest.approx(0.5)


def test_averaged_shipped_diagram_balances_at_the_process_nodes():
    """Phase K: before, WPP read 6.7 % out of balance in average mode while every year balanced."""
    df = build_sankey()
    avg = prepare_flows(df, C.MIN_YEAR, C.MAX_YEAR, aggregate="avg")
    for node in PROCESS_NODES:
        inflow = avg[avg.target == node].volume.sum()
        outflow = avg[avg.source == node].volume.sum()
        assert outflow == pytest.approx(inflow, rel=0.01), node


def test_remap_ids_drops_flows_whose_endpoint_has_no_node():
    """The documented failure mode the node-coverage fence exists for."""
    df = pd.DataFrame({"source": ["A", "A"], "target": ["B", "Ghost"], "volume": [1.0, 2.0]})
    out = remap_ids(df, ["A", "B"])
    assert len(out) == 1 and out["source_id"].tolist() == [0] and out["target_id"].tolist() == [1]


def test_build_figure_honours_fixed_positions_and_labels():
    labels, xs, ys, colors = load_node_params()
    assert len(labels) == 31 and len(set(labels)) == 31
    df = prepare_flows(build_sankey(), aggregate="avg")
    fig = build_figure(labels, xs, ys, colors, remap_ids(df, labels), value_unit="Mm³")
    sankey = fig.data[0]
    assert sankey.arrangement == "fixed"
    assert list(sankey.node.label) == labels
    assert len(sankey.link.value) == len(df)
    assert all(v >= 0 for v in sankey.link.value)
    # The repositioned End-of-Life sits right of Long-term USE and left of Energy.
    pos = dict(zip(labels, xs))
    assert pos["Long-term USE"] < pos["End-of-Life"] < pos["Energy"]
