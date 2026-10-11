"""Regression coverage for authentication error handling."""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from api.auth import LoginRequest, login


class _MissingUserResult:
    def fetchone(self):
        return None


class _MissingUserSession:
    def execute(self, *_args, **_kwargs):
        return _MissingUserResult()


def test_unknown_user_is_rejected_without_server_error():
    """The enumeration-delay branch must return 401 rather than raise NameError."""
    with patch("api.auth.time.sleep") as delay, pytest.raises(HTTPException) as exc:
        login(LoginRequest(username="missing_user", password="invalid"), _MissingUserSession())

    assert exc.value.status_code == 401
    delay.assert_called_once_with(0.5)
