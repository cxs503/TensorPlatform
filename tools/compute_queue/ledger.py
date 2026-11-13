"""compute_queue 账本（v1）—— cluster_jobs 扩展 + 双实现（PG / 内存）。

验收标准 ④：cluster_jobs 扩展 placement / cpu_seconds / wait_s 字段
（+ sched 标记列：新旧调度器分账，cutover 前双跑不互抢）。
向后兼容 ⑤：旧列语义不变，旧 cluster_queue.py 读写不受影响（只增不改）。
"""
from __future__ import annotations

import os
import subprocess
import time
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

from .scheduler import JobSpec

MIGRATION_SQL = """
ALTER TABLE cluster_jobs ADD COLUMN IF NOT EXISTS placement text;
ALTER TABLE cluster_jobs ADD COLUMN IF NOT EXISTS cpu_seconds double precision;
ALTER TABLE cluster_jobs ADD COLUMN IF NOT EXISTS wait_s double precision;
ALTER TABLE cluster_jobs ADD COLUMN IF NOT EXISTS sched text NOT NULL DEFAULT 'cluster_queue';
"""

PGENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}


def sql_lit(s) -> str:
    return "'" + str(s).replace("'", "''") + "'"


class Ledger(ABC):
    @abstractmethod
    def insert_job(self, spec: JobSpec) -> int: ...
    @abstractmethod
    def mark_running(self, jid: int, placement: str, wait_s: float): ...
    @abstractmethod
    def mark_finished(self, jid: int, state: str, rc: Optional[int],
                      cpu_seconds: Optional[float], note: str = ""): ...
    @abstractmethod
    def queued_jobs(self, limit: int = 8) -> List[dict]: ...
    @abstractmethod
    def node_running(self, node: str) -> Tuple[int, int]: ...
    @abstractmethod
    def local_running(self) -> Tuple[int, int]: ...
    @abstractmethod
    def get(self, jid: int) -> Optional[dict]: ...
    @abstractmethod
    def kill(self, jid: int): ...


class InMemoryLedger(Ledger):
    """测试/HTTP 演示用内存账本（语义与 PG 版一致）。"""

    def __init__(self):
        self.rows: dict = {}
        self._seq = 0

    def insert_job(self, spec: JobSpec) -> int:
        self._seq += 1
        self.rows[self._seq] = {
            "id": self._seq, "repo": spec.repo, "ref": spec.ref,
            "mode": spec.mode, "cmd": spec.effective_cmd,
            "cmd_tpl": spec.cmd_tpl, "shards": spec.shards or None,
            "workdir": spec.workdir, "cores": spec.cores,
            "mem_g": spec.mem_g, "timeout": spec.timeout,
            "submitter": spec.submitter, "state": "queued",
            "node": None, "placement": None, "rc": None,
            "cpu_seconds": None, "wait_s": None, "sched": "compute_queue",
            "attempts": 0, "note": "",
            "submitted_ts": time.time(),
        }
        return self._seq

    def mark_running(self, jid, placement, wait_s):
        r = self.rows[jid]
        r.update(state="running", node=placement, placement=placement,
                  wait_s=wait_s, attempts=r["attempts"] + 1)

    def mark_finished(self, jid, state, rc, cpu_seconds, note=""):
        r = self.rows[jid]
        r.update(state=state, rc=rc, cpu_seconds=cpu_seconds, note=note)

    def queued_jobs(self, limit=8):
        rows = [r for r in self.rows.values() if r["state"] == "queued"]
        return sorted(rows, key=lambda r: r["id"])[:limit]

    def _running_on(self, node):
        return [r for r in self.rows.values()
                if r["state"] == "running" and r["node"] == node]

    def node_running(self, node):
        rs = self._running_on(node)
        return (sum(int(r["cores"] or 1) for r in rs), len(rs))

    def local_running(self):
        return self.node_running("local-99")

    def get(self, jid):
        return self.rows.get(int(jid))

    def kill(self, jid):
        r = self.rows.get(int(jid))
        if r and r["state"] in ("queued", "running"):
            r.update(state="killed", note="user-kill")


class PgLedger(Ledger):
    """PG 账本（cluster_jobs 表；psql -tA 管道，与 cluster_queue.py 同构）。"""

    def __init__(self, db: Optional[str] = None):
        self.db = db or os.environ.get("CQ_PGDB", "tensorplatform")

    def q(self, sql):
        r = subprocess.run(
            ["runuser", "-u", "postgres", "--", "psql", "-d", self.db,
             "-tA", "-F", "\x01", "-R", "\x02", "-c", sql],
            capture_output=True, text=True, env=PGENV)
        if r.returncode != 0:
            raise RuntimeError("psql: " + r.stderr.strip()[:300])
        return [rec.split("\x01") for rec in r.stdout.strip().split("\x02")
                if rec.strip()]

    def migrate(self):
        for stmt in [s.strip() for s in MIGRATION_SQL.split(";") if s.strip()]:
            self.q(stmt)

    def insert_job(self, spec: JobSpec) -> int:
        cols = ("repo, ref, mode, cmd, cmd_tpl, shards, stage_dir, timeout, "
                "cores, submitter, sched")
        vals = "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s" % (
            sql_lit(spec.repo or ""), sql_lit(spec.ref), sql_lit(spec.mode),
            sql_lit(spec.effective_cmd), sql_lit(spec.cmd_tpl or ""),
            str(spec.shards) if spec.shards else "NULL",
            sql_lit(spec.workdir or ""), str(int(spec.timeout or 3600)),
            str(int(spec.cores or 1)), sql_lit(spec.submitter),
            sql_lit("compute_queue"))
        row = self.q("INSERT INTO cluster_jobs (%s) VALUES (%s) RETURNING id"
                      % (cols, vals))[0]
        # psql 会在 RETURNING 行后附命令标签（INSERT 0 1），取首 token
        return int(row[0].split()[0])

    def mark_running(self, jid, placement, wait_s):
        self.q("UPDATE cluster_jobs SET state='running', node=%s, placement=%s, "
               "wait_s=%s, attempts=attempts+1, started_at=now() "
               "WHERE id=%s AND state='queued'"
               % (sql_lit(placement), sql_lit(placement),
                  "%.3f" % float(wait_s), int(jid)))

    def mark_finished(self, jid, state, rc, cpu_seconds, note=""):
        self.q("UPDATE cluster_jobs SET state=%s, rc=%s, cpu_seconds=%s, "
               "finished_at=now(), note=%s WHERE id=%s"
               % (sql_lit(state),
                  "NULL" if rc is None else str(int(rc)),
                  "NULL" if cpu_seconds is None else "%.3f" % float(cpu_seconds),
                  sql_lit(note), int(jid)))

    def queued_jobs(self, limit=8):
        rows = self.q("SELECT id, repo, ref, mode, cmd, coalesce(cmd_tpl,''), "
                      "coalesce(shards,0), coalesce(stage_dir,''), "
                      "coalesce(cores,1), coalesce(timeout,3600), "
                      "extract(epoch FROM submitted_at) "
                      "FROM cluster_jobs WHERE state='queued' "
                      "ORDER BY id LIMIT %d" % int(limit))
        return [{"id": int(r[0]), "repo": r[1], "ref": r[2], "mode": r[3],
                 "cmd": r[4], "cmd_tpl": r[5], "shards": int(r[6]),
                 "workdir": r[7], "cores": int(r[8]), "timeout": int(r[9]),
                 "submitted_ts": float(r[10])} for r in rows]

    def _running_agg(self, node_sql):
        row = self.q("SELECT coalesce(sum(coalesce(cores,1)),0), count(*) "
                     "FROM cluster_jobs WHERE state='running' AND node=%s"
                     % node_sql)[0]
        return (int(float(row[0] or 0)), int(row[1]))

    def node_running(self, node):
        return self._running_agg(sql_lit(node))

    def local_running(self):
        return self._running_agg(sql_lit("local-99"))

    def get(self, jid):
        rows = self.q("SELECT id, state, coalesce(node,''), coalesce(placement,''), "
                      "coalesce(rc,-1), coalesce(cpu_seconds,-1), "
                      "coalesce(wait_s,-1), coalesce(note,'') "
                      "FROM cluster_jobs WHERE id=%s" % int(jid))
        if not rows:
            return None
        r = rows[0]
        return {"id": int(r[0]), "state": r[1], "node": r[2],
                "placement": r[3], "rc": int(r[4]),
                "cpu_seconds": None if float(r[5]) < 0 else float(r[5]),
                "wait_s": None if float(r[6]) < 0 else float(r[6]),
                "note": r[7]}

    def kill(self, jid):
        self.q("UPDATE cluster_jobs SET state='killed', finished_at=now(), "
               "note='user-kill' WHERE id=%s AND state IN ('queued','running')"
               % int(jid))
