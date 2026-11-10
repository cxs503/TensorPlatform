"""Seeded synthetic benchmark-table corpus for the caliber reader.

All numbers are generated from one declared synthetic law so that the ground truth
of every case is known exactly:

    C_t = (1+k)*C_f(V*L/nu) + c_res*Fn^p (+ allowance(L) if the row is on the
                                            full-size flavor)

with the viscous line and the size-allowance line taken from the *injected*
configuration (never hard-coded here).  Six cases cover the decision space the
reader must separate:

===============  =========================================================
case             what the reader must conclude
===============  =========================================================
01 declared       declared caliber correct, allowance inside the numbers
02 undeclared     no caliber column at all: infer it from geometry + closure
03 allowance off  the size allowance was left out of the full-size numbers
04 extra allowance an undocumented allowance was folded into the full-size numbers
05 degraded       noisy/short table: residual above tolerance, judgment refused
06 corrupt        force/coefficient identity violated: WITHHELD, non-zero exit
===============  =========================================================

The generator is deterministic (seeded) so the corpus is reproducible byte for
byte and can be re-created from this module at any time.
"""
from __future__ import annotations

import json
import math
from typing import Dict, List, Mapping, Optional

import numpy as np

from .lines import LineSet
from .reader import DEFAULT_CONFIG, SCALE_FULL, SCALE_REDUCED, GRAVITY

GENERATOR_VERSION = "caliber-synth/1.0"

#: Synthetic physical parameters of the corpus (domain-free by construction).
PHYSICS = {
    "reduced_length": 6.0,
    "scale_factor": 30.0,
    "form_factor": 1.18,
    "residual_coefficient": 0.30,
    "residual_exponent": 4.0,
    "speed_ratio_reduced": [0.16, 0.20, 0.26],
    "speed_ratio_full": [0.20, 0.26],
    "density_reduced": 998.2,
    "viscosity_reduced": 1.004e-6,
    "density_full": 1025.0,
    "viscosity_full": 1.188e-6,
    "area_reduced": 4.0,
    "undocumented_allowance_fraction": 0.06,
    "degrade_noise": 0.02,
}


def _row(row_id: str, object_id: str, speed: float, length: float, density: float,
         viscosity: float, area: float, coefficient: float,
         declared: Optional[str]) -> Dict[str, object]:
    force = 0.5 * density * speed ** 2 * area * coefficient
    row: Dict[str, object] = {
        "row_id": row_id,
        "object_id": object_id,
        "speed_m_s": speed,
        "length_m": length,
        "density_kg_m3": density,
        "viscosity_m2_s": viscosity,
        "area_m2": area,
        "force_N": force,
        "coefficient": coefficient,
    }
    if declared is not None:
        row["scale"] = declared
    return row


def _speed(speed_ratio: float, length: float) -> float:
    return speed_ratio * math.sqrt(GRAVITY * length)


def build_cases(lines: LineSet, config: Optional[Mapping[str, object]] = None) -> Dict[str, Dict]:
    """Return ``{case_id: payload}`` for the six-case corpus."""
    cfg = dict(DEFAULT_CONFIG)
    if config:
        cfg.update(dict(config))
    viscous = str(cfg["viscous_line"])
    allowance_name = cfg.get("allowance_line")
    p = float(cfg["residual_exponent"])
    ph = dict(PHYSICS)
    k1 = ph["form_factor"]
    c1 = ph["residual_coefficient"]
    l_red = ph["reduced_length"]
    l_full = l_red * ph["scale_factor"]
    s_red = ph["area_reduced"]
    s_full = s_red * ph["scale_factor"] ** 2

    def cf(length: float, speed: float, nu: float) -> float:
        return lines.eval(viscous, speed * length / nu).value

    def allowance(length: float) -> float:
        if allowance_name and lines.has(str(allowance_name)):
            return lines.eval(str(allowance_name), length).value
        return 0.0

    def ct(length: float, speed: float, nu: float, with_allowance: bool,
           extra_fraction: float = 0.0) -> float:
        base = k1 * cf(length, speed, nu) + c1 * (speed / math.sqrt(GRAVITY * length)) ** p
        if with_allowance:
            base += allowance(length)
        if extra_fraction:
            base += extra_fraction * k1 * cf(length, speed, nu)
        return base

    rng_master = np.random.default_rng(20261110)
    cases: Dict[str, Dict] = {}

    def reduced_rows(declared: Optional[str], rng=None, noise: float = 0.0,
                     drop_viscosity_row: int = -1) -> List[Dict]:
        out = []
        for i, fn in enumerate(ph["speed_ratio_reduced"]):
            v = _speed(fn, l_red)
            value = ct(l_red, v, ph["viscosity_reduced"], False)
            if noise and rng is not None:
                value *= 1.0 + float(rng.normal(0.0, noise))
            row = _row(f"R{i + 1}", "obj_alpha", v, l_red, ph["density_reduced"],
                       ph["viscosity_reduced"], s_red, value, declared)
            if i == drop_viscosity_row:
                row.pop("viscosity_m2_s")
            out.append(row)
        return out

    def full_rows(declared: Optional[str], with_allowance: bool, rng=None,
                  noise: float = 0.0, extra_fraction: float = 0.0) -> List[Dict]:
        out = []
        for i, fn in enumerate(ph["speed_ratio_full"]):
            v = _speed(fn, l_full)
            value = ct(l_full, v, ph["viscosity_full"], with_allowance, extra_fraction)
            if noise and rng is not None:
                value *= 1.0 + float(rng.normal(0.0, noise))
            out.append(_row(f"F{i + 1}", "obj_alpha", v, l_full, ph["density_full"],
                            ph["viscosity_full"], s_full, value, declared))
        return out

    def case(case_id: str, description: str, rows: List[Dict], expected: Dict,
             seed: int) -> Dict:
        return {
            "case_id": case_id,
            "table_id": case_id,
            "object_id": "obj_alpha",
            "description": description,
            "generator": {
                "version": GENERATOR_VERSION,
                "seed": seed,
                "physics": ph,
                "lines_fingerprint": lines.source_fingerprint,
            },
            "rows": rows,
            "expected": expected,
        }

    # 01 -- declared caliber correct, allowance inside the numbers
    rows = reduced_rows(SCALE_REDUCED) + full_rows(SCALE_FULL, True)
    cases["case_01_declared_consistent"] = case(
        "case_01_declared_consistent",
        "Both flavors declared; the size allowance is inside the full-size numbers.",
        rows,
        {"status": "CONSISTENT",
         "scale_assignment": {r["row_id"]: ("reduced" if r["row_id"].startswith("R") else "full")
                              for r in rows},
         "allowance_in_quoted": True,
         "error_decision_allowed": True,
         "exit_code": 0}, seed=11)

    # 02 -- no caliber column at all
    rows = reduced_rows(None) + full_rows(None, True)
    cases["case_02_undeclared_scale"] = case(
        "case_02_undeclared_scale",
        "No caliber column; only geometry + closure can say which rows are which.",
        rows,
        {"status": "CALIBER_INFERRED",
         "scale_assignment": {r["row_id"]: ("reduced" if r["row_id"].startswith("R") else "full")
                              for r in rows},
         "allowance_in_quoted": True,
         "error_decision_allowed": True,
         "exit_code": 0}, seed=12)

    # 03 -- size allowance omitted from the full-size numbers
    rows = reduced_rows(SCALE_REDUCED) + full_rows(SCALE_FULL, False)
    cases["case_03_allowance_omitted"] = case(
        "case_03_allowance_omitted",
        "Full-size numbers quoted without the size allowance: the two flavors are not "
        "on one basis.",
        rows,
        {"status": "SUBSIDY_OMITTED",
         "scale_assignment": {r["row_id"]: ("reduced" if r["row_id"].startswith("R") else "full")
                              for r in rows},
         "allowance_in_quoted": False,
         "error_decision_allowed": False,
         "exit_code": 0}, seed=13)

    # 04 -- undocumented extra allowance in the full-size numbers
    rows = reduced_rows(SCALE_REDUCED) + full_rows(
        SCALE_FULL, True, extra_fraction=ph["undocumented_allowance_fraction"])
    cases["case_04_extra_allowance"] = case(
        "case_04_extra_allowance",
        "An undocumented allowance (6% of the viscous term) is folded into the full-size "
        "numbers.",
        rows,
        {"status": "ALLOWANCE_DETECTED",
         "scale_assignment": {r["row_id"]: ("reduced" if r["row_id"].startswith("R") else "full")
                              for r in rows},
         "allowance_fraction": ph["undocumented_allowance_fraction"],
         "error_decision_allowed": False,
         "exit_code": 0}, seed=14)

    # 05 -- degraded table: noise + one row without the viscosity column
    rng = np.random.default_rng(int(rng_master.integers(1, 10 ** 6)))
    rows = (reduced_rows(SCALE_REDUCED, rng=rng, noise=ph["degrade_noise"],
                         drop_viscosity_row=1)
            + full_rows(SCALE_FULL, True, rng=rng, noise=ph["degrade_noise"]))
    cases["case_05_degraded"] = case(
        "case_05_degraded",
        f"{int(ph['degrade_noise'] * 100)}% quoted-value noise plus one row missing its "
        "viscosity column.",
        rows,
        {"status": "DEGRADED",
         "error_decision_allowed": False,
         "exit_code": 3}, seed=15)

    # 06 -- corrupt table: force/coefficient identity violated + negative speed
    rows = reduced_rows(SCALE_REDUCED) + full_rows(SCALE_FULL, True)
    rows[0]["force_N"] = float(rows[0]["force_N"]) * 1000.0
    rows[3]["speed_m_s"] = -float(rows[3]["speed_m_s"])
    cases["case_06_corrupt"] = case(
        "case_06_corrupt",
        "Force column inconsistent with the coefficient column by 1e3 and a negative "
        "speed: the table cannot be read.",
        rows,
        {"status": "WITHHELD_CORRUPT",
         "error_decision_allowed": False,
         "exit_code": 2}, seed=16)

    return cases


def write_cases(directory, lines: LineSet,
                config: Optional[Mapping[str, object]] = None) -> List[str]:
    """Materialise the corpus as JSON files; returns the written paths."""
    import os
    os.makedirs(directory, exist_ok=True)
    paths = []
    for case_id, payload in build_cases(lines, config).items():
        path = os.path.join(directory, f"{case_id}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        paths.append(path)
    return paths