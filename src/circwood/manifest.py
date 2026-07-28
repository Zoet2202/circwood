"""Provenance for the generated dataset — ``data/processed/manifest.json``.

Answers "where did this CSV come from and how old is it?" without anyone having
to remember. Written by every fresh run (see :mod:`circwood.refresh`), read by
the UIs to state the data vintage and by the refresh workflow to describe what
changed.

Two different ages are recorded per table and they are not interchangeable:

* ``updated`` — SSB's own last-revision date for that table. The age of the
  *statistic*. This is what "data as of" means to a reader.
* ``fetched_at`` — when this project pulled it. The age of *our copy*.

A refresh that changes ``fetched_at`` but no ``updated`` date means SSB has
published nothing new; a change in ``updated`` is the signal that the numbers
may genuinely move.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from circwood import config

MANIFEST_NAME = "manifest.json"

SCHEMA_VERSION = 1


def manifest_path() -> Path:
    """Where the manifest lives, resolved *now*.

    Deliberately a function, not a module constant: a constant would bind
    ``config.PROCESSED_DIR`` at import time, so the refresh loop would snapshot
    one path for restore-on-failure and write to another. They are the same path
    in production and different everywhere else, which is the shape of bug that
    only shows up once something has already been overwritten.
    """
    return config.PROCESSED_DIR / MANIFEST_NAME


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _package_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("circwood")
    except PackageNotFoundError:  # pragma: no cover - only when not installed
        return "unknown"


def _git(*args: str) -> str | None:
    """Run a git command in the repo root; ``None`` if git or the repo is absent."""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=config.ROOT_DIR,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - env dependent
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class Manifest:
    """Everything needed to say what a generated dataset is made of."""

    schema_version: int
    generated_at: str
    package_version: str
    git_sha: str | None
    git_branch: str | None
    git_dirty: bool
    tables: dict[str, dict[str, Any]] = field(default_factory=dict)
    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)

    # -- reading ------------------------------------------------------------
    @property
    def data_as_of(self) -> str | None:
        """The newest SSB revision date across the source tables (an ISO date).

        This is the honest "data as of" for a UI: the dataset is only as current
        as its most recently revised input, and quoting our own pull timestamp
        instead would overstate how fresh the statistics are.
        """
        dates = [t.get("updated") for t in self.tables.values() if t.get("updated")]
        return max(dates)[:10] if dates else None

    @property
    def oldest_table_as_of(self) -> str | None:
        """The *oldest* SSB revision date — the real limit on the dataset."""
        dates = [t.get("updated") for t in self.tables.values() if t.get("updated")]
        return min(dates)[:10] if dates else None

    @property
    def year_range(self) -> tuple[int | None, int | None]:
        sankey = self.outputs.get("sankey_data.csv", {})
        return sankey.get("year_min"), sankey.get("year_max")

    def describe(self) -> str:
        """One line for a UI caption or a log."""
        as_of = self.data_as_of or "unknown"
        y0, y1 = self.year_range
        years = f"{y0}–{y1}" if y0 and y1 else "unknown years"
        return f"Data as of {as_of} · {years} · built {self.generated_at[:10]}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Manifest":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in payload.items() if k in known})


def describe_dataset(df: pd.DataFrame, path: Path) -> dict[str, Any]:
    """Summarise one generated CSV for the manifest."""
    return {
        "path": str(path.relative_to(config.ROOT_DIR)) if path.is_relative_to(config.ROOT_DIR) else str(path),
        "rows": int(len(df)),
        "year_min": int(df["year"].min()) if len(df) else None,
        "year_max": int(df["year"].max()) if len(df) else None,
        "nodes": int(len(set(df["source"]) | set(df["target"]))) if len(df) else 0,
        "total_volume": float(df["volume"].sum()) if "volume" in df else None,
        "sha256": _sha256_file(path),
    }


def build_manifest(
    vintages: Mapping[str, Any],
    outputs: Mapping[str, dict[str, Any]] | None = None,
) -> Manifest:
    """Assemble a :class:`Manifest` from ingestion vintages and output summaries.

    ``vintages`` is ``IngestResult.vintages`` — ``TableVintage`` objects, or the
    plain dicts they serialise to. Phase A already collected every per-table
    field, so nothing here re-derives them.
    """
    tables = {
        table_id: (v.to_dict() if hasattr(v, "to_dict") else dict(v))
        for table_id, v in vintages.items()
    }
    dirty = _git("status", "--porcelain")
    return Manifest(
        schema_version=SCHEMA_VERSION,
        generated_at=_utcnow(),
        package_version=_package_version(),
        git_sha=_git("rev-parse", "HEAD"),
        git_branch=_git("rev-parse", "--abbrev-ref", "HEAD"),
        git_dirty=bool(dirty),
        tables=dict(sorted(tables.items())),
        outputs=dict(sorted((outputs or {}).items())),
    )


def write_manifest(manifest: Manifest, path: Path | None = None) -> Path:
    path = Path(path) if path is not None else manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def read_manifest(path: Path | None = None) -> Manifest | None:
    """Load the manifest, or ``None`` if it has not been generated yet.

    Returns ``None`` rather than raising: a checkout that has never run a fresh
    build is a normal state, and a UI should caption "vintage unknown" instead
    of refusing to start.
    """
    path = Path(path) if path is not None else manifest_path()
    if not path.exists():
        return None
    try:
        return Manifest.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


__all__ = [
    "MANIFEST_NAME",
    "SCHEMA_VERSION",
    "Manifest",
    "build_manifest",
    "describe_dataset",
    "manifest_path",
    "read_manifest",
    "write_manifest",
]
