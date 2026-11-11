"""治理成熟化**集成验证** API（节点 p465w1-N07）。

与同目录的 ``governance_maturity.py``（m8339 的报表门/证据链/能力台账/自愈库）并存：
那个模块是**库**（四个成熟化能力的实现），本模块是**面**（把 N01×N02×N03 的集成验证
暴露成 HTTP 端点），两者互不改写。

四个端点：

===============================  ==================================================
``GET  /contracts``              三契约源 + 场交换契约的清单与 sha256 指纹（审计链锚点）
``POST /audit``                  度量-契约一致性（M1–M8）+ 场交换-契约一致性（F1–F8）
``POST /e2e-run``                端到端治理链路（心跳→事件流→画像→场交换→契约校验）
``GET  /report``                 最近一次成熟化报告（acceptance.json，含判据数字与路径）
===============================  ==================================================

设计约束
--------
* **零领域语义**：端点只吃 agent/工单/端口这类机制参数，不认识任何学科量；
* **非法输入 422**：``scope`` 只允许 ``metrics``/``field``；仿真规模上限
  ``num_agents * ticks <= 500000``（超过就 422，而不是把 API 进程拖死）；
* **诚实降级**：审计/仿真自身异常返回 500 + 具名 detail，不返回"看起来 PASS"的空壳。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.tensorplatform.governance_maturity import contracts as GM  # noqa: E402
from src.tensorplatform.governance_maturity.e2e import (  # noqa: E402
    E2EConfig,
    run_governance_chain,
)
from src.tensorplatform.governance_maturity.field_audit import audit_field_exchange  # noqa: E402
from src.tensorplatform.governance_maturity.metrics_audit import audit_metrics_contract  # noqa: E402

router = APIRouter(prefix="/governance/maturity", tags=["governance-maturity"])

REPORT_REL = Path("artifacts") / "governance-maturity" / "acceptance.json"
AUDIT_SCOPES = ("metrics", "field")
MAX_EVENTS = 500_000


class AuditRequest(BaseModel):
    scope: List[str] = Field(default_factory=lambda: list(AUDIT_SCOPES))
    runtime: bool = True

    @field_validator("scope")
    @classmethod
    def _scope_known(cls, value: List[str]) -> List[str]:
        if not value:
            raise ValueError("scope 不能为空；可选 metrics/field")
        unknown = [s for s in value if s not in AUDIT_SCOPES]
        if unknown:
            raise ValueError(f"未知 scope: {unknown}；可选 {list(AUDIT_SCOPES)}")
        return list(dict.fromkeys(value))


class E2ERequest(BaseModel):
    num_agents: int = Field(100, ge=1, le=500, description="并发 agent 数")
    ticks: int = Field(1000, ge=1, le=5000, description="每个 agent 的心跳次数")
    drop_rate: float = Field(0.0, ge=0.0, le=0.5, description="故障注入丢包率（正对照用）")
    exchange_every: int = Field(100, ge=1, le=5000, description="每多少个 tick 插一次场交换")
    mission_every: int = Field(50, ge=1, le=5000)
    mission_length: int = Field(4, ge=2, le=64)
    seed: int = Field(20261111, ge=0, le=2 ** 31 - 1)

    @field_validator("num_agents")
    @classmethod
    def _size_guard(cls, value: int, info) -> int:
        ticks = info.data.get("ticks", 1000)
        if value * ticks > MAX_EVENTS:
            raise ValueError(f"规模过大: num_agents*ticks={value * ticks} > {MAX_EVENTS}")
        return value

    @field_validator("ticks")
    @classmethod
    def _size_guard_ticks(cls, value: int, info) -> int:
        agents = info.data.get("num_agents", 100)
        if agents * value > MAX_EVENTS:
            raise ValueError(f"规模过大: num_agents*ticks={agents * value} > {MAX_EVENTS}")
        return value


@router.get("/contracts")
def get_contracts() -> Dict[str, Any]:
    """契约清单与指纹（供第三方按 sha256 复核报告里的契约版本）。"""
    try:
        return {"status": "ok", "inventory": GM.contract_inventory()}
    except GM.ContractError as exc:
        raise HTTPException(status_code=500, detail=f"CONTRACT_ERROR: {exc}") from exc


@router.post("/audit")
def post_audit(req: Optional[AuditRequest] = None) -> Dict[str, Any]:
    """度量-契约一致性（M1–M8）+ 场交换-契约一致性（F1–F8）。"""
    req = req or AuditRequest()
    out: Dict[str, Any] = {"scopes": req.scope}
    try:
        if "metrics" in req.scope:
            out["metrics"] = audit_metrics_contract(runtime=req.runtime).as_dict()
        if "field" in req.scope:
            out["field"] = audit_field_exchange().as_dict()
    except Exception as exc:  # noqa: BLE001 - 显式 500，不返回空壳 PASS
        raise HTTPException(status_code=500,
                            detail=f"AUDIT_ERROR: {type(exc).__name__}: {exc}") from exc
    diffs = [out[k]["total_diff"] for k in ("metrics", "field") if k in out]
    out["total_diff"] = sum(diffs)
    out["status"] = "PASS" if out["total_diff"] == 0 else "FAIL"
    return out


@router.post("/e2e-run")
def post_e2e(req: Optional[E2ERequest] = None) -> Dict[str, Any]:
    """端到端治理链路仿真：返回丢失率/画像误差/延迟分位/场交换校验等判据数字。"""
    req = req or E2ERequest()
    cfg = E2EConfig(num_agents=req.num_agents, ticks=req.ticks, drop_rate=req.drop_rate,
                    exchange_every=req.exchange_every, mission_every=req.mission_every,
                    mission_length=req.mission_length, seed=req.seed)
    try:
        result = run_governance_chain(cfg).as_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"INVALID_CONFIG: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500,
                            detail=f"E2E_ERROR: {type(exc).__name__}: {exc}") from exc
    return {"status": "ok", "run": result}


@router.get("/report")
def get_report() -> Dict[str, Any]:
    """最近一次成熟化报告（脚本 ``scripts/run_governance_maturity.py`` 生成）。"""
    path = _ROOT / REPORT_REL
    if not path.is_file():
        raise HTTPException(status_code=404,
                            detail=f"REPORT_NOT_FOUND: {REPORT_REL.as_posix()} 尚未生成，"
                                   f"请先运行 scripts/run_governance_maturity.py")
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    return {"status": "ok", "path": REPORT_REL.as_posix(), "report": data}