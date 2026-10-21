#!/usr/bin/env python3
"""PR 卫生：推送前并 main + 存量 dirty PR 保鲜（2026-10-21 owner 报障收口）。

事故形态：agent 分支基于陈旧克隆 main（TensorPhysics 克隆曾停在 e90c4cc
而 main 已到 f2962d5f）→ owner 批量合并后 PR 大面积 dirty 合不了。
白名单机械解（只解 append-only 台账/库代码冲突，未知冲突跳过留人工）：
  benchmarks/pending/README.md、operator_log/*.md = 双侧行并集（append-only）；
  operator_log/*.py、benchmarks/pending/*/gates_verified.json = 取分支侧
    （本 PR 交付物；差异留在 PR diff 供 owner 裁）；
  tools/agent/auto_approver.py = 取 main 版 + ours 独有纯函数嫁接，
    py_compile + 四锚点（12288/180/branch_diff_block/machine_json_values）。

用法（si_harvest / benchmark_dispatch 推送前调）：
  from pr_hygiene import merge_main_before_push
  ok, how = merge_main_before_push(wt, repo)
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import time
import urllib.request

CONFLICT_BALLAST = ("<<<<<<<", "=======", ">>>>>>>")


def sh(cmd: str, cwd: str, timeout: int = 600):
    return subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                          text=True, timeout=timeout)


def _keep(lines: list[str], out: list[str]) -> None:
    for x in lines:
        if x not in out:
            out.append(x)


def union_resolve_text(text: str) -> str:
    """冲突块双侧行并集（保序去重，append-only 台账语义）。纯函数。"""
    out: list[str] = []
    ours: list[str] = []
    theirs: list[str] = []
    mode = None
    for ln in text.splitlines(keepends=True):
        if ln.startswith("<<<<<<<"):
            mode = "ours"
            continue
        if ln.startswith("=======") and mode == "ours":
            mode = "theirs"
            continue
        if ln.startswith(">>>>>>>") and mode == "theirs":
            _keep(ours, out)
            _keep(theirs, out)
            ours, theirs, mode = [], [], None
            continue
        if mode == "ours":
            ours.append(ln)
        elif mode == "theirs":
            theirs.append(ln)
        else:
            out.append(ln)
    return "".join(out)


def unresolved(wt: str) -> list[str]:
    st = sh("git status --porcelain", wt).stdout
    return [l.split()[-1] for l in st.splitlines() if l[:2] in
            ("UU", "AA", "DU", "UD", "AU", "UA", "DD")]


def graft_approver(wt: str, p: str) -> str:
    """auto_approver 冲突：main 版为基，ours 独有 repo_pythonpath 嫁接。"""
    sh(f'git checkout --theirs "{p}"', wt)
    src = open(os.path.join(wt, p), encoding="utf-8").read()
    if "repo_pythonpath" in src:
        sh(f'git add "{p}"', wt)
        return "approver:main 已含 repo_pythonpath"
    ours = sh(f"git show HEAD:{p}", wt).stdout
    if "def repo_pythonpath(" not in ours:
        sh(f'git add "{p}"', wt)
        return "approver:纯取 main"
    i = ours.index("def repo_pythonpath(")
    j = ours.index("\n\n\ndef ", i)
    fn = ours[i:j]
    anchor = "\ndef checks_capacity("
    if anchor not in src:
        sh(f'git add "{p}"', wt)
        return "approver:锚缺失纯取 main（保持可合并，功能以 main 为准）"
    src = src.replace(anchor, "\n\n" + fn + anchor, 1)
    open(os.path.join(wt, p), "w", encoding="utf-8").write(src)
    c = sh(f'python3 -m py_compile "{p}"', wt)
    if c.returncode != 0:
        sh(f'git checkout --theirs "{p}"', wt)
        sh(f'git add "{p}"', wt)
        return "approver:嫁接编译失败回退纯 main"
    for probe in ('"max_tokens": 12288', "timeout=180", "branch_diff_block",
                  "machine_json_values"):
        if probe not in src:
            sh(f'git checkout --theirs "{p}"', wt)
            sh(f'git add "{p}"', wt)
            return f"approver:锚点缺失{probe!r}回退纯 main"
    sh(f'git add "{p}"', wt)
    return "approver:main+repo_pythonpath 嫁接"


def resolve_conflicts(wt: str) -> tuple[bool, str]:
    """白名单机械解；返回 (全部解掉?, 说明)。未知冲突文件→False。"""
    notes = []
    for f in unresolved(wt):
        if f == "benchmarks/pending/README.md" or (
                f.startswith("operator_log/") and f.endswith(".md")):
            fp = os.path.join(wt, f)
            raw = open(fp, encoding="utf-8").read()  # 先读后写：截断式 open 的求值顺序坑
            open(fp, "w", encoding="utf-8").write(union_resolve_text(raw))
            sh(f'git add "{f}"', wt)
            notes.append(f"{f} 并集")
        elif f.startswith("operator_log/") and f.endswith(".py"):
            # 班次校验脚本：双侧各自重写（docstring 都会撞），并集必产坏语法
            # —— 取分支侧（PR 本身交付物），差异留在 PR diff 供 owner 裁
            sh(f'git checkout --ours "{f}" && git add "{f}"', wt)
            notes.append(f"{f} 取分支侧")
        elif f.startswith("benchmarks/") and f.endswith("/gates_verified.json"):
            # 案例机器工件：main 侧来自他案/旧案，分支侧是本 PR 交付——取分支侧
            sh(f'git checkout --ours "{f}" && git add "{f}"', wt)
            notes.append(f"{f} 取分支侧")
        elif f.endswith("tools/agent/auto_approver.py"):
            notes.append(graft_approver(wt, f))
        else:
            return False, f"未知冲突文件 {f}"
    if unresolved(wt):
        return False, "仍有未解冲突"
    return True, "; ".join(notes) or "clean"


def fetch_fresh(wt: str, tries: int = 3, sleep: int = 15) -> bool:
    """fetch origin main（443 闪断重试）。新鲜度校验由调用方对 API 做。"""
    for _ in range(tries):
        try:
            if sh("git fetch -q origin main", wt, 180).returncode == 0:
                return True
        except subprocess.TimeoutExpired:  # fetch 卡死：算一次失败，进退避
            pass
        time.sleep(sleep)
    return False


def merge_main_before_push(wt: str, note: str = "pr_hygiene") -> tuple[bool, str]:
    """推送前并 main：干净/白名单可解 → merge commit（True）；否则不动分支。

    返回 (继续推送?, 说明)。False 时调用方应跳过本轮推送（分支不动，
    下轮重试或留人工）——绝不能把带冲突标记的分支推上去。
    """
    if sh("git rev-parse --is-inside-work-tree", wt).returncode != 0:
        return False, "非 git 工作树"
    sh("git merge --abort", wt)
    # 代理残留未提交改动会挡 merge → 暂存（保留在 stash，不丢东西）
    if sh("git status --porcelain", wt).stdout.strip():
        sh("git stash -u -m pr-hygiene-pre-merge", wt)
    if not fetch_fresh(wt):
        return False, "fetch 失败（443？）——跳过本轮，分支未动"
    r = sh("git merge --no-commit --no-ff origin/main", wt)
    if sh("git rev-parse -q --verify MERGE_HEAD", wt).returncode != 0:
        sh("git merge --abort", wt)
        return False, f"merge 未启动（rc={r.returncode}）{r.stderr.strip()[-80:]}"
    if unresolved(wt):
        ok, how = resolve_conflicts(wt)
        if not ok:
            sh("git merge --abort", wt)
            return False, how
    else:
        how = "clean"
    c = sh('git commit --no-edit -m "merge: 并入 main（' + note + '）"', wt)
    if c.returncode != 0:
        sh("git merge --abort", wt)
        return False, "merge commit 失败"
    return True, how


def gh(path: str, method: str = "GET", tok: str = "",
       data: bytes | None = None) -> dict:
    req = urllib.request.Request(
        "https://api.github.com" + path, method=method,
        data=data.encode() if isinstance(data, str) else data,
        headers={"Authorization": f"token {tok}",
                 "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"{}")


def gh_token() -> str:
    for p in (os.environ.get("GH_TOKEN_FILE", ""), "/tmp/gh_token"):
        if p and os.path.exists(p):
            return open(p).read().strip()
    return ""


def push_branch_api(repo: str, branch: str, wt: str, local_sha: str,
                    local_base: str, main_sha: str | None = None,
                    note: str = "pr_hygiene") -> tuple[bool, str]:
    """API 通道两亲本推送（github.com:443 断时的正确兜底）。

    单亲 squash（api_push 式）树对但祖先不含 main → GitHub 按远古
    merge-base 重新判 dirty（2026-10-21 m139 实测 behind 13）。本函数产
    parents=[remote_head, main_sha]（main_sha 缺省单亲=普通快推）。
    local_base：本地在场的、树 == 远端头树的提交（squash 造的远端提交
    本地无对象，diff 必须打在 local_base 上）。
    """
    try:
        tok = gh_token()
        cur = gh(f"/repos/cxs503/{repo}/git/ref/heads/{branch}", tok=tok)[
            "object"]["sha"]
        parents = [cur] + ([main_sha] if main_sha else [])
        r = sh(f"git diff --no-renames --name-status {local_base} {local_sha}",
               wt)
        entries = []
        for ln in r.stdout.splitlines():
            if not ln.strip():
                continue
            parts = ln.split("\t")
            st, path = parts[0], parts[-1]
            if st not in ("A", "M", "D"):
                return False, f"异常 diff 态 {st}: {path}"
            if st == "D":
                entries.append({"path": path, "mode": "100644",
                                "type": "blob", "sha": None})
            else:
                b64 = base64.b64encode(
                    open(os.path.join(wt, path), "rb").read()).decode()
                sha = gh("/repos/cxs503/" + repo + "/git/blobs", method="POST",
                         tok=tok, data=json.dumps(
                             {"content": b64, "encoding": "base64"}))["sha"]
                entries.append({"path": path, "mode": "100644",
                                "type": "blob", "sha": sha})
        if not entries:
            return True, "diff 空=远端已同"
        base_tree = gh(f"/repos/cxs503/{repo}/git/commits/{cur}",
                       tok=tok)["tree"]["sha"]
        tree = gh("/repos/cxs503/" + repo + "/git/trees", method="POST",
                  tok=tok, data=json.dumps(
                      {"base_tree": base_tree, "tree": entries}))["sha"]
        c = gh("/repos/cxs503/" + repo + "/git/commits", method="POST",
               tok=tok, data=json.dumps({
                   "message": f"push: {branch}（{note}）",
                   "tree": tree, "parents": parents}))
        req = urllib.request.Request(
            "https://api.github.com/repos/cxs503/" + repo +
            "/git/refs/heads/" + branch.replace("/", "%2F"),
            method="PATCH",
            data=json.dumps({"sha": c["sha"], "force": False}).encode(),
            headers={"Authorization": f"token {tok}",
                     "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=60):
            pass
        return True, c["sha"][:8]
    except Exception as e:
        return False, type(e).__name__


def keep_open_prs(prs: list[tuple[str, int, str | None]]) -> list[str]:
    """存量 PR 保鲜扫描：dirty → GitHub update-branch（api 通道，443 免疫）。

    prs: [(repo, pr_no, worktree_path_or_None)]；返回日志行。
    update-branch 只在服务端可自动合并时成功；dirty（真冲突）会 422——
    工作树在场的走本地 merge_main_before_push + 调用方自推，否则记人工。
    """
    tok = gh_token()
    logs = []
    for repo, no, wt in prs:
        try:
            d = gh(f"/repos/cxs503/{repo}/pulls/{no}", tok=tok)
        except Exception as e:
            logs.append(f"{repo}#{no} 查询失败 {type(e).__name__}")
            continue
        state = d.get("mergeable_state")
        if state != "dirty":
            continue
        try:
            gh(f"/repos/cxs503/{repo}/pulls/{no}/update-branch",
               method="PUT", tok=tok)
            logs.append(f"{repo}#{no} update-branch 已请求")
        except urllib.error.HTTPError as e:
            logs.append(f"{repo}#{no} 服务端不可自动合并"
                        f"（{e.code}）{'工作树在 ' + wt if wt else '无工作树，留人工'}")
    return logs
