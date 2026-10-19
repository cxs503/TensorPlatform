# -*- coding: utf-8 -*-
"""
Issue #27 回归测试：/auth/register 校验失败应返回 422，而非 500。

根因：Pydantic v2 的 @validator 抛出 ValueError 后，exc.errors() 的 ctx 字段
包含原始 ValueError 对象，JSONResponse 序列化失败 → TypeError → 全局异常处理器
返回 500。修复方式：在异常处理器中清洗 errors，只保留 loc/msg/type 三个字段。
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-testing-only"
os.environ["REFRESH_SECRET_KEY"] = "test-refresh-key-for-testing-only"


@pytest.fixture(scope="module")
def client():
    from main import app
    with TestClient(app) as c:
        yield c


def _assert_422_structure(resp):
    """断言 422 响应体包含 detail 列表，每项含 loc/msg/type。"""
    assert resp.status_code == 422, f"期望 422，实际 {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "detail" in body
    assert isinstance(body["detail"], list)
    assert len(body["detail"]) >= 1
    for item in body["detail"]:
        assert "loc" in item, f"缺少 loc 字段: {item}"
        assert "msg" in item, f"缺少 msg 字段: {item}"
        assert "type" in item, f"缺少 type 字段: {item}"


class TestRegisterValidation422:
    """Issue #27: 注册校验失败返回 422 而非 500"""

    def test_password_missing_special_char(self, client):
        """密码缺少特殊字符 → 422"""
        resp = client.post(
            "/api/v1/auth/register",
            json={
                "username": "testuser_valid",
                "password": "NoSpecial123",  # 缺特殊字符
                "email": "test@example.com",
            },
        )
        _assert_422_structure(resp)
        # 确认错误信息涉及密码
        msgs = " ".join(item["msg"] for item in resp.json()["detail"])
        assert "密码" in msgs or "password" in msgs.lower()

    def test_username_too_short(self, client):
        """用户名过短 → 422"""
        resp = client.post(
            "/api/v1/auth/register",
            json={
                "username": "ab",  # 短于 3 字符
                "password": "ValidPass123!",
            },
        )
        _assert_422_structure(resp)

    def test_password_too_short(self, client):
        """密码过短 → 422"""
        resp = client.post(
            "/api/v1/auth/register",
            json={
                "username": "testuser_valid2",
                "password": "Sh0rt!",  # 少于 8 位
            },
        )
        _assert_422_structure(resp)

    def test_password_missing_uppercase(self, client):
        """密码缺少大写字母 → 422"""
        resp = client.post(
            "/api/v1/auth/register",
            json={
                "username": "testuser_valid3",
                "password": "nouppercase123!",
            },
        )
        _assert_422_structure(resp)

    def test_invalid_email_format(self, client):
        """邮箱格式错误 → 422"""
        resp = client.post(
            "/api/v1/auth/register",
            json={
                "username": "testuser_valid4",
                "password": "ValidPass123!",
                "email": "not-an-email",
            },
        )
        _assert_422_structure(resp)

    def test_valid_register_not_500(self, client):
        """合法注册不应返回 500（201/200/409 均可接受）"""
        import uuid
        unique = uuid.uuid4().hex[:8]
        resp = client.post(
            "/api/v1/auth/register",
            json={
                "username": f"validuser_{unique}",
                "password": "ValidPass123!",
                "email": f"valid_{unique}@example.com",
            },
        )
        assert resp.status_code != 500, f"合法注册不应返回 500: {resp.text}"
        # 注册成功 200/201，或用户名冲突 400/409，都算通过
        assert resp.status_code in (200, 201, 400, 409), (
            f"合法注册期望 200/201/400/409，实际 {resp.status_code}: {resp.text}"
        )
