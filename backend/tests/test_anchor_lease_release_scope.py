"""Regression tests for anchor-scoped lease release (no database required)."""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.anchor_leases import release


class FakeSession:
    def __init__(self):
        self.statement = None
        self.params = None

    def execute(self, statement, params):
        self.statement = str(statement)
        self.params = params

        class Result:
            rowcount = 1
        return Result()


def test_release_with_lease_id_still_scopes_to_anchor():
    db = FakeSession()
    assert release(db, "anchor-a", lease_id=42, holder="agent-a") == 1
    assert "anchor_slug = :slug" in db.statement
    assert "(:lease_id IS NULL OR id = :lease_id)" in db.statement
    assert "AND holder = :holder" in db.statement
    assert db.params == {
        "slug": "anchor-a", "reason": "released",
        "lease_id": 42, "holder": "agent-a",
    }


def test_release_without_id_still_scopes_to_anchor():
    db = FakeSession()
    assert release(db, "anchor-b") == 1
    assert "anchor_slug = :slug" in db.statement
    assert db.params["lease_id"] is None
    assert "AND holder = :holder" not in db.statement
