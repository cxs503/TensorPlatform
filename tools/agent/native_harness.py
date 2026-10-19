"""自研 agent harness（runner=native）——零 OpenHands 运行时的自主开发循环。

平台协议完全复用 tensor_agent.py（claim/heartbeat/complete/approvals、事件桥、
worktree 预备、桥工具实现）；本文件只替代 Conversation/Agent/LLM 那一层：
litellm 直连 + 自有工具 schema/执行器 + 自己的预算与中断纪律。

对比 OpenHands 路径（runner=openhands）的设计差异，全部来自实战死法：
  1. 流式逐 chunk 检查 stop 事件 → pause() 真正可中断在飞 LLM 调用
     （OpenHands pause() 打不断在飞调用，需 150s watchdog 兜底）。
  2. 预算检查内联在每次 LLM 调用与工具执行之间 → 停机即诚实终态。
  3. reasoning_effort 按模型显式覆盖（drop_params 不丢该参，m32/m33 教训）。
  4. 消息历史每步落盘（版本+校验和）→ abort 续跑从持久化文件恢复，不依赖
     SDK persistence；切换底座前后灵魂逐位可验。
  5. LLM 经 llm_gateway 端点池：额度耗尽换端点不换 Session（2026-10-19
     owner 架构原则：Session 是主体，上下文是灵魂，LLM 是可替换底座）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from typing import Any, Callable

import litellm

import llm_gateway
import tensor_agent as ta


class _Paused(Exception):
    """stop 事件在流式读取中被观测到——run() 立即返回。"""


class NativeHarness:
    """MissionRunner 的 native 会话实现：对外暴露 Conversation 同形接口
    （run / send_message / pause），MissionRunner 外层循环（nudge/abort 续跑/
    终态记账）原样复用。"""

    def __init__(self, runner: Any, workspace: str, state_dir: str) -> None:
        self.r = runner                    # MissionRunner（预算/stop_reason/bridge）
        self.workspace = workspace
        self.stop_event = threading.Event()
        self.state_path = os.path.join(state_dir, "native_messages.json")
        bare = self.r.model or os.environ.get("LLM_MODEL", "deepseek-v4-flash")
        bare = bare.split("/", 1)[-1]
        self.model = f"openai/{bare}"
        self.bare_model = bare
        self.messages: list[dict[str, Any]] = self._load_state()
        self.tools = _TOOL_SCHEMAS
        self.gateway = llm_gateway.Gateway()
        # 挂起看门狗：litellm 流式读会吞掉读超时（2026-10-19 13:06 实证：SSE
        # 连接 ESTAB 但永不出块，timeout=120 不抛异常，主循环冻死、心跳停跳
        # 15+ 分钟）。异常路径归 Gateway 分诊，无异常路径归这里：独立线程监视
        # 进度时间戳，非工具期停滞超限强退，交给 cron 重生+收割+refill 自愈。
        self._last_progress = time.monotonic()
        self._busy_tool = False
        self._conv_done = True     # 安全默认：run() 进入才武装（claim 失败
                                   # 弃用的 harness 不会成为定时炸弹）

        def _stall_watch() -> None:
            stall_s = float(os.environ.get("LLM_STALL_EXIT_SECONDS", "900"))
            while not self.stop_event.wait(30):
                if self.r.stop_reason is not None or self._conv_done:
                    return  # mission 终态/对话结束：看门狗退役，勿杀空闲守护
                idle = time.monotonic() - self._last_progress
                if not self._busy_tool and idle > stall_s:
                    ta.log(f"native stall watchdog: 非工具期无进度 {idle:.0f}s > "
                           f"{stall_s:.0f}s，强退待自愈（cron 重生+收割+refill）")
                    os._exit(137)

        threading.Thread(target=_stall_watch, daemon=True,
                         name="stall-watchdog").start()

    # ── Conversation 同形接口 ──

    def send_message(self, text: str) -> None:
        self._last_progress = time.monotonic()
        self.messages.append({"role": "user", "content": text})
        self._save_state()

    def pause(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        """推进到模型给出纯文本收尾（外层 nudge 接管）或 stop/预算触发。
        _Paused 在此消化——漏到 MissionRunner 会被当成端点 abort 走续跑。"""
        self._conv_done = False   # nudge/resume 续跑时重新武装看门狗
        try:
            self._run_loop()
        except _Paused:
            return
        except llm_gateway.GatewayUnavailable as exc:
            # 配置级故障按终态落地（同 budget_exceeded 通道），不进端点
            # abort 续跑环——否则冷却期会被反复重入空转
            ta.log(f"native: {exc}")
            self.r.stop_reason = "gateway_unavailable"
        finally:
            # 2026-10-19 m52 实证：干净收尾（纯文本→request_acceptance）不设
            # stop_reason，看门狗线程不退役、_last_progress 冻结，900s 后把
            # 守护连同下一个在飞 mission 一起杀掉（m51 遗留线程 15:45:48 杀
            # m52）。run() 返回即本对话结束，看门狗必须退役。
            self._conv_done = True

    def _run_loop(self) -> None:
        while True:
            self._gate()
            content, tool_calls, finish = self._llm()
            self.messages.append({
                "role": "assistant",
                "content": content or "",
                **({"tool_calls": tool_calls} if tool_calls else {}),
            })
            self._save_state()
            if not tool_calls:
                clean = _strip_think(content or "")
                if clean.strip():
                    self.r.bridge.emit("note", {"subtype": "assistant",
                                                "text": ta._clip(clean, 1500)})
                if finish == "length":
                    self.r.bridge.emit("note", {
                        "subtype": "note",
                        "text": "finish_reason=length：输出被 max_tokens 截断。"})
                return
            for tc in tool_calls:
                self._gate()
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                self.r.bridge.action_count += 1
                self.r.bridge.emit("tool_call", {"tool": name,
                                                 "args": ta._clip(args, 900)})
                self._busy_tool = True   # 工具子进程有自家超时，不参与停滞判定
                try:
                    result = self._exec(name, args)
                finally:
                    self._busy_tool = False
                    self._last_progress = time.monotonic()
                self.r.bridge.emit("tool_result", {"text": ta._clip(result, 3500)})
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": ta._clip(result, 3500),
                })
                self._save_state()
                if ta.BRIDGE and ta.BRIDGE.acceptance_requested:
                    return  # 验收已提请：协议要求立即停止一切工具调用

    # ── 预算与中断纪律 ──

    def _gate(self) -> None:
        if self.stop_event.is_set():
            raise _Paused
        elapsed = int(time.monotonic() - self.r.t0)
        if self.r.stop_reason is None and (
                elapsed > self.r.max_wall
                or self.r.bridge.action_count > self.r.max_tools + 10):
            self.r.stop_reason = "budget_exceeded"
        if self.r.stop_reason:
            raise _Paused

    # ── LLM 层：litellm 直连，流式，重试，可中断 ──

    def _llm(self) -> tuple[str, list[dict[str, Any]], str | None]:
        params: dict[str, Any] = {
            "model": self.model,
            "messages": self.messages,
            "tools": self.tools,
            "stream": True,           # 中继非流式大上下文会无限挂起
            "drop_params": True,
            "timeout": int(os.environ.get("LLM_TIMEOUT_SECONDS", "120")),
            "max_tokens": 8192,
        }
        effort = ta.REASONING_EFFORT_OVERRIDES.get(self.bare_model)
        if effort:
            params["reasoning_effort"] = effort
        last: Exception | None = None
        for attempt in range(6):
            # 每次尝试重新选端点：上一轮的 quota 冷却可能已把队列前移
            self._last_progress = time.monotonic()
            ep, cred = self.gateway.pick(self.bare_model)
            if ep is None:
                # 配置了端点池但全部不可用：显式失败，不静默退回 env
                # （env 多半就是冷却中端点自己的凭证，退回=绕过冷却）
                raise llm_gateway.GatewayUnavailable(
                    "llm gateway: 无可用端点（全部冷却/缺凭证/不服务 "
                    f"{self.bare_model}）——检查 llm_endpoints.json 与冷却状态")
            call = dict(params)
            call.update(cred)
            if ep is not None:
                actual = ep.serves(self.bare_model)
                if actual and actual != self.bare_model:
                    # 别名换底座：任务要的模型 → 该端点实际服役名
                    call["model"] = f"openai/{actual}"
            try:
                out = self._stream_completion(call)
                self.gateway.report_ok(ep)
                return out
            except _Paused:
                raise
            except llm_gateway.GatewayUnavailable:
                raise   # 配置级故障：重试无意义，交上层以 stop 理由落地
            except Exception as exc:  # noqa: BLE001  分类处置：换底座或退避
                last = exc
                cls = llm_gateway.classify_error(exc)
                action = self.gateway.report(ep, cls)
                ta.log(f"native llm attempt {attempt + 1}/6 failed "
                       f"（{cls} -> {action}）："
                       f"{type(exc).__name__}: {str(exc)[:160]}")
                wait_s = 0 if action == "rotate" else 5 + 5 * attempt
                if self.stop_event.wait(wait_s):
                    raise _Paused from None
        raise RuntimeError(f"llm exhausted retries: {last}") from last

    def _stream_completion(
            self, params: dict[str, Any]) -> tuple[str, list[dict[str, Any]], str | None]:
        response = litellm.completion(**params)
        content_parts: list[str] = []
        finish: str | None = None
        # tool_calls 的 JSON 参数按 id 跨 chunk 拼接
        pending: dict[int, dict[str, Any]] = {}
        for chunk in response:
            self._last_progress = time.monotonic()
            if self.stop_event.is_set():
                response.close()
                raise _Paused
            choices = getattr(chunk, "choices", None) or [{}]
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue
            piece = getattr(delta, "content", None)
            if piece:
                content_parts.append(piece)
            if getattr(delta, "tool_calls", None):
                for tc in delta.tool_calls:
                    # 个别中继不给 index：单一工具调用流下退化为槽 0
                    idx = tc.index if tc.index is not None else (min(pending) if pending else 0)
                    slot = pending.setdefault(idx, {"id": "", "name": "", "args": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["args"] += tc.function.arguments
            if getattr(choices[0], "finish_reason", None):
                finish = choices[0].finish_reason
        tool_calls = [
            {"id": slot["id"] or f"call_{i}", "type": "function",
             "function": {"name": slot["name"],
                          "arguments": slot["args"] or "{}"}}
            for i, slot in sorted(pending.items())
        ]
        return "".join(content_parts), tool_calls, finish

    # ── 工具执行：桥工具复用 tensor_agent 实现，bash/编辑器为本文件自有 ──

    def _exec(self, name: str, args: dict[str, Any]) -> str:
        try:
            fn = _TOOL_FUNCS.get(name)
            if fn is None:
                return f"未知工具 {name}；可用：{', '.join(_TOOL_FUNCS)}"
            return fn(self, args)
        except _Paused:
            raise
        except Exception as exc:  # noqa: BLE001  工具失败要回给模型而不是掀桌子
            return f"工具 {name} 执行失败：{type(exc).__name__}: {exc}"

    def _bash(self, args: dict[str, Any]) -> str:
        command = args.get("command", "")
        timeout = min(int(args.get("timeout", 120)), 600)
        try:
            proc = subprocess.run(
                ["bash", "-lc", command], cwd=self.workspace,
                capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return f"命令超时（>{timeout}s）被杀。"
        out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        return f"exit={proc.returncode}\n{out.strip() or '(无输出)'}"

    def _editor(self, args: dict[str, Any]) -> str:
        cmd = args.get("command", "view")
        path = os.path.join(self.workspace, args.get("path", ""))
        path = os.path.normpath(path)
        if not path.startswith(os.path.normpath(self.workspace)):
            return "拒绝：路径越出工作区。"
        if cmd == "view":
            if not os.path.isfile(path):
                return f"文件不存在：{args.get('path')}"
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            return "".join(f"{i + 1:6d}\t{line}" for i, line in enumerate(lines))
        if cmd == "create":
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(args.get("file_text", ""))
            return f"已创建 {args.get('path')}"
        if cmd == "str_replace":
            if not os.path.isfile(path):
                return f"文件不存在：{args.get('path')}"
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            old, new = args.get("old_str", ""), args.get("new_str", "")
            hits = text.count(old)
            if hits == 0:
                return "old_str 未命中。"
            if hits > 1:
                return f"old_str 命中 {hits} 处，必须唯一。"
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text.replace(old, new, 1))
            return "已替换。"
        return f"未知编辑命令 {cmd}（view/create/str_replace）。"

    # ── 持久化：每步落盘，abort 续跑恢复 ──

    def _save_state(self) -> None:
        body = json.dumps(self.messages, ensure_ascii=False)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": 1,
                       "checksum": hashlib.sha256(
                           body.encode("utf-8")).hexdigest(),
                       "messages": self.messages}, fh, ensure_ascii=False)
        os.replace(tmp, self.state_path)

    def _load_state(self) -> list[dict[str, Any]]:
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list) and data:      # v0 裸数组（历史文件）
                ta.log(f"native: resumed {len(data)} messages (v0 无校验)")
                return data
            if isinstance(data, dict) and data.get("messages"):
                body = json.dumps(data["messages"], ensure_ascii=False)
                if (data.get("checksum")
                        and data["checksum"] != hashlib.sha256(
                            body.encode("utf-8")).hexdigest()):
                    ta.log("native: state checksum 不符——弃用恢复，从零开始")
                    return []
                msgs = data["messages"]
                ta.log(f"native: resumed {len(msgs)} messages "
                       f"(v{data.get('version')}, checksum 一致)")
                return msgs
        except (OSError, json.JSONDecodeError):
            pass
        return []


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_think(text: str) -> str:
    """MiniMax 等把思考混进 content 的 <think> 标签：展示层剥掉，消息历史保原样。"""
    return _THINK_RE.sub("", text).strip()


# ── 工具 schema（litellm tools 参数）与实现分派 ──

def _mk(name: str, description: str, properties: dict[str, Any],
        required: list[str]) -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": required}}}


_BRIDGE_TOOLS: dict[str, tuple[type, Callable[[Any], Any], Callable[[Any], str]]] = {
    # name -> (tensor_agent Action 类, do_* 执行器, Observation -> 回给模型的文本)
    "commit": (ta.CommitAction, ta.do_commit,
               lambda o: f"committed {o.head}\n{o.stats}" if o.ok
               else f"commit failed: {o.error}"),
    "run_tests": (ta.RunTestsAction, ta.do_run_tests, lambda o: o.output),
    "web_search": (ta.WebSearchAction, ta.do_web_search, lambda o: o.results),
    "fetch_url": (ta.FetchUrlAction, ta.do_fetch_url,
                  lambda o: (o.body if o.ok else f"失败：{o.body}")),
    "request_acceptance": (ta.RequestAcceptanceAction, ta.do_request_acceptance,
                           lambda o: o.message),
    "dispatch_sub_mission": (ta.DispatchSubMissionAction, ta.do_dispatch_sub_mission,
                             lambda o: o.message),
    "await_children": (ta.AwaitChildrenAction, ta.do_await_children,
                       lambda o: o.message),
}


def _call_bridge(harness: NativeHarness, args: dict[str, Any], *,
                 name: str, fields: dict[str, str]) -> str:
    action_cls, do_fn, render = _BRIDGE_TOOLS[name]
    kwargs = {py: args.get(js) for js, py in fields.items() if args.get(js) is not None}
    return render(do_fn(action_cls(**kwargs)))


_TOOL_FUNCS: dict[str, Callable[[NativeHarness, dict[str, Any]], str]] = {
    # 名字与 GOAL_TEMPLATE 的工具清单严格一致，模型按提示词叫工具
    "terminal": NativeHarness._bash,
    "str_replace_editor": NativeHarness._editor,
    "commit": lambda h, a: _call_bridge(h, a, name="commit",
                                        fields={"message": "message"}),
    "run_tests": lambda h, a: _call_bridge(h, a, name="run_tests",
                                           fields={"command": "command"}),
    "web_search": lambda h, a: _call_bridge(h, a, name="web_search",
                                            fields={"query": "query"}),
    "fetch_url": lambda h, a: _call_bridge(h, a, name="fetch_url",
                                           fields={"url": "url"}),
    "request_acceptance": lambda h, a: _call_bridge(
        h, a, name="request_acceptance",
        fields={"headline": "headline", "summary": "summary",
                "checks_passed": "checks_passed", "checks_total": "checks_total",
                "evidence_files": "evidence_files"}),
    "dispatch_sub_mission": lambda h, a: _call_bridge(
        h, a, name="dispatch_sub_mission",
        fields={"goal": "goal", "repo": "repo", "role": "role", "model": "model",
                "base_ref": "base_ref", "co_repos": "co_repos",
                "max_tool_calls": "max_tool_calls",
                "max_wall_seconds": "max_wall_seconds"}),
    "await_children": lambda h, a: _call_bridge(
        h, a, name="await_children",
        fields={"timeout_minutes": "timeout_minutes", "poll_seconds": "poll_seconds"}),
}

_TOOL_SCHEMAS = [
    _mk("terminal", "在工作区执行 bash 命令（git/构建/临时脚本）。禁止 git push。",
        {"command": {"type": "string"}, "timeout": {"type": "integer"}},
        ["command"]),
    _mk("str_replace_editor",
        "文件编辑：command=view 看行号全文；create 新建（file_text 全量）；"
        "str_replace 唯一匹配替换（old_str 必须在文件中唯一）。",
        {"command": {"type": "string", "enum": ["view", "create", "str_replace"]},
         "path": {"type": "string"},
         "file_text": {"type": "string"},
         "old_str": {"type": "string"}, "new_str": {"type": "string"}},
        ["command", "path"]),
    _mk("commit", "提交主仓库全部变更（git add -A + commit，中文提交信息）。",
        {"message": {"type": "string"}}, ["message"]),
    _mk("run_tests", "跑仓库默认测试套件（或给定命令，cwd=worktree）。",
        {"command": {"type": "string"}}, []),
    _mk("web_search", "GitHub 仓库与 arXiv 论文检索。", {"query": {"type": "string"}},
        ["query"]),
    _mk("fetch_url", "抓取白名单域 URL 内容。",
        {"url": {"type": "string"}}, ["url"]),
    _mk("request_acceptance",
        "提请人工验收。有判据工件（validation_report.json 等）时必须把路径填进 "
        "evidence_files——平台直读工件生成机器摘录，审批器以工件数字为准。"
        "成功后立即结束对话，不再调用任何工具。",
        {"headline": {"type": "string"}, "summary": {"type": "string"},
         "checks_passed": {"type": "integer"}, "checks_total": {"type": "integer"},
         "evidence_files": {"type": "array", "items": {"type": "string"},
                            "description": "判据工件 JSON 路径（≤4 个）"}},
        ["headline", "summary", "checks_passed", "checks_total"]),
    _mk("dispatch_sub_mission",
        "编排者专用：派发子任务（role 经 roles.json 解析模型，与 model 互斥）。",
        {"goal": {"type": "string"}, "repo": {"type": "string"},
         "role": {"type": "string"}, "model": {"type": "string"},
         "base_ref": {"type": "string"}, "co_repos": {"type": "array", "items": {"type": "string"}},
         "max_tool_calls": {"type": "integer"}, "max_wall_seconds": {"type": "integer"}},
        ["goal", "repo"]),
    _mk("await_children",
        "编排者专用：轮询等待全部子任务终态（心跳自动续命）。",
        {"timeout_minutes": {"type": "integer"}, "poll_seconds": {"type": "integer"}},
        []),
]
