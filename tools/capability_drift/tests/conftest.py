"""pytest fixtures：合成沙箱仓（2 个能力）+ 一次性审批 key。"""
from __future__ import annotations

import json
import secrets
from pathlib import Path

import pytest

from tools.capability_drift import approvals, baseline as baseline_mod, model, pipeline, sandbox
from tools.capability_drift.runner import run_cases


@pytest.fixture(scope="session")
def approval_key() -> str:
    return secrets.token_hex(16)


@pytest.fixture()
def key_env(monkeypatch, approval_key: str) -> str:
    monkeypatch.setenv(approvals.KEY_ENV, approval_key)
    return approval_key


@pytest.fixture()
def sandbox2(tmp_path: Path) -> sandbox.Sandbox:
    return sandbox.build_sandbox(tmp_path / "repo", n_capabilities=2, base_relax=0.60)


def calibrate(sb: sandbox.Sandbox, key: str, workers: int = 2) -> dict:
    """跑一遍案例并用 owner 签名写入基线（测试用标定）。"""
    bundle = model.load_bundle(sb.registry_dir)
    runs = run_cases([c for cap in bundle.capabilities for c in cap.cases], sb.repo, workers=workers)
    observed = pipeline.observed_values(bundle, runs)
    for cap_id, values in observed.items():
        approval = approvals.make_approval(
            "baseline_update", "owner-test", key,
            capability=cap_id, values_sha256=approvals.sha256_payload(values),
        )
        baseline_mod.update_baseline(sb.baseline_path(), cap_id, values, approval, key=key)
    return observed


@pytest.fixture()
def calibrated(sandbox2: sandbox.Sandbox, key_env: str):
    values = calibrate(sandbox2, key_env)
    return sandbox2, values


def write_waiver(sb: sandbox.Sandbox, key: str, cap_id: str, metric: str, **over) -> dict:
    record = approvals.make_approval(
        "waiver", "owner-test", key,
        capability=cap_id, metric=metric, max_rel_err=0.60, reason="known trade-off",
    )
    record.update(over)
    sandbox.write_registry_bundle(sb, waivers=[record])
    return record


def dump(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path