"""Autonomy exit dashboard (IN20-R7): measure the decay of human dependence.

Three metrics, all SQL-recomputable:

* M1 ``unbroken_autonomy_hours`` -- hours since the last human-side action
  (owner adjudication / coordinator session action).
* M2 ``human_interventions_per_day`` -- human-side action counts split by
  actor class (``owner`` vs ``coordinator``) inside a rolling window.
* M3 ``mechanism_coverage`` -- share of the eight standing duties that have a
  daemon (or in-platform stand-in) **and** show activity evidence in the window.

Evidence grading follows the platform rule: ``REAL`` when measured from the
database, ``WITHHELD`` when the query failed or no evidence exists.  The module
never fabricates a number: missing evidence is reported as ``WITHHELD``.

Zero domain semantics: nothing in this package knows about any physical or
engineering domain -- it only reads platform bookkeeping tables.
"""

from .sr_readiness import (  # noqa: F401
    CLOCK_SLOT,
    PACK_CHECKS,
    TARGETS,
    build_report as build_sr_report,
    chronicle_payload as sr_chronicle_payload,
    continuity as sr_continuity,
    measure_sr1,
    measure_sr2,
    measure_sr3,
    measure_sr4,
)

from .metrics import (  # noqa: F401
    MECHANISMS,
    ActionSource,
    Mechanism,
    PgRunner,
    action_sources,
    audit_mechanisms,
    build_baseline,
    chronicle_payload,
    default_daemon_roots,
    human_input_metrics,
    render_for_psql,
    tables_in,
)

__all__ = [
    "CLOCK_SLOT",
    "PACK_CHECKS",
    "TARGETS",
    "build_sr_report",
    "measure_sr1",
    "measure_sr2",
    "measure_sr3",
    "measure_sr4",
    "sr_chronicle_payload",
    "sr_continuity",
    "MECHANISMS",
    "ActionSource",
    "Mechanism",
    "PgRunner",
    "action_sources",
    "audit_mechanisms",
    "build_baseline",
    "chronicle_payload",
    "default_daemon_roots",
    "human_input_metrics",
    "render_for_psql",
    "tables_in",
]

__version__ = "1.1.0"  # + SR1..SR4 inheritance-readiness block (IN27-H7)