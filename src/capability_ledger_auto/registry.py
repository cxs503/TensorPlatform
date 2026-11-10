"""能力定义注册表（capability definition registry）。

从 capabilities.json 读取 21 项能力定义（名称/描述/期望证据类型）。
零领域语义：注册表只定义模式（pattern），不硬编码任何域词条。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 证据类型枚举（五元：theory/code/config/data/report）
EVIDENCE_TYPES = {"theory", "code", "config", "data", "report"}

# 等级枚举
LEVELS = ["NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL"]
LEVEL_RANK = {lv: i for i, lv in enumerate(LEVELS)}


@dataclass
class CapabilityDef:
    """单条能力定义。

    Attributes
    ----------
    capability_id : str
        能力唯一标识（如 "calc.resistance"）。
    label : str
        人类可读标签。
    description : str
        能力描述。
    expected_evidence_types : set[str]
        期望的证据类型子集（theory/code/config/data/report）。
    code_patterns : list[str]
        代码匹配正则模式（用于在 src/ 下搜索实现）。
    test_patterns : list[str]
        测试匹配正则模式（用于在 tests/ 下搜索测试）。
    report_patterns : list[str]
        报告匹配正则模式（用于在 tensor_reports/ 下搜索报告）。
    initial_level : str
        初始等级（默认 WITHHELD）。
    code_roots : list[str]
        该能力的代码扫描根（可覆盖扫描器默认；空表示用默认布局根）。
    """
    capability_id: str
    label: str
    description: str
    expected_evidence_types: set[str] = field(default_factory=set)
    code_patterns: list[str] = field(default_factory=list)
    test_patterns: list[str] = field(default_factory=list)
    report_patterns: list[str] = field(default_factory=list)
    initial_level: str = "WITHHELD"
    code_roots: list[str] = field(default_factory=list)

    def __post_init__(self):
        # 验证证据类型
        invalid = self.expected_evidence_types - EVIDENCE_TYPES
        if invalid:
            raise ValueError(f"无效证据类型: {invalid}")
        # 验证等级
        if self.initial_level not in LEVEL_RANK:
            raise ValueError(f"无效初始等级: {self.initial_level}")


@dataclass
class CapabilityRegistry:
    """能力定义注册表。

    从 JSON 文件加载能力定义，提供查询接口。
    """
    capabilities: dict[str, CapabilityDef] = field(default_factory=dict)
    schema_version: str = "1.0"
    source_path: str = ""

    @classmethod
    def from_json(cls, path: str | Path) -> "CapabilityRegistry":
        """从 JSON 文件加载注册表。"""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"注册表文件不存在: {path}")

        with open(path, encoding="utf-8") as f:
            raw = json.load(f)

        registry = cls(
            schema_version=raw.get("schema_version", "1.0"),
            source_path=str(path),
        )

        for cap_raw in raw.get("capabilities", []):
            cap = CapabilityDef(
                capability_id=cap_raw["capability_id"],
                label=cap_raw.get("label", cap_raw["capability_id"]),
                description=cap_raw.get("description", ""),
                expected_evidence_types=set(cap_raw.get("expected_evidence_types", [])),
                code_patterns=cap_raw.get("code_patterns", []),
                test_patterns=cap_raw.get("test_patterns", []),
                report_patterns=cap_raw.get("report_patterns", []),
                initial_level=cap_raw.get("initial_level", "WITHHELD"),
                code_roots=list(cap_raw.get("code_roots", [])),
            )
            registry.capabilities[cap.capability_id] = cap

        return registry

    @classmethod
    def from_snapshot(cls, path: str | Path) -> "CapabilityRegistry":
        """从 capabilities_snapshot.json 格式加载（兼容旧格式）。"""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"快照文件不存在: {path}")

        with open(path, encoding="utf-8") as f:
            raw = json.load(f)

        registry = cls(
            schema_version=raw.get("schema_version", "1.0"),
            source_path=str(path),
        )

        caps = raw.get("capabilities", {})
        for cap_id, level in caps.items():
            parts = cap_id.split(".")
            base = parts[-1] if parts else cap_id
            registry.capabilities[cap_id] = CapabilityDef(
                capability_id=cap_id,
                label=cap_id,
                description="",
                expected_evidence_types={"code", "report"},
                code_patterns=[base],
                test_patterns=[f"test_{base}", f"test_{cap_id.replace('.', '_')}"],
                report_patterns=[base],
                initial_level=level if level in LEVEL_RANK else "WITHHELD",
            )

        return registry

    def get(self, capability_id: str) -> CapabilityDef | None:
        return self.capabilities.get(capability_id)

    def list_all(self) -> list[CapabilityDef]:
        return list(self.capabilities.values())

    def count(self) -> int:
        return len(self.capabilities)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_path": self.source_path,
            "capability_count": self.count(),
            "capabilities": {
                cid: {
                    "capability_id": c.capability_id,
                    "label": c.label,
                    "description": c.description,
                    "expected_evidence_types": sorted(c.expected_evidence_types),
                    "code_patterns": c.code_patterns,
                    "test_patterns": c.test_patterns,
                    "report_patterns": c.report_patterns,
                    "initial_level": c.initial_level,
                    "code_roots": list(c.code_roots),
                }
                for cid, c in self.capabilities.items()
            },
        }
