#!/usr/bin/env python3
"""IN28-N06 v0 退役审计用例：写者指纹 / 进程指纹 / 双条件判定。

判据 (b)：**v0 进程不存在 + v1 唯一写者（写审计 0 条 v0 写）**。
本文件用假 ``/proc`` 树与合成账本断言判定逻辑，不碰真进程；真退役由
``python3 -m tools.coordinator_shadow.retirement --retire`` 在生产上执行，并落 artifact。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from tools.coordinator_shadow import retirement as R

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def fake_proc(tmp_path, entries: dict[int, str]):
    """造一个假 /proc：每个 PID 一个目录 + cmdline（\\0 分隔）。"""
    root = tmp_path / "proc"
    root.mkdir(parents=True, exist_ok=True)
    for pid, cmd in entries.items():
        d = root / str(pid)
        d.mkdir()
        (d / "cmdline").write_bytes("\0".join(cmd.split(" ")).encode() + b"\0")
    return root


def test_writer_fingerprint_requires_both_beat_id_and_schema():
    assert R.writer_of({"beat_id": "996693", "schema": R.V1_SCHEMA}) == "v1"
    assert R.writer_of({"beat_id": None, "schema": None}) == "legacy"
    assert R.writer_of({"beat_id": "996693", "schema": None}) == "legacy"
    assert R.writer_of({"beat_id": None, "schema": R.V1_SCHEMA}) == "legacy"


def test_audit_writes_flags_legacy_rows_after_retirement():
    rows = [
        {"id": 1, "ts": NOW - timedelta(hours=2), "beat_id": None, "schema": None},
        {"id": 2, "ts": NOW - timedelta(hours=2), "beat_id": "996692", "schema": R.V1_SCHEMA},
        {"id": 3, "ts": NOW + timedelta(minutes=1), "beat_id": None, "schema": None,
         "model": "qwen3.7-plus"},
        {"id": 4, "ts": NOW + timedelta(minutes=2), "beat_id": "996698", "schema": R.V1_SCHEMA},
    ]
    report = R.audit_writes(rows, retired_at=NOW)
    assert report["counts"] == {"v1": 2, "legacy": 2}
    assert report["after_retirement"] == {"v1": 1, "legacy": 1}
    assert report["legacy_after_retirement_count"] == 1
    assert report["legacy_after_retirement"][0]["id"] == 3
    assert report["v1_share"] == 0.5

    clean = R.audit_writes(rows[:2], retired_at=NOW)
    assert clean["legacy_after_retirement_count"] == 0


def test_scan_processes_separates_v0_and_v1(tmp_path):
    proc = fake_proc(tmp_path, {
        111: "/usr/bin/python3 coordinator_shadow.py",
        222: "/usr/bin/python3 -m tools.coordinator_shadow.daemon --interval 1800",
        333: "/usr/bin/python3 something_else.py",
    })
    found = R.scan_processes(str(proc))
    assert [p["pid"] for p in found["v0"]] == [111]
    assert [p["pid"] for p in found["v1"]] == [222]
    assert found["v0_alive"] is True and found["v1_alive"] is True


def test_scan_processes_detects_no_v0(tmp_path):
    proc = fake_proc(tmp_path, {
        222: "/usr/bin/python3 -m tools.coordinator_shadow.daemon --interval 1800"})
    found = R.scan_processes(str(proc))
    assert found["v0_alive"] is False and found["v1_alive"] is True


def test_audit_pass_requires_all_three_conditions(tmp_path):
    v1_only = fake_proc(tmp_path / "a", {
        222: "/usr/bin/python3 -m tools.coordinator_shadow.daemon"})
    report = R.audit(None, NOW, proc_root=str(v1_only))
    assert report["criteria"]["v0_process_absent"] is True
    assert report["criteria"]["sole_writer"] is True
    assert report["criteria"]["pass"] is True
    assert report["ledger_error"] is None

    with_v0 = fake_proc(tmp_path / "b", {
        111: "/usr/bin/python3 coordinator_shadow.py",
        222: "/usr/bin/python3 -m tools.coordinator_shadow.daemon"})
    report = R.audit(None, NOW, proc_root=str(with_v0))
    assert report["criteria"]["v0_process_absent"] is False
    assert report["criteria"]["sole_writer"] is False
    assert report["criteria"]["pass"] is False

    no_writer = fake_proc(tmp_path / "c", {})
    report = R.audit(None, NOW, proc_root=str(no_writer))
    assert report["criteria"]["sole_writer"] is False
    assert report["criteria"]["pass"] is False


def test_retire_processes_actually_stops_a_child(tmp_path):
    """对一个真子进程做退役（不是模拟）：SIGTERM 后 /proc/<pid> 必须消失。"""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        assert os.path.exists(f"/proc/{child.pid}")
        actions = R.retire_processes([child.pid], grace_seconds=5)
        child.wait(timeout=10)          # 由父进程回收（生产里是 init 回收）
        assert actions[0]["gone"] is True
        assert "SIGTERM" in actions[0]["actions"]
        assert not os.path.exists(f"/proc/{child.pid}")
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)


def test_retire_processes_reports_already_gone():
    actions = R.retire_processes([999999], grace_seconds=0.1)
    assert actions[0]["gone"] is True
    assert actions[0]["actions"] == ["already-gone"]


def test_writer_fingerprint_rule_is_documented_in_artifact(tmp_path):
    proc = fake_proc(tmp_path / "d", {
        222: "/usr/bin/python3 -m tools.coordinator_shadow.daemon"})
    report = R.audit(None, NOW, proc_root=str(proc))
    rule = report["writer_fingerprint_rule"]
    assert "beat_id" in rule["v1"] and "schema" in rule["v1"]
    assert json.dumps(report, default=str)  # 可序列化，落 artifact 无障碍
    assert report["schema"] == R.SCHEMA