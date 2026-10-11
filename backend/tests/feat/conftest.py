# -*- coding: utf-8 -*-
"""conftest for the `feat` test package.

Keeps import path simple (``backend`` on sys.path) and provides a tiny fake
``db`` so DB-dependent helpers can be exercised without a real server.
"""
import os
import sys
from pathlib import Path

import pytest

# Make ``backend/`` importable as the project root for `services.*` / `api.*`.
_THIS = Path(__file__).resolve()
_BACKEND = _THIS.parents[2]  # .../backend
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("ENVIRONMENT", "test")


class _FakeResult:
    """Mimics the subset of SQLAlchemy Result used by the helpers."""

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._rows[0][0] if self._rows else None


class _FakeDB:
    """DB stub that records executes and never touches a real connection.

    Any SELECT / CREATE TABLE returns empty results; commits/rollbacks are
    no-ops. This lets us assert that DB-touching helpers short-circuit
    gracefully (they all swallow exceptions).
    """

    def __init__(self):
        self.executed = []

    def execute(self, stmt, params=None):
        # Coerce TextClause -> str so substring assertions in tests work.
        self.executed.append((str(stmt), params))
        return _FakeResult([])

    def commit(self):
        self.executed.append(("__commit__", None))

    def rollback(self):
        self.executed.append(("__rollback__", None))


@pytest.fixture
def fake_db():
    return _FakeDB()
