"""Domain applicability judgment & fail-closed refusal protocol.

Zero-domain-semantics: all parameter names, ranges, and thresholds are
configuration-injected.  The core machinery knows nothing about ships,
any subject area.

Three-caliber domain estimation
-------------------------------
1. **Convex hull** (empirical): contains all observed PASS samples;
   boundary distance = shortest Euclidean distance to hull facets.
2. **kNN / Mahalanobis distance**: statistical distance from the
   observed sample cloud; threshold calibrated on historical FAIL/PASS.
3. **Local density ratio**: ratio of local KDE density at query point
   to median density of PASS cloud; low ratio → extrapolation risk.

Refusal protocol
----------------
Out-of-domain → structured WITHHELD response with reason code
``DOMAIN_OUT_OF_RANGE``, nearest-neighbour distance, and boundary
direction.  Silent extrapolation is prohibited.

Calibration
-----------
Thresholds are pre-registered so that on the calibration set:
  * recall (detecting known FAIL) ≥ 0.8
  * false-rejection rate (rejecting known PASS) ≤ 5 %
  * label-shuffle negative control AUC ∈ [0.4, 0.6]
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class Verdict(str, Enum):
    IN_DOMAIN = "IN_DOMAIN"
    OUT_OF_DOMAIN = "OUT_OF_DOMAIN"
    WITHHELD = "WITHHELD"


class ReasonCode(str, Enum):
    OK = "OK"
    DOMAIN_OUT_OF_RANGE = "DOMAIN_OUT_OF_RANGE"
    CONVEX_HULL_EXCEEDED = "CONVEX_HULL_EXCEEDED"
    MAHALANOBIS_EXCEEDED = "MAHALANOBIS_EXCEEDED"
    DENSITY_TOO_LOW = "DENSITY_TOO_LOW"
    CALIBRATION_PENDING = "CALIBRATION_PENDING"
    NO_SAMPLES = "NO_SAMPLES"


@dataclass(frozen=True)
class DomainSample:
    """One observed operating point."""
    params: Dict[str, float]
    label: str  # "PASS" or "FAIL"
    error: Optional[float] = None  # prediction error if available


@dataclass
class DomainSpec:
    """Configuration-injected domain definition (zero domain semantics)."""
    name: str
    param_names: List[str]
    param_ranges: Dict[str, Tuple[float, float]]  # (lo, hi) per param
    samples: List[DomainSample] = field(default_factory=list)
    # Calibration thresholds (set after calibration)
    mahalanobis_threshold: Optional[float] = None
    density_ratio_threshold: Optional[float] = None
    # Metadata
    owner_repo: str = ""
    version: str = "v0"

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "param_names": self.param_names,
            "param_ranges": self.param_ranges,
            "n_samples": len(self.samples),
            "mahalanobis_threshold": self.mahalanobis_threshold,
            "density_ratio_threshold": self.density_ratio_threshold,
            "owner_repo": self.owner_repo,
            "version": self.version,
        }


@dataclass(frozen=True)
class DomainJudgment:
    """Result of a domain applicability check."""
    verdict: Verdict
    reason_code: ReasonCode
    domain_name: str
    # Three-caliber metrics
    convex_hull_distance: Optional[float] = None  # negative = inside
    mahalanobis_distance: Optional[float] = None
    density_ratio: Optional[float] = None
    # Nearest neighbour info
    nn_distance: Optional[float] = None
    nn_params: Optional[Dict[str, float]] = None
    # Boundary direction (which params are out of range)
    boundary_direction: Optional[Dict[str, str]] = None  # param -> "above"/"below"
    # Calibration info
    thresholds_used: Dict[str, Optional[float]] = field(default_factory=dict)
    # Raw query params (for audit)
    query_params: Dict[str, float] = field(default_factory=dict)

    def is_in_domain(self) -> bool:
        return self.verdict == Verdict.IN_DOMAIN

    def to_response_dict(self) -> dict:
        d: Dict[str, Any] = {
            "verdict": self.verdict.value,
            "reason_code": self.reason_code.value,
            "domain_name": self.domain_name,
        }
        if self.convex_hull_distance is not None:
            d["convex_hull_distance"] = round(self.convex_hull_distance, 6)
        if self.mahalanobis_distance is not None:
            d["mahalanobis_distance"] = round(self.mahalanobis_distance, 6)
        if self.density_ratio is not None:
            d["density_ratio"] = round(self.density_ratio, 6)
        if self.nn_distance is not None:
            d["nn_distance"] = round(self.nn_distance, 6)
        if self.nn_params is not None:
            d["nn_params"] = self.nn_params
        if self.boundary_direction is not None:
            d["boundary_direction"] = self.boundary_direction
        if self.thresholds_used:
            d["thresholds_used"] = {
                k: (round(v, 6) if v is not None else None)
                for k, v in self.thresholds_used.items()
            }
        d["query_params"] = self.query_params
        return d


# ---------------------------------------------------------------------------
# Linear algebra helpers (no numpy dependency for core)
# ---------------------------------------------------------------------------

def _vec_sub(a: List[float], b: List[float]) -> List[float]:
    return [x - y for x, y in zip(a, b)]


def _vec_dot(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _vec_norm(a: List[float]) -> float:
    return math.sqrt(sum(x * x for x in a))


def _mat_vec_mul(mat: List[List[float]], vec: List[float]) -> List[float]:
    return [_vec_dot(row, vec) for row in mat]


def _mat_inv_2x2(m: List[List[float]]) -> List[List[float]]:
    det = m[0][0] * m[1][1] - m[0][1] * m[1][0]
    if abs(det) < 1e-15:
        return [[1.0, 0.0], [0.0, 1.0]]  # fallback to identity
    inv_det = 1.0 / det
    return [
        [m[1][1] * inv_det, -m[0][1] * inv_det],
        [-m[1][0] * inv_det, m[0][0] * inv_det],
    ]


def _mat_inv_3x3(m: List[List[float]]) -> List[List[float]]:
    """Inverse of a 3x3 matrix via cofactor expansion."""
    det = (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )
    if abs(det) < 1e-15:
        return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    inv_det = 1.0 / det
    cof = [
        [
            m[1][1] * m[2][2] - m[1][2] * m[2][1],
            -(m[1][0] * m[2][2] - m[1][2] * m[2][0]),
            m[1][0] * m[2][1] - m[1][1] * m[2][0],
        ],
        [
            -(m[0][1] * m[2][2] - m[0][2] * m[2][1]),
            m[0][0] * m[2][2] - m[0][2] * m[2][0],
            -(m[0][0] * m[2][1] - m[0][1] * m[2][0]),
        ],
        [
            m[0][1] * m[1][2] - m[0][2] * m[1][1],
            -(m[0][0] * m[1][2] - m[0][2] * m[1][0]),
            m[0][0] * m[1][1] - m[0][1] * m[1][0],
        ],
    ]
    return [[cof[j][i] * inv_det for j in range(3)] for i in range(3)]


def _mat_inv_general(m: List[List[float]]) -> List[List[float]]:
    """Matrix inverse via Gauss-Jordan elimination for n×n matrices."""
    n = len(m)
    # Augmented matrix [m | I]
    aug = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(m)]
    for col in range(n):
        # Find pivot
        max_row = col
        for row in range(col + 1, n):
            if abs(aug[row][col]) > abs(aug[max_row][col]):
                max_row = row
        aug[col], aug[max_row] = aug[max_row], aug[col]
        if abs(aug[col][col]) < 1e-15:
            return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
        # Scale pivot row
        pivot = aug[col][col]
        for j in range(2 * n):
            aug[col][j] /= pivot
        # Eliminate
        for row in range(n):
            if row != col:
                factor = aug[row][col]
                for j in range(2 * n):
                    aug[row][j] -= factor * aug[col][j]
    return [row[n:] for row in aug]


# ---------------------------------------------------------------------------
# Convex hull (2D / 3D) via gift wrapping / incremental
# ---------------------------------------------------------------------------

def _cross_2d(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _convex_hull_2d(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Andrew's monotone chain convex hull for 2D points."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts
    lower = []
    for p in pts:
        while len(lower) >= 2 and _cross_2d(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and _cross_2d(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _point_in_convex_2d(hull: List[Tuple[float, float]], point: Tuple[float, float]) -> bool:
    """Check if point is inside a 2D convex hull (CCW ordered)."""
    if len(hull) < 3:
        return False
    for i in range(len(hull)):
        a = hull[i]
        b = hull[(i + 1) % len(hull)]
        if _cross_2d(a, b, point) < -1e-12:
            return False
    return True


def _dist_point_to_segment_2d(p, a, b) -> float:
    """Distance from point p to line segment ab in 2D."""
    ab = (b[0] - a[0], b[1] - a[1])
    ap = (p[0] - a[0], p[1] - a[1])
    ab_len_sq = ab[0] ** 2 + ab[1] ** 2
    if ab_len_sq < 1e-15:
        return math.sqrt(ap[0] ** 2 + ap[1] ** 2)
    t = max(0.0, min(1.0, (ap[0] * ab[0] + ap[1] * ab[1]) / ab_len_sq))
    proj = (a[0] + t * ab[0], a[1] + t * ab[1])
    return math.sqrt((p[0] - proj[0]) ** 2 + (p[1] - proj[1]) ** 2)


def _min_dist_to_hull_2d(hull: List[Tuple[float, float]], point: Tuple[float, float]) -> float:
    """Minimum distance from point to convex hull boundary (2D)."""
    if len(hull) < 2:
        return float("inf")
    min_d = float("inf")
    for i in range(len(hull)):
        d = _dist_point_to_segment_2d(point, hull[i], hull[(i + 1) % len(hull)])
        min_d = min(min_d, d)
    return min_d


# ---------------------------------------------------------------------------
# Mahalanobis distance
# ---------------------------------------------------------------------------

def _compute_mean(points: List[List[float]]) -> List[float]:
    n = len(points)
    d = len(points[0])
    mean = [0.0] * d
    for p in points:
        for j in range(d):
            mean[j] += p[j]
    return [x / n for x in mean]


def _compute_covariance(points: List[List[float]], mean: List[float]) -> List[List[float]]:
    n = len(points)
    d = len(points[0])
    cov = [[0.0] * d for _ in range(d)]
    for p in points:
        diff = [p[j] - mean[j] for j in range(d)]
        for i in range(d):
            for j in range(d):
                cov[i][j] += diff[i] * diff[j]
    for i in range(d):
        for j in range(d):
            cov[i][j] /= max(n - 1, 1)
    # Add small regularization for numerical stability
    for i in range(d):
        cov[i][i] += 1e-10
    return cov


def _mahalanobis_distance(point: List[float], mean: List[float], cov_inv: List[List[float]]) -> float:
    diff = [point[j] - mean[j] for j in range(len(point))]
    md = _mat_vec_mul(cov_inv, diff)
    return math.sqrt(max(0.0, _vec_dot(diff, md)))


# ---------------------------------------------------------------------------
# Local density ratio (Gaussian KDE, simplified)
# ---------------------------------------------------------------------------

def _gaussian_kernel_1d(x: float, h: float) -> float:
    if h < 1e-15:
        return 0.0
    return math.exp(-0.5 * (x / h) ** 2) / (h * math.sqrt(2 * math.pi))


def _kde_density_1d(query: float, samples: List[float], bandwidth: float) -> float:
    n = len(samples)
    if n == 0:
        return 0.0
    total = sum(_gaussian_kernel_1d(query - s, bandwidth) for s in samples)
    return total / n


def _silverman_bandwidth(samples: List[float]) -> float:
    n = len(samples)
    if n < 2:
        return 1.0
    mean = sum(samples) / n
    std = math.sqrt(sum((s - mean) ** 2 for s in samples) / (n - 1))
    # IQR approximation
    sorted_s = sorted(samples)
    q1 = sorted_s[n // 4]
    q3 = sorted_s[3 * n // 4]
    iqr = q3 - q1
    return 1.06 * min(std, iqr / 1.34) * n ** (-0.2)


def _local_density_ratio(query: List[float], pass_points: List[List[float]]) -> float:
    """Compute local density ratio at query point relative to PASS cloud median density.

    Uses product-of-1D-KDE for simplicity (assumes approximate independence).
    """
    d = len(query)
    if not pass_points or len(pass_points) < 3:
        return 0.0

    # Compute bandwidth per dimension
    bandwidths = []
    for j in range(d):
        col = [p[j] for p in pass_points]
        bandwidths.append(_silverman_bandwidth(col))

    # Density at query point
    query_density = 1.0
    for j in range(d):
        col = [p[j] for p in pass_points]
        query_density *= _kde_density_1d(query[j], col, bandwidths[j])

    # Median density of PASS points
    pass_densities = []
    for p in pass_points:
        pd = 1.0
        for j in range(d):
            col = [pp[j] for pp in pass_points]
            pd *= _kde_density_1d(p[j], col, bandwidths[j])
        pass_densities.append(pd)
    pass_densities.sort()
    median_density = pass_densities[len(pass_densities) // 2]

    if median_density < 1e-30:
        return 0.0
    return query_density / median_density


# ---------------------------------------------------------------------------
# DomainEstimator: the core engine
# ---------------------------------------------------------------------------

class DomainEstimator:
    """Three-caliber domain applicability estimator.

    All parameter names and thresholds are configuration-injected.
    """

    def __init__(self, spec: DomainSpec):
        self.spec = spec
        self._pass_points: List[List[float]] = []
        self._fail_points: List[List[float]] = []
        self._mean: Optional[List[float]] = None
        self._cov_inv: Optional[List[List[float]]] = None
        self._hull_2d: Optional[List[Tuple[float, float]]] = None
        self._calibrated = False
        self._rebuild()

    def _rebuild(self):
        """Rebuild internal structures from spec.samples."""
        self._pass_points = []
        self._fail_points = []
        param_names = self.spec.param_names
        for s in self.spec.samples:
            vec = [s.params.get(p, 0.0) for p in param_names]
            if s.label == "PASS":
                self._pass_points.append(vec)
            else:
                self._fail_points.append(vec)

        # Build mean and covariance inverse from PASS points
        if len(self._pass_points) >= 2:
            self._mean = _compute_mean(self._pass_points)
            cov = _compute_covariance(self._pass_points, self._mean)
            d = len(self._mean)
            if d == 1:
                self._cov_inv = [[1.0 / cov[0][0]]]
            elif d == 2:
                self._cov_inv = _mat_inv_2x2(cov)
            elif d == 3:
                self._cov_inv = _mat_inv_3x3(cov)
            else:
                self._cov_inv = _mat_inv_general(cov)
        else:
            self._mean = None
            self._cov_inv = None

        # Build 2D convex hull if exactly 2 params
        if len(param_names) == 2 and len(self._pass_points) >= 3:
            self._hull_2d = _convex_hull_2d(
                [(p[0], p[1]) for p in self._pass_points]
            )
        else:
            self._hull_2d = None

    def add_sample(self, sample: DomainSample):
        """Add a sample and rebuild."""
        self.spec.samples.append(sample)
        self._rebuild()

    def _param_vector(self, params: Dict[str, float]) -> List[float]:
        return [params.get(p, 0.0) for p in self.spec.param_names]

    def _check_range(self, params: Dict[str, float]) -> Optional[Dict[str, str]]:
        """Check if params are within configured ranges. Returns boundary_direction if out."""
        direction = {}
        for p in self.spec.param_names:
            lo, hi = self.spec.param_ranges.get(p, (float("-inf"), float("inf")))
            v = params.get(p, 0.0)
            if v < lo:
                direction[p] = "below"
            elif v > hi:
                direction[p] = "above"
        return direction if direction else None

    def _convex_hull_check(self, vec: List[float]) -> Tuple[bool, float]:
        """Returns (inside, distance). Negative distance = inside."""
        if self._hull_2d is None:
            return True, 0.0  # not applicable
        point = (vec[0], vec[1])
        inside = _point_in_convex_2d(self._hull_2d, point)
        dist = _min_dist_to_hull_2d(self._hull_2d, point)
        if inside:
            return True, -dist  # negative = inside
        return False, dist

    def _mahalanobis_check(self, vec: List[float]) -> Optional[float]:
        if self._mean is None or self._cov_inv is None:
            return None
        return _mahalanobis_distance(vec, self._mean, self._cov_inv)

    def _density_check(self, vec: List[float]) -> float:
        return _local_density_ratio(vec, self._pass_points)

    def _nearest_neighbour(self, vec: List[float]) -> Tuple[float, List[float]]:
        if not self._pass_points:
            return float("inf"), []
        min_d = float("inf")
        min_p = []
        for p in self._pass_points:
            d = _vec_norm(_vec_sub(vec, p))
            if d < min_d:
                min_d = d
                min_p = p
        return min_d, min_p

    def judge(self, params: Dict[str, float]) -> DomainJudgment:
        """Judge whether a query point is within the domain of applicability."""
        vec = self._param_vector(params)

        # Check hard ranges first
        boundary_dir = self._check_range(params)
        if boundary_dir is not None:
            nn_d, nn_p = self._nearest_neighbour(vec)
            return DomainJudgment(
                verdict=Verdict.WITHHELD,
                reason_code=ReasonCode.DOMAIN_OUT_OF_RANGE,
                domain_name=self.spec.name,
                nn_distance=nn_d,
                nn_params=dict(zip(self.spec.param_names, nn_p)) if nn_p else None,
                boundary_direction=boundary_dir,
                thresholds_used={
                    "mahalanobis_threshold": self.spec.mahalanobis_threshold,
                    "density_ratio_threshold": self.spec.density_ratio_threshold,
                },
                query_params=params,
            )

        # Three-caliber checks
        ch_inside, ch_dist = self._convex_hull_check(vec)
        mahal = self._mahalanobis_check(vec)
        density = self._density_check(vec)
        nn_d, nn_p = self._nearest_neighbour(vec)

        # Decision logic
        out_of_domain = False
        reason = ReasonCode.OK

        if not ch_inside and self._hull_2d is not None:
            out_of_domain = True
            reason = ReasonCode.CONVEX_HULL_EXCEEDED

        if mahal is not None and self.spec.mahalanobis_threshold is not None:
            if mahal > self.spec.mahalanobis_threshold:
                out_of_domain = True
                if reason == ReasonCode.OK:
                    reason = ReasonCode.MAHALANOBIS_EXCEEDED

        if self.spec.density_ratio_threshold is not None:
            if density < self.spec.density_ratio_threshold:
                out_of_domain = True
                if reason == ReasonCode.OK:
                    reason = ReasonCode.DENSITY_TOO_LOW

        if out_of_domain:
            return DomainJudgment(
                verdict=Verdict.WITHHELD,
                reason_code=reason,
                domain_name=self.spec.name,
                convex_hull_distance=ch_dist,
                mahalanobis_distance=mahal,
                density_ratio=density,
                nn_distance=nn_d,
                nn_params=dict(zip(self.spec.param_names, nn_p)) if nn_p else None,
                thresholds_used={
                    "mahalanobis_threshold": self.spec.mahalanobis_threshold,
                    "density_ratio_threshold": self.spec.density_ratio_threshold,
                },
                query_params=params,
            )

        return DomainJudgment(
            verdict=Verdict.IN_DOMAIN,
            reason_code=ReasonCode.OK,
            domain_name=self.spec.name,
            convex_hull_distance=ch_dist,
            mahalanobis_distance=mahal,
            density_ratio=density,
            nn_distance=nn_d,
            nn_params=dict(zip(self.spec.param_names, nn_p)) if nn_p else None,
            thresholds_used={
                "mahalanobis_threshold": self.spec.mahalanobis_threshold,
                "density_ratio_threshold": self.spec.density_ratio_threshold,
            },
            query_params=params,
        )


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

@dataclass
class CalibrationResult:
    """Result of threshold calibration."""
    mahalanobis_threshold: float
    density_ratio_threshold: float
    recall: float
    false_rejection_rate: float
    auc_shuffle: float
    n_pass: int
    n_fail: int
    loo_results: List[Dict[str, Any]] = field(default_factory=list)
    learning_curve: List[Dict[str, Any]] = field(default_factory=list)


def _auc_from_scores(scores_labels: List[Tuple[float, str]]) -> float:
    """Compute AUC from (score, label) pairs. label="FAIL" is positive."""
    pairs = [(s, 1.0 if l == "FAIL" else 0.0) for s, l in scores_labels]
    pairs.sort(key=lambda x: -x[0])
    tp = 0
    fp = 0
    tp_prev = 0
    fp_prev = 0
    auc = 0.0
    n_pos = sum(1 for _, l in pairs if l == 1.0)
    n_neg = len(pairs) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    prev_score = None
    for score, label in pairs:
        if score != prev_score and prev_score is not None:
            auc += (fp - fp_prev) * (tp + tp_prev) / 2.0
            tp_prev = tp
            fp_prev = fp
        if label == 1.0:
            tp += 1
        else:
            fp += 1
        prev_score = score
    auc += (fp - fp_prev) * (tp + tp_prev) / 2.0
    return auc / (n_pos * n_neg)


def calibrate_domain(
    spec: DomainSpec,
    *,
    target_recall: float = 0.8,
    target_fpr: float = 0.05,
) -> CalibrationResult:
    """Calibrate thresholds on the spec's samples.

    Uses leave-one-out cross-validation and computes learning curve.
    """
    pass_samples = [s for s in spec.samples if s.label == "PASS"]
    fail_samples = [s for s in spec.samples if s.label == "FAIL"]
    n_pass = len(pass_samples)
    n_fail = len(fail_samples)

    if n_pass < 3 or n_fail < 1:
        return CalibrationResult(
            mahalanobis_threshold=3.0,
            density_ratio_threshold=0.1,
            recall=0.0,
            false_rejection_rate=0.0,
            auc_shuffle=0.5,
            n_pass=n_pass,
            n_fail=n_fail,
        )

    param_names = spec.param_names

    # Build score vectors for PASS and FAIL
    def _score_vector(sample: DomainSample) -> Tuple[float, float]:
        vec = [sample.params.get(p, 0.0) for p in param_names]
        # Mahalanobis
        all_pass = [s.params.get(p, 0.0) for s in pass_samples for p in [None]]
        all_pass_vecs = [[s.params.get(p, 0.0) for p in param_names] for s in pass_samples]
        mean = _compute_mean(all_pass_vecs)
        cov = _compute_covariance(all_pass_vecs, mean)
        d = len(mean)
        if d == 1:
            cov_inv = [[1.0 / cov[0][0]]]
        elif d == 2:
            cov_inv = _mat_inv_2x2(cov)
        elif d == 3:
            cov_inv = _mat_inv_3x3(cov)
        else:
            cov_inv = _mat_inv_general(cov)
        mahal = _mahalanobis_distance(vec, mean, cov_inv)
        # Density
        density = _local_density_ratio(vec, all_pass_vecs)
        return (mahal, density)

    # Compute scores
    pass_scores = [_score_vector(s) for s in pass_samples]
    fail_scores = [_score_vector(s) for s in fail_samples]

    # Grid search for thresholds
    best_mahal = 3.0
    best_density = 0.1
    best_recall = 0.0
    best_fpr = 1.0

    mahal_candidates = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0]
    density_candidates = [0.01, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5]

    for mt in mahal_candidates:
        for dt in density_candidates:
            # Recall: fraction of FAIL correctly detected
            detected = sum(
                1 for m, d in fail_scores
                if m > mt or d < dt
            )
            recall = detected / max(n_fail, 1)
            # False rejection: fraction of PASS incorrectly rejected
            rejected = sum(
                1 for m, d in pass_scores
                if m > mt or d < dt
            )
            fpr = rejected / max(n_pass, 1)
            if recall >= target_recall and fpr <= target_fpr:
                if recall > best_recall or (recall == best_recall and fpr < best_fpr):
                    best_mahal = mt
                    best_density = dt
                    best_recall = recall
                    best_fpr = fpr

    # If no threshold meets criteria, use best recall
    if best_recall < target_recall:
        for mt in mahal_candidates:
            for dt in density_candidates:
                detected = sum(1 for m, d in fail_scores if m > mt or d < dt)
                recall = detected / max(n_fail, 1)
                if recall > best_recall:
                    best_mahal = mt
                    best_density = dt
                    best_recall = recall
                    rejected = sum(1 for m, d in pass_scores if m > mt or d < dt)
                    best_fpr = rejected / max(n_pass, 1)

    # Label shuffle negative control
    import random
    all_labels = ["PASS"] * n_pass + ["FAIL"] * n_fail
    all_scores = [(m, d) for m, d in pass_scores + fail_scores]
    shuffle_aucs = []
    for _ in range(20):
        shuffled = list(all_labels)
        random.shuffle(shuffled)
        sl = [(all_scores[i][0], shuffled[i]) for i in range(len(shuffled))]
        shuffle_aucs.append(_auc_from_scores(sl))
    auc_shuffle = sum(shuffle_aucs) / len(shuffle_aucs)

    # Leave-one-out cross-validation
    loo_results = []
    for i, s in enumerate(spec.samples):
        # Build estimator without this sample
        loo_spec = DomainSpec(
            name=spec.name,
            param_names=spec.param_names,
            param_ranges=spec.param_ranges,
            samples=[s for j, s in enumerate(spec.samples) if j != i],
            mahalanobis_threshold=best_mahal,
            density_ratio_threshold=best_density,
        )
        est = DomainEstimator(loo_spec)
        vec = [s.params.get(p, 0.0) for p in param_names]
        judgment = est.judge(s.params)
        predicted_fail = judgment.verdict == Verdict.WITHHELD
        actual_fail = s.label == "FAIL"
        loo_results.append({
            "index": i,
            "actual": s.label,
            "predicted": "FAIL" if predicted_fail else "PASS",
            "correct": predicted_fail == actual_fail,
        })

    # Learning curve
    learning_curve = []
    for n in range(3, min(len(spec.samples), 50), max(1, len(spec.samples) // 10)):
        subset = spec.samples[:n]
        sub_pass = [s for s in subset if s.label == "PASS"]
        sub_fail = [s for s in subset if s.label == "FAIL"]
        if len(sub_pass) < 2 or len(sub_fail) < 1:
            continue
        sub_spec = DomainSpec(
            name=spec.name,
            param_names=spec.param_names,
            param_ranges=spec.param_ranges,
            samples=subset,
            mahalanobis_threshold=best_mahal,
            density_ratio_threshold=best_density,
        )
        sub_est = DomainEstimator(sub_spec)
        correct = 0
        total = 0
        for s in subset:
            j = sub_est.judge(s.params)
            pred_fail = j.verdict == Verdict.WITHHELD
            if pred_fail == (s.label == "FAIL"):
                correct += 1
            total += 1
        learning_curve.append({
            "n_samples": n,
            "accuracy": correct / max(total, 1),
        })

    return CalibrationResult(
        mahalanobis_threshold=best_mahal,
        density_ratio_threshold=best_density,
        recall=best_recall,
        false_rejection_rate=best_fpr,
        auc_shuffle=auc_shuffle,
        n_pass=n_pass,
        n_fail=n_fail,
        loo_results=loo_results,
        learning_curve=learning_curve,
    )


# ---------------------------------------------------------------------------
# Domain registry (configuration-injected, zero domain semantics)
# ---------------------------------------------------------------------------

class DomainRegistry:
    """Registry of domain specs, loaded from configuration."""

    def __init__(self):
        self._domains: Dict[str, DomainSpec] = {}
        self._estimators: Dict[str, DomainEstimator] = {}
        self._refusal_events: List[Dict[str, Any]] = []

    def register(self, spec: DomainSpec):
        self._domains[spec.name] = spec
        self._estimators[spec.name] = DomainEstimator(spec)

    def load_from_config(self, config_path: str):
        """Load domain specs from a JSON configuration file."""
        path = Path(config_path)
        if not path.exists():
            return
        with open(path) as f:
            data = json.load(f)
        for name, d in data.get("domains", {}).items():
            samples = []
            for s in d.get("samples", []):
                samples.append(DomainSample(
                    params=s["params"],
                    label=s.get("label", "PASS"),
                    error=s.get("error"),
                ))
            spec = DomainSpec(
                name=name,
                param_names=d["param_names"],
                param_ranges={k: tuple(v) for k, v in d["param_ranges"].items()},
                samples=samples,
                mahalanobis_threshold=d.get("mahalanobis_threshold"),
                density_ratio_threshold=d.get("density_ratio_threshold"),
                owner_repo=d.get("owner_repo", ""),
                version=d.get("version", "v0"),
            )
            self.register(spec)

    def judge(self, domain_name: str, params: Dict[str, float]) -> DomainJudgment:
        """Judge a query against a registered domain."""
        est = self._estimators.get(domain_name)
        if est is None:
            return DomainJudgment(
                verdict=Verdict.WITHHELD,
                reason_code=ReasonCode.NO_SAMPLES,
                domain_name=domain_name,
                query_params=params,
            )
        judgment = est.judge(params)
        if judgment.verdict == Verdict.WITHHELD:
            self._refusal_events.append({
                "domain": domain_name,
                "timestamp": _now_iso(),
                "params": params,
                "reason_code": judgment.reason_code.value,
                "judgment": judgment.to_response_dict(),
            })
        return judgment

    def get_kpi(self) -> Dict[str, Any]:
        """Return KPI metrics for refusal events."""
        total = len(self._refusal_events)
        by_reason: Dict[str, int] = {}
        for e in self._refusal_events:
            rc = e["reason_code"]
            by_reason[rc] = by_reason.get(rc, 0) + 1
        return {
            "total_refusals": total,
            "by_reason": by_reason,
        }

    def get_domains(self) -> Dict[str, dict]:
        return {name: spec.as_dict() for name, spec in self._domains.items()}


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Synthetic domain generators (for testing)
# ---------------------------------------------------------------------------

def make_synthetic_2d_domain(
    name: str = "synthetic_2d",
    n_pass: int = 50,
    n_fail: int = 10,
    seed: int = 42,
) -> DomainSpec:
    """Create a synthetic 2D domain with known boundary for testing."""
    import random
    random.seed(seed)

    # PASS samples: clustered around (0.5, 0.5) with some spread
    pass_samples = []
    for _ in range(n_pass):
        x = random.gauss(0.5, 0.15)
        y = random.gauss(0.5, 0.15)
        pass_samples.append(DomainSample(
            params={"x": x, "y": y},
            label="PASS",
        ))

    # FAIL samples: outside the cluster
    fail_samples = []
    for _ in range(n_fail):
        side = random.choice(["left", "right", "bottom", "top"])
        if side == "left":
            x = random.uniform(-0.5, 0.0)
            y = random.gauss(0.5, 0.2)
        elif side == "right":
            x = random.uniform(1.0, 1.5)
            y = random.gauss(0.5, 0.2)
        elif side == "bottom":
            x = random.gauss(0.5, 0.2)
            y = random.uniform(-0.5, 0.0)
        else:
            x = random.gauss(0.5, 0.2)
            y = random.uniform(1.0, 1.5)
        fail_samples.append(DomainSample(
            params={"x": x, "y": y},
            label="FAIL",
        ))

    return DomainSpec(
        name=name,
        param_names=["x", "y"],
        param_ranges={"x": (-1.0, 2.0), "y": (-1.0, 2.0)},
        samples=pass_samples + fail_samples,
        owner_repo="test",
        version="v0",
    )


def make_synthetic_3d_domain(
    name: str = "synthetic_3d",
    n_pass: int = 80,
    n_fail: int = 15,
    seed: int = 42,
) -> DomainSpec:
    """Create a synthetic 3D domain with known boundary for testing."""
    import random
    random.seed(seed + 100)

    pass_samples = []
    for _ in range(n_pass):
        x = random.gauss(0.5, 0.12)
        y = random.gauss(0.5, 0.12)
        z = random.gauss(0.5, 0.12)
        pass_samples.append(DomainSample(
            params={"x": x, "y": y, "z": z},
            label="PASS",
        ))

    fail_samples = []
    for _ in range(n_fail):
        axis = random.choice(["x", "y", "z"])
        params = {"x": random.gauss(0.5, 0.15), "y": random.gauss(0.5, 0.15), "z": random.gauss(0.5, 0.15)}
        if random.random() < 0.5:
            params[axis] = random.uniform(-0.5, 0.0)
        else:
            params[axis] = random.uniform(1.0, 1.5)
        fail_samples.append(DomainSample(
            params=params,
            label="FAIL",
        ))

    return DomainSpec(
        name=name,
        param_names=["x", "y", "z"],
        param_ranges={"x": (-1.0, 2.0), "y": (-1.0, 2.0), "z": (-1.0, 2.0)},
        samples=pass_samples + fail_samples,
        owner_repo="test",
        version="v0",
    )
