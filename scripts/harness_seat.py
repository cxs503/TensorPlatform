#!/usr/bin/env python3
"""Harness v3 — 席位（seat）参考实现：每个检查点 append 一条 journal 记录。

这不是模拟器玩具，而是 tp_worker.run_mission 的**最小同构**：
  - 把"一次工具调用"折成一个可验证的工作步骤（写证据文件 + 落 journal）；
  - 进程随时可能被 kill -9，重启后按 journal 续跑，而不是从零；
  - 证据复核：journal 说做过但证据文件不在 → 该步重做并记 deviation；
  - 预算帽跨续跑累计（journal.calls_used 唯一权威），用尽即停，绝不追加。

用法（一般由 harness_supervisor.py 拉起）:
  python3 scripts/harness_seat.py --mission-id 7 --journal-root /tmp/j \\
      --workdir /tmp/w --steps 6 --budget-calls 100 --attempt 1 [--resume] \\
      [--journal-off] [--die-after-step K]

退出码: 0=步骤全部完成 / 3=预算耗尽（诚实收尾）/ 4=journal 缺失-降级从零但完成
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import signal
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import harness_journal as HJ  # noqa: E402


def evidence_path(workdir: pathlib.Path, sid: str) -> pathlib.Path:
    return workdir / "evidence" / f"{sid}.json"


def evidence_ok(workdir: pathlib.Path, sid: str) -> bool:
    p = evidence_path(workdir, sid)
    if not p.exists():
        return False
    try:
        d = json.loads(p.read_text())
    except Exception:
        return False
    return d.get("step") == sid


def make_steps(n: int, mission_id: int):
    return [{"id": f"s{i}", "what": f"mission#{mission_id} 第 {i}/{n} 步：产出证据 evidence/s{i}.json"}
            for i in range(1, n + 1)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mission-id", type=int, required=True)
    ap.add_argument("--journal-root", default=None)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--steps", type=int, default=6)
    ap.add_argument("--step-seconds", type=float, default=0.15)
    ap.add_argument("--budget-calls", type=int, default=0, help="0=不限")
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--journal-off", action="store_true", help="对照组：完全不用 journal")
    ap.add_argument("--die-after-step", type=int, default=0, help="自证崩溃点（测试用）")
    ap.add_argument("--result-json", default="")
    a = ap.parse_args(argv)

    mid, workdir = a.mission_id, pathlib.Path(a.workdir)
    (workdir / "evidence").mkdir(parents=True, exist_ok=True)
    steps = make_steps(a.steps, mid)
    ids = [s["id"] for s in steps]
    logpath = workdir / "seat_log.jsonl"

    def slog(obj):
        with open(logpath, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%H:%M:%S"), **obj},
                               ensure_ascii=False) + "\n")

    journal_on = not a.journal_off
    state = {"goal": f"harness v3 席位续跑演示 mission#{mid}",
             "plan": [s["what"] for s in steps], "next_intent": "", "calls_used": 0}
    resumed, degraded, brief_text = [], False, ""

    if journal_on and a.resume:
        b = HJ.resume_brief(a.journal_root, mid,
                            verified_evidence=lambda s: evidence_ok(workdir, s.get("id", "")))
        brief_text, degraded = b["text"], b["degraded"]
        resumed = list(b["resumed_steps"])
        state["calls_used"] = b["calls_used"]
        slog({"event": "resume_brief_injected", "mode": b["mode"],
              "resumed_steps": resumed, "evidence_missing": b["evidence_missing_steps"],
              "degraded": degraded, "calls_used": state["calls_used"], "text": brief_text})
        print(brief_text, flush=True)

    if journal_on:
        HJ.write_meta(a.journal_root, mid, steps_total=a.steps,
                      budget={"max_tool_calls": a.budget_calls},
                      attempts=a.attempt)
        HJ.append(a.journal_root, mid, kind="attempt_start", agent_id="harness-seat",
                  attempt=a.attempt, goal=state["goal"], plan=state["plan"],
                  steps_total=a.steps, calls_used=state["calls_used"],
                  resumed_steps=resumed, degraded=degraded,
                  dirty_tree=HJ.tree_snapshot(workdir),
                  next_intent="按 plan 顺序执行未完成步骤")
    else:
        slog({"event": "journal_disabled", "attempt": a.attempt})

    done = {s: {"id": s, "what": next(x["what"] for x in steps),
                "evidence": str(evidence_path(workdir, s)), "attempt": a.attempt}
            for s in resumed}
    # 复核：resumed 列表已做磁盘核对；若调用方 --resume 但日志声称完成而证据缺失，
    # resume_brief 已把该步放进 evidence_missing，这里只做记录（不伪造）。
    deviations = []
    skipped = len(done)

    def checkpoint(kind="checkpoint", status="running", intent=""):
        HJ.append(a.journal_root, mid, kind=kind, agent_id="harness-seat",
                  attempt=a.attempt, goal=state["goal"], plan=state["plan"],
                  done_steps=list(done.values()), verified_facts=[
                      f"{sid} 证据文件已落盘 {evidence_path(workdir, sid).name}"
                      for sid in sorted(done)],
                  unverified_assumptions=["未跑集成测试前，步骤正确性只由证据文件存在性证明"],
                  calls_used=state["calls_used"], steps_total=a.steps,
                  progress=round(len(done) / a.steps, 4), status=status,
                  dirty_tree=HJ.tree_snapshot(workdir),
                  deviations=deviations, next_intent=intent)

    for i, s in enumerate(steps, 1):
        sid = s["id"]
        if sid in done and evidence_ok(workdir, sid):
            slog({"event": "skip_done_step", "step": sid})
            continue
        if a.budget_calls and state["calls_used"] >= a.budget_calls:
            HJ.append(a.journal_root, mid, kind="attempt_end", agent_id="harness-seat",
                      attempt=a.attempt, status="budget_exhausted",
                      calls_used=state["calls_used"], steps_total=a.steps,
                      progress=round(len(done) / a.steps, 4),
                      done_steps=list(done.values()), deviations=deviations,
                      next_intent="预算用尽：不得追加预算，交人工决定是否补派")
            slog({"event": "budget_exhausted", "calls_used": state["calls_used"],
                  "cap": a.budget_calls})
            _write_result(a, workdir, dict(status="budget_exhausted", resumed=resumed,
                                           done=sorted(done), skipped=skipped,
                                           calls_used=state["calls_used"], degraded=degraded,
                                           attempt=a.attempt))
            return 3
        time.sleep(a.step_seconds)                     # 模拟真实工具调用耗时
        p = evidence_path(workdir, sid)
        p.write_text(json.dumps({"step": sid, "attempt": a.attempt,
                                 "ts": time.strftime("%H:%M:%S"),
                                 "what": s["what"]}, ensure_ascii=False))
        state["calls_used"] += 1
        done[sid] = {"id": sid, "what": s["what"], "evidence": str(p), "attempt": a.attempt}
        nxt = f"下一步 {steps[i]['id']}" if i < len(steps) else "全部步骤完成，收尾"
        if journal_on:
            checkpoint(intent=nxt)
        slog({"event": "step_done", "step": sid, "progress": round(len(done) / a.steps, 3),
              "calls_used": state["calls_used"]})
        if a.die_after_step and i == a.die_after_step:
            slog({"event": "self_sigkill", "step": sid})
            os.kill(os.getpid(), signal.SIGKILL)        # 真·kill -9，不给清理机会

    if journal_on:
        HJ.append(a.journal_root, mid, kind="attempt_end", agent_id="harness-seat",
                  attempt=a.attempt, status="steps_done", steps_total=a.steps,
                  done_steps=list(done.values()), calls_used=state["calls_used"],
                  progress=round(len(done) / a.steps, 4), deviations=deviations,
                  next_intent="步骤已全部完成，可调用 finish")
    raft = dict(status="steps_done", resumed=resumed, done=sorted(done), skipped=skipped,
                calls_used=state["calls_used"], degraded=degraded, attempt=a.attempt)
    _write_result(a, workdir, raft)
    slog({"event": "attempt_end", "status": "steps_done", **raft})
    return 4 if degraded else 0


def _write_result(a, workdir: pathlib.Path, res: dict):
    out = pathlib.Path(a.result_json) if a.result_json else workdir / "seat_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())