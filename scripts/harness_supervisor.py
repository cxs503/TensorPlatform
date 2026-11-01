#!/usr/bin/env python3
"""Harness v3 — supervisor：拉起席位，允许它在任意检查点被 kill -9，然后按 journal 续跑。

这是"进程退出 ≠ 成果必须丢失"的执行侧证明：
  ① 拉起 seat；② 轮询 journal 直到进度 ≥ 阈值；③ 对 seat **真发 SIGKILL**；
  ④ 检查 journal 完整性；⑤ 用 --resume 重领同一单；⑥ 汇总续跑步数。
预算帽：续跑**不追加**预算——budget 只在第一轮下发一次，journal.calls_used 累计。

用法:
  python3 scripts/harness_supervisor.py --mission-id 7 --workdir /tmp/w \\
      --journal-root /tmp/j --steps 6 --budget-calls 100 \\
      --kill-at-progress 0.34 --max-attempts 3 [--journal-off]

输出（stdout）: 单行 JSON 汇总；同时写 <workdir>/supervisor_summary.json
退出码: 0=最终步骤全部完成 / 2=未完成（含预算耗尽/续跑失败）
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import signal
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import harness_journal as HJ  # noqa: E402


def journal_progress(root, mid) -> float:
    st = HJ.read(root, mid)
    return HJ.merge(st["entries"])["progress"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mission-id", type=int, required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--journal-root", default=None)
    ap.add_argument("--steps", type=int, default=6)
    ap.add_argument("--step-seconds", type=float, default=0.15)
    ap.add_argument("--budget-calls", type=int, default=0)
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--kill-at-progress", type=float, default=0.0,
                    help=">0 时在首轮进度达该比例后对 seat 真发 SIGKILL")
    ap.add_argument("--kill-after-seconds", type=float, default=0.0,
                    help=">0 时首轮开始后固定延迟即 SIGKILL（对照组的无日志场景用）")
    ap.add_argument("--journal-off", action="store_true")
    ap.add_argument("--force-resume", action="store_true",
                    help="把本轮当续跑（跨 supervisor 进程：平台 reclaim 后新席位重领同一单）")
    ap.add_argument("--resume-probe", action="store_true",
                    help="门禁红证：即使 journal 关闭也要求出现续跑（用于证明门可红）")
    a = ap.parse_args(argv)

    mid, workdir = a.mission_id, pathlib.Path(a.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    events = []
    killed = 0
    final_rc = None
    for attempt in range(1, a.max_attempts + 1):
        cmd = [sys.executable, str(HERE / "harness_seat.py"),
               "--mission-id", str(mid), "--workdir", str(workdir),
               "--steps", str(a.steps), "--step-seconds", str(a.step_seconds),
               "--budget-calls", str(a.budget_calls), "--attempt", str(attempt)]
        if a.journal_root:
            cmd += ["--journal-root", str(a.journal_root)]
        if a.journal_off:
            cmd += ["--journal-off"]
        resume_this = attempt > 1 or a.force_resume
        if resume_this:
            cmd += ["--resume"]
        child = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True)
        ev = {"attempt": attempt, "pid": child.pid, "resume": resume_this}
        # 首轮按需 kill -9
        if a.kill_at_progress > 0 and attempt == 1 and not a.journal_off:
            deadline = time.time() + max(10.0, a.steps * a.step_seconds * 8)
            while time.time() < deadline and child.poll() is None:
                if journal_progress(a.journal_root, mid) >= a.kill_at_progress:
                    os.kill(child.pid, signal.SIGKILL)
                    killed += 1
                    ev["killed_at_progress"] = round(journal_progress(a.journal_root, mid), 3)
                    break
                time.sleep(0.02)
        if a.kill_after_seconds > 0 and attempt == 1 and a.kill_at_progress <= 0:
            deadline = time.time() + a.kill_after_seconds
            while time.time() < deadline and child.poll() is None:
                time.sleep(0.01)
            if child.poll() is None:
                os.kill(child.pid, signal.SIGKILL)
                killed += 1
                ev["killed_after_seconds"] = a.kill_after_seconds
        out, _ = child.communicate()
        rc = child.returncode
        ev["rc"] = rc
        ev["killed"] = rc == -signal.SIGKILL
        ev["stdout_tail"] = (out or "")[-400:]
        st = HJ.read(a.journal_root, mid)
        m = HJ.merge(st["entries"])
        ev["journal"] = {"valid_records": len(st["entries"]),
                         "corrupt_records": len(st["corrupt"]),
                         "degraded": st["degraded"], "reason": st["reason"],
                         "progress": m["progress"], "status": m["status"],
                         "calls_used": m["calls_used"]}
        if a.journal_off:
            ev["journal"] = {"valid_records": 0, "corrupt_records": 0, "degraded": True,
                             "reason": "journal_disabled", "progress": 0.0,
                             "status": "", "calls_used": 0}
        events.append(ev)
        final_rc = rc
        if rc == 0:
            break
        if rc == 3:                                     # 预算耗尽：不追加预算、不重启
            break
        time.sleep(0.05)

    st = HJ.read(a.journal_root, mid)
    m = HJ.merge(st["entries"])
    res = json.loads((workdir / "seat_result.json").read_text()) \
        if (workdir / "seat_result.json").exists() else {}
    summary = {
        "mission_id": mid,
        "journal_on": not a.journal_off,
        "attempts": len(events),
        "kills": killed,
        "max_attempts": a.max_attempts,
        "final_rc": final_rc,
        "status": res.get("status") or m.get("status") or "unknown",
        "resumed_steps": res.get("resumed", []),
        "resumed_step_count": len(res.get("resumed", []) or []),
        "steps_total": a.steps,
        "done_steps": res.get("done", []),
        "done_step_count": len(res.get("done", []) or []),
        "calls_used": m.get("calls_used") if not a.journal_off else res.get("calls_used", 0),
        "budget_calls": a.budget_calls,
        "journal_valid_records": len(st["entries"]),
        "journal_corrupt_records": len(st["corrupt"]),
        "journal_degraded": st["degraded"],
        "journal_reason": st["reason"],
        "events": events,
        "resume_probe": a.resume_probe,
        "force_resume": a.force_resume,
    }
    (workdir / "supervisor_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if (summary["status"] == "steps_done") else 2


if __name__ == "__main__":
    sys.exit(main())