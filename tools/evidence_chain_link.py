"""结论五元证据链（IN59-A3）——S2+ 成果声明的可追溯性契约。

问题
----
``outcome_evaluations`` 判的是「等级」（S×E），``evidence_refs`` 只要求「有引用」。
于是 S3 声明可以带一条 ``report:9`` 就成立——**报告有、原始数据没有**也能登记。
本模块把「结论 → 五元」落成机器可裁的判据：

| 元素 | 键 | 接受形态 | 机器判据 |
|---|---|---|---|
| 理论或文献依据 | ``theory`` | DOI / URL / arXiv / ``derivation:`` 推导串 | 前缀白名单 + 最短长度 |
| 代码版本 | ``code`` | ``<sha>`` / ``repo@<sha>`` | 7–40 位十六进制 |
| 实验配置 | ``config`` | ``sha256:<64hex>`` / ``<64hex>`` / ``path@sha256:<64hex>`` | 64 位十六进制摘要 |
| 原始计算结果 | ``data`` | 文件路径 / ``artifact:`` / ``file:`` / ``s3://`` / ``sha256:`` | 路径或摘要，不接受裸编号 |
| 分析报告 | ``report`` | ``report:123`` / ``#123`` / ``123`` | 正整数 report_id |

规则（先于实现，机器可裁）
--------------------------
1. **S2+ 必须带链**（S1 豁免）：``REQUIRED_FROM_GRADES``。
2. **缺件封顶**：五元不全（缺失或形态非法）→ E 级封顶 ``INCOMPLETE_E_CAP='E1'``，
   只降不升；完整链不改变申报值。
3. **降级联动**：封顶后若低于该 S 级的 E 门槛（S3/S4/S-N 需 E≥2、S5 需 E≥3），
   由 ``tools.outcome_evaluations.decide`` 判 ``candidate``（不计入分布、不给信用）。
4. **红队口径**：S3+ 声明缺 ``data``（原始计算结果）→ 必命中 ``CHAIN_RAW_DATA_MISSING``，
   相位落 ``candidate``。缺报告/缺提交同理，但缺原始数据是最常见的「无法复算」形态。
5. **零领域语义**：本模块不认识任何学科词，只认「元素 / 引用 / 等级」。

落库：``outcome_evidence_chains``（一条结论一行，含五元引用 + 完整度 + 封顶结果），
并建 ``outcome_eval_chain_audit`` 视图给结算面（信用结算按 ``chain_blocked`` 拦付）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

CHAIN_TABLE = "outcome_evidence_chains"
VIEW_CHAIN_AUDIT = "outcome_eval_chain_audit"

# (键, 中文名) —— 顺序即「五元」顺序，报告与前端按此顺序展示
ELEMENTS: tuple[tuple[str, str], ...] = (
    ("theory", "理论或文献依据（doi/url/推导）"),
    ("code", "代码版本（commit sha）"),
    ("config", "实验配置（config hash）"),
    ("data", "原始计算结果（数据文件/图锚）"),
    ("report", "分析报告（report_id）"),
)
ELEMENT_KEYS: tuple[str, ...] = tuple(k for k, _ in ELEMENTS)
ELEMENT_LABELS: dict[str, str] = dict(ELEMENTS)

REQUIRED_FROM_GRADES: tuple[str, ...] = ("S2", "S3", "S4", "S5", "S-N")
INCOMPLETE_E_CAP = "E1"
E_ORDER: dict[str, int] = {"E0": 0, "E1": 1, "E2": 2, "E3": 3, "E4": 4}
# 各 S 级对 E 的下限（与 tools.outcome_evaluations.decide 的 candidate 规则同口径）
GRADE_MIN_E: dict[str, int] = {"S1": 1, "S2": 1, "S3": 2, "S4": 2, "S5": 3, "S-N": 2}

CHAIN_STATUSES: tuple[str, ...] = ("complete", "partial", "missing")

_DOI_RE = re.compile(r"(?:doi:)?10\.\d{4,9}/\S+", re.I)
_HEX_RE = re.compile(r"^[0-9a-f]{7,40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DATA_EXT_RE = re.compile(r"\.(json|csv|tsv|parquet|dat|txt|npz|npy|h5|hdf5|nc|png|svg|pdf|ya?ml|log)\b", re.I)

# 允许「引用」写法：{键: "..."} 或 {键: {"ref": "..."}}（后者便于前端带 caption）
_REF_KEYS = ("ref", "value", "id")


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _raw_ref(value: Any) -> str:
    if isinstance(value, Mapping):
        for k in _REF_KEYS:
            if value.get(k) not in (None, ""):
                return _clean(value.get(k))
        return ""
    if isinstance(value, (list, tuple)):
        parts = [_clean(v) for v in value if _clean(v)]
        return parts[0] if len(parts) == 1 else ""
    return _clean(value)


# ─────────────────────────── 逐元素形态判据 ─────────────────────────── #


def valid_theory(value: str) -> bool:
    s = value.strip()
    lo = s.lower()
    if lo.startswith(("http://", "https://")) and len(s) > 12:
        return True
    if _DOI_RE.fullmatch(s):
        return True
    for prefix in ("arxiv:", "isbn:", "derivation:", "cite:", "std:"):
        if lo.startswith(prefix) and len(s) > len(prefix) + 5:
            return True
    return False


def valid_code(value: str) -> bool:
    s = value.strip()
    if "@" in s:                      # repo@sha
        repo, _, sha = s.rpartition("@")
        return bool(repo.strip()) and bool(_HEX_RE.fullmatch(sha.strip().lower()))
    return bool(_HEX_RE.fullmatch(s.lower()))


def valid_config(value: str) -> bool:
    s = value.strip()
    lo = s.lower()
    if lo.startswith("sha256:"):
        return bool(_SHA256_RE.fullmatch(lo[7:]))
    if _SHA256_RE.fullmatch(lo):
        return True
    if "@" in s:                      # configs/run.yaml@sha256:<hex>
        head, _, tail = s.rpartition("@")
        return bool(head.strip()) and valid_config(tail)
    return False


def valid_data(value: str) -> bool:
    s = value.strip()
    lo = s.lower()
    if len(s) < 6:
        return False
    if lo.startswith(("sha256:", "artifact:", "file:", "s3://", "gs://", "run:")):
        return True
    if "/" in s or "\\" in s:
        return True
    return bool(_DATA_EXT_RE.search(s))


def valid_report(value: str) -> bool:
    s = value.strip().lstrip("#")
    if s.lower().startswith("report:"):
        s = s[7:].strip()
    return s.isdigit() and int(s) > 0


VALIDATORS = {
    "theory": (valid_theory, "CHAIN_INVALID_THEORY"),
    "code": (valid_code, "CHAIN_INVALID_CODE"),
    "config": (valid_config, "CHAIN_INVALID_CONFIG"),
    "data": (valid_data, "CHAIN_INVALID_DATA"),
    "report": (valid_report, "CHAIN_INVALID_REPORT"),
}


# ─────────────────────────── 链分析（纯函数） ─────────────────────────── #


@dataclass(frozen=True)
class ChainReport:
    """一条结论的证据链体检结果（不含任何等级语义，等级联动在 decide）。"""

    refs: dict[str, str]
    present: tuple[str, ...]
    missing: tuple[str, ...]
    invalid: tuple[str, ...]
    completeness: float
    status: str
    required: bool
    cap: str | None
    codes: tuple[str, ...]
    conclusion_ref: str | None = None

    @property
    def complete(self) -> bool:
        return self.status == "complete"

    def as_dict(self) -> dict[str, Any]:
        return {
            "conclusion_ref": self.conclusion_ref,
            "elements": dict(self.refs),
            "present_elements": list(self.present),
            "missing_elements": list(self.missing),
            "invalid_elements": list(self.invalid),
            "completeness": self.completeness,
            "chain_status": self.status,
            "e_level_cap": self.cap,
            "capped": bool(self.cap),
            "required": self.required,
            "codes": list(self.codes),
        }


def _as_mapping(raw: Any) -> tuple[dict[str, Any], bool]:
    """接受 {键: 引用}、[{element, ref}]、JSON 字符串；返回 (映射, 是否给了链)。"""
    if raw is None or raw == "":
        return {}, False
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return {}, False
    if isinstance(raw, Mapping):
        return dict(raw), True
    if isinstance(raw, (list, tuple)):
        out: dict[str, Any] = {}
        for item in raw:
            if isinstance(item, Mapping) and item.get("element"):
                out[_clean(item.get("element")).lower()] = item
        return out, bool(out)
    return {}, False


def analyze_chain(raw: Any, grade: str | None = None) -> ChainReport:
    """体检五元链（缺失 / 非法 / 完整度 / 封顶结论）。

    参数
    ----
    raw   : payload 里的 ``evidence_chain``（映射或元素列表）
    grade : 申报 S 级；``REQUIRED_FROM_GRADES`` 之外的等级不要求链（S1 豁免）
    """
    data, given = _as_mapping(raw)
    conclusion_ref = _clean(data.get("conclusion_ref") or data.get("claim_ref")) or None
    required = _clean(grade).upper() in REQUIRED_FROM_GRADES

    refs: dict[str, str] = {}
    present: list[str] = []
    missing: list[str] = []
    invalid: list[str] = []
    codes: list[str] = []

    for key in ELEMENT_KEYS:
        ref = _raw_ref(data.get(key))
        if not ref:
            missing.append(key)
            continue
        ok, bad_code = VALIDATORS[key]
        if ok(ref):
            present.append(key)
            refs[key] = ref
        else:
            invalid.append(key)
            codes.append(bad_code)

    completeness = round(len(present) / len(ELEMENT_KEYS), 4)
    if len(present) == len(ELEMENT_KEYS):
        status = "complete"
    elif not present and not invalid:
        status = "missing"
    else:
        status = "partial"

    cap: str | None = None
    if required:
        if not given:
            codes.append("CHAIN_ABSENT")
        if status != "complete":
            cap = INCOMPLETE_E_CAP
            codes.append(f"CHAIN_CAPS_E_TO_{cap}")
            for key in missing:
                codes.append(f"CHAIN_MISSING_{key.upper()}")
            if "data" in missing:
                codes.append("CHAIN_RAW_DATA_MISSING")

    return ChainReport(
        refs=refs,
        present=tuple(present),
        missing=tuple(missing),
        invalid=tuple(invalid),
        completeness=completeness,
        status=status,
        required=required,
        cap=cap,
        codes=tuple(_uniq(codes)),
        conclusion_ref=conclusion_ref,
    )


def _uniq(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def cap_e_level(e_level: str | None, cap: str | None) -> str | None:
    """只降不升：``cap`` 存在且低于申报 E 时返回 ``cap``，否则原样返回。"""
    if not e_level or not cap:
        return e_level
    if E_ORDER.get(e_level, 0) > E_ORDER.get(cap, 0):
        return cap
    return e_level


def below_grade_floor(grade: str | None, e_level: str | None) -> bool:
    """封顶后的 E 是否已低于该 S 级对 E 的最低要求（低于 → 只能 candidate）。"""
    if not grade or not e_level:
        return False
    floor = GRADE_MIN_E.get(_clean(grade).upper())
    if floor is None:
        return False
    return E_ORDER.get(e_level, 0) < floor


# ─────────────────────────── 落库（DDL / SQL / 记录） ─────────────────────────── #

CHAIN_DDL = f"""
CREATE TABLE IF NOT EXISTS {CHAIN_TABLE} (
    chain_id            TEXT PRIMARY KEY,
    evaluation_id       TEXT NOT NULL,
    artifact_id         TEXT NOT NULL,
    mission_id          BIGINT,
    contributor         TEXT,
    conclusion_ref      TEXT,
    grade               TEXT,
    e_level_requested   TEXT,
    e_level_granted     TEXT,
    theory_ref          TEXT,
    code_ref            TEXT,
    config_ref          TEXT,
    data_ref            TEXT,
    report_ref          TEXT,
    present_elements    TEXT NOT NULL DEFAULT '[]',
    missing_elements    TEXT NOT NULL DEFAULT '[]',
    invalid_elements    TEXT NOT NULL DEFAULT '[]',
    completeness        REAL NOT NULL,
    chain_status        TEXT NOT NULL,
    capped              BOOLEAN NOT NULL DEFAULT FALSE,
    codes               TEXT NOT NULL DEFAULT '[]',
    repo                TEXT,
    organization_id     BIGINT,
    created_at          TEXT NOT NULL,
    CHECK (chain_status IN ('complete','partial','missing')),
    CHECK (completeness >= 0.0 AND completeness <= 1.0),
    CHECK (e_level_requested IS NULL OR e_level_requested IN ('E0','E1','E2','E3','E4')),
    CHECK (e_level_granted IS NULL OR e_level_granted IN ('E0','E1','E2','E3','E4'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_outcome_chain_evaluation
    ON {CHAIN_TABLE} (evaluation_id);
CREATE INDEX IF NOT EXISTS idx_outcome_chain_status
    ON {CHAIN_TABLE} (chain_status, capped);
CREATE INDEX IF NOT EXISTS idx_outcome_chain_mission
    ON {CHAIN_TABLE} (mission_id);

DROP VIEW IF EXISTS {VIEW_CHAIN_AUDIT};

CREATE VIEW {VIEW_CHAIN_AUDIT} AS
SELECT
    e.evaluation_id,
    e.artifact_id,
    e.mission_id,
    e.contributor,
    e.grade,
    e.e_level,
    e.status,
    e.organization_id,
    coalesce(c.chain_status, 'missing') AS chain_status,
    coalesce(c.completeness, 0.0) AS chain_completeness,
    coalesce(c.capped, FALSE) AS chain_capped,
    CASE
      WHEN e.status = 'confirmed'
       AND e.grade IN ('S3','S4','S5','S-N')
       AND coalesce(c.chain_status, 'missing') <> 'complete' THEN TRUE
      ELSE FALSE
    END AS chain_blocked
FROM outcome_evaluations e
LEFT JOIN {CHAIN_TABLE} c ON c.evaluation_id = e.evaluation_id;
"""

CHAIN_COLUMNS: tuple[str, ...] = (
    "chain_id", "evaluation_id", "artifact_id", "mission_id", "contributor",
    "conclusion_ref", "grade", "e_level_requested", "e_level_granted",
    "theory_ref", "code_ref", "config_ref", "data_ref", "report_ref",
    "present_elements", "missing_elements", "invalid_elements",
    "completeness", "chain_status", "capped", "codes", "repo",
    "organization_id", "created_at",
)


def dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=False)


def chain_id_for(evaluation_id: str) -> str:
    return f"chain:{evaluation_id}"


def chain_record(
    payload: Mapping[str, Any],
    report: ChainReport,
    *,
    evaluation_id: str,
    e_level_requested: str | None,
    grade: str | None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """ChainReport → ``outcome_evidence_chains`` 可入库行（一结论一行，按评价幂等）。"""
    granted = cap_e_level(e_level_requested, report.cap)
    return {
        "chain_id": chain_id_for(evaluation_id),
        "evaluation_id": evaluation_id,
        "artifact_id": _clean(payload.get("artifact_id")),
        "mission_id": _int_or_none(payload.get("mission_id")),
        "contributor": _clean(payload.get("contributor")) or None,
        "conclusion_ref": report.conclusion_ref,
        "grade": _clean(grade) or None,
        "e_level_requested": _clean(e_level_requested) or None,
        "e_level_granted": granted,
        "theory_ref": report.refs.get("theory") or None,
        "code_ref": report.refs.get("code") or None,
        "config_ref": report.refs.get("config") or None,
        "data_ref": report.refs.get("data") or None,
        "report_ref": report.refs.get("report") or None,
        "present_elements": dumps_json(list(report.present)),
        "missing_elements": dumps_json(list(report.missing)),
        "invalid_elements": dumps_json(list(report.invalid)),
        "completeness": report.completeness,
        "chain_status": report.status,
        "capped": bool(report.cap),
        "codes": dumps_json(list(report.codes)),
        "repo": _clean(payload.get("repo")) or None,
        "organization_id": payload.get("organization_id"),
        "created_at": created_at or now_utc(),
    }


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def chain_insert_sql() -> str:
    cols = ", ".join(CHAIN_COLUMNS)
    vals = ", ".join(f":{c}" for c in CHAIN_COLUMNS)
    return f"INSERT INTO {CHAIN_TABLE} ({cols}) VALUES ({vals})"


def store_chain(conn, record: Mapping[str, Any], *, commit: bool = False) -> None:
    """按 evaluation_id 幂等写链（先删同键再插：重判/重跑结果一致，不叠行）。"""
    from sqlalchemy import text

    conn.execute(
        text(f"DELETE FROM {CHAIN_TABLE} WHERE evaluation_id = :evaluation_id"),
        {"evaluation_id": record["evaluation_id"]},
    )
    conn.execute(text(chain_insert_sql()), dict(record))
    if commit:
        conn.commit()


def ensure_chain_schema(conn) -> None:
    """幂等建表/建视图（迁移 105 之外的自愈路径：CI/本地 SQLite 也能跑）。"""
    from sqlalchemy import text

    for stmt in [s.strip() for s in CHAIN_DDL.split(";") if s.strip()]:
        conn.execute(text(stmt))
    conn.commit()


def chain_stats_queries(*, org_filter: bool = False) -> dict[str, str]:
    """质量中心用的可复算 SQL（原样暴露给页面，避免「前端另算一套」）。"""
    where_tbl = f"WHERE (:org IS NULL OR organization_id = :org)" if org_filter else ""
    where_view = f"WHERE (:org IS NULL OR e.organization_id = :org)" if org_filter else ""
    return {
        "by_status": (
            f"SELECT coalesce(chain_status,'missing') AS chain_status, count(*) AS n "
            f"FROM {CHAIN_TABLE} {where_tbl} GROUP BY 1 ORDER BY 1"
        ),
        # 跨引擎可移植：只算 avg，四舍五入留在读面（PG/SQLite 的 round 语义不同）
        "completeness_avg": f"SELECT avg(completeness) FROM {CHAIN_TABLE} {where_tbl}",
        "capped": f"SELECT count(*) FROM {CHAIN_TABLE} WHERE capped = TRUE"
                  + ("" if not org_filter else " AND (:org IS NULL OR organization_id = :org)"),
        "blocked": (
            f"SELECT count(*) FROM {VIEW_CHAIN_AUDIT} e "
            f"WHERE chain_blocked = TRUE"
            + ("" if not org_filter else " AND (:org IS NULL OR e.organization_id = :org)")
        ),
    }


def settlement_guard_sql() -> str:
    """结算面链检：只有链完整的 S3+ 才允许按申报等级入信用账。"""
    return (
        "SELECT evaluation_id, grade, status, contributor, mission_id "
        f"FROM {VIEW_CHAIN_AUDIT} "
        "WHERE chain_blocked = FALSE AND status IN ('confirmed','rejected')"
    )


def chain_row_to_dict(row: Any) -> dict[str, Any]:
    out = dict(row)
    for key in ("present_elements", "missing_elements", "invalid_elements", "codes"):
        raw = out.get(key)
        if isinstance(raw, str):
            try:
                out[key] = json.loads(raw)
            except (TypeError, ValueError):
                out[key] = []
    return out