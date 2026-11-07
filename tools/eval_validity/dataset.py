"""只读数据抽取：把「评价判级」与「下游真实结果」拼成 ``EvalRecord``。

三路下游证据（都只读，不改任何状态）：

1. **PR 闸历史**（``tensor_dev_missions.result->'pr_gate'->'history'``）：
   ``merged`` → merge；``merged`` 之后又 ``closed`` → rollback；
   ``closed`` 未合并 → discard；``open`` → 尚未可观测（censored）。
   仅对装了 PR 闸的仓（TensorLBM/TensorDEM/...）有效——``ship-performance-platform``
   是本地私有仓、直推 master，**没有任何 pr_gate**，必须换证据源。
2. **生命周期事件**（``mission_lifecycle_log``）：``custody_requeue`` → rework，
   ``orphan_detect`` → discard，``write_off`` → discard。
3. **船仓合并台账**（``ship_merge_ledger.jsonl``，由 ship_merge.py 追加写）：
   ``merged`` → merge；``held``（分支独有测试红门）→ rework；
   ``conflict`` → rework；``worktree_add_failed`` 无 result → 排除。

标签口径（二分类，1 = 下游好结果）：

- ``merge``   → 1
- ``rework``  → 0
- ``rollback``→ 0
- ``discard`` → 0
- 无任何可观测终局 → **censored，剔除**（不是 0，也不是 1；缺失不许伪装成负例）。

``psql`` 调用前有 SQL 白名单（只允许单条 SELECT/WITH），防止只读工具被复用成写通道。
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[2]

#: 下游终局类别（任务书口径）
OUTCOME_MERGE = "merge"
OUTCOME_ROLLBACK = "rollback"
OUTCOME_REWORK = "rework"
OUTCOME_DISCARD = "discard"
OUTCOME_CENSORED = "censored"

POSITIVE_OUTCOMES = (OUTCOME_MERGE,)
NEGATIVE_OUTCOMES = (OUTCOME_ROLLBACK, OUTCOME_REWORK, OUTCOME_DISCARD)

DEFAULT_SHIP_LEDGER = Path("/root/tp-agent/ship_merge_ledger.jsonl")


@dataclass
class EvalRecord:
    """一条「判级 → 下游结果」观测。"""

    mission_id: int
    repo: str
    grade: str | None          # S1..S5 / S-N
    e_level: str | None        # E0..E4
    eval_status: str           # confirmed / candidate / rejected
    decided_at: float          # epoch seconds（评价落定时刻，walk-forward 排序键）
    outcome: str               # merge/rollback/rework/discard/censored
    evidence: str              # 证据来源：pr_gate / lifecycle / ship_ledger
    mission_status: str | None = None
    finished_at: float | None = None   # 任务完成时刻（walk-forward 的时间轴）
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ref_time(self) -> float:
        """walk-forward 排序键：任务完成时刻优先，缺失才退回评价落定时刻。

        为什么不是 ``decided_at``：平台的待评价记录是**成批回填**的，同一波里
        几百条评价的 ``decided_at`` 挤在几小时内，而它们描述的任务横跨十天。
        用回填时刻排序等于把历史压成一瞬，时间窗就退化成随机切分（实测
        AUC 会随「哪一波先回填」乱跳）。用任务完成时刻才是真实时间序。
        """
        return self.finished_at if self.finished_at is not None else self.decided_at

    @property
    def y(self) -> int | None:
        if self.outcome in POSITIVE_OUTCOMES:
            return 1
        if self.outcome in NEGATIVE_OUTCOMES:
            return 0
        return None

    @property
    def labeled(self) -> bool:
        return self.y is not None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["y"] = self.y
        d["labeled"] = self.labeled
        return d


# ───────────────────────────── 只读 psql ─────────────────────────────

_SELECT_ONLY = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|truncate|alter|create|grant|revoke|copy|"
    r"vacuum|call|do|merge)\b", re.IGNORECASE)


def assert_read_only(sql: str) -> str:
    """SQL 只读白名单：单条 SELECT/WITH，且不含任何写/DDL 关键字。

    分号多条语句一律拒绝（防止 ``select 1; drop table ...`` 这类夹带）。
    """
    if not _SELECT_ONLY.match(sql):
        raise ValueError("只允许 SELECT/WITH 只读查询")
    if sql.strip().rstrip(";").count(";"):
        raise ValueError("只允许单条语句（分号多条被拒）")
    if _FORBIDDEN.search(sql):
        raise ValueError("SQL 含写/DDL 关键字，拒绝执行")
    return sql


def psql_rows(sql: str, dsn_cmd: str = "psql -U foundation -d tensorplatform",
              timeout: int = 60) -> list[list[str]]:
    """执行只读 SQL，返回 ``|`` 分隔的未解析行（调用方自己 split）。"""
    assert_read_only(sql)
    proc = subprocess.run(dsn_cmd.split() + ["-tAc", sql],
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "").strip()[:400])
    return [ln.split("|") for ln in proc.stdout.splitlines() if ln.strip()]


# ───────────────────────────── 证据解析 ─────────────────────────────

def outcome_from_pr_history(history: Iterable[dict],
                            as_of: float | None = None,
                            horizon_hours: float = 24.0) -> str:
    """PR 闸历史 → 终局类别（带观测期）。

    - ``merged`` 后又出现 ``closed`` → rollback（合并被撤销）；
    - 结尾 ``merged`` → merge；
    - 结尾 ``closed``（未合并）→ discard；
    - 只有 ``open``：超过 ``horizon_hours`` 仍未合并 → discard（unlanded timeout，
      即「该落地的落不了地」），未超期 → censored（还没定局，不许当负例）。

    观测期是显式参数而不是拍脑袋：不给观测期就会把「昨天下单、今天还没合并」
    错判成失败，那是把时间混进标签里。
    """
    hist = [h for h in (history or []) if isinstance(h, dict)]
    states = [str(h.get("state") or "") for h in hist]
    if not states:
        return OUTCOME_CENSORED
    if "closed" in states:
        first_closed = states.index("closed")
        return OUTCOME_ROLLBACK if "merged" in states[:first_closed] else OUTCOME_DISCARD
    if "merged" in states:
        return OUTCOME_MERGE
    if "open" in states:
        last_at = None
        for h in reversed(hist):
            last_at = _epoch(h.get("at"))
            if last_at is not None:
                break
        if as_of is not None and last_at is not None and \
                (as_of - last_at) >= horizon_hours * 3600.0:
            return OUTCOME_DISCARD
        return OUTCOME_CENSORED
    return OUTCOME_CENSORED


def load_ship_ledger(path: str | Path | None = None) -> dict[int, str]:
    """读船仓合并台账 → ``{mission_id: outcome}``。

    分支名形如 ``agent/m544``；``result`` 缺失（如 worktree_add_failed）的条目
    无法判定，直接跳过（诚实留白，不猜）。
    """
    p = Path(path) if path else DEFAULT_SHIP_LEDGER
    if not p.exists():
        return {}
    mapping: dict[int, str] = {}
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            e = json.loads(ln)
        except json.JSONDecodeError:
            continue
        br = e.get("branch") or ""
        m = re.search(r"m(\d+)", br)
        res = e.get("result")
        if not m or res not in ("merged", "held", "conflict"):
            continue
        outcome = {
            "merged": OUTCOME_MERGE,
            "held": OUTCOME_REWORK,      # 分支独有测试红门 → 必须返工
            "conflict": OUTCOME_REWORK,  # 合并冲突 → 必须重做
        }[res]
        mapping[int(m.group(1))] = outcome
    return mapping


#: 落账（landing）台账里代表「真进 trunk」的动作
MERGE_ACTIONS = frozenset({"merged", "merged_newred_pass", "redev_merged"})

#: 代表「被闸门挡住/需要返工」的动作（红测试、冲突、仪式失败、返工派单）
ADVERSE_ACTIONS = frozenset({
    "checks_hold", "merge_failed", "mainfix_dispatched", "fix_dispatched",
    "redev_dispatched", "redev_hollow_round", "red_cap_redev", "arb_redev_closed",
    "redev_hold_human", "heal_dispatched", "heal_hold_no_landing",
    "custody_requeued", "artifact_hold", "ratchet_freeze",
})

#: 只有「排队/等干净/已开 PR」等中间态，不构成终局 → 仍属 censored
PENDING_ACTIONS = frozenset({
    "dangling_pr_opened", "order_hold", "dirty_wait", "custody_deferred",
    "custody_active", "alert_harness", "arb_superset_rebuild",
})


def load_landing_ledger(paths: Sequence[str | Path]) -> dict[tuple[str, int], dict]:
    """读落账台账（merge_master / merge_governor 的 ``events`` 数组）。

    GitHub 侧 11 仓的 PR 落账**本地**有权威流水：每次 merge / 红门 / 返工派单
    都追加一条 ``{repo, mid, action, ts}``。比 ``pr_gate`` 回填更完整、且天然带
    时间戳——能区分「先合并后回归」这类**回退/返工**，这是 `merged` 布尔量做不到的。

    返回 ``{(repo, mission_id): {"merge_ts":.., "adverse_ts":.., "actions":[...]}}``。
    """
    acc: dict[tuple[str, int], dict] = {}
    for path in paths:
        p = Path(path)
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        events = data.get("events") if isinstance(data, dict) else data
        for e in events or []:
            if not isinstance(e, dict):
                continue
            repo = e.get("repo")
            mid_raw = e.get("mid")
            action = e.get("action")
            if not repo or mid_raw is None or not action:
                continue
            try:
                mid = int(str(mid_raw))
            except ValueError:
                continue
            ts = _epoch(e.get("ts"))
            key = (str(repo), mid)
            slot = acc.setdefault(key, {"merge_ts": None, "adverse_ts": None,
                                        "actions": [], "sources": []})
            slot["actions"].append({"action": action, "ts": ts,
                                    "pr": e.get("pr")})
            slot["sources"].append(p.name)
            if action in MERGE_ACTIONS and ts is not None:
                slot["merge_ts"] = ts if slot["merge_ts"] is None else min(slot["merge_ts"], ts)
            elif action in ADVERSE_ACTIONS and ts is not None:
                slot["adverse_ts"] = ts if slot["adverse_ts"] is None else max(slot["adverse_ts"], ts)
    return acc


def outcome_from_landing(entry: dict | None) -> tuple[str, str]:
    """落账台账 → (终局类别, 说明)。

    - 有过 merge、且 merge 之后没有返工证据 → ``merge``；
    - 有过 merge、但之后出现红门/返工派单 → ``rework``（合并后回归 = 回退形态）；
    - 从未 merge、但有红门/返工证据 → ``rework``（闸门红，必须返工）；
    - 只有排队/开关 PR 等中间态 → ``censored``。
    """
    if not entry:
        return OUTCOME_CENSORED, "no_ledger_entry"
    merge_ts, adverse_ts = entry.get("merge_ts"), entry.get("adverse_ts")
    if merge_ts is not None and adverse_ts is not None and adverse_ts > merge_ts:
        return OUTCOME_REWORK, "post_merge_regression"
    if merge_ts is not None:
        return OUTCOME_MERGE, "landed"
    if adverse_ts is not None:
        return OUTCOME_REWORK, "gate_red"
    return OUTCOME_CENSORED, "pending_only"


def _epoch(ts: str | None) -> float | None:
    if not ts:
        return None
    s = ts.strip().replace(" ", "T")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# ─────────────────────────── 主抽取 SQL ───────────────────────────

EXTRACT_SQL = """
SELECT e.mission_id, e.repo, COALESCE(e.grade,''), COALESCE(e.e_level,''),
       e.status, COALESCE(e.decided_at, e.created_at)::text,
       COALESCE(m.status,''),
       COALESCE(m.result->'pr_gate'->'history','[]'::jsonb)::text,
       COALESCE((SELECT bool_or(l.action = 'custody_requeue')
                   FROM mission_lifecycle_log l
                  WHERE l.mission_id = e.mission_id
                    AND l.created_at > COALESCE(m.finished_at, m.created_at)), false),
       COALESCE((SELECT bool_or(l.action = 'orphan_detect')
                   FROM mission_lifecycle_log l
                  WHERE l.mission_id = e.mission_id
                    AND l.created_at > COALESCE(m.finished_at, m.created_at)), false),
       COALESCE((SELECT bool_or(l.action = 'write_off')
                   FROM mission_lifecycle_log l
                  WHERE l.mission_id = e.mission_id), false),
       COALESCE(m.finished_at, m.created_at)::text
  FROM outcome_evaluations e
  JOIN tensor_dev_missions m ON m.id = e.mission_id
 WHERE e.status = {status}
 ORDER BY e.mission_id
""".strip()


def extract_records(dsn_cmd: str = "psql -U foundation -d tensorplatform",
                    repos: Sequence[str] | None = None,
                    ship_ledger: str | Path | None = None,
                    statuses: Sequence[str] = ("confirmed",),
                    rows: Sequence[Sequence[str]] | None = None,
                    as_of: float | None = None,
                    horizon_hours: float = 24.0,
                    landing_ledgers: Sequence[str | Path] | None = None
                    ) -> list[EvalRecord]:
    """抽取 ``EvalRecord`` 列表。

    ``rows`` 可注入（测试/离线回放用），否则走只读 psql。
    ``repos`` 为 None 表示不过滤（全生态口径）；给定则只保留这些仓。
    """
    if rows is None:
        status_list = ", ".join(f"'{s}'" for s in statuses)
        sql = EXTRACT_SQL.format(status="ANY (ARRAY[" + status_list + "])")
        rows = psql_rows(sql, dsn_cmd=dsn_cmd)

    ship_map = load_ship_ledger(ship_ledger)
    landing = load_landing_ledger(list(landing_ledgers or []))
    repo_filter = set(repos) if repos else None
    out: list[EvalRecord] = []
    for r in rows:
        if len(r) < 11:
            continue
        (mid_s, repo, grade, e, status, decided, mstatus,
         hist_json, requeued, orphaned, written_off) = r[:11]
        fin_raw = r[11] if len(r) > 11 else ""
        repo = repo.strip()
        if repo_filter and repo not in repo_filter:
            continue
        try:
            mid = int(mid_s)
        except ValueError:
            continue
        t = _epoch(decided)
        if t is None:
            continue
        try:
            hist = json.loads(hist_json) if hist_json else []
        except json.JSONDecodeError:
            hist = []

        outcome, evidence, why = OUTCOME_CENSORED, "none", "unobserved"
        entry = landing.get((repo, mid))
        if entry:  # 1) 落账台账：最权威（有 merge 时间戳，能识别合并后回归）
            outcome, why = outcome_from_landing(entry)
            evidence = "merge_ledger"
        elif mid in ship_map:  # 2) 船仓合并台账：本地私有仓没有 PR 闸，只能看台账
            outcome, evidence, why = ship_map[mid], "ship_ledger", "ship_merge"
        else:  # 3) PR 闸回填（滞后且偏成功样本，只作兜底）
            pr = (outcome_from_pr_history(hist, as_of=as_of,
                                          horizon_hours=horizon_hours)
                  if hist else OUTCOME_CENSORED)
            if pr != OUTCOME_CENSORED:
                outcome, evidence, why = pr, "pr_gate", "pr_history"
            elif str(orphaned).lower() in ("t", "true"):
                outcome, evidence, why = OUTCOME_DISCARD, "lifecycle", "orphan_detect"
            elif str(written_off).lower() in ("t", "true"):
                outcome, evidence, why = OUTCOME_DISCARD, "lifecycle", "write_off"
            elif str(requeued).lower() in ("t", "true"):
                # 注：核查过全部 110 条 custody_requeue 都早于 finished_at
                # （任务内部重排队），因此该信号不构成「下游返工」，只留痕。
                outcome, evidence, why = OUTCOME_REWORK, "lifecycle", "custody_requeue"

        out.append(EvalRecord(
            mission_id=mid, repo=repo, grade=grade or None, e_level=e or None,
            eval_status=status, decided_at=t, outcome=outcome, evidence=evidence,
            mission_status=mstatus or None,
            finished_at=_epoch(fin_raw),
            detail={"n_pr_events": len(hist), "why": why,
                    "ledger_actions": [a["action"] for a in (entry or {}).get("actions", [])]},
        ))
    return out


def class_summary(records: Sequence[EvalRecord]) -> dict:
    """按仓 × 终局类别的计数（含 censored），报告第一节的覆盖表。"""
    by_repo: dict[str, dict[str, int]] = {}
    for r in records:
        d = by_repo.setdefault(r.repo, {})
        d[r.outcome] = d.get(r.outcome, 0) + 1
        d["total"] = d.get("total", 0) + 1
    return by_repo