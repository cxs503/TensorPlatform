"""Deterministic artifact writing + sha256 manifest (idempotency proof).

Acceptance criterion #4: two runs over the same inputs must produce byte-identical
artifacts. Two design rules make that hold:

1. payloads contain no wall-clock value and no random id — everything is derived
   from the inputs (see ``model``),
2. serialisation is canonical: UTF-8, sorted keys, no trailing whitespace
   variation, ``\\n`` line endings, one trailing newline.

``write_artifact_set`` returns the manifest, including a ``combined_sha256`` over
the ordered (name, sha256) list. Comparing that single value between two runs is
the machine check used by the CLI (``verify-idempotent``) and by the test suite.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

from .model import canonical_json


def _bytes_of(payload: Any) -> bytes:
    return (json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_artifact_set(out_dir: Path | str, payloads: Dict[str, Any]) -> Dict[str, Any]:
    """Write ``payloads`` under ``out_dir`` and return the sha256 manifest."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for name in sorted(payloads):
        data = _bytes_of(payloads[name])
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files.append({"name": name, "sha256": sha256_bytes(data), "bytes": len(data)})
    combined = hashlib.sha256(
        canonical_json([{"name": f["name"], "sha256": f["sha256"]} for f in files]).encode(
            "utf-8"
        )
    ).hexdigest()
    manifest = {
        "schema_version": "evidence-governance-manifest/1.0",
        "files": files,
        "combined_sha256": combined,
        "n_files": len(files),
    }
    (out / "manifest.json").write_bytes(_bytes_of(manifest))
    return manifest


def combined_digest(payloads: Dict[str, Any]) -> str:
    """In-memory equivalent of ``write_artifact_set(...)['combined_sha256']``."""
    files = [
        {"name": name, "sha256": sha256_bytes(_bytes_of(payloads[name]))}
        for name in sorted(payloads)
    ]
    return hashlib.sha256(canonical_json(files).encode("utf-8")).hexdigest()