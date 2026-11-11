"""2D 不可压 Navier-Stokes + Smagorinsky SGS —— 真实 LES 求解器。

方法
----
- 通道流：x 方向周期，y 方向无滑移壁（y=0, y=Ly）。
- 时间推进：显式对流（中心差分）+ 显式扩散（nu + nu_t）+ 投影法（Poisson）。
- Smagorinsky 亚格子模型：nu_t = (C_s * dx)^2 * |S|，|S| = sqrt(2 S_ij S_ij)。
- Poisson 求解：解 ∇²p = rhs，x 方向 FFT（d²/dx² → -k²）+ y 方向 Neumann
  三对角（幽灵点 p[-1]=p[1]），k=0 模取零均值。

验证锚（判据 a）：
- 层流 Poiseuille（Re=100）：u(y) = 1.5 U_mean (1 - (2y/Ly - 1)^2)，
  壁面摩擦 C_D = 12/Re（以 U_mean 为参考速度）。
"""

from __future__ import annotations

import math

import numpy as np


def _poisson_solve(rhs: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """解 ∇²p = rhs：x 周期，y 方向 Neumann（零梯度）。返回零均值 p。

    分离变量 p = Σ_k p_k(y) e^{ikx}；对每个 k 解 1D 三对角
    (d²/dy² - k²) p_k = rhs_k，Neumann 端点用镜像幽灵点。
    k=0 模奇异（常数零空间）：取零均值 + 末行固定 p_k[ny-1]=0 定标。
    """
    nx, ny = rhs.shape
    rhs_k = np.fft.fft(rhs, axis=0)
    p_k = np.zeros_like(rhs_k)
    for n in range(nx):
        k = 2.0 * math.pi * n / (nx * dx)
        A = np.zeros((ny, ny), dtype=complex)
        for j in range(ny):
            A[j, j] = -2.0 / dy**2 - k**2
            if j > 0:
                A[j, j - 1] = 1.0 / dy**2
            if j < ny - 1:
                A[j, j + 1] = 1.0 / dy**2
        # Neumann 端点（幽灵点 p[-1]=p[1]）：j=0 行次对角 2/dy²
        A[0, 1] = 2.0 / dy**2
        A[ny - 1, ny - 2] = 2.0 / dy**2
        if n == 0:
            # 定标：p_0[ny-1] = 0
            A[-1, :] = 0.0
            A[-1, -1] = 1.0
            rhs_k[n, -1] = 0.0
        p_k[n] = np.linalg.solve(A, rhs_k[n])
    p = np.fft.ifft(p_k, axis=0).real
    return p - p.mean()


class LESChannel:
    """2D 通道 LES 求解器（Smagorinsky）。"""

    def __init__(
        self,
        Lx: float = 8.0,
        Ly: float = 1.0,
        nx: int = 32,
        ny: int = 16,
        nu: float = 1.0,
        C_s: float = 0.1,
        U_mean: float = 1.0,
        body_force_x: float = 0.0,
    ) -> None:
        if nx < 4 or ny < 4:
            raise ValueError("nx, ny >= 4")
        if nu < 0 or U_mean <= 0:
            raise ValueError("nu>=0, U_mean>0")
        self.Lx, self.Ly = Lx, Ly
        self.nx, self.ny = nx, ny
        self.dx = Lx / nx
        self.dy = Ly / ny
        self.nu = nu
        self.C_s = C_s
        self.U_mean = U_mean
        self.body_force_x = body_force_x
        self.x = (np.arange(nx) + 0.5) * self.dx
        self.y = (np.arange(ny) + 0.5) * self.dy
        self.u = np.full((nx, ny), U_mean)
        self.v = np.zeros((nx, ny))
        self.nu_t = np.zeros((nx, ny))
        self.t = 0.0

    def _grad(self, f: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        fx = (np.roll(f, -1, axis=0) - np.roll(f, 1, axis=0)) / (2.0 * self.dx)
        fy = np.zeros_like(f)
        fy[:, 1:-1] = (f[:, 2:] - f[:, :-2]) / (2.0 * self.dy)
        fy[:, 0] = (f[:, 1] - f[:, 0]) / self.dy
        fy[:, -1] = (f[:, -1] - f[:, -2]) / self.dy
        return fx, fy

    def _lap(self, f: np.ndarray) -> np.ndarray:
        d2x = (np.roll(f, -1, axis=0) - 2.0 * f + np.roll(f, 1, axis=0)) / self.dx**2
        d2y = np.zeros_like(f)
        d2y[:, 1:-1] = (f[:, 2:] - 2.0 * f[:, 1:-1] + f[:, :-2]) / self.dy**2
        d2y[:, 0] = (f[:, 1] - f[:, 0]) / self.dy**2
        d2y[:, -1] = (f[:, -1] - f[:, -2]) / self.dy**2
        return d2x + d2y

    def _smagorinsky(self) -> np.ndarray:
        du_dx, du_dy = self._grad(self.u)
        dv_dx, dv_dy = self._grad(self.v)
        S11, S22 = du_dx, dv_dy
        S12 = 0.5 * (du_dy + dv_dx)
        S = math.sqrt(2.0) * np.sqrt(S11**2 + S22**2 + 2.0 * S12**2)
        return (self.C_s * self.dx) ** 2 * S

    def step(self, dt: float) -> None:
        if dt <= 0:
            raise ValueError("dt>0")
        nu_t = self._smagorinsky()
        self.nu_t = nu_t
        nu_eff = self.nu + nu_t
        du_dx, du_dy = self._grad(self.u)
        dv_dx, dv_dy = self._grad(self.v)
        u_star = self.u - dt * (self.u * du_dx + self.v * du_dy) + dt * nu_eff * self._lap(self.u) + dt * self.body_force_x
        v_star = self.v - dt * (self.u * dv_dx + self.v * dv_dy) + dt * nu_eff * self._lap(self.v)
        # 投影：解 ∇²p = div/dt，u = u* - dt ∇p（y 方向用壁面差分，非周期）
        du_dx, _ = self._grad(u_star)
        _, dv_dy = self._grad(v_star)
        div = du_dx + dv_dy
        p = _poisson_solve(div / dt, self.dx, self.dy)
        dp_dx, dp_dy = self._grad(p)
        self.u = u_star - dt * dp_dx
        self.v = v_star - dt * dp_dy
        # 无滑移壁
        self.u[:, 0] = 0.0
        self.u[:, -1] = 0.0
        self.v[:, 0] = 0.0
        self.v[:, -1] = 0.0
        self.t += dt

    def run(self, t_end: float, dt: float) -> None:
        n = int(round(t_end / dt))
        for _ in range(n):
            self.step(dt)

    def wall_shear(self) -> float:
        """壁面摩擦应力 tau_w = nu |du/dy|_wall（取两壁幅值平均）。"""
        bot = self.nu * (self.u[:, 1] - self.u[:, 0]) / self.dy
        top = self.nu * (self.u[:, -2] - self.u[:, -1]) / self.dy
        return 0.5 * (bot.mean() + top.mean())

    def drag_coefficient(self) -> float:
        """C_D = tau_w / (0.5 rho U_mean^2)，rho=1（无量纲）。"""
        return self.wall_shear() / (0.5 * self.U_mean**2)

    def mean_velocity(self) -> float:
        return self.u.mean()


def poiseuille_profile(y: np.ndarray, Ly: float, U_mean: float) -> np.ndarray:
    """层流 Poiseuille 解析解 u(y) = 1.5 U_mean (1 - (2y/Ly - 1)^2)。"""
    return 1.5 * U_mean * (1.0 - (2.0 * y / Ly - 1.0) ** 2)


def poiseuille_C_D(Re: float) -> float:
    """层流通道 C_D = 12/Re（参考速度 U_mean，rho=1）。"""
    if Re <= 0:
        raise ValueError("Re>0")
    return 12.0 / Re

class LESChannel1D:
    """1D 通道 LES 求解器（Smagorinsky）—— 判据 a 的 C_D 计算。

    动量方程：du/dt = (nu + nu_t) d²u/dy² + f_x，nu_t = (C_s dy)² |du/dy|。
    无滑移壁 u[0]=u[ny-1]=0。稳态层流解 u(y) = (f_x/2nu) y(Ly-y)，C_D = 12/Re。
    """

    def __init__(
        self,
        Ly: float = 1.0,
        ny: int = 64,
        nu: float = 0.01,
        C_s: float = 0.1,
        U_mean: float = 1.0,
        body_force_x: float = 0.0,
    ) -> None:
        if ny < 4:
            raise ValueError("ny >= 4")
        if nu < 0 or U_mean <= 0:
            raise ValueError("nu>=0, U_mean>0")
        self.Ly = Ly
        self.ny = ny
        self.dy = Ly / ny
        self.nu = nu
        self.C_s = C_s
        self.U_mean = U_mean
        self.body_force_x = body_force_x
        self.y = (np.arange(ny) + 0.5) * self.dy
        self.u = np.zeros(ny)
        self.nu_t = np.zeros(ny)
        self.t = 0.0

    def _lap(self, u: np.ndarray) -> np.ndarray:
        d2 = np.zeros_like(u)
        d2[1:-1] = (u[2:] - 2.0 * u[1:-1] + u[:-2]) / self.dy**2
        d2[0] = (u[1] - u[0]) / self.dy**2
        d2[-1] = (u[-1] - u[-2]) / self.dy**2
        return d2

    def _grad(self, u: np.ndarray) -> np.ndarray:
        g = np.zeros_like(u)
        g[1:-1] = (u[2:] - u[:-2]) / (2.0 * self.dy)
        g[0] = (u[1] - u[0]) / self.dy
        g[-1] = (u[-1] - u[-2]) / self.dy
        return g

    def step(self, dt: float) -> None:
        if dt <= 0:
            raise ValueError("dt>0")
        du_dy = self._grad(self.u)
        self.nu_t = (self.C_s * self.dy) ** 2 * np.abs(du_dy)
        nu_eff = self.nu + self.nu_t
        du_dt = nu_eff * self._lap(self.u) + self.body_force_x
        self.u += dt * du_dt
        self.u[0] = 0.0
        self.u[-1] = 0.0
        self.t += dt

    def run(self, t_end: float, dt: float) -> None:
        n = int(round(t_end / dt))
        for _ in range(n):
            self.step(dt)

    def mean_velocity(self) -> float:
        return float(self.u.mean())

    def wall_shear(self) -> float:
        bot = self.nu * (self.u[1] - self.u[0]) / self.dy
        top = self.nu * (self.u[-2] - self.u[-1]) / self.dy
        return 0.5 * (abs(bot) + abs(top))

    def drag_coefficient(self) -> float:
        return self.wall_shear() / (0.5 * self.U_mean**2)
