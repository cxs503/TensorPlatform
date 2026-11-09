#!/usr/bin/env python3
"""判据可裁决性 schema（P151W1-N10 / E1–E5 的前置门结构定义）。

三维结构（每条判据文本的「可机器裁决」必要成分）：

* ``P1`` **数值阈值**：形状为 ``<比较符> [=] <数>`` 或 ``±<数>``。
* ``P2`` **参考种类**：来自一个**封闭词表**的显式词（解析解/实验/文献/基线/自洽/守恒）。
* ``P3`` **工件路径**：``artifacts/`` / ``runs/`` / ``.json`` / ``.png``。

纪律（与平台边界铁律一致）
--------------------------
1. 本文件**零领域语义**：只有形状/符号/方法论词表，无任何领域词条、域判据、域算例。
2. 词表与形状全部数据化（``data/quality/judgeability/schema.json`` 可覆盖），本文件给默认值，
   使「判据形状」可被复审与增删而不动代码。
3. 本模块是**纯函数库**：不读库、不写盘、不看时钟——同输入必同输出（E2 可复现性的前提）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA_ID = "tp.quality.judgeability/v1"
SCHEMA_VERSION = "1.0.0"

#: 注入的建议槽位区块的定界标记。分类器**永远先剥离**该区块再判定，因此
#: 「注入建议 → 判据变强」这类自证满足（self-satisfaction）在结构上不可能发生。
SLOT_BLOCK_BEGIN = "【证据槽位|EVIDENCE-SLOTS-BEGIN】"
SLOT_BLOCK_END = "【证据槽位|EVIDENCE-SLOTS-END】"

#: 判据段落的起头标记（按优先级）。真实任务书前半段是任务描述，数字常出现在描述里，
#: 若整篇扫描会把「Ma 2–10」之类的描述性数字误判成 P1；先切出判据段可显著提高精度。
CRITERIA_MARKERS: Tuple[str, ...] = (
    "机器可算验收判据",
    "机器验收判据",
    "机器可裁决判据",
    "可算验收判据",
    "验收判据",
    "验收标准",
    "接受判据",
)

#: 判据段之后常出现的下一节起点（用于截断，避免把交付/纪律里的路径算进判据段）。
SECTION_STOPS: Tuple[str, ...] = ("【交付】", "【纪律】", "【实现路径】", "【工程目标", "【物理目标")

#: P2 封闭词表（方法论词，非领域词）。命中需出现该词的显式形态。
REFERENCE_KINDS: Tuple[str, ...] = ("解析解", "实验", "文献", "基线", "自洽", "守恒")

#: 数值形状：可选正负号 + 整数/小数 + 可选科学计数指数。
_NUM = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
#: 数后面的边界：不允许紧跟字母/数字/下划线（避免 task_key=a1、P22、3ms 之类误判）。
_TAIL = r"\s*(?:%|‰)?(?![\w.])"   # 数后不许紧跟字母/数字/下划线/小数点（"1.2GB" 不许截成 "1."）

#: P1 形状表（每条给出 rule_id 便于逐例复核）
P1_RULES: Tuple[Tuple[str, str], ...] = (
    ("P1.comparator", rf"[<>≤≥＜＞⩽⩾]\s*=?\s*{_NUM}{_TAIL}"),
    ("P1.plusminus", rf"±\s*{_NUM}{_TAIL}"),
    ("P1.equal", rf"==?\s*{_NUM}{_TAIL}"),
)

#: P3 形状表
P3_RULES: Tuple[Tuple[str, str], ...] = (
    ("P3.dir_artifacts", r"artifacts/"),
    ("P3.dir_runs", r"runs/"),
    ("P3.file_json", r"\.json(?![0-9A-Za-z_])"),
    ("P3.file_png", r"\.png(?![0-9A-Za-z_])"),
)


@dataclass(frozen=True)
class DimensionSpec:
    """一维判据的结构说明（可序列化为 JSON schema 片段，供报告与审计引用）。"""

    key: str
    title: str
    kind: str                     # "threshold" | "reference" | "artifact"
    required_for_strong: bool
    shapes: Tuple[str, ...]
    description: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "kind": self.kind,
            "required_for_strong": self.required_for_strong,
            "shapes": list(self.shapes),
            "description": self.description,
        }


DIMENSIONS: Tuple[DimensionSpec, ...] = (
    DimensionSpec(
        key="P1",
        title="数值阈值",
        kind="threshold",
        required_for_strong=True,
        shapes=tuple(rid for rid, _ in P1_RULES),
        description="出现形如 `<= N` / `>= N` / `±N` / `= N` 的数值阈值（含全角符号与科学计数）。",
    ),
    DimensionSpec(
        key="P2",
        title="参考种类",
        kind="reference",
        required_for_strong=True,
        shapes=tuple(REFERENCE_KINDS),
        description="出现封闭词表中的显式参考种类词，标明对标来源。",
    ),
    DimensionSpec(
        key="P3",
        title="工件路径",
        kind="artifact",
        required_for_strong=False,
        shapes=tuple(rid for rid, _ in P3_RULES),
        description="出现可复算的工件位置（artifacts/、runs/、.json、.png）；缺此维只记 advisory。",
    ),
)

#: fail-closed 分级：模式 → 缺 P1/P2 时的动作
GATE_MODES: Tuple[str, ...] = ("off", "advisory", "enforce", "block")

MODE_ACTIONS: Dict[str, str] = {
    "off": "pass",              # 完全旁路（可回滚开关）
    "advisory": "pass_logged",  # 只记录，不改派发（默认）
    "enforce": "inject",        # 回填「证据槽位」建议后照常派发
    "block": "refuse",          # 缺 P1 或 P2 硬拦，不派发
}


def compile_p1() -> Tuple[Tuple[str, "re.Pattern[str]"], ...]:
    return tuple((rid, re.compile(pat)) for rid, pat in P1_RULES)


def compile_p3() -> Tuple[Tuple[str, "re.Pattern[str]"], ...]:
    return tuple((rid, re.compile(pat)) for rid, pat in P3_RULES)


def reference_pattern(kind: str) -> "re.Pattern[str]":
    """参考种类词按显式词匹配（不做同义扩展：词表封闭是判据可复核的前提）。"""
    return re.compile(re.escape(kind))


def as_json_schema() -> Dict[str, Any]:
    """三维结构 schema 的可发布形态（报告/审计 JSON 的 ``schema`` 段）。"""
    return {
        "schema": SCHEMA_ID,
        "version": SCHEMA_VERSION,
        "dimensions": [d.as_dict() for d in DIMENSIONS],
        "reference_kinds": list(REFERENCE_KINDS),
        "criteria_markers": list(CRITERIA_MARKERS),
        "section_stops": list(SECTION_STOPS),
        "gate_modes": list(GATE_MODES),
        "mode_actions": dict(MODE_ACTIONS),
        "strength_rule": {
            "strong": "P1 与 P2 同时命中",
            "weak": "P1 或 P2 缺失",
            "advisory_only": ["P3"],
        },
        "slot_block": {"begin": SLOT_BLOCK_BEGIN, "end": SLOT_BLOCK_END,
                       "note": "分类前必剥离；注入文本不含数字，避免自证满足"},
    }


def load_schema(path: Optional[str] = None) -> Dict[str, Any]:
    """读外置 schema（若文件存在则与内置默认合并，未知键忽略）；缺文件 → 内置默认。

    降级纪律：读不到/读坏了不许崩，返回内置默认并在 ``_fallback`` 段留痕。
    """
    default = as_json_schema()
    if not path:
        return default
    p = Path(path)
    if not p.is_file():
        default["_fallback"] = f"schema file not found: {path}"
        return default
    try:
        ext = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - 损坏文件
        default["_fallback"] = f"{type(exc).__name__}: {exc}"
        return default
    merged = dict(default)
    for k, v in (ext or {}).items():
        if k in merged and isinstance(v, (list, dict, str)):
            merged[k] = v
    merged["_external"] = str(p)
    return merged


def slot_block_bounds(text: str) -> List[Tuple[int, int]]:
    """返回文本中所有注入区块的 [start, end) 区间（含标记本身）。"""
    spans: List[Tuple[int, int]] = []
    cursor = 0
    while True:
        i = text.find(SLOT_BLOCK_BEGIN, cursor)
        if i < 0:
            break
        j = text.find(SLOT_BLOCK_END, i)
        if j < 0:
            spans.append((i, len(text)))
            break
        spans.append((i, j + len(SLOT_BLOCK_END)))
        cursor = spans[-1][1]
    return spans


def strip_slot_blocks(text: str) -> str:
    """剥离注入区块，得到「作者原文」。分类一律在原文上进行（幂等、无自证满足）。"""
    out = []
    last = 0
    for a, b in slot_block_bounds(text or ""):
        # 注入时区块前会补一个换行；把该换行算进区块，保证「剥离 == 作者原文」逐字节还原
        if a > 0 and (text or "")[a - 1] == "\n" and a - 1 >= last:
            a -= 1
        if b < len(text or "") and text[b] == "\n":   # 区块后的换行也归属区块
            b += 1
        out.append(text[last:a])
        last = b
    out.append((text or "")[last:])
    return "".join(out)


def criteria_segment(text: str) -> Tuple[str, str]:
    """切出判据段。返回 ``(segment, scope)``，scope ∈ {``criteria``, ``whole``}。

    * 找到最早出现的判据标记 → 从该标记起到下一个节起点（或文末）；
    * 一个标记都没有 → 整篇当判据段，scope=``whole``（审计里可据此统计覆盖面）。
    """
    raw = text or ""
    hits = [(raw.find(m), m) for m in CRITERIA_MARKERS if raw.find(m) >= 0]
    if not hits:
        return raw, "whole"
    pos, _marker = min(hits)
    end = len(raw)
    for stop in SECTION_STOPS:
        k = raw.find(stop, pos + 1)
        if 0 <= k < end:
            end = k
    return raw[pos:end], "criteria"