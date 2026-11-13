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
import signal
import subprocess
import threading
import sys
import time
import traceback
import urllib.error
import urllib.request
import uuid

ENV = pathlib.Path("/root/tp-agent/agent.env")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mvd_gate  # noqa: E402  70% 预算收口检查点（纯函数，模式门控）
HARNESS_VER = "20261103.2"   # LLM 退避重试 7 次 + 任务终局进程组收割（36 单泄漏 64 核实证）


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
def _resolve_trunk(src: pathlib.Path) -> str:
    """解析仓库默认主干（origin/HEAD 短名→main→master）——各仓默认枝不同，禁止硬编码。

    2026-11-03 事故修复：prepare_worktree 曾硬编码 master（11-02 教训只为 ship 仓而设），
    九个求解器仓默认 main——worker 回收加载现文件后求解器仓单全灭（ship/TensorPapers 幸存）。
    """
    p = subprocess.run(["git", "-C", str(src), "symbolic-ref", "--short",
                        "refs/remotes/origin/HEAD"], capture_output=True, text=True, timeout=30)
    ref = p.stdout.strip()
    if p.returncode == 0 and ref:
        return ref
    for cand in ("main", "master"):
        q = subprocess.run(["git", "-C", str(src), "rev-parse", "--verify",
                            "--quiet", cand + "^{commit}"], capture_output=True, timeout=30)
        if q.returncode == 0:
            return cand
    raise RuntimeError(f"默认主干不可解析: {src}")


def prepare_worktree(mission: dict) -> pathlib.Path:
    repo = mission.get("repo") or ""
    # 非默认仓库走别名映射（如船舶平台不在 tensor-ecosystem 下）
    _ALIASES = {"ship-platform": pathlib.Path("/root/ship-performance-platform-incoming/ship-performance-platform"),
                # factory profile 正式名与短名都解析到同一仓（2026-11-01 12 连败根因：注册表脱节）
                "ship-performance-platform": pathlib.Path("/root/ship-performance-platform-incoming/ship-performance-platform")}
    src = _ALIASES.get(repo, REPO_ROOT / repo)
    dst = WORK_ROOT / f"m{mission['id']}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return dst
    if not (src / ".git").exists():
        raise RuntimeError(f"仓库不存在或不是 git: {src}")
    branch = f"agent/m{mission['id']}"
    # 2026-11-02 收口线教训：从 HEAD 切枝会继承本体游离 HEAD 的灾难提交（m1824 删 954 文件波及 m1839+ 全部分支）。
    # 一律从默认主干切枝（按仓解析，2026-11-03 修），本体当前 HEAD 只用于展示。
    p = subprocess.run(["git", "-C", str(src), "worktree", "add", "-B", branch, str(dst), _resolve_trunk(src)],
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


_MISSION_PGIDS: set = set()  # 本任务经 shell 工具派生的进程组（任务终局统一收割）


def _reap_mission_processes() -> None:
    """任务终局收割（2026-11-03 owner 批）：agent 后台起的计算进程不再泄漏。
    05:46-06:00 中继断连窗 36 单 llm_error，其 nohup 后台脚本无人收割，
    ~64 核孤儿把 load15 顶在 326 → selfstarter 连续 CAPACITY-HOLD 全舰队空转。"""
    global _MISSION_PGIDS
    if not _MISSION_PGIDS:
        return
    for pgid in list(_MISSION_PGIDS):
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    time.sleep(1.5)
    for pgid in list(_MISSION_PGIDS):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    _MISSION_PGIDS = set()
    log("任务终局收割：本任务派生的后台进程组已清理")


def tool_run_shell(root, command, timeout=120):
    banned = re.search(r"\b(rm\s+-rf\s+/|git\s+push|git\s+reset\s+--hard|shutdown|reboot|curl\s+.*\|)",
                       command)
    if banned:
        return "REJECTED: 命令被安全策略拦截"
    # 2026-11-03 owner 批：命令独立会话组——sh 退出后 nohup 后台儿孙留在同组可追踪；
    # 跨工具调用存活（agent 起 服务/长算 后续轮询的合法模式不受影响），任务终局统一收割。
    p = subprocess.Popen(command, shell=True, cwd=str(root),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, start_new_session=True)
    _MISSION_PGIDS.add(p.pid)
    try:
        out_raw, err_raw = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)  # 整组杀：连同 sh 的后台儿孙（超时泄漏同源）
        except (ProcessLookupError, PermissionError):
            pass
        out_raw, err_raw = p.communicate()
        return f"exit={p.returncode}\nTIMEOUT after {timeout}s（进程组已整组收割）\n" + (out_raw or "")[-2000:]
    out = (out_raw or "")[-6000:] + (("\nSTDERR:\n" + (err_raw or "")[-3000:]) if err_raw else "")
    return f"exit={p.returncode}\n{out}"


def tool_cluster(action, repo=None, cmd=None, ref="main", node="auto",
                 shards=None, cmd_tpl=None, job_id=None, timeout=3600, stage_dir=None):
    """计算集群工具（owner 2026-11-03「计算的时候调用」「当集群用」）：
    外协方双节点 loong64 128核×2。重计算/大规模扫描一律走集群，别压 99。"""
    if action not in ("nodes", "run", "shard", "ps", "kill", "fetch"):
        return "REJECTED: action 只允许 nodes/run/shard/ps/kill/fetch"
    if shards is not None and (not isinstance(shards, int) or shards < 1 or shards > 64):
        return "REJECTED: shards 须 1-64"
    timeout = min(int(timeout or 3600), 7200)
    argv = ["python3", "/root/tp-agent/cluster/cluster_exec.py", action]
    if action == "run":
        if not (repo and cmd):
            return "REJECTED: run 需要 repo+cmd"
        argv += ["--repo", repo, "--cmd", cmd, "--ref", ref, "--node", node,
                 "--timeout", str(timeout)]
        if stage_dir:
            argv += ["--stage-dir", stage_dir]
        if job_id:
            argv += ["--job-id", job_id]
    elif action == "shard":
        if not (repo and cmd_tpl and shards):
            return "REJECTED: shard 需要 repo+cmd_tpl+shards"
        argv += ["--repo", repo, "--cmd-tpl", cmd_tpl, "--shards", str(shards),
                 "--ref", ref]
        if stage_dir:
            argv += ["--stage-dir", stage_dir]
        if job_id:
            argv += ["--job-id", job_id]
    elif action in ("kill", "fetch"):
        if not job_id:
            return "REJECTED: %s 需要 job_id" % action
        argv += ["--job-id", job_id]
    elif action == "ps":
        if job_id:
            argv += ["--job-id", job_id]
    try:
        out = subprocess.run(argv, capture_output=True, text=True,
                             timeout=timeout + 120).stdout
        return out[-8000:]
    except subprocess.TimeoutExpired:
        return "TIMEOUT: 集群作业超 %ss（长作业建议 shard 后台跑 + ps 轮询）" % (timeout + 120)


def _parse_edit_whitelist(goal):
    """编辑白名单机器门（借 ARIS edit-whitelist 2026-10-31）：goal 显式声明
    「EDIT_WHITELIST: 前缀1, 前缀2」时，write_file 只放行命中前缀的路径。
    指令级「禁改」可被模型忽略，工具级不可；无声明=休眠（零行为变化）。"""
    m = re.search(r"EDIT_WHITELIST[：:]\s*([^\n]+)", goal or "")
    if not m:
        return None
    prefs = []
    for p in re.split(r"[,，、;；]", m.group(1)):
        p = p.strip().lstrip("./").strip()
        if p:
            prefs.append(os.path.normpath(p))
    return prefs or None


def _wl_ok(path, prefs):
    p = os.path.normpath(str(path).strip().lstrip("./"))
    return any(p == q or p.startswith(q.rstrip("/") + "/") for q in prefs)


TOOLS = [
    {"type": "function", "function": {
        "name": "list_dir", "description": "列出工作区目录",
        "parameters": {"type": "object", "properties": {"rel": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "读取工作区文件",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file", "description": "写入/覆盖工作区文件（若任务声明 EDIT_WHITELIST 则机器级只放行白名单内路径）",
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
        "name": "run_cluster", "description":
            "调用计算集群（外协方双节点 loong64 128核×2，repo 从 GitHub 缓存秒级就绪）。"
            "action=nodes 查负载；run 在单节点 repo 检出内执行命令；shard 把 {i}/{n} 分片"
            "并行撒两节点（每分片日志 shard_<i>.log）；ps/kill/fetch 按 job_id 跟踪/杀/取回。"
            "重计算（全量测试/参数扫描/收敛研究）一律走这里，不要压 99 控制面。",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["nodes", "run", "shard", "ps", "kill", "fetch"]},
            "repo": {"type": "string", "description": "仓库名（如 TensorBEM）"},
            "cmd": {"type": "string", "description": "run 的命令（在 repo 检出内执行）"},
            "cmd_tpl": {"type": "string", "description": "shard 命令模板，{i}/{n} 会替换"},
            "shards": {"type": "integer", "description": "分片总数 1-64"},
            "ref": {"type": "string", "description": "git ref，默认 main"},
            "node": {"type": "string", "enum": ["auto", "n315", "n330"]},
            "job_id": {"type": "string"},
            "stage_dir": {"type": "string", "description": "本地目录 tar 直达节点（测自己的工作树用这个；缺省=GitHub main）"},
            "timeout": {"type": "integer", "description": "秒，上限 7200"}},
            "required": ["action"]}}},
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
- 只改工作树内的文件；不要自己 push、不要改 .git 配置；开发项成熟（验收通过）后平台会自动把你的分支推上 GitHub——commit 记录只要最终成果，禁止过程流水账/指令引文/任何秘密。
- 先读代码再改；改动要最小、可验证。
- 能用测试证明就证明（pytest 等），并在 finish 里写明跑了什么。
- 不确定就缩小范围，如实说明未完成的部分。
完成后必须调用 finish 工具，总结做了什么、怎么验证的。"""


def _bootstrap_tail(head=2400, tail=700):  # head 2400: 含 ECOSYSTEM-MAP 全貌表（owner 2026-11-02 令）
    """冷启动包（借 owner 另一平台 /hello 握手设计 2026-10-31）：
    入场一次注入 身份/核心循环/自检（头部）+ 陷阱账本/强脑打法（尾部），
    免去新会话多轮探索。文件缺失/损坏静默降级为空，不影响主流程。"""
    try:
        t = pathlib.Path("/root/tp-agent/AGENT_BOOTSTRAP.md").read_text(encoding="utf-8")
        if len(t) <= head + tail:
            return t
        return t[:head] + "\n……（中间节略；全文可读 /root/tp-agent/AGENT_BOOTSTRAP.md）……\n" + t[-tail:]
    except Exception:
        return ""


_BOOT = _bootstrap_tail()
if _BOOT:
    SYSTEM += "\n\n=== 冷启动包（入场身份+陷阱+打法；WORLD 行过期先校准 :8010/health）===\n" + _BOOT


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


def _tool_audit_caps() -> str:
    """审计船舶平台能力清单（委托单一解析器 + 台账消费者渲染）。

    2026-11-07 m5352：此前这里是**单一硬编码绝对路径**，该文件在本机只剩 .pyc，
    于是本工具对全部 agent 恒返回「读取能力清单失败」——证据分级仪整月不可用的根因。
    现在路径/指纹只经 tools/audit/ledger_paths.py（消费者契约门）。
    """
    try:
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from tools.audit.ledger_consumer_audit_tool import audit_capabilities as _render_ledger
        return _render_ledger()
    except Exception as exc:
        return f"读取能力清单失败: {type(exc).__name__}: {exc}"
    cap_path = "/root/ship-performance-platform-incoming/ship-performance-platform/backend/platform_harness/capabilities.json"
    try:
        d = json.load(open(cap_path))
        caps = d.get("capabilities", {})
        lines = []
        for k, v in sorted(caps.items()):
            ev = v.get("evidence", "?") if isinstance(v, dict) else str(v)
            summ = v.get("summary", "")[:50] if isinstance(v, dict) else ""
            lines.append(f"{k:30s} {ev:15s} {summ}")
        return f"能力清单 ({len(caps)} 个):\n" + "\n".join(lines)
    except Exception as e:
        return f"读取能力清单失败: {e}"


_LLM_BACKOFF = (5, 15, 45, 90, 120, 120)  # 2026-11-03 owner 批：7 次累计 ~6.6 分钟


def call_llm(messages, tools, attempts=7):
    # 2026-10-30：原版零重试，一次网络抖动=整单裸死（「模型未调用 finish」主因之一）。
    # 2026-11-03 owner 批：3 次×15s 撑不过中继分钟级断连窗（05:46-06:00 窗 36 单 llm_error
    # 实证）→ 7 次指数退避；心跳线程兜底，长退避不触发平台 agent_lost。
    # 瞬态类（超时/连接重置/429/5xx）退避重试；4xx 客户端类（401/400 等）立即上抛。
    body = {"model": MODEL, "messages": messages, "tools": tools,
            "tool_choice": "auto", "max_tokens": 12000, "temperature": 0.2}
    data = json.dumps(body).encode()
    for i in range(attempts):
        r = urllib.request.Request(LLM + "/chat/completions", data=data, method="POST")
        r.add_header("Authorization", "Bearer " + LLM_KEY)
        r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=300) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < attempts - 1:
                time.sleep(_LLM_BACKOFF[min(i, len(_LLM_BACKOFF) - 1)]); continue
            raise
        except Exception:
            if i < attempts - 1:
                time.sleep(_LLM_BACKOFF[min(i, len(_LLM_BACKOFF) - 1)]); continue
            raise
    raise RuntimeError("call_llm 重试耗尽")


# ── 上下文压缩（2026-10-30）：messages 无界增长→上下文溢出→LLM 报错→裸死，长任务主因）
COMPACT_CHARS = int(CFG.get("TP_COMPACT_CHARS", "280000"))

def _msg_chars(messages):
    return sum(len(str(m.get("content") or "")) for m in messages)

def compact_messages(messages, keep_tail=12):
    """超阈值：保 system+任务目标+最近 keep_tail 条原样，中间旧内容截到 400 字。
    assistant 消息只截 content，tool_calls 等结构键原样保留。"""
    if _msg_chars(messages) <= COMPACT_CHARS or len(messages) <= keep_tail + 2:
        return messages, False
    head, tail = messages[:2], messages[-keep_tail:]
    mid = messages[2:-keep_tail]
    comp = []
    for m in mid:
        c = m.get("content")
        if c is not None and len(str(c)) > 400:
            m2 = dict(m); m2["content"] = str(c)[:400] + "\n…[上下文压缩：省略]"
            comp.append(m2)
        else:
            comp.append(m)
    return head + comp + tail, True


# ── 证据面拓宽（2026-10-30）：git status 对 gitignore 产物全盲，m425 实证 runs/ 真产物被误杀
_EVIDENCE_NOISE = re.compile(r"(__pycache__|\.pyc$|\.pytest_cache|node_modules|\.egg-info|\.venv/|\.ipynb_checkpoints)")

def evidence_snapshot(root: pathlib.Path) -> set:
    """git status --porcelain --ignored 条目集合（含 ignored；与起始基线做差集=新产物）。"""
    out = set()
    try:
        p = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--ignored"],
                           capture_output=True, text=True, timeout=60)
        for ln in p.stdout.splitlines():
            if ln.strip():
                out.add(ln[3:].strip().rstrip("/"))
    except Exception:
        pass
    return out



def _seat_memory():
    """席位记忆（owner 2026-11-01 个体记忆原语）：从目录边车 :8011 拉本席条目，
    claim 时注入首条消息；不可用静默降级（不影响主流程）。"""
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:8011/directory/{AGENT_ID}", timeout=2) as r:
            d = json.loads(r.read().decode())
        if not d or d.get("error"):
            return ""
        parts = [f"【席位记忆】{AGENT_ID}（{d.get('model','')}/{d.get('role','')}）"
                 f" 战绩 {d.get('missions_done',0)}/{d.get('missions_total',0)}："]
        for k, lab in (("self_desc","自述"),("skills","擅长"),("current_goal","当前目标"),
                       ("needs","需要"),("offers","可提供"),("recent_missions","近作"),
                       ("lessons","教训账本")):
            v = (d.get(k) or "").strip()
            if v:
                parts.append(f"[{lab}] {v[:600]}")
        parts.append("→ 续写记忆：finish 前 POST http://127.0.0.1:8011/directory 更新本席字段、"
                     f"POST /directory/{AGENT_ID}/lesson 追加一条教训（append-only）；"
                     "发现同伴（能力/需要/在干什么）：GET http://127.0.0.1:8011/directory")
        return "\n".join(parts) + "\n\n"
    except Exception:
        return ""

def run_mission(token: str, mission: dict, sid: str, claim_token: str, root: pathlib.Path) -> dict:
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": _seat_memory() + f"任务目标:\n{mission['goal']}\n\n仓库: {mission.get('repo')}\n"
                                            f"工作目录: {root}\n分支: agent/m{mission['id']}\n\n"
                                            f"★纪律：读文件不要超过 8 次就要开始动手；"
                                            f"完成后必须调用 finish 工具；没有产物就不算完成（产物计入 docs/、artifacts/、runs/ 等 worktree 内路径）。\n"
                                            f"★成果入库（owner 2026-10-30）：验收通过后你的分支由平台自动推送 GitHub，commit 只要最终成果。\n\n"
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
    fail_stage, compactions = "", 0
    # 70% 预算收口检查点（m8456）：注入一次，advisory；TP_MVD_CHECKPOINT=off 时零动作
    mvd_mode = mvd_gate.mode_from_env()
    mvd_injected, mvd_events = False, []
    wl = _parse_edit_whitelist(mission.get("goal", ""))
    if wl:
        log(f"编辑白名单机器门激活: {wl}")
    base_evidence = evidence_snapshot(root)   # 检出时已存在的条目=基线，差集去噪
    # 2026-10-31 证据门修复（owner 点名批准）：agent 规范提交自己分支后 git status 干净，
    # 被误判「零改动」（m633/m638/m644 实证共 3562 行真增量被冤判）。
    # 记录起始 HEAD，收尾 diff base..工作树（已提交+未提交一并计入证据）。
    try:
        _p = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                            capture_output=True, text=True, timeout=30)
        base_sha = _p.stdout.strip()
    except Exception:
        base_sha = ""
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
        messages, _did = compact_messages(messages)
        if _did:
            compactions += 1
            log(f"上下文压缩 #{compactions} → {len(messages)} 条 / {_msg_chars(messages)} 字符")
        try:
            resp = call_llm(messages, TOOLS)
        except Exception as e:
            log(f"LLM 调用失败(已重试7次·累计退避~6.6min): {e}")
            fail_stage = "llm_error"
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
            fail_stage = fail_stage or "no_progress"
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
                    if wl is not None and not _wl_ok(args["path"], wl):
                        result = (f"REJECTED: 编辑白名单机器门——本任务声明 EDIT_WHITELIST: "
                                  f"{', '.join(wl)}；路径 {args['path']} 在白名单外，写入被拒。"
                                  f"只准改白名单内文件；确需越界改动，在 finish 里说明理由交人工裁。")
                    else:
                        result = tool_write_file(root, args["path"], args.get("content", ""))
                        changed.append(args["path"])
                elif name == "run_shell":
                    result = tool_run_shell(root, args["command"])
                elif name == "run_cluster":
                    result = tool_cluster(args.get("action"), repo=args.get("repo"),
                                          cmd=args.get("cmd"), ref=args.get("ref", "main"),
                                          node=args.get("node", "auto"),
                                          shards=args.get("shards"),
                                          cmd_tpl=args.get("cmd_tpl"),
                                          job_id=args.get("job_id"),
                                          timeout=args.get("timeout", 3600),
                                          stage_dir=args.get("stage_dir"))
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
                _elapsed = int(time.time() - t0)
                _hb = None
                try:
                    _hb = api(f"/tensor/missions/{mission['id']}/heartbeat",
                              {"claim_token": claim_token, "head": f"{name} @ call {calls}",
                               "tool_calls": calls, "elapsed_seconds": _elapsed,
                               "wall_seconds": _elapsed,
                               "changed_files": changed[-20:]}, token)
                except Exception as _e:
                    log(f"心跳失败(不阻断): {_e}")
                # 70% 预算收口检查点：把服务端/本地 MVD 判定变成模型能看到的指令
                if mvd_mode != "off":
                    try:
                        _gate = mvd_gate.evaluate_heartbeat(
                            _hb, mode=mvd_mode, already_injected=mvd_injected,
                            tool_calls=calls, max_tool_calls=max_calls,
                            wall_seconds=_elapsed, max_wall_seconds=max_wall,
                            changed_files=changed, checks=None, headline=None)
                        if _gate["event"]:
                            mvd_events.append(_gate["event"])
                            log(f"MVD 检查点 {_gate['budget_pct']}% "
                                f"({_gate['reason']}, missing={_gate['event'].get('missing')})")
                        if _gate["inject"]:
                            messages.append({"role": "user", "content": _gate["instruction"]})
                            mvd_injected = True
                    except Exception as _e:
                        log(f"MVD 检查点异常(不阻断): {_e}")
                hb_at = time.time()
        if final:
            break

    if final is None and calls >= max_calls:
        fail_stage = fail_stage or "budget_exhausted"
    elif final is None and time.time() - t0 >= max_wall:
        fail_stage = fail_stage or "wall_exhausted"

    # 强制收尾轮（2026-10-30）：预算耗尽≠裸死——给一次明确要求 finish 的收尾机会，
    # 把近成功单（产物已落盘只是没喊完成）转成诚实 finish，而不是自动判 failed。
    if final is None and calls > 0 and fail_stage != "llm_error":
        messages.append({"role": "user", "content":
                         "预算/时间已尽，这是最后一次机会：立即调用 finish 工具，如实总结已完成项、"
                         "未完成项与产物文件位置（含 docs/、artifacts/、runs/ 路径）。不调用 finish 本单记 failed。"})
        try:
            resp = call_llm(messages, TOOLS)
            _m2 = resp["choices"][0]["message"]
            for _tc in (_m2.get("tool_calls") or []):
                if _tc["function"]["name"] == "finish":
                    final = json.loads(_tc["function"].get("arguments") or "{}")
                    fail_stage = (fail_stage + "+forced_finish").lstrip("+")
                    log("强制收尾轮：模型补调 finish，按其自述总结收账")
                    break
        except Exception as _e:
            log(f"强制收尾轮失败: {_e}")

    diff = ""
    try:
        p = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True, timeout=60)
        diff = p.stdout[-2000:]
    except Exception:
        pass
    # 以工作树实际改动为准（不信模型自述）；ignored 产物（runs/、artifacts/ 等）计入证据（m425 教训）
    tracked = [ln[3:].strip() for ln in diff.splitlines() if ln.strip()]
    new_ignored = [e for e in sorted(evidence_snapshot(root) - base_evidence)
                   if not _EVIDENCE_NOISE.search(e)][:50]
    committed = []
    if base_sha:
        try:
            p2 = subprocess.run(["git", "-C", str(root), "diff", "--name-only", base_sha],
                                capture_output=True, text=True, timeout=60)
            committed = [ln.strip() for ln in p2.stdout.splitlines()
                         if ln.strip() and not _EVIDENCE_NOISE.search(ln.strip())][:100]
        except Exception:
            pass
    on_disk = sorted(set(tracked) | set(new_ignored) | set(committed))
    _hb_stop.set()
    return {"summary": (final or {}).get("summary") or f"完成 {calls} 次工具调用（未调用 finish）",
            "checks": (final or {}).get("checks") or "",
            "changed_files": sorted(set(changed)),
            "on_disk_changes": on_disk,
            "evidence_ignored": new_ignored,
            "fail_stage": fail_stage, "compactions": compactions,
            "mvd_checkpoint": (mvd_events[-1] if mvd_events else None),
            "mvd_injected": mvd_injected,
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
    log(f"worker {AGENT_ID} | role={ROLE} | model={MODEL} | harness={HARNESS_VER} | session={sid[:8]}…")
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
            # 队列空 → 任务榜直领兜底（FEAT-BOARD-CLAIM）。
            # 主路径仍是「谁空谁领」，但队列见底时不必等 dispatcher 造单：服务端从
            # feature_ledger 的开放锚挑一个（跳过活租约/在飞单覆盖的）→ 锚级租约 →
            # 组装 goal → 落 mission，本席随即按 mission_id 领走。
            # 开关 TP_BOARD_FALLBACK=0 可关断（对照组/排障用），TP_BOARD_DOMAIN 可钉域。
            if CFG.get("TP_BOARD_FALLBACK", "1").strip() not in ("0", "false", "off"):
                _bbody = {"worker_role": ROLE, "model": MODEL, "holder": f"worker:{AGENT_ID}"}
                if CFG.get("TP_BOARD_DOMAIN"):
                    _bbody["domain"] = CFG["TP_BOARD_DOMAIN"]
                sb, bd = api("/tensor/board/claim", _bbody, token)
                if sb == 200 and bd.get("claimed"):
                    log(f"board 造单锚 {bd.get('anchor', {}).get('slug')} → mission#{bd['mission_id']}")
                    s2, d2 = api("/tensor/missions/claim",
                                 {"agent_id": AGENT_ID, "runner": "native", "model": MODEL,
                                  "mission_id": bd["mission_id"], "claim_null_model": True}, token)
                    if s2 == 200 and d2.get("claimed"):
                        d = d2
                    else:
                        log(f"board 造单未能领取 {s2} {str(d2)[:120]}")
                elif sb != 200:
                    log(f"board/claim 失败 {sb} {str(bd)[:120]}")
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
        finally:
            _reap_mission_processes()  # 任务终局收割：成功/失败/worker 异常/SystemExit 全路径
        log(f"完成 {result['tool_calls']} 次调用 / {result['elapsed']}s / "
            f"声明改动 {len(result['changed_files'])} 个 / 实际落盘 {len(result['on_disk_changes'])} 个 / "
            f"finish={'是' if result['finished'] else '否'}")

        # ★ 反吹牛闸门：必须有 finish 且工作树真有改动，才允许提请"成功验收"
        has_evidence = result["finished"] and len(result["on_disk_changes"]) > 0
        if not has_evidence:
            reason = ("模型未调用 finish" if not result["finished"]
                      else "工作树无任何改动，不能报成功")
            if not result["finished"] and result.get("fail_stage"):
                reason = f"模型未调用 finish（终局: {result['fail_stage']}）"
            log(f"⚠ 证据不足 → 不提请验收：{reason}")
            s, d = api(f"/tensor/missions/{mission['id']}/complete",
                       {"claim_token": ct, "status": "failed", "acceptance": False,
                        "summary": {"headline": f"未完成：{reason}", "model": MODEL,
                                    "agent_session_id": sid, "tool_calls": result["tool_calls"],
                                    "changed_files": result["changed_files"],
                                    "declared_summary": result["summary"][:300]},
                        "checks": {"gate": "no-evidence", "reason": reason,
                                   "fail_stage": result.get("fail_stage", ""),
                                   "evidence_ignored": (result.get("evidence_ignored") or [])[:20],
                                   "compactions": result.get("compactions", 0)}}, token)
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
                                "checks": result["checks"], "git_status": result["git_status"],
                                "fail_stage": result.get("fail_stage", ""),
                                "compactions": result.get("compactions", 0)}},
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