#!/usr/bin/env python3
"""compute_gate 单测（机器门）：/proc 解析、作用域判定、静态硬拦、阈值判据、迁移不丢工作。

红队实证（真起 32 核进程树 + 真递交队列）见 tools/compute_gate/redteam.py，
本文件只做可离线重复的逻辑级断言——两者都绿才算本门可信。
"""
import json
import os
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.compute_gate import guard, ledger, policy, preflight, procs, queue  # noqa: E402


# ── 假 /proc 构造 ────────────────────────────────────────────────────
def _mkproc(tmp, pid, ppid, cmdline, utime=0, stime=0, cwd="/", state="R", comm=None, start=1000):
    d = pathlib.Path(tmp) / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    # 真 /proc/<pid>/stat 布局：state f0, ppid f1, f2..f10 填充, utime f11, stime f12,
    # cutime/cstime f13/f14, f15..f18 填充, starttime f19, vsize f20, rss f21
    stat = ("%d (%s) %s %d %s %d %d 0 0 0 0 0 0 %d 0 %d 0"
            % (pid, comm or cmdline.split(" ")[0][:15], state, ppid, " ".join(["0"] * 9),
               utime, stime, start, 100))
    (d / "stat").write_text(stat + "\n")
    (d / "cmdline").write_bytes(cmdline.replace(" ", "\x00").encode() + b"\x00")
    (d / "cwd").symlink_to(cwd)


def test_procs_tree_accounting(tmp_path):
    _mkproc(tmp_path, 100, 1, "bash -lc pytest -n 32", utime=50, cwd=str(tmp_path))
    _mkproc(tmp_path, 101, 100, "python3 pytest -n 32", utime=200, cwd=str(tmp_path))
    _mkproc(tmp_path, 102, 101, "python3 worker", utime=300, cwd=str(tmp_path))
    ps = procs.read_procs(str(tmp_path))
    assert set(procs.subtree(ps, 100)) == {100, 101, 102}
    # 用 own_ticks 聚合：50+200+300 ticks
    hz = procs.CLK_TCK
    assert abs(procs.subtree_cpu_seconds(ps, 100) - 550 / hz) < 1e-6
    assert procs.mission_id_from_path("/root/tp-agent/work/m17371/tools") == 17371
    assert procs.mission_id_from_path("/root/tp-agent/cluster") is None


def test_work_scope_roots_only_takes_entry_process(tmp_path):
    root = tmp_path / "work"
    wt = root / "m99001"
    wt.mkdir(parents=True)
    _mkproc(tmp_path, 1, 0, "systemd")                    # 系统
    _mkproc(tmp_path, 200, 1, "python3 tp_worker.py", cwd="/root/tp-agent")  # 控制面，域外 cwd
    _mkproc(tmp_path, 201, 200, "bash -lc pytest -n 32", cwd=str(wt))        # 树入口
    _mkproc(tmp_path, 202, 201, "python3 pytest -n 32", cwd=str(wt))         # 子树
    ps = procs.read_procs(str(tmp_path))
    assert procs.work_scope_roots(ps, [str(root)]) == [201]


def test_queue_runner_descendants_are_excluded(tmp_path):
    root = tmp_path / "work"
    wt = root / "m99002"
    wt.mkdir(parents=True)
    _mkproc(tmp_path, 300, 1, "python3 /root/tp-agent/cluster/cluster_queue.py --sched",
            cwd="/root/tp-agent/cluster")
    _mkproc(tmp_path, 301, 300, "python3 -m pytest -n 32", cwd=str(wt))
    ps = procs.read_procs(str(tmp_path))
    runners = procs.matched_pids(ps, ["cluster_queue.py --sched"], [])
    assert runners == {300}
    assert procs.descendant_of_any(ps, 301, runners) is True
    # 同一条算力，若不是 runner 子孙 → 必须判违例
    assert procs.descendant_of_any(ps, 301, set()) is False


def test_threshold_semantics_cores_times_seconds():
    cfg = policy.load_config()
    assert policy.cpu_threshold_seconds(cfg) == pytest.approx(8.0 * 600.0)
    # 32 核实跑 20min = 38400 CPU 秒 ≫ 阈值；4 核跑 5min = 1200 CPU 秒 < 阈值
    assert 32 * 1200 > policy.cpu_threshold_seconds(cfg)
    assert 4 * 300 < policy.cpu_threshold_seconds(cfg)


def test_scan_flags_active_hog_only_after_two_samples(tmp_path):
    """真 /proc：起一棵燃烧树。首次扫描只建档（无历史速率）；第二次扫描（有速率）判违例。
    再把阈值抬到 8 核×600s → 同一条进程不违例（正常短测试不被误伤）。"""
    import subprocess
    work = tmp_path / "work"
    d = work / "m99003"
    d.mkdir(parents=True)
    code = "import time\nt=time.time()\nwhile time.time()-t<8: pass\n"
    p = subprocess.Popen([sys.executable, "-c", code], cwd=str(d))
    try:
        cfg = dict(policy.load_config())
        cfg.update({"work_roots": [str(work)], "hog_cores": 0.6, "hog_seconds": 1.0,
                    "events_path": str(tmp_path / "ev.jsonl"),
                    "state_path": str(tmp_path / "st.json")})
        st = {"trees": {}}
        v1, obs1, st = guard.scan(cfg, state=st)
        assert any(o["pid"] == p.pid for o in obs1), obs1
        assert v1 == [], "首次扫描无速率历史，不下判定"
        time.sleep(1.5)
        v2, obs2, st = guard.scan(cfg, state=st)
        assert [o["pid"] for o in v2] == [p.pid], (v2, obs2)
        # 阈值抬到生产值（8 核×600s = 4800 CPU 秒）→ 不判违例
        cfg2 = dict(cfg); cfg2.update({"hog_cores": 8.0, "hog_seconds": 600.0})
        time.sleep(1.5)
        v3, obs3, _ = guard.scan(cfg2, state={"trees": dict(st["trees"])})
        assert v3 == []
    finally:
        p.kill()
        p.wait(timeout=10)


def test_scan_splits_idle_heavy_tree_into_advisory_not_action(tmp_path):
    """历史累计超阈值但当前空闲的遗留树 → advisory 只记账，不判违例（防误杀慢作业）。"""
    work = tmp_path / "work"
    (work / "m99005").mkdir(parents=True)
    # 60000 ticks = 600 CPU 秒，阈值设成 0.5 核×1s = 0.5 → 远超
    _mkproc(tmp_path, 500, 1, "python3 slow_but_idle.py", utime=30000, stime=30000,
            cwd=str(work / "m99005"))
    cfg = dict(policy.load_config())
    cfg.update({"work_roots": [str(work)], "hog_cores": 0.5, "hog_seconds": 1.0,
                "events_path": str(tmp_path / "ev.jsonl"),
                "state_path": str(tmp_path / "st.json")})
    st = {"trees": {}}
    guard.scan(cfg, proc_root=str(tmp_path), state=st)
    time.sleep(0.2)
    v, obs, st = guard.scan(cfg, proc_root=str(tmp_path), state=st)   # CPU 未增长 → cores_now=0
    assert v == []
    adv = st.get("_advisories") or []
    assert [a["pid"] for a in adv] == [500], (adv, obs)


def test_scan_excludes_own_ancestor_chain(tmp_path):
    """gate 自身及祖先链永不判违例（否则会把自己所在的 worker/控制面停掉）。"""
    cfg = dict(policy.load_config())
    cfg.update({"work_roots": [os.getcwd()], "hog_cores": 0.0, "hog_seconds": 0.0,
                "events_path": str(tmp_path / "e.jsonl"), "state_path": str(tmp_path / "s.json")})
    # 阈值 0，若作用域判定失效这里会把 pytest 自己的树判违例
    v, obs, _ = guard.scan(cfg, state={"trees": {}})
    assert all(not o["gate_self"] for o in v)


# ── 静态硬拦 ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("cmd", [
    "pytest -n 32 tests/",
    "pytest -n auto",
    "xargs -P 16 -I{} echo {}",
    "make -j32",
    "nohup python3 long_job.py > out.log 2>&1 &",
    "setsid ./solver &",
    "python3 solve.py &",
    "sleep 900",
])
def test_preflight_blocks_heavy_or_escaping(cmd):
    r = preflight.check_local_command(cmd)
    assert r["verdict"] == "redirect", cmd
    assert r.get("suggested", {}).get("cmd")


@pytest.mark.parametrize("cmd", [
    "pytest -q tests/test_x.py",
    "pytest -n 4 tests/unit",
    "python3 -m pytest tests/tools/compute_gate -x -q",
    "git status --short",
    "ls tools | head",
    "grep -rn 'foo' tools/ | head -20",
    "make -j4 lint",
])
def test_preflight_allows_small_compute(cmd):
    r = preflight.check_local_command(cmd)
    assert r["verdict"] == "allow", (cmd, r)


def test_preflight_strips_background_on_redirect():
    r = preflight.check_local_command("nohup pytest -n 32 tests/ &")
    assert not r["suggested"]["cmd"].endswith("&")
    assert "nohup" not in r["suggested"]["cmd"]
    msg = preflight.format_redirect(r)
    assert "run_compute" in msg and "cjN" in msg


# ── 迁移不丢工作 / 失败保护 ──────────────────────────────────────────
class _FakeLedger:
    def __init__(self):
        self.events = []

    def record(self, kind, **kw):
        self.events.append({"kind": kind, **kw})
        return {"kind": kind}

    def annotate_job(self, jid, note):
        self.events.append({"kind": "annotate", "jid": jid, "note": note})
        return True


def _obs(tmp_path, cmd="python3 burn.py --cores 32"):
    d = tmp_path / "work" / "m99004"
    d.mkdir(parents=True)
    code = "import time\nt=time.time()\nwhile time.time()-t<6: pass\n"
    import subprocess
    p = subprocess.Popen([sys.executable, "-c", code], cwd=str(d))
    time.sleep(0.8)
    ps = procs.read_procs()
    return p, {"root": p.pid, "pid": p.pid, "key": ps[p.pid].key, "tree_pids": 1,
               "alive": 1, "cpu_seconds": 3.0, "cores_now": 3.0, "cores_ema": 4.0,
               "rss_mb": 1.0, "cwd": str(d), "cmd": cmd, "mission_id": 99004,
               "queue_runner_descendant": False, "gate_self": False,
               "threshold_cpu_seconds": 240.0}


def _cfg(tmp_path, dry_run=False):
    cfg = dict(policy.load_config())
    cfg.update({"work_roots": [str(tmp_path / "work")],
                "events_path": str(tmp_path / "ev.jsonl"),
                "state_path": str(tmp_path / "st.json"),
                "dry_run": dry_run, "enforce": True})
    return cfg


def test_enforce_submits_same_command_and_kills_local(tmp_path, monkeypatch):
    p, obs = _obs(tmp_path)
    cfg = _cfg(tmp_path)
    calls = {}

    def fake_submit(cfg_, **kw):
        calls.update(kw)
        return {"jid": "cj999", "queued_ahead": 2, "queue_position": 3, "state": "queued"}

    monkeypatch.setattr(queue, "submit", fake_submit)
    led = _FakeLedger()
    res = guard.enforce(cfg, {"_procs": procs.read_procs()}, obs, ledger=led)
    assert res["jid"] == "cj999"
    # 递交的是**同一条命令**，且带原 worktree 作 stage_dir（工作不丢）
    assert calls["cmd"] == obs["cmd"]
    assert calls["stage_dir"] == obs["cwd"]
    assert calls["mid"] == 99004
    assert any(e["kind"] == "hog_detected" and "违例待迁移" in (e.get("note") or "") for e in led.events)
    assert any(e["kind"] == "migrated" and e.get("jid") == "cj999" for e in led.events)
    # 本地树已杀
    time.sleep(1.0)
    assert not pathlib.Path("/proc/%d" % p.pid).exists() or procs.read_procs()[p.pid].state in ("Z", "X")


def test_enforce_keeps_local_process_when_submit_fails(tmp_path, monkeypatch):
    """递交失败 → SIGCONT 恢复 + 不杀（宁可不迁移也不丢工作）。"""
    p, obs = _obs(tmp_path)
    cfg = _cfg(tmp_path)

    def boom(cfg_, **kw):
        raise RuntimeError("scheduler down")

    monkeypatch.setattr(queue, "submit", boom)
    led = _FakeLedger()
    res = guard.enforce(cfg, {"_procs": procs.read_procs()}, obs, ledger=led)
    assert "jid" not in res
    assert any(e["kind"] == "migrate_failed" for e in led.events)
    time.sleep(0.5)
    st = procs.read_procs().get(p.pid)
    assert st is not None and st.state not in ("T", "Z", "X"), st
    p.kill(); p.wait(timeout=10)


def test_enforce_dry_run_touches_nothing(tmp_path):
    p, obs = _obs(tmp_path)
    cfg = _cfg(tmp_path, dry_run=True)
    led = _FakeLedger()
    res = guard.enforce(cfg, {"_procs": procs.read_procs()}, obs, ledger=led)
    assert res["steps"][0]["step"] == "dry_run"
    assert any(e["kind"] == "hog_detected" for e in led.events)
    assert procs.read_procs()[p.pid].state not in ("T",)
    p.kill(); p.wait(timeout=10)


# ── 账本 / 回执 ──────────────────────────────────────────────────────
def test_ledger_jsonl_and_usage_receipt(tmp_path):
    cfg = dict(policy.load_config())
    cfg.update({"events_path": str(tmp_path / "ev.jsonl"),
                "state_path": str(tmp_path / "st.json")})
    led = ledger.Ledger(cfg)
    led.record("hog_detected", mission_id=1, pid=10, cpu_seconds=600.0, note="违例待迁移")
    led.record("migrated", mission_id=1, pid=10, jid="cj1", cpu_seconds=600.0)
    u = led.usage()
    assert u["local"]["hog_events"] == 1
    assert u["local"]["cpu_min_consumed_before_intercept"] == pytest.approx(10.0)
    evs = [json.loads(l) for l in pathlib.Path(cfg["events_path"]).read_text().splitlines()]
    assert [e["kind"] for e in evs] == ["hog_detected", "migrated"]


def test_repo_of_falls_back_to_dirname(tmp_path):
    d = tmp_path / "some-tree"
    d.mkdir()
    assert queue.repo_of(str(d)) == "some-tree"


def test_cmd_from_shell_wrapped_argv():
    argv = ["bash", "-lc", "pytest -n 32 tests/ --maxfail=1"]
    assert queue.cmd_from_argv(argv, shell_wrapped=True) == "pytest -n 32 tests/ --maxfail=1"
    assert queue.cmd_from_argv(["python3", "a.py", "x y"]).endswith("'x y'")