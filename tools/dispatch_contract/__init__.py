"""dispatch_contract —— 派单前契约就绪门（fail-closed，零领域语义）。

模块边界
--------
* :mod:`contract_schema` —— v1 契约文档的静态校验 + 工件载荷（字段/单位/版本）
  口径校验。规则全部来自 ``config/dispatch_contract.v1.json``。
* :mod:`readiness` —— 就绪检查器：给定一条派单声明与其上游注册表快照，返回
  ``READY | BLOCK``（含未满足需求清单与风险分）。缺契约 → 降级恒等放行（不崩）。
* :mod:`corpus` —— 从历史任务快照推导回测语料（契约推断 + 派单时刻上游快照）。
* :mod:`backtest` —— 回测：混淆矩阵 / PR 曲线 / 打乱标签负控 / 平衡评估设计。
* :mod:`lint` —— 契约缺陷体检（口径不符、无判据、工件类别缺失）。

设计铁律
--------
1. 零领域语义：本包不出现任何领域词条、领域判据或领域算例；一切键名/阈值/路径
   前缀来自配置。
2. fail-closed 但不拖死派单链路：检查器自身永不抛异常；输入畸形 → 降级放行并
   在返回结构里标记 ``degraded``，由调用方决定是否记录。
3. 判定可复算：返回结构含逐条需求明细，任何结论都能回溯到具体 schema 字段。
"""

from __future__ import annotations

from .contract_schema import load_config, validate_contract, canonical_unit, version_compatible
from .readiness import assess, VERDICT_BLOCK, VERDICT_READY, SCHEMA as READINESS_SCHEMA

__all__ = [
    "load_config",
    "validate_contract",
    "canonical_unit",
    "version_compatible",
    "assess",
    "VERDICT_BLOCK",
    "VERDICT_READY",
    "READINESS_SCHEMA",
]