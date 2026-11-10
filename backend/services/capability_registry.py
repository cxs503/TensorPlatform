"""
能力台账自动化证据链 — 能力注册服务

设计目标：
- 新能力声明时自动注册到能力台账（名称/描述/领域/预期等级）
- 零领域语义：不硬编码任何域词条
- 支持批量注册与去重

用法:
    from services.capability_registry import CapabilityRegistry

    registry = CapabilityRegistry(db_path="data/capability_evidence.db")
    registry.init()

    # 注册单个能力
    registry.register("calc.resistance", description="阻力计算", domain="resistance")

    # 批量注册
    registry.register_batch([
        {"capability_id": "calc.resistance", "description": "...", "domain": "..."},
        ...
    ])

    # 从配置文件注册
    registry.register_from_config("config/capability_discovery/capabilities.json")
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from services.capability_sql_tracer import (
    CapabilitySQLTracer,
    EVIDENCE_WITHHELD,
    VALID_LEVELS,
)

logger = logging.getLogger(__name__)


class CapabilityRegistry:
    """
    能力注册服务：自动注册新能力到台账。
    """

    def __init__(self, db_path: str | Path = "data/capability_evidence.db"):
        self.tracer = CapabilitySQLTracer(db_path=db_path)
        self.tracer.init_schema()

    def init(self):
        """初始化（确保 schema 存在）。"""
        self.tracer.init_schema()

    def register(
        self,
        capability_id: str,
        description: str = "",
        domain: str = "",
        claimed_level: str = EVIDENCE_WITHHELD,
        expected_level: str = EVIDENCE_WITHHELD,
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        """
        注册单个能力。

        Args:
            capability_id: 能力唯一标识
            description: 能力描述
            domain: 所属领域（通用标签）
            claimed_level: 自述等级
            expected_level: 预期等级
            metadata: 附加元数据

        Returns:
            注册结果
        """
        result = self.tracer.register_capability(
            capability_id=capability_id,
            description=description,
            domain=domain,
            claimed_level=claimed_level,
            expected_level=expected_level,
            metadata=metadata,
        )
        logger.info(f"Capability registered: {result}")
        return result

    def register_batch(self, capabilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        批量注册能力。

        Args:
            capabilities: [{capability_id, description, domain, claimed_level, expected_level, metadata}]

        Returns:
            注册结果列表
        """
        results = []
        for cap in capabilities:
            try:
                result = self.register(
                    capability_id=cap["capability_id"],
                    description=cap.get("description", ""),
                    domain=cap.get("domain", ""),
                    claimed_level=cap.get("claimed_level", EVIDENCE_WITHHELD),
                    expected_level=cap.get("expected_level", EVIDENCE_WITHHELD),
                    metadata=cap.get("metadata"),
                )
                results.append(result)
            except (KeyError, ValueError) as e:
                logger.warning(f"Failed to register capability: {cap} - {e}")
                results.append({"status": "error", "error": str(e), "input": cap})
        return results

    def register_from_config(self, config_path: str | Path) -> list[dict[str, Any]]:
        """
        从配置文件批量注册能力。

        期望格式:
        {
            "capabilities": [
                {"id": "calc.resistance", "description": "...", "domain": "..."},
                ...
            ]
        }

        Args:
            config_path: 配置文件路径（JSON）

        Returns:
            注册结果列表
        """
        path = Path(config_path)
        if not path.exists():
            logger.warning(f"Config file not found: {path}")
            return []

        with open(path, "r", encoding="utf-8") as f:
            config = json.load(f)

        capabilities = config.get("capabilities", [])
        # 适配不同字段名
        normalized = []
        for cap in capabilities:
            normalized.append({
                "capability_id": cap.get("id", cap.get("capability_id", "")),
                "description": cap.get("description", ""),
                "domain": cap.get("domain", ""),
                "claimed_level": cap.get("claimed_level", EVIDENCE_WITHHELD),
                "expected_level": cap.get("expected_level", EVIDENCE_WITHHELD),
                "metadata": cap.get("metadata"),
            })

        return self.register_batch(normalized)

    def get(self, capability_id: str) -> dict | None:
        """获取能力信息。"""
        return self.tracer.get_capability(capability_id)

    def list_all(self) -> list[dict]:
        """列出所有已注册能力。"""
        return self.tracer.list_capabilities()

    def count(self) -> int:
        """获取已注册能力数量。"""
        return len(self.list_all())
