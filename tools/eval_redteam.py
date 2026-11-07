"""评价真实性红队抽样复算（IN24-E5）—— 对 `outcome_evaluations` 已确认行做分层盲序独立复算。

问题：E1 把 S×E 评价落成了表 + API + 回填，但**评价本身是否可信**没有被检验。
本模块是评价的评价（红队）：从 `status='confirmed'` 行分层抽样，用**独立实现**的
证据口径重算 E 级与 S 级天花板，逐项复算 `grade_reason` 里出现的数字，再与申报值对照。

四条纪律
--------
1. **盲序（blind）**：复算器只吃事实（mission 行 / 报告 / 增量 / 证据引用解析），
   `blind_payload()` 强制剥离 grade / e_level / grade_reason / reviewer —— 先重算、后对照，
   避免「照着答案找证据」。抽样顺序按 `sha256(seed:evaluation_id)` 重排，即盲序。
2. **独立实现**：等级阶梯与类别映射在本模块**另行实现**（`CATEGORY_GRADE`、
   `confirm_ceiling`），不复用回填器的 `S_BY_CATEGORY`；两份映射若不一致，
   `rule_drift()` 单独报 `RULE_MAPPING_DRIFT`（评价规则本身漂移也是发现）。
3. **只降不升**：红队只做降级/否决，不做追认。申报低于可证天花板 → `UNDERCLAIM`（只记录）。
4. **零领域语义**：本模块只认「记录 / 证据 / 等级」，不认识任何学科词；领域词随数据注入。

判定策略（先于实现，机器可裁）
------------------------------
| 情形 | 判定 | 动作 |
|---|---|---|
| 证据引用不可解析（report id 不存在 / mission 不符） | `REJECT_BROKEN_EVIDENCE` | status→rejected |
| 申报等级 > 可证天花板（如 S3 而 E<2 / 无增量） | `REJECT_UPGRADE` | status→rejected |
| 申报 E > 重算 E | `DOWNGRADE_EVIDENCE` | e_level→重算值（S 同步夹到新天花板） |
| `grade_reason` 数字/缺失声明与事实不符 | `DOWNGRADE_REASON` | e_level 降一级（下限 E1） |
| 申报 < 可证值 | `UNDERCLAIM` | 无动作，仅留档 |
| 全部对上 | `MATCH` | 无动作 |

复算记录落 `eval_redteam_rechecks`（迁移 104）：抽样层、盲序号、申报值/重算值、
判定、动作、不匹配码、数字对照 —— 每条抽样单一行，含 0 发现（MATCH）也留档。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

E_LEVELS: tuple[str, ...] = ("E0", "E1", "E2", "E3", "E4")
GRADES: tuple[str, ...] = ("S1", "S2", "S3", "S4", "S5", "S-N")
GRADE_ORDER: dict[str, int] = {"S1": 1, "S2": 2, "S3": 3, "S4": 4, "S5": 5}
E_ORDER: dict[str, int] = {e: i for i, e in enumerate(E_LEVELS)}

REDTEAM_ID = "eval-redteam-recompute"
SCAN_CAP = "E2"          # 机器扫描上限：E3/E4 需外部复现/独立复算，扫描不可自证
MIN_SAMPLE = 10          # 验收判据下限：抽样 ≥10 单
DEFAULT_SAMPLE = 12

# 独立实现的「能力增量类别 → 等级」映射（§7.1 结构判据）。与回填器的映射互为交叉校验。
CATEGORY_GRADE: dict[str, str] = {
    "new_capability": "S3",
    "reusable_knowledge": "S3",
    "capability_extension": "S2",
    "defect_fix": "S2",
    "falsified_hypothesis": "S-N",
    "no_substantive_delta": "S1",
}
DEFAULT_GRADE = "S1"

# 各等级在「已确认」轨上所需的最小证据级
GRADE_MIN_E: dict[str, int] = {"S1": 1, "S2": 1, "S3": 2, "S4": 2, "S5": 3, "S-N": 2}
BASELINE_GRADES = frozenset({"S2", "S4"})     # 须声明对照基线
SCOPE_GRADES = frozenset({"S5"})              # 须声明检索范围
INDEPENDENT_GRADES = frozenset({"S4", "S5", "S-N"})  # 须独立评审/复核

BLIND_FORBIDDEN = ("grade", "e_level", "grade_reason", "reviewer",
                   "reason_codes", "review_status")

VERDICTS = ("MATCH", "UNDERCLAIM", "DOWNGRADE_EVIDENCE", "DOWNGRADE_REASON",
            "REJECT_UPGRADE", "REJECT_BROKEN_EVIDENCE")

RECHECK_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS eval_redteam_rechecks (
        recheck_id        TEXT PRIMARY KEY,
        run_id            TEXT NOT NULL,
        evaluation_id     TEXT NOT NULL,
        artifact_id       TEXT,
        mission_id        BIGINT,
        repo              TEXT,
        stratum           TEXT NOT NULL,
        blind_order       INTEGER NOT NULL,
        declared_grade    TEXT,
        declared_e        TEXT,
        recomputed_grade  TEXT,
        recomputed_e      TEXT,
        verdict           TEXT NOT NULL,
        action            TEXT NOT NULL,
        mismatch_codes    TEXT NOT NULL DEFAULT '[]',
        numbers_declared  TEXT NOT NULL DEFAULT '{}',
        numbers_recomputed TEXT NOT NULL DEFAULT '{}',
        blind_payload     TEXT NOT NULL DEFAULT '{}',
        reviewer          TEXT NOT NULL,
        applied           INTEGER NOT NULL DEFAULT 0,
        created_at        TEXT NOT NULL
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_eval_redteam_recheck
        ON eval_redteam_rechecks (evaluation_id, run_id)
    """,
)


# ──────────────────────────── 事实与记录 ────────────────────────────

@dataclass(frozen=True)
class ReportFacts:
    report_id: int
    status: str
    chars: int
    figures: int


@dataclass(frozen=True)
class MissionFacts:
    """复算器的**全部**输入：只有平台事实，不含任何申报等级。"""
    mission_id: int
    mission_status: str
    finished_at: str | None
    contributor: str | None
    repo: str | None
    reports: tuple[ReportFacts, ...] = ()
    delta_categories: tuple[str, ...] = ()
    delta_artifacts: int = 0
    broken_refs: tuple[str, ...] = ()
    unverifiable_refs: tuple[str, ...] = ()
    baseline_declared: bool = False
    scope_declared: bool = False
    reviewer_kind: str | None = None    # 审查类型是记录属性（非等级），用于判独立评审门槛

    @property
    def published_reports(self) -> tuple[ReportFacts, ...]:
        return tuple(r for r in self.reports if r.status == "published")

    @property
    def primary_report(self) -> ReportFacts | None:
        pub = self.published_reports
        if pub:
            return max(pub, key=lambda r: r.report_id)
        return max(self.reports, key=lambda r: r.report_id) if self.reports else None

    @property
    def delta_category(self) -> str | None:
        return self.delta_categories[0] if self.delta_categories else None


@dataclass(frozen=True)
class Record:
    """待复核的评价记录（含申报值；**不得**进入盲序复算）。"""
    evaluation_id: str
    artifact_id: str
    mission_id: int | None
    contributor: str | None
    grade: str | None
    e_level: str | None
    grade_reason: str | None
    evidence_refs: tuple[Mapping[str, Any], ...] = ()
    baseline_or_scope: str | None = None
    boundary: str | None = None
    reviewer: str | None = None
    reviewer_kind: str | None = None
    reason_codes: tuple[str, ...] = ()
    repo: str | None = None

    @property
    def stratum(self) -> str:
        return f"{self.grade or 'NA'}/{self.e_level or 'NA'}"


@dataclass(frozen=True)
class Recheck:
    evaluation_id: str
    artifact_id: str
    mission_id: int | None
    repo: str | None
    stratum: str
    blind_order: int
    declared_grade: str | None
    declared_e: str | None
    recomputed_grade: str | None
    recomputed_e: str | None
    verdict: str
    action: str
    mismatch_codes: tuple[str, ...] = ()
    numbers_declared: Mapping[str, Any] = field(default_factory=dict)
    numbers_recomputed: Mapping[str, Any] = field(default_factory=dict)
    blind_payload: Mapping[str, Any] = field(default_factory=dict)


# ──────────────────────────── 盲序 ────────────────────────────

def blind_payload(facts: MissionFacts) -> dict[str, Any]:
    """把事实压成盲序输入：**结构上**不可能带申报等级（键名硬校验）。"""
    payload = {
        "mission_id": facts.mission_id,
        "mission_status": facts.mission_status,
        "finished_at": facts.finished_at,
        "contributor": facts.contributor,
        "repo": facts.repo,
        "reports": [{"id": r.report_id, "status": r.status, "chars": r.chars, "figures": r.figures}
                    for r in facts.reports],
        "delta_categories": list(facts.delta_categories),
        "delta_artifacts": facts.delta_artifacts,
        "broken_refs": list(facts.broken_refs),
        "unverifiable_refs": list(facts.unverifiable_refs),
        "baseline_declared": facts.baseline_declared,
        "scope_declared": facts.scope_declared,
        "reviewer_kind": facts.reviewer_kind,
    }
    leaked = [k for k in BLIND_FORBIDDEN if k in payload]
    if leaked:  # pragma: no cover - 结构性护栏
        raise AssertionError(f"blind payload leaked declared grade fields: {leaked}")
    return payload


def blind_key(seed: str, evaluation_id: str) -> str:
    return hashlib.sha256(f"{seed}:{evaluation_id}".encode("utf-8")).hexdigest()


# ──────────────────────────── 独立重算 ────────────────────────────

def recompute_evidence_level(facts: MissionFacts) -> tuple[str, tuple[str, ...]]:
    """证据级重算：已发布报告 + 增量 双证据 → E2；仅其一 → E1；都无 → E0（扫描上限 E2）。"""
    has_report = bool(facts.published_reports)
    has_delta = bool(facts.delta_categories)
    codes: list[str] = []
    if has_report and has_delta:
        level = "E2"
    elif has_report or has_delta:
        level = "E1"
    else:
        level = "E0"
    if facts.reports and not facts.published_reports:
        codes.append("REPORT_NOT_PUBLISHED")
    if not has_delta:
        codes.append("NO_DELTA_RECORD")
    return level, tuple(codes)


def confirm_ceiling(*, e_level: str, category: str | None, baseline_declared: bool,
                    scope_declared: bool, reviewer_kind: str | None) -> str | None:
    """在给定证据/审查条件下「已确认」轨可成立的最高 S 级；None = 不得确认。"""
    e_rank = E_ORDER.get(e_level or "E0", 0)
    if e_rank <= 0:
        return None
    target = CATEGORY_GRADE.get(category or "", DEFAULT_GRADE)
    independent = reviewer_kind == "independent"

    if target == "S-N":
        # 否定轨：须 E≥2 且独立复核，否则不得进入已确认分布
        return "S-N" if (e_rank >= GRADE_MIN_E["S-N"] and independent) else None

    reachable = 1
    for grade, need in GRADE_MIN_E.items():
        if grade == "S-N" or GRADE_ORDER[grade] > GRADE_ORDER[target]:
            continue
        if e_rank < need:
            continue
        if grade in BASELINE_GRADES and not baseline_declared:
            continue
        if grade in SCOPE_GRADES and not scope_declared:
            continue
        if grade in INDEPENDENT_GRADES and not independent:
            continue
        reachable = max(reachable, GRADE_ORDER[grade])
    return f"S{reachable}"


def recompute_grade(facts: MissionFacts) -> tuple[str | None, tuple[str, ...]]:
    """S 级重算：类别映射 ⊕ 等级门槛 ⊕ 独立评审/基线/范围要求 → 可确认天花板。"""
    e_level, codes = recompute_evidence_level(facts)
    ceiling = confirm_ceiling(
        e_level=e_level,
        category=facts.delta_category,
        baseline_declared=facts.baseline_declared,
        scope_declared=facts.scope_declared,
        reviewer_kind=facts.reviewer_kind,
    )
    if ceiling is None:
        codes = codes + ("NO_CONFIRMABLE_GRADE",)
    return ceiling, codes


def rule_drift(official_mapping: Mapping[str, str]) -> list[dict[str, str]]:
    """独立映射 vs 回填器映射的漂移清单（空 = 两份口径一致）。"""
    drift: list[dict[str, str]] = []
    for category in sorted(set(official_mapping) | set(CATEGORY_GRADE)):
        mine = CATEGORY_GRADE.get(category)
        theirs = official_mapping.get(category)
        if mine != theirs:
            drift.append({"category": category, "redteam": str(mine), "official": str(theirs)})
    return drift


# ──────────────────────────── 理由数字复算 ────────────────────────────

_NUM_PATTERNS: tuple[tuple[str, str], ...] = (
    ("mission_id", r"mission\s+(\d+)"),
    ("finished_at", r"于\s*([0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9:.+\-]+)\s*完结"),
    ("contributor", r"贡献者\s+([^\s；;，,]+)"),
    ("report_id", r"#(\d+)"),
    ("report_chars", r"正文\s*(\d+)\s*字符"),
    ("report_figures", r"图\s*(\d+)\s*张"),
    ("delta_category", r"能力增量类别\s*([^\s（(]+)"),
    ("delta_artifacts", r"工件\s*(\d+)\s*项"),
)


def parse_reason_numbers(reason: str | None) -> dict[str, Any]:
    """从 grade_reason 抽取可复算的数字/标识（抽取失败即缺项，不猜）。"""
    text = reason or ""
    out: dict[str, Any] = {}
    for key, pattern in _NUM_PATTERNS:
        match = re.search(pattern, text)
        if match:
            raw = match.group(1).strip()
            out[key] = int(raw) if raw.isdigit() else raw
    if "未关联已发布报告" in text:
        out["report_published_claim"] = False
    elif "已发布" in text:
        out["report_published_claim"] = True
    if "未登记" in text and "能力增量类别" in text:
        out["delta_registered_claim"] = False
    return out


def recompute_reason_numbers(facts: MissionFacts, declared: Mapping[str, Any]
                             ) -> tuple[dict[str, Any], tuple[str, ...]]:
    """按事实复算理由里的数字；返回（复算值, 不匹配码）。"""
    report = facts.primary_report
    recomputed: dict[str, Any] = {
        "mission_id": facts.mission_id,
        "mission_status": facts.mission_status,
        "finished_at": facts.finished_at,
        "contributor": facts.contributor,
        "report_id": report.report_id if report else None,
        "report_chars": report.chars if report else None,
        "report_figures": report.figures if report else None,
        "report_published": bool(facts.published_reports),
        "delta_category": facts.delta_category,
        "delta_artifacts": facts.delta_artifacts,
    }
    codes: list[str] = []
    for key in ("mission_id", "report_id", "report_chars", "report_figures",
                "delta_category", "delta_artifacts"):
        if key in declared and declared[key] != recomputed[key]:
            codes.append(f"MISMATCH_{key.upper()}")
    if "contributor" in declared and declared["contributor"] != recomputed["contributor"]:
        codes.append("MISMATCH_CONTRIBUTOR")
    if "finished_at" in declared and not _same_instant(declared["finished_at"], recomputed["finished_at"]):
        codes.append("MISMATCH_FINISHED_AT")
    if "report_published_claim" in declared and declared["report_published_claim"] != recomputed["report_published"]:
        codes.append("CONTRADICTED_REPORT_ABSENCE")
    if "delta_registered_claim" in declared and declared["delta_registered_claim"] != bool(recomputed["delta_category"]):
        codes.append("CONTRADICTED_DELTA_ABSENCE")
    return recomputed, tuple(codes)


def _same_instant(a: Any, b: Any) -> bool:
    """时间戳比较只到秒（＋时区口径差异不影响结论）。"""
    def norm(value: Any) -> str:
        if value is None:
            return ""
        return str(value).replace(" ", "T")[:19]
    return norm(a) == norm(b)


# ──────────────────────────── 判定 ────────────────────────────

def judge(record: Record, facts: MissionFacts) -> Recheck:
    """先盲序重算，再与申报对照；只降不升。"""
    blind = blind_payload(facts)
    recomputed_e, ev_codes = recompute_evidence_level(facts)
    recomputed_grade, grade_codes = recompute_grade(facts)
    declared_numbers = parse_reason_numbers(record.grade_reason)
    numbers, number_codes = recompute_reason_numbers(facts, declared_numbers)

    codes: list[str] = list(ev_codes) + list(grade_codes) + list(number_codes)
    verdict = "MATCH"
    action = "none"

    declared_grade = (record.grade or "").strip() or None
    declared_e = (record.e_level or "").strip() or None

    # 1) 证据引用断了 —— 申报所依赖的载体不存在，先否决
    if facts.broken_refs:
        verdict, action = "REJECT_BROKEN_EVIDENCE", "reject"
        codes = ["BROKEN_EVIDENCE_REF"] + codes
    # 2) 冒升级：申报超过当前证据/审查条件下可确认的天花板
    elif _exceeds_ceiling(declared_grade, recomputed_grade, declared_e, recomputed_e):
        verdict, action = "REJECT_UPGRADE", "reject"
        codes = [f"UPGRADE_EXCEEDS_CEILING:{declared_grade}>{recomputed_grade or 'NONE'}"] + codes
    # 3) E 级冒报：降级到重算值
    elif E_ORDER.get(declared_e or "E0", 0) > E_ORDER.get(recomputed_e, 0):
        verdict, action = "DOWNGRADE_EVIDENCE", "downgrade"
        codes = [f"E_OVERCLAIM:{declared_e}>{recomputed_e}"] + codes
    # 4) 理由数字/缺失声明与事实不符：降一级（下限 E1）
    elif [c for c in codes if c.startswith(("MISMATCH_", "CONTRADICTED_"))]:
        verdict, action = "DOWNGRADE_REASON", "downgrade"
        codes = ["REASON_NOT_REPRODUCIBLE"] + codes
    # 5) 申报低于可证值：只记录，不追认
    elif _below_reachable(declared_grade, declared_e, recomputed_grade, recomputed_e):
        verdict, action = "UNDERCLAIM", "none"
        codes = [f"UNDERCLAIM:{declared_grade}/{declared_e}<{recomputed_grade}/{recomputed_e}"] + codes

    return Recheck(
        evaluation_id=record.evaluation_id,
        artifact_id=record.artifact_id,
        mission_id=record.mission_id,
        repo=record.repo,
        stratum=record.stratum,
        blind_order=0,      # 由抽样器填盲序号
        declared_grade=declared_grade,
        declared_e=declared_e,
        recomputed_grade=recomputed_grade,
        recomputed_e=recomputed_e,
        verdict=verdict,
        action=action,
        mismatch_codes=tuple(dict.fromkeys(codes)),
        numbers_declared=dict(declared_numbers),
        numbers_recomputed={k: numbers.get(k) for k in declared_numbers},
        blind_payload=blind,
    )


def _exceeds_ceiling(declared_grade: str | None, ceiling: str | None,
                     declared_e: str | None, recomputed_e: str) -> bool:
    """申报等级是否超过天花板（None=无证据/否定轨不成立 → 任何等级都不可成立）。"""
    if not declared_grade or ceiling is None:
        return True
    if declared_grade == "S-N" or ceiling == "S-N":
        return declared_grade != ceiling
    if declared_grade not in GRADE_ORDER:
        return True
    if GRADE_ORDER[declared_grade] > GRADE_ORDER[ceiling]:
        return True
    # 申报的 E 若撑不起自己申报的 S 门槛，同样越界
    return GRADE_MIN_E.get(declared_grade, 1) > E_ORDER.get(declared_e or "E0", 0)


def _below_reachable(declared_grade: str | None, declared_e: str | None,
                     ceiling: str | None, recomputed_e: str) -> bool:
    if not declared_grade or not ceiling or declared_grade == "S-N" or ceiling == "S-N":
        return False
    return GRADE_ORDER.get(declared_grade, 0) < GRADE_ORDER.get(ceiling, 0)


# ──────────────────────────── 分层抽样 ────────────────────────────

def stratified_sample(records: Sequence[Record], *, size: int = DEFAULT_SAMPLE,
                      seed: str = "in24-e5") -> list[Record]:
    """按 (S, E) 分层抽样：每层至少 1 单（层数 ≤ size 时），余量按比例+最大余数分配。

    返回顺序 = 盲序（sha256(seed:evaluation_id) 排序），与分层顺序无关。
    """
    if size < 1:
        raise ValueError("size must be >= 1")
    strata: dict[str, list[Record]] = {}
    for rec in records:
        strata.setdefault(rec.stratum, []).append(rec)
    for key, bucket in strata.items():
        strata[key] = sorted(bucket, key=lambda r: blind_key(seed, r.evaluation_id))

    keys = sorted(strata)
    quota: dict[str, int] = {k: 0 for k in keys}
    remaining = size
    for key in keys:                      # 每层 1 单（若 size 足够）
        if remaining <= 0:
            break
        quota[key] = 1
        remaining -= 1

    if remaining > 0:                     # 余量：比例 + 最大余数，且不超层容量
        caps = {k: len(strata[k]) - quota[k] for k in keys}
        raw = {k: len(strata[k]) / len(records) * remaining for k in keys}
        extra = {k: min(int(raw[k]), caps[k]) for k in keys}
        left = remaining - sum(extra.values())
        for key in sorted(keys, key=lambda k: (-(raw[k] - int(raw[k])), k)):
            if left <= 0:
                break
            if extra[key] < caps[key]:
                extra[key] += 1
                left -= 1
        for key in keys:
            quota[key] += extra[key]
        left = size - sum(quota.values())  # 仍有剩余则按容量回填，尽量凑到 size
        for key in keys:
            while left > 0 and quota[key] < len(strata[key]):
                quota[key] += 1
                left -= 1

    picked: list[Record] = []
    for key in keys:
        picked.extend(strata[key][: quota[key]])
    picked.sort(key=lambda r: blind_key(seed, r.evaluation_id))
    return picked


def assign_blind_order(rechecks: Iterable[Recheck]) -> list[Recheck]:
    return [replace(r, blind_order=i) for i, r in enumerate(rechecks, start=1)]


def _count(records: Iterable[Record]) -> dict[str, int]:
    out: dict[str, int] = {}
    for rec in records:
        out[rec.stratum] = out.get(rec.stratum, 0) + 1
    return dict(sorted(out.items()))


# ──────────────────────────── 落库 ────────────────────────────

def ensure_schema(conn) -> None:
    from sqlalchemy import text
    for stmt in RECHECK_DDL:
        conn.execute(text(stmt))


def _json_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw]
    try:
        value = json.loads(raw)
    except Exception:
        return []
    return [str(x) for x in value] if isinstance(value, list) else []


def _json_items(raw: Any) -> list[Any]:
    if isinstance(raw, (list, tuple)):
        return list(raw)
    try:
        value = json.loads(raw)
    except Exception:
        return []
    return value if isinstance(value, list) else []


def apply_actions(conn, *, run_id: str, rechecks: Sequence[Recheck],
                  now: str | None = None) -> dict[str, int]:
    """幂等落复算记录 + 执行降级/否决动作（只对 confirmed 行生效）。"""
    from sqlalchemy import text
    stamp = now or datetime.now(timezone.utc).isoformat()
    ensure_schema(conn)
    existing = {
        row[0] for row in conn.execute(
            text("SELECT evaluation_id FROM eval_redteam_rechecks WHERE run_id = :run"),
            {"run": run_id},
        )
    }
    stats = {"records": 0, "skipped": 0, "rejected": 0, "downgraded": 0, "matched": 0}
    for rc in rechecks:
        if rc.evaluation_id in existing:
            stats["skipped"] += 1
            continue
        recheck_id = hashlib.sha256(f"{run_id}:{rc.evaluation_id}".encode()).hexdigest()[:32]
        conn.execute(text(
            """
            INSERT INTO eval_redteam_rechecks
              (recheck_id, run_id, evaluation_id, artifact_id, mission_id, repo, stratum,
               blind_order, declared_grade, declared_e, recomputed_grade, recomputed_e,
               verdict, action, mismatch_codes, numbers_declared, numbers_recomputed,
               blind_payload, reviewer, applied, created_at)
            VALUES
              (:rid, :run, :eid, :aid, :mid, :repo, :stratum,
               :border, :dg, :de, :rg, :re,
               :verdict, :action, :codes, :decl, :recomp,
               :blind, :reviewer, 0, :now)
            """
        ), {
            "rid": recheck_id, "run": run_id, "eid": rc.evaluation_id,
            "aid": rc.artifact_id, "mid": rc.mission_id, "repo": rc.repo,
            "stratum": rc.stratum, "border": rc.blind_order,
            "dg": rc.declared_grade, "de": rc.declared_e,
            "rg": rc.recomputed_grade, "re": rc.recomputed_e,
            "verdict": rc.verdict, "action": rc.action,
            "codes": json.dumps(list(rc.mismatch_codes), ensure_ascii=False),
            "decl": json.dumps(dict(rc.numbers_declared), ensure_ascii=False, default=str),
            "recomp": json.dumps(dict(rc.numbers_recomputed), ensure_ascii=False, default=str),
            "blind": json.dumps(dict(rc.blind_payload), ensure_ascii=False, default=str),
            "reviewer": REDTEAM_ID, "now": stamp,
        })
        stats["records"] += 1
        if rc.verdict == "MATCH":
            stats["matched"] += 1

        row = conn.execute(text(
            "SELECT status, reason_codes FROM outcome_evaluations WHERE evaluation_id = :eid"
        ), {"eid": rc.evaluation_id}).first()
        if row is None or row[0] != "confirmed":
            continue
        codes = _json_list(row[1])
        new_codes = [c for c in rc.mismatch_codes if c not in codes]
        if rc.action == "reject":
            conn.execute(text(
                """
                UPDATE outcome_evaluations
                   SET status = 'rejected', review_status = 'rejected_by_redteam',
                       reason_codes = :codes, decided_at = :now
                 WHERE evaluation_id = :eid AND status = 'confirmed'
                """
            ), {"codes": json.dumps(codes + new_codes, ensure_ascii=False),
                "now": stamp, "eid": rc.evaluation_id})
            stats["rejected"] += 1
        elif rc.action == "downgrade":
            new_e, new_grade = _downgrade_target(rc)
            conn.execute(text(
                """
                UPDATE outcome_evaluations
                   SET grade = :grade, e_level = :e, review_status = 'redteam_downgraded',
                       reason_codes = :codes, decided_at = :now
                 WHERE evaluation_id = :eid AND status = 'confirmed'
                """
            ), {"grade": new_grade, "e": new_e,
                "codes": json.dumps(codes + new_codes, ensure_ascii=False),
                "now": stamp, "eid": rc.evaluation_id})
            stats["downgraded"] += 1
    conn.commit()
    return stats


def _downgrade_target(rc: Recheck) -> tuple[str, str | None]:
    """降级落点：E 取重算值；理由数字问题再降一级（下限 E1）；S 只降不升。"""
    target_e = rc.recomputed_e
    if rc.verdict == "DOWNGRADE_REASON":
        target_e = E_LEVELS[max(1, E_ORDER.get(rc.declared_e or "E1", 1) - 1)]
    if E_ORDER.get(target_e, 0) > E_ORDER.get(rc.declared_e or "E0", 0):
        target_e = rc.declared_e or "E1"          # 只降不升
    target_grade = rc.recomputed_grade or rc.declared_grade
    if (rc.declared_grade in GRADE_ORDER and target_grade in GRADE_ORDER
            and GRADE_ORDER[target_grade] > GRADE_ORDER[rc.declared_grade]):
        target_grade = rc.declared_grade          # 只降不升
    return target_e, target_grade


# ──────────────────────────── 数据装载（PG） ────────────────────────────

def load_records(conn, *, repo: str | None = None, status: str = "confirmed") -> list[Record]:
    from sqlalchemy import text
    sql = """
        SELECT evaluation_id, artifact_id, mission_id, contributor, grade, e_level,
               grade_reason, evidence_refs, baseline_or_scope, boundary, reviewer,
               reviewer_kind, reason_codes, repo
          FROM outcome_evaluations
         WHERE status = :status
    """
    params: dict[str, Any] = {"status": status}
    if repo:
        sql += " AND repo = :repo"
        params["repo"] = repo
    sql += " ORDER BY evaluation_id"
    out: list[Record] = []
    for row in conn.execute(text(sql), params).mappings():
        out.append(Record(
            evaluation_id=row["evaluation_id"],
            artifact_id=row["artifact_id"],
            mission_id=row["mission_id"],
            contributor=row["contributor"],
            grade=row["grade"],
            e_level=row["e_level"],
            grade_reason=row["grade_reason"],
            evidence_refs=tuple(_json_items(row["evidence_refs"])),
            baseline_or_scope=row["baseline_or_scope"],
            boundary=row["boundary"],
            reviewer=row["reviewer"],
            reviewer_kind=row["reviewer_kind"],
            reason_codes=tuple(_json_list(row["reason_codes"])),
            repo=row["repo"],
        ))
    return out


def load_facts(conn, record: Record) -> MissionFacts:
    """从平台记录取事实（只取事实，不取等级）。"""
    from sqlalchemy import text
    mid = record.mission_id
    if mid is None:
        return MissionFacts(mission_id=-1, mission_status="unknown", finished_at=None,
                            contributor=None, repo=record.repo,
                            reviewer_kind=record.reviewer_kind,
                            broken_refs=("mission_id missing",))
    mission = conn.execute(
        text("SELECT * FROM tensor_dev_missions WHERE id = :mid"), {"mid": mid}
    ).mappings().first() or {}
    reports = tuple(
        ReportFacts(report_id=int(r["id"]), status=str(r["status"]),
                    chars=int(r["chars"] or 0), figures=int(r["figures"] or 0))
        for r in conn.execute(text(
            """
            SELECT id, status, length(markdown) AS chars,
                   coalesce(jsonb_array_length(figures), 0) AS figures
              FROM tensor_reports WHERE mission_id = :mid ORDER BY id
            """
        ), {"mid": mid}).mappings()
    )
    deltas = conn.execute(text(
        """
        SELECT category, coalesce(jsonb_array_length(artifacts), 0) AS n_artifacts
          FROM tensor_capability_deltas WHERE mission_id = :mid ORDER BY id
        """
    ), {"mid": mid}).mappings().all()
    categories = tuple(dict.fromkeys(str(d["category"]) for d in deltas if d["category"]))
    artifacts = sum(int(d["n_artifacts"] or 0) for d in deltas)

    broken, unverifiable = resolve_evidence_refs(record, mid,
                                                 {r.report_id for r in reports},
                                                 bool(deltas), bool(mission))
    return MissionFacts(
        mission_id=mid,
        mission_status=str(mission.get("status") or "unknown"),
        finished_at=(str(mission["finished_at"]) if mission.get("finished_at") is not None else None),
        contributor=(mission.get("claimed_by") or mission.get("assignee")
                     or record.contributor),
        repo=str(mission.get("repo") or record.repo or "") or None,
        reports=reports,
        delta_categories=categories,
        delta_artifacts=artifacts,
        broken_refs=tuple(broken),
        unverifiable_refs=tuple(unverifiable),
        baseline_declared=bool((record.baseline_or_scope or "").strip()),
        scope_declared=bool((record.baseline_or_scope or "").strip()),
        reviewer_kind=record.reviewer_kind,
    )


_REF_API = re.compile(r"/missions/(\d+)/")
_REF_DELTA = re.compile(r"tensor_capability_deltas:mission=(\d+)")


def resolve_evidence_refs(record: Record, mission_id: int, report_ids: set[int],
                          delta_present: bool, mission_present: bool
                          ) -> tuple[list[str], list[str]]:
    """证据引用可解析性：返回（断链, 不可判）。"""
    broken: list[str] = []
    unverifiable: list[str] = []
    for ref in record.evidence_refs:
        kind = str(ref.get("kind") or "")
        value = str(ref.get("ref") or "")
        if kind == "api":
            match = _REF_API.search(value)
            if not match:
                unverifiable.append(f"{kind}:{value}")
            elif int(match.group(1)) != mission_id or not mission_present:
                broken.append(f"{kind}:{value}")
        elif kind == "report":
            if not value.isdigit() or int(value) not in report_ids:
                broken.append(f"{kind}:{value}")
        elif kind == "artifact":
            match = _REF_DELTA.search(value)
            if match:
                if int(match.group(1)) != mission_id or not delta_present:
                    broken.append(f"{kind}:{value}")
            else:
                unverifiable.append(f"{kind}:{value}")
        else:
            unverifiable.append(f"{kind}:{value}")
    return broken, unverifiable


def _official_mapping() -> dict[str, str]:
    """回填器映射（若可导入），用于交叉校验规则是否漂移。"""
    try:
        from tools.backfill_outcome_evaluations import S_BY_CATEGORY  # type: ignore
        return dict(S_BY_CATEGORY)
    except Exception:  # pragma: no cover - 仅用于可选交叉校验
        return {}



# ──────────────────────── 非空洞性控件（0 发现也要能证明装置会亮） ────────────────────────

def mutation_controls(pairs: Sequence[tuple[Record, MissionFacts]]) -> dict[str, Any]:
    """对抽中样本做受控篡改，检验判定器是否必然报警（灵敏度/非空洞证据）。

    四条变异（每条都只改一个申报面/事实面）：
      M1 证据级抬一级          → 期望非 MATCH
      M2 等级抬到 S5（最高）   → 期望 REJECT_UPGRADE
      M3 理由正文字数 +1       → 期望 DOWNGRADE_REASON
      M4 抽掉增量记录          → 期望非 MATCH
    """
    checks: list[dict[str, Any]] = []
    for rec, facts in pairs:
        base = judge(rec, facts)

        def expect(tag: str, mutated_rec: Record, mutated_facts: MissionFacts,
                   predicate) -> None:
            rc = judge(mutated_rec, mutated_facts)
            checks.append({"evaluation_id": rec.evaluation_id, "mutation": tag,
                           "verdict": rc.verdict, "detected": bool(predicate(rc))})

        e_idx = min(len(E_LEVELS) - 1, E_ORDER.get(rec.e_level or "E1", 1) + 1)
        expect("M1_E_BUMP", replace(rec, e_level=E_LEVELS[e_idx]), facts,
               lambda rc: rc.verdict != "MATCH")
        expect("M2_GRADE_S5", replace(rec, grade="S5"), facts,
               lambda rc: rc.verdict == "REJECT_UPGRADE")
        nums = parse_reason_numbers(rec.grade_reason)
        if isinstance(nums.get("report_chars"), int):
            bumped = (rec.grade_reason or "").replace(
                f"正文 {nums['report_chars']} 字符", f"正文 {nums['report_chars'] + 1} 字符")
            expect("M3_CHARS_PLUS1", replace(rec, grade_reason=bumped), facts,
                   lambda rc: rc.verdict == "DOWNGRADE_REASON")
        expect("M4_DROP_DELTA", rec,
               replace(facts, delta_categories=(), delta_artifacts=0),
               lambda rc: rc.verdict != "MATCH")
        checks[-1]["baseline_verdict"] = base.verdict

    total = len(checks)
    detected = sum(1 for c in checks if c["detected"])
    by_mutation: dict[str, dict[str, int]] = {}
    for c in checks:
        slot = by_mutation.setdefault(c["mutation"], {"total": 0, "detected": 0})
        slot["total"] += 1
        slot["detected"] += int(c["detected"])
    return {"total": total, "detected": detected,
            "detection_rate": round(detected / total, 4) if total else 0.0,
            "by_mutation": by_mutation, "checks": checks}


def ground_truth_controls(conn, *, repo: str | None = None,
                          limit: int = 10) -> dict[str, Any]:
    """正控：对平台**已否决**的行重放同一复算器，看能否独立复现否决。

    复现 = 复算器也从当前事实出发判出 reject。复现不了的行如实记为
    `not_reproducible`（说明审计链事实已变或原否决依据不在平台记录里）。
    """
    rejected = load_records(conn, repo=repo, status="rejected")[:limit]
    rows: list[dict[str, Any]] = []
    for rec in rejected:
        rc = judge(rec, load_facts(conn, rec))
        rows.append({"evaluation_id": rec.evaluation_id, "mission_id": rec.mission_id,
                     "declared": f"{rec.grade}/{rec.e_level}",
                     "redteam_verdict": rc.verdict, "redteam_action": rc.action,
                     "reproduced": rc.action == "reject",
                     "codes": list(rc.mismatch_codes)})
    return {"checked": len(rows),
            "reproduced": sum(1 for r in rows if r["reproduced"]),
            "rows": rows}

# ──────────────────────────── 一次完整红队运行 ────────────────────────────

def run_recheck(conn, *, repo: str | None = None, size: int = DEFAULT_SAMPLE,
                seed: str = "in24-e5", apply: bool = False,
                run_id: str | None = None) -> dict[str, Any]:
    records = load_records(conn, repo=repo)
    if not records:
        return {"population": 0, "sample": 0, "verdicts": {}, "findings": [], "sample_ids": []}
    size = max(MIN_SAMPLE, size)
    picked = stratified_sample(records, size=size, seed=seed)
    pairs = [(rec, load_facts(conn, rec)) for rec in picked]
    rechecks = assign_blind_order(judge(rec, facts) for rec, facts in pairs)

    verdicts = {v: 0 for v in VERDICTS}
    for rc in rechecks:
        verdicts[rc.verdict] = verdicts.get(rc.verdict, 0) + 1
    run_id = run_id or f"redteam-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    stats = apply_actions(conn, run_id=run_id, rechecks=rechecks) if apply else {}

    drift = rule_drift(_official_mapping()) if _official_mapping() else []
    return {
        "run_id": run_id,
        "population": len(records),
        "population_by_stratum": _count(records),
        "sample": len(rechecks),
        "sample_by_stratum": _count(picked),
        "strata_covered": sorted(_count(picked)),
        "strata_in_population": sorted(_count(records)),
        "verdicts": verdicts,
        "rule_drift": drift,
        "controls": {"mutations": mutation_controls(pairs),
                     "ground_truth": ground_truth_controls(conn, repo=repo)},
        "writeback": stats,
        "findings": [
            {"blind_order": rc.blind_order, "evaluation_id": rc.evaluation_id,
             "mission_id": rc.mission_id, "repo": rc.repo, "stratum": rc.stratum,
             "declared": f"{rc.declared_grade}/{rc.declared_e}",
             "recomputed": f"{rc.recomputed_grade}/{rc.recomputed_e}",
             "verdict": rc.verdict, "action": rc.action,
             "codes": list(rc.mismatch_codes)}
            for rc in rechecks
        ],
        "rechecks": [rc.__dict__ for rc in rechecks],
    }


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    import pathlib
    from sqlalchemy import create_engine

    parser = argparse.ArgumentParser(description="评价真实性红队抽样复算（IN24-E5）")
    parser.add_argument("--dsn", default="postgresql+psycopg://postgres@/tensorplatform")
    parser.add_argument("--repo", default=None)
    parser.add_argument("--size", type=int, default=DEFAULT_SAMPLE)
    parser.add_argument("--seed", default="in24-e5")
    parser.add_argument("--apply", action="store_true", help="落复算记录并执行降级/否决")
    parser.add_argument("--out", default=None, help="汇总 JSON 输出路径")
    args = parser.parse_args(argv)

    engine = create_engine(args.dsn, future=True)
    with engine.connect() as conn:
        summary = run_recheck(conn, repo=args.repo, size=args.size, seed=args.seed,
                              apply=args.apply)
    payload = json.dumps(summary, ensure_ascii=False, indent=2, default=str)
    if args.out:
        pathlib.Path(args.out).write_text(payload, encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "rechecks"},
                     ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())