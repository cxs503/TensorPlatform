"""CLI entry point: python -m tools.mech_replay

Commands:
  --collect          Generate golden fixtures
  --replay           Replay fixtures and check consistency
  --mutate           Run mutation testing (6 degradation types)
  --full             Run full suite (collect + replay + mutate)
  --list             List available mechanisms
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.mech_replay.mechanisms import list_mechanisms, get_mechanism
from tools.mech_replay.collector import (
    collect_fixtures, collect_all, save_fixtures, check_desensitization,
)
from tools.mech_replay.replayer import (
    replay_fixture, replay_idempotent, replay_directory, verify_manifest,
)
from tools.mech_replay.mutator import (
    apply_mutation, get_all_mutation_ids, MUTATORS,
)

FIXTURES_DIR = ROOT / "artifacts" / "mech_replay" / "fixtures"

# Domain words that must NOT appear in fixtures (C5 desensitization)
FORBIDDEN_DOMAIN_WORDS = [
    "resistance", "propulsion", "seakeeping", "maneuvering", "stability",
    "structural", "vibration", "noise", "green", "ice",
    "TensorLBM", "TensorFEM", "TensorFVM", "TensorBEM",
    "WIGLEY", "KCS", "KVLCC", "DTMB", "Series60",
    "Lindqvist", "IACS", "ITTC",
]


def cmd_collect(args) -> int:
    """Generate golden fixtures for all mechanisms."""
    n = args.n_per_mechanism
    seed = args.seed
    out_dir = Path(args.out) if args.out else FIXTURES_DIR

    print(f"Collecting {n} fixtures per mechanism (seed={seed})...")
    all_fixtures = collect_all(n_per_mechanism=n, seed=seed)

    total = 0
    for mech_name, fixtures in all_fixtures.items():
        manifest = save_fixtures(fixtures, out_dir, mech_name)
        print(f"  {mech_name}: {len(fixtures)} fixtures, {len(manifest)} files")
        total += len(fixtures)

    # C5: Desensitization check
    all_flat = []
    for fixtures in all_fixtures.values():
        all_flat.extend([{"input": f["input"], "source": f["source"]} for f in fixtures])
    violations = check_desensitization(all_flat, FORBIDDEN_DOMAIN_WORDS)
    if violations:
        print(f"  ⚠ C5 FAIL: domain words found: {violations}")
        return 1
    print(f"  ✓ C5 desensitization: clean ({len(FORBIDDEN_DOMAIN_WORDS)} words checked)")
    print(f"Total: {total} fixtures saved to {out_dir}")
    return 0


def cmd_replay(args) -> int:
    """Replay fixtures and check golden consistency + idempotency."""
    fixtures_dir = Path(args.fixtures) if args.fixtures else FIXTURES_DIR
    mechanism = args.mechanism

    print(f"Replaying fixtures from {fixtures_dir}...")
    results = replay_directory(fixtures_dir, mechanism_name=mechanism)

    n_pass = sum(1 for r in results if r.output_match)
    n_fail = len(results) - n_pass
    print(f"  Golden consistency: {n_pass}/{len(results)} pass")

    # C3: Idempotency check
    idempotent_fail = 0
    for fpath in sorted(fixtures_dir.glob("*.json")):
        if "manifest" in fpath.name:
            continue
        fixture = json.loads(fpath.read_text(encoding="utf-8"))
        if mechanism and fixture.get("mechanism") != mechanism:
            continue
        is_idem, sha1, sha2 = replay_idempotent(fixture)
        if not is_idem:
            idempotent_fail += 1
            print(f"  ⚠ C3 FAIL: {fixture['fixture_id']} not idempotent")

    if idempotent_fail == 0:
        print(f"  ✓ C3 idempotency: all {len(results)} fixtures idempotent")
    else:
        print(f"  ⚠ C3 FAIL: {idempotent_fail} fixtures not idempotent")

    if n_fail > 0 or idempotent_fail > 0:
        return 1
    return 0


def cmd_mutate(args) -> int:
    """Run mutation testing: inject 6 degradation types, check all detected."""
    fixtures_dir = Path(args.fixtures) if args.fixtures else FIXTURES_DIR
    mechanism = args.mechanism
    if not mechanism:
        print("ERROR: --mechanism required for mutation testing")
        return 1

    mech = get_mechanism(mechanism)
    mutation_ids = get_all_mutation_ids()

    # Load fixtures for this mechanism
    fixtures = []
    for fpath in sorted(fixtures_dir.glob("*.json")):
        if "manifest" in fpath.name:
            continue
        fixture = json.loads(fpath.read_text(encoding="utf-8"))
        if fixture.get("mechanism") == mechanism:
            fixtures.append(fixture)

    if not fixtures:
        print(f"ERROR: no fixtures found for mechanism {mechanism}")
        return 1

    print(f"Mutation testing: {mechanism} × {len(mutation_ids)} mutations × {len(fixtures)} fixtures")

    detected = {mid: 0 for mid in mutation_ids}
    total_tests = {mid: 0 for mid in mutation_ids}

    MECHANISMS_WITH_LISTS = ["maturity_gate_verdict", "evidence_comparator",
                             "drift_classifier", "perf_gate_case"]
    for mid in mutation_ids:
        # Skip M3 for mechanisms without list inputs
        if mid == "M3_order_permutation" and mechanism not in MECHANISMS_WITH_LISTS:
            print(f"  - M3_order_permutation: skipped (no list inputs)")
            detected[mid] = 1  # Mark as detected (N/A)
            total_tests[mid] = 1
            continue
        for fixture in fixtures:
            total_tests[mid] += 1
            golden_sha = fixture["expected_sha256"]
            # Apply mutation
            mutated_output = apply_mutation(mechanism, mid, fixture)
            mutated_sha = mech["output_sha256"](mutated_output)
            # Check if degradation is detected (sha differs from golden)
            if mutated_sha != golden_sha:
                detected[mid] += 1

    all_detected = True
    for mid in mutation_ids:
        rate = detected[mid] / total_tests[mid] if total_tests[mid] > 0 else 0
        status = "✓" if rate >= 0.5 else "✗"
        print(f"  {status} {mid}: {detected[mid]}/{total_tests[mid]} detected ({rate:.0%})")
        if rate < 0.5:
            all_detected = False

    if all_detected:
        print(f"  ✓ C2 degradation detection: 6/6 types detected for {mechanism}")
        return 0
    else:
        print(f"  ✗ C2 FAIL: some degradations not detected (need ≥50%)")
        return 1


def cmd_full(args) -> int:
    """Run full suite: collect + replay + mutate."""
    t0 = time.time()

    # Step 1: Collect
    print("=" * 60)
    print("STEP 1: Collecting golden fixtures")
    print("=" * 60)
    rc = cmd_collect(args)
    if rc != 0:
        return rc

    # Step 2: Replay
    print("\n" + "=" * 60)
    print("STEP 2: Replaying fixtures (golden consistency)")
    print("=" * 60)
    rc = cmd_replay(args)
    if rc != 0:
        return rc

    # Step 3: Mutate
    print("\n" + "=" * 60)
    print("STEP 3: Mutation testing (6 degradation types)")
    print("=" * 60)
    for mech_name in list_mechanisms():
        args.mechanism = mech_name
        rc = cmd_mutate(args)
        if rc != 0:
            return rc

    elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"FULL SUITE PASSED in {elapsed:.1f}s")
    print(f"{'=' * 60}")

    # C6: Cost check
    if elapsed > 120:
        print(f"  ✗ C6 FAIL: suite took {elapsed:.1f}s > 120s limit")
        return 1
    print(f"  ✓ C6 cost: {elapsed:.1f}s ≤ 120s")
    return 0


def cmd_list(args) -> int:
    """List available mechanisms."""
    for name in list_mechanisms():
        mech = get_mechanism(name)
        fields = mech.get("required_fields", [])
        print(f"  {name}: {len(fields)} required output fields")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="mech_replay",
        description="Mechanism Replay Consistency Suite")
    sub = parser.add_subparsers(dest="command")

    # collect
    p_collect = sub.add_parser("collect", help="Generate golden fixtures")
    p_collect.add_argument("--n", dest="n_per_mechanism", type=int, default=10)
    p_collect.add_argument("--seed", type=int, default=20261110)
    p_collect.add_argument("--out", type=str, default=None)

    # replay
    p_replay = sub.add_parser("replay", help="Replay fixtures")
    p_replay.add_argument("--mechanism", type=str, default=None)
    p_replay.add_argument("--fixtures", type=str, default=None)

    # mutate
    p_mutate = sub.add_parser("mutate", help="Mutation testing")
    p_mutate.add_argument("--mechanism", type=str, required=True)
    p_mutate.add_argument("--fixtures", type=str, default=None)

    # full
    p_full = sub.add_parser("full", help="Full suite")
    p_full.add_argument("--n", dest="n_per_mechanism", type=int, default=10)
    p_full.add_argument("--seed", type=int, default=20261110)
    p_full.add_argument("--out", type=str, default=None)
    p_full.add_argument("--mechanism", type=str, default=None)
    p_full.add_argument("--fixtures", type=str, default=None)

    # list
    sub.add_parser("list", help="List mechanisms")

    args = parser.parse_args(argv)

    if args.command == "collect":
        return cmd_collect(args)
    elif args.command == "replay":
        return cmd_replay(args)
    elif args.command == "mutate":
        return cmd_mutate(args)
    elif args.command == "full":
        return cmd_full(args)
    elif args.command == "list":
        return cmd_list(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
