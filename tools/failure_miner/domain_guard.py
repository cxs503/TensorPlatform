"""Domain semantics guard: verify zero domain vocabulary in failure miner output.

Scans generated text for domain blacklist terms. All domain vocabulary must
come from external config (injected at runtime), never hardcoded here.
"""
from __future__ import annotations

import re
from typing import Any, Sequence


def scan_for_domain_terms(
    texts: list[str],
    domain_blacklist: list[str],
) -> list[dict[str, Any]]:
    """Scan texts for domain blacklist terms.

    Args:
        texts: list of text strings to scan
        domain_blacklist: list of domain terms (ASCII matched on word boundaries,
                         CJK matched as substrings)

    Returns:
        List of {text_index, term, position} for each hit.
    """
    hits = []
    for idx, text in enumerate(texts):
        if not text:
            continue
        for term in domain_blacklist:
            # ASCII terms: word boundary match
            if term.isascii():
                pattern = r'\b' + re.escape(term) + r'\b'
                for m in re.finditer(pattern, text, re.IGNORECASE):
                    hits.append({
                        "text_index": idx,
                        "term": term,
                        "position": m.start(),
                        "context": text[max(0, m.start()-20):m.end()+20],
                    })
            else:
                # CJK terms: substring match
                pos = 0
                while True:
                    pos = text.find(term, pos)
                    if pos == -1:
                        break
                    hits.append({
                        "text_index": idx,
                        "term": term,
                        "position": pos,
                        "context": text[max(0, pos-20):pos+len(term)+20],
                    })
                    pos += 1
    return hits


def verify_zero_domain_semantics(
    traps_candidates: list[dict],
    domain_blacklist: list[str],
) -> dict[str, Any]:
    """Verify that traps_candidates contain zero domain terms.

    Args:
        traps_candidates: list of trap candidate dicts
        domain_blacklist: domain vocabulary list

    Returns:
        Dict with 'clean' (bool), 'hits' (list), 'scanned_count' (int).
    """
    texts = []
    for candidate in traps_candidates:
        # Scan all text fields
        for key in ("description", "rule_text", "signature_type", "error_code",
                     "evidence_sql", "file_fingerprint"):
            val = candidate.get(key, "")
            if isinstance(val, str):
                texts.append(val)
        # Also scan mission_ids as string representation
        mids = candidate.get("mission_ids", [])
        if isinstance(mids, list):
            texts.append(str(mids))

    hits = scan_for_domain_terms(texts, domain_blacklist)

    return {
        "clean": len(hits) == 0,
        "hits": hits,
        "scanned_count": len(texts),
        "total_candidates": len(traps_candidates),
    }


def load_domain_blacklist(path: str | None = None) -> list[str]:
    """Load domain blacklist from config file.

    Args:
        path: path to domain lexicon JSON file. If None, returns empty list.

    Returns:
        List of domain terms (ASCII + CJK combined).
    """
    import json
    from pathlib import Path

    if path is None:
        return []

    p = Path(path)
    if not p.exists():
        return []

    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)

    terms = []
    terms.extend(data.get("ascii_terms", []))
    terms.extend(data.get("cjk_terms", []))
    return terms
