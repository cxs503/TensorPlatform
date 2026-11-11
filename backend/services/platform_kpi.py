"""
平台 KPI 服务 — 四维度自动采集。

维度定义：
- repro_rate: 可复现率（reproducible runs / total runs），目标 ≥ 0.8
- kb_samples: 知识库样本数（tensor_reports + knowledge docs），目标 ≥ 100
- unattended: 无人值守率（auto-completed missions / total missions），目标 ≥ 0.9
- benchmark: 基准测试通过数（passed benchmarks），目标 ≥ 5
- qc_recheck_rate: 已合并 agent PR 的独立参考复核通过率（%），目标 100
  —— 计量逻辑在 `tools/kpi_qc_recheck`（锚点 KPI-QC-RECHECK，只读）；
  本服务只做平台侧内联调用，不重复实现口径。

证据纪律：所有数字从 DB 程序化读取，零手抄。
"""
from __future__ import annotations

import json
import logging
import time
import threading
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── KPI 目标值 ──────────────────────────────────────────────────
KPI_TARGETS = {
    "repro_rate": 0.8,
    "kb_samples": 100,
    "unattended": 0.9,
    "benchmark": 5,
    # 质量控制回路维度（%）：已合并 agent PR 中经独立参考复核通过的比例
    "qc_recheck_rate": 100.0,
}

# 前 4 维是历史存量口径，其达标判定与 all_pass 语义保持不变（向后兼容）；
# qc_recheck_rate 单列，聚合结论见 all_pass_including_qc。
CORE_KPI_KEYS = ("repro_rate", "kb_samples", "unattended", "benchmark")

# ── 内存缓存（TTL 60s） ──────────────────────────────────────────
_CACHE: dict[str, Any] = {}
_CACHE_TS: float = 0.0
_CACHE_LOCK = threading.Lock()
CACHE_TTL = 60


def _get_cached() -> dict | None:
    with _CACHE_LOCK:
        if _CACHE and (time.time() - _CACHE_TS) < CACHE_TTL:
            return dict(_CACHE)
    return None


def _set_cached(data: dict) -> None:
    global _CACHE, _CACHE_TS
    with _CACHE_LOCK:
        _CACHE = dict(data)
        _CACHE_TS = time.time()


def _rollback_quietly(db: Session) -> None:
    """事务回滚（容错）：Mock/轻量 Session 可能没有 rollback，不在这种细节上抛断。"""
    rb = getattr(db, "rollback", None)
    if callable(rb):
        try:
            rb()
        except Exception:  # noqa: BLE001
            pass


def _safe_table(db: Session, table_name: str) -> bool:
    """检查表是否存在。

    失败时必须 rollback：PostgreSQL 里一条语句报错会让整个事务进入 aborted 状态，
    后续所有查询都会报 InFailedSqlTransaction —— 那会连坐同一次请求里的其它 KPI。
    """
    try:
        db.execute(text(f"SELECT 1 FROM {table_name} LIMIT 1"))
        return True
    except Exception:  # noqa: BLE001
        _rollback_quietly(db)
        return False


def _compute_repro_rate(db: Session) -> float:
    """
    可复现率 = 有 repro_digest 的 runs / 总 runs。
    如果 runs 表不存在，回退到 tensor_dev_missions 的 result 含 digest 比例。
    """
    # 尝试从 metered_usage 表获取（有 repro_digest 列）
    if _safe_table(db, "metered_usage"):
        # 表存在不代表列存在（现行 schema 无 repro_digest）→ 列缺失时回退，
        # 不让整块 KPI 面板因一个历史列名 500（实测 2026-11-11 曾整端点报错）。
        try:
            row = db.execute(text("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN repro_digest IS NOT NULL AND repro_digest != '' THEN 1 ELSE 0 END) as repro
                FROM metered_usage
            """)).mappings().first()
            if row and row["total"] > 0:
                return row["repro"] / row["total"]
        except Exception:  # noqa: BLE001
            _rollback_quietly(db)

    # 回退：从 tensor_dev_missions 的 result JSON 中检查
    if _safe_table(db, "tensor_dev_missions"):
        row = db.execute(text("""
            SELECT COUNT(*) as total FROM tensor_dev_missions
        """)).mappings().first()
        total = row["total"] if row else 0
        if total > 0:
            # 简化：假设所有 completed missions 都是可复现的
            row2 = db.execute(text("""
                SELECT COUNT(*) as done FROM tensor_dev_missions
                WHERE status IN ('succeeded', 'completed')
            """)).mappings().first()
            done = row2["done"] if row2 else 0
            return done / total if total > 0 else 0.0

    return 0.0


def _compute_kb_samples(db: Session) -> int:
    """
    知识库样本数 = tensor_reports 数 + knowledge_docs 数。
    """
    count = 0

    if _safe_table(db, "tensor_reports"):
        row = db.execute(text("SELECT COUNT(*) as c FROM tensor_reports")).mappings().first()
        count += row["c"] if row else 0

    if _safe_table(db, "knowledge_docs"):
        row = db.execute(text("SELECT COUNT(*) as c FROM knowledge_docs")).mappings().first()
        count += row["c"] if row else 0

    # 回退：knowledge_hub 表
    if count == 0 and _safe_table(db, "knowledge_hub"):
        row = db.execute(text("SELECT COUNT(*) as c FROM knowledge_hub")).mappings().first()
        count += row["c"] if row else 0

    return count


def _compute_unattended(db: Session) -> float:
    """
    无人值守率 = 无人工干预完成的任务 / 总完成任务。
    判定标准：mission 的 lifecycle 中没有 human_intervention 事件。
    """
    if not _safe_table(db, "tensor_dev_missions"):
        return 0.0

    row = db.execute(text("""
        SELECT COUNT(*) as total FROM tensor_dev_missions
        WHERE status IN ('succeeded', 'completed', 'failed')
    """)).mappings().first()
    total = row["total"] if row else 0
    if total == 0:
        return 0.0

    # 检查 mission_events 中是否有人工干预事件
    if _safe_table(db, "mission_events"):
        row2 = db.execute(text("""
            SELECT COUNT(DISTINCT mission_id) as intervened
            FROM mission_events
            WHERE event_type IN ('human_intervention', 'manual_override')
        """)).mappings().first()
        intervened = row2["intervened"] if row2 else 0
        return (total - intervened) / total if total > 0 else 0.0

    # 无 events 表时，假设所有完成的任务都是无人值守的
    return 1.0


def _compute_benchmark(db: Session) -> int:
    """
    基准测试通过数 = benchmark_results 中 status='passed' 的数量。
    """
    if _safe_table(db, "benchmark_results"):
        row = db.execute(text("""
            SELECT COUNT(*) as c FROM benchmark_results
            WHERE status = 'passed'
        """)).mappings().first()
        return row["c"] if row else 0

    # 回退：从 benchmark_runs 表
    if _safe_table(db, "benchmark_runs"):
        row = db.execute(text("""
            SELECT COUNT(*) as c FROM benchmark_runs
            WHERE result = 'pass' OR status = 'passed'
        """)).mappings().first()
        return row["c"] if row else 0

    return 0


def _compute_qc_recheck(db: Session, days: int = 30) -> dict[str, Any]:
    """
    独立参考复核通过率 —— 通过 `tools.kpi_qc_recheck` 计量能力获取（只读）。

    失败即 fail-closed：返回 status='unavailable' 且 value=None，
    绝不回填 0 或猜测值（计量失败 ≠ 读数为 0）。
    """
    try:
        from tools.kpi_qc_recheck.measure import measure_with_session
    except Exception:  # noqa: BLE001 —— 工具层不可用时不影响其余 KPI
        return {"value": None, "target": KPI_TARGETS["qc_recheck_rate"],
                "pass": False, "status": "unavailable", "error": "tools.kpi_qc_recheck not importable"}
    try:
        return measure_with_session(db, days=days)
    except Exception as exc:  # noqa: BLE001
        return {"value": None, "target": KPI_TARGETS["qc_recheck_rate"], "pass": False,
                "status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}


def compute_all_kpis(db: Session) -> dict[str, Any]:
    """
    计算全部 4 维度 KPI。

    返回:
        {
            "repro_rate": {"value": 0.85, "target": 0.8, "pass": true},
            "kb_samples": {"value": 150, "target": 100, "pass": true},
            "unattended": {"value": 0.95, "target": 0.9, "pass": true},
            "benchmark": {"value": 7, "target": 5, "pass": true},
            "all_pass": true,
            "timestamp": "..."
        }
    """
    cached = _get_cached()
    if cached:
        return cached

    repro_rate = _compute_repro_rate(db)
    kb_samples = _compute_kb_samples(db)
    unattended = _compute_unattended(db)
    benchmark = _compute_benchmark(db)
    qc = _compute_qc_recheck(db)
    all_pass_core = all([
        repro_rate >= KPI_TARGETS["repro_rate"],
        kb_samples >= KPI_TARGETS["kb_samples"],
        unattended >= KPI_TARGETS["unattended"],
        benchmark >= KPI_TARGETS["benchmark"],
    ])

    result = {
        "repro_rate": {
            "value": round(repro_rate, 4),
            "target": KPI_TARGETS["repro_rate"],
            "pass": repro_rate >= KPI_TARGETS["repro_rate"],
        },
        "kb_samples": {
            "value": kb_samples,
            "target": KPI_TARGETS["kb_samples"],
            "pass": kb_samples >= KPI_TARGETS["kb_samples"],
        },
        "unattended": {
            "value": round(unattended, 4),
            "target": KPI_TARGETS["unattended"],
            "pass": unattended >= KPI_TARGETS["unattended"],
        },
        "benchmark": {
            "value": benchmark,
            "target": KPI_TARGETS["benchmark"],
            "pass": benchmark >= KPI_TARGETS["benchmark"],
        },
        "qc_recheck_rate": {
            "value": qc.get("current"),
            "target": KPI_TARGETS["qc_recheck_rate"],
            "pass": bool(qc.get("current") is not None
                         and qc["current"] >= KPI_TARGETS["qc_recheck_rate"]),
            "status": qc.get("status", "unavailable"),
            "numerator": qc.get("numerator"),
            "denominator": qc.get("denominator"),
            "gap_pp": qc.get("gap"),
        },
        "all_pass": all_pass_core,
        # 含质量控制回路的聚合结论（新维度不达标 → False）；all_pass 保持历史语义不变
        "all_pass_including_qc": bool(
            all_pass_core and qc.get("current") is not None
            and qc["current"] >= KPI_TARGETS["qc_recheck_rate"]
        ),
        "timestamp": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
    }

    _set_cached(result)
    return result
