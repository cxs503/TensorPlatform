"""
API密钥管理服务
支持API Key生成、权限控制、使用限制、调用日志
"""
import hashlib
import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# 密钥存储目录
import os
KEY_DIR = Path(os.environ.get("KEY_DIR", "/tmp/foundation-keys"))
KEY_DIR.mkdir(parents=True, exist_ok=True)


class APIKey:
    """API密钥"""

    def __init__(
        self,
        key_id: str,
        key_hash: str,
        name: str,
        user_id: int,
        permissions: List[str] = None,
        rate_limit: int = 100,  # 每分钟请求数
        expires_at: Optional[datetime] = None,
    ):
        self.key_id = key_id
        self.key_hash = key_hash
        self.name = name
        self.user_id = user_id
        self.permissions = permissions or []
        self.rate_limit = rate_limit
        self.expires_at = expires_at
        self.created_at = datetime.now()
        self.last_used = None
        self.is_active = True
        self.usage_count = 0


class APIKeyManager:
    """API密钥管理器"""

    def __init__(self):
        self.keys: Dict[str, APIKey] = {}
        self.usage: List[Dict] = []
        self.max_log_size = 10000
        self._load_keys()

    def _load_keys(self):
        """从文件加载密钥"""
        keys_file = KEY_DIR / "api_keys.json"
        if keys_file.exists():
            with open(keys_file, "r") as f:
                data = json.load(f)
                for key_id, key_data in data.items():
                    key = APIKey(
                        key_id,
                        key_data["key_hash"],
                        key_data["name"],
                        key_data["user_id"],
                        key_data.get("permissions", []),
                        key_data.get("rate_limit", 100),
                    )
                    key.created_at = datetime.fromisoformat(
                        key_data.get("created_at", datetime.now().isoformat())
                    )
                    key.last_used = (
                        datetime.fromisoformat(key_data["last_used"])
                        if key_data.get("last_used")
                        else None
                    )
                    key.is_active = key_data.get("is_active", True)
                    key.usage_count = key_data.get("usage_count", 0)
                    self.keys[key_id] = key

    def _save_keys(self):
        """保存密钥到文件"""
        data = {}
        for key_id, key in self.keys.items():
            data[key_id] = {
                "key_hash": key.key_hash,
                "name": key.name,
                "user_id": key.user_id,
                "permissions": key.permissions,
                "rate_limit": key.rate_limit,
                "created_at": key.created_at.isoformat(),
                "last_used": key.last_used.isoformat() if key.last_used else None,
                "is_active": key.is_active,
                "usage_count": key.usage_count,
            }
        keys_file = KEY_DIR / "api_keys.json"
        with open(keys_file, "w") as f:
            json.dump(data, f, indent=2)

    def _hash_key(self, key: str) -> str:
        """哈希密钥"""
        return hashlib.sha256(key.encode()).hexdigest()

    def generate_key(
        self,
        name: str,
        user_id: int,
        permissions: List[str] = None,
        rate_limit: int = 100,
        days: int = 365,
    ) -> tuple:
        """生成新的API密钥"""
        # 生成随机密钥
        key = f"kl_{secrets.token_urlsafe(32)}"
        key_id = f"key_{secrets.token_hex(8)}"
        key_hash = self._hash_key(key)

        # 设置过期时间
        expires_at = datetime.now() + timedelta(days=days)

        # 创建密钥对象
        api_key = APIKey(
            key_id,
            key_hash,
            name,
            user_id,
            permissions,
            rate_limit,
            expires_at,
        )

        self.keys[key_id] = api_key
        self._save_keys()

        # 返回完整密钥（只显示一次）
        return key_id, key

    def verify_key(self, key: str) -> Optional[APIKey]:
        """验证密钥"""
        key_hash = self._hash_key(key)

        for api_key in self.keys.values():
            if api_key.key_hash == key_hash:
                # 检查是否激活
                if not api_key.is_active:
                    return None
                # 检查是否过期
                if api_key.expires_at and api_key.expires_at < datetime.now():
                    return None
                # 更新使用时间
                api_key.last_used = datetime.now()
                api_key.usage_count += 1
                self._save_keys()
                return api_key

        return None

    def get_key(self, key_id: str) -> Optional[APIKey]:
        """获取密钥信息"""
        return self.keys.get(key_id)

    def list_keys(self, user_id: Optional[int] = None) -> List[Dict]:
        """列出密钥"""
        keys = list(self.keys.values())
        if user_id:
            keys = [k for k in keys if k.user_id == user_id]

        return [
            {
                "key_id": k.key_id,
                "name": k.name,
                "user_id": k.user_id,
                "permissions": k.permissions,
                "rate_limit": k.rate_limit,
                "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                "created_at": k.created_at.isoformat(),
                "last_used": k.last_used.isoformat() if k.last_used else None,
                "is_active": k.is_active,
                "usage_count": k.usage_count,
            }
            for k in keys
        ]

    def revoke_key(self, key_id: str) -> bool:
        """撤销密钥"""
        if key_id in self.keys:
            self.keys[key_id].is_active = False
            self._save_keys()
            return True
        return False

    def delete_key(self, key_id: str) -> bool:
        """删除密钥"""
        if key_id in self.keys:
            del self.keys[key_id]
            self._save_keys()
            return True
        return False

    def check_permission(self, key: str, permission: str) -> bool:
        """检查密钥权限"""
        api_key = self.verify_key(key)
        if not api_key:
            return False
        # admin拥有所有权限
        if "admin" in api_key.permissions:
            return True
        return permission in api_key.permissions

    def log_usage(self, key_id: str, endpoint: str, method: str, status_code: int):
        """记录使用日志"""
        self.usage_log.append(
            {
                "key_id": key_id,
                "endpoint": endpoint,
                "method": method,
                "status_code": status_code,
                "timestamp": datetime.now().isoformat(),
            }
        )
        # 限制日志大小
        if len(self.usage_log) > self.max_log_size:
            self.usage_log = self.usage_log[-self.max_log_size :]

    def get_usage_log(
        self, key_id: Optional[str] = None, limit: int = 100
    ) -> List[Dict]:
        """获取使用日志"""
        logs = self.usage_log
        if key_id:
            logs = [l for l in logs if l.get("key_id") == key_id]
        return logs[-limit:]


# 全局实例
_api_key_manager: Optional[APIKeyManager] = None


def get_api_key_manager() -> APIKeyManager:
    """获取API密钥管理器"""
    global _api_key_manager
    if _api_key_manager is None:
        _api_key_manager = APIKeyManager()
    return _api_key_manager
