"""检索凭证重放门：让"我检索过了，这是新东西"变成可复算的凭证。

问题
----
提案的新颖性主张（"节点池没有覆盖 X""提案池没人提过 Y"）历史上只写结论、不写凭证：
读了哪些文件、搜了哪些词、命中了什么、命中集合的指纹是什么——一概缺失。于是三种
坏账无法区分：

* **空检索**：主张"没覆盖"，其实一个词都没搜（或搜的是别的域的词）
* **过期检索**：搜的时候没命中，之后仓库/池子长出了命中项，主张却还挂着
* **走样检索**：声称搜了 ``tools/gate``，实际只搜了 ``tools/gate/one_file.py``

机制
----
1. ``Receipt``：一条检索凭证 = (检索词, 检索范围, 命中集合, 指纹)。指纹是命中集合的
   sha256（排序后），因此"命中集合变了"必然表现为指纹漂移。
2. ``replay_receipt``：用**同一个解析器**在**当前**语料上重跑，逐项对比命中集合：
   ``MATCH`` / ``DRIFT``（增删具体项）/ ``UNREPLAYABLE``（缺解析器/范围不存在）。
3. ``claim_gate``：把凭证聚合到新颖性主张上。新颖性主张要求"所有凭证都重放 MATCH
   且命中集合为空"；覆盖主张要求"至少一份凭证有命中"。不满足就 ``REFUTED``，
   重放不了就 ``UNREPLAYABLE``（绝不当成通过）。
4. ``negative_space_map``：把"声明的语义区域 → 检索词 → 是否被搜过 / 是否命中"铺成
   地图。**从未被搜过的词**才是真正的负空间（比"搜过没命中"更危险）。

零领域语义：区域与检索词全部来自数据文件（``data/criteria_metrology/retrieval_regions.json``）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

REPLAY_MATCH = "MATCH"
REPLAY_DRIFT = "DRIFT"
REPLAY_UNREPLAYABLE = "UNREPLAYABLE"


def _digest(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class Receipt:
    """一条检索凭证。不可变；指纹由内容决定。"""

    claim_id: str
    kind: str                      # code | pool | node
    query: tuple[str, ...]
    scope: tuple[str, ...]
    hits: tuple[str, ...]
    tool: str = "tools.criteria_metrology.retrieval"
    created_at: str | None = None
    digest: str = ""
    #: 检索时显式排除的路径前缀（"除本模块外"这类主张必须把排除项写进凭证，
    #: 否则重放时会把主张自己的实现算成先例）
    exclude: tuple[str, ...] = ()

    @property
    def n_hits(self) -> int:
        return len(self.hits)

    @property
    def is_empty(self) -> bool:
        return not self.hits

    def payload(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "kind": self.kind,
            "query": list(self.query),
            "scope": list(self.scope),
            "hits": sorted(self.hits),
            "exclude": sorted(self.exclude),
        }

    def as_dict(self) -> dict[str, Any]:
        return {**self.payload(), "tool": self.tool, "created_at": self.created_at, "digest": self.digest}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Receipt":
        return build_receipt(
            claim_id=d["claim_id"],
            kind=d["kind"],
            query=d["query"],
            scope=d["scope"],
            hits=d["hits"],
            tool=d.get("tool", "tools.criteria_metrology.retrieval"),
            created_at=d.get("created_at"),
            exclude=d.get("exclude") or (),
        )


def build_receipt(
    *,
    claim_id: str,
    kind: str,
    query: Sequence[str],
    scope: Sequence[str],
    hits: Iterable[str],
    tool: str = "tools.criteria_metrology.retrieval",
    created_at: str | None = None,
    exclude: Sequence[str] = (),
) -> Receipt:
    q = tuple(sorted({str(x).strip() for x in query if str(x).strip()}))
    ex = tuple(sorted({str(x).strip() for x in exclude if str(x).strip()}))
    s = tuple(sorted({str(x).strip() for x in scope if str(x).strip()}))
    h = tuple(sorted({str(x).strip() for x in hits if str(x).strip()}))
    payload = {
        "claim_id": claim_id,
        "kind": kind,
        "query": list(q),
        "scope": list(s),
        "hits": list(h),
        "exclude": list(ex),
    }
    return Receipt(
        claim_id=claim_id,
        kind=kind,
        query=q,
        scope=s,
        hits=h,
        tool=tool,
        created_at=created_at,
        digest=_digest(payload),
        exclude=ex,
    )


# --------------------------------------------------------------------------- #
# 解析器（可重放）
# --------------------------------------------------------------------------- #


class FileTermIndex:
    """``kind='code'``：在给定路径集合里做确定性文本检索。

    命中项记成 ``relpath:lineno``（不含行内容，避免语料漂移把无关编辑算成命中变化）。
    """

    kind = "code"

    def __init__(
        self,
        root: Path,
        *,
        suffixes: Sequence[str] = (".py", ".md", ".json", ".yaml", ".yml"),
        exclude: Sequence[str] = (),
    ) -> None:
        self.root = Path(root)
        self.suffixes = tuple(suffixes)
        #: 排除前缀（例如"除本节点自己的模块外"这种主张必须显式排除，否则自证循环）
        self.exclude = tuple(exclude)

    def _iter_files(self, scope: Sequence[str]):
        for entry in scope:
            p = self.root / entry
            if p.is_dir():
                for f in sorted(p.rglob("*")):
                    if f.is_file() and f.suffix in self.suffixes:
                        yield f
            elif p.is_file():
                yield p

    def resolve(self, receipt: Receipt) -> tuple[str, list[str], str | None]:
        missing = [s for s in receipt.scope if not (self.root / s).exists()]
        if missing:
            return REPLAY_UNREPLAYABLE, [], f"scope_missing:{','.join(missing[:5])}"
        hits: list[str] = []
        for f in self._iter_files(receipt.scope):
            rel0 = str(f.relative_to(self.root)) if str(f).startswith(str(self.root)) else str(f)
            if any(rel0.startswith(ex) for ex in tuple(self.exclude) + tuple(receipt.exclude)):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                low = line.lower()
                if any(t.lower() in low for t in receipt.query):
                    rel = str(f.relative_to(self.root)) if str(f).startswith(str(self.root)) else str(f)
                    hits.append(f"{rel}:{i}")
        return REPLAY_MATCH, sorted(set(hits)), None


class SnapshotIndex:
    """``kind in {pool, node}``：对池/节点快照做重放。

    快照结构：``{ "pool|query|scope": [hit, ...] }``。快照本身有指纹（``digest``），
    快照缺失或指纹变化都会显式报告，不会静默给出 MATCH。
    """

    def __init__(self, snapshot: dict[str, Any], *, kind: str) -> None:
        self.kind = kind
        self.entries: dict[str, list[str]] = dict(snapshot.get("entries") or {})
        self.digest = snapshot.get("digest") or _digest(self.entries)
        self.version = snapshot.get("version")

    def resolve(self, receipt: Receipt) -> tuple[str, list[str], str | None]:
        key = "|".join([receipt.kind, ",".join(receipt.query), ",".join(receipt.scope)])
        if key not in self.entries:
            return REPLAY_UNREPLAYABLE, [], f"snapshot_key_missing:{key[:120]}"
        return REPLAY_MATCH, sorted(set(self.entries[key])), None


@dataclass
class ReplayResult:
    claim_id: str
    receipt_digest: str
    status: str
    hits_now: list[str]
    added: list[str]
    removed: list[str]
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == REPLAY_MATCH and not self.added and not self.removed

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "receipt_digest": self.receipt_digest,
            "status": self.status,
            "n_hits_recorded": len(self.hits_now) - len(self.added) + len(self.removed),
            "n_hits_now": len(self.hits_now),
            "added": self.added,
            "removed": self.removed,
            "ok": self.ok,
            "detail": self.detail,
        }


def replay_receipt(receipt: Receipt, resolvers: dict[str, Any]) -> ReplayResult:
    """用当前语料重放一条凭证。"""
    resolver = resolvers.get(receipt.kind)
    if resolver is None:
        return ReplayResult(
            receipt.claim_id, receipt.digest, REPLAY_UNREPLAYABLE, [], [], [], f"no_resolver_for_kind:{receipt.kind}"
        )
    status, hits_now, detail = resolver.resolve(receipt)
    if status != REPLAY_MATCH:
        return ReplayResult(receipt.claim_id, receipt.digest, status, hits_now, [], [], detail)
    recorded = set(receipt.hits)
    now = set(hits_now)
    added = sorted(now - recorded)
    removed = sorted(recorded - now)
    new_status = REPLAY_MATCH if not added and not removed else REPLAY_DRIFT
    return ReplayResult(receipt.claim_id, receipt.digest, new_status, sorted(now), added, removed, detail)


def claim_gate(
    claim: dict[str, Any],
    receipts: Sequence[Receipt],
    resolvers: dict[str, Any],
) -> dict[str, Any]:
    """把凭证聚合到新颖性/覆盖主张上。

    主张契约（数据驱动）：``{"claim_id","claim_kind":"novelty"|"coverage","statement"}``
    """
    results = [replay_receipt(r, resolvers) for r in receipts]
    unreplayable = [r for r in results if r.status == REPLAY_UNREPLAYABLE]
    drift = [r for r in results if r.status == REPLAY_DRIFT]
    total_hits = sum(len(r.hits_now) for r in results)
    kind = claim.get("claim_kind", "novelty")

    if unreplayable:
        verdict = "UNREPLAYABLE"
        reason = f"{len(unreplayable)} 条凭证无法重放（范围/快照缺失）"
    elif kind == "novelty":
        if drift:
            verdict = "REFUTED"
            reason = f"重放发现命中集合变化（新增 {sum(len(r.added) for r in drift)} 项）→ 新颖性主张已过期"
        elif total_hits > 0:
            verdict = "REFUTED"
            reason = f"检索命中 {total_hits} 项 → 主张的'未覆盖'不成立"
        else:
            verdict = "SUPPORTED"
            reason = "全部凭证重放 MATCH 且命中集合为空"
    else:  # coverage
        if total_hits == 0:
            verdict = "REFUTED"
            reason = "覆盖主张但全部凭证命中为空（空检索）"
        elif drift:
            verdict = "REFUTED"
            reason = "命中集合已漂移，覆盖结论需重算"
        else:
            verdict = "SUPPORTED"
            reason = f"全部凭证重放 MATCH，共 {total_hits} 项命中"
    return {
        "claim_id": claim.get("claim_id"),
        "claim_kind": kind,
        "statement": claim.get("statement"),
        "verdict": verdict,
        "reason": reason,
        "n_receipts": len(receipts),
        "n_match": sum(1 for r in results if r.status == REPLAY_MATCH),
        "n_drift": len(drift),
        "n_unreplayable": len(unreplayable),
        "total_hits_now": total_hits,
        "results": [r.as_dict() for r in results],
    }


# --------------------------------------------------------------------------- #
# 负空间图谱
# --------------------------------------------------------------------------- #


def negative_space_map(
    regions: dict[str, list[str]],
    receipts: Sequence[Receipt],
    *,
    resolvers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把"语义区域 → 检索词"铺成覆盖/负空间地图。

    对每个区域给出四个数：声明词数、被搜过词数、命中词数、**从未被搜过词数**
    （后者才是真正的负空间）。可选 ``resolvers``：给了就用当前语料重算命中，
    不给就用凭证里的历史命中（离线模式）。
    """
    searched: dict[str, set[str]] = {}
    hit_terms: dict[str, set[str]] = {}
    for r in receipts:
        for t in r.query:
            searched.setdefault(t, set()).add(r.claim_id)
    # 命中归属：只统计"该词单独出现在凭证里"的凭证，避免多词凭证模糊归属
    for r in receipts:
        if len(r.query) != 1:
            continue
        term = r.query[0]
        hits = r.hits
        if resolvers is not None:
            res = replay_receipt(r, resolvers)
            hits = tuple(res.hits_now)
        if hits:
            hit_terms.setdefault(term, set()).update(hits)

    out: dict[str, Any] = {"regions": {}, "n_terms_declared": 0, "n_terms_searched": 0, "n_terms_covered": 0}
    for region, terms in regions.items():
        declared = list(dict.fromkeys(terms))
        searched_terms = [t for t in declared if t in searched]
        covered = [t for t in declared if t in hit_terms]
        unprobed = [t for t in declared if t not in searched]
        out["regions"][region] = {
            "n_declared": len(declared),
            "n_searched": len(searched_terms),
            "n_covered": len(covered),
            "n_unprobed": len(unprobed),
            "coverage_ratio": (len(covered) / len(declared)) if declared else 0.0,
            "probe_ratio": (len(searched_terms) / len(declared)) if declared else 0.0,
            "unprobed_terms": unprobed,
            "uncovered_terms": [t for t in searched_terms if t not in hit_terms],
            "covered_terms": covered,
        }
        out["n_terms_declared"] += len(declared)
        out["n_terms_searched"] += len(searched_terms)
        out["n_terms_covered"] += len(covered)
    out["coverage_ratio"] = (
        out["n_terms_covered"] / out["n_terms_declared"] if out["n_terms_declared"] else 0.0
    )
    out["probe_ratio"] = out["n_terms_searched"] / out["n_terms_declared"] if out["n_terms_declared"] else 0.0
    return out


def receipts_from_records(records: Iterable[dict[str, Any]]) -> list[Receipt]:
    return [Receipt.from_dict(r) for r in records]