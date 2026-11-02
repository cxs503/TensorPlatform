"""Truth Gates 独立性规则门禁（m2038）。

一条判据一个测试：2-of-3 的**法定人数 / 份额 / 平局确定性 / 投票人唯一性 /
证据独立性 / 空哈希不可判真**。旧规则下这些用例里的多数会红——审计脚本
docs/audit/m2038_truth_gates_independence_audit.py 给出前后对照的数。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from services.truth_gates_aggregator import (  # noqa: E402
    aggregate_votes, reconcile_hash, QUORUM, CHEAT_SHARE,
)


def V(model, verdict, conf=0.9, ev=None):
    d = {"voter_model": model, "verdict": verdict, "confidence": conf}
    if ev is not None:
        d["evidence_hash"] = ev
    return d


# ── 法定人数 / 份额 ─────────────────────────────────────────────────
class TestQuorumAndShare:
    def test_two_of_three_confirmed_with_independent_evidence(self):
        r = aggregate_votes([V("a", "cheat", 0.9, "h1"), V("b", "cheat", 0.9, "h2"),
                             V("c", "pass")])
        assert r["is_confirmed_cheat"] is True
        assert r["is_confirmed_cheat_independent"] is True
        assert r["independence_blocks"] == []

    def test_minority_2_of_10_is_not_a_strike(self):
        votes = [V("a", "cheat"), V("b", "cheat")] + [V("p%d" % i, "pass") for i in range(8)]
        r = aggregate_votes(votes)
        assert r["total_voters"] == 10
        assert r["cheat_share"] == pytest.approx(0.2)
        assert r["is_confirmed_cheat"] is False
        assert "cheat_share_below_2_3" in r["confirmation_blocks"]

    def test_two_voter_group_cannot_strike(self):
        r = aggregate_votes([V("a", "cheat"), V("b", "cheat")])
        assert r["is_confirmed_cheat"] is False
        assert "quorum_not_met" in r["confirmation_blocks"]
        assert r["quorum"] == QUORUM == 3

    def test_five_voters_need_supermajority(self):
        votes = [V("c%d" % i, "cheat", 0.9, "h%d" % i) for i in range(3)] + \
                [V("p%d" % i, "pass") for i in range(2)]
        r = aggregate_votes(votes)
        assert r["cheat_share"] == pytest.approx(0.6)
        assert r["is_confirmed_cheat"] is False          # 3/5 < 2/3
        votes4 = votes[:3] + [V("c4", "cheat", 0.9, "h4")] + [V("p0", "pass")]
        assert aggregate_votes(votes4)["is_confirmed_cheat"] is True   # 4/5 >= 2/3

    def test_share_threshold_constant_is_two_thirds(self):
        assert CHEAT_SHARE == pytest.approx(2.0 / 3.0)


# ── 平局 / 确定性 ───────────────────────────────────────────────────
class TestDeterminism:
    def test_tie_is_split_and_has_no_winner(self):
        r = aggregate_votes([V("a", "cheat"), V("b", "cheat"), V("c", "pass"), V("d", "pass")])
        assert r["tally"] == {"cheat": 2, "pass": 2}
        assert r["majority_verdict"] is None
        assert r["is_split"] is True

    def test_tie_is_order_independent(self):
        a = [V("a", "cheat"), V("b", "cheat"), V("c", "pass"), V("d", "pass")]
        b = [V("d", "pass"), V("c", "pass"), V("b", "cheat"), V("a", "cheat")]
        ra, rb = aggregate_votes(a), aggregate_votes(b)
        assert ra["majority_verdict"] == rb["majority_verdict"] is None
        assert ra["is_split"] == rb["is_split"] is True

    def test_full_payload_order_independent(self):
        a = [V("a", "cheat", 0.9, "h1"), V("b", "pass", 0.4), V("c", "cheat", 0.7, "h2")]
        b = list(reversed(a))
        assert aggregate_votes(a) == aggregate_votes(b)

    def test_single_vote_is_not_split(self):
        r = aggregate_votes([V("a", "pass")])
        assert r["is_split"] is False
        assert r["majority_verdict"] is None      # 一票不构成多数
        assert r["is_confirmed_cheat"] is False


# ── 投票人唯一性 ────────────────────────────────────────────────────
class TestVoterUniqueness:
    def test_duplicate_voter_counts_once(self):
        r = aggregate_votes([V("rogue", "cheat"), V("rogue", "cheat"), V("c", "pass")])
        assert r["total_voters"] == 2
        assert r["total_votes"] == 3
        assert r["duplicate_voters"] == ["rogue"]
        assert r["is_confirmed_cheat"] is False      # 自我确认被阻断
        assert "quorum_not_met" in r["confirmation_blocks"]

    def test_duplicate_keeps_harshest_verdict(self):
        r = aggregate_votes([V("x", "pass"), V("x", "cheat"), V("y", "pass"), V("z", "pass")])
        assert r["tally"]["cheat"] == 1
        assert r["tally"]["pass"] == 2

    def test_whitespace_in_voter_id_collapses(self):
        r = aggregate_votes([V(" rogue ", "cheat"), V("rogue", "cheat")])
        assert r["total_voters"] == 1


# ── 证据独立性 ──────────────────────────────────────────────────────
class TestEvidenceIndependence:
    def test_correlated_hash_blocks_independent_confirmation(self):
        r = aggregate_votes([V("a", "cheat", 0.9, "same"), V("b", "cheat", 0.9, "same"),
                             V("c", "pass")])
        assert r["is_confirmed_cheat"] is True            # 票数上成立
        assert r["is_confirmed_cheat_independent"] is False   # 但证据同源
        assert r["evidence"]["correlated"] is True
        assert "correlated_evidence_hash" in r["independence_blocks"]

    def test_missing_hash_is_flagged_not_silently_accepted(self):
        r = aggregate_votes([V("a", "cheat"), V("b", "cheat"), V("c", "pass")])
        assert r["evidence"]["missing"] is True
        assert r["is_confirmed_cheat_independent"] is False
        assert "evidence_hash_missing" in r["independence_blocks"]

    def test_hash_prefix_and_case_are_normalised(self):
        r = aggregate_votes([V("a", "cheat", 0.9, "sha256:ABC"), V("b", "cheat", 0.9, "abc"),
                             V("c", "pass")])
        assert r["evidence"]["correlated"] is True    # 规范化后同源


# ── 非法选票 ────────────────────────────────────────────────────────
class TestInvalidBallots:
    def test_unknown_verdict_is_excluded_not_tallied(self):
        r = aggregate_votes([V("a", "unknown"), V("b", "unknown"), V("c", "pass")])
        assert "unknown" not in r["tally"]
        assert r["invalid_verdicts"] == ["unknown", "unknown"]
        assert r["majority_verdict"] is None

    def test_case_and_padding_are_normalised(self):
        r = aggregate_votes([V("a", " CHEAT "), V("b", "Cheat", 0.9, "h2"), V("c", "pass")])
        assert r["tally"]["cheat"] == 2

    def test_empty_input_is_safe(self):
        r = aggregate_votes([])
        assert r["total_voters"] == 0 and r["is_confirmed_cheat"] is False
        assert r["is_split"] is False and r["confirmation_blocks"] == []

    def test_all_invalid_yields_no_result(self):
        r = aggregate_votes([V("a", "??"), V("b", "")])
        assert r["total_voters"] == 0
        assert r["confirmation_blocks"] == ["no_valid_ballot"]


# ── 哈希对账 ────────────────────────────────────────────────────────
class TestHashReconciliation:
    def test_blank_hashes_are_an_anomaly(self):
        for a, b in (("", ""), ("  ", ""), (None, None), ("", "abc")):
            r = reconcile_hash(a, b)
            assert r["match"] is False and r["is_anomaly"] is True
            assert r["reason"] == "missing_hash"

    def test_sha256_prefix_is_not_a_mismatch(self):
        r = reconcile_hash("sha256:AB12", "ab12")
        assert r["match"] is True and r["is_anomaly"] is False

    def test_genuine_mismatch_stays_an_anomaly(self):
        r = reconcile_hash("ab12", "cd34")
        assert r["is_anomaly"] is True and r["match"] is False


# ── API 层：重复投票不得静默成为第二张独立票 ────────────────────────
class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return []


class _FakeDB:
    def __init__(self, duplicate_row):
        self._dup = duplicate_row
        self.committed = 0

    def execute(self, *_a, **_k):
        return _FakeResult(self._dup)

    def add(self, _obj):
        pass

    def commit(self):
        self.committed += 1

    def rollback(self):
        pass

    def refresh(self, _obj):
        pass


def test_api_rejects_second_ballot_from_same_voter():
    try:
        from api.truth_gates import submit_vote, VoteSubmit
        from fastapi import HTTPException
    except Exception as exc:                        # 缺 psycopg2 等运行期依赖时跳过
        pytest.skip("api module unavailable: %s" % exc)

    req = VoteSubmit(vote_group_id="vg1", mission_id=2038, voter_model="rogue",
                     verdict="cheat", confidence=0.9, evidence_hash="h1")
    db = _FakeDB(duplicate_row=(7, "cheat"))
    with pytest.raises(HTTPException) as ei:
        submit_vote(req, db=db, user={"username": "u", "role": "admin"})
    assert ei.value.status_code == 409
    assert db.committed == 0