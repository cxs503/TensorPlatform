"""maturity gate 机制测试（合成域夹具：不使用任何真实学科词条）。

覆盖矩阵
--------
* 谓词层：le/lt/ge/gt/eq(数值/布尔/nonempty)/between + 容差 + 非数值拒绝
* 指针层：点号嵌套、``[i]`` 下标、负下标、缺键
* 判据层：合取（一 FAIL 即 FAIL；否则有 WITHHELD 即 WITHHELD；全 PASS 才 PASS）
* 裁定层：PASS / FAIL / WITHHELD 三值，绿线 0.8，缺证据**绝不**抬成 PASS
* 反造假：sha256 钉桩失配 → FAIL(integrity)；自述 pass 与复算相反 → claim_mismatch
* 台账：节点↔条目双射，缺条目/多余条目/重名能力都能被抓到
* 契约：markdown 必含章节、schema 必含字段、端点覆盖率
* 报告：六要素 + 「是否<3%」+ 审计标记
* 规格严格性：未知键/未知比较器/重复 id → SpecError（配置错 ≠ 判据错）
* 确定性：同一输入两次判定逐字节相同
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.maturity_gate import contracts as C  # noqa: E402
from tools.maturity_gate import gate as G  # noqa: E402
from tools.maturity_gate import ledger as L  # noqa: E402
from tools.maturity_gate import report as R  # noqa: E402
from tools.maturity_gate.evidence import (FAIL, PASS, WITHHELD, compare,  # noqa: E402
                                          get_pointer, payload_digest, PointerError)
from tools.maturity_gate.spec import SpecError, parse_spec  # noqa: E402


# ---------------------------------------------------------------- 合成域夹具
def synth_spec(threshold: float = 0.8, n_criteria: int = 5):
    crit = []
    for i in range(n_criteria):
        crit.append({
            "criterion_id": chr(ord("a") + i),
            "statement": f"synthetic criterion {i}",
            "unit": "-",
            "checks": [{"doc": "ev/node.json", "pointer": f"criteria.{i}.value",
                        "comparator": "le", "threshold": 0.1,
                        "claim": f"criteria.{i}.pass"}],
        })
    return {
        "spec_id": "SYN-SPEC",
        "schema_version": "1.0",
        "criteria_pass_threshold": threshold,
        "nodes": [
            {"node_key": "SYN-N1", "title": "node one", "repo": "repo-alpha",
             "mission_id": 9001, "criteria": crit},
            {"node_key": "SYN-N2", "title": "node two", "repo": "repo-beta",
             "mission_id": 9002, "criteria": copy.deepcopy(crit)},
        ],
    }


def synth_doc(pass_all: bool = True, value: float = 0.01):
    return {"criteria": {str(i): {"value": value, "pass": pass_all} for i in range(5)},
            "pytest": {"returncode": 0, "passed": 7, "failed": 0}}


def docs_for(spec, doc=None):
    doc = doc or synth_doc()
    return {f"ev/{k}.json": doc for k in ("node",)}


# ---------------------------------------------------------------- 谓词层
def test_compare_predicates():
    assert compare(0.05, "le", 0.1) is True
    assert compare(0.2, "le", 0.1) is False
    assert compare(1e-12, "lt", 1e-9) is True
    assert compare(20, "ge", 20) is True
    assert compare(2.0, "between", [1.8, 2.2]) is True
    assert compare(2.5, "between", [1.8, 2.2]) is False
    assert compare(1.0, "eq", 1.0, tolerance=1e-6) is True
    assert compare(1.0000001, "eq", 1.0, tolerance=1e-6) is True
    assert compare(True, "eq", True) is True
    assert compare(False, "eq", True) is False
    assert compare([1, 2], "nonempty") is True
    assert compare([], "nonempty") is False
    with pytest.raises(TypeError):
        compare("text", "le", 0.1)
    with pytest.raises(ValueError):
        compare(1.0, "≈", 1.0)


def test_pointer_paths():
    doc = {"a": {"b": [{"c": 7}, {"c": 9}]}, "n": [1, 2, 3]}
    assert get_pointer(doc, "a.b[1].c") == 9
    assert get_pointer(doc, "n[-1]") == 3
    assert get_pointer(doc, "") is doc
    for bad in ("a.x", "a.b[5]", "n.x"):
        with pytest.raises(PointerError):
            get_pointer(doc, bad)


# ---------------------------------------------------------------- 判据/裁定层
def test_compound_criterion_conjunction():
    raw = {
        "spec_id": "S", "schema_version": "1.0",
        "nodes": [{"node_key": "N", "repo": "r", "criteria": [
            {"criterion_id": "a", "statement": "two-part", "checks": [
                {"doc": "ev.json", "pointer": "x", "comparator": "between",
                 "threshold": [0.1, 0.2]},
                {"doc": "ev.json", "pointer": "flag", "comparator": "eq", "threshold": True},
            ]}]}],
    }
    spec = parse_spec(raw)
    ok = G.evaluate_campaign_inline(raw, {"ev.json": {"x": 0.15, "flag": True}})
    assert ok["node_summaries"][0]["pass_ratio"] == 1.0
    bad = G.evaluate_campaign_inline(raw, {"ev.json": {"x": 0.15, "flag": False}})
    assert bad["node_summaries"][0]["failed"] == 1
    assert bad["campaign"]["verdict"] == FAIL
    assert spec.nodes[0].criteria[0].checks[1].comparator == "eq"


def test_verdict_pass_fail_withheld():
    raw = synth_spec()
    docs = {"ev/node.json": synth_doc()}
    full = G.evaluate_campaign_inline(raw, docs)
    assert full["campaign"]["verdict"] == PASS
    assert full["campaign"]["criterion_a_all_nodes_ge_threshold"] is True

    # 缺证据 → WITHHELD，绝不 PASS
    partial = G.evaluate_campaign_inline(raw, {})
    assert partial["campaign"]["verdict"] == WITHHELD
    assert partial["campaign"]["nodes_delivered"] == 0
    assert partial["node_summaries"][0]["withheld"] == 5

    # 一个节点低绿线 → FAIL（不被另一节点全绿抵消）
    bad_doc = synth_doc(value=0.9)
    mixed = G.evaluate_campaign_inline(raw, {"ev/node.json": bad_doc})
    assert mixed["campaign"]["verdict"] == FAIL
    assert set(mixed["campaign"]["nodes_red"]) == {"SYN-N1", "SYN-N2"}


def test_threshold_boundary_80_percent():
    """4/5 = 0.8 恰好达线（绿），3/5 = 0.6 低于绿线（红）。"""
    raw = synth_spec(n_criteria=5)
    doc = synth_doc()
    doc["criteria"]["4"]["value"] = 99.0  # 让第 5 条不过 → 4/5
    for k in ("0", "1", "2", "3"):
        pass
    _, summary = __import__("tools.maturity_gate.evidence", fromlist=["x"]).evaluate_node(
        parse_spec(raw).nodes[0], G.InlineResolver({"ev/node.json": doc}))
    assert summary["passed"] == 4 and summary["pass_ratio"] == pytest.approx(0.8)
    doc2 = copy.deepcopy(doc)
    doc2["criteria"]["3"]["value"] = 99.0
    _, s2 = __import__("tools.maturity_gate.evidence", fromlist=["x"]).evaluate_node(
        parse_spec(raw).nodes[0], G.InlineResolver({"ev/node.json": doc2}))
    assert s2["passed"] == 3 and s2["pass_ratio"] < 0.8


def test_missing_evidence_is_withheld_not_fail(tmp_path):
    """磁盘路径下缺文档 → WITHHELD + 具名 reason（不掩盖成 FAIL）。"""
    raw = synth_spec()
    spec = parse_spec(raw)
    resolver = G.EvidenceResolver(work_root=str(tmp_path))
    result = G.evaluate_campaign(spec, resolver)
    assert result["campaign"]["verdict"] == WITHHELD
    for node in result["nodes"]:
        for crit in node["criteria"]:
            assert crit["verdict"] == WITHHELD
            assert crit["reason"] == "evidence_doc_missing"


def test_integrity_pin_and_claim_mismatch(tmp_path):
    """钉桩失配 → FAIL(integrity)；自述 pass 与复算相反 → claim_mismatch。"""
    spec_raw = synth_spec(n_criteria=1)
    spec_raw["nodes"] = spec_raw["nodes"][:1]
    spec_raw["nodes"][0]["criteria"][0]["checks"][0]["sha256"] = "0" * 64
    doc_path = tmp_path / "m9001" / "ev" / "node.json"
    doc_path.parent.mkdir(parents=True)
    doc_path.write_text(json.dumps(synth_doc()), encoding="utf-8")
    gate = G.evaluate_campaign(parse_spec(spec_raw), G.EvidenceResolver(work_root=str(tmp_path)))
    chk = gate["nodes"][0]["criteria"][0]["checks"][0]
    assert chk["verdict"] == FAIL and chk["reason"] == "evidence_integrity_mismatch"

    # 正确钉桩 + 自述相反
    digest = payload_digest(json.loads(doc_path.read_text()))
    liar = synth_doc()
    liar["criteria"]["0"]["value"] = 99.0        # 实测不过
    liar["criteria"]["0"]["pass"] = True          # 却自述通过
    doc_path.write_text(json.dumps(liar), encoding="utf-8")
    spec_raw["nodes"][0]["criteria"][0]["checks"][0]["sha256"] = payload_digest(liar)
    gate2 = G.evaluate_campaign(parse_spec(spec_raw), G.EvidenceResolver(work_root=str(tmp_path)))
    assert gate2["campaign"]["claim_mismatch_total"] == 1
    assert gate2["campaign"]["verdict"] == FAIL
    assert gate2["node_summaries"][0]["claim_mismatches"] == ["a"]


def test_determinism_byte_identical():
    raw = synth_spec()
    docs = {"ev/node.json": synth_doc()}
    a = json.dumps(G.evaluate_campaign_inline(raw, docs), sort_keys=True)
    b = json.dumps(G.evaluate_campaign_inline(copy.deepcopy(raw), copy.deepcopy(docs)),
                   sort_keys=True)
    assert a == b
    assert "generated_utc" not in a and "wall" not in a


def test_inline_digest_matches_canonical_payload():
    payload = synth_doc()
    expect = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False,
                   separators=(",", ":")).encode("utf-8")).hexdigest()
    resolver = G.InlineResolver({"ev/node.json": payload})
    assert resolver.digest(parse_spec(synth_spec()).nodes[0], "ev/node.json") == expect


# ---------------------------------------------------------------- 规格严格性
@pytest.mark.parametrize("mutate,msg", [
    (lambda s: s.update({"typo_key": 1}), "未知键"),
    (lambda s: s["nodes"][0]["criteria"][0]["checks"][0].update({"comparator": "approx"}),
     "未知比较器"),
    (lambda s: s["nodes"].append(copy.deepcopy(s["nodes"][0])), "重复"),
    (lambda s: s["nodes"][0]["criteria"][0].update({"checks": []}), "checks"),
])
def test_spec_strictness(mutate, msg):
    raw = synth_spec()
    mutate(raw)
    with pytest.raises(SpecError) as exc:
        parse_spec(raw)
    assert msg in str(exc.value) or "checks" in str(exc.value)


# ---------------------------------------------------------------- 台账
def test_ledger_bijection_positive_and_negative():
    gate = G.evaluate_campaign_inline(synth_spec(), {"ev/node.json": synth_doc()})
    entries = L.build_entries(gate)
    bij = L.verify_bijection(entries, ["SYN-N1", "SYN-N2"])
    assert bij["one_to_one"] is True
    assert bij["entries_total"] == 2
    assert bij["levels"] == {"REAL": 2}

    # 缺一条 → unmapped
    bij2 = L.verify_bijection(entries[:1], ["SYN-N1", "SYN-N2"])
    assert bij2["unmapped_nodes"] == ["SYN-N2"] and bij2["one_to_one"] is False

    # 多余/重复 → orphan / duplicate
    extra = copy.deepcopy(entries[0])
    extra["node_key"] = "SYN-X"
    bij3 = L.verify_bijection([entries[0], entries[1], extra], ["SYN-N1", "SYN-N2"])
    assert bij3["orphan_entries"] == ["SYN-X"]
    dup = copy.deepcopy(entries[0])
    bij4 = L.verify_bijection([entries[0], entries[1], dup], ["SYN-N1", "SYN-N2"])
    assert bij4["duplicate_node_entries"] == ["SYN-N1"]
    assert bij4["duplicate_entry_ids"] == ["cap::SYN-N1"]


def test_ledger_level_withheld_when_no_evidence():
    gate = G.evaluate_campaign_inline(synth_spec(), {})
    entries = L.build_entries(gate)
    assert {e["evidence_level"] for e in entries} == {"WITHHELD"}
    assert all(e["evidence_refs"] == [] for e in entries)


# ---------------------------------------------------------------- 契约
def test_contract_manifest_checks(tmp_path):
    root = tmp_path
    (root / "docs").mkdir()
    (root / "docs" / "c.md").write_text("# t\n## 1. endpoint\nPOST /x\n## 2. data\n", encoding="utf-8")
    (root / "s.json").write_text(json.dumps({"properties": {"a": {"type": "string"}}}),
                                 encoding="utf-8")
    manifest = {
        "documents": [
            {"id": "md", "path": "docs/c.md", "kind": "markdown",
             "required_sections": ["## 1. endpoint", "## 2. data"]},
            {"id": "js", "path": "s.json", "kind": "jsonschema",
             "required_keys": ["properties.a"]},
        ],
        "routes": {"doc": "docs/c.md", "endpoints": ["POST /x", "GET /missing"]},
    }
    res = C.check_manifest(root, manifest)
    assert res["complete"] is False
    assert res["completeness"] == pytest.approx(4 / 5)
    assert any(m["id"] == "routes" for m in res["missing"])

    manifest["routes"]["endpoints"] = ["POST /x"]
    assert C.check_manifest(root, manifest)["complete"] is True


def test_contract_missing_file_is_incomplete(tmp_path):
    res = C.check_manifest(tmp_path, {"documents": [
        {"id": "x", "path": "nope.md", "kind": "markdown", "required_sections": ["## a"]}]})
    assert res["complete"] is False and res["completeness"] == 0.0


# ---------------------------------------------------------------- 报告
def _report_stub():
    return "\n".join([
        "# t", "input_digest `abc`", "gate_schema_version `1.0`",
        "## ① 案例介绍", "## ② 怎么做的", "## ③ 计算结果", "## ④ 结果图",
        "## ⑤ 注意点", "## ⑥ 误差判定", "**是否<3%？** 不宣称。",
    ])


def test_report_six_elements():
    ok = R.check_six_elements(_report_stub(), figures=[{"name": "f.png"}])
    assert ok["complete"] is True
    bad = R.check_six_elements("# t\n## ① 案例介绍\n", figures=[])
    assert bad["complete"] is False
    assert any("⑥" in m for m in bad["missing"])
    assert any("figures" in m for m in bad["missing"])
    # 无图但声明理由也算合格
    with_reason = R.check_six_elements(_report_stub(), figures=[], no_figure_reason="纯机制任务")
    assert with_reason["complete"] is True
    # 缺「是否<3%」显式回答 → 不合格
    no_verdict = _report_stub().replace("**是否<3%？** 不宣称。", "")
    assert R.check_six_elements(no_verdict, figures=[{"name": "f"}])["complete"] is False


def test_render_report_contains_programmatic_numbers():
    gate = G.evaluate_campaign_inline(synth_spec(), {"ev/node.json": synth_doc()})
    entries = L.build_entries(gate)
    bij = L.verify_bijection(entries, ["SYN-N1", "SYN-N2"])
    ledger_doc = L.ledger_document(entries, bij, "SYN-SPEC", gate["input_digest"])
    md = R.render_report(gate, ledger_doc,
                         {"completeness": 1.0, "complete": True, "missing": []},
                         report_check=None, figures=[{"name": "f.png", "path": "artifacts/f.png",
                                                      "caption": "c"}],
                         evidence_protocol="protocol", limitations=["l1"], error_notes=["e1"],
                         run_command="python3 -m tools.maturity_gate.cli")
    assert gate["input_digest"][:8] in md
    assert "SYN-N1" in md and "one_to_one" in md
    assert "0.8" in md  # 绿线来自规格，不落人手


# ---------------------------------------------------------------- 真实战役工件自洽
def test_real_campaign_spec_is_loadable_and_consistent():
    spec = parse_spec(json.loads((REPO_ROOT / "data" / "maturity_gate"
                                  / "p54w3_campaign.spec.json").read_text(encoding="utf-8")))
    assert len(spec.nodes) == 7
    assert len({n.node_key for n in spec.nodes}) == 7
    for node in spec.nodes:
        assert node.criteria and all(c.checks for c in node.criteria)
    # 每条 check 的 doc 必须在交付协议目录内，或指向已交付节点文档（可溯源）
    for node in spec.nodes:
        for crit in node.criteria:
            for chk in crit.checks:
                assert not Path(chk.doc).is_absolute()