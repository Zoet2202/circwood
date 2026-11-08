"""Doc-drift fences (ROADMAP Phase I): the data dictionary and the constants stay complete."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from circwood import build_sankey, config
from circwood import constants as C

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def dictionary() -> str:
    return (ROOT / "DATA_DICTIONARY.md").read_text(encoding="utf-8")


def test_every_node_is_in_the_data_dictionary(dictionary):
    df = build_sankey()
    nodes = set(df["source"]) | set(df["target"])
    missing = sorted(n for n in nodes if f"| {n} |" not in dictionary)
    assert not missing, f"nodes missing from DATA_DICTIONARY.md: {missing}"


def _edge_documented(dictionary: str, source: str, target: str) -> bool:
    """An edge is documented if a table row names the pair, allowing the grouped spellings used there."""
    rows = [ln for ln in dictionary.splitlines() if ln.startswith("| ") and ("→" in ln or "⇄" in ln)]
    for row in rows:
        cell = row.split("|")[1]
        if "⇄" in cell:
            a, b = (s.strip() for s in cell.split("⇄"))
            if {source, target} == {a, b}:
                return True
            continue
        if "→" not in cell:
            continue
        lhs, rhs = (s.strip() for s in cell.split("→", 1))
        sources = [s.strip() for s in lhs.split(" / ")]
        targets = [t.strip() for t in rhs.split(" / ")]
        if source in sources and target in targets:
            return True
    return False


def test_every_edge_is_in_the_data_dictionary(dictionary):
    df = build_sankey()
    edges = sorted(set(zip(df["source"], df["target"])))
    missing = [f"{s} -> {t}" for s, t in edges if not _edge_documented(dictionary, s, t)]
    assert not missing, f"edges missing from DATA_DICTIONARY.md: {missing}"


def test_every_coefficient_is_documented_in_methods_or_the_dictionary():
    text = (ROOT / "METHODS.md").read_text(encoding="utf-8") + (ROOT / "DATA_DICTIONARY.md").read_text(encoding="utf-8")
    names = [n for n in dir(C) if n.isupper() and not n.startswith("_")]
    missing = [n for n in names if f"`{n}`" not in text and n not in text]
    assert not missing, f"constants not documented: {missing}"


def test_the_constants_index_names_only_constants_that_exist():
    """The reverse fence: a constant removed from the code must leave METHODS §11 too."""
    text = (ROOT / "METHODS.md").read_text(encoding="utf-8")
    section = text.split("## 11. Constants index", 1)[1]
    listed = re.findall(r"^\| `([A-Z][A-Z0-9_]*)` \|", section, flags=re.M)
    assert listed, "the constants index table is empty or has moved"
    missing = [n for n in listed if not hasattr(C, n)]
    assert not missing, f"METHODS.md §11 lists constants that no longer exist: {missing}"


def test_readme_and_guide_mention_the_current_outputs():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    guide = (ROOT / "SANKEY_GUIDE.md").read_text(encoding="utf-8")
    for name in ("beta_series.csv", "hwp_stock.csv", "forest_resource.csv", "economic_value.csv"):
        assert name in readme or name in guide, name
    # Stale claims that drifted once must not come back.
    assert "→ **Material recovery**" not in guide and "End-of-Life → Material recovery" not in guide
    assert "NODE_RENAME_MAP" not in guide
    assert "Streamlit Community Cloud" not in readme


def test_config_files_named_in_the_dictionary_exist(dictionary):
    for name in re.findall(r"`config/([A-Za-z_]+\.(?:csv|json))`", dictionary):
        assert (config.CONFIG_DIR / name).exists(), name
