#!/usr/bin/env python3
"""Harness v3 断点续跑机器门禁的 pytest 形态（mission m1231）。

与 scripts/run_harness_v3_gate.py 共用同一套判据，但拆成独立断言，
便于 CI 单点定位失败原因（门禁任何一条不满足都会红）。
"""
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SCRIPTS = ROOT / "scripts"


def _sup(tmp_path, name, **kw):
    work = tmp_path / name / "work"
    jr = tmp_path / name / "journal"
    work.mkdir(parents=True)
    jr.mkdir(parents=True)
    cmd = [sys.executable, str(SCRIPTS / "harness_supervisor.py"),
           "--workdir", str(work), "--journal-root", str(jr)]
    for k, v in kw.items():
        flag = "--" + k.replace("_", "-")
        cmd += [flag] if v is True else [flag, str(v)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    line = (p.stdout or "").strip().splitlines()
    assert line, f"supervisor 无输出: {p.stderr[-500:]}"
    d = json.loads(line[-1])
    d["_work"], d["_journal"] = work, jr
    return d


def _seat_log(work):
    p = work / "seat_log.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def test_kill9_then_resume_recovers_completed_steps(tmp_path):
    s = _sup(tmp_path, "A", mission_id=201, steps=6, step_seconds=0.08,
             budget_calls=100, max_attempts=3, kill_at_progress=0.34)
    assert s["kills"] == 1, "kill -9 必须真发"
    killed_at = [e["killed_at_progress"] for e in s["events"] if e.get("killed_at_progress")]
    assert killed_at and killed_at[0] >= 1 / 3, "击杀点必须在 ≥1/3 进度"
    assert s["attempts"] >= 2, "supervisor 必须重领同一单"
    assert s["resumed_step_count"] >= 1, "必须从 journal 恢复 ≥1 个已完步骤"
    assert s["done_step_count"] == 6 and s["status"] == "steps_done"
    assert s["journal_corrupt_records"] == 0 and not s["journal_degraded"]
    assert any(e.get("event") == "resume_brief_injected" for e in _seat_log(s["_work"]))


def test_resumed_steps_are_reused_not_redone(tmp_path):
    """续跑不是'重做一遍'：被恢复步骤的证据文件保留 attempt=1，只有新步骤是 attempt=2。"""
    s = _sup(tmp_path, "A2", mission_id=202, steps=6, step_seconds=0.08,
             budget_calls=100, max_attempts=3, kill_at_progress=0.34)
    resumed = set(s["resumed_steps"])
    assert resumed
    ev = s["_work"] / "evidence"
    attempts = {p.stem: json.loads(p.read_text())["attempt"] for p in ev.glob("s*.json")}
    assert all(attempts[sid] == 1 for sid in resumed), "恢复的步骤不应被重做"
    assert any(attempts[sid] == 2 for sid in attempts if sid not in resumed)


def test_without_journal_restarts_from_zero(tmp_path):
    s = _sup(tmp_path, "B", mission_id=203, steps=6, step_seconds=0.08,
             budget_calls=100, max_attempts=2, journal_off=True, kill_after_seconds=0.25)
    assert s["kills"] == 1
    assert s["resumed_step_count"] == 0, "无 journal 时必须从零（这正是 journal 的净贡献）"
    assert s["done_step_count"] == 6


def test_total_corruption_degrades_honestly(tmp_path):
    """日志被彻底写坏（进度不可知）：降级从零、如实记录，不得伪造续跑。"""
    s = _sup(tmp_path, "C1a", mission_id=204, steps=6, step_seconds=0.05,
             budget_calls=100, max_attempts=1, kill_at_progress=0.5)
    jp = pathlib.Path(s["_journal"]) / "missions" / "m204" / "journal.jsonl"
    jp.write_bytes(b"\x00\xff" * 256)
    cmd = [sys.executable, str(SCRIPTS / "harness_supervisor.py"),
           "--workdir", str(s["_work"]), "--journal-root", str(s["_journal"]),
           "--mission-id", "204", "--steps", "6", "--step-seconds", "0.05",
           "--budget-calls", "100", "--max-attempts", "1", "--force-resume"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    s2 = json.loads((p.stdout or "").strip().splitlines()[-1])
    assert s2["resumed_step_count"] == 0, "日志全损时不得声称续跑"
    assert s2["journal_degraded"] is True
    assert s2["done_step_count"] == 6
    brief = [e for e in _seat_log(s["_work"]) if e.get("event") == "resume_brief_injected"]
    assert brief and brief[-1]["degraded"] is True


def test_partial_corruption_uses_verified_prefix_only(tmp_path):
    s = _sup(tmp_path, "C2a", mission_id=205, steps=6, step_seconds=0.05,
             budget_calls=100, max_attempts=1, kill_at_progress=0.5)
    jp = pathlib.Path(s["_journal"]) / "missions" / "m205" / "journal.jsonl"
    with open(jp, "a") as f:
        f.write('{"schema":"harness.journal.v3","seq":99,"kind":"checkpoi\n')
        f.write("@@garbage@@\n")
    # 复用同一 workdir/journal 续跑，才能命中同一份日志
    cmd = [sys.executable, str(SCRIPTS / "harness_supervisor.py"),
           "--workdir", str(s["_work"]), "--journal-root", str(s["_journal"]),
           "--mission-id", "205", "--steps", "6", "--step-seconds", "0.05",
           "--budget-calls", "100", "--max-attempts", "1", "--force-resume"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    s2 = json.loads((p.stdout or "").strip().splitlines()[-1])
    assert s2["journal_degraded"] is True
    assert s2["journal_corrupt_records"] >= 2
    assert s2["resumed_step_count"] >= 1
    assert set(s2["resumed_steps"]) <= {f"s{i}" for i in range(1, 7)}
    assert s2["done_step_count"] == 6
    brief = [e for e in _seat_log(s["_work"]) if e.get("event") == "resume_brief_injected"]
    assert brief and brief[-1]["degraded"] is True


def test_budget_cap_survives_resume(tmp_path):
    s = _sup(tmp_path, "D", mission_id=206, steps=6, step_seconds=0.05,
             budget_calls=4, max_attempts=3, kill_at_progress=0.34)
    assert s["kills"] == 1 and s["resumed_step_count"] >= 1
    assert s["calls_used"] <= 4, "续跑不得追加预算"
    assert s["status"] == "budget_exhausted"
    assert s["attempts"] <= 2, "预算耗尽后不得再拉起"
    assert all((e.get("journal") or {}).get("calls_used", 0) <= 4 for e in s["events"])


def test_gate_is_red_capable(tmp_path):
    """门禁必须可红：关掉 journal 后 '恢复≥1步' 判据必须判红，否则门是空的。"""
    s = _sup(tmp_path, "E", mission_id=207, steps=6, step_seconds=0.05,
             budget_calls=100, max_attempts=2, journal_off=True,
             kill_after_seconds=0.2, resume_probe=True)
    assert s["resumed_step_count"] < 1


def test_full_gate_script_passes(tmp_path):
    out = tmp_path / "gate"
    p = subprocess.run([sys.executable, str(SCRIPTS / "run_harness_v3_gate.py"),
                        "--outdir", str(out), "--base", str(tmp_path / "gwork")],
                       capture_output=True, text=True, timeout=600)
    rep = json.loads((out / "gate_report.json").read_text())
    assert rep["verdict"] == "PASS", p.stdout[-1500:]
    assert rep["gate_red_capable"] is True
    assert (out / "gate_evidence.md").exists()