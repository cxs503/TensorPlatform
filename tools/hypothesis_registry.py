#!/usr/bin/env python3
"""hypothesis_registry —— 预注册假设台账（盲预测不可改 + 开盲流 + 红队负例）。

背景（IN59-A2）
---------------
战役要求每一次「实验」之前先把假设写进机器可查的台账：假设陈述、理论依据
（必须引文献或推导）、可证伪实验设计、**实验实施前登记的数值/方向预测**，
实验做完再回填 ``outcome``。缺这一层，事后的「我早就猜到了」无法与事后的
合理化区分，falsified 结果就被系统性隐瞒。

本模块给出一张**可机审**的表（SQLite，DDL 见 :data:`DDL`）与四个动作：

============  ==============================================================
``register``  登记（字段非空 + 预测冻结为 sha256 承诺 + 登记时证据集必须为空）
``log_run``   记录一次实验执行（要求：已登记、仍 pending、run_utc >= 登记时刻）
``close``     结题判定（要求：至少一次 run、终态单向、falsified 必带复盘）
``verify``    完整性审计（承诺复算 + 事件哈希链 + 时序 + 终态守卫）
============  ==============================================================

不可改与开盲流：机器门
----------------------
1. **字段冻结**：``prediction_before`` 等 10 个字段由 SQLite 触发器
   ``trg_hr_immutable_core`` 硬拒 UPDATE（不靠 Python 自觉，裸 SQL 也改不动）。
2. **预测承诺**：``prediction_commitment = sha256(规范 JSON)``。即使有人
   ``DROP TRIGGER`` 后改写预测，``verify`` 复算承诺即报 ``commitment_mismatch``。
3. **时序锁**：``experiment_runs`` 插入触发器要求 (a) 假设已登记，
   (b) 假设仍 pending，(c) ``run_utc >= registered_utc``。三条件把「先跑后登」
   （post-hoc registration）与「倒填时间戳」（backdating）变成硬报错。
   登记侧另有 :func:`register` 的双门：``evidence_at_registration`` 自述清单
   必须为空，且声明的 ``result_paths`` 不得已存在非空文件（机器事实）。

事件哈希链：``event_log`` 每行 ``entry_hash = sha256(prev|kind|hid|utc|payload_sha)``，
行不可 UPDATE/DELETE；改任一行 → 后续 hash 全对不上，``verify`` 报 ``chain_broken``。

明确的能力边界（诚实声明，见 :func:`limitations`）
--------------------------------------------------
台账能证明的是**库内顺序**：run 一定在登记之后、预测一定没被改、结题一定单向。
它不能证明墙钟世界的真实性——登记前是否已在库外跑过实验，只能靠
``result_paths`` 预声明（结果文件必须先不存在）与外部时间戳旁证来逼近。

S-N 轨（bootstrap 权4）
-----------------------
``falsified`` **不计失败率**，登记进 S-N 轨，权重 4，并标 ``failure_kb=True``
（自动进失败知识库，联 A4）。见 :func:`sn_ledger`。

用法
----
    python3 tools/hypothesis_registry.py init  --db artifacts/hypothesis_registry.db
    python3 tools/hypothesis_registry.py register --db ... --file prereg.json
    python3 tools/hypothesis_registry.py run      --db ... --hid H1 --ref runs/x/out.json
    python3 tools/hypothesis_registry.py close    --db ... --hid H1 --outcome falsified \\
        --basis-file basis.txt --postmortem-file pm.txt
    python3 tools/hypothesis_registry.py verify   --db ...   # 非 0 退出码 = 审计失败
    python3 tools/hypothesis_registry.py report   --db ... --out-json s.json --out-md s.md
    python3 tools/hypothesis_registry.py sql      --db ...

PostgreSQL 部署：:func:`pg_ddl` 生成等价 DDL（CHECK/唯一约束 + 同语义触发器），
``report`` 落盘 ``<out-json 同目录>/hypothesis_registry_pg.sql``。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

__all__ = [
    "SCHEMA_VERSION",
    "OUTCOMES",
    "TERMINAL_OUTCOMES",
    "RegistryError",
    "DDL",
    "connect",
    "init_db",
    "canonical_json",
    "prediction_commitment",
    "register",
    "log_run",
    "close",
    "get",
    "list_hypotheses",
    "verify_integrity",
    "summary",
    "sn_ledger",
    "render_markdown",
    "limitations",
    "pg_ddl",
    "validate_rule",
    "evaluate_rule",
    "RULE_OPS",
]

SCHEMA_VERSION = "1.0.0"
OUTCOMES = ("pending", "confirmed", "falsified", "inconclusive")
TERMINAL_OUTCOMES = ("confirmed", "falsified", "inconclusive")

#: 结题字段长度下限（防空串 / 防 "ok" 这类无信息裁决理由）
MIN_VERDICT_BASIS_CHARS = 20
MIN_POSTMORTEM_CHARS = 40


class RegistryError(Exception):
    """Raised when a registry invariant would be violated (machine gate)."""


# --------------------------------------------------------------------------- #
# DDL：CHECK 约束 = 取值域机器门；触发器 = 不可改 / 时序 / 终态机器门
# --------------------------------------------------------------------------- #
DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS hypothesis_registry (
    hypothesis_id                  TEXT PRIMARY KEY,
    mission_id                     TEXT NOT NULL,
    domain                         TEXT NOT NULL DEFAULT '',
    hypothesis_statement           TEXT NOT NULL
        CHECK (length(trim(hypothesis_statement)) > 0),
    theory_basis                   TEXT NOT NULL
        CHECK (length(trim(theory_basis)) > 0),
    falsification_experiment_design TEXT NOT NULL
        CHECK (length(trim(falsification_experiment_design)) > 0),
    prediction_before              TEXT NOT NULL
        CHECK (json_valid(prediction_before)),
    prediction_commitment          TEXT NOT NULL
        CHECK (length(prediction_commitment) = 64),
    registered_utc                 TEXT NOT NULL,
    registered_by                  TEXT NOT NULL DEFAULT '',
    evidence_at_registration       TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(evidence_at_registration)),
    blind_guards                   TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(blind_guards)),
    outcome                        TEXT NOT NULL DEFAULT 'pending'
        CHECK (outcome IN ('pending', 'confirmed', 'falsified', 'inconclusive')),
    outcome_utc                    TEXT,
    verdict_basis                  TEXT,
    postmortem_note                TEXT,
    sn_track                       TEXT,
    sn_weight                      REAL,
    failure_kb                     INTEGER NOT NULL DEFAULT 0
        CHECK (failure_kb IN (0, 1)),
    schema_version                 TEXT NOT NULL DEFAULT '1.0.0'
);

CREATE TABLE IF NOT EXISTS experiment_runs (
    hypothesis_id TEXT NOT NULL,
    run_seq       INTEGER NOT NULL,
    run_ref       TEXT NOT NULL,
    run_utc       TEXT NOT NULL,
    payload       TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload)),
    logged_utc    TEXT NOT NULL,
    PRIMARY KEY (hypothesis_id, run_seq),
    FOREIGN KEY (hypothesis_id) REFERENCES hypothesis_registry (hypothesis_id)
);

CREATE TABLE IF NOT EXISTS event_log (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,
    hypothesis_id TEXT NOT NULL,
    event_utc     TEXT NOT NULL,
    payload_sha   TEXT NOT NULL,
    prev_hash     TEXT NOT NULL,
    entry_hash    TEXT NOT NULL
);

-- 1) 台账 append-only：禁删
CREATE TRIGGER IF NOT EXISTS trg_hr_no_delete
BEFORE DELETE ON hypothesis_registry
BEGIN
    SELECT RAISE(ABORT, 'hypothesis_registry is append-only (no DELETE)');
END;

-- 2) 登记核心字段永久冻结（含 prediction_before 与其承诺）
CREATE TRIGGER IF NOT EXISTS trg_hr_immutable_core
BEFORE UPDATE OF hypothesis_id, mission_id, hypothesis_statement, theory_basis,
    falsification_experiment_design, prediction_before, prediction_commitment,
    registered_utc, evidence_at_registration, blind_guards
ON hypothesis_registry
BEGIN
    SELECT RAISE(ABORT, 'registered hypothesis is immutable (prediction_before frozen)');
END;

-- 3) 结题单向 + 无实验不得裁决 + 必填裁决依据 / falsified 必带复盘
CREATE TRIGGER IF NOT EXISTS trg_hr_outcome_guard
BEFORE UPDATE OF outcome ON hypothesis_registry
WHEN OLD.outcome <> 'pending'
BEGIN
    SELECT RAISE(ABORT, 'outcome is terminal: already closed (no re-judgement)');
END;

CREATE TRIGGER IF NOT EXISTS trg_hr_outcome_needs_run
BEFORE UPDATE OF outcome ON hypothesis_registry
WHEN NEW.outcome <> 'pending'
 AND (SELECT COUNT(*) FROM experiment_runs WHERE hypothesis_id = OLD.hypothesis_id) = 0
BEGIN
    SELECT RAISE(ABORT, 'no experiment run recorded: cannot judge before experiment');
END;

CREATE TRIGGER IF NOT EXISTS trg_hr_outcome_needs_basis
BEFORE UPDATE OF outcome ON hypothesis_registry
WHEN NEW.outcome <> 'pending'
 AND (NEW.verdict_basis IS NULL OR length(trim(NEW.verdict_basis)) < 20)
BEGIN
    SELECT RAISE(ABORT, 'verdict_basis required (>=20 chars) for any judgement');
END;

CREATE TRIGGER IF NOT EXISTS trg_hr_falsified_needs_postmortem
BEFORE UPDATE OF outcome ON hypothesis_registry
WHEN NEW.outcome = 'falsified'
 AND (NEW.postmortem_note IS NULL OR length(trim(NEW.postmortem_note)) < 40)
BEGIN
    SELECT RAISE(ABORT, 'falsified requires a postmortem_note (>=40 chars)');
END;

-- 4) 实验 run 只能挂在已登记、仍 pending、且不早于登记时刻的假设上
CREATE TRIGGER IF NOT EXISTS trg_run_requires_registration
BEFORE INSERT ON experiment_runs
WHEN NOT EXISTS (
    SELECT 1 FROM hypothesis_registry WHERE hypothesis_id = NEW.hypothesis_id
)
BEGIN
    SELECT RAISE(ABORT, 'unregistered hypothesis: log_run before register is refused');
END;

CREATE TRIGGER IF NOT EXISTS trg_run_hypothesis_pending
BEFORE INSERT ON experiment_runs
WHEN (SELECT outcome FROM hypothesis_registry WHERE hypothesis_id = NEW.hypothesis_id)
     <> 'pending'
BEGIN
    SELECT RAISE(ABORT, 'hypothesis already closed: cannot log further runs');
END;

CREATE TRIGGER IF NOT EXISTS trg_run_not_backdated
BEFORE INSERT ON experiment_runs
WHEN NEW.run_utc < (
    SELECT registered_utc FROM hypothesis_registry WHERE hypothesis_id = NEW.hypothesis_id
)
BEGIN
    SELECT RAISE(ABORT, 'run_utc precedes registration: backdated run refused');
END;

CREATE TRIGGER IF NOT EXISTS trg_run_no_update
BEFORE UPDATE ON experiment_runs
BEGIN
    SELECT RAISE(ABORT, 'experiment_runs is append-only (no UPDATE)');
END;

CREATE TRIGGER IF NOT EXISTS trg_run_no_delete
BEFORE DELETE ON experiment_runs
BEGIN
    SELECT RAISE(ABORT, 'experiment_runs is append-only (no DELETE)');
END;

-- 5) 事件链 append-only
CREATE TRIGGER IF NOT EXISTS trg_event_no_update
BEFORE UPDATE ON event_log
BEGIN
    SELECT RAISE(ABORT, 'event_log is append-only (no UPDATE)');
END;

CREATE TRIGGER IF NOT EXISTS trg_event_no_delete
BEFORE DELETE ON event_log
BEGIN
    SELECT RAISE(ABORT, 'event_log is append-only (no DELETE)');
END;
"""


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def connect(db_path: "str | os.PathLike", *, create: bool = True) -> sqlite3.Connection:
    """Open (and by default create-initialise) the registry database."""
    path = Path(db_path)
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables/triggers if absent (idempotent)."""
    conn.executescript(DDL)


def now_utc() -> str:
    """ISO-8601 UTC, second resolution, ``Z``-suffixed (lexicographically sortable)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no NaN/Inf, UTF-8 kept readable."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prediction_commitment(prediction: Any) -> str:
    """sha256 of the canonical prediction JSON: tamper evidence of the blind guess."""
    return sha256_text(canonical_json(prediction))


def _has_content(value: Any) -> bool:
    """A prediction must actually predict something (non-empty, no blank strings)."""
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_has_content(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_content(v) for v in value)
    return value is not None


def _slug(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return (slug or "h")[:limit].strip("-")


def _append_event(
    conn: sqlite3.Connection,
    kind: str,
    hypothesis_id: str,
    event_utc: str,
    payload: Any,
) -> str:
    payload_sha = sha256_text(canonical_json(payload))
    row = conn.execute("SELECT entry_hash FROM event_log ORDER BY seq DESC LIMIT 1").fetchone()
    prev_hash = row["entry_hash"] if row else ""
    entry_hash = sha256_text("|".join([prev_hash, kind, hypothesis_id, event_utc, payload_sha]))
    conn.execute(
        "INSERT INTO event_log (kind, hypothesis_id, event_utc, payload_sha, prev_hash, entry_hash)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (kind, hypothesis_id, event_utc, payload_sha, prev_hash, entry_hash),
    )
    return entry_hash


def _require_fields(fields: Mapping[str, str]) -> None:
    missing = [k for k, v in fields.items() if not str(v or "").strip()]
    if missing:
        raise RegistryError(f"register: missing required field(s) {missing}")


# --------------------------------------------------------------------------- #
# 登记
# --------------------------------------------------------------------------- #
def register(
    conn: sqlite3.Connection,
    *,
    mission_id: str,
    hypothesis_statement: str,
    theory_basis: str,
    falsification_experiment_design: str,
    prediction: Any,
    hypothesis_id: Optional[str] = None,
    slug: str = "",
    domain: str = "",
    registered_by: str = "",
    registered_utc: Optional[str] = None,
    evidence_at_registration: Sequence[str] = (),
    result_paths: Sequence["str | os.PathLike"] = (),
    blind_guards_extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Register one hypothesis **before** the experiment (blind prediction frozen).

    Machine gates (all raise :class:`RegistryError`, no partial write):

    * every narrative field non-empty; ``prediction`` must actually predict;
    * ``evidence_at_registration`` must be empty  → an experiment that already
      produced evidence cannot be registered retroactively;
    * each ``result_paths`` entry must be absent or empty → the declared result
      artefact must not exist yet (the machine fact behind the self-report);
    * ``hypothesis_id`` must be unique.

    Returns the stored row as a dict (``prediction`` decoded back).
    """
    _require_fields(
        {
            "mission_id": mission_id,
            "hypothesis_statement": hypothesis_statement,
            "theory_basis": theory_basis,
            "falsification_experiment_design": falsification_experiment_design,
        }
    )
    if not _has_content(prediction):
        raise RegistryError("register: prediction_before must be non-empty (a real prediction)")

    # --- gate 0: prediction 携带的判据必须当场可执行（否则冻结的是空话） ---
    if isinstance(prediction, Mapping) and "rule" in prediction:
        validate_rule(prediction["rule"])

    # --- gate A: 登记时不得已有证据（自述清单） -----------------------------
    prior = [str(e) for e in evidence_at_registration if str(e).strip()]
    if prior:
        raise RegistryError(
            "post-hoc registration refused: evidence already exists at registration time: "
            f"{prior[:5]}"
        )

    # --- gate B: 声明的结果路径必须尚不存在（机器事实，非自述） ------------
    preexisting = []
    for raw in result_paths:
        p = Path(raw)
        if p.exists() and p.stat().st_size > 0:
            preexisting.append(str(p))
    if preexisting:
        raise RegistryError(
            "post-hoc registration refused: declared result artefact(s) already hold results: "
            f"{preexisting[:5]}"
        )

    hid = hypothesis_id or f"{mission_id}-{_slug(slug or hypothesis_statement)}"
    registered_utc = registered_utc or now_utc()
    prediction_text = canonical_json(prediction)
    commitment = prediction_commitment(prediction)
    guards = {
        "evidence_at_registration": [],
        "result_paths": [str(p) for p in result_paths],
        "result_paths_absent": True,
        "commitment_algo": "sha256(canonical_json(prediction_before))",
        "schema_version": SCHEMA_VERSION,
    }
    if blind_guards_extra:
        guards.update(dict(blind_guards_extra))

    try:
        conn.execute(
            "INSERT INTO hypothesis_registry (hypothesis_id, mission_id, domain,"
            " hypothesis_statement, theory_basis, falsification_experiment_design,"
            " prediction_before, prediction_commitment, registered_utc, registered_by,"
            " evidence_at_registration, blind_guards, outcome, schema_version)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, 'pending', ?)",
            (
                hid,
                mission_id,
                domain,
                hypothesis_statement,
                theory_basis,
                falsification_experiment_design,
                prediction_text,
                commitment,
                registered_utc,
                registered_by,
                canonical_json(guards),
                SCHEMA_VERSION,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise RegistryError(f"register: {exc}") from exc

    _append_event(
        conn,
        "registered",
        hid,
        registered_utc,
        {"prediction_commitment": commitment, "mission_id": mission_id, "guards": guards},
    )
    return get(conn, hid)


# --------------------------------------------------------------------------- #
# 实验执行 / 结题
# --------------------------------------------------------------------------- #
def log_run(
    conn: sqlite3.Connection,
    hypothesis_id: str,
    *,
    run_ref: str,
    payload: Optional[Any] = None,
    run_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """Attach one experiment execution to a registered, still-pending hypothesis.

    ``run_utc`` is the **external** execution timestamp (log/artefact time, not
    "now"): the DB refuses anything earlier than ``registered_utc`` so a
    backdated run cannot be smuggled in.
    """
    if not str(run_ref).strip():
        raise RegistryError("log_run: run_ref required (what was executed)")
    row = conn.execute(
        "SELECT outcome, registered_utc FROM hypothesis_registry WHERE hypothesis_id = ?",
        (hypothesis_id,),
    ).fetchone()
    if row is None:
        raise RegistryError(f"log_run: hypothesis {hypothesis_id!r} is not registered")
    if row["outcome"] != "pending":
        raise RegistryError(f"log_run: {hypothesis_id!r} already closed ({row['outcome']})")
    run_utc = run_utc or now_utc()
    if run_utc < row["registered_utc"]:
        raise RegistryError(
            f"log_run: run_utc {run_utc} precedes registration {row['registered_utc']} "
            "(backdated run refused)"
        )
    seq_row = conn.execute(
        "SELECT COALESCE(MAX(run_seq), 0) AS m FROM experiment_runs WHERE hypothesis_id = ?",
        (hypothesis_id,),
    ).fetchone()
    seq = int(seq_row["m"]) + 1
    payload = {} if payload is None else payload
    payload_text = canonical_json(payload)
    try:
        conn.execute(
            "INSERT INTO experiment_runs (hypothesis_id, run_seq, run_ref, run_utc, payload, logged_utc)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (hypothesis_id, seq, str(run_ref), run_utc, payload_text, now_utc()),
        )
    except sqlite3.IntegrityError as exc:
        raise RegistryError(f"log_run: {exc}") from exc
    _append_event(
        conn,
        "run_logged",
        hypothesis_id,
        run_utc,
        {"run_ref": str(run_ref), "seq": seq, "payload_sha": sha256_text(payload_text)},
    )
    return {"hypothesis_id": hypothesis_id, "run_seq": seq, "run_utc": run_utc}


#: outcome → S-N 轨记账（falsified：权4，不计失败率，自动进失败知识库 A4）
SN_RULES: Dict[str, Dict[str, Any]] = {
    "confirmed": {
        "sn_track": "S-C",
        "sn_weight": 1.0,
        "failure_kb": 0,
        "counts_in_failure_rate": True,
        "note": "假设成立：常规成果轨",
    },
    "falsified": {
        "sn_track": "S-N",
        "sn_weight": 4.0,
        "failure_kb": 1,
        "counts_in_failure_rate": False,
        "note": "假设被证伪：按 bootstrap 权4 进 S-N 轨，不计失败率，进失败知识库(A4)",
    },
    "inconclusive": {
        "sn_track": "S-N",
        "sn_weight": 1.0,
        "failure_kb": 0,
        "counts_in_failure_rate": False,
        "note": "证据不足：S-N 轨权1，待补实验（既非成功也非失败）",
    },
}


def sn_ledger(outcome: str) -> Dict[str, Any]:
    """Bookkeeping for one judgement (bootstrap rule: weight 4 for falsified)."""
    if outcome not in SN_RULES:
        raise RegistryError(f"sn_ledger: unknown outcome {outcome!r}")
    return {"outcome": outcome, **SN_RULES[outcome]}


def close(
    conn: sqlite3.Connection,
    hypothesis_id: str,
    *,
    outcome: str,
    verdict_basis: str,
    postmortem_note: str = "",
    outcome_utc: Optional[str] = None,
    computed: Optional[Any] = None,
) -> Dict[str, Any]:
    """Judge a hypothesis: terminal, one-shot, evidence-required.

    * ``outcome`` must be terminal (``confirmed|falsified|inconclusive``);
    * at least one ``experiment_runs`` row must exist (no verdict without a run);
    * ``verdict_basis`` >= 20 chars — the frozen criterion plus the compared
      numbers, not a free-form "looks right";
    * ``falsified`` additionally requires a postmortem note >= 40 chars.
    """
    if outcome not in TERMINAL_OUTCOMES:
        raise RegistryError(f"close: outcome must be one of {TERMINAL_OUTCOMES}, got {outcome!r}")
    if len(str(verdict_basis or "").strip()) < MIN_VERDICT_BASIS_CHARS:
        raise RegistryError(
            f"close: verdict_basis must be >= {MIN_VERDICT_BASIS_CHARS} chars "
            "(state the frozen rule and the compared numbers)"
        )
    if outcome == "falsified" and len(str(postmortem_note or "").strip()) < MIN_POSTMORTEM_CHARS:
        raise RegistryError(
            f"close: falsified needs postmortem_note >= {MIN_POSTMORTEM_CHARS} chars "
            "(what broke, which assumption of the theory failed)"
        )
    row = conn.execute(
        "SELECT outcome, prediction_before FROM hypothesis_registry WHERE hypothesis_id = ?",
        (hypothesis_id,),
    ).fetchone()
    if row is None:
        raise RegistryError(f"close: hypothesis {hypothesis_id!r} is not registered")
    if row["outcome"] != "pending":
        raise RegistryError(f"close: {hypothesis_id!r} already closed as {row['outcome']}")
    ledger = sn_ledger(outcome)
    outcome_utc = outcome_utc or now_utc()
    try:
        conn.execute(
            "UPDATE hypothesis_registry SET outcome = ?, outcome_utc = ?, verdict_basis = ?,"
            " postmortem_note = ?, sn_track = ?, sn_weight = ?, failure_kb = ?"
            " WHERE hypothesis_id = ?",
            (
                outcome,
                outcome_utc,
                str(verdict_basis).strip(),
                (str(postmortem_note).strip() or None),
                ledger["sn_track"],
                ledger["sn_weight"],
                ledger["failure_kb"],
                hypothesis_id,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise RegistryError(f"close: {exc}") from exc
    _append_event(
        conn,
        "closed",
        hypothesis_id,
        outcome_utc,
        {
            "outcome": outcome,
            "verdict_basis_sha": sha256_text(str(verdict_basis).strip()),
            "prediction_commitment": prediction_commitment(json.loads(row["prediction_before"])),
            "computed": computed,
            "sn": ledger,
        },
    )
    return get(conn, hypothesis_id)


# --------------------------------------------------------------------------- #
# 读取 / 审计
# --------------------------------------------------------------------------- #
def _decode(row: sqlite3.Row) -> Dict[str, Any]:
    out = dict(row)
    if isinstance(out.get("prediction_before"), str):
        try:
            out["prediction"] = json.loads(out["prediction_before"])
        except json.JSONDecodeError:
            out["prediction"] = None
    if isinstance(out.get("evidence_at_registration"), str):
        out["evidence_at_registration"] = json.loads(out["evidence_at_registration"])
    if isinstance(out.get("blind_guards"), str):
        out["blind_guards"] = json.loads(out["blind_guards"])
    return out


def get(conn: sqlite3.Connection, hypothesis_id: str) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM hypothesis_registry WHERE hypothesis_id = ?", (hypothesis_id,)
    ).fetchone()
    if row is None:
        raise RegistryError(f"get: hypothesis {hypothesis_id!r} not found")
    out = _decode(row)
    try:
        recomputed = prediction_commitment(json.loads(row["prediction_before"]))
    except json.JSONDecodeError:
        recomputed = "invalid-json"
    out["prediction_commitment_recomputed"] = recomputed
    out["commitment_ok"] = recomputed == row["prediction_commitment"]
    out["runs"] = [
        dict(r)
        for r in conn.execute(
            "SELECT run_seq, run_ref, run_utc, payload, logged_utc FROM experiment_runs"
            " WHERE hypothesis_id = ? ORDER BY run_seq",
            (hypothesis_id,),
        )
    ]
    out["sn"] = sn_ledger(row["outcome"]) if row["outcome"] != "pending" else None
    return out


def list_hypotheses(
    conn: sqlite3.Connection, mission_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    if mission_id:
        rows = conn.execute(
            "SELECT hypothesis_id FROM hypothesis_registry WHERE mission_id = ?"
            " ORDER BY hypothesis_id",
            (mission_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT hypothesis_id FROM hypothesis_registry ORDER BY hypothesis_id"
        ).fetchall()
    return [get(conn, r["hypothesis_id"]) for r in rows]


def verify_integrity(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Full audit. Returns a machine-checkable report (``ok`` is the gate).

    Checks
    ------
    ``commitment``      每个假设的 prediction 承诺复算一致（防 DROP TRIGGER 后改预测）
    ``chain``           event_log 哈希链连续、prev_hash 串得上
    ``ordering``        每条 run 的 run_utc >= 该假设 registered_utc
    ``terminal``        非 pending 必须有 run、>=20 字裁决依据、falsified 有复盘
    ``outcome_domain``  outcome ∈ 取值域
    ``orphan_runs``     没有挂在不存在假设上的 run
    """
    failures: List[str] = []
    details: Dict[str, Any] = {}

    commit_bad: List[str] = []
    for row in conn.execute(
        "SELECT hypothesis_id, prediction_before, prediction_commitment FROM hypothesis_registry"
    ):
        try:
            recomputed = prediction_commitment(json.loads(row["prediction_before"]))
        except json.JSONDecodeError:
            commit_bad.append(f"{row['hypothesis_id']}:prediction_not_json")
            continue
        if recomputed != row["prediction_commitment"]:
            commit_bad.append(f"{row['hypothesis_id']}:commitment_mismatch")
    details["commitment"] = {"ok": not commit_bad, "bad": commit_bad}
    if commit_bad:
        failures.append(f"commitment:{commit_bad}")

    chain_bad: List[str] = []
    prev = ""
    for row in conn.execute("SELECT * FROM event_log ORDER BY seq"):
        expect = sha256_text(
            "|".join([prev, row["kind"], row["hypothesis_id"], row["event_utc"], row["payload_sha"]])
        )
        if row["prev_hash"] != prev or row["entry_hash"] != expect:
            chain_bad.append(f"seq={row['seq']} kind={row['kind']}")
        prev = row["entry_hash"]
    details["chain"] = {"ok": not chain_bad, "bad": chain_bad}
    if chain_bad:
        failures.append(f"chain_broken:{chain_bad}")

    order_bad: List[str] = []
    for row in conn.execute(
        "SELECT r.hypothesis_id, r.run_seq, r.run_utc, h.registered_utc"
        " FROM experiment_runs r JOIN hypothesis_registry h USING (hypothesis_id)"
    ):
        if row["run_utc"] < row["registered_utc"]:
            order_bad.append(f"{row['hypothesis_id']}#{row['run_seq']}")
    details["ordering"] = {"ok": not order_bad, "bad": order_bad}
    if order_bad:
        failures.append(f"ordering:{order_bad}")

    terminal_bad: List[str] = []
    for row in conn.execute("SELECT * FROM hypothesis_registry"):
        hid, outcome = row["hypothesis_id"], row["outcome"]
        if outcome == "pending":
            continue
        runs = conn.execute(
            "SELECT COUNT(*) AS n FROM experiment_runs WHERE hypothesis_id = ?", (hid,)
        ).fetchone()["n"]
        if runs == 0:
            terminal_bad.append(f"{hid}:no_run")
        if len(str(row["verdict_basis"] or "").strip()) < MIN_VERDICT_BASIS_CHARS:
            terminal_bad.append(f"{hid}:thin_basis")
        if outcome == "falsified" and len(
            str(row["postmortem_note"] or "").strip()
        ) < MIN_POSTMORTEM_CHARS:
            terminal_bad.append(f"{hid}:no_postmortem")
    details["terminal"] = {"ok": not terminal_bad, "bad": terminal_bad}
    if terminal_bad:
        failures.append(f"terminal:{terminal_bad}")

    domain_bad = [
        f"{r['hypothesis_id']}:{r['outcome']}"
        for r in conn.execute("SELECT hypothesis_id, outcome FROM hypothesis_registry")
        if r["outcome"] not in OUTCOMES
    ]
    details["outcome_domain"] = {"ok": not domain_bad, "bad": domain_bad}
    if domain_bad:
        failures.append(f"outcome_domain:{domain_bad}")

    orphan = [
        f"{r['hypothesis_id']}#{r['run_seq']}"
        for r in conn.execute(
            "SELECT hypothesis_id, run_seq FROM experiment_runs WHERE hypothesis_id NOT IN"
            " (SELECT hypothesis_id FROM hypothesis_registry)"
        )
    ]
    details["orphan_runs"] = {"ok": not orphan, "bad": orphan}
    if orphan:
        failures.append(f"orphan_runs:{orphan}")

    n_h = conn.execute("SELECT COUNT(*) AS n FROM hypothesis_registry").fetchone()["n"]
    n_e = conn.execute("SELECT COUNT(*) AS n FROM event_log").fetchone()["n"]
    scope = limitations()
    return {
        "ok": not failures,
        "failures": failures,
        "details": details,
        "n_hypotheses": int(n_h),
        "n_events": int(n_e),
        "schema_version": SCHEMA_VERSION,
        "blindness_basis": scope["enforced"],
        "residual_risk": scope["residual"],
    }


def limitations() -> Dict[str, Any]:
    """What the registry *can* and *cannot* prove (honest scope statement)."""
    return {
        "enforced": [
            "registered field set is immutable (SQLite trigger, bare SQL included)",
            "prediction commitment = sha256(canonical JSON), recomputed by verify (tamper evident)",
            "run cannot be logged before registration; run_utc >= registered_utc (no backdating)",
            "no verdict without a logged run; outcome is terminal and single-shot",
            "falsified requires a postmortem note; verdict basis must be substantive (>=20 chars)",
            "event_log hash chain makes re-ordering / history editing detectable",
            "registration refused when a declared result artefact already holds results",
        ],
        "residual": [
            "库内顺序可证，墙钟世界的真实性不可证：登记前若已在库外跑过实验，"
            "只能靠 result_paths 预声明 + 外部时间戳旁证逼近",
            "预测的解读仍由人/智能体裁决（verdict_basis 留痕，可事后审计）",
            "本地 SQLite 台账与线上 PG 实例之间需同步（本模块不负责跨库一致性）",
        ],
    }


def summary(conn: sqlite3.Connection, mission_id: Optional[str] = None) -> Dict[str, Any]:
    """Aggregate view: per-outcome counts, S-N ledger totals, acceptance numbers."""
    rows = list_hypotheses(conn, mission_id)
    by_outcome = {o: 0 for o in OUTCOMES}
    for r in rows:
        by_outcome[r["outcome"]] = by_outcome.get(r["outcome"], 0) + 1
    sn_track: Dict[str, Dict[str, float]] = {}
    failure_kb = 0
    for r in rows:
        if r["outcome"] == "pending":
            continue
        track = r["sn_track"] or sn_ledger(r["outcome"])["sn_track"]
        entry = sn_track.setdefault(track, {"count": 0, "weight": 0.0})
        entry["count"] += 1
        entry["weight"] += float(r["sn_weight"] or 0.0)
        failure_kb += int(r["failure_kb"] or 0)
    integrity = verify_integrity(conn)
    return {
        "mission_id": mission_id or "*",
        "n_hypotheses": len(rows),
        "by_outcome": by_outcome,
        "n_pending": by_outcome["pending"],
        "n_closed": len(rows) - by_outcome["pending"],
        "sn_track": sn_track,
        "failure_kb_entries": failure_kb,
        "falsified_not_counted_in_failure_rate": True,
        "integrity_ok": integrity["ok"],
        "integrity_failures": integrity["failures"],
        "blindness_basis": integrity["blindness_basis"],
        "residual_risk": integrity["residual_risk"],
        "schema_version": SCHEMA_VERSION,
        "hypotheses": [
            {
                "hypothesis_id": r["hypothesis_id"],
                "mission_id": r["mission_id"],
                "outcome": r["outcome"],
                "statement": r["hypothesis_statement"],
                "prediction": r["prediction"],
                "prediction_commitment": r["prediction_commitment"],
                "commitment_ok": r["commitment_ok"],
                "registered_utc": r["registered_utc"],
                "outcome_utc": r["outcome_utc"],
                "verdict_basis": r["verdict_basis"],
                "postmortem_note": r["postmortem_note"],
                "n_runs": len(r["runs"]),
                "sn": r["sn"],
            }
            for r in rows
        ],
    }


def render_markdown(summary_data: Mapping[str, Any]) -> str:
    """Human-readable table (numbers pasted programmatically, never hand-copied)."""
    lines = [
        f"# hypothesis_registry 台账摘要（mission `{summary_data['mission_id']}`）",
        "",
        f"- 假设数 **{summary_data['n_hypotheses']}**，已结题 **{summary_data['n_closed']}**，"
        f"pending **{summary_data['n_pending']}**",
        f"- outcome 分布：{summary_data['by_outcome']}",
        f"- S-N 记账：{summary_data['sn_track']}（falsified 权4、不计失败率；"
        f"失败知识库条目 {summary_data['failure_kb_entries']}）",
        "- 完整性审计：**"
        + ("PASS" if summary_data["integrity_ok"] else "FAIL")
        + "**"
        + (
            f"（{summary_data['integrity_failures']}）"
            if not summary_data["integrity_ok"]
            else ""
        ),
        "",
        "| hypothesis_id | outcome | n_runs | 承诺一致 | 登记(UTC) | 结题(UTC) |",
        "|---|---|---|---|---|---|",
    ]
    for h in summary_data["hypotheses"]:
        lines.append(
            f"| `{h['hypothesis_id']}` | {h['outcome']} | {h['n_runs']} | "
            f"{'ok' if h['commitment_ok'] else 'MISMATCH'} | {h['registered_utc']} | "
            f"{h['outcome_utc'] or '—'} |"
        )
    lines += ["", "## 盲预测（登记快照，不可改）", ""]
    for h in summary_data["hypotheses"]:
        lines += [
            f"### `{h['hypothesis_id']}` — {h['outcome']}",
            "",
            "```json",
            json.dumps(h["prediction"], ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            f"- commitment：`{h['prediction_commitment']}`",
            f"- 裁决依据：{h['verdict_basis'] or '（未结题）'}",
        ]
        if h["postmortem_note"]:
            lines.append(f"- 复盘：{h['postmortem_note']}")
        lines.append("")
    return "\n".join(lines)# --------------------------------------------------------------------------- #
# 冻结规则执行器（登记在 prediction 里的判据由机器执行，不靠结题时再商量）
# --------------------------------------------------------------------------- #
RULE_OPS = (">=", ">", "<=", "<", "==", "!=", "in_range")


def validate_rule(rule: Any) -> None:
    """Structural check for a frozen rule blob ``{"all": [check, ...]}``.

    Called at registration time when ``prediction`` carries a ``rule`` key: a
    criterion that cannot be executed cannot be frozen, so it is refused up
    front instead of being reinterpreted after the numbers are known.
    """
    if not isinstance(rule, Mapping) or not isinstance(rule.get("all"), (list, tuple)):
        raise RegistryError("validate_rule: rule must be {'all': [check, ...]}")
    if len(rule["all"]) == 0:
        raise RegistryError("validate_rule: rule.all is empty (nothing to decide)")
    for i, check in enumerate(rule["all"]):
        if not isinstance(check, Mapping):
            raise RegistryError(f"validate_rule: check {i} is not an object")
        if not str(check.get("metric", "")).strip():
            raise RegistryError(f"validate_rule: check {i} has no metric")
        op = check.get("op")
        if op not in RULE_OPS:
            raise RegistryError(f"validate_rule: check {i} op {op!r} not in {RULE_OPS}")
        if op == "in_range":
            rng = check.get("range")
            if not (isinstance(rng, (list, tuple)) and len(rng) == 2):
                raise RegistryError(f"validate_rule: check {i} in_range needs range=[lo,hi]")
            lo, hi = rng
            if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)) or lo > hi:
                raise RegistryError(f"validate_rule: check {i} range must be numeric lo<=hi")
        elif not isinstance(check.get("value"), (int, float)):
            raise RegistryError(f"validate_rule: check {i} needs numeric value")


def _check_one(check: Mapping[str, Any], measurements: Mapping[str, Any]) -> Dict[str, Any]:
    metric = str(check["metric"])
    op = check["op"]
    label = str(check.get("label", metric))
    if metric not in measurements:
        return {"metric": metric, "op": op, "label": label, "status": "missing",
                "measured": None, "expected": check.get("range", check.get("value"))}
    measured = measurements[metric]
    if op == "in_range":
        lo, hi = check["range"]
        passed = bool(lo <= measured <= hi)
        expected: Any = [lo, hi]
    else:
        expected = check["value"]
        passed = bool(
            measured >= expected if op == ">=" else
            measured > expected if op == ">" else
            measured <= expected if op == "<=" else
            measured < expected if op == "<" else
            measured == expected if op == "==" else
            measured != expected
        )
    return {
        "metric": metric,
        "op": op,
        "label": label,
        "measured": measured,
        "expected": expected,
        "status": "pass" if passed else "fail",
    }


def evaluate_rule(rule: Mapping[str, Any], measurements: Mapping[str, Any]) -> Dict[str, Any]:
    """Run the frozen rule against measured numbers.

    * all checks pass → ``confirmed``
    * any check fails → ``falsified``
    * any metric missing → ``inconclusive`` (证据不足，不当成通过也不当成失败)
    """
    validate_rule(rule)
    checks = [_check_one(c, measurements) for c in rule["all"]]
    missing = [c["metric"] for c in checks if c["status"] == "missing"]
    failed = [c["metric"] for c in checks if c["status"] == "fail"]
    if missing:
        verdict = "inconclusive"
    elif failed:
        verdict = "falsified"
    else:
        verdict = "confirmed"
    return {
        "verdict": verdict,
        "checks": checks,
        "missing_metrics": missing,
        "failed_metrics": failed,
        "rule_id": rule.get("rule_id", ""),
    }

# --------------------------------------------------------------------------- #
# PostgreSQL 部署 DDL（线上实例；语义与 SQLite 触发器一致）
# --------------------------------------------------------------------------- #
def pg_ddl() -> str:
    """Equivalent PostgreSQL DDL (CHECK constraints + same trigger semantics)."""
    return """-- hypothesis_registry (PostgreSQL 部署版, generated by tools/hypothesis_registry.py)
-- 与 SQLite DDL 语义一致：CHECK 约束 = 取值域门；触发器 = 不可改 / 时序 / 终态门。
CREATE TABLE IF NOT EXISTS hypothesis_registry (
    hypothesis_id                   TEXT PRIMARY KEY,
    mission_id                      TEXT NOT NULL,
    domain                          TEXT NOT NULL DEFAULT '',
    hypothesis_statement            TEXT NOT NULL CHECK (length(btrim(hypothesis_statement)) > 0),
    theory_basis                    TEXT NOT NULL CHECK (length(btrim(theory_basis)) > 0),
    falsification_experiment_design TEXT NOT NULL
        CHECK (length(btrim(falsification_experiment_design)) > 0),
    prediction_before               JSONB NOT NULL,
    prediction_commitment           TEXT NOT NULL CHECK (length(prediction_commitment) = 64),
    registered_utc                  TIMESTAMPTZ NOT NULL,
    registered_by                   TEXT NOT NULL DEFAULT '',
    evidence_at_registration        JSONB NOT NULL DEFAULT '[]'::jsonb,
    blind_guards                    JSONB NOT NULL DEFAULT '{}'::jsonb,
    outcome                         TEXT NOT NULL DEFAULT 'pending'
        CHECK (outcome IN ('pending','confirmed','falsified','inconclusive')),
    outcome_utc                     TIMESTAMPTZ,
    verdict_basis                   TEXT,
    postmortem_note                 TEXT,
    sn_track                        TEXT,
    sn_weight                       DOUBLE PRECISION,
    failure_kb                      BOOLEAN NOT NULL DEFAULT FALSE,
    schema_version                  TEXT NOT NULL DEFAULT '1.0.0'
);

CREATE TABLE IF NOT EXISTS experiment_runs (
    hypothesis_id TEXT NOT NULL REFERENCES hypothesis_registry (hypothesis_id),
    run_seq       INTEGER NOT NULL,
    run_ref       TEXT NOT NULL,
    run_utc       TIMESTAMPTZ NOT NULL,
    payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
    logged_utc    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (hypothesis_id, run_seq)
);

CREATE TABLE IF NOT EXISTS event_log (
    seq           BIGSERIAL PRIMARY KEY,
    kind          TEXT NOT NULL,
    hypothesis_id TEXT NOT NULL,
    event_utc     TIMESTAMPTZ NOT NULL,
    payload_sha   TEXT NOT NULL,
    prev_hash     TEXT NOT NULL,
    entry_hash    TEXT NOT NULL
);

CREATE OR REPLACE FUNCTION hr_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'registered hypothesis is immutable (prediction_before frozen)';
END; $$;

CREATE OR REPLACE FUNCTION hr_outcome_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.outcome <> 'pending' THEN
        RAISE EXCEPTION 'outcome is terminal: already closed (no re-judgement)';
    END IF;
    IF NEW.outcome <> 'pending' THEN
        IF (SELECT count(*) FROM experiment_runs WHERE hypothesis_id = OLD.hypothesis_id) = 0 THEN
            RAISE EXCEPTION 'no experiment run recorded: cannot judge before experiment';
        END IF;
        IF length(btrim(coalesce(NEW.verdict_basis, ''))) < 20 THEN
            RAISE EXCEPTION 'verdict_basis required (>=20 chars) for any judgement';
        END IF;
        IF NEW.outcome = 'falsified'
           AND length(btrim(coalesce(NEW.postmortem_note, ''))) < 40 THEN
            RAISE EXCEPTION 'falsified requires a postmortem_note (>=40 chars)';
        END IF;
    END IF;
    RETURN NEW;
END; $$;

CREATE OR REPLACE FUNCTION hr_run_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE st TEXT; reg TIMESTAMPTZ;
BEGIN
    SELECT outcome, registered_utc INTO st, reg FROM hypothesis_registry
      WHERE hypothesis_id = NEW.hypothesis_id;
    IF st IS NULL THEN
        RAISE EXCEPTION 'unregistered hypothesis: log_run before register is refused';
    END IF;
    IF st <> 'pending' THEN
        RAISE EXCEPTION 'hypothesis already closed: cannot log further runs';
    END IF;
    IF NEW.run_utc < reg THEN
        RAISE EXCEPTION 'run_utc precedes registration: backdated run refused';
    END IF;
    RETURN NEW;
END; $$;

DROP TRIGGER IF EXISTS trg_hr_immutable_core ON hypothesis_registry;
CREATE TRIGGER trg_hr_immutable_core BEFORE UPDATE ON hypothesis_registry
  FOR EACH ROW EXECUTE FUNCTION hr_immutable();
DROP TRIGGER IF EXISTS trg_hr_outcome_guard ON hypothesis_registry;
CREATE TRIGGER trg_hr_outcome_guard BEFORE UPDATE OF outcome ON hypothesis_registry
  FOR EACH ROW EXECUTE FUNCTION hr_outcome_guard();
DROP TRIGGER IF EXISTS trg_run_guard ON experiment_runs;
CREATE TRIGGER trg_run_guard BEFORE INSERT ON experiment_runs
  FOR EACH ROW EXECUTE FUNCTION hr_run_guard();
"""


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _load_json(path: "str | os.PathLike") -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="hypothesis_registry: 预注册假设台账（盲预测不可改）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="建表建触发器")
    p.add_argument("--db", required=True)

    p = sub.add_parser("register", help="登记假设（--file 为 JSON 列表或单对象）")
    p.add_argument("--db", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--by", default="")

    p = sub.add_parser("run", help="记录一次实验执行")
    p.add_argument("--db", required=True)
    p.add_argument("--hid", required=True)
    p.add_argument("--ref", required=True)
    p.add_argument("--payload-file", default=None)
    p.add_argument("--run-utc", default=None)

    p = sub.add_parser("close", help="结题判定")
    p.add_argument("--db", required=True)
    p.add_argument("--hid", required=True)
    p.add_argument("--outcome", required=True, choices=list(TERMINAL_OUTCOMES))
    p.add_argument("--basis-file", required=True)
    p.add_argument("--postmortem-file", default=None)
    p.add_argument("--outcome-utc", default=None)

    p = sub.add_parser("verify", help="完整性审计（失败则退出码 1）")
    p.add_argument("--db", required=True)

    p = sub.add_parser("report", help="台账报告 JSON + markdown + PG DDL")
    p.add_argument("--db", required=True)
    p.add_argument("--mission", default=None)
    p.add_argument("--out-json", required=True)
    p.add_argument("--out-md", default=None)
    p.add_argument("--out-pg-sql", default=None)

    p = sub.add_parser("sql", help="验收 SQL")
    p.add_argument("--db", required=True)
    p.add_argument("--mission", default=None)

    args = ap.parse_args(argv)
    conn = connect(args.db)

    if args.cmd == "init":
        init_db(conn)
        print(json.dumps({"ok": True, "db": str(args.db), "schema_version": SCHEMA_VERSION}))
        return 0

    if args.cmd == "register":
        init_db(conn)
        payload = _load_json(args.file)
        items = payload if isinstance(payload, list) else [payload]
        out = []
        for item in items:
            spec = dict(item)
            out.append(
                register(
                    conn,
                    mission_id=spec["mission_id"],
                    hypothesis_statement=spec["hypothesis_statement"],
                    theory_basis=spec["theory_basis"],
                    falsification_experiment_design=spec["falsification_experiment_design"],
                    prediction=spec["prediction_before"],
                    hypothesis_id=spec.get("hypothesis_id"),
                    slug=spec.get("slug", ""),
                    domain=spec.get("domain", ""),
                    registered_by=args.by or spec.get("registered_by", ""),
                    registered_utc=spec.get("registered_utc"),
                    evidence_at_registration=spec.get("evidence_at_registration", ()),
                    result_paths=spec.get("result_paths", ()),
                )
            )
        print(
            json.dumps(
                {
                    "ok": True,
                    "registered": [
                        {
                            "hypothesis_id": r["hypothesis_id"],
                            "prediction_commitment": r["prediction_commitment"],
                            "registered_utc": r["registered_utc"],
                        }
                        for r in out
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.cmd == "run":
        payload = _load_json(args.payload_file) if args.payload_file else {}
        out = log_run(conn, args.hid, run_ref=args.ref, payload=payload, run_utc=args.run_utc)
        print(json.dumps({"ok": True, **out}, ensure_ascii=False))
        return 0

    if args.cmd == "close":
        basis = Path(args.basis_file).read_text(encoding="utf-8")
        pm = (
            Path(args.postmortem_file).read_text(encoding="utf-8")
            if args.postmortem_file
            else ""
        )
        out = close(
            conn,
            args.hid,
            outcome=args.outcome,
            verdict_basis=basis,
            postmortem_note=pm,
            outcome_utc=args.outcome_utc,
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "hypothesis_id": out["hypothesis_id"],
                    "outcome": out["outcome"],
                    "sn": out["sn"],
                    "failure_kb": out["failure_kb"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.cmd == "verify":
        rep = verify_integrity(conn)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if rep["ok"] else 1

    if args.cmd == "report":
        init_db(conn)
        sum_data = summary(conn, args.mission)
        out_json = Path(args.out_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(sum_data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        if args.out_md:
            Path(args.out_md).write_text(render_markdown(sum_data), encoding="utf-8")
        pg_path = args.out_pg_sql or str(out_json.parent / "hypothesis_registry_pg.sql")
        Path(pg_path).write_text(pg_ddl(), encoding="utf-8")
        print(json.dumps({"ok": True, "out_json": str(out_json), "out_pg_sql": pg_path}, ensure_ascii=False))
        return 0

    if args.cmd == "sql":
        where = f"'{args.mission}'" if args.mission else "NULL"
        q = f"""
        SELECT hypothesis_id, mission_id, outcome, sn_track, sn_weight, failure_kb,
               (SELECT COUNT(*) FROM experiment_runs r WHERE r.hypothesis_id = h.hypothesis_id) AS n_runs,
               registered_utc, outcome_utc
          FROM hypothesis_registry h
         WHERE ({where} IS NULL OR mission_id = {where})
         ORDER BY hypothesis_id
        """
        for row in conn.execute(q):
            print(" | ".join(str(row[k]) for k in row.keys()))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())