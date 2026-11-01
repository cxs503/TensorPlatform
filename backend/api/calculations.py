"""
Ship performance calculations API.

Provides resistance and stability calculation endpoints with timing instrumentation,
plus a timing baseline endpoint for latency statistics.
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.hull_geometry import SURFACE_REL_TOL
from services.resistance_core import (
    CB_BAND_HI,
    CB_BAND_LO,
    axisymmetric_wetted_surface,
    estimate_resistance,
    geometry_gate,
)
from services.ship_timing import get_collector, timed_calc

router = APIRouter(prefix="/calculations", tags=["船舶总体性能计算"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ResistanceRequest(BaseModel):
    """Resistance calculation request."""
    ship_type: str = Field(default="SUBOFF", description="Hull type identifier")
    speed_ms: float = Field(default=1.0, gt=0, description="Speed in m/s")
    length: float = Field(default=4.356, gt=0, description="Length overall (m)")
    beam: float = Field(default=0.508, gt=0, description="Beam (m)")
    draft: float = Field(default=0.476, gt=0, description="Draft (m)")
    cb: Optional[float] = Field(
        default=None, gt=0, le=1,
        description="Block coefficient. 可缺省：缺省时 Cr 在几何允许区间 Cb∈(0, π/4] 内取误差带，"
                    "不臆造 Cb（旧版默认 0.542 无来源）",
    )
    water_density: float = Field(default=998.0, gt=0, description="Water density (kg/m³)")
    kinematic_viscosity: float = Field(default=1.004e-6, gt=0, description="Kinematic viscosity (m²/s)")
    wetted_surface_m2: Optional[float] = Field(
        default=None, gt=0,
        description="声明的湿表面积（试验值）。给出时覆盖平台闭式 S=πBL(0.5+0.25Cb)，"
                    "避免把 S 的经验偏差隐式吸收进阻力误差",
    )


class ResistanceResponse(BaseModel):
    """Resistance calculation response."""
    ship_type: str
    speed_ms: float
    total_resistance_n: float
    frictional_resistance_n: float
    residual_resistance_n: float
    reynolds_number: float
    froude_number: float
    computation_time_ms: float
    ct: float = 0.0
    wetted_surface_m2: Optional[float] = None
    wetted_surface_source: str = "platform_closure"
    wetted_surface_closure_m2: Optional[float] = None
    cb_used: Optional[float] = None
    cb_source: str = "declared"
    total_resistance_band_n: Optional[List[float]] = None
    in_validity_range: bool = True
    method: str = ""
    warnings: List[str] = []


class StabilityRequest(BaseModel):
    """Stability calculation request."""
    ship_type: str = Field(default="generic", description="Hull type identifier")
    displacement_t: float = Field(default=1000.0, gt=0, description="Displacement (tonnes)")
    gm_m: float = Field(default=1.0, gt=0, description="Metacentric height (m)")
    kb_m: float = Field(default=2.0, gt=0, description="KB (m)")
    bm_m: float = Field(default=3.0, gt=0, description="BM (m)")
    heel_angle_deg: float = Field(default=10.0, description="Heel angle (degrees)")


class StabilityResponse(BaseModel):
    """Stability calculation response."""
    ship_type: str
    heel_angle_deg: float
    righting_arm_m: float
    righting_moment_knm: float
    gm_effective_m: float
    computation_time_ms: float


# ---------------------------------------------------------------------------
# Resistance calculation (Holtrop-Mennen simplified for SUBOFF)
# ---------------------------------------------------------------------------

@router.post("/resistance", response_model=ResistanceResponse)
@timed_calc("resistance")
async def calc_resistance(req: ResistanceRequest):
    """总阻力分解（物理核在 services.resistance_core，端点只做几何闸门与装配）。

    口径（与既有实现一致，改动只做"显式化"）：
        Cf = 0.075/(log10(Re)-2)²            ITTC-1957 平板摩擦线
        Cr = 0.002·(1+2·Cb)·Fn^2.5          平台经验剩余阻力
        Rt = ½·ρ·S·(Cf+Cr)·V²               S 默认取平台闭式，可被声明值覆盖

    几何闸门：Cb/L/B/T 的闭合与湿表面积口径必须通过 check_geometry；Cb 缺省时按几何
    允许区间给 Cr 误差带（不臆造 Cb），并如实标注 cb_source="band_over_geometry"。
    """
    t0 = time.perf_counter()

    # 硬闸门：正性 + 长宽比（不含湿表面积口径——闭式本身已知有偏差，不能拿它否决声明值）
    gate = geometry_gate(
        length=req.length, beam=req.beam, draft=req.draft,
        cb=req.cb if req.cb is not None else 0.5,  # 闭式正性检查用占位值，结果不用其 Cb
    )
    if not gate["ok"]:
        raise HTTPException(status_code=400, detail={"geometry_violations": gate["violations"]})

    warnings: List[str] = []
    if req.cb is not None and req.wetted_surface_m2 is not None:
        s_closure = axisymmetric_wetted_surface(req.cb, req.length, req.beam)
        bias = (s_closure - req.wetted_surface_m2) / req.wetted_surface_m2
        if abs(bias) > SURFACE_REL_TOL:
            warnings.append(
                f"平台闭式湿表面积 {s_closure:.4f} m² 与声明值 {req.wetted_surface_m2:.4f} m² "
                f"偏差 {bias * 100:+.2f}%（已按声明值计算；Rt ∝ S，Ct 不受影响）"
            )
    if req.cb is None:
        band = [CB_BAND_LO, CB_BAND_HI]
        low = estimate_resistance(length=req.length, beam=req.beam, draft=req.draft,
                                 cb=band[0], speed_ms=req.speed_ms, rho=req.water_density,
                                 nu=req.kinematic_viscosity,
                                 wetted_surface_m2=req.wetted_surface_m2)
        high = estimate_resistance(length=req.length, beam=req.beam, draft=req.draft,
                                   cb=band[1], speed_ms=req.speed_ms, rho=req.water_density,
                                   nu=req.kinematic_viscosity,
                                   wetted_surface_m2=req.wetted_surface_m2)
        core = low
        band_force = [low["total_resistance_n"], high["total_resistance_n"]]
        cb_source = "band_over_geometry"
        cb_used = None
        warnings.append(
            "Cb 未提供：Cr 在几何允许区间 Cb∈(0, π/4] 内给误差带，"
            "total_resistance_n 取区间下端（保守），total_resistance_band_n 给出区间"
        )
        cr_value = band_force
        ct_value = high["ct"]
    else:
        try:
            core = estimate_resistance(length=req.length, beam=req.beam, draft=req.draft,
                                       cb=req.cb, speed_ms=req.speed_ms, rho=req.water_density,
                                       nu=req.kinematic_viscosity,
                                       wetted_surface_m2=req.wetted_surface_m2)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        band_force = None
        cb_source = "declared"
        cb_used = req.cb
        ct_value = core["ct"]
        if not core["validity"]["in_validity_range"]:
            warnings.append(
                "超出 Cr 经验式适用域（Fn<=0.4 且 Cb>=0.55）："
                f"Fn={core['froude_number']}, Cb={req.cb}"
            )

    elapsed_ms = (time.perf_counter() - t0) * 1000

    return ResistanceResponse(
        ship_type=req.ship_type,
        speed_ms=req.speed_ms,
        total_resistance_n=core["total_resistance_n"],
        frictional_resistance_n=core["frictional_resistance_n"],
        residual_resistance_n=core["residual_resistance_n"],
        reynolds_number=core["reynolds_number"],
        froude_number=core["froude_number"],
        computation_time_ms=round(elapsed_ms, 3),
        ct=round(ct_value, 8),
        wetted_surface_m2=core["wetted_surface_m2"],
        wetted_surface_source=core["wetted_surface_source"],
        wetted_surface_closure_m2=core["wetted_surface_closure_m2"],
        cb_used=cb_used,
        cb_source=cb_source,
        total_resistance_band_n=band_force,
        in_validity_range=core["validity"]["in_validity_range"],
        method=f"ITTC-1957 Cf + {core['cr_model']}",
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Stability calculation (simplified GZ curve)
# ---------------------------------------------------------------------------

@router.post("/stability", response_model=StabilityResponse)
@timed_calc("stability")
async def calc_stability(req: StabilityRequest):
    """
    Compute righting arm and moment at a given heel angle.

    Uses the wall-sided formula: GZ = GM·sin(φ) + ½·BM·tan²(φ)·sin(φ)
    """
    t0 = time.perf_counter()

    phi = math.radians(req.heel_angle_deg)
    GM = req.gm_m
    BM = req.bm_m
    disp = req.displacement_t * 1000  # tonnes → kg

    # Wall-sided formula
    GZ = GM * math.sin(phi) + 0.5 * BM * (math.tan(phi) ** 2) * math.sin(phi)

    # Righting moment
    g = 9.81
    RM = disp * g * GZ / 1000  # kN·m

    elapsed_ms = (time.perf_counter() - t0) * 1000

    return StabilityResponse(
        ship_type=req.ship_type,
        heel_angle_deg=req.heel_angle_deg,
        righting_arm_m=round(GZ, 6),
        righting_moment_knm=round(RM, 4),
        gm_effective_m=round(GM, 4),
        computation_time_ms=round(elapsed_ms, 3),
    )


# ---------------------------------------------------------------------------
# Timing baseline endpoint
# ---------------------------------------------------------------------------

@router.get("/timing_baseline")
async def get_timing_baseline():
    """
    Return p50/p90/p99 wall-clock latency statistics per endpoint.

    Response format:
    {
      "timing_records": [...],
      "baseline": {
        "resistance": {"count": N, "p50": ..., "p90": ..., "p99": ...},
        "stability":  {"count": M, "p50": ..., "p90": ..., "p99": ...}
      },
      "total_records": K
    }
    """
    collector = get_collector()
    records = collector.get_records()
    baseline = collector.get_baseline()

    return {
        "timing_records": records,
        "baseline": baseline,
        "total_records": len(records),
    }
