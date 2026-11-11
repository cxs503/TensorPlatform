"""Ledger linkage: compare structural levels against the platform's own claims.

The platform publishes a capability ledger (``artifacts/capability_ledger/``)
in which each capability carries a ``claimed_level``.  This module cross-checks
those claims against the structurally observed level and flags every entry whose
claim is stronger than the evidence — in particular **claimed REAL but observed
MOCK/STUB**.

Two rules make the linkage safe:

* the canonical ledger is **never** mutated: the ledger content is pinned by
  ``tools.audit.ledger_paths`` (sha256 + contract gate), so an automated write
  would break the contract gate and destroy the ability to tell "what the
  platform claimed at that moment" from "what a later audit thought".  Instead an
  *overlay* is written, and ``--apply-to`` can produce a **derived** ledger copy.
* the capability-id → endpoint-path mapping is either injected (config) or
  derived from an existing adapter registry; when no mapping exists the entry is
  reported ``UNMAPPED`` rather than silently closed as agreement.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .model import LEVEL_RANK, ProbeReport

OVERCLAIM_FLAGS = ("MOCK_OVERCLAIM", "STUB_OVERCLAIM")
#: every flag this module can emit (kept explicit so the report can enumerate them)
ALL_FLAGS = ("MOCK_OVERCLAIM", "STUB_OVERCLAIM", "UNVERIFIED_WITHHELD",
             "OBSERVED_UNCLAIMED", "UNDERCLAIM", "AGREE", "UNMAPPED")


def _tokens(text: str) -> List[str]:
    parts = re.split(r"[^0-9a-zA-Z]+", text.lower())
    out = []
    for part in parts:
        if not part or part in {"api", "v1", "http", "post", "get", "tensor", "capabilities"}:
            continue
        out.append(part[:-1] if len(part) > 3 and part.endswith("s") else part)
    return out


def match_score(cap_id: str, path: str) -> float:
    """Token-overlap score in [0,1] between a ledger id and an endpoint path."""
    a, b = set(_tokens(cap_id)), set(_tokens(path))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a)


def mapping_from_adapters() -> Dict[str, str]:
    """Use the existing physics-audit registry as a cap_id → path oracle, if present."""
    try:
        from tools.capability_audit import adapters as cap_adapters
    except Exception:
        return {}
    out: Dict[str, str] = {}
    for spec in getattr(cap_adapters, "SPECS", []) or []:
        cap_id = getattr(spec, "cap_id", None)
        endpoint = getattr(spec, "endpoint", None)
        if cap_id and endpoint:
            out[cap_id] = endpoint
    return out


def load_ledger(root: Optional[str] = None) -> Dict[str, Any]:
    """Ledger via the single contract resolver; degraded result is reported honestly."""
    try:
        from tools.audit.ledger_paths import resolve_ledger
        ref = resolve_ledger(root)
        payload: Any = None
        if ref.get("path"):
            try:
                payload = json.loads(Path(ref["path"]).read_text(encoding="utf-8"))
            except Exception:
                payload = None
        return {"status": ref.get("status"), "path": ref.get("path"),
                "sha256": ref.get("sha256"), "reasons": ref.get("reasons", []),
                "payload": payload}
    except Exception as exc:                       # pragma: no cover - defensive
        return {"status": "DEGRADED", "path": None, "sha256": None,
                "reasons": [f"RESOLVER_UNAVAILABLE: {type(exc).__name__}"],
                "payload": None}


def build_overlay(probes: Sequence[ProbeReport], ledger: Dict[str, Any],
                  mapping: Optional[Dict[str, str]] = None,
                  min_match: float = 0.5) -> Dict[str, Any]:
    """Compare claims with observations; return the overlay document."""
    payload = ledger.get("payload") or {}
    caps = payload.get("capabilities") or []
    mapping = dict(mapping or {})
    by_path = {p.path: p for p in probes}

    entries: List[Dict[str, Any]] = []
    for cap in caps:
        cap_id = cap.get("id") or cap.get("capability_id") or ""
        claimed = cap.get("claimed_level") or "WITHHELD"
        path = mapping.get(cap_id)
        method = "POST"
        if path and " " in path:
            method, path = path.split(" ", 1)
        if path and path not in by_path:
            # adapters store paths without the deployment prefix; normalise both ways
            for prefix in ("/api/v1", "/api"):
                cand = path if path.startswith(prefix) else f"{prefix}{path}"
                if cand in by_path:
                    path = cand
                    break
        if not path or path not in by_path:
            scored = sorted(((match_score(cap_id, p.path), p.path) for p in probes),
                            reverse=True)
            if scored and scored[0][0] >= min_match:
                path = scored[0][1]
        probe = by_path.get(path) if path else None
        entry: Dict[str, Any] = {
            "cap_id": cap_id,
            "domain": cap.get("domain", ""),
            "claimed_level": claimed,
            "endpoint": path,
            "mapping_method": "adapter" if cap_id in (mapping or {}) else (
                "token_match" if probe else "none"),
        }
        if probe is None:
            entry.update({"observed_level": None, "flag": "UNMAPPED",
                          "action": "no endpoint in this scan matched the ledger id"})
        else:
            observed = probe.level
            entry.update({
                "observed_level": observed,
                "reason_codes": probe.reason_codes,
                "flags": probe.flags,
                "detail": probe.detail,
            })
            if (observed in ("MOCK", "STUB")
                    and LEVEL_RANK.get(claimed, 0) > LEVEL_RANK[observed]):
                entry["flag"] = f"{observed}_OVERCLAIM"
                entry["action"] = (f"auto-flag: claimed {claimed} but structurally {observed}; "
                                   f"demote to {observed} pending re-audit")
            elif claimed == "NOT_SUPPORTED" and observed in ("REAL", "MOCK", "STUB"):
                entry["flag"] = "OBSERVED_UNCLAIMED"
                entry["action"] = (f"claimed NOT_SUPPORTED yet an endpoint answers as {observed}: "
                                   f"the ledger is silent about a live surface")
            elif observed == "WITHHELD" and LEVEL_RANK.get(claimed, 0) > LEVEL_RANK["WITHHELD"]:
                entry["flag"] = "UNVERIFIED_WITHHELD"
                entry["action"] = (f"claim {claimed} is stronger than the evidence (WITHHELD): "
                                   f"no structural probe result — still an overclaim by omission")
            elif observed == "REAL" and LEVEL_RANK.get(claimed, 0) < LEVEL_RANK["REAL"]:
                entry["flag"] = "UNDERCLAIM"
                entry["action"] = f"claim {claimed} is weaker than the structural observation {observed}"
            else:
                entry["flag"] = "AGREE"
                entry["action"] = "no change"
        entries.append(entry)

    flag_counts: Dict[str, int] = {}
    for entry in entries:
        flag_counts[entry["flag"]] = flag_counts.get(entry["flag"], 0) + 1
    return {
        "schema": "mock-detector-ledger-overlay/1.0",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "ledger": {"path": ledger.get("path"), "sha256": ledger.get("sha256"),
                   "status": ledger.get("status"), "reasons": ledger.get("reasons", []),
                   "n_capabilities": len(caps), "mutated": False},
        "overclaim_flags": list(OVERCLAIM_FLAGS),
        "flag_counts": flag_counts,
        "mocked_capability_ids": sorted(e["cap_id"] for e in entries
                                        if e["flag"] in ("MOCK_OVERCLAIM", "STUB_OVERCLAIM")),
        "entries": entries,
    }


def apply_overlay(overlay: Dict[str, Any], ledger: Dict[str, Any],
                  out_path: str) -> Dict[str, Any]:
    """Write a **derived** ledger copy carrying ``observed_level`` + ``mock_flag``."""
    payload = json.loads(json.dumps(ledger.get("payload") or {}))
    flags = {e["cap_id"]: e for e in overlay.get("entries", [])}
    marked = 0
    for cap in payload.get("capabilities", []):
        cap_id = cap.get("id") or cap.get("capability_id")
        entry = flags.get(cap_id)
        if not entry:
            continue
        cap["observed_level_structural"] = entry.get("observed_level")
        cap["mock_flag"] = entry.get("flag")
        if entry["flag"] in ("MOCK_OVERCLAIM", "STUB_OVERCLAIM"):
            cap["reconciled_level_proposed"] = entry["observed_level"]
            marked += 1
    payload["derived_from_sha256"] = ledger.get("sha256")
    payload["derived_note"] = ("derived by tools.mock_detector: canonical ledger untouched; "
                               "structural evidence is scoped to level_scope=STRUCTURAL")
    payload["n_mock_flags"] = marked
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    return {"out_path": out_path, "n_mock_flags": marked}