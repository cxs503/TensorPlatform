"""动态升级机制（IN59-A6）：任务谱系连续失败/无进展时自动生成升级菜单。

核心规则
--------
1. 同任务谱系连续 failed ≥ 3 → 触发升级菜单
2. 同席位心跳正常但 no_progress ≥ 2 轮 → 触发升级菜单
3. 同一假设被证伪 2 次后不得第三次重跑（强制换路线）
4. 升级菜单选项：请求更强Agent / 请求领域专家知识包 / 重分配席位 / 换技术路线

落库形状
--------
- ``upgrade_menus`` 表：(menu_id, lineage_key, trigger_reason, options_json, status, created_at, disposed_at, disposition)
- ``hypothesis_registry`` 表：(hypothesis_id, lineage_key, hypothesis_text, falsification_count, status, created_at)
"""
from __future__ import annotations

from .core import (
    SCHEMA,
    UPGRADE_OPTIONS,
    TRIGGER_REASONS,
    DISPOSITION_ACTIONS,
    check_lineage_failures,
    check_seat_no_progress,
    build_upgrade_menu,
    record_disposition,
    HYPOTHESIS_MAX_FALSIFICATIONS,
    register_hypothesis,
    record_hypothesis_falsification,
    is_hypothesis_blocked,
    DDL,
)

__all__ = [
    "SCHEMA",
    "UPGRADE_OPTIONS",
    "TRIGGER_REASONS",
    "DISPOSITION_ACTIONS",
    "check_lineage_failures",
    "check_seat_no_progress",
    "build_upgrade_menu",
    "record_disposition",
    "HYPOTHESIS_MAX_FALSIFICATIONS",
    "register_hypothesis",
    "record_hypothesis_falsification",
    "is_hypothesis_blocked",
    "DDL",
]
