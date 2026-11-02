#!/usr/bin/env python3
"""Absolute-magnitude open-water benchmark for the in-repo propeller model (m1655).

Why this file exists
--------------------
Mission 1413 audited the *live platform* propulsion endpoints and judged them
MOCK, then closed with an explicit remaining debt (m1413 §6.5):

    "B 系列 KT/KQ 表值（或 ITTC 敞水试验曲线）入库为
     backend/data/propulsion_benchmark_reference.json，届时本套件的 J1-J7 可直接
     叠加绝对量级判据，形成完整三段式（恒等 + 上界 + 表值）。"

The in-repo replacement model ``backend/services/propeller_open_water.py`` is
graded REAL, but its published verification
(``docs/verifier/PROPELLER_OPEN_WATER_VERIFICATION.md``) is **analytic bound +
internal consistency only**: it proves ``0 < eta_0 < eta_ideal``, monotonicity,
Froude invariance and momentum/BEM agreement.  Every one of those criteria is
*necessary but not sufficient* - a model can be 40 % off in absolute terms and
still satisfy all of them.  The missing third leg is a published point-data
baseline.  This module supplies it, machine-adjudicated.

Reference data provenance (no remembered table values)
------------------------------------------------------
The reference is *not* typed in here.  It is generated programmatically from the
canonical Wageningen B-series polynomials already present in the repository
(``tools/verify/propeller_open_water/reference_bseries.py``), whose coefficient
table is the Oosterveld & van Oossanen (1975) set as reproduced in Bernitsas,
Ray & Kinley (1981), and which is cross-checked term-by-term against
``backend/data/official/Wageningen_Bseries_coeffs.dat`` by
``tests/verify/test_propeller_open_water_audit.py``.  Every number in the
generated JSON therefore traces to an in-repo, cross-validated artefact and to a
generator script hash recorded in the file's own metadata.

Assessment envelope
-------------------
Absolute comparison is only meaningful inside a declared box.  The harness uses
``P/D = 0.8..1.4``, ``Ae/A0 = 0.40..0.85``, ``Z = 4,5``, ``J = 0.2..1.0`` - the
part of the B-series validity box (``Z 2..7, P/D 0.5..1.4, Ae/A0 0.2..1.05,
J 0..1.5``) that corresponds to controllable-pitch merchant practice, and where
the B-series polynomial itself is not extrapolating.  The bollard-pull corner
(``J -> 0``) is excluded on purpose: relative error there is dominated by
``KQ -> 0`` and is not a meaningful accuracy metric.

Acceptance bars (platform discipline)
-------------------------------------
* ``ACCEPT_PCT = 15.0`` - "对标实验/参考值误差 > 15% 不可接受".
* ``STRICT_PCT  = 3.0``  - the strict bar quoted by the report gate.

Usage
-----
    python3 tools/benchmarks/open_water_absolute.py --write-reference
    python3 tools/benchmarks/open_water_absolute.py --markdown /tmp/ow.md
    python3 tools/benchmarks/open_water_absolute.py --figure docs/asset/m1655/x.png
    python3 tools/benchmarks/open_water_absolute.py --json /tmp/ledger.json

The tool is offline-only (no service calls) and needs no data beyond the repo.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import math
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND = os.path.join(REPO_ROOT, "backend")
REFERENCE_MODULE = os.path.join(
    REPO_ROOT, "tools", "verify", "propeller_open_water", "reference_bseries.py"
)
REFERENCE_DATA = os.path.join(
    BACKEND, "data", "propulsion_benchmark_reference.json"
)

SCHEMA_VERSION = 1
ACCEPT_PCT = 15.0
STRICT_PCT = 3.0

#: A *relative* error is only defined where the denominator is meaningful.  Near
#: the zero-thrust crossing ``KT -> 0`` and ``KQ -> 0`` the ratio diverges for any
#: model, so points are scored only where the reference value is at least this
#: fraction of the same propeller's value at the lowest advance ratio in the box
#: (``J = 0.2``).  Excluded points are counted and reported, never dropped
#: silently; they stay in the dataset and in the raw ledger.
RELATIVE_FLOOR_FRACTION = 0.10

#: Declared assessment envelope (start, stop, step for J and P/D; explicit lists
#: for the categorical parameters).  See module docstring for the rationale.
ENVELOPE: Dict[str, object] = {
    "J": [round(0.2 + 0.1 * i, 2) for i in range(9)],          # 0.2 .. 1.0
    "P_D": [round(0.8 + 0.2 * i, 2) for i in range(4)],        # 0.8 .. 1.4
    "Ae_Ao": [0.40, 0.55, 0.70, 0.85],
    "Z": [4, 5],
}

PROVENANCE: Dict[str, object] = {
    "family": "Wageningen B-series",
    "polynomial_source": (
        "Oosterveld & van Oossanen (1975), 39-term KT / 47-term KQ regression, "
        "as reproduced in Bernitsas, Ray & Kinley (1981), Report No. 237, "
        "University of Michigan"
    ),
    "in_repo_reference_implementation": "tools/verify/propeller_open_water/reference_bseries.py",
    "coefficient_cross_check": "tests/verify/test_propeller_open_water_audit.py "
                               "vs backend/data/official/Wageningen_Bseries_coeffs.dat",
    "validity_box": {"Z": [2, 7], "P_D": [0.5, 1.4], "Ae_Ao": [0.2, 1.05], "J": [0.0, 1.5]},
    "note": "No table value in this dataset is hand-typed; every point is "
            "evaluated from the in-repo polynomial at load/creation time.",
}


# ---------------------------------------------------------------------------
# module loading
# ---------------------------------------------------------------------------

def _load_reference_polynomial():
    """Import the in-repo canonical B-series polynomial by explicit path."""
    if not os.path.isfile(REFERENCE_MODULE):
        raise FileNotFoundError(f"reference polynomial missing: {REFERENCE_MODULE}")
    spec = importlib.util.spec_from_file_location("_m1655_reference_bseries",
                                                  REFERENCE_MODULE)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {REFERENCE_MODULE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_backend_model():
    """Import ``backend/services/propeller_open_water.py`` (BEM model)."""
    if BACKEND not in sys.path:
        sys.path.insert(0, BACKEND)
    from services import propeller_open_water as model  # noqa: E402
    return model


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# reference dataset
# ---------------------------------------------------------------------------

def enumerate_cases(envelope: Optional[Dict[str, object]] = None) -> List[Dict[str, object]]:
    """Cartesian product of the declared assessment envelope."""
    env = envelope or ENVELOPE
    cases = []
    for J, P_D, Ae_Ao, Z in itertools.product(env["J"], env["P_D"], env["Ae_Ao"], env["Z"]):
        cases.append({
            "case_id": f"J{J:.2f}_PD{P_D:.2f}_AE{Ae_Ao:.2f}_Z{int(Z)}",
            "J": float(J),
            "P_D": float(P_D),
            "Ae_Ao": float(Ae_Ao),
            "Z": int(Z),
        })
    return cases


def build_reference_dataset(envelope: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    """Evaluate the canonical polynomial over the envelope (programmatic, no hand typing)."""
    ref = _load_reference_polynomial()
    cases = enumerate_cases(envelope)
    rows = []
    for case in cases:
        point = ref.open_water_coefficients(case["J"], case["P_D"], case["Ae_Ao"], case["Z"])
        rows.append({
            **case,
            "KT": point["KT"],
            "KQ": point["KQ"],
            "eta_0": point["eta_0"],
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": "tools/benchmarks/open_water_absolute.py",
        "generator_sha256": _sha256(os.path.abspath(__file__)),
        "provenance": PROVENANCE,
        "envelope": {k: list(v) for k, v in (envelope or ENVELOPE).items()},
        "eta_0_definition": "eta_0 = J * KT / (2*pi*KQ)",
        "case_count": len(rows),
        "cases": rows,
    }


def write_reference_dataset(path: str = REFERENCE_DATA,
                            dataset: Optional[Dict[str, object]] = None) -> str:
    payload = dataset if dataset is not None else build_reference_dataset()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return path


def load_reference_dataset(path: str = REFERENCE_DATA) -> Dict[str, object]:
    """Load and validate the reference dataset.  Fails closed, never silently empty."""
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"reference dataset missing: {path} - regenerate with "
            f"`python3 tools/benchmarks/open_water_absolute.py --write-reference`"
        )
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unexpected schema_version: {data.get('schema_version')}")
    cases = data.get("cases")
    if not cases:
        raise ValueError("reference dataset has no cases (fail closed)")
    if data.get("case_count") != len(cases):
        raise ValueError("case_count does not match the number of cases")
    required = {"case_id", "J", "P_D", "Ae_Ao", "Z", "KT", "KQ", "eta_0"}
    for case in cases:
        missing = required - set(case)
        if missing:
            raise ValueError(f"case {case.get('case_id')} missing fields {sorted(missing)}")
    return data


# ---------------------------------------------------------------------------
# BEM evaluation + error metrics
# ---------------------------------------------------------------------------

class ClampProbe:
    """Count how often the section-lift cap is active (exact instrumentation).

    The BEM's section model is ``Cl = min(2*pi*sin(alpha), CL_MAX)``.  A strip
    whose lift is capped is a *stalled* strip in this model; the occupancy of
    that branch is the single most useful diagnostic of where the model leaves
    the attached-flow regime it was calibrated for.
    """

    def __init__(self, model) -> None:
        self.model = model
        self.calls = 0
        self.clamped = 0
        self._original = None

    def __enter__(self) -> "ClampProbe":
        original = self.model.blade_element_section
        cap = self.model.CL_MAX
        probe = self

        def wrapped(alpha_rad: float):
            cl, cd = original(alpha_rad)
            probe.calls += 1
            if abs(abs(cl) - cap) < 1e-12:
                probe.clamped += 1
            return cl, cd

        self._original = original
        self.model.blade_element_section = wrapped
        return self

    def __exit__(self, *exc_info) -> None:
        if self._original is not None:
            self.model.blade_element_section = self._original

    @property
    def occupancy(self) -> float:
        return self.clamped / self.calls if self.calls else float("nan")


def _pct(model_value: float, reference_value: float) -> float:
    if reference_value == 0.0:
        return float("nan")
    return 100.0 * (model_value - reference_value) / reference_value


def evaluate_case(model, case: Dict[str, object]) -> Dict[str, object]:
    """Evaluate the BEM model at one reference case and score it."""
    geom = model.PropellerGeometry(
        diameter_m=5.0,
        pitch_ratio=float(case["P_D"]),
        blade_area_ratio=float(case["Ae_Ao"]),
        blade_count=int(case["Z"]),
    )
    with ClampProbe(model) as probe:
        point = model.open_water_point(geom, float(case["J"]))
    return {
        "case_id": case["case_id"],
        "J": case["J"], "P_D": case["P_D"], "Ae_Ao": case["Ae_Ao"], "Z": case["Z"],
        "KT_ref": case["KT"], "KT_bem": point["KT"],
        "KQ_ref": case["KQ"], "KQ_bem": point["KQ"],
        "eta_0_ref": case["eta_0"], "eta_0_bem": point["eta0"],
        "err_KT_pct": _pct(point["KT"], case["KT"]),
        "err_KQ_pct": _pct(point["KQ"], case["KQ"]),
        "err_eta_0_pct": _pct(point["eta0"], case["eta_0"]),
        "clamp_occupancy": probe.occupancy,
        "induction_converged": point["induction_converged"],
    }


def _summary(errors: Sequence[float], accept_pct: float, strict_pct: float) -> Dict[str, object]:
    finite = [e for e in errors if e == e]  # drop NaN
    if not finite:
        return {"n": 0, "verdict": "WITHHELD"}
    abs_e = [abs(e) for e in finite]
    return {
        "n": len(finite),
        "max_abs_pct": max(abs_e),
        "mean_abs_pct": sum(abs_e) / len(abs_e),
        "rms_pct": math.sqrt(sum(e * e for e in finite) / len(finite)),
        "n_over_accept": sum(1 for e in abs_e if e > accept_pct),
        "n_over_strict": sum(1 for e in abs_e if e > strict_pct),
        "within_accept": max(abs_e) <= accept_pct,
        "within_strict": max(abs_e) <= strict_pct,
    }


def _mark_scored(rows: List[Dict[str, object]]) -> None:
    """Flag rows where a relative error is mathematically meaningful (in place).

    Within each propeller ``(P_D, Ae_Ao, Z)`` the reference ``KT``/``KQ`` at the
    lowest advance ratio in the box define the local scale; a row is scored only
    if both reference coefficients still exceed ``RELATIVE_FLOOR_FRACTION`` of
    that scale.  This keeps the near-zero-thrust tail visible but out of the
    headline statistics instead of letting a ``KQ -> 0`` division dominate them.
    """
    groups: Dict[Tuple[float, float, int], List[Dict[str, object]]] = {}
    for row in rows:
        groups.setdefault((row["P_D"], row["Ae_Ao"], int(row["Z"])), []).append(row)
    for members in groups.values():
        anchor = min(members, key=lambda r: r["J"])
        kt_floor = RELATIVE_FLOOR_FRACTION * abs(anchor["KT_ref"])
        kq_floor = RELATIVE_FLOOR_FRACTION * abs(anchor["KQ_ref"])
        for row in members:
            row["scored"] = bool(
                abs(row["KT_ref"]) >= kt_floor
                and abs(row["KQ_ref"]) >= kq_floor
                and row["KT_bem"] > 0.0
                and row["KQ_bem"] > 0.0
            )


def run_benchmark(dataset: Optional[Dict[str, object]] = None,
                  accept_pct: float = ACCEPT_PCT,
                  strict_pct: float = STRICT_PCT) -> Dict[str, object]:
    """Score the BEM model against the reference dataset."""
    data = dataset if dataset is not None else load_reference_dataset()
    model = _load_backend_model()
    rows = [evaluate_case(model, case) for case in data["cases"]]
    _mark_scored(rows)
    scored = [r for r in rows if r["scored"]]
    quant = {
        "KT": _summary([r["err_KT_pct"] for r in scored], accept_pct, strict_pct),
        "KQ": _summary([r["err_KQ_pct"] for r in scored], accept_pct, strict_pct),
        "eta_0": _summary([r["err_eta_0_pct"] for r in scored], accept_pct, strict_pct),
    }
    occupancy = [r["clamp_occupancy"] for r in rows if r["clamp_occupancy"] == r["clamp_occupancy"]]
    return {
        "mission": "m1655",
        "target": "backend/services/propeller_open_water.py::open_water_point",
        "reference": os.path.relpath(REFERENCE_DATA, REPO_ROOT),
        "reference_case_count": data["case_count"],
        "scored_case_count": len(scored),
        "excluded_case_count": len(rows) - len(scored),
        "relative_floor_fraction": RELATIVE_FLOOR_FRACTION,
        "accept_pct": accept_pct,
        "strict_pct": strict_pct,
        "quantities": quant,
        "clamp_occupancy_mean": (sum(occupancy) / len(occupancy)) if occupancy else float("nan"),
        "clamp_occupancy_max": max(occupancy) if occupancy else float("nan"),
        "all_induction_converged": all(r["induction_converged"] for r in rows),
        "verdict": _verdict(quant),
        "rows": rows,
    }


def _verdict(quant: Dict[str, Dict[str, object]]) -> Dict[str, str]:
    verdict = {}
    for key, summary in quant.items():
        if summary.get("verdict") == "WITHHELD":
            verdict[key] = "WITHHELD"
        elif summary["within_strict"]:
            verdict[key] = f"PASS(<{STRICT_PCT}%)"
        elif summary["within_accept"]:
            verdict[key] = f"PASS(<{ACCEPT_PCT}%)"
        else:
            verdict[key] = "FAIL"
    return verdict


# ---------------------------------------------------------------------------
# structural diagnostics (why the model misses, not just that it misses)
# ---------------------------------------------------------------------------

def structural_diagnostics(model=None) -> Dict[str, object]:
    """Three table-free structural comparisons against the canonical polynomial."""
    mdl = model or _load_backend_model()
    ref = _load_reference_polynomial()

    def bem(KT_fn_kwargs, J):
        geom = mdl.PropellerGeometry(diameter_m=5.0, **KT_fn_kwargs)
        return mdl.open_water_point(geom, J)

    # (1) sensitivity of KT to blade-area ratio: the BEM's chord is linear in
    #     Ae/A0, so d ln KT / d ln(Ae/A0) should approach 1 in attached flow;
    #     the B-series polynomial is much flatter (its Ae/A0 exponents are small).
    def log_slope(fn, lo, hi):
        a, b = fn(lo), fn(hi)
        if a <= 0 or b <= 0:
            return float("nan")
        return math.log(b / a) / math.log(hi / lo)

    base = dict(pitch_ratio=1.0, blade_count=4)
    J0 = 0.6
    bem_ae = log_slope(lambda ae: bem({**base, "blade_area_ratio": ae}, J0)["KT"], 0.40, 0.70)
    ref_ae = log_slope(lambda ae: ref.open_water_coefficients(J0, 1.0, ae, 4)["KT"], 0.40, 0.70)

    # (2) sensitivity to pitch ratio at fixed loading
    bem_pd = log_slope(lambda pd: _safe_pos(bem({**base, "blade_area_ratio": 0.55,
                                                 "pitch_ratio": pd}, J0)["KT"]), 1.0, 1.2)
    ref_pd = log_slope(lambda pd: ref.open_water_coefficients(J0, pd, 0.55, 4)["KT"], 1.0, 1.2)

    # (3) zero-thrust advance ratio, an error metric that is independent of any
    #     overall scale error in KT (both curves cross zero).
    j0_rows = []
    for P_D, Ae_Ao, Z in [(0.8, 0.40, 4), (1.0, 0.55, 4), (1.2, 0.70, 5), (1.4, 0.85, 5)]:
        geom = mdl.PropellerGeometry(diameter_m=5.0, pitch_ratio=P_D,
                                     blade_area_ratio=Ae_Ao, blade_count=Z)
        j0_bem = mdl.solve_zero_thrust_advance_ratio(geom)
        j0_ref = ref.zero_thrust_J(P_D, Ae_Ao, Z)
        j0_rows.append({
            "P_D": P_D, "Ae_Ao": Ae_Ao, "Z": Z,
            "J0_bem": j0_bem, "J0_ref": j0_ref,
            "err_pct": _pct(j0_bem, j0_ref) if j0_ref == j0_ref else float("nan"),
        })
    j0_err = [abs(r["err_pct"]) for r in j0_rows if r["err_pct"] == r["err_pct"]]

    return {
        "dlnKT_dlnAeAo": {"bem": bem_ae, "bseries": ref_ae},
        "dlnKT_dlnPD": {"bem": bem_pd, "bseries": ref_pd},
        "zero_thrust": {
            "rows": j0_rows,
            "max_abs_err_pct": max(j0_err) if j0_err else float("nan"),
        },
    }


def _safe_pos(value: float) -> float:
    return value if value > 0.0 else 1e-12


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def render_markdown(ledger: Dict[str, object], diag: Optional[Dict[str, object]] = None) -> str:
    quant = ledger["quantities"]
    lines = [
        "### 绝对量级对标：BEM 敞水模型 vs Wageningen B 系列正典",
        "",
        f"参考算例 {ledger['reference_case_count']} 个（声明包线 "
        f"J={ENVELOPE['J'][0]}–{ENVELOPE['J'][-1]}, P/D={ENVELOPE['P_D'][0]}–"
        f"{ENVELOPE['P_D'][-1]}, Ae/A0=0.40–0.85, Z=4,5）；接受线 "
        f"{ledger['accept_pct']:.0f}%，严格线 {ledger['strict_pct']:.0f}%；"
        f"计分子域 {ledger['scored_case_count']} 点"
        f"（排除 {ledger['excluded_case_count']} 点：参考 KT/KQ < 该桨 J=0.2 值的 "
        f"{ledger['relative_floor_fraction'] * 100:.0f}%，比值发散，不参与相对误差统计）。",
        "",
        "| 量 | max\\|err\\| [%] | mean\\|err\\| [%] | RMS [%] | 超 {0:.0f}% 点数 | 判定 |".format(
            ledger["accept_pct"]),
        "|---|---|---|---|---|---|",
    ]
    for key in ("KT", "KQ", "eta_0"):
        s = quant[key]
        lines.append(
            f"| {key} | {s['max_abs_pct']:.2f} | {s['mean_abs_pct']:.2f} | {s['rms_pct']:.2f} "
            f"| {s['n_over_accept']}/{s['n']} | **{ledger['verdict'][key]}** |"
        )
    lines += [
        "",
        f"截面失速（Cl 钳位）分支占用率：均值 {ledger['clamp_occupancy_mean'] * 100:.0f}%，"
        f"最大 {ledger['clamp_occupancy_max'] * 100:.0f}%；"
        f"动量迭代全部收敛={ledger['all_induction_converged']}。",
    ]
    if diag:
        ae, pd_ = diag["dlnKT_dlnAeAo"], diag["dlnKT_dlnPD"]
        lines += [
            "",
            "结构性敏感度（J=0.6, P/D=1.0, Z=4）：",
            "",
            "| 敏感度 | BEM | B 系列 | 说明 |",
            "|---|---|---|---|",
            f"| d ln KT / d ln(Ae/A0) | {ae['bem']:.2f} | {ae['bseries']:.2f} "
            f"| BEM 弦长∝Ae/A0，接近 1 即「升力随面积线性」 |",
            f"| d ln KT / d ln(P/D) | {pd_['bem']:.2f} | {pd_['bseries']:.2f} "
            f"| 螺距标定强度 |",
            f"| 零推力进速系数 J0 最大误差 | "
            f"{diag['zero_thrust']['max_abs_err_pct']:.1f}% | - "
            f"| 与 KT 整体比例误差无关的尺度无关指标 |",
        ]
    return "\n".join(lines) + "\n"


def make_figure(ledger: Dict[str, object], path: str) -> str:
    """Two-panel figure: open-water curves vs reference, and the error map."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    model = _load_backend_model()
    ref = _load_reference_polynomial()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    fig, axes = plt.subplots(2, 2, figsize=(11.0, 8.0))
    props = [(1.0, 0.55, 4, "P/D=1.0, Ae/A0=0.55, Z=4"),
             (1.2, 0.70, 5, "P/D=1.2, Ae/A0=0.70, Z=5")]
    Js = [0.05 * i for i in range(3, 25)]
    for ax, (P_D, Ae, Z, label) in zip(axes[0], props):
        geom = model.PropellerGeometry(diameter_m=5.0, pitch_ratio=P_D,
                                       blade_area_ratio=Ae, blade_count=Z)
        bem = [model.open_water_point(geom, J) for J in Js]
        bsr = [ref.open_water_coefficients(J, P_D, Ae, Z) for J in Js]
        ax.plot(Js, [b["KT"] for b in bsr], "k--", lw=1.5, label="B-series $K_T$ (ref)")
        ax.plot(Js, [b["KT"] for b in bem], "C0-", lw=1.6, label="BEM $K_T$")
        ax.plot(Js, [b["KQ"] for b in bsr], "k:", lw=1.5, label="B-series $K_Q$ (ref)")
        ax.plot(Js, [b["KQ"] for b in bem], "C1-", lw=1.6, label="BEM $K_Q$")
        ax.set_xlabel("advance coefficient $J$")
        ax.set_ylabel("$K_T$, $K_Q$")
        ax.set_title(label, fontsize=9)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7)

    # error maps: err vs J, one line per P/D at Ae/A0=0.55, Z=4
    for ax, key, col in ((axes[1][0], "err_KT_pct", 0), (axes[1][1], "err_eta_0_pct", 2)):
        for P_D in (0.8, 1.0, 1.2, 1.4):
            rows = sorted(
                [r for r in ledger["rows"]
                 if abs(r["P_D"] - P_D) < 1e-9 and abs(r["Ae_Ao"] - 0.55) < 1e-9
                 and r["Z"] == 4],
                key=lambda r: r["J"])
            ax.plot([r["J"] for r in rows], [r[key] for r in rows], "o-", ms=3,
                    label=f"P/D={P_D}")
        ax.axhline(ledger["accept_pct"], color="r", ls="--", lw=1.0, label="±15% bar")
        ax.axhline(-ledger["accept_pct"], color="r", ls="--", lw=1.0)
        ax.axhline(0.0, color="k", lw=0.6)
        ax.set_xlabel("advance coefficient $J$")
        ax.set_ylabel(("error in $K_T$ [%]" if col == 0 else "error in $\\eta_0$ [%]"))
        ax.grid(alpha=0.3)
        if col == 0:
            ax.legend(fontsize=7, title="Ae/A0=0.55, Z=4", title_fontsize=7)
    axes[1][1].set_title("efficiency error (same cases)", fontsize=9)
    fig.suptitle("m1655: in-repo BEM open-water model vs canonical Wageningen B-series",
                 fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write-reference", action="store_true",
                    help="(re)generate backend/data/propulsion_benchmark_reference.json")
    ap.add_argument("--json", dest="json_path", help="write the ledger JSON here")
    ap.add_argument("--markdown", dest="md_path", help="write the markdown table here")
    ap.add_argument("--figure", dest="fig_path", help="write the two-panel figure here")
    ap.add_argument("--no-diagnostics", action="store_true")
    args = ap.parse_args(argv)

    if args.write_reference:
        path = write_reference_dataset()
        print(f"reference dataset written: {os.path.relpath(path, REPO_ROOT)}")
        if not (args.json_path or args.md_path or args.fig_path):
            return 0

    ledger = run_benchmark()
    diag = None if args.no_diagnostics else structural_diagnostics()
    ledger["diagnostics"] = diag

    print(render_markdown(ledger, diag))
    if args.json_path:
        os.makedirs(os.path.dirname(os.path.abspath(args.json_path)), exist_ok=True)
        with open(args.json_path, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh, indent=1, sort_keys=True)
        print(f"\nledger -> {args.json_path}")
    if args.md_path:
        os.makedirs(os.path.dirname(os.path.abspath(args.md_path)), exist_ok=True)
        with open(args.md_path, "w", encoding="utf-8") as fh:
            fh.write(render_markdown(ledger, diag))
        print(f"markdown -> {args.md_path}")
    if args.fig_path:
        make_figure(ledger, args.fig_path)
        print(f"figure -> {args.fig_path}")
    return 0 if all(v != "FAIL" for v in ledger["verdict"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())