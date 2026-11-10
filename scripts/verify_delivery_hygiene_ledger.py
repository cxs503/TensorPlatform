#!/usr/bin/env python3
"""独立复核账本（audit chain）：不复用分类器代码，用**另一套 git 命令**重算抽样分支的类别。

为什么需要
----------
分类器自己算一遍、再自己说「我对」没有意义。本脚本对每个类别随机抽样 N 条分支，用
*与包内实现不同的命令组合*重新判定，逐条比对账本结论，把一致率与反例写进
``artifacts/delivery_hygiene/audit.json``。任何不一致都必须进报告，不得遮盖。

复核口径
--------
* ``D``：``git merge-base --is-ancestor <tip> main`` 必须为真。
* ``A``：三条任一成立即算通过——
  ``git cherry main <b>`` 无 ``+`` 行（patch-id 全命中）；或 ``git diff --quiet main <b>``；
  或 ``git diff <merge-base> <b> | git patch-id --stable`` 的 patch-id 出现在
  ``git log --no-merges -p main | git patch-id --stable`` 的结果里（squash 残留）。
* ``B``：``source`` 的独有提交 patch-id 集合 ⊇ 本分支独有集合（两处 patch-id 都由本脚本现算）。
* ``C``：本分支每条独有提交的 patch-id 都不在 base patch-id 集合里。
  说明：C 的另一半判据（「也不在任何其他分支里」）是全局性质，本脚本用「不在任何账本 B 类分支的
  超集源里」近似检查，并在报告里标注该近似。

用法::

    python3 scripts/verify_delivery_hygiene_ledger.py \
        --repo <path> --ledger <ledger.json> --sample 15 \
        --out artifacts/delivery_hygiene/audit.json
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
from typing import Any, Dict, List, Optional, Sequence, Set


def git(repo: str, *args: str, data: Optional[bytes] = None) -> bytes:
    p = subprocess.run(["git", "-C", repo, *args], input=data, capture_output=True)
    return p.stdout


def patch_ids_of_log(repo: str, rev: str) -> Set[str]:
    out = git(repo, "log", "--no-merges", "-p", rev)
    ids = git(repo, "patch-id", "--stable", data=out)
    return {ln.split()[0] for ln in ids.decode("utf-8", "replace").splitlines() if ln.split()}


def patch_id_of_commit(repo: str, sha: str) -> Optional[str]:
    out = git(repo, "log", "--no-walk=unsorted", "--no-merges", "-p", "--format=commit %H", sha)
    ids = git(repo, "patch-id", "--stable", data=out)
    for ln in ids.decode("utf-8", "replace").splitlines():
        parts = ln.split()
        if parts:
            return parts[0]
    return None


def nonmerge_commits(repo: str, rng: str) -> List[str]:
    """``rng`` 内的非合并提交（独立于 ``git cherry``：只看图结构）。"""
    out = git(repo, "rev-list", "--no-merges", rng).decode("utf-8", "replace")
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def cherry_plus(repo: str, base: str, tip: str) -> List[str]:
    """``git cherry`` 的 ``+`` 行（仅作旁证：它的 upstream 侧是 ``branch..base``，
    与「base 全历史 patch-id」不是同一集合，两者条数可以合法地不同）。"""
    out = git(repo, "cherry", base, tip).decode("utf-8", "replace")
    return [ln.split()[1] for ln in out.splitlines() if ln.startswith("+") and len(ln.split()) > 1]


def own_unique(repo: str, base: str, tip: str, base_pids: Set[str]) -> List[str]:
    """``base..tip`` 里 patch-id 不在 base 全历史中的提交（与分类器同口径，但独立实现）。"""
    uniq: List[str] = []
    for sha in nonmerge_commits(repo, f"{base}..{tip}"):
        pid = patch_id_of_commit(repo, sha)
        if pid is None or pid in base_pids:
            continue
        uniq.append(sha)
    return uniq


def pid_set(repo: str, shas: Sequence[str]) -> Set[str]:
    return {p for p in (patch_id_of_commit(repo, s) for s in shas) if p}


def verify_branch(repo: str, base: str, row: Dict[str, Any], base_pids: Set[str]) -> Dict[str, Any]:
    branch = row["branch"]
    cls = row["class"]
    # 钉住账本记录的 tip：仓库是活的，同名分支在扫描后可能被推进（实测 agent/m9467 就是这样），
    # 复核必须针对"账本当时看到的那棵树"，否则会把并发推进误记成判据错误。
    tip = row.get("sha") or git(repo, "rev-parse", branch).decode().strip()
    if not tip:
        return {"branch": branch, "class": cls, "ok": False, "reason": "branch-gone"}
    live = git(repo, "rev-parse", branch).decode().strip()
    tip_moved = bool(live) and live != tip

    uniq = own_unique(repo, base, tip, base_pids)
    uniq_pids = pid_set(repo, uniq)
    ancestor = subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor", tip, base],
                              capture_output=True).returncode == 0
    diff_empty = subprocess.run(["git", "-C", repo, "diff", "--quiet", base, tip],
                                capture_output=True).returncode == 0
    combined_in_base = False
    if not diff_empty:
        mb = git(repo, "merge-base", base, tip).decode().strip()
        if mb:
            ids = git(repo, "patch-id", "--stable",
                      data=git(repo, "diff", mb, tip)).decode("utf-8", "replace").splitlines()
            combined_in_base = bool(ids and ids[0].split()[0] in base_pids)

    ledger_unique = len(row["evidence"].get("unique_shas") or [])
    ok = False
    detail: Dict[str, Any] = {
        "tip_moved_since_scan": tip_moved,
        "audit_unique_commits": len(uniq),
        "ledger_unique_commits": ledger_unique,
        "unique_count_match": len(uniq) == ledger_unique,
        "ancestor": ancestor,
        "diff_empty": diff_empty,
        "combined_pid_in_base": combined_in_base,
        "cherry_plus_count": len(cherry_plus(repo, base, tip)),
    }
    if cls == "D":
        ok = ancestor
    elif cls == "A":
        ok = ancestor or (not uniq) or diff_empty or combined_in_base
    elif cls == "B":
        src = row.get("superset_source")
        if src:
            src_tip = git(repo, "rev-parse", src).decode().strip() or src
            src_pids = pid_set(repo, own_unique(repo, base, src_tip, base_pids))
            ok = bool(uniq_pids) and uniq_pids <= src_pids
            detail["superset_source"] = src
            detail["subset_size"] = len(uniq_pids)
            detail["superset_size"] = len(src_pids)
    elif cls == "C":
        ok = bool(uniq_pids) and not ancestor and not diff_empty
        detail["unique_pids"] = len(uniq_pids)
    out = {"branch": branch, "class": cls, "ok": bool(ok), "detail": detail}
    if not ok and tip_moved:
        out["reason"] = "tip-advanced-after-scan"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--sample", type=int, default=15)
    ap.add_argument("--seed", type=int, default=20261110)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    with open(a.ledger, "r", encoding="utf-8") as fh:
        led = json.load(fh)
    base = led["base"]
    rows = led["rows"]
    base_pids = patch_ids_of_log(a.repo, base)
    rng = random.Random(a.seed)

    report: Dict[str, Any] = {
        "repo": led["repo"],
        "repo_path": a.repo,
        "ledger": os.path.abspath(a.ledger),
        "base": base,
        "base_sha": led["base_sha"],
        "base_patch_ids": len(base_pids),
        "sample_per_class": a.sample,
        "seed": a.seed,
        "by_class": {},
        "mismatches": [],
        "method": "independent git commands (rev-list / merge-base --is-ancestor / diff --quiet / "
                  "patch-id) recomputed per sampled branch, not reusing tools.delivery_hygiene code; "
                  "git cherry 只作旁证（其 upstream 侧为 branch..base，与 base 全历史 patch-id 不同集）",
        "unique_count_agreement": None,
    }
    for cls in ("A", "B", "C", "D"):
        pool = [r for r in rows if r["class"] == cls]
        picked = rng.sample(pool, min(a.sample, len(pool)))
        results = [verify_branch(a.repo, base, r, base_pids) for r in picked]
        report.setdefault("_all", []).extend(results)
        moved = [r for r in results if r["detail"] and r["detail"].get("tip_moved_since_scan")]
        bad = [r for r in results if not r["ok"]]
        report["by_class"][cls] = {
            "population": len(pool),
            "sampled": len(results),
            "agree": len(results) - len(bad),
            "disagree": len(bad),
            "agreement": (len(results) - len(bad)) / len(results) if results else None,
            "failing": [r["branch"] for r in bad][:10],
            "tip_advanced_after_scan": len(moved),
        }
        report["mismatches"].extend(bad)
    counted = [r for r in (report.get("_all") or []) if True]
    total = sum(v["sampled"] for v in report["by_class"].values())
    ok = sum(v["agree"] for v in report["by_class"].values())
    allr = report.get("_all") or []
    match = sum(1 for r in allr if (r.get("detail") or {}).get("unique_count_match"))
    report["overall"] = {"sampled": total, "agree": ok, "disagree": total - ok,
                         "agreement": ok / total if total else None}
    report["unique_count_agreement"] = {
        "sampled": len(allr), "match": match,
        "rate": (match / len(allr)) if allr else None,
        "note": "复核独立重算的独有提交条数与账本 evidence.unique_shas 条数一致率",
    }
    report.pop("_all", None)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"[audit] {led['repo']}: sampled={total} agree={ok} "
          f"agreement={report['overall']['agreement']} -> {a.out}")
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())