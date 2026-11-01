"""冰区航行战役第八波跨域集成门 (W8-N08).

模块结构
--------
dimensions.py      量纲代数与单位换算 (唯一事实来源)
contracts.py       W8-N01~N07 接口契约登记表 + 校验器 (量纲/坐标系/反馈环)
reference_nodes.py N01 相变融膜 / N02 液晃 (势流 FD) 参考实现
nodes_downstream.py N03 滚-液晃耦合+稳性 / N04 SEA / N05 堵塞 / N06 流变 / N07 EKF
pipeline.py        端到端数据流 + 质量/动量/能量守恒审计
probe.py           上游 7 节点 worktree/branch 兼容性只读探测
gate.py            战役级验收门: 判据计算 + 工件 + 图
"""
from .contracts import CONTRACTS, EDGES, validate_contracts, edge_units_table
from .dimensions import DimensionError, convert, dim_of, compatible
from .pipeline import run_pipeline, validate_payload
from .probe import probe_all

__all__ = [
    "CONTRACTS",
    "EDGES",
    "validate_contracts",
    "edge_units_table",
    "DimensionError",
    "convert",
    "dim_of",
    "compatible",
    "run_pipeline",
    "validate_payload",
    "probe_all",
]