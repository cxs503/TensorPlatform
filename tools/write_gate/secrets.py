#!/usr/bin/env python3
"""凭证泄漏检测 —— 内容级判据（模式 + 熵 + 上下文豁免）。

设计要点
--------
* **模式全部外置**（``policy.secrets.rules``）：本文件只实现匹配/豁免/脱敏机制，
  不含任何具体口令、密钥或"像密钥"的样例字面量 —— 既守零领域语义，也避免把样例
  密钥写进仓库造成二次泄漏。测试里的样例由拼接生成。
* **脱敏是硬约束**：命中值永远只回显前缀 + 掩码 + 长度，判决 JSON 可安全归档进
  artifact（否则门本身变成泄漏渠道）。
* **三层过滤**降误报：规则正则 → 行级豁免（``allow``/``allow_line_patterns``）→
  可选的熵闸（长随机串才有资格判红，纯 hex 摘要/自然语言标识符不判）。
* **行号可定位**：命中带 1-based 行号，便于定位与人工复核。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .model import WriteGateError


@dataclass(frozen=True)
class SecretRule:
    id: str
    pattern: "re.Pattern[str]"
    allow: Tuple["re.Pattern[str]", ...]
    needs_entropy: bool = False


@dataclass(frozen=True)
class SecretHit:
    rule_id: str
    line: int
    excerpt: str
    length: int
    entropy: Optional[float] = None


def _compile(pattern: str, what: str) -> "re.Pattern[str]":
    try:
        return re.compile(pattern)
    except re.error as exc:  # pragma: no cover - 配置错误路径
        raise WriteGateError(f"bad_pattern:{what}:{exc}") from exc


def load_rules(cfg: Dict[str, Any]) -> Tuple[List[SecretRule], List["re.Pattern[str]"]]:
    """从策略装载规则与全局行级豁免。"""
    rules: List[SecretRule] = []
    for rule in cfg["secrets"]["rules"]:
        rules.append(SecretRule(
            id=str(rule["id"]),
            pattern=_compile(str(rule["pattern"]), f"rule:{rule['id']}"),
            allow=tuple(_compile(str(a), f"allow:{rule['id']}") for a in rule.get("allow", [])),
            needs_entropy=bool(rule.get("entropy", False)),
        ))
    line_allow = [_compile(str(p), "allow_line") for p in cfg["secrets"].get("allow_line_patterns", [])]
    return rules, line_allow


def shannon_entropy(text: str) -> float:
    """每字符香农熵（bit）。纯 hex≈3.7、随机 base64≈5.2 —— 熵闸据此分档。"""
    if not text:
        return 0.0
    counts: Dict[str, int] = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    total = float(len(text))
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def redact(token: str, *, keep_prefix: int = 4, max_chars: int = 60) -> str:
    """脱敏：前缀 + 定长掩码 + 长度。绝不回显完整命中值。"""
    head = token[:max(0, keep_prefix)]
    return f"{head}{'*' * min(8, max(1, len(token) - len(head)))}…(len={len(token)})"[:max_chars]


def _entropy_ok(token: str, ent_cfg: Dict[str, Any]) -> Tuple[bool, float]:
    value = shannon_entropy(token)
    if len(token) < int(ent_cfg["min_length"]):
        return False, value
    if value < float(ent_cfg["min_entropy_bits_per_char"]):
        return False, value
    if ent_cfg.get("require_mixed_case", True):
        if not (any(c.islower() for c in token) and any(c.isupper() for c in token)):
            return False, value
    if ent_cfg.get("require_digit", True) and not any(c.isdigit() for c in token):
        return False, value
    # 标识符 vs 随机串的判别子：camelCase 长标识符数字占比极低（实测 2%），
    # 随机 base64 一般 >20%。只用"有数字"挡不住长标识符，故加占比闸。
    digits = sum(1 for c in token if c.isdigit())
    if digits / len(token) < float(ent_cfg.get("min_digit_ratio", 0.1)):
        return False, value
    return True, value


def scan_text(text: str, cfg: Dict[str, Any], *,
              rules: Optional[Sequence[SecretRule]] = None,
              line_allow: Optional[Sequence["re.Pattern[str]"]] = None,
              path: str = "") -> Tuple[List[SecretHit], List[Dict[str, Any]]]:
    """扫文本；返回 ``(命中, 豁免留痕)``。命中带脱敏片段与行号。"""
    if not isinstance(text, str):
        raise WriteGateError("invalid_content_type")
    if rules is None or line_allow is None:
        rules, line_allow = load_rules(cfg)
    max_line = int(cfg["secrets"]["max_line_chars"])
    keep = int(cfg["output"]["redact_keep_prefix"])
    max_chars = int(cfg["output"]["max_excerpt_chars"])
    ent_cfg = cfg["secrets"]["entropy"]
    limit = int(cfg["output"]["max_violations_per_file"])

    hits: List[SecretHit] = []
    suppressed: List[Dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if len(line) > max_line:
            line = line[:max_line]
        if any(p.search(line) for p in line_allow):
            continue
        for rule in rules:
            for match in rule.pattern.finditer(line):
                # 有捕获组时用组 1：正式规则把"凭证值"放在组里，脱敏粒度才对得上
                token = match.group(1) if match.groups() and match.group(1) else match.group(0)
                if any(a.search(line) for a in rule.allow):
                    suppressed.append({"path": path, "rule_id": rule.id, "line": lineno,
                                       "reason": "line_allow"})
                    continue
                entropy: Optional[float] = None
                if rule.needs_entropy and ent_cfg["enabled"]:
                    ok, entropy = _entropy_ok(token, ent_cfg)
                    if not ok:
                        suppressed.append({"path": path, "rule_id": rule.id, "line": lineno,
                                           "reason": "entropy_gate",
                                           "entropy": round(entropy, 3)})
                        continue
                hits.append(SecretHit(rule_id=rule.id, line=lineno,
                                      excerpt=redact(token, keep_prefix=keep, max_chars=max_chars),
                                      length=len(token), entropy=None if entropy is None else round(entropy, 3)))
                if len(hits) >= limit:
                    return hits, suppressed
    return hits, suppressed


def looks_binary(data: bytes) -> bool:
    """粗略二进制判据：前 8KB 内出现 NUL 或解码失败。"""
    head = data[:8192]
    if b"\x00" in head:
        return True
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def scan_bytes(data: bytes, cfg: Dict[str, Any], *, path: str = ""):
    """扫字节内容；超预算或二进制返回 ``(hits=[], advisories=[跳过说明])``。"""
    if len(data) > int(cfg["secrets"]["max_blob_bytes"]):
        return [], [{"path": path, "reason": "blob_too_large", "bytes": len(data)}]
    if looks_binary(data):
        return [], [{"path": path, "reason": "binary_skipped", "bytes": len(data)}]
    return scan_text(data.decode("utf-8", errors="replace"), cfg, path=path)