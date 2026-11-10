"""绿色航行能效计算链（green navigation energy chain）—— EEDI / CII / 退化轨迹。

本包补齐 ``backend/services/green_navigation.py`` 自陈的缺口（该模块把
L4「CII A–E 等级」标为 NOT_SUPPORTED，且完全没有主机油耗图 / 船-机-桨匹配 /
年度退化轨迹）。这里给出**从螺旋桨推力平衡到碳强度评级**的完整可复算链：

    geometry   几何先验门（Cb/L/B/T/Δ/DWT 一致性，不合格即拒算）
    engine     主机 SFOC 油耗图：SFOC = SFOC_min + a(P/P_MCR-p*)² + b(n/n_MCR-n*)²
    matching   船-机-桨自航点：R_T(V) = T = ρ n² D⁴ K_T(J)，J 二分求解
    eedi       EEDI_attained（75% MCR 参考点 + P_AE 规则）与 required/compliant
    cii        CII_attained / required / A–E 评级（G4 边界）/ 5 年粗糙度退化轨迹
    chain      端到端装配 + E1..E8 验收判据评估

证据分层（沿用本仓 ``services.green_navigation`` 的 D5 纪律，不把"可推导"
和"抄表"混成一个 REAL）：

=================  ============================================  ==========
层                 依据                                          等级
=================  ============================================  ==========
阻力                Holtrop-Mennen(1982/1984) 回归 + ITTC-57/78    REAL*
自航点               动量/叶元平衡（力平衡残差 ~1e-9 N）            REAL
KT/KQ                Wageningen B-series 回归多项式                 REAL*
轴系/齿轮传递        常数输入（η_shaft=0.99, η_gear=1.0）           WITHHELD
SFOC 曲线形状        与转录的厂家部分负荷表拟合（残差 <5%）         REAL*
SFOC 2-D 偏转速项    b 项在螺旋桨定律下不可辨识（共线）→ 置 0      WITHHELD
EEDI 参考线/阶段折减  MEPC 转录系数（复用 services.green_navigation） WITHHELD
CII 参考线/Z 系数     同上                                          WITHHELD
CII G4 评级边界 dd    MEPC.354(78) 转录（离线未回读原文）           WITHHELD
=================  ============================================  ==========

``REAL*`` = 数学/物理可独立复算，但含回归式或转录输入的固有精度带（各模块
docstring 内逐条声明适用域与误差带）。

**零领域语义**：船型/主机/螺旋桨/参考表全部经 ``data/*.json`` 注入（
:func:`green_navigation.chain.load_case` 读文件），代码里不硬编码任何单一
船名或域判据常量。
"""
from __future__ import annotations

__all__ = [
    "geometry",
    "engine",
    "matching",
    "eedi",
    "cii",
    "chain",
]

__version__ = "1.0.0"