#!/usr/bin/env python3
"""operator 值班派发器：把 operator 角色做成平台 mission 班次（P7）。

owner 2026-10-19 指令：operator 角色进编制、给足权限、自主行动，人与前任
逐渐退出。本脚本由 cron 定期触发，决定是否派发一班 platform-operator 值班：

  1. OFF 开关：agent_work/operator_shift.OFF 存在 → 直接退出（熔断由人拉）
  2. 已有值班班次在飞（queued/claimed/running）→ 不重复派
  3. 有开发 mission 在飞 → 上一班开始 > SHIFT_GAP 秒即续班（密集值守）
  4. 平台空闲 → 距上一班 > IDLE_GAP 秒才派例行班
  5. 凭据缺 → 退出报错（不静默降级）

派发本身走 dispatch_helper（注入常驻记忆 platform-operator，origin=agent，
worktree=独立 jailed agent/m<id>，push 基建级禁用）。值班 goal 由本脚本
生成（模板+日期），交付=operator_log/<date>.md commit + request_acceptance。

用法（cron，operator.env 不入库 chmod 600）：
  flock -n .../operator_shift.lock bash -c '
    set -a; source .../agent_work/operator.env; set +a
    python .../tools/agent/operator_shift.py' >> .../operator_shift.log 2>&1
手动冒烟：加 --force 跳过间隔门（在飞/OFF 门不跳）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = os.environ.get("TENSOR_API_BASE", "http://localhost:3000/api/v1")
ECO = Path("/srv/tensor/shared/tensor_eco")
OFF_SWITCH = ECO / "agent_work" / "operator_shift.OFF"
LOCK = ECO / "operator_shift.lock"          # flock 由 cron 层持
HELPER = ECO / "TensorPlatform" / "tools" / "agent" / "dispatch_helper.py"
PY = "/srv/tensor/shared/venvs/tensor_eco/bin/python"
OPERATOR_MODEL = "glm-5.2"                  # P1 角色映射：operator=最强常驻
OPERATOR_REPO = "TensorPlatform"            # 交付物=operator_log，平台侧
BUDGET = "40,3600,2000000"                  # tool_calls,wall_seconds,tokens
SHIFT_GAP = 25 * 60                         # 密集值守：有开发在飞时隔 25 分钟
IDLE_GAP = 6 * 3600                         # 平台空闲：6 小时一班例行
IN_FLIGHT = ("queued", "claimed", "running")
MARKER = "platform-operator 值班班次"        # goal 里的身份标记（识别本角色班次）


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"{ts} {msg}", flush=True)


def api(method: str, path: str, body: dict | None = None, tok: str | None = None):
    headers = {"Content-Type": "application/json"}
    if tok:
        headers["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode() if body is not None else None,
        method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"null")
        except Exception:  # noqa: BLE001
            return exc.code, {}


GOAL_TEMPLATE = """platform-operator 值班班次 {date}（角色交接期：例行自主，新颖上报）

你是平台的 operator agent（身份见下方常驻记忆）。这一班先完整读
identity.md 与 playbook.md 再动手，产出标准=前任做得到的你要做得到：

1. 巡检五步（playbook「巡检清单」逐项）：在飞心跳鲜度 / 守护计数=7 /
   中继健康 / 网关冷却态 / 终态 mission result。
2. 自主处置（例行级，不再请示）：infra 类终局（agent_lost/budget_exceeded/
   agent_crash/gateway_unavailable）按剧本 refill 续班；守护缺员按剧本换血；
   冷却态过期按剧本清理。honest FAIL 重派与新颖故障：定性记录标 [上报]，
   不动手。API 动作凭据见 identity.md「工具面」。
3. 交付（验收判据，全部满足才算完）：
   a. 在本 worktree 新建 operator_log/{date}.md——结论式记录：巡检结论
      （每步一行+关键数字直读不手抄）、处置动作（mission id+动作+理由）、
      [上报] 项、给下一班的建议。禁止过程流水账。
   b. git add 该文件并 commit（消息："operator log {date}: <一句话摘要>"）。
   c. request_acceptance（branch=agent/m<id>，head=commit 后的 HEAD，
      summary 一句话讲清巡检结论与处置）。
4. 红线（不可违反）：不 git push（worktree 已基建禁用）；不动用户/角色/
   组织表；不删任何数据；kill 仅精确 PID；不碰 GPU0-4 与跳板机端口；
   yueyue 的 /tmp 不动；psql 写操作仅限剧本明列的修复动作。
预算 40 工具调用，省着用：巡检 SQL 合并单条；日志一次写完再 commit。
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="跳过间隔门（冒烟用）；OFF/在飞门不跳")
    args = ap.parse_args()

    if OFF_SWITCH.exists():
        log("SKIP: OFF 开关存在（operator_shift.OFF）")
        return 0
    user, pw = os.environ.get("OPERATOR_USER"), os.environ.get("OPERATOR_PASS")
    if not (user and pw):
        log("ERROR: OPERATOR_USER/OPERATOR_PASS 未设置（source operator.env）")
        return 1
    code, body = api("POST", "/auth/login", {"username": user, "password": pw})
    if code != 200:
        log(f"ERROR: operator 登录失败 {code}")
        return 1
    tok = body["access_token"]

    code, body = api("GET", "/tensor/missions?limit=100", tok=tok)
    items = body.get("items", []) if code == 200 else []
    shifts = [m for m in items if MARKER in (m.get("goal") or "")]
    devs = [m for m in items if MARKER not in (m.get("goal") or "")]

    live = [m for m in shifts if m.get("status") in IN_FLIGHT]
    if live:
        log(f"SKIP: 值班班次 m{live[0]['id']} 在飞（{live[0]['status']}）")
        return 0

    last_started = 0.0
    for m in shifts:  # items 按创建倒序；取最近一班起点
        ts = m.get("created_at") or ""
        try:
            t = datetime.fromisoformat(
                ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        last_started = max(last_started, t)
    since = time.time() - last_started if last_started else None

    if not args.force:
        dev_live = [m for m in devs if m.get("status") in IN_FLIGHT]
        gap = SHIFT_GAP if dev_live else IDLE_GAP
        mode = "密集值守" if dev_live else "平台空闲例行"
        if since is not None and since < gap:
            log(f"SKIP: {mode}间隔未到（距上一班 {int(since)}s < {gap}s，"
                f"开发在飞 {len(dev_live)}）")
            return 0

    # base_ref=运行线当前分支（值班 worktree 从这里分叉，auto_approver 按
    # 此判「新 commit」）；分支名取共享 checkout 当前 HEAD 所在分支
    r = subprocess.run(["git", "-C", str(ECO / OPERATOR_REPO),
                        "rev-parse", "--abbrev-ref", "HEAD"],
                       capture_output=True, text=True, timeout=30)
    base_ref = r.stdout.strip() or "main"

    date = time.strftime("%Y-%m-%d")
    goal_file = ECO / "agent_work" / f"operator_shift_goal_{date}.txt"
    goal_file.write_text(GOAL_TEMPLATE.format(date=date), encoding="utf-8")

    env = dict(os.environ, REVIEW_USER=user, REVIEW_PASS=pw)
    r = subprocess.run(
        [PY, str(HELPER), "--repo", OPERATOR_REPO, "--base-ref", base_ref,
         "--model", OPERATOR_MODEL, "--runner", "native", "--budget", BUDGET,
         "--origin", "agent", "--memory-key", "platform-operator",
         "--goal-file", str(goal_file)],
        capture_output=True, text=True, timeout=120, env=env)
    if r.returncode != 0:
        log(f"ERROR: dispatch_helper 失败: {r.stderr.strip()[:300]}")
        return 1
    out = json.loads(r.stdout)
    log(f"DISPATCHED: m{out.get('id')} base={base_ref} model={OPERATOR_MODEL} "
        f"memory={out.get('memory_injected')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
