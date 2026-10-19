"""LLM Gateway：可切换计算底座（2026-10-19 owner 架构原则落地）。

Session 是科研主体，上下文是灵魂，LLM 是可替换的推理执行器。额度耗尽时
换执行器，而不是让 agent 失去研究过程与任务状态。本模块给 native_harness
提供端点池与错误分类轮转：

  quota    （429/配额/余额）  → 立即冷却当前端点，轮转下一家；
  transient（超时/网络/5xx）  → 退避重试，达阈值再轮转；
  fatal    （401/403/模型不存在）→ 标记坏死并告警，绝不无限重试。

配置 llm_endpoints.json（与 roles.json 同目录，owner 可编辑；密钥只写
env 变量名，不落明文）：
  {
    "endpoints": [
      {"name": "relay-a", "base_url_env": "LLM_BASE_URL",
       "api_key_env": "LLM_API_KEY",
       "models": ["qwen3.7-plus", "glm-5.2", "deepseek-v4-flash"],
       "aliases": {"qwen3.7-plus": "qwen3.5-plus"}}
    ],
    "cooldown_seconds": 1800,
    "transient_threshold": 3
  }
  aliases 是跨模型兜底（换底座不换 Session）：键=任务要的模型，值=该端点
  实际服役名。无该文件时退化为现状：单端点直接读 LLM_BASE_URL/LLM_API_KEY。

冷却状态跨进程落盘（daemon 重启不失忆）：
  /srv/tensor/shared/tensor_eco/agent_work/llm_gateway_state.json
纪律：模型路由只决定谁来推理；已作出的审批裁决不因换端点而重开。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

CONFIG_PATH = Path(os.environ.get(
    "LLM_ENDPOINTS_CONFIG",
    str(Path(__file__).resolve().parent / "llm_endpoints.json")))
STATE_PATH = Path(os.environ.get(
    "LLM_GATEWAY_STATE",
    "/srv/tensor/shared/tensor_eco/agent_work/llm_gateway_state.json"))

DEFAULT_COOLDOWN = 1800      # quota 冷却：30 分钟（额度一般不是秒级恢复）
DEFAULT_TRANSIENT_N = 3      # 连续 transient 达此数 → 视同端点不健康，轮转


class GatewayUnavailable(RuntimeError):
    """配置了端点池但全部不可用（冷却/缺凭证/不服务该模型）。

    必须显式失败而不是退回 env 默认：env 里多半就是冷却中端点自己的
    凭证，退回=绕过冷却；缺 env 则是无凭证请求（PR #22 初审第 1 点）。
    未配置端点池时 Gateway 用 env-default 合成端点，不会走到这里。"""



def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] gateway: {msg}", flush=True)


def _load_config() -> dict:
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _load_state() -> dict:
    try:
        st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return st if isinstance(st, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(st: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, STATE_PATH)
    except OSError:
        pass  # 状态丢了大不了冷却失忆，不阻断推理


class Endpoint:
    """一个可用的 API 底座。密钥在 pick() 时才从 env 解析（不缓存明文）。"""

    def __init__(self, spec: dict) -> None:
        self.name: str = spec.get("name", "unnamed")
        self.base_url_env: str = spec.get("base_url_env", "")
        self.api_key_env: str = spec.get("api_key_env", "")
        self.models: list[str] = list(spec.get("models") or [])
        self.aliases: dict[str, str] = dict(spec.get("aliases") or {})
        # base_url 允许直接写死（内网地址非密钥）；密钥必须走 env
        self.base_url: str = spec.get("base_url") or ""

    def resolve(self) -> dict:
        """返回 litellm 参数片段；env 缺失的端点解析为空（视为不可用）。"""
        base = self.base_url or os.environ.get(self.base_url_env, "")
        key = os.environ.get(self.api_key_env, "") if self.api_key_env else ""
        if not base or (self.api_key_env and not key):
            return {}
        out = {"base_url": base}
        if key:
            out["api_key"] = key
        return out

    def serves(self, model: str) -> str | None:
        """该端点能为 model 服务吗：原样服役或显式别名。返回实际模型名。"""
        if not self.models or model in self.models:
            return model
        return self.aliases.get(model)


class Gateway:
    def __init__(self) -> None:
        self.cfg = _load_config()
        self.cooldown = int(self.cfg.get("cooldown_seconds",
                                         DEFAULT_COOLDOWN))
        self.transient_n = int(self.cfg.get("transient_threshold",
                                            DEFAULT_TRANSIENT_N))
        self._transient_counts: dict[str, int] = {}
        if self.cfg.get("endpoints"):
            self.endpoints = [Endpoint(e) for e in self.cfg["endpoints"]]
        else:
            # 退化形态：无配置 = 现状单端点（LLM_BASE_URL/LLM_API_KEY）
            self.endpoints = [Endpoint({
                "name": "env-default",
                "base_url_env": "LLM_BASE_URL",
                "api_key_env": "LLM_API_KEY"})]

    # ── 端点健康状态（跨进程落盘） ──

    def _blocked(self, ep: Endpoint, now: float) -> str | None:
        st = _load_state().get(ep.name) or {}
        until = st.get("blocked_until") or 0
        if until > now:
            return st.get("reason", "cooldown")
        return None

    def _mark(self, ep: Endpoint, seconds: float, reason: str) -> None:
        st = _load_state()
        entry = st.get(ep.name) or {}
        entry.update({"blocked_until": time.time() + seconds,
                      "reason": reason, "marked_at": time.time()})
        st[ep.name] = entry
        _save_state(st)
        log(f"endpoint {ep.name} 冷却 {int(seconds)}s（{reason}）")

    # ── 选择与轮转 ──

    def pick(self, model: str) -> tuple[Endpoint | None, dict]:
        """选一个健康且能服务 model 的端点。返回 (端点, litellm 参数片段)。
        全军覆没时返回 (None, {})——调用方应 raise GatewayUnavailable 显式
        失败（勿退回 env：那是绕过冷却或无凭证请求）。"""
        now = time.time()
        for ep in self.endpoints:
            if self._blocked(ep, now):
                continue
            actual = ep.serves(model)
            if actual is None:
                continue
            params = ep.resolve()
            if not params:
                log(f"endpoint {ep.name} env 未解析（{ep.base_url_env}"
                    f"/{ep.api_key_env}），跳过")
                continue
            return ep, params
        return None, {}

    def report(self, ep: Endpoint | None, classification: str) -> str:
        """调用方把失败分类回报给 gateway。返回处置动作：
        rotate（换端点重试）/ retry（同端点退避重试）/ giveup。"""
        if ep is None:
            return "retry"          # env 默认形态：无处可换
        if classification == "quota":
            self._mark(ep, self.cooldown, "quota")
            return "rotate"
        if classification == "fatal":
            self._mark(ep, 86400, "fatal:凭据或模型不可用")  # 人工介入级
            return "rotate"
        # transient：计数达阈值才轮转
        n = self._transient_counts.get(ep.name, 0) + 1
        self._transient_counts[ep.name] = n
        if n >= self.transient_n:
            self._transient_counts[ep.name] = 0
            self._mark(ep, min(self.cooldown, 600), "transient×"
                       + str(n))
            return "rotate"
        return "retry"

    def report_ok(self, ep: Endpoint | None) -> None:
        if ep is not None:
            self._transient_counts[ep.name] = 0


_QUOTA_RE = ("quota", "insufficient", "balance", "rate limit", "ratelimit",
             "额度", "余额", "配额", "429", "too many requests")
_FATAL_RE = ("invalid api key", "unauthorized", "permission", "not found",
             "does not exist", "model_not_found", "无可用渠道",
             "invalid_request_error")


def classify_error(exc: Exception) -> str:
    """错误三分类。litellm 把 HTTP 状态包在异常字符串里，按文本+状态码判。"""
    text = str(exc).lower()
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None)
    if status == 429 or any(k in text for k in _QUOTA_RE):
        return "quota"
    if status in (401, 403) or any(k in text for k in _FATAL_RE):
        return "fatal"
    return "transient"   # 超时/网络/5xx/未知 → 保守按暂时
