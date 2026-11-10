"""WITHHELD 三态清偿闭环：门声明 ↔ 参考数据登记 ↔ 自动重判 ↔ 派单 + KPI（IN50-W4-N09）。

背景
----
平台正典把验收判决分三态 ``PASS < WITHHELD < WARN < FAIL``
（``backend/services/benchmark_integrity.py``）。WITHHELD 表示"证据不足，尚不可判"，
工程上最危险的退化是把它变成**永久黑洞**：门写着 WITHHELD，没人知道缺哪张数据集、
缺到什么程度、什么时候能还、谁欠着。本模块把 WITHHELD 做成**可清偿的债务**：

1. **门声明**（``declare_gate``）：门工件 ``requires_reference`` 声明所需参考数据集
   （dataset_id）、容差 ``tolerance``、不确定度展开因子 ``k_factor`` 与实测值。
   落库时做**规范化契约哈希**（只覆盖判决契约字段：门身份/数据集/容差/k/实测值/
   声明时刻）作为不可变基线 ``artifact_sha256``；``tolerance`` 等契约列由数据库
   触发器保护为不可就地更新。
2. **数据登记**（``register_reference``）：参考数据集登记必须带 DOI（或 url）、
   原始文件 ``sha256``、数字化方法与**数字化不确定度 u_dig**；登记即校验
   （复用 ``tools/vv/reference_registry.validate_dataset``，缺出处 ⇒ 不可作判据），
   触发登记事件并**自动重判**所有绑定该数据集的门。
3. **重判**（``rejudge_gate``）：读门工件里的实测值 m，参考值 r（按测量自变量在
   数字化表上插值；不外推），分母 ``D = max(|r|, 0.05·y_scale)``，

       E = (m − r)/D,    band = max( tol, k·sqrt(u_exp² + u_dig²)/D )

   ``|E| ≤ band`` ⇒ PASS；否则 FAIL；缺数据集/数据集不可作判据/缺实测值/超覆盖范围
   ⇒ WITHHELD。三态全部保留（不压成二态），每次判决追加一行**哈希链审计**。
4. **反作弊**：tol 只能来自门原始工件的哈希基线——重判时若传入工件指纹与基线不一致
   （例如事后把 15% 放成 30% 好让 FAIL 变 PASS），判决直接 FAIL 并记
   ``TAMPER_BLOCKED``；即使绕过哈希核对，生效容差也取 ``min(基线, 现行)``——只允许
   收紧、不允许放宽；审计表 append-only（触发器拒绝 UPDATE/DELETE）。
5. **KPI + 派单**：``withheld_gate_share``、``withheld_mean_age_days`` 写入
   ``tensor_guide_tracker``（按小时桶幂等 upsert）；未清偿门按 (repo, dataset_id, age)
   自动派单，同一 (repo, dataset_id) 的多门合并成一张工单，工单号由该二元组哈希得出
   （跨波幂等，不重复派单）。

纪律
----
* 零领域语义：本模块不含任何具体物理量/船型/算例；所有名称由调用方注入
  （测试用 ``SYN-*`` 合成域名，真实战役用真实工件）。
* 判据先于结论：不设 WARN 缓冲带；误差超 band 就是 FAIL，缺证据就是 WITHHELD。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# 平台三态与参考登记册共用同一实现
try:  # 作为包导入（pytest 从仓根跑）
    from tools.vv.reference_registry import interp, validate_dataset
except ImportError:  # pragma: no cover - 直接脚本执行路径
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.vv.reference_registry import interp, validate_dataset

SCHEMA_ID = "withheld-gate/v1"
DATASET_SCHEMA_ID = "experimental-reference/v1"

PASS, WITHHELD, FAIL = "PASS", "WITHHELD", "FAIL"
VERDICTS = (PASS, WITHHELD, FAIL)

#: 机器可读原因码（落库 + 派单分类）
R_NO_REFERENCE = "NO_REFERENCE"                  # 门要求的数据集尚未登记
R_REFERENCE_INCOMPLETE = "REFERENCE_INCOMPLETE"  # 已登记但出处/尺度不全，不可作判据
R_MEASUREMENT_MISSING = "MEASUREMENT_MISSING"    # 门工件没给实测值
R_OUT_OF_RANGE = "REFERENCE_OUT_OF_RANGE"        # 测量自变量超出数字化表覆盖（不外推）
R_TOLERANCE_TAMPERED = "TOLERANCE_TAMPERED"      # 契约字段被改（含放宽 tol）
R_WITHIN_BAND = "WITHIN_BAND"                    # 在容差带内
R_WITHIN_UNCERTAINTY = "WITHIN_UNCERTAINTY"      # 超 tol 但在不确定度预算内
R_OUT_OF_BAND = "OUT_OF_BAND"                    # 超容差且超不确定度预算

#: 门工件里参与哈希基线的字段——恰好是"判决契约"，改一个即篡改
CONTRACT_FIELDS = (
    "gate_id", "mission_id", "node", "repo", "quantity", "unit", "reason_code",
    "required_dataset_id", "tolerance", "k_factor", "measurement", "declared_at",
)

#: 派单动作：按当前原因码给出"怎么还这家债"的唯一动作口径
ACTIONS = {
    R_NO_REFERENCE: "登记参考数据集（DOI+原始文件 sha256+数字化方法+u_dig）后自动重判",
    R_REFERENCE_INCOMPLETE: "补齐数据集出处（DOI/原始文件 sha256/数字化方法/u_dig）后自动重判",
    R_OUT_OF_RANGE: "扩展数字化表自变量覆盖范围或补测该量程后重判（禁止外推）",
    R_MEASUREMENT_MISSING: "补测门所要求的实测量并重签门工件契约基线",
    R_TOLERANCE_TAMPERED: "撤销契约改写，按门原始工件基线重新提交（tol 只可收紧）",
}

KPI_SHARE = "withheld_gate_share"
KPI_AGE = "withheld_mean_age_days"
#: KPI 目标值：份额 0（不允许 WITHHELD 长期挂账），平均账龄 7 天（一周内清偿）
KPI_TARGETS = {KPI_SHARE: 0.0, KPI_AGE: 7.0}


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def as_dt(value: Any) -> Optional[datetime]:
    """兼容 sqlite(str) / psycopg(datetime) 的时间读数。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def artifact_contract(artifact: Mapping[str, Any]) -> Dict[str, Any]:
    """抽出判决契约字段（唯一参与哈希基线的子集）。"""
    req = artifact.get("requires_reference") or {}
    return {
        "gate_id": artifact.get("gate_id"),
        "mission_id": artifact.get("mission_id"),
        "node": artifact.get("node"),
        "repo": artifact.get("repo"),
        "quantity": artifact.get("quantity"),
        "unit": artifact.get("unit"),
        "reason_code": artifact.get("reason_code"),
        "required_dataset_id": req.get("dataset_id"),
        "tolerance": req.get("tolerance"),
        "k_factor": req.get("k_factor"),
        "measurement": req.get("measurement"),
        "declared_at": artifact.get("declared_at"),
    }


def artifact_fingerprint(artifact: Mapping[str, Any]) -> str:
    """门工件契约指纹：容差/k/实测值/数据集绑定任一改动都会变。"""
    return sha256_text(canonical(artifact_contract(artifact)))


# --------------------------------------------------------------------------- #
# DDL：SQLite（测试/独立运行）与 PostgreSQL（backend/migrations/113 同构）
# --------------------------------------------------------------------------- #
_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS tensor_withheld_gates (
    gate_id              TEXT PRIMARY KEY,
    mission_id           INTEGER,
    node                 TEXT NOT NULL DEFAULT '',
    repo                 TEXT NOT NULL DEFAULT '',
    quantity             TEXT NOT NULL DEFAULT '',
    unit                 TEXT NOT NULL DEFAULT '',
    reason_code          TEXT NOT NULL DEFAULT '',
    required_dataset_id  TEXT NOT NULL,
    tolerance            REAL NOT NULL,
    k_factor             REAL NOT NULL DEFAULT 2.0,
    artifact_sha256      TEXT NOT NULL,
    artifact_json        TEXT NOT NULL,
    verdict              TEXT NOT NULL DEFAULT 'WITHHELD',
    verdict_reason       TEXT NOT NULL DEFAULT '',
    verdict_reason_code  TEXT NOT NULL DEFAULT '',
    cleared_at           TEXT,
    created_at           TEXT NOT NULL,
    last_judged_at       TEXT,
    CHECK (verdict IN ('PASS','FAIL','WITHHELD')),
    CHECK (tolerance > 0 AND tolerance <= 0.5),
    CHECK (k_factor > 0)
);

CREATE TABLE IF NOT EXISTS tensor_reference_datasets (
    dataset_id            TEXT PRIMARY KEY,
    doi                   TEXT NOT NULL DEFAULT '',
    url                   TEXT NOT NULL DEFAULT '',
    citation              TEXT NOT NULL DEFAULT '',
    original_file_sha256  TEXT NOT NULL,
    digitisation_method   TEXT NOT NULL,
    u_dig                 REAL NOT NULL,
    u_dig_kind            TEXT NOT NULL DEFAULT 'relative',
    usable_as_gate        INTEGER NOT NULL DEFAULT 0,
    validation_json       TEXT NOT NULL,
    payload_json          TEXT NOT NULL,
    registered_at         TEXT NOT NULL,
    registered_by         TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tensor_withheld_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    gate_id     TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    event       TEXT NOT NULL,
    verdict     TEXT NOT NULL,
    payload     TEXT NOT NULL,
    prev_hash   TEXT NOT NULL,
    row_hash    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (gate_id, seq)
);

CREATE INDEX IF NOT EXISTS ix_withheld_gates_verdict
    ON tensor_withheld_gates (verdict, required_dataset_id);
CREATE INDEX IF NOT EXISTS ix_withheld_gates_repo
    ON tensor_withheld_gates (repo, required_dataset_id);

-- 契约字段不可就地更新：放宽 tol = 篡改（结构层防线，不靠调用方自觉）
CREATE TRIGGER IF NOT EXISTS trg_withheld_gates_contract_immutable
BEFORE UPDATE OF tolerance, k_factor, required_dataset_id, artifact_sha256, artifact_json
ON tensor_withheld_gates
BEGIN
    SELECT RAISE(ABORT, 'withheld gate contract fields are immutable (tol widening is tampering)');
END;

-- 审计表 append-only
CREATE TRIGGER IF NOT EXISTS trg_withheld_audit_no_update
BEFORE UPDATE ON tensor_withheld_audit
BEGIN
    SELECT RAISE(ABORT, 'withheld audit rows are append-only (UPDATE refused)');
END;

CREATE TRIGGER IF NOT EXISTS trg_withheld_audit_no_delete
BEFORE DELETE ON tensor_withheld_audit
BEGIN
    SELECT RAISE(ABORT, 'withheld audit rows are append-only (DELETE refused)');
END;

-- 独立运行/测试时补建 KPI 表（结构对齐 backend/migrations/095_tensor_repro_integrity.sql）
CREATE TABLE IF NOT EXISTS tensor_guide_tracker (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    metric_key      TEXT NOT NULL,
    metric_value    REAL NOT NULL DEFAULT 0,
    target_value    REAL,
    snapshot_at     TEXT NOT NULL,
    notes           TEXT NOT NULL DEFAULT '',
    UNIQUE (organization_id, metric_key, snapshot_at)
);
"""


# --------------------------------------------------------------------------- #
# 连接适配：sqlite3 与 psycopg 共用极薄 API（``?`` 占位符）
# --------------------------------------------------------------------------- #
class Conn:
    """把 DB-API 连接包成统一接口：``?`` 占位符、dict 行、双 dialect。"""

    def __init__(self, raw: Any, dialect: str) -> None:
        self.raw = raw
        self.dialect = dialect

    def _sql(self, sql: str) -> str:
        return sql if self.dialect == "sqlite" else sql.replace("?", "%s")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        if self.dialect == "sqlite":
            return self.raw.execute(self._sql(sql), tuple(params))
        cur = self.raw.cursor()  # pragma: no cover - 需 PG 环境
        cur.execute(self._sql(sql), tuple(params))
        return cur

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        cur = self.execute(sql, params)
        rows = cur.fetchall()
        if not rows:
            return []
        if isinstance(rows[0], Mapping):
            return [dict(r) for r in rows]
        cols = [d[0] for d in (cur.description or [])]
        return [dict(zip(cols, r)) for r in rows]

    def one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def commit(self) -> None:
        self.raw.commit()

    def close(self) -> None:
        self.raw.close()


def connect(target: Any = ":memory:", *, ensure_schema: bool = True) -> Conn:
    """``target`` 可为 sqlite3 连接、Conn、sqlite 路径或 PG DSN 字符串。"""
    if isinstance(target, Conn):
        return target
    if isinstance(target, sqlite3.Connection):
        conn = Conn(target, "sqlite")
    elif isinstance(target, str) and target.startswith("postgres"):
        import psycopg  # pragma: no cover - 生产路径
        conn = Conn(psycopg.connect(target), "postgres")
    else:
        target = str(target)
        if target != ":memory:":
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        conn = Conn(sqlite3.connect(target), "sqlite")
    if ensure_schema:
        ensure_tables(conn)
    return conn


def _split_sql(script: str) -> List[str]:
    """按 ``;`` 切分但保留触发器 ``BEGIN … END`` 块（块体内也有分号）。"""
    out: List[str] = []
    buf = ""
    depth = 0
    for raw_line in script.splitlines():
        line = raw_line.split("--")[0] if depth == 0 else raw_line
        stripped = line.strip()
        if not stripped:
            continue
        buf += line + "\n"
        low = stripped.upper()
        if low.startswith("BEGIN") and depth == 0:
            depth = 1
        elif depth and (low.startswith("END;") or low == "END"):
            depth = 0
        if depth == 0 and stripped.endswith(";"):
            out.append(buf.strip().rstrip(";").strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def ensure_tables(conn: Conn) -> None:
    if conn.dialect == "sqlite":
        for stmt in _split_sql(_DDL_SQLITE):
            conn.execute(stmt)
        conn.commit()


# --------------------------------------------------------------------------- #
# 三态判决（核心公式，零领域语义）
# --------------------------------------------------------------------------- #
def judge(
    measured: Optional[float],
    reference: Optional[float],
    *,
    tolerance: float,
    k_factor: float = 2.0,
    u_exp: float = 0.0,
    u_dig: float = 0.0,
    denominator_floor: float = 0.0,
) -> Dict[str, Any]:
    """单点三态判决。

    E = (m − r)/D，D = max(|r|, denominator_floor)；
    band = max(tol, k·sqrt(u_exp² + u_dig²)/D)。
    ``u_exp``/``u_dig`` 与量同单位（绝对标准不确定度）；缺测或缺参考值 ⇒ WITHHELD。
    """
    if measured is None or reference is None:
        return {"verdict": WITHHELD,
                "reason_code": R_MEASUREMENT_MISSING if measured is None else R_NO_REFERENCE,
                "E": None, "band": None}
    tol = float(tolerance)
    k = float(k_factor)
    d = max(abs(float(reference)), float(denominator_floor))
    if d <= 0.0:
        d = 1.0  # 参考值为 0 且未声明尺度：退化为绝对误差口径（显式）
    e = (float(measured) - float(reference)) / d
    u_band = k * math.sqrt(float(u_exp) ** 2 + float(u_dig) ** 2) / d
    band = max(tol, u_band)
    base = {"E": e, "band": band, "tolerance_band": tol,
            "uncertainty_band": u_band, "denominator": d}
    if abs(e) <= tol:
        return {"verdict": PASS, "reason_code": R_WITHIN_BAND, **base}
    if abs(e) <= u_band:
        return {"verdict": PASS, "reason_code": R_WITHIN_UNCERTAINTY, **base}
    return {"verdict": FAIL, "reason_code": R_OUT_OF_BAND, **base}


# --------------------------------------------------------------------------- #
# 参考数据集：构造 / 元数据 / 登记
# --------------------------------------------------------------------------- #
def make_reference_dataset(
    dataset_id: str,
    points: Sequence[Sequence[float]],
    *,
    citation: str,
    doi: str,
    digitised_by: str,
    retrieved_from: str,
    retrieved_sha256: str,
    digitisation_method: str,
    u_dig: float,
    u_dig_kind: str = "relative",
    tolerance: float = 0.15,
    y_scale: Optional[float] = None,
    quantity: str = "",
    unit: str = "",
) -> Dict[str, Any]:
    """构造一张合规的数字化参考表（可直接喂给 ``reference_registry.gate``）。

    ``points`` 每项 ``[x, y, sigma]``，``sigma`` 为该点数字化读数不确定度（绝对）。
    """
    return {
        "schema": DATASET_SCHEMA_ID,
        "dataset_id": dataset_id,
        "title": quantity or dataset_id,
        "quantity": quantity,
        "unit": unit,
        "y_scale": y_scale if y_scale is not None else
        (max(abs(float(p[1])) for p in points) if points else None),
        "tolerance": {"gate": float(tolerance),
                      "why": "平台精度标准：对标误差 >15% 不可接受；容差以门工件基线为准"},
        "points": [list(map(float, p)) for p in points],
        "digitisation": {
            "method": digitisation_method,
            "uncertainty": {"u_dig": float(u_dig), "type": u_dig_kind},
            "original_file_sha256": retrieved_sha256,
        },
        "provenance": {
            "citation": citation,
            "doi": doi,
            "digitised_by": digitised_by,
            "retrieved_from": retrieved_from,
            "retrieved_sha256": retrieved_sha256,
            "license_note": "数字化表按原文献引用；再分发前核对期刊条款",
        },
    }


def digitisation_meta(dataset: Mapping[str, Any]) -> Tuple[float, str, str, str]:
    """返回 (u_dig, kind, method, original_file_sha256)。缺项返回空值由调用方判级。"""
    dig = dataset.get("digitisation") or {}
    unc = dig.get("uncertainty") or {}
    if isinstance(unc, (int, float)):
        unc = {"u_dig": float(unc), "type": "relative"}
    u_dig = float(unc.get("u_dig", 0.0) or 0.0)
    kind = str(unc.get("type") or "relative")
    method = str(dig.get("method") or "")
    sha = str(dig.get("original_file_sha256")
              or (dataset.get("provenance") or {}).get("retrieved_sha256") or "")
    return u_dig, kind, method, sha


def register_reference(
    conn: Conn,
    dataset: Mapping[str, Any],
    *,
    actor: str = "agent",
    now: Optional[datetime] = None,
    rejudge: bool = True,
) -> Dict[str, Any]:
    """登记参考数据集 → 触发登记事件 → 自动重判所有绑定门。

    登记门槛（缺一即 ``usable_as_gate=False``，绑定门保持 WITHHELD）：
    出处（citation + DOI 或 url）、原始文件 sha256、数字化方法、数字化不确定度 u_dig。
    """
    now = now or utc_now()
    ds = dict(dataset)
    ds_id = str(ds.get("dataset_id") or "").strip()
    if not ds_id:
        raise ValueError("dataset_id 必填")
    u_dig, kind, method, orig_sha = digitisation_meta(ds)
    prov = ds.get("provenance") or {}
    has_locator = bool(str(prov.get("doi") or "").strip() or str(prov.get("url") or "").strip())
    missing: List[str] = []
    if not has_locator:
        missing.append("doi|url")
    if not orig_sha:
        missing.append("original_file_sha256")
    if not method:
        missing.append("digitisation.method")
    if "uncertainty" not in (ds.get("digitisation") or {}):
        missing.append("digitisation.uncertainty.u_dig")

    validation = validate_dataset(ds)
    validation = {**validation, "registration_missing": missing}
    usable = bool(validation["usable_as_gate"]) and not missing

    conn.execute(
        """INSERT INTO tensor_reference_datasets
               (dataset_id, doi, url, citation, original_file_sha256, digitisation_method,
                u_dig, u_dig_kind, usable_as_gate, validation_json, payload_json,
                registered_at, registered_by)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT (dataset_id) DO UPDATE SET
               doi=excluded.doi, url=excluded.url, citation=excluded.citation,
               original_file_sha256=excluded.original_file_sha256,
               digitisation_method=excluded.digitisation_method,
               u_dig=excluded.u_dig, u_dig_kind=excluded.u_dig_kind,
               usable_as_gate=excluded.usable_as_gate,
               validation_json=excluded.validation_json, payload_json=excluded.payload_json,
               registered_at=excluded.registered_at, registered_by=excluded.registered_by""",
        (ds_id, str(prov.get("doi") or ""), str(prov.get("url") or ""),
         str(prov.get("citation") or ""), orig_sha, method, u_dig, kind,
         1 if usable else 0, canonical(validation), canonical(ds), iso(now), actor),
    )

    gates = [g["gate_id"] for g in conn.query(
        "SELECT gate_id FROM tensor_withheld_gates WHERE required_dataset_id = ? ORDER BY gate_id",
        (ds_id,))]
    for gid in gates:
        _audit(conn, gid, "DATASET_REGISTERED", WITHHELD,
               {"dataset_id": ds_id, "usable_as_gate": usable, "missing": missing,
                "u_dig": u_dig, "u_dig_kind": kind, "original_file_sha256": orig_sha,
                "actor": actor}, now)
    results = [rejudge_gate(conn, gid, now=now) for gid in gates] if rejudge else []
    conn.commit()
    return {"dataset_id": ds_id, "usable_as_gate": usable, "missing": missing,
            "u_dig": u_dig, "u_dig_kind": kind, "original_file_sha256": orig_sha,
            "validation_verdict": validation["verdict"],
            "bound_gates": gates, "rejudged": results}


# --------------------------------------------------------------------------- #
# 门声明 / 重判
# --------------------------------------------------------------------------- #
def declare_gate(
    conn: Conn,
    artifact: Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
    artifact_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """声明一门 WITHHELD 门：落不可变契约基线（哈希）后立即重判一次。"""
    now = now or utc_now()
    art = dict(artifact)
    if art.get("schema") and art["schema"] != SCHEMA_ID:
        raise ValueError(f"门工件 schema={art['schema']!r}，期望 {SCHEMA_ID!r}")
    gid = str(art.get("gate_id") or "").strip()
    if not gid:
        raise ValueError("gate_id 必填")
    req = art.get("requires_reference") or {}
    if not req.get("dataset_id"):
        raise ValueError("requires_reference.dataset_id 必填（WITHHELD 门必须指认欠哪张数据集）")
    tol = float(req.get("tolerance") if req.get("tolerance") is not None else 0.15)
    k = float(req.get("k_factor") if req.get("k_factor") is not None else 2.0)
    if not (0.0 < tol <= 0.5):
        raise ValueError(f"tolerance={tol} 超出 (0, 0.5]：容差不能自证宽松")
    if k <= 0:
        raise ValueError(f"k_factor={k} 必须为正")

    art.setdefault("declared_at", iso(now))
    art.setdefault("reason_code", R_NO_REFERENCE)
    fingerprint = artifact_fingerprint(art)
    contract = artifact_contract(art)

    existing = conn.one(
        "SELECT artifact_sha256, tolerance FROM tensor_withheld_gates WHERE gate_id = ?", (gid,))
    if existing:
        if existing["artifact_sha256"] != fingerprint:
            _audit(conn, gid, "TAMPER_BLOCKED", FAIL,
                   {"stage": "declare", "baseline": existing["artifact_sha256"],
                    "incoming": fingerprint,
                    "baseline_tolerance": existing["tolerance"],
                    "incoming_tolerance": contract["tolerance"]}, now)
            conn.commit()
            return {"gate_id": gid, "verdict": FAIL, "reason_code": R_TOLERANCE_TAMPERED,
                    "tampered": True,
                    "detail": "门已存在且契约指纹不同：拒绝覆盖基线（tol 只可收紧、不可放宽）"}
        return rejudge_gate(conn, gid, now=now)

    conn.execute(
        """INSERT INTO tensor_withheld_gates
               (gate_id, mission_id, node, repo, quantity, unit, reason_code,
                required_dataset_id, tolerance, k_factor, artifact_sha256, artifact_json,
                verdict, verdict_reason, verdict_reason_code, cleared_at, created_at,
                last_judged_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (gid, art.get("mission_id"), str(art.get("node") or ""), str(art.get("repo") or ""),
         str(art.get("quantity") or ""), str(art.get("unit") or ""),
         str(art.get("reason_code") or R_NO_REFERENCE), str(req.get("dataset_id")),
         tol, k, fingerprint, canonical(art), WITHHELD,
         f"{R_NO_REFERENCE}: 门已声明，等待参考数据集登记", R_NO_REFERENCE, None,
         iso(now), None),
    )
    _audit(conn, gid, "GATE_DECLARED", WITHHELD,
           {"dataset_id": req.get("dataset_id"), "tolerance": tol, "k_factor": k,
            "artifact_sha256": fingerprint,
            "artifact_path": str(artifact_path) if artifact_path else None,
            "measurement": contract.get("measurement")}, now)
    conn.commit()
    return rejudge_gate(conn, gid, now=now)


def _reference_from_dataset(
    dataset: Mapping[str, Any], at: Optional[float]
) -> Tuple[Optional[float], float, Optional[float]]:
    """按测量自变量在数字化表上插值 → (ref, denominator_floor, sigma_at)。超范围不外推。"""
    pts = [p for p in (dataset.get("points") or [])
           if isinstance(p, (list, tuple)) and len(p) >= 2]
    ys = [abs(float(p[1])) for p in pts]
    y_scale_raw = dataset.get("y_scale")
    y_scale = ((y_scale_raw.get("value") if isinstance(y_scale_raw, dict) else y_scale_raw)
               or (max(ys) if ys else 0.0))
    floor = 0.05 * float(y_scale) if y_scale else 0.0
    if not pts:
        val = dataset.get("value")
        return (float(val) if isinstance(val, (int, float)) else None), floor, None
    if at is None:
        return float(pts[0][1]), floor, (float(pts[0][2]) if len(pts[0]) > 2 else None)
    xs = [float(p[0]) for p in pts]
    if at < min(xs) or at > max(xs):
        return None, floor, None  # 不外推：超覆盖范围 = 无参考值
    ref = interp(xs, [float(p[1]) for p in pts], float(at))
    sigma = None
    if all(len(p) > 2 for p in pts):
        sigma = interp(xs, [float(p[2]) for p in pts], float(at))
    return ref, floor, sigma


def rejudge_gate(
    conn: Conn,
    gate_id: str,
    *,
    now: Optional[datetime] = None,
    artifact: Optional[Mapping[str, Any]] = None,
    artifact_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """按已登记数据集重判一门。

    给出 ``artifact``/``artifact_path`` 时做哈希核对：与落库基线不一致 ⇒ FAIL +
    ``TAMPER_BLOCKED``。生效容差永远取门原始工件与基线的较紧者。
    """
    now = now or utc_now()
    row = conn.one("SELECT * FROM tensor_withheld_gates WHERE gate_id = ?", (gate_id,))
    if not row:
        raise KeyError(f"门 {gate_id} 未声明")

    baseline_fp = row["artifact_sha256"]
    incoming_fp: Optional[str] = None
    if artifact_path is not None:
        incoming_fp = artifact_fingerprint(json.loads(Path(artifact_path).read_text("utf-8")))
    elif artifact is not None:
        incoming_fp = artifact_fingerprint(dict(artifact))

    if incoming_fp is not None and incoming_fp != baseline_fp:
        _audit(conn, gate_id, "TAMPER_BLOCKED", FAIL,
               {"stage": "rejudge", "baseline": baseline_fp, "incoming": incoming_fp,
                "note": "契约字段（tol/k/实测值/数据集绑定）与基线不符"}, now)
        _set_verdict(conn, gate_id, FAIL, R_TOLERANCE_TAMPERED,
                     "门工件契约指纹与落库基线不符：拒绝按新容差判决", now)
        conn.commit()
        return {"gate_id": gate_id, "verdict": FAIL, "reason_code": R_TOLERANCE_TAMPERED,
                "tampered": True, "cleared": False,
                "detail": "tol 放宽/契约改写被拒；该门不计清偿"}

    art = json.loads(row["artifact_json"]) if isinstance(row["artifact_json"], str) \
        else dict(row["artifact_json"])
    req = art.get("requires_reference") or {}
    tol = min(float(row["tolerance"]), float(req.get("tolerance") or row["tolerance"]))
    k = float(row["k_factor"])
    meas = req.get("measurement") or {}
    m = meas.get("value")
    u_exp = float(meas.get("u_exp") or 0.0)

    ds_row = conn.one("SELECT * FROM tensor_reference_datasets WHERE dataset_id = ?",
                      (row["required_dataset_id"],))
    metrics: Dict[str, Any] = {}
    if not ds_row:
        verdict, reason = WITHHELD, R_NO_REFERENCE
        detail = f"数据集 {row['required_dataset_id']} 尚未登记（门保持 WITHHELD）"
    elif not int(ds_row["usable_as_gate"] or 0):
        verdict, reason = WITHHELD, R_REFERENCE_INCOMPLETE
        validation = (json.loads(ds_row["validation_json"])
                      if isinstance(ds_row["validation_json"], str) else dict(ds_row["validation_json"]))
        detail = (f"数据集不可作判据：validate.blocking={validation.get('blocking')}, "
                  f"registration_missing={validation.get('registration_missing')}")
        metrics = {"dataset_validation": validation}
    elif m is None:
        verdict, reason = WITHHELD, R_MEASUREMENT_MISSING
        detail = "门工件未声明实测值 measurement.value"
    else:
        dataset = (json.loads(ds_row["payload_json"])
                   if isinstance(ds_row["payload_json"], str) else dict(ds_row["payload_json"]))
        ref, floor, sigma = _reference_from_dataset(dataset, meas.get("at"))
        if ref is None:
            verdict, reason = WITHHELD, R_OUT_OF_RANGE
            detail = (f"测量自变量 at={meas.get('at')} 超出数字化表覆盖范围："
                      "不做外推，无法判 PASS/FAIL")
            metrics = {"at": meas.get("at")}
        else:
            u_dig_decl, kind, _method, _sha = digitisation_meta(dataset)
            if kind == "absolute":
                u_dig_abs = abs(u_dig_decl)
            elif u_dig_decl > 0.0:
                u_dig_abs = abs(u_dig_decl * ref)
            elif sigma is not None:
                u_dig_abs = abs(float(sigma))  # 回落到逐点数字化不确定度
            else:
                u_dig_abs = 0.0
            res = judge(m, ref, tolerance=tol, k_factor=k, u_exp=u_exp,
                        u_dig=u_dig_abs, denominator_floor=floor)
            verdict, reason = res["verdict"], res["reason_code"]
            detail = (f"E={res['E']:+.4f} band={res['band']:.4f} "
                      f"(tol={res['tolerance_band']:.3f}, "
                      f"k·u/D={res['uncertainty_band']:.4f}) 判定 {verdict}")
            metrics = {"measured": m, "reference": ref, "E": res["E"], "band": res["band"],
                       "tolerance_band": res["tolerance_band"],
                       "uncertainty_band": res["uncertainty_band"],
                       "u_exp": u_exp, "u_dig": u_dig_abs, "u_dig_kind": kind,
                       "at": meas.get("at"), "denominator": res["denominator"]}

    _set_verdict(conn, gate_id, verdict, reason, detail, now)
    _audit(conn, gate_id, "REJUDGED", verdict,
           {"reason_code": reason, "detail": detail, "tolerance": tol, "k_factor": k,
            "dataset_id": row["required_dataset_id"],
            "artifact_sha256": baseline_fp, **metrics}, now)
    conn.commit()
    return {"gate_id": gate_id, "verdict": verdict, "reason_code": reason,
            "cleared": verdict == PASS, "detail": detail,
            "tolerance": tol, "artifact_sha256": baseline_fp, **metrics}


def _set_verdict(conn: Conn, gate_id: str, verdict: str, reason_code: str,
                 detail: str, now: datetime) -> None:
    conn.execute(
        """UPDATE tensor_withheld_gates
              SET verdict = ?, verdict_reason = ?, verdict_reason_code = ?, cleared_at = ?,
                  last_judged_at = ?
            WHERE gate_id = ?""",
        (verdict, f"{reason_code}: {detail}", reason_code,
         iso(now) if verdict == PASS else None, iso(now), gate_id))


# --------------------------------------------------------------------------- #
# 哈希链审计
# --------------------------------------------------------------------------- #
def _audit(conn: Conn, gate_id: str, event: str, verdict: str,
           payload: Mapping[str, Any], now: datetime) -> str:
    """追加一行哈希链审计：row_hash = H(prev_hash|gate|seq|event|verdict|payload|ts)。"""
    prev = conn.one(
        "SELECT row_hash, seq FROM tensor_withheld_audit WHERE gate_id = ? "
        "ORDER BY seq DESC LIMIT 1", (gate_id,))
    prev_hash = prev["row_hash"] if prev else "0" * 64
    seq = int(prev["seq"]) + 1 if prev else 1
    created = iso(now)
    row_hash = sha256_text("|".join([prev_hash, gate_id, str(seq), event, verdict,
                                     canonical(payload), created]))
    conn.execute(
        """INSERT INTO tensor_withheld_audit
               (gate_id, seq, event, verdict, payload, prev_hash, row_hash, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (gate_id, seq, event, verdict, canonical(payload), prev_hash, row_hash, created))
    return row_hash


def verify_audit_chain(conn: Conn, gate_id: Optional[str] = None) -> Dict[str, Any]:
    """重算每条链：prev 指针与 row_hash 都必须自洽，否则报断裂行。"""
    if gate_id:
        chains = {gate_id: conn.query(
            "SELECT * FROM tensor_withheld_audit WHERE gate_id = ? ORDER BY seq", (gate_id,))}
    else:
        chains = {}
        for r in conn.query("SELECT * FROM tensor_withheld_audit ORDER BY gate_id, seq"):
            chains.setdefault(r["gate_id"], []).append(r)
    broken: List[Dict[str, Any]] = []
    checked = 0
    for gid, rows in chains.items():
        prev_hash = "0" * 64
        for r in rows:
            payload = r["payload"] if isinstance(r["payload"], str) else canonical(r["payload"])
            expected = sha256_text("|".join([prev_hash, gid, str(r["seq"]), r["event"],
                                             r["verdict"], payload, str(r["created_at"])]))
            checked += 1
            if str(r["prev_hash"]) != prev_hash or str(r["row_hash"]) != expected:
                broken.append({"gate_id": gid, "seq": r["seq"],
                               "prev_ok": str(r["prev_hash"]) == prev_hash,
                               "hash_ok": str(r["row_hash"]) == expected})
            prev_hash = str(r["row_hash"])
    return {"ok": not broken, "rows": checked, "chains": len(chains), "broken": broken}


# --------------------------------------------------------------------------- #
# KPI + 自动派单
# --------------------------------------------------------------------------- #
def open_withheld_gates(conn: Conn) -> List[Dict[str, Any]]:
    return conn.query(
        """SELECT gate_id, repo, required_dataset_id, verdict_reason_code, verdict_reason,
                  tolerance, created_at, node, mission_id
             FROM tensor_withheld_gates
            WHERE verdict = 'WITHHELD' AND cleared_at IS NULL
            ORDER BY repo, required_dataset_id, created_at""")


def compute_kpis(conn: Conn, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """withheld_gate_share（未清偿 WITHHELD / 全部门）与 withheld_mean_age_days。"""
    now = now or utc_now()
    gates = conn.query(
        "SELECT gate_id, verdict, cleared_at, created_at, verdict_reason_code "
        "FROM tensor_withheld_gates")
    total = len(gates)
    open_w = [g for g in gates if g["verdict"] == WITHHELD and not g["cleared_at"]]
    ages = []
    for g in open_w:
        created = as_dt(g["created_at"])
        if created:
            # 时钟回拨/未来声明：账龄下限 0，不允许负账龄（负数会把均值拉低成假清偿）
            ages.append(max(0.0, (now - created).total_seconds() / 86400.0))
    cleared = [g for g in gates if g["verdict"] == PASS and g["cleared_at"]]
    clear_ages = []
    for g in cleared:
        created, done = as_dt(g["created_at"]), as_dt(g["cleared_at"])
        if created and done:
            clear_ages.append(max(0.0, (done - created).total_seconds() / 86400.0))
    by_reason: Dict[str, int] = {}
    for g in open_w:
        rc = str(g["verdict_reason_code"] or "UNKNOWN")
        by_reason[rc] = by_reason.get(rc, 0) + 1
    return {
        "snapshot_at": iso(now),
        "gates_total": total,
        "gates_open_withheld": len(open_w),
        "gates_pass": len(cleared),
        "gates_fail": len([g for g in gates if g["verdict"] == FAIL]),
        KPI_SHARE: (len(open_w) / total) if total else 0.0,
        KPI_AGE: (sum(ages) / len(ages)) if ages else 0.0,
        "open_withheld_max_age_days": max(ages) if ages else 0.0,
        "mean_time_to_clear_days": (sum(clear_ages) / len(clear_ages)) if clear_ages else 0.0,
        "open_by_reason": by_reason,
        "targets": dict(KPI_TARGETS),
    }


def snapshot_kpis(conn: Conn, *, organization_id: int = 0,
                  now: Optional[datetime] = None) -> Dict[str, Any]:
    """把 KPI 写入 tensor_guide_tracker（小时桶幂等 upsert，密钥对齐 095 表结构）。"""
    now = now or utc_now()
    kpis = compute_kpis(conn, now=now)
    bucket = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")
    rows = []
    for key in (KPI_SHARE, KPI_AGE):
        value = float(kpis[key])
        note = (f"source=withheld_gates; gates_total={kpis['gates_total']}; "
                f"open_withheld={kpis['gates_open_withheld']}; "
                f"pass={kpis['gates_pass']}; fail={kpis['gates_fail']}; "
                f"open_by_reason={canonical(kpis['open_by_reason'])}")
        conn.execute(
            """INSERT INTO tensor_guide_tracker
                   (organization_id, metric_key, metric_value, target_value, snapshot_at, notes)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT (organization_id, metric_key, snapshot_at) DO UPDATE SET
                   metric_value=excluded.metric_value, target_value=excluded.target_value,
                   notes=excluded.notes""",
            (organization_id, key, value, KPI_TARGETS[key], bucket, note))
        rows.append({"metric_key": key, "metric_value": value,
                     "target_value": KPI_TARGETS[key], "snapshot_at": bucket})
    conn.commit()
    return {"snapshot_at": bucket, "written": rows, "kpis": kpis}


def dispatch_unresolved(conn: Conn, *, now: Optional[datetime] = None,
                        min_age_days: float = 0.0) -> List[Dict[str, Any]]:
    """未清偿 WITHHELD 门按 (repo, dataset_id, age) 自动派单（幂等工单号）。"""
    now = now or utc_now()
    buckets: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for g in open_withheld_gates(conn):
        buckets.setdefault((str(g["repo"] or "UNASSIGNED"),
                            str(g["required_dataset_id"])), []).append(g)
    orders: List[Dict[str, Any]] = []
    for (repo, ds_id), gates in buckets.items():
        ages = [(now - as_dt(g["created_at"])).total_seconds() / 86400.0
                for g in gates if as_dt(g["created_at"])]
        age = max(ages) if ages else 0.0
        if age < min_age_days:
            continue
        oldest = min(gates, key=lambda g: str(g["created_at"]))
        order_id = "WO-" + sha256_text(f"{repo}|{ds_id}")[:12]
        orders.append({
            "order_id": order_id,
            "repo": repo,
            "dataset_id": ds_id,
            "n_gates": len(gates),
            "gate_ids": sorted(g["gate_id"] for g in gates),
            "age_days": round(age, 4),
            "reason_code": oldest["verdict_reason_code"],
            "tolerance": float(oldest["tolerance"]),
            "oldest_gate": oldest["gate_id"],
            "oldest_created_at": str(oldest["created_at"]),
            "action": ACTIONS.get(str(oldest["verdict_reason_code"]),
                                  "人工判定后重新登记/重判"),
            "priority": 1 if age >= 14 else (2 if age >= 7 else 3),
        })
    orders.sort(key=lambda o: (o["repo"], o["dataset_id"], -o["age_days"]))
    return orders


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _load_json(path: str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="WITHHELD 三态清偿闭环：门声明 ↔ 参考数据登记 ↔ 自动重判 ↔ 派单/KPI")
    ap.add_argument("--db", default=":memory:", help="sqlite 路径或 PG DSN")
    ap.add_argument("--declare", metavar="ARTIFACT_JSON", help="声明（或引用）门工件")
    ap.add_argument("--register", metavar="DATASET_JSON", help="登记参考数据集（触发重判）")
    ap.add_argument("--rejudge", metavar="GATE_ID", help="单门重判")
    ap.add_argument("--artifact", metavar="ARTIFACT_JSON",
                    help="重判时提供现行工件做哈希核对（不符即 TAMPER_BLOCKED）")
    ap.add_argument("--kpi", action="store_true", help="算 KPI 并写 tensor_guide_tracker")
    ap.add_argument("--org-id", type=int, default=0)
    ap.add_argument("--dispatch", action="store_true", help="未清偿门按 (repo,dataset,age) 派单")
    ap.add_argument("--min-age-days", type=float, default=0.0)
    ap.add_argument("--verify", action="store_true", help="校验哈希链审计")
    ap.add_argument("--now", help="注入时钟（ISO8601，复现/测试用）")
    ap.add_argument("--out", help="结果 JSON 落盘路径")
    args = ap.parse_args(argv)

    now = as_dt(args.now) if args.now else utc_now()
    conn = connect(args.db)
    result: Dict[str, Any] = {"db": args.db, "now": iso(now or utc_now())}
    try:
        if args.declare:
            result["declare"] = declare_gate(conn, _load_json(args.declare),
                                            now=now, artifact_path=Path(args.declare))
        if args.register:
            result["register"] = register_reference(conn, _load_json(args.register), now=now)
        if args.rejudge:
            artifact = _load_json(args.artifact) if args.artifact else None
            result["rejudge"] = rejudge_gate(
                conn, args.rejudge, now=now, artifact=artifact,
                artifact_path=Path(args.artifact) if args.artifact else None)
        if args.kpi:
            result["kpi"] = snapshot_kpis(conn, organization_id=args.org_id, now=now)
        if args.dispatch:
            result["dispatch"] = dispatch_unresolved(conn, now=now,
                                                     min_age_days=args.min_age_days)
        if args.verify:
            result["audit"] = verify_audit_chain(conn)
    finally:
        conn.commit()
        conn.close()

    text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"[out] → {out}")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
