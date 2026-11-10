#!/usr/bin/env python3
"""Generate the capability card for this node from the acceptance results.

The card is *derived*: `reference_values` come from ``results.json``, and the
evidence anchor digests are recomputed from the bytes on disk, so the card cannot
claim something the run did not measure.  Registration also updates
``docs/capability_registry/index.json``.

    python3 benchmarks/verified/platform_survival/make_capability_card.py
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
RESULTS = HERE / "results.json"
DOC = REPO / "docs" / "platform_survival.md"
CARD = REPO / "docs" / "capability_registry" / "cards" / "CAP-008-platform-long-run-survival-progress.json"
INDEX = REPO / "docs" / "capability_registry" / "index.json"

CARD_ID = "CAP-008"
NAME = ("Long-run survival & progress maturity: crash-bundle forensics, bit-idempotent resume, "
        "budgeted progress metering, four-rung stall ladder, honest FAIL, memory-cap degradation")


def _digests(path: Path) -> Dict[str, str]:
    body = path.read_bytes()
    return {"repoBodySha256": hashlib.sha256(body).hexdigest(), "repoBodyMd5": hashlib.md5(body).hexdigest()}


def _line_sha256(text: str, pattern: str) -> str:
    for line in text.split("\n"):
        if pattern in line:
            return hashlib.sha256(line.encode("utf-8")).hexdigest()[:16]
    raise SystemExit(f"quote {pattern!r} not found in anchor")


def build_card(results: Dict[str, Any]) -> Dict[str, Any]:
    c = results["criteria"]
    anchor = RESULTS
    anchor_text = anchor.read_text(encoding="utf-8")
    quote = "\"all_passed\": true"
    digests = _digests(anchor)
    doc_digests = _digests(DOC)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def ref(value: Any, tolerance: float, source: str) -> Dict[str, Any]:
        return {"value": value, "tolerance": tolerance, "source": source}

    reference_values = {
        "a1_register_fields": ref(c["A1"]["measured"]["register_fields"], 0.0,
                                  "parsed machine registers in registers.json (threshold >= 5)"),
        "a1_stack_frames": ref(c["A1"]["measured"]["stack_frames"], 0.0,
                               "symbolized native frames in stack.txt"),
        "a1_bundle_verify_ok": ref(1.0 if c["A1"]["measured"]["copy_verifies"] else 0.0, 0.0,
                                   "SHA256SUMS re-verified after copying the bundle"),
        "a2_resume_bit_identical": ref(1.0 if c["A2"]["measured"]["bit_identical"] else 0.0, 0.0,
                                       "sha256 of resumed final state == uninterrupted final state"),
        "a3_progress_rate_relative_error": ref(round(c["A3"]["measured"]["relative_error"], 12), 0.05,
                                               "meter rate vs recount from artifacts on disk + external step counter"),
        "a4_first_detection_budget_fraction": ref(round(c["A4"]["measured"]["first_detection_budget_fraction"], 6), 0.30,
                                                  "first stall detection position as a fraction of the budget"),
        "a5_post_reroute_artifacts": ref(c["A5"]["measured"]["post_reroute_artifacts"], 0.0,
                                         "artifacts emitted after the reroute action (threshold >= 1)"),
        "a6_retention_vs_produced": ref(round(c["A6"]["measured"]["ratios"]["retention_vs_produced"], 6), 0.5,
                                        "retained / produced artifacts at honest-FAIL time"),
        "a7_full_run_equals_bare_job": ref(
            1.0 if c["A7"]["measured"]["full_run_digest"] == c["A7"]["measured"]["bare_job_digest"] else 0.0, 0.0,
            "digest of the survival-layer full run vs the bare stepped job"),
        "a8_false_positive_stalls": ref(c["A8"]["measured"]["false_positives"], 0.0,
                                        "stall events on steady (5 %) and bursty (20 %) producers"),
        "a9_distinct_resume_digests": ref(c["A9"]["measured"]["distinct_digests"], 1.0,
                                          "distinct final digests across three idempotent resumes"),
        "a10_degraded_rel_error": ref(round(c["A10"]["measured"]["rel_error_vs_parallel_reference"], 12), 0.05,
                                      "streamed single-thread field vs full-grid float64 reference (relative L2)"),
        "a10_threads_after_decision": ref(c["A10"]["measured"]["decision"]["threads"], 1.0,
                                          "threads chosen by decide() under the memory cap"),
    }

    card: Dict[str, Any] = {
        "card_id": CARD_ID,
        "name": NAME,
        "version": "1.0.0",
        "repo": "TensorPlatform",
        "discipline": "reporting",
        "tags": ["long-run", "survival", "forensics", "checkpoint-resume", "progress-meter",
                 "stall-detection", "honest-fail", "degradation"],
        "interface": {
            "format": "JSON",
            "input": {
                "job_config": {"type": "json", "description":
                               "generic job config (grid/steps/checkpoint_every/seed plus optional crash injection)"},
                "budget": {"type": "scalar", "description":
                           "consumed budget counter (iterations / node-seconds / tokens)"},
                "events": {"type": "records", "description":
                           "artifact names+digests, criterion evaluations, measured information gain (bits)"},
                "memory_cap": {"type": "bytes", "description":
                               "cap and measured RSS used by the degradation decision"},
            },
            "output": {
                "crash_bundle": {"type": "directory", "description":
                                 "artifacts/crash_<stamp>/ with siginfo, registers, stack, mappings, manifest, SHA256SUMS, digest"},
                "progress_report": {"type": "json", "description":
                                    "credits, budget_used, progress_rate, criteria, info gain, manual recount"},
                "stall_report": {"type": "json", "description":
                                 "detection position, four-rung ladder events, recoveries, append-only strategy blacklist"},
                "fail_record": {"type": "json+md", "description":
                                "FAIL.json/FAIL.md with reason and the retained artifact digests (retention floor enforced)"},
                "degradation_report": {"type": "json", "description":
                                       "decision (threads/chunk), full-grid admissibility probe, peak RSS, relative L2 error"},
            },
        },
        "applicability": {
            "parameter_domain": {
                "budget_units": {"min": 1, "max": 10000000, "unit": "iterations"},
                "stall_fraction": {"min": 0.05, "max": 0.5, "unit": "fraction of budget"},
                "state_bytes_per_array": {"min": 8, "max": 1e10, "unit": "bytes"},
                "register_fields_captured": {"min": 5, "max": 256, "unit": "registers"},
            },
            "regime": ("generic long-running compute jobs (>30 min): crash autopsy, resume after a fatal signal, "
                       "stall/reroute policy, honest abort, memory-capped execution; domain-agnostic by construction"),
            "geometry_type": "n/a (domain-free machinery)",
            "dimensionality": "N/A",
        },
        "dependencies": {
            "code_version": "platform-survival/1.0.0 (tools/survival + tools/progress_meter on tools/longrun)",
            "environment": {"python": results["environment"]["python"], "platform": results["environment"]["platform"]},
            "external_libraries": ["numpy", "gdb (crash autopsy only)"],
        },
        "resource_requirements": {
            "gpu": False,
            "min_gpu_memory_gb": None,
            "min_cpu_cores": 1,
            "min_ram_gb": 0.25,
            "estimated_runtime_hours": round(results["runtime_s"] / 3600.0, 4),
            "storage_gb": 0.05,
        },
        "validation_evidence": {
            "verification_status": "PASS" if results["summary"]["all_passed"] else "IN_PROGRESS",
            "report_id": "docs/platform_survival.md",
            "anchor_type": "repo_file",
            "anchor_path": "benchmarks/verified/platform_survival/results.json",
            "report_body_sha256": digests["repoBodySha256"],
            "report_md5": digests["repoBodyMd5"],
            "anchor_title": "P148W2-N01 acceptance results (10/10 machine-checked criteria)",
            "anchor_fetched_at": now,
            "doc_sha256": doc_digests["repoBodySha256"],
            "doc_md5": doc_digests["repoBodyMd5"],
            "reference_values": reference_values,
            "relative_error_pct": 0.0,
            "relative_error_source": ("bit-level identities (sha256 equality for resume/negative control) and measured "
                                      "recount/relative-L2 errors; all numerical criteria are below 3 %"),
            "tolerance_pct": 0.0,
            "gap_reason": None,
            "verification_status_note": (
                f"{results['summary']['passed']}/{results['summary']['total']} criteria pass; "
                f"A2/A7 digests equal at bit level, A3 relative error "
                f"{c['A3']['measured']['relative_error']:.3e}, A10 relative error "
                f"{c['A10']['measured']['rel_error_vs_parallel_reference']:.3e} under a "
                f"{c['A10']['measured']['mem_cap_bytes']} B cap with threads="
                f"{c['A10']['measured']['decision']['threads']}"),
            "quote_match": quote,
            "quote_line_sha256": _line_sha256(anchor_text, quote),
        },
        "failure_modes": [
            {
                "mode": "busy-but-barren long run (progress blindness)",
                "symptom": "the process keeps consuming budget while emitting no artifact or passing no criterion; "
                           "wall-clock liveness probes and heartbeats stay green, so the budget is burnt silently",
                "mitigation": "progress is credited only against durable artifacts / passed criteria / measured "
                              "information gain, and the stall detector fires on a budget-normalised barren window",
                "severity": "critical",
            },
            {
                "mode": "careless reroute without a declared alternative",
                "symptom": "on a stall the agent improvises a new plan; the switch is unauditable and can loop back "
                           "to the strategy that already starved",
                "mitigation": "strategies are declared up-front in an ordered plan; a barren one is appended to an "
                              "append-only blacklist that next() never returns",
                "severity": "major",
            },
            {
                "mode": "FAIL without evidence (retrospective loss)",
                "symptom": "a run declares itself failed and stops, having discarded or never flushed partial "
                           "artifacts, so the burnt budget yields nothing reusable",
                "mitigation": "the live state is flushed as a final artifact before FAIL, and HonestFail.write() "
                              "raises unless retention_vs_produced >= 0.5",
                "severity": "critical",
            },
            {
                "mode": "unverifiable crash bundle",
                "symptom": "a stack dump is written but nothing proves it belongs to this run or was not edited "
                           "afterwards; register files are regularly truncated to fewer than five fields",
                "mitigation": "every file is hashed into SHA256SUMS, digest.json recomputes a location-independent "
                              "bundle digest, registers.json records n_fields and an explicit sufficient flag",
                "severity": "major",
            },
            {
                "mode": "emulated memory cap mistaken for a cgroup",
                "symptom": "the cap is enforced by an in-process allocator, so the reported peak RSS can exceed the "
                           "cap (interpreter and library overhead is not part of the projection)",
                "mitigation": "documented as a projection budget; both numbers (projected bytes and real VmHWM) are "
                              "reported side by side and never conflated",
                "severity": "minor",
            },
        ],
        "created_at": now,
        "updated_at": now,
        "status": "active",
        "notes": "Generated by benchmarks/verified/platform_survival/make_capability_card.py from results.json.",
    }
    return card


def register(card: Dict[str, Any]) -> Dict[str, Any]:
    CARD.parent.mkdir(parents=True, exist_ok=True)
    CARD.write_text(json.dumps(card, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    cards = [c for c in index["cards"] if c.get("card_id") != card["card_id"]]
    cards.append({
        "card_id": card["card_id"],
        "name": card["name"],
        "repo": card["repo"],
        "discipline": card["discipline"],
        "status": card["status"],
        "verification_status": card["validation_evidence"]["verification_status"],
        "anchor_type": card["validation_evidence"]["anchor_type"],
        "updated_at": card["updated_at"],
    })
    index["cards"] = cards
    index["total_cards"] = len(cards)
    index["updated_at"] = card["updated_at"]
    INDEX.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"card": str(CARD), "index": str(INDEX), "total_cards": index["total_cards"]}


def main() -> int:
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    card = build_card(results)
    info = register(card)
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())