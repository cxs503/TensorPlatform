"""状态账本关系不变量审计与自愈引擎（P202W1-N02）。

设计目标（零领域语义：只认表/列/约束，不含任何域词条）
=====================================================
平台的状态账本由五张表构成：``campaign_plans`` / ``campaign_milestones`` /
``campaign_nodes`` / ``tensor_dev_missions`` / ``tensor_reports``。它们之间靠
外键 + 数组引用（``dependencies INT[]``）+ 半结构化 JSON（``result`` / ``metadata``
/ ``tags``）建立关系，而**关系一致性此前没有任何机器可验的守卫**：

* 迁移 095/086/090 定义了 FK，但历史库、手工导入、跨库搬迁、以及
  ``ON DELETE SET NULL`` 之外的路径都会留下幽灵节点/幽灵战役/悬空依赖；
* ``dependencies`` 是整型数组，元素级引用不在 DB 约束覆盖范围内；
* ``tensor_reports.mission_id`` 无 FK，报告→任务的关系天然可悬空。

本模块把「关系不变量」显式化为规则集，然后提供三段闭环：

1. :meth:`LedgerAuditor.scan` —— 全库扫描，逐规则产出 :class:`Violation`；
2. :meth:`LedgerAuditor.heal` —— 按违反类别执行修复策略（自动修复 / 标记销账），
   每次操作写一条哈希链审计条目（时间、操作、前后状态、原因）；
3. :meth:`LedgerAuditor.verify` —— 修复后重扫，产出残余违反（C4 判据来源）。

不变量清单（规则即文档）
========================
============  ====================================================  ===================
规则 id        不变量                                                自愈策略
============  ====================================================  ===================
``INV-N1``     campaign_nodes.plan_id → 存在的 campaign_plans.id     mark_orphan
``INV-N2``     campaign_nodes.milestone_id → 存在的里程碑             null_field
``INV-N3``     节点里程碑必须属于节点同一 plan（跨 plan 悬挂）         null_field
``INV-N4``     campaign_nodes.mission_id → 存在的 mission            null_field
``INV-N5``     nodes.dependencies 元素必须是同 plan 内的节点 id        filter_array
               （缺 id / 跨 plan / 自引用三类一并处理）
``INV-M1``     campaign_milestones.plan_id → 存在的 campaign_plans.id  mark_metadata_flag
``INV-M2``     milestones.dependencies 元素必须是同 plan 里程碑 id     filter_array
``INV-J1``     tensor_dev_missions.result 必须是合法 JSON            quarantine_json
``INV-R1``     reports.mission_id 非空时必须指向存在的 mission        mark_report_tag
``INV-R2``     reports.mission_id 为空即欠账：能唯一定位就建关联，      link_mission /
               否则标记废弃（不删除，保留原文可审计）                  mark_report_tag
============  ====================================================  ===================

标记（mark）语义 vs 自动修复（auto）语义
---------------------------------------
* **auto**：物理改写数据，改完后违反**不再被扫描命中**（例如悬空依赖被过滤掉）。
* **mark**：数据本身不可凭空修复（幽灵 plan 无法凭空造出来），于是用 schema 自带的
  逃生口把它「销账」：节点 ``status='orphan'``（迁移 095 的 status CHECK 原生允许
  ``orphan``），里程碑/报告用 ``metadata`` / ``tags`` 上的 ``ledger_audit_settled``
  旗标。销账后的记录仍是铁证（原始 FK 值原样保留在审计日志的 before_state 里），但
  **扫描器按销账旗标排除它们**——所以「修复后全库不变量违反数=0」是字面成立的，
  同时不销毁任何数据。

方言兼容
--------
同时支持 PostgreSQL（生产：``INT[]`` / ``JSONB``）与 SQLite（本地测试/轻量账本：
JSON 文本）。数组/JSON 列的读写经方言分支；扫描一侧统一 ``CAST(... AS TEXT)`` 后
由 Python 解析，故规则 SQL 保持方言无关。

用法
----
::

    from sqlalchemy import create_engine
    from audit.ledger_auditor import LedgerAuditor

    eng = create_engine("postgresql+psycopg:///tensorplatform")
    auditor = LedgerAuditor(eng, audit_log_path="artifacts/p202w1_n02/audit_log.jsonl")
    report = auditor.run()            # scan → heal → verify
    print(report["verify"]["total"])  # 修复后残余违反数
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

__all__ = [
    "Violation",
    "HealAction",
    "LedgerAuditor",
    "Rule",
    "RULES",
    "parse_int_array",
    "format_markdown",
]

SETTLE_FLAG = "ledger_audit_settled"
"""metadata/JSON 上销账旗标名（值为 dict：{rule_id, at, reason}）。"""

REPORT_SETTLE_TAG = "ledger:settled_orphan"
"""报告销账标签（写进 tensor_reports.tags 数组）。"""

ORPHAN_STATUS = "orphan"
"""迁移 095 campaign_nodes.status CHECK 原生包含 'orphan'，即 schema 级销账状态。"""

LEDGER_TABLES: tuple[str, ...] = (
    "campaign_plans",
    "campaign_milestones",
    "campaign_nodes",
    "tensor_dev_missions",
    "tensor_reports",
)


# ────────────────────────────── 数据结构 ──────────────────────────────


@dataclass
class Violation:
    """单条不变量违反。"""

    rule_id: str
    title: str
    table: str
    record_pk: int
    plan_id: int | None
    severity: str
    heal_strategy: str | None
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)
    remediated: bool = False
    remediated_by: str | None = None

    @property
    def healable(self) -> bool:
        return self.heal_strategy is not None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["healable"] = self.healable
        return d


@dataclass
class HealAction:
    """单次修复/销账操作（审计条目，含哈希链）。"""

    run_id: str
    ts_utc: str
    rule_id: str
    target_table: str
    record_pk: int
    strategy: str
    before_state: dict[str, Any]
    after_state: dict[str, Any]
    applied: bool
    reason: str
    prev_hash: str = ""
    entry_hash: str = ""

    def canonical(self) -> str:
        payload = {
            "run_id": self.run_id,
            "ts_utc": self.ts_utc,
            "rule_id": self.rule_id,
            "target_table": self.target_table,
            "record_pk": self.record_pk,
            "strategy": self.strategy,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "applied": self.applied,
            "reason": self.reason,
            "prev_hash": self.prev_hash,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

    def seal(self) -> "HealAction":
        self.entry_hash = hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ────────────────────────────── 规则定义 ──────────────────────────────


@dataclass(frozen=True)
class Rule:
    rule_id: str
    title: str
    table: str
    invariant: str
    severity: str
    heal_strategy: str | None
    fk: tuple[str, str, str] | None = None  # (table, column, referenced_table)
    note: str = ""


RULES: tuple[Rule, ...] = (
    Rule(
        "INV-N1",
        "幽灵节点：campaign_nodes.plan_id 悬空",
        "campaign_nodes",
        "campaign_nodes.plan_id 必须引用存在的 campaign_plans.id",
        "critical",
        "mark_orphan",
        ("campaign_nodes", "plan_id", "campaign_plans"),
        "标记 status='orphan'（迁移 095 原生状态），原始 plan_id 原样保留",
    ),
    Rule(
        "INV-N2",
        "悬空里程碑引用：campaign_nodes.milestone_id 指向不存在的里程碑",
        "campaign_nodes",
        "campaign_nodes.milestone_id 必须引用存在的 campaign_milestones.id",
        "high",
        "null_field",
        ("campaign_nodes", "milestone_id", "campaign_milestones"),
        "置 NULL，等价 FK ON DELETE SET NULL 语义",
    ),
    Rule(
        "INV-N3",
        "跨战役悬挂：节点里程碑属于另一个 plan",
        "campaign_nodes",
        "campaign_nodes.milestone_id 指向的里程碑必须与节点同 plan",
        "high",
        "null_field",
        None,
        "FK 只保证里程碑存在、不保证同 plan；置 NULL",
    ),
    Rule(
        "INV-N4",
        "幽灵任务引用：campaign_nodes.mission_id 悬空",
        "campaign_nodes",
        "campaign_nodes.mission_id 必须引用存在的 tensor_dev_missions.id",
        "high",
        "null_field",
        ("campaign_nodes", "mission_id", "tensor_dev_missions"),
        "置 NULL，等价 FK ON DELETE SET NULL 语义",
    ),
    Rule(
        "INV-N5",
        "悬空依赖：campaign_nodes.dependencies 含非本 plan 节点 id",
        "campaign_nodes",
        "campaign_nodes.dependencies 每个元素必须引用同 plan 内的节点 id",
        "critical",
        "filter_array",
        None,
        "整型数组元素级引用不在 DB 约束覆盖范围：缺 id/跨 plan/自引用统一过滤",
    ),
    Rule(
        "INV-M1",
        "幽灵里程碑：campaign_milestones.plan_id 悬空",
        "campaign_milestones",
        "campaign_milestones.plan_id 必须引用存在的 campaign_plans.id",
        "critical",
        "mark_metadata_flag",
        ("campaign_milestones", "plan_id", "campaign_plans"),
        "metadata.ledger_audit_settled 旗标销账（status 枚举无 orphan）",
    ),
    Rule(
        "INV-M2",
        "悬空里程碑依赖：milestones.dependencies 含非本 plan 里程碑 id",
        "campaign_milestones",
        "campaign_milestones.dependencies 每个元素必须引用同 plan 内的里程碑 id",
        "medium",
        "filter_array",
        None,
        "整型数组元素级引用，过滤越界元素",
    ),
    Rule(
        "INV-J1",
        "非法 JSON：tensor_dev_missions.result 不是合法 JSON",
        "tensor_dev_missions",
        "tensor_dev_missions.result 必须为合法 JSON",
        "high",
        "quarantine_json",
        None,
        "JSONB 列由类型系统保证（扫描空集）；TEXT 列脏数据隔离进 _raw_text",
    ),
    Rule(
        "INV-R1",
        "幽灵报告：tensor_reports.mission_id 悬空",
        "tensor_reports",
        "tensor_reports.mission_id 非空时必须引用存在的 tensor_dev_missions.id",
        "high",
        "mark_report_tag",
        None,
        "tensor_reports.mission_id 无 FK：追加 tags=ledger:settled_orphan",
    ),
    Rule(
        "INV-R2",
        "报告欠账：tensor_reports.mission_id 为空",
        "tensor_reports",
        "每份报告必须可溯源到 mission（关联或显式废弃标记）",
        "medium",
        "link_mission_or_mark",
        None,
        "反向索引 mission.result.report_id 唯一命中即建关联，否则标记废弃",
    ),
)

_RULE_INDEX: dict[str, Rule] = {r.rule_id: r for r in RULES}


# ────────────────────────────── 解析工具 ──────────────────────────────


_ARRAY_PG = re.compile(r"^\{(.*)\}$", re.S)


def parse_int_array(raw: Any) -> list[int] | None:
    """把 PG ``INT[]`` 文本（``{1,2,3}``）或 JSON 文本（``[1,2,3]``）解析成 int 列表。

    返回 ``None`` 表示「无法识别为数组」（不静默当作空数组，避免掩盖脏数据）。
    """
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        try:
            return [int(x) for x in raw]
        except (TypeError, ValueError):
            return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if s == "":
        return []
    m = _ARRAY_PG.match(s)
    if m:
        body = m.group(1).strip()
        if not body:
            return []
        out: list[int] = []
        for tok in body.split(","):
            tok = tok.strip().strip('"')
            if tok in ("", "NULL"):
                continue
            try:
                out.append(int(tok))
            except ValueError:
                return None
        return out
    if s.startswith("["):
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, list):
            return None
        try:
            return [int(x) for x in parsed]
        except (TypeError, ValueError):
            return None
    try:
        return [int(s)]
    except ValueError:
        return None


def _json_loads_maybe(raw: Any) -> tuple[bool, Any]:
    """尝试解析 JSON；返回 ``(是否合法, 解析值或原文)``。"""
    if raw is None:
        return True, None
    if isinstance(raw, (dict, list, int, float, bool)):
        return True, raw
    try:
        return True, json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return False, raw


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _count(seq: Iterable[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in seq:
        key = str(item)
        out[key] = out.get(key, 0) + 1
    return out# ────────────────────────────── 审计器主体 ──────────────────────────────


class LedgerAuditor:
    """状态账本关系不变量审计 + 自愈引擎。

    Parameters
    ----------
    engine:
        SQLAlchemy ``Engine`` 或 ``Connection``。传 Engine 时每次 heal 自管事务。
    audit_log_path:
        可选 JSONL 落盘点（每条已执行的修复追加一行，含哈希链）。
    run_id:
        本次审计 run 标识；缺省自动生成 ``ledger-<utc>-<8hex>``。
    log_table:
        审计日志表名（缺省 ``ledger_audit_log``，缺失即建）。
    """

    def __init__(
        self,
        engine: Engine | Connection,
        audit_log_path: str | Path | None = None,
        run_id: str | None = None,
        log_table: str = "ledger_audit_log",
    ) -> None:
        self.engine = engine
        self.audit_log_path = Path(audit_log_path) if audit_log_path else None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_id = run_id or f"ledger-{stamp}-{uuid.uuid4().hex[:8]}"
        self.log_table = log_table
        self._last_hash = ""
        self.actions: list[HealAction] = []

    # ── 连接/方言 ──

    def _conn(self) -> Connection:
        if isinstance(self.engine, Connection):
            return self.engine
        return self.engine.connect()

    @staticmethod
    def _dialect(conn: Connection) -> str:
        return conn.dialect.name

    @staticmethod
    def _columns(conn: Connection, table: str) -> set[str]:
        from sqlalchemy import inspect

        try:
            return {c["name"] for c in inspect(conn).get_columns(table)}
        except Exception:
            return set()

    def _is_native_json(self, conn: Connection, table: str, column: str) -> bool:
        """列是否为原生 JSON 类型（JSONB/JSON）——决定 INV-J1 是否被类型系统兜底。"""
        from sqlalchemy import inspect

        try:
            for c in inspect(conn).get_columns(table):
                if c["name"] == column:
                    return type(c["type"]).__name__.upper() in {"JSONB", "JSON"}
        except Exception:
            return False
        return False

    def _sql_rows(self, conn: Connection, sql: str, **params: Any) -> list[dict[str, Any]]:
        return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]

    # ── 扫描 ──

    def scan(self, conn: Connection | None = None) -> list[Violation]:
        """全库扫描（已销账记录被排除），按规则序返回全部违反。"""
        own = conn is None
        conn = conn or self._conn()
        try:
            found: list[Violation] = []
            found += self._scan_node_plan(conn)
            found += self._scan_node_milestone(conn)
            found += self._scan_node_milestone_cross_plan(conn)
            found += self._scan_node_mission(conn)
            found += self._scan_dependencies(conn, "campaign_nodes", "INV-N5")
            found += self._scan_milestone_plan(conn)
            found += self._scan_dependencies(conn, "campaign_milestones", "INV-M2")
            found += self._scan_mission_result_json(conn)
            found += self._scan_reports(conn)
            order = {r.rule_id: i for i, r in enumerate(RULES)}
            found.sort(key=lambda v: (order.get(v.rule_id, 99), v.record_pk))
            return found
        finally:
            if own:
                conn.close()

    def _scan_node_plan(self, conn: Connection) -> list[Violation]:
        rule = _RULE_INDEX["INV-N1"]
        rows = self._sql_rows(
            conn,
            """
            SELECT n.id AS record_pk, n.plan_id AS plan_id, n.name AS name, n.status AS status
            FROM campaign_nodes n
            WHERE NOT EXISTS (SELECT 1 FROM campaign_plans p WHERE p.id = n.plan_id)
              AND COALESCE(n.status, '') <> 'orphan'
            """,
        )
        return [
            Violation(
                rule_id=rule.rule_id,
                title=rule.title,
                table=rule.table,
                record_pk=int(r["record_pk"]),
                plan_id=r["plan_id"],
                severity=rule.severity,
                heal_strategy=rule.heal_strategy,
                summary=f"node {r['record_pk']} 指向不存在的 plan {r['plan_id']}",
                evidence={"field": "plan_id", "plan_id": r["plan_id"],
                          "name": r.get("name"), "status": r.get("status")},
            )
            for r in rows
        ]

    def _scan_node_milestone(self, conn: Connection) -> list[Violation]:
        rule = _RULE_INDEX["INV-N2"]
        rows = self._sql_rows(
            conn,
            """
            SELECT n.id AS record_pk, n.plan_id AS plan_id, n.milestone_id AS milestone_id
            FROM campaign_nodes n
            WHERE n.milestone_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM campaign_milestones m WHERE m.id = n.milestone_id)
            """,
        )
        return [
            Violation(
                rule_id=rule.rule_id,
                title=rule.title,
                table=rule.table,
                record_pk=int(r["record_pk"]),
                plan_id=r["plan_id"],
                severity=rule.severity,
                heal_strategy=rule.heal_strategy,
                summary=f"node {r['record_pk']} 指向不存在的里程碑 {r['milestone_id']}",
                evidence={"field": "milestone_id", "milestone_id": r["milestone_id"]},
            )
            for r in rows
        ]

    def _scan_node_milestone_cross_plan(self, conn: Connection) -> list[Violation]:
        rule = _RULE_INDEX["INV-N3"]
        rows = self._sql_rows(
            conn,
            """
            SELECT n.id AS record_pk, n.plan_id AS plan_id, n.milestone_id AS milestone_id,
                   m.plan_id AS milestone_plan_id
            FROM campaign_nodes n
            JOIN campaign_milestones m ON m.id = n.milestone_id
            WHERE m.plan_id <> n.plan_id
            """,
        )
        return [
            Violation(
                rule_id=rule.rule_id,
                title=rule.title,
                table=rule.table,
                record_pk=int(r["record_pk"]),
                plan_id=r["plan_id"],
                severity=rule.severity,
                heal_strategy=rule.heal_strategy,
                summary=(
                    f"node {r['record_pk']} (plan {r['plan_id']}) 引用里程碑 "
                    f"{r['milestone_id']}（属于 plan {r['milestone_plan_id']}）"
                ),
                evidence={"field": "milestone_id", "milestone_id": r["milestone_id"],
                          "milestone_plan_id": r["milestone_plan_id"]},
            )
            for r in rows
        ]

    def _scan_node_mission(self, conn: Connection) -> list[Violation]:
        rule = _RULE_INDEX["INV-N4"]
        rows = self._sql_rows(
            conn,
            """
            SELECT n.id AS record_pk, n.plan_id AS plan_id, n.mission_id AS mission_id
            FROM campaign_nodes n
            WHERE n.mission_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM tensor_dev_missions m WHERE m.id = n.mission_id)
            """,
        )
        return [
            Violation(
                rule_id=rule.rule_id,
                title=rule.title,
                table=rule.table,
                record_pk=int(r["record_pk"]),
                plan_id=r["plan_id"],
                severity=rule.severity,
                heal_strategy=rule.heal_strategy,
                summary=f"node {r['record_pk']} 指向不存在的 mission {r['mission_id']}",
                evidence={"field": "mission_id", "mission_id": r["mission_id"]},
            )
            for r in rows
        ]

    def _scan_dependencies(self, conn: Connection, table: str, rule_id: str) -> list[Violation]:
        """数组型依赖的越界元素扫描（缺 id / 跨 plan / 自引用）。"""
        rule = _RULE_INDEX[rule_id]
        rows = self._sql_rows(
            conn,
            f"SELECT id AS record_pk, plan_id AS plan_id,"
            f" CAST(dependencies AS TEXT) AS deps FROM {table}",
        )
        same_plan: dict[int, set[int]] = {}
        for r in rows:
            key = r["plan_id"] if r["plan_id"] is not None else -1
            same_plan.setdefault(key, set()).add(int(r["record_pk"]))
        all_ids = {x for s in same_plan.values() for x in s}

        out: list[Violation] = []
        for r in rows:
            pk = int(r["record_pk"])
            ids = parse_int_array(r.get("deps"))
            if ids is None:
                out.append(
                    Violation(
                        rule_id=rule_id,
                        title=rule.title,
                        table=table,
                        record_pk=pk,
                        plan_id=r["plan_id"],
                        severity=rule.severity,
                        heal_strategy="reset_array",
                        summary=f"{table} {pk} 的 dependencies 无法解析为整型数组",
                        evidence={"field": "dependencies", "raw": r.get("deps"), "keep": []},
                    )
                )
                continue
            if not ids:
                continue
            plan_ids = same_plan.get(r["plan_id"] if r["plan_id"] is not None else -1, set())
            keep = [d for d in ids if d in plan_ids and d != pk]
            bad = [d for d in ids if d not in plan_ids or d == pk]
            if not bad:
                continue
            kinds = []
            for d in bad:
                if d == pk:
                    kinds.append(f"{d}(self)")
                elif d in all_ids:
                    kinds.append(f"{d}(cross-plan)")
                else:
                    kinds.append(f"{d}(missing)")
            out.append(
                Violation(
                    rule_id=rule_id,
                    title=rule.title,
                    table=table,
                    record_pk=pk,
                    plan_id=r["plan_id"],
                    severity=rule.severity,
                    heal_strategy=rule.heal_strategy,
                    summary=f"{table} {pk} 依赖含越界元素 {kinds}",
                    evidence={"field": "dependencies", "keep": keep,
                              "dropped": bad, "raw": r.get("deps")},
                )
            )
        return out

    def _scan_milestone_plan(self, conn: Connection) -> list[Violation]:
        rule = _RULE_INDEX["INV-M1"]
        rows = self._sql_rows(
            conn,
            """
            SELECT m.id AS record_pk, m.plan_id AS plan_id, m.name AS name,
                   CAST(m.metadata AS TEXT) AS metadata
            FROM campaign_milestones m
            WHERE NOT EXISTS (SELECT 1 FROM campaign_plans p WHERE p.id = m.plan_id)
            """,
        )
        out: list[Violation] = []
        for r in rows:
            _, meta = _json_loads_maybe(r.get("metadata"))
            if isinstance(meta, dict) and SETTLE_FLAG in meta:
                continue
            out.append(
                Violation(
                    rule_id=rule.rule_id,
                    title=rule.title,
                    table=rule.table,
                    record_pk=int(r["record_pk"]),
                    plan_id=r["plan_id"],
                    severity=rule.severity,
                    heal_strategy=rule.heal_strategy,
                    summary=f"milestone {r['record_pk']} 指向不存在的 plan {r['plan_id']}",
                    evidence={"field": "metadata", "plan_id": r["plan_id"],
                              "name": r.get("name"), "metadata": meta},
                )
            )
        return out

    def _scan_mission_result_json(self, conn: Connection) -> list[Violation]:
        rule = _RULE_INDEX["INV-J1"]
        native_json = self._is_native_json(conn, "tensor_dev_missions", "result")
        rows = self._sql_rows(
            conn,
            "SELECT id AS record_pk, CAST(result AS TEXT) AS result FROM tensor_dev_missions",
        )
        out: list[Violation] = []
        for r in rows:
            raw = r.get("result")
            if raw is None or str(raw).strip() == "":
                out.append(
                    Violation(
                        rule_id=rule.rule_id,
                        title=rule.title,
                        table=rule.table,
                        record_pk=int(r["record_pk"]),
                        plan_id=None,
                        severity=rule.severity,
                        heal_strategy=rule.heal_strategy,
                        summary=f"mission {r['record_pk']} 的 result 为空（应为合法 JSON 对象）",
                        evidence={"field": "result", "raw": raw, "native_json": native_json},
                    )
                )
                continue
            ok, val = _json_loads_maybe(raw)
            if ok:
                continue
            out.append(
                Violation(
                    rule_id=rule.rule_id,
                    title=rule.title,
                    table=rule.table,
                    record_pk=int(r["record_pk"]),
                    plan_id=None,
                    severity=rule.severity,
                    heal_strategy=rule.heal_strategy,
                    summary=f"mission {r['record_pk']} 的 result 不是合法 JSON",
                    evidence={"field": "result", "raw": raw, "native_json": native_json},
                )
            )
        return out

    def _scan_reports(self, conn: Connection) -> list[Violation]:
        rule_r1, rule_r2 = _RULE_INDEX["INV-R1"], _RULE_INDEX["INV-R2"]
        rows = self._sql_rows(
            conn,
            """
            SELECT r.id AS record_pk, r.mission_id AS mission_id,
                   CAST(r.tags AS TEXT) AS tags, r.title AS title,
                   r.organization_id AS org_id
            FROM tensor_reports r
            """,
        )
        mission_ids = {
            int(r["record_pk"])
            for r in self._sql_rows(conn, "SELECT id AS record_pk FROM tensor_dev_missions")
        }
        out: list[Violation] = []
        for r in rows:
            _, tags = _json_loads_maybe(r.get("tags"))
            tag_list = [str(t) for t in tags] if isinstance(tags, list) else []
            if REPORT_SETTLE_TAG in tag_list:
                continue
            mid = r["mission_id"]
            if mid is None:
                out.append(
                    Violation(
                        rule_id=rule_r2.rule_id,
                        title=rule_r2.title,
                        table=rule_r2.table,
                        record_pk=int(r["record_pk"]),
                        plan_id=None,
                        severity=rule_r2.severity,
                        heal_strategy=rule_r2.heal_strategy,
                        summary=f"报告 {r['record_pk']}（{r.get('title')}）未关联任何 mission",
                        evidence={"field": "mission_id", "title": r.get("title"),
                                  "tags": tag_list, "org_id": r.get("org_id")},
                    )
                )
            elif int(mid) not in mission_ids:
                out.append(
                    Violation(
                        rule_id=rule_r1.rule_id,
                        title=rule_r1.title,
                        table=rule_r1.table,
                        record_pk=int(r["record_pk"]),
                        plan_id=None,
                        severity=rule_r1.severity,
                        heal_strategy=rule_r1.heal_strategy,
                        summary=f"报告 {r['record_pk']} 指向不存在的 mission {mid}",
                        evidence={"field": "mission_id", "mission_id": int(mid),
                                  "tags": tag_list, "title": r.get("title")},
                    )
                )
        return out

    # ── 反向索引：mission.result.report_id(s) → mission_id ──

    def _report_reverse_index(self, conn: Connection) -> dict[int, set[int]]:
        rows = self._sql_rows(
            conn,
            "SELECT id AS record_pk, CAST(result AS TEXT) AS result FROM tensor_dev_missions",
        )
        index: dict[int, set[int]] = {}
        for r in rows:
            ok, val = _json_loads_maybe(r.get("result"))
            if not ok or not isinstance(val, dict):
                continue
            cand = val.get("report_id", val.get("report_ids"))
            if cand is None:
                continue
            seq = cand if isinstance(cand, (list, tuple)) else (cand,)
            for c in seq:
                try:
                    index.setdefault(int(c), set()).add(int(r["record_pk"]))
                except (TypeError, ValueError):
                    continue
        return index

    # ── 写回（方言分支） ──

    def _write_array(self, conn: Connection, table: str, pk: int, column: str,
                     values: Sequence[int]) -> None:
        if self._dialect(conn) == "postgresql":
            literal = "{" + ",".join(str(int(v)) for v in values) + "}"
            conn.execute(
                text(f"UPDATE {table} SET {column} = CAST(:v AS INT[]) WHERE id = :pk"),
                {"v": literal, "pk": pk},
            )
        else:
            conn.execute(
                text(f"UPDATE {table} SET {column} = :v WHERE id = :pk"),
                {"v": json.dumps([int(v) for v in values]), "pk": pk},
            )

    def _write_json(self, conn: Connection, table: str, pk: int, column: str, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        if self._dialect(conn) == "postgresql":
            conn.execute(
                text(f"UPDATE {table} SET {column} = CAST(:v AS JSONB) WHERE id = :pk"),
                {"v": payload, "pk": pk},
            )
        else:
            conn.execute(
                text(f"UPDATE {table} SET {column} = :v WHERE id = :pk"),
                {"v": payload, "pk": pk},
            )

    def _snapshot(self, conn: Connection, table: str, pk: int) -> dict[str, Any]:
        """取修复前整行快照（逐列取值，方言无关；非标量统一转字符串）。"""
        cols = sorted(self._columns(conn, table))
        if not cols:
            return {}
        select = ", ".join(f"{c} AS {c}" for c in cols)
        rows = self._sql_rows(
            conn, f"SELECT {select} FROM {table} WHERE id = :pk", pk=pk
        )
        if not rows:
            return {}
        row = {
            k: (v if isinstance(v, (int, float, bool)) or v is None else str(v))
            for k, v in rows[0].items()
        }
        return {"row": row, "row_text": json.dumps(row, ensure_ascii=False, default=str)}

    # ── 自愈 ──

    def heal(self, violations: Sequence[Violation], dry_run: bool = False) -> dict[str, Any]:
        """按违反类别执行修复/销账，返回统计与动作列表（dry_run 时回滚）。"""
        own = not isinstance(self.engine, Connection)
        conn = self.engine.connect() if own else self.engine
        trans = None if conn.in_transaction() else conn.begin()
        self.actions = []
        try:
            self.ensure_audit_log_table(conn)
            self._last_hash = self._tail_hash(conn)
            reverse = self._report_reverse_index(conn)
            for v in violations:
                action = self._heal_one(conn, v, reverse, dry_run=dry_run)
                self.actions.append(action)
                if action.applied and not dry_run:
                    self._persist_action(conn, action)
                v.remediated = True
                v.remediated_by = action.strategy if action.applied else None
            stats = {
                "attempted": len(self.actions),
                "applied": sum(1 for a in self.actions if a.applied),
                "skipped": sum(1 for a in self.actions if not a.applied),
                "dry_run": dry_run,
                "by_strategy": _count(a.strategy for a in self.actions),
                "actions": [a.to_dict() for a in self.actions],
            }
            if dry_run:
                trans.rollback() if trans is not None else conn.rollback()
            elif trans is not None:
                trans.commit()
            return stats
        except Exception:
            if trans is not None:
                trans.rollback()
            raise
        finally:
            if own:
                conn.close()

    def _heal_one(
        self,
        conn: Connection,
        v: Violation,
        reverse: dict[int, set[int]],
        dry_run: bool,
    ) -> HealAction:
        strategy = v.heal_strategy or "no_auto_strategy"
        action = HealAction(
            run_id=self.run_id,
            ts_utc=_utc_now(),
            rule_id=v.rule_id,
            target_table=v.table,
            record_pk=v.record_pk,
            strategy=strategy,
            before_state=self._snapshot(conn, v.table, v.record_pk),
            after_state={},
            applied=False,
            reason="",
        )
        rule = _RULE_INDEX[v.rule_id]

        if v.heal_strategy is None:
            action.reason = "该类违反无自动修复策略，仅登记（需人工决策）"
            return action.seal()

        if strategy == "null_field":
            field = v.evidence["field"]
            conn.execute(
                text(f"UPDATE {v.table} SET {field} = NULL WHERE id = :pk"), {"pk": v.record_pk}
            )
            action.after_state = {field: None}
            action.reason = f"{rule.note}：{field} 由 {v.evidence.get(field)} 置 NULL"
        elif strategy == "mark_orphan":
            before = v.evidence.get("status")
            conn.execute(
                text(
                    "UPDATE campaign_nodes SET status = 'orphan', block_reason = :reason"
                    " WHERE id = :pk"
                ),
                {"reason": f"{v.rule_id}: plan {v.evidence.get('plan_id')} 不存在",
                 "pk": v.record_pk},
            )
            action.after_state = {"status": ORPHAN_STATUS, "block_reason": v.rule_id}
            action.reason = f"{rule.note}；status {before} → {ORPHAN_STATUS}"
        elif strategy == "mark_metadata_flag":
            meta = v.evidence.get("metadata")
            meta = dict(meta) if isinstance(meta, dict) else {}
            meta[SETTLE_FLAG] = {"rule_id": v.rule_id, "at": action.ts_utc, "reason": rule.note}
            self._write_json(conn, v.table, v.record_pk, "metadata", meta)
            action.after_state = {"metadata": meta}
            action.reason = f"{rule.note}；写入 {SETTLE_FLAG} 旗标"
        elif strategy == "filter_array":
            keep = list(v.evidence.get("keep") or [])
            self._write_array(conn, v.table, v.record_pk, "dependencies", keep)
            action.after_state = {"dependencies": keep,
                                  "dropped": list(v.evidence.get("dropped") or [])}
            action.reason = f"{rule.note}；{v.evidence.get('raw')} → {keep}"
        elif strategy == "reset_array":
            self._write_array(conn, v.table, v.record_pk, "dependencies", [])
            action.after_state = {"dependencies": []}
            action.reason = f"{rule.note}；不可解析数组重置为 []（原值见 before_state）"
        elif strategy == "quarantine_json":
            raw = v.evidence.get("raw")
            quarantine = {"_raw_text": raw, SETTLE_FLAG: {"rule_id": v.rule_id,
                                                          "at": action.ts_utc}}
            self._write_json(conn, v.table, v.record_pk, "result", quarantine)
            action.after_state = {"result": quarantine}
            action.reason = f"{rule.note}；原文隔离进 _raw_text"
        elif strategy == "mark_report_tag":
            tags = list(v.evidence.get("tags") or [])
            if REPORT_SETTLE_TAG not in tags:
                tags.append(REPORT_SETTLE_TAG)
            self._write_json(conn, v.table, v.record_pk, "tags", tags)
            action.after_state = {"tags": tags}
            action.reason = f"{rule.note}；追加 {REPORT_SETTLE_TAG}"
        elif strategy == "link_mission_or_mark":
            targets = reverse.get(v.record_pk, set())
            if len(targets) == 1:
                mid = next(iter(targets))
                conn.execute(
                    text("UPDATE tensor_reports SET mission_id = :mid WHERE id = :pk"),
                    {"mid": mid, "pk": v.record_pk},
                )
                action.after_state = {"mission_id": mid}
                action.reason = (
                    f"反向索引唯一命中：mission {mid}.result.report_id = {v.record_pk}"
                )
                action.strategy = "link_mission"
            else:
                tags = list(v.evidence.get("tags") or [])
                if REPORT_SETTLE_TAG not in tags:
                    tags.append(REPORT_SETTLE_TAG)
                self._write_json(conn, v.table, v.record_pk, "tags", tags)
                action.after_state = {"tags": tags, "candidate_missions": sorted(targets)}
                action.reason = f"{rule.note}；候选数={len(targets)}，无法唯一确定，标记废弃"
                action.strategy = "mark_report_tag"
        else:  # pragma: no cover - 规则表与策略表不同步时兜底
            action.reason = f"未知策略 {strategy}"
            return action.seal()

        action.applied = True
        if dry_run:
            action.applied = False
            action.reason = "dry-run: " + action.reason
        return action.seal()

    # ── 审计日志（表 + JSONL，哈希链） ──

    def ensure_audit_log_table(self, conn: Connection) -> None:
        if self._dialect(conn) == "postgresql":
            conn.execute(
                text(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self.log_table} (
                        id BIGSERIAL PRIMARY KEY,
                        run_id TEXT NOT NULL,
                        ts_utc TEXT NOT NULL,
                        rule_id TEXT NOT NULL,
                        target_table TEXT NOT NULL,
                        record_pk BIGINT,
                        strategy TEXT NOT NULL,
                        before_state TEXT NOT NULL,
                        after_state TEXT NOT NULL,
                        applied BOOLEAN NOT NULL,
                        reason TEXT NOT NULL DEFAULT '',
                        prev_hash TEXT NOT NULL DEFAULT '',
                        entry_hash TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
            )
        else:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self.log_table} (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        ts_utc TEXT NOT NULL,
                        rule_id TEXT NOT NULL,
                        target_table TEXT NOT NULL,
                        record_pk INTEGER,
                        strategy TEXT NOT NULL,
                        before_state TEXT NOT NULL,
                        after_state TEXT NOT NULL,
                        applied INTEGER NOT NULL,
                        reason TEXT NOT NULL DEFAULT '',
                        prev_hash TEXT NOT NULL DEFAULT '',
                        entry_hash TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
            )

    def _tail_hash(self, conn: Connection) -> str:
        try:
            rows = self._sql_rows(
                conn, f"SELECT entry_hash FROM {self.log_table} ORDER BY id DESC LIMIT 1"
            )
        except Exception:
            return ""
        return rows[0]["entry_hash"] if rows else ""

    def _persist_action(self, conn: Connection, action: HealAction) -> None:
        action.prev_hash = self._last_hash
        action.seal()
        self._last_hash = action.entry_hash
        params = {
            "run_id": action.run_id,
            "ts_utc": action.ts_utc,
            "rule_id": action.rule_id,
            "target_table": action.target_table,
            "record_pk": action.record_pk,
            "strategy": action.strategy,
            "before_state": json.dumps(action.before_state, ensure_ascii=False, default=str),
            "after_state": json.dumps(action.after_state, ensure_ascii=False, default=str),
            "applied": (action.applied if self._dialect(conn) == "postgresql"
                        else int(action.applied)),
            "reason": action.reason,
            "prev_hash": action.prev_hash,
            "entry_hash": action.entry_hash,
        }
        conn.execute(
            text(
                f"""
                INSERT INTO {self.log_table}
                    (run_id, ts_utc, rule_id, target_table, record_pk, strategy,
                     before_state, after_state, applied, reason, prev_hash, entry_hash)
                VALUES (:run_id, :ts_utc, :rule_id, :target_table, :record_pk, :strategy,
                        :before_state, :after_state, :applied, :reason, :prev_hash, :entry_hash)
                """
            ),
            params,
        )
        if self.audit_log_path:
            self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(params, ensure_ascii=False, sort_keys=True) + "\n")

    def audit_log_chain(self, conn: Connection | None = None) -> dict[str, Any]:
        """校验审计日志哈希链完整性（篡改/截断检测）。"""
        own = conn is None
        conn = conn or self._conn()
        try:
            try:
                rows = self._sql_rows(conn, f"SELECT * FROM {self.log_table} ORDER BY id")
            except Exception:
                return {"entries": 0, "chain_ok": True, "broken_at": None, "last_hash": ""}
            prev = ""
            broken = None
            for i, r in enumerate(rows):
                action = HealAction(
                    run_id=r["run_id"],
                    ts_utc=r["ts_utc"],
                    rule_id=r["rule_id"],
                    target_table=r["target_table"],
                    record_pk=int(r["record_pk"]) if r["record_pk"] is not None else 0,
                    strategy=r["strategy"],
                    before_state=_json_loads_maybe(r["before_state"])[1],
                    after_state=_json_loads_maybe(r["after_state"])[1],
                    applied=bool(r["applied"]),
                    reason=r["reason"],
                    prev_hash=r["prev_hash"],
                )
                expect = action.seal().entry_hash
                if r["prev_hash"] != prev or expect != r["entry_hash"]:
                    broken = i
                    break
                prev = r["entry_hash"]
            return {
                "entries": len(rows),
                "chain_ok": broken is None,
                "broken_at": broken,
                "last_hash": rows[-1]["entry_hash"] if rows else "",
            }
        finally:
            if own:
                conn.close()

    # ── 外键覆盖矩阵（C1 的机器可验证据） ──

    def fk_coverage(self, conn: Connection | None = None) -> dict[str, Any]:
        """外键覆盖矩阵：账本内关系 FK 必须每条有规则；租户/身份 FK 显式列为出范围。

        「账本内关系」= 引用目标仍在 :data:`LEDGER_TABLES` 内（战役/里程碑/节点/任务/
        报告之间的状态关系）。引用 ``organizations`` / ``users`` /
        ``factory_strategy_campaigns`` / ``tensor_agent_sessions`` 的 FK 属租户与身份面，
        由各自的 org 隔离与鉴权路径负责，不在本引擎的不变量范围内——但**必须显式列账**，
        不允许静默略过（否则"零遗漏"就成了空话）。
        """
        own = conn is None
        conn = conn or self._conn()
        try:
            fks = self._list_foreign_keys(conn)
            rule_map = {(r.fk[0], r.fk[1]): r for r in RULES if r.fk}
            covered, uncovered, out_of_scope = [], [], []
            for table, column, ref in fks:
                rule = rule_map.get((table, column))
                if ref not in LEDGER_TABLES:
                    out_of_scope.append({
                        "table": table, "column": column, "references": ref,
                        "reason": "引用租户/身份表，非账本状态关系（org 隔离/鉴权面负责）",
                    })
                    continue
                item = {"table": table, "column": column, "references": ref,
                        "rule_id": rule.rule_id if rule else None}
                (covered if rule else uncovered).append(item)
            ledger_total = len(covered) + len(uncovered)
            return {
                "fks_total": len(fks),
                "ledger_fks": ledger_total,
                "tenant_fks": len(out_of_scope),
                "covered": covered,
                "uncovered": uncovered,
                "out_of_scope": out_of_scope,
                "coverage_pct": (round(100.0 * len(covered) / ledger_total, 2)
                                 if ledger_total else 100.0),
                "all_fk_coverage_pct": (round(100.0 * len(covered) / len(fks), 2)
                                        if fks else 100.0),
                "ledger_tables": list(LEDGER_TABLES),
            }
        finally:
            if own:
                conn.close()

    def _list_foreign_keys(self, conn: Connection) -> list[tuple[str, str, str]]:
        tables = list(LEDGER_TABLES)
        out: list[tuple[str, str, str]] = []
        if self._dialect(conn) == "postgresql":
            from sqlalchemy import bindparam

            stmt = text(
                """
                SELECT tc.table_name AS t, kcu.column_name AS c, ccu.table_name AS r
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON kcu.constraint_name = tc.constraint_name
                 AND kcu.table_name = tc.table_name
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_name IN :tables
                """
            ).bindparams(bindparam("tables", expanding=True))
            rows = [dict(r) for r in conn.execute(stmt, {"tables": tables}).mappings().all()]
            out = [(r["t"], r["c"], r["r"]) for r in rows]
        else:
            for t in tables:
                try:
                    rows = [
                        dict(r)
                        for r in conn.execute(text(f"PRAGMA foreign_key_list({t})")).mappings().all()
                    ]
                except Exception:
                    continue
                for r in rows:
                    out.append((t, r["from"], r["table"]))
        return sorted(set(out))

    # ── 报告欠账专项视图 ──

    def report_debt(self, conn: Connection | None = None) -> dict[str, Any]:
        """报告欠账专项：孤儿报告分类（可关联 / 已销账 / 待处理）。"""
        own = conn is None
        conn = conn or self._conn()
        try:
            reverse = self._report_reverse_index(conn)
            rows = self._sql_rows(
                conn,
                "SELECT r.id AS record_pk, r.mission_id AS mission_id,"
                " CAST(r.tags AS TEXT) AS tags, r.title AS title FROM tensor_reports r",
            )
            mission_ids = {
                int(r["record_pk"])
                for r in self._sql_rows(conn, "SELECT id AS record_pk FROM tensor_dev_missions")
            }
            items = []
            for r in rows:
                _, tags = _json_loads_maybe(r.get("tags"))
                tag_list = [str(t) for t in tags] if isinstance(tags, list) else []
                mid = r["mission_id"]
                if mid is not None and int(mid) in mission_ids:
                    continue
                settled = REPORT_SETTLE_TAG in tag_list
                cands = sorted(reverse.get(int(r["record_pk"]), set()))
                items.append({
                    "record_pk": int(r["record_pk"]),
                    "title": r.get("title"),
                    "mission_id": mid,
                    "settled": settled,
                    "candidates": cands,
                    "resolution": (
                        "settled" if settled
                        else ("relinkable" if (mid is None and len(cands) == 1)
                              else "needs_mark")
                    ),
                })
            return {
                "orphan_reports": len(items),
                "relinkable": sum(1 for i in items if i["resolution"] == "relinkable"),
                "needs_mark": sum(1 for i in items if i["resolution"] == "needs_mark"),
                "already_settled": sum(1 for i in items if i["settled"]),
                "items": items,
            }
        finally:
            if own:
                conn.close()

    # ── 端到端 run ──

    def run(self, dry_run: bool = False) -> dict[str, Any]:
        """scan → heal → verify 全流程，返回机器可验报告 dict。"""
        started = datetime.now(timezone.utc)
        before = self.scan()
        debt_before = self.report_debt()
        heal = self.heal(before, dry_run=dry_run)
        after = self.scan()
        debt_after = self.report_debt()
        conn = self._conn()
        try:
            fk = self.fk_coverage(conn)
            chain = self.audit_log_chain(conn)
        finally:
            if not isinstance(self.engine, Connection):
                conn.close()
        finished = datetime.now(timezone.utc)
        attempted = len(before)
        report_actions = [a for a in heal["actions"]
                          if a["target_table"] == "tensor_reports"]
        c5_linked = sum(1 for a in report_actions if a["strategy"] == "link_mission")
        c5_marked = sum(1 for a in report_actions if a["strategy"] == "mark_report_tag")
        c5_before = debt_before["orphan_reports"]
        c5_resolved_pct = (
            round(100.0 * (c5_linked + c5_marked) / c5_before, 2) if c5_before else 100.0
        )
        return {
            "run_id": self.run_id,
            "started_utc": started.isoformat(timespec="seconds"),
            "finished_utc": finished.isoformat(timespec="seconds"),
            "elapsed_s": round((finished - started).total_seconds(), 3),
            "scan": {
                "total": len(before),
                "by_rule": _count(v.rule_id for v in before),
                "by_severity": _count(v.severity for v in before),
                "violations": [v.to_dict() for v in before],
            },
            "heal": heal,
            "verify": {
                "total": len(after),
                "by_rule": _count(v.rule_id for v in after),
                "residual": [v.to_dict() for v in after],
            },
            "report_debt": {"before": debt_before, "after": debt_after},
            "fk_coverage": fk,
            "audit_log": chain,
            "criteria": {
                "C1_fk_coverage_pct": fk["coverage_pct"],
                "C1_uncovered_fks": len(fk["uncovered"]),
                "C1_ledger_fks": fk["ledger_fks"],
                "C1_out_of_scope_fks": fk["tenant_fks"],
                "C2_ghost_nodes_found": sum(1 for v in before if v.rule_id == "INV-N1"),
                "C3_heal_rate_pct": (round(100.0 * heal["applied"] / attempted, 2)
                                     if attempted else 100.0),
                "C4_residual_violations": len(after),
                "C5_orphan_reports_before": c5_before,
                "C5_orphan_reports_after": debt_after["orphan_reports"],
                "C5_reports_linked": c5_linked,
                "C5_reports_marked": c5_marked,
                "C5_resolved_pct": c5_resolved_pct,
                "C6_audit_entries": chain["entries"],
                "C6_chain_ok": chain["chain_ok"],
            },
        }


def format_markdown(report: dict[str, Any]) -> str:
    """把 run() 结果渲染成 markdown 摘要（供报告/审计面直接嵌入）。"""
    lines = [
        f"# 账本审计 run `{report['run_id']}`",
        "",
        f"- 扫描违反总数：**{report['scan']['total']}**",
        f"- 修复/销账执行：**{report['heal']['applied']}** / 跳过 {report['heal']['skipped']}",
        f"- 修复后残余违反：**{report['verify']['total']}**",
        f"- FK 覆盖：{report['fk_coverage']['coverage_pct']}%"
        f"（{report['fk_coverage']['fks_total']} 条外键，未覆盖 "
        f"{len(report['fk_coverage']['uncovered'])} 条）",
        f"- 审计日志：{report['audit_log']['entries']} 条，哈希链"
        f"{'完好' if report['audit_log']['chain_ok'] else '断裂'}",
        "",
        "## 按规则分布（修复前）",
        "",
        "| 规则 | 违反数 | 自愈策略 |",
        "|---|---:|---|",
    ]
    for rule in RULES:
        n = report["scan"]["by_rule"].get(rule.rule_id, 0)
        lines.append(f"| {rule.rule_id} {rule.title} | {n} | {rule.heal_strategy or '—'} |")
    lines += [
        "",
        "## 按规则分布（修复后）",
        "",
        "| 规则 | 残余违反数 |",
        "|---|---:|",
    ]
    for rule in RULES:
        n = report["verify"]["by_rule"].get(rule.rule_id, 0)
        lines.append(f"| {rule.rule_id} | {n} |")
    lines += ["", "## 报告欠账", ""]
    b, a = report["report_debt"]["before"], report["report_debt"]["after"]
    lines.append(f"- 修复前孤儿报告：{b['orphan_reports']}"
                 f"（可关联 {b['relinkable']} / 待标记 {b['needs_mark']}）")
    lines.append(f"- 修复后孤儿报告：{a['orphan_reports']}"
                 f"（已销账 {a['already_settled']}）")
    return "\n".join(lines) + "\n"