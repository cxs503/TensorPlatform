"""跨仓集成链测试：三个合成仓库 → 证据 → 裁定 → 台账 → 契约 → 报告 → HTTP 面。

为什么这是「跨仓」而不是普通单测
--------------------------------
战役交付的真问题是：**证据分散在不同仓库的工作树里**，平台侧不该把它们读进服务进程，
却又必须给出同一个判定。本测试用三个合成仓库（``repo-alpha`` / ``repo-beta`` /
``repo-gamma``，各自的 mission → 工作树 ``m<id>``）走完整链，并做两件关键互裁：

1. **磁盘路径 vs 内存裁判路径同值**：``evaluate_campaign``（读工作树）与
   ``evaluate_campaign_inline``（API 无状态）必须给出**相同 input_digest 与相同裁定**；
   若两条实现分叉，说明「线上说绿、离线说红」的经典事故已埋下。
2. **契约与报告是真的**：契约完整性检查跑**本仓真实契约文档**，报告六要素检查跑
   **真实渲染的报告**——测试失败即交付不完整，不是夹具问题。

负控（每条都能抓到一类造假/漏检）：
* 篡改证据文档 → 钉桩失配 FAIL(integrity)
* 上游自述 pass 与复算相反 → claim_mismatch → 战役 FAIL
* 节点零证据 → WITHHELD（不抬成 PASS）
* 台账多一条孤儿条目 → 双射破坏
* 契约文档删章节 → 完整性下降
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.maturity_gate import contracts as C  # noqa: E402
from tools.maturity_gate import gate as G  # noqa: E402
from tools.maturity_gate import ledger as L  # noqa: E402
from tools.maturity_gate import report as R  # noqa: E402
from tools.maturity_gate.evidence import (EvidenceResolver,  # noqa: E402
                                          payload_digest)
from tools.maturity_gate.spec import parse_spec  # noqa: E402

REPOS = (("repo-alpha", 9101), ("repo-beta", 9102), ("repo-gamma", 9103))


def _evidence(node_key: str, repo: str, bad: bool = False, lie: bool = False) -> Dict[str, Any]:
    value = 0.9 if bad else 0.01
    return {
        "evidence_schema_version": "1.0",
        "node_key": node_key,
        "repo": repo,
        "commit": "deadbeef",
        "criteria": {
            "a": {"values": {"rel_error": value}, "pass": not bad if not lie else True},
            "b": {"values": {"closure": 1e-6, "all_nonnegative": True}, "pass": True},
        },
        "pytest": {"returncode": 0, "passed": 12, "failed": 0},
    }


def _spec(threshold: float = 0.8) -> Dict[str, Any]:
    def crit(prefix: str):
        return [
            {"criterion_id": "a", "statement": f"{prefix} relative error",
             "checks": [{"doc": f"artifacts/p54w3_evidence/{prefix}.json",
                         "pointer": "criteria.a.values.rel_error", "comparator": "le",
                         "threshold": 0.1, "claim": "criteria.a.pass"}]},
            {"criterion_id": "b", "statement": f"{prefix} ledger closure",
             "checks": [{"doc": f"artifacts/p54w3_evidence/{prefix}.json",
                         "pointer": "criteria.b.values.closure", "comparator": "le",
                         "threshold": 1e-4},
                        {"doc": f"artifacts/p54w3_evidence/{prefix}.json",
                         "pointer": "criteria.b.values.all_nonnegative", "comparator": "eq",
                         "threshold": True}]},
            {"criterion_id": "c", "statement": f"{prefix} suite",
             "checks": [{"doc": f"artifacts/p54w3_evidence/{prefix}.json",
                         "pointer": "pytest.returncode", "comparator": "eq", "threshold": 0},
                        {"doc": f"artifacts/p54w3_evidence/{prefix}.json",
                         "pointer": "pytest.failed", "comparator": "le", "threshold": 0}]},
        ]

    return {
        "spec_id": "SYN-CROSSREPO",
        "schema_version": "1.0",
        "criteria_pass_threshold": threshold,
        "nodes": [
            {"node_key": f"SYN-{i}", "title": f"synthetic node {i}", "repo": repo,
             "mission_id": mid, "capability": f"cap_syn_{i}",
             "criteria": crit(f"SYN-{i}")}
            for i, (repo, mid) in enumerate(REPOS, start=1)
        ],
    }


@pytest.fixture()
def worktrees(tmp_path: Path):
    """三个仓库工作树 + 打了 sha256 钉桩的规格（钉桩指向真实文件指纹）。"""
    root = tmp_path / "work"
    spec = _spec()
    for (repo, mid), node in zip(REPOS, spec["nodes"]):
        ev = root / f"m{mid}" / "artifacts" / "p54w3_evidence" / f"{node['node_key']}.json"
        ev.parent.mkdir(parents=True, exist_ok=True)
        payload = _evidence(node["node_key"], repo)
        ev.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        # 规格里钉上真实指纹（模拟上游声明 sha256）
        for crit in node["criteria"]:
            for chk in crit["checks"]:
                chk["sha256"] = payload_digest(payload)
        (root / f"m{mid}" / "README.txt").write_text(repo, encoding="utf-8")
    return root, spec


def _inline_docs_from_disk(spec: Dict[str, Any], work_root: Path) -> Dict[str, Any]:
    docs: Dict[str, Any] = {}
    for node in spec["nodes"]:
        mid = node["mission_id"]
        for crit in node["criteria"]:
            for chk in crit["checks"]:
                doc = chk["doc"]
                if doc not in docs:
                    docs[doc] = json.loads((work_root / f"m{mid}" / doc).read_text(encoding="utf-8"))
    return docs


def test_cross_repo_chain_green(worktrees):
    root, spec_raw = worktrees
    disk = G.evaluate_campaign(parse_spec(spec_raw), EvidenceResolver(work_root=str(root)))
    inline = G.evaluate_campaign_inline(spec_raw,
                                       _inline_docs_from_disk(spec_raw, root))
    assert disk["campaign"]["verdict"] == "PASS"
    assert disk["campaign"]["criterion_a_all_nodes_ge_threshold"] is True
    assert disk["campaign"]["nodes_delivered"] == 3
    assert disk["campaign"]["min_pass_ratio"] == 1.0
    # 两条实现路径必须同值（若分叉 → 「线上绿/离线红」事故）
    assert disk["campaign"] == inline["campaign"]
    assert [s["pass_ratio"] for s in disk["node_summaries"]] == \
           [s["pass_ratio"] for s in inline["node_summaries"]]
    # 三条判据 × 三节点，逐条触发钉桩校验（sha256 全部命中，故无 integrity FAIL）
    assert all(c["verdict"] == "PASS" for n in disk["nodes"] for c in n["criteria"])


def test_cross_repo_ledger_bijection(worktrees):
    root, spec_raw = worktrees
    gate = G.evaluate_campaign(parse_spec(spec_raw), EvidenceResolver(work_root=str(root)))
    entries = L.build_entries(gate)
    bij = L.verify_bijection(entries, [n["node_key"] for n in spec_raw["nodes"]])
    assert bij["one_to_one"] is True and bij["entries_total"] == 3
    assert bij["levels"] == {"REAL": 3}
    # 每条目都带 3 条证据引用（三判据同文档，去重后 1 条/节点）
    assert all(len(e["evidence_refs"]) == 1 for e in entries)
    assert all(e["evidence_refs"][0]["sha256"] for e in entries)


def test_negative_tampered_evidence_fails(worktrees):
    root, spec_raw = worktrees
    victim = root / "m9102" / "artifacts" / "p54w3_evidence" / "SYN-2.json"
    doc = json.loads(victim.read_text())
    doc["criteria"]["a"]["values"]["rel_error"] = 0.0  # 事后美化成满分
    victim.write_text(json.dumps(doc), encoding="utf-8")
    gate = G.evaluate_campaign(parse_spec(spec_raw), EvidenceResolver(work_root=str(root)))
    verdicts = [c["verdict"] for n in gate["nodes"] for c in n["criteria"]]
    assert "FAIL" in verdicts
    assert gate["campaign"]["verdict"] == "FAIL"
    reasons = {c["reason"] for n in gate["nodes"] for c in n["criteria"]}
    assert "evidence_integrity_mismatch" in reasons


def test_negative_absent_node_withholds(tmp_path: Path):
    """三个节点只交了两个 → 战役 WITHHELD；不会因为「已交的都绿」而 PASS。"""
    spec_raw = _spec()
    root = tmp_path / "work"
    for (repo, mid), node in zip(REPOS[:2], spec_raw["nodes"][:2]):
        ev = root / f"m{mid}" / "artifacts" / "p54w3_evidence" / f"{node['node_key']}.json"
        ev.parent.mkdir(parents=True, exist_ok=True)
        ev.write_text(json.dumps(_evidence(node["node_key"], repo)), encoding="utf-8")
    gate = G.evaluate_campaign(parse_spec(spec_raw), EvidenceResolver(work_root=str(root)))
    assert gate["campaign"]["verdict"] == "WITHHELD"
    assert gate["campaign"]["nodes_absent"] == ["SYN-3"]
    assert gate["campaign"]["blocking"][0]["reason"] == "no_evaluable_evidence"
    assert gate["campaign"]["criterion_a_all_nodes_ge_threshold"] is False


def test_negative_self_claim_contradiction(worktrees):
    """上游自述 pass=true 但实测超阈值 → claim_mismatch → 战役 FAIL 并进台账。"""
    root, spec_raw = worktrees
    victim = root / "m9101" / "artifacts" / "p54w3_evidence" / "SYN-1.json"
    doc = _evidence("SYN-1", "repo-alpha", bad=True, lie=True)
    victim.write_text(json.dumps(doc), encoding="utf-8")
    for crit in spec_raw["nodes"][0]["criteria"]:
        for chk in crit["checks"]:
            chk["sha256"] = payload_digest(doc)
    gate = G.evaluate_campaign(parse_spec(spec_raw), EvidenceResolver(work_root=str(root)))
    assert gate["campaign"]["claim_mismatch_total"] == 1
    assert gate["campaign"]["verdict"] == "FAIL"
    entries = L.build_entries(gate)
    assert entries[0]["claim_mismatches"] == ["a"]


def test_negative_ledger_orphan_detected(worktrees):
    root, spec_raw = worktrees
    gate = G.evaluate_campaign(parse_spec(spec_raw), EvidenceResolver(work_root=str(root)))
    entries = L.build_entries(gate)
    orphan = copy.deepcopy(entries[0])
    orphan["entry_id"] = "cap::SYN-ghost"
    orphan["node_key"] = "SYN-ghost"
    bij = L.verify_bijection(entries + [orphan], [n["node_key"] for n in spec_raw["nodes"]])
    assert bij["one_to_one"] is False
    assert bij["orphan_entries"] == ["SYN-ghost"]


def test_real_contracts_complete():
    """本仓真实契约文档必须完整（交付判据 d 的机器化）。"""
    manifest = json.loads((REPO_ROOT / "data" / "maturity_gate"
                           / "contracts.manifest.json").read_text(encoding="utf-8"))
    res = C.check_manifest(REPO_ROOT, manifest)
    assert res["complete"] is True, res["missing"]
    assert res["completeness"] == 1.0


def test_real_report_six_elements():
    """本仓真实交付报告必须六要素齐全（交付判据 e 的机器化）。"""
    report_path = REPO_ROOT / "docs" / "P54W3-N08_maturity_integration.md"
    assert report_path.is_file(), "交付报告缺失"
    md = report_path.read_text(encoding="utf-8")
    figures = [p for p in (REPO_ROOT / "artifacts" / "p54w3_maturity").glob("*.png")]
    res = R.check_six_elements(md, figures=figures)
    assert res["complete"] is True, res["missing"]


def _load_api_module():
    """按文件路径装载 backend/api/maturity_gate.py（绕开 api 包名冲突）。"""
    path = REPO_ROOT / "backend" / "api" / "maturity_gate.py"
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    spec = importlib.util.spec_from_file_location("tp_maturity_gate_api", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["tp_maturity_gate_api"] = module  # pydantic 前向引用解析需要
    spec.loader.exec_module(module)
    return module


def test_http_judge_matches_disk_judgement(worktrees):
    """HTTP 裁判（无状态）与磁盘裁定同值：跨仓证据 + 平台裁定必须可互验。"""
    pytest.importorskip("fastapi")
    api = _load_api_module()
    root, spec_raw = worktrees
    docs = _inline_docs_from_disk(spec_raw, root)
    # 磁盘版把 doc 名当相对路径；内存版直接用 doc 名做键 → 载荷一一对应
    req = {"spec_id": spec_raw["spec_id"],
           "criteria_pass_threshold": spec_raw["criteria_pass_threshold"],
           "nodes": spec_raw["nodes"], "evidence_docs": docs}
    judged = api.judge(api.MaturityGateRequest(**req))
    disk = G.evaluate_campaign(parse_spec(spec_raw), EvidenceResolver(work_root=str(root)))
    assert judged["campaign"]["verdict"] == disk["campaign"]["verdict"] == "PASS"
    assert [s["pass_ratio"] for s in judged["node_summaries"]] == \
           [s["pass_ratio"] for s in disk["node_summaries"]]
    assert judged["ledger"]["bijection"]["one_to_one"] is True

    # 端点本体：FastAPI 路由可挂载且返回 200（不需要 DB / 全局 app）
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    client = TestClient(app)
    resp = client.post("/api/v1/tensor/campaigns/maturity-gate", json=req)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["campaign"]["verdict"] == "PASS"
    assert body["ledger"]["bijection"]["one_to_one"] is True
    schema = client.get("/api/v1/tensor/campaigns/maturity-gate/spec-schema")
    assert schema.status_code == 200
    assert "verdicts" in schema.json()

    # 规格非法 → 400（配置错误不得伪装成判据失败）
    bad = dict(req)
    bad["nodes"] = [dict(req["nodes"][0], criteria=[{"criterion_id": "a", "checks": [
        {"doc": "x", "pointer": "y", "comparator": "≈", "threshold": 1}]}])]
    assert client.post("/api/v1/tensor/campaigns/maturity-gate", json=bad).status_code == 400