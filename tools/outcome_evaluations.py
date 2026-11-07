"""S×E 成果评价实体与记录流（AOG SPEC v1.2 §7.1 落机器）。

三件事，一个模块：

1. **实体**：``outcome_evaluations`` DDL（表）+ 字段语义。字段覆盖 §7.1「评价记录与
   治理要求」：成果 ID / 关联 Mission / 贡献者 / 成果等级（S1–S5 与否弃轨 S-N）/
   等级理由 / 证据级 E0–E4 / 独立审查者 / 对照基线或新颖性检索范围 / 适用边界 /
   记录与判定时间。
2. **判据**：``decide()`` 把 §7.1「分级判定规则」8/9 条与 S-N 门槛写成可执行规则
   （纯函数，无 DB、无 Web）。任一硬判据不过 → ``rejected``（无理由必拒）；
   合法但不到「已确认」门槛 → ``candidate``（登记但**不计入**分布）。
3. **统计**：``summarize()`` 只做**分层计数**（S 层 × E 层矩阵、否弃轨单列、
   待评价/候选分行）。硬编码 ``weighted_score=None`` / ``ranking=None``，
   不做加权、不做排名（§7.1「不得将 S1–S5 加权成单一排名」）。
   所有数字都来自 ``stats_queries()`` 返回的 SQL，Python 侧只重塑形状 → 可独立复算。

**零领域语义**：本模块不含任何具体学科/物理/业务词，等级语义由调用方（各领域系统）
按 §7.1 第 7 条提供，本模块只提供结构、记录与统计。测试夹具一律用合成域名。

边界（已知局限，写清楚不粉饰）：
- ``evidence_refs`` 只做**形状**校验（kind ∈ 白名单 + ref 非空），不解析引用内容是否
  真的支持该等级；引用可解析性由独立审查者负责（§7.1「独立审查者」栏留痕）。
- ``auto_scan`` 审查者类型可产出 confirmed（S1–S3），这是**机器证据扫描**确认，
  不是人工/同行独立评审。统计里单独分层（``by_reviewer_kind``），报告须如实标注。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

try:  # 允许以脚本/包两种方式运行（tools 目录直跑时同名导入）
    from tools.evidence_chain_link import (
        analyze_chain,
        below_grade_floor,
        cap_e_level,
    )
except ImportError:  # pragma: no cover
    from evidence_chain_link import (  # type: ignore
        analyze_chain,
        below_grade_floor,
        cap_e_level,
    )

# ─────────────────────────────── 常量 ───────────────────────────────

S_GRADES: tuple[str, ...] = ("S1", "S2", "S3", "S4", "S5")
S_NEGATIVE = "S-N"
REGISTRABLE_GRADES: tuple[str, ...] = S_GRADES + (S_NEGATIVE,)
E_LEVELS: tuple[str, ...] = ("E0", "E1", "E2", "E3", "E4")

#: 等级理由最小长度（按去空白字符数计，防空格填充凑数）
REASON_MIN_CHARS = 50

#: 证据引用类型白名单（与领域无关的通用证据载体）
EVIDENCE_KINDS: tuple[str, ...] = (
    "report",     # 平台报告
    "commit",     # 代码提交
    "artifact",   # 工件（JSON/图/数据）
    "sql",        # 可复算 SQL
    "run",        # 运行记录
    "doc",        # 文档
    "api",        # 线上端点
    "benchmark",  # 基准/对照实验
)

#: 审查者类型：independent=独立（人工/同行/跨主体）评审；auto_scan=机器证据扫描
REVIEWER_KINDS: tuple[str, ...] = ("independent", "auto_scan")

STATUSES: tuple[str, ...] = ("pending", "confirmed", "candidate", "rejected")
#: 计入成果分布的状态（候选不计入，§7.1 第 9 条）
COUNTED_STATUSES: tuple[str, ...] = ("confirmed",)

PENDING_REASON_CODE = "PENDING_NO_EVALUATION"

#: 统计窗口默认值：本仓近 30 单
DEFAULT_WINDOW = 30

_GRADE_RANK = {g: i for i, g in enumerate(S_GRADES, start=1)}
_E_RANK = {e: i for i, e in enumerate(E_LEVELS)}

TAB_MISSIONS = "tensor_dev_missions"
TAB_EVALS = "outcome_evaluations"

# ─────────────────────────────── DDL ───────────────────────────────

#: SQLite/PG 通用子集：JSON 以 TEXT 承载（Python 侧解析，跨引擎一致）。
DDL = f"""
CREATE TABLE IF NOT EXISTS {TAB_EVALS} (
    evaluation_id   TEXT PRIMARY KEY,
    artifact_id     TEXT NOT NULL,
    mission_id      BIGINT,
    contributor     TEXT NOT NULL,
    grade           TEXT,
    e_level         TEXT,
    grade_reason    TEXT,
    evidence_refs   TEXT NOT NULL DEFAULT '[]',
    reviewer        TEXT,
    reviewer_kind   TEXT,
    review_status   TEXT NOT NULL DEFAULT 'unreviewed',
    baseline_or_scope TEXT,
    boundary        TEXT,
    status          TEXT NOT NULL,
    reason_codes    TEXT NOT NULL DEFAULT '[]',
    reason_source   TEXT NOT NULL DEFAULT 'manual',
    repo            TEXT,
    organization_id BIGINT,
    window_finished_at TEXT,
    created_at      TEXT NOT NULL,
    decided_at      TEXT,
    CHECK (status IN ('pending','confirmed','candidate','rejected')),
    CHECK (grade IS NULL OR grade IN ('S1','S2','S3','S4','S5','S-N')),
    CHECK (e_level IS NULL OR e_level IN ('E0','E1','E2','E3','E4')),
    CHECK (reviewer_kind IS NULL OR reviewer_kind IN ('independent','auto_scan')),
    CHECK (status <> 'pending' OR (grade IS NULL AND e_level IS NULL)),
    CHECK (status IN ('pending','rejected') OR (grade IS NOT NULL AND e_level IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_outcome_eval_mission
    ON {TAB_EVALS} (mission_id, status);
CREATE INDEX IF NOT EXISTS idx_outcome_eval_grade
    ON {TAB_EVALS} (status, grade, e_level);
CREATE UNIQUE INDEX IF NOT EXISTS uq_outcome_eval_artifact_active
    ON {TAB_EVALS} (artifact_id) WHERE status <> 'rejected';
"""


def now_utc() -> str:
    """UTC ISO8601（秒精度，稳定可比较、跨引擎 TEXT 排序一致）。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ─────────────────────────── 文本/形状工具 ───────────────────────────


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _norm_len(text: str) -> int:
    """等级理由长度：去全部空白后计字符（防用空格/换行凑够 50 字）。"""
    return len("".join(text.split()))


def _canon_actor(name: str) -> str:
    """贡献者/审查者身份归一：小写、去空白、去 @ 后缀差异。"""
    out = "".join(str(name or "").split()).lower()
    return out.split("@")[0] if "@" in out else out


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def dumps_json(value: Any) -> str:
    return _dumps(value)


def loads_json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


# ─────────────────────────── 判据（§7.1） ───────────────────────────


@dataclass
class Verdict:
    """一次评价写入的判定结果。"""

    status: str                     # confirmed | candidate | rejected
    grade: str | None
    e_level: str | None
    codes: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        """非 rejected 视为**写入成功**（candidate 也是合法登记）。"""
        return self.status != "rejected"

    @property
    def counted(self) -> bool:
        """是否计入成果分布（候选不计入）。"""
        return self.status in COUNTED_STATUSES

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "accepted": self.accepted,
            "counted_in_distribution": self.counted,
            "grade": self.grade,
            "e_level": self.e_level,
            "reason_codes": list(self.codes),
        }


def normalize_evidence_refs(raw: Any) -> tuple[list[dict[str, str]], list[str]]:
    """证据引用归一 + 形状校验。返回 (refs, codes)。

    接受两种写法：``{"kind": "report", "ref": "123"}`` 或 ``"report:123"``。
    """
    codes: list[str] = []
    if raw is None:
        return [], ["EVIDENCE_REF_MISSING"]
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, (list, tuple)) or not raw:
        return [], ["EVIDENCE_REF_MISSING"]

    refs: list[dict[str, str]] = []
    bad = False
    for item in raw:
        kind = ref = ""
        if isinstance(item, Mapping):
            kind = _clean(item.get("kind")).lower()
            ref = _clean(item.get("ref"))
        elif isinstance(item, str):
            head, sep, tail = item.partition(":")
            if sep:
                kind, ref = head.strip().lower(), tail.strip()
            else:
                kind, ref = "", item.strip()
        else:
            bad = True
            continue
        if kind not in EVIDENCE_KINDS or not ref:
            bad = True
            continue
        refs.append({"kind": kind, "ref": ref})
    if not refs:
        codes.append("EVIDENCE_REF_INVALID" if bad else "EVIDENCE_REF_MISSING")
    elif bad:
        # 部分合法部分非法 → 整体按非法拒（不允许浑水摸鱼）
        codes.append("EVIDENCE_REF_INVALID")
    return refs, codes


def decide(payload: Mapping[str, Any]) -> Verdict:
    """§7.1 判据执行器（纯函数）。

    硬判据（任一不过 → rejected）：
      ARTIFACT_ID_MISSING / MISSION_ID_INVALID / CONTRIBUTOR_MISSING
      GRADE_MISSING / GRADE_INVALID / E_LEVEL_MISSING / E_LEVEL_INVALID
      REASON_MISSING / REASON_TOO_SHORT(去空白后 < 50 字符)
      EVIDENCE_REF_MISSING / EVIDENCE_REF_INVALID
      REVIEWER_MISSING / REVIEWER_KIND_INVALID / REVIEWER_NOT_INDEPENDENT(自评)
      E0_NOT_REGISTRABLE（§7.1 规则 8：任何等级须 E≥1）
      NEGATIVE_BELOW_E2（否定轨须 E≥2）
      BOUNDARY_MISSING（适用边界必填）
      BASELINE_REQUIRED（S2 须有对照基线；S4 须有基线）
      SCOPE_REQUIRED（S5 须有新颖性检索范围）

    证据链判据（IN59-A3，五元契约见 ``tools.evidence_chain_link``；只降不升）：
      S2+ 声明缺五元链 → E 级封顶 E1，并留 ``CHAIN_*`` 命中码（``CHAIN_ABSENT`` /
      ``CHAIN_MISSING_<元素>`` / ``CHAIN_INVALID_<元素>`` / ``CHAIN_RAW_DATA_MISSING``）；
      封顶后低于该 S 级的 E 门槛 → status=candidate（不计分布、不给信用）。

    软判据（合法但不到确认门槛 → candidate，登记不计入分布）：
      CANDIDATE_S3_NEEDS_E2 / CANDIDATE_S4_NEEDS_E2 / CANDIDATE_S5_NEEDS_E3
      CANDIDATE_NEEDS_INDEPENDENT_REVIEW（S4/S5/S-N 机器扫描不足以确认）
    """
    codes: list[str] = []

    artifact_id = _clean(payload.get("artifact_id"))
    if not artifact_id:
        codes.append("ARTIFACT_ID_MISSING")

    mission_raw = payload.get("mission_id")
    mission_id: int | None = None
    if mission_raw in (None, ""):
        codes.append("MISSION_ID_INVALID")
    else:
        try:
            mission_id = int(mission_raw)
            if mission_id <= 0:
                codes.append("MISSION_ID_INVALID")
        except (TypeError, ValueError):
            codes.append("MISSION_ID_INVALID")

    contributor = _clean(payload.get("contributor"))
    if not contributor:
        codes.append("CONTRIBUTOR_MISSING")

    grade_raw = _clean(payload.get("grade")).upper()
    grade: str | None = None
    if not grade_raw:
        codes.append("GRADE_MISSING")
    elif grade_raw in REGISTRABLE_GRADES:
        grade = grade_raw
    else:
        codes.append("GRADE_INVALID")

    e_raw = _clean(payload.get("e_level")).upper()
    e_level: str | None = None
    if not e_raw:
        codes.append("E_LEVEL_MISSING")
    elif e_raw in E_LEVELS:
        e_level = e_raw
    else:
        codes.append("E_LEVEL_INVALID")

    reason = _clean(payload.get("grade_reason"))
    if not reason:
        codes.append("REASON_MISSING")
    elif _norm_len(reason) < REASON_MIN_CHARS:
        codes.append("REASON_TOO_SHORT")

    refs, ref_codes = normalize_evidence_refs(payload.get("evidence_refs"))
    codes.extend(ref_codes)

    reviewer = _clean(payload.get("reviewer"))
    reviewer_kind = _clean(payload.get("reviewer_kind") or "independent").lower()
    if not reviewer:
        codes.append("REVIEWER_MISSING")
    if reviewer_kind not in REVIEWER_KINDS:
        codes.append("REVIEWER_KIND_INVALID")
    elif reviewer and contributor and _canon_actor(reviewer) == _canon_actor(contributor):
        codes.append("REVIEWER_NOT_INDEPENDENT")

    boundary = _clean(payload.get("boundary"))
    if not boundary:
        codes.append("BOUNDARY_MISSING")

    baseline = _clean(payload.get("baseline_or_scope"))

    # 五元证据链体检：链不全 → E 封顶（只降不升）。S1 不要求链，故不产生命中码。
    chain = analyze_chain(payload.get("evidence_chain"), grade)
    chain_codes: list[str] = list(chain.codes)
    if chain.cap:
        e_level = cap_e_level(e_level, chain.cap)   # 只降不升：缺件 → E 至多 E1

    if e_level and grade:
        e_rank = _E_RANK[e_level]
        if e_rank < 1:
            codes.append("E0_NOT_REGISTRABLE")
        if grade == S_NEGATIVE and e_rank < 2:
            codes.append("NEGATIVE_BELOW_E2")
        if grade in ("S2", "S4") and not baseline:
            codes.append("BASELINE_REQUIRED")
        if grade == "S5" and not baseline:
            codes.append("SCOPE_REQUIRED")

    if codes:
        return Verdict(status="rejected", grade=grade, e_level=e_level, codes=_uniq(codes))

    assert grade is not None and e_level is not None  # 硬判据保证
    e_rank = _E_RANK[e_level]
    candidate_codes: list[str] = []
    if grade == "S3" and e_rank < 2:
        candidate_codes.append("CANDIDATE_S3_NEEDS_E2")
    if grade == "S4":
        if e_rank < 2:
            candidate_codes.append("CANDIDATE_S4_NEEDS_E2")
        if reviewer_kind != "independent":
            candidate_codes.append("CANDIDATE_NEEDS_INDEPENDENT_REVIEW")
    if grade == "S5":
        if e_rank < 3:
            candidate_codes.append("CANDIDATE_S5_NEEDS_E3")
        if reviewer_kind != "independent":
            candidate_codes.append("CANDIDATE_NEEDS_INDEPENDENT_REVIEW")
    if grade == S_NEGATIVE and reviewer_kind != "independent":
        candidate_codes.append("CANDIDATE_NEEDS_INDEPENDENT_REVIEW")
    if chain.cap and below_grade_floor(grade, e_level):
        # 链不全把 E 压到该 S 级门槛之下 → 只能 candidate（拦截而不放行）
        candidate_codes.append("CANDIDATE_CHAIN_CAP_BELOW_GRADE")

    return Verdict(
        status="candidate" if candidate_codes else "confirmed",
        grade=grade,
        e_level=e_level,
        codes=_uniq(candidate_codes + chain_codes),
    )


def _uniq(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


# ─────────────────────── 待评价记录自动生成 ───────────────────────


def artifact_id_for_mission(mission_id: int | str) -> str:
    """一张 mission 的默认成果 ID（一 mission 一主成果行；多成果可另行登记）。"""
    return f"mission:{int(mission_id)}"


def evaluation_id_for(artifact_id: str) -> str:
    """确定性评价 ID（UUIDv5）→ 同一成果重放不产生重复行（幂等）。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"outcome-eval:{artifact_id}"))


def pending_record_from_mission(mission: Mapping[str, Any], *, artifact_id: str | None = None) -> dict[str, Any]:
    """succeeded mission → 待评价记录（status='pending'，等级留空待写入）。

    字段只从 mission 行取可核事实（id/repo/contributor/完结时间），
    **不预填等级**（预填会让「待评价」变自证）。
    """
    mission_id = int(mission["id"])
    artifact = _clean(artifact_id) or artifact_id_for_mission(mission_id)
    contributor = _clean(
        mission.get("claimed_by") or mission.get("worker_id") or mission.get("agent_id") or ""
    ) or "unattributed"
    finished = mission.get("finished_at")
    if isinstance(finished, datetime):
        finished = finished.replace(microsecond=0).isoformat()
    created = now_utc()
    return {
        "evaluation_id": evaluation_id_for(artifact),
        "artifact_id": artifact,
        "mission_id": mission_id,
        "contributor": contributor,
        "grade": None,
        "e_level": None,
        "grade_reason": None,
        "evidence_refs": "[]",
        "reviewer": None,
        "reviewer_kind": None,
        "review_status": "unreviewed",
        "baseline_or_scope": None,
        "boundary": None,
        "status": "pending",
        "reason_codes": _dumps([PENDING_REASON_CODE]),
        "reason_source": "auto_pending",
        "repo": _clean(mission.get("repo")) or None,
        "organization_id": mission.get("organization_id"),
        "window_finished_at": str(finished) if finished else None,
        "created_at": created,
        "decided_at": None,
    }


def record_from_verdict(
    payload: Mapping[str, Any],
    verdict: Verdict,
    *,
    evaluation_id: str | None = None,
    reason_source: str = "manual",
    created_at: str | None = None,
    window_finished_at: str | None = None,
) -> dict[str, Any]:
    """payload + verdict → 可入库行（rejected 也留痕，便于审计负例）。"""
    raw_artifact = payload.get("artifact_id")
    artifact_id = (_clean(raw_artifact)
                   or artifact_id_for_mission(payload.get("mission_id") or 0))
    refs, _ = normalize_evidence_refs(payload.get("evidence_refs"))
    ts = now_utc()
    decided = None if verdict.status == "pending" else ts
    try:
        mission_id = int(payload.get("mission_id"))
    except (TypeError, ValueError):
        mission_id = None
    if not evaluation_id:
        # rejected 是「一次被拒尝试」的留痕，可与该成果的 pending 行共存 → 必须唯一；
        # 有效判定则按成果确定性 ID（重放幂等，pending 行就地升级）。
        if verdict.status == "rejected":
            evaluation_id = f"{artifact_id}:rejected:{uuid.uuid4().hex[:12]}"
        else:
            evaluation_id = evaluation_id_for(artifact_id)
    return {
        "evaluation_id": evaluation_id,
        "artifact_id": artifact_id,
        "mission_id": mission_id,
        "contributor": _clean(payload.get("contributor")) or "unattributed",
        "grade": verdict.grade,
        "e_level": verdict.e_level,
        "grade_reason": _clean(payload.get("grade_reason")) or None,
        "evidence_refs": _dumps(refs),
        "reviewer": _clean(payload.get("reviewer")) or None,
        "reviewer_kind": (_clean(payload.get("reviewer_kind") or "independent").lower() or None),
        "review_status": "unreviewed" if verdict.status == "rejected" else "reviewed",
        "baseline_or_scope": _clean(payload.get("baseline_or_scope")) or None,
        "boundary": _clean(payload.get("boundary")) or None,
        "status": verdict.status,
        "reason_codes": _dumps(verdict.codes),
        "reason_source": reason_source,
        "repo": _clean(payload.get("repo")) or None,
        "organization_id": payload.get("organization_id"),
        "window_finished_at": window_finished_at,
        "created_at": created_at or ts,
        "decided_at": decided,
    }


# 入库列顺序（API 与回填共用，避免两处漂移）
COLUMNS: tuple[str, ...] = (
    "evaluation_id", "artifact_id", "mission_id", "contributor", "grade", "e_level",
    "grade_reason", "evidence_refs", "reviewer", "reviewer_kind", "review_status",
    "baseline_or_scope", "boundary", "status", "reason_codes", "reason_source",
    "repo", "organization_id", "window_finished_at", "created_at", "decided_at",
)


def insert_sql() -> str:
    cols = ", ".join(COLUMNS)
    vals = ", ".join(f":{c}" for c in COLUMNS)
    return f"INSERT INTO {TAB_EVALS} ({cols}) VALUES ({vals})"


def insert_if_absent_sql() -> str:
    """幂等插入：同一 (artifact_id) 已有非 rejected 行则跳过（跨引擎可用的写法）。"""
    cols = ", ".join(COLUMNS)
    vals = ", ".join(f":{c}" for c in COLUMNS)
    return (
        f"INSERT INTO {TAB_EVALS} ({cols}) "
        f"SELECT {vals} WHERE NOT EXISTS ("
        f"SELECT 1 FROM {TAB_EVALS} WHERE artifact_id = :artifact_id AND status <> 'rejected')"
    )


def upgrade_pending_sql() -> str:
    """就地判定/重判已有有效行（pending 升级或再评价；保留记录流连续性，不新增行）。

    条件 ``status <> 'rejected'``：同成果至多一条有效记录（分部唯一索引同口径），
    因此重放/重判是幂等的；被拒尝试另起新行留痕。
    """
    return (
        f"UPDATE {TAB_EVALS} SET "
        "evaluation_id = :evaluation_id, contributor = :contributor, grade = :grade, "
        "e_level = :e_level, grade_reason = :grade_reason, evidence_refs = :evidence_refs, "
        "reviewer = :reviewer, reviewer_kind = :reviewer_kind, review_status = :review_status, "
        "baseline_or_scope = :baseline_or_scope, boundary = :boundary, status = :status, "
        "reason_codes = :reason_codes, reason_source = :reason_source, repo = :repo, "
        "organization_id = :organization_id, "
        "window_finished_at = COALESCE(window_finished_at, :window_finished_at), "
        "decided_at = :decided_at "
        "WHERE artifact_id = :artifact_id AND status <> 'rejected'"
    )


def store_record(conn, record: Mapping[str, Any], *, commit: bool = False) -> bool:
    """落库一条判定结果（API 与回填脚本共用，避免两处漂移）。

    - rejected → 直接 INSERT（每次被拒尝试一行留痕，不占用分部唯一索引）
    - 同成果已有 pending 行 → UPDATE 就地判定
    - 否则 INSERT（受分部唯一索引保护）

    返回 True 表示就地升级了 pending 行。``conn`` 为 SQLAlchemy Connection/Session。
    """
    from sqlalchemy import text

    if record.get("status") == "rejected":
        conn.execute(text(insert_sql()), dict(record))
        if commit:
            conn.commit()
        return False
    updated = conn.execute(text(upgrade_pending_sql()), dict(record)).rowcount
    if not updated:
        conn.execute(text(insert_sql()), dict(record))
    if commit:
        conn.commit()
    return bool(updated)


# ─────────────────────────────── 统计 ───────────────────────────────


def _window_cte(org_filter: bool, include_rejected: bool = False) -> str:
    org = "AND m.organization_id = :org " if org_filter else ""
    active_filter = "1 = 1" if include_rejected else "e.status <> 'rejected'"
    return (
        f"WITH win AS (\n"
        f"  SELECT m.id AS mission_id, m.finished_at AS finished_at\n"
        f"  FROM {TAB_MISSIONS} m\n"
        f"  WHERE m.repo = :repo AND m.status = 'succeeded' {org}\n"
        f"  ORDER BY m.finished_at DESC NULLS LAST, m.id DESC\n"
        f"  LIMIT :window\n"
        f"), active AS (\n"
        f"  SELECT e.* FROM {TAB_EVALS} e\n"
        f"  JOIN win ON win.mission_id = e.mission_id\n"
        f"  WHERE {active_filter}\n"
        f")\n"
    )


def stats_queries(*, org_filter: bool = False) -> dict[str, str]:
    """统计用 SQL（分层计数，全部可独立执行复算）。"""
    w = _window_cte(org_filter)
    return {
        "window": (
            f"{w}SELECT count(*) AS missions FROM win"
        ),
        "coverage": (
            f"{w}SELECT\n"
            f"  (SELECT count(*) FROM win) AS missions,\n"
            f"  (SELECT count(DISTINCT mission_id) FROM active) AS missions_with_record,\n"
            f"  (SELECT count(DISTINCT mission_id) FROM active WHERE status = 'pending') AS missions_pending,\n"
            f"  (SELECT count(DISTINCT mission_id) FROM active WHERE status IN ('confirmed','candidate')) AS missions_decided,\n"
            f"  (SELECT count(*) FROM active WHERE status = 'pending') AS records_pending,\n"
            f"  (SELECT count(*) FROM active WHERE status = 'confirmed') AS records_confirmed,\n"
            f"  (SELECT count(*) FROM active WHERE status = 'candidate') AS records_candidate"
        ),
        "by_grade": (
            f"{w}SELECT COALESCE(grade, 'PENDING') AS grade, status, count(*) AS n\n"
            f"FROM active GROUP BY grade, status ORDER BY grade, status"
        ),
        "by_e_level": (
            f"{w}SELECT COALESCE(e_level, 'PENDING') AS e_level, status, count(*) AS n\n"
            f"FROM active GROUP BY e_level, status ORDER BY e_level, status"
        ),
        "matrix": (
            f"{w}SELECT COALESCE(grade, 'PENDING') AS grade, COALESCE(e_level, 'PENDING') AS e_level,\n"
            f"       status, count(*) AS n\n"
            f"FROM active GROUP BY grade, e_level, status ORDER BY grade, e_level, status"
        ),
        "by_reviewer_kind": (
            f"{w}SELECT COALESCE(reviewer_kind, 'none') AS reviewer_kind, status, count(*) AS n\n"
            f"FROM active GROUP BY reviewer_kind, status ORDER BY reviewer_kind, status"
        ),
        "by_reason_source": (
            f"{w}SELECT reason_source, status, count(*) AS n\n"
            f"FROM active GROUP BY reason_source, status ORDER BY reason_source, status"
        ),
        "attempts": (
            f"{_window_cte(org_filter, include_rejected=True)}"
            f"SELECT status, count(*) AS n FROM active GROUP BY status ORDER BY status"
        ),
        "records": (
            f"{w}SELECT e.evaluation_id, e.artifact_id, e.mission_id, e.contributor, e.grade,\n"
            f"       e.e_level, e.status, e.reviewer, e.reviewer_kind, e.reason_source,\n"
            f"       e.reason_codes, e.decided_at\n"
            f"FROM active e ORDER BY e.mission_id DESC, e.evaluation_id"
        ),
    }


def summarize(conn, *, repo: str, org: int | None = None, window: int = DEFAULT_WINDOW) -> dict[str, Any]:
    """近 ``window`` 张 succeeded mission 的 S×E 分布（分层计数，禁加权禁排名）。

    ``conn`` 需支持 ``execute(text, params).mappings().all()``（SQLAlchemy Connection/Session）。
    """
    from sqlalchemy import text  # 局部导入：纯逻辑部分（decide）不依赖 sqlalchemy

    params = {"repo": repo, "window": int(window)}
    if org is not None:
        params["org"] = int(org)
    queries = stats_queries(org_filter=org is not None)

    def rows(name: str) -> list[dict[str, Any]]:
        return [dict(r) for r in conn.execute(text(queries[name]), params).mappings().all()]

    coverage = dict(rows("coverage")[0]) if rows("coverage") else {}
    win_rows = rows("window")
    missions = int(win_rows[0]["missions"]) if win_rows else 0

    def ladder(name: str, key: str, domain: Sequence[str]) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {v: {} for v in domain}
        out["PENDING"] = {}
        for r in rows(name):
            bucket = str(r[key])
            out.setdefault(bucket, {})
            out[bucket][str(r["status"])] = int(r["n"])
        return out

    matrix: dict[str, dict[str, dict[str, int]]] = {}
    for r in rows("matrix"):
        g, e, st, n = str(r["grade"]), str(r["e_level"]), str(r["status"]), int(r["n"])
        matrix.setdefault(g, {}).setdefault(e, {})[st] = n

    # 分布只数 confirmed；candidate/pending 单列，不混入
    confirmed_grade: dict[str, int] = {g: 0 for g in REGISTRABLE_GRADES}
    confirmed_e: dict[str, int] = {e: 0 for e in E_LEVELS}
    for g, inner in matrix.items():
        for e, st in inner.items():
            n = st.get("confirmed", 0)
            if g in confirmed_grade:
                confirmed_grade[g] += n
            if e in confirmed_e:
                confirmed_e[e] += n

    records = rows("records")
    return {
        "schema": "outcome_evaluations.stats.v1",
        "repo": repo,
        "window": int(window),
        "missions_in_window": missions,
        "coverage": {k: int(v or 0) for k, v in coverage.items()},
        "distribution_confirmed": {
            "by_grade": confirmed_grade,
            "by_e_level": confirmed_e,
            "total": sum(confirmed_grade.values()),
        },
        "sxe_matrix_confirmed": {
            g: {e: matrix.get(g, {}).get(e, {}).get("confirmed", 0) for e in E_LEVELS}
            for g in REGISTRABLE_GRADES
        },
        "sxe_matrix_candidate": {
            g: {e: matrix.get(g, {}).get(e, {}).get("candidate", 0) for e in E_LEVELS}
            for g in REGISTRABLE_GRADES
        },
        "ladders": {
            "by_grade": ladder("by_grade", "grade", REGISTRABLE_GRADES),
            "by_e_level": ladder("by_e_level", "e_level", E_LEVELS),
            "by_reviewer_kind": ladder("by_reviewer_kind", "reviewer_kind", REVIEWER_KINDS),
            "by_reason_source": ladder("by_reason_source", "reason_source", ("manual", "rule_derived", "auto_pending")),
        },
        "records": records,
        "policy": {
            "weighting": "forbidden",
            "ranking": "forbidden",
            "weighted_score": None,
            "ranking": None,
            "note": "§7.1：S1–S5 不得加权成单一分数或排名；分布仅分层计数，候选与待评价不计入 confirmed 分布。",
        },
        "sql": queries,
        "sql_params": params,
    }


def render_markdown(stats: Mapping[str, Any]) -> str:
    """把统计结果渲染成报告用分层表（无比率排名、无加权）。"""
    lines: list[str] = []
    cov = stats["coverage"]
    lines.append(f"仓库 `{stats['repo']}` · 窗口近 {stats['window']} 单 succeeded mission")
    lines.append("")
    lines.append(f"- 窗口 mission 数：**{stats['missions_in_window']}**")
    lines.append(f"- 已生成待评价记录：{cov.get('missions_with_record', 0)} 张 mission")
    lines.append(
        f"- 已判定（confirmed/candidate）：{cov.get('missions_decided', 0)}；"
        f"仍待评价：{cov.get('missions_pending', 0)}"
    )
    lines.append(
        f"- 记录行：confirmed {cov.get('records_confirmed', 0)} / "
        f"candidate {cov.get('records_candidate', 0)} / pending {cov.get('records_pending', 0)}"
    )
    lines.append("")
    lines.append("**S 等级分布（confirmed）**")
    lines.append("")
    lines.append("| 等级 | 数量 |")
    lines.append("|---|---|")
    for g, n in stats["distribution_confirmed"]["by_grade"].items():
        lines.append(f"| {g} | {n} |")
    lines.append("")
    lines.append("**E 等级分布（confirmed）**")
    lines.append("")
    lines.append("| 证据级 | 数量 |")
    lines.append("|---|---|")
    for e, n in stats["distribution_confirmed"]["by_e_level"].items():
        lines.append(f"| {e} | {n} |")
    lines.append("")
    lines.append("**S×E 矩阵（confirmed 计数；括号内为 candidate）**")
    lines.append("")
    header = "| S\\E | " + " | ".join(E_LEVELS) + " |"
    lines.append(header)
    lines.append("|" + "---|" * (len(E_LEVELS) + 1))
    for g in REGISTRABLE_GRADES:
        cells = []
        for e in E_LEVELS:
            c = stats["sxe_matrix_confirmed"][g][e]
            cand = stats["sxe_matrix_candidate"][g][e]
            cells.append(f"{c}" + (f" ({cand})" if cand else ""))
        lines.append(f"| {g} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("> 禁加权、禁排名：上表为分层计数，不提供任何合成分数或主体排名。")
    return "\n".join(lines)


__all__ = [
    "COLUMNS",
    "COUNTED_STATUSES",
    "DDL",
    "DEFAULT_WINDOW",
    "EVIDENCE_KINDS",
    "E_LEVELS",
    "PENDING_REASON_CODE",
    "REASON_MIN_CHARS",
    "REGISTRABLE_GRADES",
    "REVIEWER_KINDS",
    "S_GRADES",
    "S_NEGATIVE",
    "STATUSES",
    "Verdict",
    "artifact_id_for_mission",
    "decide",
    "dumps_json",
    "evaluation_id_for",
    "insert_if_absent_sql",
    "insert_sql",
    "loads_json",
    "normalize_evidence_refs",
    "now_utc",
    "pending_record_from_mission",
    "record_from_verdict",
    "render_markdown",
    "stats_queries",
    "store_record",
    "summarize",
    "upgrade_pending_sql",
]