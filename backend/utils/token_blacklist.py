"""
Token黑名单管理
使用Redis存储已注销的Token
"""
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import redis

    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("Redis不可用，使用内存黑名单")


class TokenBlacklist:
    """Token黑名单管理器"""

    def __init__(self):
        self._use_redis = False
        self._memory_blacklist = {}

        if REDIS_AVAILABLE:
            try:
                self._redis = redis.Redis(
                    host=os.getenv("REDIS_HOST", "localhost"),
                    port=int(os.getenv("REDIS_PORT", 6379)),
                    password=os.getenv("REDIS_PASSWORD", None),
                    db=1,
                    decode_responses=True,
                    socket_timeout=5,
                )
                self._redis.ping()
                self._use_redis = True
                logger.info("Token黑名单使用Redis存储")
            except Exception as e:
                logger.warning(f"Redis连接失败，使用内存黑名单: {e}")

    def add(self, token: str, expires_in_seconds: int) -> bool:
        """将Token加入黑名单"""
        try:
            if self._use_redis:
                key = f"blacklist:{token[:32]}"
                self._redis.setex(key, expires_in_seconds, "1")
                return True
            else:
                self._memory_blacklist[token] = time.time() + expires_in_seconds
                self._cleanup_memory()
                return True
        except Exception as e:
            logger.error(f"添加Token到黑名单失败: {e}")
            return False

    def is_blacklisted(self, token: str) -> bool:
        """检查Token是否在黑名单中"""
        try:
            if self._use_redis:
                key = f"blacklist:{token[:32]}"
                return self._redis.exists(key) > 0
            else:
                if token in self._memory_blacklist:
                    expiry = self._memory_blacklist[token]
                    if time.time() < expiry:
                        return True
                    else:
                        del self._memory_blacklist[token]
                return False
        except Exception as e:
            logger.error(f"检查Token黑名单失败: {e}")
            return False

    def _cleanup_memory(self):
        """清理过期条目"""
        now = time.time()
        expired = [k for k, v in self._memory_blacklist.items() if v < now]
        for k in expired:
            del self._memory_blacklist[k]


token_blacklist = TokenBlacklist()
