"""绿色航行域 API（EEDI / EEXI / CII）。

路由前缀统一挂在 /calculations/green/* 下（与 resistance/stability 同族），
计算核心在 services/green_navigation.py（纯函数、无 FastAPI 依赖、可单测）。

证据分层（D5）在响应里逐项暴露：
  - attained_evidence_grade = REAL      达到值（能量→燃油→CO₂，可独立复算）
  - required_evidence_grade = WITHHELD  要求值（规则表系数转录，离线不可复核）
  - CII 等级 = NOT_SUPPORTED（未取得 G4 原文，不提供假能力）

所有物理输入均无静默默认：未知燃料/船型/阶段一律 4xx 报错。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from services import green_navigation as gn

router = APIRouter(prefix="/calculations/green", tags=["绿色航行能效"])


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------

class FuelMix(BaseModel):
    """燃料与燃油耗规格。"""

    fuel: str = Field(..., description="燃料键：hfo/lfo/mdo/lpg_propane/lpg_butane/lng/methanol/ethanol")
    sfoc_g_per_kwh: float = Field(..., gt=0, le=400, description="燃油消耗率 (g/kWh)")
    cf_override: Optional[float] = Field(
        default=None, gt=0, le=4, description="自定义碳因子；缺省则按燃料化学计量导出"
    )


class EediRequest(BaseModel):
    ship_type: str = Field(..., description="船型键（见 /calculations/green/evidence）")
    capacity_t: float = Field(..., gt=0, description="Capacity (DWT 或 70%DWT/GT，按船型)")
    v_ref_kn: float = Field(..., gt=0, le=40, description="参考航速 (kn)")
    p_me_kw: float = Field(..., gt=0, description="主机功率 (kW)")
    main_fuel: FuelMix = Field(..., description="主机燃料")
    p_ae_kw: Optional[float] = Field(
        default=None, gt=0, description="辅机功率；缺省按 0.025·ΣMCR+250 折算（≥10000 kW）"
    )
    aux_fuel: Optional[FuelMix] = Field(default=None, description="辅机燃料；缺省同主机")
    p_pti_kw: float = Field(default=0.0, ge=0, description="轴带发电机功率 (kW)")
    p_eff_kw: float = Field(default=0.0, ge=0, description="创新能效技术功率扣除 (kW)")
    f_i: float = Field(default=1.0, gt=0, description="舱容/载重修正系数")
    f_c: float = Field(default=1.0, gt=0, description="立方容量修正系数")
    f_l: float = Field(default=1.0, gt=0, description="冷藏货物修正系数")
    phase: str = Field(default="phase2", description="EEDI 阶段：phase0/phase1/phase2/phase3")


class EexiRequest(BaseModel):
    ship_type: str = Field(..., description="船型键")
    capacity_t: float = Field(..., gt=0)
    v_ref_kn: float = Field(..., gt=0, le=40)
    p_me_kw: float = Field(..., gt=0)
    main_fuel: FuelMix = Field(...)
    p_ae_kw: Optional[float] = Field(default=None, gt=0)
    aux_fuel: Optional[FuelMix] = Field(default=None)
    p_pti_kw: float = Field(default=0.0, ge=0)
    p_eff_kw: float = Field(default=0.0, ge=0)


class CiiRequest(BaseModel):
    ship_type: str = Field(...)
    capacity_t: float = Field(..., gt=0, description="Capacity (DWT)")
    year: int = Field(..., ge=2019, le=2100, description="营运年份")
    distance_nm: Optional[float] = Field(default=None, gt=0, description="航程 (nm)")
    co2_g: Optional[float] = Field(default=None, ge=0, description="CO₂ 排放量 (g)")
    fuel_mass_t: Optional[float] = Field(default=None, ge=0, description="或直接给加油量 (t)")
    fuel: Optional[str] = Field(default=None, description="与 fuel_mass_t 配套的燃料键")

    @model_validator(mode="after")
    def _check_source(self):
        has_co2 = self.co2_g is not None
        has_fuel = self.fuel_mass_t is not None and self.fuel is not None
        if has_co2 == has_fuel:
            raise ValueError("必须且只能提供 (co2_g) 或 (fuel_mass_t + fuel) 之一")
        if self.distance_nm is None:
            raise ValueError("distance_nm 必填")
        return self


def _fuel_spec(mix: FuelMix) -> Dict[str, float]:
    try:
        cf = mix.cf_override if mix.cf_override is not None else gn.carbon_factor(mix.fuel)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"fuel": mix.fuel, "cf": cf, "sfoc": mix.sfoc_g_per_kwh}


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------

@router.get("/fuel_factors")
async def fuel_factors() -> Dict[str, object]:
    """燃料碳因子：化学计量导出 vs 规则公布值逐项比对。"""
    rows: List[Dict[str, object]] = gn.fuel_factor_table()
    worst = max(r["rel_err_pct"] for r in rows if "rel_err_pct" in r)
    return {
        "unit": "gCO2/gFuel",
        "derivation": "C_F = w_C · 44.009/12.011（碳完全氧化计量）",
        "table": rows,
        "worst_rel_err_pct": worst,
        "within_3pct": worst < 3.0,
        "evidence_grade": gn.EVIDENCE_REAL,
    }


@router.get("/evidence")
async def evidence() -> Dict[str, object]:
    """本域证据分级自陈（分层 REAL / WITHHELD / NOT_SUPPORTED）。"""
    payload = gn.evidence_summary()
    payload["registered_ship_types"] = {
        "eedi_reference_lines": sorted(gn.EEDI_REFERENCE_LINES),
        "eexi_reduction": sorted(gn.EEXI_REDUCTION),
        "cii_reference_lines": sorted(gn.CII_REFERENCE_LINES),
    }
    return payload


@router.post("/eedi")
async def calc_eedi(req: EediRequest) -> Dict[str, object]:
    """EEDI：达到值(REAL) + 要求值(WITHHELD) + 合规判定。"""
    aux = req.aux_fuel or req.main_fuel
    p_ae = req.p_ae_kw if req.p_ae_kw is not None else gn.auxiliary_power_default(req.p_me_kw)
    chain = gn.PowerChain(**_build_body(req, aux, p_ae))
    try:
        result = gn.evaluate_eedi(req.ship_type, chain, req.capacity_t, req.v_ref_kn, req.phase)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result["p_ae_kw"] = p_ae
    result["p_ae_source"] = "explicit" if req.p_ae_kw is not None else "default(0.025·ΣMCR+250)"
    return result


@router.post("/eexi")
async def calc_eexi(req: EexiRequest) -> Dict[str, object]:
    """EEXI：达到值(REAL) + 要求值(WITHHELD) + 合规判定。"""
    aux = req.aux_fuel or req.main_fuel
    p_ae = req.p_ae_kw if req.p_ae_kw is not None else gn.auxiliary_power_default(req.p_me_kw)
    chain = gn.PowerChain(**_build_body(req, aux, p_ae))
    try:
        result = gn.evaluate_eexi(req.ship_type, chain, req.capacity_t, req.v_ref_kn)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result["p_ae_kw"] = p_ae
    result["p_ae_source"] = "explicit" if req.p_ae_kw is not None else "default(0.025·ΣMCR+250)"
    return result


@router.post("/cii")
async def calc_cii(req: CiiRequest) -> Dict[str, object]:
    """CII：达到值(REAL) + 要求值(WITHHELD) + 比值；A–E 等级显式 NOT_SUPPORTED。"""
    if req.co2_g is not None:
        co2_g = req.co2_g
        source = "co2_g"
    else:
        try:
            co2_g = gn.voyage_co2_from_fuel(req.fuel, req.fuel_mass_t)["co2_g"]
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        source = "fuel_mass_t·C_F"
    try:
        result = gn.evaluate_cii(req.ship_type, co2_g, req.capacity_t, req.distance_nm, req.year)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result["co2_g"] = co2_g
    result["co2_source"] = source
    return result


@router.post("/voyage_co2")
async def calc_voyage_co2(body: Dict[str, object]) -> Dict[str, object]:
    """由加油量直接给 CO₂（可与功率链互证）。"""
    fuel = body.get("fuel")
    mass = body.get("fuel_mass_t")
    if fuel is None or mass is None:
        raise HTTPException(status_code=422, detail="fuel 与 fuel_mass_t 必填")
    try:
        out = gn.voyage_co2_from_fuel(str(fuel), float(mass))
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    out["evidence_grade"] = gn.EVIDENCE_REAL
    return out


def _build_body(req, aux: FuelMix, p_ae: float) -> Dict[str, object]:
    main = _fuel_spec(req.main_fuel)
    aux_spec = _fuel_spec(aux)
    return {
        "p_me_kw": req.p_me_kw,
        "sfoc_me": main["sfoc"],
        "cf_me": main["cf"],
        "p_ae_kw": p_ae,
        "sfoc_ae": aux_spec["sfoc"],
        "cf_ae": aux_spec["cf"],
        "p_pti_kw": req.p_pti_kw,
        "p_eff_kw": req.p_eff_kw,
        "f_i": getattr(req, "f_i", 1.0),
        "f_c": getattr(req, "f_c", 1.0),
        "f_l": getattr(req, "f_l", 1.0),
    }