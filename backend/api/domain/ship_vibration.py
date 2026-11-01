"""船舶振动噪声域（vibration & noise）——解析级参考实现与端点。

背景（为什么有这个模块）
------------------------
平台 21 项能力清单与本仓 ``backend/api/domain/*`` 均无振动噪声域：对
``/api/v1/calculations/{vibration,noise,...}`` 8 条候选路径实测 404，而控制组
``calculations/{stability,resistance}`` 返回 405（路由存在、方法不允许），
说明 404 是「路由真的没有」而非探针失效 —— 该域此前为 **NOT_SUPPORTED**。

本模块以**闭合解析解**（Euler–Bernoulli 梁、Kirchhoff 薄板、单自由度隔振、
质量定律隔声、IEC 61672 A 计权）给出可机器判定证据等级为 REAL 的参考实现，
并配套两个**独立**交叉验证工具：

* ``beam_fem_frequencies``  —— Hermite 三次单元一致质量有限元，与解析特征值
  比对，给出真实的离散误差（不是自证）；
* ``plate_rayleigh_frequency`` —— Rayleigh 商 + 数值积分，独立复算 Navier 闭式解。

单位约定：全 SI（m, kg, s, N, Pa, Hz, dB）。长度别混 mm。

精度纪律：本模块所有函数都有解析/文献基准，测试目标 <3%（多数 <0.1%），
无法给出基准的量（如实际船体阻尼、设备源级）**不编造**，见 ``docs/audits/``。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(tags=["船舶振动噪声"])

AIR_RHO = 1.225      # kg/m^3, 20 degC 标准空气密度
AIR_C = 343.0        # m/s, 20 degC 声速
G = 9.80665


# ===========================================================================
# 1. Euler–Bernoulli 梁：无量纲特征值 lambda_n
# ===========================================================================
def _beam_char(bc: str):
    """返回 (特征函数 g(lambda), lambda 扫描上限系数)。

    用「乘开」形式避免 tan 的极点，保证扫符号时函数连续：
      C-C / F-F : cos(l)cosh(l) - 1 = 0
      C-F       : cos(l)cosh(l) + 1 = 0
      C-P       : sin(l)cosh(l) - cos(l)sinh(l) = 0   （等价 tan l = tanh l）
    """
    bc = bc.lower()
    if bc in ("clamped-clamped", "cc", "fixed-fixed"):
        return (lambda x: np.cos(x) * np.cosh(x) - 1.0), "cc"
    if bc in ("clamped-free", "cf", "cantilever", "fixed-free"):
        return (lambda x: np.cos(x) * np.cosh(x) + 1.0), "cf"
    if bc in ("clamped-pinned", "cp", "fixed-pinned", "propped"):
        return (lambda x: np.sin(x) * np.cosh(x) - np.cos(x) * np.sinh(x)), "cp"
    if bc in ("free-free", "ff"):
        return (lambda x: np.cos(x) * np.cosh(x) - 1.0), "ff"
    raise ValueError(f"unsupported bc: {bc!r}")


def beam_eigenvalues(bc: str, n_modes: int = 5, tol: float = 1e-12) -> List[float]:
    """Euler–Bernoulli 均匀梁的无量纲特征值 lambda_n（升序）。

    pinned-pinned 取解析 lambda_n = n*pi；其余三个 BC 用**扫符号 + 二分**求根
    （不查表、不硬编码），因此可与 Blevins 表值做独立比对。
    """
    if n_modes < 1:
        raise ValueError("n_modes must be >= 1")
    bc_l = bc.lower()
    if bc_l in ("pinned-pinned", "pp", "simply-supported", "ss"):
        return [float(n * math.pi) for n in range(1, n_modes + 1)]

    g, kind = _beam_char(bc)
    x_max = (n_modes + 3) * math.pi
    # 扫描起点取 0.5：cos(x)cosh(x)-1 在 x->0 处因浮点相消会返回**精确 0**
    # （1 - x^4/6 与 1 不可区分），若从 1e-6 起步会误采一个伪刚体根。
    # 所有受支持 BC 的首个非零根均 > 1.87，故 0.5 起步安全。
    xs = np.arange(0.5, x_max, 5e-3)
    vals = g(xs)
    roots: List[float] = []
    for i in range(len(xs) - 1):
        a, b = xs[i], xs[i + 1]
        fa, fb = vals[i], vals[i + 1]
        if fa * fb > 0 or fa == 0.0:
            continue
        lo, hi, flo = float(a), float(b), float(fa)
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            fm = float(g(mid))
            if flo * fm <= 0:
                hi = mid
            else:
                lo, flo = mid, fm
            if hi - lo < tol:
                break
        root = 0.5 * (lo + hi)
        if root < 1.0:      # 刚体模态守卫（不应出现，防御性）
            continue
        roots.append(root)
    # free-free：前两阶为刚体模态（lambda=0），已由扫描起点排除
    if len(roots) < n_modes:
        raise RuntimeError(f"root finding failed for bc={bc}: got {len(roots)}")
    return roots[:n_modes]


def beam_natural_frequencies(
    bc: str,
    E: float,
    I: float,
    rho: float,
    A: float,
    L: float,
    n_modes: int = 5,
) -> List[float]:
    """梁横向弯曲固有频率 f_n = lambda_n^2/(2*pi*L^2) * sqrt(EI/(rho*A))  [Hz]."""
    for name, v in (("E", E), ("I", I), ("rho", rho), ("A", A), ("L", L)):
        if not (isinstance(v, (int, float)) and v > 0):
            raise ValueError(f"{name} must be > 0, got {v!r}")
    lams = beam_eigenvalues(bc, n_modes)
    c = math.sqrt(E * I / (rho * A)) / (2.0 * math.pi * L * L)
    return [float(lam * lam * c) for lam in lams]


def beam_fem_frequencies(
    bc: str,
    E: float,
    I: float,
    rho: float,
    A: float,
    L: float,
    n_elem: int = 20,
    n_modes: int = 5,
) -> List[float]:
    """Hermite 三次单元 + 一致质量矩阵的有限元固有频率 [Hz]（独立交叉验证用）。

    单元矩阵（标准 Euler–Bernoulli 梁单元，自由度 [w1, th1, w2, th2]）::

        Ke = EI/l^3 * [[12, 6l, -12, 6l], [6l, 4l^2, -6l, 2l^2],
                       [-12, -6l, 12, -6l], [6l, 2l^2, -6l, 4l^2]]
        Me = rho*A*l/420 * [[156, 22l, 54, -13l], [22l, 4l^2, 13l, -3l^2],
                            [54, 13l, 156, -22l], [-13l, -3l^2, -22l, 4l^2]]

    BC：C-C/C-P 约束两端 w、C-C 另约束两端 theta；C-F 只约束 x=0 的 w 与 theta。
    free-free 含两个刚体模态，本函数不支持（抛 ValueError），避免给出可疑数。
    """
    bc_l = bc.lower()
    if n_elem < 2:
        raise ValueError("n_elem must be >= 2")
    ndof = 2 * (n_elem + 1)
    l = L / n_elem
    ke = (E * I / l ** 3) * np.array(
        [[12, 6 * l, -12, 6 * l],
         [6 * l, 4 * l * l, -6 * l, 2 * l * l],
         [-12, -6 * l, 12, -6 * l],
         [6 * l, 2 * l * l, -6 * l, 4 * l * l]], dtype=float)
    me = (rho * A * l / 420.0) * np.array(
        [[156, 22 * l, 54, -13 * l],
         [22 * l, 4 * l * l, 13 * l, -3 * l * l],
         [54, 13 * l, 156, -22 * l],
         [-13 * l, -3 * l * l, -22 * l, 4 * l * l]], dtype=float)
    K = np.zeros((ndof, ndof))
    M = np.zeros((ndof, ndof))
    for e in range(n_elem):
        d = [2 * e, 2 * e + 1, 2 * e + 2, 2 * e + 3]
        K[np.ix_(d, d)] += ke
        M[np.ix_(d, d)] += me

    if bc_l in ("clamped-clamped", "cc", "fixed-fixed"):
        fixed = [0, 1, ndof - 2, ndof - 1]
    elif bc_l in ("clamped-pinned", "cp", "fixed-pinned", "propped"):
        fixed = [0, 1, ndof - 2]
    elif bc_l in ("clamped-free", "cf", "cantilever", "fixed-free"):
        fixed = [0, 1]
    elif bc_l in ("pinned-pinned", "pp", "simply-supported", "ss"):
        fixed = [0, ndof - 2]
    elif bc_l in ("free-free", "ff"):
        raise ValueError("free-free has rigid-body modes; FEM check not supported")
    else:
        raise ValueError(f"unsupported bc: {bc!r}")

    free = [i for i in range(ndof) if i not in fixed]
    Kf = K[np.ix_(free, free)]
    Mf = M[np.ix_(free, free)]
    # 对称正定质量阵 -> Cholesky 变换成标准特征值问题，只用 numpy（免 scipy/BLAS 依赖）
    Lc = np.linalg.cholesky(Mf)
    Lc_inv = np.linalg.inv(Lc)
    A = Lc_inv @ Kf @ Lc_inv.T
    A = 0.5 * (A + A.T)                      # 去对称化舍入
    w2 = np.clip(np.linalg.eigh(A)[0], 0.0, None)
    f = np.sqrt(w2) / (2.0 * math.pi)
    return [float(x) for x in f[:n_modes]]


def beam_mode_shape(
    bc: str, n: int, L: float, n_points: int = 101
) -> Dict[str, List[float]]:
    """第 n 阶（1-based）梁振型闭式表达式，返回 {"x": [...], "w": [...]}（w 已归一化 max|w|=1）。

    C-C : W = cosh(l x/L) - cos(l x/L) - s (sinh(l x/L) - sin(l x/L)),
          s = (cosh l - cos l)/(sinh l - sin l)
    C-F : W = cosh - cos - s (sinh - sin), s = (cosh l + cos l)/(sinh l + sin l)
    C-P : W = cosh - cos - s (sinh - sin), s = (cosh l - cos l)/(sinh l - sin l)  （同 C-C 型，lambda 不同）
    P-P : W = sin(l x/L)
    """
    lams = beam_eigenvalues(bc, n)
    lam = lams[n - 1]
    xs = np.linspace(0.0, L, n_points)
    u = xs / L
    bc_l = bc.lower()
    if bc_l in ("pinned-pinned", "pp", "simply-supported", "ss"):
        w = np.sin(lam * u)
    else:
        ch, co = np.cosh(lam * u), np.cos(lam * u)
        sh, si = np.sinh(lam * u), np.sin(lam * u)
        if bc_l in ("clamped-clamped", "cc", "fixed-fixed", "free-free", "ff"):
            s = (math.cosh(lam) - math.cos(lam)) / (math.sinh(lam) - math.sin(lam))
        elif bc_l in ("clamped-free", "cf", "cantilever", "fixed-free"):
            s = (math.cosh(lam) + math.cos(lam)) / (math.sinh(lam) + math.sin(lam))
        elif bc_l in ("clamped-pinned", "cp", "fixed-pinned", "propped"):
            s = (math.cosh(lam) - math.cos(lam)) / (math.sinh(lam) - math.sin(lam))
        else:
            raise ValueError(f"unsupported bc: {bc!r}")
        w = (ch - co) - s * (sh - si)
    m = float(np.max(np.abs(w))) or 1.0
    return {"x": xs.tolist(), "w": (w / m).tolist()}


# ===========================================================================
# 2. 板：Kirchhoff 薄板 + Rayleigh 商独立复算
# ===========================================================================
def plate_rigidity(E: float, nu: float, h: float) -> float:
    """抗弯刚度 D = E h^3 / (12 (1 - nu^2))  [N m]."""
    if not (-0.99 < nu < 0.5):
        raise ValueError("nu out of physical range (-1, 0.5)")
    return E * h ** 3 / (12.0 * (1.0 - nu ** 2))


def plate_natural_frequency(
    m: int, n: int, a: float, b: float, E: float, nu: float, rho: float, h: float
) -> float:
    """四边简支（SSSS）矩形薄板 Helmholtz/Navier 闭式解 [Hz]。

        f_mn = (pi/2) * sqrt(D / (rho h)) * [(m/a)^2 + (n/b)^2]

    m 沿 a 方向半波数、n 沿 b 方向（均 1-based）。
    """
    if m < 1 or n < 1:
        raise ValueError("m, n must be >= 1")
    if a <= 0 or b <= 0:
        raise ValueError("a, b must be > 0")
    D = plate_rigidity(E, nu, h)
    return float((math.pi / 2.0) * math.sqrt(D / (rho * h)) * ((m / a) ** 2 + (n / b) ** 2))


def plate_rayleigh_frequency(
    m: int, n: int, a: float, b: float, E: float, nu: float, rho: float, h: float,
    n_grid: int = 200,
) -> float:
    """Rayleigh 商 + 数值积分独立复算 SSSS 板频率 [Hz]（不调用上面的闭式解）。

        omega^2 = D * int(int (w_xx + w_yy)^2) / (rho h * int(int w^2))
    w = sin(m pi x/a) sin(n pi y/b)。数值积分用 numpy 梯度 + 梯形法。
    """
    D = plate_rigidity(E, nu, h)
    x = np.linspace(0.0, a, n_grid)
    y = np.linspace(0.0, b, n_grid)
    X, Y = np.meshgrid(x, y, indexing="ij")
    W = np.sin(m * math.pi * X / a) * np.sin(n * math.pi * Y / b)
    dxx = np.gradient(np.gradient(W, x, axis=0), x, axis=0)
    dyy = np.gradient(np.gradient(W, y, axis=1), y, axis=1)
    lap = dxx + dyy
    _trapz = getattr(np, "trapezoid", None) or np.trapz   # numpy>=2 改名，兼容旧版
    num = D * float(_trapz(_trapz(lap ** 2, y, axis=1), x))
    den = rho * h * float(_trapz(_trapz(W ** 2, y, axis=1), x))
    return float(math.sqrt(num / den) / (2.0 * math.pi))


def plate_frequencies(
    a: float, b: float, E: float, nu: float, rho: float, h: float, n_max: int = 3
) -> List[Dict[str, float]]:
    """SSSS 板前若干阶频率，按频率升序返回 [{m, n, f_hz}, ...]."""
    out = []
    for m in range(1, n_max + 1):
        for n in range(1, n_max + 1):
            out.append({
                "m": m, "n": n,
                "f_hz": plate_natural_frequency(m, n, a, b, E, nu, rho, h),
            })
    out.sort(key=lambda r: r["f_hz"])
    return out


# ===========================================================================
# 3. 激励：螺旋桨叶频、轴频、柴油机点火频、共振裕度
# ===========================================================================
def blade_rate_orders(n_blades: int, rpm: float, n_harmonics: int = 3) -> Dict[str, object]:
    """螺旋桨激励频率。

    shaft frequency f_s = rpm/60 [Hz]；blade passing frequency BPF = n_blades * f_s；
    k 阶叶频 = k * BPF。返回轴频/叶频/各阶叶频（含"叶频"作为首要激励）。
    """
    if n_blades < 1:
        raise ValueError("n_blades must be >= 1")
    if rpm <= 0:
        raise ValueError("rpm must be > 0")
    f_s = rpm / 60.0
    bpf = n_blades * f_s
    return {
        "shaft_hz": f_s,
        "bpf_hz": bpf,
        "harmonics_hz": [bpf * k for k in range(1, max(1, n_harmonics) + 1)],
    }


def diesel_firing_frequencies(
    rpm: float, n_cylinders: int, stroke: int = 4, n_harmonics: int = 3
) -> Dict[str, object]:
    """柴油机气缸点火频率（firing frequency）。

    4 冲程：F = (n_cyl/2) * rpm/60；2 冲程：F = n_cyl * rpm/60。
    """
    if rpm <= 0 or n_cylinders < 1:
        raise ValueError("rpm>0 and n_cylinders>=1 required")
    if stroke not in (2, 4):
        raise ValueError("stroke must be 2 or 4")
    f_rev = rpm / 60.0
    f_fire = (n_cylinders / 2.0 if stroke == 4 else float(n_cylinders)) * f_rev
    return {
        "rev_hz": f_rev,
        "firing_hz": f_fire,
        "harmonics_hz": [f_fire * k for k in range(1, max(1, n_harmonics) + 1)],
    }


def resonance_margin(f_exc: float, f_nat: float, tol: float = 0.15) -> Dict[str, object]:
    """共振裕度：margin = (f_exc - f_nat)/f_nat。

    判据（工程惯例，非规范条文）：|margin| >= tol 视为安全分离；本平台取 15%，
    可在调用处改写。返回 passed + 建议（升/降速或改结构）。
    """
    if f_nat <= 0 or f_exc <= 0:
        raise ValueError("frequencies must be > 0")
    margin = (f_exc - f_nat) / f_nat
    passed = abs(margin) >= tol
    if passed:
        advice = "分离裕度满足要求"
    elif margin > 0:
        advice = "激励高于固有频率，需提高刚度/降低转速或加大分离"
    else:
        advice = "激励低于固有频率，需降刚度/升高转速或加大分离"
    return {
        "f_exc_hz": f_exc,
        "f_nat_hz": f_nat,
        "margin": margin,
        "margin_pct": margin * 100.0,
        "tol": tol,
        "passed": passed,
        "advice": advice,
    }


def resonance_screening(
    f_natural: Sequence[float],
    n_blades: int,
    rpm: float,
    n_cylinders: Optional[int] = None,
    stroke: int = 4,
    n_harmonics: int = 3,
    tol: float = 0.15,
) -> Dict[str, object]:
    """船体/上层建筑固有频率 vs 主要激励源的共振筛查（多对多）。

    输出每条 (激励, 固有频率) 组合的裕度与失败清单，供总体设计早期判据使用。
    """
    ex: List[Dict[str, object]] = []
    br = blade_rate_orders(n_blades, rpm, n_harmonics)
    for k, f in enumerate(br["harmonics_hz"], start=1):
        ex.append({"source": f"propeller {k}xBPF", "f_hz": f})
    ex.insert(0, {"source": "shaft 1x", "f_hz": br["shaft_hz"]})
    if n_cylinders:
        df = diesel_firing_frequencies(rpm, n_cylinders, stroke, n_harmonics)
        for k, f in enumerate(df["harmonics_hz"], start=1):
            ex.append({"source": f"diesel {k}x firing", "f_hz": f})

    rows = []
    fails = []
    worst = None
    for e in ex:
        for i, fn in enumerate(f_natural, start=1):
            r = resonance_margin(float(e["f_hz"]), float(fn), tol)
            row = {
                "excitation": e["source"],
                "f_exc_hz": e["f_hz"],
                "mode_index": i,
                "f_nat_hz": float(fn),
                "margin_pct": r["margin_pct"],
                "passed": r["passed"],
            }
            rows.append(row)
            if not r["passed"]:
                fails.append(row)
            if worst is None or abs(row["margin_pct"]) < abs(worst["margin_pct"]):
                worst = row
    return {
        "excitation_set": ex,
        "checks": rows,
        "n_checks": len(rows),
        "n_failed": len(fails),
        "failed": fails,
        "worst": worst,
        "tol": tol,
        "passed": len(fails) == 0,
    }


# ===========================================================================
# 4. 隔振：单自由度传递率
# ===========================================================================
def isolation_transmissibility(f: float, f_n: float, zeta: float = 0.05) -> Dict[str, object]:
    """基础激励单自由度隔振传递率。

        T = sqrt((1 + (2 zeta r)^2) / ((1 - r^2)^2 + (2 zeta r)^2)),  r = f/f_n

    解析自检：r = sqrt(2) 时 T = 1（与 zeta 无关）；r -> 0 时 T -> 1；
    r >> 1 时 T ~ 1/(r^2) 衰减。隔振生效判据 T < 1  <=>  r > sqrt(2)。
    """
    if f <= 0 or f_n <= 0:
        raise ValueError("f and f_n must be > 0")
    if not (0.0 <= zeta < 1.0):
        raise ValueError("zeta must be in [0, 1)")
    r = f / f_n
    num = 1.0 + (2.0 * zeta * r) ** 2
    den = (1.0 - r * r) ** 2 + (2.0 * zeta * r) ** 2
    if den <= 0.0:
        # 无阻尼共振（zeta=0 且 r=1）：传递率理论上无穷大，返回 inf 而非抛除零异常
        return {
            "freq_ratio": r,
            "transmissibility": math.inf,
            "isolation_db": math.inf,
            "isolated": False,
            "note": "无阻尼共振：T->inf，工程上必须有阻尼或避开该转速",
        }
    T = math.sqrt(num / den)
    return {
        "freq_ratio": r,
        "transmissibility": T,
        "isolation_db": 20.0 * math.log10(T) if T > 0 else float("-inf"),
        "isolated": T < 1.0,
        "note": "T<1 需 r>sqrt(2)=1.414" if T >= 1.0 else "隔振区",
    }


# ===========================================================================
# 5. 空气声：质量定律隔声量 / 吻合频率 / dB 叠加 / A 计权
# ===========================================================================
def panel_tl(
    f: float, surface_density: float, field_incidence: bool = True,
    rho0: float = AIR_RHO, c0: float = AIR_C,
) -> Dict[str, object]:
    """单层板隔声量 TL [dB]（质量定律）。

    正入射（精确）::

        tau = 1 / (1 + (omega m'' / (2 rho0 c0))^2),  TL_n = -10 log10(tau)

    场入射（工程经验，ISO 717 / 常规船体声学做法）::

        TL_f = TL_n - 5   (等价 TL_f ~ 20 log10(m'' f) - 47 dB)

    返回 TL、板的面质量、正入射/场入射两值、低频渐近式值，便于交叉比对。
    """
    if f <= 0 or surface_density <= 0:
        raise ValueError("f and surface_density must be > 0")
    omega = 2.0 * math.pi * f
    x = omega * surface_density / (2.0 * rho0 * c0)
    tl_n = 10.0 * math.log10(1.0 + x * x)
    tl_f = tl_n - 5.0
    return {
        "tl_normal_db": tl_n,
        "tl_field_db": tl_f if field_incidence else tl_n,
        "tl_asymptote_db": 20.0 * math.log10(surface_density * f) - 42.53,
        "mass_law_exponent": 6.0,
        "f_hz": f,
        "surface_density": surface_density,
    }


def coincidence_frequency(
    E: float, nu: float, rho: float, h: float, c0: float = AIR_C
) -> float:
    """单层板吻合（临界）频率 f_c = c0^2/(2 pi h) * sqrt(12 rho (1-nu^2)/E)  [Hz]。

    钢的经验校核：f_c[kHz] ≈ 12 / h[mm]（h=5mm -> 2.4kHz），本函数应复现该量级。
    """
    if h <= 0 or E <= 0 or rho <= 0:
        raise ValueError("h, E, rho must be > 0")
    return float(
        c0 ** 2 / (2.0 * math.pi * h)
        * math.sqrt(12.0 * rho * (1.0 - nu ** 2) / E)
    )


def combine_levels_db(levels: Sequence[float]) -> float:
    """不相干声源能量叠加：L = 10 log10(sum 10^(Li/10))。两个 60 dB -> 63.01 dB。"""
    lv = [float(x) for x in levels]
    if not lv:
        raise ValueError("levels must be non-empty")
    return float(10.0 * math.log10(sum(10.0 ** (x / 10.0) for x in lv)))


def required_tl(source_level: float, target_level: float) -> float:
    """由源级与目标级所需的隔声量 TL_req = L_src - L_tgt [dB]（能量口径）。"""
    return float(source_level - target_level)


def a_weighting(f: float) -> float:
    """IEC 61672-1 A 计权修正 [dB]（解析式，非查表）。

        R_A = (12194^2 f^4) / ((f^2+20.6^2) sqrt((f^2+107.7^2)(f^2+737.9^2)) (f^2+12194^2))
        A   = 20 log10(R_A) + 2.00
    """
    if f <= 0:
        raise ValueError("f must be > 0")
    f2 = f * f
    ra = (12194.0 ** 2 * f ** 4) / (
        (f2 + 20.6 ** 2)
        * math.sqrt((f2 + 107.7 ** 2) * (f2 + 737.9 ** 2))
        * (f2 + 12194.0 ** 2)
    )
    return float(20.0 * math.log10(ra) + 2.00)


def octave_band_levels(
    total_level: float, center_freqs: Sequence[float], weights: Optional[Sequence[float]] = None
) -> Dict[str, object]:
    """把总级按给定倍频程权重分配（权重归一化），并回算 A 计权总级。

    仅用于「已知总线谱形状」的筛选；无实测谱时不建议用它替代实测。
    """
    fc = [float(x) for x in center_freqs]
    w = [1.0 / len(fc)] * len(fc) if weights is None else [float(x) for x in weights]
    s = sum(w)
    if s <= 0:
        raise ValueError("weights must sum > 0")
    w = [x / s for x in w]
    levels = [total_level + 10.0 * math.log10(x) for x in w]
    a_levels = [lv + a_weighting(f) for lv, f in zip(levels, fc)]
    return {
        "center_freqs_hz": fc,
        "band_levels_db": levels,
        "band_levels_dba": a_levels,
        "total_dba": combine_levels_db(a_levels),
    }


# ===========================================================================
# 6. FastAPI 端点层
# ===========================================================================
class BeamRequest(BaseModel):
    bc: str = Field("clamped-clamped", description="clamped-clamped|cantilever|clamped-pinned|pinned-pinned")
    E: float = Field(2.1e11, gt=0, description="Young's modulus [Pa]")
    I: float = Field(1.6e-4, gt=0, description="second moment of area [m^4]")
    rho: float = Field(7850.0, gt=0, description="density [kg/m^3]")
    A: float = Field(0.02, gt=0, description="cross-section area [m^2]")
    L: float = Field(6.0, gt=0, description="span [m]")
    n_modes: int = Field(5, ge=1, le=20)
    fem_check: bool = Field(False, description="also run Hermite FEM cross-check")
    n_elem: int = Field(20, ge=2, le=400)


class PlateRequest(BaseModel):
    a: float = Field(3.0, gt=0)
    b: float = Field(2.0, gt=0)
    E: float = Field(2.1e11, gt=0)
    nu: float = Field(0.3, gt=-0.99, lt=0.5)
    rho: float = Field(7850.0, gt=0)
    h: float = Field(0.012, gt=0)
    n_max: int = Field(3, ge=1, le=8)
    rayleigh_check: bool = Field(True, description="independent Rayleigh-quotient recompute for mode (1,1)")


class ExcitationRequest(BaseModel):
    n_blades: int = Field(5, ge=1, le=12)
    rpm: float = Field(120.0, gt=0)
    n_cylinders: Optional[int] = Field(None, ge=1, le=24)
    stroke: int = Field(4)
    n_harmonics: int = Field(3, ge=1, le=10)
    f_natural: List[float] = Field(default_factory=list, description="structure natural frequencies [Hz]")
    tol: float = Field(0.15, gt=0, lt=1.0)


class IsolationRequest(BaseModel):
    f: float = Field(25.0, gt=0, description="excitation frequency [Hz]")
    f_n: float = Field(8.0, gt=0, description="isolator natural frequency [Hz]")
    zeta: float = Field(0.05, ge=0.0, lt=1.0)


class NoiseRequest(BaseModel):
    f: float = Field(500.0, gt=0)
    surface_density: float = Field(47.1, gt=0, description="steel 6mm: rho*h [kg/m^2]")
    field_incidence: bool = True
    E: Optional[float] = Field(2.1e11, gt=0)
    nu: float = Field(0.3, gt=-0.99, lt=0.5)
    rho: Optional[float] = Field(7850.0, gt=0)
    h: Optional[float] = Field(0.006, gt=0)
    source_level: Optional[float] = Field(None, description="[dB], for required TL")
    target_level: Optional[float] = Field(None, description="[dB]")


class LevelsRequest(BaseModel):
    levels: List[float] = Field(..., min_length=1)
    center_freqs: Optional[List[float]] = None


@router.post("/calculations/vibration/beam_natural")
def api_beam_natural(req: BeamRequest) -> Dict[str, object]:
    try:
        f = beam_natural_frequencies(req.bc, req.E, req.I, req.rho, req.A, req.L, req.n_modes)
        out: Dict[str, object] = {
            "method": "Euler-Bernoulli 梁闭式解 + 数值求根（无查表）",
            "bc": req.bc,
            "lambda_n": beam_eigenvalues(req.bc, req.n_modes),
            "frequencies_hz": f,
            "evidence": "REAL",
        }
        if req.fem_check:
            ff = beam_fem_frequencies(req.bc, req.E, req.I, req.rho, req.A, req.L,
                                      req.n_elem, req.n_modes)
            out["fem_hz"] = ff
            out["fem_vs_analytic_pct"] = [
                100.0 * (b - a) / a for a, b in zip(f, ff)
            ]
        return out
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/calculations/vibration/plate_natural")
def api_plate_natural(req: PlateRequest) -> Dict[str, object]:
    try:
        freqs = plate_frequencies(req.a, req.b, req.E, req.nu, req.rho, req.h, req.n_max)
        out: Dict[str, object] = {
            "method": "Kirchhoff 薄板 SSSS Navier 闭式解",
            "rigidity_D": plate_rigidity(req.E, req.nu, req.h),
            "modes": freqs,
            "evidence": "REAL",
        }
        if req.rayleigh_check:
            fr = plate_rayleigh_frequency(1, 1, req.a, req.b, req.E, req.nu, req.rho, req.h)
            f11 = plate_natural_frequency(1, 1, req.a, req.b, req.E, req.nu, req.rho, req.h)
            out["rayleigh_f11_hz"] = fr
            out["rayleigh_vs_navier_pct"] = 100.0 * (fr - f11) / f11
        return out
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/calculations/vibration/excitation")
def api_excitation(req: ExcitationRequest) -> Dict[str, object]:
    try:
        out: Dict[str, object] = {
            "blade_rate": blade_rate_orders(req.n_blades, req.rpm, req.n_harmonics),
        }
        if req.n_cylinders:
            out["diesel"] = diesel_firing_frequencies(
                req.rpm, req.n_cylinders, req.stroke, req.n_harmonics)
        if req.f_natural:
            out["screening"] = resonance_screening(
                req.f_natural, req.n_blades, req.rpm, req.n_cylinders,
                req.stroke, req.n_harmonics, req.tol)
        out["evidence"] = "REAL"
        return out
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/calculations/vibration/isolation")
def api_isolation(req: IsolationRequest) -> Dict[str, object]:
    try:
        return isolation_transmissibility(req.f, req.f_n, req.zeta)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/calculations/noise/panel_tl")
def api_panel_tl(req: NoiseRequest) -> Dict[str, object]:
    try:
        out: Dict[str, object] = panel_tl(req.f, req.surface_density,
                                          req.field_incidence)
        if req.E and req.rho and req.h:
            out["coincidence_hz"] = coincidence_frequency(req.E, req.nu, req.rho, req.h)
        if req.source_level is not None and req.target_level is not None:
            out["required_tl_db"] = required_tl(req.source_level, req.target_level)
            out["margin_db"] = out["tl_field_db"] if req.field_incidence else out["tl_normal_db"]
            out["passes_required"] = out["margin_db"] >= out["required_tl_db"]
        out["evidence"] = "REAL"
        return out
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/calculations/noise/levels")
def api_levels(req: LevelsRequest) -> Dict[str, object]:
    try:
        out: Dict[str, object] = {"combined_db": combine_levels_db(req.levels)}
        if req.center_freqs:
            out["a_weighted"] = [a_weighting(f) for f in req.center_freqs]
            out["octave"] = octave_band_levels(out["combined_db"], req.center_freqs)
        out["evidence"] = "REAL"
        return out
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e