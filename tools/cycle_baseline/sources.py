"""cycle_baseline.sources —— 取数层（只读）：Postgres 队列/报告证据 + git 落地时间索引。

为什么分两条证据线
------------------
* **队列/执行/报告** 在平台库里（mission 生命周期、领取事件、报告行）→ SQL。
* **落地（merge）** 在 git 历史里（merge commit 或 push 路径的 mission commit）→ git log。

两者都不改写任何表：本工具唯一写盘目标是 ``--out-json`` / ``--out-md`` / ``--figure-dir``。
传输：优先 ``TP_PG_PASSWORD`` 走 TCP，否则回落 ``su postgres -c psql``（本机 peer 认证）。
SQL 经 **stdin** 管道喂给 psql（不落临时文件）——踩坑记录：本机曾出现磁盘打满
（/tmp 42G、根分区 100%），``campaign_throughput._psql`` 的临时文件方案直接抛
"No usable temporary directory"；stdin 方案在零可用磁盘下仍可只读取数。
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
from typing import Any, Iterable, Optional, Sequence

from . import spec

WINDOW_DAYS = spec.DEFAULT_WINDOW_DAYS

_TERMINAL_LIST = "', '".join(spec.FALLBACK_TERMINAL)

#: 窗口锚点模板。**必须**用固定 asof 而不是 now()：否则「行级导出」与「SQL 独立聚合」
#: 两次查询之间种群会漂移（在飞单落终态），导致同一批数据算出的分位差 0.1%、n 差 1~2，
#: 对拍永远红（本节点第一次跑就是这样红的）。asof 作为参数登记在报告里，SQL 模板本身
#: 保持稳定 → 重跑同 asof 得到同 cohort、同 sha256。
BASE_CTE = f"""
WITH base AS (
  SELECT m.id AS mission_id, m.status, m.repo, m.claimed_by AS worker,
         m.created_at, m.finished_at,
         cl.claimed_at, rp.report_at,
         (SELECT n.plan_id FROM campaign_nodes n WHERE n.mission_id = m.id
           ORDER BY n.id LIMIT 1) AS plan_id,
         (SELECT n.name FROM campaign_nodes n WHERE n.mission_id = m.id
           ORDER BY n.id LIMIT 1) AS node,
         (SELECT n.planned_start FROM campaign_nodes n WHERE n.mission_id = m.id
           ORDER BY n.id LIMIT 1) AS planned_start,
         m.session_id, m.report_owed,
         (SELECT count(*) FROM tensor_reports t WHERE t.mission_id = m.id) AS n_reports,
         (SELECT count(*) FROM mission_lifecycle_log l WHERE l.mission_id = m.id
           AND l.action = 'pr_gate') AS n_pr_events,
         (SELECT count(*) FROM mission_lifecycle_log l WHERE l.mission_id = m.id
           AND l.action = 'pr_gate' AND l.detail->>'state' = 'merged') AS n_pr_merged
    FROM tensor_dev_missions m
    LEFT JOIN LATERAL (
      SELECT min(e.created_at) AS claimed_at
        FROM tensor_agent_session_events e
       WHERE e.session_id = m.session_id AND e.kind = 'note'
         AND e.payload->>'subtype' = 'mission_claimed') cl ON true
    LEFT JOIN LATERAL (
      SELECT min(t.created_at) AS report_at
        FROM tensor_reports t WHERE t.mission_id = m.id) rp ON true
   WHERE m.status IN ('{_TERMINAL_LIST}')
     AND m.created_at >= timestamp with time zone '{{asof}}' - interval '{{days}} days'
     AND m.created_at <  timestamp with time zone '{{asof}}'
)"""

SQL_COHORT = BASE_CTE + """
SELECT json_build_object(
  'mission_id', mission_id, 'status', status, 'repo', repo, 'worker', worker,
  'created_at', created_at, 'finished_at', finished_at,
  'claimed_at', claimed_at, 'report_at', report_at,
  'plan_id', plan_id, 'node', node, 'planned_start', planned_start,
  'session_id', session_id, 'report_owed', report_owed, 'n_reports', n_reports,
  'n_pr_events', n_pr_events, 'n_pr_merged', n_pr_merged)::text
FROM base ORDER BY mission_id"""

#: 独立 SQL 聚合（percentile_cont）：与 Python 统计层逐项对拍，防"两套统计口径各说各话"
SQL_LEDGER = BASE_CTE + """,
segs AS (
  SELECT mission_id, created_at, finished_at,
         EXTRACT(EPOCH FROM (claimed_at - created_at))  AS t_queue,
         EXTRACT(EPOCH FROM (finished_at - claimed_at)) AS t_exec,
         EXTRACT(EPOCH FROM (report_at - finished_at))  AS t_report_raw,
         CASE WHEN report_at IS NULL OR finished_at IS NULL THEN NULL
              ELSE GREATEST(EXTRACT(EPOCH FROM (report_at - finished_at)), 0)
          END AS t_report,
         EXTRACT(EPOCH FROM (finished_at - created_at)) AS cycle_s
    FROM base)
SELECT json_build_object(
  'cohort_n', (SELECT count(*) FROM segs),
  'asof', '{asof}', 'window_days', {days},
  't_queue', json_build_object(
      'n', count(t_queue),
      'p50', percentile_cont(0.5) WITHIN GROUP (ORDER BY t_queue),
      'p90', percentile_cont(0.9) WITHIN GROUP (ORDER BY t_queue),
      'p99', percentile_cont(0.99) WITHIN GROUP (ORDER BY t_queue)),
  't_exec', json_build_object(
      'n', count(t_exec),
      'p50', percentile_cont(0.5) WITHIN GROUP (ORDER BY t_exec),
      'p90', percentile_cont(0.9) WITHIN GROUP (ORDER BY t_exec),
      'p99', percentile_cont(0.99) WITHIN GROUP (ORDER BY t_exec)),
  't_report', json_build_object(
      'n', count(t_report),
      'p50', percentile_cont(0.5) WITHIN GROUP (ORDER BY t_report),
      'p90', percentile_cont(0.9) WITHIN GROUP (ORDER BY t_report),
      'p99', percentile_cont(0.99) WITHIN GROUP (ORDER BY t_report),
      'p50_raw', percentile_cont(0.5) WITHIN GROUP (ORDER BY t_report_raw),
      'negative_n', count(*) FILTER (WHERE t_report_raw < 0)),
  'cycle_s', json_build_object(
      'n', count(cycle_s),
      'p50', percentile_cont(0.5) WITHIN GROUP (ORDER BY cycle_s),
      'p90', percentile_cont(0.9) WITHIN GROUP (ORDER BY cycle_s),
      'p99', percentile_cont(0.99) WITHIN GROUP (ORDER BY cycle_s)),
  't_merge_note', 'merge_at 不在库内：由 git 落地索引提供（见 MISSION_ID_RE / git log）'
  )::text FROM segs"""

#: 归因表用 SQL（终态 × repo 的计数与周期分位）：与 Python 归因表对拍 n 与 cycle_p50
SQL_ATTRIBUTION = BASE_CTE + """,
segs AS (
  SELECT status, repo,
         EXTRACT(EPOCH FROM (finished_at - created_at)) AS cycle_s FROM base)
SELECT json_build_object('status', status, 'repo', repo, 'n', count(*),
  'cycle_p50', percentile_cont(0.5) WITHIN GROUP (ORDER BY cycle_s))::text
FROM segs GROUP BY status, repo ORDER BY status, repo"""

#: git 侧 mission id 反查（落地时间索引）。三种历史写法都认：
#:   "Merge #NNNN (m1234) via merge-master" / "m1234: ..." / 分支名 agent/m1234
GIT_LOG_CMD = "git -C '{repo}' log --all --since='{since}' --format='%ct %H %s'"
MISSION_ID_RE = re.compile(r"\(m(\d+)\)|agent/m(\d+)|^m(\d+):")


def render_sql(tmpl: str, asof: str, window_days: int = WINDOW_DAYS) -> str:
    """把窗口锚点填进模板（asof 为 ISO-8601 UTC 字符串）。"""
    return tmpl.replace("{asof}", asof).replace("{days}", str(int(window_days)))


def sql_for_segment(asof: str, window_days: int = WINDOW_DAYS) -> dict[str, str]:
    """证据链登记用的 SQL 原文（已填入本次 asof/window，行内可直接粘贴执行）。"""
    return {
        "t_queue": render_sql(SQL_LEDGER, asof, window_days),
        "t_exec": render_sql(SQL_LEDGER, asof, window_days),
        "t_merge": render_sql(SQL_COHORT, asof, window_days)
                   + "\n-- merge_at: 由 git 落地索引补齐（见 sources.git_merge_index）",
        "t_report": render_sql(SQL_LEDGER, asof, window_days),
        "cycle_s": render_sql(SQL_LEDGER, asof, window_days),
        "attribution": render_sql(SQL_ATTRIBUTION, asof, window_days),
    }



def psql(sql: str, db: str = "", timeout: int = 600) -> str:
    """只读执行 SQL（stdin 管道，无临时文件）；失败即抛，绝不返回空串冒充结果。"""
    db = db or os.environ.get("TP_PG_DB", "tensorplatform")
    pwd = os.environ.get("TP_PG_PASSWORD", "")
    if pwd:
        env = dict(os.environ, PGPASSWORD=pwd)
        cmd = ["psql", "-h", os.environ.get("TP_PG_HOST", "127.0.0.1"),
               "-U", os.environ.get("TP_PG_USER", "foundation"), "-d", db, "-tA", "-v", "ON_ERROR_STOP=1", "-f", "-"]
        proc = subprocess.run(cmd, input=sql, capture_output=True, text=True,
                              env=env, timeout=timeout)
        transport = "tcp"
    else:
        proc = subprocess.run(["su", "postgres", "-c", f"psql -d {db} -tA -v ON_ERROR_STOP=1 -f -"],
                              input=sql, capture_output=True, text=True, timeout=timeout)
        transport = "peer"
    if proc.returncode != 0:
        raise RuntimeError(f"psql({transport}) failed: {(proc.stderr or '')[:400]}")
    return proc.stdout



SNAPSHOT_MARKS = ("@@LEDGER", "@@ATTR", "@@COHORT")
SNAPSHOT_END = "@@END"


def fetch_snapshot(asof: str, window_days: int = WINDOW_DAYS,
                   db: str = "") -> dict[str, Any]:
    """**单事务快照**内取齐三批证据（行级导出 + SQL 聚合 + 归因聚合）。

    为什么必须同快照：窗口按 created_at 锚定，但窗口内仍有"asof 时在飞、之后才终态"的
    mission，其 claimed_at/finished_at 在两次独立查询之间会变。实测两次查询差 1 条记录 →
    n 差 0.017%、p50 差 1e-4，对拍永远红。REPEATABLE READ 单事务把它们锁到同一快照上。

    输出按 ``\echo`` 标记切段，纯文本解析（不依赖 psql 的 JSON 输出模式）。
    """
    sql = (
        "BEGIN ISOLATION LEVEL REPEATABLE READ;\n"
        f"\\echo {SNAPSHOT_MARKS[0]}\n"
        + render_sql(SQL_LEDGER, asof, window_days) + ";\n"
        f"\\echo {SNAPSHOT_MARKS[1]}\n"
        + render_sql(SQL_ATTRIBUTION, asof, window_days) + ";\n"
        f"\\echo {SNAPSHOT_MARKS[2]}\n"
        + render_sql(SQL_COHORT, asof, window_days) + ";\n"
        f"\\echo {SNAPSHOT_END}\n"
        "COMMIT;\n"
    )
    out = psql(sql, db)
    parts: dict[str, list[str]] = {m: [] for m in SNAPSHOT_MARKS}
    cur: Optional[str] = None
    for line in out.splitlines():
        line = line.strip()
        if line == SNAPSHOT_END:
            break
        if line in parts:
            cur = line
            continue
        if cur and line:
            parts[cur].append(line)

    def _jsonl(lines: list[str]) -> list[dict]:
        return [json.loads(x) for x in lines]

    ledger_lines = parts[SNAPSHOT_MARKS[0]]
    if not ledger_lines:
        raise RuntimeError("snapshot: SQL_LEDGER produced no rows")
    return {
        "asof": asof,
        "window_days": window_days,
        "ledger_sql": json.loads(ledger_lines[0]),
        "attribution_sql": _jsonl(parts[SNAPSHOT_MARKS[1]]),
        "cohort": _jsonl(parts[SNAPSHOT_MARKS[2]]),
    }


def fetch_rows(sql: str, db: str = "") -> list[dict[str, Any]]:
    rows = []
    for line in psql(sql, db).splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def fetch_cohort(asof: str, window_days: int = WINDOW_DAYS, db: str = "") -> list[dict[str, Any]]:
    return fetch_rows(render_sql(SQL_COHORT, asof, window_days), db)


def fetch_ledger_sql(asof: str, window_days: int = WINDOW_DAYS, db: str = "") -> dict[str, Any]:
    out = psql(render_sql(SQL_LEDGER, asof, window_days), db).strip().splitlines()
    if not out:
        raise RuntimeError("SQL_LEDGER returned no rows (cohort empty or query broken)")
    return json.loads(out[0])


def fetch_attribution_sql(asof: str, window_days: int = WINDOW_DAYS,
                          db: str = "") -> list[dict[str, Any]]:
    return fetch_rows(render_sql(SQL_ATTRIBUTION, asof, window_days), db)


# ── git 落地索引 ────────────────────────────────────────────────────────
def git_merge_index(repos_root: str, since: str = "60 days ago",
                    repos: Optional[Sequence[str]] = None,
                    timeout: int = 240) -> dict[int, dict[str, Any]]:
    """mission_id → 最早落地记录 {merge_ct, sha, repo, subject}。

    只读 git（不 checkout、不 fetch）；仓库缺失/命令失败只跳过并计数，绝不静默当 0。
    """
    root = pathlib.Path(repos_root)
    index: dict[int, dict[str, Any]] = {}
    stats = {"repos_scanned": 0, "repos_failed": 0, "commits_scanned": 0,
             "missions_matched": 0}
    if not root.is_dir():
        return {"index": index, "stats": {**stats, "repos_failed": -1}}
    dirs = sorted(p for p in root.iterdir() if p.is_dir())
    wanted = {r.lower() for r in repos} if repos else None
    for d in dirs:
        if wanted and d.name.lower() not in wanted:
            continue
        if not (d / ".git").exists():
            stats["repos_failed"] += 1
            continue
        cmd = GIT_LOG_CMD.format(repo=str(d), since=since)
        try:
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            stats["repos_failed"] += 1
            continue
        if proc.returncode != 0:
            stats["repos_failed"] += 1
            continue
        stats["repos_scanned"] += 1
        for line in proc.stdout.splitlines():
            parts = line.split(" ", 2)
            if len(parts) < 3:
                continue
            stats["commits_scanned"] += 1
            try:
                ct = int(parts[0])
            except ValueError:
                continue
            sha, subject = parts[1], parts[2]
            m = MISSION_ID_RE.search(subject)
            if not m:
                continue
            mid = int(m.group(1) or m.group(2) or m.group(3))
            cur = index.get(mid)
            if cur is None or ct < cur["merge_ct"]:
                index[mid] = {"merge_ct": ct, "sha": sha, "repo": d.name,
                              "subject": subject[:160]}
    stats["missions_matched"] = len(index)
    return {"index": index, "stats": stats}


def merge_index_from_fixture(path: str | os.PathLike) -> dict[int, dict[str, Any]]:
    raw = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    return {int(k): v for k, v in raw.items()}


def attach_merge_times(missions: Iterable[dict[str, Any]],
                       index: dict[int, dict[str, Any]]) -> dict[str, int]:
    """把 git 落地时间戳写回 mission 行（``merge_at`` ISO 字符串）；返回命中统计。"""
    from .segments import parse_ts  # 局部导入避免循环
    from datetime import datetime, timezone
    hit = 0
    for m in missions:
        rec = index.get(int(m.get("mission_id") or 0))
        if not rec:
            continue
        m["merge_at"] = datetime.fromtimestamp(rec["merge_ct"], tz=timezone.utc).isoformat()
        m["merge_sha"] = rec["sha"]
        hit += 1
    return {"merge_evidence_matched": hit}