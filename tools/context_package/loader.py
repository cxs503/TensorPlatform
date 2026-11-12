"""loader —— 干货包配置、文本归一、确定性 token 估计、shingle 去重。

平台共性机制，零领域语义：本模块只处理"文本→候选→预算"的通用算术，
域词条一律来自配置/DB/工件注入。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "context_package.v1.json"

_CJK = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_WORD = re.compile(r"[A-Za-z0-9_]+")
_TOKEN_SPLIT = re.compile(r"[\s,;:/\\|()+\[\]{}<>\"'`，。；：、（）【】“”‘’！？!?=\-]+")
_STOP = {"the", "and", "for", "with", "from", "this", "that", "are", "was", "not", "all",
         "有", "的", "了", "在", "是", "和", "与", "或", "对", "把", "被", "为", "要", "就",
         "不", "一个", "本单", "任务", "工作", "必须", "需要", "以及", "并且", "如果"}


def load_config(path: Path | str | None = None) -> dict:
    """读协议配置；缺文件/坏文件退回内置最小配置（degraded，不崩）。"""
    p = Path(path) if path else DEFAULT_CONFIG
    try:
        with open(p, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:
        cfg = {
            "schema_version": "context-package/v1",
            "defaults": {"total_budget_tokens": 1200, "per_entry_max_tokens": 110,
                         "per_slot_max_items": 4, "pool_limit": 300,
                         "dedupe_jaccard": 0.6, "min_relevance": 0.08,
                         "density_gate": "require_decision", "as_of_lookback_hours": 24},
            "token_estimator": {"cjk_per_token": 1.0, "ascii_chars_per_token": 3.6,
                                "per_entry_overhead": 8},
            "decisions": {"reuse": "复用既有能力", "avoid": "规避失败模式"},
            "slots": {"ko_index": {"source": "knowledge_base", "share": 0.6,
                                   "required": True, "decisions": ["reuse", "avoid"]},
                      "negative_knowledge": {"source": "negative_knowledge_ledger",
                                             "share": 0.4, "required": True,
                                             "decisions": ["avoid"]}},
            "task_classes": {"generic": {"label": "通用", "detect": [],
                                         "slots": ["ko_index", "negative_knowledge"],
                                         "budget_tokens": 1000}},
            "ledger": {"jsonl_dir": "runs/context_injections"},
            "mode_env": "CONTEXT_PACKAGE_MODE",
            "modes": ["off", "report", "enforce"],
            "_degraded_config": True,
        }
    cfg.setdefault("_config_path", str(p))
    return cfg


# --------------------------------------------------------------------------- #
# 确定性 token 估计（两臂同口径）
# --------------------------------------------------------------------------- #
def est_tokens(text: Any, cfg: Mapping[str, Any] | None = None) -> int:
    cfg = cfg or load_config()
    te = cfg.get("token_estimator") or {}
    cjk_w = float(te.get("cjk_per_token", 1.0)) or 1.0
    asc_w = float(te.get("ascii_chars_per_token", 3.6)) or 3.6
    s = "" if text is None else str(text)
    if not s:
        return 0
    n_cjk = len(_CJK.findall(s))
    n_other = max(0, len(s) - n_cjk)
    return int(round(n_cjk / cjk_w + n_other / asc_w))


# --------------------------------------------------------------------------- #
# 词元 / shingle
# --------------------------------------------------------------------------- #
def terms(text: Any) -> set[str]:
    """归一化词元集合：ASCII 词 + CJK 二元组（无分词依赖，确定性）。"""
    s = ("" if text is None else str(text)).lower()
    out: set[str] = set()
    for w in _WORD.findall(s):
        if len(w) >= 2 and w not in _STOP:
            out.add(w)
    cjk_runs = re.findall(r"[\u3400-\u9fff]+", s)
    for run in cjk_runs:
        if len(run) == 1:
            continue
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if bg not in _STOP:
                out.add(bg)
    return out


def shingles(text: Any, k: int = 4) -> set[str]:
    toks = [t for t in _TOKEN_SPLIT.split(("" if text is None else str(text)).lower()) if t]
    if len(toks) < k:
        return {" ".join(toks)} if toks else set()
    return {" ".join(toks[i:i + k]) for i in range(len(toks) - k + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / float(len(a | b))


def overlap_score(query_terms: set[str], text: Any) -> float:
    """查询词元与候选文本的重合度（余弦式归一，∈[0,1]）。"""
    ct = terms(text)
    if not query_terms or not ct:
        return 0.0
    inter = len(query_terms & ct)
    if inter <= 0:
        return 0.0
    return inter / (inter + 4.0)


def clip(text: Any, max_chars: int) -> str:
    s = re.sub(r"\s+", " ", "" if text is None else str(text)).strip()
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars].rstrip()
    for sep in ("；", "。", ";", "，", ","):
        idx = cut.rfind(sep)
        if idx >= max_chars * 0.55:
            return cut[:idx] + "…"
    return cut + "…"


def sha16(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8", "replace"))
        h.update(b"\x1f")
    return h.hexdigest()[:16]


def iter_terms_weighted(text: Any) -> Iterable[tuple[str, int]]:
    """(term, 出现次数) —— 供 hit attribution 使用。"""
    s = ("" if text is None else str(text)).lower()
    counts: dict[str, int] = {}
    for t in terms(s):
        counts[t] = s.count(t)
    return counts.items()


def env_flag(name: str, default: str = "") -> str:
    return os.environ.get(name, default)