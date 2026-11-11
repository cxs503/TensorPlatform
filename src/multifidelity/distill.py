"""Teacher→student distillation for high-fidelity → low-fidelity transfer.

The framework in this module is deliberately solver agnostic; it only needs

* a **teacher**: an expensive, high-fidelity evaluator returning a *distribution*
  over the predicted scalar (a grid-refined ensemble is used here, so the spread
  is the discretisation uncertainty rather than a modelling guess);
* a **student**: a cheap evaluator with a scalar head plus a scale ``sigma`` from
  which a distribution over the same discretised output axis is induced.

Soft targets live on a discretised output axis ``c_b``:

    p_teacher(b) = softmax_b( -(y_t - c_b)^2 / (2 sigma_t^2) )
    p_student(b) = softmax_b( -(y_s - c_b)^2 / (2 sigma_s^2) )

and the distillation objective is the standard one

    L = alpha * L_task + (1 - alpha) * L_KD,      L_KD = KL(p_teacher || p_student)

with ``L_task`` the plain regression loss against the teacher's mean label.  The
``sigma`` of the teacher is the ensemble spread, so ``p_teacher`` is a genuine
soft label: it says *how much* the high-fidelity answer is worth, which a hard
label cannot.  ``KL`` is used verbatim (its entropy term is kept, not dropped)
so the reported number is the divergence, not a cross-entropy proxy.

The teacher used for the platform acceptance study is a grid-refined solution of
a steady 1D convection–diffusion boundary-value problem; the *reference* used to
score both models is a Richardson extrapolation of two much finer grids, i.e. a
third, independent number rather than the teacher's own mean.

Only the standard library and numpy are used.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

TWO_PI = 2.0 * np.pi


# --------------------------------------------------------------------------- #
# Teacher: grid-refined high-fidelity operator
# --------------------------------------------------------------------------- #
def _thomas(lower: Sequence[float], diag: Sequence[float],
            upper: Sequence[float], rhs: Sequence[float]) -> List[float]:
    """Tridiagonal solve, pure python so the cost is a real solver cost."""
    n = len(diag)
    cp = [0.0] * n
    dp = [0.0] * n
    cp[0] = upper[0] / diag[0]
    dp[0] = rhs[0] / diag[0]
    for i in range(1, n):
        m = diag[i] - lower[i] * cp[i - 1]
        if abs(m) < 1e-300:
            raise ZeroDivisionError("singular tridiagonal system")
        cp[i] = (upper[i] / m) if i < n - 1 else 0.0
        dp[i] = (rhs[i] - lower[i] * dp[i - 1]) / m
    x = [0.0] * n
    x[-1] = dp[-1]
    for i in range(n - 2, -1, -1):
        x[i] = dp[i] - cp[i] * x[i + 1]
    return x


class GridOperator:
    """Steady 1D convection–diffusion BVP on a uniform grid.

    ``v du/dx = kappa d2u/dx2 + s(x)``, ``u(0) = u(1) = 0``,
    ``s(x) = amp * (1 + shape * sin(2*pi*x))``.

    Returns the outlet flux ``phi = -kappa du/dx(1)`` — a boundary quantity, which
    is what a coupled solver would actually send across an interface.  Central
    differences + Thomas algorithm: ``O(n)`` work, ``O(h^2)`` accuracy.
    """

    def __init__(self, n_cells: int = 256):
        if n_cells < 8:
            raise ValueError("n_cells too small")
        self.n_cells = int(n_cells)

    def solve(self, v: float, kappa: float, amp: float, shape: float) -> float:
        n = self.n_cells
        h = 1.0 / n
        n_int = n - 1
        lower = [-kappa / h ** 2 - v / (2 * h)] * n_int
        diag = [2 * kappa / h ** 2] * n_int
        upper = [-kappa / h ** 2 + v / (2 * h)] * n_int
        lower[0] = 0.0
        upper[-1] = 0.0
        rhs = []
        for i in range(1, n):
            x = i * h
            rhs.append(amp * (1.0 + shape * math.sin(TWO_PI * x)))
        u_int = _thomas(lower, diag, upper, rhs)
        # u(1) = 0, so the outlet flux is purely diffusive; 2nd-order one-sided slope
        u_nm1 = u_int[-1]
        u_nm2 = u_int[-2]
        slope = (3 * 0.0 - 4 * u_nm1 + u_nm2) / (2 * h)
        return -kappa * slope


class HighFidelityTeacher:
    """Ensemble of grid refinements: mean = answer, spread = uncertainty."""

    def __init__(self, grid_sizes: Sequence[int] = (128, 192, 256, 384),
                 reference_sizes: Sequence[int] = (512, 1024)):
        self.grid_sizes = tuple(int(n) for n in grid_sizes)
        self.reference_sizes = tuple(int(n) for n in reference_sizes)
        self._ops = {n: GridOperator(n) for n in set(self.grid_sizes) | set(self.reference_sizes)}

    def fine(self, params: Sequence[float]) -> np.ndarray:
        v, kappa, amp, shape = (float(p) for p in params)
        return np.array([self._ops[n].solve(v, kappa, amp, shape) for n in self.grid_sizes])

    def evaluate(self, params: Sequence[float]) -> Tuple[float, float]:
        """(mean, spread) of the refinement ensemble."""
        values = self.fine(params)
        return float(np.mean(values)), float(np.std(values, ddof=1))

    def reference(self, params: Sequence[float]) -> float:
        """Richardson-extrapolated value from two much finer grids (2nd order)."""
        v, kappa, amp, shape = (float(p) for p in params)
        coarse_n, fine_n = self.reference_sizes
        coarse = self._ops[coarse_n].solve(v, kappa, amp, shape)
        fine = self._ops[fine_n].solve(v, kappa, amp, shape)
        return float(fine + (fine - coarse) / 3.0)

    # -- batching ----------------------------------------------------------
    def evaluate_batch(self, X: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        means, spreads = [], []
        for row in X:
            mean, spread = self.evaluate(row)
            means.append(mean)
            spreads.append(spread)
        return np.asarray(means), np.asarray(spreads)

    def reference_batch(self, X: np.ndarray) -> np.ndarray:
        return np.asarray([self.reference(row) for row in X])


# --------------------------------------------------------------------------- #
# Data generation
# --------------------------------------------------------------------------- #
PARAM_NAMES = ("vel", "diff", "amp", "shape")


def sample_params(n: int, seed: int = 0) -> np.ndarray:
    """Latin-hypercube style sampling of the four inputs (domain neutral)."""
    rng = np.random.default_rng(seed)
    dims = len(PARAM_NAMES)
    X = np.empty((n, dims))
    for j in range(dims):
        edges = (np.arange(n + 1) + rng.random()) / n
        pick = rng.permutation(n)
        X[:, j] = edges[pick]
    # map uniforms to the parameter box (log for the two scale-like inputs)
    X[:, 0] = 0.2 * (2.0 / 0.2) ** X[:, 0]          # velocity   [0.2, 2.0]
    X[:, 1] = 0.01 * (0.2 / 0.01) ** X[:, 1]        # diffusivity[0.01, 0.2]
    X[:, 2] = 0.5 + 1.5 * X[:, 2]                   # source amp [0.5, 2.0]
    X[:, 3] = -1.5 + 3.0 * X[:, 3]                  # source shape [-1.5, 1.5]
    return X


def _features(X: np.ndarray) -> np.ndarray:
    """Standardisation used by the student and the closed-form baseline."""
    v, kap, amp, shape = X[:, 0], X[:, 1], X[:, 2], X[:, 3]
    return np.column_stack([np.log(v), np.log(kap), amp, shape])


# --------------------------------------------------------------------------- #
# Student: small numpy MLP with a Gaussian-to-bins soft head
# --------------------------------------------------------------------------- #
def _mm(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Small-matrix product.

    ``einsum`` without BLAS is used deliberately: for the layer sizes here a
    threaded gemm spends more time in the thread pool than in arithmetic
    (measured 15.5 ms vs 0.22 ms per 280x32 @ 32x32 product on a 128-core node),
    and the non-BLAS path is also bit-reproducible across thread settings.
    """
    return np.einsum("ij,jk->ik", a, b, optimize=False)


def _mm_tn(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """``a.T @ b`` for small matrices (see :func:`_mm`)."""
    return np.einsum("ij,ik->jk", a, b, optimize=False)


def _softmax_logits(mu: np.ndarray, sigma: np.ndarray, centers: np.ndarray) -> np.ndarray:
    """Per-row logits of a Gaussian read-out centred on ``mu`` with width ``sigma``."""
    z = -0.5 * ((mu[:, None] - centers[None, :]) / sigma[:, None]) ** 2
    return z


def _log_softmax(z: np.ndarray) -> np.ndarray:
    m = np.max(z, axis=1, keepdims=True)
    shifted = z - m
    return shifted - np.log(np.sum(np.exp(shifted), axis=1, keepdims=True))


def _entropy_rows(p: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        terms = np.where(p > 0, p * np.log(np.where(p > 0, p, 1.0)), 0.0)
    return -np.sum(terms, axis=1)


@dataclass
class DistillConfig:
    """Student capacity, distillation weight and soft-label temperature.

    ``temperature_rel`` is the soft-label bandwidth as a fraction of the output
    standard deviation.  It is the regression analogue of the softmax
    temperature: small => the teacher distribution collapses to a delta (KD
    degenerates into a hard-label fit), large => the label is blurred.
    """

    width: int = 32
    alpha: float = 0.5
    bins: int = 101
    epochs: int = 400
    lr: float = 5e-3
    seed: int = 0
    temperature_rel: float = 0.10
    l2: float = 1e-6


class StudentSurrogate:
    """Two hidden tanh layers with a scalar head."""

    def __init__(self, config: DistillConfig, centers: np.ndarray,
                 x_mean: np.ndarray, x_std: np.ndarray,
                 y_center: float, y_scale: float):
        self.config = config
        self.centers = np.asarray(centers, dtype=float)
        self.x_mean = np.asarray(x_mean, dtype=float)
        self.x_std = np.asarray(x_std, dtype=float)
        self.y_center = float(y_center)
        self.y_scale = float(y_scale)
        self.temperature = max(config.temperature_rel, 1e-4) * self.y_scale
        rng = np.random.default_rng(config.seed)
        d_in = len(self.x_mean)
        w = config.width
        self.params: Dict[str, np.ndarray] = {
            "W1": rng.normal(0, 1.0 / math.sqrt(d_in), (d_in, w)),
            "b1": np.zeros(w),
            "W2": rng.normal(0, 1.0 / math.sqrt(w), (w, w)),
            "b2": np.zeros(w),
            "W3": rng.normal(0, 1.0 / math.sqrt(w), (w, 1)),
            "b3": np.zeros(1),
        }
        self._adam_m = {k: np.zeros_like(v) for k, v in self.params.items()}
        self._adam_v = {k: np.zeros_like(v) for k, v in self.params.items()}
        self._adam_t = 0
        self.history: List[float] = []

    # -- math --------------------------------------------------------------
    def _std(self, X: np.ndarray) -> np.ndarray:
        return (_features(X) - self.x_mean) / self.x_std

    def forward(self, X: np.ndarray):
        x = self._std(X)
        p = self.params
        h1 = np.tanh(_mm(x, p["W1"]) + p["b1"])
        h2 = np.tanh(_mm(h1, p["W2"]) + p["b2"])
        mu = (_mm(h2, p["W3"]) + p["b3"])[:, 0]
        return x, h1, h2, mu

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.forward(np.atleast_2d(X))[3] * self.y_scale + self.y_center

    def log_soft_probs(self, mu_n: np.ndarray, width: np.ndarray | float) -> np.ndarray:
        """Log soft targets of a Gaussian of standard deviation ``width`` on the bin axis."""
        return _log_softmax(_softmax_logits(mu_n, np.broadcast_to(width, mu_n.shape), self.centers))

    # -- training ----------------------------------------------------------
    def objective(self, X_all: np.ndarray, y_hard_n: np.ndarray, hard_mask: np.ndarray,
                  y_soft_n: np.ndarray, sigma_soft_n: np.ndarray):
        """Loss ``alpha*L_task + (1-alpha)*KL`` and its exact gradients.

        Exposed (rather than inlined in :meth:`fit`) so the hand-written
        backpropagation can be checked against finite differences.
        """
        cfg = self.config
        alpha = float(cfg.alpha)
        n_hard = int(np.sum(hard_mask))
        n_soft = int(len(y_soft_n))
        x, h1, h2, mu = self.forward(X_all)
        grad_mu = np.zeros_like(mu)
        l_kd = 0.0
        if alpha < 1.0:
            sig_t = np.sqrt(self.temperature ** 2 + (sigma_soft_n * self.y_scale) ** 2)
            p_t = np.exp(self.log_soft_probs(y_soft_n, sig_t))
            log_p_s = self.log_soft_probs(mu, self.temperature)
            log_p_s = np.where(np.isfinite(log_p_s), log_p_s, -1e3)
            l_kd = float(np.mean(np.maximum(_entropy_rows(p_t) - np.sum(p_t * log_p_s, axis=1), 0.0)))
            p_s = np.exp(log_p_s)
            delta = mu[:, None] - self.centers[None, :]
            grad_mu += (-(1 - alpha) / n_soft) * np.sum((p_s - p_t) * delta, axis=1) / self.temperature ** 2
        la = 0.0
        if alpha > 0.0:
            res_hard = mu[:n_hard] - y_hard_n
            la = float(np.sum(res_hard ** 2) / n_hard)
            grad_mu[:n_hard] += alpha * 2.0 * res_hard / n_hard
        loss = alpha * la + (1 - alpha) * l_kd

        d_out = grad_mu[:, None]
        grads: Dict[str, np.ndarray] = {}
        grads["W3"] = _mm_tn(h2, d_out)
        grads["b3"] = d_out.sum(axis=0)
        d_a2 = _mm(d_out, self.params["W3"].T) * (1.0 - h2 ** 2)
        grads["W2"] = _mm_tn(h1, d_a2)
        grads["b2"] = d_a2.sum(axis=0)
        d_a1 = _mm(d_a2, self.params["W2"].T) * (1.0 - h1 ** 2)
        grads["W1"] = _mm_tn(x, d_a1)
        grads["b1"] = d_a1.sum(axis=0)
        for k in self.params:
            if cfg.l2 and k.startswith("W"):
                loss += cfg.l2 * float(np.sum(self.params[k] ** 2))
                grads[k] = grads[k] + 2.0 * cfg.l2 * self.params[k]
        return float(loss), grads

    def fit(self, X_all: np.ndarray,
            y_hard_n: Optional[np.ndarray],
            hard_mask: np.ndarray,
            y_soft_n: np.ndarray,
            sigma_soft_n: np.ndarray) -> "StudentSurrogate":
        """Train with ``L = alpha*L_task + (1-alpha)*KL(p_teacher || p_student)``."""
        alpha = float(self.config.alpha)
        n_hard = int(np.sum(hard_mask))
        n_soft = int(len(y_soft_n))
        if not bool(np.all(hard_mask[:n_hard])):
            raise ValueError("hard-labelled rows must come first")
        if alpha < 1.0 and n_soft == 0:
            raise ValueError("KD term requested but no soft labels supplied")
        if alpha > 0.0 and n_hard == 0:
            raise ValueError("task term requested but no hard labels supplied")
        for _ in range(self.config.epochs):
            loss, grads = self.objective(X_all, y_hard_n, hard_mask, y_soft_n, sigma_soft_n)
            self.history.append(loss)
            self._adam_step(grads)
        return self

    def _adam_step(self, grads: Dict[str, np.ndarray]) -> None:
        cfg = self.config
        self._adam_t += 1
        b1, b2, eps = 0.9, 0.999, 1e-8
        for k, g in grads.items():
            if cfg.l2 and k.startswith("W"):
                g = g + 2.0 * cfg.l2 * self.params[k]
            self._adam_m[k] = b1 * self._adam_m[k] + (1 - b1) * g
            self._adam_v[k] = b2 * self._adam_v[k] + (1 - b2) * g ** 2
            m_hat = self._adam_m[k] / (1 - b1 ** self._adam_t)
            v_hat = self._adam_v[k] / (1 - b2 ** self._adam_t)
            self.params[k] = self.params[k] - cfg.lr * m_hat / (np.sqrt(v_hat) + eps)


class EmpiricalFormulaStudent:
    """Closed-form low-fidelity alternative: a global power-law regression.

    This is the "empirical formula" rung of the fidelity ladder: a handful of
    coefficients fitted by least squares on the hard labels.  It is included so
    the distillation gain is measured against a real low-fidelity model, not
    only against an untrained network.
    """

    def __init__(self):
        self.coef: Optional[np.ndarray] = None

    @staticmethod
    def _design(X: np.ndarray) -> np.ndarray:
        v, kap, amp, shape = X[:, 0], X[:, 1], X[:, 2], X[:, 3]
        return np.column_stack([
            np.ones(len(X)), np.log(v), np.log(kap), np.log(amp),
            shape, shape ** 2, np.log(v) * shape, np.log(kap) * shape,
        ])

    def fit(self, X: np.ndarray, y: np.ndarray) -> "EmpiricalFormulaStudent":
        A = self._design(X)
        self.coef, *_ = np.linalg.lstsq(A, y, rcond=None)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self.coef is None:
            raise RuntimeError("model is not fitted")
        return self._design(np.atleast_2d(X)) @ self.coef


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #
def rel_rmse(pred: np.ndarray, reference: np.ndarray) -> float:
    """RMSE normalised by the RMS magnitude of the reference."""
    pred = np.asarray(pred, dtype=float)
    reference = np.asarray(reference, dtype=float)
    return float(np.sqrt(np.mean((pred - reference) ** 2)) / np.sqrt(np.mean(reference ** 2)))


def accuracy(pred: np.ndarray, reference: np.ndarray) -> float:
    """Accuracy in percent: ``100 * (1 - rel_rmse)`` (0% = useless, 100% = exact)."""
    return 100.0 * max(0.0, 1.0 - rel_rmse(pred, reference))


def cost_us(fn: Callable[[np.ndarray], np.ndarray], X: np.ndarray,
            repeats: int = 3) -> float:
    """Wall-clock cost per query in microseconds (best of ``repeats``)."""
    X = np.atleast_2d(X)
    fn(X[:1])                       # warm-up (allocation, cache)
    best = float("inf")
    for _ in range(repeats):
        start = time.perf_counter()
        fn(X)
        elapsed = time.perf_counter() - start
        best = min(best, elapsed)
    return 1e6 * best / len(X)


@dataclass
class DistillResult:
    label: str
    alpha: float
    width: int
    val_rel_rmse: float
    val_accuracy: float
    cost_us: float
    speedup: float
    train_loss: float
    kl_final: float
    task_final: float
    n_hard: int
    n_soft: int
    temperature: float = float("nan")

    def to_dict(self) -> Dict[str, object]:
        return {
            "label": self.label,
            "alpha": self.alpha,
            "width": self.width,
            "val_rel_rmse": self.val_rel_rmse,
            "val_accuracy_pct": self.val_accuracy,
            "cost_us": self.cost_us,
            "speedup_vs_teacher": self.speedup,
            "train_loss": self.train_loss,
            "kl_final": self.kl_final,
            "task_final": self.task_final,
            "n_hard": self.n_hard,
            "n_soft": self.n_soft,
            "temperature": self.temperature,
        }


def _make_centers(y_all: np.ndarray, bins: int) -> np.ndarray:
    lo, hi = float(np.min(y_all)), float(np.max(y_all))
    pad = 0.15 * (hi - lo)
    return np.linspace(lo - pad, hi + pad, bins)


def build_student(X_all: np.ndarray, y_all: np.ndarray,
                  config: DistillConfig) -> StudentSurrogate:
    """Construct a student with the standardisation and soft-label axis of ``X_all``."""
    feats = _features(X_all)
    return StudentSurrogate(
        config=config,
        centers=_make_centers(y_all, config.bins),
        x_mean=feats.mean(axis=0),
        x_std=feats.std(axis=0) + 1e-12,
        y_center=float(np.mean(y_all)),
        y_scale=float(np.std(y_all)) + 1e-12,
    )


def train_student(
    X_hard: np.ndarray, y_hard: np.ndarray, sigma_hard: np.ndarray,
    X_soft: np.ndarray, y_soft: np.ndarray, sigma_soft: np.ndarray,
    X_val: np.ndarray, y_val_ref: np.ndarray,
    config: DistillConfig,
    teacher_cost: float,
    label: str = "",
) -> DistillResult:
    """Train one student configuration and score it on the held-out set.

    ``X_hard``/``y_hard`` are the rows that carry a hard label (which is what a
    distillation-disabled control keeps); ``X_soft``/``y_soft``/``sigma_soft``
    are the teacher-annotated rows (hard rows are repeated there so the KD term
    sees the full teacher budget).
    """
    X_all = np.vstack([X_hard, X_soft])
    y_all = np.concatenate([y_hard, y_soft])
    sig_all = np.concatenate([sigma_hard, sigma_soft])
    hard_mask = np.concatenate([np.ones(len(X_hard), bool), np.zeros(len(X_soft), bool)])
    # a KD-disabled arm must not be able to read the hard labels twice
    if config.alpha >= 1.0:
        X_all, y_all, sig_all = X_hard, y_hard, sigma_hard
        hard_mask = np.ones(len(X_hard), bool)

    student = build_student(X_all, y_all, config)
    y_hard_n = (y_hard - student.y_center) / student.y_scale
    student.fit(X_all, y_hard_n, hard_mask,
                (y_all - student.y_center) / student.y_scale, sig_all / student.y_scale)
    pred = student.predict(X_val)
    cost = cost_us(student.predict, X_val)
    return DistillResult(
        label=label or f"alpha={config.alpha:.2f}|w={config.width}",
        alpha=config.alpha,
        width=config.width,
        val_rel_rmse=rel_rmse(pred, y_val_ref),
        val_accuracy=accuracy(pred, y_val_ref),
        cost_us=cost,
        speedup=teacher_cost / cost if cost > 0 else float("inf"),
        train_loss=float(student.history[-1]) if student.history else float("nan"),
        kl_final=float(_final_kl(student, X_all, (y_all - student.y_center) / student.y_scale,
                                sig_all / student.y_scale)),
        task_final=float(_final_task(student, X_hard, y_hard)),
        n_hard=int(len(X_hard)),
        n_soft=int(len(X_soft)),
        temperature=student.temperature,
    )


def _final_kl(student: StudentSurrogate, X_all, y_soft_n, sigma_soft_n) -> float:
    _, _, _, mu = student.forward(X_all)
    log_p_s = student.log_soft_probs(mu, student.temperature)
    log_p_s = np.where(np.isfinite(log_p_s), log_p_s, -1e3)
    sig_t = np.sqrt(student.temperature ** 2 + (sigma_soft_n * student.y_scale) ** 2)
    p_t = np.exp(student.log_soft_probs(y_soft_n, sig_t))
    kl = _entropy_rows(p_t) - np.sum(p_t * log_p_s, axis=1)
    return float(np.mean(np.maximum(kl, 0.0)))


def _final_task(student: StudentSurrogate, X_hard, y_hard) -> float:
    if len(X_hard) == 0:
        return float("nan")
    pred = student.predict(X_hard)
    return float(np.sqrt(np.mean((pred - np.asarray(y_hard, float)) ** 2)))


def pareto_front(points: Sequence[DistillResult]) -> List[DistillResult]:
    """Non-dominated set: higher accuracy and lower cost is better."""
    ordered = sorted(points, key=lambda r: (r.cost_us, -r.val_accuracy))
    front: List[DistillResult] = []
    best_acc = -float("inf")
    for point in ordered:
        if point.val_accuracy > best_acc:
            front.append(point)
            best_acc = point.val_accuracy
    return front


def pareto_sweep(
    X_hard, y_hard, sigma_hard, X_soft, y_soft, sigma_soft,
    X_val, y_val_ref, teacher_cost: float,
    widths: Sequence[int] = (4, 8, 16, 32, 64),
    alphas: Sequence[float] = (0.0, 0.25, 0.5, 0.75, 1.0),
    base: Optional[DistillConfig] = None,
) -> Tuple[List[DistillResult], List[DistillResult]]:
    """Sweep student capacity × distillation weight; return (all points, front)."""
    base = base or DistillConfig()
    results: List[DistillResult] = []
    for width in widths:
        for alpha in alphas:
            cfg = DistillConfig(**{**base.__dict__, "width": int(width), "alpha": float(alpha)})
            results.append(train_student(
                X_hard, y_hard, sigma_hard, X_soft, y_soft, sigma_soft,
                X_val, y_val_ref, cfg, teacher_cost=teacher_cost,
                label=f"alpha={alpha:.2f}|w={width}",
            ))
    return results, pareto_front(results)
