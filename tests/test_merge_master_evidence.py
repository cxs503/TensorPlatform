"""Regression checks for current-head evidence and the execute opt-in boundary."""
import os
from pathlib import Path
import subprocess

import pytest

from tools.merge_master import gates as G
from tools.merge_master.cli import _build_reconciler, build_parser
from tools.merge_master.model import ACTION_LEAVE, Reason
from tools.merge_master.providers import GitHub
from test_merge_master import CLEAN_DIFF, REPO, make, mission, mk_pr


def check(**changes):
    return dict({"id": 1, "name": "unit-tests", "head_sha": "sha1",
                 "status": "completed", "conclusion": "success"}, **changes)


@pytest.mark.parametrize("checks", [
    [], [check(head_sha="old")], [check(head_sha=None)],
    [check(status="queued", conclusion=None)],
    [check(status="in_progress", conclusion="success")],
    [check(conclusion="failure")], [check(conclusion="cancelled")],
    [check(conclusion="timed_out")], [check(conclusion="action_required")],
    [check(conclusion=None)], [check(conclusion="skipped")],
    [check(conclusion="neutral")], [check(name="lint")],
])
def test_bad_evidence_never_merges_even_with_a_passed_report(tmp_path, checks):
    pr = mk_pr(1)
    m = mission(1, checks="10000 passed")
    rec, ledger, _ = make(tmp_path, [pr], {pr.key: m},
                          checks={(REPO, pr.head_sha): checks})
    decision = rec.run_cycle().decisions[0]
    assert decision.action == ACTION_LEAVE
    assert decision.reasons
    assert rec.merger.calls == []
    assert ledger.writes == []
    assert not all(g.ok for g in G.GateSuite().evaluate(
        pr, m, checks, diff_text=CLEAN_DIFF))


def test_missing_head_cannot_be_used_as_binding():
    assert not G.gate_quality([check(head_sha="")]).ok
    assert not G.gate_tests(mission(1), [check(head_sha="")]).ok


def test_main_failure_is_diagnostic_not_a_waiver(tmp_path):
    pr = mk_pr(1)
    rec, ledger, _ = make(tmp_path, [pr], {pr.key: mission(1)},
        checks={(REPO, pr.head_sha): [check(conclusion="failure")]})
    rec.checks.baseline = {REPO: ["unit-tests"]}
    d = rec.run_cycle().decisions[0]
    assert d.action == ACTION_LEAVE and Reason.QUALITY_RED in d.reasons
    assert rec.merger.calls == [] and ledger.writes == []


@pytest.mark.parametrize("critical", [None, "skipped", "neutral", "failure"])
def test_required_check_must_be_present_and_successful(tmp_path, critical):
    pr = mk_pr(1)
    checks = [check()]
    if critical is not None:
        checks.append(check(id=2, name="boundary", conclusion=critical))
    rec, ledger, _ = make(tmp_path, [pr], {pr.key: mission(1)},
                          checks={(REPO, pr.head_sha): checks})
    rec.gates = G.GateSuite(required_checks=["unit-tests", "boundary"])
    d = rec.run_cycle().decisions[0]
    assert d.action == ACTION_LEAVE and Reason.QUALITY_RED in d.reasons
    assert rec.merger.calls == [] and ledger.writes == []
    suite = rec.gates.evaluate(pr, mission(1), checks, diff_text=CLEAN_DIFF)
    quality = next(g for g in suite if g.gate == G.G2)
    assert not quality.ok


def test_successful_current_checks_pass_both_entry_points(tmp_path):
    pr = mk_pr(1)
    checks = [check(), check(id=2, name="boundary"),
              check(id=3, name="optional-docs", conclusion="skipped")]
    rec, _, _ = make(tmp_path, [pr], {pr.key: mission(1, checks="")},
                     checks={(REPO, pr.head_sha): checks})
    rec.gates = G.GateSuite(required_checks=["unit-tests", "boundary"])
    assert all(g.ok for g in rec.evaluate(pr))
    assert all(g.ok for g in rec.gates.evaluate(
        pr, mission(1, checks=""), checks, diff_text=CLEAN_DIFF))


def test_cli_required_checks_reach_reconciler(tmp_path):
    args = build_parser().parse_args([
        "plan", "--outdir", str(tmp_path), "--required-check", "unit-tests",
        "--required-check", "boundary"])
    rec = _build_reconciler(args, {"gh": object(), "ledger": object(),
                                   "audit": object()}, True, [])
    assert rec.gates.required_checks == ("unit-tests", "boundary")


@pytest.mark.parametrize("invalid", ["unit-tests", [""], [" "], [None]])
def test_invalid_required_check_configuration_is_rejected(invalid):
    with pytest.raises(ValueError):
        G.GateSuite(required_checks=invalid)


class ScriptedHttp:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.urls = []

    def request(self, url, **kwargs):
        self.urls.append(url)
        return next(self.responses)


def page(checks, total):
    return {"status": 200, "json": {"total_count": total, "check_runs": checks}}


def test_failed_101st_check_is_not_lost():
    http = ScriptedHttp([
        page([check(id=i) for i in range(1, 101)], 101),
        page([check(id=101, name="critical", conclusion="failure")], 101),
    ])
    checks = GitHub(http=http, token="").check_runs(REPO, "sha1")
    assert len(checks) == 101
    assert not G.gate_quality(checks, head_sha="sha1").ok
    assert all("filter=latest" in url for url in http.urls)
    assert http.urls[1].endswith("page=2")


@pytest.mark.parametrize("second", [
    {"status": 503}, {"status": 200, "json": {}},
    page([], 101), page([check(id=1)], 101),
    page([check(id=101)], 102),
])
def test_partial_or_changing_results_cannot_return_a_green_prefix(second):
    http = ScriptedHttp([page([check(id=i) for i in range(1, 101)], 101), second])
    checks = GitHub(http=http, token="").check_runs(REPO, "sha1")
    assert not G.gate_quality(checks, head_sha="sha1").ok
    assert not G.gate_tests(mission(1), checks, head_sha="sha1").ok


@pytest.mark.parametrize("response", [
    {"status": 401}, {"status": 0}, {"status": 200, "json": []},
    page([None], 1), page([{"name": "unit-tests"}], 1),
    page([check()], "1"), page([check()], 0),
    page([check(), check()], 2),
])
def test_unavailable_or_malformed_response_blocks(response):
    checks = GitHub(http=ScriptedHttp([response]), token="").check_runs(REPO, "sha1")
    assert not G.gate_quality(checks, head_sha="sha1").ok


def test_empty_response_is_missing_evidence():
    checks = GitHub(http=ScriptedHttp([page([], 0)]), token="").check_runs(REPO, "sha1")
    assert checks == []
    assert G.gate_quality(checks, head_sha="sha1").evidence["reason"] == Reason.QUALITY_MISSING


def run_daemon(tmp_path, value, mode, repos=None):
    # Intercept the interpreter, exercising the real shell entry point without
    # credentials, network, a merge or a platform database write.
    shim = tmp_path / "python3"
    shim.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("TP_MM_")}
    env.update(PATH=str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
               TP_REPO_ROOT=str(tmp_path))
    if value is not None:
        env["TP_MM_ALLOW_EXECUTE"] = value
    if repos is not None:
        env["TP_MM_REPOS"] = repos
    script = Path(__file__).resolve().parents[1] / "ops/merge_master/daemon.sh"
    return subprocess.run(["bash", str(script), mode], env=env,
                          capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize("value", [None, "", "0", "1", "false", "true", "01"])
@pytest.mark.parametrize("mode", ["loop", "execute"])
def test_daemon_execution_requires_literal_one(tmp_path, value, mode):
    result = run_daemon(tmp_path, value, mode)
    assert ("--execute" in result.stdout.splitlines()) == (value == "1")
    assert result.returncode == (2 if mode == "execute" and value != "1" else 0)


@pytest.mark.parametrize("repos", [
    "--execute", "TensorPlatform --execute", "Tensor*", " ", ".", "..",
    "TensorPlatform\n--execute", "TensorPlatform\t--execute",
])
@pytest.mark.parametrize("mode", ["loop", "plan", "selfcheck"])
def test_repository_list_cannot_inject_execution_options(tmp_path, repos, mode):
    result = run_daemon(tmp_path, "0", mode, repos)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "refused" in result.stderr


def test_multiple_repositories_remain_separate_arguments(tmp_path):
    result = run_daemon(tmp_path, "0", "loop", "TensorPlatform TensorBench")
    assert result.returncode == 0
    args = result.stdout.splitlines()
    start = args.index("--repos")
    assert args[start + 1:start + 3] == ["TensorPlatform", "TensorBench"]
    assert "--execute" not in args
