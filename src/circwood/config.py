"""Filesystem paths for the Circwood WP4 project.

All paths are anchored to the repository root (three levels up from this file:
``src/circwood/config.py`` -> repo root) so the package behaves identically no
matter what the current working directory is.
"""

from __future__ import annotations

from pathlib import Path

# .../repo/src/circwood/config.py -> parents[2] == repo root
ROOT_DIR = Path(__file__).resolve().parents[2]

CONFIG_DIR = ROOT_DIR / "config"
SSB_QUERY_DIR = CONFIG_DIR / "ssb_queries"
NODES_FILE = CONFIG_DIR / "nodes.json"

# Researcher-editable carbon model (open in Excel as CSV).
CARBON_DENSITIES_FILE = CONFIG_DIR / "carbon_densities.csv"
CARBON_NODES_FILE = CONFIG_DIR / "carbon_nodes.csv"

DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

# Generated pipeline outputs.
SANKEY_CSV = PROCESSED_DIR / "sankey_data.csv"
CARBON_CSV = PROCESSED_DIR / "carbon_data.csv"

# Raw source inputs used by the fresh (API) pipeline.
TREINDUSTRIEN_CSV = RAW_DIR / "treindustrien_production_data.csv"
FAO_CSV = RAW_DIR / "FAO_Data.csv"

__all__ = [
    "ROOT_DIR",
    "CONFIG_DIR",
    "SSB_QUERY_DIR",
    "NODES_FILE",
    "CARBON_DENSITIES_FILE",
    "CARBON_NODES_FILE",
    "DATA_DIR",
    "RAW_DIR",
    "PROCESSED_DIR",
    "SANKEY_CSV",
    "CARBON_CSV",
    "TREINDUSTRIEN_CSV",
    "FAO_CSV",
]
