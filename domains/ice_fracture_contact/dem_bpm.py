"""二维粘结颗粒离散元（BPM/parallel-bond DEM）——冰层断裂与接触的领域模型。

物理构成（三部分，缺一不可）
----------------------------
1. **接触**（未粘结 / 粘结断裂后）：线性法向弹簧 + 黏性阻尼 + 速度正则化库仑摩擦
   （滑移长度模型，见局限）。法向不计拉力（``F_n ≥ 0``）。
2. **平行粘结**（"冰晶键"）：在接触点同时传递法向力 F_n、切向力 F_t 与弯矩 M，
   均为**增量式**（这是 BPM 与普通接触弹簧的本质区别：M 会被累积，从而提供抗弯刚度）。
   刚度取自梁类比：``k_n = E_b A / L``、``k_s = r_s k_n``、``k_b = E_b I / L``，
   ``A = 2 r̄ t``、``I = t (2 r̄)³/12``。因 ``A/L = t`` 与颗粒尺寸无关，
   宏观模量与颗粒半径解耦（这也是下面解析校验的基础）。
3. **断裂准则**：键截面拉应力 + 弯曲应力
   ``σ = F_n/A + |M|/W``（``W = t(2r̄)²/6``）与剪应力 ``τ = |F_t|/A``；
   ``σ > σ_b`` 或 ``|τ| > τ_b`` 即断键。``σ_b`` 按 Weibull 分布抽样
   （中位值 ``σ_med``、模数 ``m``），提供冰的**尺寸效应**与分散性。

数值格式
--------
显式 DEM（symplectic Euler：
"逐步计算力 → 更新速度 → 更新位置"，力在步首更新，因此增量式键力每步只累积一次）。
稳定步长由全系统最硬模态给出：``dt = safety · min(2√(m/k_n), 2√(m/k_s), 2√(I/k_b))``。

单位：SI。平面厚度 ``t``（默认 0.05 m）——二维切片代表厚度 t 的薄片，
所有宏观量（E、σ）按 ``σ = F/(W t)`` 折算为三维应力，可与文献对比。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

try:  # 近邻搜索用 KD 树；无 scipy 时退化为 O(N²)，结果一致
    from scipy.spatial import cKDTree
except Exception:  # pragma: no cover
    cKDTree = None

GAMMA_W = 0.5772156649015329  # Euler–Mascheroni（未用，保留常量便于扩展）


@dataclass
class BondParams:
    """平行粘结微参数。"""

    modulus: float = 7.8e9        # E_b（键模量, Pa）
    ks_ratio: float = 0.25        # k_s / k_n
    sigma_median: float = 2.5e6   # 键拉强度中位值, Pa
    weibull_m: float = 4.0        # Weibull 模数（>0）
    tau_coeff: float = 1.0        # τ_b = tau_coeff · σ_b
    enable_moment: bool = True    # False ⇒ 退化为中心力键（用于假设对照）
    enable_shear: bool = True     # False ⇒ 无切向键刚度（中心力格）

    def validate(self) -> None:
        if self.modulus <= 0:
            raise ValueError("modulus 必须为正")
        if self.sigma_median <= 0:
            raise ValueError("sigma_median 必须为正")
        if self.weibull_m <= 0:
            raise ValueError("weibull_m 必须为正")
        if not (0.0 <= self.ks_ratio < 10.0):
            raise ValueError("ks_ratio 越界")


@dataclass
class ContactParams:
    """未粘结接触参数。"""

    kn_scale: float = 1.0     # 接触法向刚度 = kn_scale · 粘结 k_n
    ks_ratio: float = 0.25
    friction: float = 0.3
    zeta: float = 0.3         # 局部阻尼比（0 ⇒ 无阻尼，用于能量守恒检验）
    enabled: bool = True


@dataclass
class Wall:
    """刚性墙：法向 n 指向材料内部，平面位置 ``coord``（沿 n 的坐标），材料满足 x·n > coord。"""

    normal: Tuple[float, float]
    coord: float
    velocity: float = 0.0          # 沿 n 的速度（正 = 向材料推进方向？= 减小间隙）
    kn: float = 1.0e11
    zeta: float = 0.5
    span: Optional[Tuple[float, float]] = None  # 沿切向的接触区（限定平板宽度）
    label: str = ""

    def tangent(self) -> Tuple[float, float]:
        return (-self.normal[1], self.normal[0])


class IceDEM:
    """二维粘结颗粒集合体。"""

    def __init__(
        self,
        pos: np.ndarray,
        radius: np.ndarray,
        *,
        rho: float = 917.0,
        thickness: float = 0.05,
        bond: Optional[BondParams] = None,
        contact: Optional[ContactParams] = None,
        seed: int = 0,
        bond_cutoff_ratio: float = 1.02,
        dt_safety: float = 0.20,
        gravity: float = 0.0,
        mass_scale: float = 1.0,
    ):
        self.pos = np.asarray(pos, dtype=float).copy()
        self.r = np.asarray(radius, dtype=float).copy()
        self.n = int(self.r.size)
        if self.pos.shape != (self.n, 2):
            raise ValueError("pos 形状必须为 (N,2)")
        self.rho = float(rho)
        self.t = float(thickness)
        self.bond_p = bond or BondParams()
        self.bond_p.validate()
        self.contact_p = contact or ContactParams()
        self.rng = np.random.default_rng(seed)
        self.gravity = float(gravity)

        # 质量缩放（准静态 DEM 的标准做法）：刚度不变、惯量 ×β ⇒ 稳定步长 ×√β。
        # 只有在惯性效应可忽略（v << √(E/ρβ)）时结果才与 β 无关，本包在测试中校验此条。
        self.mass_scale = float(mass_scale)
        if self.mass_scale <= 0:
            raise ValueError("mass_scale 必须为正")
        self.m = rho * math.pi * self.r ** 2 * self.t * self.mass_scale
        self.I = 0.5 * self.m * self.r ** 2
        self.vel = np.zeros((self.n, 2))
        self.ang = np.zeros(self.n)
        self.omega = np.zeros(self.n)
        self.fixed = np.zeros(self.n, dtype=bool)
        self.walls: List[Wall] = []
        self.ext_force = np.zeros((self.n, 2))   # 外力（静力标定用）

        self._build_bonds(bond_cutoff_ratio)
        self._init_contact_stiffness()
        self._rebuild_contacts()
        self.dt_safety = float(dt_safety)
        self.dt = self._critical_dt() * self.dt_safety
        self.step_count = 0
        self.energy_injected = 0.0
        self.rebuild_every = 25     # 近邻表重建间隔（颗粒单步位移 << 半径，足够安全）

    # ------------------------------------------------------------------ 构建
    def _build_bonds(self, cutoff_ratio: float) -> None:
        """以初始近邻（中心距 ≤ cutoff·(ri+rj)）建立粘结网络。"""
        i, j = self._pairs(cutoff_ratio)
        self.bi, self.bj = i, j
        nb = i.size
        d = np.linalg.norm(self.pos[j] - self.pos[i], axis=1)
        rbar = 0.5 * (self.r[i] + self.r[j])
        area = 2.0 * rbar * self.t
        inertia = self.t * (2.0 * rbar) ** 3 / 12.0
        kn = self.bond_p.modulus * area / d
        ks = kn * self.bond_p.ks_ratio if self.bond_p.enable_shear else np.zeros(nb)
        kb = self.bond_p.modulus * inertia / d if self.bond_p.enable_moment else np.zeros(nb)
        self.kn, self.ks, self.kb = kn, ks, kb
        self.area, self.sect_mod, self.rbar = area, inertia / rbar, rbar
        self.Fn = np.zeros(nb)
        self.Ft = np.zeros(nb)
        self.M = np.zeros(nb)
        self.alive = np.ones(nb, dtype=bool)
        self.cb = 2.0 * self.contact_p.zeta * np.sqrt(np.minimum(kn, 1e18) * 0.5 * self.m[i])
        # 弯曲（弯矩）阻尼：相对转动模态在切向弹簧很弱时无阻尼，会持续振铃并放大切向力；
        # 按 Kelvin–Voigt 加同相阻尼 M += c_m·(ωj-ωi)，c_m = 2ζ√(kb·I_eff)，I_eff = I_i I_j/(I_i+I_j)。
        I_eff = 0.5 * self.I[i]
        self.cm = 2.0 * self.contact_p.zeta * np.sqrt(np.maximum(kb, 0.0) * I_eff)
        # Weibull 键强（中位值 = sigma_median）
        u = self.rng.random(nb)
        scale = (math.log(2.0)) ** (-1.0 / self.bond_p.weibull_m)
        self.sigma_b = self.bond_p.sigma_median * scale * (-np.log(1.0 - u)) ** (1.0 / self.bond_p.weibull_m)
        self.tau_b = self.bond_p.tau_coeff * self.sigma_b
        self.n_broken = 0
        self.break_log: List[Dict[str, float]] = []

    def _init_contact_stiffness(self) -> None:
        """接触法向刚度的代表值（= 粘结法向刚度中位数；接触刚度按比例缩放）。"""
        self._kn_typ = float(np.median(self.kn)) if self.kn.size else 1.0

    def refresh_dt(self, safety: Optional[float] = None) -> float:
        """按当前刚度/墙体重算稳定步长（加墙后必须调用）。"""
        if safety is not None:
            self.dt_safety = float(safety)
        self.dt = self._critical_dt() * self.dt_safety
        return self.dt

    def _pairs(self, cutoff_ratio: float = 1.02) -> Tuple[np.ndarray, np.ndarray]:
        cutoff = 2.0 * float(self.r.max()) * cutoff_ratio
        if cKDTree is not None:
            tree = cKDTree(self.pos)
            pairs = np.array(sorted(tree.query_pairs(r=cutoff, output_type="set")), dtype=int)
            if pairs.size == 0:
                return np.zeros(0, int), np.zeros(0, int)
            i, j = pairs[:, 0], pairs[:, 1]
        else:  # pragma: no cover
            dmat = np.linalg.norm(self.pos[:, None, :] - self.pos[None, :, :], axis=2)
            i, j = np.where(np.triu(dmat < cutoff, 1))
        d = np.linalg.norm(self.pos[j] - self.pos[i], axis=1)
        keep = d <= cutoff_ratio * (self.r[i] + self.r[j])
        return i[keep], j[keep]

    def _rebuild_contacts(self) -> None:
        """重算近邻对；剔除仍存活的粘结对（避免键力与接触力重复计数）。"""
        i, j = self._pairs(1.02)
        if self.alive.any():
            alive_key = set((self.bi[self.alive] * self.n + self.bj[self.alive]).tolist())
            mask = np.array([(a * self.n + b) not in alive_key for a, b in zip(i, j)], dtype=bool)
            i, j = i[mask], j[mask]
        self.ci, self.cj = i, j

    # ------------------------------------------------------------------ 步长
    def _critical_dt(self) -> float:
        mm = float(self.m.min())
        kmax = float(max(np.max(self.kn) if self.kn.size else 0.0,
                         np.max(self.ks) if self.ks.size else 0.0,
                         self.contact_p.kn_scale * float(np.max(self.kn)) if self.kn.size else 0.0))
        cands = [2.0 * math.sqrt(mm / kmax)] if kmax > 0 else [1e-3]
        if self.kb.size and np.max(self.kb) > 0:
            cands.append(2.0 * math.sqrt(float(self.I.min()) / float(np.max(self.kb))))
        # 切向键力 + 力臂（2R）构成的转动模态：实测最硬，忽略它会在 Δt 偏大时自旋发散
        ksmax = float(np.max(self.ks)) if self.ks.size and np.max(self.ks) > 0 else 0.0
        if ksmax > 0:
            lever2 = (2.0 * float(self.r.mean())) ** 2
            cands.append(2.0 * math.sqrt(float(self.I.min()) / (ksmax * lever2)))
            cands.append(2.0 * math.sqrt(mm / ksmax))
        for w in self.walls:
            cands.append(2.0 * math.sqrt(mm / w.kn))
        return float(min(cands))

    # ------------------------------------------------------------------ 力
    def _bond_forces(self) -> Tuple[np.ndarray, np.ndarray]:
        F = np.zeros((self.n, 2))
        T = np.zeros(self.n)
        if not self.alive.any():
            return F, T
        idx = np.nonzero(self.alive)[0]
        i, j = self.bi[idx], self.bj[idx]
        dx = self.pos[j] - self.pos[i]
        d = np.linalg.norm(dx, axis=1)
        nrm = dx / d[:, None]
        tan = np.stack([-nrm[:, 1], nrm[:, 0]], axis=1)
        ri = self.r[i][:, None] * nrm
        rj = -self.r[j][:, None] * nrm
        wi, wj = self.omega[i], self.omega[j]
        vip = self.vel[i] + np.stack([-wi * ri[:, 1], wi * ri[:, 0]], axis=1)
        vjp = self.vel[j] + np.stack([-wj * rj[:, 1], wj * rj[:, 0]], axis=1)
        vrel = vjp - vip
        dvn = np.einsum("ij,ij->i", vrel, nrm)
        dvt = np.einsum("ij,ij->i", vrel, tan)
        dw = wj - wi
        dt = self.dt
        kn, ks, kb = self.kn[idx], self.ks[idx], self.kb[idx]
        self.Fn[idx] += kn * dvn * dt
        self.Ft[idx] += ks * dvt * dt
        self.M[idx] += kb * dw * dt
        # 阻尼按分量配套：无对应刚度则不加该分量的黏性力（否则会凭空给颗粒送自旋）
        fn = self.Fn[idx] + self.cb[idx] * dvn
        ft = self.Ft[idx] + (self.cb[idx] * dvt if np.any(ks > 0) else 0.0)
        m = self.M[idx] + self.cm[idx] * dw
        # 断裂判定
        sigma = fn / self.area[idx] + np.abs(m) / self.sect_mod[idx]
        tau = np.abs(ft) / self.area[idx]
        broken = ((fn > 0) & (sigma > self.sigma_b[idx])) | (tau > self.tau_b[idx])
        if broken.any():
            bsel = idx[broken]
            self.alive[bsel] = False
            self.n_broken += int(bsel.size)
            for k, bidx in enumerate(bsel):
                self.break_log.append({
                    "step": int(self.step_count),
                    "time": float(self.step_count * self.dt),
                    "sigma_over_sb": float(sigma[broken][k] / self.sigma_b[bidx]),
                    "tau_over_tb": float(tau[broken][k] / self.tau_b[bidx]),
                    "mode": "tension" if sigma[broken][k] / self.sigma_b[bidx]
                    >= tau[broken][k] / self.tau_b[bidx] else "shear",
                })
            keep = ~broken
            i, j, nrm, tan, fn, ft, m = i[keep], j[keep], nrm[keep], tan[keep], fn[keep], ft[keep], m[keep]
            ri, rj = ri[keep], rj[keep]
        # 符号约定：f 为作用在 i 上的力（拉为正 ⇒ 把 j 拉回 i 的反向）
        fx = fn * nrm[:, 0] + ft * tan[:, 0]
        fy = fn * nrm[:, 1] + ft * tan[:, 1]
        # 力对严格反号（内部力，动量守恒到机器精度）
        F[:, 0] += np.bincount(i, fx, minlength=self.n) - np.bincount(j, fx, minlength=self.n)
        F[:, 1] += np.bincount(i, fy, minlength=self.n) - np.bincount(j, fy, minlength=self.n)
        # 弯矩 + 切向力对两颗粒的反向力偶（合力矩为零，角动量守恒）
        Ti = self.r[i] * ft + m
        Tj = self.r[j] * ft - m
        T += np.bincount(j, Tj, minlength=self.n) + np.bincount(i, Ti, minlength=self.n)
        return F, T

    def _contact_forces(self) -> Tuple[np.ndarray, np.ndarray]:
        F = np.zeros((self.n, 2))
        T = np.zeros(self.n)
        if not self.contact_p.enabled or self.ci.size == 0:
            return F, T
        i, j = self.ci, self.cj
        dx = self.pos[j] - self.pos[i]
        d = np.linalg.norm(dx, axis=1)
        pen = self.r[i] + self.r[j] - d
        act = pen > 0
        if not act.any():
            return F, T
        i, j, d, pen = i[act], j[act], d[act], pen[act]
        nrm = dx[act] / d[:, None]
        tan = np.stack([-nrm[:, 1], nrm[:, 0]], axis=1)
        vrel = self.vel[j] - self.vel[i]
        dvn = np.einsum("ij,ij->i", vrel, nrm)
        dvt = np.einsum("ij,ij->i", vrel, tan)
        kn = self.contact_p.kn_scale * self._kn_typ
        ks = kn * self.contact_p.ks_ratio
        meff = 0.5 * self.m[i]
        cn = 2.0 * self.contact_p.zeta * np.sqrt(kn * meff)
        fn = np.maximum(0.0, kn * pen - cn * dvn)
        # 库仑摩擦用有界光滑式（tanh 滑移正则）：f_t = -μ f_n tanh(v_t/v_ref)。
        # 不能用「速度×大系数」形式——对转动惯量极小的圆盘会形成转速正反馈而发散。
        vref = 1.0e-3
        ft = -self.contact_p.friction * fn * np.tanh(dvt / vref)
        fx = fn * nrm[:, 0] + ft * tan[:, 0]
        fy = fn * nrm[:, 1] + ft * tan[:, 1]
        F[:, 0] += np.bincount(j, fx, minlength=self.n) - np.bincount(i, fx, minlength=self.n)
        F[:, 1] += np.bincount(j, fy, minlength=self.n) - np.bincount(i, fy, minlength=self.n)
        T += np.bincount(j, -self.r[j] * ft, minlength=self.n) + np.bincount(i, -self.r[i] * ft, minlength=self.n)
        self._last_contacts = (i, j, fx, fy)
        return F, T

    def wall_forces(self) -> Tuple[np.ndarray, np.ndarray, Dict[str, float]]:
        F = np.zeros((self.n, 2))
        T = np.zeros(self.n)
        loads: Dict[str, float] = {}
        for w in self.walls:
            nvec = np.array(w.normal, dtype=float)
            tvec = np.array(w.tangent(), dtype=float)
            # pen = r - x·n + coord（材料在 x·n > coord 一侧）
            s = self.r - (self.pos @ nvec) + w.coord
            act = s > 0
            if w.span is not None:
                along = self.pos @ tvec
                act &= (along >= w.span[0]) & (along <= w.span[1])
            if not act.any():
                loads[w.label] = loads.get(w.label, 0.0)
                continue
            idx = np.nonzero(act)[0]
            pen = s[idx]
            vn = self.vel[idx] @ nvec - w.velocity
            meff = self.m[idx]
            cn = 2.0 * w.zeta * np.sqrt(w.kn * meff)
            # 阻尼符号：pen 的变化率 = -vn，Kelvin–Voigt 取 F = k·pen + c·(dpen/dt) = k·pen - c·vn
            fn = np.maximum(0.0, w.kn * pen - cn * vn)
            vt = self.vel[idx] @ tvec
            fr = self.contact_p.friction
            ft = -fr * fn * np.tanh(vt / 1.0e-3)
            fvec = fn[:, None] * nvec + ft[:, None] * tvec
            F[idx] += fvec
            rvec = -self.r[idx][:, None] * nvec
            T[idx] += rvec[:, 0] * fvec[:, 1] - rvec[:, 1] * fvec[:, 0]
            loads[w.label] = loads.get(w.label, 0.0) + float(fn.sum())
        return F, T, loads

    # ------------------------------------------------------------------ 积分
    def step(self, nsteps: int = 1) -> Dict[str, float]:
        loads: Dict[str, float] = {}
        for _ in range(nsteps):
            if self.step_count and self.step_count % self.rebuild_every == 0:
                self._rebuild_contacts()
            Fb, Tb = self._bond_forces()
            Fc, Tc = self._contact_forces()
            Fw, Tw, loads = self.wall_forces()
            F = Fb + Fc + Fw + self.ext_force
            Tq = Tb + Tc + Tw
            if self.gravity:
                F[:, 1] -= self.gravity * self.m
            free = ~self.fixed
            self.vel[free] += self.dt * F[free] / self.m[free, None]
            self.omega[free] += self.dt * Tq[free] / self.I[free]
            self.pos += self.dt * self.vel
            self.ang += self.dt * self.omega
            self.step_count += 1
            for w in self.walls:
                w.coord += w.velocity * self.dt
        return loads

    # ------------------------------------------------------------------ 量测
    def kinetic_energy(self) -> float:
        return float(0.5 * np.sum(self.m * np.sum(self.vel ** 2, axis=1))
                     + 0.5 * np.sum(self.I * self.omega ** 2))

    def elastic_energy(self) -> float:
        alive = self.alive
        e = 0.5 * (self.Fn[alive] ** 2 / self.kn[alive]
                   + np.where(self.ks[alive] > 0, self.Ft[alive] ** 2 / np.maximum(self.ks[alive], 1e-30), 0.0)
                   + np.where(self.kb[alive] > 0, self.M[alive] ** 2 / np.maximum(self.kb[alive], 1e-30), 0.0))
        return float(np.sum(e))

    def mechanical_energy(self) -> float:
        return self.kinetic_energy() + self.elastic_energy()

    def total_momentum(self) -> np.ndarray:
        return (self.m[:, None] * self.vel).sum(axis=0)

    def slab_stress(self, axis: int, half_width: float, center: Optional[float] = None) -> float:
        """切片内 homogenized 应力 σ_aa（a = axis）：σ_ab = Σ f_a l_b / V。

        粘结（存活）与未粘结接触都计入；破坏后接触传力不会凭空消失。
        """
        a = int(axis)
        other = 1 - a
        c0 = float(np.mean(self.pos[:, a])) if center is None else float(center)
        stat = self.pos[:, other]
        span = stat.max() - stat.min() + 2 * self.r.max()
        vol = 2.0 * half_width * span * self.t
        total = 0.0
        mid = 0.5 * (self.pos[self.bi, a] + self.pos[self.bj, a])
        sel = (np.abs(mid - c0) <= half_width) & self.alive
        if sel.any():
            i, j = self.bi[sel], self.bj[sel]
            dx = self.pos[j] - self.pos[i]
            d = np.linalg.norm(dx, axis=1)
            nrm = dx / d[:, None]
            tan = np.stack([-nrm[:, 1], nrm[:, 0]], axis=1)
            force = self.Fn[sel][:, None] * nrm + self.Ft[sel][:, None] * tan
            total += float(np.sum(force[:, a] * dx[:, a]))
        last = getattr(self, "_last_contacts", None)
        if last is not None and len(last[0]):
            i, j, fxc, fyc = last
            fv = np.stack([fxc, fyc], axis=1)
            midc = 0.5 * (self.pos[i, a] + self.pos[j, a])
            m = np.abs(midc - c0) <= half_width
            if m.any():
                total += float(np.sum(fv[m, a] * (self.pos[j[m], a] - self.pos[i[m], a])))
        return total / vol

    def slab_stress_xx(self, half_width: float) -> float:
        """中央竖直切片内的 homogenized σ_xx（Love–Weber：σ_ab = Σ f_a l_b / V）。

        粘结（存活）与未粘结接触都计入；破坏后接触传力不会凭空消失。
        """
        x0 = float(np.mean(self.pos[:, 0]))
        vol = 2.0 * half_width * (self.pos[:, 1].max() - self.pos[:, 1].min()
                                  + 2 * self.r.max()) * self.t
        total = 0.0
        sel = np.abs(0.5 * (self.pos[self.bi, 0] + self.pos[self.bj, 0]) - x0) <= half_width
        sel &= self.alive
        if sel.any():
            i, j = self.bi[sel], self.bj[sel]
            dx = self.pos[j] - self.pos[i]
            d = np.linalg.norm(dx, axis=1)
            nrm = dx / d[:, None]
            tan = np.stack([-nrm[:, 1], nrm[:, 0]], axis=1)
            fx = self.Fn[sel] * nrm[:, 0] + self.Ft[sel] * tan[:, 0]
            total += float(np.sum(fx * dx[:, 0]))
        last = getattr(self, "_last_contacts", None)
        if last is not None and len(last[0]):
            i, j, fxc, _fy = last
            mid = 0.5 * (self.pos[i, 0] + self.pos[j, 0])
            m = np.abs(mid - x0) <= half_width
            if m.any():
                dx = self.pos[j[m], 0] - self.pos[i[m], 0]
                total += float(np.sum(fxc[m] * dx))
        return total / vol

    def set_elastic_from_displacement(self, u: np.ndarray) -> None:
        """按给定位移场直接赋弹性键力（不做时间积分）：用于解析刚度校验。

        F_n = k_n (Δu·n)、F_t = k_s (Δu·t)、M = 0（均匀应变场无相对转角）。
        """
        dx = self.pos[self.bj] - self.pos[self.bi]
        d = np.linalg.norm(dx, axis=1)
        nrm = dx / d[:, None]
        tan = np.stack([-nrm[:, 1], nrm[:, 0]], axis=1)
        du = u[self.bj] - u[self.bi]
        self.Fn = self.kn * np.einsum("ij,ij->i", du, nrm)
        self.Ft = self.ks * np.einsum("ij,ij->i", du, tan)
        self.M = np.zeros_like(self.Fn)

    def interior_bond_mask(self, margin: float = 2.0) -> np.ndarray:
        """内部键掩码（两端点都离边界 > margin 个颗粒半径）：消除自由面缺键偏置。"""
        x, y = self.pos[:, 0], self.pos[:, 1]
        xmin, xmax = x.min() + margin * self.r.max(), x.max() - margin * self.r.max()
        ymin, ymax = y.min() + margin * self.r.max(), y.max() - margin * self.r.max()
        inside = ((x > xmin) & (x < xmax) & (y > ymin) & (y < ymax))
        return inside[self.bi] & inside[self.bj]

    def virial_stress(self, mask: np.ndarray, vol: float) -> np.ndarray:
        """对给定键集合做 Love–Weber 均匀化，返回 2×2 应力张量（Pa）。"""
        i, j = self.bi[mask], self.bj[mask]
        dx = self.pos[j] - self.pos[i]
        d = np.linalg.norm(dx, axis=1)
        nrm = dx / d[:, None]
        tan = np.stack([-nrm[:, 1], nrm[:, 0]], axis=1)
        f = self.Fn[mask][:, None] * nrm + self.Ft[mask][:, None] * tan
        return (f[:, :, None] * dx[:, None, :]).sum(axis=0) / vol

    # ------------------------------------------------------------------ 加载/标定
    def fix_rows(self, *, bottom: bool = True, top: bool = False, tol: float = 0.75) -> None:
        """固定最下/最上排颗粒（位移边界，用于解析格校验）。"""
        y = self.pos[:, 1]
        ymin, ymax = y.min(), y.max()
        if bottom:
            self.fixed |= (y - ymin) <= tol * 2.0 * float(self.r.mean())
        if top:
            self.fixed |= (ymax - y) <= tol * 2.0 * float(self.r.mean())

    def apply_row_traction(self, total_force: float, *, top: bool = True, tol: float = 0.75) -> int:
        """在最上（或最下）排颗粒上均布竖向合力（单轴应力边界）。"""
        y = self.pos[:, 1]
        ymax, ymin = y.max(), y.min()
        row = (ymax - y) <= tol * 2.0 * float(self.r.mean()) if top else (y - ymin) <= tol * 2.0 * float(self.r.mean())
        nrow = int(row.sum())
        self.ext_force[:] = 0.0
        if nrow:
            self.ext_force[row, 1] = total_force / nrow
        return nrow

    def scale_bond_modulus(self, factor: float) -> None:
        """整体缩放键刚度（标定宏观模量用；E 与刚度严格线性）。"""
        self.kn *= factor
        self.ks *= factor
        self.kb *= factor
        self._init_contact_stiffness()
        self.refresh_dt()

    def set_bond_strength(self, sigma_median: float, weibull_m: Optional[float] = None,
                          *, seed: Optional[int] = None) -> None:
        """重设键强分布（标定用；复现同一随机流）。"""
        self.bond_p.sigma_median = float(sigma_median)
        if weibull_m is not None:
            self.bond_p.weibull_m = float(weibull_m)
        rng = np.random.default_rng(seed) if seed is not None else self.rng
        u = rng.random(self.bi.size)
        scale = (math.log(2.0)) ** (-1.0 / self.bond_p.weibull_m)
        self.sigma_b = (self.bond_p.sigma_median * scale
                        * (-np.log(1.0 - u)) ** (1.0 / self.bond_p.weibull_m))
        self.tau_b = self.bond_p.tau_coeff * self.sigma_b

    def reset_bonds(self) -> None:
        """复位键状态（可反复加载同一样本；不重置键强分布）。"""
        self.alive[:] = True
        self.Fn[:] = 0.0
        self.Ft[:] = 0.0
        self.M[:] = 0.0
        self.n_broken = 0
        self.break_log = []

    def axial_stress(self, loads: Dict[str, float], label: str, width: float) -> float:
        return float(loads.get(label, 0.0)) / (width * self.t)

    def strain_fit(self, axis: int, region: Optional[slice] = None) -> np.ndarray:
        """最小二乘拟合位移梯度：返回 2×2 张量 [[du_x/dx, du_x/dy],[du_y/dx, du_y/dy]]。"""
        p = self.pos if region is None else self.pos[region]
        u = self.vel * 0.0
        return u  # 占位（位移场见 DisplacementRecorder）


class DisplacementRecorder:
    """记录初始位置，用当前位形反算位移场与应变（用于 E / ν 量测）。"""

    def __init__(self, sim: IceDEM):
        self.x0 = sim.pos.copy()

    def strains(self, sim: IceDEM, mask: Optional[np.ndarray] = None) -> Dict[str, float]:
        u = sim.pos - self.x0
        x = self.x0
        if mask is None:
            mask = np.ones(sim.n, dtype=bool)
        X, Y = x[mask, 0], x[mask, 1]
        ux, uy = u[mask, 0], u[mask, 1]
        # 线性拟合 u = a + b·(x,y)
        A = np.stack([np.ones_like(X), X - X.mean(), Y - Y.mean()], axis=1)
        cx, *_ = np.linalg.lstsq(A, ux, rcond=None)
        cy, *_ = np.linalg.lstsq(A, uy, rcond=None)
        return {"du_x_dx": float(cx[1]), "du_x_dy": float(cx[2]),
                "du_y_dx": float(cy[1]), "du_y_dy": float(cy[2])}


# ---------------------------------------------------------------------- 生成器
def build_lattice(
    nx: int,
    ny: int,
    radius: float,
    *,
    jitter: float = 0.0,
    seed: int = 0,
    staggered: bool = True,
) -> Tuple[np.ndarray, np.ndarray]:
    """三角格点集合体（可加位置抖动）。返回 (pos, radius)。"""
    rng = np.random.default_rng(seed)
    sp = 2.0 * radius
    pos = []
    for jy in range(ny):
        for ix in range(nx):
            x = ix * sp + (0.5 * sp if (staggered and jy % 2) else 0.0)
            y = jy * sp * math.sqrt(3.0) / 2.0
            pos.append((x, y))
    pos = np.array(pos, dtype=float)
    if jitter > 0:
        pos += jitter * sp * (rng.random(pos.shape) - 0.5)
    rad = radius * (1.0 + 0.05 * (rng.random(pos.shape[0]) - 0.5))
    return pos, rad


def build_disc(n_rings: int, radius: float, *, jitter: float = 0.0, seed: int = 0):
    """圆形试样（巴西劈裂用）：三角格点填充圆盘后裁剪。"""
    sp = 2.0 * radius
    R = n_rings * sp
    pos, rad = build_lattice(2 * n_rings + 4, 2 * n_rings + 4, radius, jitter=jitter, seed=seed)
    c = pos.mean(axis=0)
    keep = np.linalg.norm(pos - c, axis=1) <= R
    return pos[keep] - c + np.array([R, R]), rad[keep]


def build_beam(nx: int, ny: int, radius: float, *, jitter: float = 0.0, seed: int = 0):
    """梁试样（三点弯用）。"""
    return build_lattice(nx, ny, radius, jitter=jitter, seed=seed)


def add_box_walls(sim: IceDEM, *, top_vel: float = 0.0, bottom_vel: float = 0.0,
                  side: bool = False, kn: Optional[float] = None,
                  span: Optional[Tuple[float, float, str]] = None):
    """给集合体加上下（可选左右）刚性墙。``span`` 为 (lo, hi, axis) 限定接触区。"""
    if kn is None:
        kn = 2.0 * sim._kn_typ  # 与颗粒刚度同量级：避免墙刚度把步长压死
    # 墙紧贴试样实际外表面（零初始间隙）：用各方向的真实极值，避免「空行程」
    xmin = float((sim.pos[:, 0] - sim.r).min())
    xmax = float((sim.pos[:, 0] + sim.r).max())
    ymin = float((sim.pos[:, 1] - sim.r).min())
    ymax = float((sim.pos[:, 1] + sim.r).max())
    span_top = span_bottom = None
    if span is not None:
        lo, hi, which = span
        if which in ("top", "both"):
            span_top = (lo, hi)
        if which in ("bottom", "both"):
            span_bottom = (lo, hi)
    sim.walls.append(Wall(normal=(0.0, 1.0), coord=ymin, velocity=bottom_vel, kn=kn,
                          span=span_bottom, label="bottom"))
    sim.walls.append(Wall(normal=(0.0, -1.0), coord=-ymax, velocity=top_vel, kn=kn,
                          span=span_top, label="top"))
    if side:
        sim.walls.append(Wall(normal=(1.0, 0.0), coord=xmin, kn=kn, label="left"))
        sim.walls.append(Wall(normal=(-1.0, 0.0), coord=-xmax, kn=kn, label="right"))
    sim.refresh_dt()
    return sim