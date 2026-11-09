#!/usr/bin/env python3
"""判据可裁决性基线审计器（P151W1-N10 / ③ 三维切片分布报告 + E2 可复现）。

输入：mission 快照记录列表（``[{id, repo, campaign, is_maturity, status, goal}]``），
输出：**确定性** JSON——无时钟、无随机、无集合遍历序、浮点定点、键序固定。
同批记录两次运行 → ``digest`` 逐位一致（E2 的机器判据）。

切片：``repo`` / ``campaign`` / ``is_maturity`` 三维；每片给 P1/P2/P3 命中率与强弱分布。
另给 ``pairs``：切成「P1 与 P2 同时命中」「只 P1」「只 P2」「双缺」四格，
直接回答「缺的是哪一维」——这是改进优先级的依据。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import classifier as C
from . import gate as G
from . import schema as S

AUDIT_SCHEMA = "tp.quality.judgeability.audit/v1"


def canonical_bytes(obj: Any) -> bytes:
    """确定性序列化（键排序 + 紧凑分隔符 + 非 ASCII 保留）→ 用作 sha256 依据。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def sha256_of(obj: Any) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def _rate(k: int, n: int) -> float:
    return round(k / n, 6) if n else 0.0


def _slice_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    p1 = sum(1 for r in rows if r["dims"]["P1"])
    p2 = sum(1 for r in rows if r["dims"]["P2"])
    p3 = sum(1 for r in rows if r["dims"]["P3"])
    strong = sum(1 for r in rows if r["verdict"] == "strong")
    both = sum(1 for r in rows if r["dims"]["P1"] and r["dims"]["P2"])
    only1 = sum(1 for r in rows if r["dims"]["P1"] and not r["dims"]["P2"])
    only2 = sum(1 for r in rows if r["dims"]["P2"] and not r["dims"]["P1"])
    neither = n - both - only1 - only2
    return {
        "n": n,
        "P1": p1, "P2": p2, "P3": p3,
        "P1_rate": _rate(p1, n), "P2_rate": _rate(p2, n), "P3_rate": _rate(p3, n),
        "strong": strong, "weak": n - strong, "strong_rate": _rate(strong, n),
        "pairs": {"P1&P2": both, "only_P1": only1, "only_P2": only2, "neither": neither},
        # 三维齐全但因「无判据段」被任务书级 fail-closed 判 weak 的单数
        "dims_complete_but_weak": sum(
            1 for r in rows if r["dims"]["P1"] and r["dims"]["P2"]
            and r["verdict"] != "strong"),
        "scope_whole": sum(1 for r in rows if r["scope"] == "whole"),
    }


def audit_records(records: Sequence[Dict[str, Any]], *,
                  batch_label: str = "terminal_missions",
                  limit: Optional[int] = None) -> Dict[str, Any]:
    """对一批 mission 记录做三维审计，返回确定性报告 dict。"""
    recs = [r for r in records if (r.get("goal") or "").strip()]
    # 稳定排序：按 id 数值优先，退化到字符串，保证与输入顺序无关
    def _key(r: Dict[str, Any]) -> Tuple[int, str]:
        try:
            return (0, "%012d" % int(r.get("id")))
        except Exception:
            return (1, str(r.get("id")))
    recs = sorted(recs, key=_key)
    if limit:
        recs = recs[: int(limit)]

    per_record: List[Dict[str, Any]] = []
    for r in recs:
        j = C.classify_goal(r.get("goal") or "")
        per_record.append({
            "id": r.get("id"),
            "repo": r.get("repo") or "unknown",
            "campaign": r.get("campaign") or "unattributed",
            "is_maturity": bool(r.get("is_maturity")),
            "status": r.get("status") or "unknown",
            "verdict": j.verdict,
            "dims": {k: bool(v) for k, v in j.dims.items()},
            "missing": list(j.missing),
            "hard_missing": list(j.hard_missing),
            "scope": j.scope,
            "flags": list(j.flags),
            "segment_chars": j.segment_chars,
            "goal_chars": j.raw_chars,
            "evidence": {k: [h.text for h in v][:3] for k, v in j.dims.items()},
        })

    def _group(key: str) -> Dict[str, Any]:
        buckets: Dict[str, List[Dict[str, Any]]] = {}
        for row in per_record:
            buckets.setdefault(str(row[key]), []).append(row)
        return {k: _slice_summary(v) for k, v in sorted(buckets.items())}

    goals = [r.get("goal") or "" for r in recs]
    payload: Dict[str, Any] = {
        "schema": AUDIT_SCHEMA,
        "schema_ref": S.SCHEMA_ID,
        "batch": batch_label,
        "n_records": len(per_record),
        "inputs_digest": sha256_of([{"id": r.get("id"), "goal": r.get("goal")}
                                    for r in recs]),
        "overall": _slice_summary(per_record),
        "slices": {
            "repo": _group("repo"),
            "campaign": _group("campaign"),
            "is_maturity": _group("is_maturity"),
        },
        "gate_probe": {
            "advisory": _probe(goals, "advisory"),
            "enforce": G.replay_enforce(goals),
            "block_would_refuse": sum(
                1 for g in goals if G.decide(g, mode="block").blocked),
            "leak_false_promotion": G.leak_rate(goals, marker=True),
            "leak_naive_control": G.leak_rate(goals, marker=False),
        },
        "per_record": per_record,
    }
    payload["digest"] = sha256_of({k: v for k, v in payload.items() if k != "digest"})
    return payload


def _probe(goals: List[str], mode: str) -> Dict[str, Any]:
    out = {"n": len(goals), "actions": {}}
    for g in goals:
        a = G.decide(g, mode=mode).action
        out["actions"][a] = out["actions"].get(a, 0) + 1
    out["actions"] = dict(sorted(out["actions"].items()))
    return out


def reproducibility(records: Sequence[Dict[str, Any]], *, runs: int = 2,
                    batch_label: str = "terminal_missions") -> Dict[str, Any]:
    """同批记录跑 ``runs`` 次，逐次 digest 比对（E2：sha256 相同）。"""
    digests = [audit_records(records, batch_label=batch_label)["digest"]
               for _ in range(max(2, int(runs)))]
    return {"runs": len(digests), "digests": digests,
            "identical": len(set(digests)) == 1, "sha256": digests[0]}


_VERBAL = re.compile(r"(?:以内|以下|不超过|至多|至少)\s*$|(?:以内|以下|不超过)")
_UNIT = re.compile(r"\d+(?:\.\d+)?\s*(?:GB|MB|KB|ms|kg|kHz|Hz|MPa|kPa)\b")


def limitation_probe(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """量出**已知假阴面**的规模（诚实披露，不粉饰）：

    * ``verbal_threshold``：判据段缺 P1，但用了「以内/以下/不超过」这类口头阈值；
    * ``unit_letter``：判据段缺 P1，但存在「数值+单位字母」（如 1.2GB）被形状判据拒认。
    """
    goals = [r.get("goal") or "" for r in records if (r.get("goal") or "").strip()]
    verbal_all: List[Any] = []
    verbal_miss: List[Any] = []
    unit_all: List[Any] = []
    unit_miss: List[Any] = []
    for r in records:
        g = r.get("goal") or ""
        if not g.strip():
            continue
        j = C.classify_goal(g)
        seg, _ = S.criteria_segment(S.strip_slot_blocks(g))
        if _VERBAL.search(seg):
            verbal_all.append(r.get("id"))
            if not j.dims["P1"]:
                verbal_miss.append(r.get("id"))
        if _UNIT.search(seg):
            unit_all.append(r.get("id"))
            if not j.dims["P1"]:
                unit_miss.append(r.get("id"))
    n = len(goals)
    return {"n": n,
            "verbal_threshold": {"seen": len(verbal_all), "seen_rate": _rate(len(verbal_all), n),
                                 "count": len(verbal_miss),
                                 "rate": _rate(len(verbal_miss), n),
                                 "ids_sample": [str(x) for x in verbal_miss[:10]]},
            "unit_letter": {"seen": len(unit_all), "seen_rate": _rate(len(unit_all), n),
                            "count": len(unit_miss), "rate": _rate(len(unit_miss), n),
                            "ids_sample": [str(x) for x in unit_miss[:10]]},
            "note": "这两类是形状判据的已知假阴面（口头阈值 / 数值后紧跟单位字母），"
                    "列为局限而非缺陷：P1 定义要求显式比较符。"}


def before_after(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """前后对照：派发前（原文） vs enforce 生效（注入后）的合规率与漏放。

    * before：P1/P2/strong 命中率（原始任务书）；
    * after ：注入槽位后「已回填槽位」率（结构性提示覆盖率）与假晋升（应为 0）；
    * delta ：给图用（命中率差）。
    """
    goals = [r.get("goal") or "" for r in records if (r.get("goal") or "").strip()]
    before_j = [C.classify_goal(g) for g in goals]
    after = [G.decide(g, mode="enforce") for g in goals]
    n = len(goals)
    b = {
        "P1": _rate(sum(1 for j in before_j if j.dims["P1"]), n),
        "P2": _rate(sum(1 for j in before_j if j.dims["P2"]), n),
        "P3": _rate(sum(1 for j in before_j if j.dims["P3"]), n),
        "strong": _rate(sum(1 for j in before_j if j.verdict == "strong"), n),
    }
    injected = sum(1 for d in after if d.injected)
    a = {
        "slot_coverage": _rate(injected, n),
        "strong_after": _rate(sum(1 for g, d in zip(goals, after)
                                  if C.classify_goal(d.goal).verdict == "strong"), n),
        "hard_missing_flagged": _rate(sum(1 for d in after if d.hard_missing), n),
    }
    return {"n": n, "before": b, "after": a,
            "delta": {"strong": round(a["strong_after"] - b["strong"], 6),
                      "slot_coverage": round(a["slot_coverage"], 6)},
            "false_promotion": a["strong_after"] - b["strong"]}