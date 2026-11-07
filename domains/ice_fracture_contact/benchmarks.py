"""冰层断裂与接触：经典 DEM 基准算例（理论腿 / 实现腿 / 实验腿三者共用的量测层）。

三个基准（均为冰力学/DEM 的教科书算例，文献值见 ``references.json``）
---------------------------------------------------------------------
1. **单轴压缩（UCS）**：上下刚性板压缩 H/W≈1.9 的试样 → 单轴抗压强度 σ_c、
   表观模量 E_app、泊松比 ν、破坏后残余强度。
2. **巴西劈裂（Brazilian）**：圆盘对径线载荷 → 抗拉强度 σ_t。
   理论腿：圆心拉应力 σ_x = 2P/(πDt)（线载荷解析解）；
   实现腿：用 Love–Weber 均匀化直接量测试样中心区 σ_xx，与解析式比对。
3. **三点弯曲（3PB）**：梁跨中加载 → 弯曲强度 σ_f = 3PL/(2th²)（梁理论）。

量测全部程序化（零手抄）：每个数字都从 ``sim`` 的数组算出，并带样本路径。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from .dem_bpm import (BondParams, ContactParams, IceDEM, add_box_walls, build_beam,
                      build_disc, build_lattice)


@dataclass
class IceSpec:
    """冰样微参数与数值参数（一次实验的全部输入，用于复现）。"""

    radius: float = 2.5e-3          # 颗粒半径 [m]
    rho: float = 917.0              # 冰密度 [kg/m³]
    thickness: float = 0.05         # 二维切片厚度 [m]
    bond_modulus: float = 7.8e9     # 键模量 E_b [Pa]（宏模量 ≈ 1.15 E_b）
    ks_ratio: float = 0.01          # 键剪/法刚度比（ν=(1-r)/(3+r)，r=0.01 ⇒ ν≈0.326）
    sigma_median: float = 2.0e6     # 键拉强度中位值 [Pa]
    weibull_m: float = 4.0          # Weibull 模数
    tau_coeff: float = 1.0
    friction: float = 0.30
    zeta: float = 0.30
    mass_scale: float = 3600.0      # 准静态质量缩放（稳定步长 ×√β，惯性效应另需校验）
    dt_safety: float = 0.10         # 实测稳定系数（0.2 会自旋/切向发散，见 tests）
    seed: int = 20261107

    def bond(self, *, central_force: bool = False) -> BondParams:
        return BondParams(modulus=self.bond_modulus,
                          ks_ratio=0.0 if central_force else self.ks_ratio,
                          sigma_median=self.sigma_median,
                          weibull_m=self.weibull_m,
                          tau_coeff=self.tau_coeff,
                          enable_shear=not central_force,
                          enable_moment=not central_force)

    def contact(self) -> ContactParams:
        return ContactParams(ks_ratio=self.ks_ratio, friction=self.friction, zeta=self.zeta)

    def as_dict(self) -> Dict[str, float]:
        return {k: float(v) for k, v in self.__dict__.items()}


def _new_sim(pos, rad, spec: IceSpec, *, central_force: bool = False, seed: Optional[int] = None) -> IceDEM:
    return IceDEM(pos, rad, rho=spec.rho, thickness=spec.thickness,
                  bond=spec.bond(central_force=central_force), contact=spec.contact(),
                  seed=spec.seed if seed is None else seed, dt_safety=spec.dt_safety,
                  mass_scale=spec.mass_scale, bond_cutoff_ratio=1.15)


def _flatten_layer(pos: np.ndarray, rad: np.ndarray, radius: float, *, top: bool) -> float:
    """把最上（或最下）**一排**颗粒压到同一平面并对齐半径；返回该平面坐标。"""
    y = pos[:, 1]
    ref = float(y.max()) if top else float(y.min())
    row = np.abs(y - ref) <= 1.2 * radius      # 同排（层内抖动 ±0.15·间距 << 1.2r）
    target = float(np.median(y[row]))          # 只做微小平移，不搬动整层
    pos[row, 1] = target
    rad[row] = radius
    return target


def _flatten_rows(pos: np.ndarray, rad: np.ndarray, radius: float) -> None:
    """把最上/最下两排颗粒压平到同一平面并对齐半径（刚性压头接触的必要条件）。

    不这样做时，抖动后的试样顶面是锯齿状：压头只先碰到一两个颗粒，局部压入造成
    载荷抖动与虚假软化（本包踩过的坑之一）。
    """
    _flatten_layer(pos, rad, radius, top=True)
    _flatten_layer(pos, rad, radius, top=False)


def load_until(sim: IceDEM, *, max_steps: int, sample_every: int = 50,
               stop_strain: Optional[float] = None, peak_drop: float = 0.35,
               strain_of=lambda s: 0.0, width: float = 1.0,
               refer: str = "top") -> List[Dict[str, float]]:
    """推进加载并采样，遇到「峰值后跌落到 peak_drop」或超出 stop_strain 即停。"""
    samples: List[Dict[str, float]] = []
    peak = 0.0
    step = 0
    while step < max_steps:
        nsub = max(1, sample_every // 5)
        f_acc = 0.0
        for _ in range(nsub):
            loads = sim.step(sample_every // nsub)
            f_acc += float(loads.get(refer, 0.0))
        loads = {refer: f_acc / nsub}
        step += sample_every
        f = float(loads.get(refer, 0.0))
        eps = float(strain_of(sim))
        sig = f / (width * sim.t)
        peak = max(peak, sig)
        samples.append({"step": step, "time": sim.step_count * sim.dt, "strain": eps,
                        "force": f, "stress": sig, "n_broken": sim.n_broken})
        if stop_strain is not None and eps >= stop_strain:
            break
        if peak > 0 and sig < (1.0 - peak_drop) * peak and eps > 0:
            break
    return samples


def linear_fit(x, y):
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    if x.size < 2:
        return float("nan"), float("nan"), float("nan")
    A = np.stack([np.ones_like(x), x], axis=1)
    coef, res, *_ = np.linalg.lstsq(A, y, rcond=None)
    yhat = A @ coef
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2)) or 1.0
    return float(coef[1]), float(coef[0]), 1.0 - ss_res / ss_tot


def _lateral_strain(sim: IceDEM, x0: np.ndarray, mask: np.ndarray) -> float:
    u = sim.pos - x0
    X = x0[mask, 0]
    A = np.stack([np.ones_like(X), X - X.mean()], axis=1)
    cx, *_ = np.linalg.lstsq(A, u[mask, 0], rcond=None)
    return float(cx[1])


# ---------------------------------------------------------------------- 1) UCS
def make_uniaxial(spec: IceSpec, nx: int = 16, ny: int = 34, jitter: float = 0.15,
                  *, central_force: bool = False):
    pos, rad = build_lattice(nx, ny, spec.radius, jitter=jitter, seed=spec.seed)
    _flatten_rows(pos, rad, spec.radius)
    sim = _new_sim(pos, rad, spec, central_force=central_force)
    add_box_walls(sim, top_vel=0.0)
    return sim


def run_uniaxial(spec: IceSpec, *, strain_rate: float = 1.0e-2, max_strain: float = 6.0e-3,
                 nx: int = 16, ny: int = 34, jitter: float = 0.15, sample_every: int = 40,
                 central_force: bool = False, seed: Optional[int] = None) -> Dict[str, object]:
    """单轴压缩。返回峰值强度、表观模量、泊松比、破坏步数、采样序列。"""
    pos, rad = build_lattice(nx, ny, spec.radius, jitter=jitter, seed=spec.seed)
    _flatten_rows(pos, rad, spec.radius)
    sim = _new_sim(pos, rad, spec, central_force=central_force, seed=seed)
    H0 = float(pos[:, 1].max() - pos[:, 1].min())
    W = float(pos[:, 0].max() - pos[:, 0].min())
    add_box_walls(sim, top_vel=0.0)
    sim.walls[1].velocity = strain_rate * H0          # top：coord 增大 ⇒ 压头下移
    y0 = sim.pos[:, 1]
    x0 = sim.pos.copy()
    c_top = sim.walls[1].coord
    strain_of = lambda s: (s.walls[1].coord - c_top) / H0
    samples = load_until(sim, max_steps=int(max_strain / strain_rate / sim.dt),
                         sample_every=sample_every, stop_strain=max_strain,
                         strain_of=strain_of, width=W)
    sig = np.array([s["stress"] for s in samples])
    eps = np.array([s["strain"] for s in samples])
    ipeak = int(np.argmax(sig))
    sigma_c = float(sig[ipeak])
    eps_peak = float(eps[ipeak])
    # 初始模量：峰值应力的 20%–50% 区间线拟合
    lo, hi = 0.2 * sigma_c, 0.5 * sigma_c
    sel = (sig >= lo) & (sig <= hi) & (eps > 0)
    E_app, _, r2 = linear_fit(eps[sel], sig[sel]) if sel.sum() >= 3 else (float("nan"), 0.0, float("nan"))
    # 泊松比：峰值前 40% 处、中央区域横向位移拟合
    win = (y0 > y0.min() + 0.3 * H0) & (y0 < y0.min() + 0.7 * H0) & (np.abs(x0[:, 0] - x0[:, 0].mean()) < 0.2 * W)
    eps_lat = _lateral_strain(sim, x0, win)
    eps_now = float(eps[-1]) if len(eps) else float("nan")
    # 侧向膨胀为正、轴向压缩量为正 ⇒ ν = +ε_lat/ε_ax
    nu = float(eps_lat / eps_now) if eps_now and eps_now > 1e-12 else float("nan")
    return {
        "case": "uniaxial_compression",
        "sigma_c_Pa": sigma_c,
        "eps_peak": eps_peak,
        "E_app_Pa": float(E_app),
        "fit_r2": float(r2),
        "nu": nu,
        "n_broken": int(sim.n_broken),
        "n_particles": int(sim.n),
        "n_bonds0": int(sim.bi.size),
        "dt_s": float(sim.dt),
        "steps": int(sim.step_count),
        "H0_m": H0,
        "W_m": W,
        "strain_rate_per_s": strain_rate,
        "samples": samples[::max(1, len(samples) // 60)],
    }


# ---------------------------------------------------------------------- 2) 巴西劈裂
def _disc_with_flat(spec: IceSpec, n_rings: int, jitter: float):
    """圆盘试样 + 上下削平成加载弦（巴西劈裂的标准做法，避免点载荷压溃）。"""
    pos, rad = build_disc(n_rings, spec.radius, jitter=jitter, seed=spec.seed)
    pos = pos - pos.mean(axis=0)                      # 归心
    R = 0.5 * float(pos[:, 0].max() - pos[:, 0].min())
    ycut = 0.93 * R
    keep = np.abs(pos[:, 1]) <= ycut
    pos, rad = pos[keep], rad[keep]
    _flatten_rows(pos, rad, spec.radius)
    return pos, rad


def make_brazilian(spec: IceSpec, n_rings: int = 10, jitter: float = 0.10,
                   *, central_force: bool = False):
    pos, rad = _disc_with_flat(spec, n_rings, jitter)
    sim = _new_sim(pos, rad, spec, central_force=central_force)
    D = float(pos[:, 0].max() - pos[:, 0].min())
    plat_half = 0.06 * D
    cx = float(pos[:, 0].mean())
    add_box_walls(sim, top_vel=0.0, span=(cx - plat_half, cx + plat_half, "both"))
    return sim, D


def run_brazilian(spec: IceSpec, *, platen_speed: float = 2.0e-3, n_rings: int = 10,
                  jitter: float = 0.10, sample_every: int = 40, max_strain: float = 0.02,
                  central_force: bool = False, seed: Optional[int] = None) -> Dict[str, object]:
    pos, rad = _disc_with_flat(spec, n_rings, jitter)
    sim = _new_sim(pos, rad, spec, central_force=central_force, seed=seed)
    D = float(pos[:, 0].max() - pos[:, 0].min())
    plat_half = 0.06 * D
    cx = float(pos[:, 0].mean())
    add_box_walls(sim, top_vel=0.0, span=(cx - plat_half, cx + plat_half, "both"))
    sim.walls[1].velocity = platen_speed
    c_top = sim.walls[1].coord
    strain_of = lambda s: (s.walls[1].coord - c_top) / D
    samples = load_until(sim, max_steps=int(max_strain / (platen_speed / D) / sim.dt),
                         sample_every=sample_every, stop_strain=max_strain,
                         strain_of=strain_of, width=D)
    P = np.array([s["force"] for s in samples])
    ipeak = int(np.argmax(P))
    P_max = float(P[ipeak])
    sigma_t_analytic = 2.0 * P_max / (math.pi * D * sim.t)
    sigma_xx_center = float(sim.slab_stress(0, 0.12 * D, center=cx))
    return {
        "case": "brazilian_disc",
        "D_m": D,
        "P_max_N": P_max,
        "sigma_t_analytic_Pa": sigma_t_analytic,
        "sigma_xx_center_Pa": sigma_xx_center,
        "center_vs_analytic": sigma_xx_center / sigma_t_analytic - 1.0 if sigma_t_analytic else None,
        "n_broken": int(sim.n_broken),
        "n_particles": int(sim.n),
        "platen_speed_m_s": platen_speed,
        "dt_s": float(sim.dt),
        "steps": int(sim.step_count),
        "samples": samples[::max(1, len(samples) // 60)],
    }


# ---------------------------------------------------------------------- 3) 三点弯
def run_bending(spec: IceSpec, *, platen_speed: float = 2.0e-3, nx: int = 36, ny: int = 12,
                jitter: float = 0.10, span_ratio: float = 0.8, sample_every: int = 40,
                max_strain: float = 0.02, central_force: bool = False,
                seed: Optional[int] = None) -> Dict[str, object]:
    pos, rad = build_beam(nx, ny, spec.radius, jitter=jitter, seed=spec.seed)
    _flatten_rows(pos, rad, spec.radius)
    sim = _new_sim(pos, rad, spec, central_force=central_force, seed=seed)
    xmin, xmax = float(pos[:, 0].min()), float(pos[:, 0].max())
    L = span_ratio * (xmax - xmin)
    cx = 0.5 * (xmin + xmax)
    h = float(pos[:, 1].max() - pos[:, 1].min())
    # 只保留上下两块压头：底部两侧支撑 + 顶部跨中加载
    sim.walls.clear()
    from .dem_bpm import Wall
    ybot = float(pos[:, 1].min() - rad.max())
    ytop = float(pos[:, 1].max() + rad.max())
    kn = 2.0 * sim._kn_typ
    sim.walls.append(Wall(normal=(0.0, 1.0), coord=ybot, kn=kn,
                          span=(cx - L / 2 - 0.05 * L, cx - L / 2 + 0.05 * L), label="support_l"))
    sim.walls.append(Wall(normal=(0.0, 1.0), coord=ybot, kn=kn,
                          span=(cx + L / 2 - 0.05 * L, cx + L / 2 + 0.05 * L), label="support_r"))
    sim.walls.append(Wall(normal=(0.0, -1.0), coord=-ytop, kn=kn, velocity=platen_speed,
                          span=(cx - 0.05 * L, cx + 0.05 * L), label="load"))
    sim.refresh_dt()
    c_top = sim.walls[-1].coord
    strain_of = lambda s: (s.walls[-1].coord - c_top) / h
    samples = load_until(sim, max_steps=int(max_strain / (platen_speed / h) / sim.dt),
                         sample_every=sample_every, stop_strain=max_strain,
                         strain_of=strain_of, width=h, refer="load")
    P = np.array([s["force"] for s in samples])
    ipeak = int(np.argmax(P))
    P_max = float(P[ipeak])
    sigma_f = 3.0 * P_max * L / (2.0 * sim.t * h ** 2)
    return {
        "case": "three_point_bending",
        "span_m": L,
        "h_m": h,
        "P_max_N": P_max,
        "sigma_f_beam_theory_Pa": sigma_f,
        "n_broken": int(sim.n_broken),
        "n_particles": int(sim.n),
        "platen_speed_m_s": platen_speed,
        "dt_s": float(sim.dt),
        "steps": int(sim.step_count),
        "samples": samples[::max(1, len(samples) // 60)],
    }


# ---------------------------------------------------------------------- 4) 尺寸效应
def run_size_effect(spec: IceSpec, sizes=(20, 28, 40), *, strain_rate: float = 1.0e-2,
                    max_strain: float = 4.0e-3, seed: Optional[int] = None,
                    aspect: float = 0.5) -> Dict[str, object]:
    """同粒径、同长宽比、仅缩放尺寸的单轴压缩：σ_c ∝ V^(-1/m_eff)（Weibull 尺寸效应）。

    只变尺寸、不变长宽比是关键：否则边界效应与尺寸效应混杂（本包踩过的坑）。
    """
    out = []
    for n in sizes:
        nx = max(8, int(round(n * aspect)))
        res = run_uniaxial(spec, strain_rate=strain_rate, max_strain=max_strain,
                           nx=nx, ny=int(n), jitter=0.15, central_force=False, seed=seed)
        out.append({"nx": nx, "ny": int(n), "sigma_c_Pa": res["sigma_c_Pa"],
                    "n_particles": res["n_particles"], "n_broken": res["n_broken"],
                    "E_app_Pa": res["E_app_Pa"], "aspect": nx / float(n)})
    V = np.array([o["n_particles"] for o in out], float)
    S = np.array([o["sigma_c_Pa"] for o in out], float)
    m_eff = float("nan")
    if np.all(S > 0) and np.all(V > 0):
        slope, _, r2 = linear_fit(np.log(V), np.log(S))
        m_eff = float(-slope) if slope < 0 else float("nan")
    else:
        r2 = float("nan")
    return {"case": "weibull_size_effect", "sizes": out, "m_eff": m_eff, "fit_r2": float(r2)}


# ---------------------------------------------------------------------- 5) 弹性标定
def calibrate_modulus(spec: IceSpec, *, target_E: float = 9.0e9, nx: int = 19, ny: int = 19,
                      strain_rate: float = 1.0e-3, max_strain: float = 2.0e-4) -> Dict[str, object]:
    """按目标模量标定 ``bond_modulus``（E 与键刚度严格线性 ⇒ 一次缩放即收敛）。

    试样保持不破坏（键强临时设为极大），保证只量弹性响应。
    """
    trial = IceSpec(**{**spec.__dict__})
    trial.sigma_median = spec.sigma_median * 1e6   # 不破坏
    res = run_uniaxial(trial, strain_rate=strain_rate, max_strain=max_strain,
                       nx=nx, ny=ny, jitter=0.15)
    E_meas = res["E_app_Pa"]
    if not (E_meas and math.isfinite(E_meas) and E_meas > 0):
        raise RuntimeError(f"弹性标定失败：E_meas={E_meas}")
    factor = target_E / E_meas
    out = {"E_measured_before": E_meas, "factor": factor,
           "bond_modulus_before": spec.bond_modulus,
           "bond_modulus_after": spec.bond_modulus * factor,
           "target_E": target_E, "nu_measured": res["nu"]}
    trial2 = IceSpec(**{**spec.__dict__})
    trial2.sigma_median = spec.sigma_median * 1e6
    trial2.bond_modulus = spec.bond_modulus * factor
    res2 = run_uniaxial(trial2, strain_rate=strain_rate, max_strain=max_strain,
                        nx=nx, ny=ny, jitter=0.15)
    out["E_measured_after"] = res2["E_app_Pa"]
    out["residual_rel"] = res2["E_app_Pa"] / target_E - 1.0
    out["nu_after"] = res2["nu"]
    return out


def calibrate_tensile(spec: IceSpec, *, target_sigma_t: float = 1.0e6, n_rings: int = 10,
                      platen_speed: float = 2.0e-3, max_iter: int = 8,
                      tol: float = 0.02) -> Dict[str, object]:
    """按目标抗拉强度标定键强中位值（σ_t 与 σ_b 近似线性 ⇒ 割线迭代）。"""
    cur = IceSpec(**spec.__dict__)
    history = []
    sigma_t = None
    for it in range(max_iter):
        res = run_brazilian(cur, platen_speed=platen_speed, n_rings=n_rings)
        sigma_t = res["sigma_t_analytic_Pa"]
        history.append({"iter": it, "sigma_median": cur.sigma_median, "sigma_t": sigma_t})
        if sigma_t > 0 and abs(sigma_t / target_sigma_t - 1.0) <= tol:
            break
        if not (sigma_t > 0):
            break
        cur.sigma_median *= target_sigma_t / sigma_t
    return {"target_sigma_t": target_sigma_t, "achieved_sigma_t": sigma_t,
            "sigma_median_final": cur.sigma_median,
            "rel_error": (sigma_t / target_sigma_t - 1.0) if sigma_t else None,
            "iterations": len(history), "history": history}


# ---------------------------------------------------------------------- 6) 解析校验
def lattice_elastic_check(*, n: int = 31, radius: float = 2.5e-3, thickness: float = 0.05,
                          bond_modulus: float = 7.8e9, strain: float = 1e-6,
                          margin: float = 1.5) -> Dict[str, object]:
    """理论↔实现闭环：中心力三角格在仿射应变场下的弹性张量。

    解析结果（二维中心力三角格，键刚度 k、厚度 t）：
        λ = μ = √3 k / 4，ν = 1/3，E = (2/√3) k / t
    校验量：σ_xx 与 E·ε 的相对误差；σ_yy/σ_xx、σ_xy/σ_xx 应≈0（Poisson 一致性）。
    """
    pos, rad = build_lattice(n, n, radius, jitter=0.0, seed=1)
    bond = BondParams(modulus=bond_modulus, ks_ratio=0.0, sigma_median=1e12, weibull_m=4.0,
                      enable_shear=False, enable_moment=False)
    sim = IceDEM(pos, rad, bond=bond, contact=ContactParams(enabled=False),
                 thickness=thickness, dt_safety=0.05)
    kn = float(np.mean(sim.kn))
    E_theory = (2.0 / math.sqrt(3.0)) * kn / thickness
    nu_theory = 1.0 / 3.0
    e = float(strain)
    F = np.array([[e, 0.0], [0.0, -nu_theory * e]])
    u = pos @ F.T
    sim.pos = pos + u
    sim.set_elastic_from_displacement(u)
    mask = sim.interior_bond_mask(margin)
    xmin = sim.pos[:, 0].min() + margin * radius
    xmax = sim.pos[:, 0].max() - margin * radius
    ymin = sim.pos[:, 1].min() + margin * radius
    ymax = sim.pos[:, 1].max() - margin * radius
    vol = (xmax - xmin) * (ymax - ymin) * thickness
    sig = sim.virial_stress(mask, vol)
    sig_xx_pred = E_theory * e
    return {
        "case": "lattice_elastic_analytic",
        "n_lattice": n,
        "kn_mean_N_per_m": kn,
        "E_theory_Pa": E_theory,
        "E_measured_Pa": float(sig[0, 0] / e),
        "rel_error_E": float(sig[0, 0] / sig_xx_pred - 1.0),
        "sigma_yy_over_sigma_xx": float(sig[1, 1] / sig[0, 0]),
        "sigma_xy_over_sigma_xx": float(sig[0, 1] / sig[0, 0]),
        "nu_theory": nu_theory,
        "n_bonds_interior": int(mask.sum()),
        "strain": e,
    }


def single_bond_check(*, radius: float = 2.5e-3, thickness: float = 0.05,
                      bond_modulus: float = 7.8e9, load: float = 100.0) -> Dict[str, object]:
    """单键解析校验：静力平衡位移 = F/k_n（相对误差应到机器精度）。"""
    pos = np.array([[0.0, 0.0], [2 * radius, 0.0]])
    rad = np.array([radius, radius])
    bond = BondParams(modulus=bond_modulus, ks_ratio=0.5, sigma_median=1e12)
    sim = IceDEM(pos, rad, bond=bond, contact=ContactParams(enabled=False, zeta=0.5),
                 thickness=thickness, dt_safety=0.05)
    sim.fixed[0] = True
    sim.ext_force[1, 0] = load
    for _ in range(20000):
        sim.step(1)
    u = float(sim.pos[1, 0] - 2 * radius)
    expected = load / float(sim.kn[0])
    return {"case": "single_bond_stiffness", "k_n": float(sim.kn[0]), "F": load,
            "u_measured": u, "u_analytic": expected,
            "rel_error": abs(u - expected) / expected}

# ---------------------------------------------------------------------- 7) 直接拉伸
def run_direct_tension(spec: IceSpec, *, strain_rate: float = 1.0e-2, nx: int = 16, ny: int = 26,
                       jitter: float = 0.10, sample_every: int = 40,
                       max_strain: float = 3.0e-3, central_force: bool = False,
                       seed: Optional[int] = None) -> Dict[str, object]:
    """直接拉伸（狗骨式夹持）：上下两排颗粒做运动学夹头，量测夹头下方切片的 σ_yy。

    为什么不用巴西劈裂当强度判据：二维削平圆盘的线载荷解析式与离散试样不一致
    （见 ``run_brazilian`` 的 center_vs_analytic），且峰值对质量缩放敏感；
    直接拉伸的加载与量测路径都更贴近「σ_t = F/A」的定义，是更干净的实验腿。
    """
    pos, rad = build_lattice(nx, ny, spec.radius, jitter=jitter, seed=spec.seed)
    _flatten_rows(pos, rad, spec.radius)
    sim = _new_sim(pos, rad, spec, central_force=central_force, seed=seed)
    y = pos[:, 1]
    tol = 1.2 * spec.radius
    grip_top = (y.max() - y) <= tol
    grip_bot = (y - y.min()) <= tol
    sim.fixed |= grip_top | grip_bot
    H0 = float(y.max() - y.min())
    W = float(pos[:, 0].max() - pos[:, 0].min())
    v = strain_rate * H0 / 2.0          # 两端各拉一半
    y0_top = float(y[grip_top].mean())
    y0_bot = float(y[grip_bot].mean())
    c_y = y0_bot
    samples = []
    peak = 0.0
    step = 0
    t = 0.0
    while step < int(max_strain / strain_rate / sim.dt):
        sim.step(sample_every)
        t += sim.dt * sample_every
        # 运动学夹头：位置 = 初值 ± v·t，速度 = ±v（键力用相对速度累积，故两者都要给）
        sim.pos[grip_top, 1] = y0_top + v * t
        sim.pos[grip_bot, 1] = y0_bot - v * t
        sim.vel[grip_top, :] = 0.0
        sim.vel[grip_bot, :] = 0.0
        sim.vel[grip_top, 1] = v
        sim.vel[grip_bot, 1] = -v
        sim.omega[grip_top] = 0.0
        sim.omega[grip_bot] = 0.0
        step += sample_every
        sig = sim.slab_stress(1, 0.15 * H0, center=c_y + 0.25 * H0)
        eps = v * t * 2.0 / H0
        samples.append({"step": step, "strain": eps, "sigma_yy": sig, "n_broken": sim.n_broken})
        peak = max(peak, sig)
        if sim.n_broken > 0 and sig < 0.6 * peak:
            break
    return {
        "case": "direct_tension",
        "sigma_t_Pa": float(peak),
        "n_broken": int(sim.n_broken),
        "n_particles": int(sim.n),
        "n_bonds0": int(sim.bi.size),
        "strain_rate_per_s": strain_rate,
        "H0_m": H0, "W_m": W,
        "dt_s": float(sim.dt), "steps": int(sim.step_count),
        "samples": samples[:: max(1, len(samples) // 50)],
    }
