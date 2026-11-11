"""多域前沿集成成熟化 API（FastAPI，>=3 端点，非法输入 422）。

端点
----
- POST /api/v1/multi-domain/wave-les   波浪操纵-LES 耦合评估（判据 a）
- POST /api/v1/multi-domain/dem-wave   DEM-波浪耦合响应（判据 b）
- POST /api/v1/multi-domain/interface  跨域接口契约检查（判据 c）
- POST /api/v1/multi-domain/e2e        端到端跨域验证（判据 d）
- GET  /api/v1/multi-domain/health     健康检查

Pydantic 校验失败 ⇒ FastAPI 自动返回 422。
"""

from __future__ import annotations

import math

import numpy as np
from fastapi import FastAPI
from pydantic import BaseModel, Field

from .wave_field import AiryWave, ship_surge_terminal_velocity
from .les import LESChannel1D, poiseuille_C_D
from .dem import PolyParticle, analytic_drag_response
from .interface import FieldGrid, InterfaceContract, exchange_field, check_contracts
from .coupling import E2EConfig, run_e2e

app = FastAPI(
    title="Multi-Domain Maturity API",
    description="波浪操纵 + LES + DEM 跨域集成成熟化验证",
    version="1.0.0",
)


class WaveLesRequest(BaseModel):
    wave_a: float = Field(gt=0, le=10, description="波幅 m")
    wave_L: float = Field(gt=0, le=1000, description="波长 m")
    water_h: float = Field(gt=0, le=1000, description="水深 m")
    ship_CD: float = Field(gt=0, le=10, description="阻力系数")
    ship_A: float = Field(gt=0, le=1e4, description="投影面积 m^2")
    les_Re: float = Field(gt=0, le=1e5, description="LES 雷诺数")
    les_ny: int = Field(default=128, ge=8, le=512)
    les_t_end: float = Field(default=100.0, gt=0, le=1000)
    les_dt: float = Field(default=0.002, gt=0, le=0.05)


class DemWaveRequest(BaseModel):
    wave_a: float = Field(gt=0, le=10)
    wave_L: float = Field(gt=0, le=1000)
    water_h: float = Field(gt=0, le=1000)
    particle_size: float = Field(gt=0, le=10, description="特征粒径 m")
    mass: float = Field(gt=0, le=1e6)
    drag_coeff: float = Field(gt=0, le=1e5)
    n_steps: int = Field(default=400, ge=10, le=10000)
    dt: float = Field(default=0.01, gt=0, le=1.0)


class InterfaceRequest(BaseModel):
    nx: int = Field(default=16, ge=2, le=256)
    ny: int = Field(default=8, ge=2, le=256)
    ratio: int = Field(default=4, ge=1, le=16)
    major_a: int = Field(default=1, ge=0)
    minor_a: int = Field(default=0, ge=0)
    major_b: int = Field(default=1, ge=0)
    minor_b: int = Field(default=2, ge=0)


class E2ERequest(BaseModel):
    wave_a: float = Field(default=0.5, gt=0, le=10)
    wave_L: float = Field(default=20.0, gt=0, le=1000)
    water_h: float = Field(default=10.0, gt=0, le=1000)
    ship_mass: float = Field(default=5.0e6, gt=0, le=1e9)
    ship_CD: float = Field(default=0.8, gt=0, le=10)
    ship_A: float = Field(default=50.0, gt=0, le=1e4)
    les_Re: float = Field(default=100.0, gt=0, le=1e5)
    interface_ratio: int = Field(default=4, ge=1, le=16)


@app.get("/api/v1/multi-domain/health")
def health() -> dict:
    return {"status": "ok", "service": "multi-domain-maturity", "version": "1.0.0"}


@app.post("/api/v1/multi-domain/wave-les")
def wave_les(req: WaveLesRequest) -> dict:
    """判据 a：LES 模型对波浪操纵性计算的影响定量评估。"""
    wave = AiryWave(a=req.wave_a, L=req.wave_L, h=req.water_h)
    F_D = wave.drift_force(req.ship_CD, req.ship_A, z=0.0)
    les = LESChannel1D(
        Ly=1.0, ny=req.les_ny, nu=1.0 / req.les_Re, U_mean=1.0,
        body_force_x=12.0 * (1.0 / req.les_Re) * 1.0,
    )
    les.run(req.les_t_end, req.les_dt)
    U_num = les.mean_velocity()
    Re_num = U_num * 1.0 / (1.0 / req.les_Re)
    C_D_les = les.drag_coefficient()
    C_D_ana = poiseuille_C_D(Re_num)
    les_err = abs(C_D_les - C_D_ana) / C_D_ana
    v_t = ship_surge_terminal_velocity(F_D, C_D_les, req.ship_A)
    return {
        "wave_omega": wave.omega,
        "stokes_drift_surface": wave.stokes_drift(0.0),
        "drift_force_N": F_D,
        "C_D_les": C_D_les,
        "C_D_analytic": C_D_ana,
        "les_error": les_err,
        "les_error_lt_15pct": bool(les_err < 0.15),
        "ship_terminal_velocity": v_t,
    }


@app.post("/api/v1/multi-domain/dem-wave")
def dem_wave(req: DemWaveRequest) -> dict:
    """判据 b：多面体颗粒在波浪载荷下的运动响应 vs 解析。"""
    wave = AiryWave(a=req.wave_a, L=req.wave_L, h=req.water_h)
    z0 = -1.0
    U0 = req.wave_a * wave.omega * math.cosh(wave.k * (z0 + req.water_h)) / math.sinh(
        wave.k * req.water_h
    )
    s = req.particle_size
    verts = [[0, 0, 0], [s, 0, 0], [0, s, 0], [0, 0, s]]
    particle = PolyParticle(verts, req.mass, drag_coeff=req.drag_coeff, rho_body=917.0)
    particle.r = [0.0, 0.0, z0]
    tau = req.mass / req.drag_coeff
    phi_eff = -math.pi / 2
    for i in range(req.n_steps):
        t = i * req.dt
        u_f = U0 * math.cos(wave.omega * t - phi_eff)
        particle.step(req.dt, [u_f, 0.0, 0.0], free_surface=0.0)
    v_end = particle.v[0]
    v_ana = analytic_drag_response(req.n_steps * req.dt, U0, wave.omega, tau, phi_eff)
    err = abs(v_end - v_ana) / max(abs(v_ana), 1e-12)
    return {
        "U0": U0,
        "omega": wave.omega,
        "tau": tau,
        "v_end": v_end,
        "v_analytic": v_ana,
        "error": err,
        "error_lt_20pct": bool(err < 0.20),
    }


@app.post("/api/v1/multi-domain/interface")
def interface_check(req: InterfaceRequest) -> dict:
    """判据 c：场交换守恒残差 + 接口版本兼容性。"""
    dx = dy = 1.0
    X, _ = np.meshgrid(np.arange(req.nx) * dx, np.arange(req.ny) * dy, indexing="ij")
    vals = 1.0 + 0.5 * np.cos(2.0 * math.pi * X / (req.nx * dx))
    g = FieldGrid(name="wave_velocity", shape=(req.nx, req.ny), dx=dx, dy=dy, values=vals)
    contract_a = InterfaceContract(
        field_name="wave_velocity", units="m/s", frame="inertial",
        shape=(req.nx, req.ny), dtype="float64",
        sign_convention="positive_up", major=req.major_a, minor=req.minor_a,
    )
    contract_b = InterfaceContract(
        field_name="wave_velocity", units="m/s", frame="inertial",
        shape=(req.nx, req.ny), dtype="float64",
        sign_convention="positive_up", major=req.major_b, minor=req.minor_b,
    )
    g_fine, res = exchange_field(g, req.ratio, contract_a)
    compat, msg = check_contracts(contract_a, contract_b)
    return {
        "conservation_residual": res,
        "residual_lt_1e-8": bool(res < 1e-8),
        "version_a": contract_a.version,
        "version_b": contract_b.version,
        "compatible": compat,
        "compat_msg": msg,
    }


@app.post("/api/v1/multi-domain/e2e")
def e2e(req: E2ERequest) -> dict:
    """判据 d：端到端跨域验证（波浪-湍流-碎冰）。"""
    cfg = E2EConfig(
        wave_a=req.wave_a,
        wave_L=req.wave_L,
        water_h=req.water_h,
        ship_mass=req.ship_mass,
        ship_CD=req.ship_CD,
        ship_A=req.ship_A,
        les_Re=req.les_Re,
        interface_ratio=req.interface_ratio,
    )
    r = run_e2e(cfg)
    return r.to_dict()
