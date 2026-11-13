"""dispatch —— 「提案即单子」的同步建单面（v2 核心改造①）。

铸造一条合规锚后**同步** ``POST {TENSOR_API_BASE}/tensor/missions`` 建单，而不是等
feeder/board-claim 下次扫描。语义差别很大：

- v1：锚进 ``pending_review`` → 人/planner 采认 → 进供给面 → 某次扫描建单（数天延迟，
  且常因无人采认而无限悬挂）；
- v2：锚 ``open`` + 单子立即可见（``origin='agent'``，``task_key=<slug>`` 保证幂等）。

幂等：``task_key = slug`` 让重复投递被 API 侧按 task_key 收敛；即使我们重试也不会
在同一 slug 上开出第二张单。失败不静默：铸造行落 ``dispatch_failed``，单子缺失可见，
``cli mint`` 可重放（``--retry-dispatch``）。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Protocol


class DispatchError(RuntimeError):
    """建单失败（HTTP 非 2xx / 返回体缺 id / 网络错误）。"""


class Dispatcher(Protocol):
    def create_mission(self, payload: Dict[str, Any]) -> int:  # pragma: no cover - 协议
        ...


def mission_payload(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """候选锚 → ``POST /tensor/missions`` 请求体。

    goal = 标题 + 验收判据（机器可裁的那半句必须进 goal，否则 worker 拿到单子也不知道
    什么算完成）；``task_key`` 用 slug，让同 slug 的重复投递在服务端收敛。
    """
    slug = str(candidate.get("slug") or "")
    title = str(candidate.get("title") or slug).strip()
    acceptance = str(candidate.get("acceptance") or "").strip()
    repo = str(candidate.get("repo") or "TensorPlatform").strip() or "TensorPlatform"
    goal = f"[{slug}] {title}"
    if acceptance:
        goal += f"\n\n验收判据（机检）：{acceptance}"
    goal += ("\n\n来源：agent 自主提案经需求入榜 v2 铸造（FEAT-DEMAND-AUTOINTAKE）；"
             "完成后 finish 的 capability_delta 必须引用本锚 slug。")
    return {
        "goal": goal[:12000],
        "repo": repo,
        "origin": "agent",
        "task_key": slug,
        "budget": {},
    }


class HttpDispatcher:
    """真实建单客户端（Bearer token）。"""

    def __init__(self, base_url: str = "", token: str = "", timeout: float = 30.0) -> None:
        self.base_url = (base_url or os.environ.get("TENSOR_API_BASE", "")
                         or "http://127.0.0.1:8010/api/v1").rstrip("/")
        self.token = token or os.environ.get("TENSOR_TOKEN", "")
        self.timeout = timeout

    def create_mission(self, payload: Dict[str, Any]) -> int:
        if not self.token:
            raise DispatchError("no bearer token (set TENSOR_TOKEN)")
        req = urllib.request.Request(
            f"{self.base_url}/tensor/missions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.token}"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            raise DispatchError(f"HTTP {exc.code}: {exc.read()[:300]!r}") from exc
        except Exception as exc:                       # 网络/解析
            raise DispatchError(f"{type(exc).__name__}: {str(exc)[:200]}") from exc
        mid = body.get("id") or body.get("mission_id")
        if not mid:
            raise DispatchError(f"response has no mission id: {str(body)[:200]}")
        return int(mid)


class RecordingDispatcher:
    """测试/红队用：只记录，不联网。返回递增的合成 id。"""

    def __init__(self, start_id: int = 900000) -> None:
        self.calls: list = []
        self._next = start_id

    def create_mission(self, payload: Dict[str, Any]) -> int:
        self.calls.append(payload)
        self._next += 1
        return self._next


class FailingDispatcher:
    """测试用：模拟建单失败（验证 dispatch_failed 不静默）。"""

    def __init__(self, error: str = "HTTP 503: upstream unavailable") -> None:
        self.error = error
        self.calls: list = []

    def create_mission(self, payload: Dict[str, Any]) -> int:
        self.calls.append(payload)
        raise DispatchError(self.error)