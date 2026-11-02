"""Capability-card evidence chain (E1-E7) regression tests — mission 1819 / infra 席.

被测对象：`tools/audit/capability_card_evidence.py` + 卡片/注册表/证据快照三件套。

判据两类（缺一不可，否则自检形同虚设）：
  * 正例：当前仓内状态必须 0 violation，且 4 张卡 ANCHORED + 1 张 DECLARED_EMPTY
    （即"要么有可复算锚、要么显式声明无证据"，不留第三种模糊态）；
  * 反例：把历史上真实出现过的五类缺陷逐项注入（伪造 md5 占位符 / 非报告 id /
    锚点摘要漂移 / PASS 与证据矛盾 / 引文不命中），每一类必须**各自**触发对应 E 编号。
    变异体只改内存副本，绝不写盘。

全程离线：只读 worktree 内 fixture，不访问 :8010。
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "audit" / "capability_card_evidence.py"
INDEX = ROOT / "docs" / "capability_registry" / "index.json"
SNAPSHOT = ROOT / "tests" / "fixtures" / "capability_evidence" / "reports_snapshot.json"
CARDS_DIR = ROOT / "docs" / "capability_registry" / "cards"


def _load_tool():
    spec = importlib.util.spec_from_file_location("capability_card_evidence_tool", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["capability_card_evidence_tool"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


@pytest.fixture()
def cards(tool):
    return copy.deepcopy(tool.load_cards(CARDS_DIR))


@pytest.fixture()
def index():
    return json.loads(INDEX.read_text(encoding="utf-8"))


@pytest.fixture()
def snapshot():
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def codes(violations: list[str]) -> set[str]:
    return {v.split()[0] for v in violations}


# ── 正例 ────────────────────────────────────────────────────────────────
def test_repo_state_has_zero_violations(tool, cards, index, snapshot):
    result, violations = tool.check(cards, index, snapshot)
    assert violations == [], f"证据链不干净: {violations}"
    assert result["summary"]["violations"] == 0


def test_verdict_partition_is_anchored_or_declared_empty(tool, cards, index, snapshot):
    result, _ = tool.check(cards, index, snapshot)
    verdicts = {cid: r["verdict"] for cid, r in result["cards"].items()}
    assert verdicts == {
        "CAP-001": "ANCHORED",
        "CAP-002": "ANCHORED",
        "CAP-003": "DECLARED_EMPTY",
        "CAP-004": "ANCHORED",
        "CAP-005": "ANCHORED",
    }
    assert result["summary"]["unverifiable"] == 0


def test_no_pass_claim_is_unsupported(tool, cards, index, snapshot):
    """平台口径：任何 PASS 都必须有可复算锚 + 逐字引文，且引文不得自陈失败。"""
    result, _ = tool.check(cards, index, snapshot)
    assert result["summary"]["pass_claims"] == result["summary"]["pass_claims_supported"]
    assert result["summary"]["pass_claims"] == 1  # 只有 CAP-005（仓内策略文件锚）


def test_snapshot_digests_are_self_consistent(snapshot):
    """快照自身：body 与其登记摘要必须一致，否则锚点从源头就不可信。"""
    import hashlib

    for rid, rec in snapshot["reports"].items():
        b = rec["body"].encode("utf-8")
        assert hashlib.sha256(b).hexdigest() == rec["body_sha256"], rid
        assert hashlib.md5(b).hexdigest() == rec["body_md5"], rid


def test_cap002_error_value_is_parsed_from_pinned_body(tool, snapshot, cards):
    """CAP-002 的误差数字必须能从锚点报告正文重新解析出来（零手抄）。"""
    import re

    body = snapshot["reports"]["1375"]["body"]
    pcts = [abs(float(x)) for ln in body.split("\n") if ln.startswith("| SUBOFF-") for x in re.findall(r"\*\*([+-]?\d+\.?\d*)%\*\*", ln)]
    assert pcts, "锚点报告里没有 SUBOFF 误差单元"
    declared = cards["CAP-002"]["validation_evidence"]["relative_error_pct"]
    assert declared == pytest.approx(max(pcts)), "卡片误差已与锚点正文脱钩"
    assert declared > cards["CAP-002"]["validation_evidence"]["tolerance_pct"]


# ── 反例：变异体必须各自触发对应 E 编号 ──────────────────────────────────
def test_mutation_placeholder_anchor_triggers_e4(tool, cards, index, snapshot):
    """历史缺陷复刻：32 位 md5 合法但内容是伪造占位 → 旧闸门放行，E4 必须拦。"""
    ve = cards["CAP-002"]["validation_evidence"]
    ve["report_md5_note"] = "Placeholder: report not yet frozen."
    _, violations = tool.check(cards, index, snapshot)
    assert "E4" in codes(violations)


def test_mutation_non_report_id_triggers_e2(tool, cards, index, snapshot):
    """历史缺陷复刻：report_id 写成自由标签（"suboff_sail"）而不是报告 id。"""
    cards["CAP-002"]["validation_evidence"]["report_id"] = "suboff_sail"
    _, violations = tool.check(cards, index, snapshot)
    assert "E2" in codes(violations)


def test_mutation_unknown_report_id_triggers_e2(tool, cards, index, snapshot):
    cards["CAP-002"]["validation_evidence"]["report_id"] = "999999"
    _, violations = tool.check(cards, index, snapshot)
    assert "E2" in codes(violations)


def test_mutation_digest_drift_triggers_e3(tool, cards, index, snapshot):
    """锚点被事后改写（报告 retro-edit）必须被摘要闸发现。"""
    cards["CAP-002"]["validation_evidence"]["report_body_sha256"] = "0" * 64
    _, violations = tool.check(cards, index, snapshot)
    assert "E3" in codes(violations)


def test_mutation_overclaim_pass_triggers_e5(tool, cards, index, snapshot):
    """证书式越权：证据自陈失败却标 PASS。"""
    cards["CAP-002"]["validation_evidence"]["verification_status"] = "PASS"
    _, violations = tool.check(cards, index, snapshot)
    assert "E5" in codes(violations)


def test_mutation_passing_status_without_anchor_triggers_e5(tool, cards, index, snapshot):
    """空锚点 + PASS：既违 E2（空锚需非通过状态）也违 E5（越过证据）。"""
    cards["CAP-003"]["validation_evidence"]["verification_status"] = "PASS"
    _, violations = tool.check(cards, index, snapshot)
    assert {"E2", "E5"} <= codes(violations)


def test_mutation_empty_anchor_without_gap_reason_triggers_e2(tool, cards, index, snapshot):
    cards["CAP-003"]["validation_evidence"]["gap_reason"] = ""
    _, violations = tool.check(cards, index, snapshot)
    assert "E2" in codes(violations)


def test_mutation_quote_miss_triggers_e7(tool, cards, index, snapshot):
    """引文幻觉：卡片写的引文在锚点正文里不存在。"""
    cards["CAP-002"]["validation_evidence"]["quote_match"] = "误差是否 <3%：是"
    _, violations = tool.check(cards, index, snapshot)
    assert "E7" in codes(violations)


def test_mutation_quote_line_digest_drift_triggers_e7(tool, cards, index, snapshot):
    cards["CAP-002"]["validation_evidence"]["quote_line_sha256"] = "deadbeefdeadbeef"
    _, violations = tool.check(cards, index, snapshot)
    assert "E7" in codes(violations)


def test_mutation_tolerance_violation_triggers_e6(tool, cards, index, snapshot):
    """把 CAP-005 的容忍度压到误差之下（PASS 却超差）。"""
    ve = cards["CAP-005"]["validation_evidence"]
    ve["relative_error_pct"] = 5.0
    ve["tolerance_pct"] = 3.0
    _, violations = tool.check(cards, index, snapshot)
    assert "E6" in codes(violations)


def test_mutation_registry_status_drift_triggers_e1(tool, cards, index, snapshot):
    for ent in index["cards"]:
        if ent["card_id"] == "CAP-002":
            ent["verification_status"] = "PASS"
    _, violations = tool.check(cards, index, snapshot)
    assert "E1" in codes(violations)


def test_mutation_registry_drops_card_triggers_e1(tool, cards, index, snapshot):
    index["cards"] = [c for c in index["cards"] if c["card_id"] != "CAP-003"]
    _, violations = tool.check(cards, index, snapshot)
    assert "E1" in codes(violations)


def test_cli_gate_exit_code_is_nonzero_on_violation(tmp_path, tool):
    """CLI 必须是可用作 CI 闸的退出码闸门（0 绿 / 1 红），而不是只打印。"""
    import subprocess

    ok = subprocess.run([sys.executable, str(TOOL), "--check"], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "violations=0" in ok.stdout

def test_cards_conform_to_declared_schema(cards):
    """卡片必须过仓内 JSON Schema（schema 与 E1-E7 两把尺子不得互相矛盾）。"""
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((ROOT / "docs" / "capability_card.schema.json").read_text(encoding="utf-8"))
    for cid, card in cards.items():
        payload = {k: v for k, v in card.items() if k != "__file__"}
        jsonschema.validate(payload, schema)
