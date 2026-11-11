"""秘密泄露扫描引擎（pattern 库 + 熵闸 + 已知值精确匹配 + 豁免）。

与仓内既有 ``tools/write_gate`` 的关系
-------------------------------------
``write_gate`` 是**提交前**的内容判据（只覆盖 staged 变更）。本模块把同一套
"模式→豁免→熵闸"机制抽成**可复用引擎**，并补齐三件事：命中值的**区间**（用于
脱敏改写）、**已知值摘要精确匹配**、**多出口**（提交 / 报告 / 工件）挂接。

设计约束
--------
* 规则/熵闸/豁免/已知值注册表全部是**数据**（``config/secret_hygiene/``），
  代码里没有任何具体凭证样例 —— 既守零领域语义，也避免把样例写进仓造成二次泄漏。
* 命中值只在内存里存在；对外只给 ``<REDACTED:type:len>`` 与行号。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_RULES = "config/secret_hygiene/rules.json"
DEFAULT_POLICY = "data/write_gate/policy.json"


class HygieneError(ValueError):
    """规则/配置错误（fail fast，不允许静默放宽判据）。"""


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: "re.Pattern[str]"
    allow: Tuple["re.Pattern[str]", ...] = ()
    needs_entropy: bool = False


@dataclass(frozen=True)
class Hit:
    rule_id: str
    line: int
    start: int
    end: int
    length: int
    entropy: Optional[float] = None
    path: str = ""

    def redacted(self) -> str:
        return f"<REDACTED:{self.rule_id}:{self.length}>"

    def to_dict(self) -> Dict[str, Any]:
        """对外 JSON：**不含明文**，只留脱敏标记与定位。"""
        return {
            "rule_id": self.rule_id,
            "path": self.path,
            "line": self.line,
            "length": self.length,
            "entropy": None if self.entropy is None else round(self.entropy, 3),
            "redacted": self.redacted(),
        }


@dataclass
class HygieneConfig:
    rules: List[Rule] = field(default_factory=list)
    allow_lines: List["re.Pattern[str]"] = field(default_factory=list)
    entropy: Dict[str, Any] = field(default_factory=dict)
    known_digests: set[str] = field(default_factory=set)
    binary_fields: List["re.Pattern[str]"] = field(default_factory=list)
    known_min_len: int = 8
    max_violations: int = 200
    source: str = ""

    def to_public_dict(self) -> Dict[str, Any]:
        """可安全归档的配置摘要（规则 id 与计数，不含正则原文）。"""
        return {
            "source": self.source,
            "rule_ids": [r.id for r in self.rules],
            "entropy_enabled": bool(self.entropy.get("enabled")),
            "entropy_min_length": self.entropy.get("min_length"),
            "entropy_min_bits_per_char": self.entropy.get("min_entropy_bits_per_char"),
            "known_value_digests": len(self.known_digests),
            "allow_line_patterns": len(self.allow_lines),
            "binary_field_patterns": len(self.binary_fields),
            "max_violations": self.max_violations,
        }


def _compile(pattern: str, what: str) -> "re.Pattern[str]":
    try:
        return re.compile(pattern)
    except re.error as exc:  # pragma: no cover - 配置错误路径
        raise HygieneError(f"bad_pattern:{what}:{exc}") from exc


def load_config(rules_path: str | Path = DEFAULT_RULES,
                root: str | Path = ".",
                known_registry: str | Path | None = None) -> HygieneConfig:
    """装载规则库 + 已知值摘要注册表。"""
    root = Path(root)
    path = Path(rules_path)
    if not path.is_absolute():
        path = root / path
    if not path.exists():
        raise HygieneError(f"rules_not_found:{path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema") != "secret_hygiene_rules/1.0":
        raise HygieneError(f"rules_schema_mismatch:{doc.get('schema')}")

    rules = [
        Rule(id=str(item["id"]),
             pattern=_compile(str(item["pattern"]), f"rule:{item['id']}"),
             allow=tuple(_compile(str(a), f"allow:{item['id']}") for a in item.get("allow", [])),
             needs_entropy=bool(item.get("entropy", False)))
        for item in doc.get("rules", [])
    ]
    if not rules:
        raise HygieneError("rules_empty")
    allow_lines = [_compile(str(p), "allow_line")
                   for p in doc.get("allow_line_patterns", [])]
    binary_fields = [_compile(str(p), "binary_field")
                     for p in doc.get("binary_field_patterns", [])]
    entropy = dict(doc.get("entropy", {}))
    output = dict(doc.get("output", {}))
    known_cfg = dict(doc.get("known_values", {}))

    digests: set[str] = set()
    if known_cfg.get("enabled"):
        rel = known_registry or known_cfg.get("registry")
        if rel:
            reg_path = Path(rel)
            if not reg_path.is_absolute():
                reg_path = root / reg_path
            if reg_path.exists():
                reg_doc = json.loads(reg_path.read_text(encoding="utf-8"))
                digests = {str(d).lower() for d in reg_doc.get("digests", [])}
    return HygieneConfig(
        rules=rules, allow_lines=allow_lines, entropy=entropy, known_digests=digests,
        binary_fields=binary_fields,
        known_min_len=int(known_cfg.get("min_token_length", 8)),
        max_violations=int(output.get("max_violations_per_file", 200)),
        source=str(path),
    )


def shannon_entropy(text: str) -> float:
    """每字符香农熵（bit）。纯 hex≈3.7、随机 base64≈5.2。"""
    if not text:
        return 0.0
    counts: Dict[str, int] = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    total = float(len(text))
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def entropy_ok(token: str, cfg: Dict[str, Any]) -> Tuple[bool, float]:
    """熵闸：长随机串才有资格判红；纯 hex 摘要、camelCase 长标识符不判。"""
    bits = shannon_entropy(token)
    if len(token) < int(cfg.get("min_length", 32)):
        return False, bits
    if bits < float(cfg.get("min_entropy_bits_per_char", 4.0)):
        return False, bits
    if cfg.get("require_mixed_case", True):
        if not (any(c.islower() for c in token) and any(c.isupper() for c in token)):
            return False, bits
    if cfg.get("require_digit", True):
        digits = sum(1 for c in token if c.isdigit())
        if not digits:
            return False, bits
        if digits / len(token) < float(cfg.get("min_digit_ratio", 0.1)):
            return False, bits
    return True, bits


_TOKEN_SPLIT = re.compile(r"[\s'\"`,;:()\[\]{}<>=|&]+")
# token 本身（连续的非分隔符字符），用于定位已知值命中的区间
_TOKEN_SPLIT_RUN = re.compile(r"[^\s'\"`,;:()\[\]{}<>=|&]+")


def _known_value_hits(line: str, line_no: int, cfg: HygieneConfig) -> List[Hit]:
    """已知值精确匹配：对行内 token 求 sha256 与注册表比对（长度 + 摘要双闸）。

    位置用 token 正则的 match 区间给出（不用 ``line.find``，避免同一行重复 token
    把区间算错、进而脱敏改写错位置）。
    """
    hits: List[Hit] = []
    if not cfg.known_digests:
        return hits
    for match in _TOKEN_SPLIT_RUN.finditer(line):
        token = match.group(0)
        if len(token) < cfg.known_min_len:
            continue
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        if digest in cfg.known_digests:
            hits.append(Hit(rule_id="known_value", line=line_no, start=match.start(),
                            end=match.end(), length=len(token)))
    return hits


def scan_text(text: str, cfg: HygieneConfig, *, path: str = "") -> List[Hit]:
    """扫描文本，返回命中列表（含脱敏标记与区间）。

    三层过滤降误报：规则正则 → 行级豁免 → 熵闸；已知值匹配独立成一路。
    """
    hits: List[Hit] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if any(p.search(line) for p in cfg.allow_lines):
            continue
        for rule in cfg.rules:
            for match in rule.pattern.finditer(line):
                token = (match.group(1) if match.groups() and match.group(1)
                         else match.group(0))
                if any(a.search(line) for a in rule.allow):
                    continue
                entropy: Optional[float] = None
                if rule.needs_entropy and cfg.entropy.get("enabled"):
                    ok, entropy = entropy_ok(token, cfg.entropy)
                    if not ok:
                        continue
                if match.groups() and match.group(1) and match.group(1) != match.group(0):
                    start, end = match.start(1), match.end(1)
                else:
                    start, end = match.start(), match.end()
                hits.append(Hit(rule_id=rule.id, line=line_no, start=start, end=end,
                                length=len(token), entropy=entropy, path=path))
                if len(hits) >= cfg.max_violations:
                    return hits
        hits.extend(_known_value_hits(line, line_no, cfg))
        if len(hits) >= cfg.max_violations:
            return hits
    return hits


def scan_payload(payload: Any, cfg: HygieneConfig, *, path: str = "",
                 suppressed: Optional[List[str]] = None) -> List[Hit]:
    """递归扫描结构化载荷（报告 dict/list）里的所有字符串叶子。

    ``path`` 形如 ``payload.figures[0].png_base64``，便于定位是哪个字段在泄漏。

    二进制字段（图件 base64 等，按 ``binary_field_patterns`` 判定）跳过匹配，但会把
    字段指针记进 ``suppressed`` —— 豁免必须留痕，不允许静默放行。
    """
    hits: List[Hit] = []

    def _walk(node: Any, pointer: str) -> None:
        if any(p.search(pointer.split(".")[-1]) for p in cfg.binary_fields):
            if suppressed is not None:
                suppressed.append(pointer)
            return
        if isinstance(node, str):
            for hit in scan_text(node, cfg, path=pointer):
                hits.append(Hit(rule_id=hit.rule_id, line=hit.line, start=hit.start,
                                end=hit.end, length=hit.length, entropy=hit.entropy,
                                path=pointer))
        elif isinstance(node, dict):
            for key, value in node.items():
                _walk(value, f"{pointer}.{key}" if pointer else str(key))
        elif isinstance(node, (list, tuple)):
            for index, value in enumerate(node):
                _walk(value, f"{pointer}[{index}]")

    _walk(payload, path)
    return hits


def scan_file(path: str | Path, cfg: HygieneConfig,
              max_bytes: int = 2_000_000) -> List[Hit]:
    """扫描单个文件（超限/二进制按"不可判定"跳过，由出口决定是否放行）。"""
    file_path = Path(path)
    data = file_path.read_bytes()[:max_bytes]
    if b"\x00" in data[:8192]:
        return []
    text = data.decode("utf-8", "replace")
    return scan_text(text, cfg, path=str(file_path))


def scan_paths(paths: Iterable[str | Path], cfg: HygieneConfig) -> List[Hit]:
    hits: List[Hit] = []
    for path in paths:
        hits.extend(scan_file(path, cfg))
    return hits