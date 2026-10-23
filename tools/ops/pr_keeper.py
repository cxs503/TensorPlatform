#!/usr/bin/env python3
"""存量 PR 保鲜：dirty PR 自动并 main（白名单机械解）+ 两亲本 API 推送。

背景：推送前门禁（PR #52）只保新推；main 前进仍会把存量 PR 撞脏
（2026-10-22 PR#50 被 #47/#55 二次撞脏=owner 第二次手撞）。
cron */20 扫五库 open PR：dirty 且本地有对应 agent 工作树 →
merge_main_before_push → push_branch_api（两亲本，祖先含 main）。
无工作树/白名单外/树不一致 → 记日志留人工，绝不动分支。

运行：python3 tools/ops/pr_keeper.py（日志 agent_work/pr_keeper.log）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pr_hygiene as ph  # noqa: E402

REPOS = ["TensorPlatform", "TensorPhysics", "TensorLBM", "TensorDEM",
         "TensorFEM"]
ECO = Path("/srv/tensor/shared/tensor_eco/agent_work")


def find_worktree(repo: str, ref: str) -> str | None:
    for d in sorted(ECO.glob(f"*/{repo}")):
        if not (d / ".git").exists():
            continue
        r = ph.sh("git rev-parse --abbrev-ref HEAD", str(d))
        if r.stdout.strip() == ref:
            return str(d)
    return None


def main() -> None:
    tok = ph.gh_token()
    for repo in REPOS:
        try:
            prs = ph.gh(f"/repos/cxs503/{repo}/pulls?state=open&per_page=30",
                        tok=tok)
        except Exception as e:
            print(f"{repo}: 列表失败 {type(e).__name__}")
            continue
        for p in prs:
            no, ref = p["number"], p["head"]["ref"]
            try:
                d = ph.gh(f"/repos/cxs503/{repo}/pulls/{no}", tok=tok)
            except Exception as e:
                print(f"{repo}#{no}: 详情失败 {type(e).__name__}")
                continue
            if d.get("mergeable_state") != "dirty":
                continue
            wt = find_worktree(repo, ref)
            if not wt:
                print(f"{repo}#{no}: dirty 无本地工作树，留人工（{ref}）")
                continue
            head0 = ph.sh("git rev-parse HEAD", wt).stdout.strip()
            remote = ph.gh(
                f"/repos/cxs503/{repo}/git/ref/heads/"
                f"{ref.replace('/', '%2F')}", tok=tok)["object"]["sha"]
            tree_local = ph.sh(f"git rev-parse {head0}^{{tree}}",
                               wt).stdout.strip()
            try:
                tree_remote = ph.gh(
                    f"/repos/cxs503/{repo}/git/commits/{remote}",
                    tok=tok)["tree"]["sha"]
            except Exception as e:
                print(f"{repo}#{no}: 远端树查询失败 {type(e).__name__}")
                continue
            if tree_local != tree_remote:
                print(f"{repo}#{no}: 本地头树 != 远端头树，留人工")
                continue
            ok, how = ph.merge_main_before_push(wt, f"pr_keeper#{no}")
            if not ok:
                print(f"{repo}#{no}: 修复未成——{how}")
                continue
            local = ph.sh("git rev-parse HEAD", wt).stdout.strip()
            main_sha = None
            try:
                main_sha = ph.gh(f"/repos/cxs503/{repo}/branches/main",
                                 tok=tok)["commit"]["sha"]
            except Exception:
                pass
            okp, note = ph.push_branch_api(
                repo, ref, wt, local, head0, main_sha, f"pr_keeper#{no}")
            print(f"{repo}#{no}: 修复推送 {'OK ' + note if okp else 'FAIL ' + note}"
                  f"（{how}）")
    print("pr_keeper pass done")


if __name__ == "__main__":
    main()
