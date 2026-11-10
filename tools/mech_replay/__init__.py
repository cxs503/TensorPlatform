"""Mechanism Replay Consistency Suite (p348w2-n02).

Golden-fixture replay + 6-type degradation mutation testing for platform
gate/mechanism pure functions.  Zero DB, zero network, zero domain vocabulary.

Mechanisms covered (5):
  1. maturity_gate_verdict  — campaign_verdict() → PASS/FAIL/WITHHELD
  2. evidence_comparator    — compare() → bool (threshold predicate)
  3. evidence_grader        — grade_contract() → grade dict
  4. drift_classifier       — classify_drift_fingerprint() → drift class
  5. perf_gate_case         — evaluate_case() → Verdict

Degradation types (6):
  M1  threshold_relax     — threshold widened by 20%
  M2  exception_swallow   — errors caught, default returned
  M3  order_permutation   — input list order shuffled
  M4  idempotency_break   — second call returns different result
  M5  audit_field_drop    — output key silently removed
  M6  short_circuit_const — always returns a constant

Usage:
    python -m tools.mech_replay --mechanism <name> --fixtures <dir>
    python -m tools.mech_replay --collect --out artifacts/mech_replay/fixtures
    python -m tools.mech_replay --mutate --mechanism <name>
    pytest tests/test_mech_replay.py -v
"""
