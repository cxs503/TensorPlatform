#!/usr/bin/env python3
"""tp_autoheal —— 元守护（owner 2026-10-31「完全依赖 AI 的运行机制」令）
冷启动 + 死亡免疫：一条进程看住全部常设守护，死了自动拉活，冷机一键全栈自起。
人不再当「继续」按钮：沉默=对可逆内部动作放行（审计+回滚兜底），人只处理残差
（对外/真金白银/赦免/署名——见 AUTONOMY_CHARTER.md）。
60s 循环：PG/API 前置健康 → 逐守护 pgrep → 缺则 setsid 拉起（冷却+翻动熔断）。
owner 否决面 = /root/tp-agent/autoheal_config.json 的 enabled 开关（指令面配置化已批）。
"""
import json, os, subprocess, time, datetime

LOG = "/var/log/tp-fleet/autoheal.log"
STATE = "/root/tp-agent/autoheal_state.json"
CONFIG = "/root/tp-agent/autoheal_config.json"
API_HEALTH = "http://127.0.0.1:8010/health"
CYCLE = 60
COOLDOWN = 120        # 同一守护两次拉起最小间隔
FLAP_WINDOW = 1800    # 翻动熔断窗口
FLAP_MAX = 5          # 窗口内重启超过此数 → 自动停用待 owner

# name: (pgrep 模式, 拉起命令, 默认启用, 说明)
REGISTRY = {
    "fleet":     ("fleet_supervisor.sh",          "/usr/bin/bash /root/tp-agent/fleet_supervisor.sh", True,  "32 工人舰队+模型门+配额兜底"),
    "dispatcher":("campaign_dispatcher.py",       "/usr/bin/python3 /root/tp-agent/campaign_dispatcher.py", True, "战役 DAG 派发+v5 自主提案波"),
    "pusher":    ("tp_pusher.py",                 "/usr/bin/python3 /root/tp-agent/tp_pusher.py", True,  "成熟成果→GitHub CI→PR"),
    "report_watch": ("report_watch.py",           "/usr/bin/python3 /root/tp-agent/report_watch.py", True, "报告断更补账（owner 常设）"),
    "qa_factory": ("qa_factory/daemon.py", "/usr/bin/python3 /root/tp-agent/work/m15877/tools/qa_factory/daemon.py", True,
                   "QA 产线：机器三查分档 + 一致性门授权清账（flock/心跳可观测）"),
    "report_qa": ("report_qa.py",                 "/usr/bin/python3 /root/tp-agent/report_qa.py", True, "报告论文级质检"),
    "guide":     ("guide_tracker.py",             "/usr/bin/python3 /root/tp-agent/guide_tracker.py", True, "指南对标 KPI 看板（owner 常设）"),
    "wave":      ("wave_cycle.py",                "/usr/bin/python3 /root/tp-agent/wave_cycle.py", True, "波次循环+议事会（owner 10-30 放行，护栏内置）"),
    "credit":    ("credit_ledger.py",             "/usr/bin/python3 /root/tp-agent/credit_ledger.py", True, "信用账本（owner 10-31 批）"),
    "shadow":    ("shadow_merge.py",              "/usr/bin/python3 /root/tp-agent/shadow_merge.py", True, "影子合并试运行（owner 10-31 批，L3 无人代合并）"),
    "shift":     ("operator_shift.py",            "/usr/bin/python3 /root/tp-agent/operator_shift.py", False, "值班长班次（已被提案波承接+曾是重复派单源，默认停）"),
    "approver":  ("auto_approver.py",             "/usr/bin/bash /root/tp-agent/auto_approver_loop.sh", False, "全自动审批器（待 owner 明示启用）"),
    "idle_guard":  ("idle_guard.py",  "/usr/bin/python3 /root/tp-agent/idle_guard.py", True, "空闲冷启动（owner 2026-11-01：无running≥10min→kill哑dispatcher+冷启动引导单）"),
    "runtime_sync": ("runtime_sync.py", "/usr/bin/python3 /root/tp-agent/runtime_sync.py", True, "生产树追平 GitHub main（owner 2026-11-01 点名放行）"),
    "eco_patrol":   ("eco_patrol.py",   "/usr/bin/python3 /root/tp-agent/eco_patrol.py", True, "生态巡更派发器 11 库（owner 2026-11-01 点名放行）"),
    "selfstarter": ("selfstarter.py", "/usr/bin/python3 /root/tp-agent/selfstarter.py", True, "全面agent自找活（owner 2026-11-01：generalist席空闲≥20min→钉扎自驱单）"),
    "agent_directory": ("agent_directory.py", "/usr/bin/python3 /root/tp-agent/agent_directory.py", True, "生态发现面+个体记忆边车 :8011（owner 2026-11-01 批）"),
    "paper_line": ("paper_line.py", "/usr/bin/python3 /root/tp-agent/paper_line.py", True, "顶刊论文生产线守护（owner 2026-11-01 批：挖矿调度+停链看门狗+帽）"),
    "planner": ("planner_line.py", "/usr/bin/python3 /root/tp-agent/planner_line.py", True, "常驻任务规划师线（owner 2026-11-01 可以冗余：与提案波/议事会三重冗余，6h 班）"),
    "fleet_governor": ("fleet_governor.py", "/usr/bin/python3 /root/tp-agent/fleet_governor.py", True, "席位治理：淘汰不干活+补员+冗余（owner 2026-11-01 批）"),
}


def log(msg):
    with open(LOG, "a") as f:
        f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}\n")


def load_json(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def save_json(path, obj):
    json.dump(obj, open(path, "w"), ensure_ascii=False, indent=1)


def alive(pattern):
    r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
    # 排除自身与 pgrep/ssh 噪声：模式都指向具体脚本名，匹配即真
    return bool(r.stdout.strip())


def api_up():
    r = subprocess.run(["curl", "-s", "-m", "5", "-o", "/dev/null", "-w", "%{http_code}",
                        API_HEALTH], capture_output=True, text=True)
    return r.stdout.strip() in ("200", "401", "403", "404")  # 端口活着即可（鉴权/路由差异不算死）


def spawn(cmd):
    subprocess.Popen(["setsid", "nohup"] + cmd.split(),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, start_new_session=True)


def main():
    log("=== tp_autoheal 启动（元守护：冷启动+死亡免疫；owner 残差治理面=autoheal_config.json）===")
    while True:
        try:
            state = load_json(STATE, {"restarts": {}, "disabled": {}})
            cfg = load_json(CONFIG, {})
            if not api_up():
                log("WARN API :8010 无响应——守护依赖它，本轮跳过（API 由容器编排保活）")
                time.sleep(CYCLE)
                continue
            now = time.time()
            for name, (pattern, cmd, default_on, desc) in REGISTRY.items():
                enabled = cfg.get(name, {}).get("enabled", default_on)
                if not enabled:
                    continue
                if state["disabled"].get(name):
                    continue  # 翻动熔断后停用，等 owner 开关复位
                if alive(pattern):
                    continue
                last = state["restarts"].get(name, {}).get("t", 0)
                if now - last < COOLDOWN:
                    continue
                hist = [t for t in state["restarts"].get(name, {}).get("hist", [])
                        if now - t < FLAP_WINDOW]
                hist.append(now)
                if len(hist) > FLAP_MAX:
                    state["disabled"][name] = f"flap x{len(hist)}/{FLAP_WINDOW}s"
                    save_json(STATE, state)
                    log(f"FLAP-BREAK {name}（{desc}）{FLAP_WINDOW}s 内 {len(hist)} 次死亡——停用待 owner（config 复位）")
                    continue
                state["restarts"][name] = {"t": now, "hist": hist}
                save_json(STATE, state)
                spawn(cmd)
                log(f"REVIVE {name}（{desc}）← {cmd} [窗口内第 {len(hist)} 次]")
        except Exception as e:
            log(f"ERROR {type(e).__name__}: {str(e)[:200]}")
        time.sleep(CYCLE)


if __name__ == "__main__":
    main()
