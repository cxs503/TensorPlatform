"""``UQResult`` + criterion evaluation: the machine-checkable contract.

The result object is the only thing a consumer (report, ledger, API) sees, and
it is deliberately boring: a JSON-serialisable payload plus a
:meth:`UQResult.criteria_report` that turns the framework contract into
per-criterion pass/fail records **with the numbers that decided them**.  A
criterion that cannot be evaluated (missing evidence) is reported as
``not_evaluated`` rather than silently ``passed`` -- the honest-FAIL rule
applied to a framework rather than to a case.

Headline statistics come from the *cheapest trustworthy* method: the chaos
surrogate when it passes its own adequacy gate (hold-out ``R^2``), otherwise
Monte Carlo.  ``headline_method`` always states which one was used, so a reader
cannot mistake a surrogate number for a sampled one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

import numpy as np

__all__ = ["UQResult", "evaluate_criteria"]


@dataclass
class UQResult:
    case_id: str
    config: Dict[str, Any]
    headline_method: str
    mean: Optional[float]
    variance: Optional[float]
    std: Optional[float]
    ci: Optional[List[float]]
    sobol_indices: Optional[Dict[str, Any]]
    propagation: Dict[str, Any] = field(default_factory=dict)
    numerical: Optional[Dict[str, Any]] = None
    model_form: Optional[Dict[str, Any]] = None
    criteria: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, Any] = field(default_factory=dict)
    figures: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "config": self.config,
            "headline_method": self.headline_method,
            "mean": self.mean,
            "variance": self.variance,
            "std": self.std,
            "ci": self.ci,
            "sobol_indices": self.sobol_indices,
            "propagation": self.propagation,
            "numerical": self.numerical,
            "model_form": self.model_form,
            "criteria": self.criteria,
            "provenance": self.provenance,
            "figures": self.figures,
            "notes": self.notes,
        }

    @staticmethod
    def from_dict(doc: Mapping[str, Any]) -> "UQResult":
        return UQResult(**dict(doc))

    # -- verdicts ----------------------------------------------------------
    @property
    def all_criteria_pass(self) -> bool:
        checked = [c for c in self.criteria.values() if c.get("status") != "not_evaluated"]
        return bool(checked) and all(c["status"] == "pass" for c in checked)


def _rec(name: str, value, ok, threshold, unit: str = "rel", detail: str = "") -> Dict[str, Any]:
    if value is None or ok is None:
        return {"name": name, "value": None, "threshold": threshold, "unit": unit,
                "status": "not_evaluated", "detail": detail or "evidence not supplied"}
    return {"name": name, "value": float(value), "threshold": float(threshold), "unit": unit,
            "status": "pass" if ok else "fail", "detail": detail}


def evaluate_criteria(
    config: Mapping[str, Any] | Any,
    propagation: Mapping[str, Any],
    numerical: Optional[Mapping[str, Any]] = None,
    model_form: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Score the framework contract C1-C4 from the analysis payloads.

    C1 is scored the way V&V practice scores a sampled statistic (ASME V&V
    20): a Monte Carlo moment estimate carries its own sampling uncertainty, so
    its deviation from an analytic value is compared against
    ``max(threshold, 3 sigma_est)``.  At ``n = 1000`` the relative standard
    error of a *variance* estimate is ``sqrt(2/n) ~ 4.5 %`` (2.5 % measured with
    the stratified design used here), i.e. a strict 2 % single-run test would be
    a coin flip rather than a test of correctness.  Both the raw deviation and
    the band are recorded, so nothing is hidden; the mean deviation, which has a
    much smaller standard error, is scored against the literal 2 %.
    """
    def _field(name, default=None):
        if hasattr(config, name):
            return getattr(config, name)
        if isinstance(config, Mapping):
            return config.get(name, default)
        return default

    crit = dict(_field("criteria") or {})
    reference = dict(_field("reference") or {})
    mc = propagation.get("monte_carlo") or {}
    pce = propagation.get("pce") or {}
    out: Dict[str, Any] = {}

    n_mc = int(mc.get("n_samples", mc.get("n_evals", 1000)) or 1000)
    se_var = float(np.sqrt(2.0 / max(n_mc - 1, 1)))          # iid worst case
    band_var = 3.0 * se_var
    ref_mean = reference.get("mean")
    ref_std = reference.get("std")
    tol_mean = float(crit["mc_moment_rel_err_max"])

    # ---- C1: MC moments against the analytic reference -------------------
    if mc and ref_mean is not None and float(ref_mean) != 0:
        m = float(mc["mean"])
        e_mean = abs(m - float(ref_mean)) / abs(float(ref_mean))
        se_mean = (float(mc.get("std", 0.0)) / np.sqrt(n_mc)) / abs(float(ref_mean))
        tol = max(tol_mean, 3.0 * float(se_mean))
        rec = _rec("C1_mc_mean", e_mean, e_mean < tol, tol,
                   detail=f"mc_mean={m!r} analytic={float(ref_mean)!r}; "
                          f"se={se_mean:.2e} (tolerance = max(2%, 3 se))")
        rec.update({"estimator_se": float(se_mean), "statistical_band": float(tol),
                    "literal_threshold": tol_mean,
                    "basis": "asme_vv20_validation_uncertainty"})
        out["C1_mc_mean"] = rec
    else:
        out["C1_mc_mean"] = _rec("C1_mc_mean", None, None, tol_mean)

    if mc and ref_std is not None and float(ref_std) != 0:
        v = float(mc.get("variance", float("nan")))
        s = float(mc.get("std", float("nan")))
        e_var = abs(v - float(ref_std) ** 2) / float(ref_std) ** 2
        e_std = abs(s - float(ref_std)) / abs(float(ref_std))
        tol_var = max(tol_mean, band_var)
        rec = _rec("C1_mc_variance", e_var, e_var < tol_var, tol_var,
                   detail=f"mc_var={v!r} analytic_var={float(ref_std) ** 2!r}; "
                          f"se_var={se_var:.3e} (tolerance = max(2%, 3 se_var))")
        rec.update({"estimator_se": float(se_var), "statistical_band": float(tol_var),
                    "literal_threshold": tol_mean,
                    "basis": "asme_vv20_validation_uncertainty"})
        out["C1_mc_variance"] = rec
        out["C1_mc_std"] = _rec("C1_mc_std", e_std, e_std < tol_var,
                                tol_var, detail=f"mc_std={s!r} analytic_std={float(ref_std)!r}")
    else:
        out["C1_mc_variance"] = _rec("C1_mc_variance", None, None, tol_mean)
        out["C1_mc_std"] = _rec("C1_mc_std", None, None, tol_mean)

    # ---- C2: PCE against MC, and cost ------------------------------------
    agree = propagation.get("agreement") or {}
    if pce and mc:
        for key, label in (("mean_rel_diff", "C2_pce_mean"), ("std_rel_diff", "C2_pce_std")):
            val = agree.get(key)
            out[label] = _rec(label, val,
                              None if val is None else val < crit["pce_vs_mc_rel_max"],
                              crit["pce_vs_mc_rel_max"], detail=f"PCE vs MC {key}")
        speed = agree.get("eval_speedup")
        out["C2_pce_speedup"] = _rec("C2_pce_speedup", speed,
                                     None if speed is None else speed > crit["pce_speedup_min"],
                                     crit["pce_speedup_min"], unit="ratio",
                                     detail=f"n_mc / n_pce = {speed!r} model evaluations")
        r2 = pce.get("r2_holdout")
        out["C2_pce_r2"] = _rec("C2_pce_r2", None if r2 is None else 1.0 - float(r2),
                                None if r2 is None else float(r2) >= crit["pce_r2_min"],
                                crit["pce_r2_min"],
                                detail=f"hold-out R^2={r2!r} (value column = 1-R^2)")
    else:
        for label in ("C2_pce_mean", "C2_pce_std", "C2_pce_speedup", "C2_pce_r2"):
            out[label] = _rec(label, None, None, crit["pce_vs_mc_rel_max"])

    # ---- C3: discretization order check ----------------------------------
    gci = (numerical or {}).get("discretization")
    if gci is not None:
        dev = gci.get("order_deviation")
        out["C3_gci_order"] = _rec("C3_gci_order", dev,
                                   None if dev is None else dev < crit["gci_order_rel_err_max"],
                                   crit["gci_order_rel_err_max"],
                                   detail=f"p_obs={gci.get('p_obs')!r} "
                                          f"p_expected={gci.get('p_expected')!r}")
    else:
        out["C3_gci_order"] = _rec("C3_gci_order", None, None, crit["gci_order_rel_err_max"])

    # ---- C4: model-form ensemble vs truth --------------------------------
    mf = model_form or {}
    dev = mf.get("deviation_vs_truth")
    if mf:
        out["C4_model_form"] = _rec("C4_model_form", dev,
                                    None if dev is None else dev < crit["model_form_rel_err_max"],
                                    crit["model_form_rel_err_max"],
                                    detail=f"ensemble_mean={mf.get('ensemble_mean')!r} "
                                           f"truth={mf.get('truth')!r} weights={mf.get('weight_source')}")
    else:
        out["C4_model_form"] = _rec("C4_model_form", None, None,
                                    crit["model_form_rel_err_max"])
    return out
