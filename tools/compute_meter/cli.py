"""``compute-meter`` CLI：放置决策 / 本机执行 / 集群递交 / 计量回填 / 看板 / 门。

设计口令（owner 2026-11-13）：「agent 计算提交给调度系统，跑在哪里是调度系统的事」。
所以对 agent 只暴露 ``submit``：它会拿策略做放置决策，本机还是集群由策略定；
``run-local`` 只给实验基线/返修用（会记 blocking 违例）。

  compute-meter decide  --mission 1 --repo R --est-cpu 600 --priority mainline
  compute-meter submit  --mission 1 --repo R --cmd "pytest -q" --priority mainline
  compute-meter run-local --mission 1 --repo R --cmd "..." --priority self_driven
  compute-meter ingest  [--days 7]
  compute-meter report  [--days 7] [--json out.json] [--markdown out.md] [--gate]
  compute-meter gate    [--days 7] [--target 0.2] [--min-cpu-seconds 600]
  compute-meter inject-panorama [--path PANORAMA.md] [--dry-run]
  compute-meter order-queue [--days 2]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
import uuid

from tools.compute_meter import dashboard, meter, panorama, sampler
from tools.compute_meter.policy import (
    PLACEMENT_CLUSTER,
    PLACEMENT_LOCAL,
    PLACEMENT_QUEUE,
    decide,
    load_policy,
    order_queue,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_ART = REPO_ROOT / "artifacts" / "compute_meter"
CLUSTER_QUEUE = pathlib.Path(os.environ.get("COMPUTE_CLUSTER_QUEUE",
                                            "/root/tp-agent/cluster/cluster_queue.py"))
DEFAULT_PANORAMA = pathlib.Path(os.environ.get("COMPUTE_PANORAMA",
                                              "/root/tp-agent/PANORAMA.md"))


# ── store 选择 ────────────────────────────────────────────────────────
def open_store(spec: str):
    """``sql`` / ``jsonl:<path>`` / 逗号分隔的多账本叠加（写只落第一个）。"""
    parts = [p.strip() for p in str(spec).split(",") if p.strip()]
    stores = []
    for p in parts:
        if p == "sql":
            stores.append(meter.SqlStore(os.environ.get("COMPUTE_DB", "tensorplatform")))
        elif p.startswith("jsonl:"):
            stores.append(meter.JsonlStore(p.split(":", 1)[1]))
        else:
            raise SystemExit("--store 只能是 sql / jsonl:<path>，可用逗号叠加")
    return stores[0] if len(stores) == 1 else meter.UnionStore(stores)


def _est(store, repo: str | None, cmd_kind: str | None, given: float | None) -> tuple[float, str]:
    if given is not None:
        return float(given), "cli:--est-cpu"
    est, basis = meter.estimate_cpu_seconds(store.records(),
                                           repo=repo, cmd_kind=cmd_kind, default=0.0)
    return est, basis


def _local_usage(store, policy, priority: str) -> float:
    """该优先级类最近 1h 在本机烧掉的 CPU 秒（本机帽的已用部分）。"""
    import datetime as _dt
    cut = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=1)
    used = 0.0
    for r in store.records():
        if r.get("placement") != "local":
            continue
        cls = policy.cls(r.get("priority_class"))
        if cls.name != policy.cls(priority).name:
            continue
        ts = r.get("finished_at") or r.get("started_at")
        if ts in (None, ""):
            continue
        try:
            d = dashboard._parse_ts(ts)
        except Exception:
            continue
        if d and d >= cut:
            used += float(r.get("cpu_seconds") or 0.0)
    return used


def _node_observations(store=None) -> list[dict] | None:
    """取集群节点实时观测（走 cluster_exec nodes，与调度器同源）。"""
    exe = CLUSTER_QUEUE.parent / "cluster_exec.py"
    if not exe.exists():
        return None
    try:
        r = subprocess.run([sys.executable, str(exe), "nodes"], capture_output=True,
                           text=True, timeout=120)
    except Exception:
        return None
    jobs = {}
    try:
        rows = meter.fetch_cluster_rows(os.environ.get("COMPUTE_DB", "tensorplatform"))
        for row in rows:
            if row.get("state") == "running" and row.get("node"):
                e = jobs.setdefault(row["node"], {"jobs": 0, "cores": 0})
                e["jobs"] += 1
                e["cores"] += int(row.get("cores") or 1)
    except Exception:
        pass
    out = []
    for ln in r.stdout.strip().splitlines():
        try:
            d = json.loads(ln)
        except Exception:
            continue
        info = d.get("info", "") or ""
        loadr = 99.0
        if d.get("ok"):
            try:
                load = float(info.split("load=")[1].split()[0])
                cores = int(info.split("cores=")[1].split()[0])
                loadr = load / cores if cores else 99.0
            except Exception:
                loadr = 99.0
        j = jobs.get(d.get("id"), {"jobs": 0, "cores": 0})
        out.append({"id": d.get("id"), "ok": bool(d.get("ok")), "loadr": loadr,
                    "running_jobs": j["jobs"], "used_cores": j["cores"]})
    return out or None


# ── 子命令 ────────────────────────────────────────────────────────────
def c_decide(a) -> int:
    policy = load_policy(a.policy)
    store = open_store(a.store)
    est, basis = _est(store, a.repo, a.cmd_kind, a.est_cpu)
    d = decide(policy, {"mission_id": a.mission, "repo": a.repo, "est_cpu_seconds": est,
                        "priority": a.priority, "cores": a.cores, "cmd_kind": a.cmd_kind},
               local_usage_cpu_seconds=_local_usage(store, policy, a.priority),
               local_load_ratio=_local_load(),
               cluster_nodes=_node_observations(store),
               forced_placement=a.force)
    print(json.dumps({**d.as_dict(), "est_basis": basis}, ensure_ascii=False, indent=2))
    return 0


def _local_load() -> float:
    try:
        cores = len(os.sched_getaffinity(0))
        return os.getloadavg()[0] / cores if cores else 0.0
    except Exception:
        return 0.0


def _run_and_measure(cmd: str, *, timeout: float) -> dict:
    """跑命令并用 /proc 采样它的 CPU 秒（含子进程，按进程树快照累计）。"""
    t0 = time.monotonic()
    p = subprocess.Popen(cmd, shell=True, start_new_session=True)
    seen: dict[int, float] = {}
    while p.poll() is None:
        for pid in [p.pid] + sampler.scan_pids(str(p.pid)):
            v = sampler.proc_cpu_seconds(pid)
            if v is not None:
                seen[pid] = max(seen.get(pid, 0.0), v)
        if time.monotonic() - t0 > timeout:
            p.kill()
            break
        time.sleep(0.2)
    for pid in [p.pid]:
        v = sampler.proc_cpu_seconds(pid)
        if v is not None:
            seen[pid] = max(seen.get(pid, 0.0), v)
    return {"cpu_seconds": round(sum(seen.values()), 3),
            "wall_seconds": round(time.monotonic() - t0, 3),
            "exit_code": int(p.returncode if p.returncode is not None else -9)}


def _record(store, rec) -> bool:
    return store.add(rec)


def c_run_local(a) -> int:
    policy = load_policy(a.policy)
    store = open_store(a.store)
    est, basis = _est(store, a.repo, a.cmd_kind, a.est_cpu)
    cls = policy.cls(a.priority)
    d = decide(policy, {"mission_id": a.mission, "repo": a.repo, "est_cpu_seconds": est,
                        "priority": a.priority, "cores": a.cores, "cmd_kind": a.cmd_kind},
               local_usage_cpu_seconds=_local_usage(store, policy, a.priority),
               local_load_ratio=_local_load(),
               cluster_nodes=_node_observations(store),
               forced_placement="local")
    res = _run_and_measure(a.cmd, timeout=a.timeout)
    ref = "local-%s" % uuid.uuid4().hex[:12]
    _record(store, meter.make_record(
        source_ref=ref, placement=PLACEMENT_LOCAL, mission_id=a.mission, repo=a.repo,
        cmd_kind=a.cmd_kind, node=policy.control_node, priority_class=cls.name,
        priority_rank=cls.rank, cores=a.cores, cpu_seconds=res["cpu_seconds"],
        wall_seconds=res["wall_seconds"], exit_code=res["exit_code"],
        policy_version=policy.version,
        decision_reason=f"{d.reason}; est={est:g}({basis})",
        started_at=None, finished_at=None))
    for v in d.violations:
        sys.stderr.write("VIOLATION %s [%s] %s\n" % (v["code"], v["severity"], v["detail"]))
    print(json.dumps({"source_ref": ref, "placement": PLACEMENT_LOCAL, **res,
                      "est_cpu_seconds": est, "est_basis": basis,
                      "violations": d.violations}, ensure_ascii=False))
    return 0 if res["exit_code"] == 0 else 1


def c_submit(a) -> int:
    policy = load_policy(a.policy)
    store = open_store(a.store)
    est, basis = _est(store, a.repo, a.cmd_kind, a.est_cpu)
    cls = policy.cls(a.priority)
    nodes = _node_observations(store)
    d = decide(policy, {"mission_id": a.mission, "repo": a.repo, "est_cpu_seconds": est,
                        "priority": a.priority, "cores": a.cores, "cmd_kind": a.cmd_kind},
               local_usage_cpu_seconds=_local_usage(store, policy, a.priority),
               local_load_ratio=_local_load(), cluster_nodes=nodes)
    if a.dry_run:
        print(json.dumps({**d.as_dict(), "est_basis": basis, "dry_run": True},
                         ensure_ascii=False, indent=2))
        return 0
    if d.placement == PLACEMENT_LOCAL:
        sys.stderr.write("策略判定本机（%s）→ 交给本机执行面\n" % d.reason)
        return c_run_local(a)

    args = [sys.executable, str(CLUSTER_QUEUE), "submit", "--repo", a.repo, "--ref", a.ref,
            "--cmd", a.cmd, "--cores", str(a.cores), "--timeout", str(int(a.timeout))]
    if a.mission:
        args += ["--mid", str(a.mission)]
    if a.stage_dir:
        args += ["--stage-dir", a.stage_dir]
    r = subprocess.run(args, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        sys.stderr.write("递交失败: " + (r.stderr or r.stdout)[:400] + "\n")
        return 2
    try:
        sub = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        sys.stderr.write("递交输出无法解析: " + r.stdout[:200] + "\n")
        return 2
    jid = sub.get("jid")
    rec = meter.make_record(
        source_ref=str(jid), placement=PLACEMENT_CLUSTER, mission_id=a.mission, repo=a.repo,
        cmd_kind=a.cmd_kind, node=None, priority_class=cls.name, priority_rank=cls.rank,
        cores=a.cores, cpu_seconds=0.0, wall_seconds=None, exit_code=None,
        policy_version=policy.version,
        decision_reason=f"{d.placement}:{d.reason}; est={est:g}({basis})")
    _record(store, rec)   # 0 秒占位：ingest 会用 cluster_jobs 实测值刷新同一 source_ref
    print(json.dumps({**sub, "placement": d.placement, "policy_version": policy.version,
                      "est_cpu_seconds": est, "est_basis": basis,
                      "decision_reason": d.reason}, ensure_ascii=False))
    return 0


def c_ingest(a) -> int:
    policy = load_policy(a.policy)
    store = open_store(a.store)
    if hasattr(store, "ensure_schema"):
        store.ensure_schema()
    out = meter.ingest_cluster_jobs(store, days=a.days, db=os.environ.get("COMPUTE_DB", "tensorplatform"),
                                    shard_nodes=len(policy.nodes),
                                    shard_node_share=policy.shard_node_share)
    print(json.dumps(out, ensure_ascii=False))
    return 0


def _build_metrics(a):
    policy = load_policy(a.policy)
    store = open_store(a.store)
    try:
        recs = store.records(days=a.days)
    except TypeError:
        recs = store.records()
    try:
        rows = meter.fetch_cluster_rows(os.environ.get("COMPUTE_DB", "tensorplatform"), days=a.days)
    except Exception:
        rows = []
    m = dashboard.metrics(recs, rows, policy)
    m["window_days"] = a.days
    return policy, m


def c_report(a) -> int:
    policy, m = _build_metrics(a)
    gate = dashboard.evaluate(m, target=a.target, min_cpu_seconds=a.min_cpu_seconds,
                              scope=getattr(a, "scope", "all"))
    m["gate"] = gate
    md = dashboard.render_markdown(m, window_label=(f"最近 {a.days:g} 天" if a.days else "全部账本"),
                                   gate=gate)
    if a.json:
        p = pathlib.Path(a.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(m, ensure_ascii=False, indent=2))
    if a.markdown:
        p = pathlib.Path(a.markdown)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(md)
    if not a.json and not a.markdown:
        print(md if not a.quiet else json.dumps(m, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"json": a.json, "markdown": a.markdown,
                          "local_share": m["summary"]["local_share"],
                          "verdict": gate["verdict"]}, ensure_ascii=False))
    return 0


def c_gate(a) -> int:
    policy, m = _build_metrics(a)
    gate = dashboard.evaluate(m, target=a.target, min_cpu_seconds=a.min_cpu_seconds,
                              scope=a.scope)
    print(json.dumps(gate, ensure_ascii=False, indent=2))
    return gate["exit_code"]


def c_inject(a) -> int:
    policy, m = _build_metrics(a)
    gate = dashboard.evaluate(m, target=a.target, min_cpu_seconds=a.min_cpu_seconds,
                              scope=getattr(a, "scope", "all"))
    block = dashboard.render_markdown(m, window_label=(f"最近 {a.days:g} 天" if a.days else "全部账本"),
                                      gate=gate)
    if a.block_out:
        p = pathlib.Path(a.block_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(block)
    res = panorama.inject_file(a.path, block, dry_run=a.dry_run)
    res["gate"] = gate["verdict"]
    print(json.dumps(res, ensure_ascii=False))
    return 0


def c_order_queue(a) -> int:
    policy = load_policy(a.policy)
    rows = [r for r in meter.fetch_cluster_rows(
        os.environ.get("COMPUTE_DB", "tensorplatform"), days=a.days) if r.get("state") == "queued"]
    for r in rows:
        r.setdefault("priority", a.default_priority)
    out = order_queue(policy, rows)
    print(json.dumps({"queued": len(out), "order": out[:a.limit],
                      "policy_version": policy.version}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="compute-meter",
                                 description="计算调度放置策略 + 计量账本（cpu_seconds × placement）")
    ap.add_argument("--policy", default=None)
    ap.add_argument("--store", default="sql", help="sql | jsonl:<path>")
    sub = ap.add_subparsers(dest="sub", required=True)

    common = dict()
    for name in ("decide", "run-local", "submit"):
        p = sub.add_parser(name)
        p.add_argument("--mission", type=int)
        p.add_argument("--repo", required=True)
        p.add_argument("--ref", default="main")
        p.add_argument("--cmd")
        p.add_argument("--cmd-kind", default="run")
        p.add_argument("--priority", default="self_driven",
                       choices=["rework", "mainline", "self_driven"])
        p.add_argument("--cores", type=int, default=1)
        p.add_argument("--est-cpu", type=float, default=None)
        p.add_argument("--timeout", type=float, default=7200)
        p.add_argument("--stage-dir", default=None)
        p.add_argument("--force", default=None, choices=[None, "local", "cluster", "queue"])
        p.add_argument("--dry-run", action="store_true")
        p.set_defaults(fn={"decide": c_decide, "run-local": c_run_local,
                           "submit": c_submit}[name])

    p = sub.add_parser("ingest"); p.add_argument("--days", type=float, default=None)
    p.set_defaults(fn=c_ingest)

    for name, fn in (("report", c_report), ("gate", c_gate)):
        p = sub.add_parser(name)
        p.add_argument("--days", type=float, default=None)
        p.add_argument("--target", type=float, default=None)
        p.add_argument("--min-cpu-seconds", type=float, default=600.0)
        p.add_argument("--json", default=None)
        p.add_argument("--markdown", default=None)
        p.add_argument("--quiet", action="store_true")
        p.add_argument("--scope", default="all", choices=["all", "share", "hygiene"],
                       help="share=只看本机占比相关判据；hygiene=只看调度卫生")
        p.set_defaults(fn=fn)

    p = sub.add_parser("inject-panorama")
    p.add_argument("--path", default=str(DEFAULT_PANORAMA))
    p.add_argument("--days", type=float, default=None)
    p.add_argument("--target", type=float, default=None)
    p.add_argument("--min-cpu-seconds", type=float, default=600.0)
    p.add_argument("--block-out", default=None)
    p.add_argument("--scope", default="all", choices=["all", "share", "hygiene"])
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=c_inject)

    p = sub.add_parser("order-queue")
    p.add_argument("--days", type=float, default=2)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--default-priority", default="self_driven")
    p.set_defaults(fn=c_order_queue)

    a = ap.parse_args(argv)
    for name in ("cmd",):
        if hasattr(a, name) and getattr(a, name) is None and a.sub in ("run-local", "submit"):
            raise SystemExit(f"{a.sub} 需要 --cmd")
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())