"""backlog_refiller — 常设补给器（渐退令核心件）。

容器 daemon：队列深度低于阈值（pending=0 且 idle worker≥6）时，
从已文档化缺口账本自动生成 mission。

核心组件：
  - daemon.BacklogRefillerDaemon: 主循环 daemon
  - gap_ledger.GapLedgerSource: 缺口账本聚合器
  - mission_generator.MissionGenerator: mission 生成器（goal 门/ext_id 去重/仓白名单）
  - audit.AuditLogger: 审计日志（可回滚）

设计约束：独立于任何人类会话与 AI 协调者会话运行，带审计日志+回滚路径。
"""
from __future__ import annotations

__version__ = "0.1.0"
