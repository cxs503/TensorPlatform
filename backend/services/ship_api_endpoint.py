"""船舶总体性能平台端点：单一事实源（single source of truth）。

背景（2026-11-01 事故复盘）
------------------------
船舶平台真实监听 ``:8001``，但平台内四条调用链曾各自硬编码 ``:8002``：

* ``backend/services/ship_proxy.py``      —— 阻力/稳性代理 + 批量 sweep
* ``backend/api/domain/ship_batch.py``    —— 批量计算端点
* ``backend/api/domain/ship_benchmark.py``—— benchmark 对标
* ``tools/optimizer/toy_optimize.py``     —— 优化器代理目标函数

``:8002`` 上没有监听进程，于是这些链路全部 Connection refused；与此同时
``:8001`` 自身宕机无人守护（autoheal 名单里没有它），造成"双重静默失效"：
能力审计只能给出 WITHHELD，而且没人知道是端口错还是服务死。

本模块把端点收敛为一处解析，任何新代码都必须经由它取 URL。

解析优先级
----------
1. 显式入参 ``env`` 中的 ``SHIP_API_BASE``（可写成 origin 或含 ``/api/v1``）
2. ``SHIP_PLATFORM_URL``（历史别名，保留向后兼容）
3. 默认 ``http://127.0.0.1:8001``

三种写法都会被规范化：``http://h:8001``、``http://h:8001/``、
``http://h:8001/api/v1``、``http://h:8001/api/v1/``。
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request
from typing import Dict, Mapping, Optional

DEFAULT_PORT = 8001
DEFAULT_ORIGIN = f"http://127.0.0.1:{DEFAULT_PORT}"
API_PREFIX = "/api/v1"

# 环境变量名，按优先级排序；第一个非空者生效
ENV_KEYS = ("SHIP_API_BASE", "SHIP_PLATFORM_URL")


def _normalize(raw: str) -> str:
    """把任意写法的端点规范化为 origin（不带 ``/api/v1``、不带尾斜杠）。"""
    value = raw.strip().rstrip("/")
    if value.endswith(API_PREFIX):
        value = value[: -len(API_PREFIX)]
    return value.rstrip("/")


def ship_api_origin(env: Optional[Mapping[str, str]] = None) -> str:
    """返回船舶平台 origin，例如 ``http://127.0.0.1:8001``。"""
    source: Mapping[str, str] = os.environ if env is None else env
    for key in ENV_KEYS:
        raw = (source.get(key) or "").strip()
        if raw:
            return _normalize(raw)
    return DEFAULT_ORIGIN


def ship_api_base(env: Optional[Mapping[str, str]] = None) -> str:
    """返回船舶平台 API 根，例如 ``http://127.0.0.1:8001/api/v1``。"""
    return ship_api_origin(env) + API_PREFIX


def ship_api_url(path: str, env: Optional[Mapping[str, str]] = None) -> str:
    """拼出完整 URL；``path`` 可带或不带前导斜杠，也可已含 ``api/v1``。"""
    clean = path.lstrip("/")
    if clean.startswith("api/v1/"):
        clean = clean[len("api/v1/"):]
    return f"{ship_api_base(env)}/{clean}"


def probe(
    env: Optional[Mapping[str, str]] = None,
    timeout: float = 3.0,
) -> Dict[str, object]:
    """健康探测，返回结构化结果（供看板 / 守护进程 / 测试消费）。

    不抛异常：网络失败也返回 ``ok=False`` 并带上原因，调用方据此决策。
    船舶平台的健康路由只挂在根路径 ``/health``（业务路由才在 ``/api/v1`` 下），
    因此依次尝试 ``{base}/health`` 与 ``{origin}/health``，任一 2xx 即视为在线。
    """
    origin = ship_api_origin(env)
    candidates = [ship_api_url("health", env), origin + "/health"]
    result: Dict[str, object] = {
        "url": candidates[0],
        "ok": False,
        "status": None,
        "detail": "",
        "tried": [],
    }
    for url in candidates:
        result["tried"].append(url)  # type: ignore[union-attr]
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (内网固定地址)
                body = resp.read().decode("utf-8", errors="replace")
                result.update(
                    {"url": url, "status": resp.status, "ok": 200 <= resp.status < 300,
                     "detail": body[:300]}
                )
                return result
        except urllib.error.HTTPError as exc:  # 服务在但路由/鉴权异常
            result.update({"url": url, "status": exc.code, "detail": f"HTTP {exc.code}"})
        except Exception as exc:  # noqa: BLE001 - 连接拒绝/超时/解析失败统一归类
            result.update({"url": url, "detail": f"{type(exc).__name__}: {exc}"})
    return result


__all__ = [
    "DEFAULT_ORIGIN",
    "DEFAULT_PORT",
    "API_PREFIX",
    "ENV_KEYS",
    "ship_api_origin",
    "ship_api_base",
    "ship_api_url",
    "probe",
]