#!/usr/bin/env python3
"""Observability minimal surface — nightly digest + alert ledger generator (read-only).

What it does
------------
Reads a snapshot of the platform's own bookkeeping (``tensor_dev_missions``,
``campaign_nodes``, ``tensor_approval_requests``, ``campaign_plans``) through ``psql -tAc``
SELECTs only, evaluates :mod:`tools.obs.alert_rules`, and renders **one page of the night**:

  ① 三军状态   fleet rollup (solver / ship / platform): running, stuck, just-claimed,
               long-but-alive, failed & succeeded inside the window, blocked/gate nodes;
  ② 异常清单   P1/P2 alerts — stuck missions, failed×3 blocked nodes, pending gates;
  ③ 待放门     every node parked on a gate/release condition + pending approval requests;
  ④ 规则与负例 the thresholds, the rule table, and *every suppression* (刚领取不告警,
               长跑但活着不告警, 未达×3 仅观察) with the observed numbers;
  ⑤ 审计链     row counts, snapshot SHA-256, as-of stamp, exact replay command.

Outputs (all inside the work tree; nothing is written outside it)
---------------------------------------------------------------
  artifacts/in15_o8/digest_<asOf>.md      the one-page digest
  artifacts/in15_o8/alerts_<asOf>.json    machine-readable alerts + suppressions + ledgers
  artifacts/in15_o8/snapshot_<asOf>.json  the exact input snapshot (replay without the DB)
  artifacts/in15_o8/staleness_<asOf>.png  figure (staleness ranking + blocked ledger)

Discipline: additive only — no writes to the platform DB, no schema change, no import of
the frozen API surface. ``--dry-run`` computes everything and writes nothing.

Usage
-----
  # live: evaluate "now" from the platform DB
  python3 tools/obs/obs_digest.py --from-db --write-snapshot \
      --md-out artifacts/in15_o8/digest_live.md --json-out artifacts/in15_o8/alerts_live.json

  # replay a real stall cohort at its own clock (2026-11-02 heartbeat blackout)
  python3 tools/obs/obs_digest.py --snapshot artifacts/in15_o8/snapshot_20261102.json \
      --as-of 2026-11-02T20:30:00Z --md-out artifacts/in15_o8/digest_20261102.md

  python3 tools/obs/obs_digest.py --from-db --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.obs.alert_rules import (  # noqa: E402
    SUPPRESS_LABEL, evaluate, iso, parse_ts, project_at, worst_offenders,
)

DEFAULT_OUT_DIR = os.path.join("artifacts", "in15_o8")
AGENT_ENV = "/root/tp-agent/agent.env"

# ── read-only SQL (SELECT only; the tool never writes to the DB) ──────────────
SQL_MISSIONS = """
SELECT json_build_object(
  'id', id, 'repo', repo, 'status', status, 'task_key', task_key,
  'created_at', created_at, 'heartbeat_at', heartbeat_at, 'finished_at', finished_at,
  'claimed_by', claimed_by, 'model', model, 'fail_stage', fail_stage)::text
FROM tensor_dev_missions
WHERE created_at > now() - interval '{days} days' OR finished_at IS NULL
ORDER BY id
"""

SQL_NODES = """
SELECT json_build_object(
  'id', id, 'plan_id', plan_id, 'name', left(name, 180), 'status', status,
  'consecutive_fails', consecutive_fails, 'block_reason', left(coalesce(block_reason,''),200),
  'mission_id', mission_id, 'node_type', node_type)::text
FROM campaign_nodes
ORDER BY id
"""

SQL_APPROVALS = """
SELECT json_build_object(
  'id', id, 'kind', kind, 'status', status, 'mission_id', mission_id,
  'from_stage', from_stage, 'to_stage', to_stage,
  'reason', left(coalesce(reason,''),200), 'created_at', created_at)::text
FROM tensor_approval_requests
ORDER BY id
"""

SQL_PLANS = """
SELECT json_build_object(
  'id', id, 'title', left(coalesce(metadata->>'title', name), 120), 'status', status)::text
FROM campaign_plans
ORDER BY id
"""


def _password_from_env_file(path: str = AGENT_ENV) -> str:
    """Fleet credentials live in the (never committed) agent env file."""
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if line.startswith("TP_PG_PASSWORD="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _psql(sql: str, timeout: int = 180) -> str:
    """Run a read-only SELECT. Tries env credentials, then the local postgres socket."""
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "PGPASSWORD": os.environ.get("TP_PG_PASSWORD") or _password_from_env_file(),
        "PGHOST": os.environ.get("TP_PG_HOST", "127.0.0.1"),
        "PGUSER": os.environ.get("TP_PG_USER", "foundation"),
        "PGDATABASE": os.environ.get("TP_PG_DB", "tensorplatform"),
    }
    runuser = "/usr/sbin/runuser" if os.path.exists("/usr/sbin/runuser") else "runuser"
    # 2nd attempt: local postgres socket — must NOT inherit PGUSER/PGHOST/PGPASSWORD
    socket_env = {"PATH": env["PATH"]}
    attempts = [(["psql", "-tAc", sql], env),
                ([runuser, "-u", "postgres", "--", "psql",
                  "-d", env["PGDATABASE"], "-tAc", sql], socket_env)]
    last = ""
    for cmd, cmd_env in attempts:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=cmd_env, timeout=timeout)
        if proc.returncode == 0:
            return proc.stdout
        last = (proc.stderr or "").strip()[:300]
    raise RuntimeError(f"psql failed: {last}")


def _rows(out: str) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def build_snapshot(since_days: int = 7, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Snapshot the platform bookkeeping (read-only)."""
    now = now or datetime.now(timezone.utc)
    return {
        "schema_version": 1,
        "generated_at": iso(now),
        "source": "tensor_dev_missions+campaign_nodes+tensor_approval_requests+campaign_plans (read-only SELECT)",
        "missions": _rows(_psql(SQL_MISSIONS.format(days=since_days))),
        "nodes": _rows(_psql(SQL_NODES)),
        "approvals": _rows(_psql(SQL_APPROVALS)),
        "plans": _rows(_psql(SQL_PLANS)),
    }


def load_snapshot(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def snapshot_digest(snapshot: Dict[str, Any]) -> str:
    """SHA-256 of the canonical snapshot — the audit-chain anchor."""
    blob = json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# ── rendering ────────────────────────────────────────────────────────────────
def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v).replace("|", "/")


def _table(headers: List[str], rows: List[List[Any]]) -> str:
    if not rows:
        return "_(空)_\n"
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(_fmt(c) for c in r) + " |")
    return "\n".join(out) + "\n"


def render_digest(snapshot: Dict[str, Any], evaluation: Dict[str, Any],
                  command: str, snapshot_path: Optional[str] = None,
                  replay_note: str = "") -> str:
    c = evaluation["counts"]
    th = evaluation["thresholds"]
    as_of = evaluation["as_of"]
    lines: List[str] = []
    add = lines.append

    add(f"# 昨夜战况一页 · IN15-O8 观测告警 digest（as-of {as_of}）")
    add("")
    add(f"- 窗口：最近 {evaluation['window_hours']} h；数据源：`{snapshot.get('source','(snapshot)')}`")
    add(f"- 快照生成：{snapshot.get('generated_at')}；快照 SHA-256：`{snapshot_digest(snapshot)[:16]}…`"
        + (f"；快照文件：`{snapshot_path}`" if snapshot_path else ""))
    add(f"- 判据：停滞={th['stuck_minutes']} min 无心跳；领取宽限={th['claim_grace_minutes']} min；"
        f"失败处置线=×{th['block_fails']}")
    add(f"- 结论：**P1 {c['alerts_p1']} 条 / P2 {c['alerts_p2']} 条**；"
        f"被规则看过并**明确不告警**的 {c['suppressions_total']} 条（负例见 ④）")
    if replay_note:
        add(f"- 回放说明：{replay_note}")
    add("")

    # ① 三军状态
    add("## ① 三军状态")
    add("")
    rows = []
    for f in evaluation["fleets"]:
        rows.append([f["label"], f["running"], f["queued"], f["stuck"], f["just_claimed"],
                     f["long_alive"], f["failed_window"], f["succeeded_window"],
                     f["blocked_nodes"], f["gate_nodes"]])
    add(_table(["军", "running", "queued", "停滞", "刚领取", "长跑活着",
                "窗口内 failed", "窗口内 succeeded", "×3 阻塞节点", "待放门节点"], rows))
    add(f"任务总数（快照内）{c['missions_total']}，非终态受检 {c['non_terminal_examined']}"
        f"（running {c['running']}），节点 {c['nodes_total']}"
        f"（blocked {c['blocked_nodes_total']}）。")
    add("")

    # ② 异常清单
    add("## ② 异常清单")
    add("")
    p1 = [a for a in evaluation["alerts"] if a["severity"] == "P1"]
    p2 = [a for a in evaluation["alerts"] if a["severity"] == "P2"]
    if not evaluation["alerts"]:
        add("**本轮无告警**（P1/P2 均为 0）——所有 running 任务心跳新鲜，无 ×3 阻塞节点。\n")
    for sev, group in (("P1", p1), ("P2", p2)):
        if not group:
            continue
        add(f"### {sev}（{len(group)} 条）")
        rows = []
        for a in group:
            ev = a["evidence"]
            detail = (f"心跳断 {ev.get('heartbeat_age_min')} min（末次 {ev.get('last_heartbeat')}）"
                      if a["rule"] == "R1_STUCK_RUNNING"
                      else f"consecutive_fails={ev.get('consecutive_fails')}；{ev.get('block_reason')}"
                      if a["rule"] == "R2_BLOCKED_THRICE"
                      else f"{ev.get('name')}｜{ev.get('block_reason')}")
            rows.append([a["rule"], a["subject"], ev.get("repo") or ev.get("plan_id"),
                         detail, a["action"]])
        add(_table(["规则", "对象", "仓/计划", "证据", "建议动作"], rows))
    add("")

    watch = evaluation["ledgers"]["watch"]
    add(f"#### 观察账（blocked 但未达 ×{th['block_fails']}，不进 P1）")
    add("")
    add(_table(["节点", "计划", "名称", "consecutive_fails", "阻塞原因"],
               [[w["id"], w["plan_id"], w["name"][:60], w["consecutive_fails"],
                 w["block_reason"][:60]] for w in watch]))
    add("")

    # ③ 待放门
    add("## ③ 待放门（parked gates，非故障）")
    add("")
    gate_rows = [[g["id"], g["plan_id"], g["name"][:64], g["block_reason"][:70]]
                 for g in evaluation["ledgers"]["gates"]]
    add(_table(["节点", "计划", "名称", "放门条件"], gate_rows))
    appr_rows = [[a["id"], a["kind"], a["mission_id"], a["from_stage"], a["to_stage"],
                  a["reason"][:60]] for a in evaluation["pending_approvals"]]
    if appr_rows:
        add("审批表 pending：")
        add(_table(["id", "kind", "mission", "from→to", "reason"], appr_rows))
    else:
        add("审批表无 pending 记录。")
    add("")

    # ④ 规则与负例
    add("## ④ 告警规则与负例（负控）")
    add("")
    add(_table(["规则", "判据", "级别", "触发数"], [
        ["R1_STUCK_RUNNING", f"running 且心跳断 ≥ {th['stuck_minutes']} min", "P1", c["stuck"]],
        ["R2_BLOCKED_THRICE", f"blocked 且 consecutive_fails ≥ {th['block_fails']}", "P1",
         c["fail_blocked_ge3"]],
        ["R3_PENDING_GATE", "节点停在放门条件上 / 审批 pending", "P2", c["gate_nodes"]],
    ]))
    sup_rows = []
    for s in evaluation["suppressions"]:
        obs = s["observed"]
        detail = ("心跳年龄 %s min / 领取年龄 %s min / 有否心跳 %s"
                  % (obs.get("heartbeat_age_min"), obs.get("claim_age_min"),
                     obs.get("heartbeat_present"))
                  if s["code"] != "S3_SUBTHRESHOLD"
                  else "consecutive_fails=%s" % obs.get("consecutive_fails"))
        sup_rows.append([s["code"], SUPPRESS_LABEL[s["code"]], s["subject"], detail])
    add(_table(["抑制码", "含义", "对象", "观测值"], sup_rows))
    grace = th["claim_grace_minutes"] * 4          # "young claim" reporting window
    youngest = sorted([r for r in evaluation["states"]
                       if r["status"] == "running" and r.get("claim_age_min") is not None
                       and r["claim_age_min"] <= grace],
                      key=lambda r: r["claim_age_min"])[:6]
    if youngest:
        add(f"刚领取窗口内的真实案例（领取年龄 ≤ {grace} min，R1 结构性静默，不告警）：")
        add("")
        add(_table(["mission", "仓", "领取年龄 min", "心跳年龄 min", "已有心跳", "判定"],
                   [[f"#{r['id']}", r["repo"], r["claim_age_min"], r["heartbeat_age_min"],
                     r["heartbeat_present"], r["state"]] for r in youngest]))
    else:
        add(f"本轮无领取年龄 ≤ {grace} min 的运行任务（该窗口在本轮为空集，非「未检查」）。")
    add("")
    add("负例意义：S1 证明「刚领取」不会被误判成停滞（负例 1）；S2 证明「跑得久但心跳新鲜」不被误判"
        "（负例 2，长跑≠停滞）；S3 证明未到 ×3 的阻塞节点只进观察账、不进处置账。"
        "没有这些负例，告警器无法与「根本没看」区分。")
    add("")

    # ⑤ 审计链
    add("## ⑤ 审计链与判据数字")
    add("")
    add(_table(["量", "值"], [
        ["as_of", as_of],
        ["快照 SHA-256", snapshot_digest(snapshot)],
        ["missions_total", c["missions_total"]],
        ["running / stuck / just_claimed / long_alive / active",
         f"{c['running']} / {c['stuck']} / {c['just_claimed']} / {c['long_alive']} / {c['active']}"],
        ["nodes_total / blocked / ×3阻塞 / 待放门 / 观察",
         f"{c['nodes_total']} / {c['blocked_nodes_total']} / {c['fail_blocked_ge3']} / "
         f"{c['gate_nodes']} / {c['watch_nodes']}"],
        ["alerts P1 / P2 / 抑制", f"{c['alerts_p1']} / {c['alerts_p2']} / {c['suppressions_total']}"],
        ["时间投影", ("重建 as-of 瞬时态：丢弃 %s 条（未创建/已终结），心跳钳位 %s 条；判据 %s"
                      % (evaluation["projection"]["dropped"], evaluation["projection"]["heartbeat_clamped"],
                         evaluation["projection"]["rule"])) if evaluation.get("projection")
         else "无（直接使用快照内当前 status）"],
    ]))
    add("复现命令（无需 DB，用提交的快照即可逐字复现本页数字）：")
    add("")
    add("```")
    add(command)
    add("```")
    add("")
    add("回归测试：`python3 -m pytest tools/obs/tests -q`")
    add("")
    return "\n".join(lines)


# ── figure ───────────────────────────────────────────────────────────────────
def render_figure(snapshot: Dict[str, Any], evaluation: Dict[str, Any], path: str,
                  top_n: int = 12) -> str:
    """Staleness ranking (left) vs the blocked/gate ledger (right)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    th = evaluation["thresholds"]
    worst = worst_offenders(evaluation, top_n)
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.2),
                             gridspec_kw={"width_ratios": [1.25, 1]})

    ax = axes[0]
    names = [f"#{r['id']} {r['repo']}" for r in worst][::-1]
    vals = [(r["heartbeat_age_min"] or 0.0) for r in worst][::-1]
    colors = ["#c0392b" if r["state"] == "stuck" else
              "#e67e22" if r["state"] == "long_alive" else
              "#7f8c8d" if r["state"] == "just_claimed" else "#2471a3"
              for r in worst][::-1]
    ax.barh(range(len(vals)), vals, color=colors)
    ax.set_yticks(range(len(vals)))
    ax.set_yticklabels(names, fontsize=7.5)
    ax.axvline(th["stuck_minutes"], color="#c0392b", ls="--", lw=1.2)
    ax.text(th["stuck_minutes"], len(vals) - 0.4, f" stale threshold {th['stuck_minutes']} min",
            color="#c0392b", fontsize=8, va="top")
    ax.set_xlabel("heartbeat age [min]   (as-of %s)" % evaluation["as_of"], fontsize=9)
    ax.set_title("(a) stalest running missions (top %d)" % len(vals), fontsize=10)

    ax = axes[1]
    labels = ["failed x3\nfail-blocked (P1)", "failed x1-2\nwatch only (S3)", "parked gate\nR3 (P2)"]
    keys = ["blocked", "watch", "gates"]
    counts = [len(evaluation["ledgers"][k]) for k in keys]
    cs = ["#c0392b", "#b7950b", "#2471a3"]
    bars = ax.bar(labels, counts, color=cs)
    for b, v in zip(bars, counts):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.05, str(v), ha="center", fontsize=10)
    ax.set_ylabel("campaign nodes", fontsize=9)
    ax.set_title("(b) node ledgers (snapshot holds %d nodes)" % evaluation["counts"]["nodes_total"],
                 fontsize=10)
    ax.set_ylim(0, max(counts + [1]) * 1.25)

    fig.suptitle("Observability minimal surface: stall-detection plane + blocked/gate ledgers "
                 "(reproducible from the committed snapshot, as-of %s)" % evaluation["as_of"],
                 fontsize=11.5)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


# ── CLI ──────────────────────────────────────────────────────────────────────
def _stamp(as_of: str) -> str:
    return (as_of or "").replace("-", "").replace(":", "").replace("T", "_").rstrip("Z")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="observability digest / alert ledger (read-only)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-db", action="store_true", help="snapshot the platform DB (SELECT only)")
    src.add_argument("--snapshot", help="evaluate a committed snapshot JSON (replay)")
    ap.add_argument("--since-days", type=int, default=7, help="mission lookback for --from-db")
    ap.add_argument("--as-of", help="ISO timestamp to evaluate at (default: snapshot generated_at)")
    ap.add_argument("--window-hours", type=int, default=24, help="failed/succeeded window")
    ap.add_argument("--md-out", help="digest markdown path")
    ap.add_argument("--json-out", help="alerts JSON path")
    ap.add_argument("--write-snapshot", nargs="?", const="", default=None,
                    help="dump the input snapshot (optional path; with --from-db)")
    ap.add_argument("--figure", help="PNG figure path (matplotlib)")
    ap.add_argument("--top", type=int, default=12, help="rows in the staleness figure")
    ap.add_argument("--project", dest="project", action="store_true", default=None,
                    help="rebuild the alive-at-as_of state (default: auto when --as-of is past)")
    ap.add_argument("--no-project", dest="project", action="store_false",
                    help="evaluate the snapshot's stored status verbatim")
    ap.add_argument("--dry-run", action="store_true", help="compute + print, write nothing")
    ap.add_argument("--fail-on-alert", action="store_true", help="exit 2 when P1 alerts exist")
    ap.add_argument("--quiet", action="store_true", help="do not echo the digest in dry-run")
    args = ap.parse_args(argv)

    if args.from_db:
        snapshot = build_snapshot(since_days=args.since_days)
        source_desc = "db"
    else:
        snapshot = load_snapshot(args.snapshot)
        source_desc = args.snapshot

    as_of = parse_ts(args.as_of) if args.as_of else parse_ts(snapshot.get("generated_at"))
    gen_at = parse_ts(snapshot.get("generated_at"))
    project = args.project
    if project is None:                      # auto: only needed when replaying a past instant
        project = bool(as_of and gen_at and as_of < gen_at - timedelta(seconds=60))
    if project:
        snapshot = project_at(snapshot, as_of)
    evaluation = evaluate(snapshot, as_of=as_of, window_hours=args.window_hours)
    stamp = _stamp(evaluation["as_of"])

    command = "python3 tools/obs/obs_digest.py --snapshot <snapshot.json> --as-of " + evaluation["as_of"]
    digest = render_digest(snapshot, evaluation, command,
                           snapshot_path=args.snapshot or args.write_snapshot,
                           replay_note=("回放历史快照（as-of 早于快照生成时间），用于验证告警规则在真实停滞"
                                        "案例上点火" if args.as_of else ""))

    payload = {
        "schema_version": 1,
        "generated_by": "tools/obs/obs_digest.py",
        "source": source_desc,
        "snapshot_sha256": snapshot_digest(snapshot),
        "as_of": evaluation["as_of"],
        "thresholds": evaluation["thresholds"],
        "projection": evaluation.get("projection"),
        "counts": evaluation["counts"],
        "alerts": evaluation["alerts"],
        "suppressions": evaluation["suppressions"],
        "ledgers": evaluation["ledgers"],
        "pending_approvals": evaluation["pending_approvals"],
        "fleets": evaluation["fleets"],
        "replay_command": command,
    }

    out_dir = DEFAULT_OUT_DIR
    md_path = args.md_out or os.path.join(out_dir, f"digest_{stamp}.md")
    json_path = args.json_out or os.path.join(out_dir, f"alerts_{stamp}.json")

    if args.dry_run:
        if not args.quiet:
            print(digest)
        print("── dry-run ──", file=sys.stderr)
        print(f"would write: {md_path}", file=sys.stderr)
        print(f"would write: {json_path}", file=sys.stderr)
        if args.write_snapshot is not None:
            print(f"would write: {args.write_snapshot or os.path.join(out_dir, f'snapshot_{stamp}.json')}",
                  file=sys.stderr)
        if args.figure:
            print(f"would write: {args.figure}", file=sys.stderr)
        print(f"counts: P1={evaluation['counts']['alerts_p1']} P2={evaluation['counts']['alerts_p2']} "
              f"suppressed={evaluation['counts']['suppressions_total']}", file=sys.stderr)
    else:
        for path in (md_path, json_path):
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write(digest)
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        if args.write_snapshot is not None:
            snap_path = args.write_snapshot or os.path.join(out_dir, f"snapshot_{stamp}.json")
            os.makedirs(os.path.dirname(snap_path) or ".", exist_ok=True)
            with open(snap_path, "w", encoding="utf-8") as fh:
                json.dump(snapshot, fh, ensure_ascii=False, indent=1)
        if args.figure:
            render_figure(snapshot, evaluation, args.figure, top_n=args.top)
        print(f"digest -> {md_path}")
        print(f"alerts -> {json_path}  (P1={evaluation['counts']['alerts_p1']} "
              f"P2={evaluation['counts']['alerts_p2']} suppressed={evaluation['counts']['suppressions_total']})")

    if args.fail_on_alert and evaluation["counts"]["alerts_p1"] > 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())