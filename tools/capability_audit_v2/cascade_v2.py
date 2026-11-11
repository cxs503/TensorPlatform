"""v2 判定级联：静态结构信号 + 可执行证据 → 证据等级（确定性函数）。

规则集 ``cav2-rules-v1``（**先于观测声明**，改规则必须升版本并写进文档）：

=====================  ==========================================  ==================
序                    条件                                        等级 / 理由码
=====================  ==========================================  ==================
1                     静态：绑定目标缺失 / 实现缺失                NOT_SUPPORTED
2                     静态：空壳函数占比超阈                        STUB
3                     探针证伪数 ≥ probe_min_contradictions         MOCK / EXEC_INVARIANT_VIOLATED
4                     静态：≥2 个独立造假信号族                     MOCK
5                     基准（解析/恒等类）越界 → 已有静态规则        MOCK
6                     pytest 断言失败（非环境错误）                 WITHHELD / EXEC_TEST_FAILED
7                     pytest 环境错误/采集失败/超时                 WITHHELD / EXEC_ENV_ERROR
8                     静态：实验型基准越界 / 证据未绑定等           WITHHELD（沿用静态理由码）
9                     静态 REAL 且（执行证据未绑定 或 通过且数值断言达标） REAL
10                    其余                                          WITHHELD / INSUFFICIENT
=====================  ==========================================  ==================

为什么探针优先于一切静态信号：探针是**执行实现得到的数值反证**，比 AST 结构信号
更强；但它只做否定（证伪）不做肯定——探针全过仍不足以判 REAL（无对标基准）。

为什么环境错误不判 MOCK：测试在缺 provider / 缺依赖的宿主上失败，是**环境**问题，
不是能力造假。把它判成 MOCK 会把 CI 宿主差异洗成领域结论（诚实性要求）。
"""
from __future__ import annotations

RULESET_VERSION = "cav2-rules-v1"

REAL = "REAL"
MOCK = "MOCK"
STUB = "STUB"
WITHHELD = "WITHHELD"
NOT_SUPPORTED = "NOT_SUPPORTED"

#: 静态判定里**已经做过正面反证**的理由码（v2 不推翻它们）
_STATIC_TERMINAL = {
    "BINDING_TARGET_MISSING": NOT_SUPPORTED,
    "CODE_MISSING": NOT_SUPPORTED,
    "STUB_DOMINATED": STUB,
    "PHYSICS_INVARIANT_VIOLATED": MOCK,
}


def adjudicate(static: dict, exec_ev: dict, thresholds: dict) -> dict:
    """返回 ``{level, reason_code, rule, static_level, contradictions, warnings}``。"""
    cap_id = static["capability"]
    static_level = static["level"]
    static_reasons = list(static.get("reasons") or [])
    pytest_ev = exec_ev.get("pytest") or {"status": "NOT_BOUND", "bound": False}
    probe_ev = exec_ev.get("probe") or {"status": "NOT_BOUND", "bound": False}
    contradictions = list(probe_ev.get("contradictions") or [])
    warnings: list[str] = []

    def out(level: str, reason: str, rule: int, extra: dict | None = None) -> dict:
        rec = {
            "capability": cap_id,
            "level": level,
            "reason_code": reason,
            "rule": rule,
            "static_level": static_level,
            "static_reasons": static_reasons,
            "contradictions": contradictions,
            "warnings": warnings + list(static.get("warnings") or []),
            "exec": {"pytest_status": pytest_ev.get("status"),
                     "probe_status": probe_ev.get("status")},
            # 测试全绿但探针证伪 ⇒ 测试套件对该能力"不判别"（执行了，但没断言到点子上）
            "non_discriminating_tests": bool(
                pytest_ev.get("status") == "PASSED" and contradictions),
            "ruleset_version": RULESET_VERSION,
        }
        if extra:
            rec.update(extra)
        return rec

    # 1/2 静态终态（实现缺失 / 空壳）——可执行证据无法救回不存在的实现
    for reason in static_reasons:
        if reason in _STATIC_TERMINAL:
            return out(_STATIC_TERMINAL[reason], reason, 1 if _STATIC_TERMINAL[reason] == NOT_SUPPORTED else 2)

    # 3 可执行不变量被证伪（最强否定证据）
    if len(contradictions) >= int(thresholds.get("probe_min_contradictions", 1)):
        return out(MOCK, "EXEC_INVARIANT_VIOLATED", 3,
                   {"violated_checks": contradictions,
                    "probe_checks": probe_ev.get("checks") or []})
    if probe_ev.get("status") in ("ERROR", "ENV_ERROR"):
        warnings.append("PROBE_UNEXECUTED")
    if probe_ev.get("bound") and probe_ev.get("status") == "OK" and not contradictions:
        warnings.append("PROBE_ALL_PASS_NO_REFERENCE")

    # 4 静态造假信号（已由静态规则判 MOCK）
    if static_level == MOCK:
        return out(MOCK, (static_reasons or ["STATIC_MOCK"])[0], 4)

    # 6 执行测试的断言失败：证据被反证，但不下 MOCK 判决（可能是测试陈旧/范围错配）
    if pytest_ev.get("status") in ("TEST_FAILED", "TEST_ERROR"):
        return out(WITHHELD, "EXEC_TEST_FAILED", 6,
                   {"failing_tests": pytest_ev.get("failing_tests") or []})
    # 7 执行侧环境错误 / 采集失败 / 超时：不指控能力
    if pytest_ev.get("status") in ("ENV_ERROR", "COLLECTION_ERROR", "TIMEOUT", "NO_TESTS"):
        return out(WITHHELD, "EXEC_ENV_ERROR", 7,
                   {"exec_status": pytest_ev.get("status")})
    if pytest_ev.get("status") == "SKIPPED":
        warnings.append("EXEC_TESTS_SKIPPED")

    # 8 静态 WITHHELD（基准越界 / 未绑定 / 无数值断言……）——沿用静态理由
    if static_level == WITHHELD:
        return out(WITHHELD, (static_reasons or ["EVIDENCE_NOT_BOUND"])[0], 8)

    # 9 REAL：静态 REAL 且执行证据不与之冲突
    if static_level == REAL:
        if not pytest_ev.get("bound"):
            warnings.append("REAL_WITHOUT_EXEC_EVIDENCE")
            return out(REAL, "NUMERIC_VERIFICATION_PASS", 9)
        if pytest_ev.get("status") != "PASSED":
            return out(WITHHELD, "EXEC_EVIDENCE_MISSING", 9,
                       {"exec_status": pytest_ev.get("status")})
        if int(pytest_ev.get("numeric_assertions") or 0) < int(
                thresholds.get("exec_numeric_assertions_min", 1)):
            return out(WITHHELD, "EXEC_NON_NUMERIC", 9,
                       {"numeric_assertions": pytest_ev.get("numeric_assertions")})
        return out(REAL, "NUMERIC_VERIFICATION_PASS", 9)

    return out(WITHHELD, "INSUFFICIENT", 10)


def adjudicate_all(static_report: dict, exec_index: dict, thresholds: dict) -> list[dict]:
    """对静态报告里的每个能力跑 v2 级联；顺序与 spec 一致（确定性）。"""
    verdicts = []
    for item in static_report:
        cap_id = item["capability"]
        verdicts.append(adjudicate(item, exec_index.get(cap_id) or {}, thresholds))
    return verdicts


def tally(verdicts: list[dict]) -> dict[str, int]:
    out = {REAL: 0, MOCK: 0, STUB: 0, WITHHELD: 0, NOT_SUPPORTED: 0}
    for v in verdicts:
        out[v["level"]] = out.get(v["level"], 0) + 1
    return out


def agreement(verdicts: list[dict], labels: dict[str, str]) -> dict:
    """与人工复核标签的一致率 + 多数类基线（必须一起报，否则一致率没有意义）。"""
    pairs = [(v["capability"], v["level"]) for v in verdicts
             if labels.get(v["capability"])]
    matched = [1 if lv == labels[cid] else 0 for cid, lv in pairs]
    n = len(pairs)
    majority = 0
    if n:
        counts: dict[str, int] = {}
        for cid in labels:
            if any(cid == c for c, _ in pairs):
                counts[labels[cid]] = counts.get(labels[cid], 0) + 1
        majority = max(counts.values()) / n if counts else 0.0
    return {
        "n": n,
        "matched": sum(matched),
        "agreement": (sum(matched) / n) if n else None,
        "majority_baseline": majority,
        "gain": ((sum(matched) / n) - majority) if n else None,
        "mismatches": [{"capability": cid,
                        "audited": lv,
                        "label": labels[cid]}
                       for cid, lv in pairs if labels[cid] != lv],
    }