"""Criterion C5: the selector package must carry zero domain semantics.

Two checks:

1. the package source (code + docs, excluding the lexicon fixture itself) is
   scanned with word boundaries against a neutral lexicon: 0 hits;
2. the test fixtures / sandbox modules are synthetic names only (no real case
   names, no numbers copied from any reference solution).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

PKG = Path(__file__).resolve().parents[1]
LEXICON = Path(__file__).resolve().parent / "fixtures" / "domain_lexicon.json"


def _lexicon() -> Dict[str, object]:
    return json.loads(LEXICON.read_text(encoding="utf-8"))


def _scan(files: List[Path], tokens: List[str]) -> List[str]:
    patterns = [(t, re.compile(rf"\b{re.escape(t)}\b", re.IGNORECASE)) for t in tokens]
    hits: List[str] = []
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        for tok, pat in patterns:
            if pat.search(text):
                hits.append(f"{f.relative_to(PKG).as_posix()}:{tok}")
    return sorted(hits)


def _package_files() -> List[Path]:
    out = []
    for p in sorted(PKG.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        if p.suffix not in (".py", ".md", ".json", ".txt"):
            continue
        if p == LEXICON:
            continue
        out.append(p)
    return out


def test_zero_domain_hits_in_package():
    lex = _lexicon()
    hits = _scan(_package_files(), list(lex["tokens"]))
    assert hits == [], f"domain vocabulary found in package: {hits[:10]}"


def test_fixtures_are_synthetic():
    # the sandbox tree only uses neutral placeholder names
    from tools.test_impact_selector import sandbox

    allowed_prefixes = ("alpha/", "beta/", "gamma/", "tests/", "docs/")
    for rel in sandbox.FILES:
        assert rel.startswith(allowed_prefixes), rel
    text = "\n".join(sandbox.FILES.values()).lower()
    lex = _lexicon()
    assert _scan([PKG / "__init__.py"], list(lex["tokens"])) == []
    assert "alpha" in text and "beta" in text
    for tok in lex["tokens"]:
        assert not re.search(rf"\b{re.escape(tok)}\b", text), tok


def test_lexicon_is_data_not_code():
    # the lexicon is a fixture: no python module may read it
    for p in PKG.rglob("*.py"):
        if "__pycache__" in p.parts or p.name == "test_zero_domain.py":
            continue
        assert "domain_lexicon" not in p.read_text(encoding="utf-8", errors="replace")