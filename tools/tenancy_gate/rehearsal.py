#!/usr/bin/env python3
"""推送前的三方合并预演门：裸仓 ``git merge-tree`` dry-run，冲突即 fail-closed。

为什么不试合并
--------------
真合并要写工作树/索引，40 并发 × 12 仓的租户工作树根本承受不起互相踩。``git
merge-tree --write-tree`` 只在对象库外算出一棵树并报告冲突，**不碰工作树、不碰索引、
不改任何 ref**——本模块只调用它，外加 ``rev-parse`` / ``merge-base`` / ``diff`` 这类
纯读命令。

状态机（只有三态，unknown 一律不放行）
------------------------------------
``clean``    merge-tree 退出码 0：可放行（``allowed=true``）。
``conflict`` 退出码 1：文件级冲突清单 + hunk 级冲突区间，``allowed=false``。
``unknown``  仓不可读 / ref 解析失败 / 无共同祖先 / 超时 / 非 0/1 退出码 / 预算耗尽
             ⇒ ``allowed=false`` 且 ``reason`` 说明原因（fail-closed，绝不"默认安全"）。

批量预演的性能取向
------------------
两两预演 N 个分支是 O(N²) 次判定，但**真正需要 merge-tree 的只有改到同一路径的
候选对**（git 按路径合并，路径不相交不可能冲突）。因此批量接口先用一次
``git diff --name-only base...branch`` 拿到每个分支的变更文件集合做预筛，
相交的候选对才真跑 merge-tree；预筛放行的对记 ``reason=prescreen_disjoint``，
证据（各自变更文件集合摘要）留在报告里，可被复核推翻。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

from .model import REHEARSAL_SCHEMA, with_digest

__all__ = [
    "GitUnavailable",
    "repo_probe",
    "changed_files",
    "rehearse_pair",
    "rehearse_pairs",
]

_BASE_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_CONFIG_NOSYSTEM": "1",
    "HOME": os.environ.get("HOME", "/tmp"),
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
}


class GitUnavailable(RuntimeError):
    """git 可执行文件缺失（整条门不可用 ⇒ 上层 fail-closed）。"""


def _git(repo: str, args, timeout: float):
    """执行只读 git 命令；返回 ``(rc, stdout, stderr)``。超时返回 rc=None。"""
    exe = shutil.which("git")
    if not exe:
        raise GitUnavailable("git_not_found")
    cmd = [exe, "-C", str(repo), "-c", "core.quotepath=false", *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_BASE_ENV,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, "", "timeout"
    return proc.returncode, proc.stdout, proc.stderr


def repo_probe(repo: str, timeout: float = 10.0) -> dict:
    """仓可读性探针：``git rev-parse --git-dir`` 成功即认为可预演。"""
    if not repo or not os.path.isdir(repo):
        return {"readable": False, "reason": "repo_missing"}
    try:
        rc, out, err = _git(repo, ["rev-parse", "--git-dir"], timeout)
    except GitUnavailable as exc:
        return {"readable": False, "reason": str(exc)}
    if rc != 0:
        return {"readable": False, "reason": "repo_not_a_git_dir", "stderr": err.strip()[:400]}
    return {"readable": True, "git_dir": out.strip()}


def resolve_ref(repo: str, ref: str, timeout: float = 10.0) -> str | None:
    rc, out, _err = _git(repo, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], timeout)
    if rc != 0 or not out.strip():
        return None
    return out.strip().splitlines()[0].strip()


def changed_files(repo: str, base: str, ref: str, timeout: float = 10.0) -> list[str] | None:
    """``base...ref`` 的变更文件集合（三方点：从共同祖先起算，与 PR 语义一致）。"""
    rc, out, _err = _git(repo, ["diff", "--name-only", "-z", "--no-renames", f"{base}...{ref}"], timeout)
    if rc != 0:
        return None
    return sorted({p for p in out.split("\0") if p})


def _parse_merge_tree(out: str) -> tuple[str, list[str], str]:
    """解析 ``--write-tree --name-only``（优先 ``-z --no-messages``，兼容旧 git）。"""
    if "\0" in out or out.endswith("\0"):
        parts = [p for p in out.split("\0")]
        tree = parts[0].strip() if parts else ""
        rest = parts[1:]
        files = []
        messages = []
        for chunk in rest:
            chunk = chunk.strip("\n")
            if not chunk:
                continue
            if chunk.startswith(("Auto-merging", "CONFLICT", "warning", "error")):
                messages.append(chunk)
            elif chunk.count("\n") >= 1 and len(chunk.splitlines()) > 1:
                messages.append(chunk)
            else:
                files.append(chunk)
        return tree, sorted(set(files)), "\n".join(messages)
    lines = out.splitlines()
    tree = lines[0].strip() if lines else ""
    files, messages, seen_blank = [], [], False
    for line in lines[1:]:
        if not line.strip():
            seen_blank = True
            continue
        if seen_blank:
            messages.append(line)
        else:
            files.append(line)
    return tree, sorted(set(files)), "\n".join(messages)


def _merge_tree(repo: str, base: str, ours: str, theirs: str, timeout: float):
    """返回 ``(rc, tree, files, messages, stderr)``。"""
    args = ["merge-tree", "--write-tree", "--name-only", "-z", "--no-messages",
            f"--merge-base={base}", ours, theirs]
    rc, out, err = _git(repo, args, timeout)
    if rc is not None and rc not in (0, 1):
        # 旧 git 不认识 --no-messages / -z 组合：退化到分节解析
        fallback = ["merge-tree", "--write-tree", "--name-only",
                    f"--merge-base={base}", ours, theirs]
        rc2, out2, err2 = _git(repo, fallback, timeout)
        if rc2 in (0, 1):
            tree, files, msgs = _parse_merge_tree(out2)
            return rc2, tree, files, msgs, err2
        return rc, "", [], "", err
    tree, files, msgs = _parse_merge_tree(out or "")
    return rc, tree, files, msgs, err


def _hunk_ranges(repo: str, base: str, ref: str, path: str, timeout: float) -> list[list[int]]:
    """单文件在 base 坐标下的变更区间 ``[[start, end], ...]``（``end<start`` 表示纯插入点）。"""
    rc, out, _err = _git(repo, ["diff", "-U0", "--no-color", "--no-renames", base, ref, "--", path], timeout)
    if rc != 0:
        return []
    ranges: list[list[int]] = []
    for line in out.splitlines():
        if not line.startswith("@@ "):
            continue
        head = line.split("@@")[1].strip()          # 形如 -12,3 +14,2
        old = head.split(" ")[0]
        body = old[1:]
        if "," in body:
            start_s, len_s = body.split(",", 1)
            start, length = int(start_s), int(len_s)
        else:
            start, length = int(body), 1
        if length == 0:
            ranges.append([start, start - 1])       # 纯插入点
        else:
            ranges.append([start, start + length - 1])
    return ranges


def _overlap(a: list[int], b: list[int]) -> list[int] | None:
    lo = max(a[0], b[0])
    hi = min(a[1], b[1])
    if lo <= hi:
        return [lo, hi]
    if a[1] < a[0] and b[0] <= a[0] <= b[1]:         # 插入点落在对方变更区间内
        return [a[0], a[0]]
    if b[1] < b[0] and a[0] <= b[0] <= a[1]:
        return [b[0], b[0]]
    return None


def rehearse_pair(repo: str, ours: str, theirs: str, config: dict, base: str | None = None) -> dict:
    """两分支三方合并 dry-run；返回三态报告（含 hunk 级冲突区间）。"""
    cfg = config["rehearsal"]
    timeout = float(cfg["timeout_seconds"])
    started = time.perf_counter()
    report = {
        "schema": REHEARSAL_SCHEMA,
        "repo": str(repo),
        "ours": str(ours),
        "theirs": str(theirs),
        "base": base or "",
        "state": "unknown",
        "allowed": False,
        "reason": "",
        "conflicted_paths": [],
        "hunks": [],
        "tree": "",
        "messages": "",
        "elapsed_ms": 0.0,
    }

    probe = repo_probe(repo, timeout)
    if not probe["readable"]:
        report["reason"] = probe["reason"]
        return _finish(report, started)
    ours_oid = resolve_ref(repo, ours, timeout)
    theirs_oid = resolve_ref(repo, theirs, timeout)
    report["ours_oid"], report["theirs_oid"] = ours_oid or "", theirs_oid or ""
    if not ours_oid or not theirs_oid:
        report["reason"] = "ref_unresolved"
        return _finish(report, started)

    if base:
        base_oid = resolve_ref(repo, base, timeout)
        if not base_oid:
            report["reason"] = "merge_base_unresolved"
            return _finish(report, started)
    else:
        rc, out, _err = _git(repo, ["merge-base", ours_oid, theirs_oid], timeout)
        base_oid = out.strip().splitlines()[0].strip() if rc == 0 and out.strip() else ""
        if not base_oid:
            report["reason"] = "no_merge_base"
            return _finish(report, started)
    report["base"] = base_oid

    rc, tree, files, messages, _stderr = _merge_tree(repo, base_oid, ours_oid, theirs_oid, timeout)
    report["tree"] = tree
    report["messages"] = messages[:2000]
    if rc is None:
        report["reason"] = "git_timeout"
        return _finish(report, started)
    if rc == 0:
        report["state"], report["allowed"], report["reason"] = "clean", True, "merge_clean"
        return _finish(report, started)
    if rc != 1:
        report["reason"] = f"merge_tree_failed_rc{rc}"
        return _finish(report, started)

    report["state"] = "conflict"
    report["allowed"] = not bool(cfg["require_clean"])
    report["reason"] = "merge_conflict"
    report["conflicted_paths"] = files
    if cfg["hunk_detail"]:
        hunks = []
        for path in files:
            ours_ranges = _hunk_ranges(repo, base_oid, ours_oid, path, timeout)
            theirs_ranges = _hunk_ranges(repo, base_oid, theirs_oid, path, timeout)
            overlaps = []
            for a in ours_ranges:
                for b in theirs_ranges:
                    hit = _overlap(a, b)
                    if hit:
                        overlaps.append(hit)
            overlaps = sorted({tuple(o) for o in overlaps})
            hunks.append(
                {
                    "path": path,
                    "ours": ours_ranges,
                    "theirs": theirs_ranges,
                    "overlap": [list(o) for o in overlaps],
                    "kind": "content" if overlaps else "path_level",
                }
            )
        report["hunks"] = hunks
    return _finish(report, started)


def _finish(report: dict, started: float) -> dict:
    report["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    body = dict(report)
    body.pop("elapsed_ms", None)      # 耗时是观测值，不进摘要 ⇒ 报告可复算
    out = dict(report)
    out["digest"] = with_digest(body)["digest"]
    return out


def rehearse_pairs(repo: str, pairs, config: dict, base: str | None = None) -> dict:
    """批量两两预演：``changed_files`` 预筛 + 预算护栏（超预算的候选对记 unknown）。"""
    cfg = config["rehearsal"]
    timeout = float(cfg["timeout_seconds"])
    total_budget = float(cfg["total_budget_seconds"])
    max_calls = int(cfg["max_merge_tree_calls"])
    detail_limit = int(cfg.get("detail_limit", 2000))
    started = time.perf_counter()

    probe = repo_probe(repo, timeout)
    pair_list = [(str(a), str(b)) for a, b in pairs]
    if not probe["readable"]:
        return _finish_batch(
            {
                "schema": REHEARSAL_SCHEMA + "/batch",
                "repo": str(repo),
                "state": "unknown",
                "allowed": False,
                "reason": probe["reason"],
                "pairs_total": len(pair_list),
                "prescreen_clean": 0,
                "rehearsed": 0,
                "conflict": 0,
                "unknown": 0,
                "results": [],
            },
            started,
        )

    cache: dict[str, list[str] | None] = {}

    def files_of(ref: str):
        if ref not in cache:
            base_ref = base or "HEAD"
            cache[ref] = changed_files(repo, base_ref, ref, timeout)
        return cache[ref]

    results = []
    prescreen_clean = conflict_n = unknown_n = rehearsed = 0
    for ours, theirs in pair_list:
        if cfg["prescreen"]:
            fa, fb = files_of(ours), files_of(theirs)
            if fa is not None and fb is not None and not (set(fa) & set(fb)):
                prescreen_clean += 1
                results.append(
                    {
                        "ours": ours,
                        "theirs": theirs,
                        "state": "clean",
                        "allowed": True,
                        "reason": "prescreen_disjoint",
                        "files_ours": len(fa),
                        "files_theirs": len(fb),
                        "conflicted_paths": [],
                    }
                )
                continue
        over_budget = (
            rehearsed >= max_calls
            or (time.perf_counter() - started) > total_budget
        )
        if over_budget:
            unknown_n += 1
            results.append(
                {
                    "ours": ours,
                    "theirs": theirs,
                    "state": "unknown",
                    "allowed": False,
                    "reason": "rehearsal_budget_exhausted",
                    "conflicted_paths": [],
                }
            )
            continue
        res = rehearse_pair(repo, ours, theirs, config, base=base)
        rehearsed += 1
        if res["state"] == "clean":
            pass
        elif res["state"] == "conflict":
            conflict_n += 1
        else:
            unknown_n += 1
        results.append(res)

    detail = results
    omitted = 0
    if detail_limit >= 0 and len(results) > detail_limit:
        # 明细优先留"非干净预筛"的对（决策相关），其余干净预筛对只按上限留样、只计数
        interesting = [r for r in results if r.get("reason") != "prescreen_disjoint"][:detail_limit]
        clean_cap = max(0, detail_limit - len(interesting))
        clean = [r for r in results if r.get("reason") == "prescreen_disjoint"][:clean_cap]
        keep = {id(r) for r in interesting} | {id(r) for r in clean}
        detail = [r for r in results if id(r) in keep]
        omitted = len(results) - len(detail)

    allowed = unknown_n == 0 and (conflict_n == 0 or not cfg["require_clean"])
    state = "clean"
    if conflict_n:
        state = "conflict"
    if unknown_n:
        state = "unknown"
    return _finish_batch(
        {
            "schema": REHEARSAL_SCHEMA + "/batch",
            "repo": str(repo),
            "state": state,
            "allowed": allowed,
            "reason": "ok" if allowed else ("unknown_present" if unknown_n else "conflict_present"),
            "pairs_total": len(pair_list),
            "prescreen_clean": prescreen_clean,
            "rehearsed": rehearsed,
            "conflict": conflict_n,
            "unknown": unknown_n,
            "merge_tree_calls": rehearsed,
            "results_total": len(results),
            "results_omitted": omitted,
            "results": detail,
        },
        started,
    )


def _finish_batch(body: dict, started: float) -> dict:
    out = dict(body)
    out["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    digest_body = {k: v for k, v in out.items() if k != "elapsed_ms"}
    out["digest"] = with_digest(digest_body)["digest"]
    return out