"""耐波性三端点的物理实现（替换 MOCK 版 /rao、/motion、/strip-theory）。

与既有端点路径/请求体完全兼容：

* ``POST /api/v1/seakeeping/rao``                    -> RAO 全频段曲线
* ``POST /api/v1/seakeeping/motion``                 -> 自然周期 + 由 RAO 换算的运动幅值
* ``POST /api/v1/seakeeping-advanced/strip-theory``  -> 切片理论（附加质量 kg）

三个端点共用 ``seakeeping_physics`` 内核，因此端点间自然周期天然自洽（<15%）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

try:                                    # 作为 backend/api 包导入（线上服务）
    from api.seakeeping_physics import (
        FREQ_GRID_DEFAULT, motion_amplitudes, natural_periods, rao_curves,
        ship_from_request, strip_theory,
    )
except Exception:                        # 独立模块路径（离线裁决面 / pytest 隔离）
    from seakeeping_physics import (
        FREQ_GRID_DEFAULT, motion_amplitudes, natural_periods, rao_curves,
        ship_from_request, strip_theory,
    )

router = APIRouter()


class SeakeepingRequest(BaseModel):
    """耐波计算请求（与历史端点同构，字段不可删）。"""
    L: float
    B: float
    T: float
    D: Optional[float] = None
    displacement: float          # 排水量 (t)
    Cb: float
    KG: float
    GM: float
    speed: float = 0.0           # 航速 (kn)
    wave_frequency: Optional[float] = None   # 波浪圆频率 (rad/s)
    heading: Optional[float] = 180.0         # 浪向角 (deg)，180=顶浪
    wave_amplitude: Optional[float] = 1.0    # 波幅 (m)


class RAOResult(BaseModel):
    frequencies: List[float]
    heave_rao: List[float]
    pitch_rao: List[float]
    roll_rao: List[float]
    heave_phase_deg: List[float]
    pitch_phase_deg: List[float]
    roll_phase_deg: List[float]
    encounter_frequencies: List[float]
    heading: float
    speed_kn: float
    method: str


class StripTheoryInput(BaseModel):
    """切片理论输入（沿用原 schema）。"""
    L: float
    B: float
    T: float
    D: Optional[float] = None
    displacement: float
    Cb: float
    Cm: Optional[float] = None
    Cwp: Optional[float] = None
    KG: float
    GM: float
    KB: Optional[float] = None
    speed: float = 0.0
    wave_frequency: float
    wave_amplitude: float = 1.0
    heading: float = 180.0
    stations: Optional[List[Dict]] = None
    water_density: float = 1025.0


_METHOD = ("STF strip theory: 20-station longitudinal integration with frequency-dependent "
           "Lewis-section 2D coefficients A33(we)/B33(we), encounter frequency "
           "we=|w-k*U*cos(beta)|, Froude-Krylov excitation with e^{-i k x cos(beta)} phase")


@router.post("/seakeeping/rao", response_model=RAOResult, summary="RAO 幅频响应算子")
async def calculate_rao(request: SeakeepingRequest):
    """全频段 RAO：消费 wave_frequency（决定栅格中心）与 heading/航速。

    返回垂荡 (m/m)、纵摇 (rad/(ka))、横摇 (rad/(ka)) 三自由度曲线，无下限截断。
    """
    ship = ship_from_request(request.model_dump())
    heading = 180.0 if request.heading is None else float(request.heading)
    freqs = list(FREQ_GRID_DEFAULT)
    if request.wave_frequency:                       # 输入频率附近的加密栅格
        w0 = float(request.wave_frequency)
        freqs = sorted({round(w0 * f, 6) for f in (0.25, 0.4, 0.6, 0.8, 0.95, 1.0,
                                                   1.05, 1.2, 1.5, 2.0, 3.0)}
                       | {round(w, 6) for w in FREQ_GRID_DEFAULT}
                       | {round(w0, 6)})
    curves = rao_curves(ship, heading_deg=heading, speed_kn=ship["speed"], freqs=freqs)
    return RAOResult(
        frequencies=curves["frequencies"],
        heave_rao=curves["heave_rao"],
        pitch_rao=curves["pitch_rao"],
        roll_rao=curves["roll_rao"],
        heave_phase_deg=curves["heave_phase_deg"],
        pitch_phase_deg=curves["pitch_phase_deg"],
        roll_phase_deg=curves["roll_phase_deg"],
        encounter_frequencies=curves["encounter_frequencies"],
        heading=heading,
        speed_kn=ship["speed"],
        method=_METHOD,
    )


@router.post("/seakeeping/motion", summary="运动响应与自然周期")
async def predict_motion(request: SeakeepingRequest):
    """自然周期（含附加质量/纵稳性高/虚拟回转半径）+ 由 RAO 换算的运动幅值。"""
    ship = ship_from_request(request.model_dump())
    heading = 180.0 if request.heading is None else float(request.heading)
    amp = float(request.wave_amplitude or 1.0)
    nat = natural_periods(ship)
    motion = motion_amplitudes(ship, request.wave_frequency, heading, ship["speed"], amp)
    return {
        "natural_periods": {"heave": nat["heave"], "pitch": nat["pitch"],
                            "roll": nat["roll"]},
        "motion_amplitudes": {"heave": motion["heave"], "pitch": motion["pitch"],
                              "roll": motion["roll"]},
        "rao": {"heave": motion["heave_rao"], "pitch": motion["pitch_rao"],
                "roll": motion["roll_rao"]},
        "added_mass": {"heave_kg": nat["A33"], "pitch_kg_m2": nat["A55"]},
        "stability": {"GM_L": nat["GM_L"], "BM_L": nat["BM_L"], "KB": nat["KB"],
                      "k_xx_eff": nat["k_xx_eff"], "k_yy": nat["k_yy"]},
        "wave": {"omega": motion["omega"], "omega_e": motion["omega_e"],
                 "amplitude": amp, "heading": heading, "speed_kn": ship["speed"]},
        "method": _METHOD,
    }


@router.post("/seakeeping-advanced/strip-theory", summary="切片理论计算")
async def strip_theory_endpoint(request: StripTheoryInput):
    """切片理论：全频段 RAO、附加质量（kg）、阻尼、自洽自然周期。"""
    ship = ship_from_request(request.model_dump())
    res = strip_theory(ship, float(request.wave_frequency), float(request.heading),
                       ship["speed"], float(request.wave_amplitude or 1.0))
    return res