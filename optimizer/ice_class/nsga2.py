"""
NSGA-II Multi-Objective Optimizer.

Implements the Non-dominated Sorting Genetic Algorithm II (Deb et al., 2002)
with:
  - Non-dominated sorting (fast)
  - Crowding distance assignment
  - Binary tournament selection
  - Simulated binary crossover (SBX)
  - Polynomial mutation
  - Constraint handling via constrained-domination principle
  - Fixed reference point for stable hypervolume tracking

Reference:
  Deb, K., Pratap, A., Agarwal, S., & Meyarivan, T. (2002).
  A fast and elitist multiobjective genetic algorithm: NSGA-II.
  IEEE Transactions on Evolutionary Computation, 6(2), 182-197.
"""
import numpy as np
from typing import Callable, Dict, List, Optional, Tuple
import time


def fast_non_dominated_sort(objectives: np.ndarray, constraint_violations: np.ndarray = None) -> List[List[int]]:
    """
    Fast non-dominated sorting with constrained-domination.

    A solution i constraint-dominates j if:
      - i is feasible and j is not, OR
      - both infeasible and i has smaller total constraint violation, OR
      - both feasible and i Pareto-dominates j

    Args:
        objectives: (N, M) array of objective values (all minimized)
        constraint_violations: (N,) array of total constraint violation (0 = feasible)

    Returns:
        List of fronts, each front is a list of indices
    """
    N = objectives.shape[0]
    if constraint_violations is None:
        constraint_violations = np.zeros(N)

    domination_count = np.zeros(N, dtype=int)
    dominated_set = [[] for _ in range(N)]
    rank = np.full(N, -1, dtype=int)

    for i in range(N):
        for j in range(i + 1, N):
            if _constrained_dominates(objectives[i], objectives[j],
                                       constraint_violations[i], constraint_violations[j]):
                dominated_set[i].append(j)
                domination_count[j] += 1
            elif _constrained_dominates(objectives[j], objectives[i],
                                         constraint_violations[j], constraint_violations[i]):
                dominated_set[j].append(i)
                domination_count[i] += 1

    fronts = []
    current_front = [i for i in range(N) if domination_count[i] == 0]
    front_idx = 0

    while current_front:
        fronts.append(current_front)
        for i in current_front:
            rank[i] = front_idx
        next_front = []
        for i in current_front:
            for j in dominated_set[i]:
                domination_count[j] -= 1
                if domination_count[j] == 0:
                    next_front.append(j)
        front_idx += 1
        current_front = next_front

    return fronts


def _constrained_dominates(obj_i: np.ndarray, obj_j: np.ndarray,
                            cv_i: float, cv_j: float) -> bool:
    """Check if solution i constraint-dominates solution j."""
    i_feasible = cv_i <= 0
    j_feasible = cv_j <= 0

    if i_feasible and not j_feasible:
        return True
    if not i_feasible and j_feasible:
        return False
    if not i_feasible and not j_feasible:
        return cv_i < cv_j

    # Both feasible: Pareto domination
    better_in_any = False
    for a, b in zip(obj_i, obj_j):
        if a < b:
            better_in_any = True
        elif a > b:
            return False
    return better_in_any


def crowding_distance(objectives: np.ndarray, front: List[int]) -> np.ndarray:
    """
    Compute crowding distance for solutions in a front.

    Args:
        objectives: (N, M) array
        front: list of indices in this front

    Returns:
        (N,) array of crowding distances (0 for non-front members)
    """
    n = len(front)
    M = objectives.shape[1]
    distances = np.zeros(objectives.shape[0])

    if n <= 2:
        for i in front:
            distances[i] = np.inf
        return distances

    front_objs = objectives[front]
    for m in range(M):
        sorted_idx = np.argsort(front_objs[:, m])
        distances[front[sorted_idx[0]]] = np.inf
        distances[front[sorted_idx[-1]]] = np.inf

        obj_range = front_objs[sorted_idx[-1], m] - front_objs[sorted_idx[0], m]
        if obj_range < 1e-12:
            continue

        for k in range(1, n - 1):
            idx = front[sorted_idx[k]]
            distances[idx] += (front_objs[sorted_idx[k + 1], m] -
                               front_objs[sorted_idx[k - 1], m]) / obj_range

    return distances


def compute_hypervolume(pareto_objs: np.ndarray, reference_point: np.ndarray) -> float:
    """
    Compute 2D/3D hypervolume indicator.

    For 3D, uses a sweep-plane algorithm.
    For higher dimensions, uses Monte Carlo estimation.

    Args:
        pareto_objs: (N, M) Pareto front objective values
        reference_point: (M,) reference point (worst-case)

    Returns:
        Hypervolume value
    """
    N, M = pareto_objs.shape
    if N == 0:
        return 0.0

    # Clip to reference point
    clipped = np.minimum(pareto_objs, reference_point)

    if M == 2:
        return _hypervolume_2d(clipped, reference_point)
    elif M == 3:
        return _hypervolume_3d(clipped, reference_point)
    else:
        return _hypervolume_mc(clipped, reference_point)


def _hypervolume_2d(objs: np.ndarray, ref: np.ndarray) -> float:
    """2D hypervolume by sorting along first objective."""
    sorted_idx = np.argsort(objs[:, 0])
    hv = 0.0
    prev_y = ref[1]
    for i in sorted_idx:
        if objs[i, 0] >= ref[0] or objs[i, 1] >= ref[1]:
            continue
        width = ref[0] - objs[i, 0]
        height = prev_y - objs[i, 1]
        if height > 0:
            hv += width * height
        prev_y = min(prev_y, objs[i, 1])
    return hv


def _hypervolume_3d(objs: np.ndarray, ref: np.ndarray) -> float:
    """3D hypervolume using sweep-plane algorithm."""
    # Sort by third objective
    sorted_idx = np.argsort(objs[:, 2])
    hv = 0.0
    prev_z = 0.0

    for k in range(len(sorted_idx)):
        idx = sorted_idx[k]
        if np.any(objs[idx] >= ref):
            continue

        dz = objs[idx, 2] - prev_z
        if dz > 0:
            # Compute 2D hypervolume of the slice at this z-level
            slice_points = objs[sorted_idx[:k+1]]
            # Filter to points with z <= objs[idx, 2]
            mask = slice_points[:, 2] <= objs[idx, 2]
            if np.any(mask):
                sp = slice_points[mask]
                hv_2d = _hypervolume_2d(sp[:, :2], ref[:2])
                hv += hv_2d * dz

        prev_z = objs[idx, 2]

    # Add final slice from last z to ref[2]
    if prev_z < ref[2]:
        all_pts = objs[sorted_idx]
        hv_2d = _hypervolume_2d(all_pts[:, :2], ref[:2])
        hv += hv_2d * (ref[2] - prev_z)

    return hv


def _hypervolume_mc(objs: np.ndarray, ref: np.ndarray, n_samples: int = 10000) -> float:
    """Monte Carlo hypervolume estimation for M > 3."""
    M = objs.shape[1]
    samples = np.random.uniform(0, 1, (n_samples, M)) * ref
    dominated = np.zeros(n_samples, dtype=bool)
    for i in range(objs.shape[0]):
        is_dominated = np.all(samples >= objs[i], axis=1)
        dominated |= is_dominated
    volume = np.prod(ref)
    return volume * np.mean(dominated)


class NSGA2Optimizer:
    """
    NSGA-II optimizer for ice-class hull form multi-objective optimization.

    Design variables: [L, B, T, Cb, α_bow, α_stem]
    Objectives: [R_ice, R_ow, -GM] (all minimized; GM negated for maximization)
    """

    def __init__(self,
                 n_objectives: int = 3,
                 n_variables: int = 6,
                 pop_size: int = 40,
                 n_generations: int = 50,
                 crossover_prob: float = 0.9,
                 crossover_eta: float = 20.0,
                 mutation_prob: float = None,
                 mutation_eta: float = 20.0,
                 seed: int = 42):
        self.n_obj = n_objectives
        self.n_var = n_variables
        self.pop_size = pop_size
        self.n_gen = n_generations
        self.cx_prob = crossover_prob
        self.cx_eta = crossover_eta
        self.mut_prob = mutation_prob or (1.0 / n_variables)
        self.mut_eta = mutation_eta
        self.seed = seed
        self.rng = np.random.RandomState(seed)

    def run(self,
            objective_fn: Callable[[np.ndarray], Tuple[np.ndarray, float]],
            lb: np.ndarray,
            ub: np.ndarray,
            verbose: bool = True) -> Dict:
        """
        Run NSGA-II optimization.

        Args:
            objective_fn: function(x) -> (objectives[3], constraint_violation)
            lb: lower bounds (6,)
            ub: upper bounds (6,)
            verbose: print progress

        Returns:
            Dictionary with Pareto front, history, timing, etc.
        """
        t0 = time.time()
        rng = self.rng

        # Initialize population with Latin Hypercube Sampling for better coverage
        pop = self._latin_hypercube_init(lb, ub, rng)

        objs = np.zeros((self.pop_size, self.n_obj))
        cvs = np.zeros(self.pop_size)

        for i in range(self.pop_size):
            objs[i], cvs[i] = objective_fn(pop[i])

        # Compute FIXED reference point from initial population
        # This ensures stable hypervolume tracking across generations
        ref_point = self._compute_fixed_reference_point(objs, cvs)

        # Track hypervolume history
        hv_history = []

        # Main generational loop
        for gen in range(self.n_gen):
            # Create offspring
            offspring = self._create_offspring(pop, objs, cvs, lb, ub, rng)

            # Evaluate offspring
            off_objs = np.zeros((self.pop_size, self.n_obj))
            off_cvs = np.zeros(self.pop_size)
            for i in range(self.pop_size):
                off_objs[i], off_cvs[i] = objective_fn(offspring[i])

            # Combine parent + offspring
            combined_pop = np.vstack([pop, offspring])
            combined_objs = np.vstack([objs, off_objs])
            combined_cvs = np.concatenate([cvs, off_cvs])

            # Non-dominated sorting
            fronts = fast_non_dominated_sort(combined_objs, combined_cvs)

            # Select next generation
            new_pop = []
            new_objs = []
            new_cvs = []

            for front in fronts:
                if len(new_pop) + len(front) <= self.pop_size:
                    for idx in front:
                        new_pop.append(combined_pop[idx])
                        new_objs.append(combined_objs[idx])
                        new_cvs.append(combined_cvs[idx])
                else:
                    remaining = self.pop_size - len(new_pop)
                    cd = crowding_distance(combined_objs, front)
                    sorted_front = sorted(front, key=lambda i: -cd[i])
                    for idx in sorted_front[:remaining]:
                        new_pop.append(combined_pop[idx])
                        new_objs.append(combined_objs[idx])
                        new_cvs.append(combined_cvs[idx])
                    break

            pop = np.array(new_pop)
            objs = np.array(new_objs)
            cvs = np.array(new_cvs)

            # Compute hypervolume of feasible Pareto front
            feasible_mask = cvs <= 0
            if np.any(feasible_mask):
                feasible_objs = objs[feasible_mask]
                pf_fronts = fast_non_dominated_sort(feasible_objs)
                if pf_fronts and len(pf_fronts[0]) > 0:
                    pf_indices = pf_fronts[0]
                    pf_objs = feasible_objs[pf_indices]
                    hv = compute_hypervolume(pf_objs, ref_point)
                else:
                    hv = 0.0
            else:
                hv = 0.0

            hv_history.append(hv)

            if verbose and (gen + 1) % 10 == 0:
                n_feasible = int(np.sum(cvs <= 0))
                print(f"Gen {gen+1}/{self.n_gen}: HV={hv:.2f}, "
                      f"feasible={n_feasible}/{self.pop_size}")

        elapsed = time.time() - t0

        # Extract final Pareto front
        feasible_mask = cvs <= 0
        if np.any(feasible_mask):
            feasible_pop = pop[feasible_mask]
            feasible_objs = objs[feasible_mask]
            pf_fronts = fast_non_dominated_sort(feasible_objs)
            pf_indices = pf_fronts[0] if pf_fronts else []
            pareto_pop = feasible_pop[pf_indices] if len(pf_indices) > 0 else feasible_pop[:0]
            pareto_objs = feasible_objs[pf_indices] if len(pf_indices) > 0 else feasible_objs[:0]
        else:
            pareto_pop = np.array([]).reshape(0, self.n_var)
            pareto_objs = np.array([]).reshape(0, self.n_obj)

        return {
            "pareto_pop": pareto_pop,
            "pareto_objs": pareto_objs,
            "final_pop": pop,
            "final_objs": objs,
            "final_cvs": cvs,
            "hv_history": hv_history,
            "reference_point": ref_point,
            "n_generations": self.n_gen,
            "pop_size": self.pop_size,
            "wall_time_s": elapsed,
            "seed": self.seed,
        }

    def _latin_hypercube_init(self, lb: np.ndarray, ub: np.ndarray, rng) -> np.ndarray:
        """Initialize population using Latin Hypercube Sampling for better coverage."""
        n = self.pop_size
        d = self.n_var
        # Generate LHS samples in [0, 1]
        samples = np.zeros((n, d))
        for j in range(d):
            perm = rng.permutation(n)
            for i in range(n):
                samples[i, j] = (perm[i] + rng.rand()) / n
        # Scale to bounds
        return lb + samples * (ub - lb)

    def _compute_fixed_reference_point(self, objs: np.ndarray, cvs: np.ndarray) -> np.ndarray:
        """
        Compute a FIXED reference point for hypervolume.

        Uses worst-case values with generous margin to ensure
        stable HV tracking across generations.
        """
        # Use generous upper bounds based on physical limits
        # R_ice: max ~2000 kN, R_ow: max ~5000 kN, -GM: max ~-0.1 (GM min ~0.1)
        # Reference point should be worse than any feasible solution
        ref = np.array([
            3000.0,   # R_ice reference [kN]
            8000.0,   # R_ow reference [kN]
            0.0,      # -GM reference (GM=0, i.e., -GM=0)
        ])
        return ref

    def _create_offspring(self, pop, objs, cvs, lb, ub, rng):
        """Create offspring via tournament selection, SBX crossover, polynomial mutation."""
        offspring = np.zeros_like(pop)
        N = pop.shape[0]

        # Tournament selection (based on rank + crowding distance)
        fronts = fast_non_dominated_sort(objs, cvs)
        rank = np.zeros(N, dtype=int)
        for f_idx, front in enumerate(fronts):
            for idx in front:
                rank[idx] = f_idx

        cd = np.zeros(N)
        for front in fronts:
            front_cd = crowding_distance(objs, front)
            for idx in front:
                cd[idx] = front_cd[idx]

        for i in range(0, N, 2):
            p1 = self._tournament_select(rank, cd, rng)
            p2 = self._tournament_select(rank, cd, rng)

            c1, c2 = pop[p1].copy(), pop[p2].copy()

            # SBX crossover
            if rng.rand() < self.cx_prob:
                c1, c2 = self._sbx_crossover(c1, c2, lb, ub, rng)

            # Polynomial mutation
            c1 = self._polynomial_mutation(c1, lb, ub, rng)
            if i + 1 < N:
                c2 = self._polynomial_mutation(c2, lb, ub, rng)

            offspring[i] = c1
            if i + 1 < N:
                offspring[i + 1] = c2

        return offspring

    def _tournament_select(self, rank, cd, rng, tournament_size=2):
        """Binary tournament selection."""
        candidates = rng.choice(len(rank), size=tournament_size, replace=False)
        best = candidates[0]
        for c in candidates[1:]:
            if rank[c] < rank[best] or (rank[c] == rank[best] and cd[c] > cd[best]):
                best = c
        return best

    def _sbx_crossover(self, p1, p2, lb, ub, rng):
        """Simulated Binary Crossover."""
        c1, c2 = p1.copy(), p2.copy()
        for i in range(len(p1)):
            if rng.rand() >= 0.5:
                if abs(p1[i] - p2[i]) > 1e-14:
                    u = rng.rand()
                    if u <= 0.5:
                        beta = (2 * u) ** (1.0 / (self.cx_eta + 1))
                    else:
                        beta = (1.0 / (2 * (1 - u))) ** (1.0 / (self.cx_eta + 1))
                    c1[i] = 0.5 * ((1 + beta) * p1[i] + (1 - beta) * p2[i])
                    c2[i] = 0.5 * ((1 - beta) * p1[i] + (1 + beta) * p2[i])
        c1 = np.clip(c1, lb, ub)
        c2 = np.clip(c2, lb, ub)
        return c1, c2

    def _polynomial_mutation(self, x, lb, ub, rng):
        """Polynomial mutation."""
        y = x.copy()
        for i in range(len(x)):
            if rng.rand() < self.mut_prob:
                delta = ub[i] - lb[i]
                u = rng.rand()
                if u < 0.5:
                    deltaq = (2 * u) ** (1.0 / (self.mut_eta + 1)) - 1
                else:
                    deltaq = 1 - (2 * (1 - u)) ** (1.0 / (self.mut_eta + 1))
                y[i] = x[i] + deltaq * delta
        return np.clip(y, lb, ub)
