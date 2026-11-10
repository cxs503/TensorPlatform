#!/usr/bin/env python3
"""progress_metrics —— 战役 DAG 节点级「进展度量 → 停滞检测 → 早停换路 → 诚实收尾」闭环。

为什么存在
----------
plan 148 中 m6945 一类节点的心跳在、预算在烧、工作区却一动不动，事后只在任务表里留下
``no_progress`` 一行字：既没有可复核的进展曲线，也没有换路建议，等人类发现时预算已经烧完。
平台现有检测各管一段，谁也不回答「这个节点到底有没有往前走」：

* ``ops/taskplane/stall_detector.py``           —— 平台整体：断粮 / 舰队心跳丢失 / 脉搏停滞；
* ``ops/taskplane/campaign_health_monitor.py``  —— 节点级超时 / 空转 / 资源泄漏（按心跳窗判活）。

本模块补的是**进展语义**这一层，且只做共性机制（零领域语义）：

    进展信号采集（工件内容哈希变化 / pytest 通过率 / 判据满足率 / 信息增益）
      → 停滞检测（连续 N 拍硬无进展 + 滑动窗斜率，双规则）
      → 早停换路（honest FAIL 报告 + 替代路径推荐 + 资源释放建议）
      → 回写战役 DAG（campaign_nodes.status='blocked' + block_detected 事件）

判据先于实现（机器可裁，数字落 ``artifacts/p148w2_n08/criteria.json``）
----------------------------------------------------------------------
C1 停滞检测：构造「连续 3 拍内容零变化」的标注场景 → 停滞标记准确率 100%（precision/recall 同报）；
C2 早停触发：故意停滞场景 → 早停触发率 100%；
C3 负控正常进展：正常进展场景 → 早停触发率 0%（且观测拍数可数，「不触发」≠「没检查」）；
C4 信息增益：新工件 → 信息增益 > 0 的判定准确率 ≥ 90%；mtime-only 触碰 → 增益必须为 0（负控）；
C5 端到端：停滞节点在真实 PostgreSQL ``campaign_nodes`` 上被写成 ``blocked``，且调度器可派谓词
   （``status NOT IN ('done','failed','blocked')``）此后不再命中该节点；
C6 报告：早停报告含进展曲线（逐拍 composite/工件数/判据率）+ 替代路径推荐 + 证据链 sha256；
C7 零领域语义 + pytest 全绿。

设计不变式
----------
* **内容派生进展**：工件进展按**内容哈希**判，不看 mtime —— 触碰时间戳不算进展（防假活）。
* **warmup 不参与判定**：首拍只是基线（没有「上次」可比），标 ``warmup=True`` 并从检测窗口剔除，
  避免「首拍增益=1.0 → 后续 0」被误读成负斜率。
* **斜率取有符号**：``slope < threshold`` 才算停滞；用 ``abs(slope)`` 会把「断崖下滑」也算成停滞。
* **fail closed**：SQL 执行失败不得假装成功；``mark_node_blocked`` 返回真实受影响行数，
  0 行（节点已终态）必须如实报告。
* **honest FAIL 不可覆盖**：``EarlyStopReport.honest_fail`` 恒真；报告文件写入后只读（sha256 留痕），
  同名文件存在时改新文件名，绝不覆盖既有失败证据。
* **可离线重放**：检测层是历史的纯函数（snapshot 序列 → verdict），断点续跑靠重放日志。

对外契约（供上游崩溃取证/续跑、下游调度器对接；漂移只改适配层）
----------------------------------------------------------------
1. 快照日志：``runs/<mission>/progress/snapshots.jsonl``，逐行 ``tp.progress.snapshot.v1``，
   带 ``prev_sha``/``sha`` 哈希链，可机械验伪；
2. 外部信号适配层 :class:`ExternalSignalAdapter`：只接受白名单 schema，未知 schema 一律 rejected
   并标 ``degraded``，**不猜测上游字段**；
3. 调度器回写面：``campaign_nodes(status='blocked', block_reason='stagnation: ...')`` +
   ``campaign_events(event_type='block_detected')``（事件类型取既有 CHECK 枚举内的合法值）。

用法
----
    python3 ops/taskplane/progress_metrics.py tick --worktree . --node-id 42 --out runs/progress
    python3 ops/taskplane/progress_metrics.py watch --worktree . --node-id 42 \
        --interval 300 --max-cycles 12 --out runs/progress --dag-block
    python3 ops/taskplane/progress_metrics.py replay --journal runs/progress/snapshots.jsonl

退出码：0=继续；2=本拍触发早停；1=输入/执行错误（fail closed）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

UTC = timezone.utc
REPO_ROOT = Path(__file__).resolve().parents[2]

SCHEMA_SIGNAL = "tp.progress.signal.v1"
SCHEMA_SNAPSHOT = "tp.progress.snapshot.v1"
SCHEMA_VERDICT = "tp.progress.verdict.v1"
SCHEMA_EARLY_STOP = "tp.progress.early_stop.v1"

# ── 信号词表（对外契约；新增只能追加） ──────────────────────────────────────
SIGNAL_ARTIFACT_CHANGE = "artifact_change_fraction"
SIGNAL_INFO_GAIN = "information_gain"
SIGNAL_PYTEST_PASS = "pytest_pass_rate"
SIGNAL_PYTEST_DELTA = "pytest_pass_delta"
SIGNAL_CRITERIA_PASS = "criteria_pass_rate"
SIGNAL_CRITERIA_DELTA = "criteria_pass_delta"
SIGNAL_EXTERNAL = "external_event"

# 参与 composite 的权重（其余信号只作证据，权重为 0）
SIGNAL_WEIGHTS: Dict[str, float] = {
    SIGNAL_ARTIFACT_CHANGE: 0.30,
    SIGNAL_INFO_GAIN: 0.25,
    SIGNAL_PYTEST_PASS: 0.20,
    SIGNAL_CRITERIA_PASS: 0.25,
}

ACTION_CONTINUE = "continue"
ACTION_WATCH = "watch"
ACTION_INVESTIGATE = "investigate"
ACTION_EARLY_STOP = "early_stop"
ACTION_OBSERVE = "observe"  # 样本不足

RULE_NO_PROGRESS_CYCLES = "consecutive_no_progress_cycles"
RULE_FLAT_SLOPE = "flat_slope"

NODE_TERMINAL = ("done", "failed", "blocked")


# ── 工具 ────────────────────────────────────────────────────────────────────
def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat()


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── 阈值 ────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ProgressThresholds:
    """可配置阈值（env 覆盖前缀 ``TP_PROGRESS_``）。

    ``min_runtime_before_early_stop_s`` 默认 0：墙钟下限在离线重放/固定夹具里不可复现，
    且「连续 N 拍硬无进展」本身已是拍数级护栏（单拍不可能触发早停）。需要额外墙钟保护的
    部署可透传非 0 值，语义是「跑够这么久才允许早停」。
    """

    stagnation_window_size: int = 5
    stagnation_slope_threshold: float = 0.01
    stagnation_min_samples: int = 3
    no_progress_cycles: int = 3
    info_gain_min_delta: float = 1e-6
    change_fraction_epsilon: float = 1e-12
    min_runtime_before_early_stop_s: float = 0.0
    scan_extensions: str = ".py,.json,.md,.txt,.csv,.yaml,.yml,.toml,.cfg,.ini,.sh"
    scan_exclude_dirs: str = (
        ".git,__pycache__,node_modules,.pytest_cache,.mypy_cache,.ruff_cache,"
        ".venv,venv,dist,build,.recycle"
    )
    max_file_bytes: int = 2_000_000
    pytest_timeout_s: int = 900

    def as_dict(self) -> Dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @property
    def extension_set(self) -> Tuple[str, ...]:
        return tuple(e.strip().lower() for e in self.scan_extensions.split(",") if e.strip())

    @property
    def exclude_set(self) -> Tuple[str, ...]:
        return tuple(d.strip() for d in self.scan_exclude_dirs.split(",") if d.strip())

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "ProgressThresholds":
        env = os.environ if env is None else env
        kwargs: Dict[str, Any] = {}
        base = cls()
        for f in fields(cls):
            raw = env.get("TP_PROGRESS_" + f.name.upper())
            if raw in (None, ""):
                continue
            current = getattr(base, f.name)
            kwargs[f.name] = type(current)(raw)  # str/int/float 一致转换；非法值抛错（fail closed）
        return cls(**kwargs)


# ── 数据模型 ────────────────────────────────────────────────────────────────
@dataclass
class ProgressSignal:
    ts: datetime
    signal_type: str
    value: float
    detail: Dict[str, Any] = field(default_factory=dict)
    source: str = "unknown"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA_SIGNAL,
            "ts": _iso(self.ts),
            "signal_type": self.signal_type,
            "value": round(self.value, 9),
            "detail": self.detail,
            "source": self.source,
            "weight": SIGNAL_WEIGHTS.get(self.signal_type, 0.0),
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ProgressSignal":
        return cls(
            ts=datetime.fromisoformat(str(raw["ts"])),
            signal_type=str(raw["signal_type"]),
            value=float(raw.get("value", 0.0)),
            detail=dict(raw.get("detail") or {}),
            source=str(raw.get("source", "unknown")),
        )


@dataclass
class ProgressSnapshot:
    ts: datetime
    node_id: Optional[int]
    cycle: int
    warmup: bool
    composite_score: float
    signals: List[ProgressSignal]
    artifact_hash: str = ""
    file_count: int = 0
    line_count: int = 0
    change_fraction: float = 0.0
    novel_file_count: int = 0
    info_gain: float = 0.0
    pytest_pass_rate: float = 0.0
    pytest_total: int = 0
    pytest_passed: int = 0
    pytest_stale: bool = True
    criteria_pass_rate: float = 0.0
    criteria_total: int = 0
    criteria_passed: int = 0
    criteria_delta: float = 0.0
    pytest_delta: float = 0.0
    hard_no_progress: bool = False
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA_SNAPSHOT,
            "ts": _iso(self.ts),
            "node_id": self.node_id,
            "cycle": self.cycle,
            "warmup": self.warmup,
            "composite_score": round(self.composite_score, 9),
            "artifact_hash": self.artifact_hash,
            "file_count": self.file_count,
            "line_count": self.line_count,
            "change_fraction": round(self.change_fraction, 9),
            "novel_file_count": self.novel_file_count,
            "info_gain": round(self.info_gain, 9),
            "pytest_pass_rate": round(self.pytest_pass_rate, 6),
            "pytest_total": self.pytest_total,
            "pytest_passed": self.pytest_passed,
            "pytest_stale": self.pytest_stale,
            "criteria_pass_rate": round(self.criteria_pass_rate, 6),
            "criteria_total": self.criteria_total,
            "criteria_passed": self.criteria_passed,
            "criteria_delta": round(self.criteria_delta, 9),
            "pytest_delta": round(self.pytest_delta, 9),
            "hard_no_progress": self.hard_no_progress,
            "notes": list(self.notes),
            "signals": [s.as_dict() for s in self.signals],
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ProgressSnapshot":
        return cls(
            ts=datetime.fromisoformat(str(raw["ts"])),
            node_id=raw.get("node_id"),
            cycle=int(raw.get("cycle", 0)),
            warmup=bool(raw.get("warmup", False)),
            composite_score=float(raw.get("composite_score", 0.0)),
            signals=[ProgressSignal.from_dict(s) for s in raw.get("signals", [])],
            artifact_hash=str(raw.get("artifact_hash", "")),
            file_count=int(raw.get("file_count", 0)),
            line_count=int(raw.get("line_count", 0)),
            change_fraction=float(raw.get("change_fraction", 0.0)),
            novel_file_count=int(raw.get("novel_file_count", 0)),
            info_gain=float(raw.get("info_gain", 0.0)),
            pytest_pass_rate=float(raw.get("pytest_pass_rate", 0.0)),
            pytest_total=int(raw.get("pytest_total", 0)),
            pytest_passed=int(raw.get("pytest_passed", 0)),
            pytest_stale=bool(raw.get("pytest_stale", True)),
            criteria_pass_rate=float(raw.get("criteria_pass_rate", 0.0)),
            criteria_total=int(raw.get("criteria_total", 0)),
            criteria_passed=int(raw.get("criteria_passed", 0)),
            criteria_delta=float(raw.get("criteria_delta", 0.0)),
            pytest_delta=float(raw.get("pytest_delta", 0.0)),
            hard_no_progress=bool(raw.get("hard_no_progress", False)),
            notes=list(raw.get("notes") or []),
        )


@dataclass
class StagnationVerdict:
    ts: datetime
    node_id: Optional[int]
    is_stalled: bool
    consecutive_no_progress_cycles: int
    slope: float
    window_values: List[float] = field(default_factory=list)
    recommendation: str = ACTION_CONTINUE
    rule_fired: Optional[str] = None
    trend_flat: bool = False
    reason: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA_VERDICT,
            "ts": _iso(self.ts),
            "node_id": self.node_id,
            "is_stalled": self.is_stalled,
            "consecutive_no_progress_cycles": self.consecutive_no_progress_cycles,
            "slope": round(self.slope, 9),
            "window_values": [round(v, 9) for v in self.window_values],
            "recommendation": self.recommendation,
            "rule_fired": self.rule_fired,
            "trend_flat": self.trend_flat,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass
class EarlyStopReport:
    ts: datetime
    node_id: Optional[int]
    reason: str
    stagnation_evidence: Dict[str, Any]
    progress_curve: List[Dict[str, Any]]
    alternative_paths: List[Dict[str, Any]]
    resources_released: Dict[str, Any]
    thresholds: Dict[str, Any]
    honest_fail: bool = True
    report_path: Optional[str] = None
    report_sha256: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA_EARLY_STOP,
            "ts": _iso(self.ts),
            "node_id": self.node_id,
            "honest_fail": True,  # 恒定，不可由调用方覆盖
            "reason": self.reason,
            "stagnation_evidence": self.stagnation_evidence,
            "progress_curve": self.progress_curve,
            "alternative_paths": self.alternative_paths,
            "resources_released": self.resources_released,
            "thresholds": self.thresholds,
            "report_path": self.report_path,
            "report_sha256": self.report_sha256,
        }


# ── 工件扫描 ────────────────────────────────────────────────────────────────
class ArtifactScanner:
    """按**内容哈希**扫描工作树工件，产出工件级进展原始量。"""

    def __init__(self, worktree: Path, thresholds: Optional[ProgressThresholds] = None,
                 exclude_paths: Sequence[Path] = ()):
        self.worktree = Path(worktree)
        self.th = thresholds or ProgressThresholds()
        # 观测者效应防线：度量器自己的输出（快照日志 / 早停报告）不得计入被观测进展
        self.exclude_paths = tuple(Path(p) for p in exclude_paths)

    def _is_own_output(self, path: Path) -> bool:
        try:
            rp = path.resolve()
        except OSError:  # pragma: no cover
            return False
        for ex in self.exclude_paths:
            try:
                ex_r = ex.resolve()
            except OSError:  # pragma: no cover
                continue
            if rp == ex_r or ex_r in rp.parents:
                return True
        return False

    def should_include(self, path: Path) -> bool:
        if not path.is_file():
            return False
        if self.exclude_paths and self._is_own_output(path):
            return False
        try:
            if path.stat().st_size > self.th.max_file_bytes:
                return False
        except OSError:
            return False
        if path.suffix.lower() not in self.th.extension_set:
            return False
        try:
            rel_parts = path.relative_to(self.worktree).parts
        except ValueError:
            return False
        return not any(part in self.th.exclude_set for part in rel_parts)

    def scan_files(self) -> List[Path]:
        if not self.worktree.exists():
            return []
        return sorted(
            (p for p in self.worktree.rglob("*") if self.should_include(p)),
            key=lambda p: str(p.relative_to(self.worktree)),
        )

    def per_file_hashes(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for p in self.scan_files():
            try:
                out[str(p.relative_to(self.worktree))] = _file_digest(p)
            except OSError:
                continue
        return out

    def snapshot_state(self) -> Dict[str, Any]:
        """一次遍历得到（聚合哈希, 文件数, 行数, 逐文件哈希）。"""
        hashes = self.per_file_hashes()
        lines = 0
        for rel in hashes:
            try:
                text = (self.worktree / rel).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            lines += text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        aggregate = hashlib.blake2b(
            _canonical_json(sorted(hashes.items())).encode("utf-8"), digest_size=16
        ).hexdigest()
        return {"artifact_hash": aggregate, "file_count": len(hashes),
                "line_count": lines, "file_hashes": hashes}

    @staticmethod
    def change_fraction(prev: Optional[Dict[str, str]], cur: Dict[str, str]) -> float:
        """内容变化比例：新增 + 删除 + 内容改变，除以两侧文件数上界。"""
        if prev is None:
            return 0.0
        changed = sum(1 for k, v in cur.items() if prev.get(k) != v)
        removed = sum(1 for k in prev if k not in cur)
        denom = max(len(prev), len(cur), 1)
        return min(1.0, (changed + removed) / denom)


# ── pytest 通过率 ───────────────────────────────────────────────────────────
_PYTEST_SUMMARY = re.compile(
    r"(?P<count>\d+)\s+(?P<kind>passed|failed|error|errors|skipped|xfailed|xpassed|deselected)\b"
)


class PytestRunner:
    """运行 pytest 并解析通过率（不解析 ANSI 颜色，不做乐观默认）。"""

    def __init__(self, worktree: Path, target: str = "tests",
                 thresholds: Optional[ProgressThresholds] = None):
        self.worktree = Path(worktree)
        self.target = target
        self.th = thresholds or ProgressThresholds()

    def run(self) -> Dict[str, Any]:
        target_path = self.worktree / self.target
        if not target_path.exists():
            return {"total": 0, "passed": 0, "failed": 0, "error": 0, "pass_rate": 0.0,
                    "returncode": None, "stale": False, "note": f"target missing: {self.target}"}
        cmd = [sys.executable, "-m", "pytest", self.target, "-q", "--tb=no", "-p", "no:cacheprovider"]
        try:
            proc = subprocess.run(cmd, cwd=str(self.worktree), capture_output=True, text=True,
                                  timeout=self.th.pytest_timeout_s)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"total": 0, "passed": 0, "failed": 0, "error": 0, "pass_rate": 0.0,
                    "returncode": None, "stale": False,
                    "note": f"pytest not run: {type(exc).__name__}"}
        return self.parse_output(proc.stdout + "\n" + proc.stderr, proc.returncode)

    @staticmethod
    def parse_output(output: str, returncode: Optional[int] = None) -> Dict[str, Any]:
        counts = {"passed": 0, "failed": 0, "error": 0}
        for m in _PYTEST_SUMMARY.finditer(output):
            kind, n = m.group("kind"), int(m.group("count"))
            if kind == "passed":
                counts["passed"] = n
            elif kind == "failed":
                counts["failed"] = n
            elif kind in ("error", "errors"):
                counts["error"] = n
        total = counts["passed"] + counts["failed"] + counts["error"]
        rate = (counts["passed"] / total) if total else 0.0
        return {"total": total, "passed": counts["passed"], "failed": counts["failed"],
                "error": counts["error"], "pass_rate": rate, "returncode": returncode,
                "stale": False}


# ── 信息增益 ────────────────────────────────────────────────────────────────
class InformationGainEstimator:
    """信息增益 = 相对上一次观测的**新内容**占比（novelty），不看 mtime。

    定义（无量纲，[0,1]）::

        novelty  = |{当前文件内容哈希 ∉ 历史见过集合}| / max(1, 当前文件数)
        growth   = max(0, Δlines) / max(1, 上次 lines)
        churn    = 内容变化文件数 / max(1, 当前文件数)
        gain     = min(1, 0.50*novelty + 0.30*min(1,growth) + 0.20*churn)

    「新工件 ⇒ gain > 0」由 novelty/churn 保证；「仅触碰 mtime ⇒ gain == 0」由内容哈希保证。
    首拍（无历史）**不定义**增益：返回 ``warmup=True, gain=0.0``，由检测层剔除，绝不当作进展证据。
    """

    def __init__(self, thresholds: Optional[ProgressThresholds] = None):
        self.th = thresholds or ProgressThresholds()
        self._seen_content: set = set()
        self._prev_hashes: Optional[Dict[str, str]] = None
        self._prev_lines: Optional[int] = None

    def estimate(self, state: Dict[str, Any]) -> Dict[str, Any]:
        cur = state["file_hashes"]
        lines = int(state["line_count"])
        warmup = self._prev_hashes is None

        novel = [rel for rel, h in cur.items() if h not in self._seen_content]
        novelty = len(novel) / max(1, len(cur)) if cur else 0.0
        growth = 0.0
        if self._prev_lines is not None and self._prev_lines > 0:
            growth = max(0, lines - self._prev_lines) / self._prev_lines
        churn = 0.0 if warmup else ArtifactScanner.change_fraction(self._prev_hashes, cur)

        gain = 0.0 if warmup else min(1.0, 0.50 * novelty + 0.30 * min(1.0, growth) + 0.20 * churn)

        self._seen_content.update(cur.values())
        self._prev_hashes = dict(cur)
        self._prev_lines = lines
        return {"warmup": warmup, "novel_files": len(novel),
                "novel_file_paths": novel[:20], "novelty": round(novelty, 9),
                "line_growth": round(growth, 9), "churn": round(churn, 9),
                "gain": round(gain, 9)}


# ── 进展采集 ────────────────────────────────────────────────────────────────
class ProgressTracker:
    """按拍采集四类进展信号 → :class:`ProgressSnapshot`（历史可重放）。"""

    def __init__(self, worktree: Path, node_id: Optional[int] = None,
                 thresholds: Optional[ProgressThresholds] = None, pytest_target: str = "tests",
                 exclude_paths: Sequence[Path] = ()):
        self.worktree = Path(worktree)
        self.node_id = node_id
        self.th = thresholds or ProgressThresholds()
        self.scanner = ArtifactScanner(self.worktree, self.th, exclude_paths)
        self.pytest_runner = PytestRunner(self.worktree, pytest_target, self.th)
        self.info = InformationGainEstimator(self.th)
        self._history: List[ProgressSnapshot] = []
        self._prev_hashes: Optional[Dict[str, str]] = None
        self._rebaseline = False
        self._last_pytest = {"pass_rate": 0.0, "total": 0, "passed": 0}
        self._last_criteria = {"pass_rate": 0.0, "total": 0, "passed": 0}

    @property
    def history(self) -> List[ProgressSnapshot]:
        return list(self._history)

    def restore(self, snapshots: Iterable[ProgressSnapshot]) -> None:
        """续跑：重放既有历史；本进程第一拍重新基线化（warmup），见模块不变式。"""
        self._history = list(snapshots)
        if self._history:
            last = self._history[-1]
            self._last_pytest = {"pass_rate": last.pytest_pass_rate,
                                 "total": last.pytest_total, "passed": last.pytest_passed}
            self._last_criteria = {"pass_rate": last.criteria_pass_rate,
                                   "total": last.criteria_total, "passed": last.criteria_passed}
        self._prev_hashes = None
        self._rebaseline = True
        self.info = InformationGainEstimator(self.th)

    def take_snapshot(self, *, run_pytest: bool = False,
                      pytest_result: Optional[Dict[str, Any]] = None,
                      criteria: Optional[Dict[str, bool]] = None,
                      ts: Optional[datetime] = None) -> ProgressSnapshot:
        ts = ts or _utcnow()
        state = self.scanner.snapshot_state()
        change = ArtifactScanner.change_fraction(self._prev_hashes, state["file_hashes"])
        gain = self.info.estimate(state)
        warmup = (not self._history) or self._rebaseline

        # pytest（不回填乐观值：没跑就标 stale 并沿用上次观测）
        if pytest_result is not None:
            pyt = dict(pytest_result, stale=False)
        elif run_pytest:
            pyt = self.pytest_runner.run()
        else:
            pyt = {"pass_rate": self._last_pytest["pass_rate"], "total": self._last_pytest["total"],
                   "passed": self._last_pytest["passed"], "stale": True}
        pass_rate = float(pyt.get("pass_rate", 0.0))
        pytest_delta = pass_rate - self._last_pytest["pass_rate"]
        self._last_pytest = {"pass_rate": pass_rate, "total": int(pyt.get("total", 0)),
                            "passed": int(pyt.get("passed", 0))}

        # 判据
        if criteria is not None:
            total = len(criteria)
            passed = sum(1 for v in criteria.values() if v)
            crit_rate = (passed / total) if total else 0.0
            crit_delta = crit_rate - self._last_criteria["pass_rate"]
            self._last_criteria = {"pass_rate": crit_rate, "total": total, "passed": passed}
            crit_source = "criteria"
        else:
            crit_rate = self._last_criteria["pass_rate"]
            total = self._last_criteria["total"]
            passed = self._last_criteria["passed"]
            crit_delta = 0.0
            crit_source = "cached" if total else "none"

        signals = [
            ProgressSignal(ts, SIGNAL_ARTIFACT_CHANGE, change,
                           {"artifact_hash": state["artifact_hash"],
                            "changed_files": round(change * max(1, len(state["file_hashes"])), 3)},
                           "artifact_scanner"),
            ProgressSignal(ts, SIGNAL_INFO_GAIN, gain["gain"],
                           {k: gain[k] for k in ("novel_files", "novelty", "line_growth", "churn", "warmup")},
                           "info_estimator"),
            ProgressSignal(ts, SIGNAL_PYTEST_PASS, pass_rate,
                           {"total": int(pyt.get("total", 0)), "passed": int(pyt.get("passed", 0)),
                            "stale": bool(pyt.get("stale", False))}, "pytest_runner"),
            ProgressSignal(ts, SIGNAL_PYTEST_DELTA, pytest_delta, {}, "pytest_runner"),
            ProgressSignal(ts, SIGNAL_CRITERIA_PASS, crit_rate,
                           {"total": total, "passed": passed, "source": crit_source}, "criteria"),
            ProgressSignal(ts, SIGNAL_CRITERIA_DELTA, crit_delta, {}, "criteria"),
        ]
        composite = sum(SIGNAL_WEIGHTS[s.signal_type] * s.value
                        for s in signals if s.signal_type in SIGNAL_WEIGHTS)

        no_content = change <= self.th.change_fraction_epsilon
        no_gain = gain["gain"] <= self.th.info_gain_min_delta
        no_criteria = crit_delta <= 0.0
        no_pytest = pytest_delta <= 0.0
        hard_no_progress = bool((not warmup) and no_content and no_gain and no_criteria and no_pytest)

        notes: List[str] = []
        if warmup:
            notes.append("warmup: 基线拍，不定义信息增益、不参与停滞判定")
        if pyt.get("stale", False):
            notes.append("pytest pass rate stale（本拍未运行）")
        if pyt.get("note"):
            notes.append(str(pyt["note"]))

        snap = ProgressSnapshot(
            ts=ts, node_id=self.node_id, cycle=len(self._history), warmup=warmup,
            composite_score=composite, signals=signals,
            artifact_hash=state["artifact_hash"], file_count=state["file_count"],
            line_count=state["line_count"], change_fraction=change,
            novel_file_count=int(gain["novel_files"]), info_gain=gain["gain"],
            pytest_pass_rate=pass_rate, pytest_total=int(pyt.get("total", 0)),
            pytest_passed=int(pyt.get("passed", 0)), pytest_stale=bool(pyt.get("stale", False)),
            criteria_pass_rate=crit_rate, criteria_total=total, criteria_passed=passed,
            criteria_delta=crit_delta, pytest_delta=pytest_delta,
            hard_no_progress=hard_no_progress, notes=notes,
        )
        self._history.append(snap)
        self._prev_hashes = state["file_hashes"]
        self._rebaseline = False
        return snap

    @staticmethod
    def attach_external(snapshot: ProgressSnapshot, ingest: "IngestResult") -> None:
        """把外部信号（上游续跑/取证、健康监控）作为证据挂到本拍（不改 composite）。"""
        if not ingest.records:
            return
        snapshot.signals.append(ProgressSignal(
            snapshot.ts, SIGNAL_EXTERNAL, float(len(ingest.records)),
            {"accepted": ingest.accepted, "rejected": ingest.rejected,
             "degraded": ingest.degraded, "kinds": ingest.kinds},
            "external_adapter"))


# ── 停滞检测 ────────────────────────────────────────────────────────────────
class StagnationDetector:
    """双规则停滞检测（历史的纯函数：同序列 → 同 verdict，可离线重放）。"""

    def __init__(self, thresholds: Optional[ProgressThresholds] = None):
        self.th = thresholds or ProgressThresholds()

    @staticmethod
    def slope(values: Sequence[float]) -> float:
        n = len(values)
        if n < 2:
            return 0.0
        x_mean = (n - 1) / 2.0
        y_mean = sum(values) / n
        num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
        den = sum((i - x_mean) ** 2 for i in range(n))
        return num / den if den else 0.0

    @staticmethod
    def trailing_no_progress(snapshots: Sequence[ProgressSnapshot]) -> int:
        count = 0
        for s in reversed(list(snapshots)):
            if s.warmup:
                continue
            if s.hard_no_progress:
                count += 1
            else:
                break
        return count

    def analyze(self, snapshots: Sequence[ProgressSnapshot],
                ts: Optional[datetime] = None) -> StagnationVerdict:
        ts = ts or _utcnow()
        real = [s for s in snapshots if not s.warmup]
        node_id = snapshots[-1].node_id if snapshots else None

        # 尾段计数在任何样本量下都是良定义的：样本不足只意味着「不做停滞判定」，
        # 不能把计数器清零（否则观测器自己的读数会与事实错拍）。
        consecutive = self.trailing_no_progress(real)
        if len(real) < self.th.stagnation_min_samples:
            return StagnationVerdict(
                ts=ts, node_id=node_id, is_stalled=False,
                consecutive_no_progress_cycles=consecutive,
                slope=0.0, recommendation=ACTION_OBSERVE,
                reason=(f"样本不足：有效拍 {len(real)} < {self.th.stagnation_min_samples}"
                        f"（warmup 剔除 {len(snapshots) - len(real)}）"),
                detail={"effective_samples": len(real), "min_samples": self.th.stagnation_min_samples})

        window = real[-self.th.stagnation_window_size:]
        values = [s.composite_score for s in window]
        slope = self.slope(values)
        # 斜率单条不足以判停滞：收敛型节点的 composite 会随 novelty 饱和自然衰减（斜率为负），
        # 这不是停滞。故 flat_slope 规则额外要求尾段至少已有 1 拍硬无进展。
        slope_flat = (len(window) >= self.th.stagnation_window_size
                      and slope < self.th.stagnation_slope_threshold
                      and consecutive >= 1)

        # 「停滞标记」只由拍数规则给出（连续 N 拍硬无进展，语义无歧义、可机器标注）；
        # 斜率是**趋势告警**而非停滞标记：稀疏但真实的进展会让 composite 呈锯齿/衰减，
        # 单看斜率必然误报（本仓实验中 26/220 拍），故只降级为 trend_flat 供复核。
        is_stalled = consecutive >= self.th.no_progress_cycles
        rule: Optional[str] = None
        if is_stalled:
            rule = RULE_NO_PROGRESS_CYCLES
        elif slope_flat:
            rule = RULE_FLAT_SLOPE

        if is_stalled:
            recommendation = ACTION_EARLY_STOP
        elif slope_flat:
            recommendation = ACTION_INVESTIGATE
        elif consecutive > 0:
            recommendation = ACTION_WATCH
        else:
            recommendation = ACTION_CONTINUE

        reason = (f"连续硬无进展 {consecutive} 拍（阈值 {self.th.no_progress_cycles}）, "
                  f"窗口斜率 {slope:.6f}（阈值 {self.th.stagnation_slope_threshold}）")
        return StagnationVerdict(
            ts=ts, node_id=node_id, is_stalled=is_stalled,
            consecutive_no_progress_cycles=consecutive, slope=slope,
            window_values=values, recommendation=recommendation, rule_fired=rule,
            trend_flat=bool(slope_flat), reason=reason,
            detail={"window_size": len(window), "effective_samples": len(real),
                    "warmup_excluded": len(snapshots) - len(real),
                    "slope_threshold": self.th.stagnation_slope_threshold,
                    "no_progress_cycles_threshold": self.th.no_progress_cycles})

    def should_early_stop(self, verdict: StagnationVerdict, runtime_s: float = 0.0) -> bool:
        if runtime_s < self.th.min_runtime_before_early_stop_s:
            return False
        return verdict.consecutive_no_progress_cycles >= self.th.no_progress_cycles


# ── 早停换路 ────────────────────────────────────────────────────────────────
class EarlyStopHandler:
    """生成 honest FAIL 报告 + 替代路径推荐；报告写入后只读留痕，绝不覆盖。"""

    def __init__(self, worktree: Path, thresholds: Optional[ProgressThresholds] = None,
                 output_dir: Optional[Path] = None):
        self.worktree = Path(worktree)
        self.th = thresholds or ProgressThresholds()
        self.output_dir = Path(output_dir) if output_dir else (self.worktree / "runs" / "early_stop")

    @staticmethod
    def build_progress_curve(history: Sequence[ProgressSnapshot]) -> List[Dict[str, Any]]:
        return [{
            "cycle": s.cycle, "ts": _iso(s.ts), "warmup": s.warmup,
            "composite_score": round(s.composite_score, 9),
            "change_fraction": round(s.change_fraction, 9),
            "info_gain": round(s.info_gain, 9),
            "file_count": s.file_count, "line_count": s.line_count,
            "pytest_pass_rate": round(s.pytest_pass_rate, 6),
            "criteria_pass_rate": round(s.criteria_pass_rate, 6),
            "hard_no_progress": s.hard_no_progress,
        } for s in history]

    def recommend(self, history: Sequence[ProgressSnapshot],
                  thresholds: Optional[ProgressThresholds] = None) -> List[Dict[str, Any]]:
        """替代路径推荐（纯函数、可单测；只依赖信号，不含任何领域判据）。"""
        th = thresholds or self.th
        last = history[-1] if history else None
        out: List[Dict[str, Any]] = []

        criteria_gap = 1.0 - (last.criteria_pass_rate if last else 0.0)
        if last and criteria_gap > 0:
            out.append({
                "strategy": "criteria_gap_squeeze",
                "description": "只推进尚未满足的判据子集，按判据逐条收敛，不再扩面",
                "confidence": round(min(0.95, 0.5 + 0.45 * criteria_gap), 4),
                "evidence": {"criteria_pass_rate": last.criteria_pass_rate,
                             "criteria_failed": last.criteria_total - last.criteria_passed},
            })

        if last and last.pytest_total > 0 and last.pytest_pass_rate < 1.0:
            out.append({
                "strategy": "test_first",
                "description": "先把红测试修绿（通过率是后续所有判据的分母），暂缓新增功能",
                "confidence": round(min(0.95, 0.5 + 0.45 * (1 - last.pytest_pass_rate)), 4),
                "evidence": {"pytest_pass_rate": last.pytest_pass_rate,
                             "failing": last.pytest_total - last.pytest_passed},
            })

        if last and last.file_count == 0:
            out.append({
                "strategy": "bootstrap_from_baseline",
                "description": "工作树内无可扫描工件：改从既有基线/模板起步，先落最小可证单元",
                "confidence": 0.8,
                "evidence": {"file_count": 0, "scan_extensions": th.scan_extensions},
            })

        out.append({
            "strategy": "decompose_min_viable_unit",
            "description": "把节点拆成可在 1 拍内验证的更小单元，逐单元交付并单独出证据",
            "confidence": 0.7,
            "evidence": {"no_progress_cycles_threshold": th.no_progress_cycles},
        })
        out.append({
            "strategy": "honest_fail_handoff",
            "description": "按 honest FAIL 结项：保留进展曲线与搜索记录，交由复核者改派或换路",
            "confidence": 0.6,
            "evidence": {"rule": "early_stop", "honest_fail": True},
        })
        out.sort(key=lambda a: -float(a["confidence"]))
        return out

    def generate_report(self, *, node_id: Optional[int], history: Sequence[ProgressSnapshot],
                        verdict: StagnationVerdict, reason: str = "",
                        mission_id: Optional[int] = None,
                        resources: Optional[Dict[str, Any]] = None) -> EarlyStopReport:
        curve = self.build_progress_curve(history)
        report = EarlyStopReport(
            ts=_utcnow(), node_id=node_id,
            reason=reason or verdict.reason,
            stagnation_evidence=verdict.as_dict(),
            progress_curve=curve,
            alternative_paths=self.recommend(history),
            resources_released=resources or {
                "node_id": node_id, "mission_id": mission_id,
                "target_status": "blocked", "release_worker": True,
                "budget_freeze_recommended": True,
            },
            thresholds=self.th.as_dict(),
        )
        payload = report.as_dict()
        # 报告自己不含 path/sha（避免自指），落盘后回填
        body = {k: v for k, v in payload.items() if k not in ("report_path", "report_sha256")}
        text = json.dumps(body, indent=2, ensure_ascii=False, sort_keys=True)
        digest = _sha256(text)

        self.output_dir.mkdir(parents=True, exist_ok=True)
        stamp = report.ts.strftime("%Y%m%dT%H%M%SZ")
        base = self.output_dir / f"early_stop_node{node_id if node_id is not None else 'x'}_{stamp}.json"
        path, n = base, 0
        while path.exists():  # honest FAIL 不可覆盖：同名只追加序号
            n += 1
            path = base.with_name(f"{base.stem}_{n}.json")
        header = f"# honest_fail_report sha256={digest}\n"
        with open(path, "x", encoding="utf-8") as fh:  # "x" = 独占创建，杜绝覆盖
            fh.write(header + text + "\n")
        # 只读留痕
        try:
            path.chmod(0o444)
        except OSError:
            pass
        report.report_path = str(path)
        report.report_sha256 = digest
        return report


# ── 快照日志（哈希链，可重放/可验伪） ───────────────────────────────────────
class JournalError(RuntimeError):
    """快照日志损坏或哈希链断裂（fail closed，不静默容忍）。"""


class ProgressJournal:
    """append-only JSONL 快照日志，逐行哈希链（``prev_sha`` → ``sha``）。"""

    def __init__(self, path: Path):
        self.path = Path(path)

    def append(self, record: Dict[str, Any]) -> Dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        prev_sha, seq = "", 0
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        prev = json.loads(line)
                        prev_sha, seq = prev["sha"], int(prev["seq"]) + 1
        envelope = {"seq": seq, "prev_sha": prev_sha, "record": record}
        envelope["sha"] = _sha256(_canonical_json(envelope))
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n")
        return envelope

    def verify(self) -> Tuple[bool, List[str]]:
        if not self.path.exists():
            return False, ["journal missing"]
        problems: List[str] = []
        prev_sha, expected_seq = "", 0
        with open(self.path, "r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    env = json.loads(line)
                except json.JSONDecodeError as exc:
                    problems.append(f"line {line_no}: not JSON ({exc.msg})")
                    prev_sha, expected_seq = "", expected_seq + 1
                    continue
                body = {k: v for k, v in env.items() if k != "sha"}
                if env.get("prev_sha") != prev_sha:
                    problems.append(f"line {line_no}: prev_sha mismatch")
                if int(env.get("seq", -1)) != expected_seq:
                    problems.append(f"line {line_no}: seq {env.get('seq')} != {expected_seq}")
                if env.get("sha") != _sha256(_canonical_json(body)):
                    problems.append(f"line {line_no}: sha mismatch")
                prev_sha, expected_seq = env.get("sha", ""), expected_seq + 1
        return (not problems), problems

    def load(self, *, verify: bool = True) -> List[Dict[str, Any]]:
        ok, problems = self.verify()
        if verify and not ok:
            raise JournalError(f"journal 校验失败：{problems[:3]}（共 {len(problems)} 项）")
        out: List[Dict[str, Any]] = []
        if not self.path.exists():
            return out
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    out.append(json.loads(line)["record"])
        return out


# ── 外部信号适配层（上游崩溃取证/续跑、健康监控 → 本模块） ──────────────────
ACCEPTED_SCHEMAS = ("tp.resume.event.v1", "tp.health.idle.v1")


@dataclass
class IngestResult:
    records: List[Dict[str, Any]] = field(default_factory=list)
    accepted: int = 0
    rejected: int = 0
    degraded: bool = False
    reason: str = ""
    kinds: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"accepted": self.accepted, "rejected": self.rejected,
                "degraded": self.degraded, "reason": self.reason,
                "kinds": list(self.kinds), "records": len(self.records)}


class ExternalSignalAdapter:
    """白名单 schema 的外部信号适配层。

    上游（崩溃取证/断点续跑、节点健康监控）契约为 JSONL，每行::

        {"schema": "tp.resume.event.v1", "ts": "...", "kind": "resume|crash",
         "checkpoint_restored": true, "mission_id": 123}

    适配层只做**校验 + 归一**：未知 schema / 缺字段一律 rejected 并标 degraded，
    绝不猜测上游字段；接口漂移时只改本类（模块其余部分不感知）。
    """

    def load(self, path: Optional[Path]) -> IngestResult:
        res = IngestResult()
        if path is None:
            res.degraded = True
            res.reason = "no external signal path configured"
            return res
        p = Path(path)
        if not p.exists():
            res.degraded = True
            res.reason = f"external signal file missing: {p}"
            return res
        with open(p, "r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as exc:
                    res.rejected += 1
                    res.reason = f"line {line_no}: not JSON ({exc.msg})"
                    continue
                if not isinstance(rec, dict) or rec.get("schema") not in ACCEPTED_SCHEMAS:
                    res.rejected += 1
                    res.reason = f"line {line_no}: unknown schema"
                    continue
                try:
                    datetime.fromisoformat(str(rec["ts"]))
                except (KeyError, ValueError):
                    res.rejected += 1
                    res.reason = f"line {line_no}: bad ts"
                    continue
                if not str(rec.get("kind", "")).strip():
                    res.rejected += 1
                    res.reason = f"line {line_no}: missing kind"
                    continue
                res.records.append(rec)
                res.accepted += 1
                res.kinds.append(str(rec["kind"]))
        res.degraded = res.rejected > 0
        return res


# ── 战役 DAG 回写面（真实 PG，fail closed） ─────────────────────────────────
class DAGError(RuntimeError):
    """战役 DAG 读写失败（不吞异常、不假装成功）。"""


@dataclass
class BlockResult:
    node_id: int
    reason: str
    previous_status: Optional[str]
    new_status: Optional[str]
    affected: int
    dry_run: bool = False

    @property
    def blocked(self) -> bool:
        return (not self.dry_run) and self.affected > 0 and self.new_status == "blocked"

    def as_dict(self) -> Dict[str, Any]:
        return {"node_id": self.node_id, "reason": self.reason,
                "previous_status": self.previous_status, "new_status": self.new_status,
                "affected": self.affected, "dry_run": self.dry_run,
                "blocked": self.blocked}


_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_PSQL_CMD_TAG = re.compile(
    r"^(BEGIN|COMMIT|ROLLBACK|START TRANSACTION|SET|RESET|SAVEPOINT|RELEASE|"
    r"INSERT \d+ \d+|UPDATE \d+|DELETE \d+|CREATE TABLE|DROP TABLE)$")


class CampaignNodeStore:
    """战役节点状态回写（生产面 = 真实 PostgreSQL；测试可注入 ``exec_fn``）。

    ``mark_node_blocked`` 只把**非终态**节点改成 ``blocked``，返回真实受影响行数：
    ``affected=0`` 表示节点已 done/failed/blocked（终态不可被停滞处置覆盖）。
    """

    def __init__(self, *, exec_fn: Optional[Callable[[str, Dict[str, str]], List[List[str]]]] = None,
                 db: str = "tensorplatform", table: str = "campaign_nodes",
                 events_table: str = "campaign_events", plans_table: str = "campaign_plans",
                 timeout_s: int = 30):
        for ident in (table, events_table, plans_table):
            if not _IDENT.match(ident):
                raise ValueError(f"非法表名：{ident!r}")
        self.table, self.events_table, self.plans_table = table, events_table, plans_table
        self.db, self.timeout_s = db, timeout_s
        self._exec_fn = exec_fn or self._default_exec

    # -- 底层执行 -----------------------------------------------------------
    def _psql_prefix(self) -> List[str]:
        cmd = os.environ.get("TP_PSQL_CMD")
        if cmd:
            return cmd.split()
        psql = shutil.which("psql") or "psql"
        if os.geteuid() == 0:
            return ["runuser", "-u", "postgres", "--", psql]
        return [psql]

    def _default_exec(self, sql: str, variables: Dict[str, str]) -> List[List[str]]:
        argv = self._psql_prefix() + ["-d", self.db, "-t", "-A", "-F", "\x02", "-v", "ON_ERROR_STOP=1"]
        for k, v in variables.items():
            argv += ["-v", f"{k}={v}"]
        argv += ["-f", "-"]
        try:
            proc = subprocess.run(argv, input=sql, capture_output=True, text=True,
                                  timeout=self.timeout_s)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DAGError(f"psql 调用失败：{type(exc).__name__}: {exc}") from exc
        if proc.returncode != 0:
            raise DAGError(f"psql 退出码 {proc.returncode}: {proc.stderr.strip()[:400]}")
        rows: List[List[str]] = []
        for line in proc.stdout.splitlines():
            if not line.strip() or _PSQL_CMD_TAG.match(line.strip()):
                continue  # 事务控制/写入命令标签不是数据行（dry-run 用 BEGIN/ROLLBACK 包裹时必须滤掉）
            rows.append(line.split("\x02"))
        return rows

    def _exec(self, sql: str, variables: Optional[Dict[str, str]] = None) -> List[List[str]]:
        return self._exec_fn(sql, variables or {})

    # -- 读取面 -------------------------------------------------------------
    def get_node(self, node_id: int) -> Optional[Dict[str, Any]]:
        rows = self._exec(
            f"SELECT id, plan_id, mission_id, name, status, COALESCE(block_reason, ''), "
            f"consecutive_fails FROM {self.table} WHERE id = :'node_id';",
            {"node_id": str(int(node_id))})
        if not rows:
            return None
        r = rows[0] + [""] * (7 - len(rows[0]))
        return {"id": int(r[0]), "plan_id": int(r[1]) if r[1] else None,
                "mission_id": int(r[2]) if r[2] else None, "name": r[3], "status": r[4],
                "block_reason": r[5], "consecutive_fails": int(r[6] or 0)}

    def is_dispatch_eligible(self, node_id: int) -> Optional[bool]:
        """与调度器可派谓词同源：``status NOT IN ('done','failed','blocked')``。"""
        rows = self._exec(
            f"SELECT count(*) FILTER (WHERE status NOT IN ('done','failed','blocked')), "
            f"count(*) FROM {self.table} WHERE id = :'node_id';",
            {"node_id": str(int(node_id))})
        if not rows or not rows[0]:
            return None
        eligible, total = int(rows[0][0] or 0), int(rows[0][1] or 0)
        return None if total == 0 else eligible > 0

    # -- 写入面 -------------------------------------------------------------
    def mark_node_blocked(self, node_id: int, reason: str, *, dry_run: bool = False) -> BlockResult:
        text = ("stagnation: " + str(reason or "").strip())[:300]
        sql = (f"WITH before AS (SELECT id, status FROM {self.table} WHERE id = :'node_id'), "
               f"upd AS (UPDATE {self.table} SET status = 'blocked', block_reason = :'reason', "
               f"actual_end = COALESCE(actual_end, now()), updated_at = now() "
               f"WHERE id = :'node_id' AND status NOT IN ('done','failed','blocked') RETURNING id) "
               f"SELECT COALESCE((SELECT status FROM before), ''), (SELECT count(*) FROM upd);")
        if dry_run:
            sql = f"BEGIN;\n{sql}\nROLLBACK;"
        rows = self._exec(sql, {"node_id": str(int(node_id)), "reason": text})
        if not rows or len(rows[-1]) < 2:
            raise DAGError("mark_node_blocked 未返回受影响行数")
        previous, affected = rows[-1][0] or None, int(rows[-1][1] or 0)
        return BlockResult(node_id=int(node_id), reason=text, previous_status=previous,
                           new_status="blocked" if affected else previous,
                           affected=affected, dry_run=dry_run)

    def record_block_event(self, node_id: int, detail: Dict[str, Any],
                           *, plan_id: Optional[int] = None,
                           dry_run: bool = False) -> Optional[int]:
        """写 ``campaign_events(event_type='block_detected')``（既有 CHECK 枚举内合法值）。

        ``dry_run=True`` 时整条语句在事务内执行后回滚：可验证 CHECK/FK/JSON 合法性而不留痕。
        """
        payload = _canonical_json(detail)
        sql = (f"INSERT INTO {self.events_table} (organization_id, plan_id, event_type, detail) "
               f"SELECT p.organization_id, p.id, 'block_detected', :'detail'::jsonb "
               f"FROM {self.plans_table} p WHERE p.id = "
               f"COALESCE(NULLIF(:'plan_id', '')::bigint, "
               f"(SELECT plan_id FROM {self.table} WHERE id = :'node_id')) "
               f"RETURNING id;")
        if dry_run:
            sql = f"BEGIN;\n{sql}\nROLLBACK;"
        rows = self._exec(sql, {"node_id": str(int(node_id)), "detail": payload,
                                "plan_id": "" if plan_id is None else str(int(plan_id))})
        if not rows or not rows[0]:
            raise DAGError("campaign_events 未返回事件 id（plan_id 缺失？）")
        return int(rows[0][0])


# ── 编排器：采集 → 判定 → 早停 → DAG 回写 ───────────────────────────────────
class ProgressOrchestrator:
    """把四段机制串成一拍 tick，供调度器 / 看门狗 / CLI 调用。

    幂等与续跑：每拍快照落 :class:`ProgressJournal`；``resume=True`` 时先重放日志再继续，
    检测器本身无内部状态（纯函数），因此重放结果与在线结果逐字一致。
    """

    def __init__(self, worktree: Path, *, node_id: Optional[int] = None,
                 thresholds: Optional[ProgressThresholds] = None,
                 output_dir: Optional[Path] = None,
                 journal_path: Optional[Path] = None,
                 dag_store: Optional[CampaignNodeStore] = None,
                 mission_id: Optional[int] = None,
                 pytest_target: str = "tests",
                 block_on_early_stop: bool = False,
                 resume: bool = False):
        self.worktree = Path(worktree)
        self.node_id = node_id
        self.th = thresholds or ProgressThresholds()
        self.output_dir = Path(output_dir) if output_dir else (self.worktree / "runs" / "progress")
        self.journal = ProgressJournal(journal_path or (self.output_dir / "snapshots.jsonl"))
        self.tracker = ProgressTracker(self.worktree, node_id, self.th, pytest_target,
                                       exclude_paths=(self.output_dir,))
        self.detector = StagnationDetector(self.th)
        self.early_stop = EarlyStopHandler(self.worktree, self.th, self.output_dir)
        self.dag = dag_store
        self.mission_id = mission_id
        self.block_on_early_stop = block_on_early_stop
        self._started = _utcnow()
        self._ticks = 0
        self._last_result: Optional[Dict[str, Any]] = None
        if resume:
            self.resume_from_journal()

    # -- 续跑 ---------------------------------------------------------------
    def resume_from_journal(self) -> int:
        records = self.journal.load()
        snaps = [ProgressSnapshot.from_dict(r) for r in records
                 if r.get("schema") == SCHEMA_SNAPSHOT]
        self.tracker.restore(snaps)
        self._ticks = len(records)
        return len(snaps)

    # -- 一拍 ---------------------------------------------------------------
    def tick(self, *, run_pytest: bool = False,
             pytest_result: Optional[Dict[str, Any]] = None,
             criteria: Optional[Dict[str, bool]] = None,
             external_signals: Optional[Path] = None,
             ts: Optional[datetime] = None,
             runtime_s: Optional[float] = None) -> Dict[str, Any]:
        snapshot = self.tracker.take_snapshot(run_pytest=run_pytest, pytest_result=pytest_result,
                                              criteria=criteria, ts=ts)
        ingest = ExternalSignalAdapter().load(external_signals) if external_signals else None
        if ingest is not None:
            ProgressTracker.attach_external(snapshot, ingest)
        self.journal.append(snapshot.as_dict())

        verdict = self.detector.analyze(self.tracker.history)
        runtime = (runtime_s if runtime_s is not None
                   else (_utcnow() - self._started).total_seconds())
        action = verdict.recommendation
        result: Dict[str, Any] = {
            "snapshot": snapshot.as_dict(),
            "verdict": verdict.as_dict(),
            "action": action,
            "runtime_s": round(runtime, 3),
            "journal": str(self.journal.path),
            "dag": None,
        }
        if action == ACTION_EARLY_STOP and self.detector.should_early_stop(verdict, runtime):
            report = self.early_stop.generate_report(
                node_id=self.node_id, history=self.tracker.history, verdict=verdict,
                mission_id=self.mission_id)
            result["early_stop_report"] = report.as_dict()
            if self.dag is not None and self.node_id is not None and self.block_on_early_stop:
                result["dag"] = self._push_dag_block(report)
        self._ticks += 1
        self._last_result = result
        return result

    def _push_dag_block(self, report: EarlyStopReport) -> Dict[str, Any]:
        assert self.dag is not None and self.node_id is not None
        try:
            block = self.dag.mark_node_blocked(self.node_id, report.reason)
            event_id = self.dag.record_block_event(
                self.node_id,
                {"source": "progress_metrics", "reason": report.reason,
                 "report_path": report.report_path, "report_sha256": report.report_sha256,
                 "consecutive_no_progress_cycles":
                     report.stagnation_evidence.get("consecutive_no_progress_cycles"),
                 "slope": report.stagnation_evidence.get("slope")})
            return {"block": block.as_dict(), "event_id": event_id,
                    "dispatch_eligible_after": self.dag.is_dispatch_eligible(self.node_id)}
        except DAGError as exc:
            # fail closed：DAG 回写失败必须显式上报，绝不静默吞掉
            return {"error": f"{type(exc).__name__}: {exc}", "block": None, "event_id": None}

    # -- 汇总 ---------------------------------------------------------------
    def summary(self) -> Dict[str, Any]:
        hist = self.tracker.history
        if not hist:
            return {"ticks": self._ticks, "status": "no_data"}
        last = hist[-1]
        verdict = self.detector.analyze(hist)
        return {
            "ticks": self._ticks,
            "node_id": self.node_id,
            "snapshots": len(hist),
            "latest_composite": round(last.composite_score, 6),
            "latest_info_gain": round(last.info_gain, 6),
            "latest_change_fraction": round(last.change_fraction, 9),
            "latest_pytest_pass_rate": round(last.pytest_pass_rate, 4),
            "latest_criteria_pass_rate": round(last.criteria_pass_rate, 4),
            "consecutive_no_progress_cycles": verdict.consecutive_no_progress_cycles,
            "slope": round(verdict.slope, 6),
            "recommendation": verdict.recommendation,
            "journal": str(self.journal.path),
            "journal_ok": self.journal.verify()[0],
        }


# ── CLI ─────────────────────────────────────────────────────────────────────
def _load_criteria(path: Optional[str]) -> Optional[Dict[str, bool]]:
    if not path:
        return None
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "criteria" in raw and isinstance(raw["criteria"], dict):
        raw = raw["criteria"]
    if not isinstance(raw, dict):
        raise ValueError("criteria JSON 必须是 {name: bool} 或 {criteria: {name: bool}}")
    return {str(k): bool(v) for k, v in raw.items()}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="progress_metrics —— 节点级进展度量与停滞处置")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--worktree", default=".", help="被测工作树根（默认当前目录）")
        sp.add_argument("--node-id", type=int, default=None, help="campaign_nodes.id")
        sp.add_argument("--mission-id", type=int, default=None)
        sp.add_argument("--out", default=None, help="输出目录（默认 <worktree>/runs/progress）")
        sp.add_argument("--journal", default=None, help="快照日志路径（默认 <out>/snapshots.jsonl）")
        sp.add_argument("--pytest-target", default="tests")
        sp.add_argument("--run-pytest", action="store_true", help="本拍实跑 pytest（慢）")
        sp.add_argument("--criteria-json", default=None, help="判据通过情况 JSON {name: bool}")
        sp.add_argument("--external-signals", default=None, help="外部信号 JSONL（上游适配层）")
        sp.add_argument("--dag-block", action="store_true", help="早停后回写 campaign_nodes（blocked）")
        sp.add_argument("--dag-dry-run", action="store_true", help="DAG 回写只演练（事务回滚）")
        sp.add_argument("--json", action="store_true", help="stdout 输出 JSON")

    tick = sub.add_parser("tick", help="采集并判定一拍")
    common(tick)
    tick.add_argument("--resume", action="store_true", help="先重放既有日志再拍")

    watch = sub.add_parser("watch", help="周期观测，早停即退出")
    common(watch)
    watch.add_argument("--interval", type=float, default=300.0, help="拍间隔秒")
    watch.add_argument("--max-cycles", type=int, default=12)
    watch.add_argument("--resume", action="store_true")
    watch.add_argument("--dry-run-interval", action="store_true",
                       help="不真睡（离线压测/CI 用），仅按拍推进")

    replay = sub.add_parser("replay", help="校验并重放快照日志")
    replay.add_argument("--journal", required=True)
    replay.add_argument("--json", action="store_true")
    return p


def _make_dag(args: argparse.Namespace, block: bool) -> Optional[CampaignNodeStore]:
    if not block or args.node_id is None:
        return None
    store = CampaignNodeStore()
    if args.dag_dry_run:
        # 演练：真实事务内 UPDATE 后回滚，拿到真实受影响行数但不留痕
        real = store.mark_node_blocked
        def preview(node_id: int, reason: str, *, dry_run: bool = False) -> BlockResult:
            return real(node_id, reason, dry_run=True)
        store.mark_node_blocked = preview  # type: ignore[assignment]
    return store


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "replay":
        journal = ProgressJournal(Path(args.journal))
        ok, problems = journal.verify()
        records = journal.load(verify=False)
        snapshots = [ProgressSnapshot.from_dict(r) for r in records if r.get("schema") == SCHEMA_SNAPSHOT]
        verdict = StagnationDetector().analyze(snapshots).as_dict()
        payload = {"journal": str(journal), "chain_ok": ok, "problems": problems,
                   "snapshots": len(snapshots), "verdict": verdict}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"journal={journal} chain_ok={ok} snapshots={len(snapshots)} "
                  f"verdict={verdict['recommendation']} {verdict['reason']}")
        return 0 if ok else 1

    try:
        criteria = _load_criteria(args.criteria_json)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR criteria: {exc}", file=sys.stderr)
        return 1

    out_dir = Path(args.out) if args.out else Path(args.worktree) / "runs" / "progress"
    orch = ProgressOrchestrator(
        Path(args.worktree), node_id=args.node_id, output_dir=out_dir,
        journal_path=Path(args.journal) if args.journal else None,
        dag_store=_make_dag(args, args.dag_block), mission_id=args.mission_id,
        pytest_target=args.pytest_target, block_on_early_stop=args.dag_block,
        resume=getattr(args, "resume", False),
    )

    def one() -> Dict[str, Any]:
        res = orch.tick(run_pytest=args.run_pytest, criteria=criteria,
                        external_signals=Path(args.external_signals) if args.external_signals else None)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            v = res["verdict"]
            print(f"[cycle {res['snapshot']['cycle']}] composite={res['snapshot']['composite_score']:.6f} "
                  f"gain={res['snapshot']['info_gain']:.6f} change={res['snapshot']['change_fraction']:.6f} "
                  f"verdict={v['recommendation']} ({v['reason']})")
            if res.get("early_stop_report"):
                r = res["early_stop_report"]
                print(f"EARLY STOP honest_fail=True report={r['report_path']} sha256={r['report_sha256']}")
                for alt in r["alternative_paths"][:3]:
                    print(f"  → {alt['strategy']} (confidence={alt['confidence']}): {alt['description']}")
            if res.get("dag"):
                print(f"  DAG: {json.dumps(res['dag'], ensure_ascii=False)}")
        return res

    if args.command == "tick":
        res = one()
        return 2 if res.get("early_stop_report") else 0

    # watch
    cycles = 0
    while cycles < args.max_cycles:
        res = one()
        cycles += 1
        if res.get("early_stop_report"):
            return 2
        if cycles < args.max_cycles and not args.dry_run_interval and args.interval > 0:
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
