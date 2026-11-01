"""
Ship API Proxy — 封装对 port 8002 船舶计算服务的 HTTP 调用。

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

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────
SHIP_API_BASE = "http://localhost:8002/api/v1"
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

    封装对 port 8002 的 HTTP 调用，支持：
    - resistance（阻力计算）
    - stability（稳性计算）
    - 自动重试 + 超时
    - 错误处理与日志
    """

    def __init__(
        self,
        base_url: str = SHIP_API_BASE,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
    ):
        self.base_url = base_url.rstrip("/")
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
# 平台精度标准：对标参考值误差 >15% 不可接受（docs/QUALITY_BAR.md）。任何
# tolerance_pct > POLICY_MAX_TOLERANCE_PCT 的门禁「永远不会失败」，按 PB-005
# 不算门禁，必须显式 gate_enabled=False 而不是把阈值放大到能过。
POLICY_MAX_TOLERANCE_PCT = 15.0

# 基准参考值不再手抄 R_T / GM：一律由可复算的解析关系派生，避免基线腐坏。
#   R_T = Ct · ½ ρ S V²            （Ct 为试验参考、ρ 为流体密度、S 为湿表面积）
#   GM  = KB + BM − KG            （墙侧公式：KB=T/2, BM=I_wp/∇）
# 审计工具：tools/audit/baseline_integrity.py（C1 恒等式/C5 跨库一致/C6 门禁策略）。


def derive_rt_from_ct(
    ct: float, rho: float, wetted_surface_m2: float, speed_ms: float
) -> float:
    """由总阻力系数反算总阻力 R_T [N]（Ct 恒等式）。"""
    if speed_ms <= 0:
        raise ValueError("speed_ms must be positive")
    return ct * 0.5 * rho * wetted_surface_m2 * speed_ms ** 2


def wall_sided_gm(
    length: float, beam: float, draft: float, cb: float, kg: float
) -> Dict[str, float]:
    """墙侧假设下的解析初稳性高（方箱/驳船近似，用于基准值派生）。

    KB = T/2，BM = I_wp/∇，GM = KB + BM − KG。
    对舷侧近垂直的箱型驳船 Cb≈0.8-0.9 该式与静水力曲线积分一致到 ~1%。
    """
    kb = 0.5 * draft
    displacement_volume = length * beam * draft * cb
    waterplane_inertia = length * beam ** 3 / 12.0
    bm = waterplane_inertia / displacement_volume
    return {
        "kb": kb,
        "bm": bm,
        "km": kb + bm,
        "gm": kb + bm - kg,
    }


def _build_benchmark_reference() -> Dict[str, Any]:
    """构造基准表：R_T / GM 全部由 Ct 恒等式与墙侧公式派生（零手抄）。"""
    kvlcc2 = {
        # KVLCC2 全尺度，取自 backend/data/benchmark_reference.json: KVLCC2-02
        "ship_type": "KVLCC2",
        "params": {
            "length": 320.0, "beam": 58.0, "draft": 20.8,
            "cb": 0.8098, "displacement": 307000, "speed": 15.0,
        },
        "reference_ct": 0.0030,
        "speed_ms": 7.717,               # 15 kn = 7.717 m/s（params.speed 为节）
        "wetted_surface_m2": 25678.0,     # Holtrop 估算（dataset 无实测值）
        "fluid_density_kg_m3": 1025.0,    # 海水 15 °C
        "tolerance_pct": 15.0,
        "gate_enabled": True,
        "provenance": "Ct=0.0030 @15kn (SIMMAN/KVLCC2 基准库 KVLCC2-02)；"
                      "R_T 由 Ct 恒等式派生",
    }
    suboff = {
        # SUBOFF AFF-1（Roddy 1990），3 m/s，淡水
        "ship_type": "SUBOFF",
        "params": {
            "length": 4.356, "beam": 0.508, "draft": 0.508,
            "cb": 0.5, "displacement": 500, "speed": 3.0,
        },
        "reference_ct": 0.0038,
        "speed_ms": 3.0,
        "wetted_surface_m2": 5.42,
        "fluid_density_kg_m3": 1000.0,    # 淡水
        "tolerance_pct": 15.0,
        "gate_enabled": False,            # Holtrop 对潜艇外形不成立 → 不作门禁
        "status": "honest_fail_not_a_gate",
        "note": "honest-FAIL: Holtrop 方法不适用于潜艇外形（Fr>0.4），"
                "历史实现把容差放到 300% 使其恒过，等于没有门禁；"
                "现改为 gate_enabled=False，结果以 WITHHELD 输出。",
        "provenance": "Ct=0.0038 @3m/s (Roddy 1990, DTRC-90/012)；"
                      "R_T 由 Ct 恒等式派生",
    }
    for case in (kvlcc2, suboff):
        case["reference_rt"] = round(
            derive_rt_from_ct(
                case["reference_ct"], case["fluid_density_kg_m3"],
                case["wetted_surface_m2"], case["speed_ms"],
            ), 2)

    barge_params = {
        "length": 80.0, "beam": 20.0, "draft": 4.0,
        "cb": 0.85, "displacement": 10000, "kg": 5.0,
    }
    barge_gm = wall_sided_gm(
        barge_params["length"], barge_params["beam"], barge_params["draft"],
        barge_params["cb"], barge_params["kg"],
    )
    barge = {
        "params": barge_params,
        "reference_gm": round(barge_gm["gm"], 3),   # 解析值 6.804 m（旧手抄值 3.5 m 错 ~94%）
        "tolerance_pct": 15.0,
        "gate_enabled": True,
        "note": "墙侧公式解析基准（KB=T/2, BM=I_wp/∇, KG=5.0）；"
                "旧手抄参考值 3.5 m 与解析值不符，已纠正。",
        "provenance": f"解析式派生: KB={barge_gm['kb']:.3f}, BM={barge_gm['bm']:.3f}, "
                      f"KM={barge_gm['km']:.3f}",
    }
    return {"resistance": {"kvlcc2_full": kvlcc2, "suboff_model": suboff},
            "stability": {"generic_barge": barge}}


BENCHMARK_REFERENCE = _build_benchmark_reference()


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
            key = "rt"
            reference = ref_data["reference_rt"]
        elif calc_type == "stability":
            result = await proxy.stability(ref_data["params"])
            key = "gm"
            reference = ref_data["reference_gm"]
        else:
            return {"error": f"Unsupported calc_type: {calc_type}", "pass": False}

        # 缺键不可静默按 0 处理：0 会被判成「100% 误差」的假结果（也是假门禁的反面）。
        if not isinstance(result, dict) or key not in result:
            return {
                "case_name": case_name,
                "calc_type": calc_type,
                "params": ref_data["params"],
                "error": f"upstream response missing '{key}'",
                "reference_value": reference,
                "status": "WITHHELD",
                "pass": None,
                "note": ref_data.get("note", ""),
            }
        computed = result[key]

        # 恒过门禁（tolerance > 策略上限 or 显式 gate_enabled=False）不算验证：
        # 照实输出 WITHHELD，不给出 pass=True 的假绿灯（PB-005）。
        gate_on = bool(ref_data.get("gate_enabled", True))
        tolerance = float(ref_data.get("tolerance_pct", POLICY_MAX_TOLERANCE_PCT))
        if not gate_on or tolerance > POLICY_MAX_TOLERANCE_PCT:
            return {
                "case_name": case_name,
                "calc_type": calc_type,
                "params": ref_data["params"],
                "computed_value": round(float(computed), 4),
                "reference_value": reference,
                "tolerance_pct": tolerance,
                "gate_enabled": gate_on,
                "status": "WITHHELD",
                "pass": None,
                "note": ref_data.get("note", ""),
                "reason": "case is not a gate (tolerance > policy max or gate_enabled=False)",
            }

        if reference == 0:
            error_pct = float("inf") if computed != 0 else 0.0
        else:
            error_pct = abs(computed - reference) / abs(reference) * 100

        passed = error_pct <= tolerance

        return {
            "case_name": case_name,
            "calc_type": calc_type,
            "params": ref_data["params"],
            "computed_value": round(computed, 4),
            "reference_value": reference,
            "error_provenance": ref_data.get("provenance", ""),
            "error_pct": round(error_pct, 2),
            "tolerance_pct": tolerance,
            "gate_enabled": gate_on,
            "status": "PASS" if passed else "FAIL",
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
