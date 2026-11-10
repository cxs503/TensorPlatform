"""
NSGA-II Multi-Objective Optimizer — General Purpose Implementation.

Reference:
  Deb, K., Pratap, A., Agarwal, S., & Meyarivan, T. (2002).
  A fast and elitist multiobjective genetic algorithm: NSGA-II.
  IEEE Transactions on Evolutionary Computation, 6(2), 182-197.
"""
import numpy as np
import time
import json
import hashlib
from typing import Callable, Dict, List, Optional, Tuple

from .metrics import hypervolume


def fast_non_dominated_sort(objectives: np.ndarray) -> List[List[int]]:
    """
    Fast non-dominated sorting (Deb et al. 2002).
    
    Args:
        objectives: (N, M) array of objective values (all minimized)
    
    Returns:
        List of fronts, each front is a list of indices
    """
    N = objectives.shape[0]
    domination_count = np.zeros(N, dtype=int)
    dominated_set = [[] for _ in range(N)]
    rank = np.full(N, -1, dtype=int)

    for i in range(N):
        for j in range(i + 1, N):
            if _dominates(objectives[i], objectives[j]):
                dominated_set[i].append(j)
                domination_count[j] += 1
            elif _dominates(objectives[j], objectives[i]):
                dominated_set[j].append(i)
                domination_count[i] += 1

    fronts = []
    current_front = [i for i in range(N) if domination_count[i] == 0]
    front_idx = 0

    while current_front:
        fronts.append(list(current_front))
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


def _dominates(obj_i: np.ndarray, obj_j: np.ndarray) -> bool:
    """Check if solution i Pareto-dominates solution j (all objectives minimized)."""
    better_in_any = False
    for a, b in zip(obj_i, obj_j):
        if a < b:
            better_in_any = True
        elif a > b:
            return False
    return better_in_any


def _violates(i: int, violations: Optional[np.ndarray]) -> float:
    if violations is None:
        return 0.0
    return float(violations[i])


def _constrained_dominates(i: int, j: int,
                           objectives: np.ndarray,
                           violations: Optional[np.ndarray]) -> bool:
    """Deb's feasibility rule (Deb 2000/2002, constrained NSGA-II).

    * feasible beats infeasible;
    * between two infeasible solutions the smaller total violation wins;
    * between two feasible solutions Pareto dominance decides.
    """
    vi = _violates(i, violations)
    vj = _violates(j, violations)
    if vi > 0 or vj > 0:
        if vi == 0.0 and vj > 0.0:
            return True
        if vi > 0.0 and vj == 0.0:
            return False
        if vi != vj:
            return vi < vj
        return False
    return _dominates(objectives[i], objectives[j])


def fast_constrained_non_dominated_sort(objectives: np.ndarray,
                                        violations: Optional[np.ndarray] = None
                                        ) -> List[List[int]]:
    """Fast non-dominated sorting with constraint handling (feasibility rule).

    ``violations`` is the total constraint violation per solution
    (``0`` = feasible, ``> 0`` = amount of violation).  With ``violations=None``
    this degenerates exactly to :func:`fast_non_dominated_sort`.
    """
    if violations is None:
        return fast_non_dominated_sort(objectives)

    N = objectives.shape[0]
    domination_count = np.zeros(N, dtype=int)
    dominated_set: List[List[int]] = [[] for _ in range(N)]

    for i in range(N):
        for j in range(i + 1, N):
            if _constrained_dominates(i, j, objectives, violations):
                dominated_set[i].append(j)
                domination_count[j] += 1
            elif _constrained_dominates(j, i, objectives, violations):
                dominated_set[j].append(i)
                domination_count[i] += 1

    fronts: List[List[int]] = []
    current_front = [i for i in range(N) if domination_count[i] == 0]
    while current_front:
        fronts.append(list(current_front))
        next_front = []
        for i in current_front:
            for j in dominated_set[i]:
                domination_count[j] -= 1
                if domination_count[j] == 0:
                    next_front.append(j)
        current_front = next_front
    return fronts


def crowding_distance(objectives: np.ndarray, front: List[int]) -> np.ndarray:
    """
    Compute crowding distance for solutions in a front.
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
        if obj_range < 1e-14:
            continue

        for k in range(1, n - 1):
            idx = front[sorted_idx[k]]
            distances[idx] += (front_objs[sorted_idx[k + 1], m] -
                               front_objs[sorted_idx[k - 1], m]) / obj_range

    return distances


def compute_hypervolume_2d(pareto_objs: np.ndarray, reference_point: np.ndarray) -> float:
    """Exact 2D hypervolume by sorting."""
    if len(pareto_objs) == 0:
        return 0.0
    
    valid = np.all(pareto_objs < reference_point, axis=1)
    pts = pareto_objs[valid]
    
    if len(pts) == 0:
        return 0.0
    
    sorted_idx = np.argsort(pts[:, 0])
    pts = pts[sorted_idx]
    
    hv = 0.0
    prev_y = reference_point[1]
    for i in range(len(pts)):
        width = reference_point[0] - pts[i, 0]
        height = prev_y - pts[i, 1]
        if height > 0:
            hv += width * height
        prev_y = min(prev_y, pts[i, 1])
    
    return hv


class NSGA2:
    """
    NSGA-II Multi-Objective Optimizer.
    """
    
    def __init__(self,
                 n_var: int,
                 n_obj: int,
                 pop_size: int = 100,
                 n_gen: int = 200,
                 crossover_prob: float = 0.9,
                 crossover_eta: float = 20.0,
                 mutation_prob: Optional[float] = None,
                 mutation_eta: float = 20.0,
                 seed: int = 42):
        self.n_var = n_var
        self.n_obj = n_obj
        self.pop_size = pop_size
        self.n_gen = n_gen
        self.cx_prob = crossover_prob
        self.cx_eta = crossover_eta
        self.mut_prob = mutation_prob if mutation_prob is not None else (1.0 / n_var)
        self.mut_eta = mutation_eta
        self.seed = seed
        self.rng = np.random.RandomState(seed)
        
        self.history = {
            'pareto_fronts': [],
            'hypervolume': [],
            'gd': [],
            'igd': [],
            'spread': [],
            'generation': [],
            'n_feasible': [],
            'min_violation': []
        }
    
    def optimize(self,
                 objective_fn: Callable[[np.ndarray], np.ndarray],
                 lb: np.ndarray,
                 ub: np.ndarray,
                 reference_point: Optional[np.ndarray] = None,
                 true_pareto: Optional[np.ndarray] = None,
                 verbose: bool = False,
                 constraint_fn: Optional[Callable[[np.ndarray], np.ndarray]] = None,
                 mutation_schedule: Optional[Callable[[int, int], float]] = None,
                 ) -> Dict:
        """Run NSGA-II optimization.

        ``constraint_fn(x)`` returns a vector of constraint values that must be
        ``<= 0`` to be satisfied; the total violation ``sum(max(0, g))`` drives
        Deb's feasibility rule.  ``None`` (default) = unconstrained.

        ``mutation_schedule(generation, n_gen) -> p_m`` optionally overrides the
        per-variable mutation probability per generation (used for annealing).
        """
        t0 = time.time()
        rng = self.rng
        
        # Initialize population uniformly
        pop = rng.uniform(lb, ub, size=(self.pop_size, self.n_var))
        
        # Evaluate initial population
        objs = np.array([objective_fn(pop[i]) for i in range(self.pop_size)])
        cv = self._constraint_violation(pop, constraint_fn)
        
        if reference_point is None:
            reference_point = np.max(objs, axis=0) * 1.1 + 1.0
        
        for gen in range(self.n_gen):
            # Pre-compute ranks for tournament selection
            fronts = fast_constrained_non_dominated_sort(objs, cv)
            rank = np.zeros(self.pop_size, dtype=int)
            for r, front in enumerate(fronts):
                for idx in front:
                    rank[idx] = r
            cd = crowding_distance(objs, fronts[0] if fronts else list(range(self.pop_size)))
            
            # Create offspring using pre-computed ranks
            pm = (mutation_schedule(gen, self.n_gen)
                  if mutation_schedule is not None else self.mut_prob)
            offspring = self._create_offspring_fast(pop, objs, rank, cd, lb, ub, rng,
                                                     cv=cv, mutation_prob=pm)
            
            # Evaluate offspring
            off_objs = np.array([objective_fn(offspring[i]) for i in range(self.pop_size)])
            off_cv = self._constraint_violation(offspring, constraint_fn)
            
            # Combine parent + offspring
            combined_pop = np.vstack([pop, offspring])
            combined_objs = np.vstack([objs, off_objs])
            combined_cv = np.concatenate([cv, off_cv])
            
            # Non-dominated sorting (feasibility rule when constraints are given)
            fronts = fast_constrained_non_dominated_sort(combined_objs, combined_cv)
            
            # Environmental selection
            new_pop = np.zeros((self.pop_size, self.n_var))
            new_objs = np.zeros((self.pop_size, self.n_obj))
            fill_count = 0
            
            for front in fronts:
                if fill_count + len(front) <= self.pop_size:
                    for idx in front:
                        new_pop[fill_count] = combined_pop[idx]
                        new_objs[fill_count] = combined_objs[idx]
                        fill_count += 1
                else:
                    remaining = self.pop_size - fill_count
                    cd = crowding_distance(combined_objs, front)
                    sorted_front = sorted(front, key=lambda i: -cd[i])
                    for idx in sorted_front[:remaining]:
                        new_pop[fill_count] = combined_pop[idx]
                        new_objs[fill_count] = combined_objs[idx]
                        fill_count += 1
                    break
            
            pop = new_pop
            objs = new_objs
            # violations of the selected parents (cheap: re-evaluate on the new pop)
            cv = self._constraint_violation(pop, constraint_fn)
            
            # Extract current Pareto front (feasible solutions only when
            # constraints are active: infeasible ones never enter front 0 of a
            # feasibility-rule sort unless the whole population is infeasible)
            pf_fronts = fast_constrained_non_dominated_sort(objs, cv)
            pf_indices = pf_fronts[0] if pf_fronts else []
            pf_objs = objs[pf_indices] if pf_indices else objs[:1]
            
            # Compute metrics (2D exact, 3D exact sweep, MC above that)
            hv = (hypervolume(pf_objs, reference_point)
                  if (len(pf_objs) and self.n_obj >= 2) else 0.0)
            
            gd_val = 0.0
            igd_val = 0.0
            spread_val = 0.0
            if true_pareto is not None and len(pf_objs) > 0:
                gd_val = self._generational_distance(pf_objs, true_pareto)
                igd_val = self._inverted_generational_distance(pf_objs, true_pareto)
                if self.n_obj == 2:
                    spread_val = self._spread_metric(pf_objs, true_pareto)
            
            self.history['pareto_fronts'].append(pf_objs.copy())
            self.history['hypervolume'].append(hv)
            self.history['gd'].append(gd_val)
            self.history['igd'].append(igd_val)
            self.history['spread'].append(spread_val)
            self.history['generation'].append(gen)
            self.history['n_feasible'].append(int(np.sum(cv <= 0)))
            self.history['min_violation'].append(float(np.min(cv)) if len(cv) else 0.0)
            
            if verbose and (gen + 1) % max(1, self.n_gen // 10) == 0:
                print(f"Gen {gen+1}/{self.n_gen}: HV={hv:.4f}, "
                      f"GD={gd_val:.6f}, IGD={igd_val:.6f}, "
                      f"|PF|={len(pf_indices)}")
        
        elapsed = time.time() - t0
        
        final_fronts = fast_constrained_non_dominated_sort(objs, cv)
        final_pf_idx = final_fronts[0] if final_fronts else list(range(min(10, len(objs))))
        
        input_config = {
            'n_var': self.n_var,
            'n_obj': self.n_obj,
            'pop_size': self.pop_size,
            'n_gen': self.n_gen,
            'crossover_prob': self.cx_prob,
            'crossover_eta': self.cx_eta,
            'mutation_prob': self.mut_prob,
            'mutation_eta': self.mut_eta,
            'seed': self.seed,
            'lb': lb.tolist(),
            'ub': ub.tolist()
        }
        input_hash = hashlib.sha256(json.dumps(input_config, sort_keys=True).encode()).hexdigest()[:16]
        
        result = {
            'pareto_front': objs[final_pf_idx],
            'pareto_set': pop[final_pf_idx],
            'all_objectives': objs,
            'all_population': pop,
            'history': self.history,
            'elapsed_seconds': elapsed,
            'input_hash': input_hash,
            'config': input_config,
            'constraint_violation': cv,
            'n_feasible': int(np.sum(cv <= 0)),
        }
        
        return result
    
    def _create_offspring_fast(self, pop, objs, rank, cd, lb, ub, rng, cv=None,
                               mutation_prob: Optional[float] = None):
        """Create offspring via tournament selection + SBX + polynomial mutation.
        Uses pre-computed ranks and crowding distances."""
        N = self.pop_size
        
        # Binary tournament selection using pre-computed rank/cd
        parent_idx = np.zeros(N, dtype=int)
        for i in range(N):
            a, b = rng.randint(0, N, 2)
            if rank[a] < rank[b]:
                parent_idx[i] = a
            elif rank[b] < rank[a]:
                parent_idx[i] = b
            else:
                if cv is not None and cv[a] != cv[b]:
                    # feasibility rule: less violation wins inside the same rank
                    parent_idx[i] = a if cv[a] < cv[b] else b
                    continue
                parent_idx[i] = a if cd[a] >= cd[b] else b
        
        offspring = pop[parent_idx].copy()
        
        # SBX Crossover
        for i in range(0, N - 1, 2):
            if rng.random() < self.cx_prob:
                p1, p2 = offspring[i], offspring[i + 1]
                for j in range(self.n_var):
                    if rng.random() < 0.5:
                        if abs(p1[j] - p2[j]) > 1e-14:
                            u = rng.random()
                            if u <= 0.5:
                                beta = (2.0 * u) ** (1.0 / (self.cx_eta + 1))
                            else:
                                beta = (1.0 / (2.0 * (1.0 - u))) ** (1.0 / (self.cx_eta + 1))
                            offspring[i, j] = 0.5 * ((1 + beta) * p1[j] + (1 - beta) * p2[j])
                            offspring[i + 1, j] = 0.5 * ((1 - beta) * p1[j] + (1 + beta) * p2[j])
        
        # Clip after crossover
        offspring = np.clip(offspring, lb, ub)
        
        # Polynomial Mutation
        pm = self.mut_prob if mutation_prob is None else mutation_prob
        for i in range(N):
            for j in range(self.n_var):
                if rng.random() < pm:
                    u = rng.random()
                    delta = ub[j] - lb[j]
                    if u < 0.5:
                        delta_q = (2.0 * u) ** (1.0 / (self.mut_eta + 1)) - 1.0
                    else:
                        delta_q = 1.0 - (2.0 * (1.0 - u)) ** (1.0 / (self.mut_eta + 1))
                    offspring[i, j] = offspring[i, j] + delta_q * delta
                    offspring[i, j] = np.clip(offspring[i, j], lb[j], ub[j])
        
        return offspring
    
    @staticmethod
    def _constraint_violation(pop: np.ndarray,
                              constraint_fn: Optional[Callable[[np.ndarray], np.ndarray]]
                              ) -> np.ndarray:
        """Total constraint violation per individual (0 = feasible)."""
        if constraint_fn is None:
            return np.zeros(len(pop), dtype=float)
        cv = np.zeros(len(pop), dtype=float)
        for i in range(len(pop)):
            g = np.atleast_1d(np.asarray(constraint_fn(pop[i]), dtype=float))
            cv[i] = float(np.sum(np.maximum(0.0, g)))
        return cv

    @staticmethod
    def _generational_distance(approx: np.ndarray, reference: np.ndarray) -> float:
        if len(approx) == 0:
            return float('inf')
        distances = []
        for a in approx:
            min_d = np.min(np.sqrt(np.sum((reference - a) ** 2, axis=1)))
            distances.append(min_d)
        return np.sqrt(np.sum(np.array(distances) ** 2)) / len(approx)
    
    @staticmethod
    def _inverted_generational_distance(approx: np.ndarray, reference: np.ndarray) -> float:
        if len(approx) == 0:
            return float('inf')
        distances = []
        for r in reference:
            min_d = np.min(np.sqrt(np.sum((approx - r) ** 2, axis=1)))
            distances.append(min_d)
        return np.sum(distances) / len(reference)
    
    @staticmethod
    def _spread_metric(approx: np.ndarray, reference: np.ndarray) -> float:
        if len(approx) < 2:
            return float('inf')
        
        sorted_idx = np.argsort(approx[:, 0])
        sorted_approx = approx[sorted_idx]
        
        d = np.sqrt(np.sum(np.diff(sorted_approx, axis=0) ** 2, axis=1))
        d_mean = np.mean(d)
        
        ref_sorted = reference[np.argsort(reference[:, 0])]
        d_extreme = (np.sqrt(np.sum((sorted_approx[0] - ref_sorted[0]) ** 2)) +
                     np.sqrt(np.sum((sorted_approx[-1] - ref_sorted[-1]) ** 2)))
        
        if d_mean < 1e-14:
            return float('inf')
        
        spread_val = (d_extreme + np.sum(np.abs(d - d_mean))) / (d_extreme + len(d) * d_mean)
        return spread_val
