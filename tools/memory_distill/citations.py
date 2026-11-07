"""引用项抽取：把一段判例文本里的「引用数字」与「证据路径」结构化成可计数的事实。

判例可入候选池的硬门槛是「>=3 个引用数字或证据路径」，因此抽取必须是
**确定性、可复算**的：同一文本永远得到同一序列（保持出现顺序、去重不重排）。

刻意不做的事：
- 不把裸整数（如 mission id、年份）当数字引用——只认 带单位/百分号/误差上下文/等号 的数；
- 不把 URL 的域名段当路径证据——路径必须落在白名单顶层目录里才计为证据。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

# 可计为「数字引用」的单位（工程/数值双栖，含中文量词）。
_UNITS = (
    "km/h", "m/s", "kN", "kPa", "kg/m3", "kg/m³", "Pa", "bar", "MB", "KB", "GB",
    "ms", "μs", "us", "min", "mm", "km", "kg", "℃", "核", "条", "项", "行", "次",
    "个", "%", "‰",
)

_UNIT_ALT = "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True))

# 1) 带百分号/千分号的数（最可信）
_RE_PERCENT = re.compile(r"[-+]?\d+(?:\.\d+)?\s*(?:%|‰)")
# 2) 误差上下文里的数（允许中间夹中文标点，但不吃空话）
_RE_ERROR = re.compile(
    r"(?:误差|相对误差|偏差|残差|relative\s+error|error|Δ|delta)\s*(?:为|是|=|：|:)?\s*"
    r"[-+]?\d+(?:\.\d+)?\s*(?:%|‰)?",
    re.IGNORECASE,
)
# 3) 「数值+单位」：要求数字后有单位，且单位后不再接字母（避免 128core 之类误吃）
_RE_QUANTITY = re.compile(
    rf"\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\s*(?:{_UNIT_ALT})(?![a-zA-Z])"
)
# 4) 等号右值（如 Nu=6.98、Cb=0.6）
_RE_EQUALS = re.compile(
    r"(?<![<>=!])=(?!=)\s*[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
)
# 5) 显式置信/容差
_RE_PM = re.compile(r"[-+]?\d+(?:\.\d+)?\s*(?:±|\+/-)\s*\d+(?:\.\d+)?")

# 证据路径：白名单顶层目录（worktree 里真实存在的产物根）+ 任意多级子路径 + 扩展名
_EVIDENCE_ROOTS = (
    "docs", "artifacts", "runs", "tests", "tools", "src", "scripts", "data",
    "agent_memory", "agent_work", "config", "schemas", "references", "eval_set",
)
_ROOT_ALT = "|".join(_EVIDENCE_ROOTS)
_RE_PATH = re.compile(
    rf"(?<![\w/.\\-])(?:{_ROOT_ALT})/[\w.\-]+(?:/[\w.\-]+)*\.(?:py|jsonl|json|md|csv|png|svg|txt|ya?ml|toml|db|parquet|npz)"
    rf"(?!::)"  # test 锚点单独计，避免同一路径双重计数
)
_RE_TEST_REF = re.compile(r"(?<![\w/])(?:[\w.\-]+/)*[\w.\-]+\.py::[\w:\[\]\-]+")
_RE_SHA256 = re.compile(r"sha256[:=]\s*([0-9a-f]{16,64})", re.IGNORECASE)


@dataclass(frozen=True)
class Citation:
    """一个可复算的引用项。

    kind:
      - ``number``  工程/数值量（自带单位或百分号或处于误差上下文）
      - ``path``    仓库内证据文件路径
      - ``test``    测试锚点（file.py::case）
      - ``digest``  内容指纹锚
    """

    kind: str
    value: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "value": self.value}


@dataclass
class CitationBundle:
    numbers: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    digests: list[str] = field(default_factory=list)

    @property
    def items(self) -> list[Citation]:
        out: list[Citation] = []
        out += [Citation("number", n) for n in self.numbers]
        out += [Citation("path", p) for p in self.paths]
        out += [Citation("test", t) for t in self.tests]
        out += [Citation("digest", d) for d in self.digests]
        return out

    def __len__(self) -> int:
        return len(self.numbers) + len(self.paths) + len(self.tests) + len(self.digests)

    def as_dict(self) -> dict:
        return {
            "numbers": list(self.numbers),
            "paths": list(self.paths),
            "tests": list(self.tests),
            "digests": list(self.digests),
            "count": len(self),
        }


def _dedup(seq: Iterable[str]) -> list[str]:
    """保序去重：按「去空白后的键」判重，保留首次出现的原始写法。"""
    seen: set[str] = set()
    out: list[str] = []
    for s in seq:
        s = (s or "").strip()
        key = re.sub(r"\s+", "", s)
        if key and key not in seen:
            seen.add(key)
            out.append(s)
    return out


def _ordered_union(seqs: Sequence[Sequence[str]]) -> list[str]:
    """按「首现位置」合并多路匹配结果，保证确定性。"""
    return _dedup([x for seq in seqs for x in seq])


def extract_numbers(text: str) -> list[str]:
    """抽取数字引用（保持出现顺序、去重）。"""
    if not text:
        return []
    found: list[str] = []
    for rx in (_RE_PERCENT, _RE_ERROR, _RE_QUANTITY, _RE_PM, _RE_EQUALS):
        for m in rx.finditer(text):
            raw = m.group(0)
            # 误差上下文只保留数值+可选百分号，剔掉中文前缀（保持引用短、可核）
            if rx is _RE_ERROR:
                num = re.search(r"[-+]?\d+(?:\.\d+)?\s*(?:%|‰)?", raw)
                if not num:
                    continue
                raw = num.group(0)
            elif rx is _RE_EQUALS:  # 引用里只留数值，不留 '='
                raw = raw.lstrip("= \t")
            found.append(raw.strip())
    return _ordered_union([found])


def extract_paths(text: str) -> list[str]:
    if not text:
        return []
    return _dedup(m.group(0) for m in _RE_PATH.finditer(text))


def extract_tests(text: str) -> list[str]:
    if not text:
        return []
    return _dedup(m.group(0) for m in _RE_TEST_REF.finditer(text))


def extract_digests(text: str) -> list[str]:
    if not text:
        return []
    return _dedup(m.group(1) for m in _RE_SHA256.finditer(text))


def extract_citations(text: str) -> CitationBundle:
    """一次性抽取四类引用项（数字/路径/测试锚/内容指纹）。"""
    return CitationBundle(
        numbers=extract_numbers(text),
        paths=extract_paths(text),
        tests=extract_tests(text),
        digests=extract_digests(text),
    )