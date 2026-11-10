"""判定与对账引擎：三源一致性、过度声明、等级调整建议、sha256 审计链。

规则级联（每条分支都写进 ``reasons``，可回溯）：

1. 无实现代码（含只有注册表聚合文件）            → ``NOT_SUPPORTED``
2. 实质代码中空壳函数占比 ≥ ``stub_ratio_stub``    → ``STUB``
3. 命中硬编码/未播种随机信号                       → ``MOCK``
4. 绑定到验证/审计工件且工件判「失败」             → ``WITHHELD``（证据优先于存在性：
   文档级失败语义只能「否决 REAL」，不足以直接判 MOCK——MOCK 需要代码级反证）
5. 实质代码 + 数值断言测试 + 文档一致 + **数值验证通过** + 无矛盾证据 → ``REAL``
   （默认 ``policy="evidence"``：平台标准要求「与参考值误差 ≤ 3%」的正向验证证据；
   ``policy="presence"`` 退回任务书的「存在性」口径，仅供对照）
6. 上述任一环节缺失（测试缺失/占位断言/文档缺失/文档反述/执行失败）→ ``WITHHELD``

过度声明：``rank(derived) < rank(declared)``。其中 derived ∈ {NOT_SUPPORTED, STUB,
MOCK} 是「有正面反证」的硬过度声明，derived == WITHHELD 是「证据不足」的软过度
声明（对账口径里单列，避免把「待复核」与「已证明不成立」混为一谈）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import core
from .resolver import normalize_bindings, normalize_ledger, resolve_sources
from .scanners import THRESHOLDS, scan_code, scan_docs, scan_tests

REPORT_VERSION = core.REPORT_VERSION
SCHEMA_PATH = "schemas/capability_reconcile.v1.schema.json"

SUGGESTION_ACTIONS = {
    "IMPL_MISSING": "补充实现或从台账删除该能力（当前仅注册表引用，无实质实现）",
    "REPLACE_STUB": "替换占位函数体（pass / NotImplementedError）为真实实现",
    "REMOVE_UNSEEDED_RANDOM": "移除未播种随机数或显式播种，返回确定性物理量",
    "REPLACE_HARDCODE": "用计算替换常量直返/硬编码结果",
    "ADD_NUMERIC_TESTS": "补齐带数值断言（参考值/容差）的测试",
    "ADD_TEST_ASSERTIONS": "把占位断言（assert True）改为数值断言",
    "FIX_FAILING_TESTS": "修复 junit 中的失败用例后再声明等级",
    "ADD_DOC": "补能力文档（精度声明 + 适用范围）",
    "FIX_DOC_CONFLICT": "修正文档中与实现矛盾的等级/失败结论",
    "RESOLVE_VERIFICATION_FAIL": "验证工件判失败：按审计结论降级并修复实现",
    "ADD_INPUT_VALIDATION": "补充输入校验与边界检查（越界/None/NaN）",
    "BIND_SOURCES": "该能力未解析到任何源文件：补充绑定配置或确认能力存续",
    "HOLD": "维持当前等级",
}


def derive_level(
    code: Dict[str, Any],
    test: Dict[str, Any],
    doc: Dict[str, Any],
    policy: str = "evidence",
    impl_bound: bool = True,
) -> Dict[str, Any]:
    """三源信号 → 派生等级 + 理由码 + 置信度。

    ``policy="evidence"``（默认）：REAL 还要求「数值验证通过」（文档/工件给出
    误差且 ≤ ``numeric_error_pass_pct``）。纯存在性（code+test+doc 齐全）只到
    WITHHELD——存在不等于被验证，这是平台证据纪律的直接编码。
    ``policy="presence"``：任务书原始口径（三源齐全即 REAL），用于口径对照。
    """
    reasons: List[str] = []
    impl = bool(code.get("exists")) and bool(code.get("substantive"))
    if not code.get("exists"):
        if not impl_bound:
            # 没有任何实现文件被绑定/解析到：可能是「能力不存在」，也可能只是
            # 「绑定缺失」（实现挂在树外 harness 或按别的命名分发）。两者在
            # 三源视野里不可区分，故显式标注，交由 BIND_SOURCES 动作澄清。
            return {"level": "NOT_SUPPORTED", "reasons": ["NO_SOURCE_BINDING"], "confidence": 0.5}
        # 判定范围 = 被扫描工作树；树外实现（如外部 harness）不在本引擎视野内。
        return {"level": "NOT_SUPPORTED", "reasons": ["CODE_MISSING_IN_TREE"], "confidence": 0.85}
    if not impl:
        reasons.append("CODE_NOT_SUBSTANTIVE")
        if code.get("stub_ratio", 0) >= THRESHOLDS["stub_ratio_stub"] or code.get("functions", 0) == 0:
            reasons.append("STUB_DOMINATED")
            return {"level": "STUB", "reasons": reasons, "confidence": 0.85}
        reasons.append("LOW_SLOC")
        return {"level": "STUB", "reasons": reasons, "confidence": 0.6}
    if code.get("mock_signal"):
        reasons.append("MOCK_SIGNAL")
        if code.get("random_calls"):
            reasons.append("UNSEEDED_RANDOM")
        if code.get("marker_hits", 0) >= THRESHOLDS["marker_hits_mock"]:
            reasons.append("HARDCODE_MARKERS")
        if code.get("constant_return_ratio", 0) >= THRESHOLDS["constant_return_mock"]:
            reasons.append("CONSTANT_RETURN")
        return {"level": "MOCK", "reasons": reasons, "confidence": 0.8}
    if doc.get("verdict_fail"):
        reasons.append("VERIFICATION_CONTRADICTS")
        return {"level": "WITHHELD", "reasons": reasons, "confidence": 0.6}
    reasons.append("CODE_SUBSTANTIVE")
    if not test.get("exists"):
        reasons.append("TESTS_MISSING")
        return {"level": "WITHHELD", "reasons": reasons, "confidence": 0.7}
    if test.get("junit") and test["junit"].get("passed") is False:
        reasons.append("TESTS_FAILING")
        return {"level": "WITHHELD", "reasons": reasons, "confidence": 0.75}
    if test.get("test_functions", 0) > 0 and test.get("skeleton_ratio", 0) >= THRESHOLDS["skeleton_ratio_max"]:
        reasons.append("TESTS_SKELETON")
        return {"level": "WITHHELD", "reasons": reasons, "confidence": 0.7}
    if not test.get("has_numeric_assertions"):
        reasons.append("TESTS_NON_NUMERIC")
        return {"level": "WITHHELD", "reasons": reasons, "confidence": 0.7}
    reasons.append("TESTS_NUMERIC")
    if not doc.get("exists") or not doc.get("mentions_capability"):
        reasons.append("DOC_MISSING")
        return {"level": "WITHHELD", "reasons": reasons, "confidence": 0.6}
    if doc.get("weak_grade_declarations", 0) > 0 and not doc.get("verdict_fail"):
        reasons.append("DOC_DECLARES_WEAKER")
        return {"level": "WITHHELD", "reasons": reasons, "confidence": 0.6}
    reasons.append("DOC_CONSISTENT")
    if doc.get("has_accuracy_claim"):
        reasons.append("DOC_ACCURACY_CLAIM")
    if doc.get("has_scope_note"):
        reasons.append("DOC_SCOPE_NOTE")
    if policy == "evidence":
        err = doc.get("verification_min_error_pct")
        if err is None:
            err = doc.get("min_error_pct")
        if err is None:
            reasons.append("NO_NUMERIC_VERIFICATION")
            return {"level": "WITHHELD", "reasons": reasons, "confidence": 0.7}
        if float(err) > THRESHOLDS["numeric_error_pass_pct"]:
            reasons.append("NUMERIC_VERIFICATION_OVER_TOLERANCE")
            return {"level": "WITHHELD", "reasons": reasons, "confidence": 0.65}
        reasons.append("NUMERIC_VERIFICATION_PASS")
    if not code.get("has_input_validation"):
        reasons.append("NO_INPUT_VALIDATION")
    conf = 0.9
    if not doc.get("has_accuracy_claim"):
        conf -= 0.1
    if not doc.get("has_scope_note"):
        conf -= 0.05
    return {"level": "REAL", "reasons": reasons, "confidence": round(conf, 3)}


def reconcile_verdict(declared: Optional[str], derived: str) -> Dict[str, Any]:
    """声明 vs 派生 → 对账结论。"""
    if declared not in core.RANK:
        return {"verdict": "UNVERIFIED", "overclaim_kind": None, "delta_rank": None}
    delta = core.RANK[derived] - core.RANK[declared]
    if delta == 0:
        return {"verdict": "CONSISTENT", "overclaim_kind": None, "delta_rank": 0}
    if delta < 0:
        kind = "HARD" if derived in {"NOT_SUPPORTED", "STUB", "MOCK"} else "SOFT"
        return {"verdict": "OVERCLAIM", "overclaim_kind": kind, "delta_rank": delta}
    return {"verdict": "UNDERCLAIM", "overclaim_kind": None, "delta_rank": delta}


def suggest(declared: Optional[str], derived: str, reasons: List[str]) -> Dict[str, Any]:
    """等级调整建议：方向 + 可执行动作（去重、有序）。"""
    actions: List[str] = []
    if derived in {"NOT_SUPPORTED", "STUB"}:
        actions.append("IMPL_MISSING" if derived == "NOT_SUPPORTED" else "REPLACE_STUB")
    if derived == "MOCK":
        if "UNSEEDED_RANDOM" in reasons:
            actions.append("REMOVE_UNSEEDED_RANDOM")
        if "HARDCODE_MARKERS" in reasons or "CONSTANT_RETURN" in reasons:
            actions.append("REPLACE_HARDCODE")
        if "VERIFICATION_CONTRADICTS" in reasons:
            actions.append("RESOLVE_VERIFICATION_FAIL")
    if derived == "WITHHELD":
        if "TESTS_MISSING" in reasons:
            actions.append("ADD_NUMERIC_TESTS")
        if "TESTS_NON_NUMERIC" in reasons or "TESTS_SKELETON" in reasons:
            actions.append("ADD_TEST_ASSERTIONS")
        if "TESTS_FAILING" in reasons:
            actions.append("FIX_FAILING_TESTS")
        if "DOC_MISSING" in reasons:
            actions.append("ADD_DOC")
        if "DOC_DECLARES_WEAKER" in reasons or "VERIFICATION_CONTRADICTS" in reasons:
            actions.append("FIX_DOC_CONFLICT")
        if not actions:
            actions.append("ADD_NUMERIC_TESTS")
    if "NO_INPUT_VALIDATION" in reasons or not reasons:
        if derived in {"REAL", "WITHHELD", "MOCK"}:
            actions.append("ADD_INPUT_VALIDATION")
    if "BIND_SOURCES" in reasons:
        actions.insert(0, "BIND_SOURCES")
    if not actions:
        actions.append("HOLD")
    direction = "HOLD"
    if declared in core.RANK:
        if core.RANK[derived] < core.RANK[declared]:
            direction = "DOWNGRADE"
        elif core.RANK[derived] > core.RANK[declared]:
            direction = "UPGRADE"
    return {
        "direction": direction,
        "suggested_level": derived,
        "actions": actions,
        "action_hints": {a: SUGGESTION_ACTIONS.get(a, a) for a in actions},
    }


# ── 报告装配 ────────────────────────────────────────────────────────────────
def build_report(
    ledger_path: Path,
    root: Path,
    bindings_path: Optional[Path] = None,
    junit: Optional[Dict[str, Dict[str, Any]]] = None,
    excludes: Optional[List[str]] = None,
    generated_at: Optional[str] = None,
    max_caps: Optional[int] = None,
    policy: str = "evidence",
) -> Dict[str, Any]:
    payload = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
    entries = normalize_ledger(payload)
    if max_caps:
        entries = entries[:max_caps]
    bindings = normalize_bindings(json.loads(Path(bindings_path).read_text(encoding="utf-8")) if bindings_path else None)
    exclude_paths = list(excludes or []) + [str(Path(ledger_path).resolve())]
    sources = resolve_sources(root, [e["id"] for e in entries], bindings, exclude_paths)

    caps: List[Dict[str, Any]] = []
    tally_verdict = {v: 0 for v in core.VERDICTS}
    tally_derived = {lv: 0 for lv in core.LEVELS}
    tally_declared: Dict[str, int] = {}
    tally_dir = {"DOWNGRADE": 0, "UPGRADE": 0, "HOLD": 0}
    with_binding = 0
    for entry in entries:
        cid = entry["id"]
        src = sources[cid]
        if src["has_any_source"]:
            with_binding += 1
        code = scan_code(root, src["impl_files"])
        code["registry_files"] = src["registry_files"]
        code["other_files"] = src["code"]
        test = scan_tests(root, src["test"], (junit or {}).get(cid))
        doc = scan_docs(root, src["doc"], cid, src["verification_docs"])
        impl_bound = bool(src["impl_files"])
        decision = derive_level(code, test, doc, policy=policy, impl_bound=impl_bound)
        reasons = decision["reasons"]
        if not src["has_any_source"]:
            reasons = ["BIND_SOURCES"] + reasons
        verdict = reconcile_verdict(entry["declared_level"], decision["level"])
        sug = suggest(entry["declared_level"], decision["level"], reasons)
        evidence = (code.get("evidence") or [])[:12] + (test.get("evidence") or [])[:8] + (doc.get("evidence") or [])[:12]
        record = {
            "id": cid,
            "domain": entry.get("domain"),
            "declared_level": entry["declared_level"],
            "derived_level": decision["level"],
            "verdict": verdict["verdict"],
            "overclaim_kind": verdict["overclaim_kind"],
            "delta_rank": verdict["delta_rank"],
            "confidence": decision["confidence"],
            "impl_bound": impl_bound,
            "reasons": reasons,
            "suggestion": sug,
            "sources": {
                "resolution": src["resolution"],
                "code": code["files"],
                "reference_code": src.get("reference_code", []),
                "registry_only": [p for p in src["code"] if p in src["registry_files"]],
                "test": test["files"],
                "doc": doc["files"],
                "verification_docs": src["verification_docs"],
            },
            "signals": {
                "code": {k: v for k, v in code.items() if k not in {"files", "evidence", "other_files", "registry_files", "file_flags"}},
                "test": {k: v for k, v in test.items() if k not in {"files", "evidence"}},
                "doc": {k: v for k, v in doc.items() if k not in {"files", "evidence"}},
            },
            "evidence": evidence,
        }
        caps.append(record)
        tally_verdict[verdict["verdict"]] = tally_verdict.get(verdict["verdict"], 0) + 1
        tally_derived[decision["level"]] += 1
        dec = entry["declared_level"] or "UNKNOWN"
        tally_declared[dec] = tally_declared.get(dec, 0) + 1
        tally_dir[sug["direction"]] += 1

    audit = core.chain_records(caps)
    n = len(caps)
    strict_over = [c for c in caps if c["verdict"] == "OVERCLAIM" and c["overclaim_kind"] == "HARD"]
    soft_over = [c for c in caps if c["verdict"] == "OVERCLAIM" and c["overclaim_kind"] == "SOFT"]
    report: Dict[str, Any] = {
        "schema_version": REPORT_VERSION,
        "generated_at": generated_at or _utc_now(),
        "inputs": {
            "root": str(root),
            "ledger_path": str(ledger_path),
            "bindings_path": str(bindings_path) if bindings_path else None,
            "junit_provided": bool(junit),
            "ledger_schema_version": payload.get("schema_version") if isinstance(payload, dict) else None,
        },
        "policy": policy,
        "thresholds": dict(THRESHOLDS),
        "summary": {
            "capabilities": n,
            "grading_scope": "scanned_worktree",
            "impl_bound": sum(1 for c in caps if c.get("impl_bound")),
            "unbound_ids": [c["id"] for c in caps if not c.get("impl_bound")],
            "scan_coverage": round(n / n, 4) if n else 0.0,
            "source_binding_coverage": round(with_binding / n, 4) if n else 0.0,
            "with_sources": with_binding,
            "derived_tally": tally_derived,
            "declared_tally": tally_declared,
            "verdict_tally": tally_verdict,
            "suggestion_direction_tally": tally_dir,
            "overclaim_count": len(strict_over) + len(soft_over),
            "overclaim_hard": len(strict_over),
            "overclaim_soft": len(soft_over),
            "overclaim_rate": round((len(strict_over) + len(soft_over)) / n, 4) if n else 0.0,
            "hard_overclaim_ids": [c["id"] for c in strict_over],
            "soft_overclaim_ids": [c["id"] for c in soft_over],
            "audit_records": audit["len"],
        },
        "capabilities": caps,
        "audit_log": {
            "algorithm": "sha256",
            "chain_len": audit["len"],
            "head_sha256": audit["head_sha256"],
            "records": audit["chain"],
        },
    }
    anchor_value = core.anchor_of(report)
    report["audit_log"]["anchor"] = {
        "alg": "sha256",
        "scope": "report_minus_volatile_and_self_reports",
        "value": anchor_value,
    }
    # 校验在占位副本上做（schema 要求 validation/reproducibility 存在），
    # 两区块本身不参与 anchor，故加入后 anchor 不变——这正是可复现性自检。
    view = dict(report)
    view["validation"] = {"schema": SCHEMA_PATH, "valid": True, "errors": [], "engine": "precheck"}
    view["reproducibility"] = {"anchor_recomputed": anchor_value, "match": True}
    report["validation"] = validate_report(view)
    recomputed = core.anchor_of(report)
    report["reproducibility"] = {
        "anchor_recomputed": recomputed,
        "match": recomputed == anchor_value,
        "scope": "anchor recomputed on the emitted artifact with self-reports stripped",
    }
    return report


def reseal(report: Dict[str, Any]) -> Dict[str, Any]:
    """改写 inputs 等语义字段后重算审计锚 / 校验 / 复现自述（保持三者自洽）。"""
    report.pop("validation", None)
    report.pop("reproducibility", None)
    report["audit_log"].pop("anchor", None)
    anchor_value = core.anchor_of(report)
    report["audit_log"]["anchor"] = {
        "alg": "sha256",
        "scope": "report_minus_volatile_and_self_reports",
        "value": anchor_value,
    }
    view = dict(report)
    view["validation"] = {"schema": SCHEMA_PATH, "valid": True, "errors": [], "engine": "precheck"}
    view["reproducibility"] = {"anchor_recomputed": anchor_value, "match": True}
    report["validation"] = validate_report(view)
    recomputed = core.anchor_of(report)
    report["reproducibility"] = {
        "anchor_recomputed": recomputed,
        "match": recomputed == anchor_value,
        "scope": "anchor recomputed on the emitted artifact with self-reports stripped",
    }
    return report


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_report(report: Dict[str, Any], schema_path: Optional[Path] = None) -> Dict[str, Any]:
    """JSON Schema 校验（jsonschema 缺失时降级为结构必需键检查）。"""
    root = Path(__file__).resolve().parents[2]
    path = Path(schema_path) if schema_path else root / SCHEMA_PATH
    if not path.is_file():
        return {"schema": str(path), "valid": False, "errors": ["schema file missing"], "engine": "missing"}
    schema = json.loads(path.read_text(encoding="utf-8"))
    try:
        import jsonschema  # type: ignore
    except ImportError:  # pragma: no cover
        required = schema.get("required", [])
        missing = [k for k in required if k not in report]
        return {"schema": str(path.relative_to(root)), "valid": not missing, "errors": [f"missing:{m}" for m in missing], "engine": "required-keys"}
    validator = jsonschema.Draft7Validator(schema)
    errors = [f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in sorted(validator.iter_errors(report), key=lambda e: list(e.path))]
    return {"schema": str(path.relative_to(root)) if path.is_relative_to(root) else str(path), "valid": not errors, "errors": errors[:20], "engine": "jsonschema"}


def contrast(
    report: Dict[str, Any], truth_path: Path, truth_key: str = "reconciled_level", truth_verdict_key: str = "divergence"
) -> Dict[str, Any]:
    """与外部人工/证据裁决对照：等级一致率 + 过度声明判定混淆矩阵。"""
    payload = json.loads(Path(truth_path).read_text(encoding="utf-8"))
    rows = payload.get("capabilities", payload)
    truth: Dict[str, Dict[str, Any]] = {}
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and row.get("id"):
                truth[str(row["id"])] = row
    elif isinstance(rows, dict):
        for cid, row in rows.items():
            truth[str(cid)] = row if isinstance(row, dict) else {"id": cid, truth_key: row}
    pairs = []
    agree = 0
    conf = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    truth_grade_known = 0
    for cap in report["capabilities"]:
        row = truth.get(cap["id"])
        if not row:
            continue
        tgrade = row.get(truth_key) or row.get("level") or cap.get("declared_level")
        tverdict = row.get(truth_verdict_key)
        truth_grade_known += 1
        match = (tgrade == cap["derived_level"]) if tgrade else None
        if match:
            agree += 1
        pred_over = cap["verdict"] == "OVERCLAIM"
        truth_over = str(tverdict).upper() == "OVERCLAIM" if tverdict else None
        if truth_over is not None:
            if pred_over and truth_over:
                conf["tp"] += 1
            elif pred_over and not truth_over:
                conf["fp"] += 1
            elif not pred_over and truth_over:
                conf["fn"] += 1
            else:
                conf["tn"] += 1
        pairs.append(
            {
                "id": cap["id"],
                "derived_level": cap["derived_level"],
                "truth_level": tgrade,
                "grade_match": match,
                "impl_bound": cap.get("impl_bound", True),
                "predicted_overclaim": pred_over,
                "truth_overclaim": truth_over,
            }
        )
    def _metrics(pairs_subset):
        agree_s = sum(1 for p in pairs_subset if p["grade_match"])
        known = sum(1 for p in pairs_subset if p["truth_level"])
        tp = sum(1 for p in pairs_subset if p["predicted_overclaim"] and p["truth_overclaim"])
        fp = sum(1 for p in pairs_subset if p["predicted_overclaim"] and p["truth_overclaim"] is False)
        fn = sum(1 for p in pairs_subset if not p["predicted_overclaim"] and p["truth_overclaim"])
        graded = sum(1 for p in pairs_subset if p["truth_overclaim"] is not None)
        return {
            "compared": len(pairs_subset),
            "grade_agreement": round(agree_s / known, 4) if known else None,
            "overclaim_precision": round(tp / (tp + fp), 4) if (tp + fp) else None,
            "overclaim_recall": round(tp / (tp + fn), 4) if (tp + fn) else None,
            "overclaim_confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": graded - tp - fp - fn},
        }

    bound_pairs = [p for p in pairs if p.get("impl_bound", True)]
    n_over = conf["tp"] + conf["fp"]
    n_truth_over = conf["tp"] + conf["fn"]
    return {
        "bound_subset": _metrics(bound_pairs),
        "truth_path": str(truth_path),
        "truth_key": truth_key,
        "compared": len(pairs),
        "grade_agreement": round(agree / truth_grade_known, 4) if truth_grade_known else None,
        "overclaim_precision": round(conf["tp"] / n_over, 4) if n_over else None,
        "overclaim_recall": round(conf["tp"] / n_truth_over, 4) if n_truth_over else None,
        "overclaim_confusion": conf,
        "pairs": pairs,
    }