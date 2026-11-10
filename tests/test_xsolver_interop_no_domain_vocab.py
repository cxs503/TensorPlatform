"""Iron-law gate: the interoperation mechanism must stay domain-agnostic.

The vocabulary is injected (``data/campaigns/<campaign>/domain_vocab.json``); this
test only walks the declared mechanism paths and asserts that no injected domain
token appears there.  Domain content (which quantity, which tolerance, which
contract) belongs to ``data/campaigns/<campaign>/`` and ``config/xsolver/``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
VOCAB_PATH = REPO / "data" / "campaigns" / "p106w2" / "domain_vocab.json"


@pytest.fixture(scope="module")
def vocab():
    if not VOCAB_PATH.exists():
        pytest.skip("injected vocabulary not present in this checkout")
    return json.loads(VOCAB_PATH.read_text(encoding="utf-8"))


def test_mechanism_code_carries_no_domain_vocabulary(vocab):
    paths = [REPO / p for p in vocab["scope"]["paths"]]
    extensions = tuple(vocab["scope"].get("extensions") or [".py"])
    tokens = [re.compile(rf"\b{re.escape(token)}\b", re.IGNORECASE)
              for token in vocab["forbidden_in_mechanism_code"]]
    offenders = []
    scanned = 0
    for base in paths:
        files = [base] if base.is_file() else sorted(base.rglob("*"))
        for path in files:
            if not path.is_file() or path.suffix not in extensions:
                continue
            scanned += 1
            text = path.read_text(encoding="utf-8", errors="ignore")
            for token, pattern in zip(vocab["forbidden_in_mechanism_code"], tokens):
                if pattern.search(text):
                    offenders.append(f"{path.relative_to(REPO)}: {token}")
    assert scanned > 0, "nothing scanned -- the scope paths are wrong"
    assert not offenders, "domain vocabulary leaked into mechanism code:\n" + "\n".join(offenders)


def test_domain_content_is_present_only_in_data_packages(vocab):
    """Sanity check the other direction: the injected data package really does carry the words."""
    manifest = json.loads((REPO / "data" / "campaigns" / "p106w2" / "interop_manifest.json")
                          .read_text(encoding="utf-8"))
    blob = json.dumps(manifest, ensure_ascii=False).lower()
    hits = [token for token in vocab["forbidden_in_mechanism_code"] if token in blob]
    assert len(hits) >= 3, "manifest should carry the domain vocabulary, not the code"