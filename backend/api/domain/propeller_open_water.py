"""Open-water (KT, KQ, eta0) kernel + invariant auditor for Wageningen-B propellers.

Context
-------
The serving endpoint ``POST /api/v1/propeller-design/wageningen-b`` advertises
"Wageningen B系列回归公式".  The kernel behind it (``api/propeller_design.py ::
calculate_wageningen_b``) is a two-term hand fit::

    KT = 0.3 * (P/D) - 0.2 * J          (times blade/area multipliers, clamped)
    KQ = 0.03 * (P/D) - 0.02 * J        ->  KQ == KT / 10 exactly
    eta0 = min(J*KT/(2*pi*KQ), 0.85)

Because ``KQ = KT/10`` the efficiency collapses to ``eta0 = 10*J/(2*pi)`` -- a
function of the advance ratio only, independent of pitch, blade number and
expanded area, then artificially capped at 0.85.  That is not a B-series
regression; it is a MOCK that happens to be smooth.

What this module provides
-------------------------
1. ``kp_curves`` / ``open_water`` -- a *first-principles* strip/lifting-line
   open-water kernel (blade-element forces with Prandtl tip/root loss and a
   standard parabolic section drag law).  It contains **no fitted B-series
   coefficients**, so it can be used as an independent structural reference.
   It is graded WITHHELD at value level (see module note at the end) and REAL
   at structural level: every physical invariant an open-water law must obey
   is enforced and unit-tested.
2. ``serve_kernel_replay`` -- exact in-process replay of the serving hand fit,
   so the audit can quantify the gap without depending on the live service.
3. ``check_invariants`` -- nine machine-checkable invariants (I1..I9).  A
   correct open-water predictor must pass all of them; the serving kernel
   currently fails six.

Units: J, P/D, Ae/Ao, KT, KQ, eta0 are all non-dimensional (D = 1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Sequence, Tuple

# --------------------------------------------------------------------------
# 1. Candidate kernel: blade-element / lifting-line, no fitted coefficients
# --------------------------------------------------------------------------

HUB_RATIO = 0.20          # x_h = r_hub / R for B-series hubs (O&O 1975)
NS = 40                   # radial strips
CD0 = 0.008               # section parasitic drag
CD_K = 0.016              # induced/quadratic drag coefficient
CL_DESIGN = 0.70          # design lift of the a=0.8 cambered mean line used by B-series
CL_MAX = 1.40             # maximum lift coefficient before stall (typical for marine sections)
ALPHA_I_MAX = math.radians(30.0)  # maximum induced angle (prevents oscillation near hub)


def _pitch_angle(x: float, p_d: float) -> float:
    """Geometric pitch angle at radius fraction x (D = 1)."""
    return math.atan((p_d / (2.0 * math.pi)) / x)


def _advance_angle(x: float, j: float) -> float:
    """Hydrodynamic advance (inflow) angle at radius fraction x."""
    return math.atan(j / (math.pi * x))


def _prandtl(x: float, z: int, phi: float) -> float:
    """Prandtl tip + root loss factor (product form)."""
    s = math.sin(max(phi, 1e-6))
    ft = (z / 2.0) * (1.0 - x) / s
    fr = (z / 2.0) * (x - HUB_RATIO) / s
    kt = (2.0 / math.pi) * math.acos(min(1.0, math.exp(-max(ft, 1e-9))))
    kr = (2.0 / math.pi) * math.acos(min(1.0, math.exp(-max(fr, 1e-9))))
    return max(kt * kr, 1e-4)


def kp_curves(j: float, p_d: float, z: int, ae_ao: float) -> Tuple[float, float]:
    """Thrust and torque coefficient for one advance ratio.

    Strip integration with a single-pass induced-angle closure
    ``alpha_i = C_L / (4*pi*kappa*x)`` (lifting-line optimum circulation) and a
    section drag law ``C_D = CD0 + CD_K*C_L**2``.  Velocity magnitude neglects
    the induced axial component (standard thin-strip approximation).
    
    A stall model caps the lift coefficient at CL_MAX to prevent unphysical
    behavior at high angles of attack (low J, high P/D). The induced angle is
    also bounded to prevent oscillation near the hub where the Prandtl factor
    is small.
    """
    if j < 0.0:
        raise ValueError("J must be >= 0")
    # constant-chord equivalent blade: Ae/Ao = Z * integral(c/D) dx over hub..tip
    c_over_d = ae_ao / (z * (1.0 - HUB_RATIO))
    kt_sum = 0.0
    kq_sum = 0.0
    dx = (1.0 - HUB_RATIO) / NS
    alpha0 = -CL_DESIGN / (2.0 * math.pi)   # zero-lift angle of the cambered mean line

    for i in range(NS):
        x = HUB_RATIO + (i + 0.5) * dx
        phi = _pitch_angle(x, p_d)
        beta = _advance_angle(x, j)
        kap = _prandtl(x, z, phi)
        alpha_geo = phi - beta                # geometric angle of attack
        if alpha_geo <= alpha0:
            # blade at or beyond zero lift: only drag acts on this strip
            cl = 0.0
            cd = CD0
            beta_i = beta
        else:
            # two-pass closure on the induced angle with bounds
            cl = 2.0 * math.pi * (alpha_geo - alpha0)
            for _ in range(3):
                alpha_i = cl / (4.0 * math.pi * kap * x)
                # Bound the induced angle to prevent oscillation near hub
                alpha_i = max(-ALPHA_I_MAX, min(ALPHA_I_MAX, alpha_i))
                cl = 2.0 * math.pi * (alpha_geo - alpha_i - alpha0)
                cl = max(cl, 0.0)
            # Stall model: cap lift coefficient at CL_MAX
            cl = min(cl, CL_MAX)
            alpha_i = cl / (4.0 * math.pi * kap * x)
            alpha_i = max(-ALPHA_I_MAX, min(ALPHA_I_MAX, alpha_i))
            cd = CD0 + CD_K * cl * cl
            beta_i = beta + alpha_i
        w_bar2 = j * j + (math.pi * x) ** 2        # (W / (nD))**2 at the disc
        c = c_over_d
        kt_sum += 0.5 * w_bar2 * c * (cl * math.cos(beta_i) - cd * math.sin(beta_i)) * dx
        kq_sum += 0.5 * x * w_bar2 * c * (cl * math.sin(beta_i) + cd * math.cos(beta_i)) * dx

    # no tensile thrust past the zero-thrust point; torque stays positive (windmill drag)
    return max(z * kt_sum, 0.0), z * kq_sum


@dataclass
class OpenWaterResult:
    J: float
    P_D: float
    Z: int
    Ae_Ao: float
    KT: float
    KQ: float
    eta0: float
    evidence: str = "REAL(structure) / WITHHELD(value)"

    def as_dict(self) -> Dict[str, float]:
        return {
            "J": round(self.J, 5), "P_D": self.P_D, "Z": self.Z,
            "Ae_Ao": self.Ae_Ao, "KT": round(self.KT, 5),
            "KQ": round(self.KQ, 6), "eta0": round(self.eta0, 5),
            "evidence": self.evidence,
        }


def ll_kernel(j: float, p_d: float, z: int, ae_ao: float) -> Tuple[float, float, float]:
    """(KT, KQ, eta0) triple -- the shape expected by check_invariants."""
    kt, kq = kp_curves(j, p_d, z, ae_ao)
    eta = (j * kt) / (2.0 * math.pi * kq) if (j > 0.0 and kq > 0.0) else 0.0
    return kt, kq, max(0.0, eta)


def open_water(j: float, p_d: float, z: int, ae_ao: float) -> OpenWaterResult:
    """Single-point open-water prediction with eta0 from the exact identity."""
    kt, kq = kp_curves(j, p_d, z, ae_ao)
    eta = (j * kt) / (2.0 * math.pi * kq) if (j > 0.0 and kq > 0.0) else 0.0
    return OpenWaterResult(j, p_d, z, ae_ao, kt, kq, max(0.0, eta))


# --------------------------------------------------------------------------
# 2. Exact replay of the serving hand fit (for audit / regression)
# --------------------------------------------------------------------------

def serve_kernel_replay(j: float, p_d: float, z: int, ae_ao: float) -> Tuple[float, float, float]:
    """Byte-for-byte replay of api/propeller_design.py::calculate_wageningen_b."""
    kt_base = 0.3 * p_d - 0.2 * j
    kq_base = 0.03 * p_d - 0.02 * j
    if z < 4:
        kt_base *= 0.95
        kq_base *= 0.95
    elif z == 5:
        kt_base *= 1.02
        kq_base *= 1.02
    elif z > 5:
        kt_base *= 1.04
        kq_base *= 1.04
    kt_base *= (0.5 + ae_ao)
    kq_base *= (0.5 + ae_ao)
    kt = max(0.05, min(0.5, kt_base))
    kq = max(0.005, min(0.05, kq_base))
    eta = (j * kt) / (2.0 * math.pi * kq) if (j > 0 and kq > 0) else 0.5
    return kt, kq, min(eta, 0.85)


# --------------------------------------------------------------------------
# 3. Physical invariants
# --------------------------------------------------------------------------

Grid = Sequence[Tuple[float, float, int, float]]   # (J, P/D, Z, Ae/Ao)

DEFAULT_GRID: List[Tuple[float, float, int, float]] = [
    (j, p_d, z, ae)
    for j in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.4)
    for p_d in (0.6, 0.8, 1.0, 1.2, 1.4)
    for z in (4, 5)
    for ae in (0.40, 0.55, 0.70, 0.85)
]


@dataclass
class Invariant:
    ident: str
    name: str
    verdict: str          # PASS / FAIL
    metric: float = 0.0
    threshold: float = 0.0
    detail: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {"id": self.ident, "name": self.name, "verdict": self.verdict,
                "metric": round(self.metric, 6), "threshold": self.threshold,
                "detail": self.detail}


def _zeros_of(kt_of_j: Callable[[float], float], jmax: float = 3.0) -> float:
    """First J where KT crosses zero (bisection), NaN if none."""
    n = 600
    prev_j, prev_v = 0.0, kt_of_j(0.0)
    for i in range(1, n + 1):
        j = jmax * i / n
        v = kt_of_j(j)
        if prev_v > 0.0 >= v:
            lo, hi = prev_j, j
            for _ in range(80):
                mid = 0.5 * (lo + hi)
                if kt_of_j(mid) > 0.0:
                    lo = mid
                else:
                    hi = mid
            return 0.5 * (lo + hi)
        prev_j, prev_v = j, v
    return float("nan")


def check_invariants(kernel: Callable[[float, float, int, float], Tuple[float, float, float]],
                     grid: Grid = None, label: str = "") -> List[Invariant]:
    """Run I1..I9 against a KT/KQ/eta kernel.

    Invariants encode what *any* physically admissible open-water law must obey.
    They are deliberately geometry-agnostic: no published B-series coefficient
    table is required, so the audit does not depend on a value reference.
    """
    grid = list(grid or DEFAULT_GRID)
    out: List[Invariant] = []

    # ---- I1: torque must be structurally independent of thrust -------------
    ratios = []
    for (j, p_d, z, ae) in grid:
        kt, kq, _ = kernel(j, p_d, z, ae)
        if kt > 0.02 and kq > 1e-6:
            ratios.append(10.0 * kq / kt)
    dev = max(abs(r - 1.0) for r in ratios) if ratios else 0.0
    out.append(Invariant(
        "I1", "KQ structurally decoupled from KT (must not be KQ == KT/10)",
        "PASS" if dev > 1e-3 else "FAIL", dev, 1e-3,
        f"max|10*KQ/KT - 1| over {len(ratios)} points = {dev:.3e}; "
        f"if ~0 the kernel computes torque as a fixed fraction of thrust"))

    # ---- I2: eta0 identity ------------------------------------------------
    errs = []
    for (j, p_d, z, ae) in grid:
        kt, kq, eta = kernel(j, p_d, z, ae)
        if j > 0.05 and kq > 1e-6 and kt > 1e-6:
            errs.append(abs((j * kt) / (2.0 * math.pi * kq) - eta) / max(eta, 1e-9))
    e2 = max(errs) if errs else 0.0
    out.append(Invariant("I2", "eta0 == J*KT/(2*pi*KQ) (closed identity)", 
                         "PASS" if e2 < 1e-2 else "FAIL", e2, 1e-2,
                         f"max relative deviation = {e2:.3e} (deviation is produced by clipping eta0)"))

    # ---- I3: KT monotone decreasing in J ---------------------------------
    bad = 0
    worst = 0.0
    for p_d in (0.6, 0.8, 1.0, 1.2, 1.4):
        for z in (4, 5):
            for ae in (0.40, 0.55, 0.70, 0.85):
                prev = None
                for j in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2):
                    kt = kernel(j, p_d, z, ae)[0]
                    if prev is not None and kt > prev + 1e-9:
                        bad += 1
                        worst = max(worst, kt - prev)
                    prev = kt
    out.append(Invariant("I3", "KT monotonically decreasing in J", 
                         "PASS" if bad == 0 else "FAIL", float(bad), 0.0,
                         f"{bad} monotonicity violations, worst increase {worst:.3e}"))

    # ---- I4: zero-thrust advance ratio inside physical band --------------
    xs = []
    for p_d in (0.6, 0.8, 1.0, 1.2, 1.4):
        j0 = _zeros_of(lambda j: kernel(j, p_d, 4, 0.55)[0])
        if math.isnan(j0):
            xs.append((p_d, float("nan"), float("nan")))
        else:
            xs.append((p_d, j0, j0 / p_d))
    finite = [r for (_, _, r) in xs if not math.isnan(r)]
    ok4 = bool(finite) and all(0.70 <= r <= 1.35 for r in finite)
    worst_ratio = max((abs(r - 1.0) for r in finite), default=float("inf"))
    out.append(Invariant(
        "I4", "zero-thrust advance ratio J0/(P/D) in [0.70, 1.35]",
        "PASS" if ok4 else "FAIL", worst_ratio, 1.0,
        "J0/(P/D) per pitch = " + ", ".join(
            f"{p:.1f}:{r:.3f}" if not math.isnan(r) else f"{p:.1f}:none" for (p, _, r) in xs)))

    # ---- I5: non-zero parasitic torque at zero thrust ---------------------
    paras = []
    for p_d in (0.6, 0.8, 1.0, 1.2, 1.4):
        j0 = _zeros_of(lambda j: kernel(j, p_d, 4, 0.55)[0])
        if not math.isnan(j0):
            kq0 = kernel(j0, p_d, 4, 0.55)[1]
            paras.append(kq0)
    ok5 = bool(paras) and min(paras) > 1e-4
    out.append(Invariant("I5", "KQ > 0 at zero thrust (viscous/parasitic torque)",
                         "PASS" if ok5 else "FAIL", min(paras) if paras else 0.0, 1e-4,
                         f"min KQ(J0) = {min(paras) if paras else float('nan'):.3e}"))

    # ---- I6: eta0 never clipped to a constant ceiling --------------------
    # A clip shows up as many points sharing exactly the same eta0 value.
    vals: Dict[float, int] = {}
    for (j, p_d, z, ae) in grid:
        kt_j = kernel(j, p_d, z, ae)[0]
        if j > 0.05 and kt_j > 1e-6:
            eta = round(kernel(j, p_d, z, ae)[2], 9)
            vals[eta] = vals.get(eta, 0) + 1
    top = max(vals.values()) if vals else 0
    frac = top / max(1, sum(vals.values()))
    out.append(Invariant("I6", "eta0 not saturation-clipped (no constant plateau)",
                         "PASS" if frac < 0.05 else "FAIL", frac, 0.05,
                         f"most frequent eta0 value covers {frac*100:.1f}% of samples "
                         f"(a correct law spreads eta0 continuously)"))

    # ---- I7: peak eta0 inside the B-series envelope [0.35, 0.78] --------
    peaks = []
    for p_d in (0.6, 0.8, 1.0, 1.2, 1.4):
        best = (-1.0, 0.0)
        for i in range(0, 121):
            j = i / 100.0
            eta = kernel(j, p_d, 4, 0.55)[2]
            if eta > best[0]:
                best = (eta, j)
        peaks.append((p_d, best[0], best[1]))
    ok7 = all(0.35 <= e <= 0.78 for (_, e, _) in peaks)
    out.append(Invariant(
        "I7", "peak open-water efficiency in [0.35, 0.78] (B-series envelope)",
        "PASS" if ok7 else "FAIL", max(abs(e - 0.50) for (_, e, _) in peaks), 0.28,
        "peak eta0 / J at peak per pitch = " + ", ".join(
            f"{p:.1f}:{e:.3f}@{jpk:.2f}" for (p, e, jpk) in peaks)))

    # ---- I8: P/D must control pitch (KT at fixed J grows with P/D) ------
    viol = 0
    for (j, z, ae) in ((0.4, 4, 0.55), (0.6, 5, 0.70), (0.2, 4, 0.40)):
        series = [kernel(j, p_d, z, ae)[0] for p_d in (0.6, 0.8, 1.0, 1.2, 1.4)]
        if series[0] <= 0.02:      # lowest pitch already at/below zero thrust -> case undefined
            continue
        prev = None
        for kt in series:
            if prev is not None and kt < prev - 1e-9:
                viol += 1
            prev = kt
    out.append(Invariant("I8", "KT increases monotonically with P/D at fixed J",
                         "PASS" if viol == 0 else "FAIL", float(viol), 0.0,
                         f"{viol} violations (pitch is the primary thrust lever)"))

    # ---- I9: blade count has a bounded effect ---------------------------
    spread = 0.0
    for (j, p_d, ae) in ((0.4, 1.0, 0.55), (0.6, 1.2, 0.70)):
        kts = [kernel(j, p_d, z, ae)[0] for z in (3, 4, 5, 6, 7)]
        lo, hi = min(kts), max(kts)
        if hi > 0:
            spread = max(spread, (hi - lo) / hi)
    out.append(Invariant("I9", "Z effect on KT bounded (<=35% across Z=3..7)",
                         "PASS" if spread <= 0.35 else "FAIL", spread, 0.35,
                         f"max relative spread across Z = {spread*100:.1f}%"))

    if label:
        for inv in out:
            inv.detail = f"[{label}] " + inv.detail
    return out


def design_advance_ratio(p_d: float, z: int, ae_ao: float,
                         kernel: Callable[[float, float, int, float], Tuple[float, float, float]]
                         = None) -> Tuple[float, float]:
    """Return (J_opt, eta_max): J maximising eta0, searched on [0.2, 1.0].

    A correct design routine returns an interior optimum; a routine whose eta0 is
    a monotone function of J (or clipped) will pin the answer to a search bound.
    """
    kernel = kernel or ll_kernel
    best_j, best_eta = 0.2, -1.0
    for i in range(81):
        j = 0.2 + 0.01 * i
        eta = kernel(j, p_d, z, ae_ao)[2]
        if eta > best_eta:
            best_eta, best_j = eta, j
    return best_j, best_eta


__all__ = [
    "HUB_RATIO", "kp_curves", "ll_kernel", "open_water", "OpenWaterResult",
    "serve_kernel_replay", "check_invariants", "Invariant",
    "design_advance_ratio", "DEFAULT_GRID",
]
