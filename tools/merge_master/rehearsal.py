"""排练（rehearsal）：用**真实队列数据**跑完整执行链路，但把写动作换成记录式桩。

为什么需要它：真实队列里可能一张都到不了「全绿」，光看 plan 模式无法证明
「合并 → 回滚规格 → 任务回写 → 审计」这段执行链在真实数据上跑得通。排练把
``Merger``/``writeback`` 换成记录桩（不碰远端、不碰任务库），其余全是真的：
真 PR 详情、真检查结果、真 diff、真边界门、真预算、真审计链。

产物是「若判据全绿将会发生什么」的完整预演 —— 与真正执行的区别只有一行：
桩函数记录而不是发 PUT/ POST。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .model import MissionRecord


class RecordingMerger:
    """记录「本该发出的合并请求」，绝不触网。"""

    def __init__(self, method: str = "merge"):
        self.method = method
        self.calls: List[Dict[str, Any]] = []

    def merge(self, repo: str, pr: int, sha: str,
              method: str = "") -> Dict[str, Any]:
        m = method or self.method
        self.calls.append({"repo": repo, "pr": pr, "sha": sha, "method": m})
        return {"ok": True, "status": 200,
                "merge_commit": "rehearsal-" + (sha or "")[:12]}


class RecordingLedger:
    """包一层真实账本：读真实验收记录，写动作只记录（不改任务状态）。"""

    def __init__(self, inner, *, status_after: str = "succeeded"):
        self.inner = inner
        self.status_after = status_after
        self.writes: List[Dict[str, Any]] = []

    def find(self, repo: str, pr: int) -> Optional[MissionRecord]:
        return self.inner.find(repo, pr)

    def writeback(self, mission_id: int, state: str, repo: str, pr: int,
                  sha: Optional[str], note: str) -> Dict[str, Any]:
        self.writes.append({"mission_id": mission_id, "state": state,
                            "repo": repo, "pr": pr, "sha": sha,
                            "note": note})
        return {"ok": True, "status": 200,
                "mission_status": self.status_after if state == "merged"
                else "awaiting_pr_merge"}