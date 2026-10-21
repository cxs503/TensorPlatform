#!/usr/bin/env python3
"""benchmark_dispatch.py 回归测试（F 改进）。

覆盖 2026-10-19 首夜实战暴露的 bug 与修复后的新逻辑：
  1. BACKLOG 迭代（旧代码 for i in BACKLOG 迭代 dict → TypeError）
  2. push 超时逃逸 + 令牌不入日志（TimeoutExpired repr 含 push URL）
  3. GIT_TERMINAL_PROMPT 必须是 str（int 0 会让 Popen 构 env 抛 TypeError）
  4. /tensor/reports 422 回归（stage 字段受 ^S[0-4]$ 约束，payload 不放）
  5. mission result 扁平结构（无 summary 键）的 PR 体生成
  6. lane_counts/lane_blocked 车道上限（P2a：gpu 串行；cpu 隔离并行≤3，
     非隔离须空道；未知条目保守按 gpu+非隔离）
  7. gates 门禁三态返回 + sync_terminal 三态处理（含 SUITE_FAILED）
  8. 网关失败：信号在 headline 而非 error_code（m61/m64 实测）→ 等 30 分钟
     平台重排，超时才自起重派 ≤3 次；回飞撤销等待钟
  9. P2b 套件门：gates 过但 suite_check 红 → SUITE_FAILED 终态
 10. wait_for_pr 门：GitHub API merged 查询；无令牌保守视为未合

跑法：/srv/tensor/shared/hermes-agent/.venv/bin/python -m pytest test_benchmark_dispatch.py -q
（eco venv 无 pytest）
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "benchmark_dispatch", HERE / "benchmark_dispatch.py")
bm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bm)

ITEM = {"key": "k1", "case": "c1", "model": "m", "budget": "1,2,3",
        "gpu": False, "wait_for": None, "goal_file": "goals/k1.txt"}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """把模块的落盘位置全部指到 tmp，防测试写真实 state/log。"""
    monkeypatch.setattr(bm, "WORK", tmp_path)
    monkeypatch.setattr(bm, "LOG", tmp_path / "test.log")
    monkeypatch.setattr(bm, "ECO", tmp_path)
    monkeypatch.setattr(bm, "BACKLOG", {"items": [dict(ITEM)]}, raising=False)
    return tmp_path


# ── 纯函数 ────────────────────────────────────────────────────────────

def test_lane_counts_unknown_is_gpu_and_dirty():
    """未知条目（状态没跟上）保守按 gpu+非隔离占道。"""
    assert bm.lane_counts([{"id": 99}], {}, {}, {}) == \
        {"gpu": 1, "cpu": 0, "cpu_dirty": 0}
    c = bm.lane_counts([{"id": 9}], {9: "fig"}, {"fig": False}, {"fig": False})
    assert c == {"gpu": 0, "cpu": 1, "cpu_dirty": 1}


def test_lane_blocked_gpu_serial():
    assert bm.lane_blocked({"gpu": 1, "cpu": 0, "cpu_dirty": 0}, "gpu", False)
    assert bm.lane_blocked({"gpu": 0, "cpu": 0, "cpu_dirty": 0},
                           "gpu", False) is None


def test_lane_blocked_cpu_mixing_rule():
    """P2a 混跑规则：非隔离须空道；隔离并行至 CPU_CAP 且不与非隔离混跑。"""
    empty = {"gpu": 0, "cpu": 0, "cpu_dirty": 0}
    two_iso = {"gpu": 0, "cpu": 2, "cpu_dirty": 0}
    full = {"gpu": 0, "cpu": 3, "cpu_dirty": 0}
    one_dirty = {"gpu": 0, "cpu": 1, "cpu_dirty": 1}
    assert bm.lane_blocked(empty, "cpu", False) is None
    assert bm.lane_blocked(two_iso, "cpu", False)      # 隔离在飞也让道
    assert bm.lane_blocked(one_dirty, "cpu", False)
    assert bm.lane_blocked(two_iso, "cpu", True) is None
    assert bm.lane_blocked(full, "cpu", True)          # 满员
    assert bm.lane_blocked(one_dirty, "cpu", True)     # 非隔离在飞不混跑


def test_report_payload_has_no_stage():
    """422 回归：stage 受 ^S[0-4]$ 约束，payload 里绝不能出现。"""
    p = bm.report_payload("# 标题\n\n第一段正文。", [], 7, "c1")
    assert "stage" not in p
    assert p["title"] == "标题"
    assert p["summary"] == "第一段正文。"
    assert p["mission_id"] == 7 and "c1" in p["tags"]


def test_report_payload_title_fallback():
    p = bm.report_payload("没有一级标题\n\n第二段。", [], 7, "c1")
    assert p["title"] == "benchmark 闭环：c1"     # 无 # 标题 → 默认标题
    assert p["summary"] == "没有一级标题"          # summary 取首个正文段


def test_pr_body_flat_result():
    """mission result 是扁平结构（headline/detail 在顶层，无 summary 键）。"""
    m = {"result": {"headline": "晋级完成", "detail": "四门全过"}}
    b = bm.pr_body(m, "c1", "agent/m9")
    assert b["title"] == "benchmarks: c1 闭环——晋级完成"
    assert b["head"] == "agent/m9" and b["base"] == "main"
    assert "晋级完成" in b["body"] and "四门全过" in b["body"]


def test_pr_body_summary_key_and_empty():
    m = {"result": {"summary": {"headline": "benchmarks: 已带前缀"}}
         }                       # 防御：若平台未来恢复 summary 键也能取到
    assert bm.pr_body(m, "c1", "b")["title"] == "benchmarks: 已带前缀"
    assert bm.pr_body({}, "c1", "b")["title"] == "benchmarks: c1 闭环"
    assert bm.pr_body({"result": "就是个字符串"}, "c1", "b")  # 非 dict 不炸


# ── push_and_pr：超时逃逸 + 令牌不泄漏 + env 类型 ─────────────────────

def _suite_proc(ok: bool) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        [], 0 if ok else 1,
        json.dumps({
            "rc": 0 if ok else 1,
            "error": "" if ok else "全库收集红：ImportError",
            "stages": [{"stage": "collect", "rc": 0 if ok else 1}]}),
        "")


def test_push_timeout_no_token_leak(isolated, monkeypatch):
    """6 次全超时 → 返回 None 不抛异常；日志不得含令牌（TimeoutExpired repr
    携带含 token 的 argv）；GIT_TERMINAL_PROMPT 必须是 str。
    套件门（push 前跑）须放行，push 本身才走到超时循环。"""
    wt = isolated / "agent_work" / "m9" / "TensorLBM"
    (wt / ".git").mkdir(parents=True)
    monkeypatch.setenv("GH_PUSH_TOKEN", "SECRETTOKEN42")
    seen_env = {}

    def fake_run(cmd, **kw):
        if "suite_check" in str(cmd[1]):
            return _suite_proc(True)
        seen_env.update(kw.get("env") or {})
        raise subprocess.TimeoutExpired(cmd="git", timeout=180)

    monkeypatch.setattr(bm.subprocess, "run", fake_run)
    monkeypatch.setattr(bm.time, "sleep", lambda s: None)   # 不真等 225s
    assert bm.push_and_pr(dict(ITEM), 9, {}) is None
    assert seen_env["GIT_TERMINAL_PROMPT"] == "0"
    assert isinstance(seen_env["GIT_TERMINAL_PROMPT"], str)
    log_text = (isolated / "test.log").read_text(encoding="utf-8")
    assert "SECRETTOKEN42" not in log_text
    assert "超时" in log_text


def test_push_gates_failed(isolated, monkeypatch):
    """gates 复算 rc!=0 → 返回 "GATES_FAILED"（终态，不推）。"""
    wt = isolated / "agent_work" / "m9" / "TensorLBM"
    wt.mkdir(parents=True)

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 1, json.dumps({"problems": ["x 对不上"]}), "")

    monkeypatch.setattr(bm.subprocess, "run", fake_run)
    assert bm.push_and_pr(dict(ITEM), 9, {}, gates_required=True) == "GATES_FAILED"


def test_push_suite_failed(isolated, monkeypatch):
    """P2b：gates 过但本地套件门红 → SUITE_FAILED 终态（不推）。"""
    wt = isolated / "agent_work" / "m9" / "TensorLBM"
    (wt / ".git").mkdir(parents=True)

    def fake_run(cmd, **kw):
        if "suite_check" in str(cmd[1]):
            return _suite_proc(False)
        return subprocess.CompletedProcess(
            cmd, 0, json.dumps({"problems": [], "verdict": "promote"}), "")

    monkeypatch.setattr(bm.subprocess, "run", fake_run)
    assert bm.push_and_pr(dict(ITEM), 9, {}, gates_required=True) == "SUITE_FAILED"
    log_text = (isolated / "test.log").read_text(encoding="utf-8")
    assert "套件门不过" in log_text


def test_push_suite_ok_reaches_push(isolated, monkeypatch):
    """套件门过 → 走到 push（此处令 push 超时以证明到达且仅到达）。"""
    wt = isolated / "agent_work" / "m9" / "TensorLBM"
    (wt / ".git").mkdir(parents=True)
    monkeypatch.setenv("GH_PUSH_TOKEN", "T")

    def fake_run(cmd, **kw):
        if "suite_check" in str(cmd[1]):
            return _suite_proc(True)
        raise subprocess.TimeoutExpired(cmd="git", timeout=180)

    monkeypatch.setattr(bm.subprocess, "run", fake_run)
    monkeypatch.setattr(bm.time, "sleep", lambda s: None)
    assert bm.push_and_pr(dict(ITEM), 9, {}) is None
    log_text = (isolated / "test.log").read_text(encoding="utf-8")
    assert "套件门过" in log_text


# ── sync_terminal：三态 + 网关等待/重派 ──────────────────────────────

def test_sync_terminal_success_flow(isolated, monkeypatch):
    """BACKLOG["items"] 迭代回归 + succeeded → push/PR/报告 → done。"""
    st = {"items": {"k1": {"mission": 9, "gates": True, "paper": True}}}
    calls = {}

    def fake_push(item, mid, m, gates_required=False, paper=False):
        calls["push"] = (item["key"], mid, gates_required, paper)
        return "https://github.com/x/pull/1"

    monkeypatch.setattr(bm, "push_and_pr", fake_push)
    monkeypatch.setattr(bm, "publish_report", lambda *a: 5)
    bm.sync_terminal({9: {"id": 9, "status": "succeeded",
                          "result": {"headline": "ok"}}}, st, "tok")
    rec = st["items"]["k1"]
    assert calls["push"] == ("k1", 9, True, True)   # gates/paper 标志透传
    assert rec["done"] and rec["pr"] and rec["report"] == 5


def test_sync_terminal_gates_failed_flow(isolated, monkeypatch):
    st = {"items": {"k1": {"mission": 9, "gates": True}}}
    monkeypatch.setattr(bm, "push_and_pr",
                        lambda *a, **k: "GATES_FAILED")
    bm.sync_terminal({9: {"id": 9, "status": "succeeded", "result": {}}}, st, "t")
    rec = st["items"]["k1"]
    assert rec["failed"] and rec["error"] == "gates_failed"


def test_sync_terminal_suite_failed_flow(isolated, monkeypatch):
    st = {"items": {"k1": {"mission": 9, "gates": True}}}
    monkeypatch.setattr(bm, "push_and_pr",
                        lambda *a, **k: "SUITE_FAILED")
    bm.sync_terminal({9: {"id": 9, "status": "succeeded", "result": {}}}, st, "t")
    rec = st["items"]["k1"]
    assert rec["failed"] and rec["error"] == "suite_failed" and not rec.get("done")


def test_sync_terminal_hold_when_push_none(isolated, monkeypatch):
    """push/PR 未成 → HOLD：state 不置 done（下轮重试，push 幂等）。"""
    st = {"items": {"k1": {"mission": 9}}}
    monkeypatch.setattr(bm, "push_and_pr", lambda *a, **k: None)
    bm.sync_terminal({9: {"id": 9, "status": "succeeded", "result": {}}}, st, "t")
    assert not st["items"]["k1"].get("done")


def test_gateway_signal_in_headline_waits(isolated, monkeypatch):
    """m61/m64 实测：error_code 空、headline=gateway_unavailable → 只记等待钟，
    不立刻判死也不立刻重派（平台会自动重排）。"""
    st = {"items": {"k1": {"mission": 9}}}
    m = {"id": 9, "status": "failed",
         "result": {"headline": "gateway_unavailable", "detail": "tool_calls=70"}}
    bm.sync_terminal({9: m}, st, "t")
    rec = st["items"]["k1"]
    assert not rec.get("failed") and not rec.get("retries")
    assert rec["gw_since"]


def test_gateway_redispatch_after_30min(isolated, monkeypatch):
    st = {"items": {"k1": {"mission": 9, "gw_since": time.time() - 4000}}}
    m = {"id": 9, "status": "failed",
         "result": {"error_code": "gateway_unavailable"}}
    bm.sync_terminal({9: m}, st, "t")
    rec = st["items"]["k1"]
    assert rec["retries"] == 1 and rec["mission"] is None
    assert rec["status"] == "retry_gateway"


def test_gateway_exhausted_to_manual(isolated, monkeypatch):
    st = {"items": {"k1": {"mission": 9, "retries": 3,
                           "gw_since": time.time() - 4000}}}
    bm.sync_terminal({9: {"id": 9, "status": "failed",
                          "result": {"headline": "gateway_unavailable"}}}, st, "t")
    rec = st["items"]["k1"]
    assert rec["failed"] and rec["error"] == "gateway_exhausted"


def test_gateway_back_inflight_clears_clock(isolated, monkeypatch):
    """平台重排回到在飞 → 撤销等待钟（防下次失败时误判已超 30 分钟）。"""
    st = {"items": {"k1": {"mission": 9, "gw_since": time.time()}}}
    bm.sync_terminal({9: {"id": 9, "status": "queued"}}, st, "t")
    assert "gw_since" not in st["items"]["k1"]


def test_non_gateway_failure_is_manual(isolated, monkeypatch):
    st = {"items": {"k1": {"mission": 9}}}
    bm.sync_terminal({9: {"id": 9, "status": "failed",
                          "result": {"error_code": "budget_exceeded"}}}, st, "t")
    rec = st["items"]["k1"]
    assert rec["failed"] and "budget_exceeded" in rec["error"]


# ── wait_for_pr 门（P3b）───────────────────────────────────────────────

def test_pr_merged_uses_api(isolated, monkeypatch):
    monkeypatch.setenv("GH_PUSH_TOKEN", "tok")
    got = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"merged": True}).encode()

    def fake_urlopen(req, timeout=0):
        got["url"] = req.full_url
        return FakeResp()

    monkeypatch.setattr(bm.urllib.request, "urlopen", fake_urlopen)
    assert bm.pr_merged(374) is True
    assert got["url"].endswith("/repos/cxs503/TensorLBM/pulls/374")


def test_pr_merged_no_token_is_false(isolated, monkeypatch):
    """无令牌/断网保守视为未合（条目继续等，不误派）。"""
    monkeypatch.setattr(bm, "gh_token", lambda: "")
    assert bm.pr_merged(374) is False
    log_text = (isolated / "test.log").read_text(encoding="utf-8")
    assert "无令牌" in log_text


def test_pr_merged_network_error_is_false(isolated, monkeypatch):
    monkeypatch.setenv("GH_PUSH_TOKEN", "tok")

    def boom(req, timeout=0):
        raise RuntimeError("conn reset")

    monkeypatch.setattr(bm.urllib.request, "urlopen", boom)
    assert bm.pr_merged(374) is False
    log_text = (isolated / "test.log").read_text(encoding="utf-8")
    assert "视为未合" in log_text


# ── P0b：GPU5_BUSY 失活检测 ────────────────────────────────────────────

class _NSmi:
    """伪 nvidia-smi：按 argv 分发两查询输出；rc≠0 模拟查询失败。"""

    def __init__(self, gpu_csv, apps_csv, rc=0):
        self.gpu_csv, self.apps_csv, self.rc = gpu_csv, apps_csv, rc

    def __call__(self, argv, **kw):
        if "--query-gpu=" in " ".join(argv):
            return subprocess.CompletedProcess(argv, self.rc,
                                               stdout=self.gpu_csv, stderr="")
        return subprocess.CompletedProcess(argv, self.rc,
                                           stdout=self.apps_csv, stderr="")


def test_gpu5_busy_true(monkeypatch):
    """GPU5 bus 出现在 compute-apps → True（尊重标记）。"""
    monkeypatch.setattr(bm.subprocess, "run", _NSmi(
        "0, 00000000:01:00.0\n5, 00000000:85:00.0\n", "00000000:85:00.0"))
    assert bm.gpu5_actually_busy() is True


def test_gpu5_busy_stale_false(monkeypatch):
    """GPU5 bus 不在 compute-apps（occupant 已退）→ False=标记失活。"""
    monkeypatch.setattr(bm.subprocess, "run", _NSmi(
        "5, 00000000:85:00.0\n", "00000000:07:00.0"))
    assert bm.gpu5_actually_busy() is False


def test_gpu5_busy_query_fail_none(monkeypatch):
    """nvidia-smi 红/缺失 → None（保守尊重标记，绝不误判空闲）。"""
    monkeypatch.setattr(bm.subprocess, "run", _NSmi("", "", rc=1))
    assert bm.gpu5_actually_busy() is None


def test_gpu5_busy_no_gpu5_row_none(monkeypatch):
    """查不到 index=5 行（索引漂移）→ None。"""
    monkeypatch.setattr(bm.subprocess, "run", _NSmi(
        "0, 00000000:01:00.0\n", "00000000:01:00.0"))
    assert bm.gpu5_actually_busy() is None


def test_respect_marker_stale_ignores(isolated, monkeypatch):
    """标记在但失活 → gpu 道不封（不再饿死 GPU 条目）；真占/不可查 → 封道。"""
    runs = isolated / "runs"
    runs.mkdir()
    (runs / "GPU5_BUSY").write_text("")
    monkeypatch.setattr(bm, "RUNS", runs)

    counts = {"gpu": 0, "cpu": 0, "cpu_dirty": 0}
    monkeypatch.setattr(bm, "gpu5_actually_busy", lambda: False)
    bm.respect_gpu5_marker(counts)
    assert counts["gpu"] == 0, "失活标记不得封 gpu 道"

    monkeypatch.setattr(bm, "gpu5_actually_busy", lambda: True)
    bm.respect_gpu5_marker(counts)
    assert counts["gpu"] == bm.GPU_CAP

    counts2 = {"gpu": 0, "cpu": 0, "cpu_dirty": 0}
    monkeypatch.setattr(bm, "gpu5_actually_busy", lambda: None)
    bm.respect_gpu5_marker(counts2)
    assert counts2["gpu"] == bm.GPU_CAP, "不可查保守封道"


def test_backlog_backward_step_l4_is_gpu():
    """车道修正回归：backward-step-l4 实测跑 run_L4.py 占 GPU5 → gpu:true。"""
    bl = json.loads((bm.WORK / "backlog.json").read_text(encoding="utf-8"))
    it = [i for i in bl["items"] if i["key"] == "backward-step-l4"]
    assert it, "backlog 缺 backward-step-l4"
    assert it[0].get("gpu") is True
