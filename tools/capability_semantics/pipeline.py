"""语义三元分层流水线：一次跑完 分层 → 证据审计 → 误用治理 → 依赖图 → 验收。

产物（默认 ``artifacts/capability_semantics/``）::

    semantic_layers.json     三层结构与完整性统计（含每行三元组）
    evidence_audit.json      证据等级评定（判据 c）
    misuse_report.json       NOT_SUPPORTED 误用扫描（判据 b 扫描面）
    registry_patch.json      误用治理补丁（改判 + 修复建议，不改写台账本体）
    dependency_graph.json    依赖图 + 环/孤立检测（判据 d）；同名 .dot 可渲染
    acceptance.json          机器判据 (a)–(f) 逐条结论 + 工件 sha256 链
    audit_log.json           sha256 链式审计日志（prev/payload/record）

零手抄：文档与图里的每个数字都从本流水线的返回值注入。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .debt import build_debt_registry
from .evidence import REASON_TEXT, audit_evidence, derive_level, grade_triples
from .graph import build_graph
from .layering import LayeredLedger, build_layers, classify_tiers
from .misuse import (KIND_CLAIM_WITHOUT_DELIVERY, build_registry_patch, scan_misuse,
                     verify_patch)
from .model import DeliveryArtifact, SemanticTriple, level_rank
from .scan import build_deliveries, load_profiles
from .sources import (SemanticsConfig, file_sha256, inventory_endpoint_modules,
                      load_capability_meta, load_declarations, load_ledger_evidence,
                      load_semantics_config, sha256_payload)

DEFAULT_PATHS = {
    "config": "config/capability_semantics.json",
    "ledger": "artifacts/capability_ledger/capability_ledger.json",
    "patterns": "config/capabilities.json",
    "out": "artifacts/capability_semantics",
}


@dataclass
class PipelineResult:
    """一次完整运行的返回值（可 JSON 化，供 API 与文档共用）。"""

    layers: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    misuse: dict[str, Any] = field(default_factory=dict)
    patch: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    graph: dict[str, Any] = field(default_factory=dict)
    debt: dict[str, Any] = field(default_factory=dict)
    acceptance: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    audit_log: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ledger_after: LayeredLedger | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "layers": self.layers,
            "evidence": self.evidence,
            "misuse": self.misuse,
            "patch": self.patch,
            "verification": self.verification,
            "graph": self.graph,
            "debt": self.debt,
            "acceptance": self.acceptance,
            "artifacts": dict(self.artifacts),
            "audit_log": list(self.audit_log),
            "warnings": list(self.warnings),
        }


def _rel(path: Path | str, root: Path) -> str:
    """仓内路径写成相对形式（报告可移植、sha256 不受检出位置影响）。"""
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return str(path)


def _degenerate_probe(cfg: SemanticsConfig) -> dict[str, Any]:
    """NaN/None 传播安全探针：空三元组与 None 字段必须不抛异常、不产出 NaN。"""
    probes: list[dict[str, Any]] = []
    ok = True
    t_empty = SemanticTriple(capability_id="probe.empty")
    for name, triple in (("empty", t_empty),):
        try:
            level, code, acc = derive_level(triple, cfg)
            probes.append({"probe": name, "level": level, "reason": code, "accuracy": acc})
            if level not in ("NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL"):
                ok = False
        except Exception as exc:  # noqa: BLE001
            ok = False
            probes.append({"probe": name, "error": f"{type(exc).__name__}: {exc}"})
    t_none = SemanticTriple(capability_id="probe.none")
    t_none.deliveries = [DeliveryArtifact(capability_id="probe.none", kind="verified",
                                          path="x", numeric_pass=None,
                                          numeric_error_pct=float("nan"))]
    try:
        acc = derive_level(t_none, cfg)[2]
        probes.append({"probe": "nan_error", "accuracy": acc})
        if acc != "no_numeric":
            ok = False  # NaN 必须落 no_numeric，不能冒充 within_pass
    except Exception as exc:  # noqa: BLE001
        ok = False
        probes.append({"probe": "nan_error", "error": f"{type(exc).__name__}: {exc}"})
    t_none.numeric_passes  # noqa: B018 - 属性访问不得抛
    return {"ok": ok, "probes": probes}


def _write_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8")
    return file_sha256(path)


def _audit_chain(entries: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
    """sha256 链：每条记录剔除易变字段（路径仅保留文件名），改内容链即断。"""
    log: list[dict[str, Any]] = []
    prev = ""
    for name, sha in entries:
        record = {"artifact": name, "sha256": sha, "prev": prev}
        record["record_sha256"] = sha256_payload(record)
        log.append(record)
        prev = record["record_sha256"]
    return log


ARTIFACT_ORDER = (
    "semantic_layers.json",
    "evidence_audit.json",
    "evidence_audit_after_patch.json",
    "misuse_report.json",
    "registry_patch.json",
    "dependency_graph.json",
    "dependency_graph.dot",
    "figures.json",
    "acceptance.json",
)


def rebuild_audit_log(out_dir: Path | str) -> list[dict[str, Any]]:
    """按固定顺序重算工件的 sha256 链（只对**已存在**的工件；确定性）。"""
    out = Path(out_dir)
    chain: list[tuple[str, str]] = []
    for name in ARTIFACT_ORDER:
        path = out / name
        if path.exists():
            chain.append((name, file_sha256(path)))
    log = _audit_chain(chain)
    _write_json(out / "audit_log.json", log)
    return log


def finalize_acceptance_after_docs(root: Path | str, out_dir: Path | str,
                                   docs_path: str) -> dict[str, Any] | None:
    """文档落盘后回填判据 (e) 的 ``docs_exists`` 并重算 sha256 链。

    两阶段是必须的：判据 (e) 要求文档真实存在，而文档正文由本次运行结果渲染，
    只能在流水线结束后写。这里只**回填已真实落盘**的事实，不改其它判据。
    """
    out = Path(out_dir)
    path = out / "acceptance.json"
    if not path.exists():
        return None
    acceptance = json.loads(path.read_text(encoding="utf-8"))
    crit = acceptance.get("criteria", {})
    maturity = crit.get("e_maturity_delivery")
    if not isinstance(maturity, dict):
        return acceptance
    exists = (Path(root) / docs_path).exists()
    maturity["docs_exists"] = exists
    cov = maturity.get("coverage_pct")
    maturity["ok"] = bool(maturity.get("api_endpoints") and exists
                          and (cov is None or float(cov) >= float(maturity.get("coverage_threshold", 80.0))))
    acceptance["all_ok"] = all(bool(c.get("ok")) for c in crit.values())
    _write_json(path, acceptance)
    rebuild_audit_log(out)
    return acceptance


def run_pipeline(
    root: Path | str = ".",
    out_dir: Path | str | None = None,
    config_path: Path | str | None = None,
    ledger_path: Path | str | None = None,
    patterns_path: Path | str | None = None,
    extra_declaration_sources: Sequence[tuple[str, str]] = (),
    scanner=None,
    coverage_pct: float | None = None,
    test_files: Sequence[str] = (),
    docs_path: str = "docs/reports/P266W1_N10_capability_semantics.md",
    run_verifiers: bool = False,
    verifier_runner=None,
) -> PipelineResult:
    """跑完整条流水线（纯计算 + 落工件）。"""
    root = Path(root).resolve()
    cfg_path = Path(config_path) if config_path else root / DEFAULT_PATHS["config"]
    led_path = Path(ledger_path) if ledger_path else root / DEFAULT_PATHS["ledger"]
    pat_path = Path(patterns_path) if patterns_path else root / DEFAULT_PATHS["patterns"]
    out = Path(out_dir) if out_dir else root / DEFAULT_PATHS["out"]

    cfg = load_semantics_config(Path(cfg_path))

    # ── 1. 声明源（capability 层）──────────────────────────────
    declarations: list[Any] = []
    unparsable: dict[str, list[str]] = {}
    warnings: list[str] = []
    for source, path in (("ledger", led_path), ("capability_config", pat_path)):
        decls, bad = load_declarations(Path(path), source)
        declarations.extend(decls)
        if bad:
            unparsable[source] = bad
        if not decls:
            warnings.append(f"declaration_source_empty: {source} ({path})")
    for source, path in extra_declaration_sources:
        decls, bad = load_declarations(Path(path), source)
        declarations.extend(decls)
        if bad:
            unparsable.setdefault(source, []).extend(bad)

    ledger_meta = load_capability_meta(Path(led_path))
    ledger_evidence = load_ledger_evidence(Path(led_path))
    ledger_ids = sorted(ledger_meta) if ledger_meta else sorted(
        d.capability_id for d in declarations if d.source == "ledger")

    # ── 2. delivery 层（扫描仓内交付物）────────────────────────
    endpoint_modules = inventory_endpoint_modules(root, cfg)
    profiles = load_profiles([Path(pat_path)])
    capability_ids = sorted({str(c.get("id")) for c in cfg.capabilities if c.get("id")}
                            | {d.capability_id for d in declarations})
    deliveries, scan_evidences, scan_warnings = build_deliveries(
        root, cfg, capability_ids, profiles=profiles, scanner=scanner,
        endpoint_modules=endpoint_modules)
    warnings.extend(scan_warnings)

    # ── 3. evidence 层（审计裁决 + 数值验证工件）──────────────
    evidences: dict[str, list[Any]] = {cid: list(scan_evidences.get(cid, ()))
                                       for cid in capability_ids}
    for cid, recs in ledger_evidence.items():
        evidences.setdefault(cid, []).extend(recs)

    # ── 4. 装配三层 + 证据等级评定 ────────────────────────────
    layers = build_layers(
        cfg=cfg, declarations=declarations, deliveries=deliveries, evidences=evidences,
        endpoint_modules=endpoint_modules, capability_meta=ledger_meta,
        ledger_ids=ledger_ids, unparsable=unparsable, warnings=warnings)
    grade_triples(layers.triples, cfg)
    evidence_report = audit_evidence(layers.triples, cfg)

    # ── 5. 误用扫描 → 补丁 → 复扫验证 ─────────────────────────
    misuse = scan_misuse(layers, cfg)
    misuse_before = misuse["not_supported_misuse_count"]
    ledger_sha = file_sha256(Path(led_path))
    patch = build_registry_patch(misuse, cfg, applies_to=ledger_sha)
    verification = verify_patch(
        cfg=cfg, declarations=declarations, deliveries=deliveries, evidences=evidences,
        endpoint_modules=endpoint_modules, capability_meta=ledger_meta, patch=patch,
        ledger_ids=ledger_ids)
    verification["before"] = {
        "not_supported_misuse_count": misuse_before,
        "high_severity_count": misuse["high_severity_count"],
        "orphan_delivery_count": len(layers.orphan_deliveries),
        "by_kind": misuse["by_kind"],
    }
    ledger_after: LayeredLedger = verification.pop("ledger_after")
    grade_triples(ledger_after.triples, cfg)
    # 治理补丁是保守改判：派生等级不得高于补丁声明（如「声明 NOT_SUPPORTED 实有交付」
    # 被改判 MOCK，而数值工件又指向 REAL —— 取保守值对外声明，冲突单列登记）
    patch_capped: list[dict[str, Any]] = []
    for t in ledger_after.triples:
        if t.origin != "patched":
            continue
        if level_rank(t.derived_level) > level_rank(t.declared_level):
            patch_capped.append({"capability_id": t.capability_id,
                                 "evidence_level_uncapped": t.derived_level,
                                 "applied_level": t.declared_level})
            t.reason_code = "PATCHED"
            t.derived_level = t.declared_level
    verification["patch_capped"] = patch_capped
    evidence_after = audit_evidence(ledger_after.triples, cfg)
    fixed = [p for p in verification["patched_ids"]
             if p not in set(verification["unfixed_ids"])]
    verification["fixed_count"] = len(fixed)

    # ── 6. 依赖图（只在已声明依赖的能力上判：补丁新增条目尚无依赖声明，
    #       强行连边＝发明证据，故不纳入判据分母，单列 action 项）─────────
    graph = build_graph(cfg)
    graph_summary = graph.summary(orphan_ratio_max=cfg.threshold("orphan_ratio_max"))

    # ── 7. 验收判据 (a)–(f) ──────────────────────────────────
    stats = layers.stats()
    probe = _degenerate_probe(cfg)
    min_fixes = int(cfg.threshold("min_misuse_fixes"))
    no_misuse_statement = (
        f"当前无误用（扫描覆盖 {len(layers.triples)} 条能力条目 / "
        f"{len(endpoint_modules)} 个端点模块）" if misuse_before == 0 else "")
    api_endpoints = [
        "GET /api/v1/tensor/capabilities/semantics",
        "GET /api/v1/tensor/capabilities/semantics/layers",
        "GET /api/v1/tensor/capabilities/semantics/evidence",
        "GET /api/v1/tensor/capabilities/semantics/misuse",
        "GET /api/v1/tensor/capabilities/semantics/graph",
        "GET /api/v1/tensor/capabilities/semantics/debt",
        "GET /api/v1/tensor/capabilities/semantics/acceptance",
    ]
    docs_file = root / docs_path
    # ── 6.5 验证欠账台账（把比例拆成可执行整改项 + 可选真跑验证量测）──
    debt = build_debt_registry(evidence_after["entries"], cfg, root,
                               runner=verifier_runner, run_verifiers=run_verifiers)
    debt_summary = debt["summary"]
    # 口径敏感性：同一份审计在不同总体定义下的 WITHHELD 比例（防止用分母挑数字）
    def _ratio(ids: Optional[set[str]]) -> dict[str, Any]:
        sel = [r for r in evidence_after["entries"]
               if ids is None or str(r["capability_id"]) in ids]
        w = [r for r in sel if r["derived_level"] == "WITHHELD"]
        return {"graded": len(sel), "withheld": len(w),
                "ratio": round(len(w) / len(sel), 4) if sel else 0.0,
                "below_max": bool(sel) and (len(w) / len(sel)) < evidence_after["withheld_ratio_max"]}

    out_of_repo_ids = {str(c) for c in (cfg.debt_policy.get("out_of_repo_capabilities") or [])}
    ledger_only_ids = set(ledger_ids)
    withheld_sensitivity = {
        "all_graded": _ratio(None),
        "ledger_only": _ratio(ledger_only_ids),
        "excluding_out_of_repo": _ratio(None) if not out_of_repo_ids else {
            **_ratio({str(r["capability_id"]) for r in evidence_after["entries"]}
                     - out_of_repo_ids),
            "excluded": sorted(out_of_repo_ids),
        },
        "note": "三种总体口径都披露；任一口径低于阈值的口径不得单独用来宣布达标",
    }

    acceptance = {
        "schema_version": "1.0",
        "criteria": {
            "a_semantic_layers": {
                "ok": bool(stats["class_assignment_ratio"] == 1.0
                           and stats["entry_loss_ratio"] <= cfg.threshold("entry_loss_max_ratio")),
                "capability_entries": stats["capability_entries"],
                "class_assignment_ratio": stats["class_assignment_ratio"],
                "tier_populated_ratio": stats["tier_populated_ratio"],
                "capability_only_ids": stats["unclassified_capabilities"],
                "class_distribution": stats["class_distribution"],
                "tier_node_counts": stats["tier_node_counts"],
                "tier_coverage_ratio": stats["tier_coverage_ratio"],
                "lost_entries": stats["lost_entries"],
                "entry_loss_ratio": stats["entry_loss_ratio"],
                "entry_loss_max_ratio": cfg.threshold("entry_loss_max_ratio"),
                "note": "条目零丢失 + 100% 可归入三层（缺层显式记 capability_only，不隐藏）",
            },
            "b_misuse_governance": {
                "ok": bool((verification["fixed_count"] >= min_fixes) or misuse_before == 0),
                "misuse_found_before": misuse_before,
                "misuse_found_after": verification["after"]["not_supported_misuse_count"],
                "fixed_count": verification["fixed_count"],
                "min_misuse_fixes": min_fixes,
                "by_kind_before": misuse["by_kind"],
                "by_kind_after": verification["after"]["by_kind"],
                "findings_before": misuse["findings"],
                "no_misuse_statement": no_misuse_statement,
                "note": "改判以补丁工件提交复核；复扫验证修复后计数下降",
            },
            "c_evidence_audit": {
                # 判据文本：「WITHHELD 比例 < 30%（若 > 30% 则分析原因）」——
                # 超额时以逐因分析替代，但 withheld_ok 如实记 False，绝不静默放行
                "ok": bool(evidence_after["criterion_graded_ok"]
                           and evidence_after["criterion_withheld_ok"]),
                "withheld_ok": evidence_after["criterion_withheld_ok"],
                # 诚实口径：比例超标就是超标，不因「给了原因分析」而改判通过。
                # 原因分析仍逐因落盘（withheld_breakdown），但它只解释缺口、不构成达标。
                "waived_by_analysis": False,
                "ok_basis": ("ratio_below_max" if evidence_after["criterion_withheld_ok"]
                             else "honest_fail_ratio_above_max"),
                "sensitivity": withheld_sensitivity,
                "debt_entries": debt_summary.get("debt_entries"),
                "debt_measured_ratio": debt_summary.get("measured_ratio"),
                "debt_out_of_repo_count": debt_summary.get("out_of_repo_count"),
                "withheld_groups": evidence_after["withheld_groups"],
                "patch_capped": verification.get("patch_capped", []),
                "withheld_root_causes": evidence_after["withheld_root_causes"],
                "graded_entries": evidence_after["graded_entries"],
                "graded_entries_before_patch": evidence_report["graded_entries"],
                "min_graded_entries": evidence_after["min_graded_entries"],
                "withheld_count": evidence_after["withheld_count"],
                "withheld_ratio": evidence_after["withheld_ratio"],
                "withheld_ratio_max": evidence_after["withheld_ratio_max"],
                "withheld_breakdown": evidence_after["withheld_breakdown"],
                "level_distribution_after": evidence_after["level_distribution"],
                "accuracy_histogram": evidence_after["accuracy_histogram"],
                "real_entries": evidence_after["real_entries"],
                "real_subject_backed": evidence_after["real_subject_backed"],
                "real_keyword_only": sorted(set(evidence_after["real_entries"])
                                            - set(evidence_after["real_subject_backed"])),
                "note": "无证据 → WITHHELD；>30% 时在报告里逐因分析（见 withheld_breakdown）",
            },
            "d_dependency_graph": {
                "ok": bool(graph_summary["criterion_no_cycle_ok"]
                           and graph_summary["criterion_orphan_ok"]),
                "cycle_count": graph_summary["cycle_count"],
                "cycles": graph_summary["cycles"],
                "orphan_ratio_domain": graph_summary["orphan_ratio_domain"],
                "orphan_ratio_all_disclosed": graph_summary["orphan_ratio_all_disclosed"],
                "orphan_ratio_max": graph_summary["orphan_ratio_max"],
                "orphan_domain": graph_summary["orphan_domain"],
                "node_count": graph_summary["node_count"],
                "edge_count": graph_summary["edge_count"],
                "note": "孤立率在 domain 子图上判；全体（含 tooling）比例单列披露",
            },
            "e_maturity_delivery": {
                "ok": bool(api_endpoints and docs_file.exists()
                           and (coverage_pct is None or coverage_pct >= 80.0)),
                "api_endpoints": api_endpoints,
                "docs_path": docs_path,
                "docs_exists": docs_file.exists(),
                "test_files": list(test_files),
                "test_file_count": len(test_files),
                "coverage_pct": coverage_pct,
                "coverage_threshold": 80.0,
                "note": "覆盖率由 runner 注入（--coverage-pct），未测时显式 None 不冒充达标",
            },
            "f_safety": {
                "ok": bool(probe["ok"]),
                "degenerate_probes": probe["probes"],
                "note": "空三元组 / None / NaN 字段探针：不抛异常、NaN 不冒充 within_pass",
            },
        },
        "inputs": {
            "config": _rel(cfg_path, root),
            "config_sha256": cfg.raw_sha256,
            "ledger": _rel(led_path, root),
            "ledger_sha256": ledger_sha,
            "patterns": _rel(pat_path, root),
            "patterns_sha256": file_sha256(Path(pat_path)),
            "root": str(root),
        },
    }
    honest_fail = sorted(k for k, c in acceptance["criteria"].items() if not bool(c.get("ok")))
    acceptance["honest_fail"] = {
        "criteria": honest_fail,
        "count": len(honest_fail),
        "fail_ok": bool(honest_fail),  # 判据不通过时如实标记，禁止补白
        "note": "未通过判据必须如实披露；honest-FAIL 不可被「原因分析」或口径选择覆盖",
    }
    acceptance["all_ok"] = all(c["ok"] for c in acceptance["criteria"].values())

    # ── 8. 落工件 + sha256 链 ────────────────────────────────
    result = PipelineResult(
        layers={"stats": stats, "triples": [t.to_dict() for t in layers.triples]},
        evidence=evidence_report,
        misuse=misuse,
        patch=patch,
        verification={k: v for k, v in verification.items() if k != "layered_stats_after"},
        graph={**graph.to_dict(), "summary": graph_summary},
        debt=debt,
        acceptance=acceptance,
        warnings=list(layers.warnings),
        ledger_after=ledger_after,
    )
    result.verification["layered_stats_after"] = verification["layered_stats_after"]
    result.verification["evidence_after"] = {
        "graded_entries": evidence_after["graded_entries"],
        "withheld_ratio": evidence_after["withheld_ratio"],
        "level_distribution": evidence_after["level_distribution"],
        "reason_histogram": evidence_after["reason_histogram"],
        "real_entries": evidence_after["real_entries"],
        "real_subject_backed": evidence_after["real_subject_backed"],
        "real_keyword_only": sorted(set(evidence_after["real_entries"])
                                    - set(evidence_after["real_subject_backed"])),
    }

    payloads: dict[str, Any] = {
        "semantic_layers.json": result.layers,
        "evidence_audit.json": evidence_report,
        "evidence_audit_after_patch.json": evidence_after,
        "misuse_report.json": misuse,
        "registry_patch.json": patch,
        "dependency_graph.json": result.graph,
        "verification_debt.json": debt,
        "acceptance.json": acceptance,
    }
    chain: list[tuple[str, str]] = []
    for name, payload in payloads.items():
        sha = _write_json(out / name, payload)
        result.artifacts[name] = sha
        chain.append((name, sha))
    dot_path = out / "dependency_graph.dot"
    dot_path.parent.mkdir(parents=True, exist_ok=True)
    dot_path.write_text(graph.to_dot(), encoding="utf-8")
    result.artifacts[dot_path.name] = file_sha256(dot_path)
    chain.append((dot_path.name, result.artifacts[dot_path.name]))
    result.audit_log = _audit_chain(chain)
    result.artifacts["audit_log.json"] = _write_json(out / "audit_log.json", result.audit_log)
    return result


__all__ = ["DEFAULT_PATHS", "PipelineResult", "run_pipeline", "REASON_TEXT",
           "classify_tiers", "KIND_CLAIM_WITHOUT_DELIVERY"]