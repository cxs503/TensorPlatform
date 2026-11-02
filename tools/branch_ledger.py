#!/usr/bin/env python3
"""未落地分支台账（infra 席）：把 ``agent/*`` 分支堆分类成「已并入 / 补丁等价已并入 / 未落地」，
并与平台报告交叉核验，量化「成果未入库」风险与重复劳动面。

动机
----
平台每单都在工作树上建 ``agent/m<mission>`` 分支，交付后由平台推送/合并。但分支只增不减：
TensorPlatform 本地仓库一度持有 600+ 条 ``agent/m*`` 分支，提案池把「未合并分支 372 条」当作
事实源反复引用（prop-1573），却**没有任何工具能回答**：

1. 这堆分支里**哪些确实还带着主干没有的提交**（= 真未落地成果）？
2. 哪些只是「补丁等价物已经进主干」（rebase / cherry-pick / squash 后留下的空壳）？
3. 哪些**既有未落地成果、又已经发过报告**（= 最高优先级：结论已公布、代码没落地）？
4. 哪些分支改的是**同一批文件**（= 后来者极易重复劳动的路径）？

没有这张台账，任何新席位都只能靠猜，重复劳动与「报告绿、代码没落」都无从发现。

判据（每一条都可被推翻）
------------------------
* **ANCHOR**：``base..branch`` 无提交（分支已在主干历史里）⇒ 已并入，可删。
* **BANKED**：``base..branch`` 有提交，但 ``git cherry base branch`` 全部为 ``-``
  ⇒ 提交按 patch-id 全部命中主干（rebase/cherry-pick/squash 的残壳），内容已入库。
* **UNIQUE**：``git cherry`` 至少一条 ``+`` ⇒ 主干**没有**等价补丁，属真未落地成果。
  ``git cherry`` 是唯一判据：只看 ``rev-list --count`` 会把「已 squash 进主干的旧分支」
  误判为未落地（这正是本工具要消灭的假信号）。

交叉核验
--------
``--reports-json`` 给一份报告快照（``GET /api/v1/tensor/reports`` 的原样 JSON，或
``{"data": [...]}``），按 ``mission_id`` 关联分支名里的 ``m<id>``：

* ``REPORTED_UNMERGED``：有 UNIQUE 提交 **且** 有已发布报告 ⇒ 结论公布了、代码没落地。
* ``UNREPORTED``：有 UNIQUE 提交但查不到报告 ⇒ 成果既没入库也没留痕。
* ``ALREADY_IN_BASE``：ANCHOR/BANKED。

纪律
----
* 只读：只跑 ``git`` 查询命令（``for-each-ref`` / ``rev-list`` / ``cherry`` / ``diff`` /
  ``merge-base`` / ``merge-tree`` 旧式只读形式），不建/删/改任何 ref，不写对象库，不改 .git 配置。
* 零第三方依赖：仅标准库；matplotlib 只在 ``--figures`` 时按需导入。
* 可复算：输出按分支名排序，JSON 内除 ``generated_at`` 外无可变字段。

用法
----
    python3 tools/branch_ledger.py --repo . --out artifacts/branch_ledger --figures
    python3 tools/branch_ledger.py --repo . --reports-json artifacts/branch_ledger/reports_snapshot.json
    python3 tools/branch_ledger.py --repo . --pattern 'agent/*' --include-remote --conflict-check

产物
----
* ``<out>/ledger.json``：逐分支记录 + 统计 + 重复劳动簇
* ``<out>/ledger.md``：人读摘要（含「开工前先查这张表」的使用说明）
* ``--figures``：``<out>/fig_branch_classes_by_month.png`` 等
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA_VERSION = "1.0"

CLASS_ANCHOR = "ANCHOR"      # base..branch 为空：已在主干历史
CLASS_BANKED = "BANKED"      # 有提交但 patch-id 全部命中主干
CLASS_CONTENT_SAME = "CONTENT_SAME"  # 补丁不等价，但触及路径上内容与主干完全一致（squash 残壳）
CLASS_UNIQUE = "UNIQUE"      # 补丁不等价且触及路径上内容与主干有净差异
CLASSES: Tuple[str, ...] = (CLASS_ANCHOR, CLASS_BANKED, CLASS_CONTENT_SAME, CLASS_UNIQUE)

LANDING_REPORTED_UNMERGED = "REPORTED_UNMERGED"
LANDING_UNREPORTED = "UNREPORTED"
LANDING_IN_BASE = "ALREADY_IN_BASE"

DEFAULT_PATTERN = "agent/*"
CONFLICT_MARKER = "<<<<<<<"
CLUSTER_MIN_SHARED_FILES = 5      # 两条 UNIQUE 分支共改 ≥5 个文件即视为可疑重复面
CLUSTER_JACCARD = 0.34            # 或 Jaccard ≥ 0.34


# ── git plumbing（只读） ───────────────────────────────────────────────────────
def run_git(repo: str, *args: str, timeout: int = 120) -> Tuple[int, str]:
    """跑一条只读 git 命令，返回 (rc, stdout)。失败不抛异常（分支可能已被删）。"""
    try:
        p = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True, text=True, timeout=timeout,
        )
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def list_refs(repo: str, pattern: str, include_remote: bool) -> List[Dict[str, str]]:
    """列出匹配 pattern 的 ref（本地在前，远端需显式开启）。"""
    fmt = "%(refname:short)%09%(objectname)%09%(committerdate:iso-strict)"
    rc, out = run_git(repo, "for-each-ref", f"--format={fmt}", "refs/heads/" + pattern)
    refs: List[Dict[str, str]] = []
    if rc == 0:
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                refs.append({"name": parts[0], "sha": parts[1], "date": parts[2], "kind": "local"})
    if include_remote:
        rc, out = run_git(repo, "for-each-ref", f"--format={fmt}", "refs/remotes/origin/" + pattern)
        if rc == 0:
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) == 3:
                    name = parts[0]
                    if name.endswith("/HEAD"):
                        continue
                    refs.append({"name": name, "sha": parts[1], "date": parts[2], "kind": "remote"})
    refs.sort(key=lambda r: r["name"])
    return refs


def rev_count(repo: str, rng: str) -> int:
    rc, out = run_git(repo, "rev-list", "--count", rng)
    if rc != 0:
        return 0
    try:
        return int(out.strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0


def cherry_split(repo: str, base: str, ref: str) -> Tuple[int, int]:
    """返回 (unique, equivalent)：git cherry 的 + / - 计数（patch-id 判等）。"""
    rc, out = run_git(repo, "cherry", base, ref)
    if rc != 0:
        return 0, 0
    plus = minus = 0
    for line in out.splitlines():
        if line.startswith("+"):
            plus += 1
        elif line.startswith("-"):
            minus += 1
    return plus, minus


def merge_base(repo: str, base: str, ref: str) -> Optional[str]:
    rc, out = run_git(repo, "merge-base", base, ref)
    sha = out.strip()
    return sha if rc == 0 and sha else None


def changed_files(repo: str, from_sha: str, ref: str) -> List[str]:
    rc, out = run_git(repo, "diff", "--name-only", from_sha, ref)
    if rc != 0:
        return []
    return sorted({p.strip() for p in out.splitlines() if p.strip()})


def content_identical(repo: str, base: str, ref: str, paths: Sequence[str]) -> bool:
    """分支触及的路径上，分支尖端内容是否与 ``base`` 完全一致（``git diff --quiet``）。

    专治 squash 合并：squash 会重写 patch-id，``git cherry`` 全部判 ``+``，单看 patch-id 会把
    「内容其实已进主干」的分支误报成未落地。这里用内容做第二道闸：触及路径净差异为空 ⇒
    没有未落地增量。反之（``False``）只说明**这些路径上存在净差异**，是 UNIQUE 的可观测依据，
    不推断作者意图。
    """
    if not paths:
        return False
    rc, _ = run_git(repo, "diff", "--quiet", base, ref, "--", *paths)
    return rc == 0


def shortstat(repo: str, from_sha: str, ref: str) -> Dict[str, int]:
    rc, out = run_git(repo, "diff", "--shortstat", from_sha, ref)
    stats = {"files": 0, "insertions": 0, "deletions": 0}
    if rc != 0 or not out.strip():
        return stats
    for num, word in re.findall(r"(\d+)\s+(files?|insertions?|deletions?)", out):
        key = {"file": "files", "files": "files", "insertion": "insertions",
               "insertions": "insertions", "deletion": "deletions",
               "deletions": "deletions"}[word]
        stats[key] = int(num)
    return stats


def conflicts_with_base(repo: str, base: str, ref: str) -> Optional[bool]:
    """旧式 ``git merge-tree``（只读、不写对象库）预测与 base 的合并是否冲突。

    返回 True=预测冲突 / False=可干净合并 / None=无法判定。判据是输出里出现冲突标记，
    或 "changed in both" 段之后的内容分叉；这里只用冲突标记 + 显式 "CONFLICT" 关键词，
    保守但不臆造：无法判定就返回 None 而不是猜 False。
    """
    mb = merge_base(repo, base, ref)
    if not mb:
        return None
    rc, out = run_git(repo, "merge-tree", mb, base, ref)
    if rc not in (0, 1):
        return None
    if CONFLICT_MARKER in out or "CONFLICT" in out:
        return True
    return False


def parse_mission_id(branch_name: str) -> Optional[int]:
    """从分支名解析 mission id：agent/m1875 / m1875 / agent/m1875-extra / agent/x-m1875-y。"""
    m = re.search(r"(?:^|[/_-])m(\d+)(?:$|[/_-])", branch_name)
    if m:
        return int(m.group(1))
    m = re.search(r"(?:^|[/_-])m(\d+)", branch_name)
    return int(m.group(1)) if m else None


# ── 分类 ───────────────────────────────────────────────────────────────────────
def classify(ahead: int, unique: int, content_same: bool = False) -> str:
    """ANCHOR / BANKED / CONTENT_SAME / UNIQUE —— 见模块文档的判据。"""
    if ahead <= 0:
        return CLASS_ANCHOR
    if unique <= 0:
        return CLASS_BANKED
    return CLASS_CONTENT_SAME if content_same else CLASS_UNIQUE


def landing_status(cls: str, has_report: bool) -> str:
    if cls != CLASS_UNIQUE:
        return LANDING_IN_BASE
    return LANDING_REPORTED_UNMERGED if has_report else LANDING_UNREPORTED


def load_reports(path: Optional[str]) -> Dict[int, Dict[str, Any]]:
    """读报告快照，返回 mission_id -> {id,title,created_at}（同一 mission 取最新一条）。"""
    if not path or not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    items = raw if isinstance(raw, list) else (raw.get("data") or raw.get("items") or raw.get("reports") or [])
    out: Dict[int, Dict[str, Any]] = {}
    for r in items:
        if not isinstance(r, dict):
            continue
        mid = r.get("mission_id")
        try:
            mid = int(mid)
        except (TypeError, ValueError):
            continue
        prev = out.get(mid)
        if prev is None or str(r.get("created_at") or "") >= str(prev.get("created_at") or ""):
            out[mid] = {
                "id": r.get("id"),
                "title": r.get("title"),
                "created_at": r.get("created_at"),
            }
    return out


def probe_reports_by_mission(mission_ids: Iterable[int], base_url: str, token: str,
                             fetch: Optional[Any] = None,
                             timeout: int = 20) -> Dict[int, Dict[str, Any]]:
    """对给定 mission id 逐个查 ``/tensor/reports?mission_id=X``，返回 mission_id -> 报告摘要。

    为什么需要它：``/tensor/reports`` 的 ``limit`` 被服务端硬顶在 100，且 ``page/offset/skip``
    全部被忽略（实测同一批 id），**无法分页拉全量**；但 ``mission_id`` 过滤是生效的。
    因此「未落地分支的报告核验」只能按 mission 逐个补探，而不是靠拉全量再本地过滤。

    ``fetch`` 可注入（默认 urllib），便于离线测试；查询失败不算命中（保守）。
    """
    out: Dict[int, Dict[str, Any]] = {}
    for mid in mission_ids:
        mid = int(mid)
        payload = None
        for _attempt in range(2):   # 一次重试：瞬时失败不该被记成「无报告」
            try:
                if fetch is not None:
                    payload = fetch(mid)
                else:
                    payload = _http_get_json(
                        f"{base_url.rstrip('/')}/tensor/reports?mission_id={mid}", token, timeout)
                break
            except Exception:
                payload = None
        if payload is None:
            continue
        items = (payload.get("items") or payload.get("data") or []) if isinstance(payload, dict) else (payload or [])
        best = None
        for r in items:
            try:
                if int(r.get("mission_id")) != mid:
                    continue
            except (TypeError, ValueError):
                continue
            if best is None or str(r.get("created_at") or "") >= str(best.get("created_at") or ""):
                best = r
        if best is not None:
            out[mid] = {"id": best.get("id"), "title": best.get("title"),
                        "created_at": best.get("created_at")}
    return out


def _http_get_json(url: str, token: str, timeout: int = 20) -> Dict[str, Any]:
    import urllib.request
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def collect(repo: str, base: str = "main", pattern: str = DEFAULT_PATTERN,
            include_remote: bool = False, conflict_check: bool = False,
            limit: Optional[int] = None,
            reports: Optional[Dict[int, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """扫一遍 refs，产出逐分支记录。纯 git 读操作，无网络。"""
    reports = reports or {}
    refs = list_refs(repo, pattern, include_remote)
    if limit:
        refs = refs[:limit]
    rows: List[Dict[str, Any]] = []
    for ref in refs:
        name = ref["name"]
        ahead = rev_count(repo, f"{base}..{name}")
        cls = CLASS_ANCHOR
        unique = equivalent = 0
        if ahead > 0:
            unique, equivalent = cherry_split(repo, base, name)
            cls = classify(ahead, unique)
        row: Dict[str, Any] = {
            "branch": name,
            "kind": ref["kind"],
            "sha": ref["sha"][:12],
            "tip_date": ref["date"],
            "month": (ref["date"] or "")[:7],
            "mission_id": parse_mission_id(name),
            "ahead": ahead,
            "behind": rev_count(repo, f"{name}..{base}"),
            "unique_commits": unique,
            "banked_commits": equivalent,
            "class": cls,
            "content_same_on_touched": False,
            "files": [],
            "diffstat": {"files": 0, "insertions": 0, "deletions": 0},
            "merge_conflict": None,
        }
        content_same = False
        if ahead > 0:
            mb = merge_base(repo, base, name)
            if mb:
                row["files"] = changed_files(repo, mb, name)
                row["diffstat"] = shortstat(repo, mb, name)
                content_same = content_identical(repo, base, name, row["files"])
                row["content_same_on_touched"] = content_same
                row["class"] = cls = classify(ahead, unique, content_same)
                if conflict_check:
                    row["merge_conflict"] = conflicts_with_base(repo, base, name)
        mid = row["mission_id"]
        rep = reports.get(mid) if mid is not None else None
        row["report_id"] = rep.get("id") if rep else None
        row["report_title"] = rep.get("title") if rep else None
        row["landing"] = landing_status(cls, bool(rep))
        rows.append(row)
    return {"schema_version": SCHEMA_VERSION, "base": base, "pattern": pattern,
            "include_remote": include_remote, "rows": rows}


# ── 汇总 / 聚类 ────────────────────────────────────────────────────────────────
def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def duplicate_clusters(rows: Sequence[Dict[str, Any]],
                       min_shared: int = CLUSTER_MIN_SHARED_FILES,
                       min_jaccard: float = CLUSTER_JACCARD) -> List[Dict[str, Any]]:
    """把「改同一批文件」的 UNIQUE 分支聚成一簇（并查集）；用于重复劳动预警。"""
    uniq = [r for r in rows if r["class"] == CLASS_UNIQUE and r["files"]]
    parent = list(range(len(uniq)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    edges: List[Tuple[int, int, int, float]] = []
    for i in range(len(uniq)):
        for j in range(i + 1, len(uniq)):
            fa, fb = set(uniq[i]["files"]), set(uniq[j]["files"])
            shared = len(fa & fb)
            jac = jaccard(fa, fb)
            if shared >= min_shared or jac >= min_jaccard:
                union(i, j)
                edges.append((i, j, shared, jac))
    groups: Dict[int, List[int]] = defaultdict(list)
    for i in range(len(uniq)):
        groups[find(i)].append(i)
    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        paths = Counter()
        for i in members:
            paths.update(set(uniq[i]["files"]))
        top = [p for p, _ in paths.most_common(8)]
        clusters.append({
            "branches": sorted(uniq[i]["branch"] for i in members),
            "missions": sorted({uniq[i]["mission_id"] for i in members if uniq[i]["mission_id"]}),
            "shared_paths": top,
            "max_shared_files": max(
                (len(set(uniq[a]["files"]) & set(uniq[b]["files"])) for a, b, _, _ in edges
                 if find(a) == find(members[0])), default=0),
        })
    clusters.sort(key=lambda c: (-len(c["branches"]), c["branches"][0]))
    return clusters


def summarize(collected: Dict[str, Any]) -> Dict[str, Any]:
    rows = collected["rows"]
    by_class = Counter(r["class"] for r in rows)
    by_landing = Counter(r["landing"] for r in rows)
    by_month: Dict[str, Dict[str, int]] = defaultdict(lambda: {c: 0 for c in CLASSES})
    for r in rows:
        by_month[r["month"]][r["class"]] += 1
    unique_rows = [r for r in rows if r["class"] == CLASS_UNIQUE]
    # 目录维度的口径是「有多少条 UNIQUE 分支触及该顶层目录」（按分支去重，不是按文件计数）
    dirs_branches: Dict[str, set] = defaultdict(set)
    dirs_files = Counter()
    for r in unique_rows:
        for p in r["files"]:
            d = p.split("/")[0] if "/" in p else "(root)"
            dirs_branches[d].add(r["branch"])
            dirs_files[d] += 1
    dirs = Counter({d: len(v) for d, v in dirs_branches.items()})
    total = len(rows)
    return {
        "total_refs": total,
        "by_class": {c: by_class.get(c, 0) for c in CLASSES},
        "by_landing": {k: by_landing.get(k, 0) for k in
                       (LANDING_REPORTED_UNMERGED, LANDING_UNREPORTED, LANDING_IN_BASE)},
        "by_month": {m: dict(v) for m, v in sorted(by_month.items())},
        "unique_commits_total": sum(r["unique_commits"] for r in rows),
        "banked_commits_total": sum(r["banked_commits"] for r in rows),
        "unlanded_branch_ratio": (len(unique_rows) / total) if total else 0.0,
        "unique_branches_with_report": sum(1 for r in unique_rows if r["report_id"]),
        "unique_branches_without_report": sum(1 for r in unique_rows if not r["report_id"]),
        "conflicting_with_base": sum(1 for r in rows if r["merge_conflict"] is True),
        "top_touched_dirs": [{"dir": d, "branches_touching": n,
                              "files_touching": dirs_files.get(d, 0)}
                             for d, n in dirs.most_common(15)],
    }


def build_ledger(collected: Dict[str, Any],
                 reports_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    stats = summarize(collected)
    clusters = duplicate_clusters(collected["rows"])
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base": collected["base"],
        "pattern": collected["pattern"],
        "include_remote": collected["include_remote"],
        "reports_coverage": reports_meta or {},
        "stats": stats,
        "duplicate_clusters": clusters,
        "rows": collected["rows"],
    }


# ── 输出 ───────────────────────────────────────────────────────────────────────
def render_markdown(ledger: Dict[str, Any], top_n: int = 25) -> str:
    s = ledger["stats"]
    rows = ledger["rows"]
    rc = ledger.get("reports_coverage") or {}
    uniq = sorted([r for r in rows if r["class"] == CLASS_UNIQUE],
                  key=lambda r: (-r["unique_commits"], r["branch"]))
    reu = [r for r in uniq if r["landing"] == LANDING_REPORTED_UNMERGED]
    unm = [r for r in uniq if r["landing"] == LANDING_UNREPORTED]
    lines = [
        "# 未落地分支台账（branch ledger）",
        "",
        f"- base：`{ledger['base']}`　pattern：`{ledger['pattern']}`　"
        f"扫描 ref 数：**{s['total_refs']}**（含远端：{'是' if ledger['include_remote'] else '否'}）",
        f"- 分类：ANCHOR（已在主干）**{s['by_class']['ANCHOR']}** / "
        f"BANKED（补丁等价已进主干）**{s['by_class']['BANKED']}** / "
        f"UNIQUE（真未落地）**{s['by_class']['UNIQUE']}**",
        f"- 未落地占比：**{s['unlanded_branch_ratio'] * 100:.1f}%**；"
        f"未落地提交合计 **{s['unique_commits_total']}** 条；补丁等价提交 **{s['banked_commits_total']}** 条",
        f"- 交叉核验：UNIQUE 且有报告（结论已公布、代码未落地）**{s['unique_branches_with_report']}** 条 / "
        f"UNIQUE 且无报告（无痕）**{s['unique_branches_without_report']}** 条",
        "",
        "## 开工前自检（后来者必读）",
        "",
        "1. 先在本表 `UNIQUE` 与 `REPORTED_UNMERGED` 两张清单里搜你要改的路径，命中即先合并在先成果；",
        "2. `BANKED` 分支**内容已在主干**（patch-id 命中），照它再干一遍没有增量；",
        f"3. `duplicate_clusters` 里 {len(ledger['duplicate_clusters'])} 簇分支改同一批文件，属重复劳动高风险面。",
        "",
        f"## REPORTED_UNMERGED（优先合并：{len(reu)} 条，按未落地提交数降序，前 {top_n}）",
        "",
        "| 分支 | mission | 未落地提交 | 报告 id | 改动文件 |",
        "|---|---|---|---|---|",
    ]
    for r in reu[:top_n]:
        lines.append(f"| `{r['branch']}` | {r['mission_id']} | {r['unique_commits']} | "
                     f"{r['report_id']} | {len(r['files'])} |")
    lines += ["", f"## UNREPORTED（有未落地提交但查不到报告：{len(unm)} 条，前 {top_n}）", "",
              "| 分支 | mission | 未落地提交 | 改动文件 | 顶端路径 |", "|---|---|---|---|---|"]
    for r in unm[:top_n]:
        head = ", ".join(f"`{p}`" for p in r["files"][:2])
        lines.append(f"| `{r['branch']}` | {r['mission_id']} | {r['unique_commits']} | "
                     f"{len(r['files'])} | {head} |")
    if ledger["duplicate_clusters"]:
        lines += ["", "## 重复劳动预警簇", "", "| 簇内分支数 | mission | 共改路径（示例） |", "|---|---|---|"]
        for c in ledger["duplicate_clusters"][:top_n]:
            lines.append(f"| {len(c['branches'])} | {', '.join(map(str, c['missions'][:8]))} | "
                         f"{', '.join('`' + p + '`' for p in c['shared_paths'][:3])} |")
    if s["top_touched_dirs"]:
        lines += ["", "## UNIQUE 分支触及的顶层目录（前 15）", "", "| 目录 | 分支数 |", "|---|---|"]
        for d in s["top_touched_dirs"]:
            lines.append(f"| `{d['dir']}` | {d['branches_touching']} |")
    if rc:
        cover = (f"- 报告核验覆盖：快照 {rc.get('snapshot_reports', 0)} 篇"
                 f"（{rc.get('snapshot_created_range', 'n/a')}）+ 按 mission 补探 "
                 f"{rc.get('probed_missions', 0)} 条 ⇒ 命中 {rc.get('matched_missions', 0)} 个 mission。"
                 " 未命中者一律记 `UNREPORTED`，含义是「在可获取窗口内查不到该 mission 的报告」，"
                 "**不是**「确认没有报告」。")
    else:
        cover = "- 本次未提供报告快照：`landing` 一律按「无报告」处理，不具核验意义。"
    lines += ["", "## 口径与局限", "", cover,
              "- 判等用 `git cherry`（patch-id）：squash 合并会改变 patch-id，故 squash 后的分支可能被"
              "误判为 `UNIQUE`——本表不宣称「UNIQUE 一定是新内容」，只宣称「主干无等价 patch-id 补丁」。",
              "- 只读：本工具不建/删/改任何 ref；`ANCHOR`/`BANKED` 仅供人工删枝参考，工具不执行删除。",
              "- `merge_conflict` 仅在 `--conflict-check` 下计算，且用旧式 `git merge-tree` 的冲突标记"
              "启发式判定，属保守估计。"]
    return "\n".join(lines) + "\n"


MAX_FILES_PER_ROW = 80


def slim_ledger(ledger: Dict[str, Any], max_files: int = MAX_FILES_PER_ROW) -> Dict[str, Any]:
    """输出前裁剪每行的改动文件清单（保留前 N 条 + 总数），防止 JSON 被 600 条分支撑到 MB 级。

    裁剪只影响人读明细，不影响任何统计量（``diffstat.files`` 与 ``len(files)`` 都在统计侧另记）。
    """
    out = dict(ledger)
    rows = []
    for r in ledger["rows"]:
        r2 = dict(r)
        files = r2.get("files") or []
        r2["files_total"] = len(files)
        r2["files"] = files[:max_files]
        r2["files_truncated"] = len(files) > max_files
        rows.append(r2)
    out["rows"] = rows
    out["max_files_per_row"] = max_files
    return out


def write_outputs(out_dir: str, ledger: Dict[str, Any],
                  max_files: int = MAX_FILES_PER_ROW) -> Dict[str, str]:
    ledger = slim_ledger(ledger, max_files)
    os.makedirs(out_dir, exist_ok=True)
    jpath = os.path.join(out_dir, "ledger.json")
    mpath = os.path.join(out_dir, "ledger.md")
    with open(jpath, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, ensure_ascii=False, indent=1, sort_keys=True)
    with open(mpath, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(ledger))
    return {"json": jpath, "markdown": mpath}


CJK_FONT_CANDIDATES: Tuple[str, ...] = (
    os.environ.get("TP_CJK_FONT", ""),
    "/workspace/PyTorch/Detection/YOLOv4/model_data/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
)


def _cjk_font_path() -> Optional[str]:
    """找一款能画中文的字体；找不到返回 None（图里改用英文标签，避免豆腐块）。"""
    for cand in CJK_FONT_CANDIDATES:
        if cand and os.path.exists(cand):
            return cand
    for root in ("/usr/share/fonts",):
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                low = f.lower()
                if low.endswith((".ttf", ".ttc", ".otf")) and any(
                        k in low for k in ("cjk", "hei", "song", "wqy", "han")):
                    return os.path.join(dirpath, f)
    return None


LABELS = {
    "zh": {
        "ylabel": "agent 分支数",
        "title_month": "agent 分支按月分类（base={base}，共 {n} 条）",
        "cls": {CLASS_ANCHOR: "ANCHOR (已并入主干)", CLASS_BANKED: "BANKED (补丁等价)",
                CLASS_CONTENT_SAME: "CONTENT_SAME (内容等价)", CLASS_UNIQUE: "UNIQUE (未落地)"},
        "xlabel_landing": "分支数",
        "title_landing": "成果落地状态（与平台报告交叉核验）",
        "xlabel_dirs": "触及该目录的 UNIQUE 分支数",
        "title_dirs": "未落地分支的改动面（重复劳动热区）",
    },
    "en": {
        "ylabel": "agent branches",
        "title_month": "agent branches by month (base={base}, n={n})",
        "cls": {CLASS_ANCHOR: "ANCHOR (merged)", CLASS_BANKED: "BANKED (patch-equivalent)",
                CLASS_CONTENT_SAME: "CONTENT_SAME (squashed)", CLASS_UNIQUE: "UNIQUE (unlanded)"},
        "xlabel_landing": "branches",
        "title_landing": "Landing status (cross-checked with platform reports)",
        "xlabel_dirs": "UNIQUE branches touching dir",
        "title_dirs": "Change surface of unlanded branches (duplication hot spots)",
    },
}


def generate_figures(ledger: Dict[str, Any], out_dir: str) -> List[str]:
    """三张图：按月分类堆叠、落地状态条形、UNIQUE 分支触及目录条形。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    cjk = _cjk_font_path()
    L = dict(LABELS["en"])
    if cjk:
        try:
            font_manager.fontManager.addfont(cjk)
            name = font_manager.FontProperties(fname=cjk).get_name()
            matplotlib.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            matplotlib.rcParams["axes.unicode_minus"] = False
            L = dict(LABELS["zh"])
        except Exception:
            cjk = None

    os.makedirs(out_dir, exist_ok=True)
    paths: List[str] = []
    s = ledger["stats"]

    months = sorted(s["by_month"])
    if months:
        fig, ax = plt.subplots(figsize=(9, 4.2))
        bottom = [0] * len(months)
        colors = {CLASS_ANCHOR: "#9aa7b4", CLASS_BANKED: "#f0b357",
                  CLASS_CONTENT_SAME: "#e2c044", CLASS_UNIQUE: "#d1495b"}
        for c in CLASSES:
            vals = [s["by_month"][m].get(c, 0) for m in months]
            ax.bar(months, vals, bottom=bottom, label=L["cls"][c], color=colors[c])
            bottom = [b + v for b, v in zip(bottom, vals)]
        ax.set_ylabel(L["ylabel"])
        ax.set_title(L["title_month"].format(base=ledger["base"], n=s["total_refs"]), fontsize=10)
        ax.legend(fontsize=8)
        ax.tick_params(axis="x", labelrotation=45, labelsize=8)
        fig.tight_layout()
        p = os.path.join(out_dir, "fig_branch_classes_by_month.png")
        fig.savefig(p, dpi=130)
        plt.close(fig)
        paths.append(p)

    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    keys = [LANDING_REPORTED_UNMERGED, LANDING_UNREPORTED, LANDING_IN_BASE]
    vals = [s["by_landing"][k] for k in keys]
    bars = ax.barh(keys, vals, color=["#d1495b", "#f0b357", "#9aa7b4"])
    span = max(vals) if max(vals) else 1
    for b, v in zip(bars, vals):
        ax.text(b.get_width() + span * 0.01 + 0.2, b.get_y() + b.get_height() / 2,
                str(v), va="center", fontsize=9)
    ax.set_xlabel(L["xlabel_landing"])
    ax.set_title(L["title_landing"], fontsize=10)
    fig.tight_layout()
    p = os.path.join(out_dir, "fig_landing_status.png")
    fig.savefig(p, dpi=130)
    plt.close(fig)
    paths.append(p)

    dirs = s["top_touched_dirs"][:12]
    if dirs:
        fig, ax = plt.subplots(figsize=(7.6, 4.2))
        names = [d["dir"] for d in dirs][::-1]
        vals = [d["branches_touching"] for d in dirs][::-1]
        ax.barh(names, vals, color="#2a6f97")
        ax.set_xlabel(L["xlabel_dirs"])
        ax.set_title(L["title_dirs"], fontsize=10)
        fig.tight_layout()
        p = os.path.join(out_dir, "fig_unique_touched_dirs.png")
        fig.savefig(p, dpi=130)
        plt.close(fig)
        paths.append(p)
    return paths


# ── CLI ────────────────────────────────────────────────────────────────────────
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="未落地分支台账：ANCHOR/BANKED/UNIQUE 分类 + 报告交叉核验")
    ap.add_argument("--repo", default=".", help="git 仓库路径（默认当前目录）")
    ap.add_argument("--base", default="main", help="基准分支（默认 main）")
    ap.add_argument("--pattern", default=DEFAULT_PATTERN, help="refs 匹配模式（默认 agent/*）")
    ap.add_argument("--include-remote", action="store_true", help="同时扫 origin/ 下的同名分支")
    ap.add_argument("--conflict-check", action="store_true", help="额外预测与 base 的合并冲突")
    ap.add_argument("--reports-json", default=None, help="报告快照 JSON（mission_id 交叉核验）")
    ap.add_argument("--out", default="artifacts/branch_ledger", help="输出目录")
    ap.add_argument("--figures", action="store_true", help="生成 matplotlib 图")
    ap.add_argument("--max-files", type=int, default=MAX_FILES_PER_ROW,
                    help="每行保留的改动文件数（默认 80，仅影响明细输出）")
    ap.add_argument("--limit", type=int, default=None, help="只扫前 N 条（调试用）")
    ap.add_argument("--probe-reports", action="store_true",
                    help="对快照未命中的 UNIQUE mission 逐个查 /tensor/reports?mission_id=X 补探")
    ap.add_argument("--api-base", default=os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1"),
                    help="平台 API 基址（--probe-reports 时用）")
    ap.add_argument("--token-env", default="TENSOR_TOKEN", help="存放 bearer token 的环境变量名")
    args = ap.parse_args(argv)

    reports = load_reports(args.reports_json)
    collected = collect(args.repo, base=args.base, pattern=args.pattern,
                        include_remote=args.include_remote,
                        conflict_check=args.conflict_check, limit=args.limit,
                        reports=reports)
    meta: Dict[str, Any] = {"snapshot_reports": len(reports)}
    if reports:
        dates = sorted(str(v.get("created_at") or "") for v in reports.values())
        meta["snapshot_created_range"] = f"{dates[0][:10]} ~ {dates[-1][:10]}"
    if args.probe_reports:
        pending = sorted({r["mission_id"] for r in collected["rows"]
                          if r["mission_id"] is not None and r["mission_id"] not in reports})
        tok = os.environ.get(args.token_env, "")
        probed = probe_reports_by_mission(pending, args.api_base, tok) if tok else {}
        reports = {**reports, **probed}
        meta["probed_missions"] = len(pending)
        meta["probed_hits"] = len(probed)
        meta["probe_skipped_no_token"] = (not tok)
        for r in collected["rows"]:
            mid = r["mission_id"]
            rep = reports.get(mid) if mid is not None else None
            r["report_id"] = rep.get("id") if rep else None
            r["report_title"] = rep.get("title") if rep else None
            r["landing"] = landing_status(r["class"], bool(rep))
    meta["matched_missions"] = sum(1 for r in collected["rows"]
                                   if r["mission_id"] in reports) if reports else 0
    ledger = build_ledger(collected, reports_meta=meta)
    paths = write_outputs(args.out, ledger, max_files=args.max_files)
    if args.figures:
        for p in generate_figures(ledger, args.out):
            paths[os.path.basename(p)] = p
    s = ledger["stats"]
    print(f"[branch_ledger] refs={s['total_refs']} ANCHOR={s['by_class']['ANCHOR']} "
          f"BANKED={s['by_class']['BANKED']} UNIQUE={s['by_class']['UNIQUE']} "
          f"REPORTED_UNMERGED={s['by_landing'][LANDING_REPORTED_UNMERGED]} "
          f"UNREPORTED={s['by_landing'][LANDING_UNREPORTED]}")
    for k, v in paths.items():
        print(f"  -> {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())