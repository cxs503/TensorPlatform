"""议会宪政化（IN27-H5）机器判据测试：程序约束不靠自述，靠红/绿。

覆盖：宪法溯源、预登记冻结与时序、红线分类、族级票决与超级多数、裁决路由、
审计链防篡改、回放比对、真实工件验收。运行：

    python3 -m pytest tests/test_council_constitution.py -q
"""
from __future__ import annotations

import json
import sys
from fractions import Fraction
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from services.council_constitution import (  # noqa: E402
    Adjudicator,
    Ballot,
    Charter,
    Conclave,
    ConstitutionError,
    Criterion,
    Ledger,
    Preregistration,
    PreregistrationError,
    RedLineClassifier,
    acceptance_summary,
    compare_with_owner,
    load_charter,
    load_redline_lexicon,
    sha256_obj,
)

CHARTER_PATH = ROOT / "data/governance/charter_manifest.json"
LEXICON_PATH = ROOT / "data/governance/redline_lexicon.json"
CASES_PATH = ROOT / "data/governance/replay_cases.json"
RESULT_PATH = ROOT / "artifacts/in27_h5/replay_result.json"

FROZEN_AT = "2026-11-06T20:00:00Z"
AFTER = "2026-11-07T09:00:00Z"


@pytest.fixture(scope="module")
def charter() -> Charter:
    return load_charter(CHARTER_PATH)


@pytest.fixture(scope="module")
def classifier() -> RedLineClassifier:
    return load_redline_lexicon(LEXICON_PATH)


def _ballot(family: str, verdict: str, cast_at: str = AFTER) -> Ballot:
    return Ballot(family_id=family, model_id=f"{family}-m", verdict=verdict,
                  rationale=f"{family} 依判据与事实得出 {verdict}（测试票，理由非空即可审计）", cast_at=cast_at)


# --------------------------------------------------------------------------- #
# 一、成文宪法
# --------------------------------------------------------------------------- #
def test_charter_provenance_all_clauses_verified(charter: Charter) -> None:
    """宪法条款必须是来源文件原文——手抄/编造即红。"""
    report = charter.verify_provenance([ROOT])
    assert report["ok"] is True, report["failures"]
    assert report["clauses_checked"] == report["clauses_verified"] >= 10


def test_charter_detects_fabricated_clause(charter: Charter) -> None:
    """篡改一句条款 → 溯源必须失败（这是宪法的防伪门）。"""
    manifest = json.loads(CHARTER_PATH.read_text(encoding="utf-8"))
    manifest["clauses"][0]["text"] = "平台可以自行决定战略方向（本句为测试伪条款）"
    bad = Charter(manifest)
    report = bad.verify_provenance([ROOT])
    assert report["ok"] is False
    assert any("条款原文与来源不符" in f for f in report["failures"])


def test_charter_digest_is_content_addressed(charter: Charter) -> None:
    manifest = json.loads(CHARTER_PATH.read_text(encoding="utf-8"))
    clone = Charter(manifest)
    assert clone.digest == charter.digest
    manifest["clauses"][0]["precedence"] = 999
    assert Charter(manifest).digest != charter.digest


def test_red_line_clauses_sort_first(charter: Charter) -> None:
    ordered = charter.precedence_sorted()
    kinds = [c.is_red_line for c in ordered]
    assert kinds[: len(charter.red_line_clauses())] == [True] * len(charter.red_line_clauses())
    assert charter.red_line_clauses(), "宪章必须含红线条款，否则红线无从机器判定"


# --------------------------------------------------------------------------- #
# 二、预登记判据
# --------------------------------------------------------------------------- #
def test_preregistration_requires_criteria() -> None:
    with pytest.raises(PreregistrationError):
        Preregistration([], frozen_by="t", frozen_at=FROZEN_AT)


@pytest.mark.parametrize(
    "eval_spec",
    [
        {"metric": "x", "op": "≈", "value": 1},          # 非法算子
        {"metric": "", "op": ">", "value": 1},            # 缺 metric
        {"metric": "x", "op": ">", "value": 1, "unit": "m"},  # 多余字段（防夹带）
    ],
)
def test_criterion_rejects_malformed_eval(eval_spec: dict) -> None:
    with pytest.raises(PreregistrationError):
        Criterion(criterion_id="c", statement="s", eval=eval_spec)


def test_criterion_rejects_unknown_verdict() -> None:
    with pytest.raises(PreregistrationError):
        Criterion(criterion_id="c", statement="s", eval={"metric": "x", "op": ">", "value": 0},
                  on_fail_verdict="WHATEVER_I_WANT")


def test_freeze_hash_is_immutable_and_content_addressed() -> None:
    a = Preregistration([Criterion("c1", "s", {"metric": "x", "op": ">", "value": 0}, True, "REJECT")],
                        frozen_by="t", frozen_at=FROZEN_AT)
    b = Preregistration([Criterion("c1", "s", {"metric": "x", "op": ">", "value": 0}, True, "REJECT")],
                        frozen_by="t", frozen_at=FROZEN_AT)
    c = Preregistration([Criterion("c1", "s", {"metric": "x", "op": ">", "value": 1}, True, "REJECT")],
                        frozen_by="t", frozen_at=FROZEN_AT)
    assert a.freeze_hash == b.freeze_hash
    assert a.freeze_hash != c.freeze_hash
    with pytest.raises(Exception):
        a.criteria = ()  # 冻结后不可改


def test_ballot_before_freeze_is_rejected() -> None:
    """时序不变量：判据必须早于票（否则等于给票后补理由）。"""
    prereg = Preregistration([Criterion("c1", "s", {"metric": "x", "op": ">", "value": 0}, True, "REJECT")],
                             frozen_by="t", frozen_at=FROZEN_AT)
    early = _ballot("fam-a", "REJECT", cast_at="2026-11-06T19:59:59Z")
    with pytest.raises(PreregistrationError):
        prereg.assert_frozen_before([early])
    prereg.assert_frozen_before([_ballot("fam-a", "REJECT")])  # 冻结后投票 → 通过


def test_criteria_evaluation_requires_all_facts() -> None:
    prereg = Preregistration([Criterion("c1", "s", {"metric": "missing_metric", "op": ">", "value": 0})],
                             frozen_by="t", frozen_at=FROZEN_AT)
    with pytest.raises(PreregistrationError):
        prereg.evaluate({})


# --------------------------------------------------------------------------- #
# 三、红线分类
# --------------------------------------------------------------------------- #
def test_red_line_lexicon_categories_are_constitutional(classifier: RedLineClassifier) -> None:
    with pytest.raises(ConstitutionError):
        RedLineClassifier({"schema": "tp.council.redline_lexicon/v1", "categories": {"vibes": ["x"]}})


def test_strategic_change_detected(classifier: RedLineClassifier) -> None:
    hits = classifier.classify("把产品定位改成专用底座并改写北极星")
    assert {h["category"] for h in hits} == {"strategic_change"}


def test_evidence_grade_dispute_is_not_red_line(classifier: RedLineClassifier) -> None:
    """证据等级/精度争议属工程可裁项——不能被红线词表误吞（否则一切升级 owner=宪政空转）。"""
    assert classifier.classify("MOCK 端点能否登记为已验证能力 REAL") == []
    assert classifier.classify("误差超 15% 能否宣称达标") == []


# --------------------------------------------------------------------------- #
# 四、族级票决
# --------------------------------------------------------------------------- #
def test_one_vote_per_family() -> None:
    c = Conclave(quorum=3)
    assert c.cast(_ballot("fam-a", "REJECT")) == "accepted"
    assert c.cast(_ballot("fam-a", "ADOPT")) == "duplicate_family"
    tally = c.tally()
    assert tally["families"] == 1 and tally["rejected"][0]["reason"] == "duplicate_family"


def test_ballot_requires_rationale_and_known_verdict() -> None:
    with pytest.raises(ConstitutionError):
        Ballot(family_id="f", model_id="m", verdict="REJECT", rationale="  ", cast_at=AFTER)
    with pytest.raises(ConstitutionError):
        Ballot(family_id="f", model_id="m", verdict="PROBABLY_FINE", rationale="x", cast_at=AFTER)


def test_contested_requires_two_thirds() -> None:
    """争议项：2/3 = 恰好达标；1/3 不足。阈值用分数精确比较，不用浮点。"""
    c = Conclave(quorum=3, supermajority=Fraction(2, 3))
    c.cast(_ballot("f1", "REJECT"))
    c.cast(_ballot("f2", "REJECT"))
    c.cast(_ballot("f3", "ADOPT"))
    t = c.tally()
    assert t["contested"] is True and t["top_share"] == "2/3"
    assert t["threshold_met"] is True and t["top_verdict"] == "REJECT"

    c2 = Conclave(quorum=3, supermajority=Fraction(2, 3))
    for f in ("f1", "f2", "f3"):
        c2.cast(_ballot(f, "ADOPT"))
    t2 = c2.tally()
    assert t2["contested"] is False and t2["threshold"] == "1/2" and t2["threshold_met"] is True


def test_quorum_blocks_thin_committees() -> None:
    c = Conclave(quorum=3)
    c.cast(_ballot("f1", "REJECT"))
    c.cast(_ballot("f2", "REJECT"))
    assert c.tally()["quorum_met"] is False
    assert c.tally()["threshold_met"] is False


# --------------------------------------------------------------------------- #
# 五、裁决路由
# --------------------------------------------------------------------------- #
def _case(**over) -> dict:
    base = {
        "case_id": "T-1",
        "question": "MOCK 端点能否登记为已验证能力",
        "scope": "证据等级",
        "disputed_action": "按 HTTP 200 登记 REAL",
        "preregistration": {
            "frozen_by": "test",
            "frozen_at": FROZEN_AT,
            "criteria": [
                {"criterion_id": "c1", "statement": "登记 REAL 要求 MOCK 命中数为 0",
                 "eval": {"metric": "mock_hit", "op": "==", "value": 0},
                 "mandatory": True, "on_fail_verdict": "DOWNGRADE_TO_MOCK"}
            ],
        },
        "evidence": [],
        "owner_options": [{"option": "降级", "consequence": "-"}],
        "recommended_option": "降级",
        "owner_ruling": {"label": "DOWNGRADE_TO_MOCK", "verbatim": "v", "source_ref": "x"},
        "divergence_analysis": "",
    }
    base.update(over)
    return base


def test_criteria_route_decides_when_committee_dithers(charter: Charter, classifier: RedLineClassifier) -> None:
    adj = Adjudicator(charter, classifier)
    res = adj.adjudicate(_case(), [_ballot("f1", "ESCALATE_TO_OWNER"), _ballot("f2", "ESCALATE_TO_OWNER"),
                                   _ballot("f3", "DOWNGRADE_TO_MOCK")], {"mock_hit": 8})
    assert res["route"] == "criteria"
    assert res["verdict"] == "DOWNGRADE_TO_MOCK"       # 判据优先票决
    assert res["criterion_vote_conflict"] is True      # 但冲突必须留痕
    assert res["self_decision"] is True
    assert compare_with_owner(res, _case())["kind"] == "agreement"


def test_red_line_route_forbids_self_decision(charter: Charter, classifier: RedLineClassifier) -> None:
    adj = Adjudicator(charter, classifier)
    case = _case(case_id="T-2", question="平台能否改写北极星与产品定位", scope="战略方向变更",
                 divergence_analysis="分权设计：机器不代签。")
    res = adj.adjudicate(case, [_ballot("f1", "REJECT"), _ballot("f2", "REJECT"), _ballot("f3", "REJECT")],
                         {"mock_hit": 0})
    assert res["route"] == "redline_escalation"
    assert res["verdict"] == "ESCALATE_TO_OWNER"
    assert res["self_decision"] is False
    packet = res["owner_packet"]
    assert packet["recommendation_is_binding"] is False
    assert packet["red_line_categories"] == ["strategic_change"]
    assert packet["committee"]["families"] == 3           # 票照收，但不改变路由
    assert compare_with_owner(res, case)["kind"] == "constitutional_divergence"


def test_divergence_without_analysis_is_mismatch(charter: Charter, classifier: RedLineClassifier) -> None:
    adj = Adjudicator(charter, classifier)
    case = _case(case_id="T-3", question="改写北极星", scope="战略", divergence_analysis="")
    res = adj.adjudicate(case, [_ballot(f, "REJECT") for f in ("f1", "f2", "f3")], {"mock_hit": 0})
    assert compare_with_owner(res, case)["kind"] == "mismatch"


def test_no_criteria_fail_and_thin_committee_escalates(charter: Charter, classifier: RedLineClassifier) -> None:
    adj = Adjudicator(charter, classifier)
    case = _case(question="某端点是否可用", scope="能力判断",
                 preregistration={"frozen_by": "t", "frozen_at": FROZEN_AT,
                                  "criteria": [{"criterion_id": "c1", "statement": "通过即用",
                                                "eval": {"metric": "mock_hit", "op": "<", "value": 0},
                                                "mandatory": True, "on_fail_verdict": None}]})
    res = adj.adjudicate(case, [_ballot("f1", "ADOPT"), _ballot("f2", "REJECT"), _ballot("f3", "REJECT")],
                         {"mock_hit": 0})
    # 三族 2:1 达 2/3 → 票决成案
    assert res["route"] == "committee" and res["verdict"] == "REJECT"

    res2 = adj.adjudicate(case, [_ballot("f1", "ADOPT"), _ballot("f2", "REJECT")], {"mock_hit": 0})
    assert res2["route"] == "escalation_no_confidence"
    assert res2["verdict"] == "ESCALATE_TO_OWNER" and res2["self_decision"] is False


def test_missing_preregistration_blocks_adjudication(charter: Charter, classifier: RedLineClassifier) -> None:
    with pytest.raises(PreregistrationError):
        Adjudicator(charter, classifier).adjudicate(_case(preregistration={}), [_ballot("f1", "REJECT")], {})


# --------------------------------------------------------------------------- #
# 六、审计链
# --------------------------------------------------------------------------- #
def test_ledger_hash_chain_and_tamper_detection(tmp_path: Path) -> None:
    led = Ledger(tmp_path / "ledger.jsonl")
    led.append({"case_id": "a", "verdict": "REJECT"})
    led.append({"case_id": "b", "verdict": "ADOPT"})
    assert led.verify()["ok"] is True and led.verify()["entries"] == 2

    lines = led.path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["record"]["verdict"] = "ESCALATE_TO_OWNER"  # 改历史
    lines[0] = json.dumps(first, ensure_ascii=False, sort_keys=True)
    led.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = led.verify()
    assert report["ok"] is False
    assert any("entry_hash 不匹配" in p for p in report["problems"])


def test_acceptance_summary_thresholds() -> None:
    comps = [
        {"case_id": "a", "kind": "agreement"},
        {"case_id": "b", "kind": "agreement"},
        {"case_id": "c", "kind": "constitutional_divergence"},
    ]
    s = acceptance_summary(comps, min_agreements=2)
    assert s["passed"] is True
    assert acceptance_summary(comps[:1] + comps[2:], min_agreements=2)["passed"] is False
    assert acceptance_summary(comps + [{"case_id": "d", "kind": "mismatch"}], min_agreements=2)["passed"] is False


# --------------------------------------------------------------------------- #
# 七、真实案例（数据驱动的端到端）
# --------------------------------------------------------------------------- #
def test_real_cases_adjudicate(charter: Charter, classifier: RedLineClassifier) -> None:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    assert len(cases) == 3
    facts = {"IN27-H5-C1": {"mock_endpoints_hit": 8}, "IN27-H5-C2": {"passed_cases_15pct": 6}}
    adj = Adjudicator(charter, classifier)
    verdicts = {}
    for case in cases:
        ballots = [_ballot(f, "ESCALATE_TO_OWNER") for f in ("fam-a", "fam-b", "fam-c")]
        res = adj.adjudicate(case, ballots, facts.get(case["case_id"], {}))
        verdicts[case["case_id"]] = res["verdict"]
        if res["route"] != "redline_escalation":
            assert res["criteria_results"], "非红线案例必须给出判据逐条结果"
    assert verdicts == {
        "IN27-H5-C1": "DOWNGRADE_TO_MOCK",
        "IN27-H5-C2": "HONEST_FAIL",
        "IN27-H5-C3": "ESCALATE_TO_OWNER",
    }


@pytest.mark.skipif(not RESULT_PATH.exists(), reason="回放工件未生成（先跑 scripts/in27_h5_ballots.py + scripts/in27_h5_replay.py）")
def test_replay_artifact_meets_acceptance() -> None:
    data = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    assert data["charter_provenance"]["ok"] is True
    assert data["audit_chain"]["ok"] is True
    acc = data["acceptance"]
    assert acc["agreements"] >= 2, acc
    assert acc["divergences"] >= 1, acc
    assert acc["mismatches"] == 0, acc
    assert acc["passed"] is True
    for res in data["results"]:
        assert res["verdict"] in (
            "ADOPT", "REJECT", "DOWNGRADE_TO_MOCK", "HONEST_FAIL", "ESCALATE_TO_OWNER")
        if res["route"] == "redline_escalation":
            assert res["self_decision"] is False
            assert res["owner_packet"]["recommendation_is_binding"] is False