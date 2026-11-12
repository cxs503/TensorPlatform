"""loop_e2e —— 在**真实流量数据**上跑一次记忆回路四环，落可复算证据。

流程（每一步都打真实表的行号与时刻，禁止补时间线）::

    ① 写入   选一张真实失败单（tensor_dev_missions.status='failed' 且有 fail_stage/理由）
             → negative_knowledge_ledger 一行 + knowledge_base 一行（kb_key=lesson:m<id>）
    ② 注入   在该教训入账**之后**创建的真实同类单里取第一张
             → 走生产路径出包（build_package/render_record/sink_db/handoff）
             → knowledge_injections 行 + handoff/m<id>.md（决策键齐）
    ③ 命中   用该单**真实交付文本**（result 摘要 + tensor_reports.markdown）机械归因
             → knowledge_injections.citation_count/cited_at + knowledge_base.cited_count
    ④ 下游   记录「该教训被用在哪一步」（used_in_step / citation_ref）

诚实标注：``--phase`` 决定 citation_ref 前缀。
- ``pre-deploy``（默认）：后继单的提示词**尚未**含该块（后端钩子还没部署），
  命中是文本回溯，不是消费事实——时间线里如实写进 citation_ref。
- ``live``：钩子已在生产跑（合并部署后），命中即消费。

用法::

    python -m tools.context_package.loop_e2e --out runs/memory_loop_e2e
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

from . import loop
from .loader import ROOT
from .taxonomy import detect_class


def _rows(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or {})
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def pick_failure(conn, repo_like: str = "%"):
    rows = _rows(conn, """
        SELECT id, repo, goal, fail_stage, finished_at, result
        FROM tensor_dev_missions
        WHERE status = 'failed' AND finished_at IS NOT NULL AND repo LIKE %(r)s
        ORDER BY finished_at DESC LIMIT 40""", {"r": repo_like})
    for r in rows:
        sig = loop._failure_signals(r)
        if sig["failure_kind"] != "failed_no_signal" and len(str(r.get("goal") or "")) > 40:
            return r, sig
    return (rows[0], loop._failure_signals(rows[0])) if rows else (None, {})


def pick_next_same_class(conn, cls: str, after_ts: str, cfg=None):
    cfg = cfg or loop.load_config()
    rows = _rows(conn, """
        SELECT id, goal, repo, created_at, claimed_by, status, result, report_id
        FROM tensor_dev_missions
        WHERE created_at > %(ts)s::timestamptz AND status IN
              ('succeeded','awaiting_acceptance','failed','stopped','revoked')
        ORDER BY created_at ASC LIMIT 400""", {"ts": after_ts})
    for r in rows:
        c, _ = detect_class(r.get("goal"), cfg)
        if c == cls:
            return r
    return None


def deliverable_text(conn, row) -> str:
    parts = [json.dumps(row.get("result") or {}, ensure_ascii=False)]
    if row.get("report_id"):
        got = _rows(conn, "SELECT title, summary, markdown FROM tensor_reports WHERE id = %(i)s",
                    {"i": row["report_id"]})
        if got:
            parts.extend(str(got[0].get(k) or "") for k in ("title", "summary", "markdown"))
    return "\n".join(parts)


def run(out_dir: Path, repo_like: str = "TensorPlatform", phase: str = "pre-deploy") -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    conn, err = loop.connect()
    if conn is None:
        return {"degraded": f"NO_DB:{err}"}
    ev: dict = {"phase": phase, "started_at": loop._iso(loop._now())}
    try:
        # ① 写入：真实失败单 → 一条负面教训
        src, sig = pick_failure(conn, repo_like)
        ev["source"] = {"mission_id": src["id"], "repo": src["repo"],
                        "fail_stage": sig["failure_kind"],
                        "failure_reason": sig["failure_reason"],
                        "finished_at": loop._iso(src["finished_at"]),
                        "goal_head": str(src["goal"])[:120]}
        w = loop.write_lesson(src["id"], conn=conn, ledger_dir=out_dir)
        ev["ring1_write"] = w
        if not (w.get("written") or w.get("skipped") == "IDEMPOTENT"):
            ev["degraded"] = f"WRITE_FAILED:{w.get('degraded')}"
            return ev
        born = w.get("kb_created_at") or w.get("nkl_registered_at")
        # ② 注入：源失败单**完结之后**创建的真实同类单（pre-deploy 回溯：注入发生在
        # 该单跑完之后，故注入时刻 >> 该单 created_at，时间线如实呈现，不冒充同期）。
        nxt = pick_next_same_class(conn, w["task_class"],
                                   loop._iso(src["finished_at"]))
        if not nxt:
            ev["ring2_inject"] = {"degraded": "NO_SAME_CLASS_MISSION_AFTER_LESSON"}
            return ev
        ev["consumer_mission"] = {"mission_id": nxt["id"], "repo": nxt["repo"],
                                  "status": nxt["status"], "claimed_by": nxt["claimed_by"],
                                  "created_at": loop._iso(nxt["created_at"]),
                                  "goal_head": str(nxt["goal"])[:120]}
        block, info = loop.inject(nxt["id"], nxt["goal"], nxt["repo"] or "",
                                  consumer=str(nxt.get("claimed_by") or ""),
                                  ledger_dir=out_dir,
                                  pin=[w["entry_id"], f"kb:{w['kb_id']}"])
        ev["replay_note"] = ("pre-deploy：注入执行于该单跑完之后（钩子未部署），"
                             "注入时刻与单 created_at 的差即历史差额，非回路时延")
        ev["ring2_inject"] = {k: info.get(k) for k in
                              ("injected", "package_id", "task_class", "package_sha256",
                               "item_ids", "tokens", "handoff", "db_sink", "degraded")}
        ev["ring2_block"] = block
        # ③ 命中 + ④ 下游：用该单真实交付文本机械归因（阈值 min_terms 先声明=3）
        res = loop.cite(nxt["id"], deliverable_text(conn, nxt), conn=conn,
                        used_in_step="同上（pre-deploy 回溯）" if phase == "pre-deploy"
                        else "本单开工首条消息的干货包",
                        citation_ref=f"tensor_dev_missions#{nxt['id']}/{phase}",
                        ledger_dir=out_dir)
        ev["ring3_cite"] = {k: res.get(k) for k in
                            ("hits", "misses", "hit_rate", "cited_at", "skipped",
                             "min_terms", "degraded", "ledger")}
        ev["ring4_downstream"] = {"citation_line": loop.citation_line(res),
                                  "used_in_step": res.get("used_in_step"),
                                  "source_mission": src["id"]}
        ev["timeline"] = loop.timeline(src["id"], conn=conn)
        ev["coverage"] = loop.coverage(date.today().isoformat(), conn=conn)
        return ev
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.context_package.loop_e2e")
    ap.add_argument("--out", default="runs/memory_loop_e2e")
    ap.add_argument("--repo", default="TensorPlatform")
    ap.add_argument("--phase", default="pre-deploy", choices=["pre-deploy", "live"])
    a = ap.parse_args(argv)
    out = Path(a.out) if Path(a.out).is_absolute() else ROOT / a.out
    ev = run(out, a.repo, a.phase)
    ev["finished_at"] = loop._iso(loop._now())
    p = out / f"e2e_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    p.write_text(json.dumps(ev, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: ev.get(k) for k in ("phase", "source", "consumer_mission",
                                             "ring2_inject", "ring3_cite", "ring4_downstream",
                                             "coverage", "degraded")},
                     ensure_ascii=False, indent=2, default=str))
    print("evidence:", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())