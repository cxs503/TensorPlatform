"""ship_perf —— 船舶总体性能"正确物理"参考实现（m1490）

背景
----
在线 :8001 船舶总体性能平台源码不在本工作树，
`artifacts/m1473/physics_audit/`（m1473 物理一致性审计，225 项）认定
resistance / stability / propulsion 三个分支存在越域与量纲级错误。
本包给出**可被服务侧直接承接**的正确实现 + 机器可裁判的判据（见
``ship_perf.conformance`` 与 ``tests/audit/ship_api_conformance.py``）。

模块
----
- ``resistance``   ITTC-57 摩擦线、Holtrop 形状因子、水下回转体分支与 Holtrop 域校验
- ``hydrostatics`` KB/BM/GM 与梯形截面几何一致，大倾角 GZ 由剖面多边形裁剪积分求得
- ``propeller``    叶元动量理论（BEMT）敞水特性 KT(J)/KQ(J)/η0(J) 与推力平衡反解转速
- ``conformance``  11 项机器判据（7 项由 FAIL 转 PASS + 4 项回归守护）
"""

from .constants import G, NU_FRESH, NU_SEA, RHO_FRESH, RHO_SEA  # noqa: F401

__all__ = [
    "resistance",
    "hydrostatics",
    "propeller",
    "conformance",
    "G",
    "RHO_SEA",
    "RHO_FRESH",
    "NU_SEA",
    "NU_FRESH",
]
__version__ = "1.0.0"