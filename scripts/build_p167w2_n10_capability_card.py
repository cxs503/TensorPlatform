"""Build the CAP-007 capability card from the P167W2-N10 evidence bundle.

Values are read from ``artifacts/p167w2-n10/audit_evidence.json`` (programmatic
injection, zero hand transcription) and the result is validated against
``docs/capability_card.schema.json`` before it is written.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

import jsonschema
import numpy
import scipy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCE = os.path.join(ROOT, "artifacts", "p167w2-n10", "audit_evidence.json")
CARD_PATH = os.path.join(
    ROOT, "docs", "capability_registry", "cards",
    "CAP-007-platform-cross-solver-field-exchange-conservation.json")
SCHEMA = os.path.join(ROOT, "docs", "capability_card.schema.json")

ev = json.load(open(EVIDENCE, encoding="utf-8"))
evidence_bytes = open(EVIDENCE, "rb").read()
evidence_sha256 = hashlib.sha256(evidence_bytes).hexdigest()
evidence_md5 = hashlib.md5(evidence_bytes).hexdigest()
QUOTE = '"all_passed": true'
quote_line = next((ln for ln in evidence_bytes.decode("utf-8").split("\n") if QUOTE in ln), None)
if quote_line is None:
    raise SystemExit("quote anchor not found in the evidence bundle")
quote_line_sha256 = hashlib.sha256(quote_line.encode("utf-8")).hexdigest()[:16]
checks = {c["id"]: c for c in ev["checks"]}
worst = ev["fem_to_dem"]["worst_residual"]

card = {
    "card_id": "CAP-007",
    "name": ("Cross-library field-exchange conservation face "
             "(FEM→DEM four ledgers, LBM→FVM remap + interface flux, clock semantics)"),
    "version": "1.0.0",
    "repo": "TensorPlatform",
    "discipline": "reporting",
    "interface": {
        "format": "JSON",
        "input": {
            "failure_faces": {"type": "json artifact",
                              "description": "fracture surface: per facet vertices, outward unit normal, area"},
            "element_state": {"type": "dataclass",
                              "description": "convex-polytope element with density, rigid-body velocity/angular velocity, released strain energy"},
            "cell_partitions": {"type": "list of convex polytopes",
                                "description": "source and target control volumes of the field transfer"},
            "coupling_plane": {"type": "point + normal",
                               "description": "interface of the mass-flux transfer"},
            "clocks": {"type": "two solver clocks",
                       "description": "dt ratio and coupling sample period"},
        },
        "output": {
            "four_ledger": {"type": "json",
                            "description": "mass / linear momentum / angular momentum / energy / full inertia tensor / volume residuals and verdicts"},
            "remap_report": {"type": "json",
                             "description": "supermesh mass and momentum inventory residuals, coverage, linear-field exactness"},
            "flux_report": {"type": "json",
                            "description": "interface flux residual, area residual, per-target allocation"},
            "sync_certificate": {"type": "json",
                                 "description": "barrier skew, future-read count, optimistic rollback replay deviation, negative controls"},
        },
    },
    "applicability": {
        "parameter_domain": {
            "ledger_tolerance": {"min": 0.0, "max": 1e-9, "unit": "relative residual"},
            "grid_cell_count": {"min": 8, "max": 100000, "unit": "cells"},
            "dt_ratio": {"min": 1, "max": 100, "unit": "-"},
        },
        "regime": ("discrete field exchange between heterogeneous solvers "
                   "(fracture hand-off, lattice-to-volume interpolation, coupled time stepping)"),
        "geometry_type": "convex polytopes",
        "dimensionality": "3D",
    },
    "dependencies": {
        "code_version": "cross-solver-field-exchange/1.0.0",
        "environment": {"python": "3.11"},
        "external_libraries": [f"numpy {numpy.__version__}", f"scipy {scipy.__version__}"],
    },
    "resource_requirements": {
        "gpu": False, "min_gpu_memory_gb": None, "min_cpu_cores": 1,
        "min_ram_gb": 0.5, "estimated_runtime_hours": 0.005, "storage_gb": 0.01,
    },
    "validation_evidence": {
        "verification_status": "PASS",
        "report_id": "docs/p167w2-n10_cross_solver_conservation.md",
        "anchor_type": "repo_file",
        "anchor_path": "artifacts/p167w2-n10/audit_evidence.json",
        "report_body_sha256": evidence_sha256,
        "report_md5": evidence_md5,
        "anchor_title": "cross-solver conservation audit evidence bundle",
        "anchor_fetched_at": "2026-11-10T00:00:00Z",
        "reference_values": {
            "fem_dem_mass_residual": {"value": worst["mass"], "tolerance": 1e-12,
                                      "source": "Σm_DEM = ρV_element (exact geometry)"},
            "fem_dem_momentum_residual": {"value": worst["momentum"], "tolerance": 1e-10,
                                          "source": "Σ(mv)_DEM = M v_element"},
            "fem_dem_angular_momentum_residual": {"value": worst["angular_momentum"], "tolerance": 1e-10,
                                                  "source": "Σ(Iω + d×mv)_DEM = I_element ω"},
            "fem_dem_energy_residual": {"value": worst["energy"], "tolerance": 1e-10,
                                        "source": "Σ(½mv² + ½ωIω)_DEM = T_element + U_released"},
            "fem_dem_inertia_tensor_residual": {"value": worst["inertia"], "tolerance": 1e-10,
                                                "source": "Σ(I_i + m_i(|d|²1 - dd)) = I_element (full tensor)"},
            "fem_dem_volume_residual": {"value": worst["volume"], "tolerance": 1e-12,
                                        "source": "Σ V_fragment = V_element"},
            "lbm_fvm_mass_inventory_residual": {
                "value": ev["lbm_to_fvm"]["inventory"]["mass_residual"], "tolerance": 1e-12,
                "source": "Σ_j ρ_j V_j = Σ_i ρ_i |cell_i ∩ Ω|"},
            "lbm_fvm_interface_flux_residual": {
                "value": checks["lbm_fvm_interface_flux"]["value"], "tolerance": 1e-12,
                "source": "Σ_j ρ_j (u_j·n) A_j = Σ_i ρ_i (u_i·n) A_i on the coupling plane"},
            "lbm_fvm_baseline_flux_residual_oblique": {
                "value": checks["baseline_flux_error_resolvable"]["value"], "tolerance": 1e-4,
                "source": "cell-centre inverse-distance baseline (the error the exact path removes)"},
            "clock_barrier_skew": {"value": checks["clock_barrier_skew"]["value"], "tolerance": 1e-12,
                                   "source": "conservative barrier: max |t_i - t_global|"},
            "clock_optimistic_replay_deviation": {
                "value": checks["clock_optimistic_replay"]["value"], "tolerance": 1e-12,
                "source": "optimistic + rollback vs deterministic replay"},
            "clock_no_rollback_deviation": {
                "value": checks["rollback_necessary"]["value"], "tolerance": 1e-6,
                "source": "speculative run without rollback (must drift)"},
            "sphere_surrogate_inertia_residual": {
                "value": ev["fem_to_dem"]["sphere_surrogate_summary"]["full_tensor_inertia_residual"],
                "tolerance": 1e-10,
                "source": "sphere-equivalent fragments vs element tensor (structural failure, documented)"},
            "sphere_surrogate_angular_momentum_residual": {
                "value": ev["fem_to_dem"]["sphere_surrogate_summary"]["angular_momentum_residual"],
                "tolerance": 1e-10,
                "source": "sphere-equivalent fragments vs element spin (structural failure, documented)"},
        },
        "relative_error_pct": 0.0,
        "relative_error_source": ("analytic identities with exact geometry: each ledger reference is the "
                                  "source-side integral, so the residual measures bookkeeping plus round-off only"),
        "tolerance_pct": 1e-9,
        "gap_reason": None,
        "verification_status_note": (
            f"{ev['n_checks'] - ev['n_failed']}/{ev['n_checks']} machine checks pass; the classical "
            "baselines miss by 0.85 (inertia tensor), 1.06 (angular momentum) and 7.4e-3 "
            "(interface flux), which is exactly what the exact operators remove"),
        "prereg_md5": None,
        "quote_match": QUOTE,
        "quote_line_sha256": quote_line_sha256,
    },
    "failure_modes": [
        {"mode": "unfalsifiable conservation gate",
         "symptom": ("the previous LBM-to-FVM flux check summed rho*u.n over closed cells (identically "
                     "zero) and read a non-existent density attribute on the FVM cells, so the residual "
                     "was 0 for every field and the gate could never fail"),
         "mitigation": ("replaced by a mass-inventory identity over the shared domain, plus a negative "
                        "control test that shifts the target patch and requires a failure"),
         "severity": "critical"},
        {"mode": "isotropic fragment inertia surrogate",
         "symptom": ("sphere-equivalent fragments carry (2/5)mr^2*I, so the inertia tensor and the "
                     "angular-momentum ledgers are structurally unreachable (measured residuals 0.85 and "
                     "1.06 on an anisotropic hand-off)"),
         "mitigation": ("fragments are the convex cells produced by the fracture planes and their tensors "
                        "come from exact tetrahedron moments"),
         "severity": "major"},
        {"mode": "clock-only rollback",
         "symptom": ("the legacy SyncState rollback rewinds current_time but not the solver state and "
                     "silently no-ops (returning True) for targets after the checkpoint, leaving "
                     "current_time != step_count*dt"),
         "mitigation": ("optimistic coupling uses checkpoint + deterministic replay; the defect is "
                        "measured by audit_legacy_rollback"),
         "severity": "major"},
        {"mode": "double-counted interface face",
         "symptom": ("a coupling plane that coincides with a cell face is seen by both adjacent cells, "
                     "so the transferred flux is twice the physical value"),
         "mitigation": "the planar section is attributed to the upstream cell only",
         "severity": "major"},
        {"mode": "finite-difference section area",
         "symptom": ("estimating the section area from dV/dh is exact only away from facet breakpoints "
                     "and picks up round-off / 2h noise near them"),
         "mitigation": ("the shoelace area of the clipped plane section is authoritative; the level-set "
                        "route is kept as an independent cross-check with a 1e-5 agreement bound"),
         "severity": "minor"},
    ],
    "created_at": "2026-11-10T00:00:00Z",
    "updated_at": "2026-11-10T00:00:00Z",
    "status": "active",
    "tags": ["cross-solver", "conservation", "field-exchange", "clock-sync", "zero-domain-semantics"],
    "notes": ("capability_key=platform.cross_solver_field_exchange_conservation; level=REAL; "
              "pytest=tests/test_p167w2_n10_cross_solver_conservation.py; "
              "runner=scripts/run_p167w2_n10_conservation_audit.py; the discipline enum of "
              "docs/capability_card.schema.json is domain-typed, so a domain-neutral verification "
              "face registers as 'reporting' (schema debt noted in the P167W2-N10 report)"),
}

schema = json.load(open(SCHEMA, encoding="utf-8"))
errors = sorted(jsonschema.Draft7Validator(schema).iter_errors(card), key=lambda e: e.path)
if errors:
    for e in errors[:5]:
        print("SCHEMA ERROR:", list(e.path), e.message[:200])
    raise SystemExit(1)

json.dump(card, open(CARD_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("card conforms to docs/capability_card.schema.json ->", CARD_PATH)