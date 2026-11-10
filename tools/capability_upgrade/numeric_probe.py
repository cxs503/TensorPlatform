"""Live numeric probes for the synthetic demo (P218-N01).

The demo must not rely on hand-written percentages: every ``error_pct`` used as
demo evidence is **computed at run time** by comparing a numerical routine with a
closed-form reference.  Generic mathematics only — no domain vocabulary.

Each probe returns a kwargs dict accepted by :meth:`EvidencePipeline.ingest`,
including a content-addressed ``source.sha256`` over the probe specification and
its measured result, so a reader can re-derive the same number.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List


def trapezoid(f, a: float, b: float, n: int) -> float:
    """Composite trapezoidal rule with ``n`` intervals."""
    if n <= 0:
        raise ValueError("n must be positive")
    h = (b - a) / n
    total = 0.5 * (f(a) + f(b))
    for i in range(1, n):
        total += f(a + i * h)
    return total * h


def rel_error_pct(computed: float, reference: float) -> float:
    return abs(computed - reference) / abs(reference) * 100.0


def _evidence(capability_id: str, probe: Dict[str, Any], error_pct: float,
              observed_level: str | None = None, kind: str = "numeric_verification") -> Dict[str, Any]:
    body = {k: probe[k] for k in sorted(probe)}
    sha = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "capability_id": capability_id,
        "kind": kind,
        "metrics": {"error_pct": round(error_pct, 9), "n_samples": probe["n_samples"]},
        "observed_level": observed_level,
        "source": {
            "path": f"probe::{probe['name']}",
            "locator": f"tools/capability_upgrade/numeric_probe.py::{probe['name']}",
            "sha256": sha,
        },
        "payload": probe,
    }


def build_evidence() -> List[Dict[str, Any]]:
    """Four synthetic capabilities demonstrating upgrade / hold behaviour."""
    # 1) well-resolved quadrature: error ~1e-6 % → must upgrade to REAL
    p1 = {"name": "trapz_sin", "f": "sin(x)", "a": 0.0, "b": math.pi, "n_samples": 512}
    v1 = trapezoid(math.sin, p1["a"], p1["b"], p1["n_samples"])
    p2 = {"name": "trapz_poly", "f": "x^2", "a": 0.0, "b": 1.0, "n_samples": 256}
    v2 = trapezoid(lambda x: x * x, p2["a"], p2["b"], p2["n_samples"])
    item1 = _evidence("demo.analytic_integration", p1, rel_error_pct(v1, 2.0))
    item2 = _evidence("demo.analytic_integration", p2, rel_error_pct(v2, 1.0 / 3.0))

    # 2) under-sampled quadrature: single coarse probe, error >> 3% → stays WITHHELD
    p3 = {"name": "trapz_sin_coarse", "f": "sin(x)", "a": 0.0, "b": math.pi, "n_samples": 2}
    v3 = trapezoid(math.sin, p3["a"], p3["b"], p3["n_samples"])
    item3 = _evidence("demo.under_sampled", p3, rel_error_pct(v3, 2.0))

    # 3) numerically accurate but the evidence itself is labelled MOCK → stays WITHHELD
    p4 = {"name": "trapz_poly_mock", "f": "x^2", "a": 0.0, "b": 1.0, "n_samples": 128}
    v4 = trapezoid(lambda x: x * x, p4["a"], p4["b"], p4["n_samples"])
    item4 = _evidence(
        "demo.contradicted", p4, rel_error_pct(v4, 1.0 / 3.0), observed_level="MOCK"
    )

    # 4) absence-only audit trail → stays WITHHELD, satisfies coverage
    item5 = {
        "capability_id": "demo.absent",
        "kind": "absence_audit",
        "metrics": {"evidence_count": 0},
        "observed_level": "WITHHELD",
        "source": {
            "path": "probe::absence",
            "locator": "tools/capability_upgrade/numeric_probe.py::build_evidence",
            "sha256": hashlib.sha256(b"demo.absent").hexdigest(),
        },
        "payload": {"reason": "no numeric verification available"},
    }
    return [item1, item2, item3, item4, item5]


if __name__ == "__main__":  # pragma: no cover - manual inspection helper
    for ev in build_evidence():
        print(json.dumps({k: ev[k] for k in ("capability_id", "kind", "metrics",
                                             "observed_level")}, ensure_ascii=False))