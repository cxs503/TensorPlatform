"""Cross-solver interoperation gate (mechanics only).

A campaign integration gate has to answer four kinds of question and nothing
else: *do the two solver arms describe the same object*, *do their curves agree
within the declared tolerance*, *does the producer's output satisfy the
consumer's interface contract*, and *is every upstream FAIL carried into the
gate verdict*.  This module implements exactly that, driven by a declarative
manifest:

``artifacts``  name -> {path, json_pointer?, sha256?}   (tamper-evident inputs)
``items``      acceptance item -> list of declarative checks
``defaults``   tolerance defaults

Verdict algebra (fail-closed, honest-FAIL preserving):
``item = FAIL`` if any check FAIL, else ``WITHHELD`` if any check WITHHELD,
else ``PASS``; ``gate = FAIL`` if any item FAIL, else ``WITHHELD`` if any item
WITHHELD, else ``PASS``.  Absent evidence therefore can never produce a green
gate, and a ``FAIL`` can never be upgraded by a later flag.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .checks import FAIL, PASS, RANK, WITHHELD, Ctx, run_check
from .units import UnitTable, load_default_table

VERDICT_SCHEMA = "tp.xsolver.interop_verdict/v1"


class GateError(Exception):
    """Raised on manifest schema violations (fail-closed, exit code 2)."""


# ---------------------------------------------------------------------------
# io helpers
# ---------------------------------------------------------------------------
def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path | str) -> str:
    return sha256_bytes(Path(path).read_bytes())


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest_of(payload: Any) -> str:
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


# ---------------------------------------------------------------------------
# manifest validation / artifact resolution
# ---------------------------------------------------------------------------
_REQUIRED_TOP = ("campaign", "items")


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    if not isinstance(manifest, Mapping):
        raise GateError("manifest must be a JSON object")
    for key in _REQUIRED_TOP:
        if key not in manifest:
            raise GateError(f"manifest missing required key {key!r}")
    if not isinstance(manifest.get("items"), list) or not manifest["items"]:
        raise GateError("manifest 'items' must be a non-empty list")
    seen_items, seen_checks = set(), set()
    for item in manifest["items"]:
        if not isinstance(item, Mapping) or "id" not in item or "checks" not in item:
            raise GateError(f"item malformed: {item!r}")
        if item["id"] in seen_items:
            raise GateError(f"duplicate item id {item['id']!r}")
        seen_items.add(item["id"])
        if not isinstance(item["checks"], list) or not item["checks"]:
            raise GateError(f"item {item['id']!r} has no checks")
        for check in item["checks"]:
            if not isinstance(check, Mapping) or "id" not in check or "kind" not in check:
                raise GateError(f"check malformed in item {item['id']!r}: {check!r}")
            if check["id"] in seen_checks:
                raise GateError(f"duplicate check id {check['id']!r}")
            seen_checks.add(check["id"])
    artifacts = manifest.get("artifacts") or {}
    if not isinstance(artifacts, Mapping):
        raise GateError("manifest 'artifacts' must be an object")


def resolve_artifacts(manifest: Mapping[str, Any], root: Path | str) -> Dict[str, Dict[str, Any]]:
    """Load declared artifacts, verifying the pinned sha256 when given."""
    base = Path(root)
    resolved: Dict[str, Dict[str, Any]] = {}
    for name, spec in (manifest.get("artifacts") or {}).items():
        spec = spec or {}
        rel = spec.get("path")
        if not rel:
            raise GateError(f"artifact {name!r} declares no path")
        path = (base / rel).resolve()
        if not path.exists():
            resolved[name] = {
                "name": name, "path": str(rel), "present": False,
                "reason": spec.get("missing_reason") or "file not found in worktree",
                "sha256": None, "data": None, "expected_sha256": spec.get("sha256"),
            }
            continue
        payload = path.read_bytes()
        actual = sha256_bytes(payload)
        expected = spec.get("sha256")
        if expected and expected != actual:
            resolved[name] = {
                "name": name, "path": str(rel), "present": False,
                "reason": f"sha256 mismatch (expected {expected[:12]}…, got {actual[:12]}…)",
                "sha256": actual, "data": None, "expected_sha256": expected,
            }
            continue
        data = None
        if str(rel).endswith(".json"):
            try:
                data = json.loads(payload.decode("utf-8"))
            except json.JSONDecodeError as exc:
                resolved[name] = {"name": name, "path": str(rel), "present": False,
                                  "reason": f"invalid JSON: {exc}", "sha256": actual,
                                  "data": None, "expected_sha256": expected}
                continue
        resolved[name] = {"name": name, "path": str(rel), "present": True, "reason": None,
                          "sha256": actual, "data": data, "expected_sha256": expected}
    return resolved


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------
def _rollup(statuses: List[str]) -> str:
    if not statuses:
        return WITHHELD
    worst = min(statuses, key=lambda s: RANK[s])
    return worst


def evaluate(manifest: Mapping[str, Any], *, root: Path | str,
             units: Optional[UnitTable] = None) -> Dict[str, Any]:
    validate_manifest(manifest)
    units = units or load_default_table()
    artifacts = resolve_artifacts(manifest, root)
    ctx = Ctx(artifacts, units, manifest.get("defaults"))

    items_out: List[Dict[str, Any]] = []
    fractures: List[Dict[str, Any]] = []
    for item in manifest["items"]:
        checks_out = []
        for spec in item["checks"]:
            outcome = run_check(spec, ctx)
            outcome["item"] = item["id"]
            checks_out.append(outcome)
            for fracture in outcome["fractures"]:
                fractures.append({"item": item["id"], **fracture})
        status = _rollup([c["status"] for c in checks_out])
        items_out.append({
            "id": item["id"],
            "title": item.get("title") or item["id"],
            "status": status,
            "checks": checks_out,
            "counts": {s: sum(1 for c in checks_out if c["status"] == s)
                       for s in (PASS, FAIL, WITHHELD)},
            "fracture_checks": [c["id"] for c in checks_out if c["status"] == FAIL],
        })

    gate_status = _rollup([i["status"] for i in items_out])
    verdict = {
        "schema": VERDICT_SCHEMA,
        "campaign": manifest.get("campaign"),
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "gate_status": gate_status,
        "honest_fail": gate_status == FAIL,
        "upgrade_forbidden": True,
        "items": items_out,
        "fractures": fractures,
        "fracture_summary": {
            "by_item": {i["id"]: i["fracture_checks"] for i in items_out if i["fracture_checks"]},
            "by_type": _count_by(fractures, "type"),
        },
        "artifacts": {
            name: {"path": a["path"], "present": a["present"], "sha256": a["sha256"],
                   "reason": a["reason"], "expected_sha256": a["expected_sha256"]}
            for name, a in artifacts.items()
        },
        "manifest_digest": digest_of(manifest),
        "verdict_digest": None,
    }
    # content digest: excludes its own slot and the run timestamp, so two runs over
    # the same manifest + artifacts produce the same digest (reproducibility anchor)
    verdict["verdict_digest"] = digest_of({k: v for k, v in verdict.items()
                                           if k not in ("verdict_digest", "generated_at")})
    return verdict


def _count_by(rows: List[Mapping[str, Any]], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        counts[str(row.get(key))] = counts.get(str(row.get(key)), 0) + 1
    return dict(sorted(counts.items()))


def summarize(verdict: Mapping[str, Any]) -> str:
    lines = [f"gate {verdict['gate_status']} · campaign {verdict.get('campaign')}"]
    for item in verdict["items"]:
        counts = item["counts"]
        lines.append(f"  [{item['status']:8s}] {item['id']:28s} "
                     f"P/{counts[PASS]} F/{counts[FAIL]} W/{counts[WITHHELD]}")
    if verdict["fractures"]:
        lines.append("  fracture points:")
        for fracture in verdict["fractures"]:
            lines.append(f"   - {fracture['item']} :: {fracture['check']} :: "
                         f"{fracture['type']} :: {fracture['message'][:120]}")
    return "\n".join(lines)


def write_verdict(verdict: Mapping[str, Any], path: Path | str) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(verdict, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def load_manifest(path: Path | str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))