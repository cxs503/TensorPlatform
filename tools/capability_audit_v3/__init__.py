"""Capability Audit v3 — 全量 21 项能力不变量探针覆盖。

v3 相对于 v2 的改进：
- 探针从 3 项扩展到 21 项全覆盖
- 每项 WITHHELD/MOCK/STUB 能力至少 1 个不变量探针
- 引入「能力指纹」概念：对同一输入多次调用，检查输出是否恒定（MOCK 检测）
- 引入「敏感性探针」：改变输入参数，检查输出是否响应（REAL 检测）
- 引入「物理不变量」：对物理计算能力，检查守恒律/恒等式
- 判级与人工复核一致率 ≥95%
"""
from .probes_v3 import run_full_audit, ProbeResult, CapabilityVerdict
from .spec_v3 import load_spec_v3, SpecV3
__version__ = "3.0.0"
__all__ = ["run_full_audit", "ProbeResult", "CapabilityVerdict", "load_spec_v3", "SpecV3"]
