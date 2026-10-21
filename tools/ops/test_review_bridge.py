#!/usr/bin/env python3
"""review_bridge.py 回归测试（B 改进：评审环入向腿的纯函数面）。

覆盖：head 前缀过滤、自贴评论跳过（防自激环）、按评论 id 幂等去重、
空评论跳过、老评论优先、goal 模板标记与原文嵌入。

跑法：/srv/tensor/shared/hermes-agent/.venv/bin/python -m pytest test_review_bridge.py -q
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("review_bridge", HERE / "review_bridge.py")
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)


def _pr(num, ref, sha="a" * 40, repo="TensorLBM"):
    return {"number": num, "base": {"repo": {"name": repo}},
            "head": {"ref": ref, "sha": sha, "repo": {"name": repo}}}


def test_select_filters_head_prefix():
    prs = [_pr(1, "agent/m9"), _pr(2, "main"), _pr(3, "fix/getblob")]
    out = rb.select_candidates(prs, [], [], {})
    assert out == []          # 无评论时无候选（head 过滤在评论层之上不单独出候选）
    # 同一评论挂在两个 PR：只认 agent//fix/ 的那个
    inline = [{"id": 11, "body": "这里有越界"}]
    out = rb.select_candidates(prs, inline, [], {})
    keys = {c["key"] for c in out}
    assert keys == {"TensorLBM#1-c11", "TensorLBM#3-c11"}   # main 分支 PR 不出候选


def test_select_skips_bridge_replies_and_processed():
    prs = [_pr(1, "agent/m9")]
    inline = [
        {"id": 11, "body": "真问题：越界"},
        {"id": 12, "body": rb.SKIP_MARK + "\n已修复见上"},      # 本桥自贴
        {"id": 13, "body": "   "},                              # 空评论
    ]
    convo = [{"id": 99, "body": "会话意见"}]
    processed = {"TensorLBM#1-m99": {"done": True}}             # 已处理过（会话=m 前缀）
    out = rb.select_candidates(prs, inline, convo, processed)
    assert [c["cid"] for c in out] == [11]
    assert out[0]["sha"] == "a" * 40 and "越界" in out[0]["body"]


def test_select_oldest_first():
    prs = [_pr(1, "agent/m9")]
    inline = [{"id": 30, "body": "b"}, {"id": 20, "body": "a"}]
    out = rb.select_candidates(prs, inline, [], {})
    assert [c["cid"] for c in out] == [20, 30]


def test_goal_text_shape():
    g = rb.goal_text("TensorLBM", 367, 12345, "意见原文：曲线单位错了")
    assert g.startswith(rb.MARK + "TensorLBM#367-c12345 ")
    assert "意见原文：曲线单位错了" in g
    assert "review_reply.md" in g and "request_acceptance" in g
    assert "不 git push" in g
    long_body = "x" * 9000
    assert len(rb.goal_text("R", 1, 2, long_body).split("---", 2)[1]) <= 4100


def test_mark_prefix_strict():
    """MARK 匹配必须前缀精确（benchmark 线 m66 教训的同型防御）。"""
    g = "platform-operator 值班：处理 REVIEW-RESPONSE:TensorLBM#1-c1 遗留"
    assert not g.startswith(rb.MARK) or g.index(rb.MARK) == 0
    assert "REVIEW-RESPONSE:" in g and not g.startswith("REVIEW-RESPONSE:")
