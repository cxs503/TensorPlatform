"""
CMA-ES (Covariance Matrix Adaptation Evolution Strategy) Optimizer

Lightweight implementation for black-box optimization with 8 parameters.
Suitable for expensive simulation-based optimization (200-500 evaluations).

Reference: Hansen N. "The CMA Evolution Strategy: A Tutorial", 2016.
"""
import numpy as np
from typing import Callable, Dict, List, Tuple, Optional
import json


class CMAES:
    """
    CMA-ES optimizer for bounded optimization.
    
    Features:
    - Handles box constraints via penalty method
    - Fixed seed for reproducibility
    - Convergence detection (stagnation)
    - Full trajectory logging
    """
    
    def __init__(self, dim: int, bounds: np.ndarray, sigma0: float = 0.3,
                 seed: int = 42, pop_size: int = None):
        """
        Initialize CMA-ES optimizer.
        
        Args:
            dim: Dimensionality
            bounds: (dim, 2) array of [lower, upper] bounds
            sigma0: Initial step size (std deviation)
            seed: Random seed for reproducibility
            pop_size: Population size (default: 4 + floor(3*log(dim)))
        """
        self.dim = dim
        self.bounds = bounds
        self.sigma = sigma0
        self.rng = np.random.RandomState(seed)
        self.seed = seed
        
        # Population size (default from CMA-ES literature)
        if pop_size is None:
            self.lambda_ = 4 + int(np.floor(3 * np.log(dim)))
        else:
            self.lambda_ = pop_size
        
        self.mu = self.lambda_ // 2  # Number of parents
        
        # Recombination weights
        self.weights = np.log(self.mu + 0.5) - np.log(np.arange(1, self.mu + 1))
        self.weights = self.weights / np.sum(self.weights)
        self.mueff = 1.0 / np.sum(self.weights**2)
        
        # Adaptation parameters
        self.cc = (4 + self.mueff / self.dim) / (self.dim + 4 + 2 * self.mueff / self.dim)
        self.cs = (self.mueff + 2) / (self.dim + self.mueff + 5)
        self.c1 = 2 / ((self.dim + 1.3)**2 + self.mueff)
        self.cmu = min(1 - self.c1, 
                       2 * (self.mueff - 2 + 1/self.mueff) / ((self.dim + 2)**2 + self.mueff))
        self.damps = 1 + 2 * max(0, np.sqrt((self.mueff - 1) / (self.dim + 1)) - 1) + self.cs
        
        # Initialize state
        self.mean = np.zeros(dim)  # Start at center of normalized space
        self.C = np.eye(dim)  # Covariance matrix
        self.pc = np.zeros(dim)  # Evolution path for C
        self.ps = np.zeros(dim)  # Evolution path for sigma
        
        # Chi-N expectation
        self.chiN = np.sqrt(self.dim) * (1 - 1/(4*self.dim) + 1/(21*self.dim**2))
        
        # Tracking
        self.evaluation_count = 0
        self.history = []
        self.psd_repairs = 0   # number of eigen-clamps applied to C (diagnostics)
        
    def sample_population(self) -> np.ndarray:
        """Sample population from current distribution."""
        # Sample from N(0, C)
        try:
            L = np.linalg.cholesky(self.C)
        except np.linalg.LinAlgError:
            # C lost positive definiteness (round-off).  Repair by eigen-clamp
            # instead of silently replacing it with an isotropic matrix, which
            # would discard the whole adaptation state.
            L = np.linalg.cholesky(self._ensure_spd())
        
        # Generate samples
        pop = np.zeros((self.lambda_, self.dim))
        for i in range(self.lambda_):
            z = self.rng.randn(self.dim)
            pop[i] = self.mean + self.sigma * (L @ z)
        
        # Clip to bounds
        pop = np.clip(pop, self.bounds[:, 0], self.bounds[:, 1])
        
        return pop
    
    def update(self, population: np.ndarray, fitnesses: np.ndarray):
        """
        Update distribution based on selected parents.
        
        Args:
            population: (lambda_, dim) array of candidates
            fitnesses: (lambda_,) array of fitness values (lower is better)
        """
        # Sort by fitness
        indices = np.argsort(fitnesses)
        selected = population[indices[:self.mu]]
        
        # Update mean (kept inside the box: the search distribution is centred
        # in the feasible region, otherwise clipping biases every sample)
        old_mean = self.mean.copy()
        self.mean = np.sum(self.weights[:, None] * selected, axis=0)
        self.mean = np.clip(self.mean, self.bounds[:, 0], self.bounds[:, 1])
        
        # Update evolution paths
        y = (self.mean - old_mean) / self.sigma
        self.ps = (1 - self.cs) * self.ps + np.sqrt(self.cs * (2 - self.cs) * self.mueff) * y
        
        # Check for conjugate evolution path update
        hsig = (np.linalg.norm(self.ps) / np.sqrt(1 - (1 - self.cs)**(2 * (self.evaluation_count / self.lambda_ + 1))) 
                < (1.4 + 2/(self.dim + 1)) * self.chiN)
        
        self.pc = (1 - self.cc) * self.pc
        if hsig:
            self.pc += np.sqrt(self.cc * (2 - self.cc) * self.mueff) * y
        
        # Update covariance matrix
        try:
            C_old = self.C.copy()
            
            # Rank-one update (Hansen 2016 eq. 47: hsig correction uses C_old)
            self.C = (1 - self.c1 - self.cmu) * C_old
            self.C += self.c1 * (np.outer(self.pc, self.pc) +
                                 (1 - hsig) * self.cc * (2 - self.cc) * C_old)
            
            # Rank-mu update
            for i in range(self.mu):
                diff = (selected[i] - old_mean) / self.sigma
                self.C += self.cmu * self.weights[i] * np.outer(diff, diff)
            
            # Ensure symmetry
            self.C = (self.C + self.C.T) / 2
            
        except (np.linalg.LinAlgError, ValueError):
            # Keep C unchanged if update fails
            pass
        
        # Update step size
        self.sigma *= np.exp((self.cs / self.damps) * (np.linalg.norm(self.ps) / self.chiN - 1))
        
        # Clamp sigma
        self.sigma = np.clip(self.sigma, 1e-10, 1e3)
        
        self.evaluation_count += self.lambda_
    
    def _ensure_spd(self) -> np.ndarray:
        """Clamp C back to positive definiteness (eigen-value floor) and return it."""
        self.C = (self.C + self.C.T) / 2
        w, V = np.linalg.eigh(self.C)
        if w.min() <= 0.0:
            floor = max(1e-20, 1e-14 * float(np.max(w)))
            w = np.maximum(w, floor)
            self.C = (V * w) @ V.T
            self.C = (self.C + self.C.T) / 2
            self.psd_repairs += 1
        return self.C

    def check_convergence(self, recent_fitnesses: List[float], 
                         patience: int = 20, rel_tol: float = 1e-5) -> Tuple[bool, str]:
        """
        Check convergence based on fitness stagnation.
        
        Args:
            recent_fitnesses: List of recent best fitness values
            patience: Number of generations without improvement
            rel_tol: Relative tolerance for improvement detection
        
        Returns:
            (converged, reason) tuple
        """
        if len(recent_fitnesses) < patience:
            return False, "Not enough generations"
        
        recent = recent_fitnesses[-patience:]
        best_so_far = recent[0]
        current_best = recent[-1]
        
        # Check for relative improvement
        if best_so_far > 0:
            rel_improvement = abs(best_so_far - current_best) / abs(best_so_far)
        else:
            rel_improvement = abs(best_so_far - current_best)
        
        # Check for stagnation
        if rel_improvement < rel_tol:
            return True, f"No significant improvement (rel<{rel_tol}) in last {patience} generations"
        
        # Check for sigma collapse
        if self.sigma < 1e-6:
            return True, f"Step size collapsed (sigma={self.sigma:.2e})"
        
        return False, "Still improving"


def optimize(objective_fn: Callable, dim: int, bounds: np.ndarray,
            max_evals: int = 300, sigma0: float = 0.3, seed: int = 42,
            patience: int = 20, verbose: bool = True) -> Dict:
    """
    Run CMA-ES optimization.
    
    Args:
        objective_fn: Function f(x) -> fitness (lower is better)
        dim: Dimensionality
        bounds: (dim, 2) array of bounds
        max_evals: Maximum number of evaluations
        sigma0: Initial step size
        seed: Random seed
        patience: Generations without improvement for convergence
        verbose: Print progress
    
    Returns:
        Optimization result dictionary
    """
    optimizer = CMAES(dim, bounds, sigma0, seed)

    converged, reason = False, "Max evaluations reached"
    best_fitness = np.inf
    best_params = None
    generation = 0
    recent_best = []
    
    while optimizer.evaluation_count < max_evals:
        # Sample and evaluate population
        population = optimizer.sample_population()
        fitnesses = np.array([objective_fn(x) for x in population])
        
        # Track best
        gen_best_idx = np.argmin(fitnesses)
        gen_best_fitness = fitnesses[gen_best_idx]
        gen_best_params = population[gen_best_idx]
        
        if gen_best_fitness < best_fitness:
            best_fitness = gen_best_fitness
            best_params = gen_best_params.copy()
        
        recent_best.append(gen_best_fitness)
        
        # Log
        optimizer.history.append({
            "generation": generation,
            "eval_count": optimizer.evaluation_count,
            "best_fitness": float(gen_best_fitness),
            "mean_fitness": float(np.mean(fitnesses)),
            "sigma": float(optimizer.sigma),
            "best_params": gen_best_params.tolist()
        })
        
        if verbose and (generation % 5 == 0 or gen_best_fitness <= best_fitness):
            print(f"Gen {generation:3d} | Eval {optimizer.evaluation_count:4d} | "
                  f"Best {gen_best_fitness:.8f} | σ={optimizer.sigma:.4f}")
        
        # Update distribution
        optimizer.update(population, fitnesses)
        
        # Check convergence
        converged, reason = optimizer.check_convergence(recent_best, patience)
        if converged:
            if verbose:
                print(f"Converged: {reason}")
            break
        
        generation += 1
    
    return {
        "best_params": best_params,
        "best_fitness": best_fitness,
        "evaluations": optimizer.evaluation_count,
        "generations": generation + 1,
        "history": optimizer.history,
        "converged": converged,
        "convergence_reason": reason,
        "psd_repairs": optimizer.psd_repairs
    }
