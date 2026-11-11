"""Verification chain for the platform metrics tools (pulse / chronicle / portrait).

The three tools that answer "is the fleet alive at the latency we claim", "did we
lose an event", and "is this seat behaving as its profile says" had no systematic
verification: their numbers were reported, never falsified against known truth.
This package builds the missing chain: a synthetic fleet / event log / seat
population with known ground truth, the acceptance thresholds, an HTTP surface,
and a runner that emits a machine-checkable report plus figures.
"""

from . import (  # noqa: F401
    chronicle_integrity,
    figures,
    portrait_consistency,
    pulse_latency,
    sim,
    stats,
)

__all__ = [
    "chronicle_integrity",
    "figures",
    "portrait_consistency",
    "pulse_latency",
    "sim",
    "stats",
]
__version__ = "1.0.0"