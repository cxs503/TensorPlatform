"""命令行入口：plan（判据清跑）/ cycle（执行）/ daemon（常驻）/ 审计校验。

用法（默认 **plan**：只判不并，治理动作必须显式 ``--execute``）::

    python3 -m tools.merge_master.cli plan --repos TensorPlatform \\
        --include-recent 8 --out artifacts/merge_master/plan.json

    python3 -m tools.merge_master.cli cycle --execute --max-merges 2
    python3 -m tools.merge_master.cli daemon --interval 300
    python3 -m tools.merge_master.cli verify-audit
    python3 -m tools.merge_master.cli selfcheck
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Sequence

from . import gates as G
from .audit import AuditLog, verify
from .engine import (ALL_REPOS, Budget, Reconciler, build_rollback)
from .model import PullRequest
from .providers import (GitHub, GitHubChecks, GitHubDiffs, GitHubMerger,
                        GitHubQueue, PlatformLedger, platform_from_env)
from .rehearsal import RecordingLedger, RecordingMerger

DEFAULT_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "artifacts",
                            "merge_master")


class FileQueue:
    """队列快照（repo/number 列表）——离线复跑与证据固化用。"""

    def __init__(self, entries: Sequence[Dict[str, Any]], gh: GitHub):
        self.entries = list(entries)
        self.gh = gh

    def list_awaiting(self) -> List[PullRequest]:
        out = []
        for e in self.entries:
            repo, num = str(e["repo"]), int(e["number"])
            r = self.gh.get_pr(repo, num)
            if r.get("status") != 200:
                out.append(PullRequest(repo=repo, number=num, head_sha="",
                                       state="open", mergeable=False,
                                       mergeable_state="unavailable"))
                continue
            d = r["json"]
            out.append(PullRequest(
                repo=repo, number=num,
                head_sha=((d.get("head") or {}).get("sha") or ""),
                branch=((d.get("head") or {}).get("ref") or ""),
                state=str(d.get("state") or "open"),
                merged=bool(d.get("merged") or d.get("merged_at")),
                mergeable=d.get("mergeable"),
                mergeable_state=str(d.get("mergeable_state") or ""),
                base_ref=((d.get("base") or {}).get("ref") or "main"),
                title=str(d.get("title") or "")[:120]))
        return out


def _recent_prs(ledger: PlatformLedger, repos: Sequence[str],
                n: int) -> List[Dict[str, Any]]:
    """从任务账本里取最近 n 张带 PR 的任务（历史复判/幂等校验用）。"""
    idx = ledger.index()
    rows = []
    for key, rec in idx.items():
        if key.startswith("__"):
            continue
        repo = getattr(rec, "repo", None)
        pr = getattr(rec, "pr_number", None)
        if repo in repos and pr:
            rows.append({"repo": repo, "number": int(pr),
                         "mission_id": getattr(rec, "mission_id", None),
                         "status": getattr(rec, "status", "")})
    rows.sort(key=lambda r: (r["mission_id"] or 0), reverse=True)
    uniq, seen = [], set()
    for r in rows:
        if (r["repo"], r["number"]) in seen:
            continue
        seen.add((r["repo"], r["number"]))
        uniq.append(r)
    return uniq[:n]


def _ledger_and_audit(args) -> Dict[str, Any]:
    os.makedirs(args.outdir, exist_ok=True)
    ledger = platform_from_env()
    audit = AuditLog(args.audit)
    gh = GitHub()
    return {"ledger": ledger, "audit": audit, "gh": gh}


def _build_reconciler(args, env: Dict[str, Any], dry_run: bool,
                      entries: Sequence[Dict[str, Any]]) -> Reconciler:
    gh = env["gh"]
    queue = FileQueue(entries, gh) if entries else GitHubQueue(
        gh, args.repos, extra_prs=[(e["repo"], e["number"])
                                   for e in entries] or None)
    budget = Budget(max_merges=args.max_merges,
                    max_mutations=args.max_mutations, max_prs=args.max_prs)
    gates = G.GateSuite(min_tests_passed=args.min_tests,
                        lexicon_path=args.lexicon or None,
                        disabled=tuple(args.disable_gate or ()))
    ledger = env["ledger"]
    merger = GitHubMerger(gh, method=args.method)
    state_name = "state.json"
    if getattr(args, "rehearse", False):
        ledger = RecordingLedger(ledger)
        merger = RecordingMerger(method=args.method)
        state_name = "state_rehearsal.json"
    return Reconciler(
        queue=queue, ledger=ledger, checks=GitHubChecks(gh),
        diffs=GitHubDiffs(gh), merger=merger,
        audit=env["audit"],
        state_path=os.path.join(args.outdir, state_name),
        gates=gates, budget=budget, dry_run=dry_run, method=args.method)


def _write(path: str, payload: Dict[str, Any]) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)


def _summarize(rep, path: str) -> Dict[str, Any]:
    c = rep.counts()
    rows = [{"pr": d.pr.key, "action": d.action, "reasons": d.reasons,
             "detail": d.detail[:200], "sha": d.pr.head_sha[:12],
             "mission_id": d.mission_id, "mission_status": d.mission_status,
             "merge_commit": d.merge_commit,
             "gates": {g.gate: g.ok for g in d.gates}}
            for d in rep.decisions]
    return {"report": path, "counts": c, "decisions": rows,
            "audit": rep.audit}


def _guard(args) -> None:
    """执行路径不接受「关掉判据」：消融只允许在 plan/排练里跑。"""
    if args.execute and args.disable_gate:
        raise SystemExit("refused: --execute 与 --disable-gate 不能同时使用")
    if args.execute and getattr(args, "rehearse", False):
        raise SystemExit("refused: --execute 与 --rehearse 互斥")


def cmd_plan(args) -> int:
    _guard(args)
    env = _ledger_and_audit(args)
    entries: List[Dict[str, Any]] = []
    if args.queue:
        entries = json.load(open(args.queue, "r", encoding="utf-8"))
        entries = entries.get("prs", entries) if isinstance(entries, dict) \
            else entries
    if args.include_recent:
        have = {(e["repo"], e["number"]) for e in entries}
        for r in _recent_prs(env["ledger"], args.repos, args.include_recent):
            if (r["repo"], r["number"]) not in have:
                entries.append({"repo": r["repo"], "number": r["number"],
                                "mission_id": r.get("mission_id"),
                                "history_status": r.get("status")})
    # 排练=真实判据+记录式写桩 → 执行链路全跑一遍（安全，因为桩不触网）
    live_write = bool(args.execute or getattr(args, "rehearse", False))
    rec = _build_reconciler(args, env, dry_run=not live_write, entries=entries)
    rep = rec.run_cycle(limit=args.limit)
    out = os.path.join(args.outdir, args.out or "plan.json")
    _write(out, rep.to_json())
    summary = _summarize(rep, out)
    _write(out.replace(".json", ".summary.json"), summary)
    print(json.dumps({"counts": summary["counts"],
                      "audit": summary["audit"],
                      "summary_path": out.replace(".json", ".summary.json")},
                     ensure_ascii=False))
    for row in summary["decisions"]:
        print("%-26s %-6s %-40s %s" % (row["pr"], row["action"],
                                       ",".join(row["reasons"]) or "-",
                                       (row["detail"] or "")[:60]))
    return 0


def cmd_cycle(args) -> int:
    return cmd_plan(args)


def cmd_daemon(args) -> int:
    _guard(args)
    env = _ledger_and_audit(args)
    hb = os.path.join(args.outdir, "heartbeat.json")
    n = 0
    while True:
        try:
            entries = []
            if args.include_recent:
                for r in _recent_prs(env["ledger"], args.repos,
                                     args.include_recent):
                    entries.append({"repo": r["repo"], "number": r["number"]})
            rec = _build_reconciler(args, env, dry_run=not args.execute,
                                    entries=entries)
            rep = rec.run_cycle(limit=args.limit)
            _write(os.path.join(args.outdir, "last_cycle.json"), rep.to_json())
            _write(hb, {"pid": os.getpid(), "cycle": rep.cycle,
                        "at": rep.generated_at, "counts": rep.counts(),
                        "dry_run": rep.dry_run,
                        "audit_seq": rep.audit.get("seq", 0)})
        except Exception as exc:  # 常驻进程不得被单轮异常打死
            _write(hb, {"pid": os.getpid(), "at": time.time(),
                        "error": repr(exc)[:300]})
        n += 1
        if args.max_cycles and n >= args.max_cycles:
            return 0
        time.sleep(args.interval)


def cmd_verify_audit(args) -> int:
    res = verify(args.audit)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if res.get("ok") else 2


def cmd_selfcheck(args) -> int:
    """容器/守护自检：判据件可导入 + 词表可读 + 审计目录可写。"""
    from .providers import _github_token
    out = {
        "schema": "tp.merge_master.selfcheck/v1",
        "gates_importable": bool(G.GateSuite),
        "boundary_lexicon": G.lexicon_digest(args.lexicon or None),
        "github_token_present": bool(_github_token()),
        "audit_dir_writable": os.access(
            os.path.dirname(args.audit) or ".", os.W_OK),
        "repos": list(args.repos),
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="merge_master",
                                 description="PR 合并判据机（平台合并口自动化）")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--repos", nargs="+", default=["TensorPlatform"],
                       help="参与清算的仓（默认只平台仓）")
        p.add_argument("--outdir", default=DEFAULT_ROOT)
        p.add_argument("--audit",
                       default=os.path.join(DEFAULT_ROOT, "audit.jsonl"))
        p.add_argument("--queue", default="",
                       help="队列快照 json（[{repo,number},...]）")
        p.add_argument("--include-recent", type=int, default=0,
                       help="额外复判任务账本里最近 N 张带 PR 的任务")
        p.add_argument("--limit", type=int, default=0)
        p.add_argument("--max-prs", type=int, default=60)
        p.add_argument("--max-merges", type=int, default=3)
        p.add_argument("--max-mutations", type=int, default=8)
        p.add_argument("--min-tests", type=int, default=1)
        p.add_argument("--lexicon", default="")
        p.add_argument("--method", default="merge",
                       choices=["merge", "squash", "rebase"])
        p.add_argument("--execute", action="store_true",
                       help="真的执行合并与回写（默认 plan：只判不并）")
        p.add_argument("--rehearse", action="store_true",
                       help="排练：真实队列+真实判据，写动作换成记录桩")
        p.add_argument("--disable-gate", nargs="*", default=[],
                       help="消融实验：关掉指定门（仅 plan/排练）")
        p.add_argument("--out", default="plan.json")

    for name in ("plan", "cycle"):
        common(sub.add_parser(name))
    d = sub.add_parser("daemon")
    common(d)
    d.add_argument("--interval", type=int, default=300)
    d.add_argument("--max-cycles", type=int, default=0)
    sub.add_parser("verify-audit").add_argument("--audit", default=os.path.join(
        DEFAULT_ROOT, "audit.jsonl"))
    sc = sub.add_parser("selfcheck")
    sc.add_argument("--audit", default=os.path.join(DEFAULT_ROOT, "audit.jsonl"))
    sc.add_argument("--lexicon", default="")
    sc.add_argument("--repos", nargs="+", default=["TensorPlatform"])
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not args.cmd:
        build_parser().print_help()
        return 14
    return {"plan": cmd_plan, "cycle": cmd_cycle, "daemon": cmd_daemon,
            "verify-audit": cmd_verify_audit,
            "selfcheck": cmd_selfcheck}[args.cmd](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())