"""Public-bundle export: Phase D's builder and its confidentiality gate.

See :mod:`circwood.export.bundle` — :func:`build_bundle` is the only way a
frame reaches the static site, and :func:`assert_publishable` is the gate it
routes through.
"""

from circwood.export.bundle import (
    MIN_ENTITIES,
    ConfidentialityError,
    assert_publishable,
    build_bundle,
    write_bundle,
)

__all__ = [
    "MIN_ENTITIES",
    "ConfidentialityError",
    "assert_publishable",
    "build_bundle",
    "write_bundle",
]
