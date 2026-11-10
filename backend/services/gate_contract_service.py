"""dev_acceptance 强制前门服务（零领域语义）。

平台侧只做共性机制：把 ``tools/gate_contract``（判据契约 + 复算 + 台账）
挂到任务验收链上。门目录/登记表/台账路径全部来自
``config/gate_contract/acceptance_policy.json``；本文件不含任何域词条/域阈值。

对外只暴露两个函数：

* :func:`precheck` —— 审批器自动批前跑的前门（返回 decision/grade/补证单）；
* :func:`describe_evidence` —— 供 API 展示「为什么不能自动批」。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]

POLICY_PATH = _REPO_ROOT / "config" / "gate_contract" / "acceptance_policy.json"


def _api():
    root = str(_REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from tools.gate_contract.acceptance import (evaluate_acceptance,  # noqa: F401
                                                load_policy, precheck_mission)
    return load_policy, precheck_mission


def load_policy(policy_path: str | Path | None = None) -> dict:
    load, _ = _api()
    return load(repo_root=_REPO_ROOT, path=policy_path or POLICY_PATH)


def precheck(mission_id: int, policy: dict | None = None) -> dict | None:
    """返回 ``None`` 表示策略未启用（保持旧行为）。异常不外溢：审批流不应被门拖死。"""
    try:
        _, precheck_mission = _api()
        return precheck_mission(int(mission_id), policy=policy)
    except Exception as exc:  # pragma: no cover - 防御性
        return {"decision": "error", "grade": "WITHHELD", "approver_verdict": "escalate",
                "note": f"gate_contract: precheck failed: {type(exc).__name__}",
                "supplement_task": None, "error": type(exc).__name__}


def describe_evidence(mission_id: int, policy: dict | None = None) -> dict[str, Any]:
    res = precheck(mission_id, policy=policy)
    if res is None:
        return {"enabled": False}
    result = res.get("result") or {}
    return {
        "enabled": True,
        "mode": res.get("mode"),
        "decision": res.get("decision"),
        "grade": res.get("grade"),
        "note": res.get("note"),
        "verdict_digest": result.get("verdict_digest"),
        "gate_sha256": result.get("gate_sha256"),
        "criteria": [{k: c.get(k) for k in ("id", "status", "code", "unit", "threshold")}
                     for c in result.get("criteria", [])],
        "supplement_task": res.get("supplement_task"),
    }
