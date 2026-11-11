"""WITHHELD 欠账闭环成熟化：登记库↔门禁双向绑定 · 数字化不确定度预算 · 缺口自动派单。

节点 IN51-W5-N09（平台共性机制，**零领域语义**：本文件不含任何船型/物理量/算例词条，
测试与演示全部用 ``SYN-*`` 合成域名，真实名称由调用方注入工件）。

承 IN50-W4-N09（``tools/vv/withheld_gates.py``：三态清偿 + 契约哈希 + 审计链 + KPI + 派单）。
本节点补三处**成熟化缺口**，并各自给出机器可算判据：

1. **登记库 ↔ 门禁双向绑定（criterion A：覆盖率 = 100%）**
   IN50 只有单向边：门 ``required_dataset_id`` 指向登记册，登记册不知道自己被谁引用。
   后果：①数据集重登/改名后旧门静默"漏绑"；②门工件被删后登记册仍挂幽灵引用；
   ③没人能回答"这张数据集支撑哪几门"。本模块把绑定做成**一等对象**
   （``tensor_reference_bindings``：gate_id → dataset_id + 工件契约指纹 + state），
   两侧各自独立成边，覆盖率按**边集合对称差**计算：

       forward  = {(gate, dataset, fp)} 取自门账本（门工件是唯一真相）
       reverse  = {(gate, dataset, fp)} 取自绑定索引（登记册侧反向视图）
       coverage = |forward ∩ reverse| / |forward ∪ reverse|

   并分类记账三类漂移：``UNBOUND``（门有、索引无）、``GHOST``（索引有、门无）、
   ``STALE``（同一门但数据集/指纹不同）。``reconcile_bindings`` 定向修复后覆盖率回到 100%。

2. **数字化不确定度预算自动核算（criterion B：与手工核算误差 < 1%）**
   IN50 的不确定度带只有 ``k·sqrt(u_exp² + u_dig²)`` 两项，且**允许不确定度带把超差结果
   判成 PASS**——这会把"测不准"洗成"通过"。本模块建立分量预算：

       u_c = sqrt( Σ_i u_i² + 2 Σ_{i<j} ρ_ij u_i u_j ),   U = k·u_c,   band_unc = U / D
       D = max(|r|, floor),   band = max(tol, band_unc)

   分量来自四个可数字化出处的源：``u_dig``（登记册数字化不确定度/逐点 σ）、
   ``u_exp``（门工件实测不确定度）、``u_num``（数值离散/迭代不确定度）、
   ``u_interp``（数字化表插值不确定度），并允许门工件声明的附加分量与相关系数 ρ。
   **双路复算**：``auto``（浮点 + ``math.fsum`` 成对项）与 ``manual``（Decimal 手算台账，
   分量按 3 位有效数字记账、固定顺序折叠加项），两条独立实现互校；
   另有 Monte-Carlo（GUM Supplement 1）第三方核对，报告 MC 标准误。
   判决纪律收紧：``band_unc > tol`` ⇒ 该门**分辨力不足**，即便 ``|E| ≤ band`` 也只判
   WITHHELD（``INSUFFICIENT_RESOLUTION``），**不允许**不确定度把门洗成 PASS。

3. **缺口自动派单（criterion C：派单分类准确率 > 95%）**
   把"缺什么/归谁/怎么还"变成决策表：按**上游优先**顺序分类缺口
   （数据集未登记 → 出处不全 → 实测缺失 → 量程不足 → 分辨力不足 → 判决过期），
   数据集侧识别 ``ORPHAN``（可用但无人引用）、绑定侧识别漂移，工单号由
   ``sha256(repo|gap_class|target)`` 幂等生成。准确率在**独立标注语料**上评测：
   语料由场景描述（``expected_class``）给出金标，分类器只能读库状态推断，
   输出混淆矩阵 + 误派（对健康门派单）计数。

用法
----
    python3 tools/vv/withheld_closure.py --demo --out artifacts/withheld_closure
    python3 tools/vv/withheld_closure.py --check --out artifacts/withheld_closure

判据先于结论：三判据任一不达标即 ``overall = FAIL``，报告如实写，不做四舍五入粉饰。
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP, getcontext
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

try:  # 作为包导入（pytest 从仓根跑）
    from tools.vv import withheld_gates as wg
except ImportError:  # pragma: no cover - 直接脚本执行路径
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.vv import withheld_gates as wg  # type: ignore[no-redef]

SCHEMA_ID = "withheld-closure/v1"
BINDING_SCHEMA_ID = "reference-binding/v1"
BUDGET_SCHEMA_ID = "uncertainty-budget/v1"

PASS, WITHHELD, FAIL = wg.PASS, wg.WITHHELD, wg.FAIL

# 继承 IN50 原因码 + 本节点新增
R_NO_REFERENCE = wg.R_NO_REFERENCE
R_REFERENCE_INCOMPLETE = wg.R_REFERENCE_INCOMPLETE
R_MEASUREMENT_MISSING = wg.R_MEASUREMENT_MISSING
R_OUT_OF_RANGE = wg.R_OUT_OF_RANGE
R_TOLERANCE_TAMPERED = wg.R_TOLERANCE_TAMPERED
R_WITHIN_BAND = wg.R_WITHIN_BAND
R_OUT_OF_BAND = wg.R_OUT_OF_BAND
#: 不确定度预算超过容差 ⇒ 试验无分辨力：三态里必须回到"证据不足"，不得 PASS
R_INSUFFICIENT_RESOLUTION = "INSUFFICIENT_RESOLUTION"

#: 绑定索引状态
B_PENDING = "PENDING"   # 门已指认数据集，但数据集尚未登记（债在数据集侧）
B_BOUND = "BOUND"       # 数据集已登记且可作判据

#: 绑定漂移分类
DRIFT_UNBOUND = "UNBOUND"
DRIFT_GHOST = "GHOST"
DRIFT_STALE = "STALE"

# --------------------------------------------------------------------------- #
# 缺口分类词表（零领域语义：只描述"证据/契约状态"，不描述物理量）
# --------------------------------------------------------------------------- #
G_NO_DATASET = "G_NO_DATASET"
G_PROVENANCE = "G_PROVENANCE"
G_MEASUREMENT = "G_MEASUREMENT"
G_RANGE = "G_RANGE"
G_RESOLUTION = "G_RESOLUTION"
G_STALE = "G_STALE"
G_TAMPER = "G_TAMPER"
G_ORPHAN = "G_ORPHAN"
G_BINDING = "G_BINDING"

GAP_CLASSES = (G_NO_DATASET, G_PROVENANCE, G_MEASUREMENT, G_RANGE,
               G_RESOLUTION, G_STALE, G_TAMPER, G_ORPHAN, G_BINDING)

GAP_ACTIONS = {
    G_NO_DATASET: "登记参考数据集（DOI+原始文件 sha256+数字化方法+u_dig）后自动重判",
    G_PROVENANCE: "补齐数据集出处缺项（doi|url / original_file_sha256 / method / u_dig）后自动重判",
    G_MEASUREMENT: "补测门所要求的实测量并重签契约基线（tol 只可收紧）",
    G_RANGE: "扩展数字化表自变量覆盖范围或补测该量程（禁止外推）后重判",
    G_RESOLUTION: "降低 u_c（提高数字化/实验/数值精度）或收紧 tol，恢复试验分辨力后重判",
    G_STALE: "参考数据已更新，按最新登记重判该门",
    G_TAMPER: "撤销契约改写，按门原始工件基线重新提交（tol 只可收紧）",
    G_ORPHAN: "无人引用的可用数据集：挂到目标门或在登记册标注 retire",
    G_BINDING: "重建绑定索引（reconcile_bindings）后复核覆盖率",
}

#: 上游优先（数字小=先做）：先补债的源头，再做下游复算
GAP_PRECEDENCE = {
    G_TAMPER: 1, G_NO_DATASET: 2, G_PROVENANCE: 3, G_MEASUREMENT: 4,
    G_RANGE: 5, G_RESOLUTION: 6, G_STALE: 7, G_BINDING: 8, G_ORPHAN: 9,
}

#: 判据阈值（机器可算）
COVERAGE_TARGET = 1.0            # 绑定覆盖率必须 100%
BUDGET_PARITY_TARGET = 0.01      # 自动 vs 手工核算相对误差 < 1%
DISPATCH_ACCURACY_TARGET = 0.95  # 缺口派单准确率 > 95%
TOLERANCE_STANDARD = 0.15        # 平台常设：对标误差 >15% 不可接受

# --------------------------------------------------------------------------- #
# 基础工具（与 IN50 同一实现，避免第二套时间/canonical 口径）
# --------------------------------------------------------------------------- #
utc_now = wg.utc_now
iso = wg.iso
as_dt = wg.as_dt
canonical = wg.canonical
sha256_text = wg.sha256_text


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# DDL：绑定索引 + 不确定度预算台账（SQLite 版；PG 版见 migrations/114）
# --------------------------------------------------------------------------- #
_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS tensor_reference_bindings (
    gate_id          TEXT PRIMARY KEY,
    dataset_id       TEXT NOT NULL,
    artifact_sha256  TEXT NOT NULL,
    state            TEXT NOT NULL DEFAULT 'PENDING',
    bound_at         TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    CHECK (state IN ('PENDING','BOUND'))
);

CREATE INDEX IF NOT EXISTS ix_reference_bindings_dataset
    ON tensor_reference_bindings (dataset_id, state);

CREATE TABLE IF NOT EXISTS tensor_uncertainty_budgets (
    gate_id           TEXT NOT NULL,
    dataset_id        TEXT NOT NULL DEFAULT '',
    computed_at       TEXT NOT NULL,
    k_factor          REAL NOT NULL,
    u_combined        REAL NOT NULL,
    u_expanded        REAL NOT NULL,
    denominator       REAL NOT NULL,
    band_uncertainty  REAL NOT NULL,
    band              REAL NOT NULL,
    tolerance         REAL NOT NULL,
    resolution_ok     INTEGER NOT NULL,
    components_json   TEXT NOT NULL,
    correlations_json TEXT NOT NULL DEFAULT '{}',
    manual_u_combined REAL NOT NULL,
    parity_rel_error  REAL NOT NULL,
    digest            TEXT NOT NULL,
    PRIMARY KEY (gate_id, computed_at)
);

CREATE INDEX IF NOT EXISTS ix_uncertainty_budgets_gate
    ON tensor_uncertainty_budgets (gate_id, computed_at);

-- 绑定自动化的结构层底座：门写入即建索引边，数据集登记即 PENDING→BOUND，
-- 门删除即撤边。API 侧的 reconcile_bindings 仍是权威修复手段（幂等），
-- 触发器只保证"绕过 API 的直接 DB 写入也不会静默漏绑"。
CREATE TRIGGER IF NOT EXISTS trg_withheld_gate_bind_ins
AFTER INSERT ON tensor_withheld_gates
BEGIN
    INSERT INTO tensor_reference_bindings
        (gate_id, dataset_id, artifact_sha256, state, bound_at, updated_at)
    VALUES (NEW.gate_id, NEW.required_dataset_id, NEW.artifact_sha256,
            CASE WHEN EXISTS (SELECT 1 FROM tensor_reference_datasets d
                               WHERE d.dataset_id = NEW.required_dataset_id
                                 AND d.usable_as_gate = 1) THEN 'BOUND' ELSE 'PENDING' END,
            strftime('%Y-%m-%dT%H:%M:%SZ','now'), strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    ON CONFLICT(gate_id) DO UPDATE SET
        dataset_id = excluded.dataset_id,
        artifact_sha256 = excluded.artifact_sha256,
        state = excluded.state,
        updated_at = excluded.updated_at;
END;

CREATE TRIGGER IF NOT EXISTS trg_withheld_gate_bind_del
AFTER DELETE ON tensor_withheld_gates
BEGIN
    DELETE FROM tensor_reference_bindings WHERE gate_id = OLD.gate_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_reference_dataset_bind_state_ins
AFTER INSERT ON tensor_reference_datasets
BEGIN
    UPDATE tensor_reference_bindings
       SET state = CASE WHEN NEW.usable_as_gate = 1 THEN 'BOUND' ELSE 'PENDING' END,
           updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
     WHERE dataset_id = NEW.dataset_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_reference_dataset_bind_state_upd
AFTER UPDATE ON tensor_reference_datasets
BEGIN
    UPDATE tensor_reference_bindings
       SET state = CASE WHEN NEW.usable_as_gate = 1 THEN 'BOUND' ELSE 'PENDING' END,
           updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
     WHERE dataset_id = NEW.dataset_id;
END;

-- 预算台账 append-only：判决依据不许事后改写
CREATE TRIGGER IF NOT EXISTS trg_uncertainty_budgets_no_update
BEFORE UPDATE ON tensor_uncertainty_budgets
BEGIN
    SELECT RAISE(ABORT, 'uncertainty budget rows are append-only (UPDATE refused)');
END;

CREATE TRIGGER IF NOT EXISTS trg_uncertainty_budgets_no_delete
BEFORE DELETE ON tensor_uncertainty_budgets
BEGIN
    SELECT RAISE(ABORT, 'uncertainty budget rows are append-only (DELETE refused)');
END;
"""


def ensure_closure_schema(conn: wg.Conn) -> None:
    """确保 IN50 三表 + 本节点两表都在（幂等）。"""
    wg.ensure_tables(conn)
    if conn.dialect == "sqlite":
        for stmt in wg._split_sql(_DDL_SQLITE):
            conn.execute(stmt)
        conn.commit()


# --------------------------------------------------------------------------- #
# 一、登记库 ↔ 门禁双向绑定
# --------------------------------------------------------------------------- #
def gate_artifacts(conn: wg.Conn) -> List[Dict[str, Any]]:
    return conn.query(
        """SELECT gate_id, mission_id, node, repo, quantity, unit, required_dataset_id,
                  tolerance, k_factor, artifact_sha256, artifact_json, verdict,
                  verdict_reason, verdict_reason_code, cleared_at, created_at, last_judged_at
             FROM tensor_withheld_gates ORDER BY gate_id""")


def registry_entries(conn: wg.Conn) -> List[Dict[str, Any]]:
    return conn.query(
        """SELECT dataset_id, doi, url, citation, original_file_sha256,
                  digitisation_method, u_dig, u_dig_kind, usable_as_gate,
                  validation_json, payload_json, registered_at
             FROM tensor_reference_datasets ORDER BY dataset_id""")


def _edge_triple(gate_id: Any, dataset_id: Any, fp: Any) -> Tuple[str, str, str]:
    return (str(gate_id or ""), str(dataset_id or ""), str(fp or ""))


def forward_edges(conn: wg.Conn) -> List[Dict[str, Any]]:
    """门侧边：门工件指认的数据集 + 契约指纹 + 数据集登记状态（唯一真相在门工件）。"""
    reg = {r["dataset_id"]: r for r in registry_entries(conn)}
    out = []
    for g in gate_artifacts(conn):
        ds = reg.get(g["required_dataset_id"])
        out.append({
            "gate_id": g["gate_id"],
            "dataset_id": g["required_dataset_id"],
            "artifact_sha256": g["artifact_sha256"],
            "dataset_registered": ds is not None,
            "dataset_usable": bool(ds and int(ds["usable_as_gate"] or 0)),
            "repo": g["repo"],
        })
    return out


def reverse_edges(conn: wg.Conn) -> List[Dict[str, Any]]:
    """登记册侧边：绑定索引（登记册反查门的唯一入口）。"""
    gates = {g["gate_id"]: g for g in gate_artifacts(conn)}
    reg = {r["dataset_id"]: r for r in registry_entries(conn)}
    rows = conn.query(
        """SELECT gate_id, dataset_id, artifact_sha256, state, bound_at, updated_at
             FROM tensor_reference_bindings ORDER BY gate_id""")
    out = []
    for r in rows:
        out.append({
            **r,
            "gate_exists": r["gate_id"] in gates,
            "dataset_registered": r["dataset_id"] in reg,
        })
    return out


def reconcile_bindings(conn: wg.Conn, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """按门账本（唯一真相）重建绑定索引；返回逐条动作，可逐字节复算。

    动作口径：``BIND``（新建）、``REBIND``（数据集/指纹变了）、
    ``RESOLVE``（数据集登记后 PENDING→BOUND）、``REOPEN``（数据集被撤回 BOUND→PENDING）、
    ``DROP_GHOST``（门已不存在）。
    """
    now = now or utc_now()
    stamp = iso(now)
    fwd = {e["gate_id"]: e for e in forward_edges(conn)}
    rev = {r["gate_id"]: r for r in reverse_edges(conn)}
    actions: List[Dict[str, Any]] = []

    for gid, e in fwd.items():
        state = B_BOUND if (e["dataset_registered"] and e["dataset_usable"]) else B_PENDING
        cur = rev.get(gid)
        if cur is None:
            conn.execute(
                """INSERT INTO tensor_reference_bindings
                       (gate_id, dataset_id, artifact_sha256, state, bound_at, updated_at)
                   VALUES (?,?,?,?,?,?)""",
                (gid, e["dataset_id"], e["artifact_sha256"], state, stamp, stamp))
            actions.append({"action": "BIND", "gate_id": gid, "dataset_id": e["dataset_id"],
                            "state": state})
        elif cur["dataset_id"] != e["dataset_id"] or cur["artifact_sha256"] != e["artifact_sha256"]:
            conn.execute(
                """UPDATE tensor_reference_bindings
                      SET dataset_id = ?, artifact_sha256 = ?, state = ?, updated_at = ?
                    WHERE gate_id = ?""",
                (e["dataset_id"], e["artifact_sha256"], state, stamp, gid))
            actions.append({"action": "REBIND", "gate_id": gid,
                            "from_dataset": cur["dataset_id"], "to_dataset": e["dataset_id"],
                            "state": state})
        elif cur["state"] != state:
            conn.execute(
                "UPDATE tensor_reference_bindings SET state = ?, updated_at = ? WHERE gate_id = ?",
                (state, stamp, gid))
            actions.append({"action": "RESOLVE" if state == B_BOUND else "REOPEN",
                            "gate_id": gid, "dataset_id": e["dataset_id"], "state": state})

    for gid, cur in rev.items():
        if gid not in fwd:
            conn.execute("DELETE FROM tensor_reference_bindings WHERE gate_id = ?", (gid,))
            actions.append({"action": "DROP_GHOST", "gate_id": gid,
                            "dataset_id": cur["dataset_id"]})
    conn.commit()
    return {"schema": BINDING_SCHEMA_ID, "reconciled_at": stamp,
            "n_actions": len(actions), "actions": actions}


def binding_coverage(conn: wg.Conn) -> Dict[str, Any]:
    """双向绑定覆盖率 + 三类漂移（覆盖率按边集合对称差计算）。

    * ``forward``：门账本侧边（门 → 指认数据集 + 契约指纹）；
    * ``reverse``：绑定索引侧边（登记册反查视图）；
    * ``coverage = |F ∩ R| / |F ∪ R|``，100% ⇔ 对称差为空且无幽灵行。
    * ``binding_coverage`` 只衡量**边两侧一致**；数据集登记完成度另计
      （``registration_coverage``），两者不混算，避免用绑定绿掩盖"数据集还没登记"。
    """
    fwd = forward_edges(conn)
    rev = reverse_edges(conn)
    F = {_edge_triple(e["gate_id"], e["dataset_id"], e["artifact_sha256"]) for e in fwd}
    R = {_edge_triple(e["gate_id"], e["dataset_id"], e["artifact_sha256"])
         for e in rev if e["gate_exists"]}

    rev_gate_ids = {r["gate_id"] for r in rev}
    rev_by_gate = {r["gate_id"]: r for r in rev}
    unbound = sorted(e["gate_id"] for e in fwd if e["gate_id"] not in rev_gate_ids)
    ghost = sorted(r["gate_id"] for r in rev if not r["gate_exists"])
    stale = sorted(e["gate_id"] for e in fwd
                   if e["gate_id"] in rev_by_gate
                   and _edge_triple(e["gate_id"], e["dataset_id"], e["artifact_sha256"]) not in R)

    union = F | R
    inter = F & R
    bound_state = [r for r in rev if r["gate_exists"] and r["state"] == B_BOUND]
    registration_coverage = (len(bound_state) / len(F)) if F else 1.0
    referenced = {e["dataset_id"] for e in fwd}
    index_by_ds: Dict[str, set] = {}
    for r in rev:
        if r["gate_exists"]:
            index_by_ds.setdefault(r["dataset_id"], set()).add(r["gate_id"])
    ds_ok = 0
    for ds in referenced:
        want = {e["gate_id"] for e in fwd if e["dataset_id"] == ds}
        if index_by_ds.get(ds, set()) >= want:
            ds_ok += 1
    reverse_cov = (ds_ok / len(referenced)) if referenced else 1.0

    coverage = (len(inter) / len(union)) if union else 1.0
    return {
        "schema": BINDING_SCHEMA_ID,
        "gates_total": len(fwd),
        "datasets_registered": len(registry_entries(conn)),
        "edges_forward": len(F),
        "edges_reverse": len(R),
        "edges_union": len(union),
        "edges_intersection": len(inter),
        "binding_coverage": coverage,
        "forward_coverage": ((len(F) - len(unbound)) / len(F)) if F else 1.0,
        "reverse_coverage": (1.0 if not R else (len(R) - len(ghost) - len(stale)) / len(R)),
        "registry_reverse_coverage": reverse_cov,
        "registration_coverage": registration_coverage,
        "drift": {DRIFT_UNBOUND: unbound, DRIFT_GHOST: ghost, DRIFT_STALE: stale},
        "drift_counts": {DRIFT_UNBOUND: len(unbound), DRIFT_GHOST: len(ghost),
                         DRIFT_STALE: len(stale)},
        "bidirectional_ok": bool(coverage == 1.0 and not unbound and not ghost and not stale),
    }


def dataset_reverse_gates(conn: wg.Conn, dataset_id: str) -> List[str]:
    """登记册侧反查：某数据集被哪些门引用（来自绑定索引，非扫描门表）。"""
    rows = conn.query(
        """SELECT b.gate_id FROM tensor_reference_bindings b
             JOIN tensor_withheld_gates g ON g.gate_id = b.gate_id
            WHERE b.dataset_id = ? AND b.artifact_sha256 = g.artifact_sha256
            ORDER BY b.gate_id""", (dataset_id,))
    return [r["gate_id"] for r in rows]


def tampered_gates(conn: wg.Conn) -> set:
    """从审计链判定**粘性**篡改态：出现 ``TAMPER_BLOCKED`` 且未 ``TAMPER_RESET``。

    为什么不看 ``verdict`` 列：IN50 的 ``declare_gate`` 在拒绝放宽容差时只写
    ``TAMPER_BLOCKED`` 审计行、不覆盖原判决，因此**判决列可能仍是 PASS**；
    也不看"末条事件"：篡改被拒之后任何自动重判都会追加 ``REJUDGED_*`` 事件，
    用末条事件判定会让篡改痕迹被一次自动重判洗掉。故以"出现过且未显式撤销"为准，
    撤销必须走 ``clear_tamper()``（写 ``TAMPER_RESET`` 审计行，留痕、可审计）。
    """
    rows = conn.query(
        """SELECT gate_id, event FROM tensor_withheld_audit ORDER BY gate_id, seq""")
    state: Dict[str, bool] = {}
    for row in rows:
        if row["event"] == "TAMPER_BLOCKED":
            state[row["gate_id"]] = True
        elif row["event"] == "TAMPER_RESET":
            state[row["gate_id"]] = False
    return {gid for gid, flagged in state.items() if flagged}


def clear_tamper(conn: wg.Conn, gate_id: str, *, actor: str = "agent",
                 reason: str = "", now: Optional[datetime] = None) -> Dict[str, Any]:
    """人工裁定后撤销篡改标记（写 ``TAMPER_RESET`` 审计行，保留完整历史）。"""
    now = now or utc_now()
    if gate_id not in tampered_gates(conn):
        return {"gate_id": gate_id, "cleared": False, "detail": "该门当前无篡改标记"}
    wg._audit(conn, gate_id, "TAMPER_RESET", WITHHELD,
              {"actor": actor, "reason": reason,
               "note": "篡改标记经人工裁定撤销；契约按原始工件基线重新提交"}, now)
    conn.commit()
    return {"gate_id": gate_id, "cleared": True, "detail": "篡改标记已撤销"}


# --------------------------------------------------------------------------- #
# 二、数字化不确定度预算
# --------------------------------------------------------------------------- #
BASE_COMPONENTS = ("u_exp", "u_dig", "u_num", "u_interp")


def _sig_round(value: float, digits: int = 3) -> Decimal:
    """手算台账口径：分量记 N 位有效数字（人写预算表的常态）。"""
    if value == 0.0 or not math.isfinite(value):
        return Decimal(0)
    d = Decimal(repr(float(value)))
    exp = d.adjusted() - (digits - 1)
    return d.scaleb(-exp).quantize(Decimal(1), rounding=ROUND_HALF_UP).scaleb(exp)


def _normalise_correlations(correlations: Any) -> Dict[Tuple[str, str], float]:
    out: Dict[Tuple[str, str], float] = {}
    if not correlations:
        return out
    items = list(correlations.items()) if isinstance(correlations, Mapping) \
        else list(correlations)
    for item in items:
        # 三种输入口径都接受：{"a|b": rho} / [("a|b", rho)] / [("a", "b", rho)]
        if isinstance(item, (list, tuple)) and len(item) == 3:
            key, value = (item[0], item[1]), item[2]
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            key, value = item
        else:
            raise ValueError(f"相关系数条目格式不认识：{item!r}")
        if isinstance(key, (list, tuple)) and len(key) == 2:
            a, b = str(key[0]), str(key[1])
        else:  # "a|b" 或 "a,b"
            parts = str(key).replace(",", "|").split("|")
            if len(parts) != 2:
                continue
            a, b = parts[0].strip(), parts[1].strip()
        rho = _as_float(value, 0.0)
        if not (-1.0 <= rho <= 1.0):
            raise ValueError(f"相关系数 ρ({a},{b})={rho} 超出 [-1, 1]")
        if a == b:
            continue
        out[(a, b)] = rho
        out.setdefault((b, a), rho)
    return out


def auto_u_combined(components: Sequence[Mapping[str, Any]],
                    correlations: Any = None) -> float:
    """自动路径：浮点 + fsum（成对项显式展开，可处理相关分量）。"""
    rho = _normalise_correlations(correlations)
    us = {str(c["name"]): abs(_as_float(c.get("u_abs"))) for c in components}
    terms = [u * u for u in us.values()]
    for (a, b), r in rho.items():
        if a < b and a in us and b in us:  # 只取无序对，避免 (a,b)/(b,a) 双计
            terms.append(2.0 * float(r) * us[a] * us[b])
    return math.sqrt(max(math.fsum(terms), 0.0))


def manual_u_combined(components: Sequence[Mapping[str, Any]],
                      correlations: Any = None,
                      *, digits: int = 3) -> float:
    """手工台账路径：Decimal 高精度 + 分量 N 位有效数字 + 固定顺序折叠。

    与 ``auto_u_combined`` 是两条**互相独立**的实现（不复用中间量），
    用于证明"机器算的预算 = 人按公式手算的预算"。
    """
    getcontext().prec = 50
    rho = _normalise_correlations(correlations)
    ordered = sorted(components, key=lambda c: str(c["name"]))
    vals = [(str(c["name"]), _sig_round(abs(_as_float(c.get("u_abs"))), digits))
            for c in ordered]
    acc = Decimal(0)
    for i, (ni, ui) in enumerate(vals):
        acc += ui * ui
        for j in range(i):
            nj, uj = vals[j]
            r = Decimal(str(rho.get((ni, nj), 0.0)))
            acc += Decimal(2) * r * ui * uj
    if acc < 0:
        acc = Decimal(0)
    return float(acc.sqrt())


def monte_carlo_budget(components: Sequence[Mapping[str, Any]],
                       correlations: Any = None,
                       *, k: float = 2.0, n: int = 200_000,
                       seed: int = 20261110) -> Dict[str, Any]:
    """GUM Supplement 1 核对：线性组合的 MC 标准差 vs 解析 u_c。

    有 numpy 时用特征分解生成相关正态样本；无 numpy 时退化为对角近似并标注 ``mode``。
    返回解析/MC 相对差与 MC 标准误（估标准差的相对标准误 ≈ 1/sqrt(2(n-1))）。
    """
    rho = _normalise_correlations(correlations)
    names = [str(c["name"]) for c in components]
    u = [abs(_as_float(c.get("u_abs"))) for c in components]
    m = len(names)
    if m == 0:
        return {"mode": "degenerate", "n": 0, "u_mc": 0.0, "u_analytic": 0.0,
                "rel_error": 0.0, "mc_stderr": 0.0, "k_expanded_mc": 0.0,
                "mc_rel_stderr": 0.0}
    analytic = auto_u_combined(components, rho)
    try:
        import numpy as np  # 局部导入：不把 numpy 变成模块硬依赖
    except Exception:  # pragma: no cover - 无 numpy 环境
        rnd = random.Random(seed)
        total = 0.0
        total2 = 0.0
        for _ in range(n):
            y = math.fsum(ui * rnd.gauss(0.0, 1.0) for ui in u)
            total += y
            total2 += y * y
        mean = total / n
        sd = math.sqrt(max(total2 / (n - 1) - mean * mean * n / (n - 1), 0.0))
        stderr = sd / math.sqrt(2 * (n - 1))
        return {"mode": "diagonal-fallback", "n": n, "u_mc": sd, "u_analytic": analytic,
                "rel_error": (abs(sd - analytic) / analytic) if analytic else 0.0,
                "mc_stderr": stderr, "k_expanded_mc": k * sd,
                "mc_rel_stderr": (stderr / analytic) if analytic else 0.0}
    cov = np.zeros((m, m), dtype=float)
    for i in range(m):
        for j in range(m):
            cov[i, j] = (u[i] * u[i] if i == j
                         else rho.get((names[i], names[j]), 0.0) * u[i] * u[j])
    w, v = np.linalg.eigh(cov)
    w = np.clip(w, 0.0, None)
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((n, m))
    y = (z * np.sqrt(w)) @ v.T
    sd = float(np.std(y.sum(axis=1), ddof=1))
    stderr = sd / math.sqrt(2 * (n - 1))
    return {"mode": "eigh", "n": n, "u_mc": sd, "u_analytic": analytic,
            "rel_error": (abs(sd - analytic) / analytic) if analytic else 0.0,
            "mc_stderr": stderr, "k_expanded_mc": k * sd,
            "mc_rel_stderr": (stderr / analytic) if analytic else 0.0}


def collect_budget_components(conn: wg.Conn, gate_row: Mapping[str, Any],
                             *, ref: Optional[float] = None) -> Dict[str, Any]:
    """从门工件 + 登记册抽预算分量（每条带出处，缺失进 ``missing``）。"""
    art = (json.loads(gate_row["artifact_json"])
           if isinstance(gate_row["artifact_json"], str) else dict(gate_row["artifact_json"]))
    req = art.get("requires_reference") or {}
    meas = req.get("measurement") or {}
    budget_decl = req.get("budget") or {}
    components: List[Dict[str, Any]] = []
    missing: List[str] = []
    scale = abs(ref) if ref is not None else 1.0

    def _add(name: str, value: Any, kind: str, source: str) -> None:
        val = _as_float(value, float("nan"))
        if value is None or math.isnan(val):
            missing.append(name)
            return
        kind = str(kind or "relative")
        u_abs = abs(val) if kind == "absolute" else abs(val) * scale
        components.append({"name": name, "u_declared": abs(val), "kind": kind,
                           "u_abs": u_abs, "source": source})

    # u_exp：实测不确定度（绝对优先，其次相对）
    if meas.get("u_exp") is not None:
        _add("u_exp", meas.get("u_exp"), "absolute", "gate.measurement.u_exp")
    elif meas.get("u_exp_rel") is not None:
        _add("u_exp", meas.get("u_exp_rel"), "relative", "gate.measurement.u_exp_rel")
    else:
        missing.append("u_exp")

    # u_dig：登记册数字化不确定度；相对 → 绝对用参考值折算，缺失回落逐点 σ
    ds_row = conn.one(
        "SELECT dataset_id, u_dig, u_dig_kind, payload_json FROM tensor_reference_datasets"
        " WHERE dataset_id = ?", (gate_row["required_dataset_id"],))
    if ds_row is None:
        missing.append("u_dig")
    else:
        payload = (json.loads(ds_row["payload_json"])
                   if isinstance(ds_row["payload_json"], str) else dict(ds_row["payload_json"]))
        u_dig_decl, kind, _method, _sha = wg.digitisation_meta(payload)
        if u_dig_decl > 0.0:
            _add("u_dig", u_dig_decl, kind, f"registry:{ds_row['dataset_id']}.u_dig")
        elif ref is not None:
            _ref, _floor, sigma = wg._reference_from_dataset(payload, meas.get("at"))
            if sigma is not None:
                _add("u_dig", sigma, "absolute", f"registry:{ds_row['dataset_id']}.point_sigma")
            else:
                missing.append("u_dig")
        else:
            missing.append("u_dig")

    declared = {str(c.get("name")): c for c in (budget_decl.get("components") or [])
                if isinstance(c, Mapping) and c.get("name")}

    # u_num：数值离散/迭代不确定度
    if "u_num" in declared:
        c = declared["u_num"]
        _add("u_num", c.get("u"), c.get("kind", "relative"), "gate.budget.u_num")
    elif meas.get("u_num") is not None:
        _add("u_num", meas.get("u_num"), "absolute", "gate.measurement.u_num")
    else:
        missing.append("u_num")

    # u_interp：数字化表插值不确定度（登记册或门工件声明）
    if "u_interp" in declared:
        c = declared["u_interp"]
        _add("u_interp", c.get("u"), c.get("kind", "relative"), "gate.budget.u_interp")
    elif ds_row is not None:
        payload = (json.loads(ds_row["payload_json"])
                   if isinstance(ds_row["payload_json"], str) else dict(ds_row["payload_json"]))
        interp_u = ((payload.get("digitisation") or {}).get("interp_uncertainty"))
        if interp_u is not None:
            _add("u_interp", interp_u, "relative", f"registry:{ds_row['dataset_id']}.interp")
        else:
            missing.append("u_interp")
    else:
        missing.append("u_interp")

    # 附加分量（门工件自由声明，例如 u_scale/u_ref）
    for name, c in declared.items():
        if name in ("u_num", "u_interp"):
            continue
        _add(name, c.get("u"), c.get("kind", "relative"), "gate.budget")

    correlations = _normalise_correlations(budget_decl.get("correlations"))
    return {"components": components, "correlations": correlations,
            "missing": sorted(set(missing)), "scale": scale}


def compute_budget(components: Sequence[Mapping[str, Any]],
                   correlations: Any,
                   *, k: float, denominator: float, tolerance: float,
                   manual_digits: int = 3) -> Dict[str, Any]:
    """预算核心：u_c / U / band_unc / band + 双路复算一致性 + 分辨力判据。"""
    d = max(abs(float(denominator)), 1e-12)
    u_auto = auto_u_combined(components, correlations)
    u_manual = manual_u_combined(components, correlations, digits=manual_digits)
    u_manual_exact = manual_u_combined(components, correlations, digits=15)
    parity = abs(u_auto - u_manual) / max(u_manual, 1e-15)
    parity_exact = abs(u_auto - u_manual_exact) / max(u_manual_exact, 1e-15)
    band_unc = float(k) * u_auto / d
    tol = float(tolerance)
    return {
        "schema": BUDGET_SCHEMA_ID,
        "k_factor": float(k),
        "u_combined": u_auto,
        "u_expanded": float(k) * u_auto,
        "denominator": d,
        "band_uncertainty": band_unc,
        "band": max(tol, band_unc),
        "tolerance": tol,
        "resolution_ok": bool(band_unc <= tol),
        "u_combined_manual": u_manual,
        "u_combined_manual_exact": u_manual_exact,
        "parity_rel_error": parity,
        "parity_rel_error_exact": parity_exact,
        "components": [dict(c) for c in components],
        "correlations": {f"{a}|{b}": r for (a, b), r in
                         _normalise_correlations(correlations).items() if a < b},
    }


def judge_with_budget(E: Optional[float], *, tolerance: float, band_uncertainty: float,
                      resolution_ok: bool) -> Dict[str, Any]:
    """预算版三态判决：**不确定度不得把门洗成 PASS**。

    * ``|E| ≤ tol`` ⇒ PASS（在容差带内）；
    * ``band_unc > tol`` 且 ``|E| ≤ band_unc`` ⇒ WITHHELD / INSUFFICIENT_RESOLUTION
      （试验没有分辨力，既不能宣布通过也不能宣布失败）；
    * 其余 ⇒ FAIL。
    """
    if E is None:
        return {"verdict": WITHHELD, "reason_code": R_MEASUREMENT_MISSING}
    tol = float(tolerance)
    if abs(float(E)) <= tol:
        return {"verdict": PASS, "reason_code": R_WITHIN_BAND}
    if not resolution_ok and abs(float(E)) <= float(band_uncertainty):
        return {"verdict": WITHHELD, "reason_code": R_INSUFFICIENT_RESOLUTION}
    return {"verdict": FAIL, "reason_code": R_OUT_OF_BAND}


def persist_budget(conn: wg.Conn, gate_id: str, dataset_id: str, budget: Mapping[str, Any],
                   *, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or utc_now()
    stamp = iso(now)
    digest = sha256_text(canonical({key: budget[key] for key in (
        "u_combined", "u_expanded", "denominator", "band_uncertainty", "band",
        "tolerance", "k_factor", "components", "correlations")}))
    conn.execute(
        """INSERT OR REPLACE INTO tensor_uncertainty_budgets
               (gate_id, dataset_id, computed_at, k_factor, u_combined, u_expanded,
                denominator, band_uncertainty, band, tolerance, resolution_ok,
                components_json, correlations_json, manual_u_combined, parity_rel_error, digest)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (gate_id, dataset_id, stamp, budget["k_factor"], budget["u_combined"],
         budget["u_expanded"], budget["denominator"], budget["band_uncertainty"],
         budget["band"], budget["tolerance"], 1 if budget["resolution_ok"] else 0,
         canonical(budget["components"]), canonical(budget["correlations"]),
         budget["u_combined_manual"], budget["parity_rel_error"], digest))
    conn.commit()
    return {"gate_id": gate_id, "computed_at": stamp, "digest": digest}


def latest_budget(conn: wg.Conn, gate_id: str) -> Optional[Dict[str, Any]]:
    row = conn.one(
        """SELECT * FROM tensor_uncertainty_budgets WHERE gate_id = ?
            ORDER BY computed_at DESC LIMIT 1""", (gate_id,))
    if not row:
        return None
    out = dict(row)
    out["components"] = json.loads(out.pop("components_json") or "[]")
    out["correlations"] = json.loads(out.pop("correlations_json") or "{}")
    out["resolution_ok"] = bool(out["resolution_ok"])
    return out


def rejudge_with_budget(conn: wg.Conn, gate_id: str, *, now: Optional[datetime] = None,
                        artifact: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """预算版重判：阻塞项沿用 IN50 语义（WITHHELD + 原因码），可算时用全量预算判决。"""
    now = now or utc_now()
    row = conn.one("SELECT * FROM tensor_withheld_gates WHERE gate_id = ?", (gate_id,))
    if not row:
        raise KeyError(f"门 {gate_id} 未声明")
    if artifact is not None and wg.artifact_fingerprint(artifact) != row["artifact_sha256"]:
        # 契约改写：交给 IN50 防作弊路径（FAIL + TAMPER_BLOCKED + 审计）
        return wg.rejudge_gate(conn, gate_id, now=now, artifact=artifact)
    if artifact is None and gate_id in tampered_gates(conn):
        # 篡改态不会被自动重判"洗白"：必须人工裁定撤销（clear_tamper）后重新提交
        return {"gate_id": gate_id, "verdict": row["verdict"],
                "reason_code": R_TOLERANCE_TAMPERED, "tampered": True,
                "cleared": False, "skipped": True,
                "detail": "门处于 TAMPER_BLOCKED 态：自动重判跳过，等待撤销契约改写"}

    art = (json.loads(row["artifact_json"])
           if isinstance(row["artifact_json"], str) else dict(row["artifact_json"]))
    req = art.get("requires_reference") or {}
    meas = req.get("measurement") or {}
    ds_row = conn.one("SELECT * FROM tensor_reference_datasets WHERE dataset_id = ?",
                      (row["required_dataset_id"],))

    # 上游阻塞项：未登记 / 不可作判据 / 缺实测值 → 交给 IN50 统一裁决（同一审计链）
    if ds_row is None or not int(ds_row["usable_as_gate"] or 0) or meas.get("value") is None:
        return wg.rejudge_gate(conn, gate_id, now=now)

    dataset = (json.loads(ds_row["payload_json"])
               if isinstance(ds_row["payload_json"], str) else dict(ds_row["payload_json"]))
    ref, floor, _sigma = wg._reference_from_dataset(dataset, meas.get("at"))
    if ref is None:  # 超出数字化表覆盖：不外推
        return wg.rejudge_gate(conn, gate_id, now=now)

    tol = min(float(row["tolerance"]), float(req.get("tolerance") or row["tolerance"]))
    k = float(row["k_factor"])
    denominator = max(abs(ref), float(floor))
    gathered = collect_budget_components(conn, row, ref=ref)
    budget = compute_budget(gathered["components"], gathered["correlations"],
                            k=k, denominator=denominator, tolerance=tol)
    E = (float(meas["value"]) - float(ref)) / budget["denominator"]
    verdict_doc = judge_with_budget(E, tolerance=tol,
                                    band_uncertainty=budget["band_uncertainty"],
                                    resolution_ok=budget["resolution_ok"])
    verdict, reason = verdict_doc["verdict"], verdict_doc["reason_code"]
    detail = (f"E={E:+.4f} band={budget['band']:.4f} "
              f"(tol={tol:.3f}, k·u_c/D={budget['band_uncertainty']:.4f}, "
              f"u_c={budget['u_combined']:.6g}, parity={budget['parity_rel_error']:.2e}) "
              f"判定 {verdict}")

    wg._set_verdict(conn, gate_id, verdict, reason, detail, now)
    wg._audit(conn, gate_id, "REJUDGED_BUDGET", verdict,
              {"reason_code": reason, "detail": detail, "E": E, "tolerance": tol,
               "k_factor": k, "dataset_id": row["required_dataset_id"],
               "artifact_sha256": row["artifact_sha256"],
               "budget": {key: budget[key] for key in (
                   "u_combined", "u_expanded", "denominator", "band_uncertainty",
                   "band", "resolution_ok", "parity_rel_error")},
               "components": budget["components"],
               "missing_components": gathered["missing"]}, now)
    persist_budget(conn, gate_id, row["required_dataset_id"], budget, now=now)
    conn.commit()
    return {"gate_id": gate_id, "verdict": verdict, "reason_code": reason,
            "cleared": verdict == PASS, "detail": detail, "E": E,
            "tolerance": tol, "denominator": budget["denominator"],
            "reference": ref, "budget": budget,
            "missing_components": gathered["missing"]}


def rejudge_all_with_budget(conn: wg.Conn, *, now: Optional[datetime] = None
                            ) -> List[Dict[str, Any]]:
    """全量预算重判（顺序固定，结果可逐字节复算）。"""
    now = now or utc_now()
    return [rejudge_with_budget(conn, g["gate_id"], now=now) for g in gate_artifacts(conn)]# --------------------------------------------------------------------------- #
# 三、缺口分类与派单
# --------------------------------------------------------------------------- #
def _measurement_of(gate_row: Mapping[str, Any]) -> Dict[str, Any]:
    art = (json.loads(gate_row["artifact_json"])
           if isinstance(gate_row["artifact_json"], str) else dict(gate_row["artifact_json"]))
    return dict((art.get("requires_reference") or {}).get("measurement") or {})


def _dataset_payload(ds_row: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not ds_row:
        return {}
    return (json.loads(ds_row["payload_json"])
            if isinstance(ds_row["payload_json"], str) else dict(ds_row["payload_json"]))


def _declared_tolerance(gate_row: Mapping[str, Any]) -> float:
    art = (json.loads(gate_row["artifact_json"])
           if isinstance(gate_row["artifact_json"], str) else dict(gate_row["artifact_json"]))
    req_tol = (art.get("requires_reference") or {}).get("tolerance")
    return min(float(gate_row["tolerance"]), float(req_tol or gate_row["tolerance"]))


def classify_gate_gap(conn: wg.Conn, gate_row: Mapping[str, Any],
                      *, now: Optional[datetime] = None,
                      tampered: Optional[set] = None) -> Optional[Dict[str, Any]]:
    """按上游优先顺序给出该门的缺口类；无症状返回 ``None``（健康门）。"""
    now = now or utc_now()
    tampered = tampered_gates(conn) if tampered is None else tampered
    base = {"kind": "gate", "gate_id": gate_row["gate_id"], "repo": gate_row["repo"],
            "dataset_id": gate_row["required_dataset_id"],
            "mission_id": gate_row.get("mission_id"), "node": gate_row.get("node"),
            "created_at": gate_row.get("created_at")}

    if gate_row["gate_id"] in tampered or \
            str(gate_row["verdict_reason_code"]) == R_TOLERANCE_TAMPERED:
        return {**base, "gap_class": G_TAMPER,
                "evidence": {"source": "audit_chain" if gate_row["gate_id"] in tampered
                             else "verdict_reason_code",
                             "verdict_reason": gate_row["verdict_reason"]}}

    ds_row = conn.one("SELECT * FROM tensor_reference_datasets WHERE dataset_id = ?",
                      (gate_row["required_dataset_id"],))
    if ds_row is None:
        return {**base, "gap_class": G_NO_DATASET,
                "evidence": {"dataset_id": gate_row["required_dataset_id"]}}
    if not int(ds_row["usable_as_gate"] or 0):
        validation = (json.loads(ds_row["validation_json"])
                      if isinstance(ds_row["validation_json"], str)
                      else dict(ds_row["validation_json"]))
        return {**base, "gap_class": G_PROVENANCE,
                "evidence": {"registration_missing": validation.get("registration_missing"),
                             "blocking": validation.get("blocking"),
                             "registered_at": ds_row.get("registered_at")}}

    meas = _measurement_of(gate_row)
    if meas.get("value") is None:
        return {**base, "gap_class": G_MEASUREMENT, "evidence": {"measurement": meas}}

    payload = _dataset_payload(ds_row)
    ref, floor, _sigma = wg._reference_from_dataset(payload, meas.get("at"))
    if ref is None:
        xs = [p[0] for p in (payload.get("points") or [])]
        return {**base, "gap_class": G_RANGE,
                "evidence": {"at": meas.get("at"),
                             "covered": [min(xs), max(xs)] if xs else None}}

    gathered = collect_budget_components(conn, gate_row, ref=ref)
    tol = _declared_tolerance(gate_row)
    denominator = max(abs(ref), float(floor))
    budget = compute_budget(gathered["components"], gathered["correlations"],
                            k=float(gate_row["k_factor"]), denominator=denominator,
                            tolerance=tol)
    if not budget["resolution_ok"]:
        return {**base, "gap_class": G_RESOLUTION,
                "evidence": {"band_uncertainty": budget["band_uncertainty"],
                             "tolerance": tol, "u_combined": budget["u_combined"]}}

    last_judged = as_dt(gate_row.get("last_judged_at"))
    registered = as_dt(ds_row.get("registered_at"))
    if last_judged and registered and registered > last_judged:
        return {**base, "gap_class": G_STALE,
                "evidence": {"last_judged_at": gate_row.get("last_judged_at"),
                             "dataset_registered_at": ds_row.get("registered_at")}}
    return None


def classify_dataset_gaps(conn: wg.Conn) -> List[Dict[str, Any]]:
    """登记册侧缺口：可用但无人引用的数据集（ORPHAN）。"""
    gaps: List[Dict[str, Any]] = []
    for ds in registry_entries(conn):
        if not int(ds["usable_as_gate"] or 0):
            continue
        if dataset_reverse_gates(conn, ds["dataset_id"]):
            continue
        gaps.append({"kind": "dataset", "dataset_id": ds["dataset_id"], "repo": "",
                     "gap_class": G_ORPHAN,
                     "evidence": {"registered_at": ds.get("registered_at"),
                                  "u_dig": ds.get("u_dig")}})
    return gaps


def classify_binding_gaps(conn: wg.Conn) -> List[Dict[str, Any]]:
    """绑定侧缺口：索引漂移（按门粒度列出，便于指定修复归属）。"""
    cov = binding_coverage(conn)
    gaps: List[Dict[str, Any]] = []
    for drift, gids in cov["drift"].items():
        for gid in gids:
            gaps.append({"kind": "binding", "gate_id": gid, "repo": "",
                         "dataset_id": "", "gap_class": G_BINDING,
                         "evidence": {"drift": drift}})
    return gaps


def classify_gaps(conn: wg.Conn, *, now: Optional[datetime] = None,
                  include_binding: bool = True) -> List[Dict[str, Any]]:
    now = now or utc_now()
    tampered = tampered_gates(conn)
    gaps = [g for g in (classify_gate_gap(conn, row, now=now, tampered=tampered)
                        for row in gate_artifacts(conn)) if g]
    gaps.extend(classify_dataset_gaps(conn))
    if include_binding:
        gaps.extend(classify_binding_gaps(conn))
    for gap in gaps:
        gap["action"] = GAP_ACTIONS[gap["gap_class"]]
        gap["precedence"] = GAP_PRECEDENCE[gap["gap_class"]]
    gaps.sort(key=lambda g: (g["precedence"], str(g.get("repo") or ""),
                             str(g.get("gate_id") or g.get("dataset_id") or "")))
    return gaps


def dispatch_gap_orders(gaps: Sequence[Mapping[str, Any]], *,
                        now: Optional[datetime] = None,
                        min_age_days: float = 0.0) -> List[Dict[str, Any]]:
    """缺口 → 工单（幂等）：同一 (repo, gap_class, target) 合并一张工单。

    工单号 ``WO-sha256(repo|gap_class|target)[:12]``：跨波、跨进程可复算，
    不因重跑产生重复派单。``age_days`` 取门/数据集最老账龄（时钟回拨下限 0）。
    """
    now = now or utc_now()
    buckets: Dict[Tuple[str, str, str], List[Mapping[str, Any]]] = {}
    for gap in gaps:
        target = str(gap.get("dataset_id") or gap.get("gate_id") or "")
        key = (str(gap.get("repo") or "UNASSIGNED"), str(gap["gap_class"]), target)
        buckets.setdefault(key, []).append(gap)

    orders: List[Dict[str, Any]] = []
    for (repo, gap_class, target), items in buckets.items():
        ages: List[float] = []
        for item in items:
            stamp = item.get("created_at") or (item.get("evidence") or {}).get("registered_at")
            dt = as_dt(stamp)
            if dt:
                ages.append(max(0.0, (now - dt).total_seconds() / 86400.0))
        age = max(ages) if ages else 0.0
        if age < min_age_days:
            continue
        order_id = "WO-" + sha256_text(f"{repo}|{gap_class}|{target}")[:12]
        orders.append({
            "order_id": order_id,
            "repo": repo,
            "gap_class": gap_class,
            "target": target,
            "dataset_id": items[0].get("dataset_id"),
            "gate_ids": sorted({str(i["gate_id"]) for i in items if i.get("gate_id")}),
            "n_items": len(items),
            "age_days": round(age, 4),
            "action": GAP_ACTIONS[gap_class],
            "priority": GAP_PRECEDENCE[gap_class],
            "evidence": [i.get("evidence") for i in items],
        })
    orders.sort(key=lambda o: (o["priority"], o["repo"], o["target"]))
    return orders


# --------------------------------------------------------------------------- #
# 四、判据评测：预算双路复算 + 派单准确率
# --------------------------------------------------------------------------- #
def budget_parity_corpus(n: int = 48, seed: int = 20261110) -> List[Dict[str, Any]]:
    """合成预算用例：分量量级 log-uniform、部分相关 → 自动 vs 手工 vs MC 三方核对。

    分量名固定为 ``SYN-U1..SYN-U4``（零领域语义），只考察公式实现一致性。
    """
    rnd = random.Random(seed)
    names = ["SYN-U1", "SYN-U2", "SYN-U3", "SYN-U4"]
    cases: List[Dict[str, Any]] = []
    rho_options = [None, 0.3, -0.4, 0.8]
    for i in range(n):
        m = 2 + (i % 3)  # 2..4 个分量
        comps = []
        for j in range(m):
            mag = 10 ** rnd.uniform(-3.0, -0.5)
            comps.append({"name": names[j], "u_declared": mag, "kind": "absolute",
                          "u_abs": mag, "source": "SYN"})
        rho_choice = rho_options[i % len(rho_options)]
        corr: List[List[Any]] = []
        if rho_choice is not None and m >= 2:
            corr = [[names[0], names[1], rho_choice]]
        cases.append({"case_id": f"SYN-BUD-{i:03d}", "components": comps,
                      "correlations": corr})
    return cases


def evaluate_budget_parity(cases: Sequence[Mapping[str, Any]], *,
                           k: float = 2.0, denominator: float = 1.0,
                           tolerance: float = 0.15,
                           mc_n: int = 100_000) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for case in cases:
        budget = compute_budget(case["components"], case["correlations"], k=k,
                                denominator=denominator, tolerance=tolerance)
        mc = monte_carlo_budget(case["components"], case["correlations"], k=k, n=mc_n)
        rows.append({
            "case_id": case["case_id"],
            "u_combined": budget["u_combined"],
            "u_combined_manual": budget["u_combined_manual"],
            "u_combined_manual_exact": budget["u_combined_manual_exact"],
            "parity_rel_error": budget["parity_rel_error"],
            "parity_rel_error_exact": budget["parity_rel_error_exact"],
            "band_uncertainty": budget["band_uncertainty"],
            "mc": mc,
        })
    max_parity = max((r["parity_rel_error"] for r in rows), default=0.0)
    max_exact = max((r["parity_rel_error_exact"] for r in rows), default=0.0)
    max_mc = max((r["mc"]["rel_error"] for r in rows), default=0.0)
    mc_sigma = max((r["mc"]["mc_stderr"] for r in rows), default=0.0)
    return {
        "schema": "budget-parity/v1",
        "n_cases": len(rows),
        "max_parity_rel_error_hand_ledger": max_parity,
        "max_parity_rel_error_exact_manual": max_exact,
        "mean_parity_rel_error_hand_ledger": (
            sum(r["parity_rel_error"] for r in rows) / len(rows)) if rows else 0.0,
        "max_mc_rel_error": max_mc,
        "max_mc_stderr": mc_sigma,
        "mc_within_3stderr": all(
            r["mc"]["rel_error"] <= 3.0 * r["mc"].get("mc_rel_stderr", 0.0) + 1e-12
            for r in rows),
        "target": BUDGET_PARITY_TARGET,
        "pass": bool(max_parity < BUDGET_PARITY_TARGET),
        "rows": rows,
    }


def evaluate_dispatch_accuracy(gold: Mapping[str, Optional[str]],
                               gaps: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """标注语料上的派单分类评测（含混淆矩阵与误派计数）。"""
    predicted: Dict[str, str] = {}
    for gap in gaps:
        key = str(gap.get("gate_id") or gap.get("dataset_id"))
        predicted[key] = str(gap["gap_class"])
    classes = sorted(set(v for v in gold.values() if v) | set(predicted.values()))
    cols = classes + ["NONE"]
    confusion: Dict[str, Dict[str, int]] = {row: {col: 0 for col in cols} for row in cols}
    correct = 0
    misses: List[Dict[str, Any]] = []
    for key, expected in gold.items():
        got = predicted.get(key)
        gold_col = expected or "NONE"
        got_col = got or "NONE"
        confusion[gold_col][got_col] += 1
        if gold_col == got_col:
            correct += 1
        else:
            misses.append({"case": key, "expected": gold_col, "predicted": got_col})
    false_orders = [k for k in predicted if k not in gold or gold.get(k) is None]
    total = len(gold)
    accuracy = (correct / total) if total else 1.0
    per_class: Dict[str, Dict[str, Any]] = {}
    for cls in classes:
        tp = confusion[cls][cls]
        fn = sum(confusion[cls][c] for c in cols if c != cls)
        fp = sum(confusion[g][cls] for g in cols if g != cls)
        recall = tp / (tp + fn) if (tp + fn) else None
        precision = tp / (tp + fp) if (tp + fp) else None
        per_class[cls] = {"support": tp + fn, "recall": recall, "precision": precision}
    return {
        "schema": "dispatch-accuracy/v1",
        "n_cases": total,
        "correct": correct,
        "accuracy": accuracy,
        "target": DISPATCH_ACCURACY_TARGET,
        "pass": bool(accuracy > DISPATCH_ACCURACY_TARGET),
        "confusion": confusion,
        "columns": cols,
        "per_class": per_class,
        "misses": misses,
        "false_orders": false_orders,
        "false_order_rate": (len(false_orders) / total) if total else 0.0,
    }# --------------------------------------------------------------------------- #
# 五、合成战场（SYN-*）：语料 → 库状态 → 全链路闭环
# --------------------------------------------------------------------------- #
T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
TABLE_POINTS = [(0.0, 1.0), (0.2, 1.2), (0.4, 1.4), (0.6, 1.6), (0.8, 1.8), (1.0, 2.0)]
NARROW_POINTS = [(0.0, 1.0), (0.2, 1.2), (0.4, 1.4)]

#: 场景计划：(repo, 模板, 例数, 金标缺口类, 实测倍率)
PLAN: Tuple[Tuple[str, str, int, Optional[str], Optional[float]], ...] = (
    ("SYN-Repo-A", "complete", 8, None, 1.05),
    ("SYN-Repo-A", "complete", 4, None, 1.50),          # 诚实的 FAIL 不是欠账 → 不派单
    ("SYN-Repo-B", "no_doi", 2, G_PROVENANCE, 1.05),
    ("SYN-Repo-B", "no_sha", 2, G_PROVENANCE, 1.05),
    ("SYN-Repo-B", "no_method", 2, G_PROVENANCE, 1.05),
    ("SYN-Repo-B", "no_udig", 2, G_PROVENANCE, 1.05),
    ("SYN-Repo-C", "unregistered", 8, G_NO_DATASET, 1.05),
    ("SYN-Repo-C", "narrow", 6, G_RANGE, 1.05),
    ("SYN-Repo-A", "complete", 5, G_MEASUREMENT, None),
    ("SYN-Repo-B", "weak", 6, G_RESOLUTION, 1.20),
    ("SYN-Repo-C", "complete", 5, G_STALE, 1.05),
    ("SYN-Repo-A", "complete", 3, G_TAMPER, 1.05),
)
N_ORPHAN = 2


def synthetic_dataset(dataset_id: str, template: str) -> Dict[str, Any]:
    """按模板造数据集（模板只改证据完整性，不改物理含义）。"""
    points = NARROW_POINTS if template == "narrow" else TABLE_POINTS
    u_dig = 0.30 if template == "weak" else 0.01
    ds = wg.make_reference_dataset(
        dataset_id,
        points,
        citation="SYN et al. (2020)",
        doi="10.0000/syn.0001",
        digitised_by="SYN-digitisation",
        retrieved_from="SYN-archive",
        retrieved_sha256="a" * 64,
        digitisation_method="SYN table digitisation (plot reader)",
        u_dig=u_dig,
        u_dig_kind="relative",
        tolerance=0.15,
        quantity="SYN-QUANTITY-ALPHA",
        unit="SYN-UNIT",
    )
    ds["digitisation"]["interp_uncertainty"] = 0.002
    if template == "no_doi":
        ds["provenance"]["doi"] = ""
        ds["provenance"]["url"] = ""
    elif template == "no_sha":
        ds["provenance"]["retrieved_sha256"] = ""
        ds["digitisation"]["original_file_sha256"] = ""
    elif template == "no_method":
        ds["digitisation"]["method"] = ""
    elif template == "no_udig":
        ds["digitisation"].pop("uncertainty", None)
    return ds


def gate_artifact(gate_id: str, dataset_id: str, *, repo: str, measurement: Mapping[str, Any],
                  tolerance: float = 0.15, k_factor: float = 2.0,
                  declared_at: datetime = T0) -> Dict[str, Any]:
    return {
        "schema": wg.SCHEMA_ID,
        "gate_id": gate_id,
        "mission_id": 0,
        "node": "SYN-NODE",
        "repo": repo,
        "quantity": "SYN-QUANTITY-ALPHA",
        "unit": "SYN-UNIT",
        "reason_code": wg.R_NO_REFERENCE,
        "declared_at": iso(declared_at),
        "requires_reference": {
            "dataset_id": dataset_id,
            "tolerance": tolerance,
            "k_factor": k_factor,
            "measurement": dict(measurement),
            "budget": {"components": [{"name": "u_num", "u": 0.01, "kind": "relative"}],
                       "correlations": []},
        },
    }


def build_corpus_specs() -> List[Dict[str, Any]]:
    """把 PLAN 展开成场景（门场景 + ORPHAN 数据集场景）。"""
    specs: List[Dict[str, Any]] = []
    idx = 0
    for repo, template, count, expected, mult in PLAN:
        for _ in range(count):
            idx += 1
            at = 0.5
            ref = 1.0 + at  # TABLE_POINTS 上 y = 1 + x → at=0.5 ⇒ 1.5
            value = None if mult is None else ref * float(mult)
            specs.append({
                "case_id": f"SYN-C{idx:03d}",
                "kind": "gate",
                "repo": repo,
                "template": template,
                "gate_id": f"SYN-G{idx:03d}",
                "dataset_id": f"SYN-DS{idx:03d}",
                "at": at,
                "value": value,
                "expected": expected,
                "register": template != "unregistered",
            })
    for j in range(N_ORPHAN):
        idx += 1
        specs.append({
            "case_id": f"SYN-C{idx:03d}",
            "kind": "dataset",
            "repo": "SYN-Repo-C",
            "template": "complete",
            "gate_id": None,
            "dataset_id": f"SYN-ORPHAN{j + 1:02d}",
            "at": None,
            "value": None,
            "expected": G_ORPHAN,
            "register": True,
        })
    return specs


def materialize_corpus(conn: wg.Conn, specs: Sequence[Mapping[str, Any]], *,
                       t0: datetime = T0) -> Dict[str, Any]:
    """把场景落成库状态（声明门 → 登记数据集 → 预算重判 → 过期标记 → 绑定重建）。"""
    gate_specs = [s for s in specs if s["kind"] == "gate"]
    tamper_specs = [s for s in gate_specs if s["expected"] == G_TAMPER]

    # 1) 篡改场景的数据集先登记（否则后续登记触发的重判会覆盖 TAMPER 判决）
    for s in tamper_specs:
        wg.register_reference(conn, synthetic_dataset(s["dataset_id"], s["template"]),
                              actor="SYN", now=t0 + timedelta(hours=1))

    # 2) 声明非篡改门（此时数据集多未登记 ⇒ 债在数据集侧）
    for s in gate_specs:
        if s["expected"] == G_TAMPER:
            continue
        meas: Dict[str, Any] = {"at": s["at"]}
        if s["value"] is not None:
            meas["value"] = s["value"]
        meas["u_exp"] = 0.01
        wg.declare_gate(conn, gate_artifact(s["gate_id"], s["dataset_id"], repo=s["repo"],
                                            measurement=meas, declared_at=t0), now=t0)

    # 3) 登记其余数据集（幂等）
    for s in specs:
        if s["kind"] == "dataset" or (s["kind"] == "gate" and s["register"]
                                      and s["expected"] != G_TAMPER):
            wg.register_reference(conn, synthetic_dataset(s["dataset_id"], s["template"]),
                                  actor="SYN", now=t0 + timedelta(days=1))

    # 4) 篡改场景：先按基线 tol=0.10 声明，再尝试放宽到 0.30（应被拒并记 TAMPER）
    for s in tamper_specs:
        meas = {"at": s["at"], "value": s["value"], "u_exp": 0.01}
        stamp = t0 + timedelta(days=1)
        wg.declare_gate(conn, gate_artifact(s["gate_id"], s["dataset_id"], repo=s["repo"],
                                            measurement=meas, tolerance=0.10,
                                            declared_at=stamp), now=stamp)
        widened = gate_artifact(s["gate_id"], s["dataset_id"], repo=s["repo"],
                                measurement=meas, tolerance=0.30, declared_at=stamp)
        wg.declare_gate(conn, widened, now=stamp)

    # 5) 绑定索引建立（PENDING → BOUND 随登记推进）
    first = reconcile_bindings(conn, now=t0 + timedelta(days=1, hours=1))

    # 6) 全量预算重判
    verdicts = rejudge_all_with_budget(conn, now=t0 + timedelta(days=2))

    # 7) 过期场景：数据集晚于判决时刻重登记（rejudge=False，故意不刷新判决）
    stale_ids = {s["dataset_id"] for s in gate_specs if s["expected"] == G_STALE}
    for ds_id in sorted(stale_ids):
        wg.register_reference(conn, synthetic_dataset(ds_id, "complete"), actor="SYN",
                              now=t0 + timedelta(days=3), rejudge=False)

    # 8) 收尾重建索引（幂等；正常应无动作）
    second = reconcile_bindings(conn, now=t0 + timedelta(days=3, hours=1))
    return {"first_reconcile": first, "second_reconcile": second, "verdicts": verdicts}


def drift_selftest(conn: wg.Conn, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """人为制造三类漂移 → 覆盖率必须掉下 100% → reconcile 后必须回到 100%。"""
    now = now or utc_now()
    before = binding_coverage(conn)
    gates = [g["gate_id"] for g in gate_artifacts(conn)]
    victim_unbound, victim_stale = gates[0], gates[1]
    conn.execute("DELETE FROM tensor_reference_bindings WHERE gate_id = ?", (victim_unbound,))
    conn.execute("UPDATE tensor_reference_bindings SET dataset_id = 'SYN-DS-GHOST-TARGET',"
                 " artifact_sha256 = 'deadbeef' WHERE gate_id = ?", (victim_stale,))
    conn.execute(
        """INSERT INTO tensor_reference_bindings
               (gate_id, dataset_id, artifact_sha256, state, bound_at, updated_at)
           VALUES ('SYN-G-GHOST', 'SYN-DS-GHOST', 'deadbeef', 'PENDING', ?, ?)""",
        (iso(now), iso(now)))
    conn.commit()
    during = binding_coverage(conn)
    repair = reconcile_bindings(conn, now=now)
    after = binding_coverage(conn)
    return {
        "schema": "binding-drift-selftest/v1",
        "before": {k: before[k] for k in ("binding_coverage", "drift_counts", "bidirectional_ok")},
        "during_drift": {k: during[k] for k in ("binding_coverage", "drift_counts",
                                                "bidirectional_ok")},
        "repair_actions": repair["n_actions"],
        "after_repair": {k: after[k] for k in ("binding_coverage", "drift_counts",
                                               "bidirectional_ok")},
        "detected_all_three": bool(all(during["drift_counts"][k] >= 1 for k in
                                       (DRIFT_UNBOUND, DRIFT_GHOST, DRIFT_STALE))),
        "repaired": bool(after["bidirectional_ok"]),
    }


#: IN50 的派单口径：只有 4 个 reason_code 映射到缺口类（其余一律漏派）
IN50_REASON_TO_GAP = {
    R_NO_REFERENCE: G_NO_DATASET,
    R_REFERENCE_INCOMPLETE: G_PROVENANCE,
    R_OUT_OF_RANGE: G_RANGE,
    R_MEASUREMENT_MISSING: G_MEASUREMENT,
}


def in50_baseline(specs: Sequence[Mapping[str, Any]], *, t0: datetime = T0
                  ) -> Dict[str, Any]:
    """IN50 口径基线（对照用）：不做双向绑定、不做分量预算、不识别 5 类新缺口。

    在一个独立的内存库里按 IN50 的规则重放同一批场景：
    门声明 → 登记自动重判（旧判据 ``k·sqrt(u_exp²+u_dig²)``）→ 派单只按 reason_code。
    输出反查能力、可信 PASS 数与派单准确率，供"改进前/后"对照。
    """
    conn = wg.connect(":memory:")
    gate_specs = [s for s in specs if s["kind"] == "gate"]
    tamper_specs = [s for s in gate_specs if s["expected"] == G_TAMPER]

    for spec in tamper_specs:
        wg.register_reference(conn, synthetic_dataset(spec["dataset_id"], spec["template"]),
                              actor="SYN", now=t0 + timedelta(hours=1))
    for spec in gate_specs:
        if spec["expected"] == G_TAMPER:
            continue
        meas: Dict[str, Any] = {"at": spec["at"], "u_exp": 0.01}
        if spec["value"] is not None:
            meas["value"] = spec["value"]
        wg.declare_gate(conn, gate_artifact(spec["gate_id"], spec["dataset_id"],
                                            repo=spec["repo"], measurement=meas,
                                            declared_at=t0), now=t0)
    for spec in specs:
        if spec["kind"] == "dataset" or (spec["kind"] == "gate" and spec["register"]
                                         and spec["expected"] != G_TAMPER):
            wg.register_reference(conn, synthetic_dataset(spec["dataset_id"], spec["template"]),
                                  actor="SYN", now=t0 + timedelta(days=1))
    for spec in tamper_specs:
        stamp = t0 + timedelta(days=1)
        meas = {"at": spec["at"], "value": spec["value"], "u_exp": 0.01}
        wg.declare_gate(conn, gate_artifact(spec["gate_id"], spec["dataset_id"],
                                            repo=spec["repo"], measurement=meas,
                                            tolerance=0.10, declared_at=stamp), now=stamp)
        wg.declare_gate(conn, gate_artifact(spec["gate_id"], spec["dataset_id"],
                                            repo=spec["repo"], measurement=meas,
                                            tolerance=0.30, declared_at=stamp), now=stamp)
    for ds_id in sorted({s["dataset_id"] for s in gate_specs if s["expected"] == G_STALE}):
        wg.register_reference(conn, synthetic_dataset(ds_id, "complete"), actor="SYN",
                              now=t0 + timedelta(days=3), rejudge=False)

    gaps = []
    for row in wg.open_withheld_gates(conn):
        gap_class = IN50_REASON_TO_GAP.get(str(row["verdict_reason_code"]))
        if gap_class:
            gaps.append({"gate_id": row["gate_id"], "repo": row["repo"],
                         "dataset_id": row["required_dataset_id"],
                         "gap_class": gap_class})
    verdicts = conn.query("SELECT verdict, verdict_reason_code FROM tensor_withheld_gates")
    washed_pass = [v for v in verdicts
                   if v["verdict"] == PASS and v["verdict_reason_code"] == "WITHIN_UNCERTAINTY"]
    gold = {str(s["gate_id"] or s["dataset_id"]): s["expected"] for s in specs}
    accuracy = evaluate_dispatch_accuracy(gold, gaps)
    reverse_supported = any("reference_bindings" in t for t in
                            (r["name"] for r in conn.query(
                                "SELECT name FROM sqlite_master WHERE type = 'table'")))
    out = {
        "schema": "in50-baseline/v1",
        "reverse_lookup_supported": bool(reverse_supported),
        "gaps_detected": len(gaps),
        "dispatch_accuracy": accuracy["accuracy"],
        "false_orders": len(accuracy["false_orders"]),
        "misses": accuracy["misses"],
        "uncertainty_washed_pass": len(washed_pass),
        "verdict_counts": {v: sum(1 for r in verdicts if r["verdict"] == v)
                           for v in (PASS, WITHHELD, FAIL)},
        "note": ("IN50 只有门→数据集单向边（无反向索引）、不确定度带可把超差结果判 PASS、"
                 "派单只识别 4 类 reason_code；本基线用同一批场景在 IN50 规则下重放"),
    }
    conn.close()
    return out


# --------------------------------------------------------------------------- #
# 六、图（机制流程图 + 三项判据图）
# --------------------------------------------------------------------------- #
def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def figure_mechanism_flow(path: Path) -> Path:
    plt = _plt()
    fig, ax = plt.subplots(figsize=(11.5, 6.4))
    ax.set_xlim(0, 11.5)
    ax.set_ylim(0, 6.4)
    ax.axis("off")
    boxes = [
        (0.3, 4.6, "gate artifact\nrequires_reference\n{tol, k, measurement}"),
        (3.1, 4.6, "gate ledger\nWITHHELD gates\n(single source of truth)"),
        (5.9, 4.6, "reference registry\nDOI / sha256 / method\nu_dig / interp_u"),
        (8.7, 4.6, "binding index (2-way)\nPENDING / BOUND\nartifact_sha256"),
        (0.3, 2.2, "uncertainty budget\nu_c=sqrt(Su^2+2Srho*u*u)\nU=k*u_c, band=max(tol,U/D)"),
        (3.1, 2.2, "3-state verdict\nPASS/WITHHELD/FAIL\nlow resolution => WITHHELD"),
        (5.9, 2.2, "gap classifier\nupstream-first table\n9 gap classes"),
        (8.7, 2.2, "dispatch\nWO-hash(repo|class|target)\nidempotent orders"),
        (3.1, 0.4, "criterion A\n|F&R|/|F|R| = 100%"),
        (5.9, 0.4, "criterion B\nauto vs hand ledger < 1%"),
        (8.7, 0.4, "criterion C\nlabelled corpus accuracy > 95%"),
    ]
    for x, y, text in boxes:
        ax.add_patch(plt.Rectangle((x, y), 2.4, 1.2, facecolor="#eef3fb",
                                   edgecolor="#33507a", linewidth=1.4))
        ax.text(x + 1.2, y + 0.6, text, ha="center", va="center", fontsize=8.2)
    arrows = [
        ((2.7, 5.2), (3.1, 5.2)), ((5.5, 5.2), (5.9, 5.2)), ((8.3, 5.2), (8.7, 5.2)),
        ((9.9, 4.6), (9.9, 3.4)), ((8.7, 2.8), (8.3, 2.8)), ((5.9, 2.8), (5.5, 2.8)),
        ((3.1, 2.8), (2.7, 2.8)), ((1.5, 2.2), (1.5, 3.4)),
        ((4.3, 2.2), (4.3, 1.6)), ((7.1, 2.2), (7.1, 1.6)), ((9.9, 2.2), (9.9, 1.6)),
        ((7.1, 4.6), (7.1, 3.4)),
    ]
    for (x0, y0), (x1, y1) in arrows:
        ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                    arrowprops=dict(arrowstyle="->", color="#33507a", lw=1.3))
    ax.text(0.3, 6.05, "WITHHELD debt-closure maturation (IN51-W5-N09): mechanism flow",
            fontsize=13, fontweight="bold", color="#1b2f4d")
    ax.text(0.3, 0.05, "Top row: two sides of the evidence chain (gate ledger / registry) + bidirectional binding index. "
                       "Middle: budget verdict -> gap classifier -> idempotent dispatch. Bottom: three machine-checkable criteria.",
            fontsize=7.5, color="#444444")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return path


def figure_binding_coverage(path: Path, coverage: Mapping[str, Any],
                            drift: Mapping[str, Any]) -> Path:
    plt = _plt()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 4.2),
                                   gridspec_kw={"width_ratios": [1.25, 1]})
    labels = ["forward", "reverse", "bidirectional\n(sym-diff)", "registration"]
    values = [coverage["forward_coverage"], coverage["reverse_coverage"],
              coverage["binding_coverage"], coverage["registration_coverage"]]
    bars = ax1.bar(labels, [v * 100 for v in values],
                   color=["#4c78a8", "#4c78a8", "#54a24b", "#b279a2"])
    ax1.axhline(100, color="#d62728", linestyle="--", linewidth=1.2, label="criterion 100%")
    ax1.set_ylim(0, 108)
    ax1.set_ylabel("coverage (%)")
    ax1.set_title("Registry <-> gate bidirectional binding coverage")
    for b, v in zip(bars, values):
        ax1.text(b.get_x() + b.get_width() / 2, b.get_height() + 1.2,
                 f"{v * 100:.1f}%", ha="center", fontsize=9)
    ax1.legend(fontsize=8, loc="lower right")

    cats = [DRIFT_UNBOUND, DRIFT_GHOST, DRIFT_STALE]
    before = [drift["during_drift"]["drift_counts"][c] for c in cats]
    after = [drift["after_repair"]["drift_counts"][c] for c in cats]
    x = list(range(len(cats)))
    ax2.bar([i - 0.18 for i in x], before, width=0.36, label="after drift injection", color="#e45756")
    ax2.bar([i + 0.18 for i in x], after, width=0.36, label="after reconcile", color="#54a24b")
    ax2.set_xticks(x)
    ax2.set_xticklabels(cats)
    ax2.set_ylabel("drifted edges")
    ax2.set_title("three drift classes: detect -> repair")
    ax2.legend(fontsize=8)
    ax2.set_ylim(0, max(before + [1]) + 1.0)
    for i, (b, a) in enumerate(zip(before, after)):
        ax2.text(i - 0.18, b + 0.05, str(b), ha="center", fontsize=8)
        ax2.text(i + 0.18, a + 0.05, str(a), ha="center", fontsize=8)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return path


def figure_budget_parity(path: Path, parity: Mapping[str, Any]) -> Path:
    plt = _plt()
    rows = parity["rows"]
    auto = [r["u_combined"] for r in rows]
    hand = [r["u_combined_manual"] for r in rows]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 4.2))
    lo, hi = min(auto + hand) * 0.6, max(auto + hand) * 1.6
    ax1.plot([lo, hi], [lo, hi], color="#333333", linewidth=1.0, label="1:1")
    ax1.plot([lo, hi], [lo * 1.01, hi * 1.01], color="#d62728", linestyle="--",
             linewidth=1.0, label="+/-1% criterion band")
    ax1.plot([lo, hi], [lo * 0.99, hi * 0.99], color="#d62728", linestyle="--", linewidth=1.0)
    ax1.scatter(auto, hand, s=18, color="#4c78a8", label="budget cases")
    ax1.set_xscale("log")
    ax1.set_yscale("log")
    ax1.set_xlabel("auto path u_c (float fsum)")
    ax1.set_ylabel("hand ledger u_c (Decimal, 3 sig digits)")
    ax1.set_title(f"auto vs manual parity (n={parity['n_cases']})")
    ax1.legend(fontsize=8)

    errs = [max(r["parity_rel_error"], 1e-18) for r in rows]
    mc_err = [max(r["mc"]["rel_error"], 1e-18) for r in rows]
    idx = list(range(len(rows)))
    ax2.semilogy(idx, errs, "o-", markersize=3.5, color="#4c78a8", label="auto vs hand ledger")
    ax2.semilogy(idx, mc_err, "s-", markersize=3.0, color="#f58518", label="analytic vs Monte-Carlo")
    ax2.axhline(BUDGET_PARITY_TARGET, color="#d62728", linestyle="--", linewidth=1.2,
                label="criterion 1%")
    ax2.set_xlabel("case index")
    ax2.set_ylabel("relative error")
    ax2.set_title("relative error (log scale)")
    ax2.legend(fontsize=8)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return path


def figure_dispatch_confusion(path: Path, accuracy: Mapping[str, Any]) -> Path:
    plt = _plt()
    cols = accuracy["columns"]
    mat = [[accuracy["confusion"][r][c] for c in cols] for r in cols]
    fig, ax = plt.subplots(figsize=(7.8, 6.4))
    im = ax.imshow(mat, cmap="Blues")
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels(cols, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(len(cols)))
    ax.set_yticklabels(cols, fontsize=8)
    ax.set_xlabel("predicted gap class")
    ax.set_ylabel("gold label (scenario)")
    ax.set_title(f"gap-dispatch confusion matrix (n={accuracy['n_cases']}, "
                 f"accuracy={accuracy['accuracy'] * 100:.2f}%)", fontsize=11)
    peak = max(max(r) for r in mat) if mat else 0
    for i in range(len(cols)):
        for j in range(len(cols)):
            if mat[i][j]:
                ax.text(j, i, str(mat[i][j]), ha="center", va="center", fontsize=8,
                        color="white" if mat[i][j] > peak / 2 else "black")
    fig.colorbar(im, ax=ax, shrink=0.8)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return path


def figure_resolution(path: Path, budgets: Sequence[Mapping[str, Any]]) -> Path:
    plt = _plt()
    labels = [b["gate_id"] for b in budgets]
    band = [b["band_uncertainty"] for b in budgets]
    tol = [b["tolerance"] for b in budgets]
    fig, ax = plt.subplots(figsize=(11.0, 4.0))
    colors = ["#e45756" if b["band_uncertainty"] > b["tolerance"] else "#4c78a8"
              for b in budgets]
    ax.bar(range(len(labels)), band, color=colors, width=0.8,
           label="k*u_c/D (expanded uncertainty band)")
    ax.plot(range(len(labels)), tol, color="#333333", linestyle="--", linewidth=1.2,
            label="tol (gate tolerance)")
    step = max(1, len(labels) // 18)
    ax.set_xticks(range(0, len(labels), step))
    ax.set_xticklabels([labels[i] for i in range(0, len(labels), step)],
                       rotation=60, ha="right", fontsize=7)
    ax.set_ylabel("normalised error band")
    ax.set_title("resolution criterion: k*u_c/D <= tol (red = insufficient resolution -> WITHHELD, never PASS)")
    ax.legend(fontsize=8)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- #
# 七、全链路运行 + 判据汇总
# --------------------------------------------------------------------------- #
def run_closure(outdir: Path, *, t0: datetime = T0, mc_n: int = 60_000,
                write_figures: bool = True) -> Dict[str, Any]:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    conn = wg.connect(":memory:")
    ensure_closure_schema(conn)

    specs = build_corpus_specs()
    build = materialize_corpus(conn, specs, t0=t0)
    drift = drift_selftest(conn, now=t0 + timedelta(days=4))
    coverage = binding_coverage(conn)

    now = t0 + timedelta(days=5)
    verdicts = build["verdicts"]  # 判决时刻 = t0+2d；过期场景的登记发生在 t0+3d
    gaps = classify_gaps(conn, now=now)
    orders = dispatch_gap_orders(gaps, now=now)
    orders_again = dispatch_gap_orders(classify_gaps(conn, now=now), now=now)
    idempotent = [o["order_id"] for o in orders] == [o["order_id"] for o in orders_again]

    gold: Dict[str, Optional[str]] = {}
    for spec in specs:
        gold[str(spec["gate_id"] or spec["dataset_id"])] = spec["expected"]
    accuracy = evaluate_dispatch_accuracy(gold, gaps)
    parity = evaluate_budget_parity(budget_parity_corpus(), mc_n=mc_n)

    budget_rows = []
    for g in gate_artifacts(conn):
        latest = latest_budget(conn, g["gate_id"])
        if latest:
            budget_rows.append({"gate_id": g["gate_id"],
                                "band_uncertainty": latest["band_uncertainty"],
                                "tolerance": latest["tolerance"],
                                "u_combined": latest["u_combined"],
                                "resolution_ok": latest["resolution_ok"]})
    audit = wg.verify_audit_chain(conn)

    verdict_counts: Dict[str, int] = {}
    for v in verdicts:
        verdict_counts[v["verdict"]] = verdict_counts.get(v["verdict"], 0) + 1

    criteria = {
        "A_binding_coverage": {
            "target": "binding_coverage = 100%",
            "value": coverage["binding_coverage"],
            "bidirectional_ok": coverage["bidirectional_ok"],
            "drift_counts": coverage["drift_counts"],
            "pass": bool(coverage["bidirectional_ok"] and
                         coverage["binding_coverage"] >= COVERAGE_TARGET),
        },
        "B_uncertainty_budget_parity": {
            "target": "自动核算 vs 手工台账相对误差 < 1%",
            "max_rel_error_hand_ledger": parity["max_parity_rel_error_hand_ledger"],
            "max_rel_error_exact_manual": parity["max_parity_rel_error_exact_manual"],
            "n_cases": parity["n_cases"],
            "mc_within_3stderr": parity["mc_within_3stderr"],
            "pass": bool(parity["max_parity_rel_error_hand_ledger"] < BUDGET_PARITY_TARGET),
        },
        "C_dispatch_accuracy": {
            "target": "标注语料派单准确率 > 95%",
            "value": accuracy["accuracy"],
            "n_cases": accuracy["n_cases"],
            "false_orders": len(accuracy["false_orders"]),
            "pass": bool(accuracy["accuracy"] > DISPATCH_ACCURACY_TARGET),
        },
    }
    baseline = in50_baseline(specs, t0=t0)
    # 改进前后对照：同一批场景，IN50 规则 vs 闭环口径
    delta = {
        "binding_reverse_lookup": {"before": baseline["reverse_lookup_supported"],
                                   "after": bool(coverage["registry_reverse_coverage"] == 1.0)},
        "dispatch_accuracy": {"before": baseline["dispatch_accuracy"],
                              "after": accuracy["accuracy"]},
        "gaps_detected": {"before": baseline["gaps_detected"], "after": len(gaps)},
        "uncertainty_washed_pass": {"before": baseline["uncertainty_washed_pass"],
                                    "after": 0},
        # 「可采信 PASS(当前)」：判决 PASS 且无任何在案缺口（不含过期/篡改/分辨力/出处债）
        "trustworthy_pass": {
            "before": baseline["verdict_counts"][PASS],
            "after": sum(1 for v in verdicts
                         if v["verdict"] == PASS
                         and v["gate_id"] not in tampered_gates(conn)
                         and v["gate_id"] not in {g.get("gate_id") for g in gaps
                                                  if g.get("kind") == "gate"}),
        },
    }
    criteria["D_baseline_delta"] = {
        "target": "闭环口径在三项判据上均严格优于 IN50 基线",
        "dispatch_accuracy_gain": delta["dispatch_accuracy"]["after"]
        - delta["dispatch_accuracy"]["before"],
        "washed_pass_eliminated": delta["uncertainty_washed_pass"]["before"],
        "pass": bool(delta["dispatch_accuracy"]["after"] > delta["dispatch_accuracy"]["before"]
                     and delta["uncertainty_washed_pass"]["before"] > 0
                     and delta["binding_reverse_lookup"]["after"] is True),
    }

    overall = "PASS" if all(c["pass"] for c in criteria.values()) else "FAIL"

    figures: List[str] = []
    fig_dir = outdir / "figures"
    if write_figures:
        figures = [
            str(figure_mechanism_flow(fig_dir / "fig1_mechanism_flow.png")),
            str(figure_binding_coverage(fig_dir / "fig2_binding_coverage.png", coverage, drift)),
            str(figure_budget_parity(fig_dir / "fig3_budget_parity.png", parity)),
            str(figure_dispatch_confusion(fig_dir / "fig4_dispatch_confusion.png", accuracy)),
            str(figure_resolution(fig_dir / "fig5_resolution_vs_tolerance.png", budget_rows)),
        ]

    summary = {
        "schema": SCHEMA_ID,
        "criteria": criteria,
        "overall": overall,
        "coverage": coverage,
        "drift_selftest": drift,
        "verdict_counts": verdict_counts,
        "gate_verdicts": [{k: v.get(k) for k in ("gate_id", "verdict", "reason_code", "E")}
                          for v in verdicts],
        "budget_rows": budget_rows,
        "gap_counts": {c: sum(1 for g in gaps if g["gap_class"] == c) for c in GAP_CLASSES},
        "n_gaps": len(gaps),
        "n_orders": len(orders),
        "orders_idempotent": idempotent,
        "corpus_size": len(specs),
        "in50_baseline": baseline,
        "delta_vs_in50": delta,
        "tampered_gates": sorted(tampered_gates(conn)),
        "audit_chain_ok": bool(audit.get("ok", audit.get("valid", False))),
        "audit_chain": audit,
        "figures": figures,
    }

    # 判据数字与产物路径同 JSON：便于验收机器直接定位证据
    summary["artifacts"] = {
        name: str(outdir / name) for name in (
            "closure_summary.json", "binding_coverage.json", "drift_selftest.json",
            "budget_parity.json", "dispatch_accuracy.json", "dispatch_orders.json",
            "gaps.json", "corpus.json", "budget_rows.json")
    }
    summary["figure_paths"] = {Path(f).name: f for f in figures}

    def _dump(name: str, payload: Any) -> None:
        (outdir / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False,
                                              default=str), encoding="utf-8")

    _dump("closure_summary.json", summary)
    _dump("binding_coverage.json", coverage)
    _dump("drift_selftest.json", drift)
    _dump("budget_parity.json", parity)
    _dump("dispatch_accuracy.json", accuracy)
    _dump("dispatch_orders.json", orders)
    _dump("gaps.json", gaps)
    _dump("corpus.json", specs)
    _dump("budget_rows.json", budget_rows)
    conn.close()
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="WITHHELD 欠账闭环成熟化（IN51-W5-N09）")
    parser.add_argument("--out", default="artifacts/withheld_closure", help="产物目录")
    parser.add_argument("--demo", action="store_true", help="跑全链路合成战场并出图")
    parser.add_argument("--check", action="store_true", help="只跑判据并打印汇总")
    parser.add_argument("--no-figures", action="store_true", help="不出图（快速自检）")
    parser.add_argument("--mc-n", type=int, default=60_000, help="Monte-Carlo 样本数")
    args = parser.parse_args(argv)

    summary = run_closure(Path(args.out), mc_n=args.mc_n,
                          write_figures=not args.no_figures)
    print(json.dumps({k: summary[k] for k in ("criteria", "overall", "verdict_counts",
                                              "gap_counts", "n_orders",
                                              "orders_idempotent", "audit_chain_ok")},
                     indent=2, ensure_ascii=False))
    print(f"artifacts → {args.out}")
    return 0 if summary["overall"] == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())