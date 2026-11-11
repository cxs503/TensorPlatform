"""验收判据 (a)(b)(c) 的机器可裁测试：事故重放、合成负控 3/3、健康仓 0 误报。"""
from __future__ import annotations

from pathlib import Path

import pytest

from verification_vitality.controls import run_negative_control
from verification_vitality.replay import discover_fix, replay


def test_negative_control_detects_three_of_three(tmp_path):
    result = run_negative_control(tmp_path)
    assert result["total"] == 3
    assert result["detected"] == 3, result["cases"]
    assert result["detection_rate"] == 1.0
    assert result["passed"] is True
    kinds = {c["case"]: c["static_kinds"] for c in result["cases"]}
    assert kinds["module_rename"] == ["module_missing"]
    assert kinds["symbol_rename"] == ["symbol_missing"]
    assert kinds["reexport_removed"] == ["symbol_missing"]



def test_git_history_incident_replay():
    """用本仓库真实历史做正反例：修复前 collection error ⇒ dead/exit≠0，修复后 ⇒ 健康。"""
    repo = Path(__file__).resolve().parents[2]
    fix = discover_fix(repo, "tests/capability_ledger_auto/conftest.py")
    assert fix["parent"], "修复提交必须能取到父提交（事故状态）"
    result = replay(repo, "tests/capability_ledger_auto",
                    before_ref=fix["parent"], after_ref=fix["commit"], timeout=120)
    assert result["before"]["dead"] > 0
    assert result["before"]["exit_code"] != 0
    assert result["after"]["dead"] == 0, result["after"]["errors"]
    assert result["after"]["exit_code"] == 0
    assert result["passed"] is True