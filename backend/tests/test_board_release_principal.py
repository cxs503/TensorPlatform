"""Focused tests for authenticated board lease release (SQL contract)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from api.tensor_board import LeaseReleaseRequest, board_release


class FakeDB:
    def __init__(self, rowcount=1):
        self.rowcount = rowcount
        self.statement = ""
        self.params = None
        self.committed = False

    def execute(self, statement, params):
        self.statement = str(statement)
        self.params = params
        return type("Result", (), {"rowcount": self.rowcount})()

    def commit(self):
        self.committed = True


def test_release_binds_principal_tenant_and_holder():
    db = FakeDB()
    with patch("api.tensor_board.resolved_org", return_value=7), patch(
        "api.tensor_board.AL.ensure_schema"
    ):
        response = board_release(
            "FEAT-A", LeaseReleaseRequest(holder="agent-x"), db,
            {"id": 42, "username": "operator"},
        )
    assert response["released"] == 1
    assert db.committed
    assert "organization_id = :org" in db.statement
    assert "meta->>'user' = :principal" in db.statement
    assert "source = :source" in db.statement
    assert db.params["principal"] == "42"
    assert db.params["holder"] == "agent-x"
    assert db.params["org"] == 7


def test_missing_principal_fails_closed():
    db = FakeDB()
    with patch("api.tensor_board.resolved_org", return_value=7), patch(
        "api.tensor_board.AL.ensure_schema"
    ):
        try:
            board_release("FEAT-A", LeaseReleaseRequest(), db, {"username": "unknown"})
        except HTTPException as exc:
            assert exc.status_code == 403
        else:
            raise AssertionError("expected 403")
    assert not db.committed


def test_lease_listing_is_principal_scoped_and_redacted():
    from api.tensor_board import board_leases
    db = FakeDB()
    # Query result is deliberately empty: inspect predicates and returned shape.
    def execute(statement, params):
        db.statement, db.params = str(statement), params
        return type("Result", (), {"all": lambda self: []})()
    db.execute = execute
    with patch("api.tensor_board.resolved_org", return_value=7), patch(
        "api.tensor_board.AL.ensure_schema"
    ):
        result = board_leases(None, db, {"id": 42, "username": "operator"})
    assert result == {"count": 0, "leases": []}
    assert "organization_id = :org" in db.statement
    assert "meta->>'user' = :principal" in db.statement
    assert db.params["org"] == 7
    assert db.params["principal"] == "42"
    assert "holder" not in db.statement.lower()


def test_lease_listing_rejects_missing_principal():
    from api.tensor_board import board_leases
    db = FakeDB()
    with patch("api.tensor_board.resolved_org", return_value=7):
        try:
            board_leases(None, db, {"username": "unknown"})
        except HTTPException as exc:
            assert exc.status_code == 403
        else:
            raise AssertionError("expected 403")
