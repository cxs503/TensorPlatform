"""TensorBench 评价回路的 HTTP 面（tb-eval-loop ①②③ 的入口）。

四个端点：

* ``POST /tensor/bench/evaluations``        —— ① 递交：把该 mission 绑定的基准规格压进集群队列；
* ``POST /tensor/bench/results``            —— ② 取证：集群作业原始结果 → ``tensorbench_results``；
* ``GET  /tensor/bench/results``            —— 实证行读面（按 mission）；
* ``GET  /tensor/bench/l4-verdict/{mid}``   —— ④ L4 判据裁定（无行即 claimed_not_proven）。

边界（不越权）：本模块不跑基准、不改任务状态、不写别的仓；数字全部由
``tools.tensorbench_eval`` 从原始字节复算。规格（benchmark/workload/ref/容差）
由调用方注入——平台层零领域语义。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth
from api.domain.tensor_common import resolved_org
from api.tenant_access import is_admin
from database.config import get_db

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(_ROOT))

from tools.maturity_gate import benchmark_source  # noqa: E402
from tools.tensorbench_eval import contract, store  # noqa: E402

router = APIRouter(prefix="/tensor/bench", tags=["tensorbench-eval"])


def _mission(db: Session, mission_id: int, user: dict) -> dict:
    row = db.execute(
        text("SELECT id, organization_id, goal, repo, result FROM tensor_dev_missions "
             "WHERE id = :mid"), {"mid": int(mission_id)}).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="mission not found")
    org_id = resolved_org(None, user, db)
    if org_id is not None and int(row["organization_id"]) != int(org_id) and not is_admin(user):
        raise HTTPException(status_code=404, detail="mission not found")
    return dict(row)


class SubmitRequest(BaseModel):
    mission_id: int
    spec: Dict[str, Any] = Field(..., description="基准规格工件（benchmark/workload/evaluation）")
    ref: str = "main"
    timeout: int = Field(1800, gt=0, le=7200)


class IngestRequest(BaseModel):
    mission_id: int
    cluster_job_id: int
    spec: Dict[str, Any]
    result: Dict[str, Any]
    workload_stdout: str


@router.post("/evaluations", dependencies=[Depends(require_auth)])
def submit_evaluation(body: SubmitRequest, db: Session = Depends(get_db),
                      user: dict = Depends(require_auth)):
    mission = _mission(db, body.mission_id, user)
    try:
        return store.submit_evaluation(db, mission, spec=body.spec, ref=body.ref,
                                       timeout=body.timeout, submitter=user.get("username", "agent"))
    except contract.ContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/results", dependencies=[Depends(require_auth)])
def ingest_result(body: IngestRequest, db: Session = Depends(get_db),
                  user: dict = Depends(require_auth)):
    mission = _mission(db, body.mission_id, user)
    try:
        return store.ingest_result(db, mission_id=body.mission_id,
                                   cluster_job_id=body.cluster_job_id, spec=body.spec,
                                   result=body.result, workload_stdout=body.workload_stdout,
                                   organization_id=mission.get("organization_id"),
                                   source="cluster_job")
    except contract.ContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/results", dependencies=[Depends(require_auth)])
def list_results(mission_id: int, db: Session = Depends(get_db), user: dict = Depends(require_auth)):
    _mission(db, mission_id, user)
    rows: List[dict] = store.rows_for_mission(db, mission_id)
    return {"mission_id": mission_id, "total": len(rows), "rows": rows}


@router.get("/l4-verdict/{mission_id}", dependencies=[Depends(require_auth)])
def l4_verdict(mission_id: int, db: Session = Depends(get_db), user: dict = Depends(require_auth)):
    mission = _mission(db, mission_id, user)
    rows = store.rows_for_mission(db, mission_id)
    return benchmark_source.verdict(rows, claimed_level=store.declared_level(mission))
