"""Energy-optimal route and speed schedule on a gridded ice field.

The planner solves the *joint* route + speed problem

    minimise   sum_edges [ fuel(v_e, ice_e) * ds_e ]
    subject to sum_edges [ ds_e / v_e ] <= deadline,
               R(v_e) v_e / eta <= 0.85 P_brake   (power the ship actually has),
               h_e <= ship.h_max_m                (ice operability limit)

with an A*/Dijkstra search on the ice-field grid (8-connected) whose edge cost
is the Lagrangian ``fuel + lambda * time``.  ``lambda`` [kg fuel per hour] is the
single Lagrange multiplier of the deadline constraint and is found by bisection:
``lambda = 0`` minimises fuel (slowest feasible speeds), ``lambda -> inf``
minimises passage time.  For a *fixed* route the per-cell Lagrangian problem is
convex in ``v`` (fuel grows with ``v``, time falls as ``1/v``) so the optimum
speed schedule has an interior solution; the search path is re-optimised for
every new ``lambda``, which is what makes the schedule and the track consistent.

The "human experience" baseline used by acceptance criterion (e) is a
great-circle (straight) track with either

* ``constant_worst_case``: one constant speed = 0.85 * the minimum sustainable
  speed along the track (the classical conservative practice of setting speed by
  the heaviest ice the ship is going to meet), or
* ``station_wise``: 0.85 * the local sustainable speed at every station (a
  master who reads the ice chart and adjusts speed zone by zone -- the stronger
  baseline).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .constants import G, KNOT, NM_M
from .ice_field import IceField
from .resistance import (
    holtrop_open_water_resistance,
    ice_components,
    wind_resistance,
)
from .ships import ShipGeometry

#: Fraction of installed brake power that may be used continuously [-].
P_AVAIL_FRACTION = 0.85
#: Prudential speed margin of the experience baseline [-].
EXPERIENCE_MARGIN = 0.85
#: Minimum ground speed allowed by the planner [m/s].
V_MIN_MS = 0.6

_EIGHT = [
    (-1, -1), (-1, 0), (-1, 1),
    (0, -1), (0, 1),
    (1, -1), (1, 0), (1, 1),
]


@dataclass
class LegResult:
    fuel_kg: float
    time_h: float
    distance_km: float
    brake_energy_mj: float


def brake_power_kw(ship: ShipGeometry, resistance_n: float, v_water_ms: float) -> float:
    if v_water_ms <= 0:
        return 0.0
    return resistance_n * v_water_ms / ship.eta_propulsive / 1000.0


def fuel_rate_kg_per_h(ship: ShipGeometry, resistance_n: float, v_water_ms: float) -> float:
    """Main-engine fuel mass flow [kg/h] at a given resistance and speed."""
    return brake_power_kw(ship, resistance_n, v_water_ms) * ship.sfoc_g_kwh / 1000.0


def max_sustainable_speed_ms(
    ship: ShipGeometry,
    h_m: float,
    sigma_mpa: float,
    p_avail_fraction: float = P_AVAIL_FRACTION,
) -> float:
    """Highest through-water speed the ship can hold in the given ice [m/s].

    Bisection on the power balance ``R(v) v / eta <= p_avail`` capped by the
    open-water design speed.  Returns 0 if even the minimum speed is impossible
    (ice beyond the ship's capability).
    """
    p_avail_kw = p_avail_fraction * ship.brake_power_kw

    def power(v: float) -> float:
        r = holtrop_open_water_resistance(ship, v)["total"] + ice_components(
            ship, v, h_m, sigma_mpa
        )["total"]
        return r * v / ship.eta_propulsive / 1000.0

    lo, hi = V_MIN_MS, max(ship.design_speed_ms, V_MIN_MS)
    if power(hi) <= p_avail_kw:
        return hi
    if power(lo) > p_avail_kw:
        return 0.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if power(mid) <= p_avail_kw:
            lo = mid
        else:
            hi = mid
    return lo


def segment_fuel(
    ship: ShipGeometry,
    h_m: float,
    sigma_mpa: float,
    v_water_ms: float,
    current_along_ms: float,
    wind_along_ms: float,
    distance_km: float,
    v_ground_override: Optional[float] = None,
) -> LegResult:
    """Fuel, time and brake energy for one straight leg [km]."""
    if distance_km <= 0:
        return LegResult(0.0, 0.0, 0.0, 0.0)
    v_g = (
        v_ground_override
        if v_ground_override is not None
        else max(v_water_ms + current_along_ms, V_MIN_MS)
    )
    v_g = max(v_g, V_MIN_MS)
    r = (
        holtrop_open_water_resistance(ship, v_water_ms)["total"]
        + ice_components(ship, v_water_ms, h_m, sigma_mpa)["total"]
        + wind_resistance(ship, v_g, wind_along_ms)
    )
    rate = fuel_rate_kg_per_h(ship, r, v_water_ms)
    ds_m = distance_km * 1000.0
    time_h = ds_m / (v_g * 3600.0)
    p_kw = brake_power_kw(ship, r, v_water_ms)
    return LegResult(
        fuel_kg=rate * time_h,
        time_h=time_h,
        distance_km=distance_km,
        brake_energy_mj=p_kw * time_h * 3.6,
    )


class RoutePlanner:
    """A* planner over a coarsened ice-field grid with a Lagrangian deadline."""

    def __init__(
        self,
        field: IceField,
        ship: ShipGeometry,
        stride: int = 3,
        n_speeds: int = 7,
    ) -> None:
        self.field = field
        self.ship = ship
        self.stride = max(1, int(stride))
        self.h_max = ship.h_max_m

        self.nodes: List[Tuple[int, int]] = []       # (iy, ix) on the field grid
        self.index_of: Dict[Tuple[int, int], int] = {}
        h_list, sig_list, uc_list, vc_list, uw_list, vw_list = [], [], [], [], [], []
        for iy in range(0, field.ny, self.stride):
            for ix in range(0, field.nx, self.stride):
                s = field.sample(*field.cell_center(iy, ix))
                if s["land"] or s["h_m"] > self.h_max + 1e-9:
                    continue
                self.index_of[(iy, ix)] = len(self.nodes)
                self.nodes.append((iy, ix))
                h_list.append(s["h_m"])
                sig_list.append(s["sigma_mpa"])
                uc_list.append(s["u_current_ms"])
                vc_list.append(s["v_current_ms"])
                uw_list.append(s["u_wind_ms"])
                vw_list.append(s["v_wind_ms"])

        if not self.nodes:
            raise ValueError("no navigable ice-field nodes for this ship/ice class")

        self.h = np.asarray(h_list)
        self.sigma = np.asarray(sig_list)
        self.u_cur = np.asarray(uc_list)
        self.v_cur = np.asarray(vc_list)
        self.u_wind = np.asarray(uw_list)
        self.v_wind = np.asarray(vw_list)

        self.v_levels = np.geomspace(V_MIN_MS, ship.design_speed_ms, n_speeds)

        # pre-computed resistance and sustainable speed per node / speed level
        n = len(self.nodes)
        self.r_table = np.zeros((n, len(self.v_levels)))
        self.v_max_node = np.zeros(n)
        for k in range(n):
            for j, v in enumerate(self.v_levels):
                self.r_table[k, j] = holtrop_open_water_resistance(ship, float(v))[
                    "total"
                ] + ice_components(ship, float(v), self.h[k], self.sigma[k])["total"]
            self.v_max_node[k] = max_sustainable_speed_ms(ship, self.h[k], self.sigma[k])

        self.dx_km = field.dx_km * self.stride
        self.dy_km = field.dy_km * self.stride
        self._geom_cache: Dict[Tuple[int, int], Tuple[float, float]] = {}

    # -- helpers -------------------------------------------------------------
    def nearest_node(self, x_km: float, y_km: float) -> int:
        iy, ix = self.field.world_to_index(x_km, y_km)
        iy = int(round(iy / self.stride)) * self.stride
        ix = int(round(ix / self.stride)) * self.stride
        for rad in range(0, 6):
            for dy in range(-rad, rad + 1):
                for dx in range(-rad, rad + 1):
                    key = (iy + dy * self.stride, ix + dx * self.stride)
                    if key in self.index_of:
                        return self.index_of[key]
        raise ValueError(f"no navigable node near ({x_km}, {y_km}) km")

    def node_xy(self, idx: int) -> Tuple[float, float]:
        iy, ix = self.nodes[idx]
        return self.field.cell_center(iy, ix)

    def _edge_geometry(self, a: int, b: int) -> Tuple[float, float, float]:
        """(distance_km, unit_x, unit_y) for the edge a -> b."""
        key = (a, b)
        if key not in self._geom_cache:
            xa, ya = self.node_xy(a)
            xb, yb = self.node_xy(b)
            d = math.hypot(xb - xa, yb - ya)
            self._geom_cache[key] = (d, (xb - xa) / d, (yb - ya) / d)
        return self._geom_cache[key]

    def _neighbors(self, idx: int) -> List[int]:
        iy, ix = self.nodes[idx]
        out = []
        for dy, dx in _EIGHT:
            key = (iy + dy * self.stride, ix + dx * self.stride)
            j = self.index_of.get(key)
            if j is not None:
                out.append(j)
        return out

    def edge_option(self, a: int, b: int, lam: float) -> Tuple[float, float, float, float]:
        """Best (cost, fuel, time_h, v_water) on edge a->b for multiplier lam."""
        d_km, ux, uy = self._edge_geometry(a, b)
        ds_m = d_km * 1000.0
        cur_along = self.u_cur[a] * ux + self.v_cur[a] * uy
        wind_along = self.u_wind[a] * ux + self.v_wind[a] * uy
        v_cap = min(self.ship.design_speed_ms, self.v_max_node[a])
        best: Optional[Tuple[float, float, float, float]] = None
        for j, v in enumerate(self.v_levels):
            v = float(v)
            if v > v_cap + 1e-9:
                continue
            v_g = max(v + cur_along, V_MIN_MS)
            r = self.r_table[a, j] + wind_resistance(self.ship, v_g, wind_along)
            rate = fuel_rate_kg_per_h(self.ship, r, v)
            time_h = ds_m / (v_g * 3600.0)
            fuel = rate * time_h
            cost = fuel + lam * time_h
            if best is None or cost < best[0]:
                best = (cost, fuel, time_h, v)
        if best is None:  # every speed blocked by the power limit
            v = V_MIN_MS
            v_g = max(v + cur_along, V_MIN_MS)
            r = self.r_table[a, 0] + wind_resistance(self.ship, v_g, wind_along)
            time_h = ds_m / (v_g * 3600.0)
            fuel = fuel_rate_kg_per_h(self.ship, r, v) * time_h
            best = (fuel + lam * time_h, fuel, time_h, v)
        return best

    # -- search --------------------------------------------------------------
    def _search(self, start: int, goal: int, lam: float) -> Dict[str, Any]:
        gx, gy = self.node_xy(goal)
        lam_safe = max(lam, 0.0)

        # admissible lower bound on cost per metre
        c_floor = math.inf
        for k in range(0, len(self.nodes), max(1, len(self.nodes) // 200)):
            v_cap = min(self.ship.design_speed_ms, self.v_max_node[k])
            for j, v in enumerate(self.v_levels):
                v = float(v)
                if v > v_cap:
                    continue
                v_g = max(v, V_MIN_MS)
                rate = fuel_rate_kg_per_h(self.ship, self.r_table[k, j], v)
                per_m = rate / (v_g * 3600.0) + lam_safe / (v_g * 3600.0)
                c_floor = min(c_floor, per_m)
        if not math.isfinite(c_floor):
            c_floor = 0.0

        def heuristic(idx: int) -> float:
            x, y = self.node_xy(idx)
            return c_floor * math.hypot(gx - x, gy - y) * 1000.0

        dist = {start: 0.0}
        prev: Dict[int, int] = {}
        pq: List[Tuple[float, int]] = [(heuristic(start), start)]
        closed = set()
        while pq:
            _, cur = heapq.heappop(pq)
            if cur in closed:
                continue
            if cur == goal:
                break
            closed.add(cur)
            for nb in self._neighbors(cur):
                cost, _, _, _ = self.edge_option(cur, nb, lam)
                nd = dist[cur] + cost
                if nd < dist.get(nb, math.inf):
                    dist[nb] = nd
                    prev[nb] = cur
                    heapq.heappush(pq, (nd + heuristic(nb), nb))
        if goal not in dist:
            raise ValueError("no navigable route between the requested waypoints")

        path = [goal]
        while path[-1] != start:
            path.append(prev[path[-1]])
        path.reverse()

        fuel = time_h = dist_m = energy = 0.0
        speeds: List[float] = []
        for a, b in zip(path[:-1], path[1:]):
            _, f, t, v = self.edge_option(a, b, lam)
            d = self._edge_geometry(a, b)[0]
            fuel += f
            time_h += t
            dist_m += d * 1000.0
            energy += brake_power_kw(self.ship, self.r_table[a, 0], v) * t * 3.6
            speeds.append(v)
        return {
            "path": path,
            "points_km": [self.node_xy(i) for i in path],
            "speeds_ms": speeds,
            "fuel_kg": fuel,
            "time_h": time_h,
            "distance_km": dist_m / 1000.0,
            "brake_energy_mj": energy,
            "lambda_kg_per_h": lam,
        }

    def plan(
        self,
        start_km: Tuple[float, float],
        end_km: Tuple[float, float],
        deadline_h: Optional[float] = None,
        lam_iterations: int = 24,
    ) -> Dict[str, Any]:
        """Plan the energy-optimal passage, honouring ``deadline_h`` if given."""
        start = self.nearest_node(*start_km)
        goal = self.nearest_node(*end_km)

        if deadline_h is None:
            res = self._search(start, goal, 0.0)
            res["deadline_h"] = None
            res["deadline_binding"] = False
            return res

        if deadline_h <= 0:
            raise ValueError("deadline must be positive")

        # slowest (lambda = 0) and fastest (lambda = huge) passages bracket it
        slow = self._search(start, goal, 0.0)
        fast = self._search(start, goal, 1e7)
        if fast["time_h"] > deadline_h:
            raise ValueError(
                f"deadline {deadline_h:.2f} h is infeasible: the ship cannot go "
                f"faster than {fast['time_h']:.2f} h through this ice"
            )
        if slow["time_h"] <= deadline_h:
            slow["deadline_h"] = deadline_h
            slow["deadline_binding"] = False
            return slow

        lo, hi = 1e-6, 1e7          # geometric bisection on lambda [kg/h]
        best = slow
        for _ in range(lam_iterations):
            mid = math.exp(0.5 * (math.log(lo) + math.log(hi)))
            res = self._search(start, goal, mid)
            best = res
            if res["time_h"] > deadline_h:
                lo = mid
            else:
                hi = mid
            if abs(res["time_h"] - deadline_h) / deadline_h < 1e-3:
                break
        best["deadline_h"] = deadline_h
        best["deadline_binding"] = True
        return best


# ---------------------------------------------------------------------------
# Experience (human) baseline
# ---------------------------------------------------------------------------

def _resample_polyline(
    waypoints: Sequence[Tuple[float, float]], n_stations: int
) -> List[Tuple[float, float]]:
    """Resample a polyline into ``n_stations`` evenly spaced points."""
    seg_len = [
        math.hypot(xb - xa, yb - ya)
        for (xa, ya), (xb, yb) in zip(waypoints[:-1], waypoints[1:])
    ]
    total = sum(seg_len)
    if total <= 0:
        raise ValueError("polyline has zero length")
    out: List[Tuple[float, float]] = []
    for k in range(n_stations):
        d = total * k / (n_stations - 1)
        acc = 0.0
        placed = False
        for idx, ((xa, ya), (xb, yb)) in enumerate(zip(waypoints[:-1], waypoints[1:])):
            seg = seg_len[idx]
            t = 0.0 if seg <= 0 else (d - acc) / seg
            if t <= 1.0 + 1e-12 or idx == len(seg_len) - 1:
                t = min(max(t, 0.0), 1.0)
                out.append((xa + (xb - xa) * t, ya + (yb - ya) * t))
                placed = True
                break
            acc += seg
        if not placed:
            out.append(tuple(waypoints[-1]))
    return out


def experience_baseline(
    field: IceField,
    ship: ShipGeometry,
    start_km: Tuple[float, float],
    end_km: Tuple[float, float],
    n_stations: int = 80,
    mode: str = "station_wise",
    waypoints: Optional[Sequence[Tuple[float, float]]] = None,
    deadline_h: Optional[float] = None,
) -> Dict[str, Any]:
    """Experience baseline: great-circle / waypoint track, experience speeds.

    ``waypoints`` allows a dogleg track (the way a master follows a latitude
    band taken from the ice chart); ``None`` gives the direct great circle.
    ``deadline_h`` uniformly scales the experience speed profile until the
    passage time equals the required ETA -- this is the *fair* baseline used by
    criterion (e), because it gets the same schedule slack as the optimiser.
    """
    if mode not in {"constant_worst_case", "station_wise"}:
        raise ValueError(f"unknown baseline mode '{mode}'")

    track = [tuple(map(float, pt)) for pt in (waypoints or [start_km, end_km])]
    points = _resample_polyline(track, n_stations)

    station = []
    for (xa, ya), (xb, yb) in zip(points[:-1], points[1:]):
        xm, ym = 0.5 * (xa + xb), 0.5 * (ya + yb)
        s = field.sample(xm, ym)
        if s["land"]:
            raise ValueError("straight baseline track crosses land")
        if s["h_m"] > ship.h_max_m:
            raise ValueError(
                f"straight baseline track crosses ice thicker than the ship limit "
                f"({s['h_m']:.2f} m > {ship.h_max_m:.2f} m)"
            )
        d = math.hypot(xb - xa, yb - ya)
        ux, uy = (xb - xa) / d, (yb - ya) / d
        station.append(
            {
                "distance_km": d,
                "h_m": s["h_m"],
                "sigma_mpa": s["sigma_mpa"],
                "current_along": s["u_current_ms"] * ux + s["v_current_ms"] * uy,
                "wind_along": s["u_wind_ms"] * ux + s["v_wind_ms"] * uy,
                "v_sustain": max_sustainable_speed_ms(ship, s["h_m"], s["sigma_mpa"]),
            }
        )

    if mode == "constant_worst_case":
        v_const = EXPERIENCE_MARGIN * min(st["v_sustain"] for st in station)
        for st in station:
            st["v_water"] = min(v_const, st["v_sustain"])
    else:
        for st in station:
            st["v_water"] = EXPERIENCE_MARGIN * st["v_sustain"]

    def _integrate(scale: float) -> Tuple[float, float, float, float]:
        fuel = time_h = energy = dist = 0.0
        for st in station:
            v_w = max(scale * st["v_water"], V_MIN_MS)
            leg = segment_fuel(
                ship, st["h_m"], st["sigma_mpa"], v_w,
                st["current_along"], st["wind_along"], st["distance_km"],
            )
            fuel += leg.fuel_kg
            time_h += leg.time_h
            energy += leg.brake_energy_mj
            dist += leg.distance_km
        return fuel, time_h, energy, dist

    scale = 1.0
    fuel, time_h, energy, dist = _integrate(scale)
    if deadline_h is not None:
        if deadline_h <= 0:
            raise ValueError("deadline must be positive")
        if time_h > deadline_h:
            raise ValueError(
                f"experience route cannot meet the {deadline_h:.2f} h deadline at "
                f"its full experience speed ({time_h:.2f} h)"
            )
        lo, hi = 0.05, 1.0      # lo -> slow, hi -> full experience speed
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            _, t_mid, _, _ = _integrate(mid)
            if t_mid > deadline_h:
                lo = mid
            else:
                hi = mid
        scale = hi
        fuel, time_h, energy, dist = _integrate(scale)

    return {
        "mode": mode,
        "speed_scale": round(scale, 6),
        "deadline_h": deadline_h,
        "waypoints": track,
        "points_km": points,
        "fuel_kg": fuel,
        "time_h": time_h,
        "distance_km": dist,
        "brake_energy_mj": energy,
        "mean_speed_kn": dist * 1000.0 / (time_h * 3600.0) / KNOT,
        "mean_h_m": float(np.mean([st["h_m"] for st in station])),
    }

# ---------------------------------------------------------------------------
# Refinement: path smoothing + exact speed allocation on a fine sampling
# ---------------------------------------------------------------------------

def _sample_polyline(
    field: IceField,
    points: Sequence[Tuple[float, float]],
    ds_km: float = 3.0,
) -> List[Dict[str, float]]:
    """Resample a polyline into ~``ds_km`` segments with field samples."""
    pts = _resample_polyline(points, max(2, int(round(_polyline_length(points) / ds_km)) + 1))
    segs: List[Dict[str, float]] = []
    for (xa, ya), (xb, yb) in zip(pts[:-1], pts[1:]):
        d = math.hypot(xb - xa, yb - ya)
        if d <= 0:
            continue
        ux, uy = (xb - xa) / d, (yb - ya) / d
        s = field.sample(0.5 * (xa + xb), 0.5 * (ya + yb))
        segs.append(
            {
                "x0": xa, "y0": ya, "x1": xb, "y1": yb,
                "distance_km": d,
                "h_m": s["h_m"],
                "sigma_mpa": s["sigma_mpa"],
                "current_along": s["u_current_ms"] * ux + s["v_current_ms"] * uy,
                "wind_along": s["u_wind_ms"] * ux + s["v_wind_ms"] * uy,
                "land": 1.0 if s["land"] else 0.0,
            }
        )
    return segs


def _polyline_length(points: Sequence[Tuple[float, float]]) -> float:
    return sum(
        math.hypot(xb - xa, yb - ya) for (xa, ya), (xb, yb) in zip(points[:-1], points[1:])
    )


def _segment_ok(
    field: IceField, ship: ShipGeometry, a: Tuple[float, float], b: Tuple[float, float],
    max_samples: int = 40,
) -> bool:
    """True if the straight segment a-b stays in navigable ice."""
    d = math.hypot(b[0] - a[0], b[1] - a[1])
    n = max(2, min(max_samples, int(d / 1.5) + 2))
    for k in range(n):
        t = k / (n - 1)
        s = field.sample(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        if s["land"] or s["h_m"] > ship.h_max_m + 1e-9:
            return False
    return True


def _segment_energy_rate(
    field: IceField, ship: ShipGeometry, a: Tuple[float, float], b: Tuple[float, float],
    v_ref: float,
) -> float:
    """Fuel per metre along a straight segment at a reference speed [kg/m]."""
    segs = _sample_polyline(field, [a, b], ds_km=max(2.0, math.hypot(b[0] - a[0], b[1] - a[1]) / 12.0))
    total = 0.0
    for sg in segs:
        r = holtrop_open_water_resistance(ship, v_ref)["total"] + ice_components(
            ship, v_ref, sg["h_m"], sg["sigma_mpa"]
        )["total"]
        rate = fuel_rate_kg_per_h(ship, r, v_ref) / (v_ref * 3600.0)
        total += rate * sg["distance_km"] * 1000.0
    return total


def smooth_polyline(
    field: IceField,
    ship: ShipGeometry,
    points: Sequence[Tuple[float, float]],
    v_ref: Optional[float] = None,
    max_passes: int = 3,
) -> List[Tuple[float, float]]:
    """Greedy line-of-sight smoothing of a grid path ("string pulling").

    A vertex is dropped when the straight shortcut is navigable *and* cheaper in
    fuel per metre at the reference speed than the detour it replaces.  This
    removes the staircase artefacts of an 8-connected grid search.
    """
    v_ref = v_ref or min(3.0, ship.design_speed_ms)
    path = [tuple(map(float, p)) for p in points]
    for _ in range(max_passes):
        out = [path[0]]
        i = 0
        changed = False
        while i < len(path) - 1:
            best_j = i + 1
            for j in range(len(path) - 1, i + 1, -1):
                if not _segment_ok(field, ship, path[i], path[j]):
                    continue
                d_short = math.hypot(path[j][0] - path[i][0], path[j][1] - path[i][1])
                d_long = _polyline_length(path[i : j + 1])
                c_short = _segment_energy_rate(field, ship, path[i], path[j], v_ref) * d_short * 1000.0
                c_long = 0.0
                for k in range(i, j):
                    dk = math.hypot(
                        path[k + 1][0] - path[k][0], path[k + 1][1] - path[k][1]
                    )
                    c_long += (
                        _segment_energy_rate(field, ship, path[k], path[k + 1], v_ref)
                        * dk * 1000.0
                    )
                if c_short <= c_long:
                    best_j = j
                    break
            if best_j != i + 1:
                changed = True
            out.append(path[best_j])
            i = best_j
        path = out
        if not changed:
            break
    return path


def refined_speed_plan(
    field: IceField,
    ship: ShipGeometry,
    points: Sequence[Tuple[float, float]],
    deadline_h: Optional[float] = None,
    ds_km: float = 3.0,
    lam_iterations: int = 40,
) -> Dict[str, Any]:
    """Exact speed allocation along a fixed track (fine sampling).

    For a given Lagrange multiplier ``lambda`` [kg/h] every segment minimises
    ``fuel + lambda * time``; the multiplier is bisected until the passage time
    matches the deadline.  The per-segment problem is one-dimensional, so a
    golden-section search is exact up to tolerance.
    """
    segs = _sample_polyline(field, points, ds_km=ds_km)
    if not segs:
        raise ValueError("empty track")
    if any(sg["land"] for sg in segs):
        raise ValueError("track crosses land / fast ice")

    gr = (math.sqrt(5.0) - 1.0) / 2.0

    def seg_best(sg: Dict[str, float], lam: float) -> Tuple[float, float, float, float]:
        v_cap = max_sustainable_speed_ms(ship, sg["h_m"], sg["sigma_mpa"])
        v_cap = min(ship.design_speed_ms, v_cap)
        if v_cap <= V_MIN_MS:
            v_cap = V_MIN_MS
        ds_m = sg["distance_km"] * 1000.0

        def cost_and_legs(v: float) -> Tuple[float, float, float]:
            leg = segment_fuel(
                ship, sg["h_m"], sg["sigma_mpa"], v,
                sg["current_along"], sg["wind_along"], sg["distance_km"],
            )
            return leg.fuel_kg + lam * leg.time_h, leg.fuel_kg, leg.time_h

        a, b = V_MIN_MS, v_cap
        c, d = b - gr * (b - a), a + gr * (b - a)
        fc, fd = cost_and_legs(c), cost_and_legs(d)
        for _ in range(80):
            if b - a < 1e-3:
                break
            if fc[0] < fd[0]:
                b, d, fd = d, c, fc
                c = b - gr * (b - a)
                fc = cost_and_legs(c)
            else:
                a, c, fc = c, d, fd
                d = a + gr * (b - a)
                fd = cost_and_legs(d)
        v_opt = 0.5 * (a + b)
        _, fuel, time_h = cost_and_legs(v_opt)
        return cost_and_legs(v_opt)[0], fuel, time_h, v_opt

    def totals(lam: float) -> Tuple[float, float, float, float]:
        fuel = time_h = energy = 0.0
        for sg in segs:
            _, f, t, v = seg_best(sg, lam)
            fuel += f
            time_h += t
            energy += brake_power_kw(ship, 0.0, v) * 0.0  # placeholder, computed below
        return fuel, time_h, energy, 0.0

    if deadline_h is None:
        lam = 0.0
    else:
        slow_fuel, slow_time, _, _ = totals(0.0)
        fast_fuel, fast_time, _, _ = totals(1e7)
        if fast_time > deadline_h:
            raise ValueError(
                f"deadline {deadline_h:.2f} h infeasible on this track "
                f"(minimum passage {fast_time:.2f} h)"
            )
        if slow_time <= deadline_h:
            lam = 0.0
        else:
            lo, hi = 1e-6, 1e7
            for _ in range(lam_iterations):
                mid = math.exp(0.5 * (math.log(lo) + math.log(hi)))
                _, t_mid, _, _ = totals(mid)
                if t_mid > deadline_h:
                    lo = mid
                else:
                    hi = mid
                if abs(t_mid - deadline_h) / deadline_h < 1e-4:
                    break
            lam = math.exp(0.5 * (math.log(lo) + math.log(hi)))

    fuel = time_h = energy = dist = 0.0
    speeds: List[float] = []
    for sg in segs:
        _, f, t, v = seg_best(sg, lam)
        fuel += f
        time_h += t
        dist += sg["distance_km"]
        v_g = max(v + sg["current_along"], V_MIN_MS)
        r = (
            holtrop_open_water_resistance(ship, v)["total"]
            + ice_components(ship, v, sg["h_m"], sg["sigma_mpa"])["total"]
            + wind_resistance(ship, v_g, sg["wind_along"])
        )
        energy += brake_power_kw(ship, r, v) * t * 3.6
        speeds.append(v)

    return {
        "points_km": [tuple(map(float, p)) for p in points],
        "speed_profile_kn": [round(v / KNOT, 3) for v in speeds],
        "speed_by_ice": _speed_by_ice(segs, speeds),
        "fuel_kg": fuel,
        "time_h": time_h,
        "distance_km": dist,
        "brake_energy_mj": energy,
        "lambda_kg_per_h": lam,
        "mean_h_m": float(np.mean([sg["h_m"] for sg in segs])),
        "deadline_h": deadline_h,
        "deadline_binding": bool(deadline_h is not None and lam > 0.0),
    }


def _speed_by_ice(segs: Sequence[Dict[str, float]], speeds: Sequence[float]) -> List[Dict[str, float]]:
    """Group the speed profile into 0.2 m ice-thickness bins (report table)."""
    bins: Dict[int, List[float]] = {}
    for sg, v in zip(segs, speeds):
        key = int(round(sg["h_m"] / 0.2))
        bins.setdefault(key, []).append(v / KNOT)
    return [
        {
            "h_bin_m": round(k * 0.2, 2),
            "mean_speed_kn": round(float(np.mean(vs)), 3),
            "n_segments": len(vs),
        }
        for k, vs in sorted(bins.items())
    ]


def plan_voyage(
    field: IceField,
    ship: ShipGeometry,
    start_km: Tuple[float, float],
    end_km: Tuple[float, float],
    deadline_h: Optional[float] = None,
    stride: int = 3,
    ds_km: float = 3.0,
    outer_iterations: int = 2,
) -> Dict[str, Any]:
    """Full pipeline: A* topology -> path smoothing -> exact speed allocation.

    The A* edge cost uses the Lagrangian ``fuel + lambda*time`` and the
    multiplier estimated from the previous refinement, so that path selection
    and speed allocation are optimised against the *same* objective.  Two outer
    iterations are enough in practice (the multiplier converges to a few %).
    """
    planner = RoutePlanner(field, ship, stride=stride)
    start = planner.nearest_node(*start_km)
    goal = planner.nearest_node(*end_km)

    lam: Optional[float] = None
    best: Optional[Dict[str, Any]] = None
    for _ in range(max(1, outer_iterations)):
        if lam is None:
            search = planner._search(start, goal, 0.0 if deadline_h is None else 1e-3)
        else:
            search = planner._search(start, goal, lam)
        smoothed = smooth_polyline(field, ship, search["points_km"])
        if lam is not None and lam > 0:
            smoothed = refine_path_offsets(field, ship, smoothed, lam)
        refined = refined_speed_plan(field, ship, smoothed, deadline_h=deadline_h, ds_km=ds_km)
        refined["search_fuel_kg"] = search["fuel_kg"]
        refined["search_time_h"] = search["time_h"]
        refined["n_grid_nodes"] = len(planner.nodes)
        best = refined
        lam = refined["lambda_kg_per_h"]
        if lam <= 0.0:
            break
    assert best is not None
    return best


def refine_path_offsets(
    field: IceField,
    ship: ShipGeometry,
    points: Sequence[Tuple[float, float]],
    lam: float,
    offsets_km: Sequence[float] = (-6.0, -3.0, 3.0, 6.0),
    passes: int = 2,
    sample_km: float = 6.0,
) -> List[Tuple[float, float]]:
    """Local perpendicular pattern search on the smoothed track.

    Every interior vertex is moved by +/-3/6 km perpendicular to the local track
    direction; a move is kept when the Lagrangian cost ``fuel + lam*time`` of the
    two adjacent legs decreases.  This lets the planner centre itself inside a
    narrow lead instead of running along grid nodes.
    """
    path = [tuple(map(float, p)) for p in points]
    if len(path) < 3:
        return path

    def leg_cost(a: Tuple[float, float], b: Tuple[float, float]) -> float:
        segs = _sample_polyline(field, [a, b], ds_km=sample_km)
        total = 0.0
        for sg in segs:
            v_cap = min(
                ship.design_speed_ms, max_sustainable_speed_ms(ship, sg["h_m"], sg["sigma_mpa"])
            )
            v_cap = max(v_cap, V_MIN_MS)
            best = None
            for k in range(7):
                v = V_MIN_MS + (v_cap - V_MIN_MS) * k / 6.0
                leg = segment_fuel(
                    ship, sg["h_m"], sg["sigma_mpa"], v,
                    sg["current_along"], sg["wind_along"], sg["distance_km"],
                )
                c = leg.fuel_kg + lam * leg.time_h
                if best is None or c < best:
                    best = c
            total += best if best is not None else 0.0
        return total

    for _ in range(passes):
        improved = False
        for i in range(1, len(path) - 1):
            ax, ay = path[i - 1]
            bx, by = path[i]
            cx, cy = path[i + 1]
            tx, ty = cx - ax, cy - ay
            norm = math.hypot(tx, ty)
            if norm <= 0:
                continue
            nx, ny = -ty / norm, tx / norm
            best_pt, best_cost = path[i], leg_cost(path[i - 1], path[i]) + leg_cost(path[i], path[i + 1])
            for d in offsets_km:
                cand = (bx + nx * d, by + ny * d)
                if not _segment_ok(field, ship, path[i - 1], cand):
                    continue
                if not _segment_ok(field, ship, cand, path[i + 1]):
                    continue
                c = leg_cost(path[i - 1], cand) + leg_cost(cand, path[i + 1])
                if c < best_cost - 1e-9:
                    best_pt, best_cost = cand, c
            if best_pt != path[i]:
                path[i] = best_pt
                improved = True
        if not improved:
            break
    return path
