#!/usr/bin/env python3
"""Harness v3 — tp_worker 集成钩子（3 行改动即可接上 journal / 续跑）。

为什么单独一层：tp_worker.py 是平台守护侧文件（不在任务工作树/白名单内），
本单只交付"可 import 的钩子 + 精确补丁说明"，由平台 owner 应用；
钩子本身与 seat/supervisor 共用同一套 journal 语义，因此行为已被门禁覆盖。

接法（tp_worker.run_mission 内）:
    # ① claim 注入（在 messages[0..1] 组装后紧接着）
    _inj = harness_journal_hook.claim_injection(mission, root)
    if _inj: messages[1]["content"] = _inj + messages[1]["content"]
    # ② 每 3 次工具调用 / 每个检查点
    harness_journal_hook.checkpoint(mission, root, tool_calls=calls,
                                    next_intent="<当前意图>")
    # ③ 收尾（finish / failed / 崩溃由 supervisor 兜底）
    harness_journal_hook.attempt_end(mission, root, status="steps_done"|"failed",
                                     tool_calls=calls, summary=result)

"已完成步骤"取自**磁盘证据**（git status --porcelain --ignored 的新增/修改路径），
不取模型自述——这与平台 existing 的证据面（evidence_snapshot）同源，避免吹牛。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import harness_journal as HJ  # noqa: E402

_NOISE = ("__pycache__", ".pyc", ".pytest_cache", "node_modules", ".egg-info",
          ".venv/", ".ipynb_checkpoints")


def enabled() -> bool:
    return os.environ.get("HARNESS_JOURNAL", "1") not in ("0", "false", "off")


def _mission_id(mission) -> int:
    return int(mission["id"] if isinstance(mission, dict) else mission)


def evidence_paths(root) -> list:
    """工作树里的真产物路径（含 ignored），去噪后作为"已完成步骤"的候选。"""
    out = []
    root = pathlib.Path(root)
    git_ok = False
    try:
        p = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=30)
        git_ok = p.returncode == 0
    except Exception:
        git_ok = False
    if git_ok:
        try:
            p = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--ignored"],
                               capture_output=True, text=True, timeout=60)
            for ln in p.stdout.splitlines():
                if not ln.strip():
                    continue
                rel = ln[3:].strip().rstrip("/")
                if any(n in rel for n in _NOISE):
                    continue
                out.append(rel)
        except Exception:
            pass
        return sorted(set(out))
    # 非 git 目录（如 /tmp 沙箱、非版本化工作区）：退化为文件清单，语义仍然"证据=磁盘文件"
    if root.exists():
        for f in sorted(root.rglob("*")):
            if not f.is_file():
                continue
            rel = str(f.relative_to(root))
            if any(n in rel for n in _NOISE):
                continue
            out.append(rel)
    return sorted(set(out))


def steps_from_evidence(root, limit=40) -> list:
    root = pathlib.Path(root)
    steps = []
    for rel in evidence_paths(root)[:limit]:
        p = root / rel
        steps.append({"id": rel, "what": f"产出/修改 {rel}",
                      "evidence": str(p), "size": p.stat().st_size if p.exists() else 0})
    return steps


def _evidence_verifier(root):
    root = pathlib.Path(root)

    def _ok(step) -> bool:
        p = pathlib.Path(step.get("evidence") or (root / str(step.get("id"))))
        return p.exists() and (not p.is_file() or p.stat().st_size >= 0)
    return _ok


def claim_injection(mission, root) -> str:
    """claim 时调用：返回要前置到首条 user 消息的续跑简报（无日志时返回降级声明）。"""
    if not enabled():
        return ""
    mid = _mission_id(mission)
    try:
        b = HJ.resume_brief(None, mid, verified_evidence=_evidence_verifier(root))
        HJ.write_meta(None, mid, repo=(mission or {}).get("repo") if isinstance(mission, dict) else "",
                      agent=os.environ.get("TP_AGENT_ID", ""))
        HJ.append(None, mid, kind="attempt_start", agent_id=os.environ.get("TP_AGENT_ID", ""),
                  goal=(mission or {}).get("goal", "")[:400] if isinstance(mission, dict) else "",
                  dirty_tree=HJ.tree_snapshot(root),
                  resumed_steps=b["resumed_steps"], degraded=b["degraded"])
        return b["text"]
    except Exception as e:                     # journal 永不让主流程失败
        return f"【断点续跑 · journal 不可用（{type(e).__name__}）：从零开始，勿声称续跑】\n"


def checkpoint(mission, root, tool_calls=0, plan=None, verified_facts=None,
               unverified_assumptions=None, next_intent="", extra=None) -> dict:
    if not enabled():
        return {}
    mid = _mission_id(mission)
    try:
        rec = HJ.append(None, mid, kind="checkpoint",
                        agent_id=os.environ.get("TP_AGENT_ID", ""),
                        tool_calls=int(tool_calls), calls_used=int(tool_calls),
                        plan=plan or [], done_steps=steps_from_evidence(root),
                        verified_facts=verified_facts or [],
                        unverified_assumptions=unverified_assumptions or [],
                        next_intent=next_intent,
                        dirty_tree=HJ.tree_snapshot(root))
        if extra:
            rec.update(extra)
        return rec
    except Exception:
        return {}


def attempt_end(mission, root, status="steps_done", tool_calls=0, summary="") -> dict:
    if not enabled():
        return {}
    mid = _mission_id(mission)
    try:
        return HJ.append(None, mid, kind="attempt_end",
                         agent_id=os.environ.get("TP_AGENT_ID", ""),
                         status=status, calls_used=int(tool_calls),
                         done_steps=steps_from_evidence(root),
                         next_intent=str(summary)[:400],
                         dirty_tree=HJ.tree_snapshot(root))
    except Exception:
        return {}


def _main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Harness v3 集成钩子 CLI")
    ap.add_argument("cmd", choices=["brief", "checkpoint", "verify"])
    ap.add_argument("--mission-id", type=int, required=True)
    ap.add_argument("--worktree", default=".")
    a = ap.parse_args(argv)
    mission = {"id": a.mission_id, "goal": ""}
    if a.cmd == "brief":
        print(claim_injection(mission, a.worktree))
        return 0
    if a.cmd == "checkpoint":
        print(json.dumps(checkpoint(mission, a.worktree, next_intent="manual"),
                         ensure_ascii=False))
        return 0
    st = HJ.read(None, a.mission_id)
    print(json.dumps({"path": st["path"], "valid": len(st["entries"]),
                      "corrupt": len(st["corrupt"]), "degraded": st["degraded"],
                      "merged": HJ.merge(st["entries"])}, ensure_ascii=False, indent=1,
                     default=str))
    return 0


if __name__ == "__main__":
    sys.exit(_main())