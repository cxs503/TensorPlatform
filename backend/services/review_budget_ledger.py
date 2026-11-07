"""审查预算账本（SPEC §6.5「审查分层与治理预算」的测量先行实现）。

设计约束（AOG SPEC v1.2 §6.1/§6.5）：
- 四类审查逐笔记账：Execution Audit / Technical Review / Outcome Assessment /
  Innovation Review，每条记录 **对象(subject) / 类型(review_type) / 耗时(duration_s) /
  结论(conclusion) / 审查者(reviewer)**，并带来源(source)与耗时来源(duration_source)；
- 本期**只测量不限额**：本模块不阻塞、不拒绝、不降级任何审查动作，只在周聚合里把
  治理活动占生产活动的比例与建议上限 CAP_RATIO(0.25) 并列输出，供后续限额决策取基线；
- 账目**只增不改**：文件以 append 模式写入，每条记录带 prev_hash/entry_hash 组成
  sha256 链，任何历史行被改写/删除都会由 verify_chain() 定位并拒绝继续追加；
- 豁免必须留痕（§6.5「不得静默免审」）：record_exemption() 与审查记录同一账本、同一链路；
- 零领域语义：本模块只认四类审查与通用记账字段，不出现任何领域专名；字段名/取值
  由调用方提供，夹具用合成域名。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REVIEW_TYPES: Tuple[str, ...] = ("execution", "technical", "outcome", "innovation")

REVIEW_TYPE_SPEC: Dict[str, str] = {
    "execution": "Execution Audit：是否真实执行、记录是否完整、资源与预算是否符合授权",
    "technical": "Technical Review：实现/设计/接口/测试/安全/可维护性",
    "outcome": "Outcome Assessment：结果是否达标、是否产生价值、局限是否披露",
    "innovation": "Innovation Review：新方法/新假设是否有对照实验与证据支持",
}

CONCLUSIONS: Tuple[str, ...] = (
    "approved",
    "approved_with_findings",
    "changes_requested",
    "rejected",
    "inconclusive",
    "exempted",
)

# 耗时来源分级：observed=原始记录自带时间戳可算；reported=由审查者申报；
# not_observable=只有事件发生、耗时不可观测（计入事件数，不计入分钟数）。
DURATION_SOURCES: Tuple[str, ...] = ("observed", "reported", "not_observable")

# §6.5 建议上限：治理消耗占同期生产预算 ≤25%（本期只并列输出，不做强制）。
CAP_RATIO: float = 0.25
# 分钟数口径下，耗时覆盖率低于该阈值时周报必须显式标注「不足以判定限额」。
MIN_DURATION_COVERAGE: float = 0.5

KIND_REVIEW = "review"
KIND_EXEMPTION = "exemption"
KIND_PRODUCTION = "production"


class LedgerValidationError(ValueError):
    """记账字段不合法（类型/结论/耗时/必填项）。"""


class LedgerImmutabilityError(RuntimeError):
    """账本链已损坏（历史行被改写/删除），拒绝继续写入。"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def isoformat(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat(timespec="seconds")


def _canonical(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def default_ledger_path() -> Path:
    """账本默认落盘位置：可在部署侧用 TP_REVIEW_BUDGET_LEDGER 覆盖。"""
    override = os.getenv("TP_REVIEW_BUDGET_LEDGER")
    if override:
        return Path(override)
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "artifacts" / "review_budget_ledger.jsonl"


def week_window(week_start: Any) -> Tuple[datetime, datetime]:
    """把任意时间落到其 ISO 周（周一 00:00 UTC 起）的 [start, end) 窗口。"""
    ts = _parse_ts(week_start)
    if ts is None:
        raise LedgerValidationError(f"无法解析周起始时间: {week_start!r}")
    monday = ts.astimezone(timezone.utc)
    monday = (monday - timedelta(days=monday.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday, monday + timedelta(days=7)


class ReviewBudgetLedger:
    """只增不改的审查预算账本（JSONL + sha256 链）。"""

    def __init__(self, path: Optional[Any] = None, clock: Any = None) -> None:
        self.path = Path(path) if path is not None else default_ledger_path()
        self._clock = clock or _utcnow
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ---------------- 读 ----------------
    def entries(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:  # 损坏行即视为篡改证据
                    raise LedgerImmutabilityError(
                        f"账本 {self.path} 第 {len(rows) + 1} 行不是合法 JSON: {exc}"
                    ) from exc
        return rows

    def verify_chain(self) -> Dict[str, Any]:
        rows = self.entries()
        prev = "0" * 64
        for index, row in enumerate(rows):
            body = {k: v for k, v in row.items() if k != "entry_hash"}
            expected = _sha256(prev + _canonical(body))
            if row.get("prev_hash") != prev or row.get("entry_hash") != expected:
                return {
                    "ok": False,
                    "entries": len(rows),
                    "broken_at": index,
                    "expected_prev": prev,
                    "expected_hash": expected,
                    "found_prev": row.get("prev_hash"),
                    "found_hash": row.get("entry_hash"),
                }
            prev = row["entry_hash"]
        return {"ok": True, "entries": len(rows), "broken_at": None, "head_hash": prev}

    # ---------------- 写 ----------------
    def _append(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            state = self.verify_chain()
            if not state["ok"]:
                raise LedgerImmutabilityError(
                    f"账本链在第 {state['broken_at']} 条处损坏，拒绝继续追加（只增不改）"
                )
            prev = state.get("head_hash") or "0" * 64
            row = dict(payload)
            row["seq"] = state["entries"]
            row["prev_hash"] = prev
            row["entry_hash"] = _sha256(prev + _canonical(row))
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(_canonical(row) + "\n")
            return row

    def _base(self, kind: str, subject: str, reviewer: str, recorded_at: Any) -> Dict[str, Any]:
        if not isinstance(subject, str) or not subject.strip():
            raise LedgerValidationError("subject(审查对象) 必须是非空字符串")
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise LedgerValidationError("reviewer(审查者) 必须是非空字符串")
        ts = _parse_ts(recorded_at) if recorded_at is not None else self._clock()
        if ts is None:
            raise LedgerValidationError(f"recorded_at 无法解析: {recorded_at!r}")
        return {
            "kind": kind,
            "subject": subject.strip(),
            "reviewer": reviewer.strip(),
            "recorded_at": isoformat(ts),
        }

    def record_review(
        self,
        review_type: str,
        subject: str,
        duration_s: float,
        conclusion: str,
        reviewer: str,
        *,
        duration_source: str = "reported",
        evidence: Optional[Dict[str, Any]] = None,
        recorded_at: Any = None,
    ) -> Dict[str, Any]:
        if review_type not in REVIEW_TYPES:
            raise LedgerValidationError(
                f"review_type 必须是 {REVIEW_TYPES} 之一，收到 {review_type!r}"
            )
        if conclusion not in CONCLUSIONS or conclusion == "exempted":
            allowed = tuple(c for c in CONCLUSIONS if c != "exempted")
            raise LedgerValidationError(f"conclusion 必须是 {allowed} 之一，收到 {conclusion!r}")
        if duration_source not in DURATION_SOURCES:
            raise LedgerValidationError(
                f"duration_source 必须是 {DURATION_SOURCES} 之一，收到 {duration_source!r}"
            )
        duration = _validate_duration(duration_s)
        if duration_source == "not_observable" and duration != 0.0:
            raise LedgerValidationError("duration_source=not_observable 时 duration_s 必须为 0")
        row = self._base(KIND_REVIEW, subject, reviewer, recorded_at)
        row.update(
            {
                "review_type": review_type,
                "duration_s": duration,
                "duration_source": duration_source,
                "conclusion": conclusion,
                "evidence": evidence or {},
            }
        )
        return self._append(row)

    def record_exemption(
        self,
        subject: str,
        reason: str,
        reviewer: str,
        *,
        review_type: str = "outcome",
        duration_s: float = 0.0,
        recorded_at: Any = None,
    ) -> Dict[str, Any]:
        """豁免留痕（§6.5：不得静默免审）。未审项与其原因进同一账本、同一链。"""
        if review_type not in REVIEW_TYPES:
            raise LedgerValidationError(f"review_type 必须是 {REVIEW_TYPES} 之一")
        if not isinstance(reason, str) or not reason.strip():
            raise LedgerValidationError("豁免必须写明 reason，禁止静默免审")
        row = self._base(KIND_EXEMPTION, subject, reviewer, recorded_at)
        row.update(
            {
                "review_type": review_type,
                "duration_s": _validate_duration(duration_s),
                "duration_source": "reported" if duration_s else "not_observable",
                "conclusion": "exempted",
                "evidence": {"reason": reason.strip()},
            }
        )
        return self._append(row)

    def record_production(
        self,
        subject: str,
        duration_s: float,
        *,
        actor: str,
        duration_source: str = "observed",
        evidence: Optional[Dict[str, Any]] = None,
        recorded_at: Any = None,
    ) -> Dict[str, Any]:
        """生产活动（治理比例的分母）。口径与审查一致，便于直接相除。"""
        if duration_source not in DURATION_SOURCES:
            raise LedgerValidationError(f"duration_source 必须是 {DURATION_SOURCES} 之一")
        duration = _validate_duration(duration_s)
        if duration_source == "not_observable" and duration != 0.0:
            raise LedgerValidationError("duration_source=not_observable 时 duration_s 必须为 0")
        row = self._base(KIND_PRODUCTION, subject, actor, recorded_at)
        row.update(
            {
                "review_type": None,
                "duration_s": duration,
                "duration_source": duration_source,
                "conclusion": None,
                "evidence": evidence or {},
            }
        )
        return self._append(row)


def _validate_duration(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LedgerValidationError(f"duration_s 必须是数字（秒），收到 {value!r}")
    value = float(value)
    if value < 0 or value != value or value in (float("inf"), float("-inf")):
        raise LedgerValidationError(f"duration_s 必须有限且非负，收到 {value!r}")
    return round(value, 3)


def aggregate_window(entries: Sequence[Dict[str, Any]], week_start: Any) -> Dict[str, Any]:
    """把账本条目聚合为「治理活动 / 生产活动」比例报告（只测量，不限额）。"""
    start, end = week_window(week_start)
    in_window = [e for e in entries if _in_window(e, start, end)]

    per_type: Dict[str, Dict[str, Any]] = {
        review_type: {
            "review_type": review_type,
            "spec": REVIEW_TYPE_SPEC[review_type],
            "reviews": 0,
            "exemptions": 0,
            "duration_s": 0.0,
            "observed_duration_s": 0.0,
            "conclusions": {},
            "reviewers": [],
            "subjects": [],
        }
        for review_type in REVIEW_TYPES
    }

    governance_events = 0
    governance_duration = 0.0
    governance_observed_events = 0
    exemptions = 0
    production_events = 0
    production_duration = 0.0

    for entry in in_window:
        kind = entry.get("kind")
        duration = float(entry.get("duration_s") or 0.0)
        if kind == KIND_PRODUCTION:
            production_events += 1
            production_duration += duration
            continue
        if kind not in (KIND_REVIEW, KIND_EXEMPTION):
            continue
        bucket = per_type.get(str(entry.get("review_type")))
        if bucket is None:
            continue
        governance_events += 1
        governance_duration += duration
        if entry.get("duration_source") in ("observed", "reported") and duration > 0:
            governance_observed_events += 1
            bucket["observed_duration_s"] += duration
        bucket["duration_s"] += duration
        bucket["subjects"].append(entry.get("subject"))
        reviewer = entry.get("reviewer")
        if reviewer and reviewer not in bucket["reviewers"]:
            bucket["reviewers"].append(reviewer)
        if kind == KIND_EXEMPTION:
            exemptions += 1
            bucket["exemptions"] += 1
        else:
            bucket["reviews"] += 1
            conclusion = str(entry.get("conclusion"))
            bucket["conclusions"][conclusion] = bucket["conclusions"].get(conclusion, 0) + 1

    review_events = governance_events - exemptions
    ratios: Dict[str, Any] = {
        "cap_ratio_spec": CAP_RATIO,
        "cap_status": "measured",
        "cap_note": "本期只测量不限额；比例与建议上限并列输出，不触发任何降级/拒绝。",
    }
    if production_duration > 0:
        minute_ratio = governance_duration / production_duration
        ratios["duration_ratio_governance_over_production"] = round(minute_ratio, 6)
        if governance_events:
            coverage = governance_observed_events / governance_events
        else:
            coverage = 0.0
        ratios["governance_duration_coverage"] = round(coverage, 4)
        if coverage < MIN_DURATION_COVERAGE:
            ratios["cap_status"] = "insufficient_duration_coverage"
        ratios["over_cap_by_minutes"] = minute_ratio > CAP_RATIO
    else:
        ratios["duration_ratio_governance_over_production"] = None
        ratios["governance_duration_coverage"] = 0.0
        ratios["cap_status"] = "no_production_activity"
        ratios["over_cap_by_minutes"] = None

    if production_events:
        ratios["event_ratio_governance_over_production"] = round(
            governance_events / production_events, 6
        )
        ratios["event_ratio_reviews_only"] = round(review_events / production_events, 6)
    else:
        ratios["event_ratio_governance_over_production"] = None
        ratios["event_ratio_reviews_only"] = None

    total_activity_duration = governance_duration + production_duration
    ratios["governance_share_of_total_duration"] = (
        round(governance_duration / total_activity_duration, 6)
        if total_activity_duration > 0
        else None
    )

    for bucket in per_type.values():
        bucket["duration_s"] = round(bucket["duration_s"], 3)
        bucket["observed_duration_s"] = round(bucket["observed_duration_s"], 3)
        bucket["subjects"] = bucket["subjects"][:20]

    return {
        "week_start": isoformat(start),
        "week_end": isoformat(end),
        "iso_week": f"{start.isocalendar()[0]}-W{start.isocalendar()[1]:02d}",
        "window_entries": len(in_window),
        "governance": {
            "events": governance_events,
            "reviews": review_events,
            "exemptions": exemptions,
            "duration_s": round(governance_duration, 3),
            "per_type": [per_type[t] for t in REVIEW_TYPES],
        },
        "production": {
            "events": production_events,
            "duration_s": round(production_duration, 3),
        },
        "ratios": ratios,
        "policy": "SPEC §6.5 测量先行：只记账不限额，豁免必须留痕。",
    }


def _in_window(entry: Dict[str, Any], start: datetime, end: datetime) -> bool:
    ts = _parse_ts(entry.get("recorded_at"))
    return ts is not None and start <= ts < end


def current_week_start(now: Any = None) -> datetime:
    ts = _parse_ts(now) if now is not None else _utcnow()
    return week_window(ts)[0]


def latest_complete_week_start(now: Any = None) -> datetime:
    """最近一个已结束的 ISO 周（周报例行窗口）。"""
    return current_week_start(now) - timedelta(days=7)


def render_weekly_markdown(report: Dict[str, Any]) -> str:
    """周报 markdown（机器生成，零手抄数字）。"""
    gov = report["governance"]
    prod = report["production"]
    ratios = report["ratios"]
    lines: List[str] = []
    lines.append(f"# 审查预算周报 {report['iso_week']}")
    lines.append("")
    lines.append(
        f"- 窗口：`{report['week_start']}` → `{report['week_end']}`（UTC，ISO 周 {report['iso_week']}）"
    )
    lines.append(f"- 窗口内账目条目：{report['window_entries']}")
    lines.append(
        f"- 治理活动：{gov['events']} 事件（审查 {gov['reviews']} + 豁免 {gov['exemptions']}），"
        f"累计 {gov['duration_s']:.1f} s"
    )
    lines.append(f"- 生产活动：{prod['events']} 事件，累计 {prod['duration_s']:.1f} s")
    lines.append("")
    lines.append("## 四类审查分布（SPEC §6.1）")
    lines.append("")
    lines.append("| 类型 | 审查数 | 豁免数 | 耗时 s | 可观测耗时 s | 结论分布 | 审查者 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for bucket in gov["per_type"]:
        conclusions = ", ".join(
            f"{k}={v}" for k, v in sorted(bucket["conclusions"].items())
        ) or "-"
        reviewers = ", ".join(bucket["reviewers"]) or "-"
        lines.append(
            f"| {bucket['review_type']} | {bucket['reviews']} | {bucket['exemptions']} | "
            f"{bucket['duration_s']:.1f} | {bucket['observed_duration_s']:.1f} | "
            f"{conclusions} | {reviewers} |"
        )
    lines.append("")
    lines.append("## 治理 / 生产比例（只测量，不限额）")
    lines.append("")
    duration_ratio = ratios["duration_ratio_governance_over_production"]
    lines.append(
        "| 指标 | 值 | 说明 |\n| --- | --- | --- |\n"
        f"| 分钟口径比例 | {('%.4f' % duration_ratio) if duration_ratio is not None else 'n/a'} | "
        "治理耗时 / 生产耗时（§6.5 目标 ≤25%） |\n"
        f"| 事件口径比例 | "
        f"{('%.4f' % ratios['event_ratio_governance_over_production']) if ratios['event_ratio_governance_over_production'] is not None else 'n/a'} | "
        "治理事件 / 生产事件 |\n"
        f"| 仅审查事件比例 | "
        f"{('%.4f' % ratios['event_ratio_reviews_only']) if ratios['event_ratio_reviews_only'] is not None else 'n/a'} | "
        "剔除豁免后的事件口径 |\n"
        f"| 耗时覆盖率 | {ratios['governance_duration_coverage']:.4f} | "
        "有可观测/申报耗时的治理事件占比 |\n"
        f"| 建议上限 | {ratios['cap_ratio_spec']:.2f} | SPEC §6.5 建议值 |\n"
        f"| 限额判定状态 | `{ratios['cap_status']}` | 覆盖率不足时不得据此判定达标 |"
    )
    lines.append("")
    lines.append(f"> {ratios['cap_note']}")
    lines.append("")
    lines.append(f"> {report['policy']}")
    return "\n".join(lines) + "\n"


def build_weekly_report(
    ledger: Optional[ReviewBudgetLedger] = None, week_start: Any = None
) -> Dict[str, Any]:
    ledger = ledger or ReviewBudgetLedger()
    start = week_window(week_start)[0] if week_start is not None else latest_complete_week_start()
    report = aggregate_window(ledger.entries(), start)
    report["ledger"] = {"path": str(ledger.path), "chain": ledger.verify_chain()}
    return report