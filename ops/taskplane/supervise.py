#!/usr/bin/env python3
"""任务平面统一监护入口（PR-9 · AOG P1）。

P0 审计（报告 #3554）定谳：任务平面全部宿主 cron/nohup 临时化，宿主重装
= 舰队失忆。本脚本把「拉起 + 巡检补拉」收进库内：compose 起容器后由它
按 taskplane.manifest.yaml 的 enabled 集拉起全部守护，30s 巡检补拉死亡
进程——纪律沿用 fleet_supervisor「只补缺员、从不杀存活」。

门控守护（宪章/待裁，默认不开）：
  AUTO_APPROVER_ON=1  auto_approver_loop.sh（owner 宪章「auto_approver 默认停」）
  ECO_PATROL_ON=1     eco_patrol.py（治理面已被 merge_governor patrol 吸收）
  TP_PUSHER_ON=1      tp_pusher.py（收库时点生产未跑）

增删守护：改 manifest + 本文件 DAEMONS/GATED + backend/tests/
test_taskplane_infra.py 固定清单，三处一致（CI 红门强制）。
"""
import os
import pathlib
import subprocess
import sys
import time

BASE = pathlib.Path(os.environ.get("TP_BASE", "/root/tp-agent"))
LOGDIR = pathlib.Path("/var/log/taskplane")
POLL = 30

# manifest enabled: true 的 16 项（顺序即拉起序：舰队底座优先）
# 注意：生产实况以 /proc 探针为准（tp_pusher/selfstarter 均相对路径启动，
# 绝对路径 ps grep 会漏检——收库时曾因此误判 tp_pusher 停机）。
DAEMONS = [
    "tp_autoheal.py",        # 元守护最先（其余死亡免疫）
    "fleet_supervisor.sh",   # tp_worker 舰队
    "tp_pusher.py",          # 成熟成果上 GitHub
    "agent_directory.py",
    "campaign_dispatcher.py",
    "credit_ledger.py",
    "fleet_governor.py",
    "guide_tracker.py",
    "merge_governor.py",
    "paper_line.py",
    "report_qa.py",
    "report_watch.py",
    "runtime_sync.py",
    "selfstarter.py",
    "shadow_merge.py",
    "wave_cycle.py",
]

# 门控守护：环境变量=1 才拉起（宪章/owner 待裁项，默认全关）
GATED = {
    "auto_approver_loop.sh": "AUTO_APPROVER_ON",
    "eco_patrol.py": "ECO_PATROL_ON",
}


def running(script: str) -> bool:
    """argv 任一 token 等于 <script> 或以 /<script> 结尾即在跑（相对/绝对
    路径启动都覆盖——生产 selfstarter 即相对路径启动）；不杀任何进程。"""
    self_name = pathlib.Path(__file__).name
    for p in pathlib.Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            cmd = (p / "cmdline").read_bytes().split(b"\0")
        except Exception:
            continue
        toks = [c.decode(errors="ignore") for c in cmd if c]
        if not toks or toks[0].endswith(self_name) or any(
                t.endswith("/" + self_name) for t in toks):
            continue  # 探活器自身与同名兄弟不计数
        if any(t == script or t.endswith("/" + script) for t in toks):
            return True
    return False


def start(script: str):
    log = LOGDIR / (script + ".log")
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as lf:
        interpreter = ["bash"] if script.endswith(".sh") else ["python3"]
        subprocess.Popen(interpreter + [str(BASE / script)],
                         stdout=lf, stderr=lf, cwd=str(BASE),
                         start_new_session=True)
    print("started %s -> %s" % (script, log), flush=True)


def main():
    LOGDIR.mkdir(parents=True, exist_ok=True)
    print("taskplane supervisor up, base=%s, daemons=%d, gated=%d"
          % (BASE, len(DAEMONS), len(GATED)), flush=True)
    while True:
        todo = list(DAEMONS) + [s for s, var in GATED.items()
                                if os.environ.get(var) == "1"]
        for script in todo:
            try:
                if not running(script):
                    start(script)
            except Exception as e:  # 单项失败不拖垮监护
                print("supervise %s error: %r" % (script, e), flush=True)
        time.sleep(POLL)


if __name__ == "__main__":
    sys.exit(main())
