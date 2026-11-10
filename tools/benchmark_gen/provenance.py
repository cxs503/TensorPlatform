"""Dataset provenance: canonical hashing, archive manifests, independent verify.

The provenance contract (acceptance criterion A2)
-------------------------------------------------
Three hashes with distinct, non-overlapping meanings:

``provenance_key`` (== ``dataset_id``)
    ``sha256`` of the canonical request: ``schema_version`` + ``generator_version``
    + ``code_fingerprint`` + ``pipeline`` + ``seed`` + body spec + condition spec.
    Requirement "each dataset sha256 maps one-to-one onto parameters + seed +
    version": any change of a parameter, the seed or the generator version
    changes this key, and re-running the same request reproduces it bit for bit.

``content_sha256``
    ``sha256`` of the canonical *payload* (everything except the provenance
    block), floats rounded to 12 significant digits (the repo-wide
    reproducibility protocol, see ``tools/repro_digest``).  Guards against silent
    drift of the numbers themselves.

``file_sha256``
    ``sha256`` of the JSON file as written.  Stored in ``MANIFEST.json`` and in
    ``SHA256SUMS`` (never inside the file, which would be self-referential).

Canonicalisation is pure data (sorted keys, fixed separators, 12 significant
digits) so that hashes are stable across Python versions and ISAs.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

__all__ = [
    "SCHEMA_VERSION",
    "SIG_DIGITS",
    "round_sig",
    "canonicalize",
    "canonical_json",
    "sha256_text",
    "sha256_file",
    "code_fingerprint",
    "provenance_key",
    "content_sha256",
    "attach_provenance",
    "verify_record",
    "write_dataset",
    "write_archive",
    "verify_archive",
]

SCHEMA_VERSION = "benchmark_dataset/1"
SIG_DIGITS = 12


def round_sig(value: float, sig: int = SIG_DIGITS) -> float:
    """Round ``value`` to ``sig`` significant digits (``0.0`` stays ``0.0``)."""
    value = float(value)
    if value == 0.0 or not math.isfinite(value):
        return value
    exponent = math.floor(math.log10(abs(value)))
    return float(round(value, sig - 1 - exponent))


def canonicalize(obj: Any, sig: int = SIG_DIGITS) -> Any:
    """Recursively coerce ``obj`` into a hash-stable structure."""
    if isinstance(obj, bool) or obj is None or isinstance(obj, (str, int)):
        return obj
    if isinstance(obj, float):
        return round_sig(obj, sig)
    if isinstance(obj, Mapping):
        return {str(k): canonicalize(v, sig) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [canonicalize(v, sig) for v in obj]
    raise TypeError(f"cannot canonicalise object of type {type(obj).__name__}")


def canonical_json(obj: Any, sig: int = SIG_DIGITS) -> str:
    """Deterministic JSON text for hashing (sorted keys, no whitespace)."""
    return json.dumps(
        canonicalize(obj, sig),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def code_fingerprint(paths: Sequence[str | Path]) -> str:
    """sha256 over ``(basename, file sha256)`` pairs, in the given order."""
    parts: List[str] = []
    for path in paths:
        p = Path(path)
        parts.append(f"{p.name}:{sha256_file(p)}")
    return sha256_text("\n".join(parts))


def provenance_key(*, generator_version: str, code_fingerprint: str, pipeline: str,
                   seed: int, body: Mapping[str, Any],
                   condition: Mapping[str, Any]) -> str:
    """Identity of a generation request (A2 one-to-one mapping)."""
    request = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": str(generator_version),
        "code_fingerprint": str(code_fingerprint),
        "pipeline": str(pipeline),
        "seed": int(seed),
        "body": body,
        "condition": condition,
    }
    return sha256_text(canonical_json(request))


def content_sha256(content: Mapping[str, Any]) -> str:
    return sha256_text(canonical_json(content))


def attach_provenance(content: Mapping[str, Any], *, generator_version: str,
                      code_fingerprint: str, pipeline: str, seed: int,
                      body: Mapping[str, Any], condition: Mapping[str, Any],
                      extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Return the full dataset document (payload + provenance block)."""
    key = provenance_key(generator_version=generator_version,
                         code_fingerprint=code_fingerprint, pipeline=pipeline,
                         seed=seed, body=body, condition=condition)
    provenance: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": str(generator_version),
        "code_fingerprint": str(code_fingerprint),
        "pipeline": str(pipeline),
        "seed": int(seed),
        "dataset_id": key,
        "provenance_key": key,
        "content_sha256": content_sha256(content),
        "request": {
            "body": canonicalize(body),
            "condition": canonicalize(condition),
        },
    }
    if extra:
        provenance.update(canonicalize(extra))
    return {"schema": SCHEMA_VERSION, "dataset_id": key,
            "content": canonicalize(content), "provenance": provenance}


def verify_record(doc: Mapping[str, Any], *, current_code_fingerprint: Optional[str] = None
                  ) -> Dict[str, Any]:
    """Recompute both hashes of an in-memory dataset document.

    Returns a report; ``ok`` is True only if the payload hash *and* the
    provenance key recompute exactly from the stored request.
    """
    prov = doc.get("provenance") or {}
    request = prov.get("request") or {}
    recomputed_key = provenance_key(
        generator_version=prov.get("generator_version", ""),
        code_fingerprint=prov.get("code_fingerprint", ""),
        pipeline=prov.get("pipeline", ""),
        seed=prov.get("seed", 0),
        body=request.get("body", {}),
        condition=request.get("condition", {}),
    )
    recomputed_content = content_sha256(doc.get("content") or {})
    stored_key = prov.get("provenance_key") or prov.get("dataset_id")
    checks = {
        "provenance_key_matches_request": recomputed_key == stored_key,
        "content_sha256_matches_payload": recomputed_content == prov.get("content_sha256"),
        "dataset_id_matches_provenance_key": doc.get("dataset_id") == stored_key,
        "schema_matches": doc.get("schema") == prov.get("schema_version") == SCHEMA_VERSION,
    }
    report: Dict[str, Any] = {
        "dataset_id": stored_key,
        "checks": checks,
        "ok": all(checks.values()),
        "recomputed": {"provenance_key": recomputed_key,
                       "content_sha256": recomputed_content},
    }
    if current_code_fingerprint is not None:
        report["code_fingerprint_matches_current"] = (
            prov.get("code_fingerprint") == current_code_fingerprint)
    return report


def write_dataset(doc: Mapping[str, Any], out_dir: str | Path) -> Dict[str, Any]:
    """Write ``<dataset_id>.json`` plus its ``.sha256`` sidecar."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    dataset_id = doc["dataset_id"]
    path = out / f"{dataset_id}.json"
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    digest = sha256_file(path)
    sidecar = out / f"{dataset_id}.json.sha256"
    sidecar.write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return {"dataset_id": dataset_id, "path": str(path),
            "sidecar": str(sidecar), "file_sha256": digest}


def write_archive(records: Iterable[Mapping[str, Any]], out_dir: str | Path,
                  *, generator_version: str, code_fingerprint: str,
                  metadata: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Write all datasets plus ``MANIFEST.json`` and ``SHA256SUMS``."""
    out = Path(out_dir)
    written = [write_dataset(doc, out) for doc in records]
    manifest = {
        "schema": SCHEMA_VERSION,
        "generator_version": str(generator_version),
        "code_fingerprint": str(code_fingerprint),
        "n_datasets": len(written),
        "datasets": sorted(written, key=lambda rec: rec["dataset_id"]),
        "metadata": canonicalize(dict(metadata or {})),
    }
    (out / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8")
    sums = "\n".join(f"{rec['file_sha256']}  {Path(rec['path']).name}"
                     for rec in manifest["datasets"]) + "\n"
    (out / "SHA256SUMS").write_text(sums, encoding="utf-8")
    return manifest


def verify_archive(out_dir: str | Path, *, current_code_fingerprint: Optional[str] = None
                   ) -> Dict[str, Any]:
    """Full archive audit: file digests, payload digests, key uniqueness."""
    out = Path(out_dir)
    manifest = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
    problems: List[str] = []
    keys: List[str] = []
    per_dataset: List[Dict[str, Any]] = []
    for rec in manifest["datasets"]:
        path = out / Path(rec["path"]).name
        if not path.exists():
            problems.append(f"missing file: {path.name}")
            continue
        actual = sha256_file(path)
        if actual != rec["file_sha256"]:
            problems.append(f"file digest mismatch: {path.name}")
        sidecar = out / f"{path.name}.sha256"
        if not sidecar.exists():
            problems.append(f"missing sidecar: {sidecar.name}")
        else:
            if actual not in sidecar.read_text(encoding="utf-8"):
                problems.append(f"sidecar digest mismatch: {sidecar.name}")
        doc = json.loads(path.read_text(encoding="utf-8"))
        report = verify_record(doc, current_code_fingerprint=current_code_fingerprint)
        per_dataset.append({"dataset_id": doc.get("dataset_id"), **report})
        if not report["ok"]:
            problems.append(f"provenance mismatch: {path.name}")
        keys.append(str(doc.get("dataset_id")))
    if len(set(keys)) != len(keys):
        problems.append("dataset_id collision: the request -> id mapping is not one-to-one")
    sums_path = out / "SHA256SUMS"
    if not sums_path.exists():
        problems.append("missing SHA256SUMS")
    else:
        listed = {}
        for line in sums_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                digest, name = line.split()
                listed[name] = digest
        for rec in manifest["datasets"]:
            name = Path(rec["path"]).name
            if listed.get(name) != rec["file_sha256"]:
                problems.append(f"SHA256SUMS entry mismatch: {name}")
    return {
        "n_datasets": len(per_dataset),
        "unique_ids": len(set(keys)) == len(keys),
        "problems": problems,
        "ok": not problems,
        "datasets": per_dataset,
    }