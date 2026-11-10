"""Pytest shim: fixtures live in ``_fixtures.py`` so tests can import the
synthetic-ledger constants directly (the test dir is put on ``sys.path``)."""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _fixtures import (  # noqa: E402,F401
    API_TESTS,
    CORE_TESTS,
    LEDGER,
    ROOT,
    graph,
    guard_factory,
    ledger,
    workspace,
)
