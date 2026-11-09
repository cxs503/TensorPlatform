"""
Core algorithm: Dynamic-programming ice-route speed optimiser.

Physics
-------
Total resistance R(v, h, σ) = R_OW(v) + R_ice(v, h, σ) + R_wind(v)
  R_OW  : Holtrop-Mennen 1984 calm-water resistance
  R_ice : TP-POLAR-1 level-ice (crushing + breaking + submersion)
  R_wind: quadratic air drag on above-water hull

Fuel rate: P(v) = R(v) · v / η_prop  [W]
Fuel per segment: E_seg = P(v) · Δt = R(v) · d / η_prop  [J]
  where d = segment length, Δt = d / v

Optimization
------------
Given a route graph G = (V, E) with ice conditions on each edge:
  min Σ_e  R_e(v_e) · d_e / η_prop
  s.t. Σ_e  d_e / v_e  ≤ T_max   (arrival-time budget)
       v_min ≤ v_e ≤ v_max

Discretise speed into K levels → standard shortest-path DP with
time-budget state.  Pareto front obtained by sweeping T_max.

Reference: sensor-fusion speed-optimisation literature (MOL / WRI methods).
"""

from __future__ import annotations

import math
import sys
import time
from dataclasses import dataclass, field
from itertools import product
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Import the polar resistance model
# ---------------------------------------------------------------------------
sys.path.insert(0, "/root/tp-agent/work/m7848")
from backend.services.polar.resistance import (
    holtrop_open_water_resistance,
    level_ice_resistance,
    ResistanceBreakdown,
)
from backend.services.polar.ships import ShipGeometry
from backend.services.polar.constants import KNOT, NM_M


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class IceCondition:
    """Ice condition on a route segment."""
    thickness_m: float          # ice thickness [m]
    concentration: float        # ice concentration [0..1] (fractional coverage)
    flexural_strength_mpa: float = 1.5   # flexural strength [MPa]
    channel_factor: float = 1.0          # 1.0 = intact, <1 = escorted

    def effective_resistance_params(self) -> Tuple[float, float]:
        """Return (h_eff, sigma_eff) accounting for concentration.
        
        For partial concentration c:
          - effective thickness h_eff = h * c^(1/3)  (empirical, Ponttinen 1993)
          - sigma_eff unchanged (material property)
        """
        c = max(0.0, min(1.0, self.concentration))
        h_eff = self.thickness_m * (c ** (1.0 / 3.0)) if c > 0 else 0.0
        return h_eff, self.flexural_strength_mpa


@dataclass
class RouteEdge:
    """A segment of the route."""
    edge_id: int
    from_node: int
    to_node: int
    distance_m: float           # segment length [m]
    ice: IceCondition           # ice condition on this segment
    current_ms: float = 0.0     # current along track [m/s] (positive = following)
    wind_ms: float = 0.0        # wind along track [m/s] (positive = following)


@dataclass
class RouteGraph:
    """Route graph: sequence of edges forming a path."""
    edges: List[RouteEdge]
    nodes: List[int] = field(default_factory=list)
    
    def __post_init__(self):
        if not self.nodes:
            # Auto-generate node list from edges
            node_set = set()
            for e in self.edges:
                node_set.add(e.from_node)
                node_set.add(e.to_node)
            self.nodes = sorted(node_set)
    
    @property
    def total_distance_m(self) -> float:
        return sum(e.distance_m for e in self.edges)
    
    @property
    def n_segments(self) -> int:
        return len(self.edges)


@dataclass
class SpeedProfile:
    """Speed assignment for each segment."""
    speeds_kn: List[float]      # speed per segment [knots]
    fuel_kg: float              # total fuel [kg]
    time_h: float               # total time [hours]
    segment_details: List[Dict[str, float]] = field(default_factory=list)


@dataclass
class OptimizationResult:
    """Result of a single optimization run."""
    status: str
    speed_profile: Optional[SpeedProfile] = None
    fuel_kg: float = 0.0
    time_h: float = 0.0
    wall_time_s: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ParetoFront:
    """Pareto-optimal set of (fuel, time) solutions."""
    points: List[Tuple[float, float]]          # (fuel_kg, time_h)
    solutions: List[SpeedProfile]               # corresponding speed profiles
    dominated_removed: int = 0
    
    def __post_init__(self):
        # Sort by fuel ascending
        paired = sorted(zip(self.points, self.solutions), key=lambda x: x[0][0])
        if paired:
            self.points, self.solutions = zip(*paired)
            self.points = list(self.points)
            self.solutions = list(self.solutions)
    
    def verify_non_dominance(self) -> bool:
        """Check that no point dominates another."""
        for i, (fi, ti) in enumerate(self.points):
            for j, (fj, tj) in enumerate(self.points):
                if i == j:
                    continue
                # j dominates i if fj <= fi and tj <= ti with at least one strict
                if fj <= fi and tj <= ti and (fj < fi or tj < ti):
                    return False
        return True
    
    @property
    def n_points(self) -> int:
        return len(self.points)


# ---------------------------------------------------------------------------
# Ship + propulsion defaults
# ---------------------------------------------------------------------------

def default_ship() -> ShipGeometry:
    """A representative ice-class cargo vessel (Polar Class 6 / Arc4)."""
    from backend.services.polar.ships import get_ship
    return get_ship("handysize_bulker")

def _default_ship_old() -> ShipGeometry:
    """A representative ice-class cargo vessel (Polar Class 6 / Arc4)."""
    return ShipGeometry(
        length_m=150.0,
        beam_m=24.0,
        draft_m=8.0,
        cb=0.68,
        bow_half_angle_deg=22.0,
        mu_hull_ice=0.15,
        displacement_m3=150.0 * 24.0 * 8.0 * 0.68,
    )


# Propulsion parameters
ETA_PROP = 0.65       # propulsive efficiency (hull × propeller × shaft)
SFC_BASE = 200.0      # base specific fuel consumption [g/kWh]
HFO_LHV = 42.7e6      # HFO lower heating value [J/kg]


def fuel_rate_kg_s(resistance_n: float, speed_ms: float) -> float:
    """Fuel mass rate [kg/s] from resistance and speed.
    
    P_shaft = R · v / η_prop  [W]
    m_dot = P_shaft · SFC / 3600e3  [kg/s]  (SFC in g/kWh → kg/J)
    """
    if speed_ms <= 0 or resistance_n <= 0:
        return 0.0
    p_shaft_w = resistance_n * speed_ms / ETA_PROP
    sfc_kg_j = SFC_BASE * 1e-3 / 3.6e6   # g/kWh → kg/J
    return p_shaft_w * sfc_kg_j


def segment_fuel_kg(
    ship: ShipGeometry,
    edge: RouteEdge,
    speed_kn: float,
) -> Tuple[float, float]:
    """Compute fuel [kg] and time [h] for one segment at given speed.
    
    Returns (fuel_kg, time_h).
    """
    v_ms = speed_kn * KNOT
    if v_ms <= 0:
        return (float('inf'), float('inf'))
    
    h_eff, sigma_eff = edge.ice.effective_resistance_params()
    
    # Total resistance
    if h_eff > 0.01:  # ice present
        breakdown = level_ice_resistance(
            ship, v_ms, h_eff, sigma_eff,
            channel_factor=edge.ice.channel_factor,
            current_along_ms=edge.current_ms,
            wind_along_ms=edge.wind_ms,
        )
        rt = breakdown.total_n
    else:
        ow = holtrop_open_water_resistance(ship, v_ms)
        rt = ow["total"]
    
    # Ground speed (with current)
    v_ground_ms = v_ms + edge.current_ms
    if v_ground_ms <= 0:
        return (float('inf'), float('inf'))
    
    # Time and fuel
    dt_s = edge.distance_m / v_ground_ms
    fuel_kg = fuel_rate_kg_s(rt, v_ms) * dt_s
    time_h = dt_s / 3600.0
    
    return (fuel_kg, time_h)


# ---------------------------------------------------------------------------
# Dynamic Programming Solver
# ---------------------------------------------------------------------------

class IceRouteOptimizer:
    """
    Ice-route speed optimiser using dynamic programming.
    
    Speed levels are discretised into `n_speed` values in [v_min, v_max].
    Time budget is discretised into `n_time_buckets` for the DP state.
    
    The DP solves:
        min Σ fuel_e(v_e)
        s.t. Σ time_e(v_e) ≤ T_max
    
    by backward induction on the route graph.
    """
    
    def __init__(
        self,
        ship: Optional[ShipGeometry] = None,
        v_min_kn: float = 3.0,
        v_max_kn: float = 16.0,
        n_speed: int = 20,
        n_time_buckets: int = 100,
    ):
        self.ship = ship or default_ship()
        self.v_min_kn = v_min_kn
        self.v_max_kn = v_max_kn
        self.n_speed = n_speed
        self.n_time_buckets = n_time_buckets
        
        # Speed grid (knots)
        self.speed_grid_kn = np.linspace(v_min_kn, v_max_kn, n_speed)
    
    def _precompute_segment_costs(
        self, graph: RouteGraph
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Precompute fuel and time for each (segment, speed) pair.
        
        Returns:
            fuel_matrix: shape (n_seg, n_speed) [kg]
            time_matrix: shape (n_seg, n_speed) [h]
        """
        n_seg = graph.n_segments
        n_spd = len(self.speed_grid_kn)
        fuel = np.full((n_seg, n_spd), np.inf)
        time_mat = np.full((n_seg, n_spd), np.inf)
        
        for i, edge in enumerate(graph.edges):
            for j, v_kn in enumerate(self.speed_grid_kn):
                f, t = segment_fuel_kg(self.ship, edge, v_kn)
                fuel[i, j] = f
                time_mat[i, j] = t
        
        return fuel, time_mat
    
    def optimize(
        self,
        graph: RouteGraph,
        time_budget_h: Optional[float] = None,
    ) -> OptimizationResult:
        """
        Solve the fuel-minimisation problem with a time-budget constraint.
        
        If time_budget_h is None, uses the minimum-time speed (v_max everywhere).
        
        Uses DP with time-bucket discretisation.
        """
        t0 = time.time()
        n_seg = graph.n_segments
        n_spd = len(self.speed_grid_kn)
        
        # Precompute costs
        fuel_mat, time_mat = self._precompute_segment_costs(graph)
        
        # If no budget, just go full speed
        if time_budget_h is None:
            best_idx = np.argmin(time_mat, axis=1)  # fastest speed per segment
            # Actually: minimum time = max speed
            best_idx = np.full(n_seg, n_spd - 1)
            speeds = [self.speed_grid_kn[best_idx[i]] for i in range(n_seg)]
            total_fuel = sum(fuel_mat[i, best_idx[i]] for i in range(n_seg))
            total_time = sum(time_mat[i, best_idx[i]] for i in range(n_seg))
            return OptimizationResult(
                status="optimal",
                speed_profile=SpeedProfile(
                    speeds_kn=speeds, fuel_kg=total_fuel, time_h=total_time
                ),
                fuel_kg=total_fuel,
                time_h=total_time,
                wall_time_s=time.time() - t0,
            )
        
        # Minimum possible time (all v_max)
        min_time_h = float(np.sum(time_mat[:, -1]))
        if time_budget_h < min_time_h * 0.999:
            return OptimizationResult(
                status="infeasible",
                wall_time_s=time.time() - t0,
                metadata={"min_time_h": min_time_h, "requested_h": time_budget_h},
            )
        
        # Maximum possible time (all v_min)
        max_time_h = float(np.sum(time_mat[:, 0]))
        
        # DP with time-bucket discretisation
        # State: (segment_index, time_bucket)
        # Value: minimum fuel to reach destination from this state
        
        # Time buckets: linearly spaced from 0 to max_time_h
        time_edges = np.linspace(0, max_time_h * 1.05, self.n_time_buckets + 1)
        bucket_width = time_edges[1] - time_edges[0]
        
        def time_to_bucket(t: float) -> int:
            """Map continuous time to bucket index."""
            idx = int(t / bucket_width)
            return max(0, min(self.n_time_buckets, idx))
        
        # DP table: dp[i][b] = min fuel from segment i to end, with b time remaining
        INF = 1e30
        dp = np.full((n_seg + 1, self.n_time_buckets + 1), INF)
        choice = np.full((n_seg, self.n_time_buckets + 1), -1, dtype=int)
        
        # Base case: at destination, no fuel needed
        dp[n_seg, :] = 0.0
        
        # Backward induction
        for i in range(n_seg - 1, -1, -1):
            for b in range(self.n_time_buckets + 1):
                t_remaining_h = time_edges[b] if b <= self.n_time_buckets else max_time_h * 1.05
                best_fuel = INF
                best_j = -1
                for j in range(n_spd):
                    t_seg = time_mat[i, j]
                    if t_seg > t_remaining_h * 1.001:
                        continue  # infeasible
                    f_seg = fuel_mat[i, j]
                    b_next = time_to_bucket(t_remaining_h - t_seg)
                    total = f_seg + dp[i + 1, b_next]
                    if total < best_fuel:
                        best_fuel = total
                        best_j = j
                dp[i, b] = best_fuel
                choice[i, b] = best_j
        
        # Recover solution
        b_start = time_to_bucket(time_budget_h)
        if dp[0, b_start] >= INF * 0.5:
            return OptimizationResult(
                status="infeasible",
                wall_time_s=time.time() - t0,
                metadata={"time_budget_h": time_budget_h},
            )
        
        speeds = []
        details = []
        b = b_start
        total_fuel = 0.0
        total_time = 0.0
        for i in range(n_seg):
            j = choice[i, b]
            v_kn = self.speed_grid_kn[j]
            f_seg = fuel_mat[i, j]
            t_seg = time_mat[i, j]
            speeds.append(v_kn)
            total_fuel += f_seg
            total_time += t_seg
            details.append({
                "segment": i,
                "speed_kn": v_kn,
                "fuel_kg": f_seg,
                "time_h": t_seg,
                "distance_m": graph.edges[i].distance_m,
            })
            b = time_to_bucket(time_edges[b] - t_seg) if b > 0 else 0
        
        return OptimizationResult(
            status="optimal",
            speed_profile=SpeedProfile(
                speeds_kn=speeds,
                fuel_kg=total_fuel,
                time_h=total_time,
                segment_details=details,
            ),
            fuel_kg=total_fuel,
            time_h=total_time,
            wall_time_s=time.time() - t0,
            metadata={
                "n_speed_levels": n_spd,
                "n_time_buckets": self.n_time_buckets,
                "time_budget_h": time_budget_h,
            },
        )
    
    def pareto_front(
        self,
        graph: RouteGraph,
        n_points: int = 30,
    ) -> ParetoFront:
        """
        Compute the Pareto front of (fuel, time) by sweeping time budget.
        
        Uses n_points evenly-spaced time budgets between min-time and max-time.
        """
        n_seg = graph.n_segments
        fuel_mat, time_mat = self._precompute_segment_costs(graph)
        
        min_time_h = float(np.sum(time_mat[:, -1]))   # all v_max
        max_time_h = float(np.sum(time_mat[:, 0]))     # all v_min
        
        # Sweep time budgets
        budgets = np.linspace(min_time_h, max_time_h, n_points)
        
        raw_points = []
        raw_solutions = []
        
        for tb in budgets:
            result = self.optimize(graph, time_budget_h=tb)
            if result.status == "optimal" and result.speed_profile is not None:
                raw_points.append((result.fuel_kg, result.time_h))
                raw_solutions.append(result.speed_profile)
        
        # Filter dominated points
        pareto_points = []
        pareto_solutions = []
        dominated_count = 0
        
        for i, (fi, ti) in enumerate(raw_points):
            is_dominated = False
            for j, (fj, tj) in enumerate(raw_points):
                if i == j:
                    continue
                if fj <= fi and tj <= ti and (fj < fi or tj < ti):
                    is_dominated = True
                    break
            if not is_dominated:
                pareto_points.append((fi, ti))
                pareto_solutions.append(raw_solutions[i])
            else:
                dominated_count += 1
        
        return ParetoFront(
            points=pareto_points,
            solutions=pareto_solutions,
            dominated_removed=dominated_count,
        )
    
    def exhaustive_search(
        self,
        graph: RouteGraph,
        n_speed_levels: int = 5,
    ) -> OptimizationResult:
        """
        Exhaustive search over all speed combinations (for verification).
        
        Only feasible for very small graphs (n_seg * log(n_speed_levels) manageable).
        Returns the fuel-optimal solution for the minimum-time constraint.
        """
        n_seg = graph.n_segments
        speeds = np.linspace(self.v_min_kn, self.v_max_kn, n_speed_levels)
        
        best_fuel = float('inf')
        best_speeds = None
        best_time = 0.0
        
        for combo in product(range(n_speed_levels), repeat=n_seg):
            total_fuel = 0.0
            total_time = 0.0
            feasible = True
            for i, j in enumerate(combo):
                v_kn = speeds[j]
                f, t = segment_fuel_kg(self.ship, graph.edges[i], v_kn)
                if math.isinf(f):
                    feasible = False
                    break
                total_fuel += f
                total_time += t
            if feasible and total_fuel < best_fuel:
                best_fuel = total_fuel
                best_speeds = [speeds[j] for j in combo]
                best_time = total_time
        
        if best_speeds is None:
            return OptimizationResult(status="infeasible")
        
        return OptimizationResult(
            status="optimal",
            speed_profile=SpeedProfile(
                speeds_kn=best_speeds, fuel_kg=best_fuel, time_h=best_time
            ),
            fuel_kg=best_fuel,
            time_h=best_time,
        )
