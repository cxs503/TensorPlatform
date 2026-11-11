"""DEM 多面体（四面体）刚体颗粒：波浪载荷下运动响应 + 多面体接触检测。

物理
----
- 刚体状态：位置 r、四元数姿态 q、线速度 v、角速度 omega。
- 受力：重力 -m g z、浮力 rho_w g V_sub z（线性化浸没分数）、
  波浪拖曳（线性化 Morison：F_D = -b (v - u_f)）。
- 解析锚（判据 b）：中性浮力 + 线性拖曳 + 振荡流 u_f(t) = U0 cos(omega t - phi)
  的闭式解（一阶线性 ODE）::

      v(t) = U0 [ (cos(omega t - phi) + omega tau sin(omega t - phi))
                  / (1 + omega^2 tau^2) - e^{-t/tau} / (1 + omega^2 tau^2) ]

  其中 tau = m/b。Airy 波在固定 (x0, z0) 处的轨道速度恰为同型余弦
  （u = U0 sin(kx - omega t)），故该锚直接适用于波浪载荷工况。

接触检测：两四面体分离轴定理（SAT），8 条轴（各 4 个面法向）。
"""

from __future__ import annotations

import math

import numpy as np

G = 9.81
RHO_WATER = 1025.0


class Tetrahedron:
    """均匀四面体（4 顶点）。"""

    def __init__(self, verts: np.ndarray) -> None:
        v = np.asarray(verts, dtype=float)
        if v.shape != (4, 3):
            raise ValueError("verts 形状必须 (4, 3)")
        self.verts = v
        self.centroid = v.mean(axis=0)
        self.volume = abs(np.linalg.det(v[1:] - v[0])) / 6.0
        if self.volume <= 0:
            raise ValueError("退化四面体（体积为零）")

    def face_normals(self) -> np.ndarray:
        """4 个面法向（单位化，朝外）。"""
        v = self.verts
        idx = [(0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)]
        out = []
        for i, j, k in idx:
            n = np.cross(v[j] - v[i], v[k] - v[i])
            n = n / np.linalg.norm(n)
            c = (v[i] + v[j] + v[k]) / 3.0
            if np.dot(n, self.centroid - c) > 0:
                n = -n
            out.append(n)
        return np.array(out)

    def inertia_tensor(self, mass: float) -> np.ndarray:
        """均匀四面体绕质心的惯性张量。

        I = (m/20) Σ_i [ (v_i·v_i) I_3 - v_i v_i^T ]，v_i 为相对质心的顶点向量。
        """
        w = self.verts - self.centroid
        S = np.zeros((3, 3))
        for i in range(4):
            S += (w[i] @ w[i]) * np.eye(3) - np.outer(w[i], w[i])
        return mass / 20.0 * S


def _quat_rotate(q: np.ndarray, vec: np.ndarray) -> np.ndarray:
    """四元数旋转（q = [w, x, y, z]）。"""
    w, x, y, z = q
    vx, vy, vz = vec
    t = np.array([
        2.0 * (y * vz - z * vy) + vx,
        2.0 * (z * vx - x * vz) + vy,
        2.0 * (x * vy - y * vx) + vz,
    ])
    t2 = np.array([
        2.0 * (y * vx - z * vy) + w * vx,
        2.0 * (z * vx - x * vz) + w * vy,
        2.0 * (x * vz - y * vx) + w * vz,
    ])
    return w * t + np.cross(np.array([x, y, z]), t2) + x * vx + y * vy + z * vz


def _quat_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return np.array([
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ])


class PolyParticle:
    """多面体（四面体）刚体颗粒。

    受力模型（判据 b 工况）：
      F = -m g z + rho_w g V_sub z - b (v - u_f)
    中性浮力（rho_body = rho_w）时重力与浮力抵消。
    """

    def __init__(
        self,
        verts: np.ndarray,
        mass: float,
        drag_coeff: float = 1.0,
        rho_body: float = 1025.0,
    ) -> None:
        if mass <= 0:
            raise ValueError("mass>0")
        if drag_coeff < 0:
            raise ValueError("drag_coeff>=0")
        self.tet = Tetrahedron(verts)
        self.mass = mass
        self.b = drag_coeff
        self.rho_body = rho_body
        self.I = self.tet.inertia_tensor(mass)
        self.r = self.tet.centroid.copy()
        self.q = np.array([1.0, 0.0, 0.0, 0.0])
        self.v = np.zeros(3)
        self.omega = np.zeros(3)
        self.t = 0.0

    def local_verts(self) -> np.ndarray:
        return self.tet.verts - self.tet.centroid

    def world_verts(self) -> np.ndarray:
        return self.r + np.array([_quat_rotate(self.q, w) for w in self.local_verts()])

    def submerged_fraction(self, free_surface: float = 0.0) -> float:
        """线性化浸没分数：质心在自由面下 => 1，上 => 0（小颗粒近似）。"""
        d = self.tet.volume ** (1.0 / 3.0)
        return float(np.clip(0.5 - (self.r[2] - free_surface) / (2.0 * d), 0.0, 1.0))

    def forces(self, u_f: np.ndarray, free_surface: float = 0.0) -> np.ndarray:
        """合力（N）。u_f: 流体速度（颗粒位置处）。"""
        F = -self.mass * G * np.array([0.0, 0.0, 1.0])
        V_sub = self.tet.volume * self.submerged_fraction(free_surface)
        F = F + RHO_WATER * G * V_sub * np.array([0.0, 0.0, 1.0])
        F = F - self.b * (self.v - u_f)
        return F

    def step(self, dt: float, u_f: np.ndarray, free_surface: float = 0.0) -> None:
        """RK4 一步（u_f 视为给定场，时间插值由调用方处理）。"""
        if dt <= 0:
            raise ValueError("dt>0")

        def deriv(state: np.ndarray) -> np.ndarray:
            r, q, v, om = state[:3], state[3:7], state[7:10], state[10:13]
            d = self.tet.volume ** (1.0 / 3.0)
            fsub = float(np.clip(0.5 - (r[2] - free_surface) / (2.0 * d), 0.0, 1.0))
            F = -self.mass * G * np.array([0.0, 0.0, 1.0])
            F = F + RHO_WATER * G * self.tet.volume * fsub * np.array([0.0, 0.0, 1.0])
            F = F - self.b * (v - u_f)
            a = F / self.mass
            dq = 0.5 * _quat_mul(q, np.array([0.0, om[0], om[1], om[2]]))
            return np.concatenate([v, dq, a, np.zeros(3)])

        state = np.concatenate([self.r, self.q, self.v, self.omega])
        k1 = deriv(state)
        k2 = deriv(state + 0.5 * dt * k1)
        k3 = deriv(state + 0.5 * dt * k2)
        k4 = deriv(state + dt * k3)
        ns = state + dt * (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0
        self.r, self.q, self.v, self.omega = ns[:3], ns[3:7], ns[7:10], ns[10:13]
        self.q = self.q / np.linalg.norm(self.q)
        self.t += dt


def analytic_drag_response(
    t: float, U0: float, omega: float, tau: float, phi: float = 0.0
) -> float:
    """线性拖曳 + 振荡流 u_f = U0 cos(omega t - phi) 的闭式速度响应（v(0)=0）。

    dv/dt + v/tau = (U0/tau) cos(omega t - phi) 的解析解。
    """
    if tau <= 0:
        raise ValueError("tau>0")
    D = 1.0 + (omega * tau) ** 2
    th = omega * t - phi
    return U0 * (math.cos(th) + omega * tau * math.sin(th)) / D - U0 * math.exp(
        -t / tau
    ) / D


def tetra_contact(a: Tetrahedron, b: Tetrahedron) -> tuple[bool, float]:
    """两四面体 SAT 接触检测。返回 (contact, min_gap)。

    轴 = 双方 8 个面法向；任一轴上投影区间分离 => 无接触。
    min_gap = 所有轴上最大分离间隙（>0 分离，<=0 接触/穿透）。
    """
    axes = np.vstack([a.face_normals(), b.face_normals()])
    va, vb = a.verts, b.verts
    min_gap = -np.inf
    for ax in axes:
        pa = va @ ax
        pb = vb @ ax
        gap_sep = max(pa.min(), pb.min()) - min(pa.max(), pb.max())
        min_gap = max(min_gap, gap_sep)
    return min_gap <= 1e-9, float(min_gap)
