"""Research analyses derived from the committed flow table.

Nothing here feeds back into the dataset: these modules read
``data/processed/carbon_data.csv`` and export parameter files for external
models (Phase F: the empirical HWP fraction β, :mod:`circwood.analysis.hwp`).
"""

from __future__ import annotations

from circwood.analysis.hwp import (
    FATE_BUCKETS,
    absorption_fractions,
    beta_series,
    classify_sink,
    export_beta_series,
    literal_beta,
)
from circwood.analysis.sensitivity import (
    beta_bounds,
    export_sensitivity,
    sensitivity_table,
    summary_table,
)

__all__ = [
    "FATE_BUCKETS",
    "absorption_fractions",
    "beta_bounds",
    "beta_series",
    "classify_sink",
    "export_beta_series",
    "export_sensitivity",
    "literal_beta",
    "sensitivity_table",
    "summary_table",
]
