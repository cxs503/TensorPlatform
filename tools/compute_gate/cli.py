#!/usr/bin/env python3
"""compute_gate.cli —— 命令行面（可被 tp_worker / systemd / 人工共用）。

  submit   --repo R [--cmd C | --cmd-tpl T --shards N] [--cores N] [--timeout S]
           [--stage-dir D] [--mid M] [--ref REF]
  status   [--jid cjN]
  fetch    --jid cjN [--wait S]
  nodes | usage
  guard    [--once | --loop] [--interval S] [--dry-run] [--json]
  preflight --cmd "…"            # 本机命令静态判定（allow/redirect，退出码 0/7）
  selftest --spawn-cores N --spawn-seconds S   # 红队用：真起 N 核燃烧子树再扫（机器门可失败）
"""
import argparse
import json
import sys

from . import compute, guard, ledger, policy, preflight


def _cfg(a):
    cfg = policy.load_config(getattr(a, "config", None))
    if getattr(a, "dry_run", False):
        cfg["dry_run"] = True
    if getattr(a, "hog_cores", None):
        cfg["hog_cores"] = float(a.hog_cores)
    if getattr(a, "hog_seconds", None):
        cfg["hog_seconds"] = float(a.hog_seconds)
    if getattr(a, "work_roots", None):
        cfg["work_roots"] = a.work_roots
    if getattr(a, "events_path", None):
        cfg["events_path"] = a.events_path
    if getattr(a, "state_path", None):
        cfg["state_path"] = a.state_path
    if getattr(a, "enforce_off", False):
        cfg["enforce"] = False
    return cfg


def main(argv=None):
    ap = argparse.ArgumentParser(prog="compute_gate", description="计算递交门（owner 2026-11-13 令）")
    ap.add_argument("--config"); ap.add_argument("--events-path"); ap.add_argument("--state-path")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("submit")
    s.add_argument("--repo", required=True); s.add_argument("--cmd"); s.add_argument("--cmd-tpl")
    s.add_argument("--shards", type=int); s.add_argument("--cores", type=int)
    s.add_argument("--timeout", type=int, default=7200); s.add_argument("--ref", default="main")
    s.add_argument("--stage-dir"); s.add_argument("--mid", type=int)

    s = sub.add_parser("status"); s.add_argument("--jid")
    s = sub.add_parser("fetch"); s.add_argument("--jid", required=True); s.add_argument("--wait", type=int, default=0)
    s = sub.add_parser("nodes")
    s = sub.add_parser("usage")

    g = sub.add_parser("guard")
    g.add_argument("--once", action="store_true"); g.add_argument("--loop", action="store_true")
    g.add_argument("--interval", type=int); g.add_argument("--dry-run", action="store_true")
    g.add_argument("--json", action="store_true"); g.add_argument("--hog-cores", type=float)
    g.add_argument("--hog-seconds", type=float); g.add_argument("--work-roots", nargs="*")
    g.add_argument("--enforce-off", action="store_true")

    p = sub.add_parser("preflight"); p.add_argument("--cmdline", dest="cmdline", required=True)

    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help(); return 2
    cfg = _cfg(a)

    if a.cmd == "submit":
        r = compute.run_compute(cfg, action="submit", repo=a.repo, cmd=a.cmd, cmd_tpl=a.cmd_tpl,
                                shards=a.shards, cores=a.cores, timeout=a.timeout, ref=a.ref,
                                stage_dir=a.stage_dir, mission_id=a.mid)
        print(json.dumps(r, ensure_ascii=False, indent=1)); return 0 if r.get("ok") else 1
    if a.cmd == "status":
        r = compute.run_compute(cfg, action="status", job_id=a.jid)
        print(r.get("raw", "")); print(json.dumps(r.get("receipt"), ensure_ascii=False)); return 0
    if a.cmd == "fetch":
        r = compute.run_compute(cfg, action="fetch", job_id=a.jid, wait_seconds=a.wait)
        print(json.dumps(r, ensure_ascii=False, indent=1)); return 0 if r.get("ok") else 1
    if a.cmd == "nodes":
        print(compute.run_compute(cfg, action="nodes")["raw"]); return 0
    if a.cmd == "usage":
        print(json.dumps(compute.run_compute(cfg, action="usage")["receipt"], ensure_ascii=False, indent=1)); return 0
    if a.cmd == "preflight":
        r = preflight.check_local_command(a.cmdline)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0 if r["verdict"] == "allow" else 7
    if a.cmd == "guard":
        if a.loop:
            guard.loop(cfg, interval=a.interval); return 0
        res = guard.scan_and_enforce(cfg, apply=cfg["enforce"])
        if a.json:
            print(json.dumps({k: v for k, v in res.items()}, ensure_ascii=False, indent=1))
        else:
            print("violations=%d trees=%d actions=%d" % (
                len(res["violations"]), len(res["observations"]), len(res["actions"])))
            for ob in res["observations"]:
                print("  tree pid=%-7d cpu=%8.1fs cores=%.2f alive=%2d %s %s" % (
                    ob["pid"], ob["cpu_seconds"], ob["cores_ema"] or 0.0, ob["alive"],
                    "VIOLATION" if ob in res["violations"] else "ok", (ob["cmd"] or "")[:70]))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())