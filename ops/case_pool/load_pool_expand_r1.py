#!/usr/bin/env python3
"""tensor_case_pool 扩容 r1 加载器（FEAT-CASEPOOL-100 供给线修复，mission 16051）。

单一事实源 = ops/case_pool/seeds/r1_<repo>.json（六仓各 7 例，共 42 例）。
本脚本做四件事，每件都留机读证据（artifacts/m16051/）：

1. 校验：case_code 唯一、name/ref_source/tol_policy 非空、repo 在六仓白名单内；
2. 去重对拍（对现有全池）：case_code 冲突 / 名称归一化相等 / CJK-bigram Jaccard>=0.5 /
   同仓同被引作者族（引文拉丁姓氏交集）四道闸；
3. 锚点对拍：拿 seed 的 anchor_probe 关键词扫 feature_ledger(slug,title)，
   命中即需人工确认为"精确覆盖锚"才填 linked_anchor；未命中 -> linked_anchor 留空（可被供给）；
4. 事务写入：ALTER ADD COLUMN IF NOT EXISTS source（幂等）+ INSERT ... ON CONFLICT DO NOTHING，
   同事务内取回执（插入行数、全池分布、抽样 10 例全字段）。

用法：
  python3 ops/case_pool/load_pool_expand_r1.py --check      # 只校验+对拍，不写库
  python3 ops/case_pool/load_pool_expand_r1.py --apply      # 事务写库 + 回执
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import unicodedata
from collections import Counter, OrderedDict

SEED_GLOB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seeds", "r1_*.json")
OUT_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "artifacts", "m16051"))
REPOS = ["TensorLBM", "TensorFEM", "TensorFVM", "TensorSPH", "TensorDG", "TensorDEM"]
BATCH = "pool-expand-r1"
MISSION_ID = 16051
DSN = os.environ.get("TP_DSN", "dbname=%s user=postgres" % os.environ.get("TP_PG_DB", "tensorplatform"))
REQUIRED = ["case_code", "repo", "name", "ref_source", "tol_policy", "dedup_key", "citation"]
NAME_JACCARD_FLAG = 0.50


def load_seeds():
    cases = []
    for path in sorted(glob.glob(SEED_GLOB)):
        with open(path, encoding="utf-8") as fh:
            rows = json.load(fh)
        for r in rows:
            r["_seed_file"] = os.path.basename(path)
            cases.append(r)
    return cases


# ---------------------------------------------------------------- validation
def validate(cases):
    problems = []
    seen = {}
    for c in cases:
        for k in REQUIRED:
            if not str(c.get(k, "")).strip():
                problems.append({"case_code": c.get("case_code"), "error": "missing_field:%s" % k})
        if c.get("repo") not in REPOS:
            problems.append({"case_code": c.get("case_code"), "error": "repo_not_in_whitelist:%s" % c.get("repo")})
        cc = c.get("case_code", "")
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{3,60}", cc):
            problems.append({"case_code": cc, "error": "case_code_format"})
        if cc in seen:
            problems.append({"case_code": cc, "error": "duplicate_inside_batch:%s" % seen[cc]})
        seen[cc] = c.get("_seed_file")
        tol = str(c.get("tol_policy", ""))
        if not re.search(r"<\d+|>=\d|区间|0\.\d", tol):
            problems.append({"case_code": cc, "error": "tol_policy_not_quantified"})
    return problems


# ---------------------------------------------------------------- dedup keys
def norm_code(s):
    return re.sub(r"[^0-9a-z]", "", str(s).lower())


def norm_name(s):
    s = unicodedata.normalize("NFKC", str(s)).lower()
    return re.sub(r"[\s/·,;:()\[\]（）\-_]+", "", s)


def bigrams(s):
    s = norm_name(s)
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


STOP = {"et", "al", "and", "the", "of", "in", "for", "with", "on", "to", "de", "der", "van", "von"}


def citation_family(text):
    """引文中的拉丁姓氏候选（>=3 字母、首字母大写、非停用词）。"""
    out = set()
    for tok in re.findall(r"[A-Z][A-Za-z]{2,}", str(text)):
        if tok.lower() in STOP:
            continue
        out.add(tok)
    return out


def dedup_against_pool(cases, pool_rows):
    """四道闸：code 归一化 / 名称归一化 / 名称 bigram / 同仓引文族。"""
    pool = []
    for r in pool_rows:
        pool.append({
            "case_code": r["case_code"], "repo": r["repo"], "name": r["name"],
            "ref_source": r["ref_source"], "status": r["status"],
            "code_n": norm_code(r["case_code"]), "name_n": norm_name(r["name"]),
            "bg": bigrams(r["name"]), "fam": citation_family(r["ref_source"]),
        })
    report = {"gate_hits": [], "per_case": []}
    for c in cases:
        hits = []
        cn, nn, bg = norm_code(c["case_code"]), norm_name(c["name"]), bigrams(c["name"])
        fam = citation_family(c["ref_source"]) | citation_family(c.get("citation", ""))
        for p in pool:
            why = []
            if cn == p["code_n"]:
                why.append("code_normalized_equal")
            if nn == p["name_n"]:
                why.append("name_normalized_equal")
            jj = jaccard(bg, p["bg"])
            if jj >= NAME_JACCARD_FLAG:
                why.append("name_bigram_jaccard=%.2f" % jj)
            shared = fam & p["fam"]
            if shared and c["repo"] == p["repo"]:
                why.append("same_repo_citation_family=%s" % ",".join(sorted(shared)))
            if why:
                hits.append({"existing": p["case_code"], "repo": p["repo"], "name": p["name"],
                             "why": why})
        report["per_case"].append({"case_code": c["case_code"], "repo": c["repo"],
                                   "dedup_key": c["dedup_key"], "hits": hits})
        for h in hits:
            hard = [w for w in h["why"] if w.startswith(("code_normalized_equal", "name_normalized_equal"))]
            report["gate_hits"].append({"case_code": c["case_code"], "existing": h["existing"],
                                        "hard_block": bool(hard), "why": h["why"]})
    report["hard_blocks"] = [g for g in report["gate_hits"] if g["hard_block"]]
    return report


def dedup_key_consistency(cases, pool_rows):
    """同 (repo, dedup_key) 只允许出现一次；跨仓允许（不同离散化体系，与既有
    TAYLOR-GREEN-LBM/SPH、SOD-FVM/DG 同例不同仓的既有惯例一致）。"""
    cnt = Counter((c["repo"], c["dedup_key"]) for c in cases)
    bad = [{"repo": k[0], "dedup_key": k[1], "count": v} for k, v in cnt.items() if v > 1]
    cross = Counter(c["dedup_key"] for c in cases)
    cross_dup = {k: v for k, v in cross.items() if v > 1}
    return {"in_repo_duplicates": bad, "cross_repo_same_setup": cross_dup}


# ---------------------------------------------------------------- anchor probe
def anchor_probe(cases, ledger_rows, pool_rows=None):
    """精确覆盖锚判定 = 三重约束：
    (a) ledger slug 以 CASE- 开头且 domain == 本算例仓；
    (b) 锚名主体（去掉 CASE- 前缀归一化后）与本算例 case_code 互相包含（同算例才可能覆盖）；
    (c) 该锚没有被既有池行"占用"（既有行 case_code 归一化 == 锚名主体，说明锚已归属别的算例）。
    任一不满足 -> 不填 linked_anchor（留空才能被 case_feeder 供给）。"""
    out = []
    owned = {}
    for r in (pool_rows or []):
        owned[(norm_code(r["case_code"]), r["repo"])] = r["case_code"]
    for c in cases:
        kws = [k for k in c.get("anchor_probe", []) if k]
        matches = []
        for lr in ledger_rows:
            hay = ("%s %s" % (lr["slug"], lr["title"])).lower()
            for kw in kws:
                if kw.lower() in hay:
                    matches.append({"slug": lr["slug"], "title": lr["title"],
                                    "domain": lr["domain"], "status": lr["status"], "keyword": kw})
                    break
        exact = []
        code = norm_code(c["case_code"])
        for m in matches:
            if not (m["slug"].startswith("CASE-") and m["domain"] == c["repo"]):
                continue
            body = norm_code(m["slug"][5:])
            if not body:
                continue
            if not (body in code or code in body):
                continue
            if (body, c["repo"]) in owned:
                m = dict(m, rejected="anchor_already_owned_by:%s" % owned[(body, c["repo"])])
                continue
            exact.append(m)
        out.append({"case_code": c["case_code"], "repo": c["repo"],
                    "declared_linked_anchor": c.get("linked_anchor") or None,
                    "keyword_matches": matches, "exact_covering_anchor": exact,
                    "decision": ("linked_anchor=%s" % exact[0]["slug"]) if exact else "linked_anchor=NULL"})
    return out


# ---------------------------------------------------------------- db
def fetch_pool(cur):
    cur.execute("select case_code, repo, name, ref_source, status, linked_anchor from tensor_case_pool order by id")
    return [{"case_code": r[0], "repo": r[1], "name": r[2], "ref_source": r[3],
             "status": r[4], "linked_anchor": r[5]} for r in cur.fetchall()]


def fetch_ledger(cur):
    cur.execute("select slug, title, domain, status from feature_ledger order by slug")
    return [{"slug": r[0], "title": r[1], "domain": r[2], "status": r[3]} for r in cur.fetchall()]


def apply_batch(cases, anchor_report):
    import psycopg
    os.makedirs(OUT_DIR, exist_ok=True)
    anchor_by_code = {a["case_code"]: a for a in anchor_report}
    with psycopg.connect(DSN) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                before = cur.execute("select count(*) from tensor_case_pool").fetchone()[0]
                # 幂等 DDL：加 source 列（历史 84 行保持 NULL，不误标来源）
                cur.execute("alter table tensor_case_pool add column if not exists source text")
                inserted = []
                for c in cases:
                    decision = anchor_by_code[c["case_code"]]
                    anchor = c.get("linked_anchor") if decision["exact_covering_anchor"] else None
                    cur.execute(
                        """insert into tensor_case_pool
                             (case_code, repo, name, ref_source, tol_policy, tier,
                              linked_anchor, status, mission_id, source)
                           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           on conflict (case_code) do nothing
                           returning case_code""",
                        (c["case_code"], c["repo"], c["name"], c["ref_source"], c["tol_policy"],
                         c.get("tier", "core"), anchor, "pending", MISSION_ID, BATCH))
                    row = cur.fetchone()
                    if row:
                        inserted.append(row[0])
                after = cur.execute("select count(*) from tensor_case_pool").fetchone()[0]
                dist = cur.execute(
                    """select repo, status, count(*) from tensor_case_pool
                        group by 1,2 order by 1,2""").fetchall()
                linked_pending = cur.execute(
                    """select count(*) from tensor_case_pool
                        where status='pending' and linked_anchor is null""").fetchone()[0]
                pend_total = cur.execute(
                    "select count(*) from tensor_case_pool where status='pending'").fetchone()[0]
                # 抽样 10 例全字段（确定性抽样：插入顺序每 4 个取 1）
                sample_codes = inserted[::4][:10]
                cur.execute(
                    """select id, case_code, repo, name, ref_source, tol_policy, tier,
                              linked_anchor, status, mission_id, source, created_at
                         from tensor_case_pool where case_code = any(%s)
                        order by case_code""", (sample_codes,))
                cols = [d[0] for d in cur.description]
                sample = [OrderedDict(zip(cols, [str(v) if v is not None else None for v in row]))
                          for row in cur.fetchall()]
                batch_rows = cur.execute(
                    """select repo, count(*) from tensor_case_pool
                        where source=%s group by 1 order by 1""", (BATCH,)).fetchall()
                dupes = cur.execute(
                    """select case_code, count(*) from tensor_case_pool
                        group by 1 having count(*)>1""").fetchall()
            receipt = {
                "batch": BATCH, "mission_id": MISSION_ID, "dsn": DSN,
                "pool_rows_before": before, "inserted": len(inserted),
                "pool_rows_after": after, "duplicate_case_code_in_pool": dupes,
                "inserted_case_codes": inserted,
                "batch_rows_by_repo": [{"repo": r[0], "rows": r[1]} for r in batch_rows],
                "pool_distribution": [{"repo": r[0], "status": r[1], "rows": r[2]} for r in dist],
                "pending_total": pend_total,
                "pending_without_anchor": linked_pending,
                "supplyable_pending": linked_pending,
                "sample_10_full_fields": sample,
            }
    return receipt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="事务写库")
    ap.add_argument("--check", action="store_true", help="只校验/对拍")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    cases = load_seeds()
    problems = validate(cases)

    import psycopg
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            pool_rows = fetch_pool(cur)
            ledger_rows = fetch_ledger(cur)

    dedup = dedup_against_pool(cases, pool_rows)
    keychk = dedup_key_consistency(cases, pool_rows)
    anchors = anchor_probe(cases, ledger_rows, pool_rows)

    summary = {
        "batch": BATCH, "mission_id": MISSION_ID, "cases": len(cases),
        "by_repo": dict(Counter(c["repo"] for c in cases)),
        "validation_problems": problems,
        "dedup": {"hard_blocks": dedup["hard_blocks"], "soft_hits": len(dedup["gate_hits"]) - len(dedup["hard_blocks"])},
        "dedup_key": keychk,
        "anchor": {"exact_covering": sum(1 for a in anchors if a["exact_covering_anchor"]),
                   "left_null": sum(1 for a in anchors if not a["exact_covering_anchor"])},
        "pool_rows_existing": len(pool_rows),
    }
    with open(os.path.join(OUT_DIR, "pool_expand_r1_dedup.json"), "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "detail": dedup["per_case"], "gate_hits": dedup["gate_hits"],
                   "dedup_key": keychk}, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT_DIR, "anchor_probe.json"), "w", encoding="utf-8") as fh:
        json.dump({"method": "keyword sweep over feature_ledger(slug,title); exact covering anchor = "
                             "ledger slug 以 CASE- 开头且 domain==repo 且关键词命中；否则 linked_anchor 留空（保证可被供给）",
                   "summary": summary["anchor"], "per_case": anchors}, fh, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))

    ok = not problems and not dedup["hard_blocks"] and not keychk["in_repo_duplicates"]
    if not ok:
        print("!! 校验未通过，拒绝写库")
        return 1
    if args.apply:
        receipt = apply_batch(cases, anchors)
        with open(os.path.join(OUT_DIR, "pool_expand_r1_receipt.json"), "w", encoding="utf-8") as fh:
            json.dump(receipt, fh, ensure_ascii=False, indent=1)
        print(json.dumps({k: v for k, v in receipt.items() if k != "sample_10_full_fields"},
                         ensure_ascii=False, indent=1))
        print("--- 抽样 10 例全字段 ---")
        for s in receipt["sample_10_full_fields"]:
            print(json.dumps(s, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())