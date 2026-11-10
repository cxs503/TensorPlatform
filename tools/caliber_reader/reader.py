"""Caliber interpretation engine: machine reading of a benchmark table's basis.

Problem this module solves
--------------------------
A benchmark row is only meaningful together with its *caliber*: which scale the
numbers are stated on (a reduced-scale test article or the full-size article),
whether the customary size-dependent allowance is inside the quoted coefficient,
whether an (undocumented) appendage/roughness allowance has been folded in, and
which line/coefficient convention was used.  Benchmark tables in the wild fail to
say.  Comparing a reduced-scale coefficient against a full-size coefficient
produces percentage deviations that are one or two orders of magnitude larger
than any modelling error — and are then mis-attributed to the solver.

The engine below does not know any physics.  It is given

* a *table*: rows of ``{speed, length, density, viscosity, coefficient}`` (plus an
  optional force column and optional declared caliber),
* a *configuration*: which injected correlation line plays the viscous role, which
  plays the size-allowance role, which exponents/bands apply, tolerances.

and it returns a *verdict*: the scale assignment per row, the recovered form
factor and residual coefficient, whether the size allowance is present in the
quoted numbers, whether an undocumented allowance is present, and a status that a
downstream decision gate can consume.  Verdicts are fail-closed: ``error_decision_allowed``
is true only when the caliber is established *and* fully explained; otherwise the
caller must withhold the error judgement.

Hypothesis space actually searched (small and pre-declared, no per-case tuning):

    H = {allowance_in_quoted: True/False}

For each hypothesis the parameters ``(1+k)`` and ``c_res`` of

    C_t = (1+k)*C_f(Re) + c_res*Fn^p + [allowance(L) if scale == allowance_flavor]

are least-squares fitted on the *calibration* rows (the smallest length present =
the reduced-scale article, which is where the fitting convention puts the
unknowns), then the *prediction* rows (larger length = full size) are re-predicted
and the closure residual is scored.  The hypothesis that closes the prediction
rows wins.  A single-scale table has no prediction rows, hence no closure: it is
reported ``WITHHELD_INSUFFICIENT`` rather than guessed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from .lines import LineSet, LineError

GRAVITY = 9.80665

# ---- verdict vocabulary ---------------------------------------------------
CONSISTENT = "CONSISTENT"
CALIBER_INFERRED = "CALIBER_INFERRED"
CALIBER_CONTRADICTION = "CALIBER_CONTRADICTION"
SUBSIDY_OMITTED = "SUBSIDY_OMITTED"
ALLOWANCE_DETECTED = "ALLOWANCE_DETECTED"
DEGRADED = "DEGRADED"
WITHHELD_CORRUPT = "WITHHELD_CORRUPT"
WITHHELD_INSUFFICIENT = "WITHHELD_INSUFFICIENT"
WITHHELD_OUT_OF_BAND = "WITHHELD_OUT_OF_BAND"

#: Only a fully explained caliber permits a downstream error judgement.
ERROR_DECISION_ALLOWED = frozenset({CONSISTENT, CALIBER_INFERRED})

EXIT_CODES: Dict[str, int] = {
    CONSISTENT: 0,
    CALIBER_INFERRED: 0,
    SUBSIDY_OMITTED: 0,
    ALLOWANCE_DETECTED: 0,
    CALIBER_CONTRADICTION: 3,
    DEGRADED: 3,
    WITHHELD_CORRUPT: 2,
    WITHHELD_INSUFFICIENT: 2,
    WITHHELD_OUT_OF_BAND: 2,
}

SCALE_REDUCED = "reduced"
SCALE_FULL = "full"


# ---- configuration --------------------------------------------------------
DEFAULT_CONFIG: Dict[str, object] = {
    "viscous_line": "viscous_line",
    "allowance_line": "allowance_line",
    "allowance_flavor": SCALE_FULL,
    "residual_exponent": 4.0,
    "form_factor_band": [1.02, 1.80],
    "residual_coefficient_band": [0.0, 1.0],
    "closure_tol_pct": 2.0,
    "degrade_tol_pct": 8.0,
    "allowance_frac_band": [0.0, 0.15],
    "allowance_frac_spread_max": 0.30,
    "similarity_tol_pct": 2.0,
    "identity_tol_pct": 0.5,
    "fields": {
        "row": "row_id",
        "object": "object_id",
        "speed": "speed_m_s",
        "length": "length_m",
        "density": "density_kg_m3",
        "viscosity": "viscosity_m2_s",
        "area": "area_m2",
        "coefficient": "coefficient",
        "force": "force_N",
        "declared_scale": "scale",
    },
    "scale_aliases": {
        "reduced": SCALE_REDUCED,
        "model": SCALE_REDUCED,
        "tank": SCALE_REDUCED,
        "small": SCALE_REDUCED,
        "full": SCALE_FULL,
        "ship": SCALE_FULL,
        "full_scale": SCALE_FULL,
        "prototype": SCALE_FULL,
        "large": SCALE_FULL,
    },
}


def _cfg(config: Optional[Mapping[str, object]]) -> Dict[str, object]:
    cfg = dict(DEFAULT_CONFIG)
    if config:
        cfg.update({k: v for k, v in config.items() if k != "fields"})
        if "fields" in config:
            fields = dict(DEFAULT_CONFIG["fields"])  # type: ignore[arg-type]
            fields.update(dict(config["fields"]))  # type: ignore[arg-type]
            cfg["fields"] = fields
        if "scale_aliases" in config:
            aliases = dict(DEFAULT_CONFIG["scale_aliases"])  # type: ignore[arg-type]
            aliases.update(dict(config["scale_aliases"]))  # type: ignore[arg-type]
            cfg["scale_aliases"] = aliases
    return cfg


# ---- rows -----------------------------------------------------------------
@dataclass
class Row:
    row_id: str
    object_id: str
    speed: float
    length: float
    density: float
    viscosity: float
    coefficient: float
    area: Optional[float] = None
    force: Optional[float] = None
    declared_scale: Optional[str] = None
    issues: List[str] = field(default_factory=list)

    @property
    def flow_index(self) -> float:
        """Inertial/viscous length ratio V*L/nu (the argument of the viscous line)."""
        return self.speed * self.length / self.viscosity

    @property
    def speed_ratio(self) -> float:
        return self.speed / math.sqrt(GRAVITY * self.length)

    def as_dict(self) -> Dict[str, object]:
        return {
            "row_id": self.row_id,
            "object_id": self.object_id,
            "speed": self.speed,
            "length": self.length,
            "density": self.density,
            "viscosity": self.viscosity,
            "coefficient": self.coefficient,
            "area": self.area,
            "force": self.force,
            "declared_scale": self.declared_scale,
            "flow_index": self.flow_index,
            "speed_ratio": self.speed_ratio,
            "issues": list(self.issues),
        }


class _Blocking(Exception):
    """Raised internally when the table cannot be read at all."""

    def __init__(self, reason: str, rows: Sequence[Row] = ()):
        super().__init__(reason)
        self.reason = reason
        self.rows = list(rows)


def parse_table(table: Mapping[str, object],
                config: Optional[Mapping[str, object]] = None) -> Tuple[List[Row], List[str]]:
    """Validate and normalise the raw table.  Returns (rows, soft_issues)."""
    cfg = _cfg(config)
    fields: Dict[str, str] = cfg["fields"]  # type: ignore[assignment]
    aliases: Dict[str, str] = cfg["scale_aliases"]  # type: ignore[assignment]
    identity_tol = float(cfg["identity_tol_pct"]) / 100.0
    raw_rows = table.get("rows")
    if not isinstance(raw_rows, Sequence) or not raw_rows:
        raise _Blocking("table has no rows")
    rows: List[Row] = []
    soft: List[str] = []
    seen: Dict[Tuple[str, str], int] = {}
    for idx, raw in enumerate(raw_rows):
        if not isinstance(raw, Mapping):
            raise _Blocking(f"row #{idx} is not an object")
        rid = str(raw.get(fields["row"], f"row_{idx}"))
        oid = str(raw.get(fields["object"], table.get("object_id", "obj")))
        key = (oid, rid)
        if key in seen:
            raise _Blocking(f"duplicate row id {rid!r} in object {oid!r}")
        seen[key] = idx
        issues: List[str] = []
        numbers: Dict[str, Optional[float]] = {}
        for name in ("speed", "length", "density", "viscosity"):
            raw_val = raw.get(fields[name])
            if raw_val is None:
                numbers[name] = None
                issues.append(f"missing field {fields[name]!r}")
                continue
            try:
                val = float(raw_val)
            except (TypeError, ValueError):
                numbers[name] = None
                issues.append(f"non-numeric {fields[name]!r}={raw_val!r}")
                continue
            if not math.isfinite(val):
                numbers[name] = None
                issues.append(f"non-finite {fields[name]!r}")
            elif val <= 0.0:
                numbers[name] = None
                issues.append(f"non-positive {fields[name]!r}={val!r}")
            else:
                numbers[name] = val
        area = raw.get(fields["area"])
        area = None if area is None else float(area)
        force = raw.get(fields["force"])
        force = None if force is None else float(force)
        coeff_raw = raw.get(fields["coefficient"])
        coefficient: Optional[float] = None
        if coeff_raw is not None:
            try:
                coefficient = float(coeff_raw)
            except (TypeError, ValueError):
                issues.append(f"non-numeric {fields['coefficient']!r}={coeff_raw!r}")
            else:
                if not math.isfinite(coefficient) or coefficient <= 0.0:
                    issues.append(f"non-positive {fields['coefficient']!r}={coefficient!r}")
                    coefficient = None
        # a force column must agree with the coefficient column (identity check)
        if coefficient is not None and force is not None:
            need = (numbers["speed"], numbers["density"], area)
            if all(v is not None for v in need) and force > 0.0:
                implied = force / (0.5 * numbers["density"] * numbers["speed"] ** 2 * area)
                if abs(implied - coefficient) / coefficient > identity_tol:
                    raise _Blocking(
                        f"row {rid!r}: force/coefficient identity violated "
                        f"(implied {implied:.6g} vs quoted {coefficient:.6g})")
        if coefficient is None and force is not None and area:
            need = (numbers["speed"], numbers["density"])
            if all(v is not None for v in need) and force > 0.0:
                coefficient = force / (0.5 * numbers["density"] * numbers["speed"] ** 2 * area)
        if coefficient is None:
            issues.append("no usable coefficient/force column")
        declared = raw.get(fields["declared_scale"])
        declared_norm = None
        if declared is not None:
            key_alias = str(declared).strip().lower()
            if key_alias in aliases:
                declared_norm = aliases[key_alias]
            else:
                issues.append(f"unknown declared scale {declared!r}")
                soft.append(f"row {rid!r}: unknown declared scale {declared!r}")
        hard = [i for i in issues if i.startswith(("missing", "non-numeric", "non-positive",
                                                   "non-finite", "no usable"))]
        row = Row(row_id=rid, object_id=oid,
                  speed=numbers["speed"] or float("nan"),
                  length=numbers["length"] or float("nan"),
                  density=numbers["density"] or float("nan"),
                  viscosity=numbers["viscosity"] or float("nan"),
                  coefficient=coefficient if coefficient is not None else float("nan"),
                  area=area, force=force, declared_scale=declared_norm, issues=issues)
        if hard:
            row.issues.append("unusable")
            soft.append(f"row {rid!r} unusable: {hard}")
        rows.append(row)
    return rows, soft


# ---- fitting --------------------------------------------------------------
def _row_coefficients(rows: Sequence[Row], lines: LineSet,
                      cfg: Mapping[str, object], scale: str,
                      allowance_on: bool) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    cf, speed_ratio, notes = [], [], []
    for row in rows:
        res = lines.eval(str(cfg["viscous_line"]), row.flow_index)
        if not res.in_range:
            raise _Blocking(f"row {row.row_id!r}: viscous line out of range ({res.note})")
        cf.append(res.value)
        speed_ratio.append(row.speed_ratio)
        if not row.issues:
            continue
        notes.append(row.row_id)
    return np.asarray(cf, dtype=float), np.asarray(speed_ratio, dtype=float), notes


def _allowance_value(rows: Sequence[Row], lines: LineSet, cfg: Mapping[str, object],
                     scale: str, on: bool) -> np.ndarray:
    name = cfg.get("allowance_line")
    out = []
    for row in rows:
        if on and name and lines.has(str(name)) and scale == cfg["allowance_flavor"]:
            res = lines.eval(str(name), row.length)
            if not res.in_range:
                raise _Blocking(f"row {row.row_id!r}: allowance line out of range ({res.note})")
            out.append(res.value)
        else:
            out.append(0.0)
    return np.asarray(out, dtype=float)


def _fit(cal_rows: Sequence[Row], lines: LineSet, cfg: Mapping[str, object],
         scale: str, allowance_on: bool) -> Tuple[np.ndarray, int]:
    cf, fn, _ = _row_coefficients(cal_rows, lines, cfg, scale, allowance_on)
    allow = _allowance_value(cal_rows, lines, cfg, scale, allowance_on)
    target = np.asarray([r.coefficient for r in cal_rows], dtype=float) - allow
    design = np.column_stack([cf, fn ** float(cfg["residual_exponent"])])
    if len(cal_rows) < 2:
        raise _Blocking("calibration set has fewer than 2 usable rows: "
                        "(1+k) and the residual coefficient are not separable")
    beta, *_ = np.linalg.lstsq(design, target, rcond=None)
    return beta, len(cal_rows)


def _predict(rows: Sequence[Row], beta: np.ndarray, lines: LineSet,
             cfg: Mapping[str, object], scale: str, allowance_on: bool) -> Dict[str, object]:
    cf, fn, _ = _row_coefficients(rows, lines, cfg, scale, allowance_on)
    allow = _allowance_value(rows, lines, cfg, scale, allowance_on)
    pred = beta[0] * cf + beta[1] * fn ** float(cfg["residual_exponent"]) + allow
    measured = np.asarray([r.coefficient for r in rows], dtype=float)
    resid_pct = (pred - measured) / measured * 100.0
    return {
        "rows": [r.row_id for r in rows],
        "predicted": pred.tolist(),
        "measured": measured.tolist(),
        "residual_pct": resid_pct.tolist(),
        "max_abs_pct": float(np.max(np.abs(resid_pct))) if resid_pct.size else 0.0,
        "mean_pct": float(np.mean(resid_pct)) if resid_pct.size else 0.0,
        "viscous_terms": (beta[0] * cf).tolist(),
    }


def _similarity_check(rows: Sequence[Row], cfg: Mapping[str, object]) -> Dict[str, object]:
    tol = float(cfg["similarity_tol_pct"]) / 100.0
    pairs = []
    reduced = [r for r in rows if r.length == min(x.length for x in rows)]
    others = [r for r in rows if r.length != min(x.length for x in rows)]
    for a in reduced:
        for b in others:
            rel = abs(a.speed_ratio - b.speed_ratio) / max(a.speed_ratio, b.speed_ratio)
            pairs.append({"reduced": a.row_id, "full": b.row_id,
                          "speed_ratio_reduced": a.speed_ratio, "speed_ratio_full": b.speed_ratio,
                          "rel_diff_pct": rel * 100.0, "similar": rel <= tol})
    matches = [any(p["similar"] for p in pairs if p["full"] == r.row_id) for r in others]
    ok = bool(pairs) and all(matches)
    return {"pairs": pairs, "ok": ok, "tol_pct": tol * 100.0}


# ---- main entry -----------------------------------------------------------
def interpret(table: Mapping[str, object], lines: LineSet,
              config: Optional[Mapping[str, object]] = None) -> Dict[str, object]:
    """Read one benchmark table and return a fail-closed caliber verdict."""
    cfg = _cfg(config)
    tol = float(cfg["closure_tol_pct"])
    degrade_tol = float(cfg["degrade_tol_pct"])
    ff_lo, ff_hi = (float(x) for x in cfg["form_factor_band"])  # type: ignore[misc]
    rc_lo, rc_hi = (float(x) for x in cfg["residual_coefficient_band"])  # type: ignore[misc]
    base: Dict[str, object] = {
        "table_id": table.get("table_id"),
        "config_fingerprint": lines.source_fingerprint,
        "hypotheses": [],
    }
    try:
        rows, soft_issues = parse_table(table, cfg)
        usable = [r for r in rows if "unusable" not in r.issues]
        if not usable:
            raise _Blocking("no usable rows after validation")
        group_ids = sorted({r.object_id for r in usable})
        groups: Dict[str, Dict[str, object]] = {}
        worst_status = CONSISTENT
        for gid in group_ids:
            grows = [r for r in usable if r.object_id == gid]
            lengths = sorted({round(r.length, 9) for r in grows})
            if len(lengths) < 2:
                groups[gid] = {"status": WITHHELD_INSUFFICIENT,
                               "reason": "single length only: no reduced/full pair, "
                                         "caliber not identifiable from closure",
                               "rows": [r.row_id for r in grows]}
                worst_status = WITHHELD_INSUFFICIENT
                continue
            cal_len = lengths[0]
            cal_rows = [r for r in grows if round(r.length, 9) == cal_len]
            pred_rows = [r for r in grows if round(r.length, 9) != cal_len]
            sim = _similarity_check(grows, cfg)
            best = None
            for allowance_on in (True, False):
                try:
                    beta, n_cal = _fit(cal_rows, lines, cfg, SCALE_REDUCED, allowance_on)
                except _Blocking as exc:
                    groups[gid] = {"status": WITHHELD_INSUFFICIENT, "reason": exc.reason,
                                   "rows": [r.row_id for r in grows]}
                    worst_status = WITHHELD_INSUFFICIENT
                    best = None
                    break
                proj = _predict(pred_rows, beta, lines, cfg, SCALE_FULL, allowance_on)
                hyp = {"allowance_in_quoted": allowance_on,
                       "form_factor": float(beta[0]),
                       "residual_coefficient": float(beta[1]),
                       "calibration_rows": n_cal,
                       "prediction": proj,
                       "score_pct": proj["max_abs_pct"]}
                base["hypotheses"].append({"object_id": gid, **hyp})
                if best is None or hyp["score_pct"] < best["score_pct"] - 1e-12:
                    best = hyp
            if best is None or gid in groups:
                continue
            k1 = best["form_factor"]
            c_res = best["residual_coefficient"]
            score = best["score_pct"]
            band_ok = (ff_lo <= k1 <= ff_hi) and (rc_lo <= c_res <= rc_hi)
            assignment = {r.row_id: (SCALE_REDUCED if round(r.length, 9) == cal_len
                                     else SCALE_FULL) for r in grows}
            declarations = {r.row_id: r.declared_scale for r in grows}
            contradiction = [rid for rid, dec in declarations.items()
                             if dec is not None and dec != assignment[rid]]
            undeclared = all(dec is None for dec in declarations.values())
            allowance_name = cfg.get("allowance_line")
            allowance_configured = bool(allowance_name) and lines.has(str(allowance_name))
            resid = best["prediction"]["residual_pct"]
            # excess of the *quoted* value over the prediction, expressed as a fraction
            # of the viscous term of the same row (that is what an undocumented
            # allowance is proportional to).  Sign: quoted > predicted -> excess > 0.
            implied_fracs = []
            for r_val, visc, meas in zip(resid, best["prediction"]["viscous_terms"],
                                        best["prediction"]["measured"]):
                if visc > 0:
                    implied_fracs.append((-r_val / 100.0) * meas / visc)
            total_allowance_value = _allowance_value(
                pred_rows, lines, cfg, SCALE_FULL, True)
            if not band_ok:
                status = WITHHELD_OUT_OF_BAND
                reason = (f"recovered (1+k)={k1:.4f} / c_res={c_res:.6f} outside the "
                          f"declared bands")
            elif score <= tol:
                if contradiction:
                    status = CALIBER_CONTRADICTION
                    reason = ("declared caliber contradicts the closure-identified one for "
                              f"{contradiction}")
                elif (not best["allowance_in_quoted"]) and allowance_configured and \
                        bool(np.any(total_allowance_value > 0.0)):
                    status = SUBSIDY_OMITTED
                    reason = ("closure only holds when the size allowance is removed from "
                              "the quoted full-scale numbers: the table's allowance is omitted")
                elif undeclared:
                    status = CALIBER_INFERRED
                    reason = ("no caliber declared; reduced/full assignment established from "
                              "geometry + closure")
                else:
                    status = CONSISTENT
                    reason = "declared caliber agrees with the closure-identified caliber"
            else:
                positive = bool(np.all(np.asarray(implied_fracs) > 0.0)) and len(resid) > 0
                frac_ok = positive and all(
                    float(cfg["allowance_frac_band"][0]) <= f <= float(cfg["allowance_frac_band"][1])
                    for f in implied_fracs)
                spread_ok = False
                if positive and implied_fracs:
                    mean_f = float(np.mean(implied_fracs))
                    spread_ok = (mean_f > 0.0 and
                                 (max(implied_fracs) - min(implied_fracs)) / mean_f
                                 <= float(cfg["allowance_frac_spread_max"]))
                if allowance_configured and frac_ok and spread_ok and score > tol:
                    status = ALLOWANCE_DETECTED
                    reason = ("quoted full-scale numbers carry an undocumented allowance of "
                              f"{float(np.mean(implied_fracs)) * 100:.2f}% of the viscous term")
                elif score <= degrade_tol and (soft_issues or any(r.issues for r in grows)):
                    status = DEGRADED
                    reason = (f"closure residual {score:.2f}% exceeds the {tol:.2f}% tolerance "
                              "and the table carries data-quality issues: judgement refused")
                elif score <= degrade_tol:
                    status = DEGRADED
                    reason = (f"closure residual {score:.2f}% above the {tol:.2f}% tolerance "
                              "(within the degraded band): judgement refused")
                else:
                    status = WITHHELD_OUT_OF_BAND
                    reason = f"no hypothesis closes: best residual {score:.2f}%"
            allowance_frac = (float(np.mean(implied_fracs)) if implied_fracs else 0.0)
            groups[gid] = {
                "status": status,
                "reason": reason,
                "rows": [r.row_id for r in grows],
                "calibration_rows": [r.row_id for r in cal_rows],
                "prediction_rows": [r.row_id for r in pred_rows],
                "scale_assignment": assignment,
                "declared_scale": declarations,
                "form_factor": k1,
                "residual_coefficient": c_res,
                "allowance_in_quoted": best["allowance_in_quoted"],
                "allowance_fraction": allowance_frac,
                "closure_residual_pct": score,
                "closure_residual_all_pct": resid,
                "form_factor_in_band": ff_lo <= k1 <= ff_hi,
                "residual_coefficient_in_band": rc_lo <= c_res <= rc_hi,
                "similarity": sim,
            }
            if status != CONSISTENT:
                worst_status = status
        base.update({
            "status": worst_status,
            "groups": groups,
            "soft_issues": soft_issues,
            "rows": [r.as_dict() for r in rows],
            "error_decision_allowed": worst_status in ERROR_DECISION_ALLOWED,
            "exit_code": EXIT_CODES.get(worst_status, 2),
            "lines": lines.as_dict(),
            "config": {k: v for k, v in cfg.items() if k != "scale_aliases"},
        })
        if len(groups) == 1:
            only = next(iter(groups.values()))
            for key in ("scale_assignment", "form_factor", "residual_coefficient",
                        "allowance_in_quoted", "allowance_fraction",
                        "closure_residual_pct", "similarity", "reason"):
                base[key] = only.get(key)
        return base
    except _Blocking as exc:
        base.update({
            "status": WITHHELD_CORRUPT,
            "reason": exc.reason,
            "error_decision_allowed": False,
            "exit_code": EXIT_CODES[WITHHELD_CORRUPT],
            "corrupt_rows": [r.as_dict() for r in exc.rows],
            "lines": lines.as_dict(),
        })
        return base
    except LineError as exc:  # configuration/line problem: fail-closed, never guess
        base.update({
            "status": WITHHELD_INSUFFICIENT,
            "reason": f"correlation line unusable: {exc}",
            "error_decision_allowed": False,
            "exit_code": EXIT_CODES[WITHHELD_INSUFFICIENT],
        })
        return base


# ---- pair inversion (two rows, two unknowns) ------------------------------
def invert_pair(reduced: Row, full: Row, lines: LineSet,
                config: Optional[Mapping[str, object]] = None,
                allowance_on: bool = True) -> Dict[str, object]:
    """Recover ``(1+k)`` and the residual coefficient from one reduced/full pair.

    Two rows carry two equations for two unknowns, so the inversion is exact; the
    returned residual therefore measures *arithmetic closure*, not physical
    agreement (see docs: the independent anchors are the analytic viscous line and
    the quoted full-size coefficient).
    """
    cfg = _cfg(config)
    rows = [reduced, full]
    cf, fn, _ = _row_coefficients(rows, lines, cfg, SCALE_REDUCED, allowance_on)
    allow = _allowance_value([full], lines, cfg, SCALE_FULL, allowance_on)
    s_full = float(allow[0])
    num = (reduced.coefficient) - (full.coefficient - s_full)
    den = cf[0] - cf[1]
    if abs(den) < 1e-12:
        raise _Blocking("viscous terms of the pair are indistinguishable: inversion unstable")
    k1 = num / den
    p = float(cfg["residual_exponent"])
    residual_term = reduced.coefficient - k1 * cf[0]
    c_res = residual_term / (fn[0] ** p)
    pred_reduced = k1 * cf[0] + residual_term
    pred_full = k1 * cf[1] + c_res * fn[1] ** p + s_full
    return {
        "form_factor": float(k1),
        "residual_term": float(residual_term),
        "residual_coefficient": float(c_res),
        "speed_ratio_exponent": p,
        "allowance_value_full": s_full,
        "viscous_reduced": float(cf[0]),
        "viscous_full": float(cf[1]),
        "flow_index_reduced": reduced.flow_index,
        "flow_index_full": full.flow_index,
        "speed_ratio_reduced": reduced.speed_ratio,
        "speed_ratio_full": full.speed_ratio,
        "closure_pct": {
            "reduced": (pred_reduced - reduced.coefficient) / reduced.coefficient * 100.0,
            "full": (pred_full - full.coefficient) / full.coefficient * 100.0,
        },
    }


# ---- deviation attribution ------------------------------------------------
def attribute_deviation(observed: float, reference: float,
                        factors: Sequence[Tuple[str, float]]) -> Dict[str, object]:
    """Sequentially attribute the gap between two numbers to named multiplicative factors.

    ``factors`` is an ordered list of ``(name, ratio)``; each ratio is applied to the
    running value in order.  The returned per-factor contribution is the percentage
    change of *that step* relative to the reference value, so the contributions add
    up (to first order) to the observed deviation.  A final ``residual_pct`` of
    near zero proves the factor set explains the gap; a large residual proves it does
    not and the gap must be attributed elsewhere (a genuine disagreement).
    """
    if reference == 0.0:
        raise ValueError("reference value must be non-zero")
    total = (observed - reference) / reference * 100.0
    running = observed
    steps = []
    for name, ratio in factors:
        before = running
        running = running * ratio
        steps.append({
            "factor": name,
            "ratio": float(ratio),
            "delta_pct_of_reference": (running - before) / reference * 100.0,
            "value_after": float(running),
        })
    residual = (running - reference) / abs(reference) * 100.0
    dominant = max(steps, key=lambda s: abs(s["delta_pct_of_reference"])) if steps else None
    return {
        "observed": float(observed),
        "reference": float(reference),
        "observed_deviation_pct": total,
        "steps": steps,
        "explained_pct": total - residual,
        "residual_pct": residual,
        "dominant_factor": dominant["factor"] if dominant else None,
        "confidence": "HIGH" if abs(residual) <= 2.0 else
                      ("MEDIUM" if abs(residual) <= 15.0 else "LOW"),
        "attribution": "CALIBER" if abs(residual) <= 15.0 else "UNEXPLAINED",
    }