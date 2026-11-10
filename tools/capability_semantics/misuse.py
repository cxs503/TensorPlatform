"""NOT_SUPPORTED 误用治理：识别 → 自动改判（补丁）→ 复扫验证。

误用分类（taxonomy，顺序即严重度）

============================  =========================================================
kind                          定义
============================  =========================================================
``UNDERCLAIM_NOT_SUPPORTED``   声明 NOT_SUPPORTED，但仓内有 code 交付（且有测试更好）
                               → 平台对外报「不支持」，实际有实现；按交付强度改判 STUB/MOCK
``UNREGISTERED_DELIVERY``      端点模块有路由端点，但账上无对应能力条目
                               → 对外等价于 NOT_SUPPORTED（注册表无条目），实际有交付
``CLAIM_WITHOUT_DELIVERY``     声明 STUB/MOCK/WITHHELD/REAL，但扫描零 code 交付、零证据
                               → 反向缺证（软信号：可能是关键词表未覆盖，需人工确认）
============================  =========================================================

改判规则（预声明，不许事后调）：

* 有 code 交付且 test 交付含 ≥ ``min_numeric_assert_tests_mock`` 个数值断言 → ``MOCK``
  （实现了但缺独立验证工件，禁止直接给 REAL）；
* 有 code 交付但无数值断言测试 → ``STUB``；
* 孤立端点模块 → ``STUB``（只证明挂得上路由，不证明算得对）。

**纪律**：治理只输出**补丁工件**（``registry_patch.json``）+ 复扫验证，不改写
规范台账本体。改判必须有交付物 sha256 与复扫前后计数支撑，报告中逐条可复核。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .layering import LayeredLedger, build_layers
from .model import DeliveryArtifact, SemanticTriple, level_rank
from .scan import orphan_endpoint_modules
from .sources import SemanticsConfig, sha256_payload

KIND_UNDERCLAIM = "UNDERCLAIM_NOT_SUPPORTED"
KIND_UNREGISTERED = "UNREGISTERED_DELIVERY"
KIND_CLAIM_WITHOUT_DELIVERY = "CLAIM_WITHOUT_DELIVERY"

SEVERITY = {
    KIND_UNDERCLAIM: "high",
    KIND_UNREGISTERED: "high",
    KIND_CLAIM_WITHOUT_DELIVERY: "low",
}

#: 修复动作码（报告与 API 共用；零领域语义，只描述治理动作）
ACTION_REGISTER = "REGISTER_CAPABILITY_ENTRY"
ACTION_DECLARE_LEVEL = "SET_DECLARED_LEVEL"
ACTION_ADD_BENCHMARK = "ADD_INDEPENDENT_BENCHMARK"
ACTION_ADD_NUMERIC_TEST = "ADD_NUMERIC_ASSERT_TEST"
ACTION_REVIEW_KEYWORDS = "REVIEW_SCAN_KEYWORDS"
ACTION_KEEP_LEDGER = "LEDGER_UNCHANGED_THIS_NODE"

ACTION_TEXT = {
    ACTION_REGISTER: "把交付物注册为能力条目（补丁给出 id/level/basis），消灭「账上无条目」",
    ACTION_DECLARE_LEVEL: "按交付强度改判声明等级，禁止 NOT_SUPPORTED 掩盖已实现能力",
    ACTION_ADD_NUMERIC_TEST: "补带数值断言的单元测试，才能进入 MOCK/REAL 评估",
    ACTION_ADD_BENCHMARK: "补独立验证工件（基准/实验对标）才能上 REAL",
    ACTION_REVIEW_KEYWORDS: "人工确认扫描关键词表是否覆盖该能力（缺覆盖会造成假阴性）",
    ACTION_KEEP_LEDGER: "规范台账本体不在本节点改写，改判以补丁工件形式提交复核",
}


@dataclass
class MisuseFinding:
    kind: str
    capability_id: str
    declared_level: str
    suggested_level: str
    basis: dict[str, Any] = field(default_factory=dict)
    actions: list[str] = field(default_factory=list)
    evidence_sha256: list[str] = field(default_factory=list)

    @property
    def severity(self) -> str:
        return SEVERITY.get(self.kind, "medium")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "capability_id": self.capability_id,
            "declared_level": self.declared_level,
            "suggested_level": self.suggested_level,
            "basis": dict(self.basis),
            "actions": list(self.actions),
            "action_text": [ACTION_TEXT.get(a, a) for a in self.actions],
            "evidence_sha256": list(self.evidence_sha256),
        }


def _delivery_basis(t: SemanticTriple, limit: int = 5) -> dict[str, Any]:
    code = t.code
    tests = t.tests
    verified = t.verified
    return {
        "code_files": len(code),
        "test_files": len(tests),
        "verified_files": len(verified),
        "numeric_asserts": t.numeric_asserts,
        "endpoint_count": t.endpoints,
        "sample_paths": [d.path for d in (code + tests)[:limit]],
    }


def _suggest_for_delivery(t: SemanticTriple, cfg: SemanticsConfig) -> tuple[str, list[str]]:
    """按交付强度给改判等级 + 修复动作。"""
    actions = [ACTION_DECLARE_LEVEL, ACTION_KEEP_LEDGER]
    if t.numeric_asserts >= int(cfg.threshold("min_numeric_assert_tests_mock")):
        level = "MOCK"
        actions.insert(0, ACTION_ADD_BENCHMARK)
    elif t.tests:
        level = "STUB"
        actions.insert(0, ACTION_ADD_NUMERIC_TEST)
    else:
        level = "STUB"
        actions.insert(0, ACTION_ADD_NUMERIC_TEST)
    return level, actions


def scan_misuse(ledger: LayeredLedger, cfg: SemanticsConfig) -> dict[str, Any]:
    """对分层结果做误用扫描（不改判，只登记）。"""
    findings: list[MisuseFinding] = []

    for t in ledger.triples:
        # 只有 code 交付才算「有实现」；仅文档/测试提及不构成实现证据
        if t.declared_level == "NOT_SUPPORTED" and t.code:
            level, actions = _suggest_for_delivery(t, cfg)
            findings.append(MisuseFinding(
                kind=KIND_UNDERCLAIM,
                capability_id=t.capability_id,
                declared_level=t.declared_level,
                suggested_level=level,
                basis=_delivery_basis(t),
                actions=actions,
                evidence_sha256=[d.sha256 for d in t.deliveries if d.sha256][:5],
            ))
        elif (t.declared_level != "NOT_SUPPORTED" and not t.has_delivery
              and not t.has_evidence):
            findings.append(MisuseFinding(
                kind=KIND_CLAIM_WITHOUT_DELIVERY,
                capability_id=t.capability_id,
                declared_level=t.declared_level,
                suggested_level="WITHHELD" if t.has_evidence else "NOT_SUPPORTED",
                basis=_delivery_basis(t),
                actions=[ACTION_REVIEW_KEYWORDS],
            ))

    for mod in ledger.orphan_deliveries:
        stem = mod.path.rsplit("/", 1)[-1].removesuffix(".py")
        findings.append(MisuseFinding(
            kind=KIND_UNREGISTERED,
            capability_id=f"unregistered.{stem}",
            declared_level="NOT_SUPPORTED",
            suggested_level="STUB",
            basis={"path": mod.path, "endpoint_count": mod.endpoint_count,
                   "verbs": list(mod.verbs), "modules": 1,
                   "sha256": mod.sha256},
            actions=[ACTION_REGISTER, ACTION_ADD_NUMERIC_TEST, ACTION_KEEP_LEDGER],
            evidence_sha256=[mod.sha256] if mod.sha256 else [],
        ))

    findings.sort(key=lambda f: (f.severity != "high", f.kind, f.capability_id))
    high = [f for f in findings if f.severity == "high"]
    return {
        "total_findings": len(findings),
        "high_severity_count": len(high),
        "by_kind": _count_by(findings),
        "not_supported_misuse_count": sum(
            1 for f in findings if f.kind in (KIND_UNDERCLAIM, KIND_UNREGISTERED)),
        "review_needed_count": sum(
            1 for f in findings if f.kind == KIND_CLAIM_WITHOUT_DELIVERY),
        "findings": [f.to_dict() for f in findings],
    }


def _count_by(findings: Sequence[MisuseFinding]) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in findings:
        out[f.kind] = out.get(f.kind, 0) + 1
    return {k: out[k] for k in sorted(out)}


def build_registry_patch(misuse_report: Mapping[str, Any], cfg: SemanticsConfig,
                         applies_to: str = "") -> dict[str, Any]:
    """误用扫描结果 → 可复核的注册表补丁工件（不改写台账本体）。"""
    patches: list[dict[str, Any]] = []
    for f in misuse_report.get("findings", []):
        if f.get("severity") != "high":
            continue
        kind = f.get("kind")
        cid = str(f.get("capability_id") or "")
        if not cid:
            continue
        entry: dict[str, Any] = {
            "capability_id": cid,
            "kind": kind,
            "from_level": f.get("declared_level", "NOT_SUPPORTED"),
            "to_level": f.get("suggested_level", "STUB"),
            "basis": f.get("basis", {}),
            "repair_actions": f.get("actions", []),
            "basis_sha256": sha256_payload({"capability_id": cid, "kind": kind,
                                            "basis": f.get("basis", {}),
                                            "to_level": f.get("suggested_level", "STUB")}),
            "evidence_sha256": f.get("evidence_sha256", []),
        }
        if kind == KIND_UNREGISTERED:
            entry["label"] = cid.split(".", 1)[-1]
            entry["domain"] = "unregistered"
            entry["delivery_paths"] = [str(f.get("basis", {}).get("path", ""))]
        patches.append(entry)
    patches.sort(key=lambda p: (p["kind"], p["capability_id"]))
    payload = {
        "schema_version": "1.0",
        "applies_to_ledger_sha256": applies_to,
        "policy": "补丁只提交复核；规范台账本体由本节点之外的流程改写（LEDGER_UNCHANGED_THIS_NODE）",
        "threshold_numeric_pass_pct": cfg.threshold("numeric_pass_pct"),
        "patch_count": len(patches),
        "patches": patches,
    }
    payload["patch_sha256"] = sha256_payload(patches)
    return payload


def verify_patch(
    cfg: SemanticsConfig,
    declarations: Sequence[Any],
    deliveries: Mapping[str, Sequence[Any]],
    evidences: Mapping[str, Sequence[Any]],
    endpoint_modules: Sequence[Any],
    capability_meta: Mapping[str, Mapping[str, str]],
    patch: Mapping[str, Any],
    ledger_ids: Sequence[str],
) -> dict[str, Any]:
    """应用补丁后复扫：改判条目不再报误用，孤立端点归零（修复前后计数对照）。"""
    entries = list(patch.get("patches") or [])
    # 补丁把「孤立端点模块」挂到新能力 id 下：补一个 code 交付，否则复扫仍旧报孤立
    aug_deliveries: dict[str, list[Any]] = {k: list(v) for k, v in deliveries.items()}
    for entry in entries:
        cid = str(entry.get("capability_id") or "")
        for path in entry.get("delivery_paths") or []:
            if not cid or not path:
                continue
            basis = entry.get("basis") or {}
            aug_deliveries.setdefault(cid, []).append(DeliveryArtifact(
                capability_id=cid,
                kind="code",
                path=str(path),
                sha256=str(basis.get("sha256") or ""),
                endpoint_count=int(basis.get("endpoint_count") or 0),
            ))
    ledger_after = build_layers(
        cfg=cfg,
        declarations=declarations,
        deliveries=aug_deliveries,
        evidences=evidences,
        endpoint_modules=endpoint_modules,
        capability_meta=capability_meta,
        patch_entries=entries,
        ledger_ids=ledger_ids,
    )
    after = scan_misuse(ledger_after, cfg)
    fixed_underclaim = [e["capability_id"] for e in entries
                        if e.get("kind") == KIND_UNDERCLAIM]
    fixed_unregistered = [e["capability_id"] for e in entries
                          if e.get("kind") == KIND_UNREGISTERED]
    patched_ids = {e["capability_id"] for e in entries}
    # 复扫中仍在原判上的条目（补丁未生效）
    unfixed = [f["capability_id"] for f in after["findings"]
               if f["severity"] == "high" and f["capability_id"] in patched_ids]
    return {
        "before": {
            "not_supported_misuse_count": None,  # 由 pipeline 填入（声明序）
        },
        "after": {
            "not_supported_misuse_count": after["not_supported_misuse_count"],
            "high_severity_count": after["high_severity_count"],
            "orphan_delivery_count": len(ledger_after.orphan_deliveries),
            "by_kind": after["by_kind"],
        },
        "patched_ids": sorted(patched_ids),
        "fixed_underclaim_ids": sorted(fixed_underclaim),
        "fixed_unregistered_ids": sorted(fixed_unregistered),
        "unfixed_ids": sorted(unfixed),
        "layered_stats_after": ledger_after.stats(),
        "ledger_after": ledger_after,
    }


__all__ = [
    "KIND_UNDERCLAIM",
    "KIND_UNREGISTERED",
    "KIND_CLAIM_WITHOUT_DELIVERY",
    "SEVERITY",
    "ACTION_TEXT",
    "ACTION_REGISTER",
    "ACTION_DECLARE_LEVEL",
    "ACTION_ADD_BENCHMARK",
    "ACTION_ADD_NUMERIC_TEST",
    "ACTION_REVIEW_KEYWORDS",
    "ACTION_KEEP_LEDGER",
    "MisuseFinding",
    "scan_misuse",
    "build_registry_patch",
    "verify_patch",
]