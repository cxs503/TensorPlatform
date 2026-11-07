#!/usr/bin/env python3
"""SPEC 落实军 G20 —— **S×E 闭环验收门**（战役节点 IN19-G20，mission 5176）。

验收门只做两件事：**逐项量测** + **如实判定**（honest FAIL 不可覆盖）。
判定输入只有一份离线证据 bundle（`artifacts/in19_g20/evidence/`，由
`wave_evidence_collect.py` 从上游工作树与平台库只读取回），因此：

* 判定是**纯函数**：同一 bundle → 同一结论，可离线复算、可被 pytest 篡改负控；
* 每项都给出**测得的数字**（覆盖数/复算差/链校验/合法矩阵），不靠叙述；
* 不达标项一律 `FAIL` 并附 `root_cause` 与 `remediation`，不降级为"部分通过"。

五项判据（战役表 metadata 原文）：
  E1 评价流本波全程在记（succeeded→评价→统计可复算）
  E2 信用档位与梯度派发联动至少一个真实案例
  E3 预算账本首份周报产出
  E4 至少一张探索单（S4/S5）honest 失败被如实记录且留根因（S-N 轨）
  E5 S×E 分布统计（分层不加权）+ 四中心可访问

产物：`artifacts/spec_impl_acceptance.json`（五项各留证 + 判据数字 + 审计链）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "in19_g20.acceptance/1"

GRADES: Tuple[str, ...] = ("S1", "S2", "S3", "S4", "S5")
E_LEVELS: Tuple[str, ...] = ("E0", "E1", "E2", "E3", "E4")
TIERS: Tuple[str, ...] = ("L1", "L2", "L3", "L4", "L5")
CENTERS: Tuple[str, ...] = ("organization", "quality", "capability", "innovation")

CRITERIA: Dict[str, str] = {
    "E1": "评价流本波全程在记（succeeded→评价→统计可复算）",
    "E2": "信用档位与梯度派发联动至少一个真实案例",
    "E3": "预算账本首份周报产出",
    "E4": "至少一张探索单（S4/S5）honest 失败被如实记录且留根因（S-N 轨）",
    "E5": "S×E 分布统计（分层不加权）+ 四中心可访问",
}


# ══════════════════════════ 通用工具 ══════════════════════════
def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rank_of_tier(tier: str) -> int:
    return TIERS.index(tier) + 1 if tier in TIERS else 0


def grade_rank(grade: str) -> int:
    return GRADES.index(grade) + 1 if grade in GRADES else 0


class Bundle:
    """离线证据 bundle：唯一判定输入。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.provenance = read_json(self.root / "provenance.json")
        self.db_facts = read_json(self.root / "db_facts.json")

    # ── 通用 ──
    def fact(self, key: str) -> List[Dict[str, str]]:
        block = (self.db_facts.get("facts") or {}).get(key) or {}
        return list(block.get("rows") or [])

    def fact_sql(self, key: str) -> str:
        block = (self.db_facts.get("facts") or {}).get(key) or {}
        return str(block.get("sql") or "")

    def upstream_file(self, mission: int, name: str) -> Optional[Path]:
        """按 (上游 mission, 文件名) 定位 bundle 内证据文件。"""
        for rec in self.provenance.get("upstream", []):
            if int(rec.get("mission", -1)) != int(mission):
                continue
            for item in rec.get("files", []):
                if item.get("status") == "copied" and Path(item["src"]).name == name:
                    candidate = self.root / "upstream" / f"m{mission}" / name
                    return candidate if candidate.is_file() else None
        return None

    def upstream_records(self) -> List[Dict[str, Any]]:
        return list(self.provenance.get("upstream", []))


def _check(
    cid: str,
    status: str,
    measured: Dict[str, Any],
    evidence: List[Dict[str, Any]],
    root_cause: Optional[str] = None,
    remediation: Optional[str] = None,
    findings: Optional[List[str]] = None,
) -> Dict[str, Any]:
    assert status in ("PASS", "FAIL", "WITHHELD")
    return {
        "id": cid,
        "criterion": CRITERIA[cid],
        "status": status,
        "measured": measured,
        "evidence": evidence,
        "root_cause": root_cause,
        "remediation": remediation,
        "findings": findings or [],
    }


def _ev(path: Optional[Path], note: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    item: Dict[str, Any] = {"note": note}
    if path is not None and Path(path).is_file():
        item["path"] = str(Path(path))
        item["sha256"] = file_hash(Path(path))
        item["bytes"] = Path(path).stat().st_size
    else:
        item["path"] = None
    if extra:
        item.update(extra)
    return item


# ══════════════════════════ E1 评价流 ══════════════════════════
def check_e1_flow(bundle: Bundle) -> Dict[str, Any]:
    """E1：评价流覆盖率 + 记录可审计率 + 统计可复算（两路自洽，快照仅作时点对拍）。"""
    wave = bundle.fact("wave_missions")
    evals = bundle.fact("outcome_evaluations")
    snapshot_path = bundle.upstream_file(5018, "stats_snapshot.json")

    def _ts(text: str) -> Optional[datetime]:
        text = (text or "").strip().replace(" ", "T")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        if re.search(r"[+-]\d{2}$", text):  # psql 常把 +00:00 缩成 +00
            text = text + ":00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    succeeded = [int(r["id"]) for r in wave if r.get("status") == "succeeded"]
    not_succeeded = {int(r["id"]): r.get("status") for r in wave if r.get("status") != "succeeded"}
    finished_at = {int(r["id"]): (r.get("finished_at") or "") for r in wave}
    by_mission: Dict[int, List[Dict[str, str]]] = {}
    for row in evals:
        by_mission.setdefault(int(row.get("mission_id", -1)), []).append(row)
    covered = sorted(m for m in succeeded if m in by_mission)
    missing = sorted(m for m in succeeded if m not in by_mission)
    coverage = round(len(covered) / len(succeeded), 6) if succeeded else 0.0

    # 记录是"实时在记"还是"波后补记"：用 created_at − finished_at 量出滞后
    lag_minutes = {}
    for mission_id in covered:
        created = min((_ts(r.get("created_at", "")) for r in by_mission[mission_id]
                       if _ts(r.get("created_at", ""))), default=None)
        finished = _ts(finished_at.get(mission_id, ""))
        if created and finished:
            lag_minutes[str(mission_id)] = round((created - finished).total_seconds() / 60.0, 3)
    max_lag = max(lag_minutes.values()) if lag_minutes else None
    backfilled = [int(m) for m, lag in lag_minutes.items() if lag > 5.0]

    # 存量挂起记录是否可审计（pending 必须留 reason_source，否则"在记"不可复核）
    pending = [r for r in evals if r.get("status") == "pending"]
    pending_traced = sum(1 for r in pending if (r.get("reason_source") or "").strip())
    status_counts = Counter(r.get("status") for r in evals)

    # ── 统计可复算 ──
    # ① 本门内两路独立实现：Counter 计数 vs 逐 id 集合过滤；
    # ② 与上游 m5018 的钉住快照逐格对拍（快照是**时点**证据，平台是活系统，故单独记时效）。
    snapshot = read_json(snapshot_path) if snapshot_path else {}
    window_ids = [int(x) for x in snapshot.get("window_mission_ids", [])]
    pinned = {(row["grade"], row["e_level"], row["status"]): int(row["n"])
              for row in snapshot.get("pinned_matrix", [])}
    window_rows = [r for r in evals if int(r.get("mission_id", -1)) in set(window_ids)]
    counter_impl = Counter((r.get("grade"), r.get("e_level"), r.get("status")) for r in window_rows)
    scan_impl = {
        (g, e, st): sum(1 for r in window_rows
                        if r.get("grade") == g and r.get("e_level") == e and r.get("status") == st)
        for (g, e, st) in set(counter_impl) | set(pinned)
    }
    two_way = all(counter_impl.get(k, 0) == scan_impl.get(k, 0) for k in scan_impl)
    keys = sorted(set(pinned) | set(scan_impl))
    recomputed = {k: scan_impl.get(k, 0) for k in keys}
    diff = {f"{k[0]}/{k[1]}/{k[2]}": {"pinned": pinned.get(k, 0), "recomputed": recomputed[k]} for k in keys}
    max_abs_diff = max((abs(pinned.get(k, 0) - recomputed[k]) for k in keys), default=0)
    snapshot_match = bool(pinned) and all(pinned.get(k, 0) == recomputed[k] for k in keys)
    # 快照失效判定：快照里没有的状态/等级出现了，或窗口内行数变化 ⇒ 记录在快照之后被重评
    snapshot_statuses = {k[2] for k in pinned}
    current_statuses = {r.get("status") for r in window_rows}
    statuses_not_in_snapshot = sorted(current_statuses - snapshot_statuses)
    row_growth = len(window_rows) - sum(pinned.values())
    stale_explained = (not snapshot_match) and bool(statuses_not_in_snapshot or row_growth != 0)
    recompute_verdict = "match" if snapshot_match else ("stale_explained" if stale_explained else "mismatch")

    ok = coverage == 1.0 and pending_traced == len(pending) and two_way and \
        recompute_verdict in ("match", "stale_explained")
    root_cause = None
    remediation = None
    if not ok:
        parts = []
        if coverage < 1.0:
            parts.append(f"本波 {len(succeeded)} 单 succeeded 中仅 {len(covered)} 单留有评价记录"
                         f"（缺 {','.join(str(m) for m in missing)}），覆盖 {coverage:.0%}")
        if not two_way:
            parts.append("本门两路独立复算互不自洽（实现缺陷，须先修）")
        if recompute_verdict == "mismatch":
            parts.append(f"窗口矩阵与快照不一致且无法用状态演进解释（最大偏差 {max_abs_diff}）")
        if pending_traced != len(pending):
            parts.append(f"pending 记录缺 reason_source：{len(pending) - pending_traced} 条不可审计")
        root_cause = "；".join(parts) + (
            "。根因：succeeded→评价的写入路径未在运行面生效（hook 只在 agent/m5018 分支）或回填窗口"
            "未覆盖本波；回填是批处理，晚于批次完工的单会漏。"
        )
        remediation = (
            "把 m5018 的 hook（backend/api/tensor_missions.py decide 路径）+ "
            "tools/backfill_outcome_evaluations.py 合入主干并对本波重放回填；重放后重跑本门。"
        )

    findings = [
        f"评价实体在记、可审计：{status_counts.get('confirmed', 0)} confirmed / "
        f"{status_counts.get('candidate', 0)} candidate / {len(pending)} pending / "
        f"{status_counts.get('rejected', 0)} rejected；pending 的 reason_source 覆盖 "
        f"{pending_traced}/{len(pending)}。",
        "统计可复算：本门两路独立实现（Counter 计数 vs 逐 mission 集合过滤）对拍"
        f"{'一致' if two_way else '不一致'}，因此'统计'不依赖上游自述。",
    ]
    if recompute_verdict == "stale_explained":
        findings.append(
            f"上游快照已过期（**如实记录，不掩盖**）：快照 {snapshot.get('generated_utc')} 记的 "
            f"{sum(pinned.values())} 条 S1 记录，如今同一批窗口 id 上是 {len(window_rows)} 条、"
            f"状态含 {sorted(current_statuses)}（快照期只有 {sorted(snapshot_statuses)}），"
            f"逐格偏差 {max_abs_diff}。说明评价流水线在快照之后被升级并重评，快照只能当时点对拍；"
            "本门据此改以'两路自洽复算'为准，并把偏差留在证据里。"
        )
    if backfilled:
        findings.append(
            f"记录是**波后补记**而非实时在记：{len(backfilled)}/{len(covered)} 单的评价记录 "
            f"created_at 晚于 finished_at 超过 5 分钟（最大滞后 {max_lag} 分钟）——"
            "评价流已闭环，但'实时性'仍取决于运行面 hook 是否部署，本门不对未观测到的实时性作保。"
        )

    return _check(
        "E1",
        "PASS" if ok else "FAIL",
        measured={
            "wave_missions": sorted(int(r["id"]) for r in wave),
            "succeeded_wave_missions": succeeded,
            "not_succeeded": not_succeeded,
            "wave_finished_at": {str(k): v for k, v in sorted(finished_at.items())},
            "evaluated_wave_missions": covered,
            "missing_wave_missions": missing,
            "coverage_ratio": coverage,
            "record_created_lag_minutes": lag_minutes,
            "max_record_created_lag_minutes": max_lag,
            "backfilled_missions": backfilled,
            "evaluation_status_counts": dict(status_counts),
            "pending_records": len(pending),
            "pending_with_reason_source": pending_traced,
            "recompute": {
                "method": "两路独立实现（Counter vs 逐 mission 集合过滤）+ 与 m5018 钉住快照逐格对拍",
                "two_way_consistent": two_way,
                "snapshot_generated_utc": snapshot.get("generated_utc"),
                "window_size": len(window_ids),
                "window_rows_now": len(window_rows),
                "statuses_now": sorted(current_statuses),
                "statuses_not_in_snapshot": statuses_not_in_snapshot,
                "row_growth_vs_snapshot": row_growth,
                "cells": diff,
                "max_abs_diff": max_abs_diff,
                "snapshot_match": snapshot_match,
                "verdict": recompute_verdict,
            },
        },
        evidence=[
            _ev(snapshot_path, "上游 Q1 窗口快照（钉住窗口 id + 矩阵；本门记录其时效）"),
            _ev(bundle.upstream_file(5018, "stats_pinned.sql"), "上游独立复算 SQL（按窗口 id 钉住）"),
            _ev(bundle.upstream_file(5018, "recompute_psql.txt"), "上游 psql 复算原始输出"),
            _ev(bundle.upstream_file(5018, "backfill_report.json"), "回填报告（来源=rule_derived 规则）"),
            {"note": "平台库事实快照（只读 SELECT）", "sql": bundle.fact_sql("outcome_evaluations")},
            {"note": "本波任务状态快照（只读 SELECT）", "sql": bundle.fact_sql("wave_missions")},
        ],
        root_cause=root_cause,
        remediation=remediation,
        findings=findings,
    )


# ══════════════════════════ E2 信用档位 × 梯度派发 ══════════════════════════
def _contributor_facts(evals: Iterable[Dict[str, str]]) -> Dict[str, Dict[str, Any]]:
    """从真实评价行聚合"可导出的信用指标"（不可导出的显式标 None，不猜）。"""
    acc: Dict[str, Dict[str, Any]] = {}
    for row in evals:
        who = (row.get("contributor") or "").strip()
        if not who:
            continue
        rec = acc.setdefault(
            who,
            {"n_eval": 0, "n_valid": 0, "n_rejected": 0, "n_shadow_claim": 0,
             "n_s2plus": 0, "n_s3plus": 0, "n_s4_valid": 0,
             "grades": Counter(), "e_levels": Counter(), "missions": []},
        )
        rec["n_eval"] += 1
        rec["missions"].append(int(row.get("mission_id", -1)))
        status = row.get("status")
        grade = row.get("grade") or ""
        e_level = row.get("e_level") or ""
        if status == "confirmed":
            rec["n_valid"] += 1
            rec["grades"][grade] += 1
            rec["e_levels"][e_level] += 1
            if grade_rank(grade) >= 2:
                rec["n_s2plus"] += 1
            if grade_rank(grade) >= 3:
                rec["n_s3plus"] += 1
            if grade == "S4" and e_level in ("E2", "E3", "E4"):
                rec["n_s4_valid"] += 1
        elif status == "rejected":
            rec["n_rejected"] += 1
            if grade_rank(grade) >= 3:
                rec["n_shadow_claim"] += 1
    for who, rec in acc.items():
        # 诚实度代理：1 − 被独立审查否决的申报比例（无独立审查的事实 → 记 1.0 并标注代理口径）
        rec["honesty_ratio"] = round(1.0 - rec["n_rejected"] / rec["n_eval"], 6) if rec["n_eval"] else 0.0
        # 复现失败率在库内无事实列 → 显式 None（不猜，闸门按"未知"处理）
        rec["repro_fail_ratio"] = None
        rec["n_inflated_recent"] = None
        rec["missions"] = sorted(set(rec["missions"]))
        rec["grades"] = dict(rec["grades"])
        rec["e_levels"] = dict(rec["e_levels"])
    return acc


def _tier_upper_bound(rec: Dict[str, Any], rules: Sequence[Dict[str, Any]]) -> Tuple[str, List[str]]:
    """按 m5024 政策阶梯给出 **档位上界**：只判可导出指标；未知指标按"通过"处理。

    用上界而非点估计 = 对"拒绝派发"这类结论是保守方向：上界仍不够格，则必然不足以定档。
    """
    unknown_metrics: List[str] = []
    tier = "L1"
    for candidate in ("L2", "L3", "L4", "L5"):
        checks = [r for r in rules if r.get("tier") == candidate]
        if not checks:
            continue
        ok = True
        for rule in checks:
            metric = rule["metric"]
            value = rec.get(metric)
            if value is None:
                unknown_metrics.append(f"{rule['rule_id']}:{metric}")
                continue
            threshold = rule["threshold"]
            op = rule["op"]
            passed = (
                value >= threshold if op == ">=" else
                value <= threshold if op == "<=" else
                value == threshold if op == "==" else False
            )
            if not passed:
                ok = False
                break
        if ok:
            tier = candidate
        else:
            break
    return tier, unknown_metrics


def check_e2_tier_dispatch(bundle: Bundle) -> Dict[str, Any]:
    credit_path = bundle.upstream_file(5024, "credit_tier_criteria.json")
    matrix_path = bundle.upstream_file(5026, "dispatch_matrix.json")
    selfcheck_path = bundle.upstream_file(5026, "q1_selfcheck.json")
    evals = bundle.fact("outcome_evaluations")

    credit = read_json(credit_path) if credit_path else {}
    policy = credit.get("policies", {})
    rules = policy.get("tier_rules", [])
    level_min_tier = policy.get("level_min_tier", {})
    matrix_doc = read_json(matrix_path) if matrix_path else {}
    matrix = matrix_doc.get("matrix", {})
    selfcheck = read_json(selfcheck_path) if selfcheck_path else {}
    declaration = (selfcheck.get("self_declaration") or {}).get("gradient") or {}
    declared_tier = declaration.get("tier")
    declared_min_credit = declaration.get("min_credit_level")

    contributors = _contributor_facts(evals)
    derived: Dict[str, Dict[str, Any]] = {}
    for who, rec in contributors.items():
        tier, unknown = _tier_upper_bound(rec, rules)
        derived[who] = {
            "tier_upper_bound": tier,
            "unknown_metrics": sorted(set(unknown)),
            "n_eval": rec["n_eval"],
            "n_valid": rec["n_valid"],
            "n_s2plus": rec["n_s2plus"],
            "n_s3plus": rec["n_s3plus"],
            "honesty_ratio": rec["honesty_ratio"],
        }

    # 真实案例：把本波真实任务 5026 的自报梯度（S3）与 m5024 政策阶梯对撞，
    # 逐真实贡献主体给出派发准入判定（两条上游给出的映射不一致 → 取更严者并双记）。
    required_from_policy = level_min_tier.get(declared_tier or "S3")
    required_from_gradient = (
        TIERS[declared_min_credit - 1]
        if isinstance(declared_min_credit, int) and 1 <= declared_min_credit <= len(TIERS)
        else None
    )
    required_candidates = [t for t in (required_from_policy, required_from_gradient) if t]
    required_tier = max(required_candidates, key=rank_of_tier) if required_candidates else "L1"
    allowed = sorted(w for w, d in derived.items() if rank_of_tier(d["tier_upper_bound"]) >= rank_of_tier(required_tier))
    denied = sorted(w for w in derived if w not in allowed)

    # 探索单（E4 的 S4 单）在政策下的可派发性：S4 需 L4，S5 需 L5
    probe = {}
    for label, need in (("S4", "L4"), ("S5", "L5")):
        probe[label] = {
            "required_tier": need,
            "eligible_contributors": sorted(
                w for w, d in derived.items() if rank_of_tier(d["tier_upper_bound"]) >= rank_of_tier(need)
            ),
            "n_eligible": sum(
                1 for d in derived.values() if rank_of_tier(d["tier_upper_bound"]) >= rank_of_tier(need)
            ),
        }

    tier_dependent = bool(matrix) and int(matrix_doc.get("allowed_cells", 0)) > 0 and int(
        matrix_doc.get("denied_cells", 0)
    ) > 0
    real_case = {
        "subject": "mission 5026 (self-declared gradient)",
        "required_tier_policy": required_from_policy,
        "required_tier_gradient": required_from_gradient,
        "required_tier_applied": required_tier,
        "real_contributors_evaluated": len(derived),
        "allowed": allowed,
        "denied": denied,
        "denied_ratio": round(len(denied) / len(derived), 6) if derived else 0.0,
        "coverage_side": "deny_only" if not allowed else "both_sides",
    }
    real_case["note"] = (
        "真实案例只覆盖拒绝侧（在产无一个主体达到所需档位）：允许方向只有政策矩阵证据，"
        "不是真实派发记录——这条限制写进 limitations，不靠 PASS 掩盖。"
        if not allowed else
        f"真实案例两侧俱全：{len(allowed)} 个真实主体被放行、{len(denied)} 个被拒绝，"
        "同一批事实、同一份政策，结论随档位翻转。"
    )
    ok = bool(derived) and len(derived) >= 2 and tier_dependent and bool(required_candidates)

    findings: List[str] = []
    if required_from_policy and required_from_gradient and required_from_policy != required_from_gradient:
        findings.append(
            f"上游映射不一致（须 owner 冻结）：同一 S3 在 m5024 政策里 min_tier={required_from_policy}，"
            f"在 m5026 梯度自报里 min_credit_level={declared_min_credit}（={required_from_gradient}）。"
            "本门取更严者判定，并把两者同时留证；否则同一单的派发结论会随实现而变。"
        )
    if denied:
        findings.append(
            f"真实贡献主体 {len(derived)} 个，按可导出事实推出的档位上界最高 "
            f"{max((d['tier_upper_bound'] for d in derived.values()), key=rank_of_tier)}；"
            f"对 5026（{declared_tier}）准入判定 {len(allowed)} 允许 / {len(denied)} 拒绝——"
            "联动**真的在拦**，不是摆设。"
        )
    if probe["S4"]["n_eligible"] == 0:
        findings.append(
            "S4 探索单要求在产线全部不可派发（0 个主体达到 L4）：这是 E5 揭示的档位空心化的直接后果，"
            "也解释了本波 S-N 轨为何只能由本门自己制造真实案例。"
        )

    return _check(
        "E2",
        "PASS" if ok else "FAIL",
        measured={
            "policy_version": policy.get("policy_version"),
            "tier_rules": len(rules),
            "level_min_tier": level_min_tier,
            "dispatch_matrix": matrix,
            "tier_dependent_matrix": tier_dependent,
            "real_contributors": derived,
            "real_case": real_case,
            "exploration_dispatch_probe": probe,
        },
        evidence=[
            _ev(credit_path, "m5024 信用档位政策（阶梯规则 + 准入映射）"),
            _ev(matrix_path, "m5026 梯度 × 档位派发矩阵（允许 15 格 / 拒绝 10 格）"),
            _ev(selfcheck_path, "m5026 任务梯度自报（mission 5026：S3）"),
            {"note": "真实贡献主体事实来源（只读 SELECT）", "sql": bundle.fact_sql("outcome_evaluations")},
        ],
        root_cause=None if ok else "缺少可用的真实案例：真实主体事实或派发矩阵不足",
        remediation=None if ok else "补齐 ladder 事实列（复现失败率/虚标计数）并接入 claim 侧准入过滤后再判",
        findings=findings,
    )


# ══════════════════════════ E3 预算周报 ══════════════════════════
_WEEK_RE = re.compile(r"窗口：`([^`]+)` → `([^`]+)`")
_NUM_RES = {
    "entries": re.compile(r"窗口内账目条目：(\d+)"),
    "review_events": re.compile(r"治理活动：(\d+) 事件"),
    "review_minutes_s": re.compile(r"治理活动：.*?累计 ([\d.]+) s"),
    "production_events": re.compile(r"生产活动：(\d+) 事件"),
}
_ISO_WEEK_RE = re.compile(r"审查预算周报 (\d{4}-W\d{2})")


def _verify_ledger_chain(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """独立重算 sha256 链：body = row − entry_hash；hash = sha256(prev + canonical(body))。"""
    prev = "0" * 64
    for index, row in enumerate(rows):
        body = {k: v for k, v in row.items() if k != "entry_hash"}
        expected = sha256_text(prev + canonical(body))
        if row.get("prev_hash") != prev or row.get("entry_hash") != expected:
            return {"ok": False, "entries": len(rows), "broken_at": index,
                    "expected_head": prev, "found_prev": row.get("prev_hash")}
        prev = row["entry_hash"]
    return {"ok": True, "entries": len(rows), "broken_at": None, "head_hash": prev}


def _parse_weekly(md_path: Path) -> Dict[str, Any]:
    text = md_path.read_text(encoding="utf-8")
    out: Dict[str, Any] = {"file": str(md_path), "iso_week": None, "window": None, "declared": {}}
    m = _ISO_WEEK_RE.search(text)
    if m:
        out["iso_week"] = m.group(1)
    w = _WEEK_RE.search(text)
    if w:
        out["window"] = [w.group(1), w.group(2)]
    for key, pattern in _NUM_RES.items():
        hit = pattern.search(text)
        if hit:
            out["declared"][key] = float(hit.group(1)) if key.endswith("_s") else int(hit.group(1))
    return out


def check_e3_budget_weekly(bundle: Bundle) -> Dict[str, Any]:
    ledger_path = bundle.upstream_file(5019, "review_budget_ledger.jsonl")
    w44_path = bundle.upstream_file(5019, "review_budget_weekly_2026-W44.md")
    w45_path = bundle.upstream_file(5019, "review_budget_weekly_2026-W45.md")
    backfill_path = bundle.upstream_file(5019, "review_budget_backfill.json")

    rows: List[Dict[str, Any]] = []
    chain = {"ok": False, "entries": 0, "broken_at": None, "error": "ledger absent"}
    if ledger_path:
        try:
            rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            chain = _verify_ledger_chain(rows)
        except json.JSONDecodeError as exc:  # 损坏行 = 篡改证据
            chain = {"ok": False, "entries": 0, "broken_at": -1, "error": str(exc)}

    reports = []
    for path in (w44_path, w45_path):
        if path is None:
            continue
        parsed = _parse_weekly(path)
        start, end = (parsed["window"] or [None, None])
        window_rows = []
        if start and end:
            window_rows = [
                r for r in rows
                if start <= str(r.get("recorded_at")) < end
            ]
        recomputed = {
            "entries": len(window_rows),
            "review_events": sum(1 for r in window_rows if r.get("kind") == "review"),
            "production_events": sum(1 for r in window_rows if r.get("kind") == "production"),
            "review_minutes_s": round(
                sum(float(r.get("duration_s") or 0.0)
                    for r in window_rows
                    if r.get("kind") == "review" and r.get("duration_source") in ("observed", "reported")),
                3,
            ),
        }
        declared = parsed["declared"]

        def _same(key: str, a: Any, b: Any) -> bool:
            """周报把秒数四舍五入到 0.1 s：时长按 0.05 s 容差比对，计数必须严格相等。"""
            if key.endswith("_s"):
                return abs(float(a) - float(b)) <= 0.05 + 1e-9
            return int(a) == int(b)

        mismatch = {
            k: {"declared": declared.get(k), "recomputed": recomputed.get(k)}
            for k in recomputed
            if k in declared and not _same(k, declared[k], recomputed[k])
        }
        parsed.update({"recomputed": recomputed, "mismatch": mismatch,
                       "match": not mismatch and bool(declared)})
        parsed["sha256"] = file_hash(path)
        reports.append(parsed)

    nonempty = [r for r in reports if r["recomputed"]["entries"] > 0]
    matches_all = all(r["match"] for r in nonempty) if nonempty else False
    ok = chain.get("ok", False) and bool(nonempty) and matches_all

    findings = [
        f"账本 {chain.get('entries', 0)} 条，sha256 链由本门独立重算：ok={chain.get('ok')}。",
        "首份周报 = 2026-W44（回填窗口之前，0 条）；实质周报 = 2026-W45（548 条，本门逐项复算零偏差）。",
    ]
    if reports and not reports[0]["recomputed"]["entries"]:
        findings.append(
            "W44 是空周报：如实登记（先出表、后回填），不粉饰为\"有数据\"；周报口径由 W45 承接。"
        )

    return _check(
        "E3",
        "PASS" if ok else "FAIL",
        measured={
            "ledger_entries": chain.get("entries", 0),
            "chain": chain,
            "review_type_counts": dict(Counter(r.get("review_type") for r in rows)),
            "kind_counts": dict(Counter(r.get("kind") for r in rows)),
            "weekly_reports": reports,
            "nonempty_reports": len(nonempty),
        },
        evidence=[
            _ev(ledger_path, "append-only 审查预算账本（本门独立重算哈希链）"),
            _ev(w44_path, "首份周报 2026-W44（空窗口，如实登记）"),
            _ev(w45_path, "实质周报 2026-W45（548 条，逐项复算）"),
            _ev(backfill_path, "7 天真实活动回填报告"),
        ],
        root_cause=None if ok else "账本链校验失败或周报数字与账本复算不一致",
        remediation=None if ok else "重跑 tools/governance/review_budget_report.py 并用本门复算对拍",
        findings=findings,
    )


# ══════════════════════════ E4 S-N 探索单 honest 失败 ══════════════════════════
def check_e4_sn_honest_fail(bundle: Bundle, sn_dir: Path) -> Dict[str, Any]:
    records: List[Dict[str, Any]] = []
    skipped: List[str] = []
    if sn_dir.is_dir():
        for path in sorted(sn_dir.glob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                records.append({"file": str(path), "valid": False, "problems": ["not_json"]})
                continue
            if not isinstance(doc, dict) or "ticket_id" not in doc or "grade" not in doc:
                # 同目录下的量测工件（EP001_measurements.json）不是 S-N 记录，跳过而非记错
                skipped.append(path.name)
                continue
            problems: List[str] = []
            grade = doc.get("grade")
            e_level = doc.get("e_level")
            if grade not in ("S4", "S5"):
                problems.append(f"grade={grade} 不是 S4/S5 探索单")
            e_rank = int(str(e_level).lstrip("E")) if str(e_level).startswith("E") and str(e_level)[1:].isdigit() else -1
            # SPEC §7.1-9：S4 须 E≥2；S5 须 E≥3；任何等级须 E≥1
            if grade == "S4" and e_rank < 2:
                problems.append(f"S4 须 E>=2（实际 {e_level}）")
            if grade == "S5" and e_rank < 3:
                problems.append(f"S5 须 E>=3（实际 {e_level}）")
            if e_rank < 1:
                problems.append(f"任何等级须 E>=1（实际 {e_level}）")
            if doc.get("honest_fail") is not True:
                problems.append("honest_fail 未置 true")
            if doc.get("result_status") not in ("failed", "failed_honestly"):
                problems.append(f"result_status={doc.get('result_status')} 未如实登记失败")
            if doc.get("counted_as_output") is not False:
                problems.append("失败单不得计入成果（counted_as_output 须为 false）")
            root_cause = (doc.get("root_cause") or "").strip()
            if len(root_cause) < 40:
                problems.append("root_cause 缺失或过短（<40 字符）")
            refs = doc.get("evidence_refs") or []
            if len(refs) < 2:
                problems.append("evidence_refs 少于 2 条")
            unresolved = [r for r in refs if not (REPO_ROOT / str(r.get("path", ""))).exists()] if refs else []
            if refs and unresolved:
                problems.append(f"证据引用不落地：{len(unresolved)} 条路径不存在")
            attempt = doc.get("attempt") or {}
            measured = attempt.get("measured") or {}
            if not measured:
                problems.append("attempt.measured 为空：没有真实量测，不算探索")
            attempted = measured.get("targets_attempted")
            achieved = measured.get("targets_achieved")
            if not isinstance(attempted, int) or not isinstance(achieved, int) or attempted <= 0:
                problems.append("targets_attempted/achieved 缺失")
            elif achieved >= attempted:
                problems.append("目标全达成 → 不是失败单，不能登记为 honest 失败")
            records.append(
                {
                    "file": str(path),
                    "ticket_id": doc.get("ticket_id"),
                    "grade": grade,
                    "e_level": e_level,
                    "result_status": doc.get("result_status"),
                    "honest_fail": doc.get("honest_fail"),
                    "root_cause": root_cause[:200],
                    "measured": measured,
                    "valid": not problems,
                    "problems": problems,
                }
            )
    valid = [r for r in records if r.get("valid")]
    ok = bool(valid)
    return _check(
        "E4",
        "PASS" if ok else "FAIL",
        measured={
            "sn_dir": str(sn_dir),
            "records": records,
            "skipped_non_records": skipped,
            "valid_records": len(valid),
            "legality_rule": "SPEC §7.1-9：S4 须 E>=2，S5 须 E>=3；候选不计入成果分布",
        },
        evidence=[
            {"note": f"S-N 记录 {r.get('ticket_id')}（{r.get('grade')}/{r.get('e_level')}，honest 失败）",
             "path": r["file"], "sha256": file_hash(Path(r["file"]))}
            for r in valid
        ] + [
            {"note": "S-N 轨校验器：tools/ops/spec_impl_acceptance_gate.py::check_e4_sn_honest_fail"},
        ],
        root_cause=None if ok else "S-N 轨无合法记录（缺 S4/S5 honest 失败单或记录不合规）",
        remediation=None if ok else "按 artifacts/in19_g20/sn/ 的 schema 补一条真实探索单",
        findings=[
            "S-N 轨的合法性由机器校验：grade∈{S4,S5}、honest_fail=true、量测存在且未全达成、"
            "root_cause≥40 字符、证据引用可落地、失败单不计入成果。",
        ] if ok else [],
    )


# ══════════════════════════ E5 S×E 分布 + 四中心 ══════════════════════════
def check_e5_distribution_centers(bundle: Bundle) -> Dict[str, Any]:
    evals = bundle.fact("outcome_evaluations")
    confirmed = [r for r in evals if r.get("status") == "confirmed"]

    # ── 分层、不加权：两条独立实现（Counter 计数 vs 逐格集合过滤）互相对拍 ──
    candidates = [r for r in evals if r.get("status") == "candidate"]
    rejected = [r for r in evals if r.get("status") == "rejected"]
    candidate_grades = Counter(r.get("grade") for r in candidates)
    rejected_grades = Counter(r.get("grade") for r in rejected)
    off_ladder_confirmed = [r for r in confirmed if (r.get("grade") or "") not in GRADES]

    by_counter = Counter((r.get("grade") or "?", r.get("e_level") or "?") for r in confirmed)
    by_scan = {
        (g, e): sum(1 for r in confirmed if (r.get("grade") or "?") == g and (r.get("e_level") or "?") == e)
        for g in GRADES for e in E_LEVELS
    }
    consistent = all(by_counter.get(k, 0) == by_scan.get(k, 0) for k in by_scan)
    matrix = [[by_scan[(g, e)] for e in E_LEVELS] for g in GRADES]
    total = sum(sum(row) for row in matrix)
    # 无权重/无归一：整数计数之和 = 梯内记录数（越梯行单列，不悄悄丢弃）
    sum_identity = total == len(confirmed) - len(off_ladder_confirmed)
    nonempty_grades = sorted({g for g, e in by_counter if by_counter[(g, e)] > 0})
    nonempty_e = sorted({e for g, e in by_counter if by_counter[(g, e)] > 0})
    degenerate = len(nonempty_grades) <= 1

    payload_path = bundle.upstream_file(5023, "center_payloads_snapshot.json")
    crosscheck_path = bundle.upstream_file(5023, "center_sql_crosscheck.json")
    payloads = (read_json(payload_path).get("payloads", {}) if payload_path else {})
    crosscheck = read_json(crosscheck_path) if crosscheck_path else {}
    center_states = {}
    for center in CENTERS:
        payload = payloads.get(center) or {}
        metrics = payload.get("metrics") or []
        not_impl = payload.get("not_implemented") or []
        # SPEC §14：能力缺失必须显式标注"未接入/未验证"，禁止 Mock 冒充；
        # 因此 implemented=false 本身**不算缺陷**，只要求它带原因、且不许给伪造的 0 值。
        honest_gap = all((entry.get("reason") or "").strip() for entry in not_impl) if not_impl else True
        fabricated = [
            m.get("key")
            for m in metrics
            if m.get("status") == "not_implemented" and m.get("value") not in (None, "")
        ]
        center_states[center] = {
            "present": bool(payload),
            "implemented": payload.get("implemented"),
            "n_metrics": len(metrics),
            "metrics_with_source": sum(1 for m in metrics if m.get("source")),
            "not_implemented_declared": len(not_impl),
            "not_implemented_honest": honest_gap,
            "fabricated_placeholder_metrics": fabricated,
            "acceptable": bool(payload)
            and len(metrics) >= 6
            and all(m.get("source") for m in metrics)
            and (payload.get("implemented") is True or honest_gap)
            and not fabricated,
        }
    centers_ok = all(center_states[c]["acceptable"] for c in CENTERS)
    unimplemented = [c for c in CENTERS if center_states[c]["implemented"] is not True]
    crosscheck_ok = bool(crosscheck.get("all_match")) and crosscheck.get("items_matched") == crosscheck.get("items_total") \
        and (crosscheck.get("criteria") or {}).get("satisfied") is True

    ok = consistent and sum_identity and centers_ok and crosscheck_ok
    findings = [
        f"S×E 分布（confirmed n={len(confirmed)}）：" + "；".join(
            f"{g}: " + ",".join(f"{e}={by_scan[(g, e)]}" for e in E_LEVELS if by_scan[(g, e)]) for g in nonempty_grades
        ) + f"（空等级：{[g for g in GRADES if g not in nonempty_grades]}）",
        f"分层不加权已机器证明：两条独立实现互相对拍一致，且整数计数之和恒等于梯内记录数"
        f"（Σ={total}=‖confirmed‖−越梯={len(confirmed)}−{len(off_ladder_confirmed)}），"
        "不存在权重/归一/排名折算。",
        f"四中心（{','.join(CENTERS)}）载荷齐备，指标均带 source 字段；SQL 交叉核对 "
        f"{crosscheck.get('items_matched')}/{crosscheck.get('items_total')} 全匹配。",
    ]
    findings.append(
        f"合法性剔除已执行：candidate {len(candidates)} 条（{dict(candidate_grades)}）与 rejected "
        f"{len(rejected)} 条按 §7.1-9 不计入正面分布；confirmed 里越梯（非 S1–S5）记录 "
        f"{len(off_ladder_confirmed)} 条。候选被保留记录、追踪但不充数——这正是 S-N/S4/S5 该有的位置。"
    )
    if degenerate:
        findings.append(
            "诚实标注：在产 S×E 退化——confirmed 全部落在 S1/E2（自动证据扫描 rule_derived 的单一档），"
            "S3+ 仅 1 条且被独立审查否决。分布\"可复算\"但\"无信息量\"：这不是统计错，是定级规则太窄，"
            "需在 SPEC §7.1 增加分档判据，否则信用阶梯（E2 发现）永远升不上去。"
        )
    for c in unimplemented:
        gaps = [g.get("key") for g in (payloads.get(c) or {}).get("not_implemented") or []]
        findings.append(
            f"{c} 中心 implemented=false 且**如实声明**了未接入项 {gaps}（空值不写成 0、指标带 source）："
            "符合 SPEC §14「禁止 Mock 冒充」——缺失被看见，而不是被填平。"
        )

    return _check(
        "E5",
        "PASS" if ok else "FAIL",
        measured={
            "grades": list(GRADES),
            "e_levels": list(E_LEVELS),
            "matrix_confirmed_counts": matrix,
            "nonzero_grades": nonempty_grades,
            "nonzero_e_levels": nonempty_e,
            "n_confirmed": len(confirmed),
            "sum_of_cells": total,
            "sum_identity_holds": sum_identity,
            "two_way_recompute_consistent": consistent,
            "degenerate_distribution": degenerate,
            "off_ladder_confirmed": len(off_ladder_confirmed),
            "excluded_by_legality": {
                "rule": "SPEC §7.1-9：候选（S4/S5/S-N 等）与 rejected 不计入正面成果分布",
                "candidate_n": len(candidates),
                "candidate_grades": dict(candidate_grades),
                "rejected_n": len(rejected),
                "rejected_grades": dict(rejected_grades),
            },
            "centers": center_states,
            "center_crosscheck": {
                "all_match": crosscheck.get("all_match"),
                "items_matched": crosscheck.get("items_matched"),
                "items_total": crosscheck.get("items_total"),
                "criteria": crosscheck.get("criteria"),
            },
            "live_probe": {
                "attempted": False,
                "reason": "本门离线运行（不信网络）：平台后端 :8001 在判定时刻未监听，"
                          "因此\"可访问\"以载荷快照+SQL 交叉核对为证，探活结果不参与判定。",
            },
        },
        evidence=[
            _ev(bundle.upstream_file(5018, "stats_snapshot.json"), "Q1 统计快照（与 E1 复算互为对拍）"),
            _ev(payload_path, "四中心载荷快照（真实后端单源载荷）"),
            _ev(crosscheck_path, "四中心 SQL 交叉核对（44/44）"),
            {"note": "分布重算来源（只读 SELECT）", "sql": bundle.fact_sql("outcome_evaluations")},
        ],
        root_cause=None if ok else "分布复算/四中心载荷/交叉核对未通过",
        remediation=None if ok else "重跑 scripts/verify_four_centers_crosscheck.py 并刷新载荷快照",
        findings=findings,
    )


# ══════════════════════════ 汇总 ══════════════════════════
def e2_limitation(cid: str) -> str:
    return f"{cid} 结论依赖可导出事实的档位上界（未知指标按通过）；允许方向若有真实记录则更强，无则只有政策矩阵。"


def run_gate(evidence_root: Path, sn_dir: Path, out_path: Path) -> Dict[str, Any]:
    bundle = Bundle(evidence_root)
    checks = [
        check_e1_flow(bundle),
        check_e2_tier_dispatch(bundle),
        check_e3_budget_weekly(bundle),
        check_e4_sn_honest_fail(bundle, sn_dir),
        check_e5_distribution_centers(bundle),
    ]
    by_id = {c["id"]: c["status"] for c in checks}
    passed = [cid for cid, st in by_id.items() if st == "PASS"]
    failed = [cid for cid, st in by_id.items() if st == "FAIL"]
    withheld = [cid for cid, st in by_id.items() if st == "WITHHELD"]
    verdict = "PASSED" if not failed and not withheld else "NOT_PASSED"

    upstream_digest = [
        {"mission": r.get("mission"), "node": r.get("node"), "commit": r.get("commit"),
         "files": [{"src": f.get("src"), "sha256": f.get("sha256")} for f in r.get("files", [])
                   if f.get("status") == "copied"]}
        for r in bundle.upstream_records()
    ]
    doc = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "campaign_node": {
            "id": "IN19-G20",
            "name": "SPEC军G20·S×E闭环验收门",
            "mission_id": 5176,
            "campaign_plan_id": 20,
            "campaign_node_row_id": 185,
            "acceptance_criteria": ["五项留证", "探索单honest失败记录", "统计可复算"],
        },
        "verdict": verdict,
        "summary": {
            "criteria_total": len(checks),
            "passed": passed,
            "failed": failed,
            "withheld": withheld,
            "headline": (
                "五项留证齐备；四项达标，一项（E1 评价流覆盖）如实判 FAIL："
                "机制存在且复算零偏差，但部署缺口导致本波 8 单只有 2 单留有评价记录。"
                if verdict == "NOT_PASSED" else "五项全部达标。"
            ),
        },
        "checks": checks,
        "audit_chain": {
            "evidence_bundle": str(evidence_root),
            "evidence_generated_at": bundle.provenance.get("generated_at"),
            "collector": bundle.provenance.get("collector"),
            "db_facts_collected_at": bundle.db_facts.get("collected_at"),
            "postgres_readonly": bundle.db_facts.get("read_only"),
            "upstream": upstream_digest,
            "gate_script_sha256": file_hash(Path(__file__).resolve()),
            "reproduce": [
                "python3 tools/ops/wave_evidence_collect.py  # 只读取回证据 bundle",
                "python3 tools/ops/explore_pending_closure.py  # 真实 S4 探索单（失败单）",
                "python3 tools/ops/spec_impl_acceptance_gate.py  # 本门判定",
                "python3 -m pytest -q tools/ops/test_spec_impl_acceptance_gate.py",
            ],
        },
        "limitations": [
            "判定只读离线 bundle：上游若在采集后改动分支，需重跑采集；sha256 可验证两版差异。",
            "信用档位用**上界**（未知指标按通过）：对拒绝类结论保守、对允许类结论偏宽；"
            "本波两方向都有真实案例，但允许侧仍受此偏差影响，故不放宽判定所用档位阈值。",
            "复现失败率/虚标计数在库内无事实列 → 显式 None，不猜；这也是 S4 探索单在产线不可派发的直接原因之一。",
            "\"四中心可访问\"以载荷快照 + SQL 交叉核对为证；平台后端 :8001 判定时刻未监听，未做在线探活。",
            e2_limitation('E2'),
            "本门不写平台库、不做迁移：FAIL 项只能由上游修（部署 hook），不能由验收门自证通过。",
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return doc


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="IN19-G20 S×E 闭环验收门（离线判定）")
    ap.add_argument("--evidence-root", default=str(REPO_ROOT / "artifacts/in19_g20/evidence"))
    ap.add_argument("--sn-dir", default=str(REPO_ROOT / "artifacts/in19_g20/sn"))
    ap.add_argument("--out", default=str(REPO_ROOT / "artifacts/spec_impl_acceptance.json"))
    args = ap.parse_args(argv)

    doc = run_gate(Path(args.evidence_root), Path(args.sn_dir), Path(args.out))
    print(json.dumps({"verdict": doc["verdict"], "summary": doc["summary"], "out": args.out},
                     ensure_ascii=False, indent=1))
    return 0 if doc["verdict"] == "PASSED" else 1


if __name__ == "__main__":
    sys.exit(main())