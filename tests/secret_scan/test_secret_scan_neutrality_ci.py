"""Zero-domain-semantics and CI/CD maturation checks (C7 + C8)."""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys

import pytest

from tools.secret_scan.calibrate import build_corpus
from tools.secret_scan.engine import Scanner
from tools.secret_scan.policy import Policy, load_policy

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POLICY = os.path.join(REPO_ROOT, "data", "secret_scan", "policy.json")
PKG = os.path.join(REPO_ROOT, "tools", "secret_scan")


@pytest.fixture(scope="module")
def raw_policy():
    return load_policy(POLICY).raw


def test_module_sources_contain_no_domain_vocabulary(raw_policy):
    forbidden = [term.lower() for term in raw_policy["neutrality"]["forbidden_terms"]]
    leaks = []
    for name in sorted(os.listdir(PKG)):
        if not name.endswith(".py"):
            continue
        text = open(os.path.join(PKG, name), "r", encoding="utf-8", errors="replace").read().lower()
        for term in forbidden:
            if re.search(r"\b{}\b".format(re.escape(term)), text):
                leaks.append((name, term))
    assert leaks == []


def test_policy_config_contains_no_domain_vocabulary(raw_policy):
    forbidden = [term.lower() for term in raw_policy["neutrality"]["forbidden_terms"]]
    text = open(POLICY, "r", encoding="utf-8").read().lower()
    text = re.sub(r'"forbidden_terms"\s*:\s*\[[^\]]*\]', "", text)
    leaks = [term for term in forbidden if re.search(r"\b{}\b".format(re.escape(term)), text)]
    assert leaks == []


def test_custom_pattern_can_be_injected(raw_policy):
    injected = copy.deepcopy(raw_policy)
    injected["detectors"]["patterns"]["rules"].append(
        {"id": "custom_token_format", "regex": r"\bCUSTOM-[0-9]{4}-[A-Z]{4}\b"})
    scanner = Scanner(Policy(injected))
    assert scanner.scan_text("token: CUSTOM-1247-ABCD").hits
    assert Scanner(Policy(raw_policy)).scan_text("token: CUSTOM-1247-ABCD").clean


def test_entropy_threshold_is_injected(raw_policy):
    corpus = build_corpus()
    bare = [case for case in corpus.secrets if case.label.startswith("bare_key")]
    raised = copy.deepcopy(raw_policy)
    raised["detectors"]["entropy"]["min_bits_per_char"] = 5.9
    scanner_raised = Scanner(Policy(raised))
    assert all(scanner_raised.scan_text(case.text).clean for case in bare)
    assert all(Scanner(Policy(raw_policy)).scan_text(case.text).hits for case in bare)


def test_whitelist_classes_are_injected(raw_policy):
    # a digest only becomes an entropy candidate at a relaxed threshold, so the
    # injection is demonstrated there (mixed-case hex matches the sha256 class)
    relaxed = copy.deepcopy(raw_policy)
    relaxed["detectors"]["entropy"]["min_bits_per_char"] = 3.5
    off = copy.deepcopy(relaxed)
    off["whitelist"]["enabled"] = False
    digest = "".join(ch.upper() if index % 2 == 0 else ch
                     for index, ch in enumerate(build_corpus().benign[3].text))  # uuid class
    assert Scanner(Policy(relaxed)).scan_text(digest).clean
    assert not Scanner(Policy(off)).scan_text(digest).clean


def test_decoder_depth_is_injected(raw_policy):
    transport = build_corpus().transport[0]
    no_decode = copy.deepcopy(raw_policy)
    no_decode["decoders"]["enabled"] = False
    assert Scanner(Policy(no_decode)).scan_text(transport.text, path="payload").clean
    assert Scanner(Policy(raw_policy)).scan_text(transport.text, path="payload").hits


def test_ci_workflow_maturation_artifacts_exist():
    workflow = os.path.join(REPO_ROOT, ".github", "workflows", "secret-scan.yml")
    assert os.path.isfile(workflow)
    text = open(workflow, "r", encoding="utf-8").read()
    assert "secret_scan.cli ci" in text
    assert "tests/secret_scan" in text
    hook = os.path.join(REPO_ROOT, "tools", "secret_scan", "hooks", "pre-commit")
    assert os.path.isfile(hook)
    makefile = open(os.path.join(REPO_ROOT, "Makefile"), "r", encoding="utf-8").read()
    assert "secret-scan:" in makefile


def test_cli_ci_is_green():
    completed = subprocess.run([sys.executable, "-m", "tools.secret_scan.cli", "ci", "--quiet"],
                               cwd=REPO_ROOT, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr[-800:]


def test_cli_scan_exit_codes(tmp_path):
    clean = tmp_path / "clean.txt"
    clean.write_text("nothing sensitive", encoding="utf-8")
    ok = subprocess.run([sys.executable, "-m", "tools.secret_scan.cli", "scan", "--path", str(clean),
                         "--json", str(tmp_path / "clean.json")], cwd=REPO_ROOT,
                        capture_output=True, text=True)
    assert ok.returncode == 0
    dirty = tmp_path / "dirty.txt"
    dirty.write_text(build_corpus().secrets[4].text, encoding="utf-8")
    blocked = subprocess.run([sys.executable, "-m", "tools.secret_scan.cli", "scan", "--path", str(dirty),
                              "--json", str(tmp_path / "dirty.json")], cwd=REPO_ROOT,
                             capture_output=True, text=True)
    assert blocked.returncode == 2
    verdict = json.loads((tmp_path / "dirty.json").read_text())
    assert verdict["clean"] is False


def test_cli_gate_report_reads_payload_file(tmp_path):
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"markdown": build_corpus().secrets[6].text}), encoding="utf-8")
    completed = subprocess.run([sys.executable, "-m", "tools.secret_scan.cli", "gate-report",
                                "--payload", str(payload), "--json", str(tmp_path / "v.json")],
                               cwd=REPO_ROOT, capture_output=True, text=True)
    assert completed.returncode == 2
    assert json.loads((tmp_path / "v.json").read_text())["verdict"] == "FAIL"


def test_cli_scan_of_own_sources_is_clean():
    hits = 0
    scanner = Scanner(load_policy(POLICY))
    for name in sorted(os.listdir(PKG)):
        if name.endswith(".py"):
            text = open(os.path.join(PKG, name), "r", encoding="utf-8").read()
            hits += len(scanner.scan_text(text, path="tools/secret_scan/" + name).hits)
    assert hits == 0