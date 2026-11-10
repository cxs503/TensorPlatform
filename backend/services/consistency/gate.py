"""Consistency Gate Protocol (CGP): turn N solver arms into one adjudicated report.

Protocol
--------
INPUT   ``(config, arms)`` where

        * ``config`` -- JSON injected at run time (see ``config/consistency/``):
          which scalar quantities are compared, their optional reference value
          and reference tolerance, which surface/profile curves are compared,
          which two energy-ledger terms must close, and the verdict bands.
        * ``arms`` -- one record per solver run (``solver_family``,
          ``solver_version``, ``quantities``, ``profiles``, ``energy.terms``,
          ``declarations``, ``provenance{input_sha256, git_commit, ...}``).

OUTPUT  one JSON report: provenance block, solver x quantity difference matrix
        per quantity, pairwise table with mean / std / max deviation, verdict
        per dimension, an overall verdict taken as the *worst* dimension, and the
        investigation list that a deviation beyond the acceptance band triggers.

Why the verdict is taken as a maximum and not as an average
----------------------------------------------------------
Averaging hides a single rogue arm: two arms agreeing to 1 % and one disagreeing
to 40 % would average to a comfortable 14 %.  The gate therefore judges on
``max_dev`` and reports the mean/std only as description.

Nothing physical is hard-coded: the same code adjudicates a bluff-body drag
coefficient, a wall-pressure curve, a heat-transfer curve or an arbitrary
synthetic quantity set -- the caller only supplies config + arms.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .metrics import (canonical_json, pair_stats, pairwise, profile_l2_rel,
                      rel_diff, sha256_of, verdict_of)

SCHEMA_VERSION = "1.0.0"
REPORT_TYPE = "cross_solver_consistency"
CAPABILITY_KEY = "platform.cross_solver_consistency"

# severity ordering used for the aggregate verdict (higher = worse)
_SEVERITY = {
    "consistent": 0,
    "acceptable": 1,
    "acceptable_undeclared": 2,
    "inconsistent": 3,
    "withheld": 4,
}

_REQUIRED_ARM_FIELDS = ("arm_id", "solver_family", "case_id", "quantities")


def _utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def worst(verdicts: Sequence[str]) -> str:
    """Aggregate verdict = highest severity present (withheld outranks all)."""
    if not verdicts:
        return "withheld"
    return max(verdicts, key=lambda v: _SEVERITY.get(v, 4))


class ConfigError(ValueError):
    """Raised when the injected configuration cannot drive the gate."""


class ArmRecordError(ValueError):
    """Raised when an arm record is structurally unusable."""


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------
def load_config(path: Any) -> Dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"consistency config not found: {p}")
    cfg = json.loads(p.read_text(encoding="utf-8"))
    validate_config(cfg)
    cfg["_config_path"] = str(p)
    cfg["_config_sha256"] = sha256_of({k: v for k, v in cfg.items()
                                       if not k.startswith("_")})
    return cfg


def validate_config(cfg: Dict[str, Any]) -> None:
    if not isinstance(cfg, dict):
        raise ConfigError("config must be a JSON object")
    for key in ("schema_version", "case_id", "quantities", "verdict"):
        if key not in cfg:
            raise ConfigError(f"config missing required key '{key}'")
    if not isinstance(cfg["quantities"], list) or not cfg["quantities"]:
        raise ConfigError("config.quantities must be a non-empty list")
    names = set()
    for q in cfg["quantities"]:
        if not isinstance(q, dict) or "name" not in q:
            raise ConfigError("each quantity needs a 'name'")
        if q["name"] in names:
            raise ConfigError(f"duplicate quantity name '{q['name']}'")
        names.add(q["name"])
        if "reference" in q and not isinstance(q["reference"], (int, float)):
            raise ConfigError(f"quantity '{q['name']}' reference must be numeric")
    rule = cfg["verdict"]
    for key in ("consistent", "acceptable"):
        if key not in rule:
            raise ConfigError(f"config.verdict missing '{key}'")
        if not (0 < float(rule[key]) <= 10):
            raise ConfigError(f"config.verdict.{key} out of range")
    if float(rule["consistent"]) >= float(rule["acceptable"]):
        raise ConfigError("verdict.consistent must be < verdict.acceptable")
    for prof in cfg.get("profiles", []) or []:
        if not isinstance(prof, dict) or "name" not in prof:
            raise ConfigError("each profile needs a 'name'")
    energy = cfg.get("energy")
    if energy is not None:
        for key in ("input_term", "dissipation_term"):
            if key not in energy:
                raise ConfigError(f"config.energy missing '{key}'")
    arms = cfg.get("arms") or {}
    if arms.get("required") and arms.get("minimum"):
        if int(arms["minimum"]) < 2:
            raise ConfigError("arms.minimum must be >= 2 for a comparison")


# ---------------------------------------------------------------------------
# arm records
# ---------------------------------------------------------------------------
def validate_arm(arm: Dict[str, Any], case_id: str,
                 required_arms: Sequence[str] = ()) -> None:
    if not isinstance(arm, dict):
        raise ArmRecordError("arm must be a JSON object")
    for key in _REQUIRED_ARM_FIELDS:
        if key not in arm:
            raise ArmRecordError(f"arm missing required field '{key}'")
    if not isinstance(arm["quantities"], dict):
        raise ArmRecordError("arm.quantities must be a mapping name -> number")
    if required_arms and arm["solver_family"] not in required_arms:
        raise ArmRecordError(
            f"solver_family '{arm['solver_family']}' not in configured arms "
            f"{list(required_arms)}")


def _traceability(arm: Dict[str, Any]) -> Dict[str, Any]:
    prov = arm.get("provenance") or {}
    missing = [k for k in ("input_sha256", "git_commit") if not prov.get(k)]
    return {"arm_id": arm.get("arm_id"), "missing": missing,
            "traceable": not missing,
            "input_sha256": prov.get("input_sha256"),
            "config_sha256": prov.get("config_sha256"),
            "git_commit": prov.get("git_commit"),
            "generated_utc": prov.get("generated_utc")}


class ConsistencyGate:
    """Mechanical implementation of the Consistency Gate Protocol."""

    def __init__(self, config: Dict[str, Any]):
        validate_config(config)
        self.cfg = config

    # -- construction helpers -------------------------------------------------
    @classmethod
    def from_path(cls, path: Any) -> "ConsistencyGate":
        return cls(load_config(path))

    @property
    def rule(self) -> Dict[str, float]:
        rule = self.cfg["verdict"]
        return {"consistent": float(rule["consistent"]),
                "acceptable": float(rule["acceptable"])}

    # -- arm admission --------------------------------------------------------
    def admit(self, arms: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]],
                                                             List[Dict[str, Any]]]:
        """Split arms into admitted / rejected, never silently dropping one."""
        cfg = self.cfg
        case_id = cfg["case_id"]
        required = (cfg.get("arms") or {}).get("required") or ()
        admitted: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        seen = set()
        for arm in arms:
            try:
                validate_arm(arm, case_id, required)
            except ArmRecordError as exc:
                rejected.append({"arm_id": (arm or {}).get("arm_id"),
                                 "reason": "invalid_record", "detail": str(exc)})
                continue
            if arm["arm_id"] in seen:
                rejected.append({"arm_id": arm["arm_id"], "reason": "duplicate_arm_id",
                                 "detail": "an arm id must be unique in one report"})
                continue
            seen.add(arm["arm_id"])
            if str(arm["case_id"]) != str(case_id):
                rejected.append({"arm_id": arm["arm_id"], "reason": "case_mismatch",
                                 "detail": f"arm case '{arm['case_id']}' != "
                                           f"config case '{case_id}'"})
                continue
            if arm.get("excluded"):
                rejected.append({"arm_id": arm["arm_id"], "reason": "self_excluded",
                                 "detail": str(arm.get("excluded_reason") or "")})
                continue
            admitted.append(arm)
        return admitted, rejected

    # -- dimensions -----------------------------------------------------------
    def _scalar_dimension(self, qcfg: Dict[str, Any],
                          arms: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        name = qcfg["name"]
        items = [(a["arm_id"], (a["quantities"] or {}).get(name)) for a in arms]
        present = [(aid, v) for aid, v in items if v is not None]
        pw = pairwise(present)
        missing = [aid for aid, v in items if v is None]
        declarations = {a["arm_id"]: list(a.get("declarations") or [])
                        for a in arms if a["arm_id"] in [p[0] for p in present]}
        declared = all(bool(d) for d in declarations.values()) if declarations else False
        verdict = verdict_of(pw["max_dev"], self.rule, declared=declared)
        if len(present) < 2:
            verdict = "withheld"
        out: Dict[str, Any] = {
            "dimension": "scalar",
            "name": name,
            "values": {aid: v for aid, v in items},
            "missing_arms": missing,
            "declarations": declarations,
            "declared": declared,
            "pairs": pw["pairs"],
            "matrix": pw["matrix"],
            "n_pairs": pw["n_pairs"],
            "mean_dev": pw["mean_dev"],
            "std_dev": pw["std_dev"],
            "max_dev": pw["max_dev"],
            "verdict": verdict,
        }
        if "reference" in qcfg:
            ref = float(qcfg["reference"])
            tol = float(qcfg.get("reference_tolerance", self.rule["acceptable"]))
            vs_ref = {}
            for aid, v in present:
                err = rel_diff(v, ref)
                vs_ref[aid] = {
                    "value": v,
                    "rel_error": err,
                    "within_tolerance": (err is not None and err <= tol),
                }
            worst_err = max([e["rel_error"] for e in vs_ref.values()
                             if e["rel_error"] is not None], default=None)
            out["reference"] = {
                "value": ref,
                "source": qcfg.get("reference_source"),
                "tolerance": tol,
                "per_arm": vs_ref,
                "max_rel_error": worst_err,
                "verdict": ("consistent" if worst_err is not None and worst_err < self.rule["consistent"]
                            else "acceptable" if worst_err is not None and worst_err <= tol
                            else "inconsistent" if worst_err is not None else "withheld"),
            }
        return out

    def _profile_dimension(self, pcfg: Dict[str, Any],
                           arms: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        name = pcfg["name"]
        curves = {}
        for a in arms:
            profs = a.get("profiles") or {}
            if name in profs:
                curves[a["arm_id"]] = profs[name]
        ids = sorted(curves)
        pairs: List[Dict[str, Any]] = []
        matrix: Dict[str, Dict[str, Optional[float]]] = {i: {} for i in ids}
        for i, ai in enumerate(ids):
            matrix[ai][ai] = 0.0
            for bj in ids[i + 1:]:
                try:
                    res = profile_l2_rel(curves[ai], curves[bj],
                                         n_grid=int(pcfg.get("n_grid", 401)))
                    d = res["l2_rel"]
                    detail = {"n_grid": res["n_grid"], "overlap": res["overlap"]}
                except (ValueError, TypeError) as exc:
                    d = None
                    detail = {"error": str(exc)}
                pairs.append({"a": ai, "b": bj, "l2_rel": d, **detail})
                matrix[ai][bj] = d
                matrix[bj][ai] = d
        stats = pair_stats([p["l2_rel"] for p in pairs])
        declarations = {a["arm_id"]: list(a.get("declarations") or [])
                        for a in arms if a["arm_id"] in ids}
        declared = all(bool(d) for d in declarations.values()) if declarations else False
        verdict = verdict_of(stats["max_dev"], self.rule, declared=declared)
        if len(ids) < 2:
            verdict = "withheld"
        return {
            "dimension": "profile",
            "name": name,
            "coordinate": pcfg.get("coordinate"),
            "n_arms": len(ids),
            "declarations": declarations,
            "declared": declared,
            "pairs": pairs,
            "matrix": matrix,
            "n_pairs": stats["n_pairs"],
            "mean_dev": stats["mean_dev"],
            "std_dev": stats["std_dev"],
            "max_dev": stats["max_dev"],
            "verdict": verdict,
        }

    def _energy_dimension(self, arms: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        ecfg = self.cfg.get("energy") or {}
        in_t = ecfg.get("input_term")
        ds_t = ecfg.get("dissipation_term")
        tol = float(ecfg.get("closure_tolerance", self.rule["acceptable"]))
        per_arm: Dict[str, Any] = {}
        residuals: List[Tuple[str, Optional[float]]] = []
        for a in arms:
            terms = ((a.get("energy") or {}).get("terms") or {})
            pin = terms.get(in_t)
            diss = terms.get(ds_t)
            residual = None
            if pin is not None and diss is not None:
                denom = abs(float(pin))
                residual = (float(pin) - float(diss)) / denom if denom > 1e-12 else None
            per_arm[a["arm_id"]] = {
                "terms": {in_t: pin, ds_t: diss},
                "closure_residual": residual,
                "closure_abs": None if residual is None else abs(residual),
                "closure_ok": (residual is not None and abs(residual) <= tol),
                "declarations": list(a.get("declarations") or []),
            }
            residuals.append((a["arm_id"], residual))
        present = [(aid, r) for aid, r in residuals if r is not None]
        pw = pairwise(present)
        declared = all(bool(per_arm[aid]["declarations"]) for aid, _ in present) \
            if present else False
        agree = verdict_of(pw["max_dev"], self.rule, declared=declared)
        if len(present) < 2:
            agree = "withheld"
        closure_fail = [aid for aid, rec in per_arm.items() if rec["closure_abs"] is not None
                        and not rec["closure_ok"]]
        closure_verdict = "inconsistent" if closure_fail else (
            "withheld" if not per_arm else "consistent")
        return {
            "dimension": "energy",
            "name": "energy_ledger",
            "input_term": in_t,
            "dissipation_term": ds_t,
            "closure_tolerance": tol,
            "per_arm": per_arm,
            "closure_fail_arms": closure_fail,
            "closure_verdict": closure_verdict,
            "pairs": pw["pairs"],
            "matrix": pw["matrix"],
            "n_pairs": pw["n_pairs"],
            "mean_dev": pw["mean_dev"],
            "std_dev": pw["std_dev"],
            "max_dev": pw["max_dev"],
            "declared": declared,
            "agreement_verdict": agree,
            "verdict": worst([closure_verdict, agree]),
        }

    # -- protocol -------------------------------------------------------------
    def run(self, arms: Sequence[Dict[str, Any]],
            run_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        admitted, rejected = self.admit(arms)
        cfg = self.cfg
        min_arms = int((cfg.get("arms") or {}).get("minimum", 2))
        dimensions: List[Dict[str, Any]] = []
        if len(admitted) >= 1:
            for qcfg in cfg["quantities"]:
                dimensions.append(self._scalar_dimension(qcfg, admitted))
            for pcfg in cfg.get("profiles") or []:
                dimensions.append(self._profile_dimension(pcfg, admitted))
            if cfg.get("energy"):
                dimensions.append(self._energy_dimension(admitted))

        required_fams = list((cfg.get("arms") or {}).get("required") or [])
        present_fams = {a["solver_family"] for a in admitted}
        missing_fams = [f for f in required_fams if f not in present_fams]
        if len(admitted) < min_arms or missing_fams:
            overall = "withheld"
        else:
            overall = worst([d["verdict"] for d in dimensions]) if dimensions else "withheld"

        investigations = []
        for d in dimensions:
            if d["verdict"] in ("inconsistent", "acceptable_undeclared"):
                worst_pair = None
                if d["pairs"]:
                    key = "rel_diff" if d["dimension"] == "scalar" else (
                        "l2_rel" if d["dimension"] == "profile" else "rel_diff")
                    cand = [p for p in d["pairs"] if p.get(key) is not None]
                    if cand:
                        worst_pair = max(cand, key=lambda p: p[key])
                investigations.append({
                    "dimension": d["dimension"],
                    "subject": d["name"] if "name" in d else "energy_ledger",
                    "verdict": d["verdict"],
                    "max_dev": d["max_dev"],
                    "worst_pair": worst_pair,
                    "action": ("raise resolution / re-check boundary treatment "
                               "before the arms are quoted as agreeing")
                    if d["verdict"] == "inconsistent" else
                    ("arms must declare the origin of the deviation in the report"),
                })
            if d["dimension"] == "scalar" and d.get("reference", {}).get("verdict") == "inconsistent":
                investigations.append({
                    "dimension": "reference",
                    "subject": d["name"],
                    "verdict": "inconsistent",
                    "max_dev": d["reference"]["max_rel_error"],
                    "worst_pair": None,
                    "action": "at least one arm misses the injected reference value "
                              "beyond its tolerance",
                })
            if d["dimension"] == "energy" and d.get("closure_fail_arms"):
                investigations.append({
                    "dimension": "energy_closure",
                    "subject": "energy_ledger",
                    "verdict": "inconsistent",
                    "max_dev": max(abs(d["per_arm"][a]["closure_residual"])
                                   for a in d["closure_fail_arms"]),
                    "worst_pair": None,
                    "action": "energy ledger does not close for "
                              + ", ".join(d["closure_fail_arms"]),
                })

        report: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "report_type": REPORT_TYPE,
            "capability": {"key": CAPABILITY_KEY, "level": "REAL",
                           "validated_by": "this report + its pytest suite"},
            "case_id": cfg["case_id"],
            "title": cfg.get("title"),
            "generated_utc": _utc(),
            "config": {
                "path": cfg.get("_config_path"),
                "sha256": cfg.get("_config_sha256"),
                "verdict_rule": self.rule,
                "quantities": [q["name"] for q in cfg["quantities"]],
                "profiles": [p["name"] for p in (cfg.get("profiles") or [])],
                "energy_terms": (None if not cfg.get("energy") else
                                 [cfg["energy"]["input_term"],
                                  cfg["energy"]["dissipation_term"]]),
                "min_arms": min_arms,
            },
            "arms": [_traceability(a) | {"solver_family": a["solver_family"],
                                         "solver_version": a.get("solver_version"),
                                         "declarations": list(a.get("declarations") or []),
                                         "case_id": a["case_id"]}
                     for a in admitted],
            "rejected_arms": rejected,
            "missing_solver_families": missing_fams,
            "n_arms": len(admitted),
            "dimensions": dimensions,
            "matrix": {d.get("name", "energy_ledger" if d["dimension"] == "energy" else d["dimension"]):
                       d["matrix"] for d in dimensions},
            "deviation_summary": {
                d.get("name", "energy_ledger" if d["dimension"] == "energy" else d["dimension"]): {
                    "dimension": d["dimension"],
                    "mean_dev": d["mean_dev"],
                    "std_dev": d["std_dev"],
                    "max_dev": d["max_dev"],
                    "n_pairs": d["n_pairs"],
                    "verdict": d["verdict"],
                } for d in dimensions
            },
            "verdict": overall,
            "investigations": investigations,
            "run_meta": run_meta or {},
        }
        report["sha256"] = sha256_of(report)
        return report


# ---------------------------------------------------------------------------
# report IO / verification
# ---------------------------------------------------------------------------
def write_report(report: Dict[str, Any], path: Any) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(report)
    payload.pop("sha256", None)
    payload["sha256"] = sha256_of(payload)
    p.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                 encoding="utf-8")
    return payload["sha256"]


def verify_report(path: Any) -> Dict[str, Any]:
    """Recompute the report digest; a mismatch is a tamper, not a rounding note."""
    p = Path(path)
    report = json.loads(p.read_text(encoding="utf-8"))
    claimed = report.get("sha256")
    payload = {k: v for k, v in report.items() if k != "sha256"}
    actual = sha256_of(payload)
    return {"path": str(p), "claimed": claimed, "actual": actual,
            "ok": claimed == actual}


def load_arms(paths: Sequence[Any]) -> List[Dict[str, Any]]:
    """Load arm records from JSON files (one arm per file)."""
    arms = []
    for path in paths:
        arms.append(json.loads(Path(path).read_text(encoding="utf-8")))
    return arms