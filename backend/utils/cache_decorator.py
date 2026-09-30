"""
缓存装饰器
"""
import hashlib
import json
from functools import wraps

from services.cache import get_cache_service


def cached(key_prefix: str, expire: int = 300):
    """缓存装饰器"""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache = get_cache_service()

            # 生成缓存key
            key_data = json.dumps(
                {"args": str(args), "kwargs": str(kwargs)}, sort_keys=True
            )
            key_hash = hashlib.md5(key_data.encode()).hexdigest()[:8]
            cache_key = f"{key_prefix}:{key_hash}"

            # 尝试从缓存获取
            cached_value = cache.get(cache_key)
            if cached_value is not None:
                return cached_value

            # 执行函数并缓存结果
            result = func(*args, **kwargs)
            cache.set(cache_key, result, expire)
            return result

        return wrapper

    return decorator


def invalidate_cache(pattern: str):
    """清除缓存装饰器"""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache = get_cache_service()
            cache.clear_pattern(f"{pattern}:*")
            return func(*args, **kwargs)

        return wrapper

    return decorator
