#!/usr/bin/env python3
"""Evidence bundle for an audit run: sha256 chain, environment fingerprint, tamper check.

Bundle layout::

    <bundle>/
        plan.json            # snapshot of the declarations that were executed
        ledger.json          # machine-readable per-pair ledger
        LEDGER.md            # human-readable mirror of the same numbers
        targets/<id>.json    # one record per target (declaration + verdicts)
        manifest.json        # sha256 + chain hash + root digest of the above

The chain is unkeyed, so it proves *internal* consistency and byte-level
tampering, not authorship (same honest limitation as the reference-comparison
bundle).  ``verify_bundle`` re-hashes everything and classifies ``CONTENT`` /
``STRUCTURAL`` / ``CHAIN`` tampering.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from tools.verification_harness.evidence import (
    DEFAULT_DEPENDENCY_PROBE,
    chain_hash,
    environment_digest,
    environment_fingerprint,
    root_digest,
    sha256_bytes,
)

from .engine import AuditPlan, ledger_markdown
from .model import AUDIT_SCHEMA_VERSION, AuditReport, round_floats

MANIFEST_NAME = "manifest.json"
TARGETS_DIRNAME = "targets"


def _safe_name(identifier: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", identifier).strip("._")
    return cleaned or "target"


def _dumps(payload: Any) -> bytes:
    return (json.dumps(round_floats(payload), indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_bundle(report: AuditReport, plan: AuditPlan, out_dir: str | Path, *,
                 dependencies: Iterable[str] = DEFAULT_DEPENDENCY_PROBE) -> Dict[str, Any]:
    """Freeze plan + ledger into ``out_dir`` and return the manifest."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / TARGETS_DIRNAME).mkdir(parents=True, exist_ok=True)

    fingerprint = environment_fingerprint(dependencies)
    env_digest = environment_digest(fingerprint)

    artifacts: List[Tuple[str, str, bytes]] = []
    artifacts.append(("plan.json", "plan", _dumps(plan.to_dict())))
    artifacts.append(("ledger.json", "ledger", _dumps(report.to_dict())))
    artifacts.append(("LEDGER.md", "ledger_markdown",
                      ledger_markdown(report).encode("utf-8")))
    for target in report.targets:
        artifacts.append((f"{TARGETS_DIRNAME}/{_safe_name(target.target_id)}.json",
                          "target_record", _dumps(target.to_dict())))

    entries: List[Dict[str, Any]] = []
    previous = env_digest
    for rel_path, role, payload in sorted(artifacts, key=lambda item: item[0]):
        target_path = out_dir / rel_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(payload)
        digest = sha256_bytes(payload)
        link = chain_hash(previous, rel_path, digest)
        entries.append({
            "path": rel_path,
            "role": role,
            "size": len(payload),
            "sha256": digest,
            "chain_hash": link,
        })
        previous = link

    manifest = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "plan_id": report.plan_id,
        "plan_digest": report.plan_digest,
        "counts": report.counts(),
        "pair_counts": report.pair_counts(),
        "environment": fingerprint,
        "environment_digest": env_digest,
        "entries": entries,
        "root_digest": root_digest(entries),
        "chain_head": previous,
    }
    (out_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def verify_bundle(out_dir: str | Path) -> Dict[str, Any]:
    """Re-hash a bundle and classify tampering (never raises on damage)."""
    out_dir = Path(out_dir)
    manifest_path = out_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        return {"ok": False, "errors": [f"missing {MANIFEST_NAME}"],
                "tamper_classes": ["STRUCTURAL"]}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = list(manifest["entries"])
        env_digest = str(manifest["environment_digest"])
        recorded_head = str(manifest["chain_head"])
        recorded_root = str(manifest["root_digest"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        return {"ok": False, "errors": [f"unreadable manifest: {exc}"],
                "tamper_classes": ["STRUCTURAL"]}

    missing: List[str] = []
    content_tampered: List[str] = []
    chain_broken: List[str] = []
    previous = env_digest
    for entry in entries:
        rel_path = str(entry.get("path"))
        digest = str(entry.get("sha256"))
        target = out_dir / rel_path
        if not target.is_file():
            missing.append(rel_path)
            previous = str(entry.get("chain_hash"))
            continue
        actual = sha256_bytes(target.read_bytes())
        if actual != digest:
            content_tampered.append(rel_path)
        if chain_hash(previous, rel_path, digest) != str(entry.get("chain_hash")):
            chain_broken.append(rel_path)
        previous = str(entry.get("chain_hash"))
    chain_head_ok = previous == recorded_head
    root_ok = root_digest(entries) == recorded_root
    recorded = {str(e.get("path")) for e in entries}
    extra = sorted(str(p.relative_to(out_dir)) for p in out_dir.rglob("*")
                   if p.is_file() and str(p.relative_to(out_dir)) not in recorded
                   and p.name != MANIFEST_NAME)

    tamper_classes = []
    if content_tampered:
        tamper_classes.append("CONTENT")
    if missing or extra:
        tamper_classes.append("STRUCTURAL")
    if chain_broken or not chain_head_ok or not root_ok:
        tamper_classes.append("CHAIN")
    return {
        "ok": not tamper_classes,
        "bundle": str(out_dir),
        "checked_entries": len(entries),
        "missing": missing,
        "extra": extra,
        "content_tampered": content_tampered,
        "chain_broken": chain_broken,
        "chain_head_ok": chain_head_ok,
        "root_digest_ok": root_ok,
        "tamper_classes": tamper_classes,
    }