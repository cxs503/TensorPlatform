"""EDIT_WHITELIST: tools/verifier/, tests/, docs/verification/, artifacts/verification/, backend/services/polar/

Regression gate for the polar (ice) passage planner -- mission m1853.

``tools/verifier/tp_ver_polar_route.py`` holds the anchor suite (analytic /
self-consistent baselines only).  This module re-runs it in CI and pins the two
defects the suite caught, so they cannot come back silently:

* brake energy reported by ``RoutePlanner._search`` used the resistance of the
  *slowest* speed level instead of the speed the leg is sailed at;
* cells too heavy for the ship to hold ``V_MIN_MS`` were admitted as navigable
  nodes and then sailed at ``V_MIN_MS`` anyway, i.e. the planner produced a
  passage through ice the hull cannot move in.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for p in (str(BACKEND), str(ROOT)):   # ROOT last -> `tools` is the repo package
    if p in sys.path:
        sys.path.remove(p)
    sys.path.insert(0, p)

from services.polar.ice_field import IceField          # noqa: E402
from services.polar.route import RoutePlanner, max_sustainable_speed_ms  # noqa: E402
from services.polar.ships import get_ship              # noqa: E402
from tools.verifier import tp_ver_polar_route as ver   # noqa: E402


@pytest.fixture(scope="module")
def ledger():
    return ver.run_all(write_artifacts=False)


def test_all_anchor_judgments_pass(ledger):
    failing = [j for j in ledger["judgments"] if j["verdict"] != "PASS"]
    assert not failing, [(j["id"], j["name"], j["measured"]) for j in failing]
    assert ledger["summary"]["pass"] == ledger["summary"]["n"] == 10


def test_brake_energy_uses_sailed_speed(ledger):
    """R4: reported brake energy must close against per-leg accounting."""
    r4 = next(j for j in ledger["judgments"] if j["id"] == "R4")
    assert r4["measured"]["rel_err"] <= 1e-6


def test_astar_stays_optimal(ledger):
    """R1: the search must still match the exact Dijkstra optimum."""
    r1 = next(j for j in ledger["judgments"] if j["id"] == "R1")
    assert r1["measured"]["rel_err"] <= 1e-9


def test_power_infeasible_ice_is_excluded_from_the_graph():
    """R9: cells the ship cannot sail in must not become planner nodes."""
    ship = get_ship(ver.SHIP_KEY)
    sigma = 8.0
    h_crit = None
    for h in np.arange(0.10, ship.h_max_m + 1e-9, 0.01):
        if max_sustainable_speed_ms(ship, float(h), sigma) <= 0.0:
            h_crit = float(h)
            break
    if h_crit is None:                                  # pragma: no cover
        pytest.skip("no power-infeasible thickness below h_max for this hull")

    field = IceField.uniform(0.3, sigma, nx=21, ny=13, dx_km=4.0)
    field.h[:, 10] = h_crit
    planner = RoutePlanner(field, ship, stride=1)

    assert float(np.min(planner.v_max_node)) > 0.0
    barrier_x = float(field.cell_center(0, 10)[0])
    assert all(abs(planner.node_xy(i)[0] - barrier_x) > 1e-9 for i in range(len(planner.nodes)))

    # the barrier spans the whole grid height, so either the plan honestly
    # refuses to exist (ValueError) or it goes around the column -- what it must
    # never do is sail through a cell the hull cannot move in.
    try:
        res = planner._search(planner.nearest_node(*ver.START_KM),
                              planner.nearest_node(*ver.GOAL_KM), 0.0)
    except ValueError as exc:
        assert "no navigable route" in str(exc)
        return
    assert res["fuel_kg"] > 0.0
    assert all(abs(planner.node_xy(i)[0] - barrier_x) > 1e-9 for i in res["path"])