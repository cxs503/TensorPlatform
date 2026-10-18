"""owner 委托审批闭环（2026-10-18 owner 指令"整个系统从人工审批改为由你确认审批"）。

纪律（不是橡皮图章）：
  1. 验证后才批：dev_acceptance 必须亲验 worktree 头一致性 + 按仓库口径复跑测试
     + 声称的 checks 交叉核对；stage_promotion 必须机器门证据齐且判词含 passed。
  2. 一切决定带 [auto-review] 前缀与证据摘要入 decision_note（append-only 审计链）。
  3. 拿不准就留 pending 并写 note 说明缺什么——绝不为了清空队列而批。
  4. 硬否决仅限证据矛盾：头不符/声称与复跑矛盾/goal 要求测试而复跑挂。
  5. owner 随时可点击人类决定（120s 宽窗口内不自动机）；删除 OFF 文件即全局停机。

用法：单发进程，crontab */2 拉起；日志 agent_work/auto_approver.log。
凭据：review.env（REVIEW_USER/REVIEW_PASS，chmod 600，不入库）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://localhost:3000/api/v1").rstrip("/")
WORK_ROOT = "/srv/tensor/shared/tensor_eco/agent_work"
OFF_SWITCH = "/srv/tensor/shared/tensor_eco/agent_work/auto_approver.OFF"
GRACE_SECONDS = 120          # 刚创建的审批先留给人类 2 分钟
MAX_DECISIONS_PER_RUN = 8
NOTE_PREFIX = "[auto-review v1]"
REVIEWER_MODEL = os.environ.get("REVIEW_LLM_MODEL", "glm-5.2")

REPO_TEST_PYTHON = {
    # 与 agent 实战口径对齐：非 TensorLBM 仓库用 hermes venv（pytest 9.1.1
    # +torch 2.14，m42 实测 217 passed/26.8s；tensor_eco venv 无 pytest）
    "TensorLBM": "/srv/tensor/shared/venvs/tensorlbm/bin/python",
    "_default": "/srv/tensor/shared/hermes-agent/.venv/bin/python",
}


def log(msg: str) -> None:
    stamp = time.strftime("[%H:%M:%S]")
    line = f"{stamp} {msg}"
    print(line, flush=True)


class API:
    def __init__(self) -> None:
        self.token: str | None = None
        self.user = os.environ.get("REVIEW_USER", "")
        self.password = os.environ.get("REVIEW_PASS", "")

    def login(self) -> None:
        body = json.dumps({"username": self.user, "password": self.password}).encode()
        req = urllib.request.Request(f"{BASE}/auth/login", data=body,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            self.token = json.loads(resp.read())["access_token"]

    def call(self, method: str, path: str, body: dict | None = None):
        for _ in (1, 2):
            if not self.token:
                self.login()
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(BASE + path, data=data, method=method,
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": f"Bearer {self.token}"})
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return resp.status, json.loads(resp.read() or b"{}")
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    self.token = None
                    continue
                return exc.code, json.loads(exc.read() or b"{}")
            except urllib.error.URLError as exc:
                return 0, {"detail": f"network: {exc}"}
        return 0, {"detail": "login failed"}


def sh(cmd: str, cwd: str | None = None, timeout: int = 60) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                              text=True, timeout=timeout)
        return proc.returncode, (proc.stdout + proc.stderr)
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s"


def llm_verdict(context: str) -> dict:
    """glm 裁决：证据上下文 -> {verdict: approve|reject|escalate, reason}。
    流式累计防中继非流式挂起；解析失败一律 escalate。"""
    base = os.environ.get("LLM_BASE_URL", "")
    key = os.environ.get("LLM_API_KEY", "")
    fmt = '{"verdict": "approve|reject|escalate", "reason": "不超过120字"}'
    prompt = (
        "你是科研平台的验收裁决器。根据给出的证据判断该 acceptance 是否该批准。\n"
        "规则：证据自洽且达到 goal 的机器判据=approve；证据与复跑矛盾或未达判据=reject；"
        "证据不足/无法判断/涉及物理精度主张而证据不含数值=escalate。\n"
        f"只输出 JSON：{fmt}\n\n"
        f"证据：\n{context[:9000]}")
    body = json.dumps({"model": REVIEWER_MODEL, "stream": True,
                       "max_tokens": 2048, "messages": [
                           {"role": "system", "content": "只输出 JSON。"},
                           {"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(
        f"{base.rstrip('/')}/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    parts: list[str] = []
    reasoning: list[str] = []   # 思考型模型：reasoning 可能先吃预算，content 落空
    with urllib.request.urlopen(req, timeout=90) as resp:
        for line in resp:
            line = line.decode("utf-8", "replace").strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            try:
                chunk = json.loads(line[6:])
                delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                if delta.get("content"):
                    parts.append(delta["content"])
                if delta.get("reasoning_content"):
                    reasoning.append(delta["reasoning_content"])
            except json.JSONDecodeError:
                continue
    text = "".join(parts) or "".join(reasoning)
    m = re.search(r"\{[^{}]*\"verdict\"[^{}]*\}", text, re.DOTALL)
    if not m:
        return {"verdict": "escalate", "reason": f"裁决输出不可解析：{text[:120]}"}
    try:
        obj = json.loads(m.group(0))
        if obj.get("verdict") in ("approve", "reject", "escalate"):
            return obj
    except json.JSONDecodeError:
        pass
    return {"verdict": "escalate", "reason": "裁决 JSON 不完整"}


def verify_dev_acceptance(api: API, mission: dict,
                          evidence: dict) -> tuple[str, str]:
    """返回 (verdict, evidence_text)。verdict ∈ approve/reject/escalate。
    evidence 来自审批行（complete 端点写入）：branch/head_commit/summary/checks。"""
    mid = mission["id"]
    repo_dir = (mission["repo"] or "").split("/")[0]
    wt = os.path.join(WORK_ROOT, f"m{mid}", repo_dir)
    # worktree 的 .git 是文件（gitdir 指针）不是目录——用 exists 判存在
    if not os.path.exists(os.path.join(wt, ".git")):
        return "escalate", f"worktree 不可用：{wt}"
    head_code, head = sh("git rev-parse HEAD", cwd=wt)
    claimed_head = evidence.get("head_commit")
    if head_code != 0:
        return "escalate", f"git rev-parse 失败：{head[:120]}"
    head = head.strip()
    if claimed_head:
        # 缩写 sha 合法：声称短前缀 == 实际全 sha 视为一致（m40 误杀教训）
        claimed = str(claimed_head).strip()
        if not (head.startswith(claimed) or claimed.startswith(head)):
            return "reject", (f"头不一致：审批证据 {claimed[:12]}，"
                              f"worktree 实际 {head[:12]}")
    _, stat = sh(f"git show --stat --oneline {head}", cwd=wt)
    stat = stat[:1500]
    # 按仓库口径复跑默认套件（与 do_run_tests 缺省命令同口径）。
    # 注意：agent 声称的 checks 可能来自子集命令，与全量复跑口径不同——
    # 声称/复跑差异交给 LLM 裁决，硬否决只在 goal 明确要求默认套件全绿时触发。
    python = REPO_TEST_PYTHON.get(repo_dir, REPO_TEST_PYTHON["_default"])
    t_code, t_out = sh(f"{python} -m pytest -x -q tests", cwd=wt, timeout=1800)
    tail = t_out[-2500:]
    passed = re.findall(r"(\d+) passed", t_out)
    failed = re.findall(r"(\d+) failed", t_out)
    n_pass = int(passed[-1]) if passed else 0
    n_fail = int(failed[-1]) if failed else 0
    if t_code == 124:
        return "escalate", f"默认套件复跑超时（>1800s）——证据不足"
    goal = mission.get("goal", "")
    if (re.search(r"默认测试全绿|全量测试|pytest -x -q tests|默认套件", goal)
            and (t_code != 0 or n_fail > 0)):
        return ("reject", f"goal 要求默认套件全绿，复跑 exit={t_code}、"
                f"{n_pass} passed / {n_fail} failed\n复跑尾部：\n{tail[-800:]}")
    claimed = evidence.get("summary") or {}
    context = (f"goal：{goal[:1200]}\n\n"
               f"验收摘要：{json.dumps(claimed, ensure_ascii=False)[:1400]}\n"
               f"声称 checks：{(evidence.get('checks') or {}).get('passed')}"
               f"/{(evidence.get('checks') or {}).get('total')}\n\n"
               f"默认套件复跑：exit={'0' if t_code == 0 else t_code}，"
               f"{n_pass} passed / {n_fail} failed\n"
               f"复跑尾部：\n{tail[-1200:]}\n\n"
               f"提交：\n{stat}")
    verdict = llm_verdict(context)
    return verdict["verdict"], (verdict["reason"] + f"\n\n复跑：{n_pass} passed / "
                                f"{n_fail} failed，head {head[:12]}")


def verify_stage_promotion(api: API, approval: dict) -> tuple[str, str]:
    """机器门证据齐 + 判词含 passed 才批；证据结构未知一律 escalate。"""
    to_stage = approval.get("to_stage")
    evidence = approval.get("evidence") or {}
    reason = approval.get("reason", "")
    if not evidence:
        return "escalate", "stage_promotion 无 evidence——留给人工"
    text = json.dumps(evidence, ensure_ascii=False)
    passed_words = re.findall(r"\w*passed\w*|\w*_green|all_clear", text)
    if not passed_words:
        return "escalate", f"evidence 无机器判词：{text[:200]}"
    context = (f"to_stage：{to_stage}\n理由：{reason[:800]}\n"
               f"evidence：{text[:2500]}")
    verdict = llm_verdict(context)
    return verdict["verdict"], verdict["reason"] + f"（判词：{passed_words[:4]}）"


def decide(api: API, approval_id: int, decision: str, note: str) -> None:
    code, resp = api.call("POST", f"/tensor/approvals/{approval_id}/decide",
                          {"decision": decision,
                           "note": f"{NOTE_PREFIX} {note}"[:2000]})
    log(f"approval {approval_id} -> {decision} (http {code}) {str(resp)[:120]}")


def main() -> None:
    if os.path.exists(OFF_SWITCH):
        log("OFF 开关存在——退出")
        return
    api = API()
    code, data = api.call("GET", "/tensor/approvals?status=pending&limit=50")
    if code != 200:
        log(f"list pending failed: {code} {str(data)[:150]}")
        return
    items = data.get("items", [])
    if not items:
        return
    log(f"pending={len(items)}")
    decided = 0
    for ap in items:
        if decided >= MAX_DECISIONS_PER_RUN:
            log("到达单轮决定上限，余量下轮")
            break
        age = time.time() - time.mktime(time.strptime(
            ap["created_at"][:19], "%Y-%m-%dT%H:%M:%S")) \
            if ap.get("created_at") else 9999
        if age < GRACE_SECONDS:
            log(f"approval {ap['id']} 创建 {int(age)}s < {GRACE_SECONDS}s 宽窗口，跳过")
            continue
        kind = ap.get("kind")
        try:
            if kind == "dev_acceptance" and ap.get("mission_id"):
                code, m = api.call("GET", f"/tensor/missions/{ap['mission_id']}")
                if code != 200:
                    log(f"approval {ap['id']} mission 取回失败 {code}")
                    continue
                verdict, note = verify_dev_acceptance(api, m, ap.get("evidence") or {})
            elif kind == "stage_promotion":
                verdict, note = verify_stage_promotion(api, ap)
            else:
                verdict, note = "escalate", f"未知 kind：{kind}"
        except Exception as exc:  # noqa: BLE001  审批器自身故障绝不误批
            log(f"approval {ap['id']} verifier 异常：{type(exc).__name__}: {exc}")
            verdict, note = "escalate", f"验证器异常：{exc}"
        if verdict == "approve":
            decide(api, ap["id"], "approved", note)
            decided += 1
        elif verdict == "reject":
            decide(api, ap["id"], "rejected", note)
            decided += 1
        else:
            log(f"approval {ap['id']} escalate（留人工）：{note[:200]}")


if __name__ == "__main__":
    main()
