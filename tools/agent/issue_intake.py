#!/usr/bin/env python3
"""GitHub Issue 入口桥（#23 M1）：带 agent-task 标签的 Issue → dev mission。

owner/GPT 2026-10-19 #23 约定的 Phase A 任务入口：
  - 只读 GitHub 元数据（labels=agent-task & state=open），凭据
    agent_work/github_read.env（600 不入库），绝不注入进 mission；
  - 幂等双保险：state 文件（issue → mission 终态记录）+ 平台 mission goal
    标记（"GitHub Issue #N"）扫描，两者任一命中即跳过；
  - fail closed 同 operator_shift 标准（GPT 复审后）：非 200/传输错误/
    结构损坏 → 记错误摘要、退出码 2、不派发；
  - 交付=jailed worktree commit + request_acceptance；PR 由门禁推送线
    （值班/人工）从验收过的分支推，普通 mission 无 GitHub 写权限。

门：OFF 开关（agent_work/issue_intake.OFF）→ 凭据 → GitHub 读 → 去重 →
逐 Issue 派发（每轮最多 MAX_PER_ROUND 条，防积压风暴）。

cron（flock 串行；手动跑也请走同一 flock）：
  7,27,47 * * * * flock -n .../issue_intake.lock bash -c '
    set -a; source .../agent_work/github_read.env;
            source .../agent_work/operator.env; set +a
    python .../tools/agent/issue_intake.py' >> .../issue_intake.log 2>&1
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
from pathlib import Path

ECO = Path("/srv/tensor/shared/tensor_eco")
OFF_SWITCH = ECO / "agent_work" / "issue_intake.OFF"
STATE = ECO / "agent_work" / "issue_intake_state.json"
HELPER = ECO / "TensorPlatform" / "tools" / "agent" / "dispatch_helper.py"
PY = "/srv/tensor/shared/venvs/tensor_eco/bin/python"
REPO_API = "https://api.github.com/repos/cxs503/TensorPlatform"
TASK_LABEL = os.environ.get("INTAKE_LABEL", "agent-task")
MARK_TMPL = "GitHub Issue #{n}"          # mission goal 里的幂等标记
BUDGET = "160,14400,6000000"             # 有限任务（GPT：M1 限定范围）
MAX_PER_ROUND = 2


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"{ts} {msg}", flush=True)


class IssueFetchError(RuntimeError):
    """GitHub Issue 列表读取失败/结构异常——fail closed，不派发。"""


def _gh_request(path: str, token: str):
    """单次 GitHub API GET：返回 (status, parsed)。传输错误抛 RuntimeError。"""
    req = urllib.request.Request(
        REPO_API + path, headers={"Authorization": "Bearer " + token,
                                  "Accept": "application/vnd.github+json",
                                  "User-Agent": "tensor-eco-ops"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"null")
        except Exception:  # noqa: BLE001
            return exc.code, None
    except Exception as exc:  # noqa: BLE001  传输层
        raise RuntimeError(f"GET {path} 传输错误: {exc!r}") from exc
    try:
        return status, json.loads(raw or b"null")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"GET {path} 响应非 JSON: {exc}") from exc


def fetch_open_issues(token: str) -> list[dict]:
    """带 TASK_LABEL 的 open Issue 列表；任何失败抛 IssueFetchError。"""
    try:
        code, body = _gh_request(
            f"/issues?labels={TASK_LABEL}&state=open&per_page=50", token)
    except RuntimeError as exc:
        raise IssueFetchError(str(exc)) from exc
    if code != 200:
        raise IssueFetchError(f"GET /issues?labels={TASK_LABEL} HTTP {code}")
    if not isinstance(body, list):
        raise IssueFetchError(f"响应非数组: {type(body).__name__}")
    for i, it in enumerate(body):
        if not isinstance(it, dict) or not isinstance(it.get("number"), int):
            raise IssueFetchError(f"issues[{i}] 缺 number 或非对象")
    # PR 也是 issues 资源：有 pull_request 字段的排除
    return [it for it in body if not it.get("pull_request")]


def load_state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    tmp = str(STATE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def api(method: str, path: str, body: dict | None = None, tok: str | None = None):
    headers = {"Content-Type": "application/json"}
    if tok:
        headers["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(
        "http://localhost:3000/api/v1" + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"null")
        except Exception:  # noqa: BLE001
            return exc.code, {}
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"{method} {path} 传输错误: {exc!r}") from exc
    return status, json.loads(raw or b"null")


def mission_goals(tok: str) -> list[dict]:
    """平台 mission 摘要（幂等第二保险）。失败抛 RuntimeError（fail closed）。"""
    code, body = api("GET", "/tensor/missions?limit=100", tok=tok)
    if code != 200 or not isinstance(body, dict) or not isinstance(
            body.get("items"), list):
        raise RuntimeError(f"GET /tensor/missions HTTP {code} 或结构异常")
    return body["items"]


GOAL_TEMPLATE = """TensorPlatform 代码任务（GitHub Issue #{n}）：{title}

{body}

要求（全部满足才算完成）：
1. 在本 worktree 定位并修复；改动最小、聚焦该 Issue，不顺手重构。
2. 必须补回归测试（tests/ 下，能独立证明修复前后行为差异）。
3. run_tests 全绿后 commit（中文提交信息，引用 Issue #{n}）。
4. request_acceptance（branch=agent/m<id>，summary 给出测试证据摘要，
   evidence_files 填测试输出/判据工件路径）。

红线：不 git push；不动用户/角色/组织表；不删数据；kill 仅精确 PID；
不碰 GPU0-4 与跳板机端口；yueyue 的 /tmp 不动。预算纪律：交付优先，
预算过半未开写测试即停新诊断先落交付。"""


def dispatch_one(issue: dict, base_ref: str, env: dict) -> dict:
    n = issue["number"]
    title = str(issue.get("title") or "")[:200]
    body_txt = str(issue.get("body") or "")[:4000]
    goal = GOAL_TEMPLATE.format(n=n, title=title, body=body_txt)
    goal_file = ECO / "agent_work" / f"intake_goal_{n}.txt"
    goal_file.write_text(goal, encoding="utf-8")
    model = os.environ.get("INTAKE_MODEL", "qwen3.7-plus")
    r = subprocess.run(
        [PY, str(HELPER), "--repo", "TensorPlatform", "--base-ref", base_ref,
         "--model", model, "--runner", "native", "--budget", BUDGET,
         "--origin", "agent", "--goal-file", str(goal_file)],
        capture_output=True, text=True, timeout=120, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"dispatch_helper 失败: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--retry-failed", action="store_true",
                    help="重试 state 里标记 failed 的 Issue（默认跳过）")
    args = ap.parse_args()

    if OFF_SWITCH.exists():
        log("SKIP: OFF 开关存在（issue_intake.OFF）")
        return 0
    token = os.environ.get("GH_TOKEN")
    user, pw = os.environ.get("OPERATOR_USER"), os.environ.get("OPERATOR_PASS")
    if not (token and user and pw):
        log("ERROR: GH_TOKEN/OPERATOR_USER/OPERATOR_PASS 未设置"
            "（source github_read.env + operator.env）")
        return 1

    try:
        issues = fetch_open_issues(token)
    except IssueFetchError as exc:
        log(f"ERROR: Issue 列表不可得，fail closed 不派发（{exc}）")
        return 2

    if not issues:
        log("OK: 无带标签 open Issue")
        return 0

    code, body = api("POST", "/auth/login", {"username": user, "password": pw})
    if code != 200:
        log(f"ERROR: 登录失败 HTTP {code}")
        return 1
    tok = body["access_token"]
    try:
        missions = mission_goals(tok)
    except RuntimeError as exc:
        log(f"ERROR: mission 列表不可得，fail closed 不派发（{exc}）")
        return 2

    state = load_state()
    goals_txt = "\n".join(str(m.get("goal") or "") for m in missions)
    dispatched = 0
    for issue in issues:
        if dispatched >= MAX_PER_ROUND:
            log(f"SKIP: 本轮已达 MAX_PER_ROUND={MAX_PER_ROUND}，下轮继续")
            break
        n = issue["number"]
        rec = state.get(str(n))
        if rec and rec.get("mission") is not None:
            log(f"SKIP: #{n} 已派发过（m{rec.get('mission')}），幂等跳过")
            continue
        if rec and rec.get("failed") and not args.retry_failed:
            log(f"SKIP: #{n} 上次派发失败（{str(rec.get('error'))[:80]}），"
                "待 --retry-failed 或人工处理")
            continue
        if MARK_TMPL.format(n=n) in goals_txt:
            log(f"SKIP: #{n} 平台已有 goal 标记，幂等跳过（第二保险）")
            continue
        r = subprocess.run(["git", "-C", str(ECO / "TensorPlatform"),
                            "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True, timeout=30)
        base_ref = r.stdout.strip() or "main"
        try:
            out = dispatch_one(issue, base_ref,
                               env=dict(os.environ, REVIEW_USER=user,
                                        REVIEW_PASS=pw))
        except Exception as exc:  # noqa: BLE001  单条失败记录不中断整轮
            state[str(n)] = {"failed": True, "error": str(exc)[:300],
                             "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ")}
            save_state(state)
            log(f"ERROR: #{n} 派发失败：{exc}")
            continue
        state[str(n)] = {"mission": out.get("id"),
                         "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "title": str(issue.get("title") or "")[:120]}
        save_state(state)
        log(f"DISPATCHED: #{n} → m{out.get('id')} base={base_ref}")
        dispatched += 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
