"""样本构造：特征在 mission 完结时刻可观测，标签在其后的下游事件发生。

**时间轴纪律（本模块的全部意义）**

```
created_at < finished_at < t_label
   |             |            |
   |             |            +-- 下游真实结果：PR merged / closed / open(超时) /
   |             |                custody_requeue（返工）/ failed|stopped|revoked|orphan（丢弃）
   |             +--------------- 特征快照时刻（tool_calls / compactions / checks /
   |                              result 里的 S×E 判级 —— 全部是 mission 自身产出的确定性函数）
   +----------------------------- 建单时刻（goal 复杂度、预算）
```

平台上 S×E 判级行的 `decided_at` 几乎全部晚于下游事件（评价波次 2026-11-07 回填），
所以**不能**用 `decided_at` 当特征时刻——那是后视镜。本模块一律用 `finished_at`
作为特征时刻，并强制 `t_label > finished_at`，否则该行整条剔除并计入 `excluded`。

标签口径（二分类，``y=1`` 交付被下游整合）：

============================  ============================================  =====
最终 PR 状态 / 治理事件        语义                                          y
============================  ============================================  =====
``merged``                    PR 已被合并（下游整合）                        1
``closed``                    PR 被关闭/弃用（下游丢弃）                     0
``open`` 且开满 ≥ horizon     超过观察窗口仍未合并（未整合 / 待返工）        0
终态 ∈ failed/stopped/…       任务失败、停止、撤销、孤儿（丢弃）             0
``custody_requeue`` 且无 PR   托管返工（返工）                              0
其余（open 未满窗口 / 无任何下游事件）  结果未定 → 剔除，记 WITHHELD        —
============================  ============================================  =====
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = "extval.v1"

FAIL_TERMINAL = ("failed", "stopped", "revoked", "orphan")
GOVERNANCE_NEGATIVE_ACTIONS = ("reclaim", "orphan_detect", "write_off", "suspend")

#: 特征名（顺序即模型输入顺序，改动须同步 SCHEMA_VERSION）
FEATURE_NAMES: tuple[str, ...] = (
    "s_ord",           # S1..S5 -> 1..5，S-N -> 0（新颖性分支）
    "e_ord",           # E0..E4 -> 0..4
    "evidence_n",      # 证据引用条数
    "reason_codes_n",  # 判据命中码条数
    "goal_len",        # goal 字符数 /100
    "goal_kw",         # goal 中的复杂度关键词命中数
    "tool_calls",      # 工具调用次数
    "compactions",     # 上下文压缩次数
    "changed_files_n",  # 落盘改动文件数
    "checks_len",      # 自检文本长度 /1000
    "has_fail_stage",  # result.fail_stage 非空
    "accepted",        # result.accepted 为真
    "git_dirty",       # result.git_status 含未提交/未跟踪改动
    "duration_h",      # finished_at - created_at（小时）
    "budget_tool_calls",  # 预算工具调用上限
    "summary_len",     # declared_summary 长度 /1000
)

COMPLEXITY_KEYWORDS = (
    "验证", "verif", "pytest", "benchmark", "基准", "对照", "校准", "收敛",
    "网格", "并行", "集群", "论文", "实验", "设计", "重构", "审计",
)


@dataclass
class Sample:
    mission_id: int
    features: dict[str, float]
    y: int
    t_features: str      # finished_at (ISO8601, UTC)
    t_label: str         # 下游标签事件时刻
    label_kind: str      # merged / closed / open_timeout / status_fail / requeue
    grade: str
    e_level: str
    repo: str
    grade_decided_at: str | None = None


@dataclass
class Dataset:
    samples: list[Sample] = field(default_factory=list)
    excluded: list[dict[str, Any]] = field(default_factory=list)
    as_of: str = ""
    horizon_hours: float = 48.0

    # ---- 便捷视图 -------------------------------------------------------
    @property
    def n(self) -> int:
        return len(self.samples)

    @property
    def n_positive(self) -> int:
        return sum(s.y for s in self.samples)

    @property
    def excluded_counts(self) -> dict[str, int]:
        return _counts(e["reason"] for e in self.excluded)

    def matrix(self) -> tuple[list[list[float]], list[int], list[str]]:
        X = [[s.features[k] for k in FEATURE_NAMES] for s in self.samples]
        y = [s.y for s in self.samples]
        t = [s.t_features for s in self.samples]
        return X, y, t

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "as_of": self.as_of,
            "horizon_hours": self.horizon_hours,
            "feature_names": list(FEATURE_NAMES),
            "n": self.n,
            "n_positive": self.n_positive,
            "positive_rate": (self.n_positive / self.n) if self.n else None,
            "label_kind_counts": _counts(s.label_kind for s in self.samples),
            "grade_counts": _counts(s.grade for s in self.samples),
            "e_level_counts": _counts(s.e_level for s in self.samples),
            "repo_counts": _counts(s.repo for s in self.samples),
            "excluded_counts": self.excluded_counts,
            "excluded": self.excluded,
            "samples": [
                {
                    "mission_id": s.mission_id,
                    "y": s.y,
                    "t_features": s.t_features,
                    "t_label": s.t_label,
                    "label_kind": s.label_kind,
                    "grade": s.grade,
                    "e_level": s.e_level,
                    "repo": s.repo,
                    "grade_decided_at": s.grade_decided_at,
                    "features": s.features,
                }
                for s in self.samples
            ],
        }


def _counts(values: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _parse_ts(value: Any) -> _dt.datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, _dt.datetime):
        ts = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            ts = _dt.datetime.fromisoformat(text)
        except ValueError:
            return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_dt.timezone.utc)
    return ts.astimezone(_dt.timezone.utc)


def _iso(ts: _dt.datetime | None) -> str | None:
    return ts.isoformat() if ts else None


def _json_load(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool):
            return default if value is None else float(value)
        return float(value)
    except (TypeError, ValueError):
        return default


def _s_ordinal(grade: str | None) -> float:
    if not grade:
        return 0.0
    if grade == "S-N":
        return 0.0
    try:
        return float(grade[1:])
    except (IndexError, ValueError):
        return 0.0


def _e_ordinal(e_level: str | None) -> float:
    if not e_level:
        return 0.0
    try:
        return float(e_level[1:])
    except (IndexError, ValueError):
        return 0.0


def build_features(mission: Mapping[str, Any], grade_row: Mapping[str, Any]) -> dict[str, float]:
    """特征只取 mission 完结时已存在于 ``result`` 与评价行里的量（确定性函数）。"""
    result = _json_load(mission.get("result"), {})
    evidence = _json_load(grade_row.get("evidence_refs"), [])
    codes = _json_load(grade_row.get("reason_codes"), [])
    goal = str(mission.get("goal") or "")
    goal_low = goal.lower()
    changed = result.get("changed_files") or []
    if isinstance(changed, str):
        changed = [c for c in changed.splitlines() if c.strip()]
    budget = _json_load(mission.get("budget"), {})
    created = _parse_ts(mission.get("created_at"))
    finished = _parse_ts(mission.get("finished_at"))
    duration_h = 0.0
    if created and finished:
        duration_h = max(0.0, (finished - created).total_seconds() / 3600.0)
    git_status = str(result.get("git_status") or "")
    dirty = any(tok in git_status for tok in ("??", " M ", "AM", "UU", "D "))
    checks = result.get("checks")
    checks_text = json.dumps(checks, ensure_ascii=False) if not isinstance(checks, str) else checks
    summary = str(result.get("declared_summary") or "")
    return {
        "s_ord": _s_ordinal(grade_row.get("grade")),
        "e_ord": _e_ordinal(grade_row.get("e_level")),
        "evidence_n": float(len(evidence) if isinstance(evidence, list) else 0),
        "reason_codes_n": float(len(codes) if isinstance(codes, list) else 0),
        "goal_len": len(goal) / 100.0,
        "goal_kw": float(sum(1 for kw in COMPLEXITY_KEYWORDS if kw in goal_low)),
        "tool_calls": _num(result.get("tool_calls")),
        "compactions": _num(result.get("compactions")),
        "changed_files_n": float(len(changed) if isinstance(changed, list) else 0),
        "checks_len": len(checks_text) / 1000.0,
        "has_fail_stage": 1.0 if result.get("fail_stage") else 0.0,
        "accepted": 1.0 if result.get("accepted") is True else 0.0,
        "git_dirty": 1.0 if dirty else 0.0,
        "duration_h": duration_h,
        "budget_tool_calls": _num(budget.get("max_tool_calls")),
        "summary_len": len(summary) / 1000.0,
    }


def derive_samples(
    missions: Sequence[Mapping[str, Any]],
    grade_rows: Sequence[Mapping[str, Any]],
    lifecycle: Sequence[Mapping[str, Any]],
    as_of: str | _dt.datetime,
    horizon_hours: float = 48.0,
) -> Dataset:
    """纯函数：原始行 -> 样本表。无数据库依赖，便于单测。

    ``missions`` 每行需含 id/status/goal/repo/budget/result/created_at/finished_at；
    ``grade_rows`` 每行需含 mission_id/grade/e_level/evidence_refs/reason_codes/decided_at；
    ``lifecycle`` 每行需含 mission_id/action/detail/created_at。
    """
    as_of_ts = _parse_ts(as_of)
    if as_of_ts is None:
        raise ValueError("as_of must be an ISO8601 timestamp")
    horizon = _dt.timedelta(hours=float(horizon_hours))

    grades: dict[int, Mapping[str, Any]] = {}
    for row in grade_rows:
        mid = row.get("mission_id")
        if mid is None or not row.get("grade"):
            continue
        mid = int(mid)
        # 同一 mission 多行（含 rejected 负例留痕）时优先 confirmed/candidate
        prev = grades.get(mid)
        if prev is None or (prev.get("status") == "rejected" and row.get("status") != "rejected"):
            grades[mid] = row

    pr_events: dict[int, list[tuple[_dt.datetime, str, dict]]] = {}
    requeue_at: dict[int, _dt.datetime] = {}
    gov_at: dict[int, _dt.datetime] = {}
    for row in lifecycle:
        mid = row.get("mission_id")
        ts = _parse_ts(row.get("created_at"))
        if mid is None or ts is None:
            continue
        mid = int(mid)
        action = row.get("action")
        detail = _json_load(row.get("detail"), {})
        if action == "pr_gate":
            state = str(detail.get("state") or "").lower()
            pr_events.setdefault(mid, []).append((ts, state, detail))
        elif action == "custody_requeue":
            requeue_at[mid] = min(ts, requeue_at.get(mid, ts))
        elif action in GOVERNANCE_NEGATIVE_ACTIONS:
            gov_at[mid] = min(ts, gov_at.get(mid, ts))

    ds = Dataset(as_of=_iso(as_of_ts) or "", horizon_hours=float(horizon_hours))

    for mission in missions:
        mid_raw = mission.get("id")
        if mid_raw is None:
            continue
        mid = int(mid_raw)
        grade_row = grades.get(mid)
        if grade_row is None:
            continue  # 未判级：不在本分析队列

        finished = _parse_ts(mission.get("finished_at"))
        created = _parse_ts(mission.get("created_at"))
        if finished is None or created is None:
            ds.excluded.append({"mission_id": mid, "reason": "no_finished_at"})
            continue
        if finished > as_of_ts:
            ds.excluded.append({"mission_id": mid, "reason": "finished_after_as_of"})
            continue

        events = sorted(pr_events.get(mid, []), key=lambda e: e[0])
        final_state = events[-1][1] if events else ""
        next_state_change = None
        if final_state == "open":
            # PR 首次进入 open 且之后无状态变化 -> 用最后一次 open 事件计时
            next_state_change = events[-1][0]

        y: int | None = None
        t_label: _dt.datetime | None = None
        kind = ""
        if final_state == "merged":
            merged_at = min(ts for ts, st, _ in events if st == "merged")
            y, t_label, kind = 1, merged_at, "merged"
        elif final_state == "closed":
            y, t_label, kind = 0, events[-1][0], "closed"
        elif final_state == "open":
            if as_of_ts - next_state_change >= horizon:
                y = 0
                t_label = next_state_change + horizon
                kind = "open_timeout"
        if y is None and str(mission.get("status")) in FAIL_TERMINAL:
            y, t_label, kind = 0, finished + _dt.timedelta(seconds=1), "status_fail"
        if y is None and mid in gov_at:
            y, t_label, kind = 0, gov_at[mid], "governance"
        if y is None and mid in requeue_at:
            y, t_label, kind = 0, requeue_at[mid], "requeue"

        if y is None:
            reason = "pr_open_within_horizon" if final_state == "open" else "no_downstream_event"
            ds.excluded.append({"mission_id": mid, "reason": reason})
            continue
        # 删失闸门：标签观察时刻晚于 as_of -> 该样本在本次快照里结果未定
        if t_label is not None and t_label > as_of_ts:
            ds.excluded.append({"mission_id": mid, "reason": "label_censored_at_as_of"})
            continue
        # 泄漏闸门：标签必须严格晚于特征时刻
        if t_label is None or t_label <= finished:
            ds.excluded.append({"mission_id": mid, "reason": "label_not_after_features"})
            continue

        ds.samples.append(
            Sample(
                mission_id=mid,
                features=build_features(mission, grade_row),
                y=y,
                t_features=_iso(finished) or "",
                t_label=_iso(t_label) or "",
                label_kind=kind,
                grade=str(grade_row.get("grade")),
                e_level=str(grade_row.get("e_level")),
                repo=str(mission.get("repo") or ""),
                grade_decided_at=_iso(_parse_ts(grade_row.get("decided_at"))),
            )
        )

    ds.samples.sort(key=lambda s: (s.t_features, s.mission_id))
    return ds


# --------------------------------------------------------------------------
# 数据库只读抓取（两段查询，join 在 Python 里做，便于把派生逻辑当纯函数测试）
# --------------------------------------------------------------------------

MISSIONS_SQL = """
SELECT m.id, m.status, m.goal, m.repo, m.budget, m.result,
       m.created_at, m.finished_at
  FROM tensor_dev_missions m
 WHERE m.finished_at IS NOT NULL
   AND m.finished_at <= %(as_of)s
   AND EXISTS (
        SELECT 1 FROM outcome_evaluations g
         WHERE g.mission_id = m.id AND g.grade IS NOT NULL)
 ORDER BY m.finished_at, m.id
"""

GRADES_SQL = """
SELECT mission_id, grade, e_level, evidence_refs, reason_codes,
       status, reviewer_kind, reason_source, decided_at
  FROM outcome_evaluations
 WHERE mission_id IS NOT NULL AND grade IS NOT NULL
"""

LIFECYCLE_SQL = """
SELECT mission_id, action, detail, created_at
  FROM mission_lifecycle_log
 WHERE action IN ('pr_gate','custody_requeue','reclaim','orphan_detect',
                  'write_off','suspend')
"""


def fetch_raw(dsn: str, as_of: str | _dt.datetime) -> tuple[list[dict], list[dict], list[dict]]:
    """只读抓取；返回 (missions, grade_rows, lifecycle)。"""
    import psycopg
    from psycopg.rows import dict_row

    as_of_ts = _parse_ts(as_of)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(MISSIONS_SQL, {"as_of": as_of_ts})
            missions = [dict(r) for r in cur.fetchall()]
            cur.execute(GRADES_SQL)
            grades = [dict(r) for r in cur.fetchall()]
            cur.execute(LIFECYCLE_SQL)
            lifecycle = [dict(r) for r in cur.fetchall()]
    return missions, grades, lifecycle


def build_dataset_from_db(
    dsn: str, as_of: str | _dt.datetime, horizon_hours: float = 48.0
) -> Dataset:
    missions, grades, lifecycle = fetch_raw(dsn, as_of)
    return derive_samples(missions, grades, lifecycle, as_of, horizon_hours)


def write_dataset(ds: Dataset, path: str) -> None:
    import os

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(ds.to_json(), fh, ensure_ascii=False, indent=2)