"""Independent analytic reference layer for the Polynomial-Chaos / UQ engine.

Mission m1972 (infrastructure seat, self-assigned work).  This module is a
**verification reference**, not production code: it re-derives, from textbook
definitions, every quantity that ``backend/services/ice_resistance_uq.py``
claims to compute, and it deliberately shares **no** code with it.

Why an independent layer is needed
----------------------------------
``backend/services/ice_resistance_uq.py`` (PCE + Smolyak sparse grid + Monte
Carlo, exposed at ``/api/v1/ice-uq``) carried **zero** regression tests before
this mission (``grep -rl ice_resistance_uq tests backend/tests`` → 0 files), so
its numbers were unpinned: nothing in the repository could tell a correct
spectral projection from an aliased one.

What is anchored here (all closed form, no fitted parameters)
-------------------------------------------------------------
* ``normal_moment`` / ``uniform_moment`` -- exact raw moments of N(0,1) and
  U(a,b), the ground truth for any quadrature rule.
* ``gh_nodes_weights`` / ``gl_nodes_weights`` -- Gauss-Hermite (probabilists')
  and Gauss-Legendre nodes, obtained from the *orthonormal-polynomial*
  three-term recurrence (Golub-Welsch via ``numpy``), i.e. an independent
  construction from the engine's hand-rolled Jacobi matrix.
* ``ReferencePCE`` -- a full tensor-product Gauss projection on the
  **normalised** Hermite / Legendre basis.  Because the basis is normalised,
  ``E[psi_a psi_b] = delta_ab`` and the statistics are plain coefficient sums
  with no ``alpha!`` bookkeeping: mean ``= c_0``, variance ``= sum_{a!=0} c_a^2``,
  Sobol ``S_d = sum_{a in A_d} c_a^2 / D``.  Being tensor-product, it needs no
  sparse-grid theory and its exactness degree is known by construction
  (``n`` nodes per dimension integrate degree ``2n-1`` exactly).
* ``required_sparse_level`` -- the classical Smolyak/Gauss-Hermite exactness
  rule (a level-``q`` sparse grid integrates every polynomial of total degree
  ``2q+1`` exactly).  This is the *criterion* for the aliasing defect: a
  total-order-``p`` projection must use ``2p <= 2q+1``.
* ``mc_reference`` -- vectorised Monte-Carlo reference on the untruncated
  input law (no silent clamping), so the engine's clamps can be quantified
  instead of hidden.

Citations
---------
* Ghanem & Spanos (1991) *Stochastic Finite Elements* -- PCE statistics and
  Sobol variance decomposition from coefficients.
* Xiu & Karniadakis (2002) SIAM J. Sci. Comput. 24(2) 619-644 -- the
  Askey-scheme correspondence (Hermite <-> normal, Legendre <-> uniform) that
  the ``distribution`` switch of an ``UncertainInput`` is supposed to honour.
* Smolyak (1963); Novak & Ritter (1996) -- sparse-grid exactness degree
  ``2q+1`` for a level-``q`` combination of ``n = q+1``-point Gauss rules.
* Roache (1994) / ASME V&V 20-2009 -- the "quote an error bar or do not quote
  the number" rule that motivates this verification layer.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Sequence, Tuple

import numpy as np

__all__ = [
    "normal_moment",
    "uniform_moment",
    "gh_nodes_weights",
    "gl_nodes_weights",
    "required_sparse_level",
    "sparse_exact_degree",
    "ReferencePCE",
    "mc_reference",
    "sobol_from_coefficients",
]


# ---------------------------------------------------------------------------
# 1. Exact moments (the ground truth for every quadrature check)
# ---------------------------------------------------------------------------

def normal_moment(k: int) -> float:
    """E[Z^k] for Z ~ N(0, 1): 0 for odd k, (k-1)!! for even k."""
    if k < 0:
        raise ValueError("moment order must be non-negative")
    if k % 2:
        return 0.0
    m = 1.0
    for j in range(1, k, 2):
        m *= j
    return m


def uniform_moment(k: int, lo: float = -1.0, hi: float = 1.0) -> float:
    """E[U^k] for U ~ Uniform(lo, hi) = (hi^{k+1} - lo^{k+1}) / ((k+1)(hi-lo))."""
    if hi <= lo:
        raise ValueError("require hi > lo")
    return (hi ** (k + 1) - lo ** (k + 1)) / ((k + 1) * (hi - lo))


# ---------------------------------------------------------------------------
# 2. Quadrature rules built from the orthonormal recurrences
# ---------------------------------------------------------------------------

def gh_nodes_weights(n: int) -> Tuple[np.ndarray, np.ndarray]:
    """n-point Gauss-Hermite rule for the *standard normal* weight.

    Independent construction: the orthonormal probabilists' Hermite
    polynomials satisfy ``He~_{k+1} = (x He~_k - sqrt(k) He~_{k-1})``, i.e.
    Jacobi-matrix off-diagonals ``sqrt(k)``, ``k = 1..n-1``.  ``numpy.linalg.eigh``
    on that matrix gives nodes (eigenvalues) and weights (squared first
    eigenvector components).  Exact for polynomials of degree ``<= 2n-1``.
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    beta = np.sqrt(np.arange(1.0, n))
    J = np.diag(beta, 1) + np.diag(beta, -1)
    nodes, vecs = np.linalg.eigh(J)
    weights = vecs[0, :] ** 2
    order = np.argsort(nodes)
    return nodes[order], weights[order]


def gl_nodes_weights(n: int) -> Tuple[np.ndarray, np.ndarray]:
    """n-point Gauss-Legendre rule on [-1, 1] (uniform weight).

    Nodes/weights come from the companion-matrix eigenvalues of the Legendre
    three-term recurrence (``P_{k+1} = ((2k+1) x P_k - k P_{k-1})/(k+1)``).
    Exact for polynomials of degree ``<= 2n-1``.
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    k = np.arange(1.0, n)
    off = k / np.sqrt(4.0 * k ** 2 - 1.0)
    J = np.diag(off, 1) + np.diag(off, -1)
    nodes, vecs = np.linalg.eigh(J)
    weights = 2.0 * vecs[0, :] ** 2
    order = np.argsort(nodes)
    return nodes[order], weights[order]


def required_sparse_level(max_poly_order: int) -> int:
    """Smallest sparse-grid level whose exactness covers a projection of order p.

    A level-``q`` Smolyak combination of ``n = q+1``-point Gauss rules is exact
    for every polynomial of total degree ``<= 2q+1``.  A projection of
    total-degree-``p`` coefficients integrates products of degree ``2p``, hence

        2p <= 2q + 1  <=>  q >= p - 1/2  <=>  q = p  (q = max_level - 1)

    i.e. ``max_level >= max_poly_order + 1``.  Using a smaller level aliases
    the higher spectral content onto the retained coefficients.
    """
    if max_poly_order < 0:
        raise ValueError("order must be non-negative")
    return max_poly_order + 1


def sparse_exact_degree(max_level: int) -> int:
    """Exactness degree of a level-``max_level`` sparse grid (``2q+1``, ``q=max_level-1``)."""
    return 2 * (max_level - 1) + 1


# ---------------------------------------------------------------------------
# 3. Independent PCE reference (tensor Gauss projection, normalised basis)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReferenceInput:
    """An uncertain input in the form the *math* needs (not the engine's form)."""

    name: str
    distribution: str  # "normal" | "uniform"
    mean: float
    std: float

    @property
    def half_width(self) -> float:
        """Uniform half-width matching the declared std (std = width/sqrt(12))."""
        return self.std * math.sqrt(3.0)


def _basis_and_norm(alpha: Sequence[int], dists: Sequence[str], xi: np.ndarray):
    """Normalised tensor basis psi_alpha(xi) for a batch of rows."""
    out = np.ones(xi.shape[0])
    for d, a in enumerate(alpha):
        if a == 0:
            continue
        if dists[d] == "normal":
            # normalised probabilists' Hermite: He_a / sqrt(a!)
            out = out * np.polynomial.hermite_e.hermeval(xi[:, d], [0] * a + [1]) \
                / math.sqrt(math.factorial(a))
        elif dists[d] == "uniform":
            # normalised Legendre: sqrt(2a+1) P_a on the reference interval
            out = out * math.sqrt(2 * a + 1) * np.polynomial.legendre.legval(
                xi[:, d], [0] * a + [1])
        else:  # pragma: no cover - guarded by ReferenceInput construction
            raise ValueError(f"unsupported distribution {dists[d]!r}")
    return out


def _multi_index_set(max_order: int, dim: int) -> List[Tuple[int, ...]]:
    idx: List[Tuple[int, ...]] = []
    for total in range(max_order + 1):
        for combo in itertools.combinations_with_replacement(range(dim), total):
            a = [0] * dim
            for c in combo:
                a[c] += 1
            idx.append(tuple(a))
    return idx


class ReferencePCE:
    """Total-order PCE on a full tensor Gauss grid (independent of the engine).

    Uses the normalised basis, so
        mean     = c_0
        variance = sum_{|a|>0} c_a^2
        S_d      = sum_{a: a_d>0, a_k=0 for k!=d} c_a^2 / variance
        S_T,d    = sum_{a: a_d>0}            c_a^2 / variance
    Parametrised by the *exactness degree* it needs, so the caller can see the
    cost/precision trade-off explicitly instead of guessing a level.
    """

    def __init__(self, inputs: Sequence[ReferenceInput], max_order: int,
                 points_per_dim: int | None = None):
        if points_per_dim is None:
            # n points/dim integrate degree 2n-1 -> exact for products of degree 2p
            points_per_dim = max_order + 1
        self.inputs = list(inputs)
        self.dim = len(self.inputs)
        self.max_order = max_order
        self.points_per_dim = points_per_dim
        self.multi_indices = _multi_index_set(max_order, self.dim)
        self._rules = []
        for inp in self.inputs:
            if inp.distribution == "normal":
                # Gauss-Hermite weights already integrate against the standard
                # normal pdf -> they sum to 1 (a probability measure).
                self._rules.append(gh_nodes_weights(points_per_dim))
            elif inp.distribution == "uniform":
                # Gauss-Legendre weights integrate against Lebesgue dx on
                # [-1, 1] and sum to 2; halving turns them into the uniform
                # probability measure (this is the trap that silently doubles
                # every uniform-input statistic).
                n_, w_ = gl_nodes_weights(points_per_dim)
                self._rules.append((n_, 0.5 * w_))
            else:
                raise ValueError(f"unsupported distribution {inp.distribution!r}")
        # standardised quadrature grid (xi) + physical grid (x)
        per_dim = [r[0] for r in self._rules]
        per_dim_w = [r[1] for r in self._rules]
        xi_rows, w_rows = [], []
        for combo in itertools.product(*[range(points_per_dim)] * self.dim):
            xi_rows.append([per_dim[d][combo[d]] for d in range(self.dim)])
            w = 1.0
            for d in range(self.dim):
                w *= per_dim_w[d][combo[d]]
            w_rows.append(w)
        self.xi = np.array(xi_rows, dtype=float)
        self.w = np.array(w_rows, dtype=float)
        self.x_phys = np.empty_like(self.xi)
        for d, inp in enumerate(self.inputs):
            if inp.distribution == "normal":
                self.x_phys[:, d] = inp.mean + inp.std * self.xi[:, d]
            else:
                self.x_phys[:, d] = inp.mean + inp.half_width * self.xi[:, d]

    # -- coefficient estimation ------------------------------------------------
    def fit(self, model: Callable[..., float]) -> "ReferencePCE":
        """Project ``model`` onto the basis (quadrature projection, normalised basis)."""
        f = np.array([model(*row) for row in self.x_phys], dtype=float)
        self.model_values = f
        self.coefficients = np.zeros(len(self.multi_indices))
        for j, alpha in enumerate(self.multi_indices):
            self.coefficients[j] = float(np.sum(self.w * f * _basis_and_norm(alpha, self._dists, self.xi)))
        return self

    @property
    def _dists(self) -> List[str]:
        return [i.distribution for i in self.inputs]

    @property
    def n_evaluations(self) -> int:
        return len(self.w)

    # -- statistics ------------------------------------------------------------
    def statistics(self) -> Dict[str, float]:
        c2 = self.coefficients ** 2
        var = float(c2.sum() - c2[0])
        return {"mean": float(self.coefficients[0]),
                "variance": var,
                "std": math.sqrt(max(var, 0.0))}

    def evaluate(self, x_phys: np.ndarray) -> float:
        """Evaluate the surrogate at a physical-space point."""
        xi = np.empty(self.dim)
        for d, inp in enumerate(self.inputs):
            if inp.distribution == "normal":
                xi[d] = (x_phys[d] - inp.mean) / inp.std
            else:
                xi[d] = (x_phys[d] - inp.mean) / inp.half_width
        val = 0.0
        for j, alpha in enumerate(self.multi_indices):
            val += self.coefficients[j] * float(_basis_and_norm(alpha, self._dists, xi[None, :])[0])
        return val

    def sobol(self) -> Dict[str, Dict[str, float]]:
        return sobol_from_coefficients(self.coefficients, self.multi_indices,
                                       [i.name for i in self.inputs])


def sobol_from_coefficients(coefficients: np.ndarray,
                            multi_indices: Sequence[Tuple[int, ...]],
                            names: Sequence[str]) -> Dict[str, Dict[str, float]]:
    """Sobol indices of a PCE given in an **orthonormal** basis.

    Independent of the engine's ``alpha!`` weights: the reference layer is
    orthonormal by construction, so the indices are plain variance shares.
    """
    c2 = np.asarray(coefficients, dtype=float) ** 2
    dim = len(names)
    total = float(c2.sum() - c2[0])
    if total <= 0.0:
        return {"first_order": {n: 0.0 for n in names},
                "total_order": {n: 0.0 for n in names},
                "variance": 0.0}
    first = {n: 0.0 for n in names}
    tot = {n: 0.0 for n in names}
    for j, alpha in enumerate(multi_indices):
        if sum(alpha) == 0:
            continue
        for d in range(dim):
            if alpha[d] > 0:
                tot[names[d]] += c2[j]
                if all(alpha[k] == 0 for k in range(dim) if k != d):
                    first[names[d]] += c2[j]
    return {
        "first_order": {k: v / total for k, v in first.items()},
        "total_order": {k: v / total for k, v in tot.items()},
        "variance": total,
    }


# ---------------------------------------------------------------------------
# 4. Monte-Carlo reference on the untruncated input law
# ---------------------------------------------------------------------------

def mc_reference(model: Callable[..., float], inputs: Sequence[ReferenceInput],
                 n_samples: int = 200_000, seed: int = 20261101) -> Dict[str, float]:
    """Vectorised MC mean/std with **no** clamping or truncation of the inputs.

    The engine's ``monte_carlo_reference`` clamps the first four inputs to
    ad-hoc physical floors; that makes its "MC ground truth" solve a different
    problem from the PCE (which never clamps).  This version stays on the law
    that was actually declared, so the difference isolates the clamp effect.
    """
    rng = np.random.default_rng(seed)
    dim = len(inputs)
    x = np.empty((n_samples, dim))
    for d, inp in enumerate(inputs):
        if inp.distribution == "normal":
            x[:, d] = inp.mean + inp.std * rng.standard_normal(n_samples)
        elif inp.distribution == "uniform":
            hw = inp.half_width
            x[:, d] = inp.mean + hw * rng.uniform(-1.0, 1.0, n_samples)
        else:  # pragma: no cover
            raise ValueError(f"unsupported distribution {inp.distribution!r}")
    f = np.array([model(*row) for row in x], dtype=float)
    return {
        "mean": float(f.mean()),
        "std": float(f.std(ddof=0)),
        "variance": float(f.var(ddof=0)),
        "n_samples": n_samples,
        # standard error of the mean: sigma/sqrt(N), used for tolerance setting
        "stderr_mean": float(f.std(ddof=0) / math.sqrt(n_samples)),
    }


# ---------------------------------------------------------------------------
# 5. Manufactured models with closed-form PCE (self-test of this layer)
# ---------------------------------------------------------------------------

def manufactured_linear(inputs: Sequence[ReferenceInput]):
    """f(x) = sum_d (x_d - mean_d)/std_d  -> mean 0, variance dim, S_d = 1/dim."""
    def f(*x):
        return sum((x[d] - inputs[d].mean) / inputs[d].std for d in range(len(inputs)))
    return f


def manufactured_quadratic(inputs: Sequence[ReferenceInput]):
    """f(x) = sum_d ((x_d - mean_d)/std_d)^2 -> mean dim, variance 2*dim (Gaussian)."""
    def f(*x):
        return sum(((x[d] - inputs[d].mean) / inputs[d].std) ** 2 for d in range(len(inputs)))
    return f


def manufactured_interaction(inputs: Sequence[ReferenceInput]):
    """f(x) = z1 + z2 + c*z1*z2 with c=1 -> mean 0, var = 2 + c^2, S1=S2=1/(2+c^2)."""
    def f(*x):
        z1 = (x[0] - inputs[0].mean) / inputs[0].std
        z2 = (x[1] - inputs[1].mean) / inputs[1].std
        return z1 + z2 + z1 * z2
    return f