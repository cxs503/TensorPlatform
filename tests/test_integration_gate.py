"""集成与互裁门 (tools/integration_gate) 的单元/契约测试。

纪律：本文件**只用合成域名** (alpha/beta/acme/metric_*) —— 平台仓零领域语义，
域数据经 data/campaigns/<c>/*.json 注入，不在代码或测试里出现域词条。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.integration_gate import compare, ledger as ledger_mod, sampler, schema  # noqa: E402
from tools.integration_gate.baseline import align_node  # noqa: E402
from tools.integration_gate.model import (Attestation, CheckResult, Criterion,  # noqa: E402
                                          PairResult, Trace, worst_verdict)
from tools.integration_gate.provenance import sha256_file, sha256_obj  # noqa: E402
from tools.integration_gate.report import build_report, render_markdown  # noqa: E402


# ─────────────────────────── helpers ───────────────────────────

def _trace(tid: str, param: str, pairs: list[tuple[float, float]]) -> Trace:
    return Trace(id=tid, kind="expression", param=param,
                 values=[p for p, _ in pairs],
                 rows=[{"param": p, "value": v} for p, v in pairs],
                 status="ok")


def _crit(cid: str, verdict: str, measured=None, op=None, thr=None, role="capability_gate"):
    return Criterion(id=cid, verdict=verdict, measured=measured, op=op, threshold=thr,
                     role=role, source="synthetic")


def _att(node="acme-n01", caps=None, crits=None) -> Attestation:
    return Attestation(node_ext_id=node, repo="acme", mission_id=1, branch="agent/x",
                       base_commit=None, head_commit=None, source_kind="synthetic",
                       source_ref="synthetic:test", source_sha256="0" * 64,
                       criteria=crits or [], capabilities=caps or [])


# ─────────────────────────── schema (fail-closed) ───────────────────────────

def test_schema_rejects_missing_field():
    with pytest.raises(schema.SchemaError):
        schema.validate_traces({"schema": schema.TRACES_SCHEMA,
                                "traces": [{"id": "t1", "kind": "expression"}]})


def test_schema_rejects_unknown_check():
    doc = {"schema": schema.PAIRS_SCHEMA,
           "pairs": [{"id": "p1", "title": "t",
                      "checks": [{"check": "not_a_check"}]}]}
    with pytest.raises(schema.SchemaError):
        schema.validate_pairs(doc)


def test_schema_rejects_bad_waypoint():
    doc = {"schema": schema.ATTESTATION_SCHEMA,
           "node": {"ext_id": "acme-n01", "repo": "acme"},
           "source": {"kind": "synthetic", "ref": "x", "sha256": "zz"},
           "criteria": []}
    with pytest.raises(schema.SchemaError):
        schema.validate_attestation(doc)


def test_numbers_override_declared_verdict():
    """自述 PASS 但数字越界 → 必须被重算成 FAIL（不接受自述结论）。"""
    verdict, conflict = schema.reconcile_criterion(
        {"id": "C1", "verdict": "PASS", "measured": 1.5, "op": "<=", "threshold": 1.0})
    assert verdict == "FAIL" and "contradicted" in conflict


# ─────────────────────────── sampler ───────────────────────────

def test_sampler_expression_and_script(tmp_path: Path):
    doc = {"schema": schema.TRACES_SCHEMA, "traces": [
        {"id": "lin", "kind": "expression", "expr": "2.0 * m + 1.0",
         "grid": {"param": "m", "values": [0.0, 1.0, 2.0]}},
        {"id": "sq", "kind": "expression", "expr": "m ** 2",
         "grid": {"param": "m", "values": [1.0, 2.0]}},
    ]}
    traces = sampler.produce_all(schema.validate_traces(doc), tmp_path)
    assert [r["value"] for r in traces["lin"].rows] == [1.0, 3.0, 5.0]
    assert traces["sq"].status == "ok"
    assert traces["sq"].provenance["trace_sha256"] == sha256_obj(
        {"id": "sq", "kind": "expression", "rows": traces["sq"].rows, "status": "ok"})


def test_sampler_expression_rejects_code_injection():
    with pytest.raises(ValueError):
        sampler.safe_eval("__import__('os').getcwd()", {})


def test_sampler_script_failure_is_fail_closed(tmp_path: Path):
    bad = tmp_path / "boom.py"
    bad.write_text("import sys\nprint('boom', file=sys.stderr)\nsys.exit(3)\n")
    spec = {"id": "boom", "kind": "script", "path": str(bad),
            "grid": {"param": "p", "values": [1.0]}}
    tr = sampler.produce_trace(spec, tmp_path)
    assert tr.status == "error" and "boom" in tr.error


# ─────────────────────────── compare ───────────────────────────

def test_trace_agreement_with_scale_transform():
    """a 是"乘以参数"后的量，b 是原量：a * param^-1 必须逐点等于 b。"""
    a = _trace("a", "e", [(0.4, 4968.76), (0.5, 3125.0), (0.6, 1958.334)])
    b = _trace("b", "e", [(0.4, 4968.76 / 0.4), (0.5, 3125.0 / 0.5),
                          (0.6, 1958.334 / 0.6)])
    res = compare.check_trace_agreement(
        {"a": "a", "b": "b", "tol": 1e-12, "transform": {"op": "scale", "power": -1.0}},
        {"a": a, "b": b})
    assert res.verdict == "AGREE", res.detail
    assert res.detail["max_rel_dev"] <= 1e-12


def test_trace_agreement_detects_mismatch():
    a = _trace("a", "e", [(0.4, 1.0), (0.5, 2.0)])
    b = _trace("b", "e", [(0.4, 1.0), (0.5, 2.2)])
    res = compare.check_trace_agreement({"a": "a", "b": "b", "tol": 1e-6}, {"a": a, "b": b})
    assert res.verdict == "DISAGREE" and res.detail["at_param"] == 0.5


def test_endpoint_agreement_uses_dense_end():
    a = _trace("a", "e", [(0.4, 10.0), (0.9, 1.0)])
    b = _trace("b", "e", [(0.4, 10.0), (0.9, 3.0)])
    res = compare.check_endpoint_agreement(
        {"a": "a", "b": "b", "end": "min", "tol": 1e-9}, {"a": a, "b": b})
    assert res.verdict == "AGREE" and res.detail["param"] == 0.4


def test_jump_ratio_catches_drop():
    tr = _trace("t", "e", [(0.79, 2.0), (0.80, 1.0)])
    res = compare.check_jump_ratio({"trace": "t", "max_ratio": 1.5}, {"t": tr})
    assert res.verdict == "DISAGREE" and res.detail["max_ratio"] == pytest.approx(2.0)


def test_withheld_when_trace_missing():
    res = compare.check_trace_agreement({"a": "ghost", "b": "ghost2", "tol": 1e-6}, {})
    assert res.verdict == "WITHHELD"
    assert "not produced" in res.detail["missing"][0]


def test_degenerate_detects_zero_response():
    metrics = {"att:x:driver": 0.044, "att:x:response": 0.0}
    res = compare.check_degenerate(
        {"driver_metric": "att:x:driver", "response_metric": "att:x:response"}, metrics)
    assert res.verdict == "DEGENERATE"


def test_metric_agreement_and_closure_threshold():
    metrics = {"m.inflow": 3.3333e-4, "m.analytic": 3.3333e-4, "m.resid": 8.5e-14}
    ok = compare.check_metric_agreement(
        {"a_metric": "m.inflow", "b_metric": "m.analytic", "tol": 1e-9}, metrics)
    assert ok.verdict == "AGREE"
    below = compare.check_metric_below(
        {"metric": "m.resid", "threshold": 1e-2, "counterpart": "convention"}, {}, metrics)
    assert below.verdict == "AGREE" and below.detail["ratio_to_threshold"] < 1e-9


def test_worst_verdict_ordering():
    assert worst_verdict(["AGREE", "WITHHELD"]) == "WITHHELD"
    assert worst_verdict(["AGREE", "DISAGREE", "AGREE"]) == "DISAGREE"
    assert worst_verdict([]) == "AGREE"


# ─────────────────────────── ledger ───────────────────────────

def test_ledger_caps_claim_to_evidence():
    att = _att(caps=[{"id": "acme.cap", "claim_level": "REAL", "summary": "s",
                      "blocking_criteria": ["C1"]}],
               crits=[_crit("C1", "PASS", 0.5, "<=", 1.0)])
    entries = ledger_mod.build_entries([att], [], {"pairs": []}, {}, "acme")
    assert entries[0]["level"] == "REAL"
    assert entries[0]["status"] == "PROVISIONAL"  # 无基线事实 → 不可从 main 复现


def test_ledger_honest_fail_cannot_be_raised():
    att = _att(caps=[{"id": "acme.cap", "claim_level": "REAL", "summary": "s"}],
               crits=[_crit("C1", "FAIL", 2.0, "<=", 1.0)])
    base = {"capabilities": [{"id": "acme.cap", "level": "MOCK", "honest_fail": True}]}
    entries = ledger_mod.build_entries([att], [], {"pairs": []}, {}, "acme", base)
    assert entries[0]["honest_fail"] is True
    assert entries[0]["level"] == "MOCK"  # 不得回填 REAL


def test_ledger_case_verdict_fail_does_not_cap():
    """工程判决 FAIL（role=case_verdict）不压实现等级。"""
    att = _att(caps=[{"id": "acme.cap", "claim_level": "REAL", "summary": "s"}],
               crits=[_crit("C1", "PASS", 0.5, "<=", 1.0),
                      _crit("C2", "FAIL", 1.2, "<=", 1.0, role="case_verdict")])
    entries = ledger_mod.build_entries([att], [], {"pairs": []}, {}, "acme")
    assert entries[0]["level"] == "REAL" and entries[0]["honest_fail"] is False


def test_ledger_pair_verdict_caps_level():
    att = _att(caps=[{"id": "acme.cap", "claim_level": "REAL", "summary": "s"}],
               crits=[_crit("C1", "PASS", 0.5, "<=", 1.0)])
    pair = PairResult(id="PX", title="t", kind="convention",
                      checks=[CheckResult("degenerate", "DEGENERATE", "blocking")],
                      cap_level="MOCK")
    entries = ledger_mod.build_entries([att], [pair],
                                       {"pairs": [{"id": "PX", "capabilities": ["acme.cap"]}]},
                                       {}, "acme")
    assert entries[0]["level"] == "MOCK"
    assert "pair_cap=MOCK" in entries[0]["notes"]


def test_ledger_deterministic_hash():
    att = _att(caps=[{"id": "acme.cap", "claim_level": "REAL", "summary": "s"}],
               crits=[_crit("C1", "PASS", 0.5, "<=", 1.0)])
    e = ledger_mod.build_entries([att], [], {"pairs": []}, {}, "acme")
    d1 = ledger_mod.build_ledger("c", e, [], [], "abc", generated_at="T")
    d2 = ledger_mod.build_ledger("c", list(reversed(e)), [], [], "abc", generated_at="T2")
    assert d1["content_sha256"] == d2["content_sha256"]
    assert sorted(d1["counts"]) == sorted(d2["counts"])


# ─────────────────────────── baseline ───────────────────────────

def test_baseline_unknown_commit_requires_contract():
    node = {"ext_id": "acme-n09", "repo": "acme", "branch": None, "base_commit": None}
    a = align_node(node, None, str(REPO), "main", "acme", workspace_root=None)
    assert a["state"] in ("UNKNOWN", "BRANCH_ONLY", "MERGED")
    assert a["contract_required"] == (a["state"] != "MERGED")


def test_baseline_external_repo_is_not_merged():
    node = {"ext_id": "acme-n10", "repo": "other-repo", "base_commit": "deadbeef"}
    a = align_node(node, None, str(REPO), "main", "acme")
    assert a["state"] == "EXTERNAL_REPO" and a["contract_required"] is True


def test_baseline_merged_commit_needs_no_contract():
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                          text=True).stdout.strip()
    node = {"ext_id": "acme-n11", "repo": "acme", "base_commit": head}
    a = align_node(node, None, str(REPO), "HEAD", "acme")
    assert a["state"] == "MERGED" and a["contract_required"] is False


# ─────────────────────────── report (end-to-end, synthetic) ───────────────────────────

def _write_campaign(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    (data / "attestations").mkdir(parents=True)
    nodes = {"schema": schema.NODES_SCHEMA, "nodes": [
        {"ext_id": "acme-n01", "label": "alpha", "repo": "acme", "mission_id": 1},
        {"ext_id": "acme-n02", "label": "beta", "repo": "other", "mission_id": 2},
    ]}
    traces = {"schema": schema.TRACES_SCHEMA, "traces": [
        {"id": "ta", "kind": "expression", "expr": "1.0 / m",
         "grid": {"param": "m", "values": [0.5, 1.0, 2.0]}, "meta": {"x": 0.5}},
        {"id": "tb", "kind": "expression", "expr": "1.0 / m + 0.0",
         "grid": {"param": "m", "values": [0.5, 1.0, 2.0]}, "meta": {"x": 0.0}},
    ]}
    pairs = {"schema": schema.PAIRS_SCHEMA, "pairs": [
        {"id": "PX-1", "title": "alpha vs beta", "capabilities": ["acme.cap1"],
         "counterpart_status": "PRESENT_ON_MAIN",
         "checks": [{"check": "trace_agreement", "a": "ta", "b": "tb", "tol": 1e-12}]},
        {"id": "PX-2", "title": "zero response", "counterpart_status": "ABSENT_ON_MAIN",
         "cap_level": "MOCK",
         "checks": [{"check": "degenerate", "driver_metric": "ta.x",
                     "response_metric": "tb.x"}]},
        {"id": "PX-3", "title": "closure", "counterpart_status": "PRESENT_ON_MAIN",
         "checks": [{"check": "metric_below", "metric": "ta.x", "threshold": 1.0}]},
    ]}
    for name, doc in (("nodes.json", nodes), ("traces.json", traces), ("pairs.json", pairs)):
        (data / name).write_text(json.dumps(doc), encoding="utf-8")
    att = {"schema": schema.ATTESTATION_SCHEMA,
           "node": {"ext_id": "acme-n01", "repo": "acme", "mission_id": 1},
           "source": {"kind": "synthetic", "ref": "synthetic:1", "sha256": "a" * 64},
           "criteria": [{"id": "C1", "verdict": "PASS", "measured": 0.5,
                         "op": "<=", "threshold": 1.0}],
           "capabilities": [{"id": "acme.cap1", "claim_level": "REAL", "summary": "s",
                             "blocking_criteria": ["C1"]}]}
    (data / "attestations" / "acme-n01.json").write_text(json.dumps(att), encoding="utf-8")
    return data


def test_cli_end_to_end_synthetic(tmp_path: Path):
    from tools.integration_gate import cli
    data = _write_campaign(tmp_path)
    out = tmp_path / "out"
    rc = cli.main(["--campaign", "synthetic", "--data", str(data), "--out", str(out),
                   "--repo-root", str(REPO), "--main", "HEAD", "--self-repo", "acme",
                   "--pytest-summary", str(tmp_path / "nope.json")])
    assert rc == 0
    rep = json.loads((out / "seed_report.json").read_text())
    assert rep["schema"].startswith("tp.integration.seed_report")
    assert len(rep["nodes"]) == 2
    i1 = rep["integration_criteria"]["I1_capability_registration"]
    # 两个节点都登记（无自证者以 WITHHELD 占位，不许"漏登记"）
    assert i1["passed"] is True and i1["value"]["registered"] == 2
    assert i1["value"]["levels_complete"] == 2
    assert rep["integration_criteria"]["I2_inter_adjudication_pairs"]["passed"] is True
    assert rep["integration_criteria"]["I5_pytest"]["passed"] is False  # 未给 pytest 摘要
    assert any(g["kind"] == "inter_adjudication_conflict" for g in rep["gaps"])
    assert rep["next_wave_seeds"], "缺口必须转成下一波播种提案"
    md = render_markdown(rep)
    assert "I1–I5" in md and "互裁" in md


def test_cli_fail_closed_on_bad_artifact(tmp_path: Path):
    from tools.integration_gate import cli
    data = _write_campaign(tmp_path)
    (data / "pairs.json").write_text(json.dumps({"schema": schema.PAIRS_SCHEMA,
                                                 "pairs": []}), encoding="utf-8")
    rc = cli.main(["--campaign", "synthetic", "--data", str(data),
                   "--out", str(tmp_path / "out2"), "--repo-root", str(REPO)])
    assert rc == 2
    err = json.loads((tmp_path / "out2" / "gate_error.json").read_text())
    assert err["stage"] == "artifact_load"


def test_package_is_domain_free():
    """零领域语义门：域词表由 data/ 注入（本文件自身不含任何域词条）。"""
    vocab_files = sorted(REPO.glob("data/campaigns/*/domain_vocab.json"))
    if not vocab_files:
        pytest.skip("no injected domain vocabulary (data/campaigns/*/domain_vocab.json)")
    banned: list[str] = []
    for vf in vocab_files:
        banned.extend(json.loads(vf.read_text(encoding="utf-8"))["words"])
    targets = sorted((REPO / "tools" / "integration_gate").glob("*.py")) + [Path(__file__)]
    import re
    for p in targets:
        text = p.read_text(encoding="utf-8")
        hits = []
        for w in banned:
            # 全词匹配（大小写不敏感）：避免把普通英文单词的子串误报成域词条
            if re.search(rf"\b{re.escape(w)}\b", text, flags=re.IGNORECASE):
                hits.append(w)
        assert not hits, f"{p.name} contains domain words: {hits}"


def test_sha256_file_roundtrip(tmp_path: Path):
    p = tmp_path / "x.json"
    p.write_text('{"a":1}')
    assert len(sha256_file(p)) == 64
    assert sha256_file(p) == sha256_file(p)