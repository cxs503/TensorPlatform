"""账本单测：InMemory 语义 + PG 迁移幂等（真 PG 可用时）。"""
import subprocess

import pytest

from tools.compute_queue.ledger import (
    InMemoryLedger, PgLedger, MIGRATION_SQL, sql_lit)
from tools.compute_queue.scheduler import JobSpec, LOCAL_ID


def test_inmemory_lifecycle():
    L = InMemoryLedger()
    jid = L.insert_job(JobSpec(cmd="x", repo="R", cores=4))
    assert L.get(jid)["state"] == "queued"
    L.mark_running(jid, "n315", 12.5)
    assert L.get(jid)["state"] == "running"
    assert L.get(jid)["placement"] == "n315"
    assert L.get(jid)["wait_s"] == 12.5
    assert L.node_running("n315") == (4, 1)
    L.mark_finished(jid, "succeeded", 0, 3.25, note="local:taskset_nice")
    g = L.get(jid)
    assert g["state"] == "succeeded" and g["rc"] == 0
    assert g["cpu_seconds"] == 3.25
    assert L.node_running("n315") == (0, 0)


def test_inmemory_local_cap_accounting():
    L = InMemoryLedger()
    a = L.insert_job(JobSpec(cmd="x", repo="R", cores=8))
    b = L.insert_job(JobSpec(cmd="y", repo="R", cores=16))
    L.mark_running(a, LOCAL_ID, 0)
    L.mark_running(b, LOCAL_ID, 0)
    assert L.local_running() == (24, 2)
    L.kill(a)
    assert L.local_running() == (16, 1)
    assert L.get(a)["state"] == "killed"


def test_inmemory_fifo():
    L = InMemoryLedger()
    ids = [L.insert_job(JobSpec(cmd=str(i), repo="R")) for i in range(5)]
    q = L.queued_jobs(3)
    assert [r["id"] for r in q] == ids[:3]


def test_sql_lit_escapes():
    assert sql_lit("a'b") == "'a''b'"
    assert sql_lit("plain") == "'plain'"


def _pg_available():
    r = subprocess.run(["/usr/sbin/runuser", "-u", "postgres", "--", "psql",
                        "-d", "postgres", "-tA", "-c", "select 1"],
                       capture_output=True, text=True,
                       env={"PATH": "/usr/sbin:/usr/bin:/bin"})
    return r.returncode == 0


@pytest.mark.skipif(not _pg_available(), reason="PG 不可用")
def test_pg_migration_idempotent():
    """迁移 SQL 连跑两遍不报错（IF NOT EXISTS）；新列存在。"""
    db = "cq_mig_test"
    subprocess.run(["/usr/sbin/runuser", "-u", "postgres", "--", "psql", "-d", "postgres",
                      "-c", "DROP DATABASE IF EXISTS %s" % db],
                   capture_output=True, env={"PATH": "/usr/sbin:/usr/bin:/bin"})
    subprocess.run(["/usr/sbin/runuser", "-u", "postgres", "--", "psql", "-d", "postgres",
                    "-c", "CREATE DATABASE %s" % db],
                   capture_output=True, env={"PATH": "/usr/sbin:/usr/bin:/bin"})
    subprocess.run(["/usr/sbin/runuser", "-u", "postgres", "--", "psql", "-d", db, "-c",
                    "CREATE TABLE cluster_jobs (id serial primary key, "
                    "repo text not null, ref text default 'main', "
                    "mode text default 'run', cmd text not null, "
                    "cmd_tpl text, shards integer, stage_dir text, "
                    "timeout integer default 3600, cores integer, "
                    "mid bigint, submitter text, state text default 'queued', "
                    "node text, attempts integer default 0, rc integer, "
                    "log_path text, note text, "
                    "submitted_at timestamptz default now(), "
                    "started_at timestamptz, finished_at timestamptz)"],
                   capture_output=True, env={"PATH": "/usr/sbin:/usr/bin:/bin"})
    L = PgLedger(db=db)
    L.migrate()
    L.migrate()  # 幂等
    jid = L.insert_job(JobSpec(cmd="x", repo="R", cores=4))
    L.mark_running(jid, "n315", 1.5)
    L.mark_finished(jid, "succeeded", 0, 2.5, note="t")
    g = L.get(jid)
    assert g["placement"] == "n315" and g["wait_s"] == 1.5
    assert g["cpu_seconds"] == 2.5 and g["state"] == "succeeded"
    subprocess.run(["/usr/sbin/runuser", "-u", "postgres", "--", "psql", "-d", "postgres",
                    "-c", "DROP DATABASE IF EXISTS %s" % db],
                   capture_output=True, env={"PATH": "/usr/sbin:/usr/bin:/bin"})
