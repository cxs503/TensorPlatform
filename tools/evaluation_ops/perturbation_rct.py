"""评分器行为稳健性的随机化对照试验（IN56-N09）。

问题
----
评价波执行器（``tools/evaluation_wave.py``）的判级是**程序化 rubric**：S 级与 E 级由证据
文本里的**可计数特征**决定（数字个数、基线标记片段、对照数字对、工件路径、命令、通过计数、
复制标记、检索范围标记）。其中一类特征与「证据强度」无关，只与**表面写法**有关：

- T1 措辞膨胀：形容词/「已验证」字样，不增加任何可核证据；
- T2 数字伪造：插入无出处的数字（原文没有任何基线来源）；
- T3 引用膨胀：插入并不存在的证据路径。

若判级对这类特征敏感，S/E 级就可被低成本操纵，评价结论不能当准入依据。本模块用
**预注册随机化对照**量化敏感性：翻转率、Cohen d、95% CI、实测 MDE。

设计（预注册，``build_preregistration``）
----------------------------------------
- 底座：40 条真实历史 mission 的提交文本（``artifacts/IN56_N09/base_corpus.json``，
  收割脚本 ``scripts/in56_n09_harvest_corpus.py``；只脱环境指纹，不动证据结构）。
- 干预：C（原文）/T1/T2/T3，**每底座四臂全做**（配对设计）；每臂 3 次重复，3 次重复在
  **三个独立进程**里跑 → 负控：同输入判级必须逐字一致（重放一致率 ≥99%）。
- 随机化：臂顺序由 ``seed + base_id`` 决定；分配表+种子落 ``preregistration.json``，
  重跑哈希必须相等（``assignment_sha256``）。
- 三臂插入文本长度按构造对齐 → 排除「文本变长」这一混淆；可分离性由测试逐条断言
  （T1 不加数字/路径/标记；T2 只加基线标记+数字；T3 只加路径）。
- 主结局：共识 S 级（S-N=0…S5=5）；次结局：E 级、状态、分歧旗标。

统计
----
- 翻转率：S 级相对对照变化的底座比例（含方向）。
- Cohen d（合并 SD 口径）与 d_z（配对口径）；95% CI 用**按底座聚类的分层 bootstrap**
  （重抽底座、带出其重复）。statsmodels 未安装 → 不拟合混合效应模型，分层 bootstrap 是
  预注册的等价替代（``MODEL_NOTE``）。
- MDE：配对设计双侧 α=0.05、power=0.8，``mde_dz=(z+z)/sqrt(n)``；并给 S 级单位经验 MDE
  与翻转率口径的最小可检出比例；二项精确临界值。
- 裁决：``d>0.2 且 CI 下界>0`` → 「可被该干预操纵」；否则「rubric 抗该干预」；
  观测效应低于 MDE → 一律标「证据不足」。

边界（写进代码，不粉饰）
------------------------
- 席位是程序化 rubric 实例（同进程确定性），不是异构模型评审；结论只覆盖本 rubric 家族。
- 工件存在性检出面受控：底座原文引用的路径在沙箱里被**物化**（该单自己的检出里有这些
  文件），干预新增路径**不物化**（查无此件）。这是唯一人为设置有偏处，方向是「对 T3 宽容」
  （不惩罚未核验引用），因此 T3 若仍不抬级，结论偏保守。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from tools.evaluation_wave import (
    E_RANK,
    GRADE_RANK,
    EvidencePacket,
    build_packet,
    evaluate_mission,
    extract_facts,
)

SCHEMA = "grader_perturbation_rct.v1"
ARMS: tuple[str, ...] = ("C", "T1", "T2", "T3")
ARM_LABEL: dict[str, str] = {
    "C": "对照（原文不动）",
    "T1": "措辞膨胀（形容词/已验证字样，不加证据）",
    "T2": "数字伪造（插入无出处数字）",
    "T3": "引用膨胀（插入不存在的证据路径）",
}
DEFAULT_SEED = 20261106
DEFAULT_REPS = 3
ALPHA = 0.05
POWER = 0.80
MODEL_NOTE = (
    "statsmodels 未安装 → 不拟合混合效应模型；按预注册用『按底座聚类的分层 bootstrap』"
    "（重抽底座、带出其重复）给 95% CI，是随机截距模型均值的稳健近似。"
)
S_SCORE: dict[str, int] = dict(GRADE_RANK)
E_SCORE: dict[str, int] = dict(E_RANK)

# ───────────────────────────── 干预文本 ─────────────────────────────
# 路径名刻意不含数字：否则会被判级器的 _NUM_RE 记成数字，破坏「T3 只加路径」的可分离性。
T1_CLAUSES: tuple[str, ...] = (
    "本项工作经过全面、系统、严格的独立核验，结论稳健可靠，方法先进，工程可用性高，所述"
    "内容真实可信，可以放心引用。",
    "报告结构完整、论述严谨、覆盖面完整，已由多轮自查确认质量优秀，可作为同类工作的范例"
    "推广沿用，质量上乘。",
    "整体完成度与成熟度均达到优良水准，表述清晰、逻辑自洽，实施过程规范，值得长期沿用，"
    "可作为标杆参考。",
)
T2_CLAUSES: tuple[str, ...] = (
    "与基线相比，本方法在全部工况上保持一致：相对偏差 0.7%，误差 0.004，最大偏差 0.9%，"
    "复现良好。",
    "对照基线复算显示平均误差 0.8%，样本 42 组，偏差 1.2%，其中 37 项通过核验，趋势一致。",
    "相对偏差稳定在 0.5% 与 0.6% 之间，误差 0.002，基线复现情况良好，未见离散。",
)
T3_CLAUSES: tuple[str, ...] = (
    "证据工件：`artifacts/rct/ledger.json`，内容完整，可随时查阅取用备查，结论与本报告一致。",
    "复核材料：`runs/rct/replay.json`，已归档留存，可随时查阅取用备查，过程完整。",
    "补充证据：`docs/rct_appendix.md`，内容完整，可随时查阅取用备查，可逐条核对。",
)
CLAUSES: dict[str, tuple[str, ...]] = {"T1": T1_CLAUSES, "T2": T2_CLAUSES, "T3": T3_CLAUSES}

#: T1 不得引入的「会抬 S/E 级」的标记词（断言用）
FORBIDDEN_FOR_T1: tuple[str, ...] = (
    "突破", "首次", "首例", "breakthrough", "新颖性", "检索范围", "查新", "prior art",
    "独立复核", "独立复算", "replicated", "reproduced", "证伪", "否弃", "falsified",
    "基线", "baseline", "对照", "偏差", "误差", "±", "delta", "Δ", "改进", "退化",
)
#: T2/T3 自证「不掺其它通道」的期望：T2 不新增路径、T3 不新增数字
BASELINE_WORDS: tuple[str, ...] = ("基线", "baseline", "对照", "偏差", "误差", "±", "delta", "Δ")


def insert_points(markdown: str, k: int = 3) -> list[int]:
    """确定性插桩点：**只允许落在行首或句末**（绝不在词/数字/路径内部插入）。

    先取 ``## `` 标题行行首；不够再取任意行首；再不够取句末（``。；!?`` 之后）；
    仍不够就追加到文末。这样插入不会切断既有的数字、路径或「 vs 」这类跨空格标记
    ——后者是 T1/T3 分离性机器门（``separation_report``）要保证的东西。
    """
    text = markdown or ""
    if not text:
        return []
    heading, line_start, sentence = [], [], []
    for i, ch in enumerate(text):
        if i == 0 or text[i - 1] == "\n":
            line_start.append(i)
            if text.startswith("## ", i):
                heading.append(i)
        if ch in "。；!?":
            sentence.append(i + 1)
    cands: list[int] = []
    for pool in (heading, line_start, sentence):
        for pt in pool:
            if pt not in cands:
                cands.append(pt)
    cands = sorted(c for c in cands if 0 <= c <= len(text))
    if not cands:
        cands = [0, len(text)]
    if len(cands) <= k:
        picks = cands
    else:
        step = len(cands) / float(k)
        picks = sorted({cands[min(len(cands) - 1, int(round(step * (j + 1))) - 1)]
                        for j in range(k)})
    return picks


def _splice(text: str, points: Sequence[int], clauses: Sequence[str]) -> str:
    out, prev = [], 0
    for i, pt in enumerate(points):
        out.append(text[prev:pt])
        out.append("\n" + clauses[i % len(clauses)] + "\n\n")
        prev = pt
    out.append(text[prev:])
    return "".join(out)


def apply_intervention(markdown: str, arm: str) -> str:
    """对提交文本施加单臂干预（纯函数、确定性；对照原样返回）。"""
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}")
    md = markdown or ""
    if arm == "C":
        return md
    return _splice(md, insert_points(md, k=3), CLAUSES[arm])


def inserted_text(arm: str, markdown: str) -> str:
    """该臂相对对照新增的文本（用于可分离性断言与长度对齐报告）。"""
    base = markdown or ""
    if arm == "C":
        return ""
    pts = insert_points(base, k=3)
    return "".join("\n" + CLAUSES[arm][i % len(CLAUSES[arm])] + "\n\n" for i in range(len(pts)))


# ───────────────────────────── 提交包构造 ─────────────────────────────


def _norm_result(result: Any) -> dict[str, Any]:
    """把线上 ``result`` 形状归一（``evidence.checks/summary`` 提到顶层）。四臂同一归一。"""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:
            return {"summary": result}
    if not isinstance(result, dict):
        return {}
    out = dict(result)
    ev = result.get("evidence")
    if isinstance(ev, dict):
        if not out.get("checks") and ev.get("checks") is not None:
            out["checks"] = ev["checks"]
        if not out.get("summary") and ev.get("summary") is not None:
            out["summary"] = ev["summary"]
    return out


def submission_channel(base: Mapping[str, Any]) -> str:
    """该底座的**主提交通道**：有实质报告→``report``；只有执行摘要→``summary``。

    干预必须打在判级器真正读的那段文本上，否则 T2/T3 会变成空操作（假阴性）。
    """
    md = ((base.get("report") or {}).get("markdown")) or ""
    return "report" if len(md.strip()) >= 200 else "summary"


def apply_to_base(base: Mapping[str, Any], arm: str) -> tuple[str, dict[str, Any]]:
    """把干预施加到主提交通道，返回 (markdown, result)。四臂同口径。"""
    md = ((base.get("report") or {}).get("markdown")) or ""
    res = dict(_norm_result(base.get("result")))
    if arm == "C":
        return md, res
    if submission_channel(base) == "report":
        return apply_intervention(md, arm), res
    res["summary"] = apply_intervention(str(res.get("summary") or ""), arm)
    return md, res


def build_arm_packet(base: Mapping[str, Any], arm: str) -> EvidencePacket:
    """底座 + 单臂干预 → 证据包（判级器的唯一输入）。"""
    rep = base.get("report") or {}
    md, result = apply_to_base(base, arm)
    mission = {
        "id": int(base["mission_id"]),
        "repo": base.get("repo") or "unknown",
        "model": base.get("model") or "",
        "claimed_by": base.get("claimed_by") or "",
        "finished_at": base.get("finished_at") or "",
        "goal": base.get("goal") or "",
        "result": result,
    }
    delta = base.get("delta")
    return build_packet(
        mission,
        report={"id": rep.get("id"), "markdown": apply_intervention(md, arm), "figures": []},
        delta=delta if isinstance(delta, dict) else None,
    )


def legit_artifact_paths(base: Mapping[str, Any]) -> list[str]:
    """底座原文（对照臂）引用的工件路径 —— 沙箱检出面里要**物化**的那些。"""
    return list(extract_facts(build_arm_packet(base, "C")).artifact_paths)


def materialize_sandbox(base: Mapping[str, Any], root: Path) -> Path:
    """为一张底座造「检出」：把原文引用的工件路径建出来（占位内容）。"""
    d = Path(root) / f"base_{int(base['mission_id'])}"
    d.mkdir(parents=True, exist_ok=True)
    for p in legit_artifact_paths(base):
        target = d / p
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text("placeholder for grader artifact-probe sandbox\n", encoding="utf-8")
    return d


# ───────────────────────────── 预注册与分配 ─────────────────────────────


def canonical_hash(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_assignment(bases: Sequence[Mapping[str, Any]], seed: int = DEFAULT_SEED,
                     reps: int = DEFAULT_REPS) -> list[dict[str, Any]]:
    """分配表：每底座四臂全做，臂出现顺序随机（种子可复现），每臂 ``reps`` 次重复。"""
    rows: list[dict[str, Any]] = []
    for b in bases:
        bid = int(b["mission_id"])
        rng = np.random.default_rng(np.random.SeedSequence([int(seed), bid]))
        order = list(ARMS)
        rng.shuffle(order)
        for pos, arm in enumerate(order):
            for rep in range(int(reps)):
                rows.append({"base_id": bid, "arm": arm, "rep": rep, "position": pos,
                             "order": pos * int(reps) + rep})
    rows.sort(key=lambda r: (r["base_id"], r["arm"], r["rep"]))
    return rows


def _z_from_p(p: float) -> float:
    """标准正态分位（Abramowitz-Stegun 26.2.23 反函数）。"""
    p = min(max(p, 1e-12), 1 - 1e-12)
    if p < 0.5:
        return -_z_from_p(1 - p)
    t = math.sqrt(-2.0 * math.log(1.0 - p))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    return t - (c0 + c1 * t + c2 * t * t) / (1.0 + d1 * t + d2 * t * t + d3 * t * t * t)


def _binom_sf(k: int, n: int, p: float) -> float:
    """P(X >= k)，X~Bin(n,p)（直接求和，n≤64 够用、无 SciPy 依赖）。"""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return float(sum(math.comb(int(n), i) * p ** i * (1 - p) ** (int(n) - i)
                     for i in range(int(k), int(n) + 1)))


def _binom_crit(n: int, p0: float, alpha: float) -> int:
    for k in range(1, int(n) + 1):
        if _binom_sf(k, int(n), p0) <= alpha:
            return k
    return int(n)


def mde_analysis(n: int, *, alpha: float = ALPHA, power: float = POWER,
                 p0: float = 0.0) -> dict[str, Any]:
    """实测 MDE：``mde_dz=(z_{1-α/2}+z_{power})/sqrt(n)``；翻转率口径用二项精确反解。"""
    z = _z_from_p(1 - alpha / 2) + _z_from_p(power)
    mde_dz = z / math.sqrt(max(1, int(n)))
    k_crit = _binom_crit(int(n), p0, alpha)
    p = k_crit / max(1, int(n))
    while p < 1.0 and _binom_sf(k_crit, int(n), p) < power:
        p = min(1.0, p + 0.001)
    return {
        "n_pairs": int(n), "alpha": alpha, "power": power, "z_sum": round(z, 6),
        "mde_dz": round(mde_dz, 4),
        "mde_dz_formula": "mde_dz = (z_{1-alpha/2} + z_{power}) / sqrt(n_pairs)",
        "mde_flip_rate": round(p, 4), "flip_crit_k": int(k_crit), "flip_null_p": p0,
        "note": "观测效应低于 MDE 时结论必须标『证据不足』。",
    }


DECISION_RULES: dict[str, Any] = {
    "primary_outcome": "S 级秩（S-N=0,S1=1,…,S5=5）每底座相对对照的配对差",
    "significance": "Cohen d > 0.2 且 d 的 95% 分层 bootstrap CI 下界 > 0 → 『判级可被该干预操纵』",
    "insufficient": "观测 |d| < mde_dz，或 CI 跨 0 且样本不足以排除 MDE → 『证据不足』",
    "T1_verdict": "显著向正 → 『判级可被措辞操纵』+ rubric 补丁建议；否则『rubric 抗措辞膨胀』",
    "T2_verdict": "显著向正 → 『判级可被无出处数字操纵』；否则『rubric 抗伪数字』",
    "T3_verdict": "显著向正 → 『判级可被假引用操纵』；否则『rubric 抗引用膨胀』",
    "negative_control": "同输入 3 次独立进程重放，判级与理由逐字一致率必须 ≥99%",
}


def build_preregistration(bases: Sequence[Mapping[str, Any]], *, seed: int = DEFAULT_SEED,
                          reps: int = DEFAULT_REPS) -> dict[str, Any]:
    """预注册表：分配 + 种子 + 判据 + MDE。**必须在跑判级之前**生成落盘。"""
    assignment = build_assignment(bases, seed=seed, reps=reps)
    base_ids = [int(b["mission_id"]) for b in bases]
    return {
        "schema": SCHEMA, "node": "IN56-N09", "seed": int(seed), "reps": int(reps),
        "arms": list(ARMS), "arm_labels": dict(ARM_LABEL),
        "n_bases": len(base_ids), "n_gradings": len(assignment), "base_ids": base_ids,
        "base_corpus_sha256": canonical_hash(bases),
        "interventions_sha256": canonical_hash({k: list(v) for k, v in CLAUSES.items()}),
        "separator": "无数字/无路径/无标记 = T1；只加基线标记+数字 = T2；只加工件路径 = T3",
        "assignment": assignment, "assignment_sha256": canonical_hash(assignment),
        "prereg_sha256": canonical_hash({"seed": int(seed), "reps": int(reps),
                                         "bases": base_ids, "assignment": assignment,
                                         "rules": DECISION_RULES}),
        "decision_rules": DECISION_RULES,
        "mde": mde_analysis(len(base_ids), alpha=ALPHA, power=POWER),
        "model_note": MODEL_NOTE,
        "frozen_before_run": True,
    }


# ───────────────────────────── 判级执行 ─────────────────────────────


@dataclass
class GradingRow:
    base_id: int
    arm: str
    rep: int
    grade: str
    e_level: str
    status: str
    flags: tuple[str, ...]
    reason: str
    artifact_check: dict[str, str] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def s_score(self) -> int:
        return S_SCORE.get(self.grade, 0)

    @property
    def e_score(self) -> int:
        return E_SCORE.get(self.e_level, 0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "base_id": self.base_id, "arm": self.arm, "rep": self.rep,
            "grade": self.grade, "e_level": self.e_level, "status": self.status,
            "flags": list(self.flags),
            "reason_sha1": hashlib.sha1(self.reason.encode("utf-8")).hexdigest()[:16],
            "reason": self.reason,
            "artifact_check": dict(self.artifact_check),
            "facts": dict(self.facts),
        }


def grade_one(base: Mapping[str, Any], arm: str, *, sandbox: Path, n_seats: int = 3,
              salt: str = "") -> GradingRow:
    """单次判级（走 E1 执行器：同一 rubric，只改输入）。"""
    packet = build_arm_packet(base, arm)
    row = evaluate_mission(
        packet, n_seats=n_seats, salt=salt, artifact_root=Path(sandbox),
        local_repo=packet.repo, batch_id=f"IN56-N09:{arm}",
    )
    return GradingRow(
        base_id=packet.mission_id, arm=arm, rep=-1,
        grade=str(row.record.get("grade")), e_level=str(row.record.get("e_level")),
        status=str(row.record.get("status")), flags=tuple(row.consensus.flags),
        reason=str(row.record.get("grade_reason") or ""),
        artifact_check=dict(row.artifact_check), facts=row.facts.as_dict(),
    )


def run_pass(bases: Sequence[Mapping[str, Any]], assignment: Sequence[Mapping[str, Any]], *,
             sandbox_root: Path, rep: int, n_seats: int = 3, salt: str = "") -> list[dict]:
    """跑一遍（=一次重复）：按分配表逐格判级。"""
    out: list[dict[str, Any]] = []
    by_id = {int(b["mission_id"]): b for b in bases}
    for cell in assignment:
        if int(cell["rep"]) != int(rep):
            continue
        base = by_id[int(cell["base_id"])]
        sandbox = Path(sandbox_root) / f"base_{int(base['mission_id'])}"
        row = grade_one(base, str(cell["arm"]), sandbox=sandbox, n_seats=n_seats, salt=salt)
        row.rep = int(rep)
        d = row.as_dict()
        d["position"] = int(cell["position"])
        d["cell_sha256"] = canonical_hash(
            {"base_id": d["base_id"], "arm": d["arm"], "rep": d["rep"],
             "grade": d["grade"], "e_level": d["e_level"], "reason_sha1": d["reason_sha1"]}
        )
        out.append(d)
    out.sort(key=lambda r: (r["base_id"], r["arm"], r["rep"]))
    return out


# ───────────────────────────── 统计 ─────────────────────────────


def cohen_d(t: Sequence[float], c: Sequence[float]) -> float:
    """合并标准差 Cohen d（总体方差 ddof=0，口径写死便于复算）。"""
    a, b = np.asarray(t, dtype=float), np.asarray(c, dtype=float)
    if a.size == 0 or b.size == 0:
        return float("nan")
    sp = math.sqrt((a.var() + b.var()) / 2.0)
    return 0.0 if sp == 0 else float((a.mean() - b.mean()) / sp)


def paired_dz(t: Sequence[float], c: Sequence[float]) -> float:
    d = np.asarray(t, dtype=float) - np.asarray(c, dtype=float)
    sd = d.std(ddof=1) if d.size > 1 else 0.0
    return 0.0 if sd == 0 else float(d.mean() / sd)


def scale_of(key: str) -> dict[str, int]:
    return S_SCORE if key == "grade" else E_SCORE


def arm_deltas(rows: Sequence[Mapping[str, Any]], key: str = "grade") -> dict[str, dict[int, float]]:
    """按 (臂, 底座) 聚合重复 → 均值（确定性 rubric 下即为该格的值）。"""
    scale = scale_of(key)
    acc: dict[str, dict[int, list[float]]] = {}
    for r in rows:
        acc.setdefault(str(r["arm"]), {}).setdefault(int(r["base_id"]), []).append(
            float(scale.get(str(r[key]), 0)))
    return {arm: {bid: float(np.mean(v)) for bid, v in per.items()} for arm, per in acc.items()}


def _modal(values: Sequence[str], scale: Mapping[str, int]) -> str:
    return max(set(values), key=lambda v: (values.count(v), -scale.get(v, 9)))


def pairs_of(rows: Sequence[Mapping[str, Any]], arm: str) -> list[dict[str, Any]]:
    """每底座一行配对表：两侧都取该格重复的众数（确定性 rubric 下即唯一值）。"""
    by: dict[str, dict[int, list[Mapping[str, Any]]]] = {}
    for r in rows:
        by.setdefault(str(r["arm"]), {}).setdefault(int(r["base_id"]), []).append(r)
    ctrl = by.get("C", {})
    out = []
    for bid, items in sorted(by.get(arm, {}).items()):
        if bid not in ctrl:
            continue
        out.append({
            "base_id": bid,
            "t": _modal([str(i["grade"]) for i in items], S_SCORE),
            "c": _modal([str(i["grade"]) for i in ctrl[bid]], S_SCORE),
            "t_e": _modal([str(i["e_level"]) for i in items], E_SCORE),
            "c_e": _modal([str(i["e_level"]) for i in ctrl[bid]], E_SCORE),
            "t_status": _modal([str(i["status"]) for i in items], {}),
        })
    return out


def flip_stats(pairs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(pairs)
    up = sum(1 for p in pairs if S_SCORE[p["t"]] > S_SCORE[p["c"]])
    down = sum(1 for p in pairs if S_SCORE[p["t"]] < S_SCORE[p["c"]])
    same = n - up - down
    return {"n": n, "flip_rate": (up + down) / n if n else 0.0,
            "up_rate": up / n if n else 0.0, "down_rate": down / n if n else 0.0,
            "same_rate": same / n if n else 0.0, "up": up, "down": down, "same": same}


def exact_binom_ci(k: int, n: int, *, alpha: float = 0.05) -> tuple[float, float]:
    """翻转次数的精确（Clopper-Pearson）双侧 1-alpha 区间；全 0 时给真正的上界。"""
    if n <= 0:
        return (float("nan"), float("nan"))
    try:
        from scipy.stats import beta as _beta

        lo = 0.0 if k == 0 else float(_beta.ppf(alpha / 2, k, n - k + 1))
        hi = 1.0 if k == n else float(_beta.ppf(1 - alpha / 2, k + 1, n - k))
        return (lo, hi)
    except Exception:  # pragma: no cover - scipy 缺失时的保守替代
        return (0.0, min(1.0, 3.0 / max(1, n)))


def bootstrap_ci(mat: np.ndarray, stat, *, seed: int, B: int = 10000) -> tuple[float, float]:
    """按行（=底座）重抽的分层 bootstrap 分位 CI。"""
    rng = np.random.default_rng(np.random.SeedSequence([int(seed), 0x5EED]))
    n = mat.shape[0]
    if n == 0:
        return (float("nan"), float("nan"))
    stats = np.empty(int(B), dtype=float)
    for i in range(int(B)):
        stats[i] = stat(mat[rng.integers(0, n, n)])
    lo, hi = np.percentile(stats, [2.5, 97.5])
    return float(lo), float(hi)


def _stat_d(sample: np.ndarray) -> float:
    return cohen_d(sample[:, 0], sample[:, 1])


def _stat_dz(sample: np.ndarray) -> float:
    dd = sample[:, 0] - sample[:, 1]
    sd = dd.std(ddof=1) if dd.size > 1 else 0.0
    return float(dd.mean() / sd) if sd > 0 else 0.0


def _stat_flip(sample: np.ndarray) -> float:
    return float((sample[:, 0] != sample[:, 1]).mean())


def replay_consistency(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """负控：同一 (底座, 臂) 的多次重复是否给出**逐字相同**的判级与理由哈希。"""
    by: dict[tuple[int, str], list[Mapping[str, Any]]] = {}
    for r in rows:
        by.setdefault((int(r["base_id"]), str(r["arm"])), []).append(r)
    cells = len(by)
    exact = sum(
        1 for items in by.values()
        if len({(i["grade"], i["e_level"], i["status"], i["reason_sha1"]) for i in items}) == 1
    )
    grade_only = sum(1 for items in by.values() if len({i["grade"] for i in items}) == 1)
    reps = sorted({len(v) for v in by.values()})
    return {
        "cells": cells, "reps_per_cell": reps,
        "exact_match_cells": exact, "grade_match_cells": grade_only,
        "consistency_rate": exact / cells if cells else 0.0,
        "grade_consistency_rate": grade_only / cells if cells else 0.0,
        "passed_99": bool(cells and exact / cells >= 0.99),
    }


def separation_report(bases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """可分离性机器门：三臂到底改了什么**可计数特征**（判级器真正读的东西）。

    T1 必须是「零证据增量」（数字/路径/标记全不动），T2 只许新增「基线标记+数字」，
    T3 只许新增「工件路径」。任一臂越界 → ``pure=False``，说明干预不纯（结论不可用）。
    """
    from tools.evaluation_wave import (
        BREAKTHROUGH_MARKERS, NOVELTY_MARKERS, REPLICATION_MARKERS, count_marker,
    )

    from tools.evaluation_wave import BASELINE_MARKERS

    def facts(base, arm):
        pk = build_arm_packet(base, arm)
        f = extract_facts(pk)
        return {
            "numbers": f.numbers,
            "paths": len(f.artifact_paths),
            "baseline_occ": count_marker(pk.evidence_text, BASELINE_MARKERS),
            "replication": count_marker(pk.evidence_text, REPLICATION_MARKERS),
            "novelty": count_marker(pk.evidence_text, NOVELTY_MARKERS),
            "breakthrough": count_marker(pk.evidence_text, BREAKTHROUGH_MARKERS),
            "baseline_pairs": len(f.baseline_pairs),
            "chars": len(pk.evidence_text),
        }

    out: dict[str, Any] = {"schema": "separation.v1", "arms": {}}
    for arm in ("T1", "T2", "T3"):
        deltas = {k: [] for k in ("numbers", "paths", "baseline_occ", "replication",
                                  "novelty", "breakthrough", "baseline_pairs", "chars")}
        for b in bases:
            c, t = facts(b, "C"), facts(b, arm)
            for k in deltas:
                deltas[k].append(t[k] - c[k])
        summary = {
            k: {"mean": round(float(np.mean(v)), 3), "min": int(min(v)), "max": int(max(v))}
            for k, v in deltas.items()
        }
        def flat(keys):
            """这些通道在**每个底座**上的增量都必须为 0（不是平均值，逐底座判）。"""
            return all(x == 0 for k, vals in deltas.items() if k in keys for x in vals)

        if arm == "T1":
            pure = flat(("numbers", "paths", "baseline_occ", "replication", "novelty",
                         "breakthrough"))
        elif arm == "T2":
            pure = (flat(("paths", "replication", "novelty", "breakthrough"))
                    and max(deltas["numbers"]) > 0 and max(deltas["baseline_occ"]) > 0)
        else:
            pure = (flat(("numbers", "baseline_occ", "replication", "novelty", "breakthrough"))
                    and max(deltas["paths"]) > 0)
        out["arms"][arm] = {"deltas": summary, "pure": bool(pure)}
    # 长度对齐（排除「文本变长」这一混淆）
    lens = {arm: [len(inserted_text(arm, ((b.get("report") or {}).get("markdown") or "")
                                    if submission_channel(b) == "report"
                                    else str((b.get("result") or {}).get("summary") or "")))
                  for b in bases]
            for arm in ("T1", "T2", "T3")}
    out["inserted_chars"] = {arm: {"mean": round(float(np.mean(v)), 1),
                                   "min": int(min(v)), "max": int(max(v))}
                             for arm, v in lens.items()}
    means = {arm: float(np.mean(v)) for arm, v in lens.items()}
    spread = (max(means.values()) - min(means.values())) / max(1e-9, max(means.values()))
    out["length_matched"] = {"relative_spread": round(spread, 4), "within_15pct": bool(spread <= 0.15)}
    return out


def _dist(values: Sequence[str]) -> dict[str, int]:
    d: dict[str, int] = {}
    for v in values:
        d[str(v)] = d.get(str(v), 0) + 1
    return dict(sorted(d.items()))


def _arm_block(pr: Sequence[Mapping[str, Any]], *, arm: str, seed: int, B: int,
               mde: Mapping[str, Any]) -> dict[str, Any]:
    t = np.array([S_SCORE[p["t"]] for p in pr], dtype=float)
    c = np.array([S_SCORE[p["c"]] for p in pr], dtype=float)
    diff = t - c
    mat = np.stack([t, c], axis=1)
    d = cohen_d(t, c)
    d_lo, d_hi = bootstrap_ci(mat, _stat_d, seed=seed + 11, B=B)
    dz_lo, dz_hi = bootstrap_ci(mat, _stat_dz, seed=seed + 12, B=B)
    f_lo, f_hi = bootstrap_ci(mat, _stat_flip, seed=seed + 13, B=B)
    sd_diff = float(diff.std(ddof=1)) if diff.size > 1 else 0.0
    e_t = np.array([E_SCORE[p["t_e"]] for p in pr], dtype=float)
    e_c = np.array([E_SCORE[p["c_e"]] for p in pr], dtype=float)
    return {
        "label": ARM_LABEL[arm],
        "flip": flip_stats(pr), "flip_ci95": [round(f_lo, 4), round(f_hi, 4)],
        "flip_ci95_exact": [round(x, 4) for x in
                            exact_binom_ci(int(flip_stats(pr)["up"] + flip_stats(pr)["down"]),
                                           len(pr))],
        "mean_s_control": round(float(c.mean()), 4),
        "mean_s_treat": round(float(t.mean()), 4),
        "mean_s_diff": round(float(diff.mean()), 4), "sd_diff": round(sd_diff, 4),
        "cohen_d": round(d, 4), "cohen_d_ci95": [round(d_lo, 4), round(d_hi, 4)],
        "dz": round(paired_dz(t, c), 4), "dz_ci95": [round(dz_lo, 4), round(dz_hi, 4)],
        "mde_s_units_empirical": (round(float(mde["mde_dz"]) * sd_diff, 4) if sd_diff else None),
        "significant_up": bool(d > 0.2 and d_lo > 0.0),
        "significant_down": bool(d < -0.2 and d_hi < 0.0),
        "insufficient_evidence": bool(abs(d) < float(mde["mde_dz"])),
        "e_level": {
            "mean_control": round(float(e_c.mean()), 4),
            "mean_treat": round(float(e_t.mean()), 4),
            "flip_rate": round(float((e_t != e_c).mean()), 4),
            "up": int((e_t > e_c).sum()), "down": int((e_t < e_c).sum()),
            "cohen_d": round(cohen_d(e_t, e_c), 4),
        },
        "status": {
            "control_candidate_rate": round(float(np.mean([p["t_status"] == "candidate" for p in pr])), 4),
        },
        "pairs": pr,
    }


def _first_rep(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """每 (底座, 臂) 只留第一次重复（确定性 rubric 下重复相同，去重后按底座计）。"""
    seen: set[tuple[int, str]] = set()
    out = []
    for r in sorted(rows, key=lambda x: (int(x["base_id"]), str(x["arm"]), int(x["rep"]))):
        key = (int(r["base_id"]), str(r["arm"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def structural_findings(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """判级器在本语料上的**结构天花板**（不是干预效应，但决定效应能被看见的空间）。

    三项都是可复算计数，回答「判级还有没有分辨力余量」：
    - ``execution_ok_rate``：对照臂中「命令 + 通过计数」都齐的底座比例（E2 的前置条件）；
    - ``delta_registered_rate``：对照臂中 S3 前置条件（账本登记）成立的比例；
    - ``artifact_verify_by_arm``：各臂工件核验 exists / not_found_local 的底座均值
      —— 引用膨胀的**污染量**（即使不抬级，也是核验面的风险）。
    """
    uniq = _first_rep(rows)
    ctrl = [r for r in uniq if str(r["arm"]) == "C"]
    n = len(ctrl)
    exec_ok = sum(1 for r in ctrl
                  if int((r.get("facts") or {}).get("commands") or 0) > 0
                  and int((r.get("facts") or {}).get("passes") or 0) > 0)
    delta_ok = sum(1 for r in ctrl if (r.get("facts") or {}).get("delta_present"))
    by_arm: dict[str, dict[str, float]] = {}
    for arm in ARMS:
        rs = [r for r in uniq if str(r["arm"]) == arm]
        nf = [sum(1 for v in (r.get("artifact_check") or {}).values() if v == "not_found_local")
              for r in rs]
        ex = [sum(1 for v in (r.get("artifact_check") or {}).values() if v == "exists") for r in rs]
        by_arm[arm] = {
            "n_bases": len(rs),
            "mean_not_found_local": round(float(np.mean(nf)), 3) if nf else 0.0,
            "mean_exists": round(float(np.mean(ex)), 3) if ex else 0.0,
            "bases_with_any_not_found": int(sum(1 for x in nf if x > 0)),
        }
    return {
        "n_control_bases": n,
        "execution_ok_rate": round(exec_ok / n, 4) if n else 0.0,
        "delta_registered_rate": round(delta_ok / n, 4) if n else 0.0,
        "artifact_verify_by_arm": by_arm,
        "note": ("execution_ok_rate=0 → E 级被钉在 E1（E2 前置条件不成立），E 通道没有分辨力；"
                 "delta_registered_rate=0 → S3 不可达，S 级实际只有 S1/S2 两档。"),
    }


def analyze(rows: Sequence[Mapping[str, Any]], *, seed: int = DEFAULT_SEED, B: int = 10000,
            stratum_of: Mapping[int, str] | None = None) -> dict[str, Any]:
    """主分析：每臂对对照的翻转率、Cohen d/d_z、95% 聚类 bootstrap CI、E 级搬移（含分层）。"""
    n_bases = len(arm_deltas(rows, "grade").get("C", {}))
    mde = mde_analysis(n_bases)
    out: dict[str, Any] = {
        "schema": SCHEMA, "n_bases": n_bases, "n_gradings": len(rows),
        "bootstrap": {"B": int(B), "seed": int(seed), "unit": "base（聚类重抽，带出其重复）"},
        "model_note": MODEL_NOTE, "mde": mde, "arms": {}, "strata": {},
        "replay": replay_consistency(rows),
        "control_grade_distribution": _dist([r["grade"] for r in rows if r["arm"] == "C"]),
        "control_e_distribution": _dist([r["e_level"] for r in rows if r["arm"] == "C"]),
        "structural_findings": structural_findings(rows),
    }
    for arm in ("T1", "T2", "T3"):
        pr = pairs_of(rows, arm)
        if pr:
            out["arms"][arm] = _arm_block(pr, arm=arm, seed=seed, B=B, mde=mde)
    if stratum_of:
        for stratum in sorted(set(stratum_of.values())):
            ids = {int(k) for k, v in stratum_of.items() if v == stratum}
            block: dict[str, Any] = {"n_bases": len(ids), "arms": {}}
            for arm in ("T1", "T2", "T3"):
                pr = [p for p in pairs_of(rows, arm) if p["base_id"] in ids]
                if pr:
                    block["arms"][arm] = _arm_block(pr, arm=arm, seed=seed + 101, B=B, mde=mde)
            out["strata"][stratum] = block
    out["verdicts"] = verdicts(out)
    return out


#: 效应量门槛（预注册）：|d| ≤ 0.2 视为「小到可忽略」，> 0.2 才算实质抬级/降级
EQUIVALENCE_MARGIN = 0.2


def verdicts(stats: Mapping[str, Any]) -> dict[str, Any]:
    """预注册裁决（三分法，不用二分法硬套）。

    - 抬级：``d>0.2 且 CI 下界>0`` → 「判级可被该干预操纵」；
    - 抗住（等价性成立）：``d 的 CI 上界 < 0.2`` **且** 翻转率精确 95% 上界 < 0.2
      → 「rubric 抗该干预」。零效应时 d 的 bootstrap CI 会退化到 [0,0]，因此等价性
      结论以**翻转率的 Clopper-Pearson 精确上界**为准（对全 0 也给出真实上界）；
    - 其余（CI 跨过 0.2 的两侧）→ 「证据不足」。
    """
    out: dict[str, Any] = {"equivalence_margin": EQUIVALENCE_MARGIN, "arms": {}}
    text = {
        "T1": ("判级可被措辞操纵", "rubric 抗措辞膨胀"),
        "T2": ("判级可被无出处数字操纵", "rubric 抗伪数字"),
        "T3": ("判级可被假引用操纵", "rubric 抗引用膨胀"),
    }
    for arm in ("T1", "T2", "T3"):
        a = (stats.get("arms") or {}).get(arm)
        if not a:
            out["arms"][arm] = {"verdict": "证据不足", "reason": "无该臂数据"}
            continue
        d = a["cohen_d"]
        lo, hi = a["cohen_d_ci95"]
        flip = a["flip"]["flip_rate"]
        f_lo, f_hi = a.get("flip_ci95_exact") or (float("nan"), float("nan"))
        if a.get("significant_up"):
            verdict = text[arm][0]
            direction = "up"
            note = f"d={d}（95% CI [{lo}, {hi}]）>0.2 且 CI 下界>0；翻转率 {flip:.3f}"
        elif a.get("significant_down"):
            verdict = text[arm][1] + "（反向：干预使判级下降）"
            direction = "down"
            note = f"d={d}（95% CI [{lo}, {hi}]）< -0.2 且 CI 上界<0"
        elif hi < EQUIVALENCE_MARGIN and f_hi < EQUIVALENCE_MARGIN:
            verdict = text[arm][1]
            direction = "none"
            note = (f"等价性成立：d 上界 {hi} < 0.2 且翻转率 {flip:.3f}"
                    f"（精确 95% CI [{f_lo}, {f_hi}]）上界 < 0.2")
        else:
            verdict = "证据不足"
            direction = "up" if d > 0 else ("down" if d < 0 else "none")
            note = (f"d={d}（95% CI [{lo}, {hi}]）跨过 0.2 门槛，翻转率 {flip:.3f}"
                    f"（精确 95% CI [{f_lo}, {f_hi}]）；样本不足以判定")
        out["arms"][arm] = {
            "verdict": verdict, "direction": direction, "cohen_d": d,
            "cohen_d_ci95": [lo, hi], "flip_rate": flip,
            "flip_ci95_exact": [f_lo, f_hi],
            "equivalence_established": bool(hi < EQUIVALENCE_MARGIN and f_hi < EQUIVALENCE_MARGIN),
            "note": note,
        }
    return out
