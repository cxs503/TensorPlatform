"""Machine-checkable acceptance criteria A..F for the maturity order.

Each criterion is a small experiment with printed numbers, so the maturity claim
is arithmetic rather than prose.  Everything runs against synthetic fixtures
(``synthetic_a`` / ``synthetic_b``) in a scratch directory; nothing here touches a
production ledger.

A  positive activation   reference truth lands -> scheduled detector flips
                        ``WITHHELD`` -> ``PASS`` inside the time budget, exactly one
                        new audit record, ``actor=auto``, record carries a sha256
B  negative control      no reference truth -> verdict stays ``WITHHELD``, detector
                        exit code 0, no audit record (idempotent pass)
C  withdrawal            reference row deleted -> verdict falls back to ``WITHHELD``
                        and the fall leaves its own audit record (no one-way ratchet)
D  counter recompute     a hand-edited counter block is refused at write time with a
                        per-field delta and zero state mutation; ``recount --repair``
                        rebuilds the cache from the base records and audits the repair
E  zero domain words     no vocabulary hit in the new code, fixtures or artifacts
                        (scanned by ``tools/evidence_domain_scan.py``)
F  idempotence/concurrency  three sequential passes and three concurrent passes leave
                        the audit log length unchanged (no audit inflation)
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from . import fixtures
from .detector import reactivate, scan
from .ledger import (
    TRIGGER_ACTIVATION,
    TRIGGER_COUNTER_REPAIR,
    TRIGGER_WITHDRAWAL,
    VERDICT_PASS,
    VERDICT_WITHHELD,
    CounterMismatch,
    ReferenceStore,
    sha256_hex,
)

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
OUT_DEFAULT = ROOT / "artifacts" / "withheld_activation"

#: A passing detector pass must beat this by construction (interval <= budget).
ACTIVATION_BUDGET_S = 300.0
WATCH_INTERVAL_S = 0.2


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _fresh(path: Path) -> Path:
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _audit(state_dir: Path) -> list:
    from .ledger import CriterionLedger

    return CriterionLedger(Path(state_dir) / "criteria.json").audit_records()


def _verdicts(state_dir: Path) -> Dict[str, str]:
    from .ledger import CriterionLedger

    ledger = CriterionLedger(Path(state_dir) / "criteria.json")
    return {sid: c["verdict"] for sid, c in sorted(ledger.criteria.items())}


def _cli(args: list) -> tuple:
    """Run the CLI in a child process; return (exit_code, parsed_stdout_or_text)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proc = subprocess.run(
        [sys.executable, "-m", "services.withheld_activation.cli", *args],
        cwd=str(BACKEND), env=env, capture_output=True, text=True, timeout=600,
    )
    payload: Any = proc.stdout
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"stdout": proc.stdout, "stderr_tail": proc.stderr.strip().splitlines()[-3:]}
    return proc.returncode, payload


def _file_sha(path: Path) -> str:
    return sha256_hex(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# criteria
# --------------------------------------------------------------------------- #
def criterion_a(state_dir: Path) -> Dict[str, Any]:
    """Reference truth arrives while a scheduler polls -> automatic PASS."""
    fixtures.seed(state_dir)
    before = _audit(state_dir)
    watcher = subprocess.Popen(
        [sys.executable, "-m", "services.withheld_activation.cli", "watch",
         "--state-dir", str(state_dir), "--interval", str(WATCH_INTERVAL_S),
         "--timeout", "60", "--stop-on-event"],
        cwd=str(BACKEND), env={**dict(os.environ), "PYTHONPATH": str(BACKEND)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    time.sleep(0.6)  # let a first (empty) pass happen: verdict still WITHHELD
    verdict_before_injection = _verdicts(state_dir)["synthetic_a"]
    injected_at = datetime.now().astimezone()
    store = ReferenceStore(state_dir / "reference_truth.json")
    row = store.upsert("synthetic_a", quantity="synthetic_quantity_alpha",
                       reference_value=101.0, uncertainty_pct=1.0,
                       source="synthetic_source_a")
    try:
        out, err = watcher.communicate(timeout=90)
    except subprocess.TimeoutExpired:  # pragma: no cover - watchdog
        watcher.kill()
        out, err = watcher.communicate()
    try:
        watcher_passes = json.loads(out or "{}").get("passes")
    except json.JSONDecodeError:
        watcher_passes = None
    after = _audit(state_dir)
    new_records = after[len(before):]
    flip = new_records[0] if new_records else {}
    decided_at = datetime.fromisoformat(flip["ts"]) if flip.get("ts") else None
    latency = (decided_at - injected_at).total_seconds() if decided_at else None
    digest = ReferenceStore(state_dir / "reference_truth.json").digest("synthetic_a")
    checks = {
        "single_new_audit_record": len(new_records) == 1,
        "trigger_activation": flip.get("trigger") == TRIGGER_ACTIVATION,
        "actor_auto": flip.get("actor") == "auto",
        "sha256_present": isinstance(flip.get("sha256"), str) and len(flip["sha256"]) == 64,
        "sha256_matches_reference_row": flip.get("sha256") == digest,
        "verdict_flipped_to_pass": _verdicts(state_dir)["synthetic_a"] == VERDICT_PASS,
        "error_within_tolerance": (flip.get("error_pct") is not None
                                   and flip["error_pct"] <= flip["tolerance_pct"]),
        "withheld_before_injection": verdict_before_injection == VERDICT_WITHHELD,
        "latency_within_budget": latency is not None and latency <= ACTIVATION_BUDGET_S,
        "watcher_exit_zero": watcher.returncode == 0,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "numbers": {
            "latency_s": round(latency, 3) if latency is not None else None,
            "budget_s": ACTIVATION_BUDGET_S,
            "poll_interval_s": WATCH_INTERVAL_S,
            "watcher_passes": watcher_passes,
            "audit_before": len(before),
            "audit_after": len(after),
            "error_pct": flip.get("error_pct"),
            "tolerance_pct": flip.get("tolerance_pct"),
            "reference_value": row["reference_value"],
            "reference_sha256": digest,
            "watcher_stderr_tail": (err or "").strip().splitlines()[-1:] if err else [],
        },
        "verdict": _verdicts(state_dir),
    }


def criterion_b(state_dir: Path) -> Dict[str, Any]:
    """Negative control: no reference truth, no flip, exit code 0."""
    fixtures.seed(state_dir)
    code1, _ = _cli(["scan", "--state-dir", str(state_dir)])
    code2, _ = _cli(["scan", "--state-dir", str(state_dir)])
    records = _audit(state_dir)
    checks = {
        "exit_zero_both_passes": code1 == 0 and code2 == 0,
        "no_audit_record": len(records) == 0,
        "verdict_still_withheld": set(_verdicts(state_dir).values()) == {VERDICT_WITHHELD},
    }
    return {"passed": all(checks.values()), "checks": checks,
            "numbers": {"exit_codes": [code1, code2], "audit_entries": len(records)},
            "verdict": _verdicts(state_dir)}


def criterion_c(state_dir: Path) -> Dict[str, Any]:
    """Withdrawal: reference row gone -> verdict falls back WITHHELD, audited."""
    fixtures.seed(state_dir)
    store = ReferenceStore(state_dir / "reference_truth.json")
    store.upsert("synthetic_a", quantity="synthetic_quantity_alpha",
                 reference_value=101.0, source="synthetic_source_a", uncertainty_pct=1.0)
    first = scan(*fixtures.open_pair(state_dir))
    verdict_after_activation = _verdicts(state_dir)["synthetic_a"]
    digest_activated = ReferenceStore(state_dir / "reference_truth.json").digest("synthetic_a")
    store.delete("synthetic_a")
    code, payload = _cli(["scan", "--state-dir", str(state_dir)])
    records = _audit(state_dir)
    fall = records[-1] if records else {}
    checks = {
        "activation_first": verdict_after_activation == VERDICT_PASS,
        "withdrawal_exit_zero": code == 0,
        "verdict_back_to_withheld": _verdicts(state_dir)["synthetic_a"] == VERDICT_WITHHELD,
        "withdrawal_audited": fall.get("trigger") == TRIGGER_WITHDRAWAL,
        "audit_has_before_digest": fall.get("old_digest") == digest_activated,
        "no_new_digest": fall.get("new_digest") is None,
        "audit_grew_by_one": len(records) == len(first["events"]) + 1,
    }
    return {"passed": all(checks.values()), "checks": checks,
            "numbers": {"activation_audit": len(first["events"]),
                        "audit_after_withdrawal": len(records),
                        "new_verdict": fall.get("new_verdict"),
                        "error_pct_at_withdrawal": fall.get("error_pct")},
            "verdict": _verdicts(state_dir)}


def criterion_d(state_dir: Path) -> Dict[str, Any]:
    """Forged counter block -> write refused with delta; repair rebuilds from base."""
    fixtures.seed(state_dir, with_references=True)
    scan(*fixtures.open_pair(state_dir))
    state_path = state_dir / "criteria.json"
    audit_before = len(_audit(state_dir))
    document = json.loads(state_path.read_text(encoding="utf-8"))
    forged = dict(document["counters"])
    forged["pass"] = int(forged.get("pass", 0)) + 1     # pretend one more subject passed
    forged["fail"] = int(forged.get("fail", 0)) - 1     # ... and one fewer failed
    document["counters"] = forged
    state_path.write_text(json.dumps(document, indent=1, sort_keys=True), encoding="utf-8")
    tampered_sha = _file_sha(state_path)

    gate_code, gate_payload = _cli(["gate", "--state-dir", str(state_dir)])
    scan_code, scan_payload = _cli(["scan", "--state-dir", str(state_dir)])
    unchanged_after_refusal = _file_sha(state_path) == tampered_sha
    library_deltas = None
    try:  # the library-level path must refuse with the same delta as the CLI
        from .ledger import CriterionLedger as _Ledger

        scan(_Ledger(state_path), ReferenceStore(state_dir / "reference_truth.json"))
    except CounterMismatch as exc:
        library_deltas = exc.deltas
    audit_unchanged = len(_audit(state_dir)) == audit_before
    deltas = (gate_payload or {}).get("deltas") if isinstance(gate_payload, dict) else None

    repair_code, repair_payload = _cli(["recount", "--state-dir", str(state_dir), "--repair"])
    repaired_doc = json.loads(state_path.read_text(encoding="utf-8"))
    records = _audit(state_dir)
    repair_records = [r for r in records if r.get("trigger") == TRIGGER_COUNTER_REPAIR]
    gate_code_after, gate_payload_after = _cli(["gate", "--state-dir", str(state_dir)])

    checks = {
        "gate_refuses_exit_2": gate_code == 2,
        "scan_refuses_exit_2": scan_code == 2,
        "delta_reported": bool(deltas) and deltas.get("pass") == 1 and deltas.get("fail") == -1,
        "no_state_mutation_on_refusal": unchanged_after_refusal and audit_unchanged,
        "library_gate_deltas_match": library_deltas == deltas,
        "repair_exit_zero": repair_code == 0,
        "repair_audited": len(repair_records) == 1 and repair_records[0].get("actor") == "auto",
        "counters_restored": (int(repaired_doc["counters"]["pass"]) == 1
                              and int(repaired_doc["counters"]["fail"]) == 1
                              and int(repaired_doc["counters"]["withheld"]) == 0),
        "gate_green_after_repair": gate_code_after == 0 and gate_payload_after.get("ok") is True,
    }
    return {"passed": all(checks.values()), "checks": checks,
            "numbers": {"gate_exit": gate_code, "scan_exit": scan_code,
                        "deltas": deltas, "audit_before": audit_before,
                        "audit_after_repair": len(records),
                        "repair_details": (repair_payload or {}).get("deltas")
                        if isinstance(repair_payload, dict) else None,
                        "repair_exit": repair_code,
                        "library_deltas": library_deltas},
            "verdict": _verdicts(state_dir)}


def _load_domain_scanner():
    spec = importlib.util.spec_from_file_location("evidence_domain_scan",
                                                  ROOT / "tools" / "evidence_domain_scan.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def criterion_e(artifacts_dir: Path) -> Dict[str, Any]:
    """Zero domain vocabulary in new code, fixtures and generated artifacts."""
    module_dir = Path(__file__).resolve().parent
    targets = [
        str(module_dir),
        "backend/tools/withheld_activation.py",
        "backend/tests/test_withheld_activation.py",
        str(artifacts_dir),
    ]
    scanner = _load_domain_scanner()
    result = scanner.scan_paths(targets, root=ROOT)
    checks = {
        "zero_hits": not result["hits"],
        "coverage_nonzero": result["scanned_files"] > 0,
    }
    return {"passed": all(checks.values()), "checks": checks,
            "numbers": {"scanned_files": result["scanned_files"],
                        "hits": result["hits"][:10], "hit_count": len(result["hits"]),
                        "scanner": "tools/evidence_domain_scan.py",
                        "lexicon": "tools/evidence_domain_lexicon.json"}}


def criterion_f(state_dir: Path) -> Dict[str, Any]:
    """Idempotent replay: three sequential and three concurrent passes, no growth."""
    fixtures.seed(state_dir)
    store = ReferenceStore(state_dir / "reference_truth.json")
    store.upsert("synthetic_a", quantity="synthetic_quantity_alpha",
                 reference_value=101.0, source="synthetic_source_a", uncertainty_pct=1.0)
    first = scan(*fixtures.open_pair(state_dir))
    baseline = len(_audit(state_dir))

    sequential = []
    for _ in range(3):
        code, _ = _cli(["scan", "--state-dir", str(state_dir)])
        sequential.append((code, len(_audit(state_dir))))
    after_sequential = len(_audit(state_dir))

    codes: list = []
    threads = []

    def _run():
        code, _ = _cli(["scan", "--state-dir", str(state_dir)])
        codes.append(code)

    for _ in range(3):
        thread = threading.Thread(target=_run)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()
    after_concurrent = len(_audit(state_dir))
    gate_code, gate_payload = _cli(["gate", "--state-dir", str(state_dir)])

    checks = {
        "first_pass_audited_once": len(first["events"]) == 1,
        "sequential_exit_zero": all(code == 0 for code, _ in sequential),
        "sequential_no_growth": all(count == baseline for _, count in sequential),
        "concurrent_exit_zero": all(code == 0 for code in codes),
        "concurrent_no_growth": after_concurrent == after_sequential,
        "single_audit_record_per_digest": after_concurrent == baseline,
        "gate_green": gate_code == 0 and gate_payload.get("ok") is True,
    }
    return {"passed": all(checks.values()), "checks": checks,
            "numbers": {"baseline_audit": baseline, "sequential_counts": sequential,
                        "after_sequential": after_sequential,
                        "after_concurrent": after_concurrent,
                        "concurrent_exit_codes": sorted(codes)},
            "verdict": _verdicts(state_dir)}


# --------------------------------------------------------------------------- #
# figure
# --------------------------------------------------------------------------- #
def make_figure(out_png: Path, timeline: list, criteria: Dict[str, Dict[str, Any]]) -> Path:
    """Two panels: verdict transitions versus audit sequence; error versus tolerance."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))

    level = {VERDICT_WITHHELD: 0, "FAIL": 1, VERDICT_PASS: 2}
    subject_ids = sorted({r["subject_id"] for r in timeline}) or ["synthetic_a"]
    for index, subject_id in enumerate(subject_ids):
        seqs = [0]
        values = [level[VERDICT_WITHHELD]]
        for record in timeline:
            if record["subject_id"] != subject_id:
                continue
            seqs.append(record["seq"])
            values.append(level[record["new_verdict"]])
        ax1.step(seqs, [v + index * 0.0 for v in values], where="post",
                 marker="o", label=subject_id)
    ax1.set_yticks([0, 1, 2])
    ax1.set_yticklabels([VERDICT_WITHHELD, "FAIL", VERDICT_PASS])
    ax1.set_xlabel("audit record sequence")
    ax1.set_ylabel("verdict")
    ax1.set_title("verdict transitions (auto re-adjudication)")
    ax1.grid(alpha=0.3)
    ax1.legend(fontsize=8)

    subjects = sorted(criteria)
    errors = [criteria[s].get("error_pct") or 0.0 for s in subjects]
    tolerances = [criteria[s].get("tolerance_pct", 0.0) for s in subjects]
    positions = range(len(subjects))
    bars = ax2.bar([p - 0.18 for p in positions], errors, width=0.36,
                   label="relative error [%]", color="#3b7dd8")
    ax2.bar([p + 0.18 for p in positions], tolerances, width=0.36,
            label="tolerance [%]", color="#d8a13b")
    for bar, value in zip(bars, errors):
        ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{value:.3f}",
                 ha="center", va="bottom", fontsize=8)
    ax2.set_xticks(list(positions))
    ax2.set_xticklabels(subjects)
    ax2.set_ylabel("percent")
    ax2.set_title("error vs tolerance at activation")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3, axis="y")

    fig.tight_layout()
    fig.savefig(out_png, dpi=140)
    plt.close(fig)
    return out_png


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #
def run(out_dir: Optional[Path] = None, *, figures: bool = False) -> Dict[str, Any]:
    # absolute: the CLI is exercised from a child process with another cwd
    out = _fresh(Path(out_dir).resolve() if out_dir else OUT_DEFAULT)
    main_dir = out / "state"
    results: Dict[str, Dict[str, Any]] = {}

    results["B"] = criterion_b(out / "negative_control")
    results["A"] = criterion_a(main_dir)
    results["C"] = criterion_c(out / "withdrawal")
    results["D"] = criterion_d(out / "counter_gate")
    results["F"] = criterion_f(out / "idempotence")

    # Demonstrate the *other* branch too: a reference row that is outside the
    # tolerance must produce FAIL, not silence.  This keeps the figure and the
    # audit chain honest about "flip" not meaning "flip to green".
    ledger, store = fixtures.open_pair(main_dir)
    beta = fixtures.DEMO_REFERENCES[1]
    fail_branch = reactivate(ledger, store, beta["subject_id"], quantity=beta["quantity"],
                             reference_value=beta["reference_value"],
                             source=beta["source"], uncertainty_pct=beta["uncertainty_pct"])
    verdicts = {sid: c["verdict"] for sid, c in ledger.criteria.items()}
    errors = {sid: c["error_pct"] for sid, c in ledger.criteria.items()}
    tolerances = {sid: c["tolerance_pct"] for sid, c in ledger.criteria.items()}
    timeline = _audit(main_dir)
    figure_path = None
    if figures:
        figure_path = make_figure(out / "activation_timeline.png", timeline,
                                  {sid: {"error_pct": errors[sid], "tolerance_pct": tolerances[sid]}
                                   for sid in verdicts})
        checks_fig = {"figure_written": Path(figure_path).exists(),
                      "figure_bytes_gt_10k": Path(figure_path).stat().st_size > 10_000}
        results["G"] = {"passed": all(checks_fig.values()), "checks": checks_fig,
                        "numbers": {"png_bytes": Path(figure_path).stat().st_size}}

    results["E"] = criterion_e(out)

    report = {
        "schema": "withheld-activation-acceptance/1.0",
        "generated_at": datetime.now().astimezone().isoformat(),
        "out_dir": str(out),
        "all_passed": all(item["passed"] for item in results.values()),
        "criteria": results,
        "figure": str(figure_path) if figure_path else None,
        "main_ledger": {
            "state_dir": str(main_dir),
            "verdicts": verdicts,
            "error_pct": errors,
            "tolerance_pct": tolerances,
            "audit_entries": len(timeline),
            "reference_table_digest": store.table_digest(),
        },
        "fail_branch": {
            "subject_id": beta["subject_id"],
            "events": fail_branch["events"],
            "audit_appended": fail_branch["audit_appended"],
        },
    }
    (out / "acceptance.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return report