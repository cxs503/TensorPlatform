"""Make the ``src/`` layout importable for the capability_ledger_auto tests.

The engine lives at ``src/capability_ledger_auto`` (repo src-layout), while the
tests import it by its top-level package name.  Without this hook the whole
test package is a *collection error* under the repo ``pytest``/CI invocation
(``PYTHONPATH=. pytest tests/``), i.e. the tests exist but never execute.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))