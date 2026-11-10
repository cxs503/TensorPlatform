"""Dispatch-time field registry + hard leakage guard.

The single invariant of this module: **a model may only read what the
dispatcher knew at the moment it had to rank the slate.**  Two independent
guards enforce it:

1. a name-level whitelist (``DISPATCH_FIELDS``) and blacklist
   (``POST_HOC_FIELDS``) -- any post-hoc column is rejected by name;
2. a *timestamp-level* guard -- every used field must carry an availability
   stamp ``field_ts[name] <= dispatch_ts``.  This catches the subtler failure
   where a permitted field is refreshed after dispatch (e.g. ``target_words``
   edited during evaluation, ``n_criteria`` grown by an appended criterion).

Both guards are machine-checkable and are exercised by
``tests/test_dispatch_evoi.py`` (``test_timestamp_leak_is_rejected``,
``test_post_hoc_field_is_rejected``) so a future edit that starts reading an
outcome-derived column fails immediately instead of silently inflating AUC.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "LeakageError",
    "DISPATCH_FIELDS",
    "POST_HOC_FIELDS",
    "assert_dispatch_time",
    "assert_no_post_hoc_columns",
]


class LeakageError(RuntimeError):
    """A feature read information that did not exist at dispatch time."""


#: fields legitimately available when the slate is ranked
DISPATCH_FIELDS: tuple[str, ...] = (
    "repo",            # which repo the order lands in
    "family",          # upstream task family (for pass-rate history)
    "tier",            # declared task tier (S1..S5 analogue)
    "n_criteria",      # number of machine-checkable acceptance criteria
    "target_words",    # target size of the deliverable
    "novelty",         # proposal novelty score (0..1), scored at intake
    "budget_units",    # compute budget granted to the order
    "value_units",     # capability value if accepted (declared at intake)
)

#: anything produced *after* the order ran.  Reading one of these is the classic
#: target leak; the list is deliberately broad because the
#: ``assert_no_post_hoc_columns`` check is name based.
POST_HOC_FIELDS: frozenset[str] = frozenset(
    {
        "passed",
        "outcome",
        "label",
        "y",
        "verdict",
        "hard_fail",
        "wall_time",
        "wall_seconds",
        "duration_s",
        "runtime_s",
        "cost_actual",
        "cores_used",
        "retry_count",
        "fail_reason",
        "first_failure_ts",
        "finished_at",
        "accepted_at",
        "accept_ts",
        "graded_at",
        "evaluation_ts",
        "report_id",
        "report_sha",
        "commit_sha",
        "score",
        "quality_score",
        "grade_reason",
        "tokens_used",
    }
)


def assert_dispatch_time(
    record: Mapping[str, Any],
    dispatch_ts: float,
    *,
    used: Sequence[str] | Iterable[str] = DISPATCH_FIELDS,
) -> None:
    """Raise :class:`LeakageError` unless every field in ``used`` was known in
    time for ``dispatch_ts``.

    ``record`` must be a candidate order carrying ``field_ts``: a mapping
    ``field -> availability timestamp``.  Missing stamps are treated as a leak
    (unproven provenance is not accepted as evidence).
    """
    for name in used:
        if name in POST_HOC_FIELDS:
            raise LeakageError(f"post-hoc field {name!r} may not be a feature")
        if name not in record:
            raise LeakageError(f"feature {name!r} missing from the order record")
        ts_map = record.get("field_ts") or {}
        stamp = ts_map.get(name)
        if stamp is None:
            raise LeakageError(f"feature {name!r} has no availability timestamp")
        if float(stamp) > float(dispatch_ts):
            raise LeakageError(
                f"feature {name!r} became available at {stamp} > dispatch_ts {dispatch_ts}"
            )


def assert_no_post_hoc_columns(columns: Iterable[str]) -> None:
    """Ensure a fitted design matrix contains no post-hoc column."""
    bad = sorted({c for c in columns if c in POST_HOC_FIELDS})
    if bad:
        raise LeakageError(f"design matrix contains post-hoc columns: {bad}")