"""
Ship API Proxy — 封装对船舶总体性能平台（默认 :8001）的 HTTP 调用。

功能：
- resistance / stability 代理调用
- 重试 / 超时 / 错误处理
- 计时装饰器（注入代理层）
- 批量/并发调用支持
"""
from __future__ import annotations

import asyncio
import functools
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from services.ship_api_endpoint import ship_api_base

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────
# 端点由 services.ship_api_endpoint 统一解析（历史事故：此处曾写死 :8002，
# 而船舶平台监听 :8001，导致代理全链路 Connection refused）。``SHIP_API_BASE``
# 保留为模块级常量供外部读取，实际取值在 ShipProxy 实例化时按环境变量重解析。
SHIP_API_BASE = ship_api_base()
DEFAULT_TIMEOUT = 30.0  # 秒
DEFAULT_RETRIES = 3
DEFAULT_RETRY_DELAY = 0.5  # 秒

# ── 计时存储 ──────────────────────────────────────────
_timing_records: List[float] = []


def record_timing(duration_ms: float) -> None:
    """记录一次调用的耗时（毫秒）。"""
    _timing_records.append(duration_ms)


def get_timing_stats() -> Dict[str, float]:
    """返回 p50 / p90 / p99 计时统计。"""
    if not _timing_records:
        return {"p50": 0.0, "p90": 0.0, "p99": 0.0, "count": 0}
    sorted_records = sorted(_timing_records)
    n = len(sorted_records)

    def percentile(p: float) -> float:
        idx = int(p / 100.0 * (n - 1))
        return sorted_records[idx]

    return {
        "p50": round(percentile(50), 2),
        "p90": round(percentile(90), 2),
        "p99": round(percentile(99), 2),
        "count": n,
        "min": round(min(sorted_records), 2),
        "max": round(max(sorted_records), 2),
        "mean": round(sum(sorted_records) / n, 2),
    }


def timed_call(func: Callable) -> Callable:
    """计时装饰器：记录被装饰函数的执行时间（毫秒）。"""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            result = await func(*args, **kwargs)
            return result
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            record_timing(duration_ms)

    return wrapper


def timed_call_sync(func: Callable) -> Callable:
    """同步计时装饰器。"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            record_timing(duration_ms)

    return wrapper


# ── 代理核心 ──────────────────────────────────────────
class ShipProxyError(Exception):
    """船舶代理调用异常。"""

    def __init__(self, message: str, status_code: int = 0, detail: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class ShipProxy:
    """
    船舶计算服务代理。

    封装对船舶平台（默认 :8001）的 HTTP 调用，支持：
    - resistance（阻力计算）
    - stability（稳性计算）
    - 自动重试 + 超时
    - 错误处理与日志
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
    ):
        # 实例化时解析，便于测试用环境变量或显式入参覆盖
        self.base_url = (base_url or ship_api_base()).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    async def _request(
        self, method: str, endpoint: str, body: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """带重试的 HTTP 请求。"""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        last_error = None

        for attempt in range(1, self.max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(self.timeout)
                ) as client:
                    if method == "POST":
                        response = await client.post(url, json=body)
                    else:
                        response = await client.get(url)

                    if response.status_code == 200:
                        data = response.json()
                        return data.get("data", data) if isinstance(data, dict) else data

                    # 非 200 响应
                    detail = ""
                    try:
                        detail = response.text
                    except Exception:
                        pass
                    last_error = ShipProxyError(
                        f"HTTP {response.status_code} from {url}",
                        status_code=response.status_code,
                        detail=detail,
                    )
                    logger.warning(
                        "Ship API attempt %d/%d failed: %s",
                        attempt,
                        self.max_retries,
                        last_error,
                    )

            except httpx.TimeoutError as e:
                last_error = ShipProxyError(
                    f"Timeout after {self.timeout}s calling {url}",
                    status_code=504,
                    detail=str(e),
                )
                logger.warning(
                    "Ship API attempt %d/%d timeout: %s",
                    attempt,
                    self.max_retries,
                    last_error,
                )
            except httpx.RequestError as e:
                last_error = ShipProxyError(
                    f"Request error calling {url}: {e}",
                    status_code=502,
                    detail=str(e),
                )
                logger.warning(
                    "Ship API attempt %d/%d error: %s",
                    attempt,
                    self.max_retries,
                    last_error,
                )
            except Exception as e:
                last_error = ShipProxyError(
                    f"Unexpected error calling {url}: {e}",
                    status_code=500,
                    detail=str(e),
                )
                logger.error(
                    "Ship API attempt %d/%d unexpected: %s",
                    attempt,
                    self.max_retries,
                    last_error,
                )

            if attempt < self.max_retries:
                await asyncio.sleep(self.retry_delay * attempt)

        raise last_error or ShipProxyError(f"All {self.max_retries} attempts failed for {url}")

    @timed_call
    async def resistance(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        调用阻力计算。

        参数示例：
        {
            "length": 100, "beam": 15, "draft": 5,
            "cb": 0.6, "displacement": 5000, "speed": 12
        }
        """
        return await self._request("POST", "calculations/resistance", body=params)

    @timed_call
    async def stability(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        调用稳性计算。

        参数示例：
        {
            "length": 100, "beam": 15, "draft": 5,
            "cb": 0.6, "displacement": 5000, "kg": 6
        }
        """
        return await self._request("POST", "calculations/stability", body=params)

    async def batch_call(
        self,
        calc_type: str,
        param_list: List[Dict[str, Any]],
        concurrent: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        批量调用指定计算类型。

        Args:
            calc_type: "resistance" 或 "stability"
            param_list: 参数列表
            concurrent: 是否并发调用

        Returns:
            结果列表，每个元素为 {"params": ..., "result": ..., "error": ...}
        """
        dispatcher = {
            "resistance": self.resistance,
            "stability": self.stability,
        }
        if calc_type not in dispatcher:
            raise ValueError(f"Unsupported calc_type: {calc_type}")

        calc_fn = dispatcher[calc_type]

        if concurrent:
            tasks = [calc_fn(params) for params in param_list]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        else:
            results = []
            for params in param_list:
                try:
                    results.append(await calc_fn(params))
                except Exception as e:
                    results.append(e)

        output = []
        for params, result in zip(param_list, results):
            if isinstance(result, Exception):
                output.append({
                    "params": params,
                    "result": None,
                    "error": str(result),
                })
            else:
                output.append({
                    "params": params,
                    "result": result,
                    "error": None,
                })
        return output

    async def batch_sweep(
        self,
        param_matrix: Dict[str, List[Any]],
        calc_type: str = "resistance",
        concurrent: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        参数扫描：从参数矩阵生成笛卡尔积，批量调用。

        Args:
            param_matrix: {"speed": [10, 12, 14], "cb": [0.5, 0.6]}
            calc_type: 计算类型
            concurrent: 是否并发

        Returns:
            结果数组
        """
        from itertools import product

        keys = list(param_matrix.keys())
        values = [param_matrix[k] for k in keys]
        param_list = [dict(zip(keys, combo)) for combo in product(*values)]

        return await self.batch_call(calc_type, param_list, concurrent=concurrent)


# ── 基准验证 ──────────────────────────────────────────
# 内置参考数据（用于 benchmark_validate）
BENCHMARK_REFERENCE = {
    "resistance": {
        # KVLCC2 典型工况参考值（Holtrop 方法）
        "kvlcc2_full": {
            "params": {
                "length": 320.0, "beam": 58.0, "draft": 20.8,
                "cb": 0.8098, "displacement": 310000, "speed": 15.0
            },
            "reference_rt": 2500.0,  # kN（参考值，允许 ±15%）
            "tolerance_pct": 15.0,
        },
        # SUBOFF 模型（注意：Holtrop 对潜艇外形误差极大，honest-FAIL）
        "suboff_model": {
            "params": {
                "length": 4.356, "beam": 0.508, "draft": 0.508,
                "cb": 0.5, "displacement": 500, "speed": 3.0
            },
            "reference_rt": 15.0,  # N（实验参考值）
            "tolerance_pct": 300.0,  # Holtrop 对 SUBOFF 误差可达 300%+
            "note": "honest-FAIL: Holtrop 方法不适用于潜艇外形，误差 >300%",
        },
    },
    "stability": {
        "generic_barge": {
            "params": {
                "length": 80.0, "beam": 20.0, "draft": 4.0,
                "cb": 0.85, "displacement": 10000, "kg": 5.0
            },
            "reference_gm": 3.5,
            "tolerance_pct": 15.0,
        },
    },
}


async def benchmark_validate(
    calc_type: str,
    case_name: str,
    proxy: Optional[ShipProxy] = None,
) -> Dict[str, Any]:
    """
    基准验证：调用代理获取计算值，与内置参考数据比较。

    Returns:
        {
            "case_name": ...,
            "calc_type": ...,
            "computed_value": ...,
            "reference_value": ...,
            "error_pct": ...,
            "pass": bool,
            "note": ...
        }
    """
    if proxy is None:
        proxy = ShipProxy()

    ref_data = BENCHMARK_REFERENCE.get(calc_type, {}).get(case_name)
    if not ref_data:
        return {
            "case_name": case_name,
            "calc_type": calc_type,
            "error": f"Unknown benchmark case: {calc_type}/{case_name}",
            "pass": False,
        }

    try:
        if calc_type == "resistance":
            result = await proxy.resistance(ref_data["params"])
            computed = result.get("rt", 0)
            reference = ref_data["reference_rt"]
            key = "rt"
        elif calc_type == "stability":
            result = await proxy.stability(ref_data["params"])
            computed = result.get("gm", 0)
            reference = ref_data["reference_gm"]
            key = "gm"
        else:
            return {"error": f"Unsupported calc_type: {calc_type}", "pass": False}

        if reference == 0:
            error_pct = float("inf") if computed != 0 else 0.0
        else:
            error_pct = abs(computed - reference) / abs(reference) * 100

        tolerance = ref_data.get("tolerance_pct", 15.0)
        passed = error_pct <= tolerance

        return {
            "case_name": case_name,
            "calc_type": calc_type,
            "params": ref_data["params"],
            "computed_value": round(computed, 4),
            "reference_value": reference,
            "error_pct": round(error_pct, 2),
            "tolerance_pct": tolerance,
            "pass": passed,
            "note": ref_data.get("note", ""),
        }
    except ShipProxyError as e:
        return {
            "case_name": case_name,
            "calc_type": calc_type,
            "error": str(e),
            "pass": False,
        }
