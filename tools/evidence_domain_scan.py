#!/usr/bin/env python3
"""Domain-vocabulary scan for the evidence-grading package (CI gate).

TensorPlatform is domain-neutral infrastructure: domain semantics arrive through
artifacts and configuration, never through platform code. The evidence engine is
a platform mechanism, so it must be provably free of domain vocabulary. This
scan turns that rule into a machine check instead of a review habit.

Scope is declared in ``tools/evidence_domain_lexicon.json`` (the lexicon is the
single exemption: it is the configuration, not the subject). ASCII terms match on
word boundaries; CJK terms match as substrings.

Exit code: 0 when no hit, 1 when any hit (CI red).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEXICON_PATH = Path(__file__).resolve().parent / "evidence_domain_lexicon.json"
_SKIP_SUFFIXES = {".pyc", ".png", ".jpg", ".gif", ".zip", ".so"}


def load_lexicon(path: Path | None = None) -> dict:
    return json.loads(Path(path or LEXICON_PATH).read_text(encoding="utf-8"))


def _patterns(lexicon: dict) -> list[tuple[str, re.Pattern]]:
    pats = []
    for term in lexicon.get("ascii_terms", []):
        pats.append((term, re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)))
    for term in lexicon.get("cjk_terms", []):
        pats.append((term, re.compile(re.escape(term))))
    return pats


def _iter_files(target: Path, root: Path):
    if target.is_file():
        yield target
        return
    if not target.exists():
        return
    for p in sorted(target.rglob("*")):
        if p.is_file() and p.suffix.lower() not in _SKIP_SUFFIXES:
            yield p


def scan_paths(targets, lexicon: dict | None = None, root: Path | None = None) -> dict:
    """Scan explicit target paths; return hits + coverage."""
    root = Path(root or ROOT)
    lexicon = lexicon or load_lexicon()
    pats = _patterns(lexicon)
    hits: list[dict] = []
    scanned = 0
    for raw in targets:
        target = Path(raw)
        if not target.is_absolute():
            target = root / target
        if target.resolve() == LEXICON_PATH.resolve():
            continue
        for path in _iter_files(target, root):
            if path.resolve() == LEXICON_PATH.resolve():
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            scanned += 1
            for lineno, line in enumerate(lines, start=1):
                for term, pat in pats:
                    if pat.search(line):
                        hits.append({
                            "path": str(path.relative_to(root)) if path.is_relative_to(root) else str(path),
                            "line": lineno,
                            "term": term,
                            "text": line.strip()[:200],
                        })
    return {"scanned_files": scanned, "hits": hits,
            "terms": len(pats), "targets": [str(t) for t in targets]}


def scan_default_targets() -> dict:
    lexicon = load_lexicon()
    return scan_paths(lexicon.get("targets", []), lexicon=lexicon)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="domain-vocabulary scan (CI gate)")
    ap.add_argument("--lexicon", default=str(LEXICON_PATH))
    ap.add_argument("paths", nargs="*", help="override targets (default: lexicon targets)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    lexicon = load_lexicon(Path(args.lexicon))
    if args.paths:
        result = scan_paths(args.paths, lexicon=lexicon)
    else:
        result = scan_default_targets()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"scanned_files={result['scanned_files']} terms={result['terms']} "
              f"hits={len(result['hits'])}")
        for hit in result["hits"]:
            print(f"{hit['path']}:{hit['line']}: {hit['term']} :: {hit['text']}")
    return 1 if result["hits"] else 0


if __name__ == "__main__":
    raise SystemExit(main())