"""``salvage.json`` — the machine-readable hand-over of a circuit-broken task.

One task in, one deterministic JSON out:

```
{ "schema_version", "task", "terminal",
  "inheritable": [ {path, class, bytes, sha256, gates, verdict} ],   # sorted by path
  "blocked_files": [ {path, class, bytes, sha256, failed_gates, why} ],
  "residual": [ {item, item_sha256, source, state, evidence} ],
  "state": "INHERITABLE" | "BLOCKED",
  "blocked": {"code", "detail"} | null,
  "continuation": {...},
  "fingerprint": {"algorithm", "subject_sha256"} }
```

Design rules (each one is a gate in the acceptance report):

* **No clock, no host, no path outside the worktree** enters the payload, so two
  runs over the same inputs produce the same bytes and the same fingerprint
  (idempotency gate).
* A file is *inheritable* only if it passes all three sift gates.  Traces are
  never promoted to achievement: ``salvage.json`` carries no node verdict and no
  acceptance claim — it only says "this material exists and is usable, these
  obligations are still open".
* ``state`` is ``INHERITABLE`` when at least one file survives the sift, else
  ``BLOCKED`` with a machine code naming the dominant failing gate
  (``no_worktree`` / ``no_candidates`` / ``parse_failed`` / ``smoke_failed`` /
  ``structural_failed``).
* Residual items are matched against inheritable material lexically only (token
  overlap between the quoted obligation and the file path + first KiB of text).
  A matched item is ``EVIDENCED`` (candidate material exists, not proof of
  completion); everything else stays ``OPEN``.
"""
from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import continuation as _continuation
from . import sift, taxonomy

SCHEMA_VERSION = "1.0.0"
SALVAGE_NAME = "salvage.json"

#: top-level entries every platform checkout is expected to be able to address.
FALLBACK_ROOTS = ("tools", "tests", "docs", "artifacts", "runs", "backend", "config", "scripts", "src", "data")

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")
ITEM_MARKERS = ("必须", "要求", "判据", "验收", "≥", "≤", "pytest", "artifacts/", "docs/", "tests/")
#: standing boilerplate that repeats on every mission and is not an obligation
BOILERPLATE_MARKERS = ("纪律", "honest FAIL", "worktree 内提交", "禁止重复", "上层智能体播种")


def canonical_json(payload: object) -> str:
    """Canonical serialisation: sorted keys, fixed indent, trailing newline."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def allowed_roots(*checkouts: Path | None) -> tuple[str, ...]:
    """Top-level layout of the destination checkout (dynamic, never hardcoded).

    The destination of a salvage is the *mission's own* repository checkout, so
    callers pass that worktree; extra checkouts can be layered on top.
    """
    roots: set[str] = set(FALLBACK_ROOTS)
    for checkout in checkouts:
        if not checkout:
            continue
        try:
            for entry in Path(checkout).iterdir():
                if entry.name.startswith("."):
                    continue
                roots.add(entry.name)
        except OSError:
            continue
    return tuple(sorted(roots))


#: largest single file carried into a package (bigger artefacts are blocked).
MAX_FILE_BYTES = 8 * 1024 * 1024


def _file_class(rel: str) -> str:
    try:
        from tools.salvage import classify as legacy

        return legacy.classify(rel)[0]
    except Exception:  # pragma: no cover - sibling package always present in-tree
        return "other"


def _is_residue(rel: str) -> bool:
    """Vendored / cache / editor residue: never a deliverable, never inherited."""
    try:
        from tools.salvage import classify as legacy

        return legacy.is_drop_class(legacy.classify(rel)[0])
    except Exception:  # pragma: no cover
        return False


def _candidate_rels(record: dict, worktree: Path) -> list[tuple[str, str]]:
    """``(rel_path, source)`` candidates: recorded evidence first, git status second."""
    seen: dict[str, str] = {}
    evidence = (record.get("result") or {}).get("evidence") or {}
    for raw in (evidence.get("summary") or {}).get("changed_files") or []:
        rel = sift.normalize_rel(raw, worktree)
        if rel and rel not in seen and not _is_residue(rel):
            seen[rel] = "evidence.changed_files"
    try:
        from tools.salvage import gitutil

        if gitutil.is_git_worktree(worktree):
            for entry in gitutil.status_entries(str(worktree)):
                rel = sift.normalize_rel(entry.path, worktree)
                if rel and rel not in seen and not _is_residue(rel):
                    seen[rel] = "git:" + str(getattr(entry, "code", "") or "")
    except Exception:  # pragma: no cover - git absent is not fatal (evidence still counted)
        pass
    return sorted(seen.items())


def _read_head(path: Path, limit: int = 4096) -> str:
    try:
        with open(path, "rb") as handle:
            return handle.read(limit).decode("utf-8", errors="replace").lower()
    except OSError:
        return ""


def _tokens(text: str) -> set[str]:
    return {tok.lower() for tok in _TOKEN_RE.findall(text or "")}


def _match_item(item_text: str, files: list[dict]) -> list[str]:
    """Lexical evidence candidates for one obligation (path + first KiB)."""
    want = _tokens(item_text)
    if not want:
        return []
    hits = []
    for row in files:
        hay = _tokens(row["path"]) | _tokens(row.get("_head", ""))
        if want & hay:
            hits.append(row["path"])
    return sorted(hits)[:20]


def acceptance_items(record: dict) -> list[dict]:
    """Obligations still open for the task, derived from recorded payload only.

    Sources, in order: explicit ``acceptance_criteria`` list on the record, the
    requirement sentences of the goal text, then the recorded headline.  The
    strings are quoted payload, never interpreted.
    """
    items: list[dict] = []
    for raw in record.get("acceptance_criteria") or []:
        text = str(raw).strip()
        if text:
            items.append({"item": text, "source": "acceptance_criteria"})
    goal = str(record.get("goal") or "")
    if goal:
        for chunk in re.split(r"(?<=[。；;\n])", goal):
            text = chunk.strip()
            if not text or len(text) > 400:
                continue
            if text.startswith("【") and text.rstrip("。").endswith("）"):
                continue  # title/parameter header, not an obligation
            if any(marker in text for marker in BOILERPLATE_MARKERS):
                continue
            if any(marker in text for marker in ITEM_MARKERS):
                items.append({"item": text, "source": "goal_requirement"})
    headline = record.get("headline") or (record.get("result") or {}).get("headline")
    if not items and headline:
        items.append({"item": str(headline).strip(), "source": "headline"})
    if not items:
        items.append({"item": "task terminated before any acceptance evidence was recorded", "source": "policy"})
    # de-duplicate on the quoted text, keep first source
    out, seen = [], set()
    for row in items:
        key = row["item"]
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out[:24]


def build(
    record: dict,
    worktree: Path,
    repo_root: Path | None = None,
    smoke: bool = True,
    max_files: int = 400,
    roots=None,
    smoke_budget: int = 6,
    workers: int = 8,
) -> dict:
    """Build the ``salvage.json`` payload for one terminal task record."""
    worktree = Path(worktree)
    taxonomy_row = taxonomy.classify(record)
    roots_tuple = tuple(roots) if roots else allowed_roots(worktree, repo_root)

    inheritable: list[dict] = []
    blocked_files: list[dict] = []
    gate_records: list[dict] = []
    candidates: list[tuple[str, str]] = []
    worktree_present = worktree.is_dir()

    if worktree_present:
        candidates = _candidate_rels(record, worktree)[:max_files]

    # Phase 1 (parallel, cheap): parse + structural.
    # Phase 2 (parallel): smoke — the file subset is chosen by sorted path, never
    # by thread arrival order, so the same inputs always smoke the same files.
    py_rels = [rel for rel, _ in candidates if sift.ext_of(rel) == sift._PY_EXT]
    smoked = set(sorted(py_rels)[: max(0, int(smoke_budget))]) if smoke else set()

    def _statics(item: tuple[str, str]) -> tuple[str, str, dict]:
        rel, source = item
        return rel, source, {
            "parse": sift.gate_parse(worktree / rel),
            "structural": sift.gate_structural(rel, roots_tuple),
        }

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        static_rows = list(pool.map(_statics, candidates))

    def _smoke(item: tuple[str, str, dict]) -> tuple[str, str, dict]:
        rel, source, gates = item
        gates["smoke"] = sift.smoke_gate(worktree / rel, rel, worktree, smoke and (rel not in py_rels or rel in smoked))
        return rel, source, sift.assemble(gates)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        sifted = list(pool.map(_smoke, static_rows))

    for rel, source, result in sifted:
        path = worktree / rel
        gate_records.append(result)
        too_big = path.is_file() and path.stat().st_size > MAX_FILE_BYTES
        if not path.is_file() or too_big:
            blocked_files.append(
                {
                    "path": rel,
                    "class": _file_class(rel),
                    "bytes": 0,
                    "sha256": None,
                    "source": source,
                    "failed_gates": ["parse"],
                    "gates": result["gates"],
                    "why": "oversized" if too_big else "absent_on_disk",
                }
            )
            continue
        blob = path.read_bytes()
        row = {
            "path": rel,
            "class": _file_class(rel),
            "bytes": len(blob),
            "sha256": sha256_bytes(blob),
            "source": source,
            "gates": result["gates"],
        }
        if result["verdict"] == sift.INHERITABLE:
            row["_head"] = _read_head(path)
            inheritable.append(row)
        else:
            row["failed_gates"] = result["failed_gates"]
            row["why"] = ",".join(result["failed_gates"])
            blocked_files.append(row)

    inheritable.sort(key=lambda r: r["path"])
    blocked_files.sort(key=lambda r: r["path"])

    residual: list[dict] = []
    for row in acceptance_items(record):
        candidates_ = _match_item(row["item"], inheritable)
        residual.append(
            {
                "item": row["item"],
                "item_sha256": sha256_text(row["item"]),
                "source": row["source"],
                "state": "EVIDENCED" if candidates_ else "OPEN",
                "evidence": candidates_,
            }
        )

    for row in inheritable:
        row.pop("_head", None)

    if not worktree_present:
        state, blocked = sift.BLOCKED, {"code": "no_worktree", "detail": "destination worktree absent on disk"}
    elif not candidates:
        state, blocked = sift.BLOCKED, {"code": "no_candidates", "detail": "no recorded or dirty file to inherit"}
    elif not inheritable:
        state, blocked = sift.BLOCKED, {
            "code": sift.dominant_reason(gate_records),
            "detail": f"0/{len(blocked_files)} candidate files survived the three-gate sift",
        }
    else:
        state, blocked = sift.INHERITABLE, None

    payload = {
        "schema_version": SCHEMA_VERSION,
        "task": {
            "mission_id": record.get("id"),
            "repo": record.get("repo"),
            "status": record.get("status"),
            "model": record.get("model") or (record.get("result") or {}).get("model"),
            "claimed_by": record.get("claimed_by"),
            "headline": record.get("headline") or (record.get("result") or {}).get("headline"),
            "parent_mission_id": record.get("parent_mission_id"),
        },
        "terminal": taxonomy_row,
        "policy": {
            "gates": list(sift.GATES),
            "smoke_enabled": bool(smoke),
            "smoke_budget_per_task": smoke_budget,
            "smoke_files": sorted(smoked),
            "allowed_roots": list(roots_tuple),
            "max_files": max_files,
        },
        "source": {
            "worktree_present": worktree_present,
            "candidate_count": len(candidates),
        },
        "counts": {
            "candidates": len(candidates),
            "inheritable": len(inheritable),
            "blocked_files": len(blocked_files),
            "residual": len(residual),
            "residual_open": sum(1 for r in residual if r["state"] == "OPEN"),
            "inheritable_bytes": sum(r["bytes"] for r in inheritable),
        },
        "inheritable": inheritable,
        "blocked_files": blocked_files,
        "residual": residual,
        "state": state,
        "blocked": blocked,
    }

    # the seed id must be derivable without the seed itself: fingerprint the body
    # first, hand that digest to the continuation builder, then fingerprint the whole.
    provisional = sha256_text(canonical_json(payload))
    continuation = _continuation.build(payload, provisional)
    payload["continuation"] = continuation
    subject = canonical_json(payload)
    payload["fingerprint"] = {
        "algorithm": "sha256:canonical-json(subject)",
        "subject_sha256": sha256_text(subject),
        "provisional_sha256": provisional,
        "subjects": {
            "inheritable": sha256_text(canonical_json(payload["inheritable"])),
            "residual": sha256_text(canonical_json(payload["residual"])),
            "continuation": sha256_text(canonical_json(continuation)),
        },
    }
    return payload


def fingerprint(payload: dict) -> str:
    return (payload.get("fingerprint") or {}).get("subject_sha256", "")


def write(payload: dict, out_dir: Path) -> Path:
    """Write ``salvage.json`` deterministically (canonical bytes)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / SALVAGE_NAME
    path.write_text(canonical_json(payload), encoding="utf-8")
    return path