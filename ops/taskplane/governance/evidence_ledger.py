#!/usr/bin/env python3
"""证据台账自动登记（evidence ledger auto-registration）——验收通过即入账 + S 级评价触发。

**要防的东西**：验收通过的能力「口口相传」而不入账——能力清单永远是旧的，S 级评价靠
事后追认，谁也说不清某个等级是根据哪次验收、哪份报告、哪组数字给的。于是台账做三件事：

1. **追加式存储**：SQLite 表 ``ledger_entries``，一条记录 = 一次验收的能力增量
   （capability_id / mission_id / category / grade / reason / 判据明细 / 工件）。行是
   **只增不改**的：``entry_hash`` 由记录内容 + 前一条 ``prev_hash`` 派生（SHA-256 链），
   任何事后改字都会让 ``verify_chain`` 断链——这就是审计追溯。
2. **判据驱动入账**：只有「验收通过 + 全部机器判据 pass + 判据条数 ≥ 下限」才入账；
   未通过 / 未验收一律写进 ``ledger_attempts``（含拒绝码），honest FAIL 留痕但不冒充能力。
3. **S 级评价自动触发**：``evaluate_grade`` 按证据强度给 S1/S2/S3，理由**必须带数字**
   （逐条判据的值/阈值、基线误差 Δ 的数值），空话理由会被 ``_vague_reason`` 拦下重算：
   - S3：全判据 pass + 相对基线可复算 + 声明突破点 + 有工件
   - S2：全判据 pass + 相对基线可复算（有对照才算增量）
   - S1：全判据 pass（无基线 → 只有执行合格）
   等级是**证据结构的函数**，不是调用方一句话自封：宣言里没有基线数字就上不了 S2。

幂等：``entry_id``（默认 mission_id:capability_id:content_digest 的哈希）唯一，
重复登记返回 ``IDEMPOTENT`` 而不重复计分。

零领域语义：capability_id / category / 判据 / 工件全部由调用方注入，模块内无学科词条。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

VERSION = "in99-n10-v1"
GENESIS = "0" * 64
GRADES = ("S1", "S2", "S3")
MIN_CRITERIA = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger_entries (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id     TEXT UNIQUE NOT NULL,
    capability_id TEXT NOT NULL,
    mission_id   INTEGER,
    category     TEXT NOT NULL,
    grade        TEXT NOT NULL,
    reason       TEXT NOT NULL,
    evidence     TEXT NOT NULL,
    acceptance   TEXT NOT NULL,
    artifacts    TEXT NOT NULL,
    created_utc  TEXT NOT NULL,
    prev_hash    TEXT NOT NULL,
    entry_hash   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_cap ON ledger_entries(capability_id);
CREATE INDEX IF NOT EXISTS idx_ledger_grade ON ledger_entries(grade);
CREATE TABLE IF NOT EXISTS ledger_attempts (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id   INTEGER,
    capability_id TEXT NOT NULL,
    accepted     INTEGER NOT NULL,
    code         TEXT NOT NULL,
    detail       TEXT NOT NULL,
    digest       TEXT NOT NULL,
    created_utc  TEXT NOT NULL
);
"""

VAGUE_PATTERNS = ("", "ok", "done", "完成", "无", "见报告", "见上文", "待补", "tbd", "n/a")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8") if isinstance(p, str) else
                 _dumps(p).encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


# ─────────────────────────── 台账存储 ───────────────────────────

def connect(path: str | Path) -> sqlite3.Connection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _last_hash(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT entry_hash FROM ledger_entries ORDER BY seq DESC LIMIT 1").fetchone()
    return row["entry_hash"] if row else GENESIS


_JSON_FIELDS = ("evidence", "acceptance", "artifacts")


def _norm_json(value: Any) -> Any:
    """入库前后同一份内容：插入时是对象，复算时是 JSON 文本——统一回对象再散列。"""
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _canonical_entry(entry: Mapping[str, Any]) -> str:
    keys = ("entry_id", "capability_id", "mission_id", "category", "grade", "reason",
            "evidence", "acceptance", "artifacts", "created_utc", "prev_hash")
    out = {}
    for k in keys:
        v = entry.get(k)
        out[k] = _norm_json(v) if k in _JSON_FIELDS else v
    return _dumps(out)


def entry_hash(entry: Mapping[str, Any]) -> str:
    return _digest(_canonical_entry(entry))


# ─────────────────────────── 判据与等级 ───────────────────────────

@dataclass(frozen=True)
class Criterion:
    id: str
    passed: bool
    value: Any = None
    threshold: Any = None

    @classmethod
    def parse(cls, raw: Any) -> "Criterion":
        if isinstance(raw, Mapping):
            cid = str(raw.get("id", raw.get("name", "?")))
            passed = bool(raw.get("passed", raw.get("pass", raw.get("ok", False))))
            return cls(cid, passed, raw.get("value"), raw.get("threshold"))
        if isinstance(raw, (str, bytes)):
            return cls(str(raw), True)          # 判据文本即已声明通过
        return cls(str(raw), bool(raw))


def _baseline_delta(baseline: Any) -> Optional[Dict[str, float]]:
    """基线可复算：需要 metric + reference + measured 三个数值，返回 {delta, rel_error}。"""
    if not isinstance(baseline, Mapping):
        return None
    try:
        ref = float(baseline["reference"])
        meas = float(baseline["measured"])
    except (KeyError, TypeError, ValueError):
        return None
    if ref == 0:
        return None
    delta = meas - ref
    return {"reference": ref, "measured": meas, "delta": delta,
            "rel_error": abs(delta) / abs(ref), "metric": str(baseline.get("metric", "?"))}


def _vague_reason(reason: str) -> bool:
    text = (reason or "").strip().lower()
    if text in VAGUE_PATTERNS:
        return True
    digits = sum(ch.isdigit() for ch in text)
    # 至少两个数字（如 "3/3 全通过"）：理由里的判据计数本身即最小的可核对量
    return digits < 2


@dataclass
class GradeVerdict:
    grade: Optional[str]
    grade_reason: str
    criteria: List[Criterion] = field(default_factory=list)
    baseline: Optional[Dict[str, float]] = None
    requires_independent_review: bool = False

    def to_dict(self) -> dict:
        return {
            "grade": self.grade,
            "grade_reason": self.grade_reason,
            "criteria": [{"id": c.id, "passed": c.passed, "value": c.value,
                          "threshold": c.threshold} for c in self.criteria],
            "baseline": self.baseline,
            "requires_independent_review": self.requires_independent_review,
        }


def evaluate_grade(record: Mapping[str, Any], min_criteria: int = MIN_CRITERIA) -> GradeVerdict:
    """按证据结构给 S1/S2/S3（无证据 → None）。理由由判据数字程序化生成。"""
    criteria = [Criterion.parse(c) for c in (record.get("acceptance") or [])]
    passed = [c for c in criteria if c.passed]
    baseline = _baseline_delta(record.get("baseline"))
    artifacts = [a for a in (record.get("artifacts") or []) if str(a).strip()]
    change = record.get("change") or {}
    declares = bool(change.get("declares_breakthrough")) if isinstance(change, Mapping) else False
    fail_ids = [c.id for c in criteria if not c.passed]

    if not record.get("accepted"):
        return GradeVerdict(None, "未验收通过，不入定额（honest FAIL 走 ledger_attempts 留痕）",
                            criteria, baseline)
    if len(criteria) < min_criteria:
        return GradeVerdict(None, f"机器判据仅 {len(criteria)} 条 < 下限 {min_criteria}，不予定级",
                            criteria, baseline)
    if fail_ids:
        return GradeVerdict(None, f"判据未全通过 {len(criteria) - len(passed)}/{len(criteria)}："
                                   f"{fail_ids}", criteria, baseline)

    base_txt = ("无基线对照（只有执行合格）" if baseline is None else
                f"{baseline['metric']}: 实测 {baseline['measured']:.6g} vs 基准 "
                f"{baseline['reference']:.6g}，Δ={baseline['delta']:.6g}"
                f"（相对误差 {baseline['rel_error']:.2%}）")
    if baseline is not None and declares and artifacts:
        grade = "S3"
        reason = (f"判据 {len(passed)}/{len(criteria)} 全通过；{base_txt}；"
                  f"声明突破点 + {len(artifacts)} 个可复算工件")
        review = True
    elif baseline is not None:
        grade = "S2"
        reason = (f"判据 {len(passed)}/{len(criteria)} 全通过；{base_txt}"
                  f"（缺突破宣言或工件，不升 S3）")
        review = True
    else:
        grade = "S1"
        reason = f"判据 {len(passed)}/{len(criteria)} 全通过；{base_txt}"
        review = False
    if _vague_reason(reason):
        raise ValueError(f"等级理由不含可核对数字，拒绝签级：{reason!r}")
    return GradeVerdict(grade, reason, criteria, baseline, review)


# ─────────────────────────── 登记 ───────────────────────────

@dataclass
class RegisterResult:
    ok: bool
    code: str
    entry: Optional[Dict[str, Any]] = None
    detail: str = ""
    grade_verdict: Optional[GradeVerdict] = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "code": self.code, "detail": self.detail,
            "entry": self.entry,
            "grade": (self.grade_verdict.to_dict() if self.grade_verdict else None),
        }


def register(path: str | Path, record: Mapping[str, Any],
             min_criteria: int = MIN_CRITERIA) -> RegisterResult:
    """验收结果自动登记。record 见模块文档；返回 RegisterResult（含拒绝码与等级）。"""
    cap = str(record.get("capability_id") or "").strip()
    category = str(record.get("category") or "").strip()
    mission_id = record.get("mission_id")
    if not cap:
        return RegisterResult(False, "MISSING_CAPABILITY_ID", detail="capability_id 为空")
    if not category:
        return RegisterResult(False, "MISSING_CATEGORY", detail="category 为空")
    verdict = evaluate_grade(record, min_criteria)
    digest = _digest(cap, mission_id, record.get("acceptance"),
                     record.get("baseline"), record.get("artifacts"))
    conn = connect(path)
    try:
        if not record.get("accepted"):
            _log_attempt(conn, mission_id, cap, False, "NOT_ACCEPTED",
                         str(record.get("detail") or "验收未通过"), digest)
            return RegisterResult(False, "NOT_ACCEPTED", detail="验收未通过，不入定额",
                                  grade_verdict=verdict)
        if verdict.grade is None:
            code = "CRITERIA_FAILED" if verdict.criteria else "NO_CRITERIA"
            _log_attempt(conn, mission_id, cap, True, code, verdict.grade_reason, digest)
            return RegisterResult(False, code, detail=verdict.grade_reason,
                                  grade_verdict=verdict)
        entry_id = record.get("entry_id") or f"{mission_id}:{cap}:{digest[:16]}"
        row = conn.execute("SELECT * FROM ledger_entries WHERE entry_id=?",
                           (str(entry_id),)).fetchone()
        if row is not None:
            return RegisterResult(True, "IDEMPOTENT", entry=_row_to_dict(row),
                                  detail="同 entry_id 已入账，未重复计分",
                                  grade_verdict=verdict)
        entry = {
            "entry_id": str(entry_id),
            "capability_id": cap,
            "mission_id": int(mission_id) if mission_id is not None else None,
            "category": category,
            "grade": verdict.grade,
            "reason": verdict.grade_reason,
            "evidence": record.get("evidence") or {},
            "acceptance": [c.to_dict() if hasattr(c, "to_dict") else
                           {"id": c.id, "passed": c.passed, "value": c.value,
                            "threshold": c.threshold} for c in verdict.criteria],
            "artifacts": [str(a) for a in (record.get("artifacts") or [])],
            "created_utc": str(record.get("created_utc") or _now()),
            "prev_hash": _last_hash(conn),
        }
        entry["entry_hash"] = entry_hash(entry)
        conn.execute(
            "INSERT INTO ledger_entries(entry_id,capability_id,mission_id,category,grade,"
            "reason,evidence,acceptance,artifacts,created_utc,prev_hash,entry_hash) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (entry["entry_id"], entry["capability_id"], entry["mission_id"],
             entry["category"], entry["grade"], entry["reason"],
             _dumps(entry["evidence"]), _dumps(entry["acceptance"]),
             _dumps(entry["artifacts"]), entry["created_utc"], entry["prev_hash"],
             entry["entry_hash"]))
        conn.commit()
        return RegisterResult(True, "REGISTERED", entry=entry,
                              detail=f"入账 {verdict.grade}",
                              grade_verdict=verdict)
    finally:
        conn.close()


def _log_attempt(conn: sqlite3.Connection, mission_id: Any, cap: str, accepted: bool,
                 code: str, detail: str, digest: str) -> None:
    conn.execute("INSERT INTO ledger_attempts(mission_id,capability_id,accepted,code,detail,"
                 "digest,created_utc) VALUES(?,?,?,?,?,?,?)",
                 (int(mission_id) if mission_id is not None else None, cap,
                  int(bool(accepted)), code, detail, digest, _now()))
    conn.commit()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    out = dict(row)
    for key in ("evidence", "acceptance", "artifacts"):
        try:
            out[key] = json.loads(out[key])
        except (TypeError, ValueError):
            pass
    return out


# ─────────────────────────── 查询与审计 ───────────────────────────

def query(path: str | Path, capability_id: Optional[str] = None,
          grade: Optional[str] = None, mission_id: Optional[int] = None,
          category: Optional[str] = None) -> List[Dict[str, Any]]:
    """能力清单查询（按入账序返回）。"""
    sql = "SELECT * FROM ledger_entries WHERE 1=1"
    params: List[Any] = []
    if capability_id:
        sql += " AND capability_id=?"
        params.append(capability_id)
    if grade:
        sql += " AND grade=?"
        params.append(grade)
    if mission_id is not None:
        sql += " AND mission_id=?"
        params.append(int(mission_id))
    if category:
        sql += " AND category=?"
        params.append(category)
    sql += " ORDER BY seq"
    conn = connect(path)
    try:
        return [_row_to_dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def capability_index(path: str | Path) -> Dict[str, Dict[str, Any]]:
    """能力清单：每个 capability 的最新等级 + 历史条数 + 最高等级。"""
    rows = query(path)
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        cur = out.setdefault(r["capability_id"], {
            "capability_id": r["capability_id"], "n_entries": 0,
            "latest_grade": None, "latest_entry": None,
            "highest_grade": None, "missions": [],
        })
        cur["n_entries"] += 1
        cur["latest_grade"] = r["grade"]
        cur["latest_entry"] = r["entry_id"]
        cur["missions"].append(r["mission_id"])
        hj = GRADES.index(r["grade"]) if r["grade"] in GRADES else -1
        cj = GRADES.index(cur["highest_grade"]) if cur["highest_grade"] in GRADES else -1
        if hj > cj:
            cur["highest_grade"] = r["grade"]
    return dict(sorted(out.items()))


def verify_chain(path: str | Path) -> Dict[str, Any]:
    """逐条复算 hash 链，返回审计结论（首个坏点 entry_id / seq）。"""
    conn = connect(path)
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM ledger_entries ORDER BY seq")]
    finally:
        conn.close()
    prev = GENESIS
    for row in rows:
        if row["prev_hash"] != prev:
            return {"ok": False, "n_entries": len(rows), "bad_seq": row["seq"],
                    "bad_entry": row["entry_id"], "code": "PREV_HASH_MISMATCH"}
        if entry_hash(row) != row["entry_hash"]:
            return {"ok": False, "n_entries": len(rows), "bad_seq": row["seq"],
                    "bad_entry": row["entry_id"], "code": "CONTENT_TAMPERED"}
        prev = row["entry_hash"]
    return {"ok": True, "n_entries": len(rows), "head_hash": prev}


def trace(path: str | Path, entry_id: str) -> Dict[str, Any]:
    """审计追溯：返回该条目及其在链上的位置 + 整链校验结论。"""
    conn = connect(path)
    try:
        row = conn.execute("SELECT * FROM ledger_entries WHERE entry_id=?", (entry_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"ok": False, "code": "NOT_FOUND", "entry_id": entry_id}
    entry = _row_to_dict(row)
    chain = verify_chain(path)
    return {"ok": True, "entry": entry, "seq": entry["seq"], "chain": chain,
            "prev_hash": entry["prev_hash"], "entry_hash": entry["entry_hash"]}


def attempts(path: str | Path) -> List[Dict[str, Any]]:
    """被拒登记留痕（honest FAIL 台账）。"""
    conn = connect(path)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM ledger_attempts ORDER BY seq")]
    finally:
        conn.close()


def s_events(path: str | Path) -> List[Dict[str, Any]]:
    """S 级评价触发事件：S2/S3 条目需要独立席位复判。"""
    return [{"entry_id": r["entry_id"], "capability_id": r["capability_id"],
             "grade": r["grade"], "reason": r["reason"],
             "requires_independent_review": r["grade"] in ("S2", "S3")}
            for r in query(path) if r["grade"] in GRADES]