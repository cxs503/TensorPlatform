"""对照实验：重计算「本机跑」vs「交给调度系统」——99 控制节点被烧掉多少 CPU 秒。

预注册（动手前写死，事后不改）：
  decision      放置策略能否把重计算的 99 侧 CPU 消耗压到 ≤20%（owner 目标）
  alternatives  A=强制本机（现状）  B=策略路由（本引擎）
  hypothesis    B 的 99 侧 CPU 秒 ≤ 20% × A 的 99 侧 CPU 秒
  metrics       ①工作负载实测 cpu_seconds（进程级 /proc 采样 + time.process_time 交叉校验）
                ②99 整机 busy CPU 秒（/proc/stat 差分，含邻居干扰 → 只作上下文不判门）
                ③墙钟、退出码、checksum（工作负载完整性）
  unit          一次工作负载运行（默认 60 CPU·秒）
  baseline      A 臂（forced local，n=--repeats）
  treatment     B 臂（policy 决策 → cluster_queue 递交）
  阈值          99 侧 CPU 秒 ≤ 20% × A 臂均值（predeclared，见 --target）
  停止规则      两臂固定重复数，不做序贯偷看
  混杂          99 上有其他 agent 进程 → 进程级采样隔离；整机 busy 只作旁证
  记录          命令/版本/参数/原始 JSON 全量落 artifacts/compute_meter/

用法：
  python3 -m tools.compute_meter.ab_experiment --burn-seconds 60 --repeats 3
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import pathlib
import statistics
import subprocess
import sys
import uuid

from tools.compute_meter import cli, meter, sampler
from tools.compute_meter.policy import decide, load_policy

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ART = REPO_ROOT / "artifacts" / "compute_meter"


def _sys_busy(fn, *a, **kw):
    b = sampler.system_cpu_seconds()
    out = fn(*a, **kw)
    return out, sampler.system_busy_between(b, sampler.system_cpu_seconds())


def run_arm_local(policy, *, burn_seconds: float, repeats: int, mission: int,
                  store) -> list[dict]:
    """A 臂：强制本机执行（现状基线）。"""
    rows = []
    cls = policy.cls("mainline")
    cmd = ("%s -m tools.compute_meter.workload --burn-seconds %g"
           % (sys.executable, burn_seconds))
    for i in range(repeats):
        res, busy = _sys_busy(cli._run_and_measure, cmd, timeout=burn_seconds * 10 + 120)
        ref = "ab-baseline-%s-%d" % (uuid.uuid4().hex[:8], i)
        store.add(meter.make_record(
            source_ref=ref, placement="local", mission_id=mission, repo="TensorPlatform",
            cmd_kind="workload", node=policy.control_node, priority_class=cls.name,
            priority_rank=cls.rank, cores=1, cpu_seconds=res["cpu_seconds"],
            wall_seconds=res["wall_seconds"], exit_code=res["exit_code"],
            policy_version=policy.version,
            decision_reason="ab:A-arm forced local (status quo baseline)"))
        rows.append({"arm": "A_local", "run": i, **res, "system": busy,
                     "local_cpu_seconds": res["cpu_seconds"]})
    return rows


def run_arm_policy(policy, *, burn_seconds: float, repeats: int, mission: int, store,
                   real_submit: bool) -> list[dict]:
    """B 臂：策略决策（+ 一次真实递交验证端到端链路）。"""
    rows = []
    cls = policy.cls("mainline")
    req = {"mission_id": mission, "repo": "TensorPlatform", "est_cpu_seconds": burn_seconds,
           "priority": "mainline", "cores": 1, "cmd_kind": "workload"}
    for i in range(repeats):
        nodes = cli._node_observations()
        b = sampler.system_cpu_seconds()
        t0 = __import__("time").monotonic()
        d = decide(policy, req, local_usage_cpu_seconds=0.0, local_load_ratio=cli._local_load(),
                   cluster_nodes=nodes)
        dt = __import__("time").monotonic() - t0
        busy = sampler.system_busy_between(b, sampler.system_cpu_seconds())
        row = {"arm": "B_policy", "run": i, "placement": d.placement, "node": d.node,
               "reason": d.reason, "decision_wall_seconds": round(dt, 4),
               "system": busy}
        if real_submit and i == 0:
            cmd = ("%s -m tools.compute_meter.workload --burn-seconds %g" % (sys.executable,
                                                                            burn_seconds))
            args = [sys.executable, str(cli.CLUSTER_QUEUE), "submit", "--repo", "TensorPlatform",
                    "--ref", "main", "--cmd", cmd, "--cores", "1",
                    "--timeout", str(int(burn_seconds * 10 + 300)),
                    "--mid", str(mission), "--submitter", "compute-meter-ab"]
            r = subprocess.run(args, capture_output=True, text=True, timeout=180)
            row["submit_rc"] = r.returncode
            row["submit_out"] = (r.stdout or r.stderr).strip().splitlines()[-1:]
            if r.returncode == 0:
                sub = json.loads(r.stdout.strip().splitlines()[-1])
                jid = sub.get("jid")
                row["jid"] = jid
                store.add(meter.make_record(
                    source_ref=str(jid), placement="cluster", mission_id=mission,
                    repo="TensorPlatform", cmd_kind="workload", node=None,
                    priority_class=cls.name, priority_rank=cls.rank, cores=1,
                    cpu_seconds=0.0, policy_version=policy.version,
                    decision_reason="ab:B-arm submit;%s" % d.reason))
        rows.append(row)
    return rows


def summarize_arm(rows: list[dict]) -> dict:
    cpu = [float(r.get("local_cpu_seconds", 0.0)) for r in rows]
    sysbusy = [float(r["system"]["busy_cpu_seconds"]) for r in rows]
    return {"n": len(rows), "local_cpu_seconds_mean": round(statistics.mean(cpu), 3) if cpu else 0.0,
            "local_cpu_seconds_total": round(sum(cpu), 3),
            "system_busy_cpu_seconds_mean": round(statistics.mean(sysbusy), 3) if sysbusy else 0.0}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="本机 vs 调度系统 对照实验（预注册）")
    ap.add_argument("--burn-seconds", type=float, default=60.0)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--mission", type=int, default=17372)
    ap.add_argument("--target", type=float, default=0.20)
    ap.add_argument("--no-real-submit", action="store_true")
    ap.add_argument("--out", default=str(ART / "ab_result.json"))
    a = ap.parse_args(argv)

    policy = load_policy()
    ART.mkdir(parents=True, exist_ok=True)
    base_store = meter.JsonlStore(ART / "ab_baseline.jsonl")
    treat_store = meter.JsonlStore(ART / "ab_treatment.jsonl")

    print("A 臂：强制本机 ×%d（每次 %gs CPU）…" % (a.repeats, a.burn_seconds), file=sys.stderr)
    a_rows = run_arm_local(policy, burn_seconds=a.burn_seconds, repeats=a.repeats,
                           mission=a.mission, store=base_store)
    print("B 臂：策略路由 ×%d（真实递交 %s）…" % (a.repeats, not a.no_real_submit),
          file=sys.stderr)
    b_rows = run_arm_policy(policy, burn_seconds=a.burn_seconds, repeats=a.repeats,
                            mission=a.mission, store=treat_store,
                            real_submit=not a.no_real_submit)

    sa, sb = summarize_arm(a_rows), summarize_arm(b_rows)
    ratio = (sb["local_cpu_seconds_mean"] / sa["local_cpu_seconds_mean"]
             if sa["local_cpu_seconds_mean"] else None)
    verdict = ("pass" if ratio is not None and ratio <= a.target
               else "fail" if ratio is not None else "insufficient_data")
    result = {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "design": {"burn_seconds": a.burn_seconds, "repeats": a.repeats,
                   "target_local_share": a.target,
                   "decision": "策略路由能否把 99 侧 CPU 消耗压到 ≤目标",
                   "arms": ["A_local(forced status quo)", "B_policy(routed)"],
                   "stopping_rule": "固定重复数，不序贯偷看",
                   "unit": "一次工作负载运行"},
        "policy_version": policy.version,
        "arm_A": sa, "arm_B": sb, "ratio_B_over_A": None if ratio is None else round(ratio, 4),
        "verdict": verdict,
        "rows": {"A_local": a_rows, "B_policy": b_rows},
        "writes": {"baseline_ledger": str(ART / "ab_baseline.jsonl"),
                   "treatment_ledger": str(ART / "ab_treatment.jsonl")},
    }
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: result[k] for k in ("verdict", "ratio_B_over_A", "arm_A", "arm_B")},
                     ensure_ascii=False, indent=2))
    return 0 if verdict == "pass" else (3 if verdict == "fail" else 4)


if __name__ == "__main__":
    raise SystemExit(main())