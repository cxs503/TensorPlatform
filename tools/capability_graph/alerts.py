"""自动预警：能力证据等级变化 → 自动重算级联 → 输出受影响清单（带延迟预算）。

设计约束
--------
* 预警必须是**变化驱动**的：调用方把等级变化交给 ``apply_level_change``，引擎在同一
  调用内完成「应用变化 → 重算级联 → 生成预警」，因此延迟可被端到端计量（而不是只
  计其中一段）。
* 预警落盘为 append-only JSONL，每行带 ``sha256`` 前 16 位指纹与延迟实测值，便于审计
  与回归比对；不回写、不覆盖历史。
* 延迟预算来自配置（``performance.alert_latency_budget_ms``），超预算的预警**照常输出
  但标记** ``within_budget=False`` —— 掩盖慢预警等于掩盖故障。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from .cascade import CascadeEvent, CascadeResult, simulate
from .model import Snapshot

__all__ = ["AlertEngine", "Alert"]


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


@dataclass
class Alert:
    alert_id: str
    capability_id: str
    from_level: str
    to_level: str
    severity: str
    affected: List[Dict[str, Any]]
    affected_count: int
    latency_ms: float
    budget_ms: float
    within_budget: bool
    created_at: str
    cascade: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "capability_id": self.capability_id,
            "from_level": self.from_level,
            "to_level": self.to_level,
            "severity": self.severity,
            "affected_count": self.affected_count,
            "affected": self.affected,
            "latency_ms": round(self.latency_ms, 4),
            "budget_ms": self.budget_ms,
            "within_budget": self.within_budget,
            "created_at": self.created_at,
            "cascade_summary": {
                "own_drop": self.cascade.get("own_drop"),
                "iterations": self.cascade.get("iterations"),
                "converged": self.cascade.get("converged"),
                "default_weight": (self.cascade.get("meta") or {}).get("default_weight"),
            },
        }

    def jsonl(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


class AlertEngine:
    """把等级变化事件变成预警。``clock`` 可注入，便于测试与稳态计量。"""

    def __init__(
        self,
        snapshot: Snapshot,
        cfg: Any,
        *,
        history_path: Optional[Path] = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.snapshot = snapshot
        self.cfg = cfg
        self._clock = clock
        self._history_path = history_path
        self._counter = 0
        self.alerts: List[Alert] = []

    # ── 事件入口 ─────────────────────────────────────────────
    def apply_level_change(
        self, capability_id: str, to_level: str, *, persist: bool = False
    ) -> Alert:
        """单个等级变化 → 级联重算 → 预警（同一调用内，延迟可端到端计量）。"""
        started = self._clock()
        event = CascadeEvent(capability_id=capability_id, to_level=to_level)
        result = simulate(self.snapshot, event, self.cfg)
        self._counter += 1
        budget = float((self.cfg.performance or {}).get("alert_latency_budget_ms", 1000.0))
        alert = self._build_alert(result, budget, started)
        self.alerts.append(alert)
        if persist and self._history_path is not None:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            with self._history_path.open("a", encoding="utf-8") as fh:
                fh.write(alert.jsonl() + "\n")
        return alert

    def apply_level_changes(
        self, changes: Sequence[Mapping[str, str]], *, persist: bool = False
    ) -> List[Alert]:
        return [
            self.apply_level_change(str(c["capability_id"]), str(c["to_level"]), persist=persist)
            for c in changes
        ]

    # ── 快照差分（另一条预警路径：从两次快照等级表比对）──
    def detect_changes(self, new_levels: Mapping[str, str]) -> List[Alert]:
        """旧快照 vs 新等级表 → 逐条变化预警（按变化项排序，确定性）。"""
        alerts: List[Alert] = []
        for node_id in sorted(new_levels):
            old = self.snapshot.nodes.get(node_id)
            if old is None or old.own_level == new_levels[node_id]:
                continue
            alerts.append(self.apply_level_change(node_id, new_levels[node_id]))
        return alerts

    # ── 观测 ─────────────────────────────────────────────────
    @property
    def last_latency_ms(self) -> float:
        return self.alerts[-1].latency_ms if self.alerts else 0.0

    @property
    def max_latency_ms(self) -> float:
        return max((a.latency_ms for a in self.alerts), default=0.0)

    def summary(self) -> Dict[str, Any]:
        by_sev: Dict[str, int] = {}
        for a in self.alerts:
            by_sev[a.severity] = by_sev.get(a.severity, 0) + 1
        return {
            "alert_count": len(self.alerts),
            "by_severity": dict(sorted(by_sev.items())),
            "max_latency_ms": round(self.max_latency_ms, 4),
            "latency_budget_ms": float(
                (self.cfg.performance or {}).get("alert_latency_budget_ms", 1000.0)
            ),
            "all_within_budget": all(a.within_budget for a in self.alerts),
            "history_path": str(self._history_path) if self._history_path else None,
        }

    # ── 内部 ─────────────────────────────────────────────────
    def _build_alert(self, result: CascadeResult, budget: float, started: float) -> Alert:
        elapsed_ms = (self._clock() - started) * 1000.0
        payload = result.to_dict()
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "capability_id": result.event["capability_id"],
                    "from": result.from_level,
                    "to": result.to_level,
                    "affected": [a["id"] for a in result.affected],
                    "seq": self._counter,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:16]
        return Alert(
            alert_id=f"alr-{fingerprint}",
            capability_id=result.event["capability_id"],
            from_level=result.from_level,
            to_level=result.to_level,
            severity=result.severity,
            affected=[
                {
                    "id": a["id"],
                    "level_before": a["level_before"],
                    "level_after": a["level_after"],
                    "degradation": a["degradation"],
                    "hop": a["hop"],
                    "path": a["path"],
                }
                for a in result.affected
            ],
            affected_count=len(result.affected),
            latency_ms=elapsed_ms,
            budget_ms=budget,
            within_budget=elapsed_ms <= budget,
            created_at=_utc_now(),
            cascade=payload,
        )