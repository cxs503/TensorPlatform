"""History rescan: read-only sweep of the most recent missions' reports/artifacts.

Why rescan history at all? A gate only protects the future. Everything already
written — reports POSTed, artifacts committed, agent work trees filled by earlier
missions — predates the gate, and that backlog is where a leak would already be
sitting. The rescan therefore

* is **read-only** and proves it (sha256 of every scanned file is compared before
  and after the sweep, and no path is ever opened for writing);
* produces an **exposure list** with mission / file / line / type, where the
  matched value is represented only by a mask + sha256 (the report must not
  become the leak it is describing);
* reports a **recomputable before/after delta**: the same corpus is scanned by a
  naive baseline profile (shaped patterns only, no decode, no entropy) and by the
  full profile. The difference is the capability the node adds — a number, not a
  claim;
* proves the repair loop on the real corpus: every exposed text is redacted in
  memory and re-scanned, and the residual hit count must be zero.
"""

from __future__ import annotations

import fnmatch
import glob
import hashlib
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .engine import Scanner
from .policy import load_policy

SCHEMA = "tp.secret_scan.history/v1"


def _readonly_static_ok() -> bool:
    """Static guarantee: this module never opens a mission file for writing.

    The check inspects this file's own source with the checker's body removed, so
    the mode literals used by the check itself cannot mask a real write mode.
    """
    import inspect
    import sys

    path = inspect.getsourcefile(sys.modules[__name__]) or ""
    if not path or not os.path.isfile(path):
        return False
    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("def _readonly_static_ok")), None)
    if start is None:
        return False
    end = start + 1
    while end < len(lines) and not (lines[end].startswith("def ") or lines[end].startswith("class ")):
        end += 1
    body = "\n".join(lines[:start] + lines[end:])
    write_modes = [quote + mode + quote for quote in ('"', "'") for mode in ("w", "a", "x")]
    return not any(mode in body for mode in write_modes) and '"wb"' not in body


def _sha256_file(path: str) -> str:
    """sha256 of a file, or "" when it disappeared mid-sweep.

    Other agents keep writing (and pruning) their own work trees while the sweep
    runs, so a vanished file is an *external* event, not a scanner side effect.
    """
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _deep_match(rel: str, globs: Iterable[str]) -> bool:
    for pattern in globs:
        if fnmatch.fnmatch(rel, pattern):
            return True
        if "**" in pattern:
            import re

            regex = re.escape(pattern).replace(r"\*\*", "\x00").replace(r"\*", "[^/]*").replace("\x00", ".*")
            if re.match("^" + regex + "$", rel):
                return True
    return False


def discover_missions(roots: Iterable[str], *, mission_glob: str = "m*", limit: int = 200) -> List[str]:
    missions: List[Tuple[float, str]] = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for entry in os.scandir(root):
            if not entry.is_dir() or not fnmatch.fnmatch(entry.name, mission_glob):
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            missions.append((mtime, entry.path))
    missions.sort(key=lambda item: (-item[0], item[1]))
    return [path for _, path in missions[:limit]]


def collect_files(mission_dir: str, cfg: Dict[str, Any]) -> List[str]:
    globs = cfg.get("file_globs", ["report*.json"])
    excludes = [str(g) for g in cfg.get("exclude_globs", [])]
    max_files = int(cfg.get("max_files_per_mission", 40))
    found: List[Tuple[float, str]] = []
    for pattern in globs:
        for path in glob.glob(os.path.join(mission_dir, pattern), recursive=True):
            if not os.path.isfile(path):
                continue
            if excludes and _deep_match(os.path.relpath(path, mission_dir).replace("\\", "/"), excludes):
                # a scan must not read its own output: the exposure list itself
                # would otherwise be rescanned while being rewritten
                continue
            try:
                found.append((os.path.getmtime(path), path))
            except OSError:
                continue
    found.sort(key=lambda item: (-item[0], item[1]))
    seen = set()
    out: List[str] = []
    for _, path in found:
        if path in seen:
            continue
        seen.add(path)
        out.append(path)
        if len(out) >= max_files:
            break
    return out


def _read_text(path: str, max_bytes: int) -> Optional[str]:
    try:
        if os.path.getsize(path) > max_bytes:
            return None
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    if b"\x00" in raw[:8192]:
        return None
    return raw.decode("utf-8", "replace")


def _exposure_rows(hits, mission: str, rel_path: str, limit: int) -> List[Dict[str, Any]]:
    rows = []
    seen = set()
    for hit in hits:
        if hit.fingerprint in seen:
            continue
        seen.add(hit.fingerprint)
        rows.append({
            "mission": mission,
            "file": rel_path,
            "line": hit.line,
            "type": hit.type,
            "detector": hit.detector,
            "via": hit.via,
            "length": hit.length,
            "preview": hit.preview,
            "value_sha256": hit.fingerprint,
            "confidence": hit.confidence,
        })
        if len(rows) >= limit:
            break
    return rows


def scan_history(
    policy_path: Optional[str] = None,
    *,
    roots: Optional[List[str]] = None,
    limit_missions: Optional[int] = None,
    max_rows: int = 400,
) -> Dict[str, Any]:
    policy = load_policy(policy_path)
    full = Scanner(policy)
    baseline = Scanner(policy.with_profile("baseline"))
    cfg = policy.history
    roots = roots or [str(r) for r in cfg.get("roots", [])]
    limit_missions = int(limit_missions or cfg.get("limit_missions", 200))
    max_files = int(cfg.get("max_total_files", 4000))
    max_bytes = int(cfg.get("max_file_bytes", 1 << 20))
    max_total_bytes = int(cfg.get("max_total_bytes", 64 << 20))

    missions = discover_missions(roots, mission_glob=str(cfg.get("mission_glob", "m*")), limit=limit_missions)
    files_scanned = 0
    bytes_scanned = 0
    scanned_missions: List[str] = []
    pre_hashes: Dict[str, str] = {}
    rows: List[Dict[str, Any]] = []
    baseline_hits = 0
    full_hits = 0
    by_type: Dict[str, int] = {}
    by_detector: Dict[str, int] = {}
    by_confidence: Dict[str, int] = {"high": 0, "low": 0}
    exposures: List[Dict[str, Any]] = []
    residual_hits = 0
    remediation_checked = 0

    for mission_dir in missions:
        mission = os.path.basename(mission_dir)
        mission_files = 0
        mission_hit = False
        for path in collect_files(mission_dir, cfg):
            if files_scanned >= max_files or bytes_scanned >= max_total_bytes:
                break
            text = _read_text(path, max_bytes)
            if text is None:
                continue
            rel_path = os.path.relpath(path, mission_dir).replace("\\", "/")
            pre_hashes[path] = _sha256_file(path)
            files_scanned += 1
            bytes_scanned += len(text.encode("utf-8", "surrogateescape"))
            mission_files += 1
            result = full.scan_text(text, path=rel_path, apply_location_whitelist=True)
            base_result = baseline.scan_text(text, path=rel_path, decode=False,
                                             apply_location_whitelist=False)
            baseline_hits += len(base_result.hits)
            full_hits += len(result.hits)
            if not result.clean:
                mission_hit = True
                for key, value in result.by_type().items():
                    by_type[key] = by_type.get(key, 0) + value
                for hit in result.hits:
                    by_detector[hit.detector] = by_detector.get(hit.detector, 0) + 1
                    by_confidence[hit.confidence] = by_confidence.get(hit.confidence, 0) + 1
                rows.extend(_exposure_rows(result.hits, mission, rel_path, limit=20))
                # repair loop on the real corpus: redact in memory, rescan
                redacted, _ = full.redact(text, path=rel_path)
                residual_hits += len(full.scan_text(redacted, path=rel_path).hits)
                remediation_checked += 1
                exposures.append({"mission": mission, "file": rel_path,
                                  "hits": len(result.hits), "redacted_bytes": len(redacted)})
        if mission_files:
            scanned_missions.append(mission)
        _ = mission_hit

    # read-only proof
    post_hashes = {path: _sha256_file(path) for path in pre_hashes}
    changed = sorted(path for path in pre_hashes if pre_hashes[path] != post_hashes[path])
    removed = [path for path in changed if post_hashes[path] == ""]
    # Concurrent writers (other agents in their own work trees) can change a file
    # while we read it; that is reported, not silently treated as our own write.
    readonly_ok = not changed

    return {
        "schema": SCHEMA,
        "policy_sha256": policy.sha256,
        "policy_profiles": ["full", "baseline"],
        "roots": roots,
        "missions_available": len(discover_missions(roots, mission_glob=str(cfg.get("mission_glob", "m*")),
                                                    limit=10 ** 6)),
        "missions_scanned": len(scanned_missions),
        "files_scanned": files_scanned,
        "bytes_scanned": bytes_scanned,
        "files_with_hits": len(exposures),
        "full_profile_hits": full_hits,
        "baseline_profile_hits": baseline_hits,
        "incremental_hits_vs_baseline": full_hits - baseline_hits,
        "by_type": by_type,
        "by_detector": by_detector,
        "by_confidence": by_confidence,
        "high_confidence_hits": by_confidence.get("high", 0),
        "low_confidence_candidates": by_confidence.get("low", 0),
        "externally_modified_files": changed[:20],
        "externally_modified_count": len(changed),
        "externally_removed_count": len(removed),
        "readonly_static_ok": _readonly_static_ok(),
        "remediation_files_checked": remediation_checked,
        "remediation_residual_hits": residual_hits,
        "readonly_verified": readonly_ok,
        "exposure_rows": rows[:max_rows],
        "exposure_rows_truncated": len(rows) > max_rows,
        "exposures": exposures[:max_rows],
    }