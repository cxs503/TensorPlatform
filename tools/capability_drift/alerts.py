"""告警：把退化判据结果封成平台事件，落 jsonl 并可选投递到平台 API。

事件结构（下游订阅方只需这一份）::

    {"event": "capability.drift.detected", "severity": "red",
     "health": {...}, "drifted": [{"capability","metric","baseline","observed",
     "rel_err","threshold","suspected_commits":[{commit,subject,...}]}],
     "run": {...}}

投递失败绝不抛异常（离线/CI 无网络是常态），只把 ``delivered/error`` 记进返回字典，
事件本体仍落盘——告警不因传输层故障丢失。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .detector import DriftReport, RED

EVENT_RED = "capability.drift.detected"
EVENT_GREEN = "capability.drift.clear"


def build_event(
    report: DriftReport,
    suspicions: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
    *,
    run_meta: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    suspicions = suspicions or {}
    drifted: List[Dict[str, Any]] = []
    for v in report.red:
        for c in v.comparisons:
            if c.status != "red":
                continue
            drifted.append(
                {
                    "capability": v.capability,
                    "solver": v.solver,
                    "metric": c.metric,
                    "baseline": c.baseline,
                    "observed": c.observed,
                    "rel_err": c.rel_err,
                    "threshold": c.threshold,
                    "abs_err": c.abs_err,
                    "suspected_commits": list(suspicions.get(v.capability, [])),
                }
            )
    severity = "red" if drifted else ("yellow" if report.health()["status"] == "yellow" else "green")
    return {
        "event": EVENT_RED if drifted else EVENT_GREEN,
        "severity": severity,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "health": report.health(),
        "scope": list(report.scope),
        "drifted": drifted,
        "drifted_capabilities": [d["capability"] for d in drifted],
        "suspected_commits": sorted(
            {s["commit"] for d in drifted for s in d["suspected_commits"] if s.get("commit")}
        ),
        "run": dict(run_meta or {}),
    }


def emit(event: Mapping[str, Any], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")
    return p


def post_event(
    event: Mapping[str, Any],
    *,
    base_url: Optional[str] = None,
    token: Optional[str] = None,
    path: str = "/tensor/events",
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """尽力投递平台事件；返回 {delivered, http_status, error}，永不抛异常。"""
    base_url = base_url or os.environ.get("TENSOR_API_BASE", "")
    token = token or os.environ.get("TENSOR_API_TOKEN", "")
    if not base_url:
        return {"delivered": False, "http_status": None, "error": "no TENSOR_API_BASE"}
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    data = json.dumps(event, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 受控内网
            body = resp.read(400).decode("utf-8", "replace")
            return {"delivered": True, "http_status": resp.status, "error": "", "body": body}
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        return {"delivered": False, "http_status": None, "error": str(exc)}