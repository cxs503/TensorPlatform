"""v2 spec 装载/校验测试。"""
import json
from pathlib import Path

import pytest

from tools.capability_audit_v2.spec_v2 import SpecV2Error, load_spec_v2

ROOT = Path(__file__).resolve().parents[2]
EXEC_SPEC = "tests/capability_audit_v2/fixtures/spec_exec.json"
REAL_SPEC = "config/capability_audit_v2/spec.json"


def test_load_synthetic_spec():
    spec = load_spec_v2(EXEC_SPEC, ROOT)
    assert spec["schema_version"] == "2.0"
    assert [c["id"] for c in spec["base_spec"]["capabilities"]] == [
        "cap.alpha", "cap.beta", "cap.gamma", "cap.delta", "cap.epsilon"]
    assert set(spec["exec"]) == {"cap.alpha", "cap.beta", "cap.gamma"}
    assert spec["thresholds"]["probe_min_contradictions"] == 1
    assert spec["execution"]["timeout_s"] == 120


def test_real_spec_declares_three_executable_capabilities():
    spec = load_spec_v2(REAL_SPEC, ROOT)
    assert len(spec["base_spec"]["capabilities"]) == 21
    exec_caps = sorted(spec["exec"])
    assert len(exec_caps) == 3
    assert all(spec["exec"][c].get("pytest") for c in exec_caps)
    probe_caps = [c for c in exec_caps if "probe" in spec["exec"][c]]
    control_caps = [c for c in exec_caps if "control_probe" in spec["exec"][c]]
    assert probe_caps == control_caps and len(probe_caps) == 1


def test_orphan_capability_rejected(tmp_path):
    raw = json.loads((ROOT / EXEC_SPEC).read_text(encoding="utf-8"))
    raw["capabilities"]["cap.unknown"] = {"pytest": {"selectors": ["x.py"]}}
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(SpecV2Error):
        load_spec_v2(p, ROOT)


def test_bad_check_kind_rejected(tmp_path):
    raw = json.loads((ROOT / EXEC_SPEC).read_text(encoding="utf-8"))
    raw["capabilities"]["cap.alpha"]["probe"]["checks"][0]["kind"] = "vibes"
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(SpecV2Error):
        load_spec_v2(p, ROOT)


def test_probe_point_must_cover_params(tmp_path):
    raw = json.loads((ROOT / EXEC_SPEC).read_text(encoding="utf-8"))
    raw["capabilities"]["cap.alpha"]["probe"]["points"][0].pop("b")
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(SpecV2Error):
        load_spec_v2(p, ROOT)


def test_missing_base_spec_rejected(tmp_path):
    p = tmp_path / "spec.json"
    p.write_text(json.dumps({"schema_version": "2.0",
                             "base_spec": "does/not/exist.json",
                             "capabilities": {}}), encoding="utf-8")
    with pytest.raises(SpecV2Error):
        load_spec_v2(p, ROOT)