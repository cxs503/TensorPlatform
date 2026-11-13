#!/usr/bin/env python3
"""compute_gate.guard —— 本机 hog guard（本机重计算硬拦 + 自动迁移到队列）。

语义（owner 2026-11-13 令）：
  work/m{id} 内进程树 ≥8 核持续 >10min 且非队列 runner 子孙 →
    ① SIGSTOP 整树（先冻结副作用）
    ② 事件账本贴「违例待迁移」注记
    ③ 自动把**同一条命令**递交到集群队列（stage_dir=原 worktree → 工作不丢）
    ④ 递交成功后杀掉本地进程树；递交失败则 SIGCONT 恢复 + 记 migrate_failed（宁可不迁移也不丢工作）

为什么用累计 CPU 判「持续」：核数×秒 与 采样抖动无关、单调，等价刻画「8 核跑满 10min」。
"""
import json
import os
import pathlib
import signal
import time

from . import ledger as ledger_mod
from . import policy
from . import procs as procs_mod
from . import queue as queue_mod


# ── 状态（跨扫描累计；pid+start_ticks 为身份，pid 复用不串账） ──────────
def load_state(cfg):
    p = pathlib.Path(cfg["state_path"])
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {"trees": {}, "last_scan": None}


def save_state(cfg, state):
    p = pathlib.Path(cfg["state_path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    tmp.replace(p)


# ── 信号 ─────────────────────────────────────────────────────────────
def signal_tree(pids, sig):
    """先子后父（防父重启子）；返回 {pid: ok}。"""
    ok = {}
    for pid in sorted(pids, reverse=True):
        try:
            os.kill(pid, sig)
            ok[pid] = True
        except ProcessLookupError:
            ok[pid] = False
        except PermissionError:
            ok[pid] = False
    return ok


def wait_gone(pids, timeout=10.0):
    t0 = time.time()
    remain = list(pids)
    while remain and time.time() - t0 < timeout:
        remain = [p for p in remain if pathlib.Path("/proc/%d" % p).exists()
                  and _state(p) not in ("Z", "X", None)]
        if remain:
            time.sleep(0.2)
    return remain


def _state(pid):
    try:
        raw = open("/proc/%d/stat" % pid, "rb").read().decode("utf-8", "replace")
        return raw.split(")", 1)[1].split()[0]
    except Exception:
        return None


# ── 扫描：找出违例树 ─────────────────────────────────────────────────
def scan(cfg, proc_root="/proc", state=None, now=None):
    """返回 (violations, observations, state)。

    violation = 累计 CPU 秒 ≥ hog_cores×hog_seconds、仍存活、且不属队列 runner/保护面 的树。
    observation = 全部在托管的树（供负对照/回执：正常 <8 核短测试出现在这里但不会进 violations）。
    """
    now = now or time.time()
    procs = procs_mod.read_procs(proc_root)
    state = state if state is not None else load_state(cfg)
    runners = procs_mod.matched_pids(procs, cfg["queue_runner_markers"], cfg["protected_commands"])
    # 只排除 gate 自身及其**祖先链**（控制面：guard → systemd/启动 shell），
    # 不排除自身的子孙——那样会把「in-process 运行时的被测算力」误当自家人放行。
    # 生产形态下 guard 是 systemd 常驻进程，祖先链只有 systemd；agent 的算力树永远不会是它的祖先。
    self_chain = set(procs_mod.ancestors(procs, os.getpid()))
    thr = policy.cpu_threshold_seconds(cfg)

    roots = procs_mod.work_scope_roots(procs, cfg["work_roots"])
    observations, violations, advisories = [], [], []
    for root in roots:
        p = procs.get(root)
        if p is None:
            continue
        members = procs_mod.subtree(procs, root)
        alive = procs_mod.subtree_alive(procs, root)
        cpu_s = procs_mod.subtree_cpu_seconds(procs, root)
        rss = procs_mod.subtree_rss_mb(procs, root)
        prev = state["trees"].get(p.key, {})
        dt = max(1e-6, now - float(prev.get("ts") or now - 1))
        # 当前速率只在「有上一次采样」时可算（首次扫描只建档、不下判定）
        cores_now = max(0.0, (cpu_s - float(prev.get("cpu_s", cpu_s))) / dt) if prev else None
        if prev and dt > 1:
            cores_ema = 0.5 * float(prev.get("cores_ema") or (cores_now or 0)) + 0.5 * (cores_now or 0)
        else:
            cores_ema = cores_now
        state["trees"][p.key] = {"ts": now, "cpu_s": cpu_s,
                                 "cores_ema": None if cores_ema is None else round(cores_ema, 2),
                                 "pid": root, "cwd": p.cwd,
                                 "peak_cpu_s": max(cpu_s, float(prev.get("peak_cpu_s") or 0))}
        obs = {"root": root, "pid": root, "key": p.key, "tree_pids": len(members),
               "alive": alive, "cpu_seconds": round(cpu_s, 1),
               "cores_now": None if cores_now is None else round(cores_now, 2),
               "cores_ema": None if cores_ema is None else round(cores_ema, 2),
               "rss_mb": round(rss, 1),
               "cwd": p.cwd, "cmd": _tree_cmd(procs, root),
               "mission_id": procs_mod.mission_id_from_path(p.cwd),
               "queue_runner_descendant": procs_mod.descendant_of_any(procs, root, runners),
               "gate_self": root in self_chain}
        observations.append(obs)
        if obs["gate_self"] or obs["queue_runner_descendant"]:
            continue
        if alive == 0:
            continue
        # 判据（两条同时成立）：
        #  (1) 累计 CPU 秒 ≥ 核数×秒——单调、与采样抖动无关，等价「8 核跑满 10min」；
        #  (2) 当前速率 ≥ 核数阈值的一半——证明它**正在**吃本机，才值得停+迁移。
        # 只满足 (1) 不满足 (2)（历史超限、当前空闲的遗留树）→ advisory 只记账不动手：
        # 慢作业也可能累计很大 CPU 秒，贸然杀=丢工作，留给人/下一轮审阅。
        active = cores_now is not None and cores_now >= float(cfg["hog_cores"]) / 2.0
        if cpu_s >= thr and active:
            obs["threshold_cpu_seconds"] = thr
            violations.append(obs)
        elif cpu_s >= thr:
            obs["threshold_cpu_seconds"] = thr
            obs["advisory"] = ("历史累计超阈值但当前空闲/低速（cores_now=%s）——只记账不动作"
                               % obs["cores_now"])
            advisories.append(obs)
    # 清理 30 分钟没再出现的树
    cut = now - 1800
    state["trees"] = {k: v for k, v in state["trees"].items() if float(v.get("ts") or 0) > cut}
    state["last_scan"] = now
    state["_procs"] = procs  # 供 enforce 复用（不落盘）
    state["_advisories"] = advisories  # 供 scan_and_enforce 记账（不动手）
    return violations, observations, state


def _tree_cmd(procs, root):
    """树里最重的那个进程的指令行（真正的算力所在）；shell 包裹则取内部命令串。"""
    members = procs_mod.subtree(procs, root)
    if not members:
        return ""
    heaviest = max(members, key=lambda pid: procs[pid].own_ticks)
    argv = procs[heaviest].cmdline.split(" ") if procs[heaviest].cmdline else []
    if os.path.basename(argv[0] if argv else "") in ("bash", "sh", "dash") and any(
            f in argv for f in ("-lc", "-c")):
        cmd = queue_mod.cmd_from_argv(argv, shell_wrapped=True)
        if cmd:
            return cmd
    if argv:
        return queue_mod.cmd_from_argv(argv)
    return procs[heaviest].comm


# ── 动作：SIGSTOP → 注记 → 递交 → 杀 ─────────────────────────────────
def enforce(cfg, state, obs, ledger=None, dry_run=None):
    """对单棵违例树执行「停—记—迁—杀」。返回处置 dict（写回执/账本）。"""
    ledger = ledger or ledger_mod.Ledger(cfg)
    dry_run = cfg["dry_run"] if dry_run is None else dry_run
    procs = state.get("_procs") or {}
    members = procs_mod.subtree(procs, obs["root"]) if procs else [obs["root"]]
    result = {"root": obs["root"], "key": obs["key"], "pids": members, "cwd": obs["cwd"],
              "cmd": obs["cmd"], "cpu_seconds": obs["cpu_seconds"], "cores_ema": obs["cores_ema"],
              "mission_id": obs["mission_id"], "dry_run": dry_run, "steps": []}

    ledger.record("hog_detected", mission_id=obs["mission_id"], pid=obs["root"],
                  tree_pids=obs["tree_pids"], tree_root_key=obs["key"],
                  cpu_seconds=obs["cpu_seconds"], cores_now=obs["cores_ema"], rss_mb=obs["rss_mb"],
                  cwd=obs["cwd"], cmd=obs["cmd"], note="违例待迁移（非队列 runner 子孙的本机重计算）",
                  detail={"threshold_cpu_seconds": obs.get("threshold_cpu_seconds")})
    if dry_run:
        result["steps"].append({"step": "dry_run", "action": "跳过 SIGSTOP/递交/杀"})
        return result

    # ① 冻结
    stopped = signal_tree(members, signal.SIGSTOP)
    result["steps"].append({"step": "sigstop", "ok": sum(1 for v in stopped.values() if v),
                            "total": len(members)})

    # ② 推导递交需求
    repo = queue_mod.repo_of(obs["cwd"]) or "TensorPlatform"
    cores = max(1, int(round(obs["cores_ema"] or cfg["hog_cores"])))
    if not obs["cmd"]:
        signal_tree(members, signal.SIGCONT)
        ledger.record("migrate_failed", mission_id=obs["mission_id"], pid=obs["root"],
                      tree_pids=obs["tree_pids"], tree_root_key=obs["key"], cwd=obs["cwd"],
                      note="无法从 /proc 还原命令 → 已 SIGCONT 恢复，未杀进程（不丢工作）")
        result["steps"].append({"step": "migrate", "ok": False, "reason": "cmd 不可还原"})
        return result

    # ③ 递交（stage_dir=原 worktree → 节点上跑同一棵树，工作不丢）
    stage_dir = obs["cwd"] if os.path.isdir(obs["cwd"]) else None
    try:
        sub = queue_mod.submit(cfg, repo=repo, cmd=obs["cmd"], cores=cores, timeout=7200,
                               stage_dir=stage_dir, mid=obs["mission_id"],
                               submitter="local-hog-migration")
        result["steps"].append({"step": "submit", "ok": True, **sub})
        result["jid"] = sub["jid"]
    except Exception as e:
        signal_tree(members, signal.SIGCONT)
        ledger.record("migrate_failed", mission_id=obs["mission_id"], pid=obs["root"],
                      tree_pids=obs["tree_pids"], tree_root_key=obs["key"],
                      cpu_seconds=obs["cpu_seconds"], cwd=obs["cwd"], cmd=obs["cmd"],
                      note="递交失败 → SIGCONT 恢复，未杀进程（工作不丢）：%s" % e)
        result["steps"].append({"step": "submit", "ok": False, "reason": str(e)[:400]})
        return result

    # ④ 注记 + 杀本地
    jid = result["jid"]
    ledger.annotate_job(jid, "违例迁移来源 local-pid=%d mid=%s（原为本机重计算，已迁移）"
                        % (obs["root"], obs["mission_id"]))
    killed = signal_tree(members, signal.SIGKILL)
    if cfg.get("kill_grace"):
        time.sleep(min(1.0, float(cfg["kill_grace"])))
    remain = wait_gone(members, timeout=5.0)
    result["steps"].append({"step": "sigkill", "ok": sum(1 for v in killed.values() if v), "remain": remain})
    ledger.record("migrated", mission_id=obs["mission_id"], pid=obs["root"],
                  tree_pids=obs["tree_pids"], tree_root_key=obs["key"],
                  cpu_seconds=obs["cpu_seconds"], cores_now=obs["cores_ema"], rss_mb=obs["rss_mb"],
                  cwd=obs["cwd"], cmd=obs["cmd"], jid=jid,
                  note="已迁移：本地树已杀，同命令在队列排队（stage_dir=原 worktree）",
                  detail={"cores_requested": cores, "remain_after_kill": remain,
                          "child_outputs_preserved_in": obs["cwd"]})
    return result


def scan_and_enforce(cfg, proc_root="/proc", apply=True):
    state = load_state(cfg)
    violations, observations, state = scan(cfg, proc_root=proc_root, state=state)
    advisories = state.pop("_advisories", [])
    actions = []
    ledger = ledger_mod.Ledger(cfg)
    # 上线预热窗（默认 600s）：守护进程每轮重启后先「只看不动」，让账本先攒基线，
    # 也避免刚接管的瞬间与在飞任务对撞。窗口内仍记账（note 标 warmup），窗口后照常强拦。
    started = float(state.get("started_at") or 0)
    now_ts = float(state.get("last_scan") or time.time())
    if not started:
        started = now_ts
        state["started_at"] = started
    warm = now_ts - started < float(cfg.get("warmup_seconds", 600))
    if apply:
        for obs in violations:
            actions.append(enforce(cfg, state, obs, ledger=ledger, dry_run=warm))
            if warm and actions:
                actions[-1]["warmup"] = True
    # advisory（历史超限但当前空闲）只记账，每小时每树最多一条
    adv_state = state.setdefault("advisories", {})
    for obs in advisories:
        last = float(adv_state.get(obs["key"], 0))
        if state.get("last_scan", 0) - last < 3600:
            continue
        adv_state[obs["key"]] = state.get("last_scan", 0)
        ledger.record("leak_advisory", mission_id=obs["mission_id"], pid=obs["root"],
                      tree_pids=obs["tree_pids"], tree_root_key=obs["key"],
                      cpu_seconds=obs["cpu_seconds"], cores_now=obs["cores_now"],
                      rss_mb=obs["rss_mb"], cwd=obs["cwd"], cmd=obs["cmd"],
                      note="历史累计 CPU 超阈值但当前空闲：只记账不动手（防误杀慢作业）")
    state.pop("_procs", None)
    save_state(cfg, state)
    return {"violations": violations, "advisories": advisories, "observations": observations,
            "actions": actions, "last_scan": state.get("last_scan")}


def loop(cfg, interval=None):
    interval = interval or cfg["scan_interval"]
    pathlib.Path(cfg["events_path"]).parent.mkdir(parents=True, exist_ok=True)
    while True:
        ts = time.strftime("[%m-%d %H:%M:%S]")
        try:
            r = scan_and_enforce(cfg, apply=cfg["enforce"])
            line = "%s compute_gate scan：violations=%d advisories=%d trees=%d actions=%d" % (
                ts, len(r["violations"]), len(r.get("advisories") or []),
                len(r["observations"]), len(r["actions"]))
            with open(str(pathlib.Path(cfg["events_path"]).parent / "guard.log"), "a") as f:
                f.write(line + "\n")
        except Exception as e:
            with open(str(pathlib.Path(cfg["events_path"]).parent / "guard.log"), "a") as f:
                f.write("%s scan error %s: %s\n" % (ts, type(e).__name__, e))
        time.sleep(float(interval))