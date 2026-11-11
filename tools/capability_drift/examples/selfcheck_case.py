"""Self-check verification case for the shipped demo registry (synthetic, zero domain semantics).

Used by ``config/capability_drift/demo`` and the CI workflow so the monitor has a
real, always-installed case to run. Numbers come from an actual damped Jacobi
iteration — not from constants.
"""
from __future__ import annotations

from tools.capability_drift.plugin import record

RELAX = 0.60  # blame anchor: this line decides demo_energy / demo_peak


def damped_energy(n: int = 16, relax: float = RELAX, iters: int = 40) -> tuple[float, float]:
    h = 1.0 / (n + 1)
    u = [0.0] * (n + 2)
    for _ in range(iters):
        prev = u
        cur = prev[:]
        for i in range(1, n + 1):
            cur[i] = (1.0 - relax) * prev[i] + relax * 0.5 * (prev[i - 1] + prev[i + 1] + h * h)
        u = cur
    energy = 0.5 * h * sum(((u[i + 1] - u[i]) / h) ** 2 for i in range(n + 1))
    peak = max(abs(x) for x in u)
    return energy, peak


def test_demo_energy():
    energy, peak = damped_energy()
    record("demo_energy", energy)
    record("demo_peak", peak)
    assert energy > 0.0