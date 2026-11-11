"""Detection-route unit tests (pattern / entropy / known-value + whitelist).

Every synthetic value used here is generated at runtime by
``tools.secret_scan.calibrate`` — the test suite must not become the leak it
guards against.
"""

from __future__ import annotations

import os

import pytest

from tools.secret_scan.calibrate import build_corpus
from tools.secret_scan.detectors import scan_entropy, scan_patterns, compile_rules, shannon_entropy
from tools.secret_scan.engine import Scanner
from tools.secret_scan.policy import Policy, PolicyError, load_policy


@pytest.fixture(scope="module")
def policy_path() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "data", "secret_scan", "policy.json")


@pytest.fixture(scope="module")
def scanner(policy_path: str) -> Scanner:
    return Scanner(load_policy(policy_path))


@pytest.fixture(scope="module")
def corpus():
    return build_corpus()


def test_shannon_entropy_bounds():
    from tools.secret_scan.calibrate import _b64, _hex

    assert shannon_entropy("") == 0.0
    assert shannon_entropy("aaaaaaaa") == 0.0
    assert 3.0 < shannon_entropy(_hex("unit", "hex32", 32)) < 4.5
    # random base64 (generated, never stored) must clear the 4.0 bit/char gate
    assert shannon_entropy(_b64("unit", "key24", 18)) > 4.0


def test_pattern_route_detects_shaped_report(policy_path, corpus):
    scanner = Scanner(load_policy(policy_path))
    shaped = [case for case in corpus.secrets if case.kind in ("url", "header", "kv", "bare")
              and case.label not in {"bare_key_32", "bare_key_43", "bare_key_24", "bare_key_64"}]
    missed = [case.label for case in shaped if not scanner.scan_text(case.text).hits]
    assert missed == []


def test_entropy_route_detects_all_bare_keys(scanner, corpus):
    bare = [case for case in corpus.secrets if case.label.startswith("bare_key")]
    assert len(bare) == 4
    for case in bare:
        result = scanner.scan_text(case.text, path="bare.txt")
        types = {hit.type for hit in result.hits}
        assert "high_entropy_string" in types, case.label
        assert all(hit.confidence in ("low", "high") for hit in result.hits)


def test_entropy_route_rejects_digests_and_identifiers(scanner, corpus):
    for case in corpus.benign:
        assert scanner.scan_text(case.text, path="benign.txt").clean, case.label


def test_known_value_route_matches_exact_value(policy_path, corpus):
    raw = load_policy(policy_path).raw
    secret = corpus.secrets[0].text
    raw = dict(raw)
    raw["detectors"] = dict(raw["detectors"])
    raw["detectors"]["known_values"] = {"enabled": True, "required": False, "min_length": 8,
                                       "sources": [{"env": "SECRET_SCAN_TEST_KNOWN", "label": "unit-test"}]}
    os.environ["SECRET_SCAN_TEST_KNOWN"] = secret
    try:
        scanner = Scanner(Policy(raw))
        result = scanner.scan_text("harmless prefix {} suffix".format(secret), path="kv.txt")
        assert any(hit.detector == "known_value" for hit in result.hits)
    finally:
        os.environ.pop("SECRET_SCAN_TEST_KNOWN", None)


def test_known_value_required_but_empty_fails_closed(policy_path):
    raw = dict(load_policy(policy_path).raw)
    raw["detectors"] = dict(raw["detectors"])
    raw["detectors"]["known_values"] = {"enabled": True, "required": True, "min_length": 8, "sources": []}
    with pytest.raises(PolicyError):
        Policy(raw)


def test_whitelist_value_classes_only_silence_entropy(scanner, corpus):
    digest = corpus.benign[0].text          # sha256 hex
    assert scanner.scan_text(digest).clean
    # the same value inside a kv secret context must still be reported
    result = scanner.scan_text('client_secret = "{}"'.format(digest))
    assert result.hits and all(hit.detector != "entropy" for hit in result.hits)


def test_whitelist_fixture_path_suppresses_but_other_paths_do_not(scanner, corpus):
    pem = [case for case in corpus.secrets if case.label == "pem_private_key"][0]
    fixture_path = "tests/fixtures/sample_key.txt"
    normal_path = "docs/report.md"
    assert scanner.scan_text(pem.text, path=fixture_path).clean
    assert not scanner.scan_text(pem.text, path=normal_path).clean


def test_inline_marker_alone_is_not_a_backdoor(scanner, corpus):
    pem = [case for case in corpus.secrets if case.label == "pem_private_key"][0]
    text = "# secret-scan:allow\n" + pem.text
    assert not scanner.scan_text(text, path="docs/report.md").clean


def test_placeholder_is_not_rescanned(scanner):
    text = 'password = "<REDACTED:password_kv:22>"'
    assert scanner.scan_text(text, path="doc.md").clean


def test_policy_missing_fails_closed():
    with pytest.raises(PolicyError):
        load_policy("/nonexistent/policy.json")


def test_policy_validation_rejects_empty_patterns(policy_path):
    raw = dict(load_policy(policy_path).raw)
    raw["detectors"] = dict(raw["detectors"])
    raw["detectors"]["patterns"] = {"enabled": True, "rules": []}
    import json
    import tempfile

    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(raw, handle)
    handle.close()
    try:
        with pytest.raises(PolicyError):
            load_policy(handle.name)
    finally:
        os.unlink(handle.name)


def test_baseline_profile_is_naive(scanner, policy_path, corpus):
    from tools.secret_scan.policy import Policy

    baseline = Scanner(Policy(load_policy(policy_path).raw).with_profile("baseline"))
    bare = [case for case in corpus.secrets if case.label.startswith("bare_key")][0]
    assert scanner.scan_text(bare.text).hits
    assert baseline.scan_text(bare.text).clean      # baseline has no entropy route


def test_entropy_route_skips_long_opaque_blobs(policy_path):
    from tools.secret_scan.calibrate import _b64

    raw = load_policy(policy_path).raw
    cfg = dict(raw["detectors"]["entropy"])
    long_token = _b64("unit", "blob", 400)
    assert len(long_token) > cfg["max_length"]
    assert scan_entropy(long_token, cfg) == []


def test_pattern_rule_needs_entropy_guard(scanner):
    # a config path assigned to a KEY-ish name must not be reported
    assert scanner.scan_text("SECRET_SCAN_POLICY=data/secret_scan/policy.json").clean


def test_scan_reports_line_numbers(scanner, corpus):
    secret = corpus.secrets[0].text
    text = "line one\nline two\n{}\n".format(secret)
    hits = scanner.scan_text(text, path="x.txt").hits
    assert hits and hits[0].line == 3