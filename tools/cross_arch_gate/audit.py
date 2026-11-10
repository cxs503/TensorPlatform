"""Zero-domain-semantics audit for the cross-architecture gate (machine check).

TensorPlatform is domain-neutral infrastructure; domain vocabulary is only
allowed to arrive through artifacts and configuration.  This module turns that
rule into a test for the gate package itself: the platform-wide lexicon
(``tools/evidence_domain_lexicon.json``, the configuration — not the subject) is
matched against the gate's code, policy and test fixture, and the count of hits
must be zero.

The audit is deliberately *narrow and mechanical*: it reports the file, line and
term for every hit so a reviewer can tell a false positive from a real leak
without re-running anything.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
LEXICON_PATH = ROOT / "tools" / "evidence_domain_lexicon.json"
SKIP_SUFFIXES = {".pyc", ".png", ".jpg", ".zip", ".so", ".json.gz"}

#: Default scope of the audit: the gate package, its pytest module and the
#: machine-readable policy.  Documentation is out of scope on purpose — prose may
#: cite the physical context of a benchmark; code may not encode it.
DEFAULT_TARGETS = (
    "tools/cross_arch_gate",
    "tests/test_cross_arch_gate.py",
)


def load_lexicon(path: Optional[Path] = None) -> Dict[str, Any]:
    return json.loads(Path(path or LEXICON_PATH).read_text(encoding="utf-8"))


def _iter_files(targets: Iterable[str]) -> Iterable[Path]:
    for target in targets:
        path = (ROOT / target) if not Path(target).is_absolute() else Path(target)
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and child.suffix not in SKIP_SUFFIXES:
                    yield child
        elif path.is_file():
            yield path


def scan(targets: Sequence[str] = DEFAULT_TARGETS, lexicon: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    lex = dict(lexicon or load_lexicon())
    ascii_terms = [t.lower() for t in lex["ascii_terms"]]
    cjk_terms = list(lex["cjk_terms"])
    ascii_re = re.compile(r"\b(" + "|".join(re.escape(t) for t in ascii_terms) + r")\b", re.IGNORECASE) \
        if ascii_terms else None

    hits: List[Dict[str, Any]] = []
    scanned: List[str] = []
    for path in _iter_files(targets):
        scanned.append(str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path))
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if ascii_re is not None:
                for match in ascii_re.finditer(line):
                    hits.append({"file": scanned[-1], "line": lineno,
                                 "term": match.group(1).lower(), "kind": "ascii"})
            for term in cjk_terms:
                if term in line:
                    hits.append({"file": scanned[-1], "line": lineno, "term": term, "kind": "cjk"})
    return {
        "schema_version": "1.0",
        "scope": list(targets),
        "files_scanned": len(set(scanned)),
        "lexicon": {"ascii_terms": len(ascii_terms), "cjk_terms": len(cjk_terms),
                    "path": str(LEXICON_PATH.relative_to(ROOT))},
        "hit_count": len(hits),
        "hits": hits,
        "pass": len(hits) == 0,
    }


def save(report: Mapping[str, Any], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path