"""平台治理成熟化：N01 度量工具 × N02 三契约源 × N03 跨库场交换 的集成验证面。

四个子模块，各管一件事，互不侵入：

* :mod:`.contracts`    —— 契约统一装载 + sha256 指纹（审计链锚点）
* :mod:`.metrics_audit`—— 度量工具事件面 vs 事件流/状态/登记词表（M1–M8）
* :mod:`.field_audit`  —— 场交换接口/端口/schema/迁移 vs 契约测试定义（F1–F8 + findings）
* :mod:`.e2e`          —— agent 心跳 → 事件流 → 画像 → 场交换 → 契约验证 的端到端链路

IRON RULE：本包零领域语义——只认识契约名、文件名、端口字段名与量纲。
"""
from __future__ import annotations

from .contracts import contract_inventory  # noqa: F401
from .field_audit import audit_field_exchange  # noqa: F401
from .metrics_audit import audit_metrics_contract  # noqa: F401

__all__ = ["contract_inventory", "audit_metrics_contract", "audit_field_exchange"]