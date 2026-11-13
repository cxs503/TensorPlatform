#!/usr/bin/env python3
"""compute_gate.ledger —— 违例/迁移账本 + cluster_jobs 注记 + local vs queued 用量回执。

双写纪律（审计要能离线读）：事件永远追加 JSONL（events_path）；PG 可用时再插
compute_gate_events 表（失败不阻塞动作，只在事件里记 pg_error）。
"""
import json
import os
import pathlib
import subprocess
import time

PGENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS compute_gate_events (
  id bigserial PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now(),
  mission_id bigint,
  kind text NOT NULL,              -- hog_detected | migrated | migrate_failed | preflight_block | resumed
  pid int, tree_pids int, tree_root_key text,
  cpu_seconds double precision, cores_now double precision, rss_mb double precision,
  cwd text, cmd text,
  jid text, note text,
  detail jsonb
);
"""


def _psql(sql, timeout=20):
    return subprocess.run(
        ["runuser", "-u", "postgres", "--", "psql", "-d", "tensorplatform", "-tA", "-v", "ON_ERROR_STOP=1",
         "-c", sql],
        capture_output=True, text=True, env=PGENV, timeout=timeout)


def _lit(v):
    if v is None:
        return "NULL"
    return "'" + str(v).replace("'", "''") + "'"


def _num(v):
    return "NULL" if v is None else repr(float(v))


class Ledger:
    def __init__(self, cfg):
        self.cfg = cfg
        self.events_path = pathlib.Path(cfg["events_path"])
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        self.pg_ok = self._ensure_table()

    # ── 建表 ───────────────────────────────────────────────────────
    def _ensure_table(self):
        try:
            r = _psql(CREATE_TABLE_SQL)
            return r.returncode == 0
        except Exception:
            return False

    # ── 事件 ───────────────────────────────────────────────────────
    def record(self, kind, mission_id=None, pid=None, tree_pids=0, tree_root_key=None,
               cpu_seconds=None, cores_now=None, rss_mb=None, cwd=None, cmd=None,
               jid=None, note=None, detail=None):
        ev = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "kind": kind, "mission_id": mission_id, "pid": pid,
            "tree_pids": tree_pids, "tree_root_key": tree_root_key,
            "cpu_seconds": None if cpu_seconds is None else round(float(cpu_seconds), 1),
            "cores_now": None if cores_now is None else round(float(cores_now), 2),
            "rss_mb": None if rss_mb is None else round(float(rss_mb), 1),
            "cwd": cwd, "cmd": (cmd or "")[:4000], "jid": jid, "note": note,
            "detail": detail or {},
        }
        with open(self.events_path, "a") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        if self.pg_ok:
            sql = ("INSERT INTO compute_gate_events (mission_id,kind,pid,tree_pids,tree_root_key,"
                   "cpu_seconds,cores_now,rss_mb,cwd,cmd,jid,note,detail) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)" % (
                       "NULL" if mission_id is None else str(int(mission_id)),
                       _lit(kind), "NULL" if pid is None else str(int(pid)), str(int(tree_pids or 0)),
                       _lit(tree_root_key), _num(cpu_seconds), _num(cores_now), _num(rss_mb),
                       _lit(cwd), _lit((cmd or "")[:4000]), _lit(jid), _lit(note),
                       _lit(json.dumps(detail or {}, ensure_ascii=False))))
            try:
                r = _psql(sql)
                if r.returncode != 0:
                    ev["pg_error"] = r.stderr.strip()[:200]
            except Exception as e:
                ev["pg_error"] = "%s: %s" % (type(e).__name__, e)
        return ev

    # ── 作业注记 ───────────────────────────────────────────────────
    def annotate_job(self, jid, note):
        """在 cluster_jobs 上贴注记（「违例待迁移」/「已迁移」）。"""
        if not jid or not str(jid).startswith("cj"):
            return False
        sql = ("UPDATE cluster_jobs SET note = coalesce(note||' | ','') || %s "
               "WHERE 'cj'||id = %s" % (_lit(note), _lit(jid)))
        try:
            return _psql(sql).returncode == 0
        except Exception:
            return False

    # ── 用量回执 ───────────────────────────────────────────────────
    def usage(self):
        """如实统计 local vs queued 用量（JSONL 为真相源；PG 只做补充统计）。"""
        local_hogs, local_cpu_s, queued = 0, 0.0, []
        if self.events_path.exists():
            for line in self.events_path.read_text().splitlines():
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get("kind") == "hog_detected":
                    local_hogs += 1
                    local_cpu_s += float(ev.get("cpu_seconds") or 0)
        if self.pg_ok:
            try:
                r = _psql("SELECT 'cj'||id, state, coalesce(round(extract(epoch FROM "
                          "coalesce(finished_at,now())-submitted_at)/60.0,1), 0), "
                          "coalesce(cores,1), state FROM cluster_jobs "
                          "WHERE submitter IN ('agent','agent-run_compute','local-hog-migration') "
                          "AND submitted_at > now()-interval '2 days' ORDER BY id DESC LIMIT 200")
                if r.returncode == 0:
                    for ln in r.stdout.strip().splitlines():
                        f = ln.split("|")
                        if len(f) >= 4:
                            queued.append({"jid": f[0], "state": f[1],
                                           "elapsed_min": float(f[2] or 0),
                                           "cores": int(f[3] or 1)})
            except Exception:
                pass
        in_flight = [j for j in queued if j["state"] in ("queued", "running")]
        core_min_queued = sum(j["elapsed_min"] * j["cores"] for j in queued)
        return {
            "local": {"hog_events": local_hogs,
                      "cpu_min_consumed_before_intercept": round(local_cpu_s / 60.0, 1),
                      "policy": "本机只准 <8 核 <10min 短测试；超限由 guard 拦截并迁移"},
            "queued": {"jobs_seen_last_2d": len(queued),
                       "jobs_in_flight": len(in_flight),
                       "core_min_occupied_last_2d": round(core_min_queued, 1),
                       "recent": queued[:5]},
            "source": {"events_jsonl": str(self.events_path),
                       "pg_table": "compute_gate_events" if self.pg_ok else None},
        }