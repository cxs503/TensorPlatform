"""干货包协议测试（合成夹具，零领域语义：域名/任务名全是假名）。"""

import json

import pytest

from tools.context_package.assembler import build_package
from tools.context_package.hook import inject
from tools.context_package.ledger import attribute, record
from tools.context_package.loader import load_config
from tools.context_package.package_schema import validate_decl, validate_package
from tools.context_package.render import render_block
from tools.context_package.taxonomy import detect_class
from tools.dispatch_contract.contract_schema import validate_contract

GOAL = "【合成任务】开发单：实现 dom-alpha 的 zeta 校验器，修 zeta 漂移 bug；容差 3%"


def cands(slot, cid, text, decision="reuse", rel_text=None):
    return {"id": cid, "slot": slot, "source": "synthetic", "text": text,
            "keyword_text": rel_text or text, "decision": decision,
            "pointer": f"synthetic#{cid}", "meta": {"domain": "DomAlpha"}}


def test_detect_class_config_driven():
    assert detect_class("工序03 出具证据")[0] == "factory_step_03"
    assert detect_class("审计单：复核交付")[0] == "audit"
    assert detect_class("算例单：跑 benchmark")[0] == "case_run"
    assert detect_class("开发单：实现 zeta 校验器")[0] == "dev"
    cid, ev = detect_class("无关键词的杂活")
    assert cid == "generic" and ev["reason"] == "NO_TRIGGER"


def test_density_gate_drops_items_without_decision():
    extra = {"ko_index": [cands("ko_index", "kb:1", "zeta 校验器复用", "reuse"),
                          cands("ko_index", "kb:2", "zeta 手册（无决策键）", "")],
             "negative_knowledge": [cands("negative_knowledge", "negk:1",
                                          "zeta 漂移 曾导致 inflated_delivery", "avoid")]}
    pkg = build_package(GOAL, repo="DomAlpha", mission_id=1, cfg=load_config(),
                        extra_candidates=extra, class_hint="dev")
    ids = [it["id"] for s in pkg["slots"] for it in s["items"]]
    assert "kb:2" not in ids, "无决策键条目必须被丢弃"
    assert {"id": "kb:2", "slot": "ko_index", "reason": "NO_DECISION"} in pkg["dropped"]
    assert pkg["audit"]["density_yield"] == 1.0
    assert validate_package(pkg, load_config()["decisions"]) == []


def test_budget_and_dedupe():
    same = "zeta 校验器 复用 同一条 知识 条目 文本"
    extra = {"ko_index": [cands("ko_index", f"kb:{i}", same, "reuse") for i in range(6)],
             "negative_knowledge": [cands("negative_knowledge", "negk:1", "zeta 漂移失败", "avoid")]}
    pkg = build_package(GOAL, repo="DomAlpha", mission_id=2, cfg=load_config(),
                        extra_candidates=extra, class_hint="dev", budget_override=260)
    used = pkg["budget"]["used_tokens"]
    assert used <= 260
    dup = [d for d in pkg["dropped"] if d["reason"] == "DUPLICATE"]
    assert dup, "同文本必须按 shingle 去重"
    assert sum(len(s["items"]) for s in pkg["slots"]) <= 8


def test_budget_scales_with_goal_size():
    small = build_package("开发单 短目标", repo="DomAlpha", extra_candidates={}, class_hint="dev")
    big = build_package("开发单 " + "zeta 校验器 漂移 容差 " * 60, repo="DomAlpha",
                        extra_candidates={}, class_hint="dev")
    assert small["budget"]["total_tokens"] <= big["budget"]["total_tokens"]
    assert small["budget"]["total_tokens"] >= 300


def test_render_has_decision_and_selfcheck():
    extra = {"ko_index": [cands("ko_index", "kb:1", "zeta 复用条目")],
             "negative_knowledge": [cands("negative_knowledge", "negk:1", "zeta 漂移", "avoid")]}
    cfg = load_config()
    pkg = build_package(GOAL, repo="DomAlpha", extra_candidates=extra, class_hint="dev", cfg=cfg)
    block = render_block(pkg, cfg["decisions"])
    assert "【干货包" in block and "密度自检" in block
    for line in block.splitlines():
        if line.startswith(" - ["):
            assert "](" in line, "每条注入行必须带决策键"


def test_ledger_and_hit_attribution(tmp_path):
    extra = {"ko_index": [cands("ko_index", "kb:9", "zeta 复用条目")],
             "negative_knowledge": [cands("negative_knowledge", "negk:9", "zeta 漂移", "avoid")]}
    cfg = load_config()
    pkg = build_package(GOAL, repo="DomAlpha", mission_id=7, extra_candidates=extra,
                        class_hint="dev", cfg=cfg)
    entry = record(pkg, rendered="x", ledger_dir=tmp_path, consumer="t")
    assert (tmp_path / f"{entry['ts'][:10]}.jsonl").exists()
    assert entry["package_sha256"] and len(entry["items"]) >= 1
    ptr = entry["items"][0]["pointer"]
    hits = attribute(entry, f"交付里引用了 {ptr} 与 kb:9", ledger_dir=tmp_path, min_terms=1)
    assert hits["hit_rate"] > 0 and hits["hits"]


def test_hook_modes_and_fail_open(tmp_path):
    block, info = inject(GOAL, "DomAlpha", mission_id=3, mode="off")
    assert block == "" and info["degraded"] == "MODE_OFF"
    block, info = inject(GOAL, "DomAlpha", mission_id=3, mode="nonsense")
    assert block == "" and info["degraded"].startswith("UNKNOWN_MODE")
    extra = {"ko_index": [cands("ko_index", "kb:1", "zeta 复用条目")],
             "negative_knowledge": [cands("negative_knowledge", "negk:1", "zeta 漂移", "avoid")]}
    block, info = inject(GOAL, "DomAlpha", mission_id=3, mode="report", extra_candidates=extra,
                         ledger_dir=tmp_path)
    assert info["injected"] and info["density_yield"] == 1.0 and "干货包" in block
    assert info["ledger"]["_ledger_path"]


def test_decl_and_contract_wiring():
    assert validate_decl(None) == []
    assert "CTXPKG_DECL_BUDGET_NOT_POSITIVE" in validate_decl({"budget_tokens": 0})
    assert "CTXPKG_DECL_SLOTS_NOT_LIST" in validate_decl({"slots": "ko_index"})
    ok, errs = validate_contract({"node_id": "n1", "contract_version": "dispatch-contract/v1",
                                  "input_contract": [], "output_contract": [],
                                  "context_package": {"slots": ["ko_index"], "budget_tokens": 800}})
    assert ok and errs == []
    ok2, errs2 = validate_contract({"node_id": "n1", "contract_version": "dispatch-contract/v1",
                                    "input_contract": [], "output_contract": [],
                                    "context_package": {"budget_tokens": -5}})
    assert not ok2 and any("context_package" in e for e in errs2)
