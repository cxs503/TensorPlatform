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
import shutil
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
            batch = [item]
            closing = False
            # coalesce whatever is already queued — tool_call/tool_result
            # pairs land back-to-back, so one batched POST covers them all
            while len(batch) < 8:
                try:
                    nxt = self.q.get_nowait()
                except queue.Empty:
                    break
                if nxt is None:
                    closing = True
                    break
                batch.append(nxt)
            self._post(batch)
            if closing:
                return

    def _post(self, batch: list[tuple[str, dict]]) -> None:
        try:
            code, resp = self.api.api(
                "POST", f"/tensor/sessions/{self.session_id}/events/batch",
                {"events": [{"kind": kind, "payload": payload}
                            for kind, payload in batch]})
            if code == 404:  # backend without the batch endpoint: per-event posts
                for kind, payload in batch:
                    code, resp = self.api.api(
                        "POST", f"/tensor/sessions/{self.session_id}/events",
                        {"kind": kind, "payload": payload})
                    if code != 200:
                        log(f"event bridge degraded ({kind} -> {code}): {str(resp)[:120]}")
                return
            if code != 200:
                log(f"event bridge degraded (batch of {len(batch)} -> {code}): "
                    f"{str(resp)[:120]}")
        except Exception as exc:  # noqa: BLE001
            log(f"event bridge error (batch of {len(batch)}): {exc}")

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


def _add_worktree(repo_path: str, wt: str, mid: int, base_ref: str) -> None:
    """One jailed checkout of repo_path at wt, branch agent/m<mid>."""
    os.makedirs(os.path.dirname(wt), exist_ok=True)
    if os.path.isdir(os.path.join(wt, ".git")):
        return
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


def prep_worktree(mission: dict) -> str:
    repo_name, repo_path = resolve_repo(mission["repo"])
    mid = mission["id"]
    base_ref = mission.get("base_ref") or "main"
    wt = os.path.join(STATE_ROOT, f"m{mid}", repo_name)
    _add_worktree(repo_path, wt, mid, base_ref)
    return wt


def prep_co_worktrees(mission: dict) -> dict[str, str]:
    """Cross-project composition: sibling checkouts for co_repos, all under
    the mission directory (m<id>/<repo>) so one workspace spans the coupled
    solvers. Co-repos always ride origin/main; per-repo refs can be added
    when a ladder needs them."""
    out: dict[str, str] = {}
    mid = mission["id"]
    for name in (mission.get("co_repos") or []):
        repo_name, repo_path = resolve_repo(name)
        wt = os.path.join(STATE_ROOT, f"m{mid}", repo_name)
        _add_worktree(repo_path, wt, mid, "main")
        out[repo_name] = wt
    return out


def worktree_head(wt: str) -> str:
    rc, out = run(["git", "-C", wt, "rev-parse", "--short", "HEAD"])
    return out.strip() if rc == 0 else "unknown"


CACHE_DIR_NAMES = (".venv", "venv", ".pytest_cache", "__pycache__",
                   ".mypy_cache", ".ruff_cache", "node_modules", ".torch")


def reclaim_mission_space(mid: int) -> None:
    """Drop the heavyweight residue from a finished mission's directory
    (venvs, caches) while keeping the git checkout for PR review.
    Audit 2026-10-18: per-mission footprint ~1G of which .venv 926M —
    this reclaims ~90% immediately at mission end; tools/agent/gc.py
    reclaims the worktree itself after the retention window."""
    base = os.path.join(STATE_ROOT, f"m{mid}")
    removed = 0
    for root, dirs, _files in os.walk(base, topdown=True):
        for d in list(dirs):
            if d in CACHE_DIR_NAMES:
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
                removed += 1
                dirs.remove(d)
    if removed:
        log(f"m{mid}: reclaimed {removed} venv/cache dirs")


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


class DispatchSubMissionAction(Action):
    goal: str = Field(description="Sub-mission goal (Chinese, self-contained, "
                                  "with machine-checkable acceptance criteria)")
    repo: str = Field(description="Primary repo short name, e.g. TensorDEM")
    role: str | None = Field(default=None,
                             description="Role name resolved via roles.json "
                                         "(architect/implementer/verifier/documenter)")
    model: str | None = Field(default=None,
                              description="Explicit relay model id (mutually exclusive with role)")
    base_ref: str = Field(default="main")
    co_repos: list[str] | None = Field(default=None,
                                       description="Extra repos the sub-mission worktree set covers")
    max_tool_calls: int | None = Field(default=None)
    max_wall_seconds: int | None = Field(default=None)


class DispatchSubMissionObservation(Observation):
    ok: bool = False
    message: str = ""

    @property
    def to_llm_content(self) -> Sequence[Any]:
        return [TextContent(text=self.message)]


class AwaitChildrenAction(Action):
    timeout_minutes: int = Field(default=25,
                                 description="Max wall time to wait for children (bounded)")
    poll_seconds: int = Field(default=30)


class AwaitChildrenObservation(Observation):
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


# ── orchestration bridge (P2 cross-project dispatch) ──

# must mirror api/tensor_missions.py DISPATCH_MAX_CHILDREN / caps
DISPATCH_MAX_CHILDREN = 8
CHILD_TERMINAL = ("succeeded", "failed", "stopped", "awaiting_acceptance")


def load_role_map() -> dict[str, str]:
    """roles.json (owner-editable, next to this script) maps role → relay
    model id. Re-read on every dispatch so edits apply without a restart."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roles.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return {str(k): str(v) for k, v in data.items()
                if not str(k).startswith("_")}
    except (OSError, ValueError):
        return {}


def do_dispatch_sub_mission(action: DispatchSubMissionAction) -> DispatchSubMissionObservation:
    assert BRIDGE is not None
    BRIDGE.tool_calls += 1
    model, role = action.model, action.role
    if model and role:
        return DispatchSubMissionObservation(
            ok=False, message="role 与 model 二选一，不要同时给出。")
    if not model and role:
        roles = load_role_map()
        model = roles.get(role)
        if not model:
            return DispatchSubMissionObservation(
                ok=False, message=f"角色 {role!r} 未定义（roles.json 现有角色："
                                  f"{sorted(roles) or '无——文件缺失'}）。请用 model= 显式指定。")
    if not model:
        return DispatchSubMissionObservation(
            ok=False, message="必须给出 role=（经 roles.json 解析）或 model= 显式模型。")
    budget: dict[str, int] = {}
    if action.max_tool_calls:
        budget["max_tool_calls"] = action.max_tool_calls
    if action.max_wall_seconds:
        budget["max_wall_seconds"] = action.max_wall_seconds
    code, resp = BRIDGE.api.api(
        "POST", "/tensor/missions/dispatch",
        {"parent_id": BRIDGE.mission_id, "claim_token": BRIDGE.claim_token,
         "goal": action.goal, "repo": action.repo, "base_ref": action.base_ref,
         "co_repos": action.co_repos, "role": role, "model": model,
         "budget": budget})
    ok = code == 200
    if ok:
        text = (f"子任务已入队：m{resp.get('id')}（model={model}，role={role or '-'}，"
                f"预算 {resp.get('budget')}）。它会由绑定该模型的常驻守护认领执行；"
                f"用 await_children 等待其终态。")
    else:
        text = f"派发失败（HTTP {code}）：{str(resp)[:300]}"
    BRIDGE.events.emit("note", {"subtype": "dispatch_sub_mission", "http": code,
                                "role": role, "model": model,
                                "child_id": resp.get("id") if ok else None,
                                "goal": action.goal[:200]})
    return DispatchSubMissionObservation(ok=ok, message=text)


def _children_snapshot() -> tuple[int, list[dict], str]:
    assert BRIDGE is not None
    code, resp = BRIDGE.api.api(
        "GET", f"/tensor/missions/{BRIDGE.mission_id}/children")
    if code != 200:
        return code, [], str(resp)[:300]
    return code, list(resp.get("items", [])), ""


def do_await_children(action: AwaitChildrenAction) -> AwaitChildrenObservation:
    assert BRIDGE is not None
    BRIDGE.tool_calls += 1
    deadline = time.monotonic() + max(1, min(action.timeout_minutes, 60)) * 60
    poll = max(10, min(action.poll_seconds, 120))
    last: list[dict] = []
    while True:
        code, items, err = _children_snapshot()
        if code != 200:
            return AwaitChildrenObservation(ok=False,
                                            message=f"children 查询失败（HTTP {code}）：{err}")
        last = items
        pending = [it for it in items if it["status"] not in CHILD_TERMINAL]
        if not pending:
            summary = "; ".join(
                f"m{it['id']}[{it['status']}] {it['goal'][:60]}" for it in items) or "无子任务"
            BRIDGE.events.emit("note", {"subtype": "await_children_done",
                                        "children": [{"id": it["id"],
                                                      "status": it["status"]} for it in items]})
            return AwaitChildrenObservation(
                ok=True,
                message=f"全部子任务已到终态：{summary}。请基于结果继续编排或汇总提请验收。")
        if time.monotonic() >= deadline:
            summary = "; ".join(f"m{it['id']}[{it['status']}]" for it in items)
            return AwaitChildrenObservation(
                ok=False,
                message=f"等待超时（{action.timeout_minutes} 分钟），仍有未终态子任务：{summary}。"
                        "可再次调用 await_children 继续等待，或检查子任务详情。")
        time.sleep(poll)


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
DispatchSubMissionTool = _fixed(
    "DispatchSubMissionTool",
    "Orchestrator only: queue a sub-mission for another resident model "
    "(role resolves via roles.json). Envelope (repo whitelist, budget caps, "
    "tree depth) is enforced server-side.",
    DispatchSubMissionAction, DispatchSubMissionObservation, do_dispatch_sub_mission)
AwaitChildrenTool = _fixed(
    "AwaitChildrenTool",
    "Orchestrator only: poll sub-missions until every child reaches a "
    "terminal status (succeeded/failed/stopped/awaiting_acceptance) or the "
    "bounded timeout expires. Heartbeats continue during the wait.",
    AwaitChildrenAction, AwaitChildrenObservation, do_await_children)


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
                         FetchUrlTool, RequestAcceptanceTool,
                         DispatchSubMissionTool, AwaitChildrenTool):
            tools.extend(tool_cls.create(conv_state))
        return tools


register_tool(MissionToolSet.name, MissionToolSet)


# ───────────────────────── mission execution ─────────────────────────

GOAL_TEMPLATE = """你是 tensor-agent，科研平台的自主开发智能体。本次任务（mission m{mid}）：

{goal}

仓库：{repo}（{repo_layout}）。
{corepos}预算：最多约 {tool_budget} 次工具调用、{wall_minutes} 分钟墙钟。

工作规则：
1. 只在当前工作区内开发；git push 已被禁用，永远不要尝试推送。
2. 可用工具：terminal（bash）、str_replace_editor（文件编辑）、web_search（GitHub+arXiv 检索）、
   fetch_url（白名单域抓取）、commit（提交全部变更）、run_tests（跑测试套件）、
   request_acceptance（提请人工验收）{orchestration_tools}。
3. 流程：先检索/理解 → 开发 → run_tests 通过 → commit（中文提交信息）→ request_acceptance。
   测试不过就修，修不过就在 request_acceptance 的 summary 里如实说明。
4. 严禁在未调用 request_acceptance 的情况下结束对话——提前收尾任务将被判 failed。
5. request_acceptance 成功后立即结束：回一句总结，不再调用任何工具。
6. 全程用简体中文写提交信息与验收材料；代码注释跟随仓库现有风格。{orchestration}"""

COREPOS_BLOCK = """协同仓库（跨项目组合）：{corepos_list}
  每个协同仓库同样检出 agent/m{mid} 分支（基于 main）。commit 工具只作用于主仓库；
  协同仓库的提交用 terminal 里 git -C <目录> 完成。你的工作区根目录同时包含上述所有仓库。
"""

ORCHESTRATION_TOOLS = "、dispatch_sub_mission（编排者派发子任务）、await_children（等待子任务终态）"

ORCHESTRATION_BLOCK = """
编排规则（仅当本任务要求你编排子任务时使用，普通开发任务忽略）：
- dispatch_sub_mission(goal, repo, role=..., budget=...) 把一个子任务入队，交给绑定该角色的
  常驻模型守护执行；子任务完成后走同样的 owner 验收链。角色见 roles.json（architect/
  implementer/verifier/documenter），也可用 model= 显式指定模型。
- await_children(timeout_minutes=...) 轮询等待全部子任务到达终态（succeeded/failed/stopped/
  awaiting_acceptance 都算终态），返回每个子任务的 id/状态/分支。等待期间心跳自动续命。
- 子任务最多 {max_children} 个/编排任务，树深 2 层封顶（子任务不能再派发），服务端硬门。
- 收尾仍必须 request_acceptance：汇总各子任务结果与证据，由 owner 终审。"""


# relay deployments whose reasoning_effort accept-set is narrower than the
# SDK default "high"; module-level so native_harness shares one source
REASONING_EFFORT_OVERRIDES = {"Qwen3.8-27B": "medium"}


def build_llm(model: str | None = None) -> LLM:
    # mission rows carry the bare relay model id; litellm needs the
    # openai/ provider prefix to route to our relay endpoint
    model_id = model or os.environ.get("LLM_MODEL", "openai/deepseek-v4-flash")
    if "/" not in model_id:
        model_id = f"openai/{model_id}"
    # Fail-fast retries tuned on this relay: 90s proved too tight for
    # legitimate agentic planning reasoning (deterministic abort chains on
    # complex contexts), 180s let spirals burn 9min/call. 120s with 4
    # retries balances both; override per deployment via LLM_TIMEOUT_SECONDS.
    # stream=True is REQUIRED: this relay hangs indefinitely on big-context
    # NON-streaming completions (~20k+ prompt tokens, any model) while the
    # same requests streamed return in seconds — measured 2026-10-18.
    # Streaming additionally needs a non-None on_token callback, wired via
    # set_token_callbacks() after Conversation construction.
    # reasoning_effort is NOT covered by drop_params: the SDK defaults it to
    # "high" (LLM field default) and litellm treats it as valid for openai/
    # models so it is always sent — m32 proved drop_params insufficient.
    # The override value must sit in the INTERSECTION of the openai client's
    # Literal["low","medium","high"] (pydantic rejects "xhigh" locally) and
    # the relay deployment's accept-set (Qwen3.8-27B: xhigh/medium/low) —
    # m33 pinned that intersection to medium/low.
    effort = REASONING_EFFORT_OVERRIDES.get(model_id.split("/", 1)[-1])
    return LLM(
        usage_id="tensor-agent",
        model=model_id,
        base_url=os.environ.get("LLM_BASE_URL", ""),
        api_key=os.environ.get("LLM_API_KEY", ""),
        timeout=int(os.environ.get("LLM_TIMEOUT_SECONDS", "120")),
        num_retries=4,
        max_tokens=8192,
        stream=True,
        drop_params=True,
        **({"reasoning_effort": effort} if effort else {}),
    )


class MissionRunner:
    def __init__(self, api: PlatformAPI, claim: dict) -> None:
        self.api = api
        self.mission: dict = claim["mission"]
        self.model: str | None = self.mission.get("model")
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
            if self.stop_reason is None:
                if (elapsed > self.max_wall
                        or self.bridge.action_count > self.max_tools + 10):
                    self.stop_reason = "budget_exceeded"
                    self._pause()
                    # do NOT return here: run()'s terminal bookkeeping can
                    # take minutes (in-flight LLM call + pause + complete
                    # round trips) while the claim reaper strikes at 90s and
                    # marks agent_lost first — m19/m21 both died this way
                    # with a 409 on the final complete. Keep beating until
                    # run() sets self.finished.
            elif self.finished:
                return

    def _pause(self) -> None:
        try:
            if BRIDGE and BRIDGE.conversation is not None:
                BRIDGE.conversation.pause()
        except Exception as exc:  # noqa: BLE001
            log(f"pause failed: {exc}")
        # Watchdog: pause() cannot interrupt an in-flight LLM call that has
        # outlived its timeout discipline (slow-drip reads / retry chains).
        # If terminal bookkeeping hasn't finished in time, exit the process —
        # the flock wrapper restarts it and the claim reaper marks this
        # mission failed/agent_lost. The bound must EXCEED the worst-case
        # legitimate unwind: litellm runs timeout×(num_retries+1) before it
        # gives up (m34: 150s watchdog fired mid-retry-chain after budget
        # stop → honest budget_exceeded misfiled as agent_lost again).
        unwind = int(os.environ.get("LLM_TIMEOUT_SECONDS", "120")) * 5 + 60
        def _escalate() -> None:
            time.sleep(unwind)
            if not self.finished:
                log(f"m{self.mission['id']}: watchdog — run() wedged past stop "
                    f"({self.stop_reason}); exiting for flock restart")
                os._exit(3)
        threading.Thread(target=_escalate, daemon=True).start()

    def run(self) -> None:
        global BRIDGE
        mid = self.mission["id"]
        worktree = prep_worktree(self.mission)
        co_wts = prep_co_worktrees(self.mission)
        # multi-repo missions work from the mission root so one terminal
        # sees every coupled solver; single-repo missions keep cwd=worktree
        workspace = os.path.dirname(worktree) if co_wts else worktree
        log(f"m{mid}: worktree {worktree} @ {worktree_head(worktree)}"
            + (f" (+co {', '.join(co_wts)})" if co_wts else ""))
        self.api.api("POST", f"/tensor/missions/{mid}/start",
                     {"claim_token": self.claim_token})

        self.bridge = EventBridge(self.api, self.session_id)
        BRIDGE = BridgeState(self.api, self.mission, self.claim_token,
                             worktree, self.bridge)

        watcher = threading.Thread(target=self.watch, daemon=True)
        watcher.start()

        state_dir = os.path.join(STATE_ROOT, f"m{mid}", "state")
        os.makedirs(state_dir, exist_ok=True)
        runner_kind = self.mission.get("runner") or "openhands"
        if runner_kind == "native":
            # 自研 harness：litellm 直连 + 自有工具循环，零 OpenHands 运行时。
            # 惰性导入避免环（native_harness 反向 import 本模块拿桥工具）。
            from native_harness import NativeHarness
            conversation = NativeHarness(self, workspace, state_dir)
            BRIDGE.conversation = conversation
        else:
            agent = Agent(llm=build_llm(self.model),
                          tools=[Tool(name=FileEditorTool.name),
                                 Tool(name=MissionToolSet.name)])
            conversation = Conversation(
                agent=agent,
                workspace=workspace,
                callbacks=[lambda ev: sdk_event_to_platform(ev, self.bridge)],
                persistence_dir=state_dir,
                visualizer=None,
                max_iteration_per_run=self.max_tools + 10,
            )
            BRIDGE.conversation = conversation
            # Enable streaming end-to-end: the SDK degrades to a non-streaming
            # completion when on_token is None (LLM.stream=True alone is not
            # enough), and non-streaming big-context requests hang this relay.
            conversation.set_token_callbacks([lambda *_a, **_k: None])
        repo_name, _ = resolve_repo(self.mission["repo"])
        if co_wts:
            repo_layout = (f"主仓库检出在工作区 ./{repo_name}/ 子目录，agent/m{mid} 分支，"
                           f"基于 {self.mission.get('base_ref') or 'main'}")
            corepos = COREPOS_BLOCK.format(
                corepos_list="、".join(sorted(co_wts)), mid=mid)
        else:
            repo_layout = (f"当前工作区即该仓库的 agent/m{mid} 分支检出，"
                           f"基于 {self.mission.get('base_ref') or 'main'}")
            corepos = ""
        goal = GOAL_TEMPLATE.format(
            mid=mid, goal=self.mission["goal"], repo=self.mission["repo"],
            repo_layout=repo_layout, corepos=corepos,
            base_ref=self.mission.get("base_ref") or "main",
            tool_budget=self.max_tools, wall_minutes=self.max_wall // 60,
            orchestration_tools=ORCHESTRATION_TOOLS,
            orchestration=ORCHESTRATION_BLOCK.format(max_children=DISPATCH_MAX_CHILDREN))
        conversation.send_message(goal)
        log(f"m{mid}: conversation running (budget {self.max_tools} tools / "
            f"{self.max_wall}s, model={self.model or 'env-default'}, "
            f"runner={runner_kind})")
        error: str | None = None
        # The LLM can end a run with a plain reply (notably after endpoint
        # timeouts) before calling run_tests/commit/request_acceptance. Resume
        # with an explicit nudge — the mission only counts as finished when
        # acceptance was requested, the owner stopped it, or budget ran out.
        nudges_left = 2       # plain-reply endings: re-enter with an instruction
        abort_resumes_left = 6  # endpoint-timeout aborts: just re-run(), free
        consecutive_aborts = 0
        while True:
            error = None
            try:
                conversation.run()
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {exc}"
                # Endpoint-timeout aborts (litellm exhausting its retries)
                # are transient: re-run() resumes from the persisted
                # conversation without growing the context. Every second
                # consecutive abort also injects a short message — same
                # context re-sampled deterministically re-spirals, a small
                # context perturbation breaks the loop.
                if abort_resumes_left > 0 and not self.stop_reason:
                    abort_resumes_left -= 1
                    consecutive_aborts += 1
                    log(f"m{mid}: run aborted ({error[:200]}) — "
                        f"resume {6 - abort_resumes_left}/6 in 10s")
                    self.bridge.emit("error", {"text": error[:400]})
                    time.sleep(10)
                    if consecutive_aborts % 2 == 0:
                        try:
                            conversation.send_message(
                                "继续任务。若上一步在长时间思考，请直接给出下一步的具体工具调用，"
                                "不要重新推演全部方案。")
                        except Exception as exc2:  # noqa: BLE001
                            log(f"m{mid}: abort nudge send failed: {exc2}")
                    continue
                log(f"m{mid}: conversation crashed: {error}")
            if error is None:
                consecutive_aborts = 0
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
            reclaim_mission_space(mid)
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
        reclaim_mission_space(mid)


def main() -> None:
    # model pinning (P0 seven-daemon fleet): --model <id> claims only
    # missions queued for that model; --claim-null additionally picks up
    # legacy NULL-model missions (the designated default daemon)
    args = sys.argv[1:]
    pinned_model: str | None = None
    claim_null = False
    if "--model" in args:
        pinned_model = args[args.index("--model") + 1]
    if "--claim-null" in args:
        claim_null = True
    agent_id = AGENT_ID if not pinned_model else f"tensor-agent[{pinned_model}]@{socket.gethostname()}"
    api = PlatformAPI()
    code, _ = api.api("GET", "/health")
    if code != 200:
        log("platform not reachable — exiting")
        sys.exit(1)
    log(f"tensor-agent up ({agent_id}), polling for missions")
    backoff = 0
    while True:
        try:
            code, claim = api.api("POST", "/tensor/missions/claim",
                                  {"agent_id": agent_id,
                                   "hostname": socket.gethostname(),
                                   "version": "1.0.0",
                                   "model": pinned_model,
                                   "claim_null_model": claim_null})
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
    # native_harness 反向 import 本模块（拿桥工具实现）。把 __main__ 注册为
    # tensor_agent，避免模块被二次执行造成 BRIDGE 等全局态双实例。
    sys.modules.setdefault("tensor_agent", sys.modules[__name__])
    main()
