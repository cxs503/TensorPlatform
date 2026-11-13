#!/usr/bin/env python3
"""compute_gate.policy —— 计算递交门策略常量与配置装载。

owner 2026-11-13 计算调度令（架构级）：
  「应该建计算调度系统，agent 计算提交给调度系统，跑在哪里是调度系统的事」
本模块只装**策略**（阈值/白名单/路径），不含动作逻辑；动作在 guard / preflight / compute。

阈值可被环境变量或 --config JSON 覆盖（机器门与红队试验要能改阈值可重复）：
  TP_COMPUTE_GATE_CONFIG=/path/to/config.json
config 键（全部可选）：
  hog_cores           触发核数（默认 8）
  hog_seconds         持续秒数（默认 600）
  scan_interval       扫描周期秒（默认 60）
  work_roots          托管 worktree 根（默认 ["/root/tp-agent/work"]）
  queue_dir           cluster_queue 所在目录（默认 /root/tp-agent/cluster）
  state_path          守卫状态文件（默认 <queue_dir>/compute_gate/state.json）
  events_path         事件账本 JSONL（默认 <queue_dir>/compute_gate/events.jsonl）
  kill_grace          迁移前 SIGTERM→SIGKILL 间隔秒（默认 5）
  dry_run             只记账不动作（默认 False；红队/演练用）
  enforce             是否真的 SIGSTOP/迁移（默认 True）
"""
import json
import os
import pathlib

DEFAULTS = {
    "hog_cores": 8.0,
    "hog_seconds": 600.0,
    "scan_interval": 60.0,
    "work_roots": ["/root/tp-agent/work"],
    "queue_dir": "/root/tp-agent/cluster",
    "state_path": None,
    "events_path": None,
    "kill_grace": 5.0,
    "warmup_seconds": 600.0,   # 守护进程启动后的观察窗（只记账不动作），窗口后转强拦
    "dry_run": False,
    "enforce": True,
    # 队列 runner 特征串：命中者的**子孙**永不判违例（调度器派下来的活本来就是它的活）
    "queue_runner_markers": [
        "cluster_queue.py --sched",
        "cluster_exec.py",
        "cluster/sched_launch.sh",
        "/root/tp-agent/cluster/jobs/",
    ],
    "protected_commands": [
        # 绝不能动：平台控制面/数据库/ssh/容器运行时
        "postgres", "psql", "sshd", "systemd", "dockerd", "containerd", "supervisord",
    ],
}

ENV_VAR = "TP_COMPUTE_GATE_CONFIG"


def queue_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("TP_QUEUE_DIR") or DEFAULTS["queue_dir"])


def load_config(config_path=None) -> dict:
    """默认 → 配置文件 → 环境变量，逐层覆盖；返回扁平 dict。"""
    cfg = dict(DEFAULTS)
    cfg["work_roots"] = list(DEFAULTS["work_roots"])
    path = config_path or os.environ.get(ENV_VAR)
    if path and pathlib.Path(path).exists():
        try:
            cfg.update(json.loads(pathlib.Path(path).read_text()))
        except Exception:
            pass
    if os.environ.get("TP_GATE_HOG_CORES"):
        cfg["hog_cores"] = float(os.environ["TP_GATE_HOG_CORES"])
    if os.environ.get("TP_GATE_HOG_SECONDS"):
        cfg["hog_seconds"] = float(os.environ["TP_GATE_HOG_SECONDS"])
    if os.environ.get("TP_GATE_DRY_RUN"):
        cfg["dry_run"] = os.environ["TP_GATE_DRY_RUN"] not in ("0", "false", "")
    if os.environ.get("TP_GATE_ENFORCE"):
        cfg["enforce"] = os.environ["TP_GATE_ENFORCE"] not in ("0", "false", "")
    qd = queue_dir()
    if not cfg.get("state_path"):
        cfg["state_path"] = str(qd / "compute_gate" / "state.json")
    if not cfg.get("events_path"):
        cfg["events_path"] = str(qd / "compute_gate" / "events.jsonl")
    return cfg


def cpu_threshold_seconds(cfg) -> float:
    """累计 CPU 秒阈值 = 核数 × 持续秒。

    用**累计 CPU 时间**而非瞬时采样判「持续 ≥N 核」：单调、不因采样抖动翻烧饼，
    且对「8 核跑满 10min」与「16 核跑 5min」等价处理（都是 80 核·分钟实算量）。
    """
    return float(cfg["hog_cores"]) * float(cfg["hog_seconds"])