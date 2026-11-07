"""变异体自检：证明三把刀**有检出能力**，且对真实现不误报。

每个变异体是一个进程内合成能力，其真值（应当被判成什么）**先于**判决写死：
  - mock_constant / mock_speed_blind   → 期望 MOCK（刀 A 抓）
  - analytic_violator                  → 期望 MOCK（刀 B 抓）
  - non_monotone                       → 期望 MOCK（刀 C 抓）
  - stub_zeros                         → 期望 STUB
  - correct_physics / rounded_physics  → 期望 REAL（阴性对照，检验误报率）

判据：detection_rate = 检出数/应检出数；false_alarm_rate = 对照被误杀数/对照数。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

from . import grade, knives, runner, spec

G = 9.80665


# ---------------------------------------------------------------- 合成物理
def _numbers(body: Dict[str, Any]) -> Dict[str, float]:
    L = float(body.get("length", 100.0))
    B = float(body.get("beam", 20.0))
    T = float(body.get("draft", 8.0))
    cb = float(body.get("cb", 0.6))
    speed = float(body.get("speed", 14.0))
    unit = (body.get("speed_unit") or "kn").lower()
    v = speed * knives.KNOT_TO_MS if unit in ("kn", "knot", "knots") else speed
    return {"L": L, "B": B, "T": T, "cb": cb, "v": v, "speed": speed}


def _phys_fields(body: Dict[str, Any]) -> Dict[str, float]:
    """审计侧合成「真实现」：ITTC-57 摩擦 + 平滑兴波项（对 speed/beam/cb 均响应）。"""
    p = _numbers(body)
    L, B, T, cb, v = p["L"], p["B"], p["T"], p["cb"], p["v"]
    rho = float(body.get("water_density", knives.RHO_DEFAULT))
    Re = knives.reynolds(v, L, float(body.get("water_viscosity") or knives.NU_DEFAULT))
    cf = knives.ittc57_cf(Re)
    S = L * (1.7 * T + B)
    Fr = v / math.sqrt(G * L)
    cr = 0.5 * Fr ** 3 + 0.02 * cb
    rf = 0.5 * rho * v * v * S * cf
    rr = 0.5 * rho * v * v * S * cr
    # 单位与平台一致：阻力 kN、系数无量纲、湿面积 m²
    return {"rt": (rf + rr) / 1000.0, "rf": rf / 1000.0, "cf": cf, "cr": cr,
            "wetted_surface": S, "froude_number": Fr}


def _round(v: float, nd: int) -> float:
    return round(v, nd)


# ---------------------------------------------------------------- 路由
def route_mock_constant(body):
    return {"rt": 500.0, "rf": 120.0, "cf": 0.0018, "cr": 0.0042,
            "wetted_surface": 2700.0, "froude_number": 0.23}


def route_mock_speed_blind(body):
    frozen = dict(body)
    frozen["speed"] = 14.0
    return _phys_fields(frozen)


def route_analytic_violator(body):
    f = _phys_fields(body)
    f["cf"] = 0.0045          # 违反 ITTC-57（真值 0.0016 量级）
    q = 0.5 * knives.RHO_DEFAULT * _numbers(body)["v"] ** 2 * f["wetted_surface"] / 1000.0
    f["rf"] = q * f["cf"]
    f["rt"] = f["rf"] + q * f["cr"]
    return f


def route_non_monotone(body):
    f = _phys_fields(body)
    s = _numbers(body)["speed"]
    f["rt"] *= 1.0 - 0.70 * math.exp(-((s - 14.0) / 2.0) ** 2)   # 14 kn 附近挖坑
    return f


def route_nondeterministic(body):
    """真实现 + 每次调用独立乘性抖动 ±2%：模拟「随机数冒充物理/模型未固化」。"""
    import random
    f = _phys_fields(body)
    jitter = random.uniform(0.98, 1.02)
    for k in ("rt", "rf"):
        f[k] *= jitter
    return f


def route_stub_zeros(body):
    return {"rt": 0.0, "rf": 0.0, "cf": 0.0, "cr": 0.0,
            "wetted_surface": 0.0, "froude_number": 0.0}


def route_correct(body):
    return _phys_fields(body)


def route_rounded(body):
    return {k: _round(v, 6) for k, v in _phys_fields(body).items()}


MUTANTS: List[Dict[str, Any]] = [
    {"id": "mock_constant", "route": route_mock_constant, "expect": "MOCK",
     "why": "输出与一切输入无关（硬编码 500 kN）"},
    {"id": "mock_speed_blind", "route": route_mock_speed_blind, "expect": "MOCK",
     "why": "对航速零敏感（速度被钉死在 14 kn）"},
    {"id": "analytic_violator", "route": route_analytic_violator, "expect": "MOCK",
     "why": "Cf 硬编码 0.0045，偏离 ITTC-57 >15%"},
    {"id": "non_monotone", "route": route_non_monotone, "expect": "MOCK",
     "why": "R_t 在 14 kn 附近非单调（物理上必须单调增）"},
    {"id": "nondeterministic", "route": route_nondeterministic, "expect": "MOCK",
     "why": "同一请求重复调用输出漂移（随机数冒充物理）"},
    {"id": "stub_zeros", "route": route_stub_zeros, "expect": "STUB",
     "why": "全字段恒为 0"},
    {"id": "correct_physics", "route": route_correct, "expect": "REAL",
     "why": "阴性对照：真实现，三刀不应击杀"},
    {"id": "rounded_physics", "route": route_rounded, "expect": "REAL",
     "why": "阴性对照：真实现 + 响应 6 位小数舍入（检验舍入假阳性）"},
]


def _mutant_ledger() -> Dict[str, Any]:
    caps = []
    for m in MUTANTS:
        base = spec._spec_resistance()
        base = {**base, "path": f"/mutant/{m['id']}",
                "analytic_checks": [c for c in base["analytic_checks"]
                                    if c.get("ref_mode") != "cross_endpoint"]}
        caps.append({"id": f"mutant.{m['id']}", "name": m["id"], "domain": "selftest",
                     "entrypoint": {"kind": "http", "method": "POST",
                                    "path": f"/mutant/{m['id']}"},
                     "evidence_grade": "REAL",
                     "evidence_ref": {"claimed_level": "REAL"},
                     "callable_spec": base})
    return {"n_capabilities": len(caps), "capabilities": caps}


def run_mutants() -> Dict[str, Any]:
    routes = {f"/mutant/{m['id']}": m["route"] for m in MUTANTS}
    transport = runner.FunctionTransport(routes)
    ledger = _mutant_ledger()
    probe = runner.probe_ledger(ledger, transport)
    by_id = {m["id"]: m for m in MUTANTS}
    rows, flagged_expected, flagged_hit, control_hit, control_false = [], 0, 0, 0, 0
    for tr in probe["capabilities"]:
        mid = tr["capability"].split(".", 1)[1]
        m = by_id[mid]
        g = grade.falsified_grade(tr)
        got = g["grade"]
        exp = m["expect"]
        killed_by = (tr.get("aggregate") or {}).get("kills") or []
        rows.append({
            "mutant": mid, "expected": exp, "got": got,
            "detected": (got == "MOCK" and exp == "MOCK") or (got == "STUB" and exp == "STUB"),
            "killed_by": killed_by, "reason": g["reason"], "why": m["why"],
            "knife_verdicts": {kr.get("driver") or kr.get("series"): kr["verdict"]
                               for kr in tr.get("knife_results") or []},
        })
        if exp in ("MOCK", "STUB"):
            flagged_expected += 1
            flagged_hit += 1 if ((got == "MOCK") == (exp == "MOCK") and
                                 (got == "STUB") == (exp == "STUB")) else 0
        else:
            control_hit += 1
            if got != "REAL":
                control_false += 1
    return {
        "n_mutants": len(rows),
        "n_expected_flagged": flagged_expected,
        "n_detected": flagged_hit,
        "detection_rate": (flagged_hit / flagged_expected) if flagged_expected else None,
        "n_controls": control_hit,
        "n_control_false_alarm": control_false,
        "false_alarm_rate": (control_false / control_hit) if control_hit else None,
        "rows": rows,
        "transport": "function",
        "n_calls": transport.n_http_calls,
    }