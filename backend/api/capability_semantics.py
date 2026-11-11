"""能力台账语义三元分层 API — /api/v1/tensor/capabilities/semantics/*

把 ``tools/capability_semantics`` 的产物（``artifacts/capability_semantics/``）暴露为
可调用端点，供前端能力中心与审计脚本机器复核：

=========================================  ==========================================
端点                                        内容
=========================================  ==========================================
GET /tensor/capabilities/semantics          总览：三层节点计数 / 验收判据 / 工件 sha256
GET .../semantics/layers                    三层结构（每能力一行三元组，可按条件过滤）
GET .../semantics/evidence                  证据等级评定（判据 c 明细）
GET .../semantics/misuse                    NOT_SUPPORTED 误用扫描 + 治理补丁
GET .../semantics/graph                     依赖图 + 环/孤立检测 + DOT 文本
GET .../semantics/debt                      验证欠账台账（逐条整改条件 + 验证量测）
GET .../semantics/acceptance                机器判据 (a)–(f) 原始结论
POST .../semantics/refresh                  触发重算（真实扫描约 45 秒，写回工件）
=========================================  ==========================================

纪律：本端点**只读工件**，判定逻辑全在 tools 层（零领域语义）；工件缺失时返回 503
并提示重算命令，不用内存默认值冒充结论。
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

_API_ROOT = Path(__file__).resolve().parents[2]
if str(_API_ROOT) not in sys.path:  # 让 backend/api 能 import 仓内 tools 包
    sys.path.insert(0, str(_API_ROOT))

from api.deps import require_auth  # noqa: E402

router = APIRouter(prefix="/tensor/capabilities/semantics", tags=["能力台账语义分层"])
logger = logging.getLogger(__name__)

ARTIFACT_DIR = _API_ROOT / "artifacts" / "capability_semantics"
ENGINE_NAME = "tp_capability_semantics"  # 私有包名：避开 backend/tools 影子包


def load_engine():
    """按文件路径加载 ``tools/capability_semantics`` 包。

    不能用 ``import tools.capability_semantics``：服务进程 ``sys.path`` 里
    ``backend`` 在前，``tools`` 会解析成 ``backend/tools``（影子包）→ ImportError。
    """
    if ENGINE_NAME in sys.modules:
        return sys.modules[ENGINE_NAME]
    pkg_dir = _API_ROOT / "tools" / "capability_semantics"
    init = pkg_dir / "__init__.py"
    if not init.exists():
        raise HTTPException(status_code=503, detail=f"引擎缺失: {pkg_dir}")
    spec = importlib.util.spec_from_file_location(
        ENGINE_NAME, init, submodule_search_locations=[str(pkg_dir)])
    if spec is None or spec.loader is None:
        raise HTTPException(status_code=503, detail=f"引擎不可加载: {pkg_dir}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ENGINE_NAME] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001
        sys.modules.pop(ENGINE_NAME, None)
        raise HTTPException(status_code=503, detail=f"引擎加载失败: {exc}") from exc
    return mod
_CACHE: dict[str, tuple[float, Any]] = {}

ARTIFACTS = {
    "summary": "semantic_layers.json",
    "evidence": "evidence_audit_after_patch.json",
    "evidence_before": "evidence_audit.json",
    "misuse": "misuse_report.json",
    "patch": "registry_patch.json",
    "graph": "dependency_graph.json",
    "debt": "verification_debt.json",
    "acceptance": "acceptance.json",
}


def _load(name: str) -> Any:
    """读工件（按 mtime 缓存）。缺失 → 503，不返回编造的空结论。"""
    rel = ARTIFACTS.get(name, name)
    path = ARTIFACT_DIR / rel
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail=(f"工件缺失: {path}；请先运行 "
                    "`python3 -m tools.capability_semantics --figures --doc`"),
        )
    mtime = path.stat().st_mtime
    cached = _CACHE.get(rel)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail=f"工件损坏: {rel}: {exc}") from exc
    _CACHE[rel] = (mtime, payload)
    return payload


def _artifact_hashes() -> dict[str, str]:
    log_path = ARTIFACT_DIR / "audit_log.json"
    if not log_path.exists():
        return {}
    try:
        log = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {rec.get("artifact", ""): rec.get("sha256", "") for rec in log if isinstance(rec, dict)}


@router.get("", dependencies=[Depends(require_auth)])
def semantics_overview(user: dict = Depends(require_auth)) -> dict[str, Any]:
    """语义三元分层总览（含验收判据结论与工件 sha256）。"""
    layers = _load("summary")
    acceptance = _load("acceptance")
    stats = layers.get("stats", {})
    return {
        "status": "ok",
        "data": {
            "schema": "capability-semantics/v1",
            "tiers": {
                "capability": stats.get("tier_node_counts", {}).get("capability", 0),
                "delivery": stats.get("tier_node_counts", {}).get("delivery", 0),
                "evidence": stats.get("tier_node_counts", {}).get("evidence", 0),
            },
            "tier_coverage_ratio": stats.get("tier_coverage_ratio", {}),
            "class_distribution": stats.get("class_distribution", {}),
            "entry_loss_ratio": stats.get("entry_loss_ratio", 0.0),
            "orphan_delivery_count": stats.get("orphan_delivery_count", 0),
            "criteria": {k: {"ok": v.get("ok"), "note": v.get("note", "")}
                         for k, v in acceptance.get("criteria", {}).items()},
            "all_ok": acceptance.get("all_ok"),
            "honest_fail": acceptance.get("honest_fail", {}),
            "artifact_sha256": _artifact_hashes(),
            "warnings": stats.get("warnings", []),
        },
    }


@router.get("/layers", dependencies=[Depends(require_auth)])
def semantics_layers(
    missing_tier: str | None = Query(None, description="只返回缺该层的条目: delivery|evidence"),
    domain: str | None = Query(None, description="按 domain 过滤"),
    limit: int = Query(200, ge=1, le=1000),
    user: dict = Depends(require_auth),
) -> dict[str, Any]:
    """三层结构明细（每能力一行三元组）。"""
    layers = _load("summary")
    triples = layers.get("triples", [])
    if domain:
        triples = [t for t in triples if t.get("domain") == domain]
    if missing_tier == "delivery":
        triples = [t for t in triples if not t.get("layer_complete", {}).get("delivery")]
    elif missing_tier == "evidence":
        triples = [t for t in triples if not t.get("layer_complete", {}).get("evidence")]
    elif missing_tier not in (None, ""):
        raise HTTPException(status_code=422, detail="missing_tier 只接受 delivery|evidence")
    return {
        "status": "ok",
        "data": {
            "total": len(triples),
            "stats": layers.get("stats", {}),
            "triples": triples[:limit],
        },
    }


@router.get("/evidence", dependencies=[Depends(require_auth)])
def semantics_evidence(
    level: str | None = Query(None, description="按评定等级过滤"),
    domain: str | None = Query(None, description="按 domain 过滤"),
    limit: int = Query(200, ge=1, le=1000),
    user: dict = Depends(require_auth),
) -> dict[str, Any]:
    """证据等级评定明细（判据 c）。"""
    report = _load("evidence")
    entries = report.get("entries", [])
    if level:
        entries = [e for e in entries if e.get("derived_level") == level.upper()]
    if domain:
        entries = [e for e in entries if e.get("domain") == domain]
    return {
        "status": "ok",
        "data": {
            "graded_entries": report.get("graded_entries", 0),
            "withheld_ratio": report.get("withheld_ratio", 0.0),
            "withheld_ratio_max": report.get("withheld_ratio_max", 0.0),
            "level_distribution": report.get("level_distribution", {}),
            "reason_histogram": report.get("reason_histogram", {}),
            "withheld_breakdown": report.get("withheld_breakdown", {}),
            "accuracy_histogram": report.get("accuracy_histogram", {}),
            "overclaim_ids": report.get("overclaim_ids", []),
            "entries": entries[:limit],
        },
    }


@router.get("/misuse", dependencies=[Depends(require_auth)])
def semantics_misuse(user: dict = Depends(require_auth)) -> dict[str, Any]:
    """NOT_SUPPORTED 误用扫描结果 + 治理补丁（判据 b）。"""
    report = _load("misuse")
    patch = _load("patch")
    return {
        "status": "ok",
        "data": {
            "total_findings": report.get("total_findings", 0),
            "not_supported_misuse_count": report.get("not_supported_misuse_count", 0),
            "review_needed_count": report.get("review_needed_count", 0),
            "by_kind": report.get("by_kind", {}),
            "findings": report.get("findings", []),
            "patch": {
                "patch_count": patch.get("patch_count", 0),
                "patch_sha256": patch.get("patch_sha256", ""),
                "applies_to_ledger_sha256": patch.get("applies_to_ledger_sha256", ""),
                "patches": patch.get("patches", []),
            },
        },
    }


@router.get("/graph", dependencies=[Depends(require_auth)])
def semantics_graph(
    format: str = Query("json", description="json|dot|mermaid"),
    user: dict = Depends(require_auth),
) -> Any:
    """能力依赖图 + 环/孤立检测。``format=dot`` 返回 Graphviz 文本。"""
    graph = _load("graph")
    if format == "dot":
        dot_path = ARTIFACT_DIR / "dependency_graph.dot"
        if not dot_path.exists():
            raise HTTPException(status_code=503, detail="dependency_graph.dot 缺失")
        return {"status": "ok", "data": {"dot": dot_path.read_text(encoding="utf-8")}}
    if format == "mermaid":
        DependencyGraph = load_engine().DependencyGraph

        g = DependencyGraph(
            nodes=list(graph.get("nodes", [])),
            edges=[(e["dep"], e["dependent"]) for e in graph.get("edges", [])
                   if isinstance(e, dict) and "dep" in e],
            roles=dict(graph.get("roles", {})),
        )
        return {"status": "ok", "data": {"mermaid": g.to_mermaid()}}
    return {
        "status": "ok",
        "data": {
            "nodes": graph.get("nodes", []),
            "edges": graph.get("edges", []),
            "roles": graph.get("roles", {}),
            "summary": graph.get("summary", {}),
        },
    }


@router.get("/debt", dependencies=[Depends(require_auth)])
def semantics_debt(
    priority: str | None = Query(None, description="按优先级过滤: P0|P1|P2|P3"),
    status: str | None = Query(None, description="按欠账状态过滤（OPEN/MEASURED/...）"),
    limit: int = Query(200, ge=1, le=1000),
    user: dict = Depends(require_auth),
) -> dict[str, Any]:
    """验证欠账台账：把 WITHHELD 比例拆成可执行整改项 + 独立验证量测。

    只读工件；工件缺失 → 503（不用内存默认值冒充结论）。
    """
    registry = _load("debt")
    records = registry.get("records", [])
    if priority:
        records = [r for r in records if str(r.get("priority")) == priority.upper()]
    if status:
        records = [r for r in records if str(r.get("status")) == status.upper()]
    return {
        "status": "ok",
        "data": {
            "schema": registry.get("schema"),
            "summary": registry.get("summary", {}),
            "policy": registry.get("policy", {}),
            "runs": registry.get("runs", []),
            "records": records[:limit],
            "filtered_total": len(records),
        },
    }


@router.get("/acceptance", dependencies=[Depends(require_auth)])
def semantics_acceptance(user: dict = Depends(require_auth)) -> dict[str, Any]:
    """机器判据 (a)–(f) 原始结论 + 工件 sha256 链。"""
    acceptance = _load("acceptance")
    return {"status": "ok", "data": {**acceptance, "artifact_sha256": _artifact_hashes()}}


@router.post("/refresh", dependencies=[Depends(require_auth)])
def semantics_refresh(
    evidence: bool = Query(False, description="是否带独立验证工件重算（等价 --evidence）"),
    user: dict = Depends(require_auth),
) -> dict[str, Any]:
    """重算三层结构与全部工件（真实扫描较慢，约 45 秒；仅审计用途）。"""
    del evidence  # 参数保留位：当前扫描内核恒启用验证工件
    run_pipeline = load_engine().run_pipeline

    try:
        result = run_pipeline(root=_API_ROOT, out_dir=ARTIFACT_DIR)
    except Exception as exc:  # noqa: BLE001
        logger.exception("semantics refresh failed")
        raise HTTPException(status_code=500, detail=f"重算失败: {exc}") from exc
    _CACHE.clear()
    return {
        "status": "ok",
        "data": {
            "all_ok": result.acceptance.get("all_ok"),
            "criteria": {k: v.get("ok") for k, v in result.acceptance["criteria"].items()},
            "artifact_sha256": result.artifacts,
            "warnings": result.warnings,
        },
    }