"""
Analytical Multi-Objective Test Functions.

Standard benchmark suite for multi-objective optimization:
- ZDT1: Convex Pareto front
- ZDT2: Concave Pareto front
- ZDT3: Disconnected Pareto front
- DTLZ1: Linear Pareto front
- DTLZ2: Spherical Pareto front

Reference:
  Zitzler, E., Deb, K., & Thiele, L. (2000). Comparison of multiobjective
  evolutionary algorithms: Empirical results. Evolutionary Computation, 8(2), 173-195.

  Deb, K., Thiele, L., Laumanns, M., & Zitzler, E. (2002). Scalable test
  problems for evolutionary multiobjective optimization.
"""
import numpy as np
from typing import Tuple


class ZDT1:
    """
    ZDT1 Test Problem — Convex Pareto Front.
    
    f1(x) = x1
    f2(x) = g(x) * [1 - sqrt(f1/g)]
    g(x) = 1 + 9 * sum(x2..xn) / (n-1)
    
    Pareto front: f2 = 1 - sqrt(f1), f1 in [0, 1]
    Pareto set: x2 = x3 = ... = xn = 0
    """
    
    def __init__(self, n_var: int = 30):
        self.n_var = n_var
        self.n_obj = 2
        self.lb = np.zeros(n_var)
        self.ub = np.ones(n_var)
        self.name = "ZDT1"
    
    def evaluate(self, x: np.ndarray) -> np.ndarray:
        """Evaluate ZDT1 for a single solution."""
        f1 = x[0]
        g = 1.0 + 9.0 * np.sum(x[1:]) / (self.n_var - 1)
        f2 = g * (1.0 - np.sqrt(f1 / g))
        return np.array([f1, f2])
    
    def __call__(self, x: np.ndarray) -> np.ndarray:
        return self.evaluate(x)
    
    def true_pareto_front(self, n_points: int = 500) -> np.ndarray:
        """Generate true Pareto front points."""
        f1 = np.linspace(0, 1, n_points)
        f2 = 1.0 - np.sqrt(f1)
        return np.column_stack([f1, f2])


class ZDT2:
    """
    ZDT2 Test Problem — Concave Pareto Front.
    
    f1(x) = x1
    f2(x) = g(x) * [1 - (f1/g)^2]
    g(x) = 1 + 9 * sum(x2..xn) / (n-1)
    
    Pareto front: f2 = 1 - f1^2, f1 in [0, 1]
    Pareto set: x2 = x3 = ... = xn = 0
    """
    
    def __init__(self, n_var: int = 30):
        self.n_var = n_var
        self.n_obj = 2
        self.lb = np.zeros(n_var)
        self.ub = np.ones(n_var)
        self.name = "ZDT2"
    
    def evaluate(self, x: np.ndarray) -> np.ndarray:
        f1 = x[0]
        g = 1.0 + 9.0 * np.sum(x[1:]) / (self.n_var - 1)
        f2 = g * (1.0 - (f1 / g) ** 2)
        return np.array([f1, f2])
    
    def __call__(self, x: np.ndarray) -> np.ndarray:
        return self.evaluate(x)
    
    def true_pareto_front(self, n_points: int = 500) -> np.ndarray:
        f1 = np.linspace(0, 1, n_points)
        f2 = 1.0 - f1 ** 2
        return np.column_stack([f1, f2])


class ZDT3:
    """
    ZDT3 Test Problem — Disconnected Pareto Front.
    
    f1(x) = x1
    f2(x) = g(x) * [1 - sqrt(f1/g) - (f1/g)*sin(10*pi*f1)]
    g(x) = 1 + 9 * sum(x2..xn) / (n-1)
    
    Pareto front: disconnected regions
    Pareto set: x2 = x3 = ... = xn = 0
    """
    
    def __init__(self, n_var: int = 30):
        self.n_var = n_var
        self.n_obj = 2
        self.lb = np.zeros(n_var)
        self.ub = np.ones(n_var)
        self.name = "ZDT3"
    
    def evaluate(self, x: np.ndarray) -> np.ndarray:
        f1 = x[0]
        g = 1.0 + 9.0 * np.sum(x[1:]) / (self.n_var - 1)
        f2 = g * (1.0 - np.sqrt(f1 / g) - (f1 / g) * np.sin(10.0 * np.pi * f1))
        return np.array([f1, f2])
    
    def __call__(self, x: np.ndarray) -> np.ndarray:
        return self.evaluate(x)
    
    def true_pareto_front(self, n_points: int = 500) -> np.ndarray:
        """Generate true Pareto front (disconnected regions)."""
        # ZDT3 has 5 disconnected regions
        regions = [
            (0.0000, 0.0830),
            (0.1822, 0.2577),
            (0.4093, 0.4538),
            (0.6183, 0.6525),
            (0.8250, 0.8518)
        ]
        
        points = []
        for a, b in regions:
            f1 = np.linspace(a, b, n_points // len(regions))
            f2 = 1.0 - np.sqrt(f1) - f1 * np.sin(10.0 * np.pi * f1)
            points.append(np.column_stack([f1, f2]))
        
        return np.vstack(points)


class DTLZ1:
    """
    DTLZ1 Test Problem — Linear Pareto Front.
    
    M-objective problem with (n_var - M + 1) variables in g.
    
    Pareto front: sum(fi) = 0.5
    Pareto set: xm = 0.5 for all m in [M, n_var]
    """
    
    def __init__(self, n_var: int = 7, n_obj: int = 3):
        self.n_var = n_var
        self.n_obj = n_obj
        self.lb = np.zeros(n_var)
        self.ub = np.ones(n_var)
        self.name = "DTLZ1"
    
    def evaluate(self, x: np.ndarray) -> np.ndarray:
        M = self.n_obj
        xm = x[M - 1:]
        
        # g function
        g = 100.0 * (len(xm) + np.sum((xm - 0.5) ** 2 - np.cos(20.0 * np.pi * (xm - 0.5))))
        
        f = np.zeros(M)
        for i in range(M):
            f[i] = 0.5 * (1.0 + g)
            for j in range(M - 1 - i):
                f[i] *= x[j]
            if i > 0:
                f[i] *= (1.0 - x[M - 1 - i])
        
        return f
    
    def __call__(self, x: np.ndarray) -> np.ndarray:
        return self.evaluate(x)
    
    def true_pareto_front(self, n_points: int = 500) -> np.ndarray:
        """Generate true Pareto front for 3 objectives (simplex)."""
        if self.n_obj == 3:
            # Generate points on the simplex sum(fi) = 0.5
            points = []
            n = int(np.sqrt(n_points))
            for i in range(n + 1):
                for j in range(n + 1 - i):
                    f1 = 0.5 * i / n
                    f2 = 0.5 * j / n
                    f3 = 0.5 - f1 - f2
                    if f3 >= -1e-10:
                        points.append([f1, f2, max(0, f3)])
            return np.array(points)
        else:
            # Generic simplex sampling
            from scipy.stats import qmc
            sampler = qmc.Sobol(d=self.n_obj - 1, scramble=False)
            samples = sampler.random(n_points)
            # Map to simplex
            points = np.zeros((n_points, self.n_obj))
            points[:, 0] = 0.5 * samples[:, 0]
            for i in range(1, self.n_obj - 1):
                points[:, i] = 0.5 * samples[:, i] * np.prod(1 - samples[:, :i])
            points[:, -1] = 0.5 - np.sum(points[:, :-1], axis=1)
            return points


class DTLZ2:
    """
    DTLZ2 Test Problem — Spherical Pareto Front.
    
    Pareto front: sum(fi^2) = 1
    Pareto set: xm = 0.5 for all m in [M, n_var]
    """
    
    def __init__(self, n_var: int = 12, n_obj: int = 3):
        self.n_var = n_var
        self.n_obj = n_obj
        self.lb = np.zeros(n_var)
        self.ub = np.ones(n_var)
        self.name = "DTLZ2"
    
    def evaluate(self, x: np.ndarray) -> np.ndarray:
        M = self.n_obj
        xm = x[M - 1:]
        
        # g function
        g = np.sum((xm - 0.5) ** 2)
        
        f = np.zeros(M)
        for i in range(M):
            f[i] = (1.0 + g)
            for j in range(M - 1 - i):
                f[i] *= np.cos(x[j] * np.pi / 2.0)
            if i > 0:
                f[i] *= np.sin(x[M - 1 - i] * np.pi / 2.0)
        
        return f
    
    def __call__(self, x: np.ndarray) -> np.ndarray:
        return self.evaluate(x)
    
    def true_pareto_front(self, n_points: int = 500) -> np.ndarray:
        """Generate true Pareto front for 3 objectives (unit sphere octant)."""
        if self.n_obj == 3:
            # Generate points on unit sphere octant
            theta = np.linspace(0, np.pi / 2, int(np.sqrt(n_points)))
            phi = np.linspace(0, np.pi / 2, int(np.sqrt(n_points)))
            points = []
            for t in theta:
                for p in phi:
                    f1 = np.cos(t)
                    f2 = np.sin(t) * np.cos(p)
                    f3 = np.sin(t) * np.sin(p)
                    points.append([f1, f2, f3])
            return np.array(points)
        else:
            # Generic sphere sampling
            from scipy.stats import qmc
            sampler = qmc.Sobol(d=self.n_obj - 1, scramble=False)
            samples = sampler.random(n_points)
            # Map to sphere
            angles = samples * np.pi / 2
            points = np.zeros((n_points, self.n_obj))
            for i in range(self.n_obj):
                points[:, i] = 1.0
                for j in range(self.n_obj - 1 - i):
                    points[:, i] *= np.cos(angles[:, j])
                if i > 0:
                    points[:, i] *= np.sin(angles[:, self.n_obj - 1 - i])
            return points
