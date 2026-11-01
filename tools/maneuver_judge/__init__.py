"""maneuver_judge — 船舶操纵性（manoeuvring）证据分级判据与独立参考解算器.

用途：给船舶总体性能平台(:8001)的操纵性端点建立**机器可裁**的判据，
使得任何"操纵性能"输出都能被判为 REAL / MOCK / STUB 之一，且不依赖
不可复核的经验系数。

分层（越靠前越硬，可信度越高）：
  L1 精确解析  —— Nomoto 一阶响应模型的闭式解，误差判据可达 1e-9。
  L2 无量纲恒等式 —— 回转运动的运动学恒等式 r = V/R，纯代数，任何
                  "物理自洽"的求解器都必须满足。
  L3 响应不变性/单调性 —— 对舵角、舵面积、排水量、航速的响应方向与
                  单调性；违反即证伪（硬编码的充分非必要证据）。
  L4 规范/语料区间 —— IMO MSC.137(76) 判据与平台自带 KCS 语料
                  (data/test_data/maneuvering_kcs.json, SIMMAN 2008/2014)
                  的区间；区间语义，不作为精确基准。

禁止事项：本模块不用任何凭记忆写下的 MMG 水动力导数做绝对误差判定
（那样会把"系数不确定度"混进"平台误差"）。MMG 只作为趋势/对称性检查。
"""
from .nomoto import (  # noqa: F401
    NomotoResponse,
    r_of_t,
    psi_of_t,
    steady_turning_radius,
    tactical_diameter,
    identify_K_from_steady_rate,
    trajectory,
)

__all__ = [
    "NomotoResponse",
    "r_of_t",
    "psi_of_t",
    "steady_turning_radius",
    "tactical_diameter",
    "identify_K_from_steady_rate",
    "trajectory",
]