"""Synthetic kernels for the cross-architecture gate — zero domain semantics.

The gate must be judgeable *before* it is pointed at any real workload, so the
fixture family is built from four arithmetic textures rather than from physics:

===================  ==============================================================
family               floating-point behaviour it stresses
===================  ==============================================================
``int_lattice``      integer-only reduction (LCG walk + mixed-modulus folds).
                     No rounding exists, so a cross-architecture difference here
                     can only be a compiler/emitter defect, never an IEEE
                     tolerance issue.
``int_matrix``       small modular integer matrix chain; same argument, plus a
                     data-independent access pattern.
``fp_chain``         a declared straight-line program of ``+ - * / sqrt fma`` over
                     fixed inputs.  Every op is one IEEE binary64 rounding, so the
                     portable oracle in :mod:`.numerics` can predict it bit for
                     bit — this family measures *arithmetic* cross-ISA agreement.
``fp_reduction``     order-sensitive summation / dot products.  Divergence here is
                     real and legitimate (different association order), so it
                     exercises the tolerance band rather than bit identity.
``fp_transcendental`` ``exp/log/log2/pow/sin/cos`` with two alternative algebraic
                     routes, i.e. a controlled libm-path divergence.
``fp_cancel``        cancelling diagnostics: two observable quantities and two
                     declared diagnostics that are O(1) terms cancelling to
                     ~1e-17.  The diagnostics are judged on an absolute tolerance
                     tied to the O(1) scale, which is why they are a separate
                     kind — a relative metric on them is meaningless.
===================  ==============================================================

Nothing in this module knows what the numbers *mean*.  Names are positional
(``chain_step_07``, ``lattice_fold_b``), and every constant is either a declared
gate parameter or derived from the integer LCG, so the same fixture runs bit for
bit identically on any node.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from . import numerics

INT = "int"
FP = "fp64"
DIAG = "fp64_diag"

INPUT_COUNT = 10
CHAIN_STEPS = 20
REDUCTION_TERMS = 4096


# --------------------------------------------------------------------------- #
# input generation (integer LCG -> exact dyadic doubles)
# --------------------------------------------------------------------------- #
def make_inputs(count: int = INPUT_COUNT) -> List[float]:
    lcg = numerics.Lcg(20261110)
    return [0.5 + 0.5 * lcg.next_unit() for _ in range(count)]


def chain_program(steps: int = CHAIN_STEPS) -> List[Tuple[str, int, int, int]]:
    """Declared immutable program: ``(op, a, b, c)`` with register indices."""
    ops = ("add", "mul", "div", "sqrt", "fma")
    lcg = numerics.Lcg(777)
    program: List[Tuple[str, int, int, int]] = []
    for step in range(steps):
        op = ops[lcg.next_int() % len(ops)]
        live = INPUT_COUNT + step
        a = lcg.next_int() % live
        b = lcg.next_int() % live
        c = lcg.next_int() % live
        program.append((op, a, b, c))
    return program


# --------------------------------------------------------------------------- #
# integer families
# --------------------------------------------------------------------------- #
def int_lattice() -> Dict[str, Tuple[str, Any]]:
    lcg = numerics.Lcg(424242)
    mod1, mod2 = (1 << 61) - 1, 1000000007
    acc_a = acc_b = 0
    carries = 0
    xor_fold = 0
    peak = 0
    for _ in range(4000):
        word = lcg.next_int()
        acc_a = (acc_a * 131 + word) % mod1
        acc_b = (acc_b + word * word) % mod2
        carries += bin(word & (word >> 1)).count("1")
        xor_fold ^= (word >> 13) & 0xFFFF
        peak = max(peak, (word >> 40) & 0xFFFFFF)
    return {
        "lattice_fold_a": (INT, acc_a),
        "lattice_fold_b": (INT, acc_b),
        "lattice_carries": (INT, carries),
        "lattice_xor_fold": (INT, xor_fold),
        "lattice_peak": (INT, peak),
        "lattice_span": (INT, acc_a % 65537),
        "lattice_popcount": (INT, bin(acc_a).count("1")),
        "lattice_digest_mod": (INT, (acc_a ^ acc_b) % mod2),
    }


def int_matrix() -> Dict[str, Tuple[str, Any]]:
    lcg = numerics.Lcg(99991)
    n = 6
    grid = [[lcg.next_int() % 1000 for _ in range(n)] for _ in range(n)]
    vec = [lcg.next_int() % 1000 for _ in range(n)]
    for _ in range(3):                     # three modular matrix-vector sweeps
        vec = [sum(grid[i][j] * vec[j] for j in range(n)) % 100003 for i in range(n)]
    trace = sum(grid[i][i] for i in range(n)) % 100003
    return {
        "matrix_vec_l1": (INT, sum(vec)),
        "matrix_vec_checksum": (INT, vec[0] * 1000003 + vec[-1]),
        "matrix_trace_mod": (INT, trace),
        "matrix_parity": (INT, sum(grid[i][j] & 1 for i in range(n) for j in range(n))),
    }


# --------------------------------------------------------------------------- #
# floating-point families
# --------------------------------------------------------------------------- #
def _eval_op(op: str, a: float, b: float, c: float, arith: str) -> float:
    if arith == "exact":
        if op == "add":
            return numerics.exact_add(a, b)
        if op == "mul":
            return numerics.exact_mul(a, b)
        if op == "div":
            return numerics.exact_div(a, b)
        if op == "sqrt":
            return numerics.exact_sqrt(abs(a))
        return numerics.exact_fma(a, b, c)
    if arith == "fma":                     # emulate a contracting build
        if op == "add":
            return numerics.exact_fma(a, 1.0, b)
        if op == "mul":
            return numerics.exact_mul(a, b)
        if op == "div":
            return a / b
        if op == "sqrt":
            return math.sqrt(abs(a))
        return numerics.exact_fma(a, b, c)
    # native: plain IEEE double ops, one rounding per operation
    if op == "add":
        return a + b
    if op == "mul":
        return a * b
    if op == "div":
        return a / b
    if op == "sqrt":
        return math.sqrt(abs(a))
    return a * b + c


def fp_chain(arith: str = "native") -> Dict[str, Tuple[str, Any]]:
    regs: List[float] = list(make_inputs())
    out: Dict[str, Tuple[str, Any]] = {}
    for step, (op, ia, ib, ic) in enumerate(chain_program()):
        value = _eval_op(op, regs[ia], regs[ib], regs[ic], arith)
        regs.append(value)
        out[f"chain_step_{step:02d}"] = (FP, value)
    return out


def _sum_naive(values: List[float]) -> float:
    total = 0.0
    for value in values:
        total += value
    return total


def _sum_reverse(values: List[float]) -> float:
    total = 0.0
    for value in reversed(values):
        total += value
    return total


def _sum_kahan(values: List[float]) -> float:
    total = 0.0
    comp = 0.0
    for value in values:
        y = value - comp
        t = total + y
        comp = (t - total) - y
        total = t
    return total


def fp_reduction(sum_variant: str = "naive") -> Dict[str, Tuple[str, Any]]:
    lcg = numerics.Lcg(31415926)
    values = [lcg.next_unit() * math.pow(10.0, (lcg.next_int() % 13) - 6)
              for _ in range(REDUCTION_TERMS)]
    weights = [0.5 + lcg.next_unit() for _ in range(REDUCTION_TERMS)]
    pick = {"naive": _sum_naive, "reverse": _sum_reverse,
            "kahan": _sum_kahan, "fsum": math.fsum}[sum_variant]
    dot = _sum_naive([v * w for v, w in zip(values, weights)])
    mean = _sum_naive(values) / REDUCTION_TERMS
    sq = _sum_naive([(v - mean) ** 2 for v in values])
    return {
        "reduction_sum": (FP, pick(values)),
        "reduction_dot": (FP, dot),
        "reduction_mean": (FP, mean),
        "reduction_variance": (FP, sq / (REDUCTION_TERMS - 1)),
        "reduction_weight_sum": (FP, pick(weights)),
        "reduction_max": (FP, max(values)),
    }


def fp_transcendental(libm: str = "stdlib") -> Dict[str, Tuple[str, Any]]:
    lcg = numerics.Lcg(2718281)
    xs = [0.25 + lcg.next_unit() * 4.0 for _ in range(5)]
    out: Dict[str, Tuple[str, Any]] = {}
    if libm == "stdlib":
        out["trans_exp"] = (FP, math.exp(xs[0]))
        out["trans_log"] = (FP, math.log(xs[1]))
        out["trans_log2"] = (FP, math.log2(xs[2]))
        out["trans_pow"] = (FP, math.pow(xs[3], 2.75))
        out["trans_sin"] = (FP, math.sin(xs[4]))
        out["trans_cos"] = (FP, math.cos(xs[4] * 1.5))
    else:                                   # alternative algebraic route
        ln2 = math.log(2.0)
        log2e = 1.0 / ln2
        out["trans_exp"] = (FP, math.pow(2.0, xs[0] * log2e))
        out["trans_log"] = (FP, math.log2(xs[1]) * ln2)
        out["trans_log2"] = (FP, math.log(xs[2]) / ln2)
        out["trans_pow"] = (FP, math.exp(2.75 * math.log(xs[3])))
        out["trans_sin"] = (FP, math.sin(xs[4]))
        out["trans_cos"] = (FP, math.cos(xs[4] * 1.5))
    return out


def fp_cancel(arith: str = "native") -> Dict[str, Tuple[str, Any]]:
    a, b, c, d = make_inputs()[:4]
    big = 1.0e6 + a                 # O(1e6) terms that cancel to ~1e-17
    tiny = 1.0e-6 * b
    if arith == "exact":
        obs_1 = numerics.exact_sub(numerics.exact_add(a, b), numerics.exact_add(b, a))
    else:
        obs_1 = (a + b) - (b + a)
    obs_2 = a - a
    # declared diagnostics: absolute scale is the O(1e6) term, so relative error
    # is meaningless here by construction (see policy ``diag_abs_tol``).
    diag_1 = ((big + tiny) + c) - (big + c) - tiny
    diag_2 = ((big * d) - (big * d)) + (c - c)
    return {
        "cancel_obs_1": (FP, obs_1),
        "cancel_obs_2": (FP, obs_2),
        "cancel_diag_1": (DIAG, diag_1),
        "cancel_diag_2": (DIAG, diag_2),
    }


# --------------------------------------------------------------------------- #
# assembly
# --------------------------------------------------------------------------- #
def run_all(arith: str = "native", sum_variant: str = "naive",
            libm: str = "stdlib") -> Dict[str, Tuple[str, Any]]:
    """Evaluate every fixture family and return ``name -> (kind, value)``."""
    fields: Dict[str, Tuple[str, Any]] = {}
    fields.update(int_lattice())
    fields.update(int_matrix())
    fields.update(fp_chain(arith))
    fields.update(fp_reduction(sum_variant))
    fields.update(fp_transcendental(libm))
    fields.update(fp_cancel(arith))
    return fields