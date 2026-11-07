"""回滚执行器：读一条 rollback 规格 → 打印/执行 revert 步骤。

默认 ``plan``（只打印，不动远端）。``--execute`` 才真正在临时克隆里 revert，
用于「明天没有人来」时由机制自助回滚；审计链里每条合并都带着对应规格。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from typing import Any, Dict, List

SCHEMA = "tp.merge_master.rollback/v1"


def load(path: str) -> Dict[str, Any]:
    spec = json.load(open(path, "r", encoding="utf-8"))
    missing = [k for k in ("repo", "merge_commit", "steps") if not spec.get(k)]
    if missing:
        raise ValueError("rollback spec missing: %s" % ",".join(missing))
    return spec


def plan(spec: Dict[str, Any]) -> List[str]:
    return [str(s) for s in spec["steps"]]


def execute(spec: Dict[str, Any], *, remote: str = "origin",
            workdir: str = "", dry_run: bool = True,
            timeout: int = 600) -> Dict[str, Any]:
    """在临时目录克隆→revert→（dry_run 则不 push）。

    返回 {ok, steps[], pushed, workdir, stdout_tail}；任何一步失败即停。
    """
    cmds: List[List[str]] = []
    repo = spec["repo"]
    url = "https://github.com/%s/%s.git" % (
        spec.get("owner", "cxs503"), repo)
    root = workdir or tempfile.mkdtemp(prefix="mm-rollback-")
    target = os.path.join(root, repo)
    cmds.append(["git", "clone", "--quiet", url, target])
    cmds.append(["git", "-C", target, "checkout", "--quiet",
                 spec.get("base_ref", "main")])
    cmds.append(["git", "-C", target, "revert", "--no-edit", "-m", "1",
                 spec["merge_commit"]])
    if not dry_run:
        cmds.append(["git", "-C", target, "push", remote,
                     spec.get("base_ref", "main")])
    steps, tail = [], ""
    for cmd in cmds:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout)
        steps.append({"cmd": " ".join(cmd), "rc": p.returncode})
        tail = (p.stdout + p.stderr)[-800:]
        if p.returncode != 0:
            return {"ok": False, "steps": steps, "pushed": False,
                    "workdir": target, "stdout_tail": tail}
    return {"ok": True, "steps": steps, "pushed": not dry_run,
            "workdir": target, "stdout_tail": tail}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="merge_master rollback executor")
    ap.add_argument("spec")
    ap.add_argument("--execute", action="store_true",
                    help="真的执行（含 push）；默认只打印计划")
    ap.add_argument("--workdir", default="")
    args = ap.parse_args(argv)
    spec = load(args.spec)
    if not args.execute:
        print(json.dumps({"schema": SCHEMA, "plan": plan(spec),
                          "dry_run": True}, ensure_ascii=False, indent=1))
        return 0
    out = execute(spec, workdir=args.workdir, dry_run=False)
    print(json.dumps(dict(out, schema=SCHEMA), ensure_ascii=False, indent=1))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())