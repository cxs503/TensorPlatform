"""Generate synthetic verified cases for cross-solver verification framework demo.

Produces:
- 74+ synthetic verified cases covering multiple disciplines
- Wigley III multi-solver outputs for arbitration demo
- LBM canonical cases (cavity, cylinder, poiseuille) with conservation residuals
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "artifacts" / "m6834"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _digest(data: dict) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# LBM canonical cases (cavity, cylinder, poiseuille)
# ---------------------------------------------------------------------------
LBM_CASES = [
    {
        "input": {
            "schema_version": "1.0.0",
            "case_id": "lbm_cavity_re1000",
            "geometry": {"type": "cavity", "dimensions": {"domain_size": [1.0, 1.0]}},
            "physics": {"Re": 1000, "boundary_conditions": {"inlet": "lid_driven", "walls": "no_slip"}},
            "solver": {"name": "TensorLBM", "version": "1.0.0", "git_commit": "a1b2c3d"},
        },
        "output": {
            "schema_version": "1.0.0",
            "case_id": "lbm_cavity_re1000",
            "solver_signature": {"name": "TensorLBM", "version": "1.0.0", "git_commit": "a1b2c3d"},
            "primary_quantities": [
                {"name": "C_D", "value": 1.0, "unit": "dimensionless"},
                {"name": "u_max", "value": 0.385, "unit": "lu/ts"},
            ],
            "conservation_residuals": [
                {"law": "mass", "residual": 1e-8, "tolerance": 1e-6},
                {"law": "momentum_x", "residual": 5e-5, "tolerance": 1e-4},
                {"law": "momentum_y", "residual": 3e-5, "tolerance": 1e-4},
            ],
            "validation_status": "pass",
        },
    },
    {
        "input": {
            "schema_version": "1.0.0",
            "case_id": "lbm_cylinder_re100",
            "geometry": {"type": "cylinder", "dimensions": {"D": 20, "domain_size": [440, 220]}},
            "physics": {"Re": 100, "boundary_conditions": {"inlet": "uniform", "outlet": "convective", "walls": "slip"}},
            "solver": {"name": "TensorLBM", "version": "1.0.0", "git_commit": "a1b2c3d"},
        },
        "output": {
            "schema_version": "1.0.0",
            "case_id": "lbm_cylinder_re100",
            "solver_signature": {"name": "TensorLBM", "version": "1.0.0", "git_commit": "a1b2c3d"},
            "primary_quantities": [
                {"name": "C_D", "value": 1.35, "unit": "dimensionless"},
                {"name": "St", "value": 0.165, "unit": "dimensionless"},
            ],
            "conservation_residuals": [
                {"law": "mass", "residual": 5e-7, "tolerance": 1e-6},
                {"law": "momentum_x", "residual": 8e-5, "tolerance": 1e-4},
                {"law": "momentum_y", "residual": 6e-5, "tolerance": 1e-4},
            ],
            "validation_status": "pass",
        },
    },
    {
        "input": {
            "schema_version": "1.0.0",
            "case_id": "lbm_poiseuille_re500",
            "geometry": {"type": "poiseuille_channel", "dimensions": {"H": 40, "L": 400}},
            "physics": {"Re": 500, "boundary_conditions": {"inlet": "parabolic", "outlet": "pressure", "walls": "no_slip"}},
            "solver": {"name": "TensorLBM", "version": "1.0.0", "git_commit": "a1b2c3d"},
        },
        "output": {
            "schema_version": "1.0.0",
            "case_id": "lbm_poiseuille_re500",
            "solver_signature": {"name": "TensorLBM", "version": "1.0.0", "git_commit": "a1b2c3d"},
            "primary_quantities": [
                {"name": "f", "value": 0.128, "unit": "dimensionless"},
            ],
            "conservation_residuals": [
                {"law": "mass", "residual": 1e-9, "tolerance": 1e-6},
                {"law": "momentum_x", "residual": 1e-5, "tolerance": 1e-4},
            ],
            "validation_status": "pass",
        },
    },
]

# ---------------------------------------------------------------------------
# Wigley III multi-solver outputs (m5520 scenario)
# ---------------------------------------------------------------------------
WIGLEY_INPUT = {
    "schema_version": "1.0.0",
    "case_id": "wigley_iii_fn0.25",
    "geometry": {
        "type": "wigley_parabolic",
        "dimensions": {"L": 100.0, "B": 10.0, "T": 6.25},
        "cb": 0.4444,
    },
    "physics": {
        "Fr": 0.25,
        "Re": 5e7,
        "boundary_conditions": {
            "free_surface": "linearized",
            "inlet": "uniform_flow",
            "outlet": "radiation",
        },
    },
    "solver": {"name": "TensorBEM", "version": "2.1.0", "git_commit": "ghi9012"},
}

WIGLEY_OUTPUTS = [
    {
        "schema_version": "1.0.0",
        "case_id": "wigley_iii_fn0.25",
        "solver_signature": {"name": "TensorLBM", "version": "1.0.0", "git_commit": "abc1234"},
        "primary_quantities": [
            {"name": "C_T", "value": 0.00320, "unit": "dimensionless"},
            {"name": "C_F", "value": 0.00192, "unit": "dimensionless"},
            {"name": "C_W", "value": 0.00128, "unit": "dimensionless"},
        ],
        "conservation_residuals": [
            {"law": "mass", "residual": 1e-8, "tolerance": 1e-6},
            {"law": "momentum_x", "residual": 5e-5, "tolerance": 1e-4},
        ],
        "validation_status": "pass",
    },
    {
        "schema_version": "1.0.0",
        "case_id": "wigley_iii_fn0.25",
        "solver_signature": {"name": "TensorFVM", "version": "2.0.0", "git_commit": "def5678"},
        "primary_quantities": [
            {"name": "C_T", "value": 0.00325, "unit": "dimensionless"},
            {"name": "C_F", "value": 0.00195, "unit": "dimensionless"},
            {"name": "C_W", "value": 0.00130, "unit": "dimensionless"},
        ],
        "conservation_residuals": [
            {"law": "mass", "residual": 5e-7, "tolerance": 1e-6},
            {"law": "momentum_x", "residual": 8e-5, "tolerance": 1e-4},
        ],
        "validation_status": "pass",
    },
    {
        "schema_version": "1.0.0",
        "case_id": "wigley_iii_fn0.25",
        "solver_signature": {"name": "TensorBEM", "version": "2.1.0", "git_commit": "ghi9012"},
        "primary_quantities": [
            {"name": "C_T", "value": 0.00318, "unit": "dimensionless"},
            {"name": "C_F", "value": 0.00191, "unit": "dimensionless"},
            {"name": "C_W", "value": 0.00127, "unit": "dimensionless"},
        ],
        "conservation_residuals": [
            {"law": "mass", "residual": 2e-7, "tolerance": 1e-6},
        ],
        "validation_status": "pass",
    },
]

# ---------------------------------------------------------------------------
# Additional synthetic cases to reach 74+
# ---------------------------------------------------------------------------
SHIP_TYPES = [
    ("kcs", "KCS container ship", {"L": 230.0, "B": 32.2, "T": 10.0}, 0.65),
    ("dtmb5415", "DTMB 5415 frigate", {"L": 147.0, "B": 18.8, "T": 5.5}, 0.52),
    ("suboff", "SUBOFF submarine", {"L": 4.356, "D": 0.508}, 0.542),
    ("series60_cb06", "Series 60 Cb=0.60", {"L": 100.0, "B": 14.0, "T": 6.0}, 0.60),
    ("series60_cb07", "Series 60 Cb=0.70", {"L": 100.0, "B": 14.0, "T": 6.0}, 0.70),
    ("vlcc", "VLCC tanker", {"L": 320.0, "B": 58.0, "T": 20.0}, 0.82),
    ("ro-ro", "Ro-Ro ferry", {"L": 180.0, "B": 28.0, "T": 6.5}, 0.55),
]

SOLVERS = ["TensorLBM", "TensorFVM", "TensorBEM"]
FNS = [0.15, 0.20, 0.25, 0.30, 0.35]

additional_cases = []
case_counter = 4  # already have 3 LBM cases

for ship_type, ship_name, dims, cb in SHIP_TYPES:
    for fn in FNS:
        for solver in SOLVERS:
            case_counter += 1
            case_id = f"{ship_type}_fn{fn:.2f}_{solver.lower()}"
            # Vary C_T slightly by solver
            base_ct = 0.003 + cb * 0.001 + fn * 0.002
            solver_offset = {"TensorLBM": 0.0, "TensorFVM": 0.00005, "TensorBEM": -0.00002}
            ct = base_ct + solver_offset[solver]

            inp = {
                "schema_version": "1.0.0",
                "case_id": case_id,
                "geometry": {"type": ship_type, "dimensions": dims, "cb": cb},
                "physics": {
                    "Fr": fn,
                    "Re": 5e7 * fn / 0.25,
                    "boundary_conditions": {"free_surface": "linearized", "inlet": "uniform_flow"},
                },
                "solver": {"name": solver, "version": "1.0.0", "git_commit": f"hash{case_counter:04d}"},
            }
            out = {
                "schema_version": "1.0.0",
                "case_id": case_id,
                "solver_signature": {"name": solver, "version": "1.0.0", "git_commit": f"hash{case_counter:04d}"},
                "primary_quantities": [
                    {"name": "C_T", "value": round(ct, 6), "unit": "dimensionless"},
                    {"name": "C_F", "value": round(ct * 0.6, 6), "unit": "dimensionless"},
                    {"name": "C_W", "value": round(ct * 0.4, 6), "unit": "dimensionless"},
                ],
                "conservation_residuals": [
                    {"law": "mass", "residual": 1e-7, "tolerance": 1e-6},
                ],
                "validation_status": "pass",
            }
            additional_cases.append({"input": inp, "output": out})

# ---------------------------------------------------------------------------
# Write all cases
# ---------------------------------------------------------------------------
all_cases = LBM_CASES + additional_cases

# Write individual case files
cases_dir = OUTPUT_DIR / "verified_cases"
cases_dir.mkdir(exist_ok=True)

for case in all_cases:
    cid = case["input"]["case_id"]
    case_file = cases_dir / f"{cid}.json"
    case_file.write_text(json.dumps(case, indent=2))

# Write Wigley III multi-solver set
wigley_dir = OUTPUT_DIR / "wigley_iii_arbitration"
wigley_dir.mkdir(exist_ok=True)
(wigley_dir / "input.json").write_text(json.dumps(WIGLEY_INPUT, indent=2))
for i, out in enumerate(WIGLEY_OUTPUTS):
    solver_name = out["solver_signature"]["name"]
    (wigley_dir / f"output_{solver_name.lower()}.json").write_text(json.dumps(out, indent=2))

# Write summary
summary = {
    "total_cases": len(all_cases),
    "lbm_canonical_cases": len(LBM_CASES),
    "ship_resistance_cases": len(additional_cases),
    "wigley_iii_multi_solver": len(WIGLEY_OUTPUTS),
    "ship_types_covered": [s[0] for s in SHIP_TYPES],
    "solvers_covered": SOLVERS,
    "froude_numbers": FNS,
}
(OUTPUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2))

print(f"Generated {len(all_cases)} verified cases")
print(f"  LBM canonical: {len(LBM_CASES)}")
print(f"  Ship resistance: {len(additional_cases)}")
print(f"  Wigley III multi-solver: {len(WIGLEY_OUTPUTS)}")
print(f"Output directory: {OUTPUT_DIR}")
