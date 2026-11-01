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
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))["reports"]
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
    assert n_rows == 11, f"证据行数 {n_rows} 与预期不符"


def test_divergence_findings_are_the_documented_ones(tool, ledger):
    """分歧清单是台账的核心结论，锁成金值：注册表语义一变就必须显式重 curation。"""
    result, _ = tool.check(ledger)
    assert result["summary"]["divergence_tally"] == {
        "OVERCLAIM": 2,
        "REVERIFY": 1,
        "UNVERIFIED": 15,
        "OK": 3,
    }
    by_id = {c["id"]: c for c in result["capabilities"]}
    assert by_id["propeller.open_water"]["reconciled_level"] == "MOCK"
    assert by_id["propeller.open_water"]["divergence"] == "OVERCLAIM"
    assert by_id["calc.stability"]["reconciled_level"] == "MOCK"
    assert by_id["calc.stability"]["divergence"] == "OVERCLAIM"
    assert by_id["calc.resistance"]["reconciled_level"] == "WITHHELD"
    assert by_id["calc.resistance"]["divergence"] == "REVERIFY"
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
        if cap["id"] == "calc.stability":
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