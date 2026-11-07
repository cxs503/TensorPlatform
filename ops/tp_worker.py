#!/usr/bin/env python3
"""TensorPlatform 原生 agent worker（runner='native'）

不依赖 OpenHands / Codex CLI：用平台自己的 mission 队列 + 任意 OpenAI 兼容端点
（tool-calling）在工作树里做真实的开发动作。

流程: login → 注册 resident worker → claim → start → LLM 工具循环（带心跳/预算）
      → complete(acceptance=true)。若平台侧审批表不可写(见 README)，退化为 stop 并记录原因。

用法:
  python3 tp_worker.py --once          # 跑一个任务就退出（推荐先这样验证）
  python3 tp_worker.py                 # 常驻轮询
  python3 tp_worker.py --dry-run       # 只 claim + 打印任务，不做动作
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import re
import subprocess
import threading
import sys
import time
import traceback
import urllib.error
import urllib.request
import uuid

ENV = pathlib.Path("/root/tp-agent/agent.env")


def load_env(p: pathlib.Path) -> dict:
    cfg = {}
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip()
    return cfg


CFG = load_env(ENV)

# SDAA/加速卡环境注入：agent.env 为单一事实源，worker 合并后所有 mission 子进程继承
for _k in ("LD_LIBRARY_PATH", "PATH"):
    if CFG.get(_k):
        os.environ[_k] = CFG[_k] + (":" + os.environ[_k] if os.environ.get(_k) else "")
for _k, _v in CFG.items():
    if _k.isupper() and "=" not in _k and _k not in os.environ:
        os.environ[_k] = _v

API = CFG["TENSOR_API_BASE"].rstrip("/")
LLM = CFG["LLM_BASE_URL"].rstrip("/")
LLM_KEY = CFG["LLM_API_KEY"]
# 多实例：环境变量优先（供 fleet_supervisor 为每个 agent 注入不同身份）
MODEL = os.environ.get("LLM_MODEL") or CFG.get("LLM_MODEL", "glm-5.2")
ROLE = os.environ.get("TP_ROLE") or CFG.get("TP_ROLE", "verifier")   # 平台允许的角色
AGENT_ID = os.environ.get("TP_AGENT_ID") or CFG.get("TP_AGENT_ID", "tp-native-1")
WORK_ROOT = pathlib.Path(os.environ.get("TP_WORK_ROOT") or CFG.get("TP_WORK_ROOT", "/root/tp-agent/work"))
MAX_CALLS = int(CFG.get("TP_MAX_TOOL_CALLS", "100"))
MAX_WALL = int(CFG.get("TP_MAX_WALL_SECONDS", "3600"))
REPO_ROOT = pathlib.Path("/root/tensor-ecosystem")
STATE = pathlib.Path(f"/root/tp-agent/state-{AGENT_ID}.json")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ─────────────────────────── 平台 API ───────────────────────────
TOK = {"v": None}   # 全局最新 token：api() 优先取它，401 自动重登 → 旧引用不再致僵尸环


def api(path: str, data=None, token=None):
    # 优先全局最新 token（401 自愈后，调用方手里的旧 token 引用无关紧要）；
    # /auth/login 本身不带 Authorization。
    if TOK["v"] and path != "/auth/login":
        token = TOK["v"]
    body = json.dumps(data).encode() if data is not None else None

    def _do(t):
        r = urllib.request.Request(API + path, data=body, method="POST" if body else "GET")
        r.add_header("Content-Type", "application/json")
        if t:
            r.add_header("Authorization", "Bearer " + t)
        try:
            with urllib.request.urlopen(r, timeout=40) as resp:
                return resp.status, json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode())
            except Exception:
                return e.code, {}
        except Exception as e:
            return 0, {"detail": str(e)}

    s, d = _do(token)
    if s == 401 and path != "/auth/login":
        # token 过期自愈：重登一次再试。凭据真错则 SystemExit 杀进程，
        # 由 fleet_supervisor 重拉（好过进程活着却全体 401 的僵尸）。
        log("token 过期(401) → 自动重登")
        TOK["v"] = login()
        s, d = _do(TOK["v"])
    return s, d


def resident_beat(token, sid, state, mid=None, note=None, error=None):
    """给 factory_resident_workers 自己的行打心跳 → 工作台才能显示"在线"。"""
    body = {"state": state}
    if mid:
        body["current_mission_id"] = mid
    if note:
        body["memory_entry"] = note[:1800]
    if error:
        body["last_error"] = str(error)[:500]
    try:
        api(f"/tensor/factory/agents/{sid}/heartbeat", body, token)
    except Exception:
        pass


def login() -> str:
    s, d = api("/auth/login", {"username": CFG["TENSOR_AGENT_USER"], "password": CFG["TENSOR_AGENT_PASS"]})
    if s != 200:
        raise SystemExit(f"登录失败 {s} {d}")
    TOK["v"] = d["access_token"]
    return d["access_token"]


def ensure_worker(token: str) -> str:
    """注册（幂等）resident worker，返回 agent_session_id。

    平台没有暴露 /tensor/factory/agents（router 未挂载），所以直接写库。
    走 PostgreSQL（psql 以 postgres 超级用户执行，避免应用账号权限问题）。
    """
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    if st.get("agent_session_id") and st.get("agent_id") == AGENT_ID:
        return st["agent_session_id"]

    db = CFG.get("TP_PG_DB", "tensorplatform")

    def psql(sql: str) -> str:
        p = subprocess.run(["su", "postgres", "-c",
                            f'psql -d {db} -q -tAc "{sql}"'],
                           capture_output=True, text=True, timeout=60)
        return p.stdout.strip()

    row = psql(f"select agent_session_id from factory_resident_workers where worker_id='{AGENT_ID}'")
    if row:
        sid = row
        # 配置漂移自愈：env 里改了 model/role 就同步到租户行，否则 claim 会 409
        psql(f"UPDATE factory_resident_workers SET model='{MODEL}', role='{ROLE}', enabled=true "
             f"WHERE worker_id='{AGENT_ID}' AND (model<>'{MODEL}' OR role<>'{ROLE}' OR enabled=false)")
    else:
        uid = psql(f"select id from users where username='{CFG['TENSOR_AGENT_USER']}'")
        org = psql(f"select organization_id from users where username='{CFG['TENSOR_AGENT_USER']}'")
        sid = str(uuid.uuid4())
        psql("INSERT INTO factory_resident_workers "
             "(agent_session_id,user_id,organization_id,worker_id,role,enabled,state,model,model_history,memory) "
             f"VALUES ('{sid}',{uid},{org},'{AGENT_ID}','{ROLE}',true,'idle','{MODEL}','[]'::jsonb,'[]'::jsonb)")
    STATE.write_text(json.dumps({"agent_session_id": sid, "agent_id": AGENT_ID}))
    return sid


# ─────────────────────────── 工作区 ───────────────────────────
def prepare_worktree(mission: dict) -> pathlib.Path:
    repo = mission.get("repo") or ""
    # 非默认仓库走别名映射（如船舶平台不在 tensor-ecosystem 下）
    _ALIASES = {"ship-platform": pathlib.Path("/root/ship-performance-platform-incoming/ship-performance-platform")}
    src = _ALIASES.get(repo, REPO_ROOT / repo)
    dst = WORK_ROOT / f"m{mission['id']}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return dst
    if not (src / ".git").exists():
        raise RuntimeError(f"仓库不存在或不是 git: {src}")
    branch = f"agent/m{mission['id']}"
    p = subprocess.run(["git", "-C", str(src), "worktree", "add", "-B", branch, str(dst), "HEAD"],
                       capture_output=True, text=True, timeout=180)
    if p.returncode != 0:
        raise RuntimeError(f"worktree 失败: {p.stderr[-400:]}")
    return dst


def _safe_path(root: pathlib.Path, rel: str) -> pathlib.Path:
    p = (root / rel).resolve()
    if not str(p).startswith(str(root.resolve())):
        raise ValueError(f"路径越界: {rel}")
    return p


# ─────────────────────────── 工具 ───────────────────────────
def tool_list_dir(root, rel="."):
    p = _safe_path(root, rel)
    items = sorted(os.listdir(p))[:200]
    return "\n".join(items)


def tool_read_file(root, path, max_bytes=20000):
    p = _safe_path(root, path)
    return p.read_text(errors="replace")[:max_bytes]


def tool_write_file(root, path, content):
    p = _safe_path(root, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"written {path} ({len(content)} bytes)"


def tool_run_shell(root, command, timeout=120):
    banned = re.search(r"\b(rm\s+-rf\s+/|git\s+push|git\s+reset\s+--hard|shutdown|reboot|curl\s+.*\|)",
                       command)
    if banned:
        return "REJECTED: 命令被安全策略拦截"
    p = subprocess.run(command, shell=True, cwd=str(root), capture_output=True,
                       text=True, timeout=timeout)
    out = (p.stdout or "")[-6000:] + (("\nSTDERR:\n" + p.stderr[-3000:]) if p.stderr else "")
    return f"exit={p.returncode}\n{out}"


TOOLS = [
    {"type": "function", "function": {
        "name": "list_dir", "description": "列出工作区目录",
        "parameters": {"type": "object", "properties": {"rel": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "读取工作区文件",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file", "description": "写入/覆盖工作区文件",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"},
                                                       "content": {"type": "string"}},
                       "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "run_shell", "description": "在工作区执行 shell（受白名单与超时限制）",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                       "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "call_ship_api", "description": "调用船舶总体性能平台 API(:8001)。path 不含 /api/v1/ 前缀，如 'calculations/resistance'。返回 {status, data}",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "API 路径，不含 /api/v1/"},
            "method": {"type": "string", "enum": ["GET","POST"], "default": "GET"},
            "body": {"type": "object", "description": "POST body（可选）"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "audit_capabilities", "description": "审计船舶平台能力清单：返回 21 个能力的证据等级(REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED)与覆盖情况",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "finish", "description": "任务完成，提交总结",
        "parameters": {"type": "object", "properties": {
            "summary": {"type": "string"}, "checks": {"type": "string"},
            "changed_files": {"type": "array", "items": {"type": "string"}}},
            "required": ["summary"]}}},
]

SYSTEM = """你是 TensorPlatform 的船舶总体性能智能体，在一个受控的 git 工作树里工作。
你可能被派去做两类任务：
A. 平台审计与改进：审计船舶总体性能平台的 API/算法/精度，找出 MOCK/STUB/缺失的能力，制定改进计划并执行修复。
B. Tensor 生态开发：在 TensorLBM/FVM/FEM/BEM 等仓库里修 bug / 加功能 / 跑 benchmark。

船舶总体性能学科：阻力 · 推进 · 耐波 · 操纵 · 稳性 · 结构 · 振动噪声 · 绿色航行 · 冰区航行。
证据分级：REAL(真实算法) · MOCK(硬编码/随机) · STUB(空壳) · WITHHELD(证据不足) · NOT_SUPPORTED(不支持)。
精度标准：对标实验/参考值误差 >15% 不可接受；几何必须先验证 Cb/L/B。

规则：
- 只改工作树内的文件；不要 push、不要改 .git 配置。
- 先读代码再改；改动要最小、可验证。
- 能用测试证明就证明（pytest 等），并在 finish 里写明跑了什么。
- 不确定就缩小范围，如实说明未完成的部分。
完成后必须调用 finish 工具，总结做了什么、怎么验证的。"""


def _tool_ship_api(args: dict) -> str:
    """代理调用船舶平台 :8001 的 API。"""
    path = args.get("path", "").lstrip("/")
    if not path.startswith("api/v1/"):
        path = "api/v1/" + path
    method = args.get("method", "GET")
    body = args.get("body")
    data = json.dumps(body).encode() if body is not None else None
    # 2026-10-29 全量平台 :8002 上线后优先走 8002，连接失败回落组件版 :8001
    for port in (8002, 8001):
        url = f"http://127.0.0.1:{port}/{path}"
        try:
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.dumps({"status": resp.status, "port": port, "data": json.loads(resp.read().decode() or "{}")}, ensure_ascii=False)[:8000]
        except urllib.error.HTTPError as e:
            return json.dumps({"status": e.code, "port": port, "data": e.read().decode()[:500]}, ensure_ascii=False)
        except Exception:
            if port == 8001:
                return json.dumps({"status": 0, "data": "8001/8002 均不可达"}, ensure_ascii=False)
            continue


CAP_SNAPSHOT_REL = pathlib.Path("artifacts/capability_ledger/capability_ledger_resolved.json")
CAP_FALSIFICATION_REL = pathlib.Path("artifacts/capability_falsification/capabilities.json")
CAP_LEDGER_REL = pathlib.Path("artifacts/capability_ledger/capability_ledger.json")
CAP_HARNESS_DEFAULT = pathlib.Path(
    "/root/ship-performance-platform-incoming/ship-performance-platform/backend/platform_harness/capabilities.json")


def _cap_sources() -> list[pathlib.Path]:
    """能力清单候选真值文件（按优先级）。

    2026-11-02 m2031：此前本工具只认一条硬编码路径（船舶 harness 的
    capabilities.json），该文件在 incoming 树里只剩 .pyc，工具对全部 agent 恒返回
    「读取能力清单失败」，证据分级仪整月不可用。改为多候选 + 仓内台账优先：
    仓内 artifacts/capability_ledger/ 是 C1~C10 签过字的单一真值（含对账等级）。
    """
    root = pathlib.Path(__file__).resolve().parents[1]
    cands: list[pathlib.Path] = []
    if os.environ.get("TP_CAP_LEDGER"):
        cands.append(pathlib.Path(os.environ["TP_CAP_LEDGER"]))
    cands.append(root / CAP_SNAPSHOT_REL)
    if os.environ.get("TP_SHIP_HARNESS_DIR"):
        cands.append(pathlib.Path(os.environ["TP_SHIP_HARNESS_DIR"]) / "backend/platform_harness/capabilities.json")
    cands.append(CAP_HARNESS_DEFAULT)
    cands.append(root / CAP_LEDGER_REL)
    # 2026-11-07 m5238/W18-N09：证伪台账（21 项，含 claimed/reconciled 双列 + callable_spec）
    # 作为兜底候选，保证本工具在其余来源缺失时仍能渲染能力清单（id 集合与上面同源）。
    cands.append(root / CAP_FALSIFICATION_REL)
    return [p for p in cands if p.is_file()]


def _render_caps(d: dict) -> str:
    """两种 schema：台账对账结果（capabilities=list）/ 平台 harness 清单（capabilities=dict）。"""
    caps = d.get("capabilities")
    lines: list[str] = []
    if isinstance(caps, list):  # 台账对账结果：自述 vs 对账双列，防止把自述当事实
        summ = d.get("summary") or {}
        tally = summ.get("divergence_tally") or {}
        lines.append(
            f"能力清单 ({len(caps)} 个；证据行 {summ.get('evidence_rows', '?')}；"
            f"分歧 OVERCLAIM {tally.get('OVERCLAIM', '?')} / REVERIFY {tally.get('REVERIFY', '?')} / "
            f"UNVERIFIED {tally.get('UNVERIFIED', '?')} / OK {tally.get('OK', '?')}):"
        )
        for c in sorted(caps, key=lambda x: str(x.get("id"))):
            lines.append(
                f"{str(c.get('id', '?')):26s} 自述 {str(c.get('claimed_level', '?')):13s} "
                f"对账 {str(c.get('reconciled_level', '?')):13s} {str(c.get('divergence', '?')):10s} "
                f"域 {c.get('domain', '?')}"
            )
        gaps = d.get("unregistered_domains") or []
        if gaps:
            lines.append(f"注册表覆盖缺口 ({len(gaps)} 个): " + "；".join(str(g.get("domain")) for g in gaps))
        if d.get("violations"):
            lines.append(f"自检违规 {len(d['violations'])} 项：" + "；".join(str(v) for v in d["violations"]))
        return "\n".join(lines)
    if isinstance(caps, dict):
        lines.append(f"能力清单 ({len(caps)} 个):")
        for k, v in sorted(caps.items()):
            ev = v.get("evidence", "?") if isinstance(v, dict) else str(v)
            summ = (v.get("summary", "") or "")[:50] if isinstance(v, dict) else ""
            lines.append(f"{k:30s} {ev:15s} {summ}")
        return "\n".join(lines)
    raise ValueError("能力清单 schema 不可识别（缺 capabilities 段）")


def _tool_audit_caps() -> str:
    """审计船舶平台能力清单。"""
    tried: list[str] = []
    for path in _cap_sources():
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            return _render_caps(d) + f"\n[真值来源] {path}"
        except Exception as exc:  # 坏文件继续试下一个候选，最终如实报出全部尝试
            tried.append(f"{path}: {exc}")
    return "读取能力清单失败：无可用真值来源。已尝试：\n" + ("\n".join(tried) or "(无候选文件存在)")


def call_llm(messages, tools):
    body = {"model": MODEL, "messages": messages, "tools": tools,
            "tool_choice": "auto", "max_tokens": 12000, "temperature": 0.2}
    r = urllib.request.Request(LLM + "/chat/completions", data=json.dumps(body).encode(), method="POST")
    r.add_header("Authorization", "Bearer " + LLM_KEY)
    r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=300) as resp:
        return json.loads(resp.read().decode())


def run_mission(token: str, mission: dict, sid: str, claim_token: str, root: pathlib.Path) -> dict:
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"任务目标:\n{mission['goal']}\n\n仓库: {mission.get('repo')}\n"
                                            f"工作目录: {root}\n分支: agent/m{mission['id']}\n\n"
                                            f"★纪律：读文件不要超过 8 次就要开始动手；"
                                            f"完成后必须调用 finish 工具；没有产物就不算完成。\n\n"
                                            f"★报告纪律（owner 常设指令，每单必报）：finish 前必须发布论文级进展报告——"
                                            f"用 run 工具: curl -s -X POST $TENSOR_API_BASE/tensor/reports "
                                            f"-H \"Authorization: Bearer $TOKEN\" -H 'Content-Type: application/json' -d @/tmp/report.json"
                                            f"（先 curl 登录 $TENSOR_AGENT_USER/$TENSOR_AGENT_PASS 拿 token；"
                                            f"/tmp/report.json 内容: title/summary/scenario_key/mission_id:{mission['id']}/tags/markdown/figures）。"
                                            f"报告要求（可发表论文质量，owner 2026-10-29 升级）：六要素=①案例介绍（物理背景/几何/参数表）；②怎么做的（方法/流程/关键设置/工具链）；③计算结果（数字表，程序化注入零手抄）；④结果图=场量云图+与解析解/文献/基线的比较曲线（matplotlib 生成 png 后 figures:[{{name,caption,png_base64}}] 内嵌；纯文档/后端任务无图必须写明理由）；⑤注意点（踩过的坑/局限/适用范围）；⑥误差判定：关键量 vs 基准误差逐项列出并显式回答「是否<3%」（无基准须声明原因，不许含糊）；honest-FAIL 如实写不得粉饰。"
                                            f"报告未发=本单未完成（守望线会自动派补报单并记欠账）。"}]
    budget = mission.get("budget")
    if isinstance(budget, str):
        try:
            budget = json.loads(budget)
        except Exception:
            budget = {}
    max_calls = int((budget or {}).get("max_tool_calls", MAX_CALLS))
    max_wall = int((budget or {}).get("max_wall_seconds", MAX_WALL))
    t0 = time.time()
    calls, changed, final = 0, [], None
    hb_at = 0.0
    nudges = 0

    # 心跳线程：单次长工具调用（>240s）期间不断供，防平台 agent_lost 误收割（m9 教训）
    _hb_stop = threading.Event()

    def _hb_loop():
        while not _hb_stop.wait(60):
            try:
                api(f"/tensor/missions/{mission['id']}/heartbeat",
                    {"claim_token": claim_token, "head": "bg-hb",
                     "tool_calls": calls, "elapsed_seconds": int(time.time() - t0)}, token)
            except Exception:
                pass  # 主循环逐调用心跳仍在，线程只是长调用期间的兜底

    threading.Thread(target=_hb_loop, daemon=True).start()

    while calls < max_calls and time.time() - t0 < max_wall:
        try:
            resp = call_llm(messages, TOOLS)
        except Exception as e:
            log(f"LLM 调用失败: {e}")
            break
        msg = resp["choices"][0]["message"]
        messages.append(msg)
        tcs = msg.get("tool_calls") or []
        if not tcs:
            # 模型空转：明确催办（最多 2 次），而不是默默算它完成
            if final is None and nudges < 2:
                nudges += 1
                log(f"模型未调用 finish（第 {nudges} 次催办）")
                messages.append({"role": "user", "content":
                                 "你还没有调用 finish。请立刻：若已完成就调用 finish 提交总结；"
                                 "若还没动手，现在就开始写文件（不要再读文件）。"})
                continue
            log(f"模型结束(无工具调用): {(msg.get('content') or '')[:100]}")
            break
        for tc in tcs:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except Exception:
                args = {}
            calls += 1
            log(f"tool#{calls} {name}({', '.join(list(args)[:3])})")
            try:
                if name == "list_dir":
                    result = tool_list_dir(root, args.get("rel", "."))
                elif name == "read_file":
                    result = tool_read_file(root, args["path"])
                elif name == "write_file":
                    result = tool_write_file(root, args["path"], args.get("content", ""))
                    changed.append(args["path"])
                elif name == "run_shell":
                    result = tool_run_shell(root, args["command"])
                elif name == "call_ship_api":
                    result = _tool_ship_api(args)
                elif name == "audit_capabilities":
                    result = _tool_audit_caps()
                elif name == "finish":
                    final = args
                    result = "ok"
                else:
                    result = f"unknown tool {name}"
            except Exception as e:
                result = f"TOOL ERROR: {type(e).__name__}: {e}"
            _content = str(result)[:12000]
            _remain = max_calls - calls
            if _remain <= 15:
                _tail = ("不足时立即收尾：把交付物写进 worktree（docs/文件）后调用 finish，不要继续探索。"
                        if _remain > 3 else "已临界：立即调用 finish 收尾，再拖会被判 failed。")
                _content += f"\n\n[预算提示] 还剩 {_remain} 次调用。{_tail}"
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": _content})
            if name == "finish":
                break
            # 心跳：每 3 次工具调用一次
            if time.time() - hb_at > 15:
                api(f"/tensor/missions/{mission['id']}/heartbeat",
                    {"claim_token": claim_token, "head": f"{name} @ call {calls}",
                     "tool_calls": calls, "elapsed_seconds": int(time.time() - t0)}, token)
                hb_at = time.time()
        if final:
            break

    diff = ""
    try:
        p = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True, timeout=60)
        diff = p.stdout[-2000:]
    except Exception:
        pass
    # 以工作树实际改动为准（不信模型自述）
    on_disk = [ln[3:].strip() for ln in diff.splitlines() if ln.strip()]
    _hb_stop.set()
    return {"summary": (final or {}).get("summary") or f"完成 {calls} 次工具调用（未调用 finish）",
            "checks": (final or {}).get("checks") or "",
            "changed_files": sorted(set(changed)),
            "on_disk_changes": on_disk,
            "finished": final is not None,
            "tool_calls": calls, "elapsed": int(time.time() - t0),
            "git_status": diff, "model": MODEL}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--agent-id", default=None, help="worker 身份（舰队用，出现在 argv 便于守护定位）")
    ap.add_argument("--role", default=None, help="平台角色 platform|data|models|applications|infrastructure|verifier|strategy")
    ap.add_argument("--model", default=None, help="LLM 模型")
    ap.add_argument("--poll", type=int, default=None, help="空闲轮询间隔秒（会加抖动）")
    args = ap.parse_args()

    # CLI 优先于 env/配置（模块级常量在 main 里被重绑）
    global AGENT_ID, ROLE, MODEL, STATE, MAX_CALLS, MAX_WALL
    if args.agent_id:
        AGENT_ID = args.agent_id
        STATE = pathlib.Path(f"/root/tp-agent/state-{AGENT_ID}.json")
    if args.role:
        ROLE = args.role
    if args.model:
        MODEL = args.model

    token = login()
    sid = ensure_worker(token)
    log(f"worker {AGENT_ID} | role={ROLE} | model={MODEL} | session={sid[:8]}…")
    resident_beat(token, sid, "idle")
    _last_beat = [time.time()]

    while True:
        # 回落顺序：网页/接口建的任务通常不带 worker_role、不带 model
        #  ① 指派给我 + 我的角色      ② 指派给我 + 任务无角色（★前端默认走这条）
        #  ③ 未指派 + 我的角色        ④ 未指派 + 任务无角色（公共池）
        attempts = [
            {"agent_id": AGENT_ID, "runner": "native", "model": MODEL,
             "worker_role": ROLE, "agent_session_id": sid, "claim_null_model": True,
             "claim_unassigned": True},
            {"agent_id": AGENT_ID, "runner": "native", "model": MODEL,
             "agent_session_id": sid, "claim_null_model": True, "claim_unassigned": True},
            {"agent_id": AGENT_ID, "runner": "native", "model": MODEL,
             "worker_role": ROLE, "agent_session_id": sid, "claim_null_model": True,
             "claim_unassigned": True},
            {"agent_id": AGENT_ID, "runner": "native", "model": MODEL,
             "claim_null_model": True},
        ]
        d = {}
        s = 0
        for body in attempts:
            s, d = api("/tensor/missions/claim", body, token)
            if s == 200 and d.get("claimed"):
                break
        if s not in (200, 409):
            log(f"claim 失败 {s} {d}")
            if args.once:
                return
            time.sleep(20)
            continue
        if not d.get("claimed"):
            log("队列空，等待")
            if time.time() - _last_beat[0] > 45:
                resident_beat(token, sid, "idle")
                _last_beat[0] = time.time()
            if args.once:
                return
            _base = args.poll or int(CFG.get("TP_POLL_SECONDS", "20"))
            time.sleep(_base + random.uniform(0, max(1, _base // 2)))
            continue

        mission = d["mission"]
        ct = mission["claim_token"]
        log(f"领到 mission#{mission['id']}: {mission['goal'][:80]}")
        resident_beat(token, sid, "running", mid=mission["id"], note=f"领取 mission#{mission['id']}")
        if args.dry_run:
            log("dry-run: 不做动作")
            return

        api(f"/tensor/missions/{mission['id']}/start", {"claim_token": ct}, token)
        try:
            root = prepare_worktree(mission)
            log(f"工作树 {root}")
            result = run_mission(token, mission, sid, ct, root)
        except SystemExit:
            raise
        except Exception as e:
            # 护栏：worker 内部异常不再裸崩（裸崩=mission 孤儿→平台判 agent_lost）
            log(f"⚠ run_mission 异常 {type(e).__name__}: {str(e)[:200]}")
            traceback.print_exc()
            api(f"/tensor/missions/{mission['id']}/complete",
                {"claim_token": ct, "status": "failed", "acceptance": False,
                 "summary": {"headline": f"worker 异常: {type(e).__name__}: {str(e)[:150]}",
                             "model": MODEL, "agent_session_id": sid,
                             "traceback_tail": traceback.format_exc()[-800:]},
                 "checks": {"gate": "worker-crash", "reason": str(e)[:300]}}, token)
            resident_beat(token, sid, "idle", error=f"crash: {str(e)[:200]}")
            if args.once:
                return
            time.sleep(10)
            continue
        log(f"完成 {result['tool_calls']} 次调用 / {result['elapsed']}s / "
            f"声明改动 {len(result['changed_files'])} 个 / 实际落盘 {len(result['on_disk_changes'])} 个 / "
            f"finish={'是' if result['finished'] else '否'}")

        # ★ 反吹牛闸门：必须有 finish 且工作树真有改动，才允许提请"成功验收"
        has_evidence = result["finished"] and len(result["on_disk_changes"]) > 0
        if not has_evidence:
            reason = ("模型未调用 finish" if not result["finished"]
                      else "工作树无任何改动，不能报成功")
            log(f"⚠ 证据不足 → 不提请验收：{reason}")
            s, d = api(f"/tensor/missions/{mission['id']}/complete",
                       {"claim_token": ct, "status": "failed", "acceptance": False,
                        "summary": {"headline": f"未完成：{reason}", "model": MODEL,
                                    "agent_session_id": sid, "tool_calls": result["tool_calls"],
                                    "changed_files": result["changed_files"],
                                    "declared_summary": result["summary"][:300]},
                        "checks": {"gate": "no-evidence", "reason": reason}}, token)
            log(f"  记为 failed → {s} {str(d)[:100]}")
            if args.once:
                return
            time.sleep(10)
            continue

        # 尝试正常提请验收
        s, d = api(f"/tensor/missions/{mission['id']}/complete",
                   {"claim_token": ct, "status": "succeeded", "acceptance": True,
                    "summary": {"headline": result["summary"][:180], "model": MODEL,
                                "agent_session_id": sid, "tool_calls": result["tool_calls"],
                                "changed_files": result["changed_files"],
                                "on_disk_changes": result["on_disk_changes"],
                                "checks": result["checks"], "git_status": result["git_status"]}},
                   token)
        if s == 200:
            log(f"✓ 已提请验收 awaiting_acceptance (approval_id={d.get('approval_id')})")
        else:
            log(f"⚠ complete 失败 {s}: {str(d)[:160]}")
            s2, d2 = api(f"/tensor/missions/{mission['id']}/stop",
                         {"claim_token": ct, "reason": f"acceptance path blocked: {str(d)[:120]}"}, token)
            log(f"  退化 stop → {s2} {str(d2)[:120]}")
        resident_beat(token, sid, "idle", note=result["summary"][:200])
        if args.once:
            return
        time.sleep(10)


if __name__ == "__main__":
    main()