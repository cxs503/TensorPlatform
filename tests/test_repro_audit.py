"""repro_audit 单测 —— 全部用合成夹具（无任何学科/仓名领域词，符合平台铁律）。"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from tools.repro_audit import core
from tools.repro_audit import persist


# ─────────────────────────── 判据口径 ───────────────────────────

def test_parse_tolerance_reads_percent_and_sci():
    assert core.parse_tolerance(["误差<5%"]) == pytest.approx(0.05)
    assert core.parse_tolerance(["≤2% 与 <1e-9"]) == pytest.approx(1e-9)
    assert core.parse_tolerance(["误差 < 5%", "另一判据 < 1%"]) == pytest.approx(0.01)
    assert core.parse_tolerance([]) == core.OBS_REL_TOL


def test_classify_value_observed_rel_and_diagnostic_abs():
    obs = core.classify_value(1.0, 1.0 + 5e-7)
    assert obs["mode"] == "observed" and obs["ok"] is True
    obs_bad = core.classify_value(1.0, 1.001)
    assert obs_bad["mode"] == "observed" and obs_bad["ok"] is False
    diag = core.classify_value(1e-12, 5e-13)
    assert diag["mode"] == "diagnostic" and diag["ok"] is True
    diag_bad = core.classify_value(1e-12, 1e-9)
    assert diag_bad["mode"] == "diagnostic" and diag_bad["ok"] is False


def test_classify_value_rejects_vanishing_recompute_on_observed_quantity():
    res = core.classify_value(10.0, 0.0)
    assert res["mode"] == "observed" and res["ok"] is False


def test_flatten_numbers_skips_bool_and_nonfinite():
    flat = core.flatten_numbers({"a": 1, "flag": True, "list": [1.5, None], "bad": float("nan")})
    assert flat == {"a": 1.0, "list[0]": 1.5}


def test_compare_and_agreement_counts():
    items = core.compare_number_maps({"x": 2.0, "y": 0.0}, {"x": 2.0 + 1e-9, "y": 0.0, "z": 1.0})
    assert [i["path"] for i in items] == ["x", "y"]
    agg = core.agreement(items)
    assert agg == {"checked": 2, "passed": 2, "drifted": 0,
                   "by_mode": {"observed": 1, "diagnostic": 1}, "rate": 1.0}


# ─────────────────────────── 抽样 ───────────────────────────

def _node(nid, repo, year, mid=None):
    return {"id": nid, "mission_id": mid or nid, "status": "done",
            "actual_end": f"{year}-05-01T00:00:00Z",
            "metadata": {"repo": repo, "acceptance_criteria": ["<1%"]}}


def test_sample_stratified_is_deterministic_and_round_robins_repos():
    nodes = [_node(i, "repoA" if i % 3 else "repoB", 2025 + i % 2) for i in range(1, 61)]
    first = core.sample_stratified(nodes, 8, seed="s")
    second = core.sample_stratified(list(reversed(nodes)), 8, seed="s")
    assert [n["id"] for n in first] == [n["id"] for n in second]
    repos = {n["metadata"]["repo"] for n in first}
    assert repos == {"repoA", "repoB"}          # 小仓不被大仓淹没
    assert len({(n["metadata"]["repo"], n["actual_end"][:4]) for n in first}) == 4


def test_sample_stratified_respects_cap_and_n():
    nodes = [_node(i, "repoA", 2025) for i in range(1, 20)]
    out = core.sample_stratified(nodes, 10, per_stratum_cap=3)
    assert len(out) == 3


def test_sample_stratified_predicate_filters():
    nodes = [_node(i, "repoA", 2025) for i in range(1, 10)]
    out = core.sample_stratified(nodes, 5, predicate=lambda n: n["id"] % 2 == 0)
    assert {n["id"] for n in out} <= {2, 4, 6, 8}


# ─────────────────────────── 执行判读 ───────────────────────────

def test_parse_pytest_extracts_counts_and_cases():
    out = ("FAILED tests/test_a.py::test_one\n"
           "1 failed, 3 passed, 2 warnings in 0.10s\n")
    parsed = core.parse_pytest(out, "")
    assert parsed["counts"]["failed"] == 1 and parsed["counts"]["passed"] == 3
    assert parsed["failing_cases"] == ["tests/test_a.py::test_one"]
    assert parsed["collected"] == 4


def test_verdict_of_blocked_on_timeout_and_missing_dep():
    timeout = {"run": {"timed_out": True, "stdout": "", "stderr": ""}, "parsed": {"collected": 3}}
    assert core.verdict_of(timeout) == "blocked"
    dep = {"run": {"timed_out": False, "exit_code": 2, "stdout": "",
                   "stderr": "ModuleNotFoundError: No module named 'zzz'"}, "parsed": {"collected": 0}}
    assert core.verdict_of(dep) == "blocked"


def test_verdict_of_pass_and_fail_and_drift():
    ok = {"run": {"timed_out": False, "exit_code": 0, "stdout": "2 passed", "stderr": ""},
          "parsed": {"collected": 2}}
    assert core.verdict_of(ok) == "pass"
    fail = {"run": {"timed_out": False, "exit_code": 1, "stdout": "1 failed, 1 passed", "stderr": ""},
            "parsed": {"collected": 2}, "reproducible": True,
            "attribution": {"label": "environment"}}
    assert core.verdict_of(fail) == "fail"
    drift = dict(fail, attribution={"label": "float_path"})
    assert core.verdict_of(drift) == "drift"


def test_attribute_drift_dependency_before_float():
    run = {"stdout": "assert 0.1 == 0.2", "stderr": ""}
    attr = core.attribute_drift(run, run, {"numpy": "1.24.0", "python_version": "3.11"},
                                {"numpy": "2.1.0", "python_version": "3.11"})
    assert attr["label"] == "dependency_version"
    attr2 = core.attribute_drift(run, run, {"numpy": "1.24.0"}, {"numpy": "1.24.0"})
    assert attr2["label"] == "float_path" and attr2["evidence"]["reproducible_reruns"] == 2
    attr3 = core.attribute_drift({"stdout": "AssertionError: boom", "stderr": ""}, run,
                                 {"cpu_count": 8}, {"cpu_count": 128})
    assert attr3["label"] == "environment"


# ─────────────────────────── KPI 与一致性 ───────────────────────────

def _item(verdict, mid):
    return {"mission_id": mid, "verdict": verdict}


def test_kpi_excludes_blocked_from_denominator():
    items = [_item("pass", 1), _item("pass", 2), _item("drift", 3), _item("fail", 4), _item("blocked", 5)]
    kpi = core.kpi_from_items(items)
    assert kpi["counts"] == {"pass": 2, "drift": 1, "fail": 1, "blocked": 1}
    assert kpi["denominator"] == 4
    assert kpi["repro_rate"] == pytest.approx(0.5)
    assert kpi["blocked_share"] == pytest.approx(0.2)


def test_kpi_consistent_tolerance_is_1e9():
    items = [_item("pass", 1), _item("fail", 2)]
    assert core.kpi_consistent(0.5, items)
    assert not core.kpi_consistent(0.5 + 1e-6, items)
    assert core.kpi_consistent(None, [])


def test_persist_payload_recomputes_rate_and_hash_is_stable():
    summary = {"items": [_item("pass", 1), _item("drift", 2), _item("blocked", 3)]}
    p1 = persist.build_payload(summary)
    p2 = persist.build_payload({"items": list(reversed(summary["items"]))})
    assert p1["metric_value"] == pytest.approx(0.5)
    assert p1["items_sha256"] == p2["items_sha256"]      # 明细哈希对顺序不敏感
    assert "ON CONFLICT" in persist.upsert_sql(p1)


def test_persist_refuses_when_no_countable_items():
    with pytest.raises(ValueError):
        persist.build_payload({"items": [_item("blocked", 1)]})


def test_persist_kpi_writes_pending_artifact(tmp_path, monkeypatch):
    monkeypatch.delenv("TP_PG_DSN", raising=False)
    monkeypatch.delenv("TP_PG_PASSWORD", raising=False)
    summary = {"items": [_item("pass", 1), _item("blocked", 2)]}
    sql_out = str(tmp_path / "kpi_upsert.sql")
    result = persist.persist_kpi(summary, sql_out=sql_out)
    assert result["status"] in ("pending", "persisted")     # 环境里有 API 时可能真写成功
    assert os.path.exists(sql_out)
    assert json.load(open(tmp_path / "kpi_pending.json"))["metric_value"] == pytest.approx(1.0)


# ─────────────────────────── 端到端：干净检出 + 执行 ───────────────────────────

SYNTH_TEST = """\
def test_identity():
    assert 1.0 * 3 == 3.0
"""


def _init_repo(path):
    os.makedirs(os.path.join(path, "tests"), exist_ok=True)
    os.makedirs(os.path.join(path, "artifacts", "m7"), exist_ok=True)
    with open(os.path.join(path, "tests", "test_alpha.py"), "w") as fh:
        fh.write(SYNTH_TEST)
    with open(os.path.join(path, "artifacts", "m7", "report.json"), "w") as fh:
        json.dump({"alpha": 3.0, "beta": 0.0, "flag": True}, fh)
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "commit", "-q", "-m", "synthetic fixture"]):
        subprocess.run(cmd, cwd=path, check=True, env=env, capture_output=True)


def test_clean_export_excludes_uncommitted_changes(tmp_path):
    repo = str(tmp_path / "repo")
    _init_repo(repo)
    with open(os.path.join(repo, "tests", "test_alpha.py"), "a") as fh:
        fh.write("# dirty working tree\n")
    dest = str(tmp_path / "export")
    core.clean_export(repo, dest)
    assert "# dirty working tree" not in open(os.path.join(dest, "tests", "test_alpha.py")).read()


def test_audit_node_end_to_end_pass_and_artifact_anchor(tmp_path):
    from tools.repro_audit import cli

    repo = str(tmp_path / "repo")
    _init_repo(repo)
    node = {"id": 1, "mission_id": 7, "name": "synthetic", "status": "done",
            "actual_end": "2026-01-02T00:00:00Z",
            "metadata": {"repo": "repoFixture", "acceptance_criteria": ["<1%"], "ext_id": "x-1"}}
    cand = {"node": node, "workdir": repo, "repo": "repoFixture"}
    record = cli.audit_node(cand)
    assert record["verdict"] == "pass", record
    assert record["commit"] and record["tree_sha"]
    assert [a["path"] for a in record["anchored_artifacts"]] == ["artifacts/m7/report.json"]
    assert len(record["anchored_artifacts"][0]["sha256"]) == 64
    # 数值层：测试未重写工件 → blocked（不进通过率分母），不得伪造 pass
    assert record["numeric"]["status"] == "blocked"
    assert record["numeric"]["reason"] == "artifact_not_regenerated_by_tests"


def test_run_command_timeout_is_reported(tmp_path):
    res = core.run_command([sys.executable, "-c", "import time; time.sleep(5)"],
                           str(tmp_path), timeout=1)
    assert res["timed_out"] is True and res["exit_code"] is None