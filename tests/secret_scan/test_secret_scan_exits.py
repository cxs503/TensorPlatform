"""The three exits must block *before* content leaves the work tree.

These tests do not assert on "returns block"; they assert on the observable
side effect that must not happen: a commit that exists, a transport that was
called, a byte that reached the disk.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from tools.secret_scan import gates
from tools.secret_scan.calibrate import build_corpus
from tools.secret_scan.engine import Scanner
from tools.secret_scan.policy import Policy, load_policy

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POLICY = os.path.join(REPO_ROOT, "data", "secret_scan", "policy.json")


@pytest.fixture(scope="module")
def corpus():
    return build_corpus()


def _git(repo, *args):
    return subprocess.run(["git"] + list(args), cwd=repo, capture_output=True, text=True)


def _init_repo(path):
    os.makedirs(path, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "agent@example.invalid")
    _git(path, "config", "user.name", "agent")
    _git(path, "config", "commit.gpgsign", "false")


# --------------------------------------------------------------------------
# exit 1: pre-commit
# --------------------------------------------------------------------------
def test_pre_commit_blocks_staged_secret(tmp_path, corpus):
    repo = tmp_path / "repo"
    _init_repo(str(repo))
    (repo / "notes.md").write_text("ok line\n" + corpus.secrets[5].text + "\n", encoding="utf-8")
    _git(str(repo), "add", "-A")
    verdict = gates.gate_pre_commit(str(repo), policy_path=POLICY, ledger_path=str(tmp_path / "l.jsonl"))
    assert verdict.decision == "block"
    assert verdict.scan.hits
    head = _git(str(repo), "log", "--oneline").stdout.strip()
    assert head == ""


def test_pre_commit_allows_clean_staging(tmp_path):
    repo = tmp_path / "repo2"
    _init_repo(str(repo))
    (repo / "notes.md").write_text("a normal note with a sha256: " + "0" * 64 + "\n", encoding="utf-8")
    _git(str(repo), "add", "-A")
    verdict = gates.gate_pre_commit(str(repo), policy_path=POLICY, ledger_path=str(tmp_path / "l.jsonl"))
    assert verdict.decision == "allow"
    assert verdict.scan.clean


def test_pre_commit_only_judges_added_lines(tmp_path, corpus):
    repo = tmp_path / "repo3"
    _init_repo(str(repo))
    (repo / "old.md").write_text(corpus.secrets[6].text, encoding="utf-8")
    _git(str(repo), "add", "-A")
    _git(str(repo), "commit", "-q", "-m", "pre-existing content")
    (repo / "new.md").write_text("clean addition\n", encoding="utf-8")
    _git(str(repo), "add", "-A")
    verdict = gates.gate_pre_commit(str(repo), policy_path=POLICY, ledger_path=str(tmp_path / "l.jsonl"))
    assert verdict.decision == "allow"


def test_pre_commit_line_numbers_map_to_source(tmp_path, corpus):
    repo = tmp_path / "repo4"
    _init_repo(str(repo))
    (repo / "a.md").write_text("l1\nl2\n{}\nl4\n".format(corpus.secrets[7].text), encoding="utf-8")
    _git(str(repo), "add", "-A")
    verdict = gates.gate_pre_commit(str(repo), policy_path=POLICY, ledger_path=str(tmp_path / "l.jsonl"))
    assert {hit.line for hit in verdict.scan.hits} == {3}


def test_pre_commit_cli_exit_code_is_two(tmp_path, corpus):
    repo = tmp_path / "repo5"
    _init_repo(str(repo))
    (repo / "a.md").write_text(corpus.secrets[8].text, encoding="utf-8")
    _git(str(repo), "add", "-A")
    completed = subprocess.run(
        [sys.executable, "-m", "tools.secret_scan.cli", "gate-commit", "--repo", str(repo),
         "--json", str(tmp_path / "verdict.json")],
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert completed.returncode == 2
    verdict = json.loads((tmp_path / "verdict.json").read_text())
    assert verdict["verdict"] == "FAIL"
    assert verdict["decision"] == "block"


def test_pre_commit_unknown_when_git_unavailable(tmp_path):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    verdict = gates.gate_pre_commit(str(not_a_repo), policy_path=POLICY)
    assert verdict.decision == "unknown"          # fail-closed, never "assume clean"


# --------------------------------------------------------------------------
# exit 2: report gate
# --------------------------------------------------------------------------
def test_report_gate_never_invokes_transport(corpus):
    sent = []
    payload = {"title": "t", "markdown": corpus.secrets[9].text, "tags": ["x"]}
    verdict = gates.gate_report(payload, policy_path=POLICY, send=lambda body: sent.append(body))
    assert verdict.decision == "block"
    assert sent == []
    assert verdict.extra["transport_invoked"] is False


def test_report_gate_invokes_transport_when_clean():
    sent = []
    payload = {"title": "clean report", "markdown": "no credentials here", "tags": ["x"],
               "figures": [{"name": "a.png", "caption": "c", "png_base64": "iVBORw0KGgoAAAANSUhEUg=="}]}
    verdict = gates.gate_report(payload, policy_path=POLICY, send=lambda body: sent.append(body))
    assert verdict.decision == "allow"
    assert len(sent) == 1


def test_report_gate_sees_through_base64_transport(corpus):
    sent = []
    hidden = corpus.transport[0].text
    payload = {"figures": [{"name": "x.png", "png_base64": hidden}]}
    verdict = gates.gate_report(payload, policy_path=POLICY, send=lambda body: sent.append(body))
    assert verdict.decision == "block"
    assert sent == []
    assert any(hit.detector == "decoded" for hit in verdict.scan.hits)


def test_report_gate_ensure_clean_raises(corpus):
    with pytest.raises(gates.GateError):
        gates.ensure_clean_report({"markdown": corpus.secrets[10].text}, policy_path=POLICY)


def test_report_gate_payload_path_is_located(corpus):
    payload = {"a": {"b": [{"c": corpus.secrets[11].text}]}}
    verdict = gates.gate_report(payload, policy_path=POLICY)
    assert verdict.scan.hits
    assert verdict.scan.hits[0].path.startswith("payload.a.b[0]")


# --------------------------------------------------------------------------
# exit 3: artifact writer
# --------------------------------------------------------------------------
def test_artifact_writer_blocks_raw_write(tmp_path, corpus):
    raw = load_policy(POLICY).raw
    strict = json.loads(json.dumps(raw))
    strict["exits"]["artifact_writer"]["redact_on_hit"] = False
    strict_path = tmp_path / "policy_strict.json"
    strict_path.write_text(json.dumps(strict), encoding="utf-8")
    target = tmp_path / "artifact.json"
    verdict = gates.gate_artifact(str(target), '{"v": "%s"}' % corpus.secrets[12].text,
                                  policy_path=str(strict_path), ledger_path=str(tmp_path / "l.jsonl"))
    assert verdict.decision == "block"
    assert not target.exists()


def test_artifact_writer_writes_only_redacted_and_verifies(tmp_path, corpus):
    target = tmp_path / "artifact_repair.md"
    secret = corpus.secrets[13].text
    verdict = gates.gate_artifact(str(target), "value: {}\n".format(secret), policy_path=POLICY,
                                  ledger_path=str(tmp_path / "l.jsonl"))
    assert verdict.decision == "block"                 # raw content refused
    assert verdict.extra["raw_write_blocked"] is True
    assert verdict.extra["verified_clean"] is True
    written = target.read_text(encoding="utf-8")
    assert secret not in written
    assert "<REDACTED:" in written
    scanner = Scanner(load_policy(POLICY))
    assert scanner.scan_text(written, path=str(target)).clean


def test_artifact_writer_allows_clean_content(tmp_path):
    target = tmp_path / "clean.md"
    verdict = gates.gate_artifact(str(target), "a clean artifact\n", policy_path=POLICY,
                                  ledger_path=str(tmp_path / "l.jsonl"))
    assert verdict.decision == "allow"
    assert target.read_text(encoding="utf-8") == "a clean artifact\n"


def test_artifact_writer_unknown_on_bad_content_type(tmp_path):
    verdict = gates.gate_artifact(str(tmp_path / "x"), 12345, policy_path=POLICY)
    assert verdict.decision == "unknown"


# --------------------------------------------------------------------------
# ledger + fail-closed policy
# --------------------------------------------------------------------------
def test_ledger_records_events_without_content(tmp_path, corpus):
    ledger = tmp_path / "events.jsonl"
    secret = corpus.secrets[14].text
    target = tmp_path / "a.md"
    gates.gate_artifact(str(target), secret, policy_path=POLICY, ledger_path=str(ledger))
    text = ledger.read_text(encoding="utf-8")
    assert secret not in text
    entry = json.loads(text.strip().splitlines()[-1])
    assert entry["exit"] == "artifact_writer"
    assert entry["decision"] == "block"


def test_gates_fail_closed_with_missing_policy(tmp_path):
    verdict = gates.gate_report({"a": "b"}, policy_path=str(tmp_path / "missing.json"))
    assert verdict.decision == "unknown"
    assert verdict.blocked is True