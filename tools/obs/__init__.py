"""Observability / alerting minimal surface (additive, read-only).

Modules
-------
alert_rules : pure rule engine (no I/O, deterministic) — stuck-running / blocked-thrice /
              pending-gate detection plus the two *suppression* (negative-control) rules.
obs_digest  : CLI — read-only snapshot from the platform DB (or a committed snapshot),
              evaluate the rules, render the one-page daily digest, dump alerts JSON.

Discipline: additive only. Nothing here imports or mutates the frozen platform API surface,
no schema surgery, no new tables. All DB access is ``SELECT`` through ``psql -tAc``.
"""

__all__ = ["alert_rules", "obs_digest"]