"""锦标赛席位数据模型：多样性基因组、反同族查重、杂交重组算子。

设计纪律（IN25-T1）
-------------------
1. **领域无关**：本模块只认识三样东西——调用方给的 *问题规格*、席位 *基因组*、席位
   *mission 文本*。不含任何船舶/平台业务语义（零域语义），故可被任意领域复用。
2. **多样性三轴**：``model_family``（模型族）× ``framework``（框架）×
   ``blind_prediction``（盲预测：先承诺通过概率再取分，禁见分改口），再叠加
   ``physics_anchor``（物理锚：席位必须承诺遵守的守恒/单调性约束）。四轴全部写进
   mission goal 的「多样性清单」，故派发侧可机读、可查重。
3. **反同族查重**：同代 ``family_signature=(model_family, framework)`` 必须唯一；
   任意两席位相似度（轴相似度与假设词项 Jaccard 各半）不得 ≥ ``sibling_similarity_max``。
   被拒候选不入席位表，但原因入审计（``candidate_rejected_*``），保证「拒了谁」可查。
4. **杂交回流**：子代 genome = 父1 的 ``model_family`` × 父2 的 ``framework``（标准
   交叉），并把父代 *实际估计量* 随 mission 一起回流（``parent_estimates``）——子代不是
   重新抽签，而是在赢家的实测量上继续做工。假设文本显式写明「双路平均」。

本模块全部为纯函数/不可变数据结构，随机性只来自显式传入的 ``random.Random``，
因此同 seed 逐字节可复现（干跑验收项之一）。
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

DIVERSITY_AXES = ("model_family", "framework", "blind_prediction", "physics_anchor")

# 打分器基座白名单：只允许「参考解/实验」锚定的打分器。模型自评（model）被结构性拒绝，
# 这是 Goodhart 免疫的机器门，而不是一句口号。
SCORER_BASIS_ALLOWED = ("reference", "experiment")

# 席位工件里出现这些键，即视为「席位自报分数」——一律丢弃并留痕（防自评污染）。
SELF_REPORTED_SCORE_KEYS = ("score", "self_reported_score", "claimed_score", "my_score")


def canonical(obj: Any) -> str:
    """稳定序列化：排序键 + 紧凑分隔符，供哈希与幂等比对。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short_hash(obj: Any, n: int = 8) -> str:
    return sha256_text(canonical(obj))[:n]


# --------------------------------------------------------------------------------------
# 基因组
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Gene:
    """一个席位的可遗传基因组（四轴）。

    ``model_family``  模型族（估计路线）
    ``framework``     框架（数值推进/工具链）
    ``blind_prediction`` 盲预测开关（True = 先提交通过概率预测再取分）
    ``physics_anchor``   物理锚（席位宣称要满足的守恒/单调性约束名）
    """

    model_family: str
    framework: str
    blind_prediction: bool
    physics_anchor: str

    def as_dict(self) -> dict:
        return {
            "model_family": self.model_family,
            "framework": self.framework,
            "blind_prediction": bool(self.blind_prediction),
            "physics_anchor": self.physics_anchor,
        }

    @property
    def family_signature(self) -> str:
        """反同族查重的硬键：同代不得重复（裸数量 = 同族堆人，正是要禁的退化）。"""
        return f"{self.model_family}|{self.framework}"


# --------------------------------------------------------------------------------------
# 问题规格
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ProblemSpec:
    """锦标赛输入：问题规格 + 机器打分器约定 + 预算/多样性门。"""

    problem_id: str
    title: str
    families: tuple[str, ...]
    frameworks: tuple[str, ...]
    anchors: tuple[str, ...]
    anchor_metrics: tuple[str, ...]  # 打分器必须回报的物理锚指标名（缺一即判无效）
    score_threshold: float = 0.97  # 通过门：score >= threshold（score=1-相对误差）
    max_rel_error: float | None = 0.03  # 并列的误差门，写进报告判据数字
    seats_per_generation: int = 5
    max_generations: int = 3
    max_total_seats: int = 24
    promote_ratio: float = 0.6
    min_promote: int = 2
    min_model_families: int = 2
    sibling_similarity_max: float = 0.80
    blind_options: tuple[bool, ...] = (True, False)
    scorer_basis: str = "reference"
    seed: int = 0

    def validate(self) -> None:
        if self.scorer_basis not in SCORER_BASIS_ALLOWED:
            raise ValueError(
                f"scorer_basis={self.scorer_basis!r} 不被接受：锦标赛的淘汰判据必须是物理/参考锚，"
                f"模型自评打分器不可用（Goodhart 免疫门）。允许值 {SCORER_BASIS_ALLOWED}"
            )
        if not self.families or not self.frameworks or not self.anchors:
            raise ValueError("families/frameworks/anchors 均不得为空")
        if not self.anchor_metrics:
            raise ValueError("anchor_metrics 不得为空：物理锚必须可机验")
        if not (0.0 < self.score_threshold <= 1.0):
            raise ValueError("score_threshold 必须落在 (0,1]")
        if self.seats_per_generation < 1:
            raise ValueError("seats_per_generation >= 1")
        if self.max_generations < 1:
            raise ValueError("max_generations >= 1")
        if self.max_total_seats < 1:
            raise ValueError("max_total_seats >= 1：总席位上限是硬顶，必须给")
        if not (0.0 < self.promote_ratio <= 1.0):
            raise ValueError("promote_ratio 必须落在 (0,1]")
        if not (0.0 <= self.sibling_similarity_max <= 1.0):
            raise ValueError("sibling_similarity_max 必须落在 [0,1]")

    def as_dict(self) -> dict:
        out = {
            "problem_id": self.problem_id,
            "title": self.title,
            "families": list(self.families),
            "frameworks": list(self.frameworks),
            "anchors": list(self.anchors),
            "anchor_metrics": list(self.anchor_metrics),
            "score_threshold": self.score_threshold,
            "max_rel_error": self.max_rel_error,
            "seats_per_generation": self.seats_per_generation,
            "max_generations": self.max_generations,
            "max_total_seats": self.max_total_seats,
            "promote_ratio": self.promote_ratio,
            "min_promote": self.min_promote,
            "min_model_families": self.min_model_families,
            "sibling_similarity_max": self.sibling_similarity_max,
            "blind_options": [bool(b) for b in self.blind_options],
            "scorer_basis": self.scorer_basis,
            "seed": self.seed,
        }
        return out


# --------------------------------------------------------------------------------------
# 席位
# --------------------------------------------------------------------------------------
@dataclass
class Seat:
    """一个氏族席位。``mission`` 是落盘/派发的机器可读载荷。"""

    seat_id: str
    generation: int
    clan_id: str
    gene: Gene
    hypothesis: str
    origin: str = "fresh"  # fresh | hybrid | mutation
    parents: tuple[str, ...] = ()
    parent_estimates: tuple[dict, ...] = ()
    mission: dict = field(default_factory=dict)

    @property
    def family_signature(self) -> str:
        return self.gene.family_signature

    def as_dict(self) -> dict:
        return {
            "seat_id": self.seat_id,
            "generation": self.generation,
            "clan_id": self.clan_id,
            "gene": self.gene.as_dict(),
            "hypothesis": self.hypothesis,
            "origin": self.origin,
            "parents": list(self.parents),
            "parent_estimates": [dict(p) for p in self.parent_estimates],
            "mission_ref": self.mission.get("mission_id", ""),
        }


# --------------------------------------------------------------------------------------
# 相似度 / 反同族查重
# --------------------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.\-+]*|[\u4e00-\u9fff]")


def token_set(text: str) -> frozenset[str]:
    return frozenset(_TOKEN_RE.findall((text or "").lower()))


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def gene_similarity(g1: Gene, g2: Gene, hyp1: str = "", hyp2: str = "") -> float:
    """两席位相似度 = 0.5×轴相似度 + 0.5×假设词项 Jaccard（∈[0,1]，越大越像）。

    轴相似度按四轴等权：``blind_prediction`` 也计入（盲/不盲是不同的科学行为）。
    """
    axes1 = (g1.model_family, g1.framework, bool(g1.blind_prediction), g1.physics_anchor)
    axes2 = (g2.model_family, g2.framework, bool(g2.blind_prediction), g2.physics_anchor)
    axis_sim = sum(1 for a, b in zip(axes1, axes2) if a == b) / len(axes1)
    tok_sim = jaccard(token_set(hyp1), token_set(hyp2))
    return 0.5 * axis_sim + 0.5 * tok_sim


def admissibility(candidate: Gene, hypothesis: str, chosen: Sequence[Seat], problem: ProblemSpec) -> tuple[bool, str]:
    """反同族查重：同代硬键唯一 + 相似度上限。返回 (是否准入, 拒因)。"""
    sigs = {s.family_signature for s in chosen}
    if candidate.family_signature in sigs:
        return False, "family_signature_duplicate"
    for seat in chosen:
        sim = gene_similarity(candidate, seat.gene, hypothesis, seat.hypothesis)
        if sim >= problem.sibling_similarity_max:
            return False, f"sibling_similarity={sim:.3f}"
    return True, ""


# --------------------------------------------------------------------------------------
# mission 文本：多样性清单注入 goal
# --------------------------------------------------------------------------------------
def diversity_clause(gene: Gene) -> str:
    """多样性清单（四轴 key=value，机读友好；任务书要求 seed mission 里必须带）。"""
    return (
        "多样性清单（四轴必填·同代禁同族）：\n"
        f"  - model_family = {gene.model_family}\n"
        f"  - framework = {gene.framework}\n"
        f"  - blind_prediction = {'on' if gene.blind_prediction else 'off'}\n"
        f"  - physics_anchor = {gene.physics_anchor}"
    )


def build_goal(problem: ProblemSpec, gene: Gene, hypothesis: str, lineage_note: str = "") -> str:
    """席位 mission 的 goal：多样性清单 + 假设 + 交付/纪律约束，全部注入文本。"""
    parts = [
        f"[{problem.problem_id}] 锦标赛氏族席位：{problem.title}",
        f"科学假设：{hypothesis}",
        diversity_clause(gene),
        f"通过门：score >= {problem.score_threshold:.4f}"
        + (f"（等价相对误差 <= {problem.max_rel_error:.4f}）" if problem.max_rel_error is not None else ""),
        f"物理锚必报指标：{', '.join(problem.anchor_metrics)}（打分器为物理/参考锚，非模型自评）",
        "交付物：artifact.json（预测序列 + 锚量残差 + 盲预测概率）；",
        "纪律：禁止自报分数（自报值一律丢弃并入审计）；盲预测席位必须先写下 predicted_pass_prob 再取分。",
    ]
    if lineage_note:
        parts.insert(2, f"谱系：{lineage_note}")
    return "\n".join(parts)


def build_mission(problem: ProblemSpec, seat: Seat, campaign_id: str = "") -> dict:
    """席位 mission 载荷（可直接 POST /tensor/missions 的形态）。"""
    return {
        "mission_id": seat.seat_id,
        "title": f"{problem.problem_id} G{seat.generation} {seat.gene.model_family}@{seat.gene.framework}",
        "campaign_id": campaign_id or problem.problem_id,
        "campaign_phase": f"G{seat.generation}",
        "clan_id": seat.clan_id,
        "generation": seat.generation,
        "origin": seat.origin,
        "parents": list(seat.parents),
        "parent_estimates": [dict(p) for p in seat.parent_estimates],
        "goal": seat.mission.get("goal", ""),
        "acceptance_criteria": [
            f"物理锚 {m} 残差可机验且达标" for m in problem.anchor_metrics
        ]
        + [f"score >= {problem.score_threshold:.4f}"],
        "diversity": gene_diversity_fields(seat.gene),
        "tags": [
            "tournament",
            f"generation:{seat.generation}",
            f"clan:{seat.clan_id}",
            f"origin:{seat.origin}",
            f"family:{seat.gene.model_family}",
        ],
    }


def gene_diversity_fields(gene: Gene) -> dict:
    return {
        "model_family": gene.model_family,
        "framework": gene.framework,
        "blind_prediction": bool(gene.blind_prediction),
        "physics_anchor": gene.physics_anchor,
        "family_signature": gene.family_signature,
    }


# --------------------------------------------------------------------------------------
# 候选池 / 抽样 / 杂交
# --------------------------------------------------------------------------------------
def candidate_pool(problem: ProblemSpec) -> list[Gene]:
    """全基因组空间（笛卡尔积，定序，供确定性抽样）。"""
    pool = []
    for family in problem.families:
        for framework in problem.frameworks:
            for blind in problem.blind_options:
                for anchor in problem.anchors:
                    pool.append(Gene(family, framework, bool(blind), anchor))
    return pool


def fresh_hypothesis(gene: Gene) -> str:
    blind = "先盲预测再取分" if gene.blind_prediction else "见分后判定"
    return (
        f"{gene.model_family} 路线在 {gene.framework} 框架内推进估计，"
        f"以 {gene.physics_anchor} 作为物理锚，{blind}"
    )


def select_fresh(
    problem: ProblemSpec,
    rng: random.Random,
    count: int,
    chosen: Sequence[Seat],
    used_family_signatures: set[str],
) -> tuple[list[tuple[Gene, str]], list[dict]]:
    """确定性抽样：按随机序扫候选池，跳过同族/近同族；返回 (准入候选, 拒绝记录)。"""
    pool = candidate_pool(problem)
    rng.shuffle(pool)
    picks: list[tuple[Gene, str]] = []
    rejections: list[dict] = []
    for gene in pool:
        if len(picks) >= count:
            break
        hypothesis = fresh_hypothesis(gene)
        if gene.family_signature in used_family_signatures:
            rejections.append({"gene": gene.as_dict(), "reason": "family_signature_used_earlier"})
            continue
        ok, reason = admissibility(gene, hypothesis, list(chosen) + [Seat("", 0, "", g, h) for g, h in picks], problem)
        if not ok:
            rejections.append({"gene": gene.as_dict(), "reason": reason})
            continue
        picks.append((gene, hypothesis))
    return picks, rejections


def hybrid_gene(problem: ProblemSpec, best: Seat, other: Seat) -> Gene:
    """交叉算子：胜者 ``model_family`` × 次席 ``framework``，盲预测强制回 on，锚继承胜者。"""
    return Gene(
        model_family=best.gene.model_family,
        framework=other.gene.framework,
        blind_prediction=True,
        physics_anchor=best.gene.physics_anchor,
    )


def mutation_gene(problem: ProblemSpec, parent: Seat, used_signatures: Iterable[str]) -> Gene:
    """单亲邻域变异：优先「同族换框架」，其次「同框架换族」，都占满则原基因组返回。

    ``used_signatures`` 是本轮已投放的 ``family_signature`` 全集——变异同样受反同族查重
    约束，否则锦标赛会在第 3 代把第 1 代的组合原样重播（裸数量的典型退化）。
    """
    used = set(used_signatures)
    for framework in problem.frameworks:
        if framework == parent.gene.framework:
            continue
        gene = Gene(parent.gene.model_family, framework, True, parent.gene.physics_anchor)
        if gene.family_signature not in used:
            return gene
    for family in problem.families:
        if family == parent.gene.model_family:
            continue
        gene = Gene(family, parent.gene.framework, True, parent.gene.physics_anchor)
        if gene.family_signature not in used:
            return gene
    return Gene(parent.gene.model_family, parent.gene.framework, True, parent.gene.physics_anchor)


def hybrid_hypothesis(best: Seat, other: Seat, gene: Gene) -> str:
    return (
        f"杂交重组：以 {best.gene.model_family} 与 {other.gene.model_family} 两路参数估计取平均"
        f"（独立模型形偏差部分相消），时间推进继承 {gene.framework}，物理锚 {gene.physics_anchor}，先盲预测再取分"
    )


def make_seat_id(generation: int, index: int, gene: Gene, salt: str = "") -> str:
    return f"T{generation:02d}-S{index:02d}-{short_hash({'gene': gene.as_dict(), 'salt': salt})}"


def promote_count(problem: ProblemSpec, surviving: int) -> int:
    """晋级席位数：max(min_promote, ceil(n*promote_ratio))，不超过 n-1（至少淘汰 1 席）。"""
    if surviving <= 1:
        return surviving
    k = max(problem.min_promote, math.ceil(surviving * problem.promote_ratio))
    return max(1, min(k, surviving - 1))