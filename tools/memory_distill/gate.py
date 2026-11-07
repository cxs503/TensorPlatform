"""判例门控：确定性、可机裁的准入审查（对应「门控审后 append-only 入库」）。

门控只判**形式可裁**的事，不判价值观：
  G1 引用门槛    >=3 个引用数字或证据路径（战役判据原文）
  G2 长度门槛    12 <= len <= max_len（bootstrap 单条 <=120 字）
  G3 秘密/指令   禁止 token/密码/Bearer/指令引文（判例只留成果，不留过程）
  G4 过程流水账  禁止「我先/现在/接下来」这类自述流水账语气
  G5 重复门槛   与既有 PLAYBOOK 行/池内判例的字符二元组 Jaccard < threshold

每条拒绝都返回稳定错误码，便于统计漏斗且可被测试红/绿验证。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .citations import CitationBundle, extract_citations

# 秘密与指令引文的禁用面（命中即拒，不做「善意忽略」）
_SECRET_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"bearer\s+[a-z0-9._\-]{8,}",
        r"authorization\s*:",
        r"api[_-]?key",
        r"password\s*=",
        r"\bsk-[a-z0-9]{8,}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"/root/tp-agent/agent\.env",
        r"\$TENSOR_AGENT_PASS",
    )
]
# 过程流水账/指令引文（判例要的是可复用打法，不是「我干了什么」的日记）
_PROCESS_PATTERNS = [
    re.compile(p)
    for p in (
        r"我(?:先|已|将|现在|然后)",
        r"现在(?:开始|动手)",
        r"接下来",
        r"按(?:用户|owner|领导)要求",
        r"如(?:上|下)所述",
        r"^\s*(?:step\s*\d+|第[一二三四五六七八九十]步)[:：]",
    )
]

DEFAULT_MAX_LEN = 120
DEFAULT_MIN_LEN = 12


@dataclass(frozen=True)
class GateConfig:
    min_citations: int = 3
    min_len: int = DEFAULT_MIN_LEN
    max_len: int = DEFAULT_MAX_LEN
    dup_threshold: float = 0.85


@dataclass
class GateDecision:
    passed: bool
    code: str
    reasons: list[str] = field(default_factory=list)
    citations: CitationBundle | None = None

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "code": self.code,
            "reasons": list(self.reasons),
            "citations": (self.citations.as_dict() if self.citations else None),
        }


def _bigrams(text: str) -> set[str]:
    s = re.sub(r"\s+", "", text)
    if len(s) < 2:
        return {s} if s else set()
    return {s[i : i + 2] for i in range(len(s) - 1)}


def jaccard(a: str, b: str) -> float:
    """字符二元组 Jaccard 相似度（中文无需分词，确定性）。"""
    sa, sb = _bigrams(a), _bigrams(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def max_similarity(text: str, corpus: Iterable[str]) -> tuple[float, str]:
    """返回 (最大相似度, 最相似的既有条目)；空语料返回 (0.0, "")。"""
    best, best_item = 0.0, ""
    for item in corpus:
        s = jaccard(text, item)
        if s > best:
            best, best_item = s, item
    return best, best_item


def gate_candidate(
    text: str,
    existing: Sequence[str] = (),
    config: GateConfig | None = None,
) -> GateDecision:
    """对一条候选判例做确定性门控；第一个命中的失败码即返回码（稳定排序）。"""
    cfg = config or GateConfig()
    body = (text or "").strip()

    if not body:
        return GateDecision(False, "REJECT_EMPTY", ["空文本"])

    cit = extract_citations(body)
    if len(body) > cfg.max_len:
        return GateDecision(
            False, "REJECT_LENGTH", [f"长度 {len(body)} > {cfg.max_len}"], cit
        )
    if len(body) < cfg.min_len:
        return GateDecision(
            False, "REJECT_LENGTH", [f"长度 {len(body)} < {cfg.min_len}"], cit
        )
    for rx in _SECRET_PATTERNS:
        m = rx.search(body)
        if m:
            return GateDecision(
                False, "REJECT_SECRET", [f"命中秘密/凭据模式: {m.group(0)[:24]}"], cit
            )
    for rx in _PROCESS_PATTERNS:
        m = rx.search(body)
        if m:
            return GateDecision(
                False, "REJECT_PROCESS_LOG", [f"过程流水账语气: {m.group(0)[:16]}"], cit
            )
    if len(cit) < cfg.min_citations:
        return GateDecision(
            False,
            "REJECT_CITATIONS",
            [f"引用项 {len(cit)} < {cfg.min_citations}（数字{len(cit.numbers)}/路径{len(cit.paths)}）"],
            cit,
        )
    sim, nearest = max_similarity(body, existing)
    if sim >= cfg.dup_threshold:
        return GateDecision(
            False,
            "REJECT_DUPLICATE",
            [f"与既有条目相似度 {sim:.3f} >= {cfg.dup_threshold}: {nearest[:40]}"],
            cit,
        )
    return GateDecision(True, "ACCEPT", ["通过 G1-G5"], cit)