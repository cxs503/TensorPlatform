"""Secret-leak scanning and redaction for every content exit of the work tree.

Why this module exists (first-principles, not tidiness)
------------------------------------------------------
Owner discipline says *a commit must never contain a secret*, and the same holds
for a report payload POSTed to the platform and for an artifact written to disk.
Before this module that discipline had **no mechanism that could prove it held**:
a leak that reaches ``git commit`` is irreversible (it lives in history), a leak
that reaches a published report is public, and a leak baked into an artifact is
shipped evidence. All three are the same failure at three different exits, so the
scanner is a single engine with three fail-closed exits.

Hard invariants (mechanically enforced by ``tests/secret_scan``):

* **No echo** — the scanner never prints, logs or persists a matched value; only
  a 2-character prefix, a mask, the length and a sha256 are emitted.
* **Zero secret literals in the repo** — the calibration corpus is generated at
  runtime from a seed, so the test fixtures are not themselves a leak.
* **Zero domain semantics** — every pattern, threshold, whitelist class, decoder
  setting and exit behaviour is injected from ``data/secret_scan/policy.json``.
* **Fail-closed** — unreadable policy, unparsable target or failed post-redaction
  verification is a BLOCK (``decision=unknown``), never "assume clean".
"""

from .model import GateError, Hit, ScanResult, Verdict  # noqa: F401
from .policy import Policy, PolicyError, load_policy  # noqa: F401
from .engine import Scanner  # noqa: F401
from .redact import redact_text  # noqa: F401

__all__ = [
    "GateError",
    "Hit",
    "Policy",
    "PolicyError",
    "ScanResult",
    "Scanner",
    "Verdict",
    "load_policy",
    "redact_text",
]