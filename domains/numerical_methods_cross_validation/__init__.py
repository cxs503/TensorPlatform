"""P97W4-N12 数值方法交叉验证与平台能力台账自动更新

本模块实现 M2 阶段 5 个数值方法新前沿节点的交叉验证：
1. SPH 毛细波 vs DG AMR 毛细波（同一解析解不同方法对标）
2. FVM 仿生减阻 vs FVM 势流-粘性混合（同一船型不同物理模型）
3. 能力台账自动更新（ext_id→能力描述→证据等级）

验收判据：
C1: SPH毛细波vs DG AMR毛细波色散误差<10%
C2: FVM仿生减阻vs混合方法摩擦阻力误差<10%
C3: 能力台账自动更新≥5条新能力
C4: 回归测试全绿（已有能力不退化）
C5: pytest≥10用例全绿
"""

__version__ = "1.0.0"
__all__ = [
    "capillary_waves",
    "drag_reduction",
    "capability_updater",
    "cross_validator",
]
