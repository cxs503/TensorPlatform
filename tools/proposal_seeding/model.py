"""提案 / 聚合项 / 计划节点三层规范模型（零领域语义）。

异构输入 → 单一规范形态
------------------------
实测的提案工件至少三种 ``goal`` 形态：纯文本（含 【小节】 头）、字典（``物理目标`` /
``实现路径`` 键）、列表（实现路径逐条）。仲裁者要「吸收各家可取细节」，就必须先把三家
写法压成同一形态：**分节 → 行**。本模块只做结构归一，不给任何节名赋语义（节名原样保留，
作为不透明键），因此换任何一个物理域都不需要改代码。

幂等的基础
----------
``digest`` 是内容寻址（标题 / 仓 / 分节 / 判据 / 依赖），不含来源标签、时间戳、路径：
* 两位提案者写出完全相同的提案 → 同一 ``digest``（精确重复可检出）；
* 同一输入重复运行 → 同一 ``digest`` 序列 → 同一 ``ext_id`` 序列。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

FLOAT_ND = 6

_HEADER_RE = re.compile(r"^\s*【([^】]{1,24})】\s*(.*)$")
_NUMBERED_RE = re.compile(r"^\s*(?:\((\d+)\)|（(\d+)）|(\d+)[.)、])\s*(.*)$")
_ASCII_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")


# ---------------------------------------------------------------------------
# canonical serialisation
# ---------------------------------------------------------------------------
def _round(value: Any) -> Any:
    if isinstance(value, float):
        r = round(value, FLOAT_ND)
        return 0.0 if r == 0 else r
    if isinstance(value, dict):
        return {str(k): _round(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_round(v) for v in value]
    return value


def canonical_json(obj: Any) -> str:
    """排序键 + 压缩分隔符 + 浮点定长 → 逐字节可复现。"""
    return json.dumps(_round(obj), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# goal normalisation
# ---------------------------------------------------------------------------
def _lines(value: Any) -> list[str]:
    """把任意 goal 片段展开成「不含空行的文本行」。"""
    out: list[str] = []
    if value is None:
        return out
    if isinstance(value, str):
        for raw in value.replace("\r\n", "\n").split("\n"):
            line = raw.strip()
            if line:
                out.append(line)
        return out
    if isinstance(value, Mapping):
        for key, sub in value.items():
            sub_lines = _lines(sub)
            if not sub_lines:
                out.append(str(key).strip())
                continue
            first = sub_lines[0]
            out.append(f"{str(key).strip()}：{first}")
            out.extend(sub_lines[1:])
        return out
    if isinstance(value, (list, tuple)):
        for item in value:
            out.extend(_lines(item))
        return out
    text = str(value).strip()
    return [text] if text else out


def _strip_ordinal(line: str) -> str:
    m = _NUMBERED_RE.match(line)
    if not m:
        return line
    tail = m.group(4) if m.group(4) is not None else ""
    return tail.strip() or line


def split_sections(goal: Any, *, default_section: str = "body") -> dict[str, list[str]]:
    """``goal`` → ``{section: [line, ...]}``（节名来自 【】 头或字典键，不解释语义）。"""
    sections: dict[str, list[str]] = {}
    current = default_section

    def push(name: str, line: str) -> None:
        sections.setdefault(name, [])
        if line and line not in sections[name]:
            sections[name].append(line)

    if isinstance(goal, Mapping):
        for key, sub in goal.items():
            name = str(key).strip() or default_section
            for line in _lines(sub):
                push(name, _strip_ordinal(line))
        return sections

    for line in _lines(goal):
        m = _HEADER_RE.match(line)
        if m:
            current = m.group(1).strip() or default_section
            sections.setdefault(current, [])
            tail = m.group(2).strip()
            if tail:
                push(current, _strip_ordinal(tail))
            continue
        push(current, _strip_ordinal(line))
    return sections


def sections_text(sections: Mapping[str, Sequence[str]]) -> str:
    parts: list[str] = []
    for name in sorted(sections):
        parts.append(name)
        parts.extend(sections[name])
    return " ".join(parts)


def ascii_tokens(text: str) -> list[str]:
    """标题里的拉丁词（用于 ext_id slug 与依赖提示消解），保持出现顺序、去重。"""
    seen: list[str] = []
    for m in _ASCII_TOKEN_RE.finditer(text or ""):
        tok = m.group(0).lower()
        if tok not in seen:
            seen.append(tok)
    return seen


# ---------------------------------------------------------------------------
# proposal
# ---------------------------------------------------------------------------
class ProposalError(ValueError):
    """提案缺字段 / 类型非法（fail-closed，调用方计入 rejected）。"""


@dataclass(frozen=True)
class Quote:
    """来源引用：哪一位提案者的哪一条，贡献了哪些节。"""

    source: str
    index: int
    raw_id: str | None = None
    sections: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "index": self.index,
                "raw_id": self.raw_id, "sections": list(self.sections)}


@dataclass(frozen=True)
class Proposal:
    source: str
    index: int
    title: str
    repo: str
    sections: dict[str, list[str]]
    criteria: tuple[str, ...] = ()
    deps: tuple[str, ...] = ()
    is_maturity: bool = False
    raw_id: str | None = None
    physics_domain: str = ""
    digest: str = ""

    @property
    def text(self) -> str:
        """相似度口径文本：标题 + 分节内容（不含来源标签）。"""
        return (self.title + " " + sections_text(self.sections)).strip()

    @property
    def maturity_text(self) -> str:
        return " ".join([self.title, sections_text(self.sections), " ".join(self.criteria)])

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "index": self.index,
            "raw_id": self.raw_id,
            "title": self.title,
            "repo": self.repo,
            "is_maturity": self.is_maturity,
            "physics_domain": self.physics_domain,
            "sections": self.sections,
            "criteria": list(self.criteria),
            "deps": list(self.deps),
            "digest": self.digest,
        }


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            for line in _lines(item):
                if line not in out:
                    out.append(line)
        return out
    return _lines(value)


def criteria_from_sections(sections: Mapping[str, Sequence[str]],
                           markers: Sequence[str] = ()) -> list[str]:
    """从「节名含标记词」的节里抽判据行（标记词来自配置，非硬编码域词）。"""
    out: list[str] = []
    if not markers:
        return out
    for name in sections:
        lname = str(name).casefold()
        if any(m.casefold() in lname for m in markers):
            for line in sections[name]:
                if line not in out:
                    out.append(line)
    return out


def proposal_from_raw(raw: Mapping[str, Any], *, source: str, index: int,
                      required: Sequence[str] = ("title", "repo", "goal"),
                      criteria_markers: Sequence[str] = ()) -> Proposal:
    """异构提案 → :class:`Proposal`（缺字段抛 :class:`ProposalError`）。"""
    if not isinstance(raw, Mapping):
        raise ProposalError(f"提案 #{index} 不是对象")
    missing = [k for k in required if not raw.get(k)]
    if missing:
        raise ProposalError(f"提案 #{index} 缺少字段: {', '.join(missing)}")
    title = str(raw["title"]).strip()
    repo = str(raw["repo"]).strip()
    sections = split_sections(raw["goal"])
    explicit = _as_str_list(
        raw.get("criteria") or raw.get("falsification_criteria") or raw.get("acceptance"))
    criteria = list(explicit)
    for line in criteria_from_sections(sections, criteria_markers):
        if line not in criteria:
            criteria.append(line)
    criteria_t = tuple(criteria)
    deps = tuple(_as_str_list(raw.get("dependencies_hint")))
    is_maturity_raw = raw.get("is_maturity", False)
    is_maturity = bool(is_maturity_raw) if isinstance(is_maturity_raw, (bool, int)) else False
    payload = {
        "title": title, "repo": repo,
        "sections": sections, "criteria": list(criteria_t), "deps": list(deps),
        "is_maturity": is_maturity,
    }
    return Proposal(
        source=source, index=index, title=title, repo=repo, sections=sections,
        criteria=criteria_t, deps=deps, is_maturity=is_maturity,
        raw_id=str(raw["id"]) if raw.get("id") is not None else None,
        physics_domain=str(raw.get("physics_domain") or raw.get("domain") or ""),
        digest=digest(payload),
    )


# ---------------------------------------------------------------------------
# aggregated item
# ---------------------------------------------------------------------------
@dataclass
class AggregatedItem:
    """一组相似提案合并后的规范项。``quotes`` 记录每一位贡献者，``absorbed`` 记录吸收明细。"""

    item_id: str
    title: str
    repo: str
    repos: tuple[str, ...]
    is_maturity: bool
    sections: dict[str, list[str]]
    criteria: tuple[str, ...]
    deps: tuple[str, ...]
    quotes: tuple[Quote, ...]
    absorbed: tuple[dict[str, Any], ...] = ()
    repo_conflict: bool = False
    max_similarity: float = 0.0
    members: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        return (self.title + " " + sections_text(self.sections)).strip()

    @property
    def sources(self) -> list[str]:
        return [q.source for q in self.quotes]

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "repo": self.repo,
            "repos": list(self.repos),
            "repo_conflict": self.repo_conflict,
            "is_maturity": self.is_maturity,
            "sections": self.sections,
            "criteria": list(self.criteria),
            "deps": list(self.deps),
            "quotes": [q.as_dict() for q in self.quotes],
            "absorbed": list(self.absorbed),
            "max_similarity": round(self.max_similarity, FLOAT_ND),
            "members": list(self.members),
        }


@dataclass
class PlanNode:
    index: int
    ext_id: str
    name: str
    repo: str
    milestone: int
    goal: str
    dependencies: list[int] = field(default_factory=list)
    is_maturity: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "ext_id": self.ext_id,
            "name": self.name,
            "repo": self.repo,
            "milestone": self.milestone,
            "goal": self.goal,
            "dependencies": list(self.dependencies),
            "is_maturity": self.is_maturity,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PlanNode":
        return cls(
            index=int(raw["index"]),
            ext_id=str(raw["ext_id"]),
            name=str(raw["name"]),
            repo=str(raw["repo"]),
            milestone=int(raw["milestone"]),
            goal=str(raw.get("goal", "")),
            dependencies=[int(x) for x in raw.get("dependencies", [])],
            is_maturity=bool(raw.get("is_maturity", False)),
            metadata=dict(raw.get("metadata", {})),
        )