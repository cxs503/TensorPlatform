#!/usr/bin/env python3
"""腐烂棘轮门禁（m2055）：证据指针腐烂不得比基线更差。

基线 = ``artifacts/verification_evidence/registry.json`` 里记录的实测值。
本测试现场重算一次（只读，git 只读子命令）并与基线比较：
新增文档若引用"全历史都不存在"的路径，或删测试后留下悬空 nodeid，这里会立刻红。

重定基线（确有正当理由时）：
    python3 tools/verification_evidence_registry.py --out artifacts/verification_evidence --figures
然后把下面的 BASELINE 改成新值，并在 commit message 里写出理由。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import verification_evidence_registry as ver  # noqa: E402

#: 实测基线（m2055, 语料 docs/**/*.md）
BASELINE = {
    "evidence_docs": 264,
    "REAL_ANCHORED": 59,
    "ROTTED": 35,
    "lost_pointers": 85,
    "node_missing": 1,
}


def test_live_registry_matches_baseline_ratchet():
    history = ver.load_git_history(REPO_ROOT)
    if history is None:
        pytest.skip("无 git 历史（导出包/浅克隆）：OFFBRANCH 判定不可用，棘轮不适用")
    reg = ver.build_registry(REPO_ROOT, "docs/**/*.md", history)

    assert reg["corpus"]["evidence_docs"] >= BASELINE["evidence_docs"] * 0.9, "证据语料不应大幅缩水"
    assert reg["grade_counts"]["ROTTED"] <= BASELINE["ROTTED"], (
        "证据文档腐烂数上升：%d > 基线 %d —— 请修指针或重定基线并在 commit 里说明理由"
        % (reg["grade_counts"]["ROTTED"], BASELINE["ROTTED"]))
    assert reg["pointers"]["lost"] <= BASELINE["lost_pointers"], (
        "LOST 指针上升：%d > 基线 %d" % (reg["pointers"]["lost"], BASELINE["lost_pointers"]))
    assert reg["pointers"]["node_missing"] <= BASELINE["node_missing"], (
        "悬空 nodeid 上升：%d > 基线 %d" % (reg["pointers"]["node_missing"], BASELINE["node_missing"]))


def test_artifacts_are_committed_and_consistent():
    reg_path = os.path.join(REPO_ROOT, "artifacts/verification_evidence/registry.json")
    reg = json.load(open(reg_path, encoding="utf-8"))
    assert reg["kpi"]["denominator_evidence_docs"] == reg["corpus"]["evidence_docs"]
    assert reg["kpi"]["numerator_real_anchored"] == reg["grade_counts"]["REAL_ANCHORED"]
    assert abs(sum(reg["grade_counts"].values()) - reg["corpus"]["evidence_docs"]) <= 0
    # 判级与指针状态不相矛盾：ROTTED 文档必须真的带 LOST 或名缺失
    for rec in reg["records"]:
        if rec["grade"] == "ROTTED":
            assert rec["n_lost"] > 0 or rec["n_node_missing"] > 0, rec["doc"]
    for name in ("fig_grade_distribution.png", "fig_pointer_status.png", "registry.md"):
        assert os.path.exists(os.path.join(REPO_ROOT, "artifacts/verification_evidence", name)), name


def test_generator_doc_matches_artifact():
    """审计页里的 KPI 数字必须和工件一致（防手抄漂移）。"""
    reg = json.load(open(os.path.join(REPO_ROOT, "artifacts/verification_evidence/registry.json"),
                        encoding="utf-8"))
    doc = open(os.path.join(REPO_ROOT, "docs/audits/verification_evidence_pointer_rot_m2055.md"),
               encoding="utf-8").read()
    k = reg["kpi"]
    expect = "%d/%d = %s" % (k["numerator_real_anchored"], k["denominator_evidence_docs"], k["rate"])
    assert expect in doc, "审计页 KPI 与工件不一致：期望 %s" % expect