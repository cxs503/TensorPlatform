"""compute_queue —— 统一计算调度系统 v1（提交/裁决/执行三层分离）。

owner 2026-11-13 计算调度令：「agent 计算提交给调度系统，跑在哪里是调度系统的事」。

提交面（双入口，永不说跑哪）：
  CLI   compute_queue.py submit --cmd "..." --repo R [--ref main]
                            | --pytest "tests/test_x.py::t"
                            | --cmd-tpl "...{i}/{n}..." --shards 16
                            [--workdir D] [--cores 8] [--mem-g 16] [--timeout 3600]
  HTTP  POST /compute/submit  {cmd|pytest|cmd_tpl+shards, repo, ref, workdir,
                               cores, mem_g, timeout}
  传 --node / body 带 node → REFUSED（HPC 语义，结构性拒绝）。

裁决面：scheduler.decide_placement（统一负载表 n315/n330/local-99；
  local 只给 <=8 核小作业、总帽 24 核、控制面保留；节点优先；无容量=排队）。

执行面：local-99（cgroup v2→v1→taskset+nice 硬限）+ 双节点 ssh（复用 cluster_exec）。

账本：cluster_jobs 扩展 placement/cpu_seconds/wait_s/sched（ledger.migrate）。

用法：
  submit / ps [--jid] / kill --jid / fetch --jid / nodes
  dispatch-once     # 单周期（冒烟/机器门用）
  serve --port 8099 # HTTP 入口
  --sched           # 常驻调度器（cutover 时替换 cluster_queue --sched）
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

from tools.compute_queue.scheduler import (  # noqa: E402
    JobSpec, HostInfo, Policy, Placement, LOCAL_ID, decide_placement)
from tools.compute_queue.ledger import (  # noqa: E402
    Ledger, PgLedger, InMemoryLedger, MIGRATION_SQL, sql_lit)
from tools.compute_queue.runners import (  # noqa: E402
    LocalRunner, NodeRunner, pick_limiter, CEXEC, CLUSTER_DIR)

PGENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
POLL = 30
JOBLOG = CLUSTER_DIR / "cq_jobs"
LOGF = CLUSTER_DIR / "cq_queue.log"


def log(msg: str):
    try:
        with open(LOGF, "a") as f:
            f.write("[%s] %s\n" % (time.strftime("%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def probe_nodes(cexec: str = CEXEC) -> dict:
    """ssh 探测双节点（复用 cluster_exec nodes）→ {id: {ok, loadr, cores, mem_free_g}}。"""
    out = {}
    try:
        r = subprocess.run([sys.executable, cexec, "nodes"],
                           capture_output=True, text=True, timeout=120, env=PGENV)
    except Exception:
        return out
    for ln in r.stdout.strip().splitlines():
        try:
            d = json.loads(ln)
        except Exception:
            continue
        e = {"ok": bool(d.get("ok"))}
        info = d.get("info", "") or ""
        if e["ok"]:
            try:
                e["loadr"] = float(info.split("load=")[1].split()[0]) / \
                    int(info.split("cores=")[1].split()[0])
                e["cores"] = int(info.split("cores=")[1].split()[0])
                e["mem_free_g"] = int(info.split("mem_free_G=")[1].split()[0])
            except Exception:
                e["ok"] = False
        if d.get("id"):
            out[d["id"]] = e
    return out


def probe_local(ledger: Optional[Ledger] = None) -> HostInfo:
    """本机 99 探测：/proc/loadavg + nproc + MemAvailable + 账本 local 在跑核数。"""
    try:
        load = float(open("/proc/loadavg").read().split()[0])
        cores = os.cpu_count() or 1
        mem_free_g = 0
        for ln in open("/proc/meminfo"):
            if ln.startswith("MemAvailable:"):
                mem_free_g = int(ln.split()[1]) // (1024 * 1024)
                break
        used, njobs = (0, 0)
        if ledger is not None:
            used, njobs = ledger.local_running()
        return HostInfo(LOCAL_ID, ok=True, loadr=load / cores, cores=cores,
                        mem_free_g=mem_free_g, used_cores=used,
                        running_jobs=njobs)
    except Exception:
        return HostInfo(LOCAL_ID, ok=False)


class ComputeQueue:
    """三层装配：ledger（账本）+ policy（裁决）+ runners（执行）。"""

    def __init__(self, ledger: Ledger, policy: Optional[Policy] = None,
                 node_probe=None, local_probe=None,
                 local_runner: Optional[LocalRunner] = None,
                 node_runner: Optional[NodeRunner] = None):
        self.ledger = ledger
        self.policy = policy or Policy()
        self.node_probe = node_probe or probe_nodes
        self.local_probe = local_probe or (lambda: probe_local(ledger))
        self.local_runner = local_runner or LocalRunner()
        self.node_runner = node_runner or NodeRunner()
        self.popens = {}   # jid -> Popen（local 型）
        self._now = time.time

    # ── 提交面 ────────────────────────────────────────────────────────
    def submit(self, spec: JobSpec) -> dict:
        err = spec.validate()
        if err:
            return {"error": "REFUSED: " + err}
        jid = self.ledger.insert_job(spec)
        ahead = len([r for r in self.ledger.queued_jobs(10 ** 6)
                     if r["id"] < jid])
        return {"jid": "cj%d" % jid, "queued_ahead": ahead,
                "note": "已递交：裁决器容量感知放置（节点优先，local 小作业兜底，"
                        "总帽 24 核），无资源排队等待。ps 查状态、fetch 取工件。"}

    # ── 裁决+执行（单周期） ───────────────────────────────────────────
    def build_load_table(self) -> dict:
        hosts = {}
        for hid, e in self.node_probe().items():
            h = HostInfo(id=hid, ok=e.get("ok", False),
                         loadr=e.get("loadr", 99.0),
                         cores=e.get("cores", 0),
                         mem_free_g=e.get("mem_free_g", 0))
            h.used_cores, h.running_jobs = self.ledger.node_running(hid)
            hosts[hid] = h
        hosts[LOCAL_ID] = self.local_probe()
        return hosts

    def dispatch(self, row: dict, hosts: dict) -> Optional[Placement]:
        spec = JobSpec(cmd=row.get("cmd", ""), cmd_tpl=row.get("cmd_tpl", ""),
                       shards=row.get("shards", 0), repo=row.get("repo", ""),
                       ref=row.get("ref", "main") or "main",
                       workdir=row.get("workdir", ""),
                       cores=row.get("cores", 1), timeout=row.get("timeout", 3600))
        jid = "cj%d" % row["id"]
        if spec.mode == "shard":
            return self._dispatch_shard(row, jid, spec, hosts)
        pl = decide_placement(spec, hosts, self.policy)
        if pl.placement is None:
            return pl                      # 无容量 → 留队
        logp = JOBLOG / ("%s.log" % jid)
        JOBLOG.mkdir(parents=True, exist_ok=True)
        st = row.get("submitted_ts")
        wait_s = max(0.0, self._now() - st) if st else 0.0
        if pl.placement == LOCAL_ID:
            self._dispatch_local(row, jid, spec, str(logp))
        else:
            self._dispatch_node(row, jid, spec, pl.placement, str(logp))
        self.ledger.mark_running(row["id"], pl.placement, wait_s)
        log("派发 %s → %s（%s；请求 %s 核，wait %.1fs）" % (
            jid, pl.placement, pl.reason, spec.cores, wait_s))
        return pl

    def _dispatch_local(self, row, jid, spec, logp):
        wd = spec.workdir or None
        strategy, _ = pick_limiter(spec.cores, spec.mem_g)
        argv = self.local_runner.build_argv(spec, wd, strategy, jid)
        with open(logp, "a") as f:
            p = subprocess.Popen(argv, cwd=wd or os.getcwd(),
                                 stdout=f, stderr=subprocess.STDOUT,
                                 env=os.environ, start_new_session=True)
        self.popens[row["id"]] = (p, jid, strategy)

    def _dispatch_node(self, row, jid, spec, node, logp):
        argv = self.node_runner.argv(spec, node, jid)
        with open(logp, "a") as f:
            p = subprocess.Popen(argv, stdout=f, stderr=subprocess.STDOUT,
                                 env=PGENV, cwd=str(CLUSTER_DIR))
        self.popens[row["id"]] = (p, jid, "node")

    def _dispatch_shard(self, row, jid, spec, hosts):
        if not all(h.ok for h in hosts.values() if h.id != LOCAL_ID):
            return Placement(None, "shard-needs-nodes")
        args = [sys.executable, CEXEC, "shard", "--repo", spec.repo,
                "--ref", spec.ref, "--cmd-tpl", spec.cmd_tpl,
                "--shards", str(spec.shards), "--job-id", jid]
        if spec.workdir:
            args += ["--stage-dir", spec.workdir]
        logp = JOBLOG / ("%s.log" % jid)
        JOBLOG.mkdir(parents=True, exist_ok=True)
        with open(logp, "a") as f:
            subprocess.run(args, stdout=f, stderr=subprocess.STDOUT,
                           timeout=1800, env=PGENV)
        self.ledger.mark_running(row["id"], "shards", 0.0)
        log("派发 %s → shard×%s（双节点轮转，detached）" % (jid, spec.shards))
        return Placement("shards", "dual-node-roundrobin")

    def reap(self):
        for key, (p, jid, strategy) in list(self.popens.items()):
            rc = p.poll()
            if rc is None:
                continue
            del self.popens[key]
            cpu = None
            if strategy == "node":
                cpu = None  # 节点侧记账 v2；v1 如实 NULL
            else:
                try:
                    from tools.compute_queue.runners import tree_cpu_seconds
                    cpu = tree_cpu_seconds(os.getpgid(p.pid))
                except Exception:
                    cpu = None
            state = "succeeded" if rc == 0 else "failed"
            self.ledger.mark_finished(key, state, rc, cpu,
                                   note="local:%s" % strategy)
            log("终态 %s：%s（rc=%s cpu=%s）" % (jid, state, rc, cpu))

    def cycle(self):
        self.reap()
        hosts = self.build_load_table()
        for row in self.ledger.queued_jobs(limit=8):
            try:
                self.dispatch(row, hosts)
            except Exception as e:
                log("dispatch cj%s error: %s %s" % (row["id"],
                                                     type(e).__name__, e))
        qn = len(self.ledger.queued_jobs(10 ** 6))
        log("cycle：queued=%s hosts=%s" % (
            qn, " ".join("%s(%s)" % (h.id, "ok" if h.ok else "DOWN")
                          for h in hosts.values())))

    # ── 查询面 ────────────────────────────────────────────────────────
    def ps(self, jid: Optional[str] = None) -> list:
        if jid:
            r = self.ledger.get(int(jid.lstrip("c")))
            return [r] if r else []
        return [r for r in self.ledger.queued_jobs(10 ** 6)]


def _jid_int(s) -> int:
    s = str(s).strip()
    return int(s[2:] if s.startswith("cj") else s)


def _spec_from_args(a) -> JobSpec:
    return JobSpec(cmd=a.cmd or "", pytest_target=a.pytest or "",
                    repo=a.repo or "", ref=a.ref or "main",
                    workdir=a.workdir or "", cores=a.cores or 1,
                    mem_g=a.mem_g or 0, timeout=a.timeout or 3600,
                    cmd_tpl=a.cmd_tpl or "", shards=a.shards or 0,
                    submitter=a.submitter or "agent")


def _add_submit_args(p):
    p.add_argument("--cmd")
    p.add_argument("--pytest", help="pytest 目标（如 tests/test_x.py::test_y）")
    p.add_argument("--cmd-tpl")
    p.add_argument("--shards", type=int)
    p.add_argument("--repo")
    p.add_argument("--ref", default="main")
    p.add_argument("--workdir", help="mission 工作树目录（与 repo 二选一）")
    p.add_argument("--cores", type=int)
    p.add_argument("--mem-g", type=int, dest="mem_g")
    p.add_argument("--timeout", type=int, default=3600)
    p.add_argument("--submitter")
    p.add_argument("--node", help=argparse.SUPPRESS)  # 结构性拒绝


def _refuse_node(a):
    if getattr(a, "node", None):
        print(json.dumps({"error": "REFUSED: 不接受 --node（HPC 语义：跑哪是"
                                   "调度系统的事，owner 2026-11-13 令）"},
                        ensure_ascii=False))
        return True
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="统一计算调度 v1（递交/裁决/执行三层分离；不指定节点）")
    ap.add_argument("--sched", action="store_true")
    ap.add_argument("--db", default=None, help="PG 库名（默认 tensorplatform）")
    sub = ap.add_subparsers(dest="sub")
    p = sub.add_parser("submit"); _add_submit_args(p)
    p = sub.add_parser("ps"); p.add_argument("--jid")
    p = sub.add_parser("kill"); p.add_argument("--jid", required=True)
    p = sub.add_parser("fetch"); p.add_argument("--jid", required=True)
    sub.add_parser("nodes")
    sub.add_parser("dispatch-once")
    p = sub.add_parser("serve"); p.add_argument("--port", type=int, default=8099)
    a = ap.parse_args(argv)

    if a.sub == "submit" and _refuse_node(a):
        return 2
    if a.sched or a.sub == "dispatch-once":
        ledger = PgLedger(db=a.db)
        ledger.migrate()
        cq = ComputeQueue(ledger)
        if a.sched:
            r = subprocess.run(["pgrep", "-f", "compute_queue.py --sched"],
                               capture_output=True, text=True, env=PGENV)
            if any(int(x) != os.getpid() for x in r.stdout.split()):
                print("sched already running"); return 0
            log("compute_queue 调度器启动 pid=%d" % os.getpid())
            while True:
                try:
                    cq.cycle()
                except Exception as e:
                    log("cycle error: %s %s" % (type(e).__name__, e))
                time.sleep(POLL)
        else:
            cq.cycle()
            print(json.dumps({"dispatched_cycle": True}, ensure_ascii=False))
        return 0

    if a.sub == "nodes":
        ledger = PgLedger(db=a.db); ledger.migrate()
        cq = ComputeQueue(ledger)
        hosts = cq.build_load_table()
        print(json.dumps({h.id: {"ok": h.ok, "loadr": round(h.loadr, 3),
                           "cores": h.cores, "mem_free_g": h.mem_free_g,
                           "used_cores": h.used_cores,
                           "running_jobs": h.running_jobs}
                          for h in hosts.values()}, ensure_ascii=False))
        return 0

    if a.sub in ("ps", "kill", "fetch"):
        ledger = PgLedger(db=a.db); ledger.migrate()
        if a.sub == "ps":
            rows = cq_rows(ledger, a.jid)
            if not rows:
                print("（无匹配作业）"); return 0
            print("%-8s %-10s %-10s %-8s %-5s %-10s %-8s" %
                  ("jid", "state", "placement", "rc", "wait_s", "cpu_s", "note"))
            for r in rows:
                print("%-8s %-10s %-10s %-8s %-5s %-10s %-8s" % (
                    "cj%d" % r["id"], r["state"], r.get("placement") or "-",
                    "-" if r.get("rc") in (None, -1) else r["rc"],
                    "-" if r.get("wait_s") is None else "%.1f" % r["wait_s"],
                    "-" if r.get("cpu_seconds") is None
                    else "%.1f" % r["cpu_seconds"],
                    (r.get("note") or "")[:40]))
            return 0
        if a.sub == "kill":
            ledger.kill(_jid_int(a.jid))
            subprocess.run([sys.executable, CEXEC, "kill", "--job-id", a.jid],
                           capture_output=True, timeout=90, env=PGENV)
            print(json.dumps({"jid": a.jid, "killed": True}))
            return 0
        r = subprocess.run([sys.executable, CEXEC, "fetch", "--job-id", a.jid],
                           capture_output=True, text=True, timeout=600, env=PGENV)
        print(r.stdout)
        return r.returncode

    if a.sub == "serve":
        from tools.compute_queue.httpd import serve
        ledger = PgLedger(db=a.db); ledger.migrate()
        serve(ComputeQueue(ledger), a.port)
        return 0

    if a.sub == "submit":
        ledger = PgLedger(db=a.db); ledger.migrate()
        cq = ComputeQueue(ledger)
        out = cq.submit(_spec_from_args(a))
        print(json.dumps(out, ensure_ascii=False))
        return 0 if "jid" in out else 2
    print("用法：submit/ps/kill/fetch/nodes/dispatch-once/serve/--sched")
    return 2


def cq_rows(ledger, jid):
    if jid:
        r = ledger.get(_jid_int(jid))
        return [r] if r else []
    rows = ledger.queued_jobs(10 ** 6)
    return rows


if __name__ == "__main__":
    sys.exit(main())
