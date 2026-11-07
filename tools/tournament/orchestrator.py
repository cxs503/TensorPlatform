"""锦标赛编排器状态机。

一轮锦标赛（``TournamentOrchestrator.run()``）的确定流程
--------------------------------------------------------
1. **生成席位**：按基因组空间做确定性抽样（反同族查重），把多样性清单注入 mission goal，
   落盘 ``missions/G<g>/<seat_id>.json``（可直接 POST 到任务池的形态）。
2. **收集工件**：调用注入的执行器 ``executor(mission) -> artifact``，工件落盘后 **再从磁盘
   读回** 再打分（收集是真实的二次读取，不是内存里凑数），sha256 记入审计。
3. **物理锚打分**：调用注入的 ``scorer(mission, artifact) -> {score, passed, metrics}``。
   打分器必须是参考/实验锚定（构造期硬门）；席位自报分数一律丢弃并留痕。
4. **排名淘汰**：按 (score desc, seat_id) 定序，前 ``promote_count`` 晋级，其余淘汰。
5. **门禁**：本代任一座位通过（score >= 门限 且 打分器判 passed 且锚指标齐全）→
   **PASS 早停**：不播后续代的席位，缺口记为未花预算；未通过则继续。
6. **杂交重组**：晋级席位的基因组交叉（胜者 model_family × 次席 framework），父代 *实测量*
   随 mission 回流（``parent_estimates``），子代再进下一代；同时每代补 1 席新族（多样性注水，
   否则锦标赛会收敛成近亲繁殖）。
7. **裁决**：冠军（带完整谱系）或全灭（``all_eliminated``，honest FAIL，不粉饰）。

预算门
------
``max_total_seats`` 是硬顶：派发前先扣预算，不够就截断本代席位并留痕 ``budget_capped``；
预算耗尽即停播并裁决。任何一代出 PASS 也立即停播后续席位（``early_stop``）。

零域语义：编排器不认识物理量、也不认识船舶——它只认 ``ProblemSpec`` 与打分器契约。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .ledger import AuditLog, NegativeLedger
from .model import (
    SELF_REPORTED_SCORE_KEYS,
    Gene,
    ProblemSpec,
    Seat,
    build_mission,
    canonical,
    fresh_hypothesis,
    gene_diversity_fields,
    gene_similarity,
    hybrid_gene,
    hybrid_hypothesis,
    make_seat_id,
    mutation_gene,
    promote_count,
    select_fresh,
    sha256_text,
)

CEILING_VERDICTS = ("champion", "all_eliminated")
# 每代至少留给「新族注水」的席位数：没有新血，锦标赛必然退化为近亲繁殖
FRESH_RESERVE = 2


def scorer_fingerprint(scorer: Callable[..., Any]) -> str:
    """打分器指纹：源码 sha256（取不到源码则退回 qualname+id 的稳定摘要）。"""
    try:
        src = inspect.getsource(scorer)
    except (OSError, TypeError):
        src = f"{getattr(scorer, '__module__', '')}.{getattr(scorer, '__qualname__', repr(scorer))}"
    return sha256_text(src)


@dataclass
class SeatRecord:
    """一个席位从派发到裁决的全生命周期（审计与报告的唯一事实来源）。"""

    seat: Seat
    mission_path: str = ""
    artifact_path: str = ""
    artifact_sha256: str = ""
    artifact: dict | None = None
    score: float | None = None
    passed: bool = False
    metrics: dict = None  # type: ignore[assignment]
    status: str = "spawned"  # spawned | scored | invalid | missing
    anchor_ok: bool = True
    failure_mode: str = "pending"
    rank: int | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.metrics is None:
            self.metrics = {}


class TournamentOrchestrator:
    """百人锦标赛编排器：数量 → 质变的引擎件。"""

    def __init__(
        self,
        problem: ProblemSpec,
        scorer: Callable[[dict, dict], dict],
        executor: Callable[[dict], dict],
        workdir: str | Path,
        clock: Callable[[], str] | None = None,
        campaign_id: str = "",
    ) -> None:
        problem.validate()
        if not callable(scorer) or not callable(executor):
            raise TypeError("scorer/executor 必须是可调用对象")
        self.problem = problem
        self.scorer = scorer
        self.executor = executor
        self.workdir = Path(workdir)
        self.campaign_id = campaign_id or problem.problem_id
        self.audit = AuditLog(clock)
        self.neg = NegativeLedger()
        self.rng = random.Random(problem.seed)
        self.records: list[SeatRecord] = []
        self.generations: list[dict] = []
        self.spent = 0
        self.stopped_early = False
        self.stop_reason = "generations_exhausted"
        self.champion: SeatRecord | None = None
        self.survivors: list[SeatRecord] = []
        self._used_signatures: set[str] = set()
        self._index = 0
        self._rejections: list[dict] = []

    # ------------------------------------------------------------------ 主循环
    def run(self) -> dict:
        from .report import build_report

        self.workdir.mkdir(parents=True, exist_ok=True)
        self.audit.append(
            "run_started",
            problem=self.problem.as_dict(),
            scorer_basis=self.problem.scorer_basis,
            scorer_fingerprint=scorer_fingerprint(self.scorer),
            campaign_id=self.campaign_id,
        )
        for generation in range(1, self.problem.max_generations + 1):
            if self.spent >= self.problem.max_total_seats:
                self.stop_reason = "budget_exhausted"
                self.audit.append("budget_exhausted", generation=generation, spent=self.spent,
                                  cap=self.problem.max_total_seats)
                break
            planned = self._plan_generation(generation)
            if not planned:
                self.stop_reason = "no_seats_plannable"
                self.audit.append("generation_empty", generation=generation)
                break
            self.audit.append(
                "generation_started",
                generation=generation,
                seats=[s.seat_id for s in planned],
                families=sorted({s.gene.model_family for s in planned}),
                origins=sorted({s.origin for s in planned}),
                budget_left=self.problem.max_total_seats - self.spent,
            )
            gen_records = [self._execute_seat(seat) for seat in planned]
            self.records.extend(gen_records)
            self.generations.append(self._settle_generation(generation, gen_records))
            if self.champion is not None:
                self.stopped_early = True
                self.stop_reason = "champion_pass"
                self.audit.append(
                    "early_stop",
                    generation=generation,
                    champion=self.champion.seat.seat_id,
                    score=self.champion.score,
                    seats_not_spawned=max(0, self.problem.max_total_seats - self.spent),
                    note="任一代出 PASS 即停播后续席位（预算门）",
                )
                break
        verdict = "champion" if self.champion is not None else "all_eliminated"
        self.audit.append(
            "run_finished",
            verdict=verdict,
            stop_reason=self.stop_reason,
            spent=self.spent,
            generations=len(self.generations),
        )
        self._record_negative_knowledge()
        return build_report(self, verdict)

    # ------------------------------------------------------------------ 席位规划
    def _plan_generation(self, generation: int) -> list[Seat]:
        remaining_budget = self.problem.max_total_seats - self.spent
        if remaining_budget <= 0:
            return []
        planned: list[Seat] = []

        # 1) 杂交/变异：上一代晋级席位的基因回流（第一代没有）
        if generation > 1 and self.survivors:
            planned.extend(self._breed(generation, remaining_budget))

        # 2) 新族注水：保证每代至少 1 席新族，避免近亲繁殖 + 满足 min_model_families
        need_fresh = self.problem.seats_per_generation - len(planned)
        if generation == 1:
            need_fresh = self.problem.seats_per_generation
        if need_fresh > 0:
            picks, rejections = select_fresh(
                self.problem, self.rng, need_fresh, planned, self._used_signatures
            )
            self._rejections.extend({"generation": generation, **r} for r in rejections)
            for reason, count in _tally(r["reason"] for r in rejections).items():
                self.audit.append(
                    "candidate_rejected", generation=generation, reason=reason, count=count
                )
            for gene, hypothesis in picks:
                planned.append(self._new_fresh_seat(generation, gene, hypothesis))

        # 3) 硬顶截断（预算门）
        if len(planned) > remaining_budget:
            dropped = planned[remaining_budget:]
            planned = planned[:remaining_budget]
            self.audit.append(
                "budget_capped",
                generation=generation,
                cap=self.problem.max_total_seats,
                dropped=[s.seat_id for s in dropped],
                kept=len(planned),
            )

        # 4) 多样性守卫
        families = {s.gene.model_family for s in planned}
        if len(families) < self.problem.min_model_families:
            self.audit.append(
                "diversity_relaxed",
                generation=generation,
                families=sorted(families),
                required=self.problem.min_model_families,
                note="候选池/预算不足以满足族数下限，已如实记录（不伪造多样性）",
            )
        return planned

    def _new_fresh_seat(self, generation: int, gene: Gene, hypothesis: str) -> Seat:
        self._index += 1
        seat_id = make_seat_id(generation, self._index, gene, salt=f"fresh:{self._index}")
        clan = f"C-{gene.model_family.replace(' ', '_')}-{self._index:02d}"
        seat = Seat(
            seat_id=seat_id,
            generation=generation,
            clan_id=clan,
            gene=gene,
            hypothesis=hypothesis,
            origin="fresh",
        )
        self._used_signatures.add(gene.family_signature)
        return seat

    def _breed(self, generation: int, remaining_budget: int) -> list[Seat]:
        """杂交 + 变异：候选算子按「双向交叉 → 其余两两交叉 → 单亲变异」定序。

        每个候选都要过反同族查重（``family_signature`` 全集），因此第 3 代不会把第 1 代的
        组合原样重播；被拒的候选入审计 ``candidate_rejected``，不是静默丢弃。
        父代 *实测量* 随 mission 一起回流（``parent_estimates``），子代在赢家的数上继续做工。
        """
        survivors = self.survivors
        if not survivors:
            return []
        max_children = max(1, self.problem.seats_per_generation - FRESH_RESERVE)
        best = survivors[0]

        plan: list[tuple[str, Gene, tuple[SeatRecord, ...]]] = []
        for other in survivors[1:]:
            plan.append(("hybrid", hybrid_gene(self.problem, best.seat, other.seat), (best, other)))
            plan.append(("hybrid", hybrid_gene(self.problem, other.seat, best.seat), (best, other)))
        for i in range(1, len(survivors)):
            for j in range(i + 1, len(survivors)):
                a, b = survivors[i], survivors[j]
                plan.append(("hybrid", hybrid_gene(self.problem, a.seat, b.seat), (a, b)))
        for parent in survivors:
            plan.append(("mutation", mutation_gene(self.problem, parent.seat, self._used_signatures), (parent,)))

        children: list[Seat] = []
        rejected: dict[str, int] = {}
        for kind, gene, parents_records in plan:
            if len(children) >= max_children:
                break
            siblings = {c.family_signature for c in children}
            parent_genes = [record.seat.gene for record in parents_records]
            if gene.family_signature in siblings:
                rejected["child_duplicates_sibling_in_generation"] = (
                    rejected.get("child_duplicates_sibling_in_generation", 0) + 1
                )
                continue
            if gene in parent_genes:
                rejected["child_equals_parent_genome"] = rejected.get("child_equals_parent_genome", 0) + 1
                continue
            if gene.family_signature in self._used_signatures and kind == "hybrid":
                self.audit.append(
                    "recombination_reused_signature",
                    generation=generation,
                    gene=gene.as_dict(),
                    note="后代复用历史同族键：允许（亲本实测量不同→信息不同），如实留痕",
                )
            self._index += 1
            if kind == "hybrid":
                seat_id = make_seat_id(generation, self._index, gene, salt=f"hybrid:{parents_records[0].seat.seat_id}")
                seat = Seat(
                    seat_id=seat_id,
                    generation=generation,
                    clan_id=parents_records[0].seat.clan_id,  # 胜者氏族续脉（谱系可追）
                    gene=gene,
                    hypothesis=hybrid_hypothesis(parents_records[0].seat, parents_records[1].seat, gene),
                    origin="hybrid",
                    parents=tuple(r.seat.seat_id for r in parents_records),
                    parent_estimates=_parent_estimates(*parents_records),
                )
                event = "hybrid_created"
            else:
                parent = parents_records[0]
                seat_id = make_seat_id(generation, self._index, gene, salt=f"mutation:{parent.seat.seat_id}")
                seat = Seat(
                    seat_id=seat_id,
                    generation=generation,
                    clan_id=parent.seat.clan_id,
                    gene=gene,
                    hypothesis=(
                        f"邻域变异：继承 {parent.seat.gene.model_family} 的估计路线与实测量，"
                        f"改换框架 {gene.framework} 做数值推进，先盲预测再取分"
                    ),
                    origin="mutation",
                    parents=(parent.seat.seat_id,),
                    parent_estimates=_parent_estimates(parent),
                )
                event = "mutation_created"
            self._used_signatures.add(gene.family_signature)
            children.append(seat)
            self.audit.append(
                event,
                generation=generation,
                child=seat.seat_id,
                parents=list(seat.parents),
                gene=gene.as_dict(),
                parent_estimates=list(seat.parent_estimates),
            )
        for reason, count in sorted(rejected.items()):
            self.audit.append("candidate_rejected", generation=generation, reason=reason, count=count)
            self._rejections.extend({"generation": generation, "reason": reason} for _ in range(count))
        return children[:remaining_budget]

    # ------------------------------------------------------------------ 派发/取件/打分
    def _execute_seat(self, seat: Seat) -> SeatRecord:
        record = SeatRecord(seat=seat)
        mission = build_mission(self.problem, seat, campaign_id=self.campaign_id)
        mission["goal"] = _compose_goal(self.problem, seat)
        seat.mission = mission

        mission_dir = self.workdir / "missions" / f"G{seat.generation:02d}"
        mission_dir.mkdir(parents=True, exist_ok=True)
        mission_path = mission_dir / f"{seat.seat_id}.json"
        mission_path.write_text(json.dumps(mission, ensure_ascii=False, indent=2, sort_keys=True), "utf-8")
        record.mission_path = str(mission_path.relative_to(self.workdir))
        self.spent += 1
        self.audit.append(
            "seat_spawned",
            seat_id=seat.seat_id,
            generation=seat.generation,
            clan_id=seat.clan_id,
            origin=seat.origin,
            gene=gene_diversity_fields(seat.gene),
            parents=list(seat.parents),
            mission_path=record.mission_path,
            goal_sha256=sha256_text(mission["goal"]),
            budget_spent=self.spent,
            budget_cap=self.problem.max_total_seats,
        )

        artifact = self._collect(mission, record)
        self._score(record, artifact)
        return record

    def _collect(self, mission: dict, record: SeatRecord) -> dict | None:
        seat_id = record.seat.seat_id
        try:
            produced = self.executor(mission)
        except Exception as error:  # 执行器崩溃 = 无工件，如实记账，不掩盖
            self.audit.append("artifact_missing", seat_id=seat_id, error=f"{type(error).__name__}: {error}")
            record.status = "missing"
            record.failure_mode = "no_artifact"
            return None
        if not isinstance(produced, dict):
            self.audit.append("artifact_missing", seat_id=seat_id, error="executor returned non-dict")
            record.status = "missing"
            record.failure_mode = "no_artifact"
            return None

        artifact_dir = self.workdir / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / f"{seat_id}.json"
        artifact_path.write_text(json.dumps(produced, ensure_ascii=False, indent=2, sort_keys=True), "utf-8")
        record.artifact_path = str(artifact_path.relative_to(self.workdir))

        # 真实的二次收集：从磁盘读回再打分（防止内存对象绕过落盘审计）
        collected = json.loads(artifact_path.read_text("utf-8"))
        payload = canonical(collected)
        record.artifact_sha256 = sha256_text(payload)
        record.artifact = collected
        self.audit.append(
            "artifact_collected",
            seat_id=seat_id,
            artifact_path=record.artifact_path,
            sha256=record.artifact_sha256,
            bytes=len(payload),
        )
        return collected

    def _score(self, record: SeatRecord, artifact: dict | None) -> None:
        seat = record.seat
        if artifact is None:
            record.score = 0.0
            record.passed = False
            record.status = "missing"
            self.audit.append("seat_scored", seat_id=seat.seat_id, score=0.0, passed=False,
                              failure_mode=record.failure_mode)
            return

        spoofed = {k: artifact[k] for k in SELF_REPORTED_SCORE_KEYS if k in artifact}
        if spoofed:
            self.audit.append(
                "score_spoof_ignored",
                seat_id=seat.seat_id,
                keys=sorted(spoofed),
                note="席位自报分数一律丢弃：淘汰判据只能来自注入的物理锚打分器",
            )

        try:
            raw = self.scorer(seat.mission, artifact)
        except Exception as error:
            record.status = "invalid"
            record.failure_mode = "scorer_error"
            record.score = 0.0
            self.audit.append("score_invalid", seat_id=seat.seat_id,
                              error=f"{type(error).__name__}: {error}")
            return
        if not isinstance(raw, dict):
            record.status = "invalid"
            record.failure_mode = "scorer_malformed"
            record.score = 0.0
            self.audit.append("score_invalid", seat_id=seat.seat_id, error="scorer returned non-dict")
            return

        score = raw.get("score")
        metrics = raw.get("metrics") or {}
        if not isinstance(score, (int, float)) or not (0.0 <= float(score) <= 1.0):
            record.status = "invalid"
            record.failure_mode = "scorer_malformed"
            record.score = 0.0
            self.audit.append("score_invalid", seat_id=seat.seat_id, error=f"bad score {score!r}")
            return
        if not isinstance(metrics, dict):
            record.status = "invalid"
            record.failure_mode = "scorer_malformed"
            record.score = 0.0
            self.audit.append("score_invalid", seat_id=seat.seat_id, error="metrics not a dict")
            return

        missing = [m for m in self.problem.anchor_metrics if m not in metrics]
        record.score = float(score)
        record.metrics = dict(metrics)
        if missing:
            record.status = "invalid"
            record.failure_mode = "anchor_metric_missing"
            record.passed = False
            self.audit.append(
                "anchor_metric_missing", seat_id=seat.seat_id, missing=missing,
                note="物理锚指标缺失 → 该席位本轮无效（不可用模型自评顶替）",
            )
            self.audit.append("seat_scored", seat_id=seat.seat_id, score=record.score,
                              passed=False, failure_mode=record.failure_mode, metrics=record.metrics)
            return

        # 物理锚：打分器回报 anchor_ok=False 即「违背物理律」→ 一票否决（无论分数多好看）
        anchor_ok = raw.get("anchor_ok")
        record.anchor_ok = True if anchor_ok is None else bool(anchor_ok)
        record.status = "scored"
        record.passed = bool(
            record.anchor_ok and record.score >= self.problem.score_threshold and raw.get("passed") is True
        )
        if record.passed:
            record.failure_mode = "none"
        elif not record.anchor_ok:
            record.failure_mode = "anchor_violation"
        else:
            record.failure_mode = "gate_unmet"
        if not record.anchor_ok:
            self.audit.append(
                "anchor_violation", seat_id=seat.seat_id, score=record.score,
                metrics=record.metrics,
                note="物理锚未过：分数再高也不得晋级/夺冠（淘汰判据是物理，不是模型自评）",
            )
        self.audit.append(
            "seat_scored",
            seat_id=seat.seat_id,
            score=record.score,
            passed=record.passed,
            anchor_ok=record.anchor_ok,
            failure_mode=record.failure_mode,
            metrics=record.metrics,
        )

    # ------------------------------------------------------------------ 裁决
    def _settle_generation(self, generation: int, records: list[SeatRecord]) -> dict:
        ranked = sorted(records, key=lambda r: (-(r.score or 0.0), r.seat.seat_id))
        for position, record in enumerate(ranked, start=1):
            record.rank = position
        passed = [r for r in ranked if r.passed]
        if passed:
            self.champion = passed[0]
            self.champion.note = "champion"
            k = len(ranked)
        else:
            k = promote_count(self.problem, len(ranked))
        promoted = ranked[:k]
        eliminated = ranked[k:]
        for record in promoted:
            record.note = "champion" if record is self.champion else "promoted"
            self.audit.append(
                "seat_promoted", generation=generation, seat_id=record.seat.seat_id,
                rank=record.rank, score=record.score, passed=record.passed,
            )
        for record in eliminated:
            record.note = "eliminated"
            self.audit.append(
                "seat_eliminated", generation=generation, seat_id=record.seat.seat_id,
                rank=record.rank, score=record.score, failure_mode=record.failure_mode,
            )
        self.survivors = promoted
        if self.champion is not None:
            self.audit.append(
                "generation_pass", generation=generation, seat_id=self.champion.seat.seat_id,
                score=self.champion.score, threshold=self.problem.score_threshold,
            )
        else:
            self.audit.append(
                "generation_no_pass", generation=generation,
                best_score=ranked[0].score if ranked else None,
                threshold=self.problem.score_threshold,
            )
        return {
            "generation": generation,
            "seats": [r.seat.seat_id for r in ranked],
            "ranked": [
                {
                    "rank": r.rank,
                    "seat_id": r.seat.seat_id,
                    "clan_id": r.seat.clan_id,
                    "origin": r.seat.origin,
                    "gene": gene_diversity_fields(r.seat.gene),
                    "parents": list(r.seat.parents),
                    "score": r.score,
                    "passed": r.passed,
                    "anchor_ok": r.anchor_ok,
                    "status": r.status,
                    "failure_mode": r.failure_mode,
                    "outcome": r.note,
                    "metrics": r.metrics,
                    "artifact_ref": r.artifact_path,
                }
                for r in ranked
            ],
            "promoted": [r.seat.seat_id for r in promoted],
            "eliminated": [r.seat.seat_id for r in eliminated],
            "passed": [r.seat.seat_id for r in passed],
            "diversity": self._generation_diversity(generation, ranked),
            "hybrids": [r.seat.seat_id for r in ranked if r.seat.origin == "hybrid"],
            "budget_after": self.spent,
        }

    def _generation_diversity(self, generation: int, ranked: Sequence[SeatRecord]) -> dict:
        pairs = []
        for i in range(len(ranked)):
            for j in range(i + 1, len(ranked)):
                a, b = ranked[i].seat, ranked[j].seat
                pairs.append(gene_similarity(a.gene, b.gene, a.hypothesis, b.hypothesis))
        rejections = [r for r in self._rejections if r["generation"] == generation]
        return {
            "families": sorted({r.seat.gene.model_family for r in ranked}),
            "frameworks": sorted({r.seat.gene.framework for r in ranked}),
            "anchors": sorted({r.seat.gene.physics_anchor for r in ranked}),
            "blind_on": sum(1 for r in ranked if r.seat.gene.blind_prediction),
            "distinct_family_signatures": len({r.seat.family_signature for r in ranked}),
            "min_pairwise_similarity": round(min(pairs), 4) if pairs else None,
            "max_pairwise_similarity": round(max(pairs), 4) if pairs else None,
            "dedup_rejections": len(rejections),
            "dedup_rejection_reasons": sorted({r["reason"] for r in rejections}),
        }

    # ------------------------------------------------------------------ 负知识轨
    def _record_negative_knowledge(self) -> None:
        for record in self.records:
            seat = record.seat
            if record is self.champion:
                verdict = "champion"
            elif record.note == "promoted":
                verdict = "retained"
            else:
                verdict = "falsified"
            self.neg.record(
                seat_id=seat.seat_id,
                generation=seat.generation,
                clan_id=seat.clan_id,
                origin=seat.origin,
                gene=gene_diversity_fields(seat.gene),
                hypothesis=seat.hypothesis,
                parents=list(seat.parents),
                verdict=verdict,
                failure_mode=record.failure_mode,
                score=record.score,
                metrics=record.metrics,
                artifact_ref=record.artifact_path,
                do_not_repeat=_do_not_repeat(self.problem, record, verdict),
            )
        self.audit.append(
            "negative_knowledge_recorded",
            count=len(self.neg.records),
            by_failure_mode=self.neg.by_failure_mode(),
            head=self.neg.head,
            note="全席产物入 S-N 负知识轨（含冠军，避免下次重播同一批失败席位）",
        )


def _parent_estimates(*records: "SeatRecord") -> tuple[dict, ...]:
    """父代实测量回流：从父代工件里取出估计量，交给子代做双路平均。"""
    out = []
    for record in records:
        artifact = record.artifact or {}
        estimate = artifact.get("estimate") if isinstance(artifact, dict) else None
        if isinstance(estimate, dict) and estimate:
            out.append({"seat_id": record.seat.seat_id, **dict(estimate)})
    return tuple(out)


def _compose_goal(problem: ProblemSpec, seat: Seat) -> str:
    from .model import build_goal

    lineage = ""
    if seat.parents:
        lineage = f"{seat.origin} of {', '.join(seat.parents)}"
        if seat.parent_estimates:
            lineage += f"；回流实测量 {len(seat.parent_estimates)} 组"
    return build_goal(problem, seat.gene, seat.hypothesis, lineage_note=lineage)


def _do_not_repeat(problem: ProblemSpec, record: SeatRecord, verdict: str) -> str:
    gene = record.seat.gene
    if verdict == "champion":
        return (
            f"{gene.model_family}+{gene.framework} 已达门限（score {record.score:.4f}）："
            f"可作为基线复用，但不能据此宣称其它族同样达标"
        )
    if record.failure_mode == "no_artifact":
        return f"{gene.model_family}+{gene.framework} 未产出工件：执行器不可用，勿计入科学结论"
    if record.failure_mode == "anchor_violation":
        return (
            f"{gene.model_family}+{gene.framework} 违背申报的物理锚（残差 {record.metrics.get('decay_consistency_residual')}）："
            f"该格式不得再用，须换数值格式或细分步长"
        )
    if record.failure_mode == "anchor_metric_missing":
        return f"{gene.model_family}+{gene.framework} 未回报物理锚指标：该组合的分数不可采信"
    score = record.score if record.score is not None else 0.0
    return (
        f"单独投放 {gene.model_family}+{gene.framework} 在该门限下不可行"
        f"（score {score:.4f} < {problem.score_threshold:.4f}）：须换族或与异号偏差族杂交，勿原样重播"
    )


def _tally(values) -> dict:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out