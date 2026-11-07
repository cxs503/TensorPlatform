#!/usr/bin/env python3
"""失败终态 → postmortem 自动收割接线（best-effort，绝不阻断结算）。

调用点：``backend/api/tensor_mission_lifecycle.py`` 的 write-off（终态结算钩子）
在 ``db.commit()`` 之后调用 :func:`auto_harvest_after_failure`。契约：

* **幂等**：同一 mission 已有 postmortem 直接跳过（UNIQUE(mission_id)）；
* **不阻断**：任何异常都吞掉并回写一条 ``skipped`` 原因，结算事务已提交，
  收割失败不得让 mission 结算 422/500；
* **可关断**：``TP_POSTMORTEM_AUTOHARVEST=0`` 时完全不动作（平台冻结开关）；
* **不越权**：收割主体是平台服务（``harvester:writeoff-hook@1.0``），定稿主体
  是独立挑战者（``challenger:postmortem-rulecheck@1.0``），二者不同体。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from tools.harvest_failure_postmortems import (  # noqa: E402
    CHALLENGER_ID,
    Repo,
    harvest,
)

log = logging.getLogger(__name__)

HOOK_HARVESTER = "harvester:writeoff-hook@1.0"


def autoharvest_enabled() -> bool:
    return os.getenv("TP_POSTMORTEM_AUTOHARVEST", "1") != "0"


def auto_harvest_after_failure(db: Session, mission_id: int) -> dict[str, Any]:
    """对单个 failed mission 做一次收割；返回可留痕的回执（不抛异常）。"""
    if not autoharvest_enabled():
        return {"status": "disabled", "mission_id": mission_id}
    try:
        engine = db.get_bind()
        repo = Repo(engine)
        repo.ensure_schema()
        existing = repo.existing_postmortem(int(mission_id))
        if existing:
            return {
                "status": "skipped",
                "mission_id": mission_id,
                "reason": "postmortem 已存在",
                "postmortem_id": existing["postmortem_id"],
                "postmortem_status": existing["status"],
            }
        # 复用主线收割器：这里只针对单条 mission（days 内 limit=全部再用 id 过滤）
        report = harvest(
            repo,
            days=int(os.getenv("TP_POSTMORTEM_HARVEST_DAYS", "7")),
            limit=1,
            apply=True,
            harvester=HOOK_HARVESTER,
            challenger=CHALLENGER_ID,
            mission_ids=[int(mission_id)],
        )
        for draft in report.drafts:
            if int(draft["mission_id"]) == int(mission_id):
                return {
                    "status": draft["status"],
                    "mission_id": mission_id,
                    "postmortem_id": draft["postmortem_id"],
                    "failure_stage": draft["failure_stage"],
                    "error_class": draft["error_class"],
                    "harvest": {
                        "confirmed": report.confirmed,
                        "disputed": report.disputed,
                        "draft_pending": report.draft_pending,
                        "withheld": report.withheld,
                    },
                }
        return {"status": "withheld", "mission_id": mission_id,
                "reason": "证据不足或非 failed 终态"}
    except Exception as exc:  # noqa: BLE001 - 结算钩子必须不失败
        log.warning("postmortem autoharvest failed for mission %s: %s", mission_id, exc)
        return {"status": "error", "mission_id": mission_id, "error": str(exc)[:300]}


def record_hook_receipt(db: Session, mission_id: int, receipt: dict[str, Any]) -> None:
    """把收割回执写进 mission_lifecycle_log（审计链，失败不致命）。"""
    try:
        db.execute(
            text(
                """
                INSERT INTO mission_lifecycle_log (mission_id, action, actor_user_id, detail)
                VALUES (:mid, 'postmortem_autoharvest', NULL, CAST(:detail AS jsonb))
                """
            ),
            {"mid": mission_id, "detail": json.dumps(receipt, ensure_ascii=False)},
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        log.info("postmortem hook receipt not logged: %s", exc)


__all__ = [
    "auto_harvest_after_failure",
    "autoharvest_enabled",
    "record_hook_receipt",
    "HOOK_HARVESTER",
]