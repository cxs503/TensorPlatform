"""``tools.uq.purity`` -- mechanical check that the UQ engine stays domain free.

The platform rule is that a *generic mechanism* must not embed domain
semantics: vocabulary of any application field belongs to the domain repository
and reaches the engine only through injected data.  This module implements the
check without itself containing any domain word -- the lexicon is a parameter,
supplied by the caller (the test suite injects it).

Usage::

    from tools.uq.purity import scan_terms
    hits = scan_terms(["tools/uq"], terms=["...", "..."])
    assert hits == []

A hit record names the file, the line number and the matched term, so a
regression can be triaged without re-reading the sources.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Union

__all__ = ["scan_terms", "scan_paths"]

PathLike = Union[str, Path]


def _iter_files(targets: Sequence[PathLike], suffixes: Sequence[str] = (".py",)) -> Iterable[Path]:
    for target in targets:
        p = Path(target)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix in suffixes:
                    yield f
        elif p.is_file():
            yield p


def scan_terms(
    targets: Sequence[PathLike],
    terms: Sequence[str],
    suffixes: Sequence[str] = (".py",),
    case_sensitive: bool = False,
) -> List[Dict[str, Any]]:
    """Return one record per ``(file, line, term)`` match of any lexicon term."""
    if not terms:
        return []
    flags = 0 if case_sensitive else re.IGNORECASE
    patterns = [(t, re.compile(rf"(?<![A-Za-z0-9_]){re.escape(t)}(?![A-Za-z0-9_])", flags))
                for t in terms]
    hits: List[Dict[str, Any]] = []
    for f in _iter_files(targets, suffixes):
        text = f.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for term, pat in patterns:
                if pat.search(line):
                    hits.append({"file": str(f), "line": lineno, "term": term,
                                 "text": line.strip()[:160]})
    return hits


def scan_paths(targets: Sequence[PathLike], terms: Sequence[str], **kwargs: Any) -> bool:
    """``True`` when the scan is clean (convenience predicate)."""
    return not scan_terms(targets, terms, **kwargs)