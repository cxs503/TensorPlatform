"""战役成熟化整合门的 HTTP 面（无状态裁判）。

两个端点（契约见 ``docs/contracts/maturity-integration-v1.md``）：

* ``POST /api/v1/tensor/campaigns/maturity-gate`` —— 收「战役规格 + 证据文档载荷」，
  返回 PASS/FAIL/WITHHELD 判定 + 台账双射 + 待办阻塞项。
* ``GET  /api/v1/tensor/campaigns/maturity-gate/spec-schema`` —— 规格结构说明（自描述）。

为什么是**无状态**：证据原文在各领域仓的工作树里，平台侧不该把别的仓的文件读进服务进程；
调用方（交付节点/CI）把载荷带上来，服务端只做判定，因此可以逐字节复算、无副作用、无残留。
平台共性机制层 → **零领域语义**：本文件里没有任何学科词条或域阈值。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:  # pragma: no cover - 导入路径兜底
    sys.path.insert(0, str(_ROOT))

from tools.maturity_gate import gate as gate_engine  # noqa: E402
from tools.maturity_gate import ledger as ledger_engine  # noqa: E402
from tools.maturity_gate.spec import SpecError, parse_spec  # noqa: E402

router = APIRouter(tags=["campaign-maturity"])

GATE_SCHEMA_VERSION = gate_engine.GATE_SCHEMA_VERSION


class MaturityGateRequest(BaseModel):
    """请求体：战役规格 + 证据载荷（结构见 contracts/maturity-gate.v1.schema.json）。"""

    spec_id: str = Field(..., description="战役/整合批次标识")
    criteria_pass_threshold: float = Field(0.8, gt=0.0, le=1.0)
    nodes: List[Dict[str, Any]] = Field(..., min_length=1)
    evidence_docs: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="doc 名 → 证据载荷；doc 名须与规格 checks[].doc 完全一致")


class MaturityGateResponse(BaseModel):
    gate_schema_version: str
    spec_id: str
    input_digest: str
    nodes: List[Dict[str, Any]]
    node_summaries: List[Dict[str, Any]]
    campaign: Dict[str, Any]
    ledger: Dict[str, Any]
    leases_note: str = "无状态裁判：不落库、不读盘；input_digest 可逐字节复算"


def judge(request: MaturityGateRequest) -> Dict[str, Any]:
    """纯函数核心：请求体 → 判定体（含台账双射）。可被 CLI / 测试直接调用。"""
    spec_raw = {
        "spec_id": request.spec_id,
        "schema_version": "1.0",
        "criteria_pass_threshold": request.criteria_pass_threshold,
        "nodes": request.nodes,
    }
    spec = parse_spec(spec_raw)
    result = gate_engine.evaluate_campaign_inline(spec_raw, request.evidence_docs)
    entries = ledger_engine.build_entries(result)
    bijection = ledger_engine.verify_bijection(entries, spec.node_keys())
    result["ledger"] = ledger_engine.ledger_document(
        entries, bijection, spec.spec_id, result["input_digest"])
    return result


@router.post("/tensor/campaigns/maturity-gate", response_model=MaturityGateResponse,
             summary="战役成熟化整合门：三值裁定 + 台账双射")
def post_maturity_gate(request: MaturityGateRequest) -> MaturityGateResponse:
    try:
        result = judge(request)
    except SpecError as exc:
        # 配置错误 ≠ 判据失败：400 而不是 200 + FAIL
        raise HTTPException(status_code=400, detail={"error": "spec_invalid",
                                                    "message": str(exc)}) from exc
    return MaturityGateResponse(**result)


@router.get("/tensor/campaigns/maturity-gate/spec-schema", summary="规格结构自描述")
def get_spec_schema() -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "spec_id": "string",
        "criteria_pass_threshold": {"type": "number", "default": 0.8, "range": "(0, 1]"},
        "nodes": [{
            "node_key": "string (unique)",
            "title": "string",
            "repo": "string",
            "mission_id": "int (optional)",
            "capability": "string (optional, 台账条目名)",
            "criteria": [{
                "criterion_id": "string (unique within node)",
                "statement": "string",
                "unit": "string (optional)",
                "checks": [{
                    "doc": "evidence_docs 的键",
                    "pointer": "a.b[0].c",
                    "comparator": ["le", "lt", "ge", "gt", "eq", "between", "nonempty"],
                    "threshold": "number | [lo, hi] | bool(eq)",
                    "sha256": "hex64 (optional pin)",
                    "claim": "pointer to upstream bool (optional)",
                }],
            }],
        }],
        "verdicts": ["PASS", "FAIL", "WITHHELD"],
        "verdict_rules": {
            "FAIL": "任一已交付节点 pass_ratio < threshold，或出现 claim_mismatch",
            "PASS": "全部节点已交付证据且 pass_ratio >= threshold",
            "WITHHELD": "无低绿线节点但仍有节点零证据（在飞/未交付）",
        },
    }