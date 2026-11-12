"""assembler —— 按任务类组装干货包：相关性打分 → 密度门 → 去重 → 预算裁剪。

核心判据（owner 2026-11-12 策展令）：**每条注入须答『它改变本任务的哪个决策？』**
答不出（无 ``decision`` 键 / 键不在配置词表）→ 计入 ``dropped`` 且绝不注入。
所有取舍都落进 ``package["audit"]``，注入可审计、可复算。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .loader import (clip, est_tokens, jaccard, load_config, overlap_score, sha16,
                     shingles, terms)
from .sources import PgReader, fetch_slot
from .taxonomy import class_spec, detect_class

DROP_REASONS = ("NO_DECISION", "UNKNOWN_DECISION", "DUPLICATE", "LOW_RELEVANCE",
                "OVER_BUDGET", "MAX_ITEMS", "TOO_LONG_AFTER_CLIP")


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _fit(text: str, max_tokens: int, cfg: Mapping[str, Any]) -> tuple[str, bool]:
    """把单条文本裁到 max_tokens 以内（确定性，返回 (text, 是否被裁)）。"""
    if est_tokens(text, cfg) <= max_tokens:
        return text, False
    lo, hi, best = 8, len(text), text[:8]
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = clip(text, mid)
        if est_tokens(cand, cfg) <= max_tokens:
            best, lo = cand, mid + 1
        else:
            hi = mid - 1
    return best, True


def build_package(goal: Any, repo: str = "", mission_id: Any = None,
                  as_of: Any = None, cfg: Mapping[str, Any] | None = None,
                  reader=None, extra_candidates: Mapping[str, list] | None = None,
                  class_hint: str | None = None, budget_override: int | None = None) -> dict:
    """组装一个干货包（纯函数式，除取数外无副作用）。

    ``extra_candidates``：``{slot: [candidate,...]}`` —— 供离线条目/注入式域数据使用
    （测试夹具与工件注入都走这里，代码不留域词条）。``reader`` 缺省用 PG 只读读取器。
    """
    cfg = cfg or load_config()
    defaults = cfg.get("defaults") or {}
    as_of = as_of or _now_iso()
    if class_hint:
        class_id, class_ev = class_hint, {"matched_triggers": [], "reason": "HINT"}
    else:
        class_id, class_ev = detect_class(goal, cfg)
    spec = class_spec(class_id, cfg)
    slot_ids = list(spec.get("slots") or [])
    class_budget = int(spec.get("budget_tokens") or defaults.get("total_budget_tokens", 1200))
    auto = defaults.get("budget_auto") or {}
    goal_tokens = est_tokens(goal, cfg)
    if budget_override:
        total_budget = int(budget_override)
    elif auto:
        floor = int(auto.get("floor_tokens", 300))
        cap_by_goal = max(floor, int(round(float(auto.get("max_ratio_to_goal", 0.4)) * goal_tokens)))
        total_budget = min(class_budget, cap_by_goal)
    else:
        total_budget = class_budget
    per_entry_max = int(defaults.get("per_entry_max_tokens", 110))
    per_slot_max_items = int(defaults.get("per_slot_max_items", 4))
    min_rel = float(defaults.get("min_relevance", 0.08))
    dedupe_thr = float(defaults.get("dedupe_jaccard", 0.6))
    decisions = set((cfg.get("decisions") or {}).keys())
    slots_cfg = cfg.get("slots") or {}

    own_reader = False
    if reader is None and not extra_candidates:
        reader = PgReader()
        own_reader = True

    # 槽预算按 share 归一（只在本次启用的槽之间分配）
    shares = {s: float((slots_cfg.get(s) or {}).get("share", 1.0 / max(1, len(slot_ids)))) for s in slot_ids}
    share_sum = sum(shares.values()) or 1.0
    slot_budget = {s: max(60, int(total_budget * shares[s] / share_sum)) for s in slot_ids}

    query = " ".join(str(x) for x in (goal, repo, spec.get("label") or "") if x)
    q_terms = terms(query)
    degraded: dict[str, str] = {}
    dropped: list[dict] = []
    pool_total = 0
    selected_shingles: list[set] = []
    slots_out: list[dict] = []
    used_total = 0

    for slot in slot_ids:
        cands: list[dict] = []
        if extra_candidates and slot in extra_candidates:
            cands = [dict(c) for c in extra_candidates[slot]]
        else:
            fetched, err = fetch_slot(slot, {"goal": goal, "repo": repo, "mission_id": mission_id,
                                             "as_of": as_of}, reader, cfg)
            cands = fetched
            if err:
                degraded[slot] = err
        pool_total += len(cands)

        for c in cands:
            rel = overlap_score(q_terms, c.get("keyword_text") or c.get("text"))
            repo_bonus = 0.0
            meta_repo = str((c.get("meta") or {}).get("domain") or (c.get("meta") or {}).get("repo") or "")
            if repo and meta_repo and meta_repo == repo:
                repo_bonus = 0.25
            c["relevance"] = round(rel + repo_bonus, 4)

        # 1) 密度门：无决策键的条目一律不注入
        gated: list[dict] = []
        for c in cands:
            dec = c.get("decision") or ""
            if not dec:
                dropped.append({"id": c.get("id"), "slot": slot, "reason": "NO_DECISION"})
                continue
            if dec not in decisions:
                dropped.append({"id": c.get("id"), "slot": slot, "reason": "UNKNOWN_DECISION",
                                "detail": dec})
                continue
            gated.append(c)

        # 2) 相关性下限
        rel_kept = []
        for c in gated:
            allowed = (slots_cfg.get(slot) or {}).get("decisions") or []
            if c.get("relevance", 0.0) < min_rel:
                dropped.append({"id": c.get("id"), "slot": slot, "reason": "LOW_RELEVANCE",
                                "detail": c.get("relevance")})
                continue
            if allowed and c["decision"] not in allowed:
                dropped.append({"id": c.get("id"), "slot": slot, "reason": "UNKNOWN_DECISION",
                                "detail": f"{c['decision']} not in {allowed}"})
                continue
            rel_kept.append(c)

        rel_kept.sort(key=lambda c: (-float(c.get("relevance", 0.0)),
                                     -float((c.get("meta") or {}).get("cited_count") or 0),
                                     str(c.get("id"))))

        # 3) 去重 + 4) 预算/条数裁剪
        items: list[dict] = []
        used_slot = 0
        for c in rel_kept:
            sh = shingles(c.get("text"))
            if any(jaccard(sh, prev) >= dedupe_thr for prev in selected_shingles):
                dropped.append({"id": c.get("id"), "slot": slot, "reason": "DUPLICATE"})
                continue
            if len(items) >= per_slot_max_items:
                dropped.append({"id": c.get("id"), "slot": slot, "reason": "MAX_ITEMS"})
                continue
            text, clipped = _fit(str(c.get("text") or ""), per_entry_max, cfg)
            tok = est_tokens(text, cfg) + int((cfg.get("token_estimator") or {}).get("per_entry_overhead", 8))
            if used_slot + tok > slot_budget[slot] or used_total + tok > total_budget:
                dropped.append({"id": c.get("id"), "slot": slot, "reason": "OVER_BUDGET",
                                "detail": {"tok": tok, "slot_left": slot_budget[slot] - used_slot,
                                           "total_left": total_budget - used_total}})
                continue
            items.append({"id": c.get("id"), "text": text, "tokens": tok,
                          "decision": c["decision"], "relevance": c.get("relevance"),
                          "pointer": c.get("pointer") or "", "clipped": clipped,
                          "source": c.get("source") or "", "meta": c.get("meta") or {}})
            selected_shingles.append(sh)
            used_slot += tok
            used_total += tok

        required = bool((slots_cfg.get(slot) or {}).get("required"))
        status = "OK" if items else ("DEGRADED" if slot in degraded else ("MISSING" if required else "EMPTY"))
        slots_out.append({
            "slot": slot,
            "label": (slots_cfg.get(slot) or {}).get("label") or slot,
            "source": (slots_cfg.get(slot) or {}).get("source"),
            "required": required,
            "budget_tokens": slot_budget[slot],
            "used_tokens": used_slot,
            "status": status,
            "candidates": len(cands),
            "items": items,
        })

    injected = sum(len(s["items"]) for s in slots_out)
    decision_bearing = sum(1 for s in slots_out for it in s["items"] if it.get("decision") in decisions)
    pkg = {
        "schema_version": cfg.get("schema_version", "context-package/v1"),
        "package_version": cfg.get("package_version", "1.0"),
        "mission_id": mission_id,
        "repo": repo,
        "task_class": class_id,
        "task_class_label": spec.get("label"),
        "class_evidence": class_ev,
        "goal_sha256": sha16(goal),
        "as_of": as_of,
        "built_at": _now_iso(),
        "budget": {"total_tokens": total_budget, "used_tokens": used_total,
                   "utilization": round(used_total / total_budget, 4) if total_budget else 0.0},
        "slots": slots_out,
        "dropped": dropped,
        "degraded_sources": degraded,
        "audit": {
            "candidate_pool": pool_total,
            "injected_items": injected,
            "decision_bearing": decision_bearing,
            "density_yield": round(decision_bearing / injected, 4) if injected else 0.0,
            "dropped_by_reason": _count_by(dropped, "reason"),
            "density_rule": (cfg.get("density_rule") or "")[:200],
            "token_estimator": cfg.get("token_estimator") or {},
            "config_path": cfg.get("_config_path"),
        },
    }
    pkg["package_id"] = "ctxp_" + sha16(pkg["mission_id"], pkg["goal_sha256"], class_id,
                                        as_of, ",".join(it["id"] for s in slots_out for it in s["items"]))
    if own_reader and reader is not None:
        reader.close()
    return pkg


def _count_by(rows: list[dict], key: str) -> dict:
    out: dict[str, int] = {}
    for r in rows:
        out[str(r.get(key))] = out.get(str(r.get(key)), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def covered_by(pkg: Mapping[str, Any], needles: Mapping[str, Any], min_overlap: int = 1) -> dict:
    """held-out 覆盖判定：包内是否有条目命中给定失败画像。

    ``needles`` = ``{"failure_kind": ..., "keywords": [...]}``；命中规则（预注册）：
    ①条目 meta.failure_kind 相等，或 ②条目文本与 needle 关键词重合 ≥ min_overlap 个。
    """
    kind = str(needles.get("failure_kind") or "")
    kws = {str(k).lower() for k in (needles.get("keywords") or []) if str(k).strip()}
    hit_ids: list[str] = []
    for s in pkg.get("slots") or []:
        for it in s.get("items") or []:
            if kind and str((it.get("meta") or {}).get("failure_kind") or "") == kind:
                hit_ids.append(it["id"])
                continue
            it_terms = terms(f"{it.get('text')} {it.get('pointer')}")
            if kws and len(kws & it_terms) >= min_overlap:
                hit_ids.append(it["id"])
    return {"covered": bool(hit_ids), "hits": hit_ids[:6]}