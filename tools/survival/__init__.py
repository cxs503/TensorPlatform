"""Long-run survival *maturity* layer: crash bundle -> verifiable evidence directory,
memory-constrained degradation, honest-FAIL retention, and the progress/stall rig.

Relationship to :mod:`tools.longrun` (mission m9082): that package owns capture
(ptrace guardian, gdb autopsy, bit-level checkpoints, idempotent resume, pressure
ladder).  This package owns the parts needed to make a *long campaign* survive on
its own:

* :mod:`forensics_bundle` -- assemble one crash into ``artifacts/crash_<stamp>/``:
  symbolized stack, >=5 parsed registers, address-space map, run-time manifest
  snapshot, plus ``SHA256SUMS``/``digest.json`` so the evidence verifies itself;
* :mod:`degrade`        -- decide-and-apply single-thread + row-streaming under a
  memory cap, with the numerical error measured against the full-grid reference;
* :mod:`honest`         -- FAIL records that cannot exist without their artifacts
  (retention floor enforced in code);
* :mod:`workload`       -- a domain-free budgeted task that drives
  :mod:`tools.progress_meter` + stall ladder end to end.

Everything here is generic machinery: no physical or engineering vocabulary.
"""

from . import degrade, forensics_bundle, honest, workload  # noqa: F401

__all__ = ["degrade", "forensics_bundle", "honest", "workload"]
__version__ = "1.0.0"
SCHEMA = "survival/1"