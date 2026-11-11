"""事故重放：用 git 历史里的真实 commit 做活性巡检的天然正反例。

符号迁移造成的静默失效很少被"随手造"出来，但历史里一定有：某次提交把测试从
collection error 状态修好（或反过来弄坏）。把那段历史抽成两个树、同一套巡检跑一遍，
就有了**不可伪造的负控/正控**——负控不需要人去编，它就是仓库自己发生过的事故。

用法（也供 pytest 复用）::

    from verification_vitality.replay import discover_fix, extract_tree, replay

    fix = discover_fix(repo_root, "tests/capability_ledger_auto/conftest.py")
    result = replay(repo_root, "tests/capability_ledger_auto",
                    before_ref=fix["parent"], after_ref=fix["commit"])
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from .patrol import patrol


def _git(repo_root: Path, *args: str, timeout: float = 60.0) -> str:
    out = subprocess.run(["git", *args], cwd=str(repo_root), capture_output=True,
                         text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def discover_fix(repo_root: Path, path: str) -> Dict[str, Optional[str]]:
    """找"首次加入 ``path`` 的提交"，把它当作事故修复点；父提交即事故状态。"""
    repo_root = Path(repo_root)
    commit = _git(repo_root, "log", "--diff-filter=A", "--format=%H", "--", path).splitlines()
    if not commit:
        raise RuntimeError(f"{path} 在历史中从未被加入")
    fix = commit[-1]
    parent = _git(repo_root, "rev-parse", f"{fix}^").strip() or None
    return {"commit": fix, "parent": parent,
            "subject": _git(repo_root, "log", "-1", "--format=%s", fix)}


def extract_tree(repo_root: Path, ref: str, dest: Path) -> Path:
    """把某 commit 的完整树导出到 ``dest``（不碰工作树、不改 .git）。"""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(["git", "archive", "--format=tar", ref], cwd=str(repo_root),
                            stdout=subprocess.PIPE)
    tar = subprocess.run(["tar", "-x", "-C", str(dest)], stdin=proc.stdout,
                         capture_output=True, text=True, timeout=180)
    if proc.stdout:
        proc.stdout.close()
    if proc.wait(timeout=180) != 0 or tar.returncode != 0:
        raise RuntimeError(f"git archive {ref} 导出失败: {tar.stderr.strip()}")
    return dest


def replay(repo_root: Path, test_dir: str, *, before_ref: str, after_ref: str,
           registry: str = "config/capabilities.json",
           timeout: float = 90.0, workdir: Optional[Path] = None) -> Dict[str, Any]:
    """对 before/after 两棵树跑同一套巡检，输出可机器裁决的重放结论。"""
    repo_root = Path(repo_root)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(workdir or tmp)
        before_tree = extract_tree(repo_root, before_ref, root / "before")
        after_tree = extract_tree(repo_root, after_ref, root / "after")
        before = patrol(before_tree, test_dirs=(test_dir,),
                        registry_path=before_tree / registry, timeout=timeout)
        after = patrol(after_tree, test_dirs=(test_dir,),
                       registry_path=after_tree / registry, timeout=timeout)
    n_before_dead = before["summary"]["dead"]
    n_after_dead = after["summary"]["dead"]
    before_exit = before["collect"]["exit_code"]
    after_exit = after["collect"]["exit_code"]
    return {
        "schema": "verification-vitality-replay/1.0",
        "test_dir": test_dir,
        "before_ref": before_ref,
        "after_ref": after_ref,
        "before": {"exit_code": before_exit, "n_units": before["summary"]["n_units"],
                   "dead": n_before_dead, "alive": before["summary"]["alive"],
                   "unmapped": before["summary"]["unmapped"],
                   "errors": before["collect"]["errors"],
                   "duration_s": before["summary"]["elapsed_s"]},
        "after": {"exit_code": after_exit, "n_units": after["summary"]["n_units"],
                  "dead": n_after_dead, "alive": after["summary"]["alive"],
                  "unmapped": after["summary"]["unmapped"],
                  "errors": after["collect"]["errors"],
                  "duration_s": after["summary"]["elapsed_s"]},
        "criteria": {
            "before_dead_and_exit_nonzero": n_before_dead > 0 and before_exit != 0,
            "after_alive_and_exit_zero": n_after_dead == 0 and after_exit == 0,
        },
        "passed": (n_before_dead > 0 and before_exit != 0
                   and n_after_dead == 0 and after_exit == 0),
    }


def main(argv: Optional[list] = None) -> int:  # pragma: no cover - CLI 薄壳
    import argparse
    ap = argparse.ArgumentParser(description="验证面活性事故重放（git 历史正反例）")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--test-dir", default="tests/capability_ledger_auto")
    ap.add_argument("--fix-file", default="tests/capability_ledger_auto/conftest.py")
    ap.add_argument("--before-ref", default=None)
    ap.add_argument("--after-ref", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    repo = Path(args.repo)
    if args.before_ref and args.after_ref:
        before, after, subject = args.before_ref, args.after_ref, None
    else:
        fix = discover_fix(repo, args.fix_file)
        before, after, subject = fix["parent"], fix["commit"], fix["subject"]
    result = replay(repo, args.test_dir, before_ref=before, after_ref=after)
    result["fix"] = {"commit": after, "subject": subject}
    text = json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True)
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())