"""Discover：试点批发现与分层。

优先读 m15462 分层清单（repo|content_type|tier 三层）；文件不在则按 owner 允许的
「repo×scenario_key×长度」预分层从 tensor_reports 现生成，回后校正。
分层清单里的条目来自仓库文档快照，必须与 tensor_reports 行对账（sha256 优先，标题次之），
对不上的如实记 unmatched —— 证据门只能对 tensor_reports 里的 markdown 做定位，
凭空认领一份不存在的报告等于假证据。
"""
import json
import os

from .gates import sha256_text

LEN_BUCKETS = ((0, 2000, "s"), (2000, 8000, "m"), (8000, 10 ** 9, "l"))


def len_bucket(n):
    for lo, hi, tag in LEN_BUCKETS:
        if lo <= n < hi:
            return tag
    return "xl"


def load_manifest(path):
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    docs = data.get("documents") if isinstance(data, dict) else data
    out = []
    for d in docs or []:
        out.append({
            "repo": d.get("repo"),
            "path": d.get("path"),
            "sha256": (d.get("sha256") or "").lower(),
            "content_type": d.get("content_type"),
            "tier": d.get("tier"),
            "chars": d.get("chars"),
            "stratum": "|".join(str(d.get(k)) for k in ("repo", "content_type", "tier")),
        })
    return out


def report_index(store, min_chars=200):
    """tensor_reports → {sha256: row}（平台侧只对 markdown 做真值下游定位）。"""
    rows = store.qj(
        "SELECT id, length(markdown) AS chars, markdown FROM tensor_reports "
        "WHERE markdown IS NOT NULL AND length(markdown) >= %s", (int(min_chars),))
    index = {}
    for r in rows:
        md = r.get("markdown") or ""
        index[sha256_text(md)[7:]] = {"id": int(r["id"]),
                                      "chars": int(r.get("chars") or len(md))}
    return index


def reconcile(docs, index, store, max_title_lookups=400):
    """分层清单 → 工包报告绑定。返回 (bound, unmatched)。"""
    bound, unmatched = [], []
    by_stem = {}
    title_lookups = 0
    for d in docs:
        row = index.get((d.get("sha256") or "").lower())
        method = "sha256"
        if row is None and title_lookups < max_title_lookups:
            stem = os.path.basename(d.get("path") or "").rsplit(".", 1)[0]
            if stem:
                title_lookups += 1
                hits = store.q(
                    "SELECT id, length(markdown) FROM tensor_reports "
                    "WHERE title LIKE %s ORDER BY id DESC LIMIT 1", (f"%{stem}%",))
                if hits:
                    row = {"id": int(hits[0][0]), "chars": int(hits[0][1] or 0)}
                    method = "title"
        if row:
            bound.append({**d, "report_id": row["id"], "report_chars": row["chars"],
                          "match": method})
        else:
            unmatched.append(d)
    return bound, unmatched


def bulk_stem_match(store, stems):
    """一次 SQL 反向标题匹配：报告标题里出现文档 stem（m13784 这类行号 token 命中率高）。"""
    stems = [x for x in stems if x]
    if not stems:
        return {}
    arr = ",".join("'" + x.replace("'", "''") + "'" for x in stems)
    rows = store.q(
        f"SELECT s.stem, min(r.id) FROM unnest(ARRAY[{arr}]::text[]) AS s(stem) "
        "JOIN tensor_reports r ON r.title ILIKE '%' || s.stem || '%' "
        "GROUP BY s.stem")
    return {r[0]: int(r[1]) for r in rows if r and r[0]}


def prestratify(store, batch_size=200, min_chars=200):
    """清单未回时的预分层：scenario_key × 长度桶（repo 归属由报告 tags 首项近似）。"""
    # 分层内按长度降序优选：长的报告信息密度更高（不放域词，纯结构启发式）
    rows = store.q(
        "SELECT id, scenario_key, length(markdown), tags FROM tensor_reports "
        "WHERE markdown IS NOT NULL AND length(markdown) >= %s "
        "AND status = 'published' AND (qa_status IS NULL OR qa_status <> 'rejected') "
        "ORDER BY length(markdown) DESC", (int(min_chars),))
    strata = {}
    for rid, skey, chars, tags in rows:
        try:
            repo = (json.loads(tags) or ["unknown"])[0]
        except Exception:
            repo = "unknown"
        key = f"{repo}|{skey or 'none'}|{len_bucket(int(chars or 0))}"
        strata.setdefault(key, []).append({"report_id": int(rid), "chars": int(chars or 0),
                                           "stratum": key, "match": "prestratified"})
    keys = sorted(strata)
    picked, i = [], 0
    while len(picked) < batch_size and any(strata.values()):
        for k in keys:
            if len(picked) >= batch_size:
                break
            if strata[k]:
                picked.append(strata[k].pop(0))
        i += 1
    return picked, {"strata_total": len(keys), "strata_covered": len(
        {p["stratum"] for p in picked})}


def discover(store, cfg, batch_size=None, manifest_path=None):
    pilot = cfg["pilot"]
    batch_size = int(batch_size or pilot["batch_size"])
    path = manifest_path or pilot.get("stratified_manifest")
    docs = load_manifest(path)
    if docs:
        index = report_index(store, pilot.get("reconcile_min_chars", 200))
        bound, unmatched = reconcile(docs, index, store)
        # 同一 report 只取一份（分层清单可能同时命中同一 markdown 的多个路径）
        seen, dedup = set(), []
        for b in bound:
            if b["report_id"] in seen:
                continue
            seen.add(b["report_id"])
            dedup.append(b)
        # 二次对账：标题里出现文档 stem（sha256/标题直配都没中的再捞一遍）
        stems = {os.path.basename(d.get("path") or "").rsplit(".", 1)[0]: d
                 for d in unmatched if d.get("path")}
        hits = bulk_stem_match(store, list(stems)[:300])
        seen_ids = {b["report_id"] for b in dedup}
        for stem, rid in hits.items():
            if rid in seen_ids:
                continue
            d = stems[stem]
            dedup.append({**d, "report_id": rid, "report_chars": None, "match": "stem"})
            seen_ids.add(rid)
        stem_matched = len(hits)
        picked = dedup[:batch_size]
        report = {
            "mode": "manifest",
            "manifest": path,
            "manifest_docs": len(docs),
            "bound": len(bound),
            "bound_unique_reports": len(dedup),
            "unmatched": len(unmatched),
            "selected": len(picked),
            "strata_total": len({d["stratum"] for d in docs}),
            "strata_covered": len({d["stratum"] for d in picked}),
            "match_methods": {"sha256": sum(1 for b in bound if b["match"] == "sha256"),
                              "title": sum(1 for b in bound if b["match"] == "title"),
                              "stem": stem_matched},
            "fill": 0,
            "unmatched_examples": [d["path"] for d in unmatched[:10]],
        }
    else:
        picked, extra = prestratify(store, batch_size, pilot.get("reconcile_min_chars", 200))
        report = {"mode": "prestratified", "manifest": None, "selected": len(picked), **extra}
    # 清单匹配不足额 → 按 owner 允许的预分层补齐（回后校正），如实记 fill 数
    fill_cfg = cfg["pilot"].get("fill_with_prestratified", True)
    if len(picked) < batch_size and fill_cfg:
        have = {p["report_id"] for p in picked}
        extra, _ = prestratify(store, batch_size - len(picked) + len(have),
                               cfg["pilot"].get("reconcile_min_chars", 200))
        added = 0
        for e in extra:
            if len(picked) >= batch_size:
                break
            if e["report_id"] in have:
                continue
            picked.append(e)
            have.add(e["report_id"])
            added += 1
        report["fill"] = added
        report["selected"] = len(picked)
        if report.get("mode") == "manifest":
            report["strata"] = sorted({p["stratum"] for p in picked})
            report["strata_covered"] = len(report["strata"])
    report["strata"] = sorted({p["stratum"] for p in picked})
    return picked, report