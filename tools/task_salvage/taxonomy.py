"""Terminal-state taxonomy for circuit-broken tasks.

The platform records the *raw* stop reason in ``result.evidence.checks`` (and
repeats it in the headline).  Raw names are not stable across runner versions
(``wall_exhausted`` for what the ledger calls a timeout, ``no_progress``,
``no_progress+forced_finish`` ...), so salvage needs a small, explicit mapping
to four operational buckets plus a catch-all.  The mapping is data-shaped:
pure function of the record, no inference beyond the documented table.

Buckets
-------
``llm_error``        the model/runtime errored out (no ``finish`` call).
``timeout``          a wall-clock ceiling was hit (``wall_exhausted``/``timeout``).
``budget_exhausted`` a token/call budget ceiling was hit.
``no_progress``      the loop stalled and was force-finished.
``other``            anything unmapped, including tasks with no evidence row.

``TARGET_BUCKETS`` are the three states this node is specified to salvage.
"""
from __future__ import annotations

TARGET_BUCKETS = ("llm_error", "timeout", "budget_exhausted")
BUCKETS = TARGET_BUCKETS + ("no_progress", "other")

#: raw ``fail_stage`` → bucket.  Keys are infrastructure words only.
_RAW_TO_BUCKET = {
    "llm_error": "llm_error",
    "model_error": "llm_error",
    "runner_error": "llm_error",
    "timeout": "timeout",
    "wall_exhausted": "timeout",
    "wall_timeout": "timeout",
    "budget_exhausted": "budget_exhausted",
    "token_budget_exhausted": "budget_exhausted",
    "call_budget_exhausted": "budget_exhausted",
    "no_progress": "no_progress",
    "no_progress+forced_finish": "no_progress",
    "forced_finish": "no_progress",
    "": "other",
    "none": "other",
}

#: substring probes used only when no ``fail_stage`` row exists.
_SUBSTRING_PROBES = (
    ("llm_error", "llm_error"),
    ("model error", "llm_error"),
    ("timeout", "timeout"),
    ("timed out", "timeout"),
    ("budget", "budget_exhausted"),
    ("no_progress", "no_progress"),
)

EVIDENCE_PATH = "result.evidence.checks.fail_stage"


def _checks(record: dict) -> dict:
    result = record.get("result") or {}
    evidence = result.get("evidence") or {}
    return evidence.get("checks") or {}


def raw_stage(record: dict) -> str:
    """Raw stop reason as recorded, lower-cased, or ``""``."""
    stage = _checks(record).get("fail_stage")
    if stage is None:
        return ""
    return str(stage).strip().lower()


def bucket_of(record: dict) -> str:
    """Map one mission record to a taxonomy bucket."""
    raw = raw_stage(record)
    if raw in _RAW_TO_BUCKET:
        return _RAW_TO_BUCKET[raw]
    # unknown non-empty raw value: probe the raw text, then the headline text.
    haystack = [raw]
    headline = record.get("headline") or (record.get("result") or {}).get("headline") or ""
    haystack.append(str(headline).lower())
    for text in haystack:
        for probe, mapped in _SUBSTRING_PROBES:
            if probe in text:
                return mapped
    return "other"


def reason_code(record: dict) -> str:
    """Stable machine code for the terminal reason (used in ``salvage.json``)."""
    raw = raw_stage(record)
    bucket = bucket_of(record)
    if bucket == "other":
        return "TERMINAL_OTHER" + ((":" + raw) if raw else ":no_evidence_row")
    return "TERMINAL_" + bucket.upper()


def classify(record: dict) -> dict:
    """Full taxonomy row for one mission record."""
    bucket = bucket_of(record)
    return {
        "bucket": bucket,
        "in_scope": bucket in TARGET_BUCKETS,
        "reason_code": reason_code(record),
        "raw_stage": raw_stage(record),
        "evidence_path": EVIDENCE_PATH,
        "has_evidence_row": bool(_checks(record)),
    }


def in_scope(record: dict) -> bool:
    return bucket_of(record) in TARGET_BUCKETS


def distill(records: list[dict]) -> dict:
    """Bucket histogram plus in-scope counts for a corpus (sorted, deterministic)."""
    counts = {b: 0 for b in BUCKETS}
    scope = 0
    for record in records:
        bucket = bucket_of(record)
        counts[bucket] = counts.get(bucket, 0) + 1
        if bucket in TARGET_BUCKETS:
            scope += 1
    return {
        "total": len(records),
        "bucket_counts": counts,
        "in_scope": scope,
        "in_scope_rate": round(scope / len(records), 4) if records else 0.0,
        "target_buckets": list(TARGET_BUCKETS),
    }