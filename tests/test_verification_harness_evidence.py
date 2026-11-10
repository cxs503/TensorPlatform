"""Evidence-bundle tests: sha256 per case, environment fingerprint, tamper detection.

Criterion A3 is pinned by brute force: single-byte mutations are applied at
random positions of the recorded artifacts and *every* mutation must be caught.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from tools.verification_harness.evidence import (
    CASES_DIRNAME,
    MANIFEST_NAME,
    TAMPER_CHAIN,
    TAMPER_CONTENT,
    TAMPER_STRUCTURAL,
    canonical_json,
    dependency_versions,
    environment_digest,
    environment_fingerprint,
    sha256_bytes,
    sha256_file,
    sha256_text,
    verify_bundle,
    verification_markdown,
    write_bundle,
)
from tools.verification_harness.registry import CaseRegistry
from tools.verification_harness.runner import run_registry

REPO = Path(__file__).resolve().parents[1]
CASE_DIR = REPO / "tools" / "verification_harness" / "cases"


@pytest.fixture(scope="module")
def bundle(tmp_path_factory) -> Path:
    registry = CaseRegistry()
    registry.discover(CASE_DIR)
    report = run_registry(registry)
    out = tmp_path_factory.mktemp("evidence") / "bundle"
    write_bundle(registry, report, out, dependencies=("pytest",))
    return out


def _manifest(bundle: Path) -> dict:
    return json.loads((bundle / MANIFEST_NAME).read_text(encoding="utf-8"))


def test_hash_primitives_are_sha256():
    assert sha256_text("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert sha256_bytes(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert sha256_bytes(b"abc") == sha256_text("abc")


def test_environment_fingerprint_is_stable_and_records_dependencies():
    first = environment_fingerprint(("pytest", "definitely-not-installed-xyz"))
    second = environment_fingerprint(("pytest", "definitely-not-installed-xyz"))
    assert first == second
    assert environment_digest(first) == environment_digest(second)
    assert first["dependencies"]["pytest"] != "ABSENT"
    assert first["dependencies"]["definitely-not-installed-xyz"] == "ABSENT"
    assert {"python_version", "platform_machine", "dependencies"} <= set(first)
    assert environment_digest(first) != environment_digest({**first, "python_version": "0.0.0"})
    assert dependency_versions(("pytest",))["pytest"] == first["dependencies"]["pytest"]


def test_bundle_archives_one_hashed_record_per_case(bundle):
    manifest = _manifest(bundle)
    case_entries = [e for e in manifest["entries"] if e["role"] == "case_record"]
    registry = CaseRegistry()
    registry.discover(CASE_DIR)
    assert len(case_entries) == len(registry) >= 5
    for entry in case_entries:
        target = bundle / entry["path"]
        assert target.is_file()
        assert sha256_file(target) == entry["sha256"], entry["path"]
        record = json.loads(target.read_text(encoding="utf-8"))
        # every case record carries its own environment fingerprint digest
        assert record["environment_digest"] == manifest["environment_digest"]
        assert record["case"]["case_id"]
        assert record["result"]["verdict"] == "PASS"
        assert record["result"]["metrics"]
    assert {e["role"] for e in manifest["entries"]} >= {"registry", "report", "error_table", "case_record"}
    assert (bundle / CASES_DIRNAME).is_dir()


def test_fresh_bundle_verifies_and_reports_no_tamper(bundle):
    verdict = verify_bundle(bundle)
    assert verdict.ok, verdict.to_dict()
    assert verdict.tamper_classes == ()
    assert verdict.checked_entries == len(_manifest(bundle)["entries"])
    assert verdict.root_digest_ok and verdict.environment_match
    assert "OK" in verification_markdown(verdict)


def test_byte_flip_in_any_case_record_is_detected(bundle, tmp_path):
    copy = _copy(bundle, tmp_path / "content")
    record = sorted((copy / CASES_DIRNAME).glob("*.json"))[0]
    payload = bytearray(record.read_bytes())
    payload[10] = payload[10] ^ 0x01
    record.write_bytes(bytes(payload))
    verdict = verify_bundle(copy)
    assert not verdict.ok
    assert record.relative_to(copy).as_posix() in verdict.content_tampered
    assert TAMPER_CONTENT in verdict.tamper_classes


def test_random_single_byte_mutations_are_all_detected(bundle, tmp_path):
    """A3: 100% detection over many random single-byte mutations."""
    rng = random.Random(20261110)
    artifacts = sorted(p for p in bundle.rglob("*") if p.is_file() and p.name != MANIFEST_NAME)
    assert artifacts
    trials = 40
    detected = 0
    for index in range(trials):
        source = artifacts[index % len(artifacts)]
        copy = _copy(bundle, tmp_path / f"mut{index}")
        target = copy / source.relative_to(bundle)
        payload = bytearray(target.read_bytes())
        position = rng.randrange(len(payload))
        original = payload[position]
        payload[position] = original ^ (1 << rng.randrange(8))
        assert payload[position] != original
        target.write_bytes(bytes(payload))
        verdict = verify_bundle(copy)
        if not verdict.ok and target.relative_to(copy).as_posix() in verdict.content_tampered:
            detected += 1
    assert detected == trials, f"tamper detection {detected}/{trials}"


def test_missing_artifact_is_structural_tamper(bundle, tmp_path):
    copy = _copy(bundle, tmp_path / "missing")
    victim = sorted((copy / CASES_DIRNAME).glob("*.json"))[0]
    victim.unlink()
    verdict = verify_bundle(copy)
    assert not verdict.ok
    assert victim.relative_to(copy).as_posix() in verdict.missing
    assert TAMPER_STRUCTURAL in verdict.tamper_classes


def test_unrecorded_artifact_is_structural_tamper(bundle, tmp_path):
    copy = _copy(bundle, tmp_path / "extra")
    (copy / CASES_DIRNAME / "smuggled.json").write_text("{}", encoding="utf-8")
    verdict = verify_bundle(copy)
    assert not verdict.ok
    assert f"{CASES_DIRNAME}/smuggled.json" in verdict.extra
    assert TAMPER_STRUCTURAL in verdict.tamper_classes


def test_manifest_chain_tamper_is_detected(bundle, tmp_path):
    copy = _copy(bundle, tmp_path / "chain")
    manifest = _manifest(copy)
    manifest["entries"][0]["chain_hash"] = "0" * 64
    (copy / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    verdict = verify_bundle(copy)
    assert not verdict.ok
    assert verdict.chain_broken
    assert TAMPER_CHAIN in verdict.tamper_classes


def test_forged_manifest_hash_cannot_hide_a_content_change(bundle, tmp_path):
    """Rewriting the recorded sha to match a doctored payload breaks the chain."""
    copy = _copy(bundle, tmp_path / "forge")
    target = sorted((copy / CASES_DIRNAME).glob("*.json"))[0]
    payload = bytearray(target.read_bytes())
    payload[0] = payload[0] ^ 0xFF
    target.write_bytes(bytes(payload))
    manifest = _manifest(copy)
    for entry in manifest["entries"]:
        if entry["path"] == target.relative_to(copy).as_posix():
            entry["sha256"] = sha256_file(target)
            entry["size"] = target.stat().st_size
    (copy / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    verdict = verify_bundle(copy)
    assert not verdict.ok
    assert TAMPER_CHAIN in verdict.tamper_classes


def test_missing_manifest_is_fail_closed(tmp_path):
    verdict = verify_bundle(tmp_path)
    assert not verdict.ok
    assert verdict.errors


def test_bundle_is_reproducible_and_deterministic(tmp_path):
    registry = CaseRegistry()
    registry.discover(CASE_DIR)
    report = run_registry(registry)
    first = write_bundle(registry, report, tmp_path / "one", dependencies=("pytest",))
    second = write_bundle(registry, report, tmp_path / "two", dependencies=("pytest",))
    assert canonical_json(first) == canonical_json(second)
    for rel in ("report.json", "error_table.md", "registry.json"):
        assert (tmp_path / "one" / rel).read_bytes() == (tmp_path / "two" / rel).read_bytes(), rel


def _copy(source: Path, target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    for path in source.rglob("*"):
        if path.is_file():
            destination = target / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(path.read_bytes())
    return target

def test_committed_anchor_bundle_verifies():
    """Ratchet: the bundle checked into ``artifacts/`` must stay internally consistent.

    Regenerating it is intentional (``python3 -m tools.verification_harness.study``);
    silently editing a recorded artifact is not.
    """
    committed = REPO / "artifacts" / "verification_harness" / "bundle"
    if not (committed / MANIFEST_NAME).is_file():
        pytest.skip("committed anchor bundle not present in this checkout")
    verdict = verify_bundle(committed)
    assert verdict.ok, verdict.to_dict()
