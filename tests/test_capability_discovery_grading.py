"""定级层：运行结局 + 声明 + 锚点 → 等级（策略即数据）。

判据 C2（提取正确性的下游）：等级必须与「证据强度」严格对应，且默认**保守**——
只有通过 + 声明 + 锚点才可能 REAL；跳过/预期失败/未运行一律不得升级。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.capability_discovery.grading import (
    DEFAULT_POLICY,
    grade_feature,
    grade_features,
    load_policy,
    policy_digest,
)
from tools.capability_discovery.model import CapabilityFeature


def make_feature(
    cap_id: str = "synth.unit.case",
    *,
    outcomes: dict | None = None,
    declared: str = "REAL",
    anchors: list[str] | None = ("ref:closed_form:x",),
    modifiers: list[str] | None = None,
    sha: str = "0" * 64,
    decls_seen: list[str] | None = None,
) -> CapabilityFeature:
    outcomes = {"passed": 1} if outcomes is None else outcomes
    returns = sum(outcomes.values()) or 1
    return CapabilityFeature(
        capability_id=cap_id,
        label="synthetic unit case",
        declared_level=declared,
        anchors=list(anchors or []),
        params={},
        tags=[],
        tests=[
            {
                "nodeid": f"t.py::test_{i}",
                "source_file": "t.py",
                "lineno": i,
                "marker_formats": ["function_kwargs"],
                "scopes": ["function"],
                "modifiers": list(modifiers or []),
                "outcome": next(iter(outcomes)) if returns == 1 else "passed",
            }
            for i in range(returns)
        ],
        sources=[{"path": "t.py", "sha256": sha}],
        marker_formats=["function_kwargs"],
        outcomes=dict(outcomes),
        declared_levels_seen=list(decls_seen if decls_seen is not None else ([declared] if declared else [])),
    )


class TestDefaultPolicyMatrix:
    @pytest.mark.parametrize(
        "kwargs,expected,state",
        [
            ({"outcomes": {"passed": 1}}, "REAL", "passed"),
            ({"outcomes": {"passed": 2}, "anchors": []}, "WITHHELD", "passed"),
            ({"outcomes": {"failed": 1}}, "MOCK", "failed"),
            ({"outcomes": {"error": 1}}, "MOCK", "error"),
            ({"outcomes": {"skipped": 1}}, "WITHHELD", "skipped"),
            ({"outcomes": {"xfailed": 1}}, "WITHHELD", "xfailed"),
            ({"outcomes": {"xpassed": 1}}, "WITHHELD", "xpassed"),
            ({"outcomes": {"not_run": 1}}, "WITHHELD", "not_run"),
            ({"outcomes": {"passed": 1, "failed": 1}}, "MOCK", "failed"),
            ({"outcomes": {"passed": 1}, "declared": ""}, "WITHHELD", "passed"),
        ],
    )
    def test_outcome_to_level(self, kwargs, expected, state):
        grade = grade_feature(make_feature(**kwargs))
        assert grade.level == expected
        assert grade.outcome_state == state
        assert grade.reasons, "定级必须给出可审计理由"

    def test_modifier_caps_level(self):
        grade = grade_feature(make_feature(modifiers=["skip"]))
        assert grade.level == "WITHHELD"
        assert any("caps level" in r for r in grade.reasons)

    def test_conflicting_declarations_cap_level(self):
        feat = make_feature(decls_seen=["REAL", "MOCK"])
        grade = grade_feature(feat)
        assert grade.level == "WITHHELD"
        assert any("conflicting" in r for r in grade.reasons)

    def test_missing_source_sha_blocks_level(self):
        grade = grade_feature(make_feature(sha=""))
        assert grade.level == "WITHHELD"
        assert any("sha256" in r for r in grade.reasons)

    def test_declared_stub_is_not_upgraded_by_passing_test(self):
        grade = grade_feature(make_feature(declared="STUB"))
        assert grade.level == "STUB"

    def test_confidence_is_bounded_and_ordered(self):
        full = grade_feature(make_feature(outcomes={"passed": 2}))
        none = grade_feature(make_feature(outcomes={"not_run": 2}))
        assert 0.0 <= none.confidence < full.confidence <= 1.0


class TestPolicyAsData:
    """策略可换、代码不改：证明「保守口径」是数据而非硬编码分支。"""

    def test_policy_digest_stable_and_distinct(self):
        pol_a = load_policy()
        pol_b = json.loads(json.dumps(pol_a))
        assert policy_digest(pol_a) == policy_digest(pol_b)
        pol_b["undeclared_passed_level"] = "REAL"
        assert policy_digest(pol_a) != policy_digest(pol_b)

    def test_declared_marker_can_be_flipped_by_policy(self, tmp_path):
        feat = make_feature(outcomes={"passed": 1}, declared="", anchors=["ref:x"])
        assert grade_feature(feat).level == "WITHHELD"
        policy = dict(DEFAULT_POLICY)
        policy["undeclared_passed_level"] = "REAL"
        policy["require_anchors_for"] = []
        assert grade_feature(feat, policy).level == "REAL"

    def test_policy_file_overrides_defaults(self, tmp_path):
        path = tmp_path / "policy.json"
        path.write_text(json.dumps({"undeclared_passed_level": "STUB"}), encoding="utf-8")
        pol = load_policy(path)
        assert pol["undeclared_passed_level"] == "STUB"
        assert pol["require_anchors_for"] == DEFAULT_POLICY["require_anchors_for"]

    def test_unknown_policy_field_rejected(self, tmp_path):
        path = tmp_path / "policy.json"
        path.write_text(json.dumps({"nonsense_field": 1}), encoding="utf-8")
        with pytest.raises(ValueError):
            load_policy(path)

    def test_grade_batch_preserves_order_and_ids(self):
        feats = [make_feature("synth.a"), make_feature("synth.b", outcomes={"failed": 1})]
        grades = grade_features(feats)
        assert [g.capability_id for g in grades] == ["synth.a", "synth.b"]
        assert [g.level for g in grades] == ["REAL", "MOCK"]
        assert all(len(g.policy_digest) == 64 for g in grades)