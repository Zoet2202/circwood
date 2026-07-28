"""Golden-snapshot and node-coverage regression fences.

The golden fixture is an exact copy of the committed
``data/processed/sankey_data.csv``. Any change to the shipped dataset —
regeneration, renaming, a transform tweak — must be deliberate: update the
fixture in the same commit (copy the new CSV over it) so the diff is visible
in review. This is the regression fence Phase C's schema widening relies on.

The node-coverage test guards the viz layer: ``remap_ids`` silently drops any
flow whose endpoint has no entry in ``config/nodes.json``, so the two must
stay in exact sync (this previously hid ~5.9 Mm³ of flows, including the
entire Pulpmills loss output).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pandas.testing as pdt

from circwood import build_sankey, config

GOLDEN_CSV = Path(__file__).parent / "fixtures" / "golden_sankey_data.csv"


def test_shipped_dataset_matches_golden_snapshot():
    shipped = build_sankey(use_cached_ssb=True)
    golden = pd.read_csv(GOLDEN_CSV)
    pdt.assert_frame_equal(shipped, golden)


def test_nodes_json_covers_dataset_exactly():
    """Every dataset node has a position, and nodes.json has no ghost labels."""
    df = build_sankey(use_cached_ssb=True)
    dataset_nodes = set(df["source"]) | set(df["target"])
    with open(config.NODES_FILE, encoding="utf-8") as fh:
        labels = {entry["label"] for entry in json.load(fh)}

    missing = sorted(dataset_nodes - labels)
    assert not missing, (
        f"Nodes with data but no nodes.json entry (their flows would be "
        f"silently dropped from the Sankey): {missing}"
    )
    ghosts = sorted(labels - dataset_nodes)
    assert not ghosts, (
        f"nodes.json labels with no data behind them (would render as "
        f"orphan bars): {ghosts}"
    )
