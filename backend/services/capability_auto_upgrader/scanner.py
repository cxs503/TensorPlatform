"""Zero-domain-semantics guard: scan source text against a configured deny list.

The vocabulary lives in ``config/capability_auto_upgrader/deny_list.json`` — a
data file, not a constant in the platform module — so the rule can be widened for
other repositories without editing code.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

DEFAULT_DENY_LIST = (
    Path(__file__).resolve().parents[3] / "config" / "capability_auto_upgrader" / "deny_list.json"
)


def load_deny_list(path: Optional[Path | str] = None) -> Dict[str, List[str]]:
    path = Path(path) if path is not None else DEFAULT_DENY_LIST
    if not path.is_file():
        raise FileNotFoundError(f"deny list not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "whole_word_ascii": list(payload.get("whole_word_ascii") or []),
        "substring_any": list(payload.get("substring_any") or []),
    }


def _word_re(term: str) -> "re.Pattern[str]":
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])", re.IGNORECASE)


def scan_text(text: str, deny: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    word_patterns = [(_word_re(t), t) for t in deny.get("whole_word_ascii", [])]
    substrings = deny.get("substring_any", [])
    for lineno, line in enumerate(text.splitlines(), 1):
        for pattern, term in word_patterns:
            if pattern.search(line):
                hits.append({"term": term, "line": lineno, "text": line.strip()[:160]})
        for term in substrings:
            if term in line:
                hits.append({"term": term, "line": lineno, "text": line.strip()[:160]})
    return hits


def scan_files(
    paths: Iterable[Path | str],
    deny: Optional[Dict[str, List[str]]] = None,
    suffixes: Sequence[str] = (".py",),
) -> Dict[str, Any]:
    deny = deny or load_deny_list()
    hits: List[Dict[str, Any]] = []
    scanned: List[str] = []
    for path in paths:
        path = Path(path)
        if path.is_dir():
            targets = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix in suffixes)
        elif path.is_file() and path.suffix in suffixes:
            targets = [path]
        else:
            continue
        for target in targets:
            scanned.append(str(target))
            for hit in scan_text(target.read_text(encoding="utf-8", errors="replace"), deny):
                hit["file"] = str(target)
                hits.append(hit)
    return {"ok": not hits, "n_files": len(scanned), "n_hits": len(hits), "hits": hits, "files": scanned}