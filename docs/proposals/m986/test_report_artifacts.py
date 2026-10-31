#!/usr/bin/env python3
"""m986 验收测试：报告 805 返工版工件与正文的可机器验证断言。

运行：pytest docs/proposals/m986/test_report_artifacts.py -q
不依赖业务代码，只读 docs/ 与 artifacts/ 内的工件。
"""
import gzip
import hashlib
import json
import os
import re

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ART = os.path.join(ROOT, "artifacts", "m986")
REPORT = os.path.join(ROOT, "docs", "proposals", "m986", "REPORT.md")


def load(name):
    with open(os.path.join(ART, name)) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def md():
    with open(REPORT) as f:
        return f.read()


@pytest.fixture(scope="module")
def audit():
    return load("keyword_audit.json")


@pytest.fixture(scope="module")
def adj():
    return load("adjudication.json")


# ------------------------------------------------------------------ 工件完整性
def test_manifest_sha256():
    man = load("MANIFEST.json")
    assert man["files"], "MANIFEST 为空"
    for item in man["files"]:
        p = os.path.join(ROOT, item["path"])
        assert os.path.exists(p), "工件缺失: %s" % item["path"]
        assert hashlib.sha256(open(p, "rb").read()).hexdigest() == item["sha256"], "sha256 不符: %s" % item["path"]


def test_png_valid():
    p = os.path.join(ART, "fig_keyword_audit.png")
    raw = open(p, "rb").read()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "不是合法 PNG"
    assert len(raw) > 20000, "PNG 过小，疑似占位图"


# ------------------------------------------------------------------ 缺陷 1：审计节
def test_audit_section_present(md):
    assert "## ⑦ 情报扫描审计" in md
    for k in ("词表规模", "限定命中数", "裁定完整性", "假阳性率", "语义覆盖率", "与人工复核一致性"):
        assert k in md, "审计指标缺失: %s" % k


def test_adjudication_complete(adj):
    cands = load("adjudication_candidates.json")
    keys_c = {"%s|%s|%s" % (c["topic"], c["corpus"], c["id"]) for c in cands}
    keys_r = {"%s|%s|%s" % (r["topic"], r["corpus"], r["id"]) for r in adj["rows"]}
    assert keys_c == keys_r, "存在未裁定候选（禁止默认放行）"
    classes = set(adj["verdict_classes"])
    assert all(r["verdict"] in classes for r in adj["rows"])
    assert len(adj["rows"]) == adj["totals"].__len__() or sum(adj["totals"].values()) == len(adj["rows"])


def test_scan_recompute(audit):
    """第二源：从 corpus/ 独立重算限定命中数，不读 keyword_audit 的中间结果。"""
    def cnt(text, words):
        t = (text or "").lower()
        n = 0
        for w in words:
            wl = w.lower()
            n += (len(re.findall(r"(?<![a-z0-9])" + re.escape(wl) + r"(?![a-z0-9])", t))
                  if re.fullmatch(r"[a-z0-9 _\-.()]+", wl) else t.count(wl))
        return n

    nodes = load("corpus/nodes.json")
    props = load("corpus/peer_proposals.json")
    for topic, spec in audit["lexicon"].items():
        for cname, units, idkey in (("A_master_nodes", nodes, "node_id"), ("B_peer_proposals", props, "id")):
            recomputed = sum(1 for u in units
                             if cnt(u["text"], spec["object"]) and cnt(u["text"], spec["context"]))
            stored = audit["per_topic"][topic][cname]["units_qualified"]
            assert recomputed == stored, "%s/%s 复算不符: %s != %s" % (topic, cname, recomputed, stored)


# ------------------------------------------------------------------ 缺陷 2：局限节
def test_limitations_section(md):
    assert "## ⑤ 注意点" in md
    for k in ("语义遗漏风险", "未扫描语料", "时间截止点", "假阳性残余"):
        assert k in md


# ------------------------------------------------------------------ 缺陷 3：误差判定节
def test_error_section_declares_na(md):
    assert "## ⑥ 误差判定" in md
    assert "误差判定不适用（N/A）" in md
    for k in ("α ∈ [2.0, 3.5]", "Schulson", "WITHHELD", "前瞻验收阈值"):
        assert k in md, "误差判定节缺项: %s" % k


# ------------------------------------------------------------------ 缺陷 4：图件豁免
def test_figure_exemption_statement(md):
    assert "图件豁免声明" in md
    assert "不适用场量云图与解析解/文献比较曲线" in md
    assert "fig_keyword_audit.png" in md


# ------------------------------------------------------------------ 缺陷 5：措辞
def test_no_overclaim_wording(md):
    """旧措辞只允许出现在“弃用/改写/不成立”的引用语境里。"""
    markers = ("弃用", "改写", "不成立", "→")
    for bad in ("zero coverage", "verified NEW"):
        for line in md.splitlines():
            if bad in line:
                assert any(m in line for m in markers), "过强措辞未被弃用标注: %s" % line.strip()[:120]
    assert "no coverage found by keyword scan" in md
    assert "assessed NEW by keyword discrimination" in md


def test_report_identity(md, adj):
    assert "[RESUBMIT v2]" in md.splitlines()[0]
    assert "mission **973**" in md
    assert "805" in md
    assert adj["temporal_check"]["report_805_created_at"] is not None