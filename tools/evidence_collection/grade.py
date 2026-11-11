"""证据等级判定器（REAL / MOCK / STUB / WITHHELD / NOT_SUPPORTED）。

规则集 ``ec-grade-v1``（阈值全部来自 spec，代码零领域语义）
=========================================================
按顺序级联，第一条命中即判决，命中的理由码写进验收工件：

1. 无任何已执行证据                     → WITHHELD  ``NO_EXECUTED_EVIDENCE``
2. 不变量判据（class=invariant）失败     → MOCK      ``INVARIANT_VIOLATED``
3. 参考误差 > accept_tolerance_pct      → MOCK      ``REFERENCE_OVER_ACCEPT``
4. 声明的端点**运行期**不存在(404/405)  → NOT_SUPPORTED ``SURFACE_NOT_IMPLEMENTED``
5. 参考误差 ≤ real_tolerance_pct 且无失败判据、无红测试 → REAL ``REFERENCE_VERIFIED``
6. 参考误差落在 (real, accept] 区间      → WITHHELD  ``BELOW_REAL_TOLERANCE``
7. 绑定测试红                           → WITHHELD  ``EXEC_FAILED``
8. 契约判据失败                         → WITHHELD  ``CONTRACT_FAILED``
9. 面清单全部缺（端点已下线）           → WITHHELD  ``SURFACE_ABSENT``
10. 只有契约/不变量证据、无外部参考值   → WITHHELD  ``CONTRACT_ONLY`` (或 ``NO_REFERENCE``)

设计要点：**只有第 5 条能给出 REAL**。契约检查通过、测试全绿、不变量自洽都不足以
证明"算对了"——它们只证明"能用、没自相矛盾"。这条纪律是本管线的核心：
自证的门槛必须锚在外部参考值上，否则"自动化"会退化成"自动盖章"。
"""
from __future__ import annotations

from tools.capability_audit_gate.taxonomy import (  # 复用已测试的五级偏序/变更分类
    LEVELS,
    POSITIVELY_REFUTED,
    RANK,
    classify_change,
    is_downgrade,
    normalize,
    tally,
)

RULESET = "ec-grade-v1"

REAL = "REAL"
MOCK = "MOCK"
STUB = "STUB"
WITHHELD = "WITHHELD"
NOT_SUPPORTED = "NOT_SUPPORTED"

__all__ = [
    "LEVELS", "RANK", "POSITIVELY_REFUTED", "normalize", "classify_change", "is_downgrade",
    "tally", "grade", "RULESET", "REAL", "MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED",
]


def _criteria(signals: dict) -> list[dict]:
    rows: list[dict] = []
    for probe in signals.get("probes") or []:
        rows.extend(probe.get("criteria") or [])
    return rows


def _executed_items(signals: dict) -> int:
    n = 0
    for probe in signals.get("probes") or []:
        if probe.get("executed"):
            n += 1
    ref = signals.get("reference") or {}
    if ref.get("status") not in (None, "MISSING"):
        n += 1
    tests = signals.get("tests") or {}
    if tests.get("status") not in (None, "MISSING"):
        n += 1
    surface = signals.get("surface") or {}
    if surface.get("status") not in (None, "NOT_EXECUTED"):
        n += 1
    return n


def grade(signals: dict, thresholds: dict) -> dict:
    """把采集信号压成一个证据等级 + 理由码 + 可复核的中间量。"""
    real_tol = float(thresholds["real_tolerance_pct"])
    accept_tol = float(thresholds["accept_tolerance_pct"])
    rows = _criteria(signals)
    ref = signals.get("reference") or {}
    tests = signals.get("tests") or {}
    surface = signals.get("surface") or {}

    fails_invariant = [r["id"] for r in rows if r["class"] == "invariant" and r["verdict"] == "FAIL"]
    fails_contract = [r["id"] for r in rows if r["class"] == "contract" and r["verdict"] == "FAIL"]
    fails_reference = [r["id"] for r in rows if r["class"] == "reference" and r["verdict"] == "FAIL"]
    # 只有"状态码判据 FAIL"才构成运行期不可达的正面反证；期望内（如显式允 404）不算
    status_codes = set()
    for row in rows:
        if row["kind"] == "status" and row["verdict"] == "FAIL" and isinstance(row["measured"], list):
            status_codes.update(c for c in row["measured"] if isinstance(c, int))

    reasons: list[str] = []
    level = WITHHELD

    executed = _executed_items(signals)
    ref_worst = ref.get("worst_pct")
    tests_status = tests.get("status")
    tests_totals = tests.get("totals") or {}
    absent_only = bool(surface.get("absent")) and not surface.get("present")

    if executed == 0:
        reasons = ["NO_EXECUTED_EVIDENCE"]
        level = WITHHELD
    elif fails_invariant:
        reasons = ["INVARIANT_VIOLATED", f"criteria={','.join(fails_invariant)}"]
        level = MOCK
    elif ref_worst is not None and float(ref_worst) > accept_tol:
        reasons = ["REFERENCE_OVER_ACCEPT",
                   f"worst_pct={float(ref_worst):.4g}>accept={accept_tol:g}"]
        level = MOCK
    elif fails_reference:
        reasons = ["REFERENCE_CRITERION_FAILED", f"criteria={','.join(fails_reference)}"]
        level = MOCK
    elif status_codes and status_codes <= {404, 405}:
        reasons = ["SURFACE_NOT_IMPLEMENTED", f"status={sorted(status_codes)}"]
        level = NOT_SUPPORTED
    elif (ref_worst is not None and float(ref_worst) <= real_tol
          and not fails_contract and tests_status not in {"FAILED", "ERROR", "TIMEOUT"}):
        reasons = ["REFERENCE_VERIFIED",
                   f"worst_pct={float(ref_worst):.6g}<=real={real_tol:g}"]
        level = REAL
    elif ref_worst is not None and float(ref_worst) > real_tol:
        reasons = ["BELOW_REAL_TOLERANCE",
                   f"worst_pct={float(ref_worst):.6g}>real={real_tol:g}"]
        level = WITHHELD
    elif tests_status in {"FAILED", "ERROR", "TIMEOUT"}:
        reasons = ["EXEC_FAILED",
                   f"tests={tests_status} failed={tests_totals.get('failures', 0)}"
                   f" errors={tests_totals.get('errors', 0)}"]
        level = WITHHELD
    elif fails_contract:
        reasons = ["CONTRACT_FAILED", f"criteria={','.join(fails_contract)}"]
        level = WITHHELD
    elif absent_only:
        reasons = ["SURFACE_ABSENT"]
        level = WITHHELD
    else:
        reasons = ["CONTRACT_ONLY" if rows else "NO_REFERENCE"]
        level = WITHHELD

    if tests_status == "PASSED" and level in {REAL, WITHHELD}:
        reasons.append(f"TESTS_PASSED n={tests_totals.get('passed', 0)}")
    if surface.get("present"):
        reasons.append(f"SURFACE_PRESENT n={len(surface['present'])}")
    if ref.get("sha256"):
        reasons.append(f"REFERENCE_ARTIFACT sha256={ref['sha256'][:16]}")

    return {
        "capability": signals.get("capability"),
        "level": level,
        "reason_codes": reasons,
        "ruleset": RULESET,
        "executed_evidence_items": executed,
        "counts": {
            "probes": len(signals.get("probes") or []),
            "criteria": len(rows),
            "criteria_pass": sum(1 for r in rows if r["verdict"] == "PASS"),
            "criteria_fail": sum(1 for r in rows if r["verdict"] == "FAIL"),
            "tests_total": tests_totals.get("tests", 0),
            "tests_passed": tests_totals.get("passed", 0),
        },
        "signals_digest_inputs": {
            "probes": [p.get("digest") for p in (signals.get("probes") or [])],
            "reference": ref.get("sha256"),
            "tests": (signals.get("tests") or {}).get("modules_digest"),
            "surface": (signals.get("surface") or {}).get("status"),
        },
    }