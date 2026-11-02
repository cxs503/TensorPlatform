"""能力证据台账对账器回归测试（mission 1567，data 席）。

被测对象：tools/audit/capability_ledger.py + artifacts/capability_ledger/capability_ledger.json
判据两类：
  * 正例：台账自身过 C1~C7，且对账结果与已入库快照一致（漂移闸）；
  * 反例（变异体判负）：把台账/快照/语料逐项注入缺陷，必须**各自**触发对应 C 编号，
    否则说明该条自检恒真（形同虚设）。
全程离线：只读工作树内的 fixture，不访问 :8010/:8001。
"""
from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "audit" / "capability_ledger.py"
LEDGER = ROOT / "artifacts" / "capability_ledger" / "capability_ledger.json"
CAP_SNAPSHOT = ROOT / "tests" / "fixtures" / "capability_ledger" / "capabilities_snapshot.json"
CORPUS = ROOT / "tests" / "fixtures" / "capability_ledger" / "reports_corpus.json"


def _load_tool():
    spec = importlib.util.spec_from_file_location("capability_ledger_tool", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["capability_ledger_tool"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


@pytest.fixture()
def ledger():
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def _violation_codes(violations: list[str]) -> set[str]:
    return {v.split()[0] for v in violations}


# ── 正例 ────────────────────────────────────────────────────────────────
def test_ledger_passes_all_selfchecks(tool, ledger):
    result, violations = tool.check(ledger)
    assert violations == [], f"台账自检不通过: {violations}"
    assert result["summary"]["violations"] == 0


def test_snapshot_coverage_is_exact(tool, ledger):
    snap = json.loads(CAP_SNAPSHOT.read_text(encoding="utf-8"))
    assert snap["capability_count"] == len(snap["capabilities"]) == 21
    ledger_ids = [c["id"] for c in ledger["capabilities"]]
    assert sorted(ledger_ids) == sorted(snap["capabilities"])


def test_every_evidence_quote_is_verbatim(tool, ledger):
    """证据必须逐字可查：命中行非空、sha256 前缀长度固定、且 report_id 在语料库内。"""
    raw = json.loads(CORPUS.read_text(encoding="utf-8"))
    corpus = {**raw["reports"], **{k: v for k, v in raw.get("docs", {}).items()}}
    n_rows = 0
    for cap in ledger["capabilities"]:
        for ev in cap["evidence"]:
            md = corpus[str(ev["report_id"])]["markdown"]
            q = tool.resolve_quote(md, ev["quote_match"])
            assert q["hit"], f"{cap['id']} 证据 {ev['report_id']} quote 未命中"
            assert q["line"] and len(q["line_sha256"]) == 16
            assert ev["asserted_level"] in ledger["level_rank"]
            assert ev["stance"] in {"contradicts", "conditional", "supports_real"}
            n_rows += 1
    for gap in ledger["unregistered_domains"]:
        for ev in gap["evidence"]:
            assert tool.resolve_quote(corpus[str(ev["report_id"])]["markdown"], ev["quote_match"])["hit"]
            n_rows += 1
    assert n_rows == 16, f"证据行数 {n_rows} 与预期不符"


def test_divergence_findings_are_the_documented_ones(tool, ledger):
    """分歧清单是台账的核心结论，锁成金值：注册表语义一变就必须显式重 curation。"""
    result, _ = tool.check(ledger)
    assert result["summary"]["divergence_tally"] == {
        "OVERCLAIM": 2,
        "REVERIFY": 0,
        "UNVERIFIED": 15,
        "OK": 4,
    }
    by_id = {c["id"]: c for c in result["capabilities"]}
    # v2 重裁（m1811）：敞水仍 MOCK（m1584 硬判据），稳性升 REAL（m1564 受证合成），
    # 阻力由 REVERIFY 升格为 OVERCLAIM（inline 审计 FAIL 8/10，绝对精度不可判据）。
    assert by_id["propeller.open_water"]["reconciled_level"] == "MOCK"
    assert by_id["propeller.open_water"]["divergence"] == "OVERCLAIM"
    assert by_id["calc.stability"]["reconciled_level"] == "REAL"
    assert by_id["calc.stability"]["divergence"] == "OK"
    assert by_id["calc.resistance"]["reconciled_level"] == "WITHHELD"
    assert by_id["calc.resistance"]["divergence"] == "OVERCLAIM"
    # 无证据不得被抬成 REAL
    for c in result["capabilities"]:
        if c["evidence_count"] == 0:
            assert c["reconciled_level"] == "WITHHELD"
            assert c["divergence"] in ("UNVERIFIED", "OK")
    # 覆盖缺口三项必须在
    assert {g["domain"].split("/")[0] for g in result["unregistered_domains"]} == {
        "耐波",
        "绿色航行",
        "基准参照数据",
        "结构",
        "振动噪声",
    }


def test_result_matches_committed_snapshot(tool, ledger):
    """漂移闸：本次复算 == artifacts 里已入库的对账快照。"""
    result, _ = tool.check(ledger)
    stored = json.loads(tool.RESOLVED.read_text(encoding="utf-8"))
    canon = lambda d: json.dumps({k: d[k] for k in ("summary", "capabilities", "unregistered_domains")},
                                 ensure_ascii=False, sort_keys=True)
    assert canon(stored) == canon(result)


def test_check_is_deterministic(tool, ledger):
    a, _ = tool.check(copy.deepcopy(ledger))
    b, _ = tool.check(copy.deepcopy(ledger))
    assert json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def test_cli_exit_zero_offline():
    proc = subprocess.run([sys.executable, str(TOOL), "--check"], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "PASS" in proc.stdout


# ── 反例（变异体判负） ──────────────────────────────────────────────────
def test_mutant_broken_quote_trips_c3(tool, ledger, monkeypatch):
    bad = copy.deepcopy(ledger)
    bad["capabilities"][0]["evidence"][0]["quote_match"] = "这句话报告里根本不存在ZZZ"
    _, violations = tool.check(bad)
    assert "C3" in _violation_codes(violations)


def test_mutant_unknown_report_trips_c2(tool, ledger, monkeypatch):
    bad = copy.deepcopy(ledger)
    bad["capabilities"][0]["evidence"][0]["report_id"] = 999999
    _, violations = tool.check(bad)
    assert "C2" in _violation_codes(violations)


def test_mutant_missing_capability_trips_c1(tool, ledger):
    bad = copy.deepcopy(ledger)
    bad["capabilities"] = [c for c in bad["capabilities"] if c["id"] != "misc.stubs"]
    _, violations = tool.check(bad)
    assert "C1" in _violation_codes(violations)


def test_mutant_claim_flip_trips_c5(tool, ledger):
    """注册表把 MOCK 报成 REAL（或反过来）必须被 C5 抓住。"""
    bad = copy.deepcopy(ledger)
    for cap in bad["capabilities"]:
        if cap["id"] == "propeller.open_water":
            cap["claimed_level"] = "MOCK"
    _, violations = tool.check(bad)
    assert "C5" in _violation_codes(violations)


def test_mutant_divergence_without_action_trips_c4(tool, ledger):
    bad = copy.deepcopy(ledger)
    for cap in bad["capabilities"]:
        if cap["id"] == "calc.resistance":  # v2 后 calc.stability 已裁决为 OK（无需 action）
            cap.pop("action", None)
    _, violations = tool.check(bad)
    assert "C4" in _violation_codes(violations)


def test_mutant_gap_quote_trips_c6(tool, ledger):
    bad = copy.deepcopy(ledger)
    bad["unregistered_domains"][0]["evidence"][0]["quote_match"] = "不存在的域判决ZZZ"
    _, violations = tool.check(bad)
    assert "C6" in _violation_codes(violations)


def test_mutant_stale_snapshot_trips_c7(tool, ledger, tmp_path, monkeypatch):
    """已入库快照与复算不一致（=对账结论漂移）必须被 C7 拦下。"""
    stale = tmp_path / "resolved.json"
    stale.write_text(json.dumps({"summary": {"capabilities": 0}, "capabilities": [], "unregistered_domains": []}),
                     encoding="utf-8")
    monkeypatch.setattr(tool, "RESOLVED", stale)
    _, violations = tool.check(ledger)
    assert "C7" in _violation_codes(violations)


def test_mutant_cli_exit_one_on_bad_ledger(tool, tmp_path, monkeypatch):
    bad = json.loads(LEDGER.read_text(encoding="utf-8"))
    bad["capabilities"][0]["evidence"][0]["quote_match"] = "不存在ZZZ"
    p = tmp_path / "ledger_bad.json"
    p.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(tool, "LEDGER", p)
    assert tool.main(["--check"]) == 1

# ── v2 新增：防腐朽三判据（C8/C9/C10） ──────────────────────────────────
def test_corpus_docs_and_ledger_are_reproducible():
    """语料库 docs 段与台账 curation 段必须由仓内脚本可复现（防手改漂移）。"""
    for script, args in (
        ("tools/audit/build_capability_corpus_docs.py", ["--check"]),
        ("tools/audit/curate_capability_ledger_m1811.py", ["--check"]),
    ):
        proc = subprocess.run([sys.executable, str(ROOT / script), *args],
                              capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, f"{script} 产物与仓内不一致: {proc.stdout}{proc.stderr}"


def test_every_must_consume_doc_is_cited(tool, ledger):
    """已合并复核文档必须全部入账（否则台账再次对已合并工作失明）。"""
    result, violations = tool.check(ledger)
    assert violations == []
    cited = {str(e["report_id"]) for c in ledger["capabilities"] for e in c["evidence"]}
    cited |= {str(e["report_id"]) for g in ledger["unregistered_domains"] for e in g["evidence"]}
    must = ledger["curation"]["must_consume"]
    assert len(must) >= 5
    for item in must:
        assert not item.get("waiver"), item["path"]
        assert "doc:" + item["path"] in cited, item["path"]


def test_every_doc_evidence_has_provenance(tool, ledger):
    """文档证据必须带 path + sha256 + blob，且 sha256 与工作树正文一致（C10 正例）。"""
    raw = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert raw["docs"], "corpus 里没有 docs 段"
    for rid, rec in raw["docs"].items():
        assert rid.startswith("doc:")
        assert rec["sha256"] and rec["blob"] and rec["as_of"]
        import hashlib
        assert hashlib.sha256((ROOT / rec["path"]).read_bytes()).hexdigest() == rec["sha256"]


def test_mutant_stale_evidence_trips_c8(tool, ledger):
    """证据过期（把容忍窗口压到 1 天）必须被 C8 拦下，而不是静默沿用旧判决。"""
    bad = copy.deepcopy(ledger)
    bad["curation"]["max_evidence_age_days"] = 1
    bad["curation"]["as_of"] = "2026-12-31T00:00:00+00:00"
    _, violations = tool.check(bad)
    assert "C8" in _violation_codes(violations)


def test_mutant_unconsumed_doc_trips_c9(tool, ledger):
    """把一篇从未被引用的复核文档写进 must_consume → C9 必须报违规。"""
    bad = copy.deepcopy(ledger)
    bad["curation"]["must_consume"].append(
        {"path": "docs/reports/m1590_structure_verification_debt.md", "why": "已合并但假设未引用"})
    bad["curation"]["must_consume"][-1]["path"] = "docs/papers/PAPER_TRACEABILITY_GATE.md"
    _, violations = tool.check(bad)
    assert "C9" in _violation_codes(violations)


def test_mutant_doc_sha_mismatch_trips_c10(tool, ledger, tmp_path, monkeypatch):
    """语料库里的文档 sha256 被篡改（=引文可能已失效）必须被 C10 拦下。"""
    raw = json.loads(CORPUS.read_text(encoding="utf-8"))
    raw["docs"][sorted(raw["docs"])[0]]["sha256"] = "0" * 64
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(tool, "CORPUS", p)
    _, violations = tool.check(ledger)
    assert "C10" in _violation_codes(violations)
