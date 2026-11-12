#!/usr/bin/env python3
"""Crossref 引文核验器（pool-expand-r1 防造假闸）。

任务红线：ref_source 必须"真实可引用、查得到"，伪造出处=零容忍。
本脚本把 ops/case_pool/seeds/r1_*.json 里的每一条 citation 送去 Crossref
`query.bibliographic` 检索，取回前 3 条候选，按标题归一化相似度 + 年份窗口打分：

  verified   : 最佳候选标题相似度 >= 0.80 且年份落在引文年份 ±2
  weak       : 相似度 0.55-0.80（需人工判读，不写库）
  not_found  : 无合格候选（教科书/专著常见；须人工给出 ISBN/馆藏或改引可检索版本）

结果落 artifacts/m16051/ref_crossref_verify.json（含每例 DOI，可复现）并把缓存写
ops/case_pool/refs_crossref_cache.json（离线可重跑，缓存命中不发请求）。

用法：python3 ops/case_pool/verify_refs_crossref.py [--refresh]
"""
from __future__ import annotations

import argparse
import difflib
import glob
import json
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SEED_GLOB = os.path.join(HERE, "seeds", "r1_*.json")
CACHE = os.path.join(HERE, "refs_crossref_cache.json")
OUT = os.path.abspath(os.path.join(HERE, "..", "..", "artifacts", "m16051", "ref_crossref_verify.json"))
UA = "TensorPlatform-casepool/1.0 (mailto:case-pool@tensorplatform.local)"


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def cite_year(text):
    m = re.findall(r"\b(1[89]\d\d|20\d\d)\b", str(text))
    return int(m[0]) if m else None


def query(text, rows=3):
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(
        {"rows": rows, "select": "DOI,title,author,issued,container-title,type",
         "query.bibliographic": text[:300]})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.load(r)["message"]["items"]


def best_match(case, items):
    want_year = cite_year(case["citation"]) or cite_year(case["ref_source"])
    best = None
    for it in items:
        title = (it.get("title") or [""])[0]
        sim = difflib.SequenceMatcher(None, norm(title), norm(case["citation"])).ratio()
        # 引文串里带整条题名时，用"题名被引文覆盖度"补一个分
        cover = 1.0 if norm(title) and norm(title) in norm(case["citation"]) else 0.0
        score = max(sim, cover)
        yr = None
        dp = (it.get("issued") or {}).get("date-parts") or [[None]]
        if dp and dp[0] and dp[0][0]:
            yr = int(dp[0][0])
        year_ok = (want_year is None) or (yr is not None and abs(yr - want_year) <= 2)
        best = best if (best and best["score"] >= score) else {
            "doi": it.get("DOI"), "title": title, "year": yr,
            "container": (it.get("container-title") or [""])[0], "type": it.get("type"),
            "score": round(score, 3), "year_ok": year_ok}
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    cache = {}
    if os.path.exists(CACHE) and not args.refresh:
        cache = json.load(open(CACHE, encoding="utf-8"))

    cases = []
    for path in sorted(glob.glob(SEED_GLOB)):
        cases.extend(json.load(open(path, encoding="utf-8")))

    rows = []
    for c in cases:
        key = c["case_code"]
        if key in cache:
            rec = cache[key]
            rec["from_cache"] = True
        else:
            try:
                rec = best_match(c, query(c["citation"]))
            except Exception as exc:  # 网络/限流
                rec = {"doi": None, "title": None, "score": 0.0, "year": None,
                       "year_ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
            rec["from_cache"] = False
            cache[key] = {k: v for k, v in rec.items() if k != "from_cache"}
            time.sleep(1.2)
        if not rec or not rec.get("doi"):
            verdict = "not_found"
        elif rec["score"] >= 0.80 and rec.get("year_ok"):
            verdict = "verified"
        elif rec["score"] >= 0.55:
            verdict = "weak"
        else:
            verdict = "not_found"
        rows.append({"case_code": key, "repo": c["repo"], "ref_source": c["ref_source"],
                     "citation": c["citation"], "verdict": verdict,
                     "best": rec, "from_cache": rec.get("from_cache", False)})

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(rows, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    tally = {}
    for r in rows:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    print(json.dumps(tally, ensure_ascii=False))
    for r in rows:
        if r["verdict"] != "verified":
            print("%-34s %-9s %s | %s" % (r["case_code"], r["verdict"],
                                          (r["best"] or {}).get("doi"),
                                          ((r["best"] or {}).get("title") or "")[:70]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())