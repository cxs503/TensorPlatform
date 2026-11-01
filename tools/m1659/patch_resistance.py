"""One-shot patch: wire the validated Holtrop-Mennen kernel into /calculations/resistance.

Run from the repo root.  Idempotent (aborts if already patched).
"""
from __future__ import annotations

import pathlib
import sys

P = pathlib.Path("backend/api/calculations.py")
src = P.read_text(encoding="utf-8")

if "HOLTROP_METHOD" in src:
    print("already patched")
    sys.exit(0)

# --- 1. request model: kernel selector + Holtrop particulars ------------------
anchor_req = '''        description="(1+k) viscous form factor from ITTC-1957; 1.0 = bare flat-plate friction only.",
    )


class ResistanceResponse(BaseModel):'''
new_req = '''        description="(1+k) viscous form factor from ITTC-1957; 1.0 = bare flat-plate friction only.",
    )
    method: str = Field(
        default="ittc_axisymmetric",
        description=(
            "Resistance kernel. 'ittc_axisymmetric' (default, backward compatible): ITTC-1957 "
            "friction + hand-fitted axisymmetric residual curve. 'holtrop_mennen': the "
            "published Holtrop-Mennen (1982/1984) regression in "
            "services/holtrop_mennen.py (wetted surface, form factor, wave, bulb, transom "
            "and correlation allowance). Benchmark evidence for the merchant-hull domain, "
            "see docs/reports/m1659_resistance_kernel_wiring.md."
        ),
    )
    cm: Optional[float] = Field(
        default=None, gt=0.5, le=1.0,
        description="Midship section coefficient (Holtrop input). None = 0.92+0.13*Cb regression.",
    )
    cwp: Optional[float] = Field(
        default=None, gt=0.5, le=1.0,
        description="Waterplane coefficient (Holtrop input). None = (1+2*Cp)/3 (Schneekluth).",
    )
    lcb_frac: float = Field(
        default=0.0, ge=-0.05, le=0.05,
        description="Longitudinal centre of buoyancy as a fraction of L (+ forward), Holtrop input.",
    )


class ResistanceResponse(BaseModel):'''
assert anchor_req in src, "request-model anchor not found"
src = src.replace(anchor_req, new_req, 1)

# --- 2. response model: provenance of the kernel ------------------------------
anchor_resp = '''    wetted_surface_m2: float = 0.0
    wetted_surface_source: str = "empirical_formula"
    form_factor: float = 1.0'''
new_resp = '''    wetted_surface_m2: float = 0.0
    wetted_surface_source: str = "empirical_formula"
    form_factor: float = 1.0
    resistance_method: str = "ittc_axisymmetric"
    wave_resistance_n: float = 0.0
    correlation_allowance_n: float = 0.0
    appendage_resistance_n: float = 0.0
    model_notes: List[str] = Field(default_factory=list)'''
assert anchor_resp in src, "response-model anchor not found"
src = src.replace(anchor_resp, new_resp, 1)

# --- 3. module constant -------------------------------------------------------
anchor_const = 'RESISTANCE_METHOD = "ITTC-1957 friction + empirical residual (axisymmetric-body fit)"'
new_const = anchor_const + '''
HOLTROP_METHOD = (
    "Holtrop-Mennen 1982 + 1984 addenda (published regression; S from the HM "
    "wetted-surface fit, Cf ITTC-1957, Rw, RB, Rtr, RA)"
)'''
assert anchor_const in src
src = src.replace(anchor_const, new_const, 1)

# --- 4. Holtrop branch inside the endpoint -----------------------------------
anchor_gate = '''    if L <= 0 or B <= 0 or T <= 0:
        raise HTTPException(status_code=400, detail="Dimensions must be positive")
'''
new_gate = anchor_gate + '''
    if req.method == "holtrop_mennen":
        return _holtrop_resistance_response(req, t0)
'''
assert anchor_gate in src
src = src.replace(anchor_gate, new_gate, 1)

# --- 5. helper implementation ------------------------------------------------
anchor_helper = '''@router.post("/resistance", response_model=ResistanceResponse)'''
new_helper = '''def _holtrop_resistance_response(req, t0: float) -> "ResistanceResponse":
    """Holtrop-Mennen branch of ``/resistance`` (thin, faithful wire-up).

    No re-fitting happens here: the numbers come from
    ``services.holtrop_mennen.holtrop_resistance`` unchanged, so the audit
    identity ``Ct = Rt / (0.5 rho S V^2)`` (canon rule R1) holds exactly and the
    endpoint can be reproduced offline by calling the service directly.
    """
    from services.holtrop_mennen import holtrop_resistance

    r = holtrop_resistance(
        req.length,
        req.beam,
        req.draft,
        req.cb,
        req.speed_ms,
        cm=req.cm,
        cwp=req.cwp,
        lcb_frac=req.lcb_frac,
        wet_surface=req.wetted_surface_m2,
        rho=req.water_density,
        nu=req.kinematic_viscosity,
    )

    notes = [f"derived {k}: {v}" for k, v in r.derived.items()] + list(r.warnings)
    if req.wetted_surface_m2 is not None:
        s_source = "input_validated"
    else:
        s_source = "holtrop_mennen_1982"
    # ``rf_n`` already carries the (1+k) form factor; form_resistance_n stays 0
    # so that Rf + Rform + Rresidual + RA + Rapp == Rt exactly.
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return ResistanceResponse(
        ship_type=req.ship_type,
        speed_ms=req.speed_ms,
        total_resistance_n=round(r.rt_n, 4),
        frictional_resistance_n=round(r.rf_n, 4),
        form_resistance_n=0.0,
        residual_resistance_n=round(r.rw_n + r.rbulb_n + r.rtr_n, 4),
        wave_resistance_n=round(r.rw_n, 4),
        correlation_allowance_n=round(r.ra_n, 4),
        appendage_resistance_n=round(r.rapp_n, 4),
        reynolds_number=round(r.re, 2),
        friction_coefficient_cf=round(r.cf, 10),
        froude_number=round(r.fn, 6),
        wetted_surface_m2=round(r.wetted_surface_m2, 6),
        wetted_surface_source=s_source,
        form_factor=round(r.form_factor, 6),
        total_resistance_coefficient=round(r.ct, 8),
        method=HOLTROP_METHOD,
        resistance_method="holtrop_mennen",
        model_notes=notes,
        computation_time_ms=round(elapsed_ms, 3),
    )


@router.post("/resistance", response_model=ResistanceResponse)'''
assert anchor_helper in src
src = src.replace(anchor_helper, new_helper, 1)

# --- 6. legacy response: label the kernel explicitly -------------------------
anchor_legacy = '''        method=RESISTANCE_METHOD,'''
new_legacy = '''        method=RESISTANCE_METHOD,
        resistance_method="ittc_axisymmetric",'''
assert anchor_legacy in src
src = src.replace(anchor_legacy, new_legacy, 1)

P.write_text(src, encoding="utf-8")
print("patched", P)