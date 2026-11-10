#!/usr/bin/env python3
"""campaign_acceptance 单测（合成域名：N-A/alpha_key/unit_x —— 零领域语义）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops/taskplane"))

import campaign_acceptance as ca  # noqa: E402

CFG = {
    "closed_statuses": ["done", "failed"],
    "failed_statuses": ["failed"],
    "open_statuses": ["pending", "in-flight"],
    "census_node_types": ["task"],
    "min_reports_per_closed_node": 1,
    "failed_ledger": {},
    "require_integration_pass": True,
    "require_governance": False,
    "require_ledger": False,
}


def _node(nid, ext, status, deps=None, mission=None, ntype="task"):
    return {"id": nid, "node_type": ntype, "status": status,
            "dependencies": deps or [], "mission_id": mission,
            "metadata": {"ext_id": ext}}


def _snapshot(nodes, reports=None):
    return {"plan": {"id": 7, "name": "synthetic"}, "nodes": nodes,
            "missions": {}, "reports": reports or {}}


def _integration(verdict="pass", passed=True):
    return {"verdict": verdict, "counts": {"pass": 2, "honest_fail": 0,
                                           "unproven": 0, "fail": 0},
            "criteria": [{"id": "x_synthetic", "passed": passed}],
            "input_digest": "deadbeef"}


# ───────────────────────── A：状态普查 ─────────────────────────

def test_census_all_closed_passes():
    snap = _snapshot([_node(1, "N-A", "done"), _node(2, "N-B", "failed")])
    c = ca.census(snap["nodes"], CFG)
    assert c["counts"]["total"] == 2
    assert c["counts"]["closed"] == 2
    assert c["counts"]["failed"] == 1
    assert c["counts"]["open"] == 0
    assert c["criterion"]["passed"] is True


def test_census_open_node_blocks():
    snap = _snapshot([_node(1, "N-A", "done"), _node(2, "N-B", "in-flight")])
    c = ca.census(snap["nodes"], CFG)
    assert c["criterion"]["passed"] is False
    assert [o["ext_id"] for o in c["open_nodes"]] == ["N-B"]
    assert c["criterion"]["detail"]["open_statuses"] == ["in-flight"]


def test_census_unknown_status_is_not_silently_closed():
    c = ca.census([_node(1, "N-A", "weird_state")], CFG)
    assert c["criterion"]["passed"] is False
    assert c["counts"]["unknown"] == 1


def test_census_ignores_gate_nodes_by_default():
    c = ca.census([_node(1, "N-G", "in-flight", ntype="gate")], CFG)
    assert c["counts"]["total"] == 0
    assert c["criterion"]["passed"] is False  # 空普查不算通过


# ───────────────────────── B：诚实失败台账 ─────────────────────────

def test_honest_fail_requires_reason_and_evidence():
    failed = [{"node_id": 2, "ext_id": "N-B", "status": "failed"}]
    ok = ca.honest_fail_audit(failed, {"N-B": {"reason": "criteria exceeded",
                                               "evidence": "doc:alpha.md"}})
    assert ok["criterion"]["passed"] is True
    bad = ca.honest_fail_audit(failed, {"N-B": {"reason": "", "evidence": ""}})
    assert bad["criterion"]["passed"] is False
    assert bad["undocumented"][0]["why"] == "missing_reason"


def test_honest_fail_ghost_entry_flagged_but_not_blocking():
    audit = ca.honest_fail_audit([], {"N-Z": {"reason": "x", "evidence": "y"}})
    assert audit["criterion"]["passed"] is True       # 无失败节点 → 判据通过
    assert audit["ghost_entries"] == ["N-Z"]          # 但幽灵条目留痕


# ───────────────────────── C：收口节点证据 ─────────────────────────

def test_evidence_gap_blocks():
    nodes = [_node(1, "N-A", "done", mission=11)]
    snap = _snapshot(nodes, reports={"11": []})
    cen = ca.census(nodes, CFG)
    ev = ca.evidence_completeness(cen["nodes"], snap, CFG)
    assert ev["criterion"]["passed"] is False
    assert ev["gaps"][0]["ext_id"] == "N-A"


def test_evidence_present_passes():
    nodes = [_node(1, "N-A", "done", mission=11)]
    snap = _snapshot(nodes, reports={"11": [{"id": 99, "title": "alpha"}]})
    cen = ca.census(nodes, CFG)
    ev = ca.evidence_completeness(cen["nodes"], snap, CFG)
    assert ev["criterion"]["passed"] is True


# ───────────────────────── D：集成门引用 ─────────────────────────

def test_integration_criterion_absent_blocks_when_required():
    c = ca.integration_criterion(None, CFG)
    assert c["passed"] is False
    relaxed = ca.integration_criterion(None, {**CFG, "require_integration_pass": False})
    assert relaxed["passed"] is True


def test_integration_criterion_follows_nested_criteria():
    assert ca.integration_criterion(_integration("pass", True), CFG)["passed"] is True
    assert ca.integration_criterion(_integration("not_ready", True), CFG)["passed"] is False
    assert ca.integration_criterion(_integration("pass", False), CFG)["passed"] is False


# ───────────────────── E：治理工具链在位性 ─────────────────────

def _write(tmp_path: Path, rel: str, text: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_governance_integrated(tmp_path):
    _write(tmp_path, "tools/synth_alpha.py", "def run_alpha(x):\n    return x\n")
    _write(tmp_path, "tests/test_synth_alpha.py", "import synth_alpha\n")
    spec = {"modules": [{"key": "alpha", "path": "tools/synth_alpha.py",
                         "module": "synth_alpha_probe", "exports": ["run_alpha"],
                         "tests": ["tests/test_synth_alpha.py"]}]}
    out = ca.governance_check(spec, tmp_path)
    assert out["criterion"]["passed"] is True
    assert out["modules"][0]["status"] == "integrated"


def test_governance_missing_export_fails(tmp_path):
    _write(tmp_path, "tools/synth_beta.py", "def other():\n    return 1\n")
    _write(tmp_path, "tests/test_synth_beta.py", "import synth_beta\n")
    spec = {"modules": [{"key": "beta", "path": "tools/synth_beta.py",
                         "module": "synth_beta_probe", "exports": ["run_beta"],
                         "tests": ["tests/test_synth_beta.py"]}]}
    out = ca.governance_check(spec, tmp_path)
    assert out["criterion"]["passed"] is False
    assert out["modules"][0]["missing_exports"] == ["run_beta"]


def test_governance_without_test_reference_fails(tmp_path):
    _write(tmp_path, "tools/synth_gamma.py", "def run_gamma():\n    return 1\n")
    _write(tmp_path, "tests/test_synth_gamma.py", "# no reference here\n")
    spec = {"modules": [{"key": "gamma", "path": "tools/synth_gamma.py",
                         "module": "synth_gamma_probe", "exports": ["run_gamma"],
                         "tests": ["tests/test_synth_gamma.py"]}]}
    out = ca.governance_check(spec, tmp_path)
    assert out["criterion"]["passed"] is False
    assert out["modules"][0]["status"] == "unreferenced"


def test_governance_import_error_is_fail_closed(tmp_path):
    _write(tmp_path, "tools/synth_broken.py", "raise RuntimeError('boom')\n")
    spec = {"modules": [{"key": "broken", "path": "tools/synth_broken.py",
                         "module": "synth_broken_probe", "exports": [],
                         "tests": []}]}
    out = ca.governance_check(spec, tmp_path)
    assert out["criterion"]["passed"] is False
    assert "import_error" in (out["modules"][0]["error"] or "")


def test_governance_smoke_pass_and_fail(tmp_path):
    _write(tmp_path, "tools/synth_delta.py", "def run_delta():\n    return 1\n")
    _write(tmp_path, "tests/test_synth_delta.py", "import synth_delta\n")
    ok_spec = {"modules": [{"key": "delta", "path": "tools/synth_delta.py",
                            "module": "synth_delta_probe", "exports": ["run_delta"],
                            "tests": ["tests/test_synth_delta.py"],
                            "smoke": {"argv": [sys.executable, "-c", "print('ok')"],
                                      "timeout": 60}}]}
    out = ca.governance_check(ok_spec, tmp_path)
    assert out["criterion"]["passed"] is True
    assert out["modules"][0]["smoke"]["status"] == "ok"

    bad_spec = {"modules": [{**ok_spec["modules"][0],
                             "smoke": {"argv": [sys.executable, "-c",
                                                "import sys; sys.exit(3)"],
                                       "timeout": 60}}]}
    bad = ca.governance_check(bad_spec, tmp_path)
    assert bad["criterion"]["passed"] is False
    assert bad["modules"][0]["status"] == "smoke_failed"


def test_governance_package_relative_import(tmp_path):
    """包内模块（相对导入）必须能通过 import_name 路径被检到，不能误判 unavailable。"""
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/helper.py", "VALUE = 7\n")
    _write(tmp_path, "pkg/worker.py",
           "from .helper import VALUE\n\ndef run_worker():\n    return VALUE\n")
    _write(tmp_path, "tests/test_pkg_worker.py", "import pkg.worker\n")
    spec = {"modules": [{"key": "worker", "path": "pkg/worker.py",
                         "module": "pkg_worker_probe",
                         "import_name": "pkg.worker",
                         "exports": ["run_worker"],
                         "tests": ["tests/test_pkg_worker.py"]}]}
    out = ca.governance_check(spec, tmp_path)
    assert out["criterion"]["passed"] is True
    assert out["modules"][0]["import_ok"] is True


def test_governance_smoke_absent_is_not_blocking(tmp_path):
    _write(tmp_path, "tools/synth_eps.py", "def run_eps():\n    return 1\n")
    _write(tmp_path, "tests/test_synth_eps.py", "import synth_eps\n")
    spec = {"modules": [{"key": "eps", "path": "tools/synth_eps.py",
                         "module": "synth_eps_probe", "exports": ["run_eps"],
                         "tests": ["tests/test_synth_eps.py"]}]}
    out = ca.governance_check(spec, tmp_path)
    assert out["criterion"]["passed"] is True
    assert out["modules"][0]["smoke"]["status"] == "not_declared"


# ───────────────────── F：能力台账登记 ─────────────────────

def test_capability_ledger_registered(tmp_path):
    _write(tmp_path, "docs/alpha.md", "evidence\n")
    _write(tmp_path, "artifacts/synth/ledger.json", json.dumps({"entries": [{
        "id": "cap.alpha", "title": "alpha gate", "level": "REAL",
        "evidence": [{"path": "docs/alpha.md"}], "registered_by": "m-test"}]}))
    spec = {"ledger_path": "artifacts/synth/ledger.json", "capability_id": "cap.alpha",
            "allowed_levels": ["REAL", "MOCK"]}
    out = ca.capability_ledger_check(spec, tmp_path)
    assert out["criterion"]["passed"] is True


def test_capability_ledger_missing_entry_and_bad_evidence(tmp_path):
    _write(tmp_path, "artifacts/synth/ledger.json", json.dumps({"entries": [{
        "id": "cap.beta", "title": "beta", "level": "REAL",
        "evidence": [{"path": "docs/nope.md"}], "registered_by": "m-test"}]}))
    spec = {"ledger_path": "artifacts/synth/ledger.json", "capability_id": "cap.beta",
            "allowed_levels": ["REAL"]}
    out = ca.capability_ledger_check(spec, tmp_path)
    assert out["criterion"]["passed"] is False
    assert any("evidence_unresolved" in p for p in out["problems"])
    missing = ca.capability_ledger_check({**spec, "capability_id": "cap.gone"}, tmp_path)
    assert any("entry_not_found" in p for p in missing["problems"])


# ───────────────────── 汇总裁决 ─────────────────────

def test_accept_verdict_pass_when_all_criteria_pass():
    nodes = [_node(1, "N-A", "done", mission=11)]
    snap = _snapshot(nodes, reports={"11": [{"id": 5}]})
    rep = ca.accept(snap, _integration(), None, None, CFG)
    assert rep["verdict"] == "pass"
    assert rep["criterion_counts"]["failed"] == []
    assert [c["id"] for c in rep["criteria"]] == [
        "a_all_task_nodes_closed", "b_failed_nodes_documented",
        "c_closed_nodes_evidenced", "d_integration_gate",
        "e_governance_integrated", "f_capability_ledger_registered"]


def test_accept_verdict_not_ready_on_open_node():
    snap = _snapshot([_node(1, "N-A", "in-flight")], reports={})
    rep = ca.accept(snap, _integration(), None, None, CFG)
    assert rep["verdict"] == "not_ready"
    assert "a_all_task_nodes_closed" in rep["criterion_counts"]["failed"]


def test_accept_deterministic_bytes():
    snap = _snapshot([_node(1, "N-A", "done", mission=11)],
                     reports={"11": [{"id": 5}]})
    a = json.dumps(ca.accept(snap, _integration(), None, None, CFG), sort_keys=True)
    b = json.dumps(ca.accept(snap, _integration(), None, None, CFG), sort_keys=True)
    assert a == b


def test_render_markdown_states_not_ready():
    snap = _snapshot([_node(1, "N-A", "in-flight")], reports={})
    md = ca.render_markdown(ca.accept(snap, _integration(), None, None, CFG))
    assert "not_ready" in md and "不可验收" in md


# ───────────────────── CLI：退出码纪律 ─────────────────────

def test_cli_exit_code_discipline(tmp_path):
    snap = _snapshot([_node(1, "N-A", "done", mission=11)],
                     reports={"11": [{"id": 5}]})
    snap_p = tmp_path / "snap.json"
    snap_p.write_text(json.dumps(snap), encoding="utf-8")
    cfg_p = tmp_path / "cfg.json"
    cfg_p.write_text(json.dumps({**CFG, "require_integration_pass": False}),
                     encoding="utf-8")
    out = tmp_path / "rep.json"
    code = ca.main(["--snapshot", str(snap_p), "--config", str(cfg_p),
                    "--json-out", str(out)])
    assert code == 0
    assert json.loads(out.read_text())["verdict"] == "pass"

    snap["nodes"][0]["status"] = "pending"
    snap_p.write_text(json.dumps(snap), encoding="utf-8")
    assert ca.main(["--snapshot", str(snap_p), "--config", str(cfg_p),
                    "--json-out", str(out)]) == 2
    assert json.loads(out.read_text())["verdict"] == "not_ready"

    assert ca.main(["--snapshot", str(tmp_path / "absent.json")]) == 3


@pytest.mark.parametrize("status,expected", [("done", "closed"), ("FAILED", "failed"),
                                             ("in_flight", "open"), (None, "unknown")])
def test_status_bucket_vocabulary(status, expected):
    assert ca._status_bucket(status, CFG) == expected