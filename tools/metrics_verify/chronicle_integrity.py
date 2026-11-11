#!/usr/bin/env python3
"""Verification of the **chronicle** side: event-stream integrity.

Contract under test (shape taken from the deployed table
``tensor_agent_session_events``: ``id, session_id, seq, kind, payload,
created_at``):

* ``seq`` is per session, contiguous and strictly increasing in append order;
* ``created_at`` is non-decreasing within a session (an append-only log);
* no duplicate event ids;
* every event of the truth stream is present.

The validator is the *reference implementation of those invariants* -- the
platform had no pure (DB-free) checker, so the checker itself is one of the
deliverables.  It is validated the hard way: the simulator injects known fault
sets (out-of-order transpositions, duplicates, drops) and the validator must
recover them at the campaign detection rates, with zero false alarms on a clean
stream.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .sim import EventStream, simulate_event_stream


class ChronicleVerifyError(ValueError):
    """Raised on a malformed stream (missing required columns, ...)."""


REQUIRED_COLUMNS: Tuple[str, ...] = ("id", "session_id", "seq", "kind", "created_at")


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise ChronicleVerifyError(f"unparseable created_at: {value!r}") from exc


def validate_event_stream(events: Sequence[Dict[str, Any]],
                          expected_per_session: Optional[Dict[str, int]] = None,
                          *, require_monotonic_ts: bool = True) -> Dict[str, Any]:
    """Check completeness / ordering / monotonicity of an event stream.

    ``expected_per_session`` is the truth count per session when the checker is
    being evaluated (a production caller reads it from the emitter's own counter);
    without it completeness is reported as ``None`` instead of a fabricated 100 %.
    """
    rows = list(events)
    for row in rows:
        missing = [c for c in REQUIRED_COLUMNS if c not in row]
        if missing:
            raise ChronicleVerifyError(f"event missing required column(s) {missing}: {row!r}")

    seen_ids: Dict[str, int] = {}
    duplicates: List[str] = []
    last_seq: Dict[str, int] = {}
    last_ts: Dict[str, datetime] = {}
    seq_regressions: List[Dict[str, Any]] = []
    ts_regressions: List[Dict[str, Any]] = []
    flagged_ids: Set[str] = set()
    flagged_indices: Set[int] = set()
    ordered_pairs = 0
    ordered_ok = 0
    per_session_counts: Dict[str, int] = {}
    per_session_max_seq: Dict[str, int] = {}

    for idx, row in enumerate(rows):
        eid = str(row["id"])
        session = str(row["session_id"])
        seq = int(row["seq"])
        ts = _parse_ts(row["created_at"])
        per_session_counts[session] = per_session_counts.get(session, 0) + 1
        per_session_max_seq[session] = max(per_session_max_seq.get(session, 0), seq)

        if eid in seen_ids:
            duplicates.append(eid)
            flagged_ids.add(eid)
            flagged_indices.add(idx)
            flagged_indices.add(seen_ids[eid])
        else:
            seen_ids[eid] = idx

        prev_seq = last_seq.get(session)
        prev_ts = last_ts.get(session)
        if prev_seq is not None:
            ordered_pairs += 1
            seq_ok = seq > prev_seq
            ts_ok = (not require_monotonic_ts) or prev_ts is None or ts >= prev_ts
            if seq_ok and ts_ok:
                ordered_ok += 1
            if not seq_ok:
                seq_regressions.append({"index": idx, "id": eid, "session_id": session,
                                        "prev_seq": prev_seq, "seq": seq})
                flagged_ids.add(eid)
                flagged_indices.update((idx - 1, idx))
            if not ts_ok:
                ts_regressions.append({"index": idx, "id": eid, "session_id": session,
                                       "prev_created_at": prev_ts.isoformat(),
                                       "created_at": ts.isoformat()})
                flagged_ids.add(eid)
                flagged_indices.update((idx - 1, idx))
        last_seq[session] = max(prev_seq, seq) if prev_seq is not None else seq
        last_ts[session] = max(prev_ts, ts) if prev_ts is not None else ts

    # missing sequence numbers per session (holes), from the stream itself
    holes: Dict[str, List[int]] = {}
    for session, count in per_session_counts.items():
        max_seq = per_session_max_seq[session]
        present = {int(r["seq"]) for r in rows if str(r["session_id"]) == session}
        missing = [s for s in range(1, max_seq + 1) if s not in present]
        if missing:
            holes[session] = missing

    completeness: Optional[float] = None
    missing_vs_truth = 0
    if expected_per_session:
        expected_total = sum(expected_per_session.values())
        missing_vs_truth = sum(
            max(0, expected_per_session.get(s, 0) - per_session_counts.get(s, 0))
            for s in set(expected_per_session) | set(per_session_counts)
        )
        if expected_total:
            completeness = max(0.0, (expected_total - missing_vs_truth) / expected_total)

    n = len(rows)
    order_correct_rate = (ordered_ok / ordered_pairs) if ordered_pairs else 1.0
    return {
        "n_events": n,
        "n_sessions": len(per_session_counts),
        "expected_per_session": dict(expected_per_session or {}),
        "per_session_counts": per_session_counts,
        "n_missing_vs_truth": missing_vs_truth,
        "completeness": completeness,
        "n_duplicates": len(duplicates),
        "duplicate_ids": duplicates[:10],
        "n_seq_holes": sum(len(v) for v in holes.values()),
        "seq_holes": {k: v[:10] for k, v in holes.items()},
        "n_seq_regressions": len(seq_regressions),
        "n_ts_regressions": len(ts_regressions),
        "seq_regressions": seq_regressions[:10],
        "ts_regressions": ts_regressions[:10],
        "ordered_pairs": ordered_pairs,
        "order_correct_rate": order_correct_rate,
        "flagged_event_ids": sorted(flagged_ids),
        "flagged_indices": sorted(i for i in flagged_indices if i >= 0),
        "n_flagged": len(flagged_ids),
        "ok": bool(
            not duplicates and not holes and not seq_regressions and not ts_regressions
            and (completeness is None or completeness == 1.0)
        ),
    }


def score_disorder_detection(stream: EventStream) -> Dict[str, Any]:
    """Detection rate of the injected transpositions + false-alarm count.

    Scored at *transposition* granularity: a swapped pair is one injected fault
    whose signature is a single sequence inversion, so the unit of both the
    numerator and the denominator is the inversion position.  Event-identity
    agreement is reported next to it for completeness.
    """
    report = validate_event_stream(stream.events, stream.expected_per_session)
    injected_pos = set(stream.injected_disorder_positions)
    flagged_pos = set(report["flagged_indices"])
    hit = injected_pos & flagged_pos
    injected_ids = set(stream.injected_disorder_ids)
    flagged_ids = set(report["flagged_event_ids"])
    # a flagged position adjacent (+/-1) to a true inversion belongs to that fault
    false_alarm_positions = [p for p in sorted(flagged_pos)
                             if all(abs(p - q) > 1 for q in injected_pos)]
    return {
        "n_injected_transpositions": len(injected_pos),
        "n_injected_events": len(injected_ids),
        "n_flagged_positions": len(flagged_pos),
        "n_injected_detected": len(hit),
        "detection_rate": (len(hit) / len(injected_pos)) if injected_pos else 1.0,
        "n_false_alarm_positions": len(false_alarm_positions),
        "false_alarm_positions": false_alarm_positions[:10],
        "false_alarm_rate": (len(false_alarm_positions) / max(1, report["n_events"])),
        "event_identity_agreement": (len(injected_ids & flagged_ids) / len(injected_ids))
        if injected_ids else 1.0,
        "inversions_observed": report["n_seq_regressions"],
        "report": report,
    }


def run_chronicle_verify(*, n_events: int = 10000, sessions: int = 5,
                         seed: int = 20261111,
                         disorder_rate: float = 0.01,
                         order_min: float = 1.0,
                         disorder_detect_min: float = 0.99) -> Dict[str, Any]:
    """Full chronicle acceptance bundle (criteria c, d + negative controls)."""
    honest = simulate_event_stream(n_events, sessions=sessions, seed=seed)
    honest_report = validate_event_stream(honest.events, honest.expected_per_session)

    disordered = simulate_event_stream(n_events, sessions=sessions, seed=seed,
                                       disorder_rate=disorder_rate)
    disorder_score = score_disorder_detection(disordered)

    dropped = simulate_event_stream(n_events, sessions=sessions, seed=seed, drop_rate=0.01)
    drop_report = validate_event_stream(dropped.events, dropped.expected_per_session)

    duplicated = simulate_event_stream(n_events, sessions=sessions, seed=seed, duplicate_rate=0.01)
    dup_report = validate_event_stream(duplicated.events, duplicated.expected_per_session)

    return {
        "honest": {
            "n_events": honest_report["n_events"],
            "n_missing_vs_truth": honest_report["n_missing_vs_truth"],
            "completeness": honest_report["completeness"],
            "order_correct_rate": honest_report["order_correct_rate"],
            "n_seq_regressions": honest_report["n_seq_regressions"],
            "n_ts_regressions": honest_report["n_ts_regressions"],
            "pass": bool(honest_report["completeness"] == 1.0
                         and honest_report["order_correct_rate"] >= order_min
                         and honest_report["n_seq_regressions"] == 0
                         and honest_report["n_ts_regressions"] == 0),
            "order_threshold": order_min,
        },
        "disorder": {
            "injected_rate": disorder_rate,
            "n_injected_transpositions": disorder_score["n_injected_transpositions"],
            "n_injected_events": disorder_score["n_injected_events"],
            "n_injected_detected": disorder_score["n_injected_detected"],
            "detection_rate": disorder_score["detection_rate"],
            "n_false_alarm_positions": disorder_score["n_false_alarm_positions"],
            "threshold": disorder_detect_min,
            "pass": bool(disorder_score["detection_rate"] > disorder_detect_min
                         and disorder_score["n_false_alarm_positions"] == 0),
        },
        "drop_control": {
            "injected_rate": 0.01,
            "n_missing_vs_truth": drop_report["n_missing_vs_truth"],
            "completeness": drop_report["completeness"],
            "detected": drop_report["n_missing_vs_truth"] > 0,
            "pass": bool(drop_report["n_missing_vs_truth"] > 0
                         and (drop_report["completeness"] or 0.0) < 1.0),
        },
        "duplicate_control": {
            "injected_rate": 0.01,
            "n_duplicates": dup_report["n_duplicates"],
            "detected": dup_report["n_duplicates"] > 0,
            "pass": dup_report["n_duplicates"] > 0,
        },
    }