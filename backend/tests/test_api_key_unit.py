# -*- coding: utf-8 -*-
"""
API密钥单元测试 - 直接测试函数而非API端点
"""
import pytest
import sqlite3
from unittest.mock import MagicMock
from types import SimpleNamespace
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from api import api_key

from api.api_key import (
    create_api_key,
    list_api_keys,
    get_api_key,
    revoke_api_key,
    delete_api_key,
    get_key_usage,
    verify_api_key,
    validate_api_key,
    rotate_api_key,
    _hash_key,
)
from api.api_key import CreateAPIKeyReq


class TestAPIKeyUnit:
    """API密钥单元测试"""

    @pytest.fixture
    def db(self):
        """SQL 会话替身；不再启用旧的文件密钥管理器。"""
        return MagicMock()

    @pytest.fixture
    def mock_current_user(self):
        """模拟当前用户"""
        return {"user_id": 123, "username": "testuser", "role": "admin"}

    @pytest.fixture
    def key(self):
        return SimpleNamespace(
            id=123, name="test-key", key_prefix="sk-demo1",
            created_at=datetime(2026, 1, 1), last_used_at=datetime(2026, 4, 7),
            is_active=True,
        )

    def test_create_api_key_success(self, db, mock_current_user, key):
        """测试成功创建API密钥"""
        db.execute.return_value.fetchone.return_value = key
        
        req = CreateAPIKeyReq(
            name="test-key",
        )
        result = create_api_key(req, db=db, current_user=mock_current_user)
        assert result["id"] == 123
        assert result["key"].startswith("sk-")
        assert len(result["key"]) > 32
        assert result["name"] == "test-key"
        assert "message" in result
        params = db.execute.call_args.args[1]
        assert params["name"] == "test-key"
        assert params["key_hash"] == _hash_key(result["key"])
        assert params["key_prefix"] == result["key"][:8]
        assert result["key"] not in params.values()
        assert "key_hash" not in result
        assert db.commit.call_count == 2  # ensure-table and insert

    def test_list_api_keys(self, db, mock_current_user, key):
        """测试列出API密钥"""
        db.execute.return_value.fetchall.return_value = [key, key]
        
        result = list_api_keys(db=db, current_user=mock_current_user)
        assert result["total"] == 2
        assert len(result["items"]) == 2
        assert all("key" not in item and "key_hash" not in item for item in result["items"])
        assert result["items"][0]["key_prefix"] == key.key_prefix
        assert "key_hash" not in str(db.execute.call_args.args[0])

    def test_get_api_key_exists(self, db, mock_current_user, key):
        """测试获取存在的密钥"""
        db.execute.return_value.fetchone.return_value = key
        
        result = get_api_key(123, db=db, current_user=mock_current_user)
        assert result["id"] == 123
        assert result["name"] == "test-key"
        assert result["key_prefix"] == key.key_prefix
        assert result["is_active"] is True
        assert result["created_at"] == "2026-01-01T00:00:00"
        assert result["last_used_at"] == "2026-04-07T00:00:00"
        assert "key" not in result and "key_hash" not in result
        assert db.execute.call_args.args[1] == {"id": 123}

    def test_get_api_key_not_exists(self, db, mock_current_user):
        """测试获取不存在的密钥"""
        db.execute.return_value.fetchone.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_api_key(999, db=db, current_user=mock_current_user)
        assert exc_info.value.status_code == 404

    def test_get_api_key_permission_denied(self, db, mock_current_user):
        """测试无权限访问密钥"""
        mock_current_user["role"] = "viewer"
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_api_key(123, db=db, current_user=mock_current_user)
        assert exc_info.value.status_code == 403
        db.execute.assert_not_called()

    def test_revoke_api_key_exists(self, db, mock_current_user, key):
        """测试撤销存在的密钥"""
        db.execute.return_value.fetchone.return_value = key
        
        result = revoke_api_key(123, db=db, current_user=mock_current_user)
        assert result["message"] == "密钥已撤销"
        assert result["id"] == 123
        statement, params = db.execute.call_args.args
        assert "SET is_active = FALSE" in str(statement)
        assert "key_hash" not in str(statement)
        assert params == {"id": 123}
        assert db.commit.call_count == 2

    def test_revoke_api_key_not_exists(self, db, mock_current_user):
        """测试撤销不存在的密钥"""
        db.execute.return_value.fetchone.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            revoke_api_key(999, db=db, current_user=mock_current_user)
        assert exc_info.value.status_code == 404

    def test_revoke_api_key_permission_denied(self, db, mock_current_user):
        """测试无权限撤销密钥"""
        mock_current_user["role"] = "viewer"
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            revoke_api_key(123, db=db, current_user=mock_current_user)
        assert exc_info.value.status_code == 403
        db.execute.assert_not_called()

    def test_revoke_api_key_failure(self, db, mock_current_user):
        """测试撤销密钥失败"""
        db.execute.side_effect = [None, RuntimeError("update failed")]
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            revoke_api_key(123, db=db, current_user=mock_current_user)
        assert exc_info.value.status_code == 500
        db.rollback.assert_called_once()

    def test_delete_api_key_exists(self, db, mock_current_user, key):
        """测试删除存在的密钥"""
        db.execute.return_value.fetchone.return_value = key
        
        result = delete_api_key(123, db=db, current_user=mock_current_user)
        assert result["message"] == "密钥已删除"
        assert db.execute.call_args.args[1] == {"id": 123}
        assert "DELETE FROM api_keys" in str(db.execute.call_args.args[0])

    def test_delete_api_key_not_exists(self, db, mock_current_user):
        """测试删除不存在的密钥"""
        db.execute.return_value.fetchone.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            delete_api_key(999, db=db, current_user=mock_current_user)
        assert exc_info.value.status_code == 404

    def test_delete_api_key_permission_denied(self, db, mock_current_user):
        """测试无权限删除密钥"""
        mock_current_user["role"] = "viewer"
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            delete_api_key(123, db=db, current_user=mock_current_user)
        assert exc_info.value.status_code == 403
        db.execute.assert_not_called()

    def test_delete_api_key_failure(self, db, mock_current_user):
        """测试删除密钥失败"""
        db.execute.side_effect = [None, RuntimeError("delete failed")]
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            delete_api_key(123, db=db, current_user=mock_current_user)
        assert exc_info.value.status_code == 500
        db.rollback.assert_called_once()

    def test_get_key_usage_exists(self, db, mock_current_user, key):
        """测试获取存在的密钥使用日志"""
        db.execute.return_value.fetchone.return_value = key
        result = get_key_usage(123, 10, db=db, current_user=mock_current_user)
        assert result["id"] == 123
        assert result["last_used_at"] == "2026-04-07T00:00:00"
        assert result["history_available"] is False
        assert db.execute.call_args.args[1] == {"id": 123}

    def test_get_key_usage_not_exists(self, db, mock_current_user):
        """测试获取不存在密钥的使用日志"""
        db.execute.return_value.fetchone.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_key_usage(999, 10, db=db, current_user=mock_current_user)
        assert exc_info.value.status_code == 404

    def test_get_key_usage_permission_denied(self, db, mock_current_user):
        """测试无权限获取密钥使用日志"""
        mock_current_user["role"] = "viewer"
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_key_usage(123, 10, db=db, current_user=mock_current_user)
        assert exc_info.value.status_code == 403
        db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_api_key_valid(self, db, key):
        """测试验证有效的API密钥"""
        db.execute.return_value.fetchone.return_value = key
        
        result = await verify_api_key("valid-api-key", db=db)
        assert result["authenticated"] is True
        select = db.execute.call_args_list[1]
        assert select.args[1] == {"key_hash": _hash_key("valid-api-key")}
        assert "is_active = TRUE" in str(select.args[0])
        assert "last_used_at = NOW()" in str(db.execute.call_args.args[0])

    @pytest.mark.asyncio
    async def test_verify_api_key_invalid(self, db):
        """测试验证无效的API密钥"""
        db.execute.return_value.fetchone.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_key("invalid-api-key", db=db)
        assert exc_info.value.status_code == 401

    def test_validate_empty_key_does_not_query(self, db):
        assert validate_api_key("", db=db) is False
        db.execute.assert_not_called()

    def test_rotate_key_replaces_hash_and_only_returns_secret_once(self, db, mock_current_user, key):
        db.execute.return_value.fetchone.return_value = key
        result = rotate_api_key(123, db=db, current_user=mock_current_user)
        params = db.execute.call_args.args[1]
        assert params["key_hash"] == _hash_key(result["key"])
        assert params["key_prefix"] == result["key_prefix"]
        assert result["key"] not in params.values()
        assert result["id"] == 123

    @pytest.mark.parametrize("operation", [
        create_api_key, list_api_keys, get_api_key, revoke_api_key,
        delete_api_key, rotate_api_key, get_key_usage,
    ])
    def test_platform_key_management_requires_admin(self, db, operation):
        args = [] if operation is list_api_keys else [123]
        if operation is create_api_key:
            args = [CreateAPIKeyReq(name="not authorized")]
        with pytest.raises(HTTPException) as exc_info:
            operation(*args, db=db, current_user={"user_id": 456, "role": "viewer"})
        assert exc_info.value.status_code == 403
        db.execute.assert_not_called()

    def test_key_lifecycle_persists_without_plaintext(self, monkeypatch, mock_current_user):
        """执行真实 SQL，验证撤销后原密钥不能再鉴权，轮换不保留旧哈希。"""
        engine = create_engine(
            "sqlite://", connect_args={"detect_types": sqlite3.PARSE_DECLTYPES},
        )
        with engine.connect() as connection:
            connection.connection.create_function(
                "NOW", 0, lambda: datetime.now().isoformat(" "),
            )
        ddl = api_key._CREATE_TABLE_SQL.replace(
            "id SERIAL PRIMARY KEY", "id INTEGER PRIMARY KEY",
        ).replace("DEFAULT NOW()", "DEFAULT CURRENT_TIMESTAMP")
        monkeypatch.setattr(api_key, "_CREATE_TABLE_SQL", ddl)
        try:
            with Session(engine) as db:
                created = create_api_key(
                    CreateAPIKeyReq(name="persisted"), db=db, current_user=mock_current_user,
                )
                key_id, raw = created["id"], created["key"]
                assert validate_api_key(raw, db) is True
                assert get_key_usage(
                    key_id, db=db, current_user=mock_current_user,
                )["last_used_at"] is not None
                revoke_api_key(key_id, db=db, current_user=mock_current_user)
                assert validate_api_key(raw, db) is False
                assert get_api_key(
                    key_id, db=db, current_user=mock_current_user,
                )["is_active"] == 0
                rotated = rotate_api_key(key_id, db=db, current_user=mock_current_user)
                assert rotated["key"] != raw
                assert validate_api_key(raw, db) is False
                assert validate_api_key(rotated["key"], db) is True
                stored = db.execute(text("SELECT key_hash FROM api_keys")).scalar_one()
                assert stored == _hash_key(rotated["key"])
                assert stored not in {raw, rotated["key"]}
                delete_api_key(key_id, db=db, current_user=mock_current_user)
                assert validate_api_key(rotated["key"], db) is False
        finally:
            engine.dispose()