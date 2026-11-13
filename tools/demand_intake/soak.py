"""soak —— 七日浸泡审计：覆盖率与帽拒率周报（FEAT-DEMAND-INTAKE-SOAK）。

背景
----
需求入榜 v2 的事后审计（audit.py）有日帽 AUDIT_DAILY_CAP=20。但单看「今天审了几条」
无法回答系统性问题：过去 7 天覆盖够不够？帽拒率（agent 铸造被审计门打回的比例）
是否健康？本模块补上**七日滚动窗口**的度量与自动调帽机制。

验收判据（机检）
----------------
- 连续 7 日日审计覆盖率 = 已审 / 待审 >= 60%
- agent 铸造帽拒率 < 20%
- 任一不达标 → 审计帽由 20/日上调至 40/日（写回 demand_mint_quota）

口径定义
--------
- **日审计覆盖率** = 当日 audited_at IS NOT NULL 的行数 / 当日可审候选总数
  （可审候选 = demand_intake_log.status='open' AND audit_verdict='' 且
   feature_ledger.status='open' 的 JOIN 结果）
- **帽拒率** = 审计 verdict=fail 的行数 / 审计抽样总数（7 日滚动）
- **连续 7 日** = 从 today 往前数 7 个自然日，每天都满足覆盖率 >= 60%

零领域语义：本模块只认 slug 形状、量化验收、状态机与计数；任何域词条都来自 DB 注入的行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import store as S

#: 覆盖率阈值（>= 此值算达标）
COVERAGE_THRESHOLD = 0.60
#: 帽拒率阈值（< 此值算健康）
REJECTION_THRESHOLD = 0.20
#: 浸泡窗口（自然日）
SOAK_WINDOW_DAYS = 7
#: 达标时的默认审计帽
CAP_NORMAL = 20
#: 不达标时的上调审计帽
CAP_ESCALATED = 40


@dataclass
class DailyAuditStat:
    """单日审计统计。"""
    day: str                     # YYYY-MM-DD
    audited: int = 0             # 当日 audited_at IS NOT NULL 的行数
    pending: int = 0             # 当日可审候选总数（审计前快照）
    sampled: int = 0             # 当日抽样数
    passed: int = 0              # 当日 pass 数
    failed: int = 0              # 当日 fail 数
    dropped: int = 0             # 当日 dropped 数

    @property
    def coverage_rate(self) -> float:
        """覆盖率 = 已审 / 待审；待审=0 时视为 1.0（vacuously true）。"""
        if self.pending == 0:
            return 1.0
        return self.audited / self.pending

    @property
    def rejection_rate(self) -> float:
        """帽拒率 = failed / sampled；sampled=0 时视为 0.0。"""
        if self.sampled == 0:
            return 0.0
        return self.failed / self.sampled


@dataclass
class SoakReport:
    """七日浸泡审计报告。"""
    window_start: str            # YYYY-MM-DD
    window_end: str              # YYYY-MM-DD
    daily_stats: List[DailyAuditStat] = field(default_factory=list)
    coverage_met: bool = False   # 连续 7 日覆盖率 >= 60%
    rejection_healthy: bool = False  # 帽拒率 < 20%
    cap_recommended: int = CAP_NORMAL
    total_audited: int = 0
    total_sampled: int = 0
    total_passed: int = 0
    total_failed: int = 0
    overall_rejection_rate: float = 0.0
    min_daily_coverage: float = 1.0
    notes: List[str] = field(default_factory=list)


def fetch_daily_audit_stats(t: S.Transport, *, days: int = SOAK_WINDOW_DAYS,
                            schema: str = "") -> List[DailyAuditStat]:
    """从 DB 拉取过去 N 日的审计统计。

    覆盖率口径：
      - 分子：当日 audited_at::date = 该日的行数
      - 分母：当日结束时可审候选总数（audit_verdict='' AND status='open' 的 JOIN）
        由于我们没有历史快照，用**当前**待审数 + 已审数 近似还原当日分母。
        这是 honest 近似——如果过去 7 天有大量新锚涌入，分母会被低估，
        覆盖率会被高估（保守方向：宁可报低不报高）。
    """
    t_log = S.q("demand_intake_log", schema)
    t_led = S.q("feature_ledger", schema)

    # 拉取过去 N 日每日审计行数
    audit_rows = t.query(f"""
        SELECT audited_at::date AS day, count(*) AS n
        FROM {t_log}
        WHERE audited_at IS NOT NULL
          AND audited_at >= current_date - INTERVAL '{int(days)} days'
        GROUP BY 1 ORDER BY 1
    """)
    audit_by_day = {str(r["day"]): int(r["n"]) for r in audit_rows}

    # 拉取过去 N 日每日抽样/通过/失败数（从 chronicle_snapshots 的 audit_run 事件）
    chron_rows = t.query(f"""
        SELECT (payload->>'at')::date AS day,
               (payload->>'sampled')::int AS sampled,
               (payload->>'passed')::int AS passed,
               (payload->>'failed')::int AS failed,
               (payload->>'dropped')::int AS dropped
        FROM {S.q('chronicle_snapshots', schema)}
        WHERE payload->>'kind' = 'demand_intake'
          AND payload->>'phase' = 'audit_run'
          AND (payload->>'at')::date >= current_date - INTERVAL '{int(days)} days'
        ORDER BY 1
    """)
    chron_by_day: Dict[str, Dict[str, int]] = {}
    for r in chron_rows:
        day = str(r["day"])
        chron_by_day[day] = {
            "sampled": int(r.get("sampled") or 0),
            "passed": int(r.get("passed") or 0),
            "failed": int(r.get("failed") or 0),
            "dropped": int(r.get("dropped") or 0),
        }

    # 当前待审候选总数（用于近似分母）
    pending_total = t.query(f"""
        SELECT count(*) AS n
        FROM {t_log} l
        JOIN {t_led} f ON f.slug = l.slug
        WHERE l.status = 'open' AND l.audit_verdict = '' AND f.status = 'open'
    """)
    pending_now = int(pending_total[0]["n"]) if pending_total else 0

    # 总已审数
    total_audited = t.query(f"""
        SELECT count(*) AS n FROM {t_log} WHERE audited_at IS NOT NULL
    """)
    audited_all = int(total_audited[0]["n"]) if total_audited else 0

    # 近似每日分母 = pending_now + (audited_all - 该日之前的已审数)
    # 简化：用 pending_now + audited_all 作为统一分母（保守近似）
    approx_denominator = pending_now + audited_all

    stats: List[DailyAuditStat] = []
    from datetime import date, timedelta
    today = date.today()
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        day_str = str(d)
        audited = audit_by_day.get(day_str, 0)
        chron = chron_by_day.get(day_str, {})
        stats.append(DailyAuditStat(
            day=day_str,
            audited=audited,
            pending=approx_denominator,
            sampled=chron.get("sampled", 0),
            passed=chron.get("passed", 0),
            failed=chron.get("failed", 0),
            dropped=chron.get("dropped", 0),
        ))
    return stats


def compute_soak_report(stats: List[DailyAuditStat], *,
                        window_days: int = SOAK_WINDOW_DAYS) -> SoakReport:
    """根据日统计计算七日浸泡报告。"""
    if not stats:
        return SoakReport(
            window_start="", window_end="",
            coverage_met=True, rejection_healthy=True,
            notes=["无审计数据（首日或审计未启动）"],
        )

    window = stats[-window_days:] if len(stats) >= window_days else stats
    total_audited = sum(s.audited for s in window)
    total_sampled = sum(s.sampled for s in window)
    total_passed = sum(s.passed for s in window)
    total_failed = sum(s.failed for s in window)

    # 覆盖率：连续 7 日 >= 60%
    daily_coverages = [s.coverage_rate for s in window]
    min_cov = min(daily_coverages) if daily_coverages else 1.0
    coverage_met = all(c >= COVERAGE_THRESHOLD for c in daily_coverages)

    # 帽拒率：7 日滚动
    overall_rej = total_failed / total_sampled if total_sampled > 0 else 0.0
    rejection_healthy = overall_rej < REJECTION_THRESHOLD

    # 帽推荐
    cap = CAP_NORMAL
    notes: List[str] = []
    if not coverage_met:
        cap = CAP_ESCALATED
        notes.append(f"覆盖率未达标（最低 {min_cov:.1%} < {COVERAGE_THRESHOLD:.0%}），"
                     f"审计帽上调至 {CAP_ESCALATED}/日")
    if not rejection_healthy:
        cap = CAP_ESCALATED
        notes.append(f"帽拒率偏高（{overall_rej:.1%} >= {REJECTION_THRESHOLD:.0%}），"
                     f"审计帽上调至 {CAP_ESCALATED}/日")
    if not notes:
        notes.append("各项指标健康，维持默认审计帽")

    return SoakReport(
        window_start=window[0].day,
        window_end=window[-1].day,
        daily_stats=window,
        coverage_met=coverage_met,
        rejection_healthy=rejection_healthy,
        cap_recommended=cap,
        total_audited=total_audited,
        total_sampled=total_sampled,
        total_passed=total_passed,
        total_failed=total_failed,
        overall_rejection_rate=round(overall_rej, 4),
        min_daily_coverage=round(min_cov, 4),
        notes=notes,
    )


def apply_cap(t: S.Transport, cap: int, *, schema: str = "") -> Dict[str, Any]:
    """把推荐审计帽写回 demand_mint_quota（今日行）。

    写策略：UPSERT 今日行（day=current_date, agent='soak-audit'），
    只更新 cap 字段，不碰 minted 计数。
    """
    tbl = S.q("demand_mint_quota", schema)
    t.execute(f"""
        INSERT INTO {tbl} (day, agent, minted, cap, updated_at)
        VALUES (current_date, 'soak-audit', 0, {int(cap)}, now())
        ON CONFLICT (day, agent) DO UPDATE
        SET cap = EXCLUDED.cap, updated_at = now()
    """)
    return {"cap_applied": cap, "agent": "soak-audit", "day": "current_date"}


def run_soak_audit(t: S.Transport, *, days: int = SOAK_WINDOW_DAYS,
                   apply_cap_flag: bool = False,
                   schema: str = "") -> SoakReport:
    """七日浸泡审计主入口。

    ``apply_cap_flag=True`` 时自动把推荐帽写回 demand_mint_quota。
    """
    stats = fetch_daily_audit_stats(t, days=days, schema=schema)
    report = compute_soak_report(stats, window_days=days)
    if apply_cap_flag:
        apply_cap(t, report.cap_recommended, schema=schema)
        S.chronicle(t, "soak_audit", {
            "window_start": report.window_start,
            "window_end": report.window_end,
            "coverage_met": report.coverage_met,
            "rejection_healthy": report.rejection_healthy,
            "cap_recommended": report.cap_recommended,
            "total_audited": report.total_audited,
            "total_sampled": report.total_sampled,
            "overall_rejection_rate": report.overall_rejection_rate,
            "min_daily_coverage": report.min_daily_coverage,
        }, schema=schema)
    return report


def report_to_dict(report: SoakReport) -> Dict[str, Any]:
    """把 SoakReport 序列化为 dict（用于 CLI 输出 / JSON 报告）。"""
    return {
        "window_start": report.window_start,
        "window_end": report.window_end,
        "coverage_met": report.coverage_met,
        "rejection_healthy": report.rejection_healthy,
        "cap_recommended": report.cap_recommended,
        "total_audited": report.total_audited,
        "total_sampled": report.total_sampled,
        "total_passed": report.total_passed,
        "total_failed": report.total_failed,
        "overall_rejection_rate": report.overall_rejection_rate,
        "min_daily_coverage": report.min_daily_coverage,
        "daily_stats": [
            {
                "day": s.day,
                "audited": s.audited,
                "pending": s.pending,
                "sampled": s.sampled,
                "passed": s.passed,
                "failed": s.failed,
                "dropped": s.dropped,
                "coverage_rate": round(s.coverage_rate, 4),
                "rejection_rate": round(s.rejection_rate, 4),
            }
            for s in report.daily_stats
        ],
        "notes": report.notes,
    }
