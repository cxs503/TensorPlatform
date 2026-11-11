"""Redaction and no-echo invariants (C4 + C6 at unit level)."""

from __future__ import annotations

import json
import os
import re

import pytest

from tools.secret_scan import gates
from tools.secret_scan.calibrate import build_corpus
from tools.secret_scan.engine import Scanner
from tools.secret_scan.policy import load_policy
from tools.secret_scan.redact import redact_text

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POLICY = os.path.join(REPO_ROOT, "data", "secret_scan", "policy.json")
PLACEHOLDER = re.compile(r"<REDACTED:[^<>]+>")


@pytest.fixture(scope="module")
def scanner():
    return Scanner(load_policy(POLICY))


@pytest.fixture(scope="module")
def corpus():
    return build_corpus()


def test_redaction_is_idempotent_for_every_injected_case(scanner, corpus):
    for case in corpus.secrets + corpus.transport:
        once, _ = scanner.redact(case.text, path="c4.txt")
        assert scanner.scan_text(once, path="c4.txt").clean, case.label
        twice, _ = scanner.redact(once, path="c4.txt")
        assert twice == once, case.label


def test_redaction_removes_value_and_keeps_context(scanner, corpus):
    case = [c for c in corpus.secrets if c.label == "password_kv"][0]
    redacted, _ = scanner.redact(case.text, path="c4.txt")
    assert case.text not in redacted
    assert redacted.startswith('password = "')
    assert redacted.endswith('"')


def test_placeholder_encodes_type_and_length(scanner, corpus):
    case = [c for c in corpus.secrets if c.label == "openai_key"][0]
    redacted, scan = scanner.redact(case.text, path="c4.txt")
    placeholders = PLACEHOLDER.findall(redacted)
    assert len(placeholders) == len({hit.fingerprint for hit in scan.hits})
    for placeholder in placeholders:
        _, type_part, length_part = placeholder.rstrip(">").split(":")
        assert type_part.strip("_")
        assert int(length_part) > 0


def test_redaction_handles_several_secrets_on_one_line(scanner, corpus):
    text = "a={} b={}".format(corpus.secrets[0].text, corpus.secrets[1].text)
    redacted, _ = scanner.redact(text, path="c4.txt")
    assert len(PLACEHOLDER.findall(redacted)) == 2
    assert scanner.scan_text(redacted, path="c4.txt").clean


def test_redaction_of_clean_text_is_identity(scanner):
    text = "nothing to see here"
    redacted, scan = scanner.redact(text, path="c4.txt")
    assert redacted == text and scan.clean


def test_verdict_json_never_contains_scanned_content(tmp_path, corpus):
    secret = corpus.secrets[4].text
    verdict = gates.gate_report({"markdown": secret}, policy_path=POLICY)
    blob = json.dumps(verdict.to_dict(), ensure_ascii=False)
    assert secret not in blob
    windows = {secret[i:i + 8] for i in range(0, len(secret) - 8)}
    assert not [window for window in windows if window in blob]


def test_artifact_verdict_never_contains_raw_content(tmp_path, corpus):
    secret = corpus.secrets[15].text
    target = tmp_path / "a.json"
    verdict = gates.gate_artifact(str(target), "x=" + secret, policy_path=POLICY,
                                  ledger_path=str(tmp_path / "l.jsonl"))
    blob = json.dumps(verdict.to_dict(), ensure_ascii=False)
    assert secret not in blob
    assert secret not in target.read_text(encoding="utf-8")


def test_select_spans_prefers_known_value(scanner, corpus):
    hits = scanner.scan_text(corpus.secrets[0].text, path="x").hits
    redacted, applied = redact_text(corpus.secrets[0].text, hits,
                                   template="<REDACTED:{type}:{len}>")
    assert applied and redacted.count("<REDACTED:") >= 1
    assert scanner.scan_text(redacted, path="x").clean


def test_typed_redaction_is_stable_across_policies():
    policy = load_policy(POLICY)
    text = build_corpus().secrets[0].text
    redacted_a, _ = Scanner(policy).redact(text, path="p")
    redacted_b, _ = Scanner(policy).redact(redacted_a, path="p")
    assert redacted_a == redacted_b