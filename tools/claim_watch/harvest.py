#!/usr/bin/env python3
"""Claim harvesting / building: turn existing artifacts into claim records.

Two products, both read-only with respect to the audited worktrees:

``build``    one claim per JSON document that records the hash of some other
             file. The claim's own artifact is the document (hash taken now), its
             ``depends_on`` is exactly the set of ``(path, sha256)`` pairs the
             document already records. This is where the *dependency set* comes
             from without any domain knowledge: repositories have been writing
             these pairs all along (manifest, lock, acceptance and provenance
             documents); nobody ever checked them for freshness.

``scan``     the raw ``(path, recorded sha256)`` pairs, used as a cross-check and
             as the substrate for the historical drift replay (retro.py).

Why not "just hash every file"? A hash of a file we hash ourselves says nothing
about whether anyone ever *claimed* anything about it. A claim needs an assertor,
a value and a time; the assertor here is the document that recorded the pair.

Documentation of the proxy choices (all listed in the report limitations):
* ``verified_at`` for a built claim is *now* (the binding is established now),
  for a scanned pair it is the document's own date field when it has one,
  otherwise the document's mtime;
* a document that declares no evidence grade yields a claim graded WITHHELD —
  harvesting must not invent authority the producer never claimed;
* pairs whose path resolves in no configured root are kept verbatim. They become
  ``dep_missing`` (STALE) rather than being dropped: a dependency the auditor
  cannot see is not a dependency that holds.

Zero domain vocabulary: key names below are structural JSON conventions
(``sha256`` / ``path`` / ``generated_on``), never substance or quantity names.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from . import claims as claim_mod
from . import fingerprint as fp_mod

HEX64 = re.compile(r"^[0-9a-f]{64}$")
_PREFIX_SHA = re.compile(r"^([A-Za-z0-9_.-]{2,40}?)_(?:sha256|sha_256|sha|hash|digest|checksum)$",
                         re.IGNORECASE)

PATH_KEYS = {
    "path", "file", "filepath", "filename", "artifact", "artifact_path", "artifact_uri",
    "uri", "source", "source_path", "src", "target", "generator", "script", "module",
    "registry", "ledger", "ref", "input", "inputs", "snapshot", "manifest", "schema",
    "name", "rel_path", "relative_path", "output", "output_path", "resource", "dataset",
    "data", "bundle", "asset", "entry", "file_name", "document", "report",
}
SHA_KEYS = {
    "sha256", "sha", "sha_256", "hash", "digest", "content_hash", "content_sha256",
    "blob_sha", "checksum", "sha256sum",
}
NUMERIC_KEYS = (
    "value", "metric", "score", "rate", "ratio", "result", "threshold", "tol",
    "tolerance", "count", "n", "total", "mean", "median", "pct", "percent",
)
DATE_KEYS = (
    "verified_at", "generated_on", "generated_at", "created_at", "date", "timestamp",
    "retrieved_at", "as_of", "review_date", "harvest_date", "recorded_at", "updated_at",
)
GRADE_KEYS = ("evidence_grade", "grade", "evidence")

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
             ".pytest_cache", "site-packages", ".tox", "dist", "build"}


# --------------------------------------------------------------------------- #
# document iteration
# --------------------------------------------------------------------------- #
def iter_json_docs(
    root: str,
    max_docs: Optional[int] = None,
    max_bytes: int = 2_000_000,
    subdirs: Optional[Sequence[str]] = None,
    exclude: Optional[Sequence[str]] = None,
) -> Iterator[Tuple[str, str]]:
    """Yield ``(absolute path, repo-relative path)`` for JSON documents.

    Deterministic order (sorted walk) so two runs produce identical ledgers.
    """
    n = 0
    root = os.path.abspath(root)
    blocked = tuple(os.path.abspath(e) + os.sep for e in (exclude or ()))
    bases = [os.path.join(root, s) for s in subdirs] if subdirs else [root]
    for base in bases:
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
            for name in sorted(filenames):
                if not name.lower().endswith((".json", ".jsonl")):
                    continue
                path = os.path.join(dirpath, name)
                if blocked and (os.path.abspath(path) + os.sep).startswith(blocked):
                    continue
                try:
                    if os.path.getsize(path) > max_bytes:
                        continue
                except OSError:
                    continue
                yield path, os.path.relpath(path, root)
                n += 1
                if max_docs is not None and n >= max_docs:
                    return


def _looks_like_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > 400:
        return False
    if HEX64.match(value):
        return False
    if value.startswith(("http://", "https://")):
        return False
    if "\n" in value:
        return False
    return "/" in value or value.endswith((".json", ".py", ".md", ".csv", ".txt", ".yaml", ".yml",
                                            ".npz", ".npy", ".jsonl", ".sql", ".png", ".pdf",
                                            ".html", ".vtk", ".pvd", ".docx", ".png"))


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and bool(HEX64.match(value))


def extract_pairs(doc: Any) -> List[Dict[str, str]]:
    """All ``(path, sha256)`` pairs recorded *inside the same object* of a document.

    Adjacency inside one object is what makes the pairing a statement rather than
    a coincidence of a document mentioning many paths and many hashes. Three
    recorded shapes are recognised, because repositories write all three:

    ``{"path": "a/b.json", "sha256": "…"}``       explicit key/value pair
    ``{"a/b.json": "…", "c/d.png": "…"}``         path-keyed hash map (manifest)
    ``{"study": "x.json", "study_sha256": "…"}``  prefixed sibling pair
    """
    out: List[Dict[str, str]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if not any(isinstance(v, str) and len(v) == 64 and HEX64.match(v) for v in node.values()):
                # No hash in this object: it cannot assert a pair, so skip the
                # (comparatively expensive) path-shape tests and only descend.
                for value in node.values():
                    walk(value)
                return
            # Shape 1: explicit path key + hash key in one object.
            paths = [(k, v) for k, v in node.items()
                     if isinstance(k, str) and k.lower() in PATH_KEYS and _looks_like_path(v)]
            shas = [(k, v) for k, v in node.items()
                    if isinstance(k, str) and k.lower() in SHA_KEYS and _is_sha(v)]
            for _, pv in paths:
                for _, sv in shas:
                    out.append({"path": pv, "sha256": sv})
            # Shape 2: path-keyed hash map (manifest style).
            for k, v in node.items():
                if isinstance(k, str) and _looks_like_path(k) and _is_sha(v):
                    out.append({"path": k, "sha256": v})
            # Shape 3: prefixed sibling pair (study / study_sha256).
            # Shape 4: two-element [path, sha256] lists.
            for value in node.values():
                if isinstance(value, list) and len(value) == 2:
                    a, b = value
                    if isinstance(a, str) and _is_sha(b) and _looks_like_path(a):
                        out.append({"path": a, "sha256": b})
                    elif isinstance(b, str) and _is_sha(a) and _looks_like_path(b):
                        out.append({"path": b, "sha256": a})
            for key, value in node.items():
                if not (isinstance(key, str) and _is_sha(value)):
                    continue
                m = _PREFIX_SHA.match(key)
                if not m:
                    continue
                prefix = m.group(1)
                for cand in (prefix, f"{prefix}_path", f"{prefix}_file", f"{prefix}_uri"):
                    if isinstance(node.get(cand), str) and _looks_like_path(node[cand]):
                        out.append({"path": node[cand], "sha256": value})
                        break
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            # Shape 4: two-element [path, sha256] pairs inside a list.
            if len(node) == 2:
                a, b = node
                if isinstance(a, str) and _is_sha(b) and _looks_like_path(a):
                    out.append({"path": a, "sha256": b})
                elif isinstance(b, str) and _is_sha(a) and _looks_like_path(b):
                    out.append({"path": b, "sha256": a})
            for value in node:
                walk(value)

    walk(doc)
    seen = set()
    unique: List[Dict[str, str]] = []
    for pair in out:
        key = (pair["path"], pair["sha256"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(pair)
    return unique


def doc_date(doc: Any, fallback_path: str) -> str:
    """Best-effort 'when was this recorded', as an ISO-8601 UTC string."""
    def from_doc(node: Any) -> Optional[str]:
        if isinstance(node, dict):
            for key in DATE_KEYS:
                value = node.get(key)
                if isinstance(value, str) and len(value) >= 8 and value[:4].isdigit():
                    text = value.replace("Z", "+00:00")
                    try:
                        dt = _dt.datetime.fromisoformat(text)
                    except ValueError:
                        continue
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=_dt.timezone.utc)
                    return dt.astimezone(_dt.timezone.utc).isoformat()
        return None

    found = from_doc(doc)
    if found:
        return found
    try:
        ts = os.path.getmtime(fallback_path)
    except OSError:
        ts = 0.0
    return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).isoformat()


def declared_grade(doc: Any) -> str:
    """Grade the producer wrote in the document, else WITHHELD (never invented)."""
    if isinstance(doc, dict):
        for key in GRADE_KEYS:
            value = doc.get(key)
            if isinstance(value, str) and value.upper() in ("REAL", "MOCK", "STUB", "WITHHELD"):
                return value.upper()
    return "WITHHELD"


def doc_numeric(doc: Any) -> Tuple[Any, Optional[str], Optional[str]]:
    """A document's own scalar assertion: (value, unit, pointer).

    Falls back to the document's digest token when the document asserts no
    number (an artifact-state claim). Never derives a number from anything.
    """
    def scan(node: Any, prefix: str = "", depth: int = 0):
        if depth > 2:
            return None
        if isinstance(node, dict):
            for key in NUMERIC_KEYS:
                if key in node:
                    v = node[key]
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        unit = node.get("unit") or node.get("units")
                        unit = unit if isinstance(unit, str) and len(unit) <= 32 else None
                        return (v, unit, f"{prefix}/{key}")
            for key, value in node.items():
                if isinstance(value, dict):
                    got = scan(value, f"{prefix}/{key}", depth + 1)
                    if got:
                        return got
        return None

    got = scan(doc)
    if got:
        return got
    return None, None, None


# --------------------------------------------------------------------------- #
# claim construction
# --------------------------------------------------------------------------- #
def _dep_uri(raw_path: str, repo: Optional[str], roots: Mapping[str, str]) -> str:
    """Qualify a recorded path so the resolver never has to guess."""
    if not raw_path:
        return raw_path
    if raw_path.startswith("file://"):
        return raw_path
    head, _, tail = raw_path.partition(":")
    if tail and len(head) > 1 and "/" not in head and head in roots:
        return raw_path  # already '<repo>:<path>' and the repo is known
    if os.path.isabs(raw_path):
        return "file://" + raw_path
    if repo and repo in roots:
        if os.path.exists(os.path.normpath(os.path.join(roots[repo], raw_path))):
            return f"{repo}:{raw_path}"
    for name, root in roots.items():
        if os.path.exists(os.path.normpath(os.path.join(root, raw_path))):
            return f"{name}:{raw_path}" if name != repo else raw_path
    return f"{repo}:{raw_path}" if repo and repo in roots else raw_path


def build_claims(
    root: str,
    repo: str,
    roots: Mapping[str, str],
    *,
    max_docs: Optional[int] = None,
    subdirs: Optional[Sequence[str]] = None,
    current_env: Optional[Mapping[str, Any]] = None,
    verified_at: Optional[str] = None,
    with_env: bool = True,
    granularity: str = "pair",
    exclude: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Claims recording what each document already asserts about other files.

    ``granularity="pair"``   (default) one claim per recorded ``(path, sha256)``
                             pair: the document is the witness, the pair is the
                             dependency. A document that pins 200 files yields 200
                             independent freshness obligations, which is the point.
    ``granularity="document"`` one claim per document with every pair in
                             ``depends_on`` (the shape used for the historical
                             replay, where a document's own number is at stake).
    """
    env = dict(current_env) if (with_env and current_env) else None
    stamp = verified_at or _dt.datetime.now(tz=_dt.timezone.utc).replace(microsecond=0).isoformat()
    claims: List[Dict[str, Any]] = []
    for path, rel in iter_json_docs(root, max_docs=max_docs, subdirs=subdirs, exclude=exclude):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            continue
        pairs = extract_pairs(doc)
        if not pairs:
            continue
        doc_sha = claim_mod.sha256_file(path)
        if not doc_sha:
            continue
        grade = declared_grade(doc)
        value, unit, pointer = doc_numeric(doc)
        if value is None:
            value, unit, pointer = f"sha256:{doc_sha[:16]}", "digest", None

        if granularity == "document":
            deps: List[Dict[str, Any]] = []
            seen = set()
            for pair in pairs:
                uri = _dep_uri(pair["path"], repo, roots)
                key = (uri, pair["sha256"])
                if key in seen:
                    continue
                seen.add(key)
                deps.append({"uri": uri, "sha256": pair["sha256"], "kind": "artifact",
                             "role": "recorded-by-document"})
                if len(deps) >= 128:
                    break
            claims.append(claim_mod.make_claim(
                claim_id=f"{repo}:{rel}",
                value=value,
                unit=unit,
                artifact_uri=f"{repo}:{rel}",
                artifact_sha256=doc_sha,
                verified_at=stamp,
                evidence_grade=grade,
                env_fingerprint=env,
                depends_on=deps,
                pointer=pointer,
                producer=rel,
                notes=f"{len(deps)} recorded dependency pair(s)",
            ))
            continue

        for index, pair in enumerate(pairs):
            uri = _dep_uri(pair["path"], repo, roots)
            claims.append(claim_mod.make_claim(
                claim_id=f"{repo}:{rel}#{index:03d}",
                value=f"sha256:{pair['sha256'][:16]}",
                unit="digest",
                artifact_uri=f"{repo}:{rel}",
                artifact_sha256=doc_sha,
                verified_at=stamp,
                evidence_grade=grade,
                env_fingerprint=env,
                depends_on=[{"uri": uri, "sha256": pair["sha256"], "kind": "artifact",
                             "role": "recorded-by-document"}],
                pointer=None,
                producer=rel,
                notes=f"pair {index} of {len(pairs)} in {rel}",
            ))
    return claims


def scan_pairs(root: str, repo: str, roots: Mapping[str, str],
               max_docs: Optional[int] = None,
               exclude: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    """Raw recorded pairs with their document and date (used by retro.py)."""
    rows: List[Dict[str, Any]] = []
    for path, rel in iter_json_docs(root, max_docs=max_docs, exclude=exclude):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            continue
        pairs = extract_pairs(doc)
        if not pairs:
            continue
        stamp = doc_date(doc, path)
        doc_sha = claim_mod.sha256_file(path)
        for pair in pairs:
            rows.append({
                "repo": repo,
                "document": rel,
                "document_path": path,
                "document_sha256": doc_sha,
                "path": pair["path"],
                "uri": _dep_uri(pair["path"], repo, roots),
                "sha256": pair["sha256"],
                "verified_at": stamp,
            })
    return rows