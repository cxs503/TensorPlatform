"""A/B 对照产物不变量：`artifacts/gate_content_criteria/ab_matrix.json` 必须可复算。

判据（先于实现）：
1. 提交的产物字段自洽：counts 与 rows 逐行统计一致；每个场景的实测裁决等于声明的
   预期裁决；`false_release_prevented` = 「旧门放行且新门未放行」的行数。
2. **可复算**：在临时目录重跑 `gate_content_criteria_demo.run()`，逐行
   (scenario, old_action, new_action, content_verdict, content_digest) 必须与提交产物
   完全一致 —— 数字不是手抄的，digest 也不是。
3. 图存在且非空（报告内嵌用）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ops.taskplane import gate_content_criteria_demo as demo  # noqa: E402

COMMITTED = REPO_ROOT / "artifacts" / "gate_content_criteria" / "ab_matrix.json"


@pytest.fixture(scope="module")
def committed() -> dict:
    assert COMMITTED.is_file(), f"缺少产物 {COMMITTED}"
    return json.loads(COMMITTED.read_text(encoding="utf-8"))


def test_counts_consistent_with_rows(committed: dict) -> None:
    rows = committed["rows"]
    counts = committed["counts"]
    assert counts["scenarios"] == len(rows) == 8
    assert counts["old_released"] == sum(1 for r in rows if r["old_action"] == "release")
    assert counts["new_released"] == sum(1 for r in rows if r["new_action"] == "release")
    assert counts["new_waited"] == sum(1 for r in rows if r["new_action"] == "wait")
    assert counts["false_release_prevented"] == sum(
        1 for r in rows if r["old_action"] == "release" and r["new_action"] != "release")


def test_every_scenario_matches_declared_verdict(committed: dict) -> None:
    for row in committed["rows"]:
        assert row["content_verdict"] == row["expected_verdict"], row["scenario"]
        assert len(row["content_digest"]) == 64
        assert row["check_details"], row["scenario"]


def test_hollow_artifact_was_falsely_released_by_legacy_gate(committed: dict) -> None:
    """缺陷本体：旧门对空洞 `{}` 工件放行，新门拦住。"""
    hollow = next(r for r in committed["rows"] if r["scenario"] == "hollow_object")
    assert hollow["old_action"] == "release"
    assert hollow["new_action"] == "wait"
    assert hollow["new_reason"] == "content_withheld"


def test_reproducible_in_tmp(tmp_path, monkeypatch, committed: dict) -> None:
    monkeypatch.setattr(demo, "OUT", tmp_path)
    fresh = demo.run()
    key = lambda r: (r["scenario"], r["old_action"], r["new_action"],
                     r["content_verdict"], r["content_digest"])
    assert [key(r) for r in fresh["rows"]] == [key(r) for r in committed["rows"]]
    assert fresh["counts"] == committed["counts"]
    assert (tmp_path / "ab_matrix.png").stat().st_size > 5000


def test_committed_png_present() -> None:
    png = REPO_ROOT / "artifacts" / "gate_content_criteria" / "ab_matrix.png"
    assert png.is_file() and png.stat().st_size > 5000