"""基线对齐：节点是否基于 main 基线、未合并分支的接口契约要求 (零领域语义)。

判据 I4 的机器实现。对每个节点：

1. 若 ``nodes.json`` 声明了 ``worktree``，就读该兄弟 worktree 的 HEAD/branch（只读），
   这样即使节点还没建本地分支也能判定；
2. ``base_commit``/``branch`` → ``git merge-base`` 分叉点、``main`` 漂移提交数、
   领先 main 的提交数、``merged_into_main``；
3. 归类 ``MERGED``（已并入 main）/``BRANCH_ONLY``（未合并）/``EXTERNAL_REPO``（别的仓）/
   ``UNKNOWN``（commit 不可见，fail-closed）；
4. 非 MERGED 一律要求 ``interface_contract``（导出符号 + 端口/单位 + 稳定性承诺）。
   契约可以由节点自己声明，也可以由本门"探针派生"（此时记 ``derived=True`` +
   被测模块 sha256，明确它是对现状的观测而非承诺）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .provenance import git_facts, worktree_head


def align_node(node: dict[str, Any], attestation: dict[str, Any] | None,
               repo_root: str, main: str = "main",
               self_repo: str | None = None,
               workspace_root: str | Path | None = None) -> dict[str, Any]:
    ext = node["ext_id"]
    repo = node.get("repo", "")
    att_node = (attestation or {}).get("node") or attestation or {}
    branch = att_node.get("branch") or node.get("branch")
    base_commit = att_node.get("base_commit") or node.get("base_commit")
    same_repo = (self_repo is None) or (repo == self_repo)

    wt: dict[str, Any] = {}
    if node.get("worktree") and workspace_root:
        wt = worktree_head(Path(workspace_root) / node["worktree"])
        if wt.get("branch") and not branch:
            branch = wt["branch"]
        if wt.get("head") and not base_commit:
            base_commit = wt["head"]

    if not same_repo:
        # 别的仓：到该节点的 worktree 里跑同一套只读 git 事实（如果有 worktree）
        wt_path = Path(wt["path"]) if wt else None
        if wt_path and wt_path.is_dir() and wt_path != Path(repo_root).resolve():
            facts = git_facts(wt_path, base_commit, "main", branch)
            if facts.get("known"):
                state = "MERGED" if facts["merged_into_main"] else "BRANCH_ONLY"
                facts["checked_in"] = "node_worktree"
            else:
                state = "EXTERNAL_REPO"
        else:
            state = "EXTERNAL_REPO"
            facts = {"known": False,
                     "error": f"repo {repo!r} is not the current repository"}
    else:
        facts = git_facts(repo_root, base_commit, main, branch)
        if not facts.get("known"):
            state = "UNKNOWN"
        else:
            state = "MERGED" if facts["merged_into_main"] else "BRANCH_ONLY"

    contract = node.get("interface_contract") or (attestation or {}).get("interface_contract")
    contract_required = state in ("BRANCH_ONLY", "EXTERNAL_REPO", "UNKNOWN")
    if facts.get("checked_in") == "node_worktree":
        facts["repo"] = repo
    contract_ok = bool(contract) if contract_required else True
    return {
        "node_ext_id": ext,
        "repo": repo,
        "branch": branch,
        "base_commit": base_commit,
        "worktree": node.get("worktree"),
        "worktree_probe": wt or None,
        "state": state,
        "facts": facts,
        "contract_required": contract_required,
        "contract_present": bool(contract),
        "contract": contract,
        "aligned": contract_ok,
        "note": _note(state, contract_required, contract_ok, facts),
    }


def _note(state: str, required: bool, ok: bool, facts: dict[str, Any]) -> str:
    if state == "MERGED":
        return "已并入 main，可从基线复现"
    if not required:
        return ""
    drift = facts.get("drift_on_main")
    ahead = facts.get("ahead_of_main")
    extra = ""
    if drift is not None and ahead is not None:
        extra = f"（领先 main {ahead} 个提交；fork 后 main 前进 {drift} 个提交）"
    if ok:
        return f"{state}: 未并入 main，接口契约已声明{extra}"
    return f"{state}: 未并入 main 且**未声明接口契约**{extra}（阻塞下一波集成）"


def align_all(nodes: list[dict[str, Any]],
              attestations: list[dict[str, Any]],
              repo_root: str, main: str = "main",
              self_repo: str | None = None,
              workspace_root: str | Path | None = None) -> list[dict[str, Any]]:
    def _ext(a: dict[str, Any]) -> str:
        return (a.get("node") or {}).get("ext_id") or a.get("node_ext_id") or ""

    by_ext = {_ext(a): a for a in attestations}
    return [align_node(n, by_ext.get(n["ext_id"]), repo_root, main, self_repo, workspace_root)
            for n in nodes]