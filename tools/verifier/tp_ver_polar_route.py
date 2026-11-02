#!/usr/bin/env python3
"""EDIT_WHITELIST: tools/verifier/, tests/, docs/verification/, artifacts/verification/, backend/services/polar/

Polar (ice) voyage planner -- analytic / first-principles anchor suite  (m1853, tp-ver seat)

Scope
-----
``backend/services/polar/route.py`` ships a real A* least-cost passage planner
(``RoutePlanner``) over a coarsened ice-field grid with a Lagrangian deadline
multiplier ``lam`` [kg/h] on passage time.  It is a *headline* capability of the
ice-navigation domain (``POST /api/v1/ice-navigation/route``), yet no document
in the repo judges it: the ice-domain verification work to date covers level-ice
resistance, the brash channel, convoy spacing, extreme ice loads and ice-class
optimisation -- not the search itself.

This suite judges the planner against **analytic and self-consistent baselines
only** -- no memorised literature table values are used anywhere:

===========================================  =========================================
R1  ASTAR_OPTIMALITY                         A* total cost == exact Dijkstra optimum
R2  HEURISTIC_ADMISSIBLE                     h(n) <= exact remaining cost for every n
R3  LEG_ACCOUNTING                           reported fuel/time/distance == sum of legs
R4  ENERGY_ACCOUNTING                        reported brake energy == sum of legs
R5  GREAT_CIRCLE_EFFICIENCY                  ice-free detour ratio vs straight line
R6  ICE_MONOTONE                             fuel up / speed cap down with ice thickness
R7  DEADLINE_BRACKET                         deadline plans stay inside [slow, deadline]
R8  INFEASIBLE_DEADLINE_RAISES               impossible deadline fails loudly, no fake plan
R9  NAVIGABILITY_HONESTY                     power-infeasible nodes are never routed through
R10 SPEED_CAP_RESPECT                        every leg speed <= min(design, v_max(node))
===========================================  =========================================

Usage
-----
    python3 tools/verifier/tp_ver_polar_route.py            # run, write artifacts
    python3 tools/verifier/tp_ver_polar_route.py --json     # machine summary on stdout

The module is importable: :func:`run_all` returns the verdict ledger so that
``tests/test_polar_route_anchor_m1853.py`` can assert on it in CI.
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import numpy as np  # noqa: E402  (after sys.path setup)

from services.polar.ice_field import IceField  # noqa: E402
from services.polar.route import (  # noqa: E402
    V_MIN_MS,
    RoutePlanner,
    brake_power_kw,
    max_sustainable_speed_ms,
    segment_fuel,
)
from services.polar.ships import get_ship  # noqa: E402

ARTIFACT_DIR = ROOT / "artifacts" / "verification"

SHIP_KEY = "handysize_bulker"
GRID = dict(nx=21, ny=13, dx_km=4.0, stride=1)
START_KM = (8.0, 16.0)
GOAL_KM = (72.0, 32.0)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _planner(h_m: float, sigma_mpa: float, *, current_ms: float = 0.0,
             wind_ms: float = 0.0, ship_key: str = SHIP_KEY,
             nx: int = None, ny: int = None, dx_km: float = None,
             stride: int = None) -> RoutePlanner:
    nx = GRID["nx"] if nx is None else nx
    ny = GRID["ny"] if ny is None else ny
    dx_km = GRID["dx_km"] if dx_km is None else dx_km
    stride = GRID["stride"] if stride is None else stride
    field = IceField.uniform(h_m, sigma_mpa, nx=nx, ny=ny, dx_km=dx_km,
                             current_ms=current_ms, wind_ms=wind_ms)
    return RoutePlanner(field, get_ship(ship_key), stride=stride)


def _node_ice(planner: RoutePlanner, idx: int) -> Tuple[float, float]:
    """(h_m, sigma_mpa) of the node, read back from the ice field itself."""
    iy, ix = planner.nodes[idx]
    return float(planner.field.h[iy, ix]), float(planner.field.sigma[iy, ix])


def _node_wind_current(planner: RoutePlanner, idx: int) -> Tuple[float, float, float, float]:
    iy, ix = planner.nodes[idx]
    f = planner.field
    return (float(f.u_current[iy, ix]), float(f.v_current[iy, ix]),
            float(f.u_wind[iy, ix]), float(f.v_wind[iy, ix]))


def _edge_dir(planner: RoutePlanner, a: int, b: int) -> Tuple[float, float, float]:
    """Distance [km] and unit direction of edge a->b, computed here (not reused)."""
    xa, ya = planner.node_xy(a)
    xb, yb = planner.node_xy(b)
    d = math.hypot(xb - xa, yb - ya)
    return d, (xb - xa) / d, (yb - ya) / d


def exact_leg(planner: RoutePlanner, a: int, b: int, v_water_ms: float) -> Dict[str, float]:
    """Canonical single-leg accounting via ``segment_fuel`` (independent of A*)."""
    d_km, ux, uy = _edge_dir(planner, a, b)
    h, sigma = _node_ice(planner, a)
    uc, vc, uw, vw = _node_wind_current(planner, a)
    leg = segment_fuel(planner.ship, h, sigma, v_water_ms,
                       current_along_ms=uc * ux + vc * uy,
                       wind_along_ms=uw * ux + vw * uy,
                       distance_km=d_km)
    return {"fuel_kg": leg.fuel_kg, "time_h": leg.time_h,
            "distance_km": leg.distance_km, "brake_energy_mj": leg.brake_energy_mj}


def recompute_totals(planner: RoutePlanner, plan: Dict[str, Any]) -> Dict[str, float]:
    tot = {"fuel_kg": 0.0, "time_h": 0.0, "distance_km": 0.0, "brake_energy_mj": 0.0}
    path: List[int] = plan["path"]
    for a, b, v in zip(path[:-1], path[1:], plan["speeds_ms"]):
        leg = exact_leg(planner, a, b, float(v))
        for k in tot:
            tot[k] += leg[k]
    return tot


def adjacency(planner: RoutePlanner) -> Dict[int, List[int]]:
    return {i: planner._neighbors(i) for i in range(len(planner.nodes))}


def dijkstra(planner: RoutePlanner, start: int, goal: int, lam: float,
             adj: Optional[Dict[int, List[int]]] = None,
             reverse: bool = False) -> Dict[int, float]:
    """Exact minimum cost from ``goal`` to every node (or start->all if not reverse).

    In the reversed role the edge cost of ``x -> y`` is taken as
    ``edge_option(y, x)`` because edge costs are departure-node dependent.
    """
    adj = adj or adjacency(planner)
    if reverse:
        radj: Dict[int, List[int]] = {i: [] for i in adj}
        for a, nbrs in adj.items():
            for b in nbrs:
                radj[b].append(a)
        adj = radj

        def cost(a: int, b: int) -> float:  # a->b in reversed graph == b->a forward
            return planner.edge_option(b, a, lam)[0]
    else:
        def cost(a: int, b: int) -> float:
            return planner.edge_option(a, b, lam)[0]

    dist = {goal: 0.0}
    pq: List[Tuple[float, int]] = [(0.0, goal)]
    while pq:
        d, cur = heapq.heappop(pq)
        if d > dist.get(cur, math.inf) + 1e-12:
            continue
        for nb in adj.get(cur, []):
            nd = d + cost(cur, nb)
            if nd < dist.get(nb, math.inf) - 1e-12:
                dist[nb] = nd
                heapq.heappush(pq, (nd, nb))
    return dist


def heuristic_replica(planner: RoutePlanner, lam: float) -> Optional[float]:
    """Replicate ``_search``'s per-metre lower bound (kg per metre).

    Replicated from the planner *on purpose*: the point of R2 is to check that
    the bound the search actually uses never exceeds the exact remaining cost,
    so the formula has to be reproduced here and then confronted with Dijkstra.
    """
    from services.polar.resistance import wind_resistance

    c_floor = math.inf
    n = len(planner.nodes)
    c_max = float(max(np.abs(planner.u_cur).max(), np.abs(planner.v_cur).max()))
    w_max = float(max(np.abs(planner.u_wind).max(), np.abs(planner.v_wind).max()))
    v_hi = float(planner.v_levels.max()) + c_max
    w_drag = abs(wind_resistance(planner.ship, v_hi, w_max)) if w_max > 0.0 else 0.0
    for k in range(0, n, max(1, n // 200)):
        for j, v in enumerate(planner.v_levels):
            v = float(v)
            r_lb = float(planner.r_table[k, j]) - w_drag
            rate_lb = brake_power_kw(planner.ship, r_lb, v) * planner.ship.sfoc_g_kwh / 1000.0
            c_floor = min(c_floor, (rate_lb + lam) / ((v + c_max) * 3600.0))
    return None if not math.isfinite(c_floor) else c_floor


def _rel(a: float, b: float) -> float:
    denom = max(abs(a), abs(b), 1e-12)
    return abs(a - b) / denom


# ---------------------------------------------------------------------------
# judgments
# ---------------------------------------------------------------------------

def judge_all(lam: float = 0.0) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    def add(cid: str, name: str, ok: bool, measured: Any, criterion: str,
            note: str = "") -> None:
        out.append({"id": cid, "name": name, "verdict": "PASS" if ok else "FAIL",
                    "measured": measured, "criterion": criterion, "note": note})

    # ---------------- R1 / R2 : search optimality + admissibility -------------
    pl = _planner(0.9, 1.2, current_ms=0.25, wind_ms=4.0)
    start, goal = pl.nearest_node(*START_KM), pl.nearest_node(*GOAL_KM)
    plan = pl._search(start, goal, lam)
    cost_astar = plan["fuel_kg"] + lam * plan["time_h"]
    dist_from_goal = dijkstra(pl, start, goal, lam, reverse=True)

    # exact optimum: least fuel among equal-cost ties is not unique, so compare
    # the Lagrangian total, which A* is supposed to minimise.
    def lag_total(p: Dict[str, Any]) -> float:
        return p["fuel_kg"] + lam * p["time_h"]

    exact = dist_from_goal.get(start, math.inf)
    r1_err = _rel(cost_astar, exact) if math.isfinite(exact) else math.inf
    add("R1", "A* optimum == exact Dijkstra optimum", r1_err <= 1e-9,
        {"astar_cost": cost_astar, "dijkstra_cost": exact, "rel_err": r1_err},
        "rel err <= 1e-9", "lam = %g kg/h, %d nodes" % (lam, len(pl.nodes)))

    c_floor = heuristic_replica(pl, lam)
    worst = None
    if c_floor is not None:
        for idx, remaining in dist_from_goal.items():
            x, y = pl.node_xy(idx)
            gx, gy = pl.node_xy(goal)
            hval = c_floor * math.hypot(gx - x, gy - y) * 1000.0
            slack = hval - remaining
            if worst is None or slack > worst[1]:
                worst = (idx, slack, hval, remaining)
    adm_ok = bool(worst) and worst[1] <= 1e-6 * max(1.0, abs(worst[3]))
    add("R2", "heuristic never over-estimates remaining cost", adm_ok,
        {"worst_slack_kg": None if not worst else worst[1],
         "h_kg": None if not worst else worst[2],
         "exact_remaining_kg": None if not worst else worst[3],
         "c_floor_kg_per_m": c_floor},
        "h(n) - remaining(n) <= 1e-6 * remaining",
        "sampled over every node reachable to the goal")

    # ---------------- R3 / R4 : leg accounting ------------------------------
    tot = recompute_totals(pl, plan)
    closing = {k: _rel(plan[k], tot[k]) for k in ("fuel_kg", "time_h", "distance_km")}
    add("R3", "reported totals == sum of canonical legs",
        max(closing.values()) <= 1e-9,
        {"reported": {k: plan[k] for k in closing}, "recomputed": tot,
         "rel_err": closing}, "rel err <= 1e-9",
        "legs recomputed with segment_fuel(), not with _search internals")

    energy_err = _rel(plan["brake_energy_mj"], tot["brake_energy_mj"])
    add("R4", "reported brake energy == sum of canonical legs", energy_err <= 1e-6,
        {"reported_mj": plan["brake_energy_mj"], "recomputed_mj": tot["brake_energy_mj"],
         "rel_err": energy_err, "criterion_rel_err": 1e-6},
        "rel err <= 1e-6",
        "brake energy must use resistance at the *chosen* speed (incl. wind)")

    # ---------------- R5 : great-circle efficiency -------------------------
    pl_free = _planner(0.0, 0.0)
    free = pl_free._search(pl_free.nearest_node(*START_KM), pl_free.nearest_node(*GOAL_KM), 0.0)
    x0, y0 = free["points_km"][0]
    x1, y1 = free["points_km"][-1]
    chord = math.hypot(x1 - x0, y1 - y0)
    ratio = free["distance_km"] / chord
    add("R5", "ice-free least-fuel route stays near the straight line", ratio <= 1.10,
        {"route_km": free["distance_km"], "chord_km": chord, "detour_ratio": ratio},
        "detour ratio <= 1.10 (8-neighbour grid)", "uniform h=0, no current/wind")

    # ---------------- R6 : monotonicity in ice thickness --------------------
    scan = []
    for h in (0.2, 0.4, 0.6, 0.8):
        p = _planner(h, 1.2)
        s, g = p.nearest_node(*START_KM), p.nearest_node(*GOAL_KM)
        r = p._search(s, g, 0.0)
        scan.append({"h_m": h, "fuel_kg": r["fuel_kg"], "time_h": r["time_h"],
                     "v_max_mean": float(np.mean(p.v_max_node)),
                     "fuel_per_km": r["fuel_kg"] / r["distance_km"]})
    fuel_up = all(b["fuel_per_km"] > a["fuel_per_km"]
                  for a, b in zip(scan[:-1], scan[1:]))
    cap_down = all(b["v_max_mean"] <= a["v_max_mean"] + 1e-12
                   for a, b in zip(scan[:-1], scan[1:]))
    add("R6", "fuel/km rises and speed cap falls with ice thickness",
        bool(fuel_up and cap_down), {"scan": scan, "fuel_monotone": fuel_up,
                                     "cap_monotone": cap_down},
        "strict fuel/km increase, non-increasing v_max", "all other inputs fixed")

    # ---------------- R7 / R8 : deadline handling --------------------------
    slow = pl._search(start, goal, 0.0)
    fast = pl._search(start, goal, 1e7)
    midpoint = 0.5 * (slow["time_h"] + fast["time_h"])
    tight = pl.plan(START_KM, GOAL_KM, deadline_h=midpoint)
    bracket_ok = (tight["deadline_binding"] is True
                  and tight["time_h"] <= midpoint * (1 + 1e-3)
                  and tight["time_h"] <= slow["time_h"] + 1e-9
                  and tight["time_h"] >= fast["time_h"] - 1e-9
                  and tight["fuel_kg"] >= slow["fuel_kg"] - 1e-9)
    add("R7", "deadline plan sits inside [slowest, deadline]", bool(bracket_ok),
        {"slow_time_h": slow["time_h"], "fast_time_h": fast["time_h"],
         "deadline_h": midpoint, "plan_time_h": tight["time_h"],
         "slow_fuel_kg": slow["fuel_kg"], "plan_fuel_kg": tight["fuel_kg"],
         "deadline_binding": tight["deadline_binding"]},
        "time_h <= deadline, fuel >= slow-fuel, binding flag set")

    raised, detail = False, ""
    try:
        pl.plan(START_KM, GOAL_KM, deadline_h=fast["time_h"] * 0.9)
    except ValueError as exc:                     # noqa: PERF203 - explicit
        raised, detail = True, str(exc)[:160]
    add("R8", "impossible deadline raises instead of fabricating a passage",
        raised, {"raised_value_error": raised, "message": detail},
        "ValueError required", "0.9 x fastest achievable passage time")

    # ---------------- R9 : navigability honesty ----------------------------
    ship9 = get_ship(SHIP_KEY)
    sigma9 = 8.0                      # heavy-ice strength: power becomes binding
    h_crit = None
    for h in np.arange(0.10, ship9.h_max_m + 1e-9, 0.01):
        if max_sustainable_speed_ms(ship9, float(h), sigma9) <= 0.0:
            h_crit = float(h)
            break
    h_base = 0.3
    honest: Dict[str, Any]
    if h_crit is None:
        honest = {"skipped": "no power-infeasible thickness below h_max for this hull",
                  "h_max_m": ship9.h_max_m}
        r9_ok = True
    else:
        field = IceField.uniform(h_base, sigma9, nx=GRID["nx"], ny=GRID["ny"],
                                 dx_km=GRID["dx_km"])
        nx = GRID["nx"]
        mid = nx // 2
        field.h[:, mid] = h_crit          # power-infeasible barrier, one column wide
        pl9 = RoutePlanner(field, ship9, stride=GRID["stride"])
        admitted = int(np.sum(pl9.v_max_node <= 0.0))
        crossed = []
        try:
            res9 = pl9._search(pl9.nearest_node(*START_KM), pl9.nearest_node(*GOAL_KM), 0.0)
            crossed = [i for i in res9["path"] if float(pl9.v_max_node[i]) <= 0.0]
            delivered = True
        except ValueError as exc:
            delivered, res9 = False, str(exc)[:120]
        center_km = float(field.cell_center(0, mid)[0])
        blocked_any = any(abs(pl9.node_xy(i)[0] - center_km) < 1e-9 for i in
                          (res9["path"] if isinstance(res9, dict) else []))
        r9_ok = admitted == 0 and not crossed
        honest = {"h_crit_m": h_crit, "h_base_m": h_base, "sigma_mpa": sigma9,
                  "admitted_zero_power_nodes": admitted,
                  "route_crossed_blocked": crossed, "route_found": delivered,
                  "route_used_barrier_column": blocked_any,
                  "barrier_km_x": center_km}
    add("R9", "power-infeasible ice is never routed through", bool(r9_ok), honest,
        "zero nodes with v_max == 0 admitted; no path node inside the barrier",
        "barrier = ice thick enough that R(v_min)*v_min/eta > P_avail; "
        "the barrier must either be excluded or make the plan raise")

    # ---------------- R10 : speed cap -------------------------------------
    worst_cap = None
    for a, b, v in zip(plan["path"][:-1], plan["path"][1:], plan["speeds_ms"]):
        cap = min(pl.ship.design_speed_ms, float(pl.v_max_node[a]))
        excess = float(v) - cap
        if worst_cap is None or excess > worst_cap["excess"]:
            worst_cap = {"node": int(a), "v_used": float(v), "v_cap": cap,
                         "excess": excess}
    add("R10", "leg speeds respect the per-node sustainable speed cap",
        bool(worst_cap) and worst_cap["excess"] <= 1e-9, worst_cap,
        "v_used <= min(design speed, v_max(node)) + 1e-9")

    return out


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------

def make_figure(path: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:                                    # pragma: no cover
        return False

    pl = _planner(0.9, 1.2, current_ms=0.25, wind_ms=4.0)
    plan = pl._search(pl.nearest_node(*START_KM), pl.nearest_node(*GOAL_KM), 0.0)

    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.2))

    ax = axes[0]
    mesh = ax.pcolormesh(pl.field.x0_km + np.arange(pl.field.nx) * pl.field.dx_km,
                         pl.field.y0_km + np.arange(pl.field.ny) * pl.field.dy_km,
                         pl.field.h, shading="auto", cmap="Blues")
    px = [p[0] for p in plan["points_km"]]
    py = [p[1] for p in plan["points_km"]]
    ax.plot(px, py, "-o", ms=3, lw=1.4, color="crimson", label="A* least-fuel route")
    xs = [START_KM[0], GOAL_KM[0]]
    ys = [START_KM[1], GOAL_KM[1]]
    ax.plot(xs, ys, "--", lw=1.0, color="black", label="straight line (chord)")
    ax.set_xlabel("x [km]")
    ax.set_ylabel("y [km]")
    ax.set_title("Uniform h=0.9 m, sigma=1.2 MPa")
    ax.legend(fontsize=7)
    fig.colorbar(mesh, ax=ax, label="ice thickness h [m]")

    ax = axes[1]
    hs = np.arange(0.1, 0.95, 0.1)
    fuels, times = [], []
    for h in hs:
        p = _planner(float(h), 1.2)
        r = p._search(p.nearest_node(*START_KM), p.nearest_node(*GOAL_KM), 0.0)
        fuels.append(r["fuel_kg"])
        times.append(r["time_h"])
    ax.plot(hs, fuels, "-s", color="tab:blue", label="fuel [kg]")
    ax.set_xlabel("uniform ice thickness h [m]")
    ax.set_ylabel("fuel [kg]")
    ax2 = ax.twinx()
    ax2.plot(hs, times, "-^", color="tab:orange", label="passage time [h]")
    ax2.set_ylabel("passage time [h]")
    ax.set_title("Monotone response to ice thickness")
    lines = ax.get_lines() + ax2.get_lines()
    ax.legend(lines, [ln.get_label() for ln in lines], fontsize=7)

    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return True


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------

def run_all(write_artifacts: bool = True) -> Dict[str, Any]:
    verdicts = judge_all()
    n_pass = sum(1 for v in verdicts if v["verdict"] == "PASS")
    ledger = {
        "suite": "tp_ver_polar_route",
        "mission": 1853,
        "subject": "backend/services/polar/route.py::RoutePlanner (A* ice passage planner)",
        "ship": SHIP_KEY,
        "grid": GRID,
        "judgments": verdicts,
        "summary": {"n": len(verdicts), "pass": n_pass,
                    "fail": len(verdicts) - n_pass,
                    "all_pass": n_pass == len(verdicts)},
    }
    if write_artifacts:
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        (ARTIFACT_DIR / "polar_route_anchor_m1853.json").write_text(
            json.dumps(ledger, indent=2, ensure_ascii=False))
        fig_ok = make_figure(ARTIFACT_DIR / "fig_polar_route_m1853.png")
        ledger["figure"] = str(ARTIFACT_DIR / "fig_polar_route_m1853.png") if fig_ok else None
    return ledger


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="print the ledger as JSON")
    ap.add_argument("--no-artifacts", action="store_true")
    args = ap.parse_args(argv)
    ledger = run_all(write_artifacts=not args.no_artifacts)
    if args.json:
        print(json.dumps(ledger, indent=2, ensure_ascii=False))
    else:
        for v in ledger["judgments"]:
            print(f"{v['verdict']:4s} {v['id']:>3s}  {v['name']}")
        s = ledger["summary"]
        print(f"\n{s['pass']}/{s['n']} PASS  ->  {'ALL PASS' if s['all_pass'] else 'FAILURES PRESENT'}")
    return 0 if ledger["summary"]["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())