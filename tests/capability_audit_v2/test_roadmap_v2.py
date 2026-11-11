"""升级路线图校验测试：缺路径 / 步骤不足 / 引用不存在路径都必须报错。"""
import json
from pathlib import Path

import pytest

from tools.capability_audit_v2 import roadmap as R

ROOT = Path(__file__).resolve().parents[2]
PATHS = "tests/capability_audit_v2/fixtures/upgrade_paths.json"


def _verdict(cap_id, level, reason="NO_BENCHMARK"):
    return {"capability": cap_id, "level": level, "reason_code": reason}


def _paths():
    return R.load_paths(ROOT / PATHS, {"cap.alpha", "cap.beta", "cap.gamma", "cap.epsilon"})


def test_roadmap_item_complete():
    rm = R.build_roadmap([_verdict("cap.beta", "MOCK")], _paths(), ROOT, "test-ruleset")
    item = rm["items"][0]
    assert item["to_level"] == "REAL"
    assert item["upgrade_class"] == "algorithm_replacement"
    assert len(item["algorithm_refs"]) == 1 and item["algorithm_refs"][0]["citation"]
    assert len(item["steps"]) == 3
    assert rm["summary"]["algorithm_refs"] == 1 and rm["summary"]["steps"] == 3


def test_missing_mock_path_rejected():
    with pytest.raises(R.RoadmapError):
        R.build_roadmap([_verdict("cap.alpha", "MOCK")], _paths(), ROOT)


def test_too_few_steps_rejected():
    paths = _paths()
    paths["cap.beta"] = json.loads(json.dumps(paths["cap.beta"]))
    paths["cap.beta"]["steps"] = paths["cap.beta"]["steps"][:2]
    with pytest.raises(R.RoadmapError):
        R.build_roadmap([_verdict("cap.beta", "MOCK")], paths, ROOT)


def test_verification_must_point_at_existing_artifact():
    paths = _paths()
    paths["cap.beta"] = json.loads(json.dumps(paths["cap.beta"]))
    paths["cap.beta"]["steps"][0]["verification"] = "python3 -m pytest -q nosuch/dir/case.py"
    with pytest.raises(R.RoadmapError):
        R.build_roadmap([_verdict("cap.beta", "MOCK")], paths, ROOT)


def test_algorithm_ref_requires_locator():
    paths = _paths()
    paths["cap.beta"] = json.loads(json.dumps(paths["cap.beta"]))
    paths["cap.beta"]["algorithm_refs"][0].pop("locator")
    with pytest.raises(R.RoadmapError):
        R.build_roadmap([_verdict("cap.beta", "MOCK")], paths, ROOT)


def test_withheld_gets_completion_action():
    rm = R.build_roadmap([_verdict("cap.epsilon", "WITHHELD", "EVIDENCE_NOT_BOUND")],
                         _paths(), ROOT)
    action = rm["evidence_completion"][0]
    assert action["capability"] == "cap.epsilon"
    assert action["next_action"]
    assert action["override"] is True  # 配置里给了专用动作


def test_stale_next_action_rejected():
    """配置里给"其实不是 WITHHELD"的能力写了 next_action ⇒ 配置漂移，必须报错。"""
    paths = _paths()
    with pytest.raises(R.RoadmapError):
        R.build_roadmap([_verdict("cap.beta", "MOCK"),
                         _verdict("cap.epsilon", "REAL")], paths, ROOT)