"""验证欠账台账单测（合成域名 ``synth.*``，脚本/路径全部由夹具注入）。

覆盖三块：
* 指标抽取：列表+id 选择、直路径、ratio→pct、缺失/NaN 一律 None（不冒充 0）；
* 验证脚本执行：真跑（假 runner）、缺脚本、执行失败、输出缺字段、禁用、越界路径；
* 欠账台账：优先级/提升条件/状态机（OPEN / MEASURED / BLOCKED_OUT_OF_REPO）与汇总。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.capability_semantics.debt import (
    PRIORITY_BY_REASON,
    RUN_DISABLED,
    RUN_EXECUTED,
    RUN_FAILED,
    RUN_MISSING,
    RUN_PARSE_INCOMPLETE,
    STATUS_BLOCKED,
    STATUS_MEASURED,
    STATUS_OPEN,
    build_debt_registry,
    extract_metric,
    run_verifier,
)
from tools.capability_semantics.sources import load_semantics_config

PAYLOAD = {
    "verdict": "PARTIAL",
    "checks": [
        {"id": "A1", "worst_abs_rel_error": 0.0125},
        {"id": "A2", "worst_abs_rel_error": 3.0},
    ],
    "summary": {"max_error_pct": 7.5},
}


# ── 指标抽取 ────────────────────────────────────────────────────────────────
def test_extract_metric_ratio_to_pct_with_list_selection():
    spec = {"list_path": "checks", "id_field": "id", "id": "A1",
            "value_field": "worst_abs_rel_error", "unit": "ratio"}
    assert extract_metric(PAYLOAD, spec) == pytest.approx(1.25)


def test_extract_metric_plain_path_is_passthrough():
    assert extract_metric(PAYLOAD, {"path": "summary.max_error_pct"}) == pytest.approx(7.5)


@pytest.mark.parametrize("spec", [
    {"list_path": "checks", "id_field": "id", "id": "MISSING", "value_field": "x"},
    {"path": "summary.absent"},
    {"list_path": "not_a_list", "id_field": "id", "id": "A1", "value_field": "x"},
    {},
    None,
])
def test_extract_metric_never_invents_numbers(spec):
    assert extract_metric(PAYLOAD, spec) is None


def test_extract_metric_rejects_nan_and_bool():
    assert extract_metric({"v": float("nan")}, {"path": "v"}) is None
    assert extract_metric({"v": True}, {"path": "v"}) is None


# ── 验证脚本执行 ────────────────────────────────────────────────────────────
def _write_script(root: Path, rel: str = "tools/verify_synth.py") -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# synthetic verifier\n", encoding="utf-8")
    return rel


def _spec(script: str, **metrics) -> dict:
    return {
        "capability_id": "synth.audited",
        "label": "synth_audit",
        "script": script,
        "command": ["python3", script, "--json", "{out}"],
        "verdict": {"path": "verdict", "pass_values": ["PASS"]},
        "metrics": metrics.get("metrics") or [
            {"label": "A1_pct", "list_path": "checks", "id_field": "id", "id": "A1",
             "value_field": "worst_abs_rel_error", "unit": "ratio"},
        ],
    }


def test_run_verifier_executes_and_records_provenance(tmp_path: Path):
    script = _write_script(tmp_path)

    def runner(cmd, cwd, timeout):
        out = Path(cmd[cmd.index("--json") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(PAYLOAD), encoding="utf-8")
        return 0, "PASS", ""

    rec = run_verifier(_spec(script), tmp_path, runner=runner)
    assert rec["status"] == RUN_EXECUTED
    assert rec["metrics"] == {"A1_pct": 1.25}
    assert rec["verdict"] == "PARTIAL" and rec["verdict_pass"] is False
    assert rec["script_sha256"] and rec["output_sha256"] and rec["stdout_sha256"]
    assert rec["duration_s"] is not None and rec["executed_at"]
    assert rec["command"].startswith("python3 ")


def test_run_verifier_missing_script_is_skipped_not_faked(tmp_path: Path):
    rec = run_verifier(_spec("tools/not_there.py"), tmp_path,
                       runner=lambda *a: (_ for _ in ()).throw(AssertionError("must not run")))
    assert rec["status"] == RUN_MISSING
    assert rec["metrics"] == {} and rec["executed_at"] is None
    assert "script missing" in rec["reason"]


def test_run_verifier_disabled_does_not_execute(tmp_path: Path):
    script = _write_script(tmp_path)
    rec = run_verifier(_spec(script), tmp_path, runner=lambda *a: (0, "", ""), enabled=False)
    assert rec["status"] == RUN_DISABLED
    assert rec["metrics"] == {}


def test_run_verifier_rejects_script_outside_repo(tmp_path: Path):
    rec = run_verifier(_spec("../../etc/passwd"), tmp_path, runner=lambda *a: (0, "", ""))
    assert rec["status"] == RUN_MISSING
    assert "escapes repo root" in rec["reason"]


def test_run_verifier_exec_failure_is_explicit(tmp_path: Path):
    script = _write_script(tmp_path)

    def boom(cmd, cwd, timeout):
        raise TimeoutError("synthetic timeout")

    rec = run_verifier(_spec(script), tmp_path, runner=boom)
    assert rec["status"] == RUN_FAILED
    assert "TimeoutError" in rec["reason"]
    assert rec["metrics"] == {}


def test_run_verifier_missing_metric_marks_parse_incomplete(tmp_path: Path):
    script = _write_script(tmp_path)

    def runner(cmd, cwd, timeout):
        out = Path(cmd[cmd.index("--json") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"verdict": "PASS"}), encoding="utf-8")
        return 0, "", ""

    rec = run_verifier(_spec(script), tmp_path, runner=runner)
    assert rec["status"] == RUN_PARSE_INCOMPLETE
    assert rec["metrics"] == {} and "metrics not parsed" in rec["reason"]


# ── 欠账台账 ────────────────────────────────────────────────────────────────
def _cfg(tmp_path: Path, runs: list[dict] | None = None, out_of_repo: list[str] | None = None):
    payload = {
        "verification_runs": runs or [],
        "verification_debt": {"out_of_repo_capabilities": out_of_repo or []},
        "thresholds": {"withheld_ratio_max": 0.3},
    }
    path = tmp_path / "config" / "capability_semantics.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return load_semantics_config(path)


def _rows():
    return [
        {"capability_id": "synth.real", "domain": "d", "declared_level": "REAL",
         "derived_level": "REAL", "reason_code": "NUMERIC_PASS_VERIFIED",
         "claim_rank_gt_derived": False, "accuracy_flag": "within_pass"},
        {"capability_id": "synth.contra", "domain": "d", "declared_level": "REAL",
         "derived_level": "MOCK", "reason_code": "CONTRADICTED_BY_AUDIT",
         "claim_rank_gt_derived": True, "accuracy_flag": "above_accept"},
        {"capability_id": "synth.audited", "domain": "d", "declared_level": "REAL",
         "derived_level": "WITHHELD", "reason_code": "UNVERIFIED_IMPLEMENTATION",
         "claim_rank_gt_derived": True, "accuracy_flag": "no_numeric"},
        {"capability_id": "synth.elsewhere", "domain": "d", "declared_level": "REAL",
         "derived_level": "WITHHELD", "reason_code": "NO_CODE_DELIVERY_WITH_TEST_REFERENCE",
         "claim_rank_gt_derived": True, "accuracy_flag": "no_numeric"},
    ]


def test_debt_registry_classifies_records(tmp_path: Path):
    script = _write_script(tmp_path)
    cfg = _cfg(tmp_path, runs=[_spec(script)], out_of_repo=["synth.elsewhere"])

    def runner(cmd, cwd, timeout):
        out = Path(cmd[cmd.index("--json") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(PAYLOAD), encoding="utf-8")
        return 0, "", ""

    debt = build_debt_registry(_rows(), cfg, tmp_path, runner=runner, run_verifiers=True)
    by_id = {r["capability_id"]: r for r in debt["records"]}
    assert "synth.real" not in by_id                      # REAL 不进欠账
    assert by_id["synth.contra"]["priority"] == "P0"      # 声明高于实得 → 最急
    assert by_id["synth.contra"]["lift_condition"]
    assert by_id["synth.audited"]["status"] == STATUS_MEASURED
    assert by_id["synth.audited"]["measurements"] == {"A1_pct": 1.25}
    assert by_id["synth.audited"]["verifier_runs"] == ["synth_audit"]
    assert by_id["synth.elsewhere"]["status"] == STATUS_BLOCKED
    summary = debt["summary"]
    assert summary["debt_entries"] == 3
    assert summary["verifiers_executed"] == 1 and summary["verifiers_failed"] == 0
    assert summary["measured_ratio"] == pytest.approx(1 / 3, rel=1e-3)
    assert summary["out_of_repo_count"] == 1
    assert PRIORITY_BY_REASON["UNVERIFIED_IMPLEMENTATION"] == "P1"


def test_debt_registry_without_execution_keeps_debt_open(tmp_path: Path):
    script = _write_script(tmp_path)
    cfg = _cfg(tmp_path, runs=[_spec(script)])
    debt = build_debt_registry(_rows(), cfg, tmp_path,
                               runner=lambda *a: (0, "", ""), run_verifiers=False)
    statuses = {r["capability_id"]: r["status"] for r in debt["records"]}
    assert statuses["synth.audited"] == STATUS_OPEN
    assert debt["summary"]["verifier_execution_enabled"] is False
    assert debt["runs"][0]["status"] == RUN_DISABLED
    assert debt["summary"]["measured_ratio"] == 0.0