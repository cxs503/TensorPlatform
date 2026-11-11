"""Structural MOCK capability detector for the platform (oracle-free).

Three structural probes — **determinism**, **monotonicity**, **idempotency** —
applied to every HTTP capability endpoint, with **zero domain semantics**: the
module knows nothing about ships, physics, or any particular quantity name.  It
only knows that a capability that claims to *compute* something must

1. give the same answer for the same request (determinism),
2. keep giving the same answer when the same request is repeated, including
   after an interleaved different request (idempotency / no hidden state),
3. let its numeric outputs respond to a monotone sweep of its numeric inputs
   (monotonic response shape — the anti-hardcoded-constant knife), and
4. do all of the above smoothly enough that the response is not a lookup of a
   handful of quantized buckets.

This is deliberately *weaker* than accuracy evidence: passing all three probes
yields ``REAL`` **at structural scope only** (``level_scope="STRUCTURAL"``,
``accuracy_verified=False``).  Accuracy still needs
``tools.capability_audit``'s analytic/benchmark families or a reference-value
run.  What the probes *can* falsify is the MOCK/STUB/random family, and they do
it without any prior knowledge of what the endpoint is supposed to compute.
"""
from .model import LEVELS, ProbeReport, ScanReport, Target  # noqa: F401

__all__ = ["LEVELS", "ProbeReport", "ScanReport", "Target"]