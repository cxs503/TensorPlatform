"""锦标赛报告装配：审计链 → 机器可验 JSON + 人类可读 markdown。

报告是「账本的投影」而不是另写一份叙事：每个数字都能在 ``audit`` / ``negative_knowledge``
里找到对应事件或记录，``criteria`` 段落把任务书的机器验收项逐条落成数字，便于 CI 断言。
"""

from __future__ import annotations

from typing import Any, Sequence

from .model import canonical, sha256_text

SCHEMA_VERSION = "tournament-report/1.0"


def _lineage(records_by_id: dict[str, Any], seat_id: str, depth: int = 0) -> list[dict]:
    """冠军谱系：沿 parents 回溯到第一代（防环，深度上限 8）。"""
    if depth > 8 or seat_id not in records_by_id:
        return []
    record = records_by_id[seat_id]
    seat = record.seat
    node = {
        "seat_id": seat.seat_id,
        "generation": seat.generation,
        "origin": seat.origin,
        "model_family": seat.gene.model_family,
        "framework": seat.gene.framework,
        "parents": list(seat.parents),
        "score": record.score,
        "passed": record.passed,
    }
    chain = [node]
    for parent in seat.parents:
        chain.extend(_lineage(records_by_id, parent, depth + 1))
    return chain


def build_report(orch, verdict: str, include_events: bool = True) -> dict:
    problem = orch.problem
    records_by_id = {r.seat.seat_id: r for r in orch.records}
    champion = orch.champion
    pass_generation = champion.seat.generation if champion else None
    distinct_families = sorted({r.seat.gene.model_family for r in orch.records})
    distinct_signatures = sorted({r.seat.family_signature for r in orch.records})
    champion_err = (
        float(champion.metrics.get("rel_error")) if champion and "rel_error" in champion.metrics else None
    )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": f"{problem.problem_id}-S{problem.seed}-{sha256_text(canonical(problem.as_dict()))[:8]}",
        "problem": problem.as_dict(),
        "scorer": {
            "basis": problem.scorer_basis,
            "fingerprint": (orch.audit.events[0]["payload"].get("scorer_fingerprint", "") if orch.audit.events else ""),
            "goodhart_immunity": "打分器由调用方注入且必须为 reference/experiment 锚定；席位自报分数一律丢弃",
        },
        "verdict": {
            "verdict": verdict,
            "stop_reason": orch.stop_reason,
            "generations_run": len(orch.generations),
            "stopped_early": orch.stopped_early,
            "pass_generation": pass_generation,
            "champion": (
                {
                    "seat_id": champion.seat.seat_id,
                    "generation": champion.seat.generation,
                    "clan_id": champion.seat.clan_id,
                    "origin": champion.seat.origin,
                    "gene": champion.seat.gene.as_dict(),
                    "score": champion.score,
                    "metrics": champion.metrics,
                    "artifact_ref": champion.artifact_path,
                    "hypothesis": champion.seat.hypothesis,
                }
                if champion
                else None
            ),
            "champion_lineage": _lineage(records_by_id, champion.seat.seat_id) if champion else [],
        },
        "budget": {
            "seat_cap": problem.max_total_seats,
            "seats_spent": orch.spent,
            "seats_unspent": problem.max_total_seats - orch.spent,
            "early_stop": orch.stopped_early,
            "early_stop_seats_saved": _early_stop_saved(problem, orch),
            "anchor_violations": orch.audit.count("anchor_violation"),
            "budget_capped_events": orch.audit.count("budget_capped"),
            "budget_exhausted_events": orch.audit.count("budget_exhausted"),
        },
        "diversity": {
            "axes": ["model_family", "framework", "blind_prediction", "physics_anchor"],
            "distinct_families": distinct_families,
            "distinct_family_signatures": distinct_signatures,
            "unique_family_signature_count": len(distinct_signatures),
            "family_signature_uniqueness_enforced": True,
            "sibling_similarity_max": problem.sibling_similarity_max,
            "dedup_rejections_total": sum(
                int(e["payload"].get("count", 1)) for e in orch.audit.of_type("candidate_rejected")
            ),
            "per_generation": [g["diversity"] for g in orch.generations],
        },
        "generations": orch.generations,
        "negative_knowledge": {
            "track": "S-N",
            "count": len(orch.neg.records),
            "seats_spawned": len(orch.records),
            "covers_all_seats": len(orch.neg.records) == len(orch.records),
            "by_failure_mode": orch.neg.by_failure_mode(),
            "head": orch.neg.head,
            "chain_valid": orch.neg.verify(),
            "records": orch.neg.records,
        },
        "audit": {
            "event_count": len(orch.audit.events),
            "head": orch.audit.head,
            "chain_valid": orch.audit.as_dict()["chain_valid"],
            "by_type": _count_by(orch.audit.types()),
            "events": orch.audit.events if include_events else [],
        },
    }
    report["criteria"] = {
        "generations_run": len(orch.generations),
        "full_chain_generations": len(orch.generations),
        "pass_generation": pass_generation,
        "seats_spawned": len(orch.records),
        "seat_cap": problem.max_total_seats,
        "seat_cap_respected": len(orch.records) <= problem.max_total_seats,
        "score_threshold": problem.score_threshold,
        "max_rel_error_gate": problem.max_rel_error,
        "champion_rel_error": champion_err,
        "champion_anchor_ok": bool(champion.anchor_ok) if champion else None,
        "best_eliminated_rel_error": _best_eliminated_error(orch),
        "anchor_violations": orch.audit.count("anchor_violation"),
        "champion_within_error_gate": (
            None if champion_err is None or problem.max_rel_error is None
            else champion_err <= problem.max_rel_error
        ),
        "diversity_fields_in_every_goal": all(
            set(("model_family", "framework", "blind_prediction", "physics_anchor"))
            <= set(r.seat.mission.get("diversity", {}))
            for r in orch.records
        ),
        "family_signature_uniqueness_per_generation": all(
            g["diversity"]["distinct_family_signatures"] == len(g["ranked"]) for g in orch.generations
        ),
        "negative_knowledge_count": len(orch.neg.records),
        "audit_event_count": len(orch.audit.events),
        "audit_chain_valid": orch.audit.as_dict()["chain_valid"],
        "scorer_scores_used": orch.audit.count("seat_scored"),
        "self_reported_scores_ignored": orch.audit.count("score_spoof_ignored"),
    }
    return report


def _best_eliminated_error(orch):
    """未夺冠席位里最好的 rel_error（离门限最近的落选者，用于误差对表）。"""
    errors = [
        float(r.metrics["rel_error"])
        for r in orch.records
        if not r.passed and "rel_error" in r.metrics
    ]
    return min(errors) if errors else None


def _early_stop_saved(problem, orch) -> int:
    """早停真实省下的席位数 = min(剩余代数×每代席位, 预算余量)；代数跑满则省 0。"""
    if not orch.stopped_early or orch.champion is None:
        return 0
    remaining_generations = problem.max_generations - orch.champion.seat.generation
    potential = max(0, remaining_generations) * problem.seats_per_generation
    return int(min(potential, problem.max_total_seats - orch.spent))


def report_digest(report: dict) -> str:
    """报告内容摘要（用于跨进程幂等比对；不含任何时间戳字段）。"""
    return sha256_text(canonical(report))


def _count_by(values: Sequence[str]) -> dict:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items()))


def render_markdown(report: dict) -> str:
    problem = report["problem"]
    verdict = report["verdict"]
    budget = report["budget"]
    lines = [
        f"# 锦标赛报告：{problem['problem_id']}",
        "",
        f"- 问题：{problem['title']}",
        f"- 裁决：**{verdict['verdict']}**（stop_reason={verdict['stop_reason']}，"
        f"代数={verdict['generations_run']}，早停={verdict['stopped_early']}）",
        f"- 预算：席位上限 {budget['seat_cap']}，实花 {budget['seats_spent']}，余 {budget['seats_unspent']}",
        f"- 通过门：score >= {problem['score_threshold']}"
        + (f"（相对误差 <= {problem['max_rel_error']}）" if problem.get("max_rel_error") is not None else ""),
        "",
        "## 每代排名",
        "",
        "| 代 | 名额 | 席位 | 族 | 框架 | 分数 | 结果 |",
        "|---|---|---|---|---|---|---|",
    ]
    for gen in report["generations"]:
        for row in gen["ranked"]:
            gene = row["gene"]
            lines.append(
                f"| {gen['generation']} | {row['rank']} | `{row['seat_id']}` | {gene['model_family']} | "
                f"{gene['framework']} | {row['score']:.4f} | {row['outcome']}/{row['failure_mode']} |"
            )
    champ = verdict["champion"]
    lines += ["", "## 冠军", ""]
    if champ:
        lines.append(
            f"- `{champ['seat_id']}`（G{champ['generation']}，{champ['origin']}，clan={champ['clan_id']}）"
            f"score={champ['score']:.4f}，metrics={champ['metrics']}"
        )
        chain = " ← ".join(f"{n['seat_id']}(G{n['generation']},{n['model_family']})" for n in verdict["champion_lineage"])
        lines.append(f"- 谱系：{chain}")
    else:
        lines.append("- 无冠军（全灭，honest FAIL）：本门限下无席位达标，见负知识轨。")
    lines += ["", "## 多样性", ""]
    for gen_div in report["diversity"]["per_generation"]:
        lines.append(
            f"- 族 {gen_div['families']} / 框架 {gen_div['frameworks']} / 盲预测 {gen_div['blind_on']} 席 / "
            f"同族键唯一={gen_div['distinct_family_signatures']} / 最大两两相似度={gen_div['max_pairwise_similarity']}"
        )
    lines += ["", "## 审计", ""]
    audit = report["audit"]
    lines.append(f"- 事件 {audit['event_count']} 条，哈希链有效={audit['chain_valid']}，head=`{audit['head'][:16]}…`")
    neg = report["negative_knowledge"]
    lines.append(
        f"- S-N 负知识轨 {neg['count']}/{neg['seats_spawned']} 席入轨（covers_all={neg['covers_all_seats']}，"
        f"链有效={neg['chain_valid']}），分布 {neg['by_failure_mode']}"
    )
    return "\n".join(lines) + "\n"