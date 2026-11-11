"""Scan orchestration: run the three probes over many targets and summarise.

Concurrency is thread-pool based (the client is stateless); retries are bounded
and only ever used to obtain a *valid* payload, never to obtain a passing
verdict — a target whose payload keeps being rejected stays ``WITHHELD``.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Sequence

from .autospec import alternate_payload
from .canonical import sha256_text
from .classify import FLAG_TABLE, classify, rule_table_json
from .httpc import JsonClient
from .model import LEVELS, ProbeReport, ScanReport, Target
from .probes import ProbeConfig, run_probes

LIMITS = [
    "Structural scope only: a REAL verdict proves the numbers are computed from the "
    "inputs and are reproducible; it does NOT prove numerical accuracy. Accuracy needs "
    "the analytic/benchmark families in tools.capability_audit or a reference-value run.",
    "Targets are discovered from the endpoint's own OpenAPI schema; endpoints whose body "
    "cannot be synthesised without domain knowledge (nested required objects, domain "
    "validation) end up WITHHELD, not MOCK.",
    "Monotonicity is sign-agnostic by design (zero domain semantics): the detector checks "
    "that an output *moves* and that it moves monotonically along a sweep, not that it "
    "moves in the physically expected direction.",
    "Asynchronous job endpoints (simulate/train/upload/stream/delete/...) are excluded by "
    "default: 'the same request twice' is meaningless for a job launcher.",
    "A structurally stable endpoint can still be wrong; a MOCK verdict is strong evidence, "
    "a REAL verdict is weak evidence.",
]


def _git_commit(root: Optional[str]) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", root or os.getcwd(), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:
        return None


def scan_targets(client: JsonClient, targets: Sequence[Target],
                 cfg: Optional[ProbeConfig] = None, concurrency: int = 8,
                 rejected_retries: int = 2, repo_root: Optional[str] = None,
                 config_sha256: str = "") -> ScanReport:
    """Probe every target; return a fully serialisable scan report."""
    cfg = cfg or ProbeConfig()
    t0 = time.time()
    probes: List[ProbeReport] = []

    def work(target: Target) -> ProbeReport:
        report = run_probes(client, target, cfg)
        attempts = 0
        while (report.level_placeholder() == "rejected" and attempts < rejected_retries):
            alt = alternate_payload(target, attempts + 1)
            attempts += 1
            if not alt:
                break
            retry_target = Target(cap_id=target.cap_id, method=target.method,
                                  path=target.path, base_payload=alt,
                                  sweep_fields=[f for f in (target.sweep_fields or [])
                                                if f in alt],
                                  timeout_s=target.timeout_s, note=target.note)
            retry_report = run_probes(client, retry_target, cfg)
            retry_report.flags.append(f"PAYLOAD_RETRY_{attempts}")
            if retry_report.det_ok is not None and retry_report.http_ok:
                return retry_report
            report = retry_report
        return report

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(work, t): t for t in targets}
        for fut in as_completed(futures):
            probes.append(fut.result())

    for report in probes:
        classify(report)

    probes.sort(key=lambda r: (LEVELS.index(r.level) if r.level in LEVELS else 99, r.path))
    report = ScanReport(
        generated_at=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        base_url=client.base_url,
        config_sha256=config_sha256 or sha256_text(json.dumps(cfg.to_dict(), sort_keys=True)),
        code_commit=_git_commit(repo_root),
        probes=probes,
        summary=summarise(probes),
        limits=list(LIMITS),
    )
    report.summary["wall_seconds"] = round(time.time() - t0, 3)
    report.summary["http_requests"] = client.calls
    report.summary["probe_config"] = cfg.to_dict()
    report.summary["rule_table"] = rule_table_json()
    report.summary["flag_table"] = FLAG_TABLE
    return report


def summarise(probes: Sequence[ProbeReport]) -> Dict[str, object]:
    counts = {level: 0 for level in LEVELS}
    reasons: Dict[str, int] = {}
    flags: Dict[str, int] = {}
    for p in probes:
        counts[p.level] = counts.get(p.level, 0) + 1
        for code in p.reason_codes:
            reasons[code] = reasons.get(code, 0) + 1
        for flag in p.flags:
            flags[flag] = flags.get(flag, 0) + 1
    n = len(probes)
    decided = counts["REAL"] + counts["MOCK"] + counts["STUB"]
    return {
        "n_targets": n,
        "level_counts": counts,
        "level_fraction": {k: (round(v / n, 6) if n else 0.0) for k, v in counts.items()},
        "n_decided": decided,
        "decision_rate": round(decided / n, 6) if n else 0.0,
        "reason_counts": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "flag_counts": dict(sorted(flags.items(), key=lambda kv: -kv[1])),
        "mock_ids": sorted(p.cap_id for p in probes if p.level == "MOCK"),
        "stub_ids": sorted(p.cap_id for p in probes if p.level == "STUB"),
        "withheld_ids": sorted(p.cap_id for p in probes if p.level == "WITHHELD"),
        "not_supported_ids": sorted(p.cap_id for p in probes if p.level == "NOT_SUPPORTED"),
        "real_ids": sorted(p.cap_id for p in probes if p.level == "REAL"),
    }