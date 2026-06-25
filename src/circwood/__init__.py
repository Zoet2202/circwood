"""Circwood WP4 — Norwegian wood-flow Sankey pipeline and app."""

from __future__ import annotations

__version__ = "0.1.0"

from circwood.pipeline import build_sankey, load_cached_sankey

__all__ = ["build_sankey", "load_cached_sankey", "__version__"]
