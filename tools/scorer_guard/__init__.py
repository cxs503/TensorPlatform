"""Scorer hardening / red-team tooling for the tournament scorer (IN25-T5).

Layout
------
``anchors``   anchor registry + hash-chained provenance ledger (fail-closed)
``physics``   closed-form physics invariants used as independent cross-checks
``score``     the scored function itself + Goodhart gates (version locked)
``prereg``    scorer version lock (pre-registration) and drift detection
``mutation``  source-level mutation harness (negative controls)
``redteam``   analytic-limit / attack case suite and verdicts
``cli``       one-shot artifact builder (JSON + Markdown + figures)
"""

__all__ = ["anchors", "physics", "score", "prereg", "mutation", "redteam"]