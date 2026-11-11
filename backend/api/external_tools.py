"""外部工具类接入面的 HTTP 端点（平台共性机制，零领域语义）。

三个端点：

* ``GET  /api/v1/external-tools/classes``  —— 规格里声明的工具类 + 各自最近一次观测状态；
* ``GET  /api/v1/external-tools/coverage`` —— KPI 口径：REAL 类数 / 目标 / 缺口 / 裁定；
* ``GET  /api/v1/external-tools/benchmark`` —— ref-vs-got 对照表（只读已落盘台账）；
* ``POST /api/v1/external-tools/probe``    —— 立刻跑探针+冒烟（可指定子集），可选落盘。

设计同 ``api/maturity_gate.py``：**声明 ≠ 观测**，未落盘台账时 coverage 直接报错而不是
猜一个数字；工具类清单与阈值全部来自规格文件（``data/external_tools/classes.json``），
本模块不含任何学科词条或域阈值。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:  # pragma: no cover - 导入路径兜底
    sys.path.insert(0, str(_ROOT))

from services import external_tools_service as svc  # noqa: E402

router = APIRouter(prefix="/external-tools", tags=["外部工具接入"])


class ProbeRequest(BaseModel):
    """请求体：可只探测子集；``persist`` 才写台账（默认不写）。"""

    tool_ids: Optional[List[str]] = Field(
        None, description="只探测这些 tool_id；缺省=全量")
    persist: bool = Field(False, description="true 时才落盘 artifacts/external_tools/latest.json")
    repo: Optional[str] = Field(None, description="VCS 类探针使用的仓库路径，缺省=平台仓")


@router.get("/classes")
def list_classes() -> Dict[str, Any]:
    """声明清单 + 最近观测状态（last_status=UNOBSERVED 表示还没跑过）。"""

    try:
        return svc.catalog()
    except svc.ExternalToolsError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/coverage")
def coverage() -> Dict[str, Any]:
    """KPI 口径的覆盖度（只读已落盘台账，数字可复算）。"""

    try:
        return svc.coverage()
    except svc.ExternalToolsError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/benchmark")
def benchmark() -> Dict[str, Any]:
    """ref-vs-got 对照表：每个声明了参考值的指标误差 + 判据（无台账→503）。"""

    try:
        return svc.benchmark()
    except svc.ExternalToolsError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/probe")
def probe(req: ProbeRequest) -> Dict[str, Any]:
    """现场跑探针+冒烟调用+不变式，返回证据台账（可选落盘）。"""

    try:
        return svc.probe(req.tool_ids, persist=req.persist, repo=req.repo)
    except svc.ExternalToolsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc