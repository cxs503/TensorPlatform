#!/usr/bin/env python3
"""tensor-agent — mission bridge daemon (OpenHands SDK harness).

Claims dev missions from the research platform and drives an OpenHands SDK
Conversation inside a per-mission git worktree. The SDK owns the agent loop,
tools, retries and context management; this bridge owns:

  claim/heartbeat/complete protocol  (platform tensor_dev_missions)
  worktree preparation               (agent_work/m<id>/<repo>, branch agent/m<id>)
  platform event bridge              (SDK events -> session tool_call/tool_result)
  mission tools                      (commit / run_tests / web_search /
                                      fetch_url / request_acceptance)
  budget enforcement                 (wall clock + iteration cap -> pause)

Env is rebuilt from an allowlist at startup: the harness terminal inherits a
sanitized world (no docker socket, no ssh agent, no host gitconfig —
GIT_CONFIG_GLOBAL points at /dev/null and push is disabled per worktree).
"""
from __future__ import annotations

import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Sequence
from typing import Any

# ───────────────────────── environment allowlist ─────────────────────────

ENV_ALLOWLIST = (
    "PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "CUDA_VISIBLE_DEVICES",
    "PYTHONUNBUFFERED", "OPENHANDS_SUPPRESS_BANNER",
    "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL",
    "TENSOR_API_BASE", "TENSOR_AGENT_USER", "TENSOR_AGENT_PASS",
)
STATE_ROOT = "/srv/tensor/shared/tensor_eco/agent_work"
VENV_AGENT = "/srv/tensor/shared/venvs/tensor_agent/bin"
REPO_PATHS = {
    "TensorPhysics": "/srv/tensor/shared/tensor_eco/TensorPhysics",
    "TensorPlatform": "/srv/tensor/shared/tensor_eco/TensorPlatform",
    "TensorFEM": "/srv/tensor/shared/tensor_eco/TensorFEM",
    "TensorDEM": "/srv/tensor/shared/tensor_eco/TensorDEM",
    "TensorFVM": "/srv/tensor/shared/tensor_eco/TensorFVM",
    "TensorLBM": "/srv/tensor/shared/TensorLBM",
}
REPO_TEST_PYTHON = {
    "TensorLBM": "/srv/tensor/shared/venvs/tensorlbm/bin/python",
    # the eco venv has all other tensor repos installed -e
    "_default": "/srv/tensor/shared/venvs/tensor_eco/bin/python",
}
FETCH_DOMAIN_ALLOWLIST = (
    "api.github.com", "github.com", "raw.githubusercontent.com",
    "objects.githubusercontent.com", "codeload.github.com",
    "arxiv.org", "export.arxiv.org", "pypi.org", "files.pythonhosted.org",
    "docs.openhands.dev",
)

AGENT_ID = f"tensor-agent@{socket.gethostname()}"
HEARTBEAT_SECONDS = 15
CLAIM_POLL_SECONDS = 20


def sanitize_env() -> None:
    keep = {k: v for k, v in os.environ.items() if k in ENV_ALLOWLIST}
    keep.setdefault("PATH", f"{VENV_AGENT}:/usr/local/bin:/usr/bin:/bin")
    keep["CUDA_VISIBLE_DEVICES"] = os.environ.get("CUDA_VISIBLE_DEVICES", "5")
    keep["PYTHONUNBUFFERED"] = "1"
    keep["OPENHANDS_SUPPRESS_BANNER"] = "1"
    keep["GIT_TERMINAL_PROMPT"] = "0"
    keep["GIT_CONFIG_GLOBAL"] = "/dev/null"      # no host credentials leak
    keep["GIT_CONFIG_NOSYSTEM"] = "1"
    os.environ.clear()
    os.environ.update(keep)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ───────────────────────── platform API client ─────────────────────────

class PlatformAPI:
    """urllib JSON client with one relogin on 401 (executor precedent)."""

    def __init__(self) -> None:
        self.base = os.environ.get("TENSOR_API_BASE", "http://localhost:3000/api/v1").rstrip("/")
        self.user = os.environ.get("TENSOR_AGENT_USER", "")
        self.password = os.environ.get("TENSOR_AGENT_PASS", "")
        self.token: str | None = None

    def login(self) -> None:
        body = json.dumps({"username": self.user, "password": self.password}).encode()
        req = urllib.request.Request(
            f"{self.base}/auth/login", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        self.token = data["access_token"]

    def api(self, method: str, path: str, body: dict | None = None) -> tuple[int, Any]:
        for attempt in (1, 2):  # one relogin on stale JWT
            if not self.token:
                try:
                    self.login()
                except Exception as exc:  # noqa: BLE001
                    return 0, f"login failed: {exc}"
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(
                self.base + path, data=data, method=method,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {self.token}"})
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return resp.status, json.loads(resp.read() or b"null")
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 1:
                    self.token = None
                    continue
                try:
                    return exc.code, json.loads(exc.read() or b"null")
                except Exception:  # noqa: BLE001
                    return exc.code, {}
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                return 0, f"transport: {exc}"
        return 0, "unreachable"


# ───────────────────────── event bridge (fire-and-forget) ─────────────────────────

class EventBridge:
    """SDK events -> platform session events. Never blocks the agent loop:
    a worker thread drains a queue; failures degrade to a log line."""

    def __init__(self, api: PlatformAPI, session_id: int) -> None:
        self.api = api
        self.session_id = session_id
        self.action_count = 0  # every real tool action (terminal/editor/bridge)
        self.q: queue.Queue[tuple[str, dict] | None] = queue.Queue(maxsize=2000)
        self.worker = threading.Thread(target=self._drain, daemon=True)
        self.worker.start()

    def _drain(self) -> None:
        while True:
            item = self.q.get()
            if item is None:
                return
            kind, payload = item
            try:
                code, resp = self.api.api(
                    "POST", f"/tensor/sessions/{self.session_id}/events",
                    {"kind": kind, "payload": payload})
                if code != 200:
                    log(f"event bridge degraded ({kind} -> {code}): {str(resp)[:120]}")
            except Exception as exc:  # noqa: BLE001
                log(f"event bridge error ({kind}): {exc}")

    def emit(self, kind: str, payload: dict) -> None:
        try:
            self.q.put_nowait((kind, payload))
        except queue.Full:
            log("event bridge queue full — dropping event")

    def close(self) -> None:
        self.q.put(None)
        self.worker.join(timeout=30)


def _clip(value: Any, limit: int = 3500) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + f"\n…[truncated {len(text) - limit} chars]"


def sdk_event_to_platform(event: Any, bridge: EventBridge) -> None:
    name = type(event).__name__
    try:
        if name == "ActionEvent":
            action = getattr(event, "action", None)
            tool = type(action).__name__ if action is not None else "unknown"
            if tool == "MessageAction":  # plain assistant reply, not a tool call
                bridge.emit("note", {"subtype": "assistant",
                                     "text": _clip(action, 1500)})
                return
            payload = {"tool": tool}
            bridge.action_count += 1  # honest budget counter: all tool actions
            if getattr(action, "command", None):
                payload["command"] = _clip(action.command, 600)
            if getattr(action, "path", None):
                payload["path"] = str(action.path)
            try:
                rest = {k: v for k, v in action.model_dump().items()
                        if k not in ("command",) and v not in (None, "", [])}
                payload["args"] = _clip(rest, 900)
            except Exception:  # noqa: BLE001
                pass
            if getattr(event, "thought", None):
                payload["thought"] = _clip(event.thought, 500)
            bridge.emit("tool_call", payload)
        elif name == "ObservationEvent":
            obs = getattr(event, "observation", None)
            bridge.emit("tool_result", {"text": _clip(obs)} if obs is not None
                        else {"text": _clip(event)})
        elif name == "MessageEvent":
            bridge.emit("note", {"subtype": "message", "text": _clip(event, 1500)})
        elif name == "ConversationErrorEvent":
            bridge.emit("error", {"text": _clip(event, 1500)})
        # SystemPromptEvent / AgentStateChangedEvent etc. stay local
    except Exception as exc:  # noqa: BLE001
        bridge.emit("note", {"subtype": "bridge_error", "text": f"{type(exc).__name__}: {exc}"})


# ───────────────────────── worktree preparation ─────────────────────────

def resolve_repo(name: str) -> tuple[str, str]:
    for key, path in REPO_PATHS.items():
        if name.rstrip("/").endswith(key):
            return key, path
    raise RuntimeError(f"unknown repo {name!r}; known: {sorted(REPO_PATHS)}")


def run(cmd: list[str], cwd: str | None = None, timeout: int = 120) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, (proc.stdout + proc.stderr)[-4000:]


def prep_worktree(mission: dict) -> str:
    repo_name, repo_path = resolve_repo(mission["repo"])
    mid = mission["id"]
    base_ref = mission.get("base_ref") or "main"
    wt = os.path.join(STATE_ROOT, f"m{mid}", repo_name)
    os.makedirs(os.path.dirname(wt), exist_ok=True)

    if not os.path.isdir(os.path.join(wt, ".git")):
        # resolve base_ref locally, else shallow-fetch from origin once
        rc, _ = run(["git", "-C", repo_path, "cat-file", "-e", f"{base_ref}^{{commit}}"])
        ref = base_ref
        if rc != 0:
            log(f"base_ref {base_ref} not local — fetching")
            rc, out = run(["git", "-C", repo_path, "fetch", "origin", base_ref,
                           "--depth=1", "--no-tags"], timeout=300)
            if rc == 0:
                ref = "FETCH_HEAD"
            else:
                rc2, _ = run(["git", "-C", repo_path, "cat-file", "-e", "origin/main^{commit}"])
                if rc2 == 0:
                    log(f"fetch failed ({out[:120]}) — falling back to origin/main")
                    ref = "origin/main"
                else:
                    raise RuntimeError(f"cannot resolve base ref {base_ref}: {out[:200]}")
        branch = f"agent/m{mid}"
        rc, out = run(["git", "-C", repo_path, "worktree", "add", "-b", branch, wt, ref],
                      timeout=300)
        if rc != 0:
            raise RuntimeError(f"worktree add failed: {out}")
        # jail + identity, all scoped to this worktree
        for key, val in (
            ("remote.origin.pushurl", "no-push"),
            ("user.name", "tensor-agent"),
            ("user.email", "tensor-agent@tensor-platform"),
        ):
            run(["git", "-C", wt, "config", key, val])
    return wt


def worktree_head(wt: str) -> str:
    rc, out = run(["git", "-C", wt, "rev-parse", "--short", "HEAD"])
    return out.strip() if rc == 0 else "unknown"


# ───────────────────────── mission bridge state ─────────────────────────

class BridgeState:
    """Per-mission handle the custom tools close over."""

    def __init__(self, api: PlatformAPI, mission: dict, claim_token: str,
                 worktree: str, events: EventBridge) -> None:
        self.api = api
        self.mission = mission
        self.mission_id = mission["id"]
        self.repo_name, _ = resolve_repo(mission["repo"])
        self.claim_token = claim_token
        self.worktree = worktree
        self.events = events
        self.tool_calls = 0
        self.acceptance_requested = False
        self.acceptance_response: dict = {}
        self.stop_reason: str | None = None   # owner_stopped / budget_exceeded
        self.conversation: Any = None


BRIDGE: BridgeState | None = None  # single-mission daemon


# ───────────────────────── SDK imports & custom tools ─────────────────────────

sanitize_env()  # must precede SDK import so the harness inherits a clean world

from pydantic import Field  # noqa: E402

from openhands.sdk import (  # noqa: E402
    Action,
    Agent,
    Conversation,
    LLM,
    Observation,
    TextContent,
    Tool,
)
from openhands.sdk.tool import ToolDefinition, ToolExecutor, register_tool  # noqa: E402
from openhands.tools.file_editor import FileEditorTool  # noqa: E402
from openhands.tools.terminal import TerminalExecutor, TerminalTool  # noqa: E402
from openhands.sdk.event import Event  # noqa: E402


class CommitAction(Action):
    message: str = Field(description="Commit message (Chinese, imperative, one line)")


class CommitObservation(Observation):
    ok: bool = False
    head: str = ""
    stats: str = ""
    error: str = ""

    @property
    def to_llm_content(self) -> Sequence[Any]:
        if self.ok:
            return [TextContent(text=f"committed {self.head}\n{self.stats}")]
        return [TextContent(text=f"commit failed: {self.error}")]


class RunTestsAction(Action):
    command: str | None = Field(
        default=None,
        description="Optional test command override (run with cwd=worktree). "
                    "Omit to use the repo default.")


class RunTestsObservation(Observation):
    ok: bool = False
    output: str = ""

    @property
    def to_llm_content(self) -> Sequence[Any]:
        return [TextContent(text=self.output)]


class WebSearchAction(Action):
    query: str = Field(description="Search query (GitHub repos or arxiv paper terms)")


class WebSearchObservation(Observation):
    results: str = ""

    @property
    def to_llm_content(self) -> Sequence[Any]:
        return [TextContent(text=self.results)]


class FetchUrlAction(Action):
    url: str = Field(description="URL to fetch (allowlisted domains only)")


class FetchUrlObservation(Observation):
    ok: bool = False
    body: str = ""

    @property
    def to_llm_content(self) -> Sequence[Any]:
        return [TextContent(text=self.body)]


class RequestAcceptanceAction(Action):
    headline: str = Field(description="One-line conclusion (Chinese)")
    summary: str = Field(description="What was done, key evidence, limitations")
    checks_passed: int = Field(description="Number of tests passed")
    checks_total: int = Field(description="Number of tests run")


class RequestAcceptanceObservation(Observation):
    ok: bool = False
    message: str = ""

    @property
    def to_llm_content(self) -> Sequence[Any]:
        return [TextContent(text=self.message)]


def _git(args: list[str]) -> tuple[int, str]:
    assert BRIDGE is not None
    return run(["git", "-C", BRIDGE.worktree] + args, timeout=180)


def do_commit(action: CommitAction) -> CommitObservation:
    assert BRIDGE is not None
    BRIDGE.tool_calls += 1
    _git(["add", "-A"])
    rc, out = _git(["commit", "-m", action.message])
    if rc != 0 and "nothing to commit" not in out:
        return CommitObservation(ok=False, error=out[-600:])
    head = worktree_head(BRIDGE.worktree)
    _, stats = _git(["show", "--stat", "--oneline", "HEAD"])
    branch = f"agent/m{BRIDGE.mission_id}"
    BRIDGE.events.emit("code_commit", {"head": head, "branch": branch,
                                       "message": action.message,
                                       "stats": stats[-800:]})
    return CommitObservation(ok=True, head=head, stats=stats[-800:])


def do_run_tests(action: RunTestsAction) -> RunTestsObservation:
    assert BRIDGE is not None
    BRIDGE.tool_calls += 1
    default_python = REPO_TEST_PYTHON.get(
        BRIDGE.repo_name, REPO_TEST_PYTHON["_default"])
    cmd = action.command or f"{default_python} -m pytest -x -q tests"
    BRIDGE.events.emit("tool_call", {"tool": "run_tests", "command": cmd})
    try:
        proc = subprocess.run(cmd, shell=True, cwd=BRIDGE.worktree,
                              capture_output=True, text=True, timeout=1800)
        out = (proc.stdout + proc.stderr)
        ok = proc.returncode == 0
    except subprocess.TimeoutExpired:
        out, ok = "test run timed out after 1800s", False
    tail = out[-6000:]
    BRIDGE.events.emit("tool_result", {"tool": "run_tests", "ok": ok,
                                       "tail": tail[-2000:]})
    return RunTestsObservation(ok=ok, output=f"exit={'0' if ok else 'nonzero'}\n{tail}")


def _ip_allowed(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = info[4][0]
        if ip.startswith(("10.", "192.168.", "127.", "169.254.")) or ip == "::1" \
                or re.match(r"^172\.(1[6-9]|2\d|3[01])\.", ip):
            return False
    return True


def http_get(url: str, timeout: int = 30, headers: dict | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "tensor-agent/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(1_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(2000).decode("utf-8", "replace") if exc.fp else ""
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return 0, f"transport: {exc}"


def do_web_search(action: WebSearchAction) -> WebSearchObservation:
    assert BRIDGE is not None
    BRIDGE.tool_calls += 1
    lines: list[str] = []
    q = urllib.parse.quote(action.query)
    code, body = http_get(
        f"https://api.github.com/search/repositories?q={q}&sort=stars&per_page=6",
        headers={"User-Agent": "tensor-agent/1.0", "Accept": "application/vnd.github+json"})
    if code == 200:
        for item in json.loads(body).get("items", []):
            lines.append(f"[GH] {item['full_name']} ★{item['stargazers_count']} — "
                         f"{(item.get('description') or '')[:110]} ({item['html_url']})")
    else:
        lines.append(f"[GH] search failed: {body[:120]}")
    code, body = http_get(
        f"https://export.arxiv.org/api/query?search_query=all:{q}&max_results=4")
    if code == 200:
        for m in re.finditer(r"<title>(.*?)</title>\s*<summary>(.*?)</summary>",
                             body, re.S):
            title = re.sub(r"\s+", " ", m.group(1)).strip()
            if title.lower().startswith("arxiv"):
                continue
            summary = re.sub(r"\s+", " ", m.group(2)).strip()[:160]
            lines.append(f"[arXiv] {title} — {summary}")
    text = "\n".join(lines)[:8000] or "no results"
    BRIDGE.events.emit("web_search", {"query": action.query, "results": text[:3000]})
    return WebSearchObservation(results=text)


def do_fetch_url(action: FetchUrlAction) -> FetchUrlObservation:
    assert BRIDGE is not None
    BRIDGE.tool_calls += 1
    try:
        parsed = urllib.parse.urlparse(action.url)
        host = parsed.hostname or ""
        if parsed.scheme not in ("http", "https"):
            return FetchUrlObservation(ok=False, body=f"scheme {parsed.scheme!r} not allowed")
        if host not in FETCH_DOMAIN_ALLOWLIST:
            return FetchUrlObservation(
                ok=False, body=f"domain {host!r} not in allowlist {FETCH_DOMAIN_ALLOWLIST}")
        if not _ip_allowed(host):
            return FetchUrlObservation(ok=False, body=f"host {host!r} resolves to private IP")
        code, body = http_get(action.url, timeout=45)
        ok = code == 200
        return FetchUrlObservation(ok=ok, body=f"HTTP {code}\n" + body[:6000])
    except Exception as exc:  # noqa: BLE001
        return FetchUrlObservation(ok=False, body=f"{type(exc).__name__}: {exc}")


def do_request_acceptance(action: RequestAcceptanceAction) -> RequestAcceptanceObservation:
    assert BRIDGE is not None
    BRIDGE.tool_calls += 1
    head = worktree_head(BRIDGE.worktree)
    code, resp = BRIDGE.api.api(
        "POST", f"/tensor/missions/{BRIDGE.mission_id}/complete",
        {
            "claim_token": BRIDGE.claim_token,
            "status": "succeeded",
            "acceptance": True,
            "branch": f"agent/m{BRIDGE.mission_id}",
            "head_commit": head,
            "summary": {"headline": action.headline, "detail": action.summary},
            "checks": {"passed": action.checks_passed, "total": action.checks_total},
        })
    if code != 200:  # one transport-level retry, then surface everything
        log(f"m{BRIDGE.mission_id}: acceptance HTTP {code}: {str(resp)[:300]}")
        time.sleep(5)
        code, resp = BRIDGE.api.api(
            "POST", f"/tensor/missions/{BRIDGE.mission_id}/complete",
            {
                "claim_token": BRIDGE.claim_token,
                "status": "succeeded",
                "acceptance": True,
                "branch": f"agent/m{BRIDGE.mission_id}",
                "head_commit": head,
                "summary": {"headline": action.headline, "detail": action.summary},
                "checks": {"passed": action.checks_passed, "total": action.checks_total},
            })
    ok = code == 200
    BRIDGE.events.emit("note", {"subtype": "request_acceptance",
                                 "http": code, "response": str(resp)[:400]})
    if ok:
        BRIDGE.acceptance_requested = True
        BRIDGE.acceptance_response = resp
        text = (f"验收已提请（approval_id={resp.get('approval_id')}）。"
                "任务进入 awaiting_acceptance，等待 owner 人工裁决。"
                "请立即结束对话：回复一句总结即可，不要再调用任何工具。")
    else:
        text = f"提请验收失败（HTTP {code}）：{str(resp)[:300]}。请修正后重试。"
    return RequestAcceptanceObservation(ok=ok, message=text)


class _FixedTool(ToolDefinition):
    """One ToolDefinition subclass per bridge tool; executor closes over the
    module-global BRIDGE (single-mission daemon)."""

    @classmethod
    def _executor(cls, action):  # noqa: ARG003
        raise NotImplementedError

    @classmethod
    def create(cls, conv_state, **params) -> Sequence[ToolDefinition]:  # noqa: ARG003
        return [cls(description=cls._description, action_type=cls._action_type,
                    observation_type=cls._observation_type,
                    executor=cls._executor)]  # type: ignore[arg-type]


class _FnExecutor(ToolExecutor):
    """Adapts a plain fn(action) into the ToolExecutor protocol."""

    def __init__(self, fn) -> None:
        self._fn = fn

    def __call__(self, action, conversation=None):  # noqa: ARG002
        return self._fn(action)


def _fixed(name: str, description: str, action_type, observation_type, fn):
    executor = _FnExecutor(fn)

    def create(cls, conv_state, **params):  # noqa: ARG001, N805
        return [cls(description=description, action_type=action_type,
                    observation_type=observation_type, executor=executor)]
    klass = type(name, (_FixedTool,), {
        "create": classmethod(create),
        "__doc__": description,
    })
    return klass


CommitTool = _fixed(
    "CommitTool",
    "Commit ALL changes in the mission worktree (git add -A + commit). "
    "Push is disabled; the human reviewer creates the PR.",
    CommitAction, CommitObservation, do_commit)
RunTestsTool = _fixed(
    "RunTestsTool",
    "Run the repository test suite (default: repo venv pytest, GPU 5 allowed). "
    "Pass command= to override with cwd=worktree.",
    RunTestsAction, RunTestsObservation, do_run_tests)
WebSearchTool = _fixed(
    "WebSearchTool",
    "Search GitHub repositories and arXiv papers for benchmarks, references "
    "and prior art.",
    WebSearchAction, WebSearchObservation, do_web_search)
FetchUrlTool = _fixed(
    "FetchUrlTool",
    "Fetch a URL (allowlisted: github/raw.githubusercontent/arxiv/pypi/docs.openhands.dev).",
    FetchUrlAction, FetchUrlObservation, do_fetch_url)
RequestAcceptanceTool = _fixed(
    "RequestAcceptanceTool",
    "Request human acceptance of the mission. Call ONCE when development is "
    "done, tests pass and changes are committed. The mission then awaits the "
    "owner's decision.",
    RequestAcceptanceAction, RequestAcceptanceObservation, do_request_acceptance)


class MissionToolSet(ToolDefinition[Action, Observation]):
    """Terminal + bridge tools sharing one sanitized terminal executor."""

    @classmethod
    def create(cls, conv_state, **params) -> Sequence[ToolDefinition]:
        working_dir = conv_state.workspace.working_dir
        terminal = TerminalExecutor(working_dir=working_dir,
                                    terminal_type="subprocess",
                                    no_change_timeout_seconds=600)
        tools: list[ToolDefinition] = [
            TerminalTool.create(conv_state, executor=terminal)[0],
        ]
        for tool_cls in (CommitTool, RunTestsTool, WebSearchTool,
                         FetchUrlTool, RequestAcceptanceTool):
            tools.extend(tool_cls.create(conv_state))
        return tools


register_tool(MissionToolSet.name, MissionToolSet)


# ───────────────────────── mission execution ─────────────────────────

GOAL_TEMPLATE = """你是 tensor-agent，科研平台的自主开发智能体。本次任务（mission m{mid}）：

{goal}

仓库：{repo}（当前工作区即该仓库的 agent/m{mid} 分支检出，基于 {base_ref}）。
预算：最多约 {tool_budget} 次工具调用、{wall_minutes} 分钟墙钟。

工作规则：
1. 只在当前工作区内开发；git push 已被禁用，永远不要尝试推送。
2. 可用工具：terminal（bash）、str_replace_editor（文件编辑）、web_search（GitHub+arXiv 检索）、
   fetch_url（白名单域抓取）、commit（提交全部变更）、run_tests（跑测试套件）、
   request_acceptance（提请人工验收）。
3. 流程：先检索/理解 → 开发 → run_tests 通过 → commit（中文提交信息）→ request_acceptance。
   测试不过就修，修不过就在 request_acceptance 的 summary 里如实说明。
4. 严禁在未调用 request_acceptance 的情况下结束对话——提前收尾任务将被判 failed。
5. request_acceptance 成功后立即结束：回一句总结，不再调用任何工具。
6. 全程用简体中文写提交信息与验收材料；代码注释跟随仓库现有风格。"""


def build_llm() -> LLM:
    # 90s fail-fast retries (proven on this relay): long-reasoning spirals
    # are killed early and each retry samples fresh reasoning instead of
    # burning 3min+ per 180s attempt.
    return LLM(
        usage_id="tensor-agent",
        model=os.environ.get("LLM_MODEL", "openai/deepseek-v4-flash"),
        base_url=os.environ.get("LLM_BASE_URL", ""),
        api_key=os.environ.get("LLM_API_KEY", ""),
        timeout=90,
        num_retries=6,
        max_tokens=8192,
    )


class MissionRunner:
    def __init__(self, api: PlatformAPI, claim: dict) -> None:
        self.api = api
        self.mission: dict = claim["mission"]
        self.claim_token: str = self.mission["claim_token"]
        self.session_id: int = claim["session_id"]
        self.budget: dict = self.mission.get("budget") or {}
        self.max_wall = int(self.budget.get("max_wall_seconds", 7200))
        self.max_tools = int(self.budget.get("max_tool_calls", 80))
        self.t0 = time.monotonic()
        self.stop_reason: str | None = None
        self.finished = False  # set once terminal bookkeeping is done
        self.bridge: EventBridge | None = None

    # ── heartbeat + budget thread ──
    def watch(self) -> None:
        assert self.bridge is not None
        while True:
            time.sleep(HEARTBEAT_SECONDS)
            elapsed = int(time.monotonic() - self.t0)
            progress = {"tool_calls": self.bridge.action_count,
                        "elapsed_seconds": elapsed,
                        "max_wall_seconds": self.max_wall,
                        "head": worktree_head(BRIDGE.worktree) if BRIDGE else None}
            code, resp = self.api.api(
                "POST", f"/tensor/missions/{self.mission['id']}/heartbeat",
                {"claim_token": self.claim_token, "progress": progress})
            if code in (401, 403, 404, 409):
                return  # mission closed (acceptance/decide/terminal): stop beating
            if code == 200 and resp.get("action") == "stop":
                self.stop_reason = "owner_stopped"
                self._pause()
                return
            if elapsed > self.max_wall:
                self.stop_reason = "budget_exceeded"
                self._pause()
                return
            if self.bridge.action_count > self.max_tools + 10:
                self.stop_reason = "budget_exceeded"
                self._pause()
                return

    def _pause(self) -> None:
        try:
            if BRIDGE and BRIDGE.conversation is not None:
                BRIDGE.conversation.pause()
        except Exception as exc:  # noqa: BLE001
            log(f"pause failed: {exc}")
        # Watchdog: pause() cannot interrupt an in-flight LLM call that has
        # outlived its timeout discipline (slow-drip reads). If terminal
        # bookkeeping hasn't finished within 150s, exit the process — the
        # flock wrapper restarts it and the claim reaper marks this mission
        # failed/agent_lost.
        def _escalate() -> None:
            time.sleep(150)
            if not self.finished:
                log(f"m{self.mission['id']}: watchdog — run() wedged past stop "
                    f"({self.stop_reason}); exiting for flock restart")
                os._exit(3)
        threading.Thread(target=_escalate, daemon=True).start()

    def run(self) -> None:
        global BRIDGE
        mid = self.mission["id"]
        worktree = prep_worktree(self.mission)
        log(f"m{mid}: worktree {worktree} @ {worktree_head(worktree)}")
        self.api.api("POST", f"/tensor/missions/{mid}/start",
                     {"claim_token": self.claim_token})

        self.bridge = EventBridge(self.api, self.session_id)
        BRIDGE = BridgeState(self.api, self.mission, self.claim_token,
                             worktree, self.bridge)

        watcher = threading.Thread(target=self.watch, daemon=True)
        watcher.start()

        state_dir = os.path.join(STATE_ROOT, f"m{mid}", "state")
        os.makedirs(state_dir, exist_ok=True)
        agent = Agent(llm=build_llm(),
                      tools=[Tool(name=FileEditorTool.name),
                             Tool(name=MissionToolSet.name)])
        conversation = Conversation(
            agent=agent,
            workspace=worktree,
            callbacks=[lambda ev: sdk_event_to_platform(ev, self.bridge)],
            persistence_dir=state_dir,
            visualizer=None,
            max_iteration_per_run=self.max_tools + 10,
        )
        BRIDGE.conversation = conversation
        goal = GOAL_TEMPLATE.format(
            mid=mid, goal=self.mission["goal"], repo=self.mission["repo"],
            base_ref=self.mission.get("base_ref") or "main",
            tool_budget=self.max_tools, wall_minutes=self.max_wall // 60)
        conversation.send_message(goal)
        log(f"m{mid}: conversation running (budget {self.max_tools} tools / "
            f"{self.max_wall}s)")
        error: str | None = None
        # The LLM can end a run with a plain reply (notably after endpoint
        # timeouts) before calling run_tests/commit/request_acceptance. Resume
        # with an explicit nudge — the mission only counts as finished when
        # acceptance was requested, the owner stopped it, or budget ran out.
        nudges_left = 2       # plain-reply endings: re-enter with an instruction
        abort_resumes_left = 4  # endpoint-timeout aborts: just re-run(), free
        while True:
            error = None
            try:
                conversation.run()
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {exc}"
                # Endpoint-timeout aborts (litellm exhausting its retries)
                # are transient: re-run() resumes from the persisted
                # conversation without growing the context.
                if abort_resumes_left > 0 and not self.stop_reason:
                    abort_resumes_left -= 1
                    log(f"m{mid}: run aborted ({error[:200]}) — "
                        f"resume {4 - abort_resumes_left}/4 in 10s")
                    self.bridge.emit("error", {"text": error[:400]})
                    time.sleep(10)
                    continue
                log(f"m{mid}: conversation crashed: {error}")
            if BRIDGE.acceptance_requested or error or self.stop_reason:
                break
            if int(time.monotonic() - self.t0) > self.max_wall:
                self.stop_reason = "budget_exceeded"
                break
            if nudges_left <= 0:
                break
            nudges_left -= 1
            log(f"m{mid}: run ended without acceptance — nudge "
                f"{2 - nudges_left}/2, actions so far {self.bridge.action_count}")
            self.bridge.emit("note", {"subtype": "nudge",
                                      "text": "任务尚未完成：重新进入循环继续执行。"})
            try:
                conversation.send_message(
                    "你还没有完成任务。请继续：用 run_tests 跑测试 → commit 提交"
                    "（中文提交信息）→ request_acceptance 提请验收。在 request_acceptance"
                    " 成功之前不允许结束对话；若确实无法完成，也要先尽力调用工具再说明障碍。")
            except Exception as exc:  # noqa: BLE001
                log(f"m{mid}: nudge send failed: {exc}")
                break
        time.sleep(3)  # let the event queue drain
        self.bridge.close()

        # ── terminal bookkeeping ──
        head = worktree_head(worktree)
        self.finished = True  # acceptance path: watch thread already exited
        if BRIDGE.acceptance_requested:
            log(f"m{mid}: awaiting acceptance (approval "
                f"{BRIDGE.acceptance_response.get('approval_id')})")
            return
        status, stop = "failed", None
        if self.stop_reason == "owner_stopped":
            stop = "owner_stopped"          # server already terminal; ignore 409
        elif self.stop_reason == "budget_exceeded":
            stop = "budget_exceeded"
        elif error:
            stop = "agent_crash"
        else:
            stop = "finished_without_acceptance"
        code, resp = self.api.api(
            "POST", f"/tensor/missions/{mid}/complete",
            {"claim_token": self.claim_token, "status": status,
             "acceptance": False, "branch": f"agent/m{mid}", "head_commit": head,
             "summary": {"headline": stop,
                         "detail": f"tool_calls={self.bridge.action_count}"},
             "checks": {}})
        self.finished = True
        log(f"m{mid}: complete -> {code} {str(resp)[:160]} (stop={stop})")


def main() -> None:
    api = PlatformAPI()
    code, _ = api.api("GET", "/health")
    if code != 200:
        log("platform not reachable — exiting")
        sys.exit(1)
    log(f"tensor-agent up ({AGENT_ID}), polling for missions")
    backoff = 0
    while True:
        try:
            code, claim = api.api("POST", "/tensor/missions/claim",
                                  {"agent_id": AGENT_ID,
                                   "hostname": socket.gethostname(),
                                   "version": "1.0.0"})
            if code != 200:
                log(f"claim error HTTP {code}: {str(claim)[:200]}")
                time.sleep(60)
                continue
            if not claim.get("claimed"):
                for rid in claim.get("reaped", []):
                    log(f"reaped stale mission {rid}")
                backoff = min(backoff + 1, 5)
                time.sleep(CLAIM_POLL_SECONDS)
                continue
            backoff = 0
            log(f"claimed mission {claim['mission']['id']}: "
                f"{claim['mission']['goal'][:80]}")
            MissionRunner(api, claim).run()
        except KeyboardInterrupt:
            log("bye")
            return
        except Exception as exc:  # noqa: BLE001
            log(f"main loop error: {type(exc).__name__}: {exc}")
            time.sleep(30)


if __name__ == "__main__":
    main()
