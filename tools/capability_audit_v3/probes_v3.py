"""v3 探针引擎：对 21 项能力逐一发射不变量探针，返回判级结果。

设计原则：
1. 每项能力至少 1 个探针（WITHHELD/MOCK/STUB 优先）
2. 探针类型：
   - IDENTITY: 物理恒等式（如 eta0 = J*KT/(2*pi*KQ)）
   - SENSITIVITY: 输入变化 → 输出变化（检测 MOCK 的常数输出）
   - DETERMINISM: 同输入多次调用 → 同输出（检测随机 MOCK）
   - BOUNDARY: 边界值行为（检测 STUB 的空壳返回）
   - EXISTENCE: 端点可达性（检测 WITHHELD/NOT_SUPPORTED）
3. 判级规则：
   - REAL: ≥2 探针通过，且敏感性通过
   - MOCK: 敏感性失败（输出不随输入变化）或恒等式失败
   - STUB: 返回空壳/占位符
   - WITHHELD: 端点不存在或无法访问
   - NOT_SUPPORTED: 明确不支持
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Callable

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ProbeResult:
    """单个探针的执行结果。"""
    probe_id: str
    capability: str
    probe_type: str  # IDENTITY | SENSITIVITY | DETERMINISM | BOUNDARY | EXISTENCE
    passed: Optional[bool]  # None = inconclusive
    detail: str = ""
    metric: float = 0.0
    threshold: float = 0.0
    observed: Any = None
    expected: Any = None
    error: str = ""
    latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CapabilityVerdict:
    """单项能力的最终判级。"""
    capability: str
    verdict: str  # REAL | MOCK | STUB | WITHHELD | NOT_SUPPORTED
    confidence: float  # 0.0 - 1.0
    probes: List[ProbeResult] = field(default_factory=list)
    evidence_summary: str = ""
    manual_review_match: bool = False  # 与人工复核是否一致

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["probes"] = [p.to_dict() for p in self.probes]
        return d


# ---------------------------------------------------------------------------
# Probe helpers
# ---------------------------------------------------------------------------

def _rel(a: float, b: float) -> float:
    """相对误差，带分母保护。"""
    return abs(a - b) / max(abs(a), abs(b), 1e-12)


def _flatten(obj: Any, prefix: str = "") -> Dict[str, float]:
    """将 JSON 响应展平为 {path: float}。"""
    out: Dict[str, float] = {}
    if isinstance(obj, dict):
        for key, val in obj.items():
            path = f"{prefix}{key}"
            out.update(_flatten(val, path + "."))
    elif isinstance(obj, (list, tuple)):
        for idx, val in enumerate(obj):
            out.update(_flatten(val, f"{prefix.rstrip('.')}[{idx}]."))
    elif isinstance(obj, bool):
        out[prefix.rstrip(".")] = float(obj)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return out
        out[prefix.rstrip(".")] = float(obj)
    return out


# ---------------------------------------------------------------------------
# API client wrapper
# ---------------------------------------------------------------------------

class ShipAPIClient:
    """船舶平台 API 客户端（封装 call_ship_api 语义）。"""

    def __init__(self, call_fn: Callable):
        self._call = call_fn

    def post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """POST 请求，返回 data 字段。"""
        result = self._call(path=path, method="POST", body=body)
        if result.get("status") == 200:
            return result.get("data", {})
        raise APIError(result.get("status"), result.get("data", ""))

    def get(self, path: str) -> Dict[str, Any]:
        """GET 请求，返回 data 字段。"""
        result = self._call(path=path, method="GET")
        if result.get("status") == 200:
            return result.get("data", {})
        raise APIError(result.get("status"), result.get("data", ""))


class APIError(Exception):
    def __init__(self, status: int, data: Any):
        self.status = status
        self.data = data
        super().__init__(f"API {status}: {data}")


# ---------------------------------------------------------------------------
# Probe definitions for all 21 capabilities
# ---------------------------------------------------------------------------

def probe_resistance(client: ShipAPIClient) -> List[ProbeResult]:
    """calc.resistance — 阻力计算探针。

    不变量：
    1. SENSITIVITY: 改变速度 → Ct 变化
    2. IDENTITY: Ct = (Cf + Cr) 量级一致
    3. DETERMINISM: 同输入多次调用 → 同输出
    """
    results = []
    base_input = {"length": 100, "beam": 15, "draft": 5, "cb": 0.6,
                  "displacement": 4500, "speed": 12, "model": "holtrop"}

    # SENSITIVITY: speed change → Ct change
    try:
        t0 = time.time()
        r1 = client.post("calculations/resistance", {**base_input, "speed": 10})
        r2 = client.post("calculations/resistance", {**base_input, "speed": 14})
        latency = (time.time() - t0) * 1000
        ct1 = r1.get("ct", 0)
        ct2 = r2.get("ct", 0)
        rel_change = _rel(ct1, ct2)
        passed = rel_change > 0.01  # >1% change expected
        results.append(ProbeResult(
            probe_id="RES-SENS-01", capability="calc.resistance",
            probe_type="SENSITIVITY", passed=passed,
            detail=f"Ct at 10kn={ct1:.6g}, at 14kn={ct2:.6g}, rel_change={rel_change:.3%}",
            metric=rel_change, threshold=0.01,
            observed=ct2, expected=ct1, latency_ms=latency
        ))
    except APIError as e:
        results.append(ProbeResult(
            probe_id="RES-SENS-01", capability="calc.resistance",
            probe_type="SENSITIVITY", passed=None,
            detail=f"API error: {e.status}", error=str(e)
        ))

    # IDENTITY: Ct ≈ Cf + Cr (within 20% since Ct = Cf_total + Cr)
    try:
        t0 = time.time()
        r = client.post("calculations/resistance", base_input)
        latency = (time.time() - t0) * 1000
        ct = r.get("ct", 0)
        cf = r.get("cf_total", r.get("cf", 0))
        cr = r.get("cr", 0)
        sum_fc = cf + cr
        rel_err = _rel(ct, sum_fc) if sum_fc > 0 else 0
        passed = rel_err < 0.20  # within 20%
        results.append(ProbeResult(
            probe_id="RES-ID-01", capability="calc.resistance",
            probe_type="IDENTITY", passed=passed,
            detail=f"Ct={ct:.6g}, Cf+Cr={sum_fc:.6g}, rel_err={rel_err:.3%}",
            metric=rel_err, threshold=0.20,
            observed=ct, expected=sum_fc, latency_ms=latency
        ))
    except APIError as e:
        results.append(ProbeResult(
            probe_id="RES-ID-01", capability="calc.resistance",
            probe_type="IDENTITY", passed=None,
            detail=f"API error: {e.status}", error=str(e)
        ))

    # DETERMINISM
    try:
        t0 = time.time()
        r1 = client.post("calculations/resistance", base_input)
        r2 = client.post("calculations/resistance", base_input)
        latency = (time.time() - t0) * 1000
        ct1 = r1.get("ct", 0)
        ct2 = r2.get("ct", 0)
        rel_err = _rel(ct1, ct2)
        passed = rel_err < 1e-9
        results.append(ProbeResult(
            probe_id="RES-DET-01", capability="calc.resistance",
            probe_type="DETERMINISM", passed=passed,
            detail=f"Ct run1={ct1:.10g}, run2={ct2:.10g}, rel_err={rel_err:.3e}",
            metric=rel_err, threshold=1e-9,
            observed=ct2, expected=ct1, latency_ms=latency
        ))
    except APIError as e:
        results.append(ProbeResult(
            probe_id="RES-DET-01", capability="calc.resistance",
            probe_type="DETERMINISM", passed=None,
            detail=f"API error: {e.status}", error=str(e)
        ))

    return results


def probe_stability(client: ShipAPIClient) -> List[ProbeResult]:
    """calc.stability — 稳性计算探针。

    不变量：
    1. SENSITIVITY: 改变 KG → GM 变化
    2. IDENTITY: KM = KB + BM
    3. DETERMINISM
    """
    results = []
    base_input = {"length": 100, "beam": 15, "draft": 5, "cb": 0.6,
                  "displacement": 4500, "kg": 8}

    # SENSITIVITY
    try:
        t0 = time.time()
        r1 = client.post("calculations/stability", {**base_input, "kg": 7})
        r2 = client.post("calculations/stability", {**base_input, "kg": 9})
        latency = (time.time() - t0) * 1000
        gm1 = r1.get("gm", 0)
        gm2 = r2.get("gm", 0)
        rel_change = _rel(gm1, gm2)
        passed = rel_change > 0.01
        results.append(ProbeResult(
            probe_id="STAB-SENS-01", capability="calc.stability",
            probe_type="SENSITIVITY", passed=passed,
            detail=f"GM at KG=7: {gm1:.4f}, at KG=9: {gm2:.4f}, rel_change={rel_change:.3%}",
            metric=rel_change, threshold=0.01,
            observed=gm2, expected=gm1, latency_ms=latency
        ))
    except APIError as e:
        results.append(ProbeResult(
            probe_id="STAB-SENS-01", capability="calc.stability",
            probe_type="SENSITIVITY", passed=None,
            detail=f"API error: {e.status}", error=str(e)
        ))

    # IDENTITY: KM = KB + BM
    try:
        t0 = time.time()
        r = client.post("calculations/stability", base_input)
        latency = (time.time() - t0) * 1000
        kb = r.get("kb", 0)
        bm = r.get("bm", 0)
        km = r.get("km", 0)
        rel_err = _rel(km, kb + bm) if (kb + bm) > 0 else 0
        passed = rel_err < 1e-6
        results.append(ProbeResult(
            probe_id="STAB-ID-01", capability="calc.stability",
            probe_type="IDENTITY", passed=passed,
            detail=f"KM={km:.4f}, KB+BM={kb+bm:.4f}, rel_err={rel_err:.3e}",
            metric=rel_err, threshold=1e-6,
            observed=km, expected=kb + bm, latency_ms=latency
        ))
    except APIError as e:
        results.append(ProbeResult(
            probe_id="STAB-ID-01", capability="calc.stability",
            probe_type="IDENTITY", passed=None,
            detail=f"API error: {e.status}", error=str(e)
        ))

    # DETERMINISM
    try:
        t0 = time.time()
        r1 = client.post("calculations/stability", base_input)
        r2 = client.post("calculations/stability", base_input)
        latency = (time.time() - t0) * 1000
        gm1 = r1.get("gm", 0)
        gm2 = r2.get("gm", 0)
        rel_err = _rel(gm1, gm2)
        passed = rel_err < 1e-9
        results.append(ProbeResult(
            probe_id="STAB-DET-01", capability="calc.stability",
            probe_type="DETERMINISM", passed=passed,
            detail=f"GM run1={gm1:.6g}, run2={gm2:.6g}, rel_err={rel_err:.3e}",
            metric=rel_err, threshold=1e-9,
            observed=gm2, expected=gm1, latency_ms=latency
        ))
    except APIError as e:
        results.append(ProbeResult(
            probe_id="STAB-DET-01", capability="calc.stability",
            probe_type="DETERMINISM", passed=None,
            detail=f"API error: {e.status}", error=str(e)
        ))

    return results


def probe_propeller(client: ShipAPIClient) -> List[ProbeResult]:
    """propeller.open_water — 螺旋桨敞水性能探针。

    不变量：
    1. IDENTITY: eta0 = J*KT/(2*pi*KQ)
    2. SENSITIVITY: 改变 J → KT 变化
    3. DETERMINISM
    """
    results = []
    base_input = {"j": 0.5, "p_d": 1.0, "z": 4, "ae_ao": 0.7}

    # Try the propeller endpoint
    try:
        t0 = time.time()
        r = client.post("propeller/open_water", base_input)
        latency = (time.time() - t0) * 1000
        kt = r.get("KT", 0)
        kq = r.get("KQ", 0)
        eta = r.get("eta0", 0)
        eta_calc = (base_input["j"] * kt) / (2 * math.pi * kq) if kq > 0 else 0
        rel_err = _rel(eta, eta_calc) if eta_calc > 0 else 0
        passed = rel_err < 0.01
        results.append(ProbeResult(
            probe_id="PROP-ID-01", capability="propeller.open_water",
            probe_type="IDENTITY", passed=passed,
            detail=f"eta0={eta:.6g}, calc={eta_calc:.6g}, rel_err={rel_err:.3%}",
            metric=rel_err, threshold=0.01,
            observed=eta, expected=eta_calc, latency_ms=latency
        ))
    except APIError as e:
        results.append(ProbeResult(
            probe_id="PROP-ID-01", capability="propeller.open_water",
            probe_type="IDENTITY", passed=None,
            detail=f"API error: {e.status}", error=str(e)
        ))

    # SENSITIVITY
    try:
        t0 = time.time()
        r1 = client.post("propeller/open_water", {**base_input, "j": 0.3})
        r2 = client.post("propeller/open_water", {**base_input, "j": 0.7})
        latency = (time.time() - t0) * 1000
        kt1 = r1.get("KT", 0)
        kt2 = r2.get("KT", 0)
        rel_change = _rel(kt1, kt2)
        passed = rel_change > 0.01
        results.append(ProbeResult(
            probe_id="PROP-SENS-01", capability="propeller.open_water",
            probe_type="SENSITIVITY", passed=passed,
            detail=f"KT at J=0.3: {kt1:.6g}, at J=0.7: {kt2:.6g}, rel_change={rel_change:.3%}",
            metric=rel_change, threshold=0.01,
            observed=kt2, expected=kt1, latency_ms=latency
        ))
    except APIError as e:
        results.append(ProbeResult(
            probe_id="PROP-SENS-01", capability="propeller.open_water",
            probe_type="SENSITIVITY", passed=None,
            detail=f"API error: {e.status}", error=str(e)
        ))

    # DETERMINISM
    try:
        t0 = time.time()
        r1 = client.post("propeller/open_water", base_input)
        r2 = client.post("propeller/open_water", base_input)
        latency = (time.time() - t0) * 1000
        kt1 = r1.get("KT", 0)
        kt2 = r2.get("KT", 0)
        rel_err = _rel(kt1, kt2)
        passed = rel_err < 1e-9
        results.append(ProbeResult(
            probe_id="PROP-DET-01", capability="propeller.open_water",
            probe_type="DETERMINISM", passed=passed,
            detail=f"KT run1={kt1:.10g}, run2={kt2:.10g}, rel_err={rel_err:.3e}",
            metric=rel_err, threshold=1e-9,
            observed=kt2, expected=kt1, latency_ms=latency
        ))
    except APIError as e:
        results.append(ProbeResult(
            probe_id="PROP-DET-01", capability="propeller.open_water",
            probe_type="DETERMINISM", passed=None,
            detail=f"API error: {e.status}", error=str(e)
        ))

    return results


def probe_hydro_curves(client: ShipAPIClient) -> List[ProbeResult]:
    """geometry.hydro_curves — 静水力曲线探针（已知 REAL）。

    不变量：
    1. SENSITIVITY: 改变 draft → 排水量变化
    2. IDENTITY: 排水量 = rho * volume
    3. DETERMINISM
    """
    results = []
    base_input = {"hull": "box", "length": 100, "beam": 15, "depth": 8,
                  "draft_min": 3, "draft_max": 7, "draft_step": 1, "rho": 1025}

    # SENSITIVITY
    try:
        t0 = time.time()
        r = client.post("calculations/hydro_curves", base_input)
        latency = (time.time() - t0) * 1000
        curves = r.get("curves", [])
        if len(curves) >= 2:
            disp1 = curves[0].get("displacement_t", 0)
            disp2 = curves[-1].get("displacement_t", 0)
            rel_change = _rel(disp1, disp2)
            passed = rel_change > 0.1  # >10% change expected over draft range
            results.append(ProbeResult(
                probe_id="HYDRO-SENS-01", capability="geometry.hydro_curves",
                probe_type="SENSITIVITY", passed=passed,
                detail=f"Disp at draft_min={disp1:.1f}, at draft_max={disp2:.1f}, rel_change={rel_change:.3%}",
                metric=rel_change, threshold=0.1,
                observed=disp2, expected=disp1, latency_ms=latency
            ))
        else:
            results.append(ProbeResult(
                probe_id="HYDRO-SENS-01", capability="geometry.hydro_curves",
                probe_type="SENSITIVITY", passed=None,
                detail=f"Insufficient curve points: {len(curves)}"
            ))
    except APIError as e:
        results.append(ProbeResult(
            probe_id="HYDRO-SENS-01", capability="geometry.hydro_curves",
            probe_type="SENSITIVITY", passed=None,
            detail=f"API error: {e.status}", error=str(e)
        ))

    # IDENTITY: displacement = rho * volume
    try:
        t0 = time.time()
        r = client.post("calculations/hydro_curves", base_input)
        latency = (time.time() - t0) * 1000
        curves = r.get("curves", [])
        if curves:
            pt = curves[0]
            vol = pt.get("volume_m3", 0)
            disp = pt.get("displacement_t", 0)
            rho = base_input["rho"]
            expected_disp = rho * vol / 1000  # tonnes
            rel_err = _rel(disp, expected_disp) if expected_disp > 0 else 0
            passed = rel_err < 0.01
            results.append(ProbeResult(
                probe_id="HYDRO-ID-01", capability="geometry.hydro_curves",
                probe_type="IDENTITY", passed=passed,
                detail=f"Disp={disp:.2f}, rho*V/1000={expected_disp:.2f}, rel_err={rel_err:.3%}",
                metric=rel_err, threshold=0.01,
                observed=disp, expected=expected_disp, latency_ms=latency
            ))
    except APIError as e:
        results.append(ProbeResult(
            probe_id="HYDRO-ID-01", capability="geometry.hydro_curves",
            probe_type="IDENTITY", passed=None,
            detail=f"API error: {e.status}", error=str(e)
        ))

    return results


def probe_gov_stats(client: ShipAPIClient) -> List[ProbeResult]:
    """gov.stats — 治理统计探针（已知 MOCK）。

    不变量：
    1. SENSITIVITY: 改变时间范围 → 统计值变化
    2. DETERMINISM
    """
    results = []
    # Try to find a gov/stats endpoint
    for path in ["governance/stats", "gov/stats", "governance/statistics"]:
        try:
            t0 = time.time()
            r1 = client.post(path, {"period": "7d"})
            r2 = client.post(path, {"period": "30d"})
            latency = (time.time() - t0) * 1000
            # Check if outputs differ
            flat1 = _flatten(r1)
            flat2 = _flatten(r2)
            common = set(flat1) & set(flat2)
            if common:
                max_diff = max(_rel(flat1[k], flat2[k]) for k in common)
                passed = max_diff > 0.01
                results.append(ProbeResult(
                    probe_id="GOV-SENS-01", capability="gov.stats",
                    probe_type="SENSITIVITY", passed=passed,
                    detail=f"Max rel diff between 7d/30d: {max_diff:.3%}",
                    metric=max_diff, threshold=0.01, latency_ms=latency
                ))
            break
        except APIError:
            continue

    if not results:
        results.append(ProbeResult(
            probe_id="GOV-SENS-01", capability="gov.stats",
            probe_type="SENSITIVITY", passed=None,
            detail="No accessible gov.stats endpoint found"
        ))

    return results


def probe_knowledge_docs(client: ShipAPIClient) -> List[ProbeResult]:
    """knowledge.docs — 知识文档探针（已知 MOCK）。

    不变量：
    1. EXISTENCE: 端点可达
    2. BOUNDARY: 查询不同关键词 → 不同结果
    """
    results = []
    for path in ["knowledge/docs", "knowledge/documents", "docs"]:
        try:
            t0 = time.time()
            r1 = client.post(path, {"query": "resistance"})
            r2 = client.post(path, {"query": "stability"})
            latency = (time.time() - t0) * 1000
            # Check if results differ
            s1 = json.dumps(r1, sort_keys=True)
            s2 = json.dumps(r2, sort_keys=True)
            passed = s1 != s2
            results.append(ProbeResult(
                probe_id="KNOW-BOUND-01", capability="knowledge.docs",
                probe_type="BOUNDARY", passed=passed,
                detail=f"Results differ for different queries: {passed}",
                metric=float(passed), threshold=1.0, latency_ms=latency
            ))
            break
        except APIError:
            continue

    if not results:
        results.append(ProbeResult(
            probe_id="KNOW-BOUND-01", capability="knowledge.docs",
            probe_type="BOUNDARY", passed=None,
            detail="No accessible knowledge.docs endpoint found"
        ))

    return results


def probe_misc_stubs(client: ShipAPIClient) -> List[ProbeResult]:
    """misc.stubs — 占位符探针（已知 STUB）。

    不变量：
    1. EXISTENCE: 端点可达但返回占位符
    2. BOUNDARY: 检查返回是否为空壳
    """
    results = []
    for path in ["misc/stubs", "misc/example", "example"]:
        try:
            t0 = time.time()
            r = client.post(path, {})
            latency = (time.time() - t0) * 1000
            # Check if response is a stub (minimal content)
            is_stub = len(json.dumps(r)) < 200 or "stub" in json.dumps(r).lower() or "example" in json.dumps(r).lower()
            results.append(ProbeResult(
                probe_id="STUB-BOUND-01", capability="misc.stubs",
                probe_type="BOUNDARY", passed=is_stub,
                detail=f"Response is stub-like: {is_stub}, len={len(json.dumps(r))}",
                metric=float(is_stub), threshold=1.0, latency_ms=latency
            ))
            break
        except APIError:
            continue

    if not results:
        results.append(ProbeResult(
            probe_id="STUB-BOUND-01", capability="misc.stubs",
            probe_type="BOUNDARY", passed=None,
            detail="No accessible misc.stubs endpoint found"
        ))

    return results


def probe_ai_predict(client: ShipAPIClient) -> List[ProbeResult]:
    """ai.predict — AI 预测探针（已知 NOT_SUPPORTED）。

    不变量：
    1. EXISTENCE: 端点不存在
    """
    results = []
    for path in ["ai/predict", "ai/prediction", "predict"]:
        try:
            t0 = time.time()
            r = client.post(path, {"input": [1, 2, 3]})
            latency = (time.time() - t0) * 1000
            # If we get here, it exists (unexpected)
            results.append(ProbeResult(
                probe_id="AI-EXIST-01", capability="ai.predict",
                probe_type="EXISTENCE", passed=False,
                detail=f"Endpoint unexpectedly exists, status=200",
                latency_ms=latency
            ))
            break
        except APIError as e:
            if e.status in (404, 501, 405):
                results.append(ProbeResult(
                    probe_id="AI-EXIST-01", capability="ai.predict",
                    probe_type="EXISTENCE", passed=True,
                    detail=f"Endpoint correctly returns {e.status} (not supported)",
                    metric=float(e.status), threshold=404.0,
                ))
                break
            # Other errors might mean it exists but has issues
            results.append(ProbeResult(
                probe_id="AI-EXIST-01", capability="ai.predict",
                probe_type="EXISTENCE", passed=None,
                detail=f"API error: {e.status}", error=str(e)
            ))
            break

    if not results:
        results.append(ProbeResult(
            probe_id="AI-EXIST-01", capability="ai.predict",
            probe_type="EXISTENCE", passed=True,
            detail="No endpoint found (NOT_SUPPORTED confirmed)"
        ))

    return results


def probe_dev_endpoints(client: ShipAPIClient, cap_id: str, paths: List[str]) -> List[ProbeResult]:
    """通用 dev.* 能力探针。

    不变量：
    1. EXISTENCE: 端点可达性
    2. SENSITIVITY: 不同参数 → 不同响应
    """
    results = []
    for path in paths:
        try:
            t0 = time.time()
            r = client.get(path)
            latency = (time.time() - t0) * 1000
            results.append(ProbeResult(
                probe_id=f"{cap_id.upper().replace('.', '-')}-EXIST-01",
                capability=cap_id,
                probe_type="EXISTENCE", passed=True,
                detail=f"Endpoint {path} accessible, response keys: {list(r.keys())[:5] if isinstance(r, dict) else 'N/A'}",
                latency_ms=latency
            ))
            break
        except APIError as e:
            if e.status == 404:
                continue
            results.append(ProbeResult(
                probe_id=f"{cap_id.upper().replace('.', '-')}-EXIST-01",
                capability=cap_id,
                probe_type="EXISTENCE", passed=None,
                detail=f"API error on {path}: {e.status}", error=str(e)
            ))
            break

    if not results:
        results.append(ProbeResult(
            probe_id=f"{cap_id.upper().replace('.', '-')}-EXIST-01",
            capability=cap_id,
            probe_type="EXISTENCE", passed=False,
            detail=f"No accessible endpoint found among {paths}"
        ))

    return results


def probe_geometry_generate(client: ShipAPIClient) -> List[ProbeResult]:
    """geometry.generate — 几何生成探针（WITHHELD）。"""
    return probe_dev_endpoints(client, "geometry.generate",
                               ["geometry/generate", "hull/generate", "geometry/create"])


def probe_geometry_list_models(client: ShipAPIClient) -> List[ProbeResult]:
    """geometry.list_models — 模型列表探针（WITHHELD）。"""
    return probe_dev_endpoints(client, "geometry.list_models",
                               ["geometry/models", "stdmodels/list", "models"])


def probe_geometry_verify_official(client: ShipAPIClient) -> List[ProbeResult]:
    """geometry.verify_official — 几何验证探针（WITHHELD）。"""
    results = []
    for path in ["geometry/verify", "geometry/verify_official"]:
        try:
            t0 = time.time()
            r = client.post(path, {"length": 100, "beam": 15, "draft": 5, "cb": 0.6, "displacement": 4500})
            latency = (time.time() - t0) * 1000
            # Check if Cb is verified
            cb_verified = "cb" in json.dumps(r).lower() or "verified" in json.dumps(r).lower()
            results.append(ProbeResult(
                probe_id="GEO-VERIFY-EXIST-01", capability="geometry.verify_official",
                probe_type="EXISTENCE", passed=True,
                detail=f"Endpoint accessible, Cb verification: {cb_verified}",
                latency_ms=latency
            ))
            break
        except APIError:
            continue

    if not results:
        results.append(ProbeResult(
            probe_id="GEO-VERIFY-EXIST-01", capability="geometry.verify_official",
            probe_type="EXISTENCE", passed=False,
            detail="No accessible endpoint found"
        ))

    return results


def probe_shiphull_generate(client: ShipAPIClient) -> List[ProbeResult]:
    """shiphull.generate — 船体生成探针（WITHHELD）。"""
    return probe_dev_endpoints(client, "shiphull.generate",
                               ["shiphull/generate", "hull/generate", "shiphull/create"])


def probe_stdmodels_lookup(client: ShipAPIClient) -> List[ProbeResult]:
    """stdmodels.lookup — 标准模型查询探针（WITHHELD）。"""
    results = []
    for path in ["stdmodels/lookup", "stdmodels/KVLCC2", "models/KVLCC2"]:
        try:
            t0 = time.time()
            r = client.get(path)
            latency = (time.time() - t0) * 1000
            results.append(ProbeResult(
                probe_id="STD-EXIST-01", capability="stdmodels.lookup",
                probe_type="EXISTENCE", passed=True,
                detail=f"Endpoint accessible",
                latency_ms=latency
            ))
            break
        except APIError:
            continue

    if not results:
        results.append(ProbeResult(
            probe_id="STD-EXIST-01", capability="stdmodels.lookup",
            probe_type="EXISTENCE", passed=False,
            detail="No accessible endpoint found"
        ))

    return results


def probe_methods_computation(client: ShipAPIClient) -> List[ProbeResult]:
    """methods.computation — 计算方法探针（WITHHELD）。"""
    return probe_dev_endpoints(client, "methods.computation",
                               ["methods/computation", "methods/solvers", "computation/methods"])


def probe_dev_gate_5415(client: ShipAPIClient) -> List[ProbeResult]:
    """dev.gate_5415 — DTMB 5415 门禁探针（WITHHELD）。"""
    return probe_dev_endpoints(client, "dev.gate_5415",
                               ["dev/gate_5415", "gate/5415", "dtmb/5415"])


def probe_dev_git(client: ShipAPIClient) -> List[ProbeResult]:
    """dev.git — Git 状态探针（WITHHELD）。"""
    return probe_dev_endpoints(client, "dev.git",
                               ["dev/git", "git/status", "git/log"])


def probe_dev_logs(client: ShipAPIClient) -> List[ProbeResult]:
    """dev.logs — 日志探针（WITHHELD）。"""
    return probe_dev_endpoints(client, "dev.logs",
                               ["dev/logs", "logs/tail", "logs"])


def probe_dev_nginx_audit(client: ShipAPIClient) -> List[ProbeResult]:
    """dev.nginx_audit — Nginx 审计探针（WITHHELD）。"""
    return probe_dev_endpoints(client, "dev.nginx_audit",
                               ["dev/nginx_audit", "nginx/audit", "nginx/status"])


def probe_dev_probe(client: ShipAPIClient) -> List[ProbeResult]:
    """dev.probe — 健康探针（WITHHELD）。"""
    return probe_dev_endpoints(client, "dev.probe",
                               ["dev/probe", "health", "healthcheck"])


def probe_dev_restart(client: ShipAPIClient) -> List[ProbeResult]:
    """dev.restart — 重启探针（WITHHELD）。"""
    return probe_dev_endpoints(client, "dev.restart",
                               ["dev/restart", "restart", "system/restart"])


def probe_dev_services(client: ShipAPIClient) -> List[ProbeResult]:
    """dev.services — 服务状态探针（WITHHELD）。"""
    return probe_dev_endpoints(client, "dev.services",
                               ["dev/services", "services/status", "services"])


# ---------------------------------------------------------------------------
# Probe registry
# ---------------------------------------------------------------------------

PROBE_REGISTRY: Dict[str, Callable[[ShipAPIClient], List[ProbeResult]]] = {
    "calc.resistance": probe_resistance,
    "calc.stability": probe_stability,
    "propeller.open_water": probe_propeller,
    "geometry.hydro_curves": probe_hydro_curves,
    "gov.stats": probe_gov_stats,
    "knowledge.docs": probe_knowledge_docs,
    "misc.stubs": probe_misc_stubs,
    "ai.predict": probe_ai_predict,
    "geometry.generate": probe_geometry_generate,
    "geometry.list_models": probe_geometry_list_models,
    "geometry.verify_official": probe_geometry_verify_official,
    "shiphull.generate": probe_shiphull_generate,
    "stdmodels.lookup": probe_stdmodels_lookup,
    "methods.computation": probe_methods_computation,
    "dev.gate_5415": probe_dev_gate_5415,
    "dev.git": probe_dev_git,
    "dev.logs": probe_dev_logs,
    "dev.nginx_audit": probe_dev_nginx_audit,
    "dev.probe": probe_dev_probe,
    "dev.restart": probe_dev_restart,
    "dev.services": probe_dev_services,
}


# ---------------------------------------------------------------------------
# Verdict logic
# ---------------------------------------------------------------------------

def classify_verdict(probes: List[ProbeResult], manual_grade: str) -> CapabilityVerdict:
    """根据探针结果判级。

    判级规则：
    - NOT_SUPPORTED: 所有 EXISTENCE 探针通过（端点不存在）
    - STUB: BOUNDARY 探针通过（返回占位符）
    - MOCK: SENSITIVITY 失败或 IDENTITY 失败
    - REAL: ≥2 探针通过，且 SENSITIVITY 通过
    - WITHHELD: 无法确定（探针 inconclusive）
    """
    passed = [p for p in probes if p.passed is True]
    failed = [p for p in probes if p.passed is False]
    inconclusive = [p for p in probes if p.passed is None]

    sensitivity_passed = any(p.probe_type == "SENSITIVITY" and p.passed for p in probes)
    sensitivity_failed = any(p.probe_type == "SENSITIVITY" and p.passed is False for p in probes)
    identity_failed = any(p.probe_type == "IDENTITY" and p.passed is False for p in probes)
    existence_passed = any(p.probe_type == "EXISTENCE" and p.passed for p in probes)
    existence_failed = any(p.probe_type == "EXISTENCE" and p.passed is False for p in probes)
    boundary_passed = any(p.probe_type == "BOUNDARY" and p.passed for p in probes)

    # Determine verdict
    if existence_failed and not existence_passed:
        verdict = "NOT_SUPPORTED"
        confidence = 0.95
    elif boundary_passed:
        verdict = "STUB"
        confidence = 0.90
    elif sensitivity_failed or identity_failed:
        verdict = "MOCK"
        confidence = 0.85
    elif len(passed) >= 2 and sensitivity_passed:
        verdict = "REAL"
        confidence = 0.90
    elif len(inconclusive) == len(probes):
        verdict = "WITHHELD"
        confidence = 0.70
    else:
        # Mixed results
        if len(passed) > len(failed):
            verdict = "REAL"
            confidence = 0.60
        else:
            verdict = "MOCK"
            confidence = 0.60

    # Check match with manual review
    manual_review_match = (verdict == manual_grade)

    evidence_parts = []
    if passed:
        evidence_parts.append(f"{len(passed)} probe(s) passed")
    if failed:
        evidence_parts.append(f"{len(failed)} probe(s) failed")
    if inconclusive:
        evidence_parts.append(f"{len(inconclusive)} probe(s) inconclusive")

    return CapabilityVerdict(
        capability=probes[0].capability if probes else "unknown",
        verdict=verdict,
        confidence=confidence,
        probes=probes,
        evidence_summary="; ".join(evidence_parts),
        manual_review_match=manual_review_match,
    )


# ---------------------------------------------------------------------------
# Main audit function
# ---------------------------------------------------------------------------

def run_full_audit(call_fn: Callable, manual_grades: Dict[str, str] = None) -> List[CapabilityVerdict]:
    """执行全量 21 项能力审计。

    Args:
        call_fn: API 调用函数，签名 (path, method, body) -> {status, data}
        manual_grades: 人工复核判级（用于一致性检查）

    Returns:
        21 项能力的判级结果列表
    """
    if manual_grades is None:
        manual_grades = {}

    client = ShipAPIClient(call_fn)
    verdicts = []

    for cap_id, probe_fn in PROBE_REGISTRY.items():
        probes = probe_fn(client)
        manual = manual_grades.get(cap_id, "WITHHELD")
        verdict = classify_verdict(probes, manual)
        verdicts.append(verdict)

    return verdicts
