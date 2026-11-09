#!/usr/bin/env python3
"""判据可裁决性分类器（P151W1-N10 / E1）。

输入一段任务书文本（可含注入的「证据槽位」区块），输出三维命中 + 强弱判定 + 缺失维，
**每个命中都带 (维, 规则 id, 起止偏移, 原文片段)**，使审计可逐例复核（不是黑箱打分）。

判据（先声明，再实现，机器可裁）：

* ``strong`` ⇔ 判据段内 P1 与 P2 同时命中；
* ``weak``   ⇔ P1 或 P2 缺失（P3 缺失只进 ``advisory``，不降级）。

纯函数、无时钟、无 IO、无随机：同输入必同输出。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

from . import schema as S

_P1 = S.compile_p1()
_P3 = S.compile_p3()


@dataclass(frozen=True)
class Hit:
    dim: str
    rule_id: str
    start: int
    end: int
    text: str

    def as_dict(self) -> Dict[str, Any]:
        return {"dim": self.dim, "rule": self.rule_id,
                "span": [self.start, self.end], "text": self.text}


@dataclass(frozen=True)
class Judgment:
    verdict: str                       # "strong" | "weak"
    dims: Dict[str, List[Hit]]         # P1/P2/P3 → 命中列表
    missing: Tuple[str, ...]           # 缺的维（P1/P2/P3 中有意义者）
    scope: str                         # "criteria" | "whole"
    segment_chars: int
    raw_chars: int
    injected_blocks: int
    flags: Tuple[str, ...] = ()

    def has(self, dim: str) -> bool:
        return bool(self.dims.get(dim))

    @property
    def hard_missing(self) -> Tuple[str, ...]:
        """硬维缺失（决定 enforce/block）：仅 P1、P2。"""
        return tuple(d for d in ("P1", "P2") if not self.has(d))

    def as_dict(self, with_hits: bool = True) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "verdict": self.verdict,
            "dims": {k: bool(v) for k, v in self.dims.items()},
            "missing": list(self.missing),
            "hard_missing": list(self.hard_missing),
            "scope": self.scope,
            "segment_chars": self.segment_chars,
            "raw_chars": self.raw_chars,
            "injected_blocks": self.injected_blocks,
            "flags": list(self.flags),
        }
        if with_hits:
            out["hits"] = {k: [h.as_dict() for h in v] for k, v in self.dims.items()}
        return out


def _hits_for(dim: str, segment: str, offset: int,
              rules: Tuple[Tuple[str, Any], ...]) -> List[Hit]:
    seen: List[Hit] = []
    for rule_id, pat in rules:
        for m in pat.finditer(segment):
            seen.append(Hit(dim=dim, rule_id=rule_id, start=offset + m.start(),
                            end=offset + m.end(), text=m.group(0)))
    return seen


def classify_text(text: str, *, scope: str = "criteria") -> Judgment:
    """判定一段文本。

    ``scope="criteria"``（默认）：先切判据段再判（推荐用于任务书）；
    ``scope="whole"``：整篇判定（用于已经是纯判据的单元，例如合成语料的单条样例）。
    """
    raw = text or ""
    blocks = len(S.slot_block_bounds(raw))
    stripped = S.strip_slot_blocks(raw)          # 关键：注入区块永不参与判定
    if scope == "criteria":
        segment, used = S.criteria_segment(stripped)
    else:
        segment, used = stripped, "whole"
    offset = stripped.find(segment) if segment else 0
    offset = max(offset, 0)

    dims: Dict[str, List[Hit]] = {
        "P1": _hits_for("P1", segment, offset, _P1),
        "P3": _hits_for("P3", segment, offset, _P3),
        "P2": [Hit(dim="P2", rule_id=f"P2.{kind}", start=offset + segment.find(kind),
                   end=offset + segment.find(kind) + len(kind), text=kind)
               for kind in S.REFERENCE_KINDS if kind in segment],
    }
    missing = tuple(d.key for d in S.DIMENSIONS if not dims[d.key])
    verdict = "weak" if (not dims["P1"] or not dims["P2"]) else "strong"
    return Judgment(verdict=verdict, dims=dims, missing=missing, scope=used,
                    segment_chars=len(segment), raw_chars=len(raw),
                    injected_blocks=blocks)


def classify_goal(goal: str) -> Judgment:
    """任务书判定：**任务书级 fail-closed**。

    * 找到显式判据段 → 按该段判 P1/P2；
    * 一个判据段标记都没有 → 记 ``flags=("no_criteria_section",)`` 并判 ``weak``：
      没有显式判据段的任务书本身即不可机器裁决（维度命中原样保留，供审计观察，
      但不许凭任务描述里的零散数字冒充判据——这是「描述里的数字」假阳面的结构性堵法）。
    """
    j = classify_text(goal, scope="criteria")
    if j.scope == "whole":
        return replace(j, verdict="weak", flags=("no_criteria_section",))
    return j


def classify_batch(items: List[Dict[str, Any]], *, text_key: str = "text",
                   scope: str = "whole") -> List[Dict[str, Any]]:
    """批量判定（保持输入顺序；不增删字段，便于与标签逐例对账）。"""
    out: List[Dict[str, Any]] = []
    for it in items:
        j = classify_text(it.get(text_key, ""), scope=scope)
        row = {"id": it.get("id"), "label": it.get("label"), "why": it.get("why")}
        row.update(j.as_dict(with_hits=False))
        out.append(row)
    return out


def confusion(rows: List[Dict[str, Any]], positive: str = "strong") -> Dict[str, Any]:
    """对账混淆矩阵 + 每类 precision/recall/F1（标签即真值，可逐例复核）。"""
    tp = fp = fn = tn = 0
    errors: List[str] = []
    for r in rows:
        gold = r.get("label")
        pred = r.get("verdict")
        if gold is None:
            continue
        if gold == positive and pred == positive:
            tp += 1
        elif gold != positive and pred == positive:
            fp += 1
            errors.append(str(r.get("id")))
        elif gold == positive and pred != positive:
            fn += 1
            errors.append(str(r.get("id")))
        else:
            tn += 1
    precision = 0.0 if (tp + fp) == 0 else tp / (tp + fp)
    recall = 0.0 if (tp + fn) == 0 else tp / (tp + fn)
    f1_pos = 0.0 if (2 * tp + fp + fn) == 0 else 2 * tp / (2 * tp + fp + fn)
    # 负类（weak）同法计算
    precision_n = 0.0 if (tn + fn) == 0 else tn / (tn + fn)
    recall_n = 0.0 if (tn + fp) == 0 else tn / (tn + fp)
    f1_neg = 0.0 if (2 * tn + fn + fp) == 0 else 2 * tn / (2 * tn + fn + fp)
    return {
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "positive_class": positive,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1_pos, 6),
        "f1_positive": round(f1_pos, 6),
        "f1_negative": round(f1_neg, 6),
        "macro_f1": round((f1_pos + f1_neg) / 2, 6),
        "support": {"positive": tp + fn, "negative": tn + fp},
        "error_ids": sorted(errors),
    }