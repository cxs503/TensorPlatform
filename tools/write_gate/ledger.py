#!/usr/bin/env python3
"""事件记账：每次 BLOCK 判决写一条不可变记录到事件账本。

设计要点
--------
* 账本为 JSONL（每行一条 JSON），追加写入，不修改历史。
* 每条记录含：时间戳（ISO-8601 UTC）、verdict digest、违规摘要、策略引用。
* 账本路径可配置（默认 ``runs/write_gate/events.jsonl``）。
* 写入失败不阻塞门判决（门已 FAIL，记账是辅助审计）。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .model import Verdict


def _utc_now_iso() -> str:
    """UTC 时间戳（ISO-8601，秒精度）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_event(verdict: Verdict, *, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """从判决构建一条事件记录。

    Parameters
    ----------
    verdict : Verdict
        门判决结果。
    context : dict, optional
        附加上下文（如 mission_id, branch, commit_sha 等）。
    """
    payload = verdict.to_json()
    event: Dict[str, Any] = {
        "timestamp": _utc_now_iso(),
        "verdict": payload["verdict"],
        "decision": payload["decision"],
        "digest": payload["digest"],
        "changes": payload["totals"]["changes"],
        "blocked": payload["totals"]["blocked"],
        "by_category": payload["totals"]["by_category"],
        "policy_sha256": payload["policy"]["sha256"],
        "violations_summary": [
            {"category": v["category"], "code": v["code"], "path": v["path"],
             "rule_id": v["rule_id"]}
            for v in payload["violations"][:20]  # 截断避免账本膨胀
        ],
    }
    if context:
        event["context"] = context
    return event


def append_event(event: Dict[str, Any], ledger_path: str | Path) -> bool:
    """追加一条事件到账本。返回是否成功。

    失败（权限/磁盘）不抛异常，只返回 False——门判决已定，记账是辅助。
    """
    try:
        path = Path(ledger_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
        return True
    except (OSError, IOError):
        return False


def record_verdict(verdict: Verdict, ledger_path: str | Path, *,
                   context: Optional[Dict[str, Any]] = None) -> bool:
    """便捷函数：构建事件并追加到账本。"""
    if verdict.verdict != "FAIL":
        return True  # 只记 BLOCK 事件
    event = build_event(verdict, context=context)
    return append_event(event, ledger_path)


def read_ledger(ledger_path: str | Path) -> List[Dict[str, Any]]:
    """读回全部事件（测试/审计用）。"""
    path = Path(ledger_path)
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events
