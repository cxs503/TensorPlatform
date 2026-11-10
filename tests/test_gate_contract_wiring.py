"""前门接线测试：dev_acceptance 审批链真的会被 gate_verify 拦下（零领域语义夹具）。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.gate_contract.contract import SCHEMA_VERSION, sha256_file  # noqa: E402


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _synthetic_gate(root: Path, mission_id: int, *, value: float, sha_ok: bool = True) -> None:
    gate_dir = root / f"g{mission_id}"
    ev = gate_dir / "evidence"
    ev.mkdir(parents=True, exist_ok=True)
    art = ev / "alpha.json"
    art.write_text(json.dumps({"metrics": {"alpha": value}}), encoding="utf-8")
    gate = {"schema_version": SCHEMA_VERSION, "gate_id": f"g{mission_id}",
            "mission_id": mission_id, "run_cmd": "pytest",
            "criteria": [{"id": "C1", "artifact": {
                "path": "evidence/alpha.json",
                "sha256": sha256_file(art) if sha_ok else "0" * 64},
                "pointer": "metrics/alpha", "op": "le", "threshold": 1.0,
                "unit": "ratio", "measured": value}]}
    (gate_dir / "gate.json").write_text(json.dumps(gate), encoding="utf-8")


def _policy(tmp: Path) -> dict:
    return {"mode": "enforce", "gate_root": str(tmp / "gates"),
            "registry": "", "ledger_db": str(tmp / "ledger.db"), "_repo_root": str(tmp)}


def test_backend_service_blocks_and_describes(tmp_path):
    root = tmp_path / "gates"
    _synthetic_gate(root, 1234, value=0.5, sha_ok=False)
    service = _load(REPO_ROOT / "backend" / "services" / "gate_contract_service.py",
                    "gate_contract_service")
    out = service.precheck(1234, policy=_policy(tmp_path))
    assert out["decision"] == "withhold" and out["approver_verdict"] == "escalate"
    desc = service.describe_evidence(1234, policy=_policy(tmp_path))
    assert desc["enabled"] is True and desc["grade"] == "WITHHELD"
    assert desc["criteria"][0]["code"] == "artifact_sha_mismatch"


def test_backend_service_allows_clean_gate(tmp_path):
    root = tmp_path / "gates"
    _synthetic_gate(root, 1235, value=0.5)
    service = _load(REPO_ROOT / "backend" / "services" / "gate_contract_service.py",
                    "gate_contract_service")
    assert service.precheck(1235, policy=_policy(tmp_path))["decision"] == "allow_auto_approve"


def test_auto_approver_hook_escalates_withheld(tmp_path, monkeypatch):
    root = tmp_path / "gates"
    _synthetic_gate(root, 4321, value=5.0)  # 阈值越线 → FAIL → reject
    policy = _policy(tmp_path)
    import tools.gate_contract.acceptance as acc
    monkeypatch.setattr(acc, "load_policy", lambda *a, **k: policy)
    approver = _load(REPO_ROOT / "tools" / "agent" / "auto_approver.py", "auto_approver_mod")
    out = approver._gate_front_verdict(4321)
    assert out is not None and out["grade"] == "FAIL"
    assert out["approver_verdict"] == "reject"
    # 4 参签名不变：审批器调用点无需改动
    import inspect
    assert list(inspect.signature(approver.verify_dev_acceptance).parameters) == [
        "api", "mission", "evidence"]


def test_auto_approver_hook_returns_none_when_module_missing(monkeypatch):
    approver = _load(REPO_ROOT / "tools" / "agent" / "auto_approver.py", "auto_approver_mod2")
    import tools.gate_contract.acceptance as acc
    def boom(*a, **k):
        raise RuntimeError("gate module down")
    monkeypatch.setattr(acc, "precheck_mission", boom)
    assert approver._gate_front_verdict(1) is None  # 门挂了不改旧行为，审批流不被拖死
