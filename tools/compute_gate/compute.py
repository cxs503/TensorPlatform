#!/usr/bin/env python3
"""compute_gate.compute —— run_compute 工具面（递交语义，agent 唯一计算入口）。

取代 run_cluster v2：
  action=submit  → repo+cmd 或 cmd_tpl+shards + cores/timeout + stage_dir
                   **立即返回** {job_id, queue_position, state}；节点由调度器指派。
  action=status  → 作业状态（排队位次/在跑节点/终态）
  action=fetch   → 轮询取回工件（带 wait：可等到终态再取）
  action=nodes   → 节点健康
  action=usage   → local vs queued 用量回执（如实记账）
每次回执都附 receipt（local vs queued 用量），满足「回执里如实记 local vs queued 用量」。
"""
import time

from . import ledger as ledger_mod
from . import queue as queue_mod


def _receipt(cfg):
    try:
        return ledger_mod.Ledger(cfg).usage()
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, e)}


def run_compute(cfg, action="submit", repo=None, cmd=None, cmd_tpl=None, shards=None,
                cores=None, timeout=7200, ref="main", stage_dir=None, job_id=None,
                mission_id=None, wait_seconds=0, poll=20):
    action = (action or "submit").lower()
    if action in ("run", "shard", "submit"):
        r = queue_mod.submit(cfg, repo=repo, cmd=cmd, cmd_tpl=cmd_tpl, shards=shards,
                             ref=ref, cores=cores, timeout=timeout, stage_dir=stage_dir,
                             mid=mission_id, submitter="agent-run_compute")
        ledger_mod.Ledger(cfg).record(
            "compute_submitted", mission_id=mission_id, cmd=(cmd or cmd_tpl or ""), jid=r["jid"],
            note="run_compute 递交", detail={"repo": repo, "cores": cores, "stage_dir": stage_dir,
                                            "queued_ahead": r.get("queued_ahead")})
        return {"ok": True, **r, "receipt": _receipt(cfg)}

    if action == "status":
        out = queue_mod.ps(cfg, job_id)
        return {"ok": True, "raw": out, "receipt": _receipt(cfg)}

    if action == "fetch":
        if not job_id:
            return {"ok": False, "error": "fetch 需要 job_id（cjN）", "receipt": _receipt(cfg)}
        waited = 0
        state_txt = queue_mod.ps(cfg, job_id)
        while wait_seconds and waited < wait_seconds and ("running" in state_txt or "queued" in state_txt):
            time.sleep(min(poll, max(1, wait_seconds - waited)))
            waited += poll
            state_txt = queue_mod.ps(cfg, job_id)
        out = queue_mod.fetch(cfg, job_id)
        return {"ok": True, "waited_seconds": waited, "state": state_txt.strip()[-600:],
                "raw": out, "receipt": _receipt(cfg)}

    if action == "nodes":
        return {"ok": True, "raw": queue_mod.nodes(cfg), "receipt": _receipt(cfg)}

    if action == "usage":
        return {"ok": True, "receipt": _receipt(cfg)}

    if action == "kill":
        if not job_id:
            return {"ok": False, "error": "kill 需要 job_id"}
        return {"ok": True, "raw": queue_mod.kill(cfg, job_id), "receipt": _receipt(cfg)}

    return {"ok": False, "error": "action 只允许 submit/status/fetch/nodes/usage/kill"}


def dispatch_tool(tool_hint_name, cfg, **kw):
    """tp_worker 侧的统一入口：把工具名映射到 action（run_compute / run_cluster 兼容名）。"""
    action = kw.pop("action", "submit")
    return run_compute(cfg, action=action, **kw)