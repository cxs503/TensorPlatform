"""Comprehensive pytest suite for mech_replay (p348w2-n02).

Acceptance criteria coverage:
  C1  golden consistency: ≥5 mechanisms × ≥40 fixtures, 100% match
  C2  degradation detection: 6 types × 5 mechanisms, each detectable in ≥50% fixtures
  C3  idempotency: same fixture twice → identical sha256
  C4  pure function: zero DB/network during replay
  C5  desensitization: no domain words in fixtures
  C6  cost: full suite ≤ 120s
  C7  real re-evaluation: drift classification re-assessment
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.mech_replay.mechanisms import (
    ALL_MECHANISMS, get_mechanism, list_mechanisms,
)
from tools.mech_replay.collector import (
    collect_fixtures, collect_all, save_fixtures, check_desensitization,
    GENERATORS,
)
from tools.mech_replay.replayer import (
    replay_fixture, replay_idempotent, replay_directory,
)
from tools.mech_replay.mutator import (
    apply_mutation, get_all_mutation_ids, MUTATORS,
)

FIXTURES_DIR = ROOT / "artifacts" / "mech_replay" / "fixtures"
N_FIXTURES_PER_MECH = 10  # 10 × 5 mechanisms = 50 total (≥40 required by C1)
SEED = 20261110

# Domain words that must NOT appear in fixture INPUTS (C5 desensitization)
FORBIDDEN_DOMAIN_WORDS = [
    "resistance", "propulsion", "seakeeping", "maneuvering", "stability",
    "vibration", "noise", "ice",
    "TensorLBM", "TensorFEM", "TensorFVM", "TensorBEM",
    "WIGLEY", "KCS", "KVLCC", "DTMB", "Series60",
    "Lindqvist", "IACS", "ITTC",
]

# Mechanisms that have list inputs (for M3 order permutation test)
MECHANISMS_WITH_LISTS = ["maturity_gate_verdict", "evidence_comparator",
                         "drift_classifier", "perf_gate_case"]


# ── Fixtures (pytest) ───────────────────────────────────────────────────

@pytest.fixture(scope="module")
def all_fixtures():
    """Generate all golden fixtures once per module."""
    return collect_all(n_per_mechanism=N_FIXTURES_PER_MECH, seed=SEED)


@pytest.fixture(scope="module")
def saved_fixtures_dir(tmp_path_factory):
    """Save fixtures to temp dir and return path."""
    d = tmp_path_factory.mktemp("mech_replay_fixtures")
    all_fix = collect_all(n_per_mechanism=N_FIXTURES_PER_MECH, seed=SEED)
    for mech_name, fixtures in all_fix.items():
        save_fixtures(fixtures, d, mech_name)
    return d


# ══════════════════════════════════════════════════════════════════════════
# C1: Golden consistency — ≥5 mechanisms × ≥40 fixtures, 100% match
# ══════════════════════════════════════════════════════════════════════════

class TestGoldenConsistency:
    """C1: Every mechanism produces identical output on replay."""

    def test_mechanism_count(self):
        """At least 5 mechanisms registered."""
        mechs = list_mechanisms()
        assert len(mechs) >= 5, f"Need ≥5 mechanisms, got {len(mechs)}"

    def test_fixture_count(self, all_fixtures):
        """At least 40 total fixtures (8 per mechanism × 5 mechanisms)."""
        total = sum(len(f) for f in all_fixtures.values())
        assert total >= 40, f"Need ≥40 fixtures, got {total}"

    @pytest.mark.parametrize("mech_name", list_mechanisms())
    def test_golden_replay_100pct(self, all_fixtures, mech_name):
        """Each fixture replays with 0 diff (sha256 match)."""
        fixtures = all_fixtures[mech_name]
        assert len(fixtures) >= 8, f"{mech_name}: need ≥8 fixtures"
        for fix in fixtures:
            result = replay_fixture(fix)
            assert result.output_match, (
                f"Golden mismatch for {result.fixture_id}: "
                f"golden={result.golden_sha[:16]}... replay={result.replay_sha[:16]}..."
            )

    @pytest.mark.parametrize("mech_name", list_mechanisms())
    def test_required_fields_present(self, all_fixtures, mech_name):
        """Output contains all required fields."""
        mech = get_mechanism(mech_name)
        for fix in all_fixtures[mech_name]:
            result = replay_fixture(fix)
            output = mech["invoke"](fix["input"])
            for field in mech["required_fields"]:
                assert field in output, (
                    f"{mech_name}: missing required field {field!r} in output"
                )


# ══════════════════════════════════════════════════════════════════════════
# C2: Degradation detection — 6 types × 5 mechanisms
# ══════════════════════════════════════════════════════════════════════════

class TestDegradationDetection:
    """C2: All 6 degradation types must be detectable for all mechanisms."""

    @pytest.mark.parametrize("mech_name", list_mechanisms())
    @pytest.mark.parametrize("mutation_id", get_all_mutation_ids())
    def test_mutation_detectable(self, all_fixtures, mech_name, mutation_id):
        """Each mutation must be detectable in ≥50% of fixtures.

        Note: M3 (order permutation) only applies to mechanisms with list inputs.
        For mechanisms without lists (evidence_grader), M3 is a no-op.
        """
        mech = get_mechanism(mech_name)
        fixtures = all_fixtures[mech_name]

        # M3 only applies to mechanisms with list inputs
        if mutation_id == "M3_order_permutation" and mech_name not in MECHANISMS_WITH_LISTS:
            pytest.skip(f"M3 not applicable to {mech_name} (no list inputs)")

        detected_count = 0
        for fix in fixtures:
            golden_sha = fix["expected_sha256"]
            mutated_output = apply_mutation(mech_name, mutation_id, fix)
            mutated_sha = mech["output_sha256"](mutated_output)
            if mutated_sha != golden_sha:
                detected_count += 1

        # At least 50% of fixtures must detect the degradation
        detection_rate = detected_count / len(fixtures)
        assert detection_rate >= 0.5, (
            f"{mech_name} × {mutation_id}: only {detected_count}/{len(fixtures)} "
            f"({detection_rate:.0%}) fixtures detected the degradation (need ≥50%)"
        )

    def test_mutation_coverage(self, all_fixtures):
        """All applicable mutation categories are detectable.

        6 mutations × 5 mechanisms = 30 categories, but M3 × evidence_grader
        is not applicable, so we have 29 applicable categories.
        Each must be detectable in ≥50% of fixtures.
        """
        total_categories = 0
        detectable_categories = 0

        for mech_name in list_mechanisms():
            mech = get_mechanism(mech_name)
            for mid in get_all_mutation_ids():
                # Skip M3 for mechanisms without lists
                if mid == "M3_order_permutation" and mech_name not in MECHANISMS_WITH_LISTS:
                    continue

                total_categories += 1
                # Check if ≥50% of fixtures detect this mutation
                detected_count = 0
                for fix in all_fixtures[mech_name]:
                    golden_sha = fix["expected_sha256"]
                    mutated_output = apply_mutation(mech_name, mid, fix)
                    mutated_sha = mech["output_sha256"](mutated_output)
                    if mutated_sha != golden_sha:
                        detected_count += 1
                detection_rate = detected_count / len(all_fixtures[mech_name])
                if detection_rate >= 0.5:
                    detectable_categories += 1

        assert total_categories >= 29, f"Expected ≥29 categories, got {total_categories}"
        assert detectable_categories == total_categories, (
            f"C2 FAIL: only {detectable_categories}/{total_categories} mutation categories detectable"
        )


# ══════════════════════════════════════════════════════════════════════════
# C3: Idempotency — same fixture twice → identical sha256
# ══════════════════════════════════════════════════════════════════════════

class TestIdempotency:
    """C3: Running the same fixture twice produces identical output."""

    @pytest.mark.parametrize("mech_name", list_mechanisms())
    def test_idempotent(self, all_fixtures, mech_name):
        """Each fixture is idempotent (run1 sha == run2 sha)."""
        for fix in all_fixtures[mech_name]:
            is_idem, sha1, sha2 = replay_idempotent(fix)
            assert is_idem, (
                f"{fix['fixture_id']} not idempotent: "
                f"run1={sha1[:16]}... run2={sha2[:16]}..."
            )

    @pytest.mark.parametrize("mech_name", list_mechanisms())
    def test_triple_idempotent(self, all_fixtures, mech_name):
        """Run 3 times to catch stateful bugs that only manifest on 3rd call."""
        mech = get_mechanism(mech_name)
        for fix in all_fixtures[mech_name]:
            inp = fix["input"]
            sha1 = mech["output_sha256"](mech["invoke"](inp))
            sha2 = mech["output_sha256"](mech["invoke"](inp))
            sha3 = mech["output_sha256"](mech["invoke"](inp))
            assert sha1 == sha2 == sha3, (
                f"{fix['fixture_id']} not triple-idempotent"
            )


# ══════════════════════════════════════════════════════════════════════════
# C4: Pure function — zero DB/network during replay
# ══════════════════════════════════════════════════════════════════════════

class TestPurity:
    """C4: Replay process makes zero DB/network calls."""

    def test_no_socket_during_replay(self, all_fixtures):
        """No socket calls during replay of all fixtures."""
        import socket
        original_socket = socket.socket
        call_count = [0]

        class CountingSocket(original_socket):
            def __init__(self, *args, **kwargs):
                call_count[0] += 1
                super().__init__(*args, **kwargs)

        socket.socket = CountingSocket
        try:
            for mech_name, fixtures in all_fixtures.items():
                for fix in fixtures:
                    replay_fixture(fix)
        finally:
            socket.socket = original_socket

        assert call_count[0] == 0, (
            f"C4 FAIL: {call_count[0]} socket calls during replay"
        )

    def test_no_import_psycopg_during_replay(self, all_fixtures):
        """psycopg2 is not imported during replay."""
        had_psycopg = "psycopg2" in sys.modules
        if had_psycopg:
            pytest.skip("psycopg2 already imported in this process")

        for mech_name, fixtures in all_fixtures.items():
            for fix in fixtures:
                replay_fixture(fix)

        assert "psycopg2" not in sys.modules, (
            "C4 FAIL: psycopg2 was imported during replay"
        )


# ══════════════════════════════════════════════════════════════════════════
# C5: Desensitization — no domain words in fixture inputs
# ══════════════════════════════════════════════════════════════════════════

class TestDesensitization:
    """C5: Fixture inputs contain zero real domain words."""

    def test_no_domain_words_in_inputs(self, all_fixtures):
        """No forbidden domain words in fixture inputs."""
        for mech_name, fixtures in all_fixtures.items():
            for fix in fixtures:
                input_blob = json.dumps(fix["input"], ensure_ascii=False)
                for word in FORBIDDEN_DOMAIN_WORDS:
                    assert word.lower() not in input_blob.lower(), (
                        f"C5 FAIL: domain word '{word}' found in {fix['fixture_id']} input"
                    )

    def test_source_is_synthetic(self, all_fixtures):
        """All fixtures are marked as synthetic."""
        for mech_name, fixtures in all_fixtures.items():
            for fix in fixtures:
                assert fix["source"] == "synthetic", (
                    f"{fix['fixture_id']}: source should be 'synthetic'"
                )


# ══════════════════════════════════════════════════════════════════════════
# C6: Cost — full suite ≤ 120s
# ══════════════════════════════════════════════════════════════════════════

class TestCost:
    """C6: Full replay suite completes within 120 seconds."""

    def test_replay_cost(self, all_fixtures):
        """Replay all fixtures in < 30s (generous margin for 120s total)."""
        t0 = time.time()
        for mech_name, fixtures in all_fixtures.items():
            for fix in fixtures:
                replay_fixture(fix)
        elapsed = time.time() - t0
        assert elapsed < 30, f"C6 FAIL: replay took {elapsed:.1f}s > 30s"

    def test_full_suite_cost(self):
        """Full collect + replay + mutate in < 120s."""
        t0 = time.time()
        # Collect
        all_fix = collect_all(n_per_mechanism=N_FIXTURES_PER_MECH, seed=SEED)
        # Replay
        for mech_name, fixtures in all_fix.items():
            for fix in fixtures:
                replay_fixture(fix)
        # Mutate
        for mech_name in list_mechanisms():
            mech = get_mechanism(mech_name)
            for mid in get_all_mutation_ids():
                for fix in all_fix[mech_name]:
                    apply_mutation(mech_name, mid, fix)
        elapsed = time.time() - t0
        assert elapsed < 120, f"C6 FAIL: full suite took {elapsed:.1f}s > 120s"


# ══════════════════════════════════════════════════════════════════════════
# C7: Real re-evaluation — drift classification drift analysis
# ══════════════════════════════════════════════════════════════════════════

class TestRealReevaluation:
    """C7: Real re-evaluation of drift classification gate."""

    def test_drift_classification_distribution(self, all_fixtures):
        """Drift classifier produces diverse classifications on replay."""
        fixtures = all_fixtures["drift_classifier"]
        mech = get_mechanism("drift_classifier")
        class_counts = {}
        for fix in fixtures:
            output = mech["invoke"](fix["input"])
            for cls in output["classifications"]:
                dc = cls["drift_class"]
                class_counts[dc] = class_counts.get(dc, 0) + 1
        # Should have at least 3 different drift classes
        assert len(class_counts) >= 3, (
            f"C7: drift classifier only produced {len(class_counts)} classes: {class_counts}"
        )

    def test_drift_replay_consistency(self, all_fixtures):
        """Drift classifier replay matches golden exactly."""
        fixtures = all_fixtures["drift_classifier"]
        for fix in fixtures:
            result = replay_fixture(fix)
            assert result.output_match, (
                f"C7 drift replay mismatch: {result.fixture_id}"
            )

    def test_maturity_gate_verdict_distribution(self, all_fixtures):
        """Maturity gate produces diverse verdicts (PASS/FAIL/WITHHELD)."""
        fixtures = all_fixtures["maturity_gate_verdict"]
        mech = get_mechanism("maturity_gate_verdict")
        verdicts = set()
        for fix in fixtures:
            output = mech["invoke"](fix["input"])
            verdicts.add(output["verdict"])
        assert len(verdicts) >= 2, (
            f"C7: maturity gate only produced verdicts: {verdicts}"
        )


# ══════════════════════════════════════════════════════════════════════════
# File-level tests (manifest integrity)
# ══════════════════════════════════════════════════════════════════════════

class TestFileIntegrity:
    """Test saved fixture files and manifests."""

    def test_save_and_reload(self, tmp_path):
        """Fixtures survive save → reload round-trip."""
        fixtures = collect_fixtures("maturity_gate_verdict", n=5, seed=42)
        manifest = save_fixtures(fixtures, tmp_path, "maturity_gate_verdict")
        assert len(manifest) == 5

        # Reload and replay
        for fname in manifest:
            fpath = tmp_path / fname
            assert fpath.exists()
            fix = json.loads(fpath.read_text(encoding="utf-8"))
            result = replay_fixture(fix)
            assert result.output_match

    def test_manifest_sha256_integrity(self, saved_fixtures_dir):
        """Manifest sha256 matches actual file content."""
        for mech_name in list_mechanisms():
            manifest_path = saved_fixtures_dir / f"{mech_name}_manifest.json"
            assert manifest_path.exists(), f"Missing manifest for {mech_name}"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for fname, expected_sha in manifest.items():
                fpath = saved_fixtures_dir / fname
                actual_sha = hashlib.sha256(fpath.read_bytes()).hexdigest()
                assert actual_sha == expected_sha, (
                    f"Manifest integrity fail: {fname}"
                )
