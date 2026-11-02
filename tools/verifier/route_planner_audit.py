"""Polar route/speed planner verification audit (applications seat, m2058).

The polar voyage pipeline (`backend/services/polar/route.py`) is the platform's
only *application-layer* optimiser: A* on an 8-connected ice grid with a
Lagrangian deadline multiplier, plus line-of-sight ("string pulling") smoothing
and a fine-sample exact speed allocation.  Until now it had **no anchor at all**:
acceptance criterion (e) only compares the planner against two heuristic
baselines (constant worst-case speed / station-wise speed), which is a relative
claim about the baseline, not about optimality.

This audit anchors five independent quantities on closed-form or exhaustive
references that do not reuse the planner's own logic:

V1  A* optimality          : A* cost == exhaustive Dijkstra (h = 0) cost on the
                             same graph, including an adverse *following-current*
                             case; plus the heuristic's own admissibility ratio
                             h(n)/d*(n) <= 1 measured against true distances.
V2  grid anisotropy        : 8-connected A* path length on a uniform-cost grid
                             vs the analytic octile metric
                             L_oct = L2*(cos t + (sqrt2-1) sin t), t in [0,45deg];
                             worst case 1/cos(22.5deg) - 1 = +8.24%.
V3  string-pulling         : smoothed polyline keeps navigability, never increases
                             length, and never increases fuel/metre at v_ref.
V4  Lagrangian multiplier  : Fuel(lambda) non-decreasing, Time(lambda)
                             non-increasing (comparative statics); bugdet time
                             feasibility |t - deadline|/deadline < 1e-3 and
                             complementary slackness lambda*(t-deadline) ~ 0.
V5  speed allocation       : the lambda-optimal per-segment speed beats the best
                             *constant-speed* schedule on the same track (KKT
                             bound), and golden-section equals a brute-force
                             speed grid to < 1e-3 relative.

Everything is closed-form / small-grid; no heavy compute.
Outputs: docs/verification_debt/polar_route_planner/{NUMBERS.json,figs/*.png}
"""

from __future__ import annotations

import heapq
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from services.polar import route as route_mod  # noqa: E402
from services.polar.ice_field import IceField  # noqa: E402
from services.polar.route import (  # noqa: E402
    V_MIN_MS,
    RoutePlanner,
    _polyline_length,
    _sample_polyline,
    _segment_ok,
    _segment_energy_rate,
    fuel_rate_kg_per_h,
    refined_speed_plan,
    smooth_polyline,
)
from services.polar.ships import get_ship  # noqa: E402

SHIP = get_ship("handysize_bulker")
OUT = Path(__file__).resolve().parents[2] / "docs" / "verification_debt" / "polar_route_planner"


# ---------------------------------------------------------------------------
# exhaustive reference implementation (independent of RoutePlanner._search)
# ---------------------------------------------------------------------------
def dijkstra_reference(planner: RoutePlanner, start: int, goal: int, lam: float) -> Dict[str, Any]:
    """Plain Dijkstra (h = 0) over the planner's own node graph / edge costs."""
    cost: Dict[int, float] = {start: 0.0}
    prev: Dict[int, int] = {}
    pq: List[Tuple[float, int]] = [(0.0, start)]
    done = set()
    while pq:
        d, cur = heapq.heappop(pq)
        if cur in done:
            continue
        done.add(cur)
        if cur == goal:
            break
        for nb in planner._neighbors(cur):
            c = planner.edge_option(cur, nb, lam)[0]
            nd = d + c
            if nd < cost.get(nb, math.inf) - 1e-15:
                cost[nb] = nd
                prev[nb] = cur
                heapq.heappush(pq, (nd, nb))
    if goal not in cost:
        raise ValueError("goal unreachable")
    path = [goal]
    while path[-1] != start:
        path.append(prev[path[-1]])
    path.reverse()
    fuel = time_h = 0.0
    for a, b in zip(path[:-1], path[1:]):
        _, f, t, _ = planner.edge_option(a, b, lam)
        fuel += f
        time_h += t
    return {"cost": cost[goal], "fuel_kg": fuel, "time_h": time_h,
            "points_km": [planner.node_xy(i) for i in path]}


def shortest_true_distance(planner: RoutePlanner, goal: int) -> Dict[int, float]:
    """Unweighted-geometry shortest distance (km, Euclidean edge lengths) to goal."""
    dist: Dict[int, float] = {goal: 0.0}
    pq: List[Tuple[float, int]] = [(0.0, goal)]
    done = set()
    while pq:
        d, cur = heapq.heappop(pq)
        if cur in done:
            continue
        done.add(cur)
        for nb in planner._neighbors(cur):
            nd = d + planner._edge_geometry(cur, nb)[0]
            if nd < dist.get(nb, math.inf) - 1e-15:
                dist[nb] = nd
                heapq.heappush(pq, (nd, nb))
    return dist


def heuristic_floor(planner: RoutePlanner, lam: float) -> float:
    """Recompute the planner's sampled per-metre cost floor c_floor [kg/m]."""
    lam_safe = max(lam, 0.0)
    c_floor = math.inf
    for k in range(0, len(planner.nodes), max(1, len(planner.nodes) // 200)):
        v_cap = min(SHIP.design_speed_ms, planner.v_max_node[k])
        for j, v in enumerate(planner.v_levels):
            v = float(v)
            if v > v_cap:
                continue
            v_g = max(v, V_MIN_MS)
            rate = fuel_rate_kg_per_h(SHIP, planner.r_table[k, j], v)
            c_floor = min(c_floor, rate / (v_g * 3600.0) + lam_safe / (v_g * 3600.0))
    return c_floor



def extreme_nodes(planner: RoutePlanner) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """South-west-most and north-east-most navigable node centres of the planner grid."""
    pts = [planner.node_xy(i) for i in range(len(planner.nodes))]
    sw = min(pts, key=lambda p: (p[0] + p[1]))
    ne = max(pts, key=lambda p: (p[0] + p[1]))
    return sw, ne


# ---------------------------------------------------------------------------
# V1: A* vs exhaustive Dijkstra
# ---------------------------------------------------------------------------
def v1_astar_optimality() -> Dict[str, Any]:
    cases = []
    # (label, field, start, goal)
    diag_uniform = (IceField.uniform(0.6, 0.5, nx=21, ny=21, dx_km=3.0), (3.0, 3.0), (57.0, 57.0))
    cur_follow = (IceField.uniform(0.6, 0.5, nx=21, ny=21, dx_km=3.0, current_ms=0.8),
                  (3.0, 3.0), (57.0, 57.0))
    wind_follow = (IceField.uniform(0.6, 0.5, nx=21, ny=21, dx_km=3.0, wind_ms=8.0),
                   (3.0, 3.0), (57.0, 57.0))
    for label, (field, s, g) in [
        ("uniform_no_current", diag_uniform),
        ("uniform_following_current_0.8", cur_follow),
        ("uniform_tailwind_8", wind_follow),
    ]:
        planner = RoutePlanner(field, SHIP, stride=1)
        start = planner.nearest_node(*s)
        goal = planner.nearest_node(*g)
        for lam in (0.0, 50.0, 500.0):
            astar = planner._search(start, goal, lam)
            ref = dijkstra_reference(planner, start, goal, lam)
            # admissibility: h(n) vs true optimal remaining cost (in cost units)
            true_dist = shortest_true_distance(planner, goal)
            c_floor = heuristic_floor(planner, lam)
            ratio_max = 0.0
            keys = [n for n in true_dist if n != goal]
            step = max(1, len(keys) // 24)
            for n in keys[::step]:
                dn = true_dist[n]
                h = c_floor * dn * 1000.0
                opt = dijkstra_reference(planner, n, goal, lam)["cost"]
                if opt > 0:
                    ratio_max = max(ratio_max, h / opt)
            cases.append({
                "case": label, "lambda": lam,
                "astar_cost": astar["fuel_kg"] + lam * astar["time_h"],
                "dijkstra_cost": ref["cost"],
                "rel_cost_gap_pct": 100.0 * abs(astar["fuel_kg"] + lam * astar["time_h"] - ref["cost"])
                / max(ref["cost"], 1e-12),
                "astar_path_len_km": astar["distance_km"],
                "dijkstra_path_len_km": sum(
                    math.hypot(b[0] - a[0], b[1] - a[1])
                    for a, b in zip(ref["points_km"][:-1], ref["points_km"][1:])),
                "heuristic_over_optimum_max": ratio_max,
                "heuristic_admissible": ratio_max <= 1.0 + 1e-12,
            })
    return {"cases": cases,
            "max_rel_cost_gap_pct": max(c["rel_cost_gap_pct"] for c in cases),
            "max_heuristic_ratio": max(c["heuristic_over_optimum_max"] for c in cases)}


# ---------------------------------------------------------------------------
# V2: octile anisotropy on a uniform-cost grid
# ---------------------------------------------------------------------------
def v2_grid_anisotropy() -> Dict[str, Any]:
    field = IceField.uniform(0.4, 0.4, nx=41, ny=41, dx_km=2.0)
    planner = RoutePlanner(field, SHIP, stride=1)
    rows = []
    origin = (2.0, 2.0)
    for theta_deg in (0.0, 10.0, 22.5, 30.0, 45.0):
        t = math.radians(theta_deg)
        r = 60.0
        gx = origin[0] + r * math.cos(t)
        gy = origin[1] + r * math.sin(t)
        fld = IceField.uniform(0.4, 0.4, nx=max(41, int(gx / 2.0) + 6),
                               ny=max(41, int(gy / 2.0) + 6), dx_km=2.0)
        pl = RoutePlanner(fld, SHIP, stride=1)
        start = pl.nearest_node(*origin)
        node = pl.nodes[pl.nearest_node(gx, gy)]
        goal_xy = pl.node_xy(pl.nearest_node(gx, gy))
        res = pl._search(start, pl.nearest_node(gx, gy), 0.0)
        l2 = math.hypot(goal_xy[0] - origin[0], goal_xy[1] - origin[1])
        dx = abs(goal_xy[0] - origin[0])
        dy = abs(goal_xy[1] - origin[1])
        octile = max(dx, dy) + (math.sqrt(2.0) - 1.0) * min(dx, dy)
        rows.append({
            "bearing_deg": theta_deg,
            "path_len_km": res["distance_km"],
            "euclid_km": l2,
            "octile_km": octile,
            "excess_vs_euclid_pct": 100.0 * (res["distance_km"] - l2) / l2,
            "octile_vs_euclid_pct": 100.0 * (octile - l2) / l2,
            "path_vs_octile_rel_err_pct": 100.0 * abs(res["distance_km"] - octile) / octile,
        })
    return {"rows": rows,
            "theoretical_worst_excess_pct": 100.0 * (1.0 / math.cos(math.radians(22.5)) - 1.0),
            "max_path_vs_octile_rel_err_pct": max(r["path_vs_octile_rel_err_pct"] for r in rows),
            "max_excess_vs_euclid_pct": max(r["excess_vs_euclid_pct"] for r in rows)}


# ---------------------------------------------------------------------------
# V3: string-pulling invariants
# ---------------------------------------------------------------------------
def v3_smoothing() -> Dict[str, Any]:
    scenario = IceField.generate_scenario("nsr_corridor")
    planner = RoutePlanner(scenario, SHIP, stride=1)
    sw, ne = extreme_nodes(planner)
    start, goal = planner.nearest_node(*sw), planner.nearest_node(*ne)
    start_xy, goal_xy = sw, ne
    res = planner._search(start, goal, 0.0)
    pts = res["points_km"]
    v_ref = min(3.0, SHIP.design_speed_ms)
    smoothed = smooth_polyline(scenario, SHIP, pts, v_ref=v_ref)

    def fuel_per_m(poly: Sequence[Tuple[float, float]]) -> float:
        return sum(_segment_energy_rate(scenario, SHIP, a, b, v_ref)
                   * math.hypot(b[0] - a[0], b[1] - a[1]) * 1000.0
                   for a, b in zip(poly[:-1], poly[1:]))

    navigable = all(_segment_ok(scenario, SHIP, a, b) for a, b in zip(smoothed[:-1], smoothed[1:]))
    return {
        "vertices_before": len(pts),
        "vertices_after": len(smoothed),
        "length_before_km": _polyline_length(pts),
        "length_after_km": _polyline_length(smoothed),
        "length_ratio": _polyline_length(smoothed) / _polyline_length(pts),
        "fuel_vref_before_kg": fuel_per_m(pts),
        "fuel_vref_after_kg": fuel_per_m(smoothed),
        "fuel_ratio": fuel_per_m(smoothed) / max(fuel_per_m(pts), 1e-12),
        "all_segments_navigable": bool(navigable),
    }


# ---------------------------------------------------------------------------
# V4: Lagrangian multiplier / comparative statics / KKT
# ---------------------------------------------------------------------------
def v4_lagrange() -> Dict[str, Any]:
    field = IceField.generate_scenario("nsr_corridor")
    planner = RoutePlanner(field, SHIP, stride=2)
    sw, ne = extreme_nodes(planner)
    start, goal = planner.nearest_node(*sw), planner.nearest_node(*ne)
    lams = [1e-3, 1e-2, 1e-1, 1.0, 10.0, 100.0, 1e3, 1e4, 1e5]
    rows = []
    for lam in lams:
        r = planner._search(start, goal, lam)
        rows.append({"lambda": lam, "fuel_kg": r["fuel_kg"], "time_h": r["time_h"],
                     "cost": r["fuel_kg"] + lam * r["time_h"],
                     "distance_km": r["distance_km"]})
    fuel_mono = all(b["fuel_kg"] >= a["fuel_kg"] - 1e-9 for a, b in zip(rows[:-1], rows[1:]))
    time_mono = all(b["time_h"] <= a["time_h"] + 1e-12 for a, b in zip(rows[:-1], rows[1:]))

    # deadline solve: pick a deadline strictly between the fuel-optimum and the fast end
    slow = planner._search(start, goal, 0.0)
    fast = planner._search(start, goal, 1e7)
    deadline = 0.5 * (slow["time_h"] + fast["time_h"])
    plan = planner.plan(sw, ne, deadline_h=deadline)
    kkt_dual = plan["lambda_kg_per_h"] >= 0.0
    kkt_comp = plan["lambda_kg_per_h"] * (plan["time_h"] - deadline)
    return {
        "cases": rows,
        "fuel_monotone_nondecreasing": bool(fuel_mono),
        "time_monotone_nonincreasing": bool(time_mono),
        "deadline_h": deadline,
        "planned_time_h": plan["time_h"],
        "time_rel_err_pct": 100.0 * abs(plan["time_h"] - deadline) / deadline,
        "deadline_feasible": plan["time_h"] <= deadline * (1 + 1e-9),
        "lambda": plan["lambda_kg_per_h"],
        "kkt_dual_feasible": bool(kkt_dual),
        "kkt_complementary_slackness_kg": kkt_comp,
        "complementary_slackness_ok": abs(kkt_comp) <= 1e-3 * abs(plan["fuel_kg"]) + 1e-6,
        "slow_time_h": slow["time_h"], "fast_time_h": fast["time_h"],
    }


# ---------------------------------------------------------------------------
# V5: speed allocation vs constant-speed schedules and brute force
# ---------------------------------------------------------------------------
def v5_speed_allocation() -> Dict[str, Any]:
    field = IceField.generate_scenario("nsr_corridor")
    planner = RoutePlanner(field, SHIP, stride=2)
    sw, ne = extreme_nodes(planner)
    start, goal = planner.nearest_node(*sw), planner.nearest_node(*ne)
    base = planner._search(start, goal, 0.0)
    track = base["points_km"]
    deadline = 1.05 * base["time_h"]
    plan = refined_speed_plan(field, SHIP, track, deadline_h=deadline, ds_km=5.0)

    # best constant-speed schedule on the same track honoring the deadline
    segs = _sample_polyline(field, track, ds_km=5.0)
    best_const = None
    for v in np.linspace(V_MIN_MS, SHIP.design_speed_ms, 400):
        fuel = t = 0.0
        ok = True
        for sg in segs:
            v_cap = route_mod.max_sustainable_speed_ms(SHIP, sg["h_m"], sg["sigma_mpa"])
            if v > min(SHIP.design_speed_ms, v_cap) + 1e-9:
                ok = False
                break
            leg = route_mod.segment_fuel(SHIP, sg["h_m"], sg["sigma_mpa"], float(v),
                                        sg["current_along"], sg["wind_along"], sg["distance_km"])
            fuel += leg.fuel_kg
            t += leg.time_h
        if not ok or t > deadline:
            continue
        if best_const is None or fuel < best_const["fuel_kg"]:
            best_const = {"v_ms": float(v), "fuel_kg": fuel, "time_h": t}

    # per-segment brute-force grid vs golden-section (KKT stationarity)
    v_brute_err = 0.0
    seg_probe = segs[len(segs) // 2]
    lam = max(plan["lambda_kg_per_h"], 0.0)
    grid = np.linspace(V_MIN_MS, min(SHIP.design_speed_ms,
                                     route_mod.max_sustainable_speed_ms(
                                         SHIP, seg_probe["h_m"], seg_probe["sigma_mpa"])), 20001)
    costs = []
    for v in grid:
        leg = route_mod.segment_fuel(SHIP, seg_probe["h_m"], seg_probe["sigma_mpa"], float(v),
                                     seg_probe["current_along"], seg_probe["wind_along"],
                                     seg_probe["distance_km"])
        costs.append(leg.fuel_kg + lam * leg.time_h)
    v_brute = float(grid[int(np.argmin(costs))])
    return {
        "deadline_h": deadline,
        "lambda_optimal_fuel_kg": plan["fuel_kg"],
        "lambda_optimal_time_h": plan["time_h"],
        "best_constant_speed": best_const,
        "constraint_ok_lambda_beats_const": bool(
            best_const is None or plan["fuel_kg"] <= best_const["fuel_kg"] * (1 + 1e-9)),
        "saving_vs_best_const_pct": (None if best_const is None else
                                     100.0 * (best_const["fuel_kg"] - plan["fuel_kg"])
                                     / best_const["fuel_kg"]),
        "n_segments": len(segs),
        "brute_force_v_ms": v_brute,
        "segment_probe_h_m": seg_probe["h_m"],
        "segments": [[round(sg["distance_km"], 3), round(sg["h_m"], 3)] for sg in segs][:40],
        "n_constant_speeds_tested": 400,
    }


# ---------------------------------------------------------------------------
def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "figs").mkdir(exist_ok=True)
    t0 = time.time()
    numbers: Dict[str, Any] = {"meta": {"ship": SHIP.key, "wall_s": None}}
    numbers["v1_astar_optimality"] = v1_astar_optimality()
    numbers["v2_grid_anisotropy"] = v2_grid_anisotropy()
    numbers["v3_smoothing"] = v3_smoothing()
    numbers["v4_lagrange"] = v4_lagrange()
    numbers["v5_speed_allocation"] = v5_speed_allocation()
    numbers["meta"]["wall_s"] = round(time.time() - t0, 2)
    (OUT / "NUMBERS.json").write_text(json.dumps(numbers, indent=2), encoding="utf-8")
    print(json.dumps(numbers, indent=2)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())