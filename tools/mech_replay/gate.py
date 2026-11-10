#!/usr/bin/env python3
"""CI 门：golden replay 全绿 + 6 类退化 100% 检出 + 覆盖率 + sha256 完整 + pytest 面。

C1–C5 判据（每条都落数字进 ``artifacts/mech_replay/gate_report.json``）
-----------------------------------------------------------------------
C1 golden replay 一致性 : ≥50 条 fixture 判词与冻结判词 100% 一致（含幂等/置换对/差分）
C2 退化检出率           : 6 类退化每类 ≥10 个变异样本，检出率 100%
C3 机制覆盖             : ≥8 个核心机制，每个 ≥5 条 fixture
C4 sha256 完整性        : 每个 fixture 文件 sha256 与清单一致（+ 快照 sha256）
C5 pytest 覆盖面        : ≥20 例覆盖采集器/重放器/变异器/CI 门

退出码 0=门绿，1=门红。任何一条不满足即红，报告里逐条给出实测值与阈值。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from tools.mech_replay import collector, mechanisms, mutate, replay, spec

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DIR = REPO_ROOT / "artifacts" / "mech_replay"
TEST_GLOB = "test_mech_replay*.py"

C1_MIN_FIXTURES = 50
C2_MIN_SAMPLES = 10
C2_MIN_CLASSES = 6
C3_MIN_MECHANISMS = 8
C3_MIN_FIXTURES_PER_MECHANISM = 5
C5_MIN_TESTS = 20


def _pytest_count(tests_dir: Path) -> int:
    """用 pytest --collect-only 数本套件的用例数（不自报，机器数）。"""
    if not (tests_dir / "__init__.py").exists() and not tests_dir.exists():
        return 0
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q",
             f"--rootdir={REPO_ROOT}", "-p", "no:cacheprovider",
             *sorted(str(p) for p in tests_dir.glob(TEST_GLOB))],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    lines = [ln for ln in proc.stdout.splitlines() if "::" in ln]
    return len(lines)


def run_gate(fixtures_dir: Path | str = DEFAULT_DIR / "fixtures",
             out_dir: Path | str = DEFAULT_DIR, *, tests_dir: Path | str | None = None,
             include_pytest: bool = True) -> dict[str, Any]:
    out = Path(out_dir)
    fixtures_dir = Path(fixtures_dir)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    fixtures = replay.load_fixtures(fixtures_dir)

    # ---- C1 replay ----
    replay_report = replay.replay(fixtures_dir)
    c1 = {
        "fixtures_total": replay_report["fixtures_total"],
        "fixtures_matched": replay_report["fixtures_matched"],
        "consistency": replay_report["consistency"],
        "idempotency_violations": replay_report["idempotency_violations"],
        "reference_disagreements": replay_report["reference_disagreements"],
        "permutation_violations": replay_report["permutation_violations"],
        "verdict_sha_mismatches": replay_report["verdict_sha_mismatches"],
        "threshold_fixtures": C1_MIN_FIXTURES,
        "pass": (replay_report["fixtures_total"] >= C1_MIN_FIXTURES
                 and replay_report["consistency"] == 1.0
                 and not replay_report["idempotency_violations"]
                 and not replay_report["reference_disagreements"]
                 and not replay_report["permutation_violations"]
                 and not replay_report["verdict_sha_mismatches"]),
    }

    # ---- C2 mutation ----
    mutation_report = mutate.run_suite(fixtures)
    class_rows = {k: {"samples": v["samples"], "detected": v["detected"],
                      "detection_rate": v["detection_rate"],
                      "decision_flip_detected": v["decision_flip_detected"],
                      "channel_detected": v["channel_detected"],
                      "required_channel": v["required_channel"],
                      "required_channel_ok": v["required_channel_ok"],
                      "undetected": v["undetected"]}
                  for k, v in mutation_report["classes"].items()}
    # 每类退化还必须在**指定语义渠道**上被检出（M1/M2/M3/M6→决策或理由码变化；
    # M4→二次调用漂移；M5→决策不变但审计面丢失）——比"判词有任何不同"更强的判据
    semantic_ok = all(v["required_channel_ok"] for v in class_rows.values())
    c2 = {
        "classes": class_rows,
        "mutation_samples": mutation_report["mutation_samples"],
        "mutation_detected": mutation_report["mutation_detected"],
        "detection_rate": mutation_report["detection_rate"],
        "decision_flip_detected": mutation_report["decision_flip_detected"],
        "decision_flip_rate": mutation_report["decision_flip_rate"],
        "semantic_ok": semantic_ok,
        "channel_requirements": {k: v["required_channel"] for k, v in class_rows.items()},
        "min_samples_per_class": mutation_report["min_samples_per_class"],
        "threshold_classes": C2_MIN_CLASSES,
        "threshold_samples": C2_MIN_SAMPLES,
        "pass": (len(class_rows) >= C2_MIN_CLASSES
                 and all(row["samples"] >= C2_MIN_SAMPLES for row in class_rows.values())
                 and all(row["detection_rate"] == 1.0 for row in class_rows.values())
                 and semantic_ok),
    }

    # ---- C3 coverage ----
    per_mech_counts = {name: 0 for name in mechanisms.MECHANISMS}
    for fixture in fixtures:
        per_mech_counts[fixture["mechanism"]] = per_mech_counts.get(fixture["mechanism"], 0) + 1
    c3 = {
        "mechanisms_covered": sum(1 for n in mechanisms.MECHANISMS if per_mech_counts.get(n, 0) > 0),
        "mechanisms_total": len(mechanisms.MECHANISMS),
        "per_mechanism": dict(sorted(per_mech_counts.items())),
        "min_per_mechanism": min(per_mech_counts.values()) if per_mech_counts else 0,
        "threshold_mechanisms": C3_MIN_MECHANISMS,
        "threshold_per_mechanism": C3_MIN_FIXTURES_PER_MECHANISM,
        "pass": (sum(1 for n in mechanisms.MECHANISMS if per_mech_counts.get(n, 0) > 0)
                 >= C3_MIN_MECHANISMS
                 and all(per_mech_counts.get(n, 0) >= C3_MIN_FIXTURES_PER_MECHANISM
                         for n in mechanisms.MECHANISMS)),
    }

    # ---- C4 sha256 ----
    integrity = collector.verify_manifest(out)
    c4 = {**integrity, "pass": integrity["all_ok"]}

    # ---- C5 pytest ----
    tests_path = Path(tests_dir) if tests_dir else REPO_ROOT / "tests"
    test_count = _pytest_count(tests_path) if include_pytest else 0
    c5 = {"test_count": test_count, "threshold": C5_MIN_TESTS,
          "pass": test_count >= C5_MIN_TESTS, "skipped": not include_pytest}

    manifest_counts = manifest.get("per_mechanism", {})
    report = {
        "schema": "mech-replay-gate-v1",
        "rules_version": spec.RULES_VERSION,
        "manifest_fixture_count": manifest.get("fixture_count"),
        "manifest_per_mechanism": manifest_counts,
        "manifest_origin_counts": manifest.get("origin_counts"),
        "manifest_snapshot_sha256": manifest.get("snapshot_sha256"),
        "criteria": {"C1": c1, "C2": c2, "C3": c3, "C4": c4, "C5": c5},
        "mutation_matrix": mutate.matrix(mutation_report["samples"]),
        "mutation_samples": [
            {k: s[k] for k in ("sample_id", "mechanism", "class", "variant", "detected",
                               "evidence", "verdict_mismatches", "idempotency_mismatches",
                               "permutation_violations")}
            for s in mutation_report["samples"]
        ],
        "replay_results": [
            {k: r[k] for k in ("fixture_id", "mechanism", "origin", "probe_class",
                               "decision", "expected_decision", "match", "idempotent",
                               "reference_agree")}
            for r in replay_report["results"]
        ],
        "green": all(c["pass"] for c in (c1, c2, c3, c4, c5)),
    }
    return report


def write_report(report: dict[str, Any], out_dir: Path | str = DEFAULT_DIR) -> Path:
    path = Path(out_dir) / "gate_report.json"
    path.write_text(json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="mech_replay CI 门（C1–C5）")
    parser.add_argument("--dir", default=str(DEFAULT_DIR))
    parser.add_argument("--no-pytest", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    out = Path(args.dir)
    report = run_gate(out / "fixtures", out, include_pytest=not args.no_pytest)
    path = write_report(report, out)
    brief = {cid: {k: report["criteria"][cid].get(k) for k in
                   ("pass", "consistency", "detection_rate", "min_samples_per_class",
                    "mechanisms_covered", "all_ok", "test_count", "fixtures_total") if k in
                   report["criteria"][cid]}
             for cid in ("C1", "C2", "C3", "C4", "C5")}
    print(json.dumps({"green": report["green"], "criteria": brief, "report": str(path)},
                     ensure_ascii=False, indent=2))
    return 0 if report["green"] else 1


if __name__ == "__main__":
    raise SystemExit(main())