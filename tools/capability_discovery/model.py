"""数据模型 —— 能力自动发现（capability auto-discovery）的公共类型。

零领域语义（zero domain semantics）
-----------------------------------
本模块不出现任何能力名称、参数名、物理量或领域词条。能力完全由**求解器仓自己**
在测试里写下的 marker 元数据声明（见 ``docs/capability_discovery.md`` 的约定），
代码只做「解析 / 聚合 / 定级 / 登记」四件与领域无关的事。

三层数据
--------
``DiscoveryRecord``
    一条「(测试节点, capability marker 声明)」对。一个测试可因 parametrize /
    类级 / 模块级声明产生多条记录。

``CapabilityFeature``
    按 ``capability_id`` 聚合后的能力特征：标签、声明等级、验证锚、参数范围、
    覆盖的测试节点、源文件 sha256、运行结局计数。它是「提取层」的产物。

``Grade``
    定级层产物：由运行结局 + 声明 + 锚点，经**策略数据**（JSON，非代码分支）
    推导出的证据等级与理由。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

# ── 证据等级（与平台其余台账共用同一枚举与序） ───────────────────────────────
LEVELS = ("NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL")
LEVEL_RANK: Dict[str, int] = {lvl: i for i, lvl in enumerate(LEVELS)}

# ── 测试运行结局（pytest 语义，非领域语义） ──────────────────────────────────
# 顺序即「矛盾度」优先级：前面的结局压过后面的。
OUTCOME_STATES = (
    "error",      # setup/teardown 失败或异常
    "failed",     # 断言失败 → 声明被证伪
    "passed",     # 通过
    "xpassed",    # xfail 却通过（意外通过，需人工复核）
    "xfailed",    # 预期失败（已知缺陷，未验证）
    "skipped",    # 跳过（无证据）
    "not_run",    # 未被本次运行收集（静态发现但没跑）
)

# ── 支持的 marker 书写格式（C1：≥5 种） ─────────────────────────────────────
MARKER_FORMATS = (
    "function_kwargs",         # @pytest.mark.capability(id=..., level=...)
    "function_positional_id",  # @pytest.mark.capability("some.id", level=...)
    "function_bare",           # @pytest.mark.capability（无参，id 由测试名派生）
    "class_kwargs",            # 类级 marker，被类内测试方法继承
    "module_pytestmark",       # pytestmark = [pytest.mark.capability(...)]
    "param_marks",             # pytest.param(..., marks=pytest.mark.capability(...))
    "runtime_marker",          # 运行期 marker 元数据（插件采集；可解非字面量 id）
)

SCOPE_FUNCTION = "function"
SCOPE_CLASS = "class"
SCOPE_MODULE = "module"


# ── 基础工具 ─────────────────────────────────────────────────────────────────
def now_iso() -> str:
    """UTC 时间戳（微秒精度，保证可排序、可去重）。"""
    return datetime.now(timezone.utc).isoformat()


def canonical_json(obj: Any) -> str:
    """确定性 JSON 序列化（键排序、无空格漂移），用于 sha256 定址。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def digest(obj: Any) -> str:
    """对象内容的 sha256（十六进制）。"""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def level_rank(level: Any) -> int:
    """等级序；未知等级按最弱（0）处理——宁可低估，不可高估。"""
    return LEVEL_RANK.get(str(level or "").upper(), 0)


def norm_level(level: Any) -> str:
    """规范化等级字符串；非法值抛 ValueError（登记层拒收幽灵等级）。"""
    lvl = str(level or "").upper()
    if lvl not in LEVEL_RANK:
        raise ValueError(f"非法证据等级: {level!r}；允许 {LEVELS}")
    return lvl


# ── 记录 / 特征 / 定级 ───────────────────────────────────────────────────────
@dataclass
class DiscoveryRecord:
    """一条 (测试节点, capability marker) 声明记录。"""

    capability_id: str
    nodeid: str
    source_file: str          # 相对仓根，posix 风格
    source_sha256: str
    lineno: int
    marker_format: str
    scope: str = SCOPE_FUNCTION
    label: str = ""
    declared_level: str = ""  # 空 = 未声明（保守定为 WITHHELD，见 grading）
    anchors: List[str] = field(default_factory=list)
    params: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    modifiers: List[str] = field(default_factory=list)  # skip / skipif / xfail

    def __post_init__(self) -> None:
        if not self.capability_id or not isinstance(self.capability_id, str):
            raise ValueError("capability_id 必须为非空字符串")
        if self.marker_format not in MARKER_FORMATS:
            raise ValueError(f"未知 marker 格式: {self.marker_format!r}")
        if self.declared_level:
            self.declared_level = norm_level(self.declared_level)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "nodeid": self.nodeid,
            "source_file": self.source_file,
            "source_sha256": self.source_sha256,
            "lineno": self.lineno,
            "marker_format": self.marker_format,
            "scope": self.scope,
            "label": self.label,
            "declared_level": self.declared_level,
            "anchors": list(self.anchors),
            "params": dict(self.params),
            "tags": list(self.tags),
            "modifiers": list(self.modifiers),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DiscoveryRecord":
        return cls(
            capability_id=d["capability_id"],
            nodeid=d.get("nodeid", ""),
            source_file=d.get("source_file", ""),
            source_sha256=d.get("source_sha256", ""),
            lineno=int(d.get("lineno", 0)),
            marker_format=d.get("marker_format", "function_kwargs"),
            scope=d.get("scope", SCOPE_FUNCTION),
            label=d.get("label", ""),
            declared_level=d.get("declared_level", ""),
            anchors=list(d.get("anchors") or []),
            params=dict(d.get("params") or {}),
            tags=list(d.get("tags") or []),
            modifiers=list(d.get("modifiers") or []),
        )


@dataclass
class CapabilityFeature:
    """按 capability_id 聚合后的能力特征（提取层产物）。"""

    capability_id: str
    label: str = ""
    declared_level: str = ""
    anchors: List[str] = field(default_factory=list)
    params: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    tests: List[Dict[str, Any]] = field(default_factory=list)     # 节点视图
    sources: List[Dict[str, str]] = field(default_factory=list)    # {path, sha256}
    marker_formats: List[str] = field(default_factory=list)
    outcomes: Dict[str, int] = field(default_factory=dict)         # state -> count
    declared_levels_seen: List[str] = field(default_factory=list)   # 冲突检测用
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_tests(self) -> int:
        return len(self.tests)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "label": self.label,
            "declared_level": self.declared_level,
            "anchors": list(self.anchors),
            "params": dict(self.params),
            "tags": list(self.tags),
            "tests": list(self.tests),
            "sources": list(self.sources),
            "marker_formats": list(self.marker_formats),
            "outcomes": dict(self.outcomes),
            "declared_levels_seen": list(self.declared_levels_seen),
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CapabilityFeature":
        return cls(
            capability_id=d["capability_id"],
            label=d.get("label", ""),
            declared_level=d.get("declared_level", ""),
            anchors=list(d.get("anchors") or []),
            params=dict(d.get("params") or {}),
            tags=list(d.get("tags") or []),
            tests=list(d.get("tests") or []),
            sources=list(d.get("sources") or []),
            marker_formats=list(d.get("marker_formats") or []),
            outcomes=dict(d.get("outcomes") or {}),
            declared_levels_seen=list(d.get("declared_levels_seen") or []),
            extra=dict(d.get("extra") or {}),
        )

    def evidence_fingerprint(self) -> str:
        """证据指纹：影响等级的字段的 sha256（不含时间戳，故可判幂等）。"""
        return digest(
            {
                "capability_id": self.capability_id,
                "declared_level": self.declared_level,
                "anchors": sorted(self.anchors),
                "params": self.params,
                "tests": sorted(t.get("nodeid", "") for t in self.tests),
                "sources": sorted(s.get("sha256", "") for s in self.sources),
                "marker_formats": sorted(self.marker_formats),
                "outcomes": self.outcomes,
            }
        )


@dataclass
class Grade:
    """定级层产物。"""

    capability_id: str
    level: str
    outcome_state: str
    reasons: List[str] = field(default_factory=list)
    confidence: float = 0.0
    policy_digest: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "level": self.level,
            "outcome_state": self.outcome_state,
            "reasons": list(self.reasons),
            "confidence": round(float(self.confidence), 4),
            "policy_digest": self.policy_digest,
        }