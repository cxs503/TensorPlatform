"""case 账本读写与结构校验（输入数据经工件注入，包内不含任何内联案例）。"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from tools.repro_settlement.canonical import sha256_obj

REQUIRED_CASE_KEYS = ("case_id", "status")
VALID_STATUS = ("pass", "blocked", "drift")


def validate(corpus: Mapping[str, Any]) -> list[str]:
    """返回问题清单（空=通过）。判据全部结构性，可机裁。"""
    errs: list[str] = []
    if not isinstance(corpus, Mapping):
        return ["corpus_not_mapping"]
    cases = corpus.get("cases")
    if not isinstance(cases, Sequence) or not cases:
        return ["cases_missing_or_empty"]
    seen: set[str] = set()
    for i, c in enumerate(cases):
        if not isinstance(c, Mapping):
            errs.append(f"case[{i}]_not_mapping")
            continue
        for k in REQUIRED_CASE_KEYS:
            if not c.get(k):
                errs.append(f"case[{i}]_missing_{k}")
        cid = c.get("case_id")
        if cid in seen:
            errs.append(f"duplicate_case_id:{cid}")
        seen.add(cid)
        st = str(c.get("status", "")).lower()
        if st not in VALID_STATUS:
            errs.append(f"case[{i}]_bad_status:{st}")
        if st == "blocked" and not isinstance(c.get("blocked"), Mapping):
            errs.append(f"case[{i}]_blocked_without_spec")
        if st == "drift" and not isinstance(c.get("drift"), Mapping):
            errs.append(f"case[{i}]_drift_without_evidence")
        if st != "pass" and not c.get("fail_source"):
            errs.append(f"case[{i}]_nonpass_without_fail_source")
    return errs


def load(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        corpus = json.load(fh)
    errs = validate(corpus)
    if errs:
        raise ValueError("invalid corpus: " + "; ".join(errs))
    return corpus


def dump(corpus: Mapping[str, Any], path: str) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(corpus, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return sha256_obj(corpus)


def tally(corpus: Mapping[str, Any]) -> dict[str, int]:
    out = {"pass": 0, "blocked": 0, "drift": 0}
    for c in corpus.get("cases", []):
        st = str(c.get("status", "")).lower()
        if st in out:
            out[st] += 1
    return out
