"""Resume-baseline analysis: what the next wave inherits, what is still missing.

Requirement (6) of the campaign node: per dead mission, list the modules that
already exist and the acceptance items that are still missing, so the re-dispatch
order can quote them directly.

Coverage is decided **lexically** — no scientific vocabulary is hardcoded here.
A criterion is tokenised into latin words (length >= 4, lower-cased) plus CJK
bigrams; the same tokeniser runs over the salvaged file paths and over the text
of salvaged docs/code (capped at 256 KiB per file).  A criterion whose tokens are
covered at or above ``threshold`` by the union of salvaged material counts as
"evidence candidate present"; everything below stays on the missing list.  Scores
are recorded per criterion so a reviewer can re-judge at a different threshold.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable

DEFAULT_THRESHOLD = 0.34
CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
LATIN = re.compile(r"[A-Za-z][A-Za-z0-9]+")
SPLIT = re.compile(r"[^0-9A-Za-z]+")


def tokenize(text: str) -> set[str]:
    """Latin words (>=4 chars) + CJK bigrams; empty set for empty text."""
    tokens: set[str] = set()
    for word in LATIN.findall(text or ""):
        lowered = word.lower()
        if len(lowered) >= 4:
            tokens.add(lowered)
    for run in CJK_RUN.findall(text or ""):
        if len(run) == 1:
            tokens.add(run)
        for i in range(len(run) - 1):
            tokens.add(run[i : i + 2])
    return tokens


def path_tokens(path: str) -> set[str]:
    tokens: set[str] = set()
    for part in SPLIT.split(path):
        if len(part) >= 3 and any(ch.isalpha() for ch in part):
            tokens.add(part.lower())
    tokens |= tokenize(path)
    return tokens


def _text_classes() -> tuple[str, ...]:
    return ("doc", "code")


def analyse(
    criteria: Iterable[str],
    files: list[dict],
    read_text: Callable[[str], str],
    threshold: float = DEFAULT_THRESHOLD,
) -> dict:
    """Per-criterion coverage plus an inventory of inherited material."""
    criteria = [c for c in criteria if isinstance(c, str) and c.strip()]
    file_tokens: list[tuple[str, set[str]]] = []
    for record in files:
        tokens = path_tokens(record["path"])
        if record.get("class") in _text_classes():
            tokens |= tokenize(read_text(record["path"]))
        file_tokens.append((record["path"], tokens))

    union: set[str] = set()
    for _, tokens in file_tokens:
        union |= tokens

    covered: list[dict] = []
    missing: list[dict] = []
    for idx, criterion in enumerate(criteria):
        crit_tokens = tokenize(criterion)
        if not crit_tokens:
            row = {
                "index": idx,
                "text": criterion,
                "tokens": [],
                "score": 0.0,
                "best_file": None,
                "verdict": "untokenisable",
            }
            missing.append(row)
            continue
        hits = crit_tokens & union
        score = len(hits) / len(crit_tokens)
        best_file = None
        best_score = 0.0
        for path, tokens in file_tokens:
            local = len(crit_tokens & tokens) / len(crit_tokens)
            if local > best_score:
                best_score, best_file = local, path
        row = {
            "index": idx,
            "text": criterion,
            "score": round(score, 4),
            "local_score": round(best_score, 4),
            "hits": sorted(hits)[:24],
            "best_file": best_file,
            "verdict": "evidence_candidate_present" if score >= threshold else "missing",
        }
        (covered if score >= threshold else missing).append(row)

    inventory: dict[str, list[str]] = {"code": [], "doc": [], "data": [], "other": []}
    for record in sorted(files, key=lambda r: r["path"]):
        inventory.setdefault(record["class"], []).append(record["path"])

    return {
        "threshold": threshold,
        "criteria_total": len(criteria),
        "criteria_covered": len(covered),
        "coverage_ratio": round(len(covered) / len(criteria), 4) if criteria else 0.0,
        "covered": covered,
        "missing": missing,
        "inventory": inventory,
        "note": (
            "coverage is a lexical pre-screen, NOT acceptance: a covered criterion still "
            "needs an executed check before any status may move"
        ),
    }