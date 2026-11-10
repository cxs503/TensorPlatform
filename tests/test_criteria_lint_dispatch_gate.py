#!/usr/bin/env python3
"""派发管线集成测试：criteria_lint 作为派发前门（p372w2-n06 成熟化要求）。

验证四件事：
1. 门在缺三要素时按模式记录/注入/拒绝；
2. 门**不误伤**：三要素齐备的任务书各模式都放行；
3. 门是 **fail-open**：机制抛异常时原样放行（门坏了不许堵死派发）；
4. 注入是纯追加且不改变判决（不自证满足）。

夹具全部用合成指标名，零领域语义。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.taskplane import campaign_dispatcher as D  # noqa: E402
from tools import criteria_lint as CL  # noqa: E402

BAD = "机器验收判据：(a) 指标 A 稳定；(b) 指标 B 与基线一致。"
GOOD = "机器验收判据：(a) 指标 A 相对实验误差 <= 5%；(b) 指标 B 与基线一致（残差 <= 1e-9）。"


def test_gate_off_returns_none(monkeypatch):
    monkeypatch.setenv(CL.ENV_MODE, "off")
    goal, d = D.criteria_lint_gate(BAD)
    assert goal == BAD and d is None


def test_gate_advisory_logs_without_touching_goal(monkeypatch):
    monkeypatch.setenv(CL.ENV_MODE, "advisory")
    goal, d = D.criteria_lint_gate(BAD, meta={"ext_id": "syn-n01"})
    assert goal == BAD
    assert d["action"] == "pass_logged"
    assert "CRIT_NO_NUMERIC_THRESHOLD" in d["reason_codes"]


def test_gate_block_refuses(monkeypatch):
    monkeypatch.setenv(CL.ENV_MODE, "block")
    goal, d = D.criteria_lint_gate(BAD, meta={"ext_id": "syn-n01"})
    assert d["action"] == "refuse"
    assert d["blocked_items"] >= 1


def test_gate_enforce_injects_append_only(monkeypatch):
    monkeypatch.setenv(CL.ENV_MODE, "enforce")
    goal, d = D.criteria_lint_gate(BAD, meta={"ext_id": "syn-n01"})
    assert d["action"] == "inject"
    assert goal.startswith(BAD)
    assert CL.strip_slot_blocks(goal) == BAD
    # 注入后判决不变 → 注入不自证满足
    monkeypatch.setenv(CL.ENV_MODE, "block")
    _, again = D.criteria_lint_gate(goal, meta={"ext_id": "syn-n01"})
    assert again["action"] == "refuse"


def test_gate_does_not_block_good_goal(monkeypatch):
    for mode in ("advisory", "enforce", "block"):
        monkeypatch.setenv(CL.ENV_MODE, mode)
        goal, d = D.criteria_lint_gate(GOOD, meta={"ext_id": "syn-n02"})
        assert d["action"] == "pass", (mode, d.get("reason_codes"))
        assert goal == GOOD


def test_gate_uses_structured_criteria_when_present(monkeypatch):
    monkeypatch.setenv(CL.ENV_MODE, "block")
    meta = {"ext_id": "syn-n03",
            "acceptance_criteria": [
                {"metric": "metric_x vs experiment", "threshold": 0.05, "unit": "relative"}]}
    _, d = D.criteria_lint_gate(BAD, meta=meta)
    assert d["action"] == "pass"
    assert d["input_sha256"] != "" and d["report_sha256"] != ""


def test_gate_is_fail_open_on_exception(monkeypatch):
    """机制抛异常 → 原样放行（fail-open），且不抛给调用方。"""
    monkeypatch.setenv(CL.ENV_MODE, "block")

    def boom(*a, **k):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(CL, "pre_dispatch", boom)
    goal, d = D.criteria_lint_gate(BAD, meta={})
    assert goal == BAD and d is None


def test_gate_decision_is_deterministic(monkeypatch):
    monkeypatch.setenv(CL.ENV_MODE, "advisory")
    _, a = D.criteria_lint_gate(BAD, meta={"ext_id": "syn-n01"})
    _, b = D.criteria_lint_gate(BAD, meta={"ext_id": "syn-n01"})
    assert a["report_sha256"] == b["report_sha256"]
    assert a["reason_code_counts"] == b["reason_code_counts"]