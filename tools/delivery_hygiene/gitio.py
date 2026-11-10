"""git 读写原语：读操作无副作用；写操作全部集中在 ``archive_and_close`` 且必须显式启用。

设计约束
--------
1. **只读函数不写任何东西**：``run_git`` 只跑查询类子命令，失败返回 ``(rc, "")`` 不抛异常
   （分支可能在本进程扫描期间被别的 worker 删掉——并发环境必须容忍）。
2. **写操作单点收口**：唯一的写路径 ``archive_and_close``：
   ``rev-parse`` 取 sha → ``update-ref refs/attic/<date>/<branch> <sha>`` → **回读校验** →
   ``update-ref -d refs/heads/<branch>``。任一步失败立即返回 ``ok=False`` 并保留已建归档 ref
   （宁多留一个归档 ref，不可丢内容）。
3. **可回滚**：归档 ref 保留完整分支名与 sha，回滚就是
   ``git update-ref refs/heads/<branch> <sha>``；manifest 里记了 sha。
4. **不动 .git 配置、不 push、不改写历史**（无 ``gc``/``prune``/``filter``/``reset``）。
"""
from __future__ import annotations

import os
import subprocess
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

GIT_TIMEOUT = 180
PATCH_ID_CHUNK = 150          # 单次 patch-id 批处理的提交数（防 argv/内存爆）


def run_git(repo: str, *args: str, timeout: int = GIT_TIMEOUT) -> Tuple[int, str]:
    """跑一条 git 子命令，返回 ``(rc, stdout)``；异常/超时折算成 rc=127。"""
    try:
        p = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                           text=True, errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return p.returncode, p.stdout


def rev_parse(repo: str, ref: str) -> Optional[str]:
    """解析 ref → 完整 sha；不存在返回 None。"""
    rc, out = run_git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if rc != 0 or not out.strip():
        return None
    return out.strip().splitlines()[0]


def rev_count(repo: str, rng: str) -> int:
    rc, out = run_git(repo, "rev-list", "--count", rng)
    if rc != 0:
        return 0
    try:
        return int(out.strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0


def is_ancestor(repo: str, maybe_ancestor: str, of_ref: str) -> bool:
    """``maybe_ancestor`` 是否为 ``of_ref`` 的祖先（tip 已在主干历史内）。"""
    rc, _ = run_git(repo, "merge-base", "--is-ancestor", maybe_ancestor, of_ref)
    return rc == 0


def merge_base(repo: str, a: str, b: str) -> Optional[str]:
    rc, out = run_git(repo, "merge-base", a, b)
    if rc != 0 or not out.strip():
        return None
    return out.strip().splitlines()[0]


def tip_date(repo: str, ref: str) -> str:
    rc, out = run_git(repo, "log", "-1", "--format=%cI", ref)
    return out.strip() if rc == 0 else ""


def cherry_split(repo: str, base: str, ref: str) -> Tuple[List[str], List[str]]:
    """``git cherry``：返回 ``(unique_shas, equivalent_shas)``。

    ``+`` = 该提交的 patch-id 在 base 里查不到（真独有）；``-`` = patch-id 已命中 base。
    这是 A 类的**唯一**判据：只看 ``rev-list --count`` 会把「rebase/cherry-pick 后的空壳」误判成未落地。
    """
    rc, out = run_git(repo, "cherry", base, ref)
    if rc != 0:
        return [], []
    uniq: List[str] = []
    equiv: List[str] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        if parts[0] == "+":
            uniq.append(parts[1])
        elif parts[0] == "-":
            equiv.append(parts[1])
    return uniq, equiv


def net_diff_empty(repo: str, base: str, ref: str) -> Optional[bool]:
    """``git diff base ref`` 是否为空（提交后又回滚 ⇒ 净内容一致）。None=无法判定。"""
    rc, _ = run_git(repo, "diff", "--quiet", base, ref)
    if rc == 0:
        return True
    if rc == 1:
        return False
    return None


def changed_files(repo: str, from_ref: str, to_ref: str, limit: int = 200) -> List[str]:
    rc, out = run_git(repo, "diff", "--name-only", from_ref, to_ref)
    if rc != 0:
        return []
    return [ln for ln in out.splitlines() if ln][:limit]


def list_refs(repo: str, pattern: str, include_remote: bool = False,
              remote: str = "origin") -> List[Dict[str, str]]:
    """列出匹配 ``pattern`` 的分支 ref（本地在前；远端需显式开启）。"""
    fmt = "%(refname:short)%09%(objectname)%09%(committerdate:iso-strict)"
    refs: List[Dict[str, str]] = []
    rc, out = run_git(repo, "for-each-ref", f"--format={fmt}", "refs/heads/" + pattern)
    if rc == 0:
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                refs.append({"name": parts[0], "sha": parts[1], "date": parts[2], "kind": "local"})
    if include_remote:
        rc, out = run_git(repo, "for-each-ref", f"--format={fmt}",
                          f"refs/remotes/{remote}/" + pattern)
        if rc == 0:
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) == 3 and not parts[0].endswith("/HEAD"):
                    refs.append({"name": parts[0], "sha": parts[1], "date": parts[2],
                                 "kind": "remote"})
    refs.sort(key=lambda r: r["name"])
    return refs


def _chunks(items: Sequence[str], n: int) -> Iterable[Sequence[str]]:
    for i in range(0, len(items), n):
        yield items[i:i + n]


def patch_id_map(repo: str, commits: Sequence[str]) -> Dict[str, str]:
    """提交 sha → patch-id（``--stable``）。用于跨分支的补丁集合包含判定（B 类）。

    为什么必须真算 patch-id：B 类判据是「本分支的独有补丁集合 ⊆ 另一分支的独有补丁集合」，
    而同一处改动的提交 sha 在不同分支上必然不同（rebase / cherry-pick），只有 patch-id 可比。
    合并提交按 ``git cherry`` 的口径排除（``--no-merges``）。
    """
    out: Dict[str, str] = {}
    commits = [c for c in commits if c]
    for chunk in _chunks(commits, PATCH_ID_CHUNK):
        try:
            p1 = subprocess.run(
                ["git", "-C", repo, "log", "--no-walk=unsorted", "--no-merges", "-p",
                 "--format=commit %H", *chunk],
                capture_output=True, timeout=GIT_TIMEOUT)
            if p1.returncode != 0 or not p1.stdout:
                continue
            # 全字节管道：git patch-id 只吃 diff 字节流，解码与否无关；历史里混入非 utf-8
            # 内容时（实测 11 MB 处就有 0xe1）text=True 会在解码处崩掉整次扫描
            p2 = subprocess.run(["git", "-C", repo, "patch-id", "--stable"],
                                input=p1.stdout, capture_output=True,
                                timeout=GIT_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if p2.returncode != 0:
            continue
        for line in p2.stdout.decode("utf-8", "replace").splitlines():
            parts = line.split()
            if len(parts) >= 2:
                out[parts[1]] = parts[0]
    return out


def resolve_short(repo: str, short_shas: Sequence[str]) -> Dict[str, str]:
    """短 sha → 完整 sha 的批量解析（``git cherry`` 只给缩写）。"""
    out: Dict[str, str] = {}
    for chunk in _chunks([s for s in short_shas if s], PATCH_ID_CHUNK):
        rc, res = run_git(repo, "rev-parse", *chunk)
        if rc != 0:
            continue
        full = [ln.strip() for ln in res.splitlines() if ln.strip()]
        if len(full) == len(chunk):
            for s, f in zip(chunk, full):
                out[s] = f
    return out


# ── 写路径（单点收口） ─────────────────────────────────────────────────────────
def attic_ref_name(branch: str, date_tag: str, prefix: str = "refs/attic") -> str:
    """归档 ref 名：``refs/attic/<date>/<branch>``（保留完整分支路径，便于原样回滚）。"""
    return f"{prefix}/{date_tag}/{branch}"


def ref_exists(repo: str, ref: str) -> Optional[str]:
    return rev_parse(repo, ref)


def is_checked_out(repo: str, branch: str) -> bool:
    rc, out = run_git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    return rc == 0 and out.strip() == branch


def archive_and_close(repo: str, branch: str, sha: str, date_tag: str,
                      prefix: str = "refs/attic",
                      apply: bool = False) -> Dict[str, object]:
    """归档并关闭一条分支。``apply=False`` 时只算计划，不碰任何 ref。

    步骤（每一步都回读校验）：建归档 ref → 确认归档 ref sha == 原 sha → 删分支 ref。
    """
    target = attic_ref_name(branch, date_tag, prefix)
    plan: Dict[str, object] = {
        "branch": branch,
        "sha": sha,
        "archive_ref": target,
        "delete_ref": f"refs/heads/{branch}",
        "applied": False,
        "ok": False,
    }
    if not apply:
        plan["ok"] = True
        plan["dry_run"] = True
        return plan

    if is_checked_out(repo, branch):
        plan["error"] = "branch-checked-out"
        return plan
    existing = ref_exists(repo, target)
    if existing is not None and existing != sha:
        plan["error"] = "attic-ref-conflict"
        plan["attic_existing_sha"] = existing
        return plan
    rc, _ = run_git(repo, "update-ref", target, sha)
    if rc != 0:
        plan["error"] = "attic-update-ref-failed"
        return plan
    if ref_exists(repo, target) != sha:
        plan["error"] = "attic-verify-failed"
        return plan
    rc, _ = run_git(repo, "update-ref", "-d", f"refs/heads/{branch}")
    if rc != 0:
        plan["error"] = "delete-ref-failed"
        return plan
    plan["applied"] = True
    plan["ok"] = True
    return plan


def repo_label(repo: str) -> str:
    """仓库标签：路径 basename 去掉 ``.git``（纯交付元数据，不含领域语义）。"""
    base = os.path.basename(os.path.abspath(repo).rstrip(os.sep))
    return base[:-4] if base.endswith(".git") else base