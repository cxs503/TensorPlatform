"""端到端跨域耦合：波浪-湍流(LES)-碎冰(DEM) 成熟化验证链。

场景
----
1. 波浪场（AiryWave）→ Stokes 漂移 → 波浪漂移力 F_D。
2. LES 通道流（1D Smagorinsky）→ 拖曳系数 C_D（判据 a：LES 对波浪操纵性影响）。
3. 船纵荡 ODE（F_D 驱动，C_D 阻力）→ 终端速度 vs 精确 tanh 解（判据 a/d）。
4. DEM 四面体颗粒在波浪轨道流中 → 速度响应 vs 闭式解（判据 b/d）。
5. 场交换接口：粗网格 → 细网格保守传递（判据 c）。

守恒口径（判据 d）：
- 船动量 m v_ship(t_end) vs 精确解 m v_exact(t_end)；
- 颗粒动量 m_p v_p(t_end) vs 闭式解；
- 场交换积分守恒残差；
- 综合守恒误差 = max(各量相对误差)。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .wave_field import (
    AiryWave,
    RHO_WATER,
    ship_surge_terminal_velocity,
    ship_surge_exact,
)
from .les import LESChannel1D, poiseuille_C_D
from .dem import PolyParticle, analytic_drag_response
from .interface import FieldGrid, InterfaceContract, exchange_field


@dataclass
class E2EConfig:
    """端到端场景配置。"""

    # 波浪
    wave_a: float = 0.5      # 波幅 m
    wave_L: float = 20.0     # 波长 m
    water_h: float = 10.0    # 水深 m
    # 船
    ship_mass: float = 5.0e6     # kg
    ship_CD: float = 0.8       # 阻力系数（波浪漂移力用）
    ship_A: float = 50.0       # 投影面积 m^2
    ship_t_end: float = 2.0e4  # s
    ship_dt: float = 1.0
    # LES
    les_Re: float = 100.0
    les_ny: int = 128
    les_t_end: float = 100.0
    les_dt: float = 0.002
    # DEM
    dem_n_steps: int = 400
    dem_dt: float = 0.01
    # 接口
    interface_ratio: int = 4


@dataclass
class E2EResult:
    """端到端结果（全部机算，零手抄）。"""

    wave_omega: float
    wave_U0: float
    stokes_drift: float
    drift_force: float
    ship_v_end: float
    ship_v_exact: float
    ship_err: float
    les_C_D: float
    les_C_D_analytic: float
    les_err: float
    dem_v_end: float
    dem_v_analytic: float
    dem_err: float
    interface_residual: float
    interface_ok: bool
    max_conservation_err: float
    verdict: str

    def to_dict(self) -> dict:
        return {
            "wave_omega": self.wave_omega,
            "wave_U0": self.wave_U0,
            "stokes_drift": self.stokes_drift,
            "drift_force": self.drift_force,
            "ship_v_end": self.ship_v_end,
            "ship_v_exact": self.ship_v_exact,
            "ship_err": self.ship_err,
            "les_C_D": self.les_C_D,
            "les_C_D_analytic": self.les_C_D_analytic,
            "les_err": self.les_err,
            "dem_v_end": self.dem_v_end,
            "dem_v_analytic": self.dem_v_analytic,
            "dem_err": self.dem_err,
            "interface_residual": self.interface_residual,
            "interface_ok": self.interface_ok,
            "max_conservation_err": self.max_conservation_err,
            "verdict": self.verdict,
        }


def run_e2e(cfg: Optional[E2EConfig] = None, verbose: bool = False) -> E2EResult:
    """运行端到端跨域耦合验证。"""
    if cfg is None:
        cfg = E2EConfig()

    # 1. 波浪场
    wave = AiryWave(a=cfg.wave_a, L=cfg.wave_L, h=cfg.water_h)
    x0, z0 = 0.0, -1.0
    U0 = cfg.wave_a * wave.omega * math.cosh(wave.k * (z0 + cfg.water_h)) / math.sinh(
        wave.k * cfg.water_h
    )
    U_S = wave.stokes_drift(0.0)
    F_D = wave.drift_force(cfg.ship_CD, cfg.ship_A, z=0.0)

    # 2. LES → C_D（1D Smagorinsky 通道）
    les = LESChannel1D(
        Ly=1.0, ny=cfg.les_ny, nu=1.0 / cfg.les_Re, U_mean=1.0,
        body_force_x=12.0 * (1.0 / cfg.les_Re) * 1.0 / 1.0**2,
    )
    les.run(cfg.les_t_end, cfg.les_dt)
    U_num = les.mean_velocity()
    Re_num = U_num * 1.0 / (1.0 / cfg.les_Re)
    C_D_les = les.drag_coefficient()
    C_D_ana = poiseuille_C_D(Re_num)
    les_err = abs(C_D_les - C_D_ana) / C_D_ana

    # 3. 船纵荡（用 LES C_D）：RK4 vs 精确 tanh 解
    from .wave_field import ship_surge_transient
    traj = ship_surge_transient(
        F_D, C_D_les, cfg.ship_A, cfg.ship_mass, cfg.ship_t_end, cfg.ship_dt,
    )
    v_ship_end = traj[-1][1]
    v_ship_exact = ship_surge_exact(cfg.ship_t_end, F_D, C_D_les, cfg.ship_A, cfg.ship_mass)
    ship_err = abs(v_ship_end - v_ship_exact) / max(abs(v_ship_exact), 1e-12)

    # 4. DEM 颗粒（中性浮力：mass = rho_w * V，无净浮力）
    s = 0.5
    V_p = s**3 / 6.0
    verts = np.array([[0, 0, 0], [s, 0, 0], [0, s, 0], [0, 0, s]], dtype=float)
    m_p = RHO_WATER * V_p
    b_drag = 200.0
    particle = PolyParticle(verts, m_p, drag_coeff=b_drag, rho_body=RHO_WATER)
    particle.r = np.array([x0, 0.0, z0])
    tau = m_p / b_drag
    phi_eff = wave.k * x0 - math.pi / 2
    for i in range(cfg.dem_n_steps):
        t = i * cfg.dem_dt
        u_f = U0 * math.cos(wave.omega * t - phi_eff)
        particle.step(cfg.dem_dt, np.array([u_f, 0.0, 0.0]), free_surface=0.0)
    v_p_end = particle.v[0]
    v_p_ana = analytic_drag_response(
        cfg.dem_n_steps * cfg.dem_dt, U0, wave.omega, tau, phi_eff
    )
    dem_err = abs(v_p_end - v_p_ana) / max(abs(v_p_ana), 1e-12)

    # 5. 接口守恒
    nx_c, ny_c = 16, 8
    dx_c = dy_c = 1.0
    X, _ = np.meshgrid(np.arange(nx_c) * dx_c, np.arange(ny_c) * dy_c, indexing="ij")
    vals = 1.0 + 0.5 * np.cos(2.0 * math.pi * X / (nx_c * dx_c))
    g_coarse = FieldGrid(name="wave_velocity", shape=(nx_c, ny_c), dx=dx_c, dy=dy_c, values=vals)
    contract = InterfaceContract(
        field_name="wave_velocity", units="m/s", frame="inertial",
        shape=(nx_c, ny_c), dtype="float64", sign_convention="positive_up",
    )
    g_fine, res = exchange_field(g_coarse, cfg.interface_ratio, contract)
    interface_ok = res < 1e-8

    # 综合守恒误差
    errs = [ship_err, dem_err, les_err]
    max_err = max(errs)
    verdict = "pass" if (
        ship_err < 0.05 and dem_err < 0.20 and les_err < 0.15
        and interface_ok and max_err < 0.05
    ) else "not_ready"

    if verbose:
        print(f"[e2e] wave_omega={wave.omega:.4f} U0={U0:.4f} U_S={U_S:.6f}")
        print(f"[e2e] F_D={F_D:.2f} N, C_D_les={C_D_les:.4f} (ana {C_D_ana:.4f}, err {les_err:.2%})")
        print(f"[e2e] ship v_end={v_ship_end:.4f} (exact {v_ship_exact:.4f}, err {ship_err:.2%})")
        print(f"[e2e] dem v_end={v_p_end:.4f} (ana {v_p_ana:.4f}, err {dem_err:.2%})")
        print(f"[e2e] interface residual={res:.3e} ok={interface_ok}")
        print(f"[e2e] max_err={max_err:.2%} verdict={verdict}")

    return E2EResult(
        wave_omega=wave.omega,
        wave_U0=U0,
        stokes_drift=U_S,
        drift_force=F_D,
        ship_v_end=v_ship_end,
        ship_v_exact=v_ship_exact,
        ship_err=ship_err,
        les_C_D=C_D_les,
        les_C_D_analytic=C_D_ana,
        les_err=les_err,
        dem_v_end=v_p_end,
        dem_v_analytic=v_p_ana,
        dem_err=dem_err,
        interface_residual=res,
        interface_ok=interface_ok,
        max_conservation_err=max_err,
        verdict=verdict,
    )
