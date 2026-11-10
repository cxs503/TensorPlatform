"""sealed_evidence —— 证据封存与回收闸（零领域语义、幂等、可复现）。

核心组件：
  * :mod:`schema` —— sealed_evidence.json 契约结构定义与校验
  * :mod:`sealer` —— 从任务 result/报告抽取制品引用 → 路径归一化 → sha256 → 写契约（幂等）
  * :mod:`recovery_gate` —— worktree_recyclable 候选四关拦截（契约存在性、sha256 匹配、
    重放可行、缺件显式登记）
  * :mod:`replay_sandbox` —— 最小依赖环境执行 replay.cmd，禁网禁写库，超时 300s
  * :mod:`drift_attributor` —— 把 repro_audit 的 drift 逐条映射到四类
    （缺失/截断/sha256 篡改/命令不存在），产出 drift_cause.json

设计约束：
  * **零领域语义**：本包不认识任何学科词/船型/仓名；所有域数据经配置/工件注入。
  * **幂等**：同任务连跑两次封存，契约 sha256 相同（字节差=0）。
  * **只读**：封存器不修改原始工件；回收闸只写事件与债务账本。
"""
from .schema import (
    SEALED_EVIDENCE_SCHEMA,
    validate_sealed_evidence,
    build_sealed_evidence,
)
from .sealer import seal_evidence
from .recovery_gate import run_recovery_gate, GateVerdict
from .replay_sandbox import run_replay_sandbox
from .drift_attributor import attribute_drift_causes, DRIFT_CATEGORIES

__all__ = [
    "SEALED_EVIDENCE_SCHEMA",
    "validate_sealed_evidence",
    "build_sealed_evidence",
    "seal_evidence",
    "run_recovery_gate",
    "GateVerdict",
    "run_replay_sandbox",
    "attribute_drift_causes",
    "DRIFT_CATEGORIES",
]
