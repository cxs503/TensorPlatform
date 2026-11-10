"""Long-run job survival layer: crash forensics + bit-idempotent resume + degradation ladder.

Scope (deliberately domain-free, per TensorPlatform iron rule "zero domain semantics"):
this package knows nothing about any physical/engineering discipline.  It provides
generic machinery that any long-running compute job (>30 min) can wrap itself in:

* :mod:`tools.longrun.guardian`  -- ptrace-attached supervisor that detects fatal
  signals, freezes the dying process, captures a *real* ELF core with ``gdb gcore``
  and symbolizes the crashing stack (native ``bt`` + ``addr2line``).
* :mod:`tools.longrun.forensics` -- in-process flight recorder (faulthandler
  all-thread stack snapshots + memory/state journal), failure-mode classifier and
  a forensic-bundle completeness scorer.
* :mod:`tools.longrun.checkpoint` -- atomic, digest-verified, chained checkpoint
  store (bit level: per-array sha256 over ``tobytes()`` with dtype/shape).
* :mod:`tools.longrun.resume`    -- idempotent resume controller + audit journal
  (same input + same checkpoint -> same output, key = sha256(input,ckpt,config)).
* :mod:`tools.longrun.ladder`    -- resource degradation ladder
  (precision -> grid -> timestep -> graceful exit) driven by a pressure probe.
* :mod:`tools.longrun.supervise` -- end-to-end driver tying the four together and
  emitting a machine-readable report.

Public API::

    from tools.longrun import supervise, checkpoint, ladder, forensics
    report = supervise.supervise(job_cfg, workdir=...)
"""

from . import checkpoint, forensics, guardian, ladder, resume  # noqa: F401
from .job import SyntheticIterativeJob  # noqa: F401

__all__ = [
    "checkpoint",
    "forensics",
    "guardian",
    "ladder",
    "resume",
    "SyntheticIterativeJob",
]
__version__ = "1.0.0"
SCHEMA = "longrun/1"