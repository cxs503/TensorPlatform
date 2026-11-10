"""
平台能力审计 API — GET /tensor/capabilities/audit

返回 21 个船舶总体性能能力的证据等级矩阵（REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED），
覆盖阻力/推进/耐波/操纵/稳性/结构/振动噪声/绿色航行/冰区航行九大学科。

数据来源：artifacts/capability_ledger/capability_ledger.json（单一真值）
证据纪律：零手抄，所有数字从 ledger JSON 程序化注入。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.deps import require_auth
from database.config import get_db

router = APIRouter(prefix="/tensor/capabilities", tags=["能力审计"])
logger = logging.getLogger(__name__)

# ── 九大学科域 ──────────────────────────────────────────────────────
NINE_DOMAINS = [
    "阻力", "推进", "耐波", "操纵", "稳性",
    "结构", "振动噪声", "绿色航行", "冰区航行",
]

# ── 21 能力 → 学科映射（从 ledger 程序化提取，此处为审计分类补充） ──
CAPABILITY_DOMAIN_MAP = {
    "propeller.open_water": "推进",
    "calc.stability": "稳性",
    "calc.resistance": "阻力",
    "geometry.hydro_curves": "几何",
    "geometry.generate": "几何",
    "geometry.list_models": "几何",
    "geometry.verify_official": "几何",
    "shiphull.generate": "几何",
    "stdmodels.lookup": "数据",
    "gov.stats": "平台",
    "knowledge.docs": "平台",
    "dev.gate_5415": "开发",
    "dev.git": "开发",
    "dev.logs": "开发",
    "dev.nginx_audit": "开发",
    "dev.probe": "开发",
    "dev.restart": "开发",
    "dev.services": "开发",
    "ai.predict": "AI",
    "methods.computation": "方法",
    "misc.stubs": "杂项",
}

# ── 证据等级排序 ──────────────────────────────────────────────────
LEVEL_RANK = {
    "NOT_SUPPORTED": 0,
    "STUB": 1,
    "MOCK": 2,
    "WITHHELD": 3,
    "REAL": 4,
}

_LEDGER_CACHE: dict | None = None
_LEDGER_PATH = Path(__file__).resolve().parents[2] / "artifacts" / "capability_ledger" / "capability_ledger.json"


def _load_ledger() -> dict:
    """加载能力台账 JSON（带内存缓存）。"""
    global _LEDGER_CACHE
    if _LEDGER_CACHE is not None:
        return _LEDGER_CACHE
    if not _LEDGER_PATH.exists():
        raise FileNotFoundError(f"Capability ledger not found: {_LEDGER_PATH}")
    with open(_LEDGER_PATH, "r", encoding="utf-8") as f:
        _LEDGER_CACHE = json.load(f)
    return _LEDGER_CACHE


def _get_reconciled_level(cap: dict) -> str:
    """
    确定能力的最终证据等级。
    如果有对账证据（evidence 列表），取最新证据的 asserted_level；
    否则取 claimed_level。
    """
    evidence = cap.get("evidence", [])
    if not evidence:
        return cap.get("claimed_level", "WITHHELD")
    # 取最新证据（按 as_of 排序）
    sorted_ev = sorted(
        evidence,
        key=lambda e: str(e.get("as_of", "")),
        reverse=True,
    )
    return sorted_ev[0].get("asserted_level", cap.get("claimed_level", "WITHHELD"))


def _build_audit_matrix() -> dict[str, Any]:
    """构建 21 能力证据矩阵。"""
    ledger = _load_ledger()
    capabilities = ledger.get("capabilities", [])

    matrix = []
    level_counts = {"REAL": 0, "MOCK": 0, "STUB": 0, "WITHHELD": 0, "NOT_SUPPORTED": 0}
    domain_coverage = {d: [] for d in NINE_DOMAINS}

    for cap in capabilities:
        cap_id = cap.get("id", "unknown")
        domain = cap.get("domain", CAPABILITY_DOMAIN_MAP.get(cap_id, "未分类"))
        reconciled_level = _get_reconciled_level(cap)
        claimed_level = cap.get("claimed_level", "WITHHELD")

        # 判断是否有分歧（overclaim）
        has_overclaim = (
            LEVEL_RANK.get(claimed_level, 0) > LEVEL_RANK.get(reconciled_level, 0)
        )

        entry = {
            "id": cap_id,
            "domain": domain,
            "claimed_level": claimed_level,
            "reconciled_level": reconciled_level,
            "evidence_count": len(cap.get("evidence", [])),
            "has_overclaim": has_overclaim,
            "note": cap.get("note", ""),
            "action": cap.get("action", ""),
        }
        matrix.append(entry)

        # 统计
        level_counts[reconciled_level] = level_counts.get(reconciled_level, 0) + 1
        if domain in domain_coverage:
            domain_coverage[domain].append(cap_id)

    # 九大学科覆盖统计
    domain_stats = {}
    for d in NINE_DOMAINS:
        caps = domain_coverage.get(d, [])
        domain_stats[d] = {
            "count": len(caps),
            "capabilities": caps,
            "covered": len(caps) > 0,
        }

    return {
        "schema_version": ledger.get("schema_version", "2.0"),
        "total_capabilities": len(matrix),
        "capabilities": matrix,
        "level_distribution": level_counts,
        "domain_coverage": domain_stats,
        "withheld_count": level_counts.get("WITHHELD", 0),
        "audit_timestamp": ledger.get("evidence_source", {}).get("retrieved_at", ""),
    }


@router.get("/audit", dependencies=[Depends(require_auth)])
def audit_capabilities(
    domain: str | None = Query(None, description="按学科过滤"),
    min_level: str | None = Query(None, description="最低证据等级过滤"),
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """
    GET /tensor/capabilities/audit

    返回 21 能力证据矩阵，含：
    - 每个能力的 claimed_level vs reconciled_level
    - 证据数量
    - 是否存在 overclaim（自述高于对账）
    - 九大学科覆盖统计
    - WITHHELD 计数（验收判据：≤3）
    """
    try:
        result = _build_audit_matrix()
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))

    # 按学科过滤
    if domain:
        result["capabilities"] = [
            c for c in result["capabilities"] if c["domain"] == domain
        ]
        result["total_capabilities"] = len(result["capabilities"])

    # 按最低等级过滤
    if min_level and min_level in LEVEL_RANK:
        min_rank = LEVEL_RANK[min_level]
        result["capabilities"] = [
            c for c in result["capabilities"]
            if LEVEL_RANK.get(c["reconciled_level"], 0) >= min_rank
        ]
        result["total_capabilities"] = len(result["capabilities"])

    return {
        "status": "ok",
        "data": result,
    }


@router.get("/audit/summary", dependencies=[Depends(require_auth)])
def audit_summary(
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """
    GET /tensor/capabilities/audit/summary

    返回精简摘要：等级分布 + 九大学科覆盖 + 验收判据。
    """
    try:
        matrix = _build_audit_matrix()
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))

    # 验收判据
    withheld_ok = matrix["withheld_count"] <= 3
    total_ok = matrix["total_capabilities"] == 21

    return {
        "status": "ok",
        "data": {
            "total_capabilities": matrix["total_capabilities"],
            "level_distribution": matrix["level_distribution"],
            "withheld_count": matrix["withheld_count"],
            "domain_coverage": {
                d: stats["count"] for d, stats in matrix["domain_coverage"].items()
            },
            "acceptance": {
                "total_21": total_ok,
                "withheld_le_3": withheld_ok,
                "all_pass": total_ok and withheld_ok,
            },
        },
    }
