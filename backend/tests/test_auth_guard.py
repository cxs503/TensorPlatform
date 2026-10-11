"""
认证守卫集成测试

测试 auth_guard_middleware 的核心行为:
  - 白名单路径放行
  - 缺少 token 返回 401
  - 无效 token 返回 401
  - 有效 token 注入 current_user
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import Request
from fastapi.responses import JSONResponse

from middleware.auth_guard import (
    _should_skip_auth,
    _extract_token,
    _decode_token,
    auth_guard_middleware,
    SKIP_AUTH_PATHS,
    SKIP_AUTH_PREFIXES,
)


class TestShouldSkipAuth:
    """白名单路径判断测试"""

    def test_health_path_skipped(self):
        assert _should_skip_auth("/health", "GET") is True
        assert _should_skip_auth("/api/v1/health", "GET") is True

    def test_login_path_skipped(self):
        assert _should_skip_auth("/api/v1/auth/login", "POST") is True
        assert _should_skip_auth("/api/v1/auth/refresh", "POST") is True
        assert _should_skip_auth("/api/v1/login", "POST") is True

    def test_metrics_path_skipped(self):
        """P0-1: /metrics 路径必须免认证"""
        assert _should_skip_auth("/metrics", "GET") is True
        assert _should_skip_auth("/api/v1/metrics", "GET") is True

    def test_docs_path_skipped(self):
        assert _should_skip_auth("/docs", "GET") is True
        assert _should_skip_auth("/openapi.json", "GET") is True

    def test_root_path_skipped(self):
        assert _should_skip_auth("/", "GET") is True

    def test_business_prefixes_are_protected(self):
        assert _should_skip_auth("/api/v1/dashboard/statistics", "GET") is False
        assert _should_skip_auth("/api/v1/rbac/users", "GET") is False
        assert _should_skip_auth("/api/v1/model-serving/deploy", "POST") is False

    def test_protected_path_not_skipped(self):
        assert _should_skip_auth("/api/v1/datasets", "GET") is False
        assert _should_skip_auth("/api/v1/models", "GET") is False
        assert _should_skip_auth("/api/v1/training", "GET") is False

    def test_all_whitelist_paths_are_strings(self):
        """确保所有白名单路径都是有效字符串"""
        for path in SKIP_AUTH_PATHS:
            assert isinstance(path, str)
            assert path.startswith("/")
        for prefix in SKIP_AUTH_PREFIXES:
            assert isinstance(prefix, str)
            assert prefix.startswith("/")


class TestExtractToken:
    """Token 提取测试"""

    def test_valid_bearer_token(self):
        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Bearer abc123.def456.ghi789"}
        assert _extract_token(mock_request) == "abc123.def456.ghi789"

    def test_no_auth_header(self):
        mock_request = MagicMock()
        mock_request.headers = {}
        assert _extract_token(mock_request) is None

    def test_empty_auth_header(self):
        mock_request = MagicMock()
        mock_request.headers = {"Authorization": ""}
        assert _extract_token(mock_request) is None

    def test_non_bearer_token(self):
        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Basic dXNlcjpwYXNz"}
        assert _extract_token(mock_request) is None


class TestDecodeToken:
    """Token 解码测试"""

    def test_invalid_token_format(self):
        assert _decode_token("not.a.valid.jwt") is None

    def test_empty_token(self):
        assert _decode_token("") is None

    @patch("middleware.auth_guard.jwt.decode")
    def test_valid_access_token(self, mock_decode):
        mock_decode.return_value = {
            "sub": "testuser",
            "user_id": 1,
            "role": "ADMIN",
            "type": "access",
        }
        payload = _decode_token("valid.token.here")
        assert payload is not None
        assert payload["sub"] == "testuser"
        assert payload["role"] == "ADMIN"

    @patch("middleware.auth_guard.jwt.decode")
    def test_refresh_token_rejected(self, mock_decode):
        """非 access token 应该被拒绝"""
        mock_decode.return_value = {
            "sub": "testuser",
            "type": "refresh",
        }
        assert _decode_token("refresh.token.here") is None


@pytest.mark.asyncio
class TestAuthGuardMiddleware:
    """认证中间件集成测试"""

    async def test_options_preflight_bypasses_auth(self):
        """OPTIONS 预检请求应该总是放行"""
        mock_request = MagicMock(spec=Request)
        mock_request.method = "OPTIONS"
        mock_request.url.path = "/api/v1/datasets"

        mock_call_next = AsyncMock()
        mock_call_next.return_value = JSONResponse({"ok": True})

        response = await auth_guard_middleware(mock_request, mock_call_next)
        assert response.status_code == 200
        mock_call_next.assert_called_once()

    async def test_whitelisted_path_bypasses_auth(self):
        """白名单路径应该免认证"""
        mock_request = MagicMock(spec=Request)
        mock_request.method = "GET"
        mock_request.url.path = "/api/v1/health"

        mock_call_next = AsyncMock()
        mock_call_next.return_value = JSONResponse({"status": "healthy"})

        response = await auth_guard_middleware(mock_request, mock_call_next)
        assert response.status_code == 200

    async def test_missing_token_returns_401(self):
        """缺少 token 应该返回 401"""
        mock_request = MagicMock(spec=Request)
        mock_request.method = "GET"
        mock_request.url.path = "/api/v1/datasets"
        mock_request.headers = {}

        mock_call_next = AsyncMock()

        response = await auth_guard_middleware(mock_request, mock_call_next)
        assert response.status_code == 401
        body = response.body.decode() if hasattr(response, "body") else ""
        assert "UNAUTHORIZED" in str(response.body) if hasattr(response, "body") else True
        mock_call_next.assert_not_called()

    async def test_valid_token_passes_and_injects_user(self):
        """有效 token 应该放行并注入 current_user"""
        mock_request = MagicMock(spec=Request)
        mock_request.method = "GET"
        mock_request.url.path = "/api/v1/datasets"
        mock_request.headers = {"Authorization": "Bearer valid.token"}
        mock_request.state = MagicMock()

        mock_call_next = AsyncMock()
        mock_call_next.return_value = JSONResponse({"data": []})

        with patch("middleware.auth_guard._decode_token") as mock_decode:
            mock_decode.return_value = {
                "sub": "testuser",
                "user_id": 1,
                "role": "ADMIN",
                "type": "access",
            }
            response = await auth_guard_middleware(mock_request, mock_call_next)

        assert response.status_code == 200
        assert mock_request.state.current_user is not None
        assert mock_request.state.current_user["username"] == "testuser"
        assert mock_request.state.current_user["role"] == "ADMIN"
