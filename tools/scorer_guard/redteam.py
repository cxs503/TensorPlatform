"""Red-team case suite: analytic limits, Goodhart attacks, physics, provenance.

A case is a small deterministic probe with an *expectation dict*; a case whose
expectation is not met is a finding.  Cases of kind ``GOODHART_ATTACK`` carry
``must_intercept=True``: the submission may never earn credit, and the specific
reason code must appear.  Cases of kind ``ANALYTIC_LIMIT`` pin the closed-form
score limits (100 / 50 / 0) to machine precision, so a *known solution earns a
known score* -- the property that makes mutation testing meaningful.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from . import anchors as A
from . import physics
from . import prereg
from . import score as S

ANALYTIC_LIMIT = "ANALYTIC_LIMIT"
GOODHART_ATTACK = "GOODHART_ATTACK"
GOODHART_CAP = "GOODHART_CAP"
PHYSICS = "PHYSICS_CONSERVATION"
PROVENANCE = "ANCHOR_PROVENANCE"
LOCK = "VERSION_LOCK"


@dataclass
class Case:
    case_id: str
    kind: str
    description: str
    must_intercept: bool
    expect: Dict[str, Any]
    fn: Callable[[], Dict[str, Any]] = field(repr=False)

    def run(self) -> Dict[str, Any]:
        return self.fn()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def verified_anchors(anchors: Sequence[Any]) -> List[Any]:
    return [a for a in anchors if a.provenance_status == "VERIFIED" and a.value is not None]


def by_id(anchors: Sequence[Any], anchor_id: str) -> Any:
    for a in anchors:
        if a.anchor_id == anchor_id:
            return a
    raise KeyError(anchor_id)


def entries(
    anchors: Sequence[Any],
    deltas: Optional[Mapping[str, float]] = None,
    skip: Sequence[str] = (),
    **extra: Any,
) -> List[Dict[str, Any]]:
    """Build a submission: value = anchor * (1 + delta), with optional extras."""
    deltas = dict(deltas or {})
    out = []
    for a in verified_anchors(anchors):
        if a.anchor_id in skip:
            continue
        rec: Dict[str, Any] = {
            "anchor_id": a.anchor_id,
            "value": float(a.value) * (1.0 + float(deltas.get(a.anchor_id, 0.0))),
        }
        for key, payload in extra.items():
            if isinstance(payload, Mapping) and a.anchor_id in payload:
                rec[key] = payload[a.anchor_id]
        out.append(rec)
    return out


def synthetic_experiment_registry() -> Dict[str, Any]:
    """Two *synthetic* measured anchors (fixture only) to exercise the leak gate.

    The values are invented on purpose and labelled ``SYNTHETIC``; they are never
    published as ``data/scorer_guard/anchors*.json`` and never used to score a
    real submission.  A leak gate cannot be tested without anchors whose exact
    value is *not* reproducible from a closed form.
    """
    recs = [
        {
            "anchor_id": "FIX-EXP-TANK-A",
            "quantity": "CT_total",
            "covariates": {"Fr": 0.25},
            "value": 0.0041234567,
            "unit": "-",
            "source_type": "EXPERIMENT",
            "source": {
                "citation": "SYNTHETIC FIXTURE - not a measurement, gate test only",
                "doc_sha256": "0" * 64,
                "retrieved": "2026-11-06",
            },
            "uncertainty_rel": 0.03,
            "score_e_max": 0.05,
            "envelope": {"Fr": [0.15, 0.40]},
            "provenance_status": "VERIFIED",
            "weight": 1.0,
        },
        {
            "anchor_id": "FIX-EXP-TANK-B",
            "quantity": "CT_total",
            "covariates": {"Fr": 0.30},
            "value": 0.0052345678,
            "unit": "-",
            "source_type": "EXPERIMENT",
            "source": {
                "citation": "SYNTHETIC FIXTURE - not a measurement, gate test only",
                "doc_sha256": "0" * 64,
                "retrieved": "2026-11-06",
            },
            "uncertainty_rel": 0.03,
            "score_e_max": 0.05,
            "envelope": {"Fr": [0.15, 0.40]},
            "provenance_status": "VERIFIED",
            "weight": 1.0,
        },
    ]
    reg: Dict[str, Any] = {"registry_id": "synthetic_leak_fixture", "version": "0", "anchors": recs}
    reg["ledger"] = {"chain": A.chain_of(recs), "head": A.chain_head(recs)}
    return reg


def make_ctx(registry: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    reg = A.build_default_registry() if registry is None else dict(registry)
    anchor_objs = A.anchors_from(reg)
    head = A.chain_head(reg["anchors"])
    lock = prereg.build_lock(reg, [])
    return {
        "registry": reg,
        "anchors": anchor_objs,
        "head": head,
        "fingerprint": S.scorer_fingerprint(head),
        "lock": lock,
    }


def _score(ctx: Mapping[str, Any], ents: Sequence[Mapping[str, Any]], **kwargs: Any) -> Dict[str, Any]:
    kwargs.setdefault("expected_fingerprint", ctx["fingerprint"])
    kwargs.setdefault("anchor_head", ctx["head"])
    return S.score_submission(ents, ctx["anchors"], **kwargs)


# --------------------------------------------------------------------------- #
# case definitions
# --------------------------------------------------------------------------- #
def build_cases(ctx: Mapping[str, Any]) -> List[Case]:
    anchors = ctx["anchors"]
    cf7 = by_id(anchors, "A-CF-ITTC57-RE1E7")
    emax = float(cf7.score_e_max)
    cf7_value = float(cf7.value)
    cases: List[Case] = []

    def limit_case(cid: str, delta_rel: float, expect: Dict[str, Any], desc: str) -> Case:
        def fn() -> Dict[str, Any]:
            return S.anchor_score(cf7_value * (1.0 + delta_rel), cf7)

        return Case(cid, ANALYTIC_LIMIT, desc, False, expect, fn)

    cases.append(limit_case("L1-exact-anchor", 0.0, {"score_eq": 100.0},
                            "exact anchor value must earn exactly 100 (known solution, known score)"))
    cases.append(limit_case("L2-half-band", emax / 2.0, {"score_eq": 50.0},
                            "error of e_max/2 must earn exactly 50 by the closed form"))
    cases.append(limit_case("L3-band-edge", emax, {"score_eq": 0.0},
                            "error exactly at e_max must earn exactly 0 (cliff edge)"))
    cases.append(limit_case("L4-beyond-band", 5.0 * emax, {"score_eq": 0.0},
                            "gross error must clamp at 0, never go negative"))
    cases.append(limit_case("L5-over-prediction", -emax / 2.0, {"score_eq": 50.0},
                            "score is symmetric in the sign of the error"))

    def sweep() -> Dict[str, Any]:
        pts = [i / 100.0 for i in range(0, 51)]
        vals = [S.anchor_score(cf7_value * (1.0 + p), cf7)["score"] for p in pts]
        monotone = all(vals[i] >= vals[i + 1] - 1e-12 for i in range(len(vals) - 1))
        return {"monotone": monotone, "n": len(vals), "first": vals[0], "last": vals[-1]}

    cases.append(Case("L6-monotone", ANALYTIC_LIMIT,
                      "score is non-increasing in |relative error| over a 0..50%% sweep",
                      False, {"monotone": True, "first_eq": 100.0, "last_eq": 0.0}, sweep))

    def exact_submission() -> Dict[str, Any]:
        out = _score(ctx, entries(anchors))
        return {"verdict": out["verdict"], "score": out["score"], "reasons": out["reasons"]}

    cases.append(Case("L7-exact-submission", ANALYTIC_LIMIT,
                      "a submission reproducing every verified anchor earns 100 and is accepted",
                      False, {"verdict": "ACCEPTED", "score_eq": 100.0}, exact_submission))

    def deterministic() -> Dict[str, Any]:
        a = _score(ctx, entries(anchors, {"A-CF-ITTC57-RE1E6": 0.01}))
        b = _score(ctx, entries(anchors, {"A-CF-ITTC57-RE1E6": 0.01}))
        return {"identical": S.canonical_json(a) == S.canonical_json(b), "score": a["score"]}

    cases.append(Case("L8-determinism", ANALYTIC_LIMIT,
                      "same submission scored twice yields byte-identical output",
                      False, {"identical": True}, deterministic))

    # ------------------------------- attacks ------------------------------- #
    def atk(cid: str, desc: str, reason: str, fn: Callable[[], Dict[str, Any]]) -> Case:
        def wrapped(_fn=fn) -> Dict[str, Any]:
            out = _fn()
            codes = [r.get("code") for r in out.get("reasons", [])]
            return {
                "verdict": out["verdict"],
                "score": out["score"],
                "codes": codes,
                "expected_code_present": reason in codes,
            }

        return Case(cid, GOODHART_ATTACK, desc, True,
                    {"verdict": "REJECTED", "score_eq": 0.0, "expected_code_present": True,
                     "code": reason}, wrapped)

    cases.append(atk(
        "A1-constant-output", "constant filler across distinct anchors", "DEGENERATE_CONSTANT_OUTPUT",
        lambda: _score(ctx, [
            {"anchor_id": a.anchor_id, "value": 0.003} for a in verified_anchors(anchors)
        ]),
    ))
    cases.append(atk(
        "A2-unit-decade", "one anchor off by three decades (unit confusion)", "UNIT_SCALE_ERROR",
        lambda: _score(ctx, entries(anchors, {"A-CF-ITTC57-RE1E9": 999.0})),
    ))
    cases.append(atk(
        "A3-nan", "NaN instead of a number (crash-to-top exploit)", "NON_FINITE_OR_NON_NUMERIC",
        lambda: _score(ctx, entries(anchors, {"A-CF-BLAS-RE1E5": float("nan")})),
    ))
    cases.append(atk(
        "A4-out-of-envelope", "claims a regime outside the anchor envelope", "OUT_OF_ENVELOPE",
        lambda: _score(ctx, entries(anchors, regime={"A-CF-ITTC57-RE1E7": {"Re": 1.0e13}})),
    ))
    cases.append(atk(
        "A5-regime-slop", "solves a nearby-but-different regime and claims the anchor", "REGIME_MISMATCH",
        lambda: _score(ctx, entries(anchors, regime={"A-CF-ITTC57-RE1E7": {"Re": 2.0e7}})),
    ))
    cases.append(atk(
        "A6-missing-anchor", "drops the hardest anchor from the submission", "MISSING_ANCHOR",
        lambda: _score(ctx, entries(anchors, skip=["A-CF-KS-RE1E9"])),
    ))
    cases.append(atk(
        "A7-quarantined-anchor", "submits against a quarantined, unsourced anchor",
        "ANCHOR_NOT_VERIFIED",
        lambda: _score(ctx, [{"anchor_id": "A-CT-EXPERIMENT-PENDING", "value": 0.004}],
                       require_all=False),
    ))
    cases.append(atk(
        "A9-physics-froude", "perfect numbers but Froude invariance broken by 5%",
        "PHYSICS_INVARIANT_VIOLATION",
        lambda: _score(ctx, entries(anchors, physics={
            "A-CF-ITTC57-RE1E7": {"froude_invariance": {"cw_model": 0.001, "cw_ship": 0.00105}}}),
        ),
    ))
    cases.append(atk(
        "A10-physics-thrust", "self-propulsion claim violates T(1-t)=R_T by 20%",
        "PHYSICS_INVARIANT_VIOLATION",
        lambda: _score(ctx, entries(anchors, physics={
            "A-CF-ITTC57-RE1E6": {"thrust_balance": {
                "thrust": 1000.0, "thrust_deduction": 0.2, "resistance": 1000.0}}}),
        ),
    ))
    cases.append(atk(
        "A11-replay", "byte-identical resubmission is refused", "REPLAY_DUPLICATE",
        lambda: _score(ctx, entries(anchors), seen_payloads=[S.payload_hash(entries(anchors))]),
    ))
    cases.append(atk(
        "A12-unlocked-scorer", "scored with a fingerprint that is not the locked one",
        "SCORER_LOCK_MISMATCH",
        lambda: _score(ctx, entries(anchors), expected_fingerprint="0" * 64),
    ))

    def leak_registry_case() -> Dict[str, Any]:
        reg = synthetic_experiment_registry()
        a2 = A.anchors_from(reg)
        head = A.chain_head(reg["anchors"])
        ents = [{"anchor_id": a.anchor_id, "value": a.value} for a in a2]
        out = S.score_submission(ents, a2, expected_fingerprint=S.scorer_fingerprint(head),
                                 anchor_head=head)
        return out

    cases.append(atk("A8-anchor-leak", "bit-exact replay of two measured anchor values",
                     "ANCHOR_LEAKAGE_SUSPECT", leak_registry_case))

    def cherry_pick() -> Dict[str, Any]:
        dels = {a.anchor_id: 0.0 for a in verified_anchors(anchors) if a.anchor_id != "A-CF-KS-RE1E9"}
        dels["A-CF-KS-RE1E9"] = 2.0  # 200% error -> 0 on the hardest anchor
        out = _score(ctx, entries(anchors, dels))
        return {"verdict": out["verdict"], "score": out["score"],
                "worst": out["diagnostics"]["worst"], "mean": out["diagnostics"]["mean"]}

    cases.append(Case("A13-cherry-pick", GOODHART_CAP,
                      "acing 4 anchors while bombing 1 is capped by the worst-case half",
                      False, {"verdict": "ACCEPTED", "score_close": [40.0, 1e-9],
                              "score_lt": 60.0}, cherry_pick))

    # ------------------------------- physics ------------------------------- #
    def froude_consistency() -> Dict[str, Any]:
        ok_res = physics.froude_invariance_residual(0.001234, 0.001234)
        bad_res = physics.froude_invariance_residual(0.001234, 0.001234 * 1.05)
        return {"ok_residual": ok_res, "bad_residual": bad_res,
                "ok_pass": physics.check_claims({"froude_invariance": {
                    "cw_model": 0.001234, "cw_ship": 0.001234}}) == [],
                "bad_flagged": len(physics.check_claims({"froude_invariance": {
                    "cw_model": 0.001234, "cw_ship": 0.001234 * 1.05}})) == 1}

    cases.append(Case("P1-froude-invariance", PHYSICS,
                      "Cw_model == Cw_ship under Froude scaling; 5% mismatch flagged",
                      False, {"ok_pass": True, "bad_flagged": True, "ok_residual_lt": 1e-12},
                      froude_consistency))

    def additivity() -> Dict[str, Any]:
        cf, k, cw = 0.003, 0.15, 0.0009
        ct = (1.0 + k) * cf + cw
        resid = physics.additivity_residual(ct, cf, k, cw)
        bad = physics.additivity_residual(ct * 1.001, cf, k, cw)
        return {"residual": resid, "bad_residual": bad,
                "closes": resid <= physics.EXACT_TOL, "bad_flagged": bad > physics.EXACT_TOL}

    cases.append(Case("P2-additivity", PHYSICS,
                      "ITTC-78 decomposition Ct=(1+k)Cf+Cw must close to 1e-6",
                      False, {"closes": True, "bad_flagged": True, "residual_lt": 1e-12}, additivity))

    def hydrostatics() -> Dict[str, Any]:
        delta = physics.hydrostatic_mass(1025.0, 0.65, 120.0, 20.0, 7.0)
        resid = physics.hydrostatic_mass_residual(1025.0, 0.65, 120.0, 20.0, 7.0, delta)
        bad = physics.hydrostatic_mass_residual(1025.0, 0.65, 120.0, 20.0, 7.0, delta * 1.02)
        return {"delta": delta, "residual": resid, "bad_residual": bad,
                "closes": resid <= physics.EXACT_TOL, "bad_flagged": bad > physics.EXACT_TOL}

    cases.append(Case("P3-hydrostatic-mass", PHYSICS,
                      "Delta = rho Cb L B T closes exactly; 2% bias flagged",
                      False, {"closes": True, "bad_flagged": True}, hydrostatics))

    def anchor_recompute() -> Dict[str, Any]:
        rep = A.provenance_report(ctx["registry"])
        return {"max_rel_dev": rep["max_rel_dev_verified"], "n_verified": rep["n_verified"],
                "n_quarantined": rep["n_quarantined"], "ledger_ok": rep["ledger"]["ok"],
                "below_1e12": rep["max_rel_dev_verified"] <= 1e-12}

    cases.append(Case("P4-anchor-recompute", PHYSICS,
                      "every verified anchor re-derived independently to rel dev <= 1e-12",
                      False, {"below_1e12": True, "n_verified": 5, "n_quarantined": 1},
                      anchor_recompute))

    # ------------------------------ provenance ----------------------------- #
    def ledger_ok() -> Dict[str, Any]:
        return A.verify_ledger(ctx["registry"])

    cases.append(Case("V1-ledger", PROVENANCE,
                      "pristine registry ledger verifies", False, {"ok": True}, ledger_ok))

    def ledger_tamper() -> Dict[str, Any]:
        reg = A.build_default_registry()
        reg["anchors"][1]["value"] = float(reg["anchors"][1]["value"]) * 1.05
        v = A.verify_ledger(reg)
        return {"ok": v["ok"], "reason": v["reason"], "tamper_detected": not v["ok"]}

    cases.append(Case("V2-ledger-tamper", PROVENANCE,
                      "inflating an anchor value by 5% breaks the chain head",
                      False, {"tamper_detected": True, "ok": False}, ledger_tamper))

    def ledger_order() -> Dict[str, Any]:
        reg = A.build_default_registry()
        recs = list(reg["anchors"])
        head_a = A.chain_head(recs)
        swapped = list(recs)
        swapped[0], swapped[1] = swapped[1], swapped[0]
        head_b = A.chain_head(swapped)
        return {"order_sensitive": head_a != head_b, "head_a": head_a[:16], "head_b": head_b[:16]}

    cases.append(Case("V3-ledger-order", PROVENANCE,
                      "chain head is order sensitive (anchors cannot be re-ordered silently)",
                      False, {"order_sensitive": True}, ledger_order))

    def fingerprint_sensitive() -> Dict[str, Any]:
        fp_a = S.scorer_fingerprint(ctx["head"])
        reg = A.build_default_registry()
        reg["anchors"][0]["value"] = float(reg["anchors"][0]["value"]) * 1.01
        fp_b = S.scorer_fingerprint(A.chain_head(reg["anchors"]))
        fp_v = S.scorer_fingerprint(ctx["head"], version="9.9.9")
        return {"anchor_sensitive": fp_a != fp_b, "version_sensitive": fp_a != fp_v, "fp": fp_a[:16]}

    cases.append(Case("V4-fingerprint", LOCK,
                      "scorer fingerprint binds anchor head and scorer version",
                      False, {"anchor_sensitive": True, "version_sensitive": True},
                      fingerprint_sensitive))

    def lock_verify() -> Dict[str, Any]:
        case_ids = [c.case_id for c in build_cases_shallow(ctx)]
        lock = prereg.build_lock(ctx["registry"], case_ids)
        ok = prereg.verify_lock(lock, ctx["registry"], case_ids)
        reg_bad = A.build_default_registry()
        reg_bad["anchors"][2]["value"] = float(reg_bad["anchors"][2]["value"]) * 1.02
        drift = prereg.verify_lock(lock, reg_bad, case_ids)
        suite_drift = prereg.verify_lock(lock, ctx["registry"], case_ids + ["X-extra"])
        return {
            "ok": ok["ok"],
            "anchor_drift_caught": "ANCHOR_HEAD_DRIFT" in drift["drift"],
            "suite_drift_caught": "CASE_SUITE_DRIFT" in suite_drift["drift"],
            "lock_fingerprint": lock["fingerprint"][:16],
        }

    cases.append(Case("V5-prereg-lock", LOCK,
                      "lock verifies; anchor drift and case-suite drift are caught",
                      False, {"ok": True, "anchor_drift_caught": True, "suite_drift_caught": True},
                      lock_verify))

    return cases


def build_cases_shallow(ctx: Mapping[str, Any]) -> List[Case]:
    """Case ids only (avoids recursion inside V5); ids are the suite identity."""
    base = ["L1-exact-anchor", "L2-half-band", "L3-band-edge", "L4-beyond-band", "L5-over-prediction",
            "L6-monotone", "L7-exact-submission", "L8-determinism",
            "A1-constant-output", "A2-unit-decade", "A3-nan", "A4-out-of-envelope",
            "A5-regime-slop", "A6-missing-anchor", "A7-quarantined-anchor", "A8-anchor-leak",
            "A9-physics-froude", "A10-physics-thrust", "A11-replay", "A12-unlocked-scorer",
            "A13-cherry-pick",
            "P1-froude-invariance", "P2-additivity", "P3-hydrostatic-mass", "P4-anchor-recompute",
            "V1-ledger", "V2-ledger-tamper", "V3-ledger-order", "V4-fingerprint", "V5-prereg-lock"]
    return [Case(cid, "ID", "", False, {}, lambda: {}) for cid in base]


def check_expect(out: Mapping[str, Any], expect: Mapping[str, Any]) -> List[str]:
    """Return a list of unmet expectations (empty == pass)."""
    problems: List[str] = []
    for key, want in expect.items():
        if key == "verdict" and out.get("verdict") != want:
            problems.append(f"verdict {out.get('verdict')!r} != {want!r}")
        elif key == "score_eq":
            got = float(out.get("score", float("nan")))
            if not abs(got - float(want)) <= 1e-9:
                problems.append(f"score {got!r} != {want!r}")
        elif key == "score_close":
            got = float(out.get("score", float("nan")))
            target, tol = float(want[0]), float(want[1])
            if not abs(got - target) <= tol:
                problems.append(f"score {got!r} not within {tol} of {target!r}")
        elif key == "score_lt" and not float(out.get("score", float("inf"))) < float(want):
            problems.append(f"score {out.get('score')!r} not < {want!r}")
        elif key == "code":
            codes = [r.get("code") for r in out.get("reasons", [])] if "reasons" in out else []
            if out.get("codes") is not None:
                codes = out["codes"]
            if want not in codes:
                problems.append(f"reason {want!r} absent (codes={codes})")
        elif key.endswith("_eq"):
            field = key[:-3]
            got = out.get(field)
            if not (isinstance(got, (int, float)) and abs(float(got) - float(want)) <= 1e-9
                    or got == want):
                problems.append(f"{field} {got!r} != {want!r}")
        elif key.endswith("_lt"):
            field = key[:-3]
            got = out.get(field)
            if not isinstance(got, (int, float)) or not float(got) < float(want):
                problems.append(f"{field} {got!r} not < {want!r}")
        else:
            if out.get(key) != want:
                problems.append(f"{key} {out.get(key)!r} != {want!r}")
    return problems


def run_cases(ctx: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Execute the suite; return per-case records plus an aggregate verdict."""
    ctx = make_ctx() if ctx is None else ctx
    results = []
    for case in build_cases(ctx):
        try:
            out = case.run()
            problems = check_expect(out, case.expect)
        except Exception as exc:  # a case that explodes is a failure, not a pass
            out, problems = {"error": repr(exc)}, [f"exception: {exc!r}"]
        results.append({
            "case_id": case.case_id,
            "kind": case.kind,
            "description": case.description,
            "must_intercept": case.must_intercept,
            "expect": case.expect,
            "output": out,
            "problems": problems,
            "passed": not problems,
        })
    attacks = [r for r in results if r["must_intercept"]]
    return {
        "n_cases": len(results),
        "n_passed": sum(1 for r in results if r["passed"]),
        "n_failed": sum(1 for r in results if not r["passed"]),
        "n_attacks": len(attacks),
        "n_attacks_intercepted": sum(
            1 for r in attacks
            if r["passed"] and r["output"].get("verdict") == "REJECTED"
            and float(r["output"].get("score", 1.0)) == 0.0
        ),
        "all_passed": all(r["passed"] for r in results),
        "cases": results,
    }