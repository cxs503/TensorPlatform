"""注册表模型 + 触发机制单元测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.capability_drift import model, sandbox


def test_load_bundle_resolves_gates_and_repo_root(sandbox2):
    bundle = model.load_bundle(sandbox2.registry_dir)
    assert bundle.repo_root == sandbox2.repo.resolve()
    assert len(bundle.capabilities) == 2
    cap = bundle.capability(sandbox2.capabilities[0].id)
    assert cap is not None
    assert cap.gates, "gates must be attached from gates.json"
    assert cap.gate(cap.metrics[0]).rel_tol == pytest.approx(0.05)
    assert bundle.baseline == {}
    assert set(bundle.file_sha256) >= {"registry.json", "gates.json", "baseline.json"}


def test_duplicate_capability_id_rejected(sandbox2):
    reg = json.loads((sandbox2.registry_dir / "registry.json").read_text())
    reg["capabilities"].append(dict(reg["capabilities"][0]))
    (sandbox2.registry_dir / "registry.json").write_text(json.dumps(reg))
    with pytest.raises(model.RegistryError):
        model.load_bundle(sandbox2.registry_dir)


def test_gate_for_undeclared_metric_rejected(sandbox2):
    gates = json.loads((sandbox2.registry_dir / "gates.json").read_text())
    gates["capabilities"][sandbox2.capabilities[0].id]["bogus_metric"] = {"rel_tol": 0.1}
    (sandbox2.registry_dir / "gates.json").write_text(json.dumps(gates))
    with pytest.raises(model.RegistryError):
        model.load_bundle(sandbox2.registry_dir)


def test_capability_without_cases_rejected(sandbox2):
    reg = json.loads((sandbox2.registry_dir / "registry.json").read_text())
    reg["capabilities"][0]["cases"] = []
    (sandbox2.registry_dir / "registry.json").write_text(json.dumps(reg))
    with pytest.raises(model.RegistryError):
        model.load_bundle(sandbox2.registry_dir)


def test_missing_registry_file_raises(tmp_path):
    with pytest.raises(model.RegistryError):
        model.load_bundle(tmp_path)


def test_trigger_matches_exact_prefix_and_glob():
    cap = model.Capability(
        id="synth.x", solver="synth",
        cases=(model.Case(id="c", node="verif/test_x.py::test_x"),),
        metrics=("m",), gates=(model.Gate("m"),),
        paths=("solver/", "shared/*.h"),
    )
    assert model.affected_capabilities([cap], ["solver/kernel.py"]) == ("synth.x",)
    assert model.affected_capabilities([cap], ["shared/core.h"]) == ("synth.x",)
    assert model.affected_capabilities([cap], ["verif/test_x.py"]) == ("synth.x",)
    assert model.affected_capabilities([cap], ["docs/README.md"]) == ()
    assert model.affected_capabilities([cap], ["solver_other/kernel.py"]) == ()


def test_file_sha256_is_content_addressed(sandbox2):
    p = sandbox2.registry_dir / "gates.json"
    before = model.sha256_file(p)
    assert model.sha256_file(p) == before
    p.write_text(p.read_text() + "\n")
    assert model.sha256_file(p) != before


def test_waiver_parse_keeps_signature(sandbox2):
    sandbox.write_registry_bundle(
        sandbox2,
        waivers=[{"capability": sandbox2.capabilities[0].id, "metric": "energy_00",
                  "max_rel_err": 0.5, "reason": "r", "approver": "owner",
                  "approved_at": "2026-01-01T00:00:00+00:00", "signature": "deadbeef"}],
    )
    bundle = model.load_bundle(sandbox2.registry_dir)
    assert len(bundle.whitelist) == 1
    assert bundle.whitelist[0].capability == sandbox2.capabilities[0].id
    assert bundle.whitelist[0].signature == "deadbeef"