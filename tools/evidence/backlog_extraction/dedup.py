"""去重（与在飞/已交付节点、历史提案池、以及 backlog 内部的近重检测）。

双口径
------
* **字段口径**（field key）：``(repo, case_normalized, metric_normalized)`` 完全相等
  → 同一「仓-案例-量」三元组，几乎必然是同一欠账的不同书写。
* **语义口径**（semantic）：量名 + 再派判据 + 依赖的 token 集合 Jaccard 相似度。
  token = 拉丁词（≥3 字符，casefold）+ CJK 二元组。相似度 ≥ ``SEM_THRESHOLD`` 判近重。

判定规则（代码即规则）
----------------------
1. 字段 key 相等 → ``duplicate``（置信 1.0）
2. 语义相似度 ≥ 0.75 且误差分桶相同 → ``duplicate``（置信 = 相似度）
3. 语义相似度 ≥ 0.55 → ``near``（人工复核，不进自动拦截）
4. 其余 → ``distinct``

注意：与「已交付节点」比较时，节点的 ``status`` 参与判定——``verified`` 节点意味着该能力
已验收，重复提取出来的欠账应标 ``already_delivered`` 而不是重新派单。
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from .schema import DebtRow, error_bucket, normalize_metric

SEM_THRESHOLD = 0.85
NEAR_THRESHOLD = 0.55
_CJK_RANGE = (0x4E00, 0x9FFF)


def _is_cjk(ch: str) -> bool:
    return _CJK_RANGE[0] <= ord(ch) <= _CJK_RANGE[1]


def tokens(text: str) -> frozenset[str]:
    """拉丁词 + CJK 二元组（单字丢弃，避免「量/值/误差」这种通用字主导相似度）。"""
    s = unicodedata.normalize("NFKC", text or "")
    for token in ("**", "__", "`", "*", "_", "~~"):
        s = s.replace(token, "")
    out: set[str] = set()
    latin: list[str] = []
    cjk_run: list[str] = []
    for ch in s:
        if ch.isascii() and (ch.isalnum() or ch in "._/-"):
            latin.append(ch)
            if cjk_run:
                out.update(_cjk_bigrams(cjk_run))
                cjk_run = []
        elif _is_cjk(ch):
            cjk_run.append(ch)
            if latin:
                out.update(_latin_tokens("".join(latin)))
                latin = []
        else:
            if latin:
                out.update(_latin_tokens("".join(latin)))
                latin = []
            if cjk_run:
                out.update(_cjk_bigrams(cjk_run))
                cjk_run = []
    if latin:
        out.update(_latin_tokens("".join(latin)))
    if cjk_run:
        out.update(_cjk_bigrams(cjk_run))
    return frozenset(out)


def _latin_tokens(blob: str) -> set[str]:
    """拉丁/数字 token：长度 ≥3 的词**或任何含数字的 token**。

    含数字的 token 必须保留——``KVLCC2-01``/``KVLCC2-04``、``a=40``/``a=80``、
    ``Fr = 0.300`` 的判别信息全在数字里；只留 ≥3 字符的字母词会让不同工况坍缩成同一
    token 集（实测过：KVLCC2-01 与 KVLCC2-04 Jaccard=1.0 的假重复）。
    """
    out: set[str] = set()
    for raw in blob.split():
        w = raw.strip("()[]{}:;,")
        if not w:
            continue
        if len(w) >= 3 or any(ch.isdigit() for ch in w):
            out.add(w.casefold())
    return out


def _cjk_bigrams(chars: Sequence[str]) -> set[str]:
    return {"".join(chars[i : i + 2]) for i in range(len(chars) - 1)}


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b) if inter else 0.0


def field_key(repo: str, case: str, metric: str) -> str:
    return "|".join((repo.casefold(), normalize_metric(case), normalize_metric(metric)))


def row_tokens(row: DebtRow) -> frozenset[str]:
    """欠账的语义 token：**量名 + 依赖**，不含再派判据。

    再派判据来自四类模板（``REQUEUE_TEMPLATES``），同根因的两条欠账**必然**共享整段
    模板文本——把它算进 token 会让任意两条同根因欠账的 Jaccard 冲到 1.0（实测过：
    ``KVLCC2-01`` 与 ``KVLCC2-04`` 被判成 1.0 相似）。故只取量名（含船型/检查点在
    量名里）与依赖项。
    """
    blob = " ".join((row.metric, " ".join(row.depends_on)))
    return tokens(blob)


@dataclass
class Target:
    """比较对象：在飞/已交付节点、历史提案，或另一条欠账。"""

    key: str
    label: str
    repo: str
    case: str
    metric: str
    status: str = ""
    source: str = ""
    tokens: frozenset[str] = frozenset()
    err_bucket: str = "-"

    @classmethod
    def from_node(cls, node: dict) -> "Target":
        blob = " ".join(
            str(node.get(k) or "")
            for k in ("ext_id", "name", "title", "goal", "metric", "criteria")
        )
        return cls(
            key=str(node.get("ext_id") or node.get("node_id") or node.get("id") or ""),
            label=str(node.get("name") or node.get("title") or ""),
            repo=str(node.get("repo") or ""),
            case=str(node.get("ext_id") or ""),
            metric=str(node.get("metric") or node.get("name") or ""),
            status=str(node.get("status") or ""),
            source="campaign_node",
            tokens=tokens(blob),
        )

    @classmethod
    def from_proposal(cls, prop: dict, *, path: str = "") -> "Target":
        blob = " ".join(
            str(prop.get(k) or "")
            for k in ("title", "goal", "why_frontier", "depends_on", "physics_domain")
        )
        return cls(
            key=str(prop.get("id") or prop.get("title") or path),
            label=str(prop.get("title") or ""),
            repo=str(prop.get("repo") or ""),
            case=str(prop.get("physics_domain") or ""),
            metric=str(prop.get("title") or ""),
            status=str(prop.get("status") or "proposed"),
            source="proposal",
            tokens=tokens(blob),
        )

    @classmethod
    def from_row(cls, row: DebtRow) -> "Target":
        return cls(
            key=row.row_id,
            label=row.metric,
            repo=row.repo,
            case=row.case,
            metric=row.metric,
            status=row.verdict,
            source="backlog_row",
            tokens=row_tokens(row),
            err_bucket=error_bucket(row.error_pct),
        )


def compare(row: DebtRow, target: Target) -> dict[str, Any]:
    sim = jaccard(row_tokens(row), target.tokens)
    # 字段口径 = 仓+案例+量+**误差分桶**全等（即 schema 指纹）。只比量名不比误差会把
    # 「同一量的两个口径」（DTMB5415 +26.5% vs +44.2%）当成一条，把两条真欠账并成一条。
    same_field = (
        bool(target.repo)
        and target.repo.casefold() == row.repo.casefold()
        and normalize_metric(target.case) == normalize_metric(row.case)
        and normalize_metric(target.metric) == normalize_metric(row.metric)
        and target.err_bucket == error_bucket(row.error_pct)
    )
    same_case = bool(target.case) and normalize_metric(target.case) == normalize_metric(row.case)
    if same_field:
        decision, confidence = "duplicate", 1.0
    elif (
        same_case
        and (
            target.err_bucket == error_bucket(row.error_pct)
            or target.err_bucket == "-"
            or error_bucket(row.error_pct) == "-"
        )
        and target.tokens
        and row_tokens(row)
        and (
            (
                target.tokens < row_tokens(row)
                and len(target.tokens) < len(row_tokens(row))
            )
            or (
                row_tokens(row) < target.tokens
                and len(row_tokens(row)) < len(target.tokens)
            )
        )
    ):
        # 同案例 + 误差同桶 + token 包含关系：同一行在不同文件里粗细不同地记了两遍
        # （判词原文 ``| a=40 | +10.5% | … |`` vs 报告 ``a=40 @ T=1 err%``）。
        decision, confidence = "duplicate", round(max(sim, 0.6), 4)
    elif (
        sim >= SEM_THRESHOLD
        and same_case
        and target.err_bucket == error_bucket(row.error_pct)
    ):
        # 语义近重**必须同案例**：``G2 直接观测量 ≤3%`` 这类通用门名在 dam_break /
        # dit_turbulence / permeability 三个 benchmark 里逐字相同，但它们是三条不同欠账。
        decision, confidence = "duplicate", round(sim, 4)
    elif sim >= SEM_THRESHOLD:
        decision, confidence = "near", round(sim, 4)
    elif sim >= NEAR_THRESHOLD:
        decision, confidence = "near", round(sim, 4)
    else:
        decision, confidence = "distinct", round(sim, 4)
    if decision == "duplicate" and target.status in ("verified", "done", "delivered"):
        decision = "already_delivered"
    return {
        "row": row.row_id,
        "row_fingerprint": row.fingerprint,
        "target": target.key,
        "target_label": target.label,
        "target_source": target.source,
        "target_status": target.status,
        "similarity": round(sim, 4),
        "decision": decision,
        "confidence": confidence,
    }


def dedupe_within(rows: Iterable[DebtRow]) -> tuple[list[DebtRow], list[dict]]:
    """backlog 内部近重合并：字段 key 相同 → 保留行号最大者（最新报告）。"""
    seen: dict[str, DebtRow] = {}
    pairs: list[dict] = []
    for row in rows:
        # 用指纹（含误差分桶）而不是纯字段 key：同一「量」在两个口径下误差不同时
        # （如 DTMB5415 的 +26.5% 与 +44.2%）是**两条独立欠账**，不能互相吞掉。
        key = row.fingerprint
        prev = seen.get(key)
        if prev is None:
            seen[key] = row
            continue
        pairs.append(
            {
                "row": row.row_id,
                "target": prev.row_id,
                "target_label": prev.metric,
                "target_source": "backlog_row",
                "target_status": prev.verdict,
                "similarity": 1.0,
                "decision": "duplicate",
                "confidence": 1.0,
                "rule": "field_key",
            }
        )
        if (row.source.file, row.source.line) > (prev.source.file, prev.source.line):
            seen[key] = row
    return list(seen.values()), pairs


def dedupe_against(rows: Iterable[DebtRow], targets: Iterable[Target]) -> list[dict]:
    targets = list(targets)
    out: list[dict] = []
    for row in rows:
        best: dict[str, Any] | None = None
        for target in targets:
            res = compare(row, target)
            if res["decision"] == "distinct":
                continue
            if best is None or res["confidence"] > best["confidence"]:
                best = res
        if best is not None:
            out.append(best)
    return out


# ------------------------------------------------------------------ 仓库侧载入
def load_campaign_nodes(path: str | Path) -> list[Target]:
    p = Path(path)
    if not p.is_file():
        return []
    payload = json.loads(p.read_text(encoding="utf-8"))
    nodes = payload.get("nodes", payload) if isinstance(payload, dict) else payload
    return [Target.from_node(n) for n in nodes]


def load_proposals(root: str | Path) -> list[Target]:
    rootp = Path(root)
    out: list[Target] = []
    if not rootp.is_dir():
        return out
    for path in sorted(rootp.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        items = payload.get("proposals") if isinstance(payload, dict) else None
        if isinstance(items, list):
            for prop in items:
                if isinstance(prop, dict):
                    out.append(Target.from_proposal(prop, path=str(path)))
        elif isinstance(payload, dict):
            out.append(Target.from_proposal(payload, path=str(path)))
    return out