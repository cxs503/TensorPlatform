"""Structural adjudication of the history exposure list.

The rescan deliberately reports raw entropy candidates. On real mission corpora
those are mostly *structural* strings (paths, file names, identifiers, digests),
and saying so with a number is more honest than either hiding them or pretending
they are credentials. This module re-reads each exposed line **read-only**,
extracts the token whose sha256 matches the finding and classifies its structure.

It never prints or stores the token: only the class label and the fingerprint are
written, so the adjudication artifact is safe to publish.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional

SCHEMA = "tp.secret_scan.adjudication/v1"

_EXT = re.compile(r"\.[A-Za-z0-9]{1,8}$")
_HEX = re.compile(r"^[0-9a-fA-F]{16,}$")
_TOKEN = re.compile(r"[A-Za-z0-9+/=_-]{16,}")


def _classify(token: str) -> str:
    if _HEX.match(token):
        return "hex_digest"
    if _EXT.search(token) or token.count("/") >= 1:
        return "path_or_filename"
    if "_" in token or "-" in token:
        return "identifier_with_separators"
    letters = sum(1 for ch in token if ch.isalpha())
    if letters / len(token) >= 0.6:
        return "letter_run_identifier"
    return "unclassified"


def adjudicate(exposure_path: str = "artifacts/secret_scan/history_exposure.json",
               roots: Optional[List[str]] = None,
               out: str = "artifacts/secret_scan/exposure_adjudication.json",
               max_rows: int = 200) -> Dict[str, Any]:
    report = json.load(open(exposure_path, encoding="utf-8"))
    roots = roots or report.get("roots", [])
    counts: Dict[str, int] = {}
    rows: List[Dict[str, Any]] = []
    unresolved = 0
    for row in report.get("exposure_rows", [])[:max_rows]:
        mission_dir = None
        for root in roots:
            candidate = os.path.join(root, row["mission"])
            if os.path.isdir(candidate):
                mission_dir = candidate
                break
        if mission_dir is None:
            unresolved += 1
            continue
        path = os.path.join(mission_dir, row["file"])
        label = "unreadable"
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read(4 << 20)
        except OSError:
            unresolved += 1
            continue
        lines = text.splitlines()
        line_text = lines[row["line"] - 1] if 0 < row["line"] <= len(lines) else ""
        label = "not_found_in_line"
        found = None
        for match in _TOKEN.finditer(line_text):
            token = match.group(0)
            if hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest() == row["value_sha256"]:
                found = token
                break
        if found is None:
            # a token may contain characters outside the narrow alphabet ('.', ':'),
            # and the finding records its length, so an exact-length window sweep
            # re-derives it without guessing
            width = int(row.get("length") or 0)
            if 0 < width <= len(line_text):
                for start in range(0, len(line_text) - width + 1):
                    candidate = line_text[start:start + width]
                    if hashlib.sha256(candidate.encode("utf-8", "surrogatepass")).hexdigest() == row["value_sha256"]:
                        found = candidate
                        break
        if found is not None:
            label = _classify(found)
        counts[label] = counts.get(label, 0) + 1
        rows.append({"mission": row["mission"], "file": row["file"], "line": row["line"],
                     "type": row["type"], "confidence": row["confidence"],
                     "value_sha256": row["value_sha256"], "structural_class": label})
    total = sum(counts.values())
    credential_like = counts.get("unclassified", 0)
    payload = {
        "schema": SCHEMA,
        "source": exposure_path,
        "rows_adjudicated": total,
        "unresolved": unresolved,
        "by_class": counts,
        "credential_like_rows": credential_like,
        "credential_like_ratio": round(credential_like / total, 4) if total else 0.0,
        "note": ("Structural classification only: the token is re-derived from the exposed line and "
                 "hashed to confirm identity; the token itself is never written to this artifact."),
        "rows": rows,
    }
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
    return payload


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="structural adjudication of the exposure list")
    parser.add_argument("--exposure", default="artifacts/secret_scan/history_exposure.json")
    parser.add_argument("--out", default="artifacts/secret_scan/exposure_adjudication.json")
    parser.add_argument("--max-rows", type=int, default=200)
    args = parser.parse_args()
    result = adjudicate(args.exposure, out=args.out, max_rows=args.max_rows)
    print(json.dumps({k: result[k] for k in ("rows_adjudicated", "by_class", "credential_like_rows",
                                             "credential_like_ratio", "unresolved")},
                     ensure_ascii=False, indent=2))