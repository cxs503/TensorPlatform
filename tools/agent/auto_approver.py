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
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://localhost:3000/api/v1").rstrip("/")
WORK_ROOT = "/nfs/wangxi/tensor_eco/agent_work"
OFF_SWITCH = "/nfs/wangxi/tensor_eco/agent_work/auto_approver.OFF"

# owner 2026-10-23 指令「取消人工审批环节，默认全部审批通过」：
# 开关文件存在 → 每轮把全部 pending（含停车场）默认批准，不再走 LLM 裁决/停车。rm 此文件即恢复原裁决流。
PASS_ALL_SWITCH = "/nfs/wangxi/tensor_eco/agent_work/auto_approver.PASS_ALL"

GRACE_SECONDS = 120          # 刚创建的审批先留给人类 2 分钟
MAX_DECISIONS_PER_RUN = 8
NOTE_PREFIX = "[auto-review v1]"
REVIEWER_MODEL = os.environ.get("REVIEW_LLM_MODEL", "glm-5.2")
ECO_ROOT = "/nfs/wangxi/tensor_eco"     # 五克隆根（基线 worktree 用）
BASE_WT_ROOT = os.path.join(WORK_ROOT, ".base_wt")

REPO_TEST_PYTHON = {
    # 与 agent 实战口径对齐：非 TensorLBM 仓库用 hermes venv（pytest 9.1.1
    # +torch 2.14，m42 实测 217 passed/26.8s；tensor_eco venv 无 pytest）
    "TensorLBM": "/nfs/wangxi/venvs/tensorlbm/bin/python",
    "_default": "/nfs/wangxi/hermes-agent/.venv/bin/python",
}


def repo_pythonpath(wt: str) -> str:
    """复跑导入隔离：src 布局 → <wt>/src；平铺 → <wt>。

    2026-10-21 实录：hermes venv 的 tensordem/tensorlbm/tensorphysics 是
    editable 装的 m42 时代工作树——不压路径时复跑的是陈年代码（新模块
    ModuleNotFoundError → 收集中断 →「0 passed / 0 failed」）。PYTHONPATH
    前置让 worktree 源码永远赢，venv 卫生不再影响裁决口径。
    """
    src_dir = os.path.join(wt, "src")
    return src_dir if os.path.isdir(src_dir) else wt


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


def verdict_prompt(context: str) -> str:
    """纯函数：裁决 prompt（便于测试锁定关键规则）。"""
    fmt = '{"verdict": "approve|reject|escalate", "reason": "不超过120字"}'
    return (
        "你是科研平台的验收裁决器。根据给出的证据判断该 acceptance 是否该批准。\n"
        "规则：证据自洽且达到 goal 的机器判据=approve；证据与复跑矛盾或未达判据=reject；"
        "证据不足/无法判断/涉及物理精度主张而证据不含数值=escalate。\n"
        "复跑失败若已标注为基线已有红（与本案交付无关），不得据此 reject；"
        "拿不准时一律 escalate 而不是 reject。\n"
        "第一行必须且只能是如下 JSON（不要思考、不要解释）：\n"
        f"{fmt}\n\n"
        f"证据：\n{context[:9000]}")


def _parse_verdict(text: str) -> dict:
    """纯函数：裁决输出解析。四级容错：完整 JSON → 宽松 JSON →
    裸 verdict 键 → 裸单词行；全失败 escalate（绝不误判成 approve/reject）。"""
    m = re.search(r"\{[^{}]*\"verdict\"[^{}]*\}", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            if obj.get("verdict") in ("approve", "reject", "escalate"):
                return obj
        except json.JSONDecodeError:
            pass
    mv = re.search(r'\"verdict\"\s*:\s*\"(approve|reject|escalate)\"', text)
    if mv:
        mr = re.search(r'\"reason\"\s*:\s*\"([^\"]{0,120})', text)
        return {"verdict": mv.group(1), "reason": mr.group(1) if mr else ""}
    mw = re.search(r"^\s*(approve|reject|escalate)\s*$", text, re.M)
    if mw:
        return {"verdict": mw.group(1), "reason": ""}
    return {"verdict": "escalate", "reason": f"裁决输出不可解析：{text[:120]}"}


def _stream_chat(base: str, key: str, messages: list) -> str:
    """流式调裁决 LLM：返回 content，空则 reasoning（思考型模型实录，
    reasoning 可能先吃光预算、content 落空）。"""
    body = json.dumps({"model": REVIEWER_MODEL, "stream": True,
                       "max_tokens": 12288, "temperature": 0,
                       "messages": messages}).encode()
    req = urllib.request.Request(
        f"{base.rstrip('/')}/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    parts: list[str] = []
    reasoning: list[str] = []
    with urllib.request.urlopen(req, timeout=180) as resp:
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
    return "".join(parts) or "".join(reasoning)


def _unparseable(v: dict) -> bool:
    return (v.get("verdict") == "escalate"
            and str(v.get("reason", "")).startswith("裁决输出不可解析"))


def llm_verdict(context: str) -> dict:
    """glm 裁决：证据上下文 -> {verdict: approve|reject|escalate, reason}。
    流式累计防中继非流式挂起；解析失败加压重试一次，再败一律 escalate。
    2026-10-21 实录：思考流 content 空 + 2048 截断 → 三单连停
    （ap108/109/110「裁决输出不可解析」）——修=max_tokens 4096 + 重试。"""
    base = os.environ.get("LLM_BASE_URL", "")
    key = os.environ.get("LLM_API_KEY", "")
    prompt = verdict_prompt(context)
    sysmsg = {"role": "system", "content": "只输出 JSON。"}
    v = _parse_verdict(
        _stream_chat(base, key, [sysmsg, {"role": "user", "content": prompt}]))
    if _unparseable(v):
        retry = prompt + (
            "\n\n上一次输出没有裁决 JSON（思考流/截断）。"
            "本次第一行必须且只能是该 JSON，不得有任何其他文字。")
        v2 = _parse_verdict(
            _stream_chat(base, key,
                         [sysmsg, {"role": "user", "content": retry}]))
        if not _unparseable(v2):
            return v2
    return v


def enumerate_failures(wt: str, python: str) -> list[str] | None:
    """全量枚举 worktree 失败测试 node id（无 -x，--tb=no）。None=枚举不可得。"""
    code, out = sh(
        f"PYTHONPATH={repo_pythonpath(wt)} {python} -m pytest"
        f" -q --tb=no tests", cwd=wt, timeout=2400)
    if code == 124:
        return None
    return re.findall(r"^(?:FAILED|ERROR) (\S+)", out, re.M)


def baseline_failures(repo_dir: str, base: str, python: str,
                      node_ids: list[str]) -> set[str] | None:
    """在 base_ref 临时 worktree 复跑指定失败集，返回基线也红的子集。
    缓存 .base_wt/<repo>@<base8>（base 前进自动重建）。
    返回 None=归因机制故障（调用方 escalate，绝不因此 approve/reject）。
    基线上不存在的测试（本案新增文件）天然不在返回集=计入新增红。"""
    if not node_ids:
        return set()
    src_repo = os.path.join(ECO_ROOT, repo_dir)
    if not os.path.exists(os.path.join(src_repo, ".git")):
        return None
    bdir = os.path.join(BASE_WT_ROOT, f"{repo_dir}@{base[:8]}")
    try:
        os.makedirs(BASE_WT_ROOT, exist_ok=True)
        if os.path.exists(bdir):
            c, cur = sh("git rev-parse HEAD", cwd=bdir)
            if c != 0 or not cur.strip().startswith(base[:8]):
                import shutil
                shutil.rmtree(bdir, ignore_errors=True)
                sh("git worktree prune", cwd=src_repo)
        if not os.path.exists(bdir):
            c, _ = sh(f"git worktree add --detach {bdir} {base}", cwd=src_repo,
                      timeout=300)
            if c != 0:
                return None
        # 按文件存在性分流：base 上不存在的测试文件（本案新增）混进同一
        # pytest 命令会让整体 usage-error、一个 FAILED 都不吐（T3 实测）——
        # 先剔除，它们天然计入新增红。
        existing = [n for n in node_ids
                    if os.path.exists(os.path.join(bdir, n.split("::")[0]))]
        if not existing:
            return set()
        quoted = " ".join(f"'{n}'" for n in existing)
        code, out = sh(
            f"PYTHONPATH={repo_pythonpath(bdir)} {python} -m pytest"
            f" -q --tb=no {quoted}", cwd=bdir, timeout=1200)
        if code == 124:
            return None
        ran_red = set(re.findall(r"^(?:FAILED|ERROR) (\S+)", out, re.M))
        if code not in (0, 1) and not ran_red:
            return None    # pytest 自身没跑起来（环境/参数问题）≠基线全绿
        return {n for n in existing if n in ran_red}
    except Exception:   # noqa: BLE001  归因机制故障≠交付有罪
        return None


def branch_diff_block(wt: str, base: str) -> str:
    """交付分支全量画像：commit 列表 + base..HEAD 全量 diffstat。

    m126 误杀教训（2026-10-21）：`git show --stat <head>` 只含末提交——
    「先交付 .md、后补验收 JSON」的正常多提交交付被误读成"仅改动该 JSON"。
    判据：分支交付 = base..HEAD 累积，不是末提交。
    """
    _, commits = sh(f"git log --oneline {base}..HEAD", cwd=wt)
    _, stat = sh(f"git diff --stat {base}..HEAD", cwd=wt)
    n = len(commits.splitlines())
    return (f"分支提交（{base}..HEAD 共 {n} 个，交付=累积非仅末提交）：\n"
            f"{commits[:700]}\n"
            f"分支全量 diffstat（{base}..HEAD）：\n{stat[:1500]}")


def claimed_deliverables(goal: str) -> list[str]:
    """提取 goal 明示的本仓交付文件（产出/新增/交付 + 含 / 的路径）。纯函数。

    只认任务自身 goal 段——注入记忆里引用的往次产物不算本案交付。
    """
    head = goal.split("往次任务记忆（平台注入", 1)[0]
    out = []
    for m in re.finditer(
            r"(?:产出|新增|交付)[^\n`]{0,12}?`?([\w./-]+\.[A-Za-z0-9]{1,6})`?", head):
        p = m.group(1)
        if "/" in p and not p.startswith("http"):
            out.append(p)
    return sorted(set(out))


def presence_split(wt: str, base: str, files: list[str]) -> tuple[list[str], list[str]]:
    """交付文件是否真在 base..HEAD 变更集：在场/缺席二分。机器判据。

    缺席不机械 reject（多仓 mission 的他仓交付会误伤）——只作为缺席事实
    交给 LLM；在场则构成"不得以未见文件 reject"的硬事实。
    """
    if not files:
        return [], []
    _, changed = sh(f"git diff --name-only {base}..HEAD", cwd=wt)
    changed_set = set(changed.split())
    present = [f for f in files if f in changed_set]
    absent = [f for f in files if f not in changed_set]
    return present, absent


def machine_json_values(wt: str, rel_files: list[str],
                        abs_files: list[str], per_file: int = 10) -> str:
    """机器直读交付 JSON 数值（worktree 实读，非 agent 转述）。

    2026-10-21 实录：complete 端点的 evidence_machine 只记文件在场性
    (ok:true)——物理精度主张没有机器数值可比，LLM 在 escalate/approve
    间边际摆动（m136/m140 approved vs m134/m141 首发 escalate，同形状
    证据两种结果）。裁决器自己有 worktree：直接读 JSON 压平数值注入
    上下文，把摆动变成可比对。
    """
    out: list[str] = []
    seen: set[str] = set()
    for p in list(rel_files) + list(abs_files):
        if not p.endswith(".json"):
            continue
        fp = p if os.path.isabs(p) else os.path.join(wt, p)
        key = os.path.normpath(fp)
        if key in seen:
            continue
        seen.add(key)
        try:
            with open(fp, encoding="utf-8") as fh:
                obj = json.load(fh)
        except (OSError, ValueError):
            continue
        flat: list[str] = []

        def _walk(o, prefix=""):
            if len(flat) >= per_file:
                return
            if isinstance(o, dict):
                for k, v in o.items():
                    if len(flat) >= per_file:
                        return
                    if isinstance(v, (dict, list)):
                        _walk(v, f"{prefix}{k}.")
                    else:
                        sval = str(v)
                        flat.append(f"{prefix}{k}"
                                    + (f"={sval}" if len(sval) <= 24
                                       else f"=<{len(sval)}字>"))
            elif isinstance(o, list):
                for v in o[:6]:
                    _walk(v, prefix)

        _walk(obj)
        if flat:
            try:
                shown = os.path.relpath(fp, wt)
            except ValueError:
                shown = fp
            out.append(f"{shown}: " + "; ".join(flat))
    if not out:
        return ""
    return ("\n机器直读 JSON 数值（裁决器实读 worktree，可信级同复跑；"
            "物理精度主张必须与这些数字比对）：\n"
            + "\n".join(out)[:2000] + "\n")


def checks_capacity(machine_checks: int, test_scope: bool,
                    n_pass: int, n_fail: int, n_skip: int) -> int | None:
    """C3 容量分母：checks 有两类——测试数 vs 锚点判据数（范畴不同）。

    有机器摘录 checks → 分母=摘录数（evidence_files 是权威账本）；
    无摘录且 goal 非测试口径 → 不比数（把锚点数当 pytest 数比
    = 系统性别 escalate：m133/m138/m139 实录，checks 25/37/48 vs 14）。
    """
    if machine_checks > 0:
        return machine_checks
    if test_scope:
        return n_pass + n_fail + n_skip
    return None


def guard_false_kill(verdict: str, reason: str, present: list[str]) -> tuple[str, str]:
    """LLM 裁 reject 且理由是"未见文件"而机器核验在场 → 转人工复核。

    不静默翻 approve（防另一类误放）；只把误杀嫌疑升级人工，附机器事实。
    m126 误杀教训机制化。
    """
    if (verdict == "reject" and present
            and re.search(r"未见|不存在|缺失|未提交|未落", reason)):
        return ("escalate",
                "LLM 裁 reject 理由为文件未见，但机器核验 base..HEAD 在场："
                f"{present[:6]}——误杀嫌疑，转人工。原始理由：{reason[:160]}")
    return verdict, reason


def _gate_front_verdict(mid: int) -> dict | None:
    """dev_acceptance 强制前门（gate.json 证据契约）。

    策略与路径全部来自 config/gate_contract/acceptance_policy.json；
    门模块缺失/异常一律返回 None（不改旧行为），绝不因门崩掉审批流。
    """
    try:
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from tools.gate_contract.acceptance import precheck_mission
        return precheck_mission(int(mid))
    except Exception:
        return None


def verify_dev_acceptance(api: API, mission: dict,
                          evidence: dict) -> tuple[str, str]:
    """返回 (verdict, evidence_text)。verdict ∈ approve/reject/escalate。
    evidence 来自审批行（complete 端点写入）：branch/head_commit/summary/checks。"""
    mid = mission["id"]
    _front = _gate_front_verdict(mid)
    if _front is not None and _front.get("decision") not in (None, "allow_auto_approve"):
        return _front["approver_verdict"], _front["note"]
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
    # C5 教训（m42）：测试在脏树上全过但交付未落 commit——gc 回收即蒸发。
    # 交付必须是分支上的 commit：工作树脏 or 零新提交 → 不批。
    _, dirty = sh("git status --porcelain", cwd=wt)
    if dirty.strip():
        return "escalate", (f"工作树有 {len(dirty.splitlines())} 项未提交变更"
                            "——交付未落 commit，留人工")
    base = mission.get("base_ref") or "main"
    _, ahead = sh(f"git rev-list --count {base}..HEAD", cwd=wt)
    if ahead.strip().isdigit() and int(ahead.strip()) == 0:
        return "escalate", f"分支相对基线 {base} 零新提交——交付未落 commit"
    goal = mission.get("goal", "")
    stat = branch_diff_block(wt, base)
    # 按仓库口径复跑默认套件（与 do_run_tests 缺省命令同口径）。
    # 注意：agent 声称的 checks 可能来自子集命令，与全量复跑口径不同——
    # 声称/复跑差异交给 LLM 裁决，硬否决只在 goal 明确要求默认套件全绿时触发。
    python = REPO_TEST_PYTHON.get(repo_dir, REPO_TEST_PYTHON["_default"])
    t_code, t_out = sh(
        f"PYTHONPATH={repo_pythonpath(wt)} {python} -m pytest"
        f" -q tests --continue-on-collection-errors", cwd=wt, timeout=1800)
    tail = t_out[-2500:]
    passed = re.findall(r"(\d+) passed", t_out)
    failed = re.findall(r"(\d+) failed", t_out)
    n_pass = int(passed[-1]) if passed else 0
    n_fail = int(failed[-1]) if failed else 0
    skips = re.findall(r"(\d+) skipped", t_out)
    n_skip = int(skips[-1]) if skips else 0
    if t_code == 124:
        return "escalate", f"默认套件复跑超时（>1800s）——证据不足"
    # 失败归因（approval 73 误杀教训）：复跑红先区分基线已有红 vs 本案
    # 新增——main 已知红不能算到交付头上；归因机制故障一律 escalate。
    new_reds: list[str] = []
    n_base_red = 0
    if t_code != 0:
        head_ids = enumerate_failures(wt, python)
        if head_ids is None:
            return "escalate", "默认套件复跑红且失败枚举不可得——归因不明，留人工"
        base_ids = baseline_failures(repo_dir, base, python, head_ids)
        if base_ids is None:
            return "escalate", "基线归因机制故障（worktree/pytest 异常）——留人工"
        new_reds = [t for t in head_ids if t not in base_ids]
        n_base_red = len(head_ids) - len(new_reds)
    # 红队 C3 教训：声称 checks 超过同头复跑容量（passed+failed+skipped）=
    # 口径矛盾。agent 跑子集时 claimed < 容量属正常，claimed > 容量不可能。
    claimed_total = (evidence.get("checks") or {}).get("total")
    _machine_early = (evidence.get("summary") or {}).get("evidence_machine") or {}
    _mc = sum(len(f.get("checks") or [])
              for f in _machine_early.get("files") or [])
    _test_scope = bool(re.search(r"默认测试|pytest|测试套件|默认套件", goal))
    _capacity = checks_capacity(_mc, _test_scope, n_pass, n_fail, n_skip)
    if (t_code == 0 and isinstance(claimed_total, int)
            and _capacity is not None and claimed_total > _capacity):
        return "escalate", (f"声称 checks {claimed_total} 项超过其口径容量 "
                            f"{_capacity}（{_mc and '机器摘录' or '测试复跑'}）"
                            "——口径矛盾，留人工")
    if re.search(r"默认测试全绿|全量测试|pytest -x -q tests|默认套件", goal):
        if new_reds:
            return ("reject", f"goal 要求默认套件全绿，复跑存在相对基线新增失败 "
                    f"{len(new_reds)} 项：{new_reds[:6]}\n复跑尾部：\n{tail[-800:]}")
        if t_code != 0:
            return "escalate", (f"复跑 exit={t_code} 但 {n_base_red} 项失败均为"
                                "基线已有红（与本案交付无关）——是否放行留人工")
    claimed = evidence.get("summary") or {}
    # 机器摘录交叉核（m45 教训机制化）：request_acceptance 的 evidence_files
    # 由平台直读工件生成 summary.evidence_machine。有摘录时——
    #   摘录任一 check passed=False → 硬否决（工件自己说不过，转述不作数）；
    #   摘录与摘要数字是否一致交给 LLM 裁决（避免格式差异机械误杀）；
    #   goal 明确要求 validation_report 判据而未附摘录 → 留人工（mid>47 起，
    #   m47 是参数上线前最后一个场景腿，仍走旧口径 LLM 红队）。
    machine = claimed.get("evidence_machine") or {}
    deliver = claimed_deliverables(goal)
    present, absent = presence_split(wt, base, deliver)
    presence_block = ""
    if deliver:
        presence_block = ("\n机器核验交付文件在场性（base..HEAD 变更集，可信级最高）：\n"
                          + (f"在场：{present}\n" if present else "")
                          + (f"缺席：{absent}\n" if absent else "")
                          + "——在场文件不得以'未见文件'为由 reject。\n")
    values_block = machine_json_values(
        wt, present,
        [str(f.get("file") or "") for f in machine.get("files") or []])
    machine_block = ""
    if machine.get("files"):
        for f in machine["files"]:
            for c in f.get("checks") or []:
                if c.get("passed") is False:
                    return "reject", (f"工件机器摘录判 {c.get('name')} passed=False："
                                      f"{c.get('detail')}（{f.get('file')}）"
                                      "——工件不过门，验收转述不作数")
        machine_block = ("\n机器直读工件摘录（evidence_files，可信级高于验收摘要，"
                         "请比对摘要数字与摘录数字是否逐字一致）：\n"
                         + json.dumps(machine, ensure_ascii=False)[:1800] + "\n")
    if (mid > 47 and "validation_report" in goal.split("往次任务记忆（平台注入", 1)[0]
            and not machine.get("files")):
        # 只看任务自身 goal：注入记忆里会引用往次 validation_report 字样，
        # 不能拿来触发本任务的摘录要求（approval 33 误升级教训）。
        return "escalate", ("goal 要求 validation_report 工件判据，但验收未附 "
                            "evidence_files 机器摘录——数字来源不可核验，留人工")
    context = (f"goal：{goal[:1200]}\n\n"
               f"验收摘要：{json.dumps(claimed, ensure_ascii=False)[:1400]}\n"
               f"声称 checks：{(evidence.get('checks') or {}).get('passed')}"
               f"/{(evidence.get('checks') or {}).get('total')}\n"
               f"{presence_block}{machine_block}{values_block}\n"
               f"默认套件复跑：exit={'0' if t_code == 0 else t_code}，"
               f"{n_pass} passed / {n_fail} failed；"
               + ("全绿" if t_code == 0 else
                  (f"新增失败（相对基线，与本案相关）{len(new_reds)} 项："
                   f"{new_reds[:6]}" if new_reds else
                   f"{n_base_red} 项失败均为基线已有红——与本案交付无关，"
                   "不得据此 reject"))
               + "\n"
               f"复跑尾部：\n{tail[-1200:]}\n\n"
               f"提交：\n{stat}")
    verdict = llm_verdict(context)
    v_out, r_out = guard_false_kill(verdict["verdict"], verdict["reason"], present)
    return v_out, (r_out + f"\n\n复跑：{n_pass} passed / "
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


MEMORY_ROOT = os.environ.get(
    "TENSOR_MEMORY_ROOT", "/nfs/wangxi/tensor_eco/agent_memory")


ESCALATED_SEEN = os.environ.get(
    "TENSOR_ESCALATED_SEEN",
    "/nfs/wangxi/tensor_eco/agent_work/approver_escalated.json")


def escalated_seen() -> dict:
    """已 escalate 停车的 approval id 表（后端只收 approved|rejected，
    escalate 落不了库——不本地停车就每 tick 重裁决+重跑套件）。"""
    try:
        return json.loads(open(ESCALATED_SEEN, encoding="utf-8").read())
    except (OSError, ValueError):
        return {}


def escalated_record(store: dict, ap: dict, note: str) -> None:
    store[str(ap["id"])] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mission": ap.get("mission_id"), "note": note[:200]}
    try:
        with open(ESCALATED_SEEN, "w", encoding="utf-8") as fh:
            json.dump(store, fh, ensure_ascii=False)
    except OSError:
        pass  # 停车失败只是回到旧行为（重复 escalate），不影响裁决正确性


def _memory_append_decision(approval: dict, decision: str) -> None:
    """裁决入常驻记忆（有路由的 mission）：审批结论是 jsonl 事实账本的一部分。
    与 tensor_agent.append_memory 各自独立实现——本文件保持纯标准库。
    与其同规（PR #22 初审第 5 点跟进）：UTC 时间戳、flock 排他写、
    fingerprint 幂等（审批器重跑/补单时同一裁决不重复入账）。"""
    mid = approval.get("mission_id")
    if not mid:
        return
    try:
        route = json.loads(open(os.path.join(
            MEMORY_ROOT, "_routes", f"m{mid}.json"), encoding="utf-8").read())
        key = route["memory_key"]
        d = os.path.join(MEMORY_ROOT, key)
        os.makedirs(d, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "mission": mid,
               "status": f"approval_{decision}",
               "approval_id": approval.get("id"),
               "kind": approval.get("kind")}
        fingerprint = (mid, rec["status"], rec["approval_id"], rec["kind"])
        import fcntl
        with open(os.path.join(d, "memory.jsonl"), "a+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.seek(0)
                for line in fh:
                    try:
                        old = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if (old.get("mission"), old.get("status"),
                            old.get("approval_id"), old.get("kind")) == fingerprint:
                        return        # 已入账：审批器重跑幂等
                fh.seek(0, os.SEEK_END)
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    except (OSError, ValueError):
        pass  # 无路由/读写失败：裁决本身不受影响


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
    if os.path.exists(PASS_ALL_SWITCH):
        code, data = api.call("GET", "/tensor/approvals?status=pending&limit=50")
        if code != 200:
            log(f"PASS_ALL list failed: {code}")
            return
        n = 0
        for ap in data.get("items", []):
            try:
                decide(api, ap["id"], "approved",
                       "PASS_ALL：owner 2026-10-23 指令「取消人工审批环节，"
                       "默认全部审批通过」——机械默认批准，未走逐案裁决")
                n += 1
            except Exception as e:                    # noqa: BLE001
                log(f"PASS_ALL approval {ap['id']} 失败 {type(e).__name__}")
        # 停车场随之清空（全部默认批准语义）
        try:
            Path("/nfs/wangxi/tensor_eco/agent_work/approver_escalated.json"
                 ).write_text("{}", encoding="utf-8")
        except OSError:
            pass
        log(f"PASS_ALL 模式：本轮默认批准 {n} 项，停车场已清空")
        return
    code, data = api.call("GET", "/tensor/approvals?status=pending&limit=50")
    if code != 200:
        log(f"list pending failed: {code} {str(data)[:150]}")
        return
    items = data.get("items", [])
    if not items:
        return
    seen = escalated_seen()
    log(f"pending={len(items)}"
        + (f"（{len(seen)} 已 escalate 停车）" if seen else ""))
    decided = 0
    for ap in items:
        if str(ap["id"]) in seen:
            # 停车=冻结：复验每 tick 重跑全套纯属烧机器（m134 实录
            # 25min/轮，flock 还挡住其他审批）。解冻=人工移出停车表。
            log(f"approval {ap['id']} 已 escalate 停车——跳过")
            continue
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
            _memory_append_decision(ap, "approved")
            decided += 1
        elif verdict == "reject":
            decide(api, ap["id"], "rejected", note)
            _memory_append_decision(ap, "rejected")
            decided += 1
        else:
            if str(ap["id"]) in seen:
                log(f"approval {ap['id']} 已 escalate 停车——跳过")
                continue
            escalated_record(seen, ap, note)
            _memory_append_decision(ap, "escalated")
            log(f"approval {ap['id']} escalate（已停车留人工）：{note[:200]}")


if __name__ == "__main__":
    main()
