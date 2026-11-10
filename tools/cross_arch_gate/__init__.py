"""Cross-architecture benchmark reproduction gate (TensorPlatform platform mechanism).

One judge, four labels, six machine-checkable criteria — and no domain vocabulary:
the gate knows about *fields*, not about what the fields mean.

Public surface
--------------
``record.build_record``      measure this machine and emit an immutable record
``gate.compare``             pure function: two records + policy -> PASS/FAIL report
``classify.classify_field``  the four-label judgement for a single field
``selftest.run_matrix``      mutation harness (detection / false-positive / NaN)
``audit.scan``               zero-domain-semantics machine check
``pipeline.run``             end-to-end pipeline that emits criteria from artifacts
``numerics``                 ``%a`` plumbing, ULP distance, portable IEEE-754 oracle

Why the split matters: ``record`` is the only part that must execute on the
architecture under test.  Everything else is a pure function of JSON records, so
the judging logic is verifiable on one machine while the measurements come from
many.
"""

from __future__ import annotations

__all__ = [
    "audit", "classify", "criteria", "fixtures", "figures", "gate",
    "numerics", "pipeline", "record", "report_md", "selftest",
]

__version__ = "1.0.0"