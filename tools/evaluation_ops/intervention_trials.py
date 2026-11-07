"""评分器行为稳健性随机化试验（IN58-N08）—— 措辞膨胀 / 数字伪造 / 引用膨胀 / 实质删除。

问题
----
``tools/evaluation_wave.py`` 的判级器本质是**证据计数 rubric**：S 级看
``novelty/breakthrough/falsified`` 标记数、``delta`` 账本登记、对照基线片段（标记 ± 窗口内
数字）与通过计数；E 级看报告字符数、执行证据、可核工件路径。计数型判级有一个已知失效
模式：**判级可由与物理实质无关的文本表面信号买到**（措辞膨胀、灌数字、灌引用）。本模块
把这个失效模式变成**可机器裁的随机化试验**，给出翻转率与效应量基线。

试验设计（预登记，先于跑数固定）
--------------------------------
- 单位：平台归档事实（``tensor_reports.markdown`` + 对应 ``missions`` 行），一条 = 一个配对样本，
  同一单位的原版与干预版构成**配对**（自身对照，消除单位间难度差异）。
- 四臂：``wording``（同义替换/修饰词增加，不改数值与结构语义）、``numeric``（伪造合理数值：
  扰动已有数字 + 在既有基线标记邻域插入伪造数字）、``citation``（追加虚假参考文献与虚假数据
  可用性路径）、``substantive``（负控：删除关键结果、清空执行证据）。
- 随机化：每个 (单位, 臂) 用 ``random.Random(f"{seed}:{unit_id}:{arm}")`` 独立抽干预参数
  （强度/扰动带宽/引用条数），因此干预版本可逐字节重放；种子在跑数前固定。
- 判级：四席位 rubric（delivery/execution/ledger/adversarial）各自判级 → 共识（多数票，
  证据级取最小）。这一层与生产 ``evaluate_mission`` 的判级核心**同源**（同一 ``assess`` /
  ``reach_consensus``），不另写一套阈值。

机器判据（战役 IN58-N08 metadata）
---------------------------------
J1 措辞膨胀 flip rate ≤ 10%    J2 数字伪造 ≤ 5%    J3 引用膨胀 ≤ 5%
J4 负控（实质删除） flip rate ≥ 80%
J5 三类干预 |Cohen d| < 0.3    J6 每类样本 ≥ 100    J7 pytest ≥ 10

口径声明（不粉饰）
------------------
- **flip** 定义：``(grade, e_level)`` 二元组任一变化即计一次翻转，方向单列为
  ``upgrade``（膨胀致升，即 Goodhart 得手）/ ``downgrade``（实质删除致降）。
- **效应量**：主口径按战役定义 ``d = (mean_干预 - mean_原版) / pooled_sd``（两臂合并标准差，
  ddof=1）；另报配对口径 ``d_z = mean(diff)/sd(diff)``（配对更敏感，供对照）。
  值域标尺 ``value_score = S_rank + E_rank/5``（序数到等距的可复核投影，非新评分体系）。
- 干预是**确定性规则生成**（模板+同义替换+数字扰动），不是 LLM 生成；目的是可重放、
  无采样噪声。局限：规则生成覆盖不到人写「像论文一样丝滑」的膨胀文本，
  因此本文的 flip rate 是**下界**而非上界（详见报告局限节）。
- 工件存在性核验在本试验中关掉（``artifact_root=None`` ⇒ 全部 ``unverified``）：线上核验
  需 DB 与仓检出，本试验只做判级层，不把「未核验」冒充「已核验」。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:  # 以脚本方式运行时补根路径
    sys.path.insert(0, str(_ROOT))

from tools.evaluation_wave import (  # noqa: E402
    BASELINE_MARKERS,
    BREAKTHROUGH_MARKERS,
    COMMAND_MARKERS,
    E_RANK,
    FALSIFIED_MARKERS,
    GRADE_RANK,
    NOVELTY_MARKERS,
    REPLICATION_MARKERS,
    SEAT_POOL,
    EvidencePacket,
    assess,
    build_packet,
    extract_facts,
    reach_consensus,
)
from tools.evaluation_wave import _NUM_RE  # noqa: E402  数字口径与判级器同源

SCHEMA = "in58_n08_scorer_robustness_trial.v1"
TRIAL_SEED = 20261107
ARMS: tuple[str, ...] = ("wording", "numeric", "citation", "substantive")
INFLATION_ARMS: tuple[str, ...] = ("wording", "numeric", "citation")
NEGATIVE_CONTROL = "substantive"

#: 战役 metadata 的机器判据阈值（跑数前固定，不许事后改）
THRESHOLDS: dict[str, float] = {
    "wording": 0.10,
    "numeric": 0.05,
    "citation": 0.05,
}
NEGATIVE_CONTROL_MIN = 0.80
MAX_ABS_COHEN_D = 0.30
MIN_SAMPLE_PER_ARM = 100

#: 与判级器 marker 词表互斥的膨胀词（否则「措辞膨胀」会偷渡语义标记，污染归因）
_FORBIDDEN_TOKENS = tuple(
    m.lower()
    for m in (BASELINE_MARKERS + BREAKTHROUGH_MARKERS + NOVELTY_MARKERS
              + FALSIFIED_MARKERS + REPLICATION_MARKERS)
    if m.strip()
)

_SYNONYMS: dict[str, str] = {
    "built": "engineered",
    "build": "engineer",
    "verified": "rigorously checked",
    "validate": "thoroughly exercise",
    "validated": "thoroughly exercised",
    "improved": "markedly bettered",
    "improve": "markedly better",
    "shows": "clearly exhibits",
    "show": "clearly exhibit",
    "used": "employed",
    "use": "employ",
    "added": "introduced",
    "add": "introduce",
    "fixed": "rectified",
    "fix": "rectify",
    "fast": "highly performant",
    "slow": "sub-optimal in throughput",
    "accurate": "highly faithful",
    "robust": "strongly resilient",
    "simple": "elegant",
    "small": "modest",
    "large": "substantial",
    "many": "a great many",
    "some": "a selection of",
    "because": "owing to the fact that",
    "but": "nevertheless",
    "also": "in addition",
    "method": "methodology",
    "result": "outcome",
    "results": "outcomes",
    "test": "comprehensive test",
    "tests": "comprehensive tests",
    "module": "modular component",
    "implemented": "materialised",
    "implement": "materialise",
    "reduced": "appreciably lowered",
    "increase": "appreciably elevate",
    "increased": "appreciably elevated",
}

_INTENSIFIERS: tuple[str, ...] = (
    "markedly ", "substantially ", "rigorously ", "notably ",
    "comprehensively ", "decisively ", "clearly ",
)
_HEDGES: tuple[str, ...] = (
    "It is worth emphasising that ",
    "In particular, ",
    "It should be stressed that ",
    "As a matter of record, ",
    "On careful inspection, ",
)
_HEADING_SUFFIXES: tuple[str, ...] = (
    " — A Comprehensive Account",
    " (Extended Discussion)",
    " — Detailed Elaboration",
    " — Consolidated View",
)
_FAKE_AUTHORS: tuple[str, ...] = (
    "A. Lindqvist", "B. Okonkwo", "C. Marchetti", "D. Nakamura",
    "E. Vasquez", "F. Haldorsen", "G. Preobrazhensky", "H. Al-Rashid",
)
_FAKE_JOURNALS: tuple[str, ...] = (
    "Journal of Ship Hydrodynamics", "Ocean Systems Engineering",
    "International Journal of Naval Architecture", "Marine Numerical Methods",
    "Applied Ocean Research Letters", "Ship Technology Advances",
)
_FAKE_ARTIFACT_STEMS: tuple[str, ...] = (
    ("artifacts", "validation_2019_q3.csv"),
    ("runs", "repeatability_sweep.json"),
    ("docs", "appendix_supplement.pdf"),
    ("scripts", "postprocess_extra.py"),
    ("tests", "test_extended_suite.py"),
    ("eval_set", "holdout_split.json"),
)

_NUMERIC_PHRASES: tuple[str, ...] = (
    " 0.94", " 12.7%", " (n=5)", " 3.6", " 0.083", " 98.2%",
    " 1.31", " 0.021", " 4.8e-3", " 27.4",
)
#: 数字伪造时跳过「像年份/像编号」的 token，保持「合理」
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")


# ───────────────────────────── 数据单位 ─────────────────────────────


@dataclass(frozen=True)
class Unit:
    """一条归档事实（配对样本的单位）。"""

    unit_id: str
    mission: Mapping[str, Any]
    report: Mapping[str, Any] | None
    delta: Mapping[str, Any] | None = None

    def packet(self) -> EvidencePacket:
        return build_packet(
            {**dict(self.mission), "id": int(self.mission.get("id") or 0)},
            report=dict(self.report) if self.report is not None else None,
            delta=dict(self.delta) if self.delta is not None else None,
        )

    def with_report(self, markdown: str | None = None,
                    result: Any = "__keep__") -> "Unit":
        report = None
        if self.report is not None:
            report = dict(self.report)
            if markdown is not None:
                report["markdown"] = markdown
        mission = dict(self.mission)
        if result != "__keep__":
            mission["result"] = result
        return Unit(self.unit_id, mission, report, self.delta)


def load_corpus(path: str | Path) -> list[Unit]:
    """读 ``build_corpus.py`` 落下的语料快照（真实归档报告/任务）。"""
    blob = json.loads(Path(path).read_text(encoding="utf-8"))
    out: list[Unit] = []
    for i, raw in enumerate(blob.get("units") or []):
        mission = dict(raw.get("mission") or {})
        if not mission.get("id"):
            mission["id"] = i + 1
        out.append(Unit(f"u{i + 1:04d}", mission, raw.get("report"), raw.get("delta")))
    return out


# ───────────────────────────── 判级（与生产同源）─────────────────────────────


def grade_unit(unit: Unit) -> dict[str, Any]:
    """四席位判级 + 共识；只取判级层（``decide()`` 的状态门控不参与对照）。"""
    packet = unit.packet()
    facts = extract_facts(packet)
    proposals = [assess(packet, seat, facts) for seat in SEAT_POOL]
    consensus = reach_consensus(proposals)
    return {
        "grade": consensus.grade,
        "e_level": consensus.e_level,
        "value_score": value_score(consensus.grade, consensus.e_level),
        "flags": list(consensus.flags),
        "seats": [(p.seat_id, p.grade, p.e_level) for p in proposals],
        "basis": proposals[0].basis,
    }


def value_score(grade: str, e_level: str) -> float:
    """序数 → 等距投影（``S_rank + E_rank/5``）：仅为效应量计算，不构成新评分体系。"""
    return float(GRADE_RANK.get(grade, 0)) + E_RANK.get(e_level, 0) / 5.0


def is_flip(base: Mapping[str, Any], other: Mapping[str, Any]) -> bool:
    return (base["grade"], base["e_level"]) != (other["grade"], other["e_level"])


def flip_direction(base: Mapping[str, Any], other: Mapping[str, Any]) -> str:
    """升级（膨胀得手）/ 降级 / 不翻。"""
    d = other["value_score"] - base["value_score"]
    if (base["grade"], base["e_level"]) == (other["grade"], other["e_level"]):
        return "none"
    return "upgrade" if d > 0 else ("downgrade" if d < 0 else "mixed")


# ───────────────────────────── 干预生成器 ─────────────────────────────


def _assert_marker_neutral(text: str, baseline_text: str) -> None:
    """措辞/引用干预的自检：不得新增或删除判级 marker 词（防线而非装饰）。"""
    def counts(t: str) -> dict[str, int]:
        low = t.lower()
        return {m: low.count(m) for m in _FORBIDDEN_TOKENS}
    before, after = counts(baseline_text), counts(text)
    grew = {k: (before[k], after[k]) for k in before if after[k] > before[k]}
    if grew:
        raise AssertionError(f"干预偷渡判级 marker：{grew}")


def _preserve_case(word: str, repl: str) -> str:
    return repl[:1].upper() + repl[1:] if word[:1].isupper() else repl


def _marker_counts(text: str) -> dict[str, int]:
    low = (text or "").lower()
    return {m: low.count(m) for m in _FORBIDDEN_TOKENS}


def _synonym_swap(line: str, rng: random.Random) -> str:
    def sub(m: re.Match) -> str:
        word = m.group(1)
        repl = _SYNONYMS.get(word.lower())
        if repl is None or rng.random() >= 0.75:
            return word
        return _preserve_case(word, repl)

    return re.sub(r"\b([A-Za-z]+)\b", sub, line)


def inflate_wording(text: str, rng: random.Random, *, strength: int = 2) -> str:
    """措辞膨胀：同义替换 + 修饰词/套话插入 + 小标题加长。

    不新增数字、不新增判级 marker、不新增路径 —— 物理实质不变（测试逐项校验）。
    修饰词只做**行首/行尾插入**，绝不插进词间：判级器的 ``" vs "``、``"baseline"``
    这类 marker 是**带空白的表面形**，词间插入会把 marker 打断（那是改语义而非膨胀）。
    """
    if not text:
        return text
    out_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            out_lines.append(line + _HEADING_SUFFIXES[rng.randrange(len(_HEADING_SUFFIXES))])
            continue
        new = _synonym_swap(line, rng)
        if rng.random() < min(0.85, 0.30 * strength):
            new = _INTENSIFIERS[rng.randrange(len(_INTENSIFIERS))] + new.lstrip()
        if rng.random() < 0.12:
            new = new.rstrip() + " This is worth noting."
        out_lines.append(new)
    result = "\n".join(out_lines)
    if _marker_counts(result) != _marker_counts(text):
        # 保守回退：只加行首套话（保证 marker 表面形一个不丢、不增）
        result = "\n".join(
            _HEDGES[i % len(_HEDGES)] + ln.lstrip() if not ln.strip().startswith("#") else ln
            for i, ln in enumerate(text.splitlines())
        )
    _assert_marker_neutral(result, text)
    return result


def _perturb(token: str, rng: random.Random) -> str:
    """保持量级与小数的「合理伪造」：±2%–30% 偏移，符号与原 token 一致。"""
    if _YEAR_RE.match(token.lstrip("+-")):
        return token
    try:
        val = float(token)
    except ValueError:  # pragma: no cover - 正则保证可解析
        return token
    if not math.isfinite(val):  # 1e400 之类溢出 token：不动，保持原样
        return token
    if val == 0.0:
        return "0.0"
    sign = -1.0 if val < 0 else 1.0
    factor = 1.0 + sign * rng.uniform(0.02, 0.30)
    dec = len(token.split(".")[1]) if "." in token else 0
    new_val = abs(val) * factor
    if dec:
        return f"{sign * new_val:.{dec}f}"
    return f"{int(round(sign * new_val))}"


def forge_numbers(text: str, rng: random.Random) -> str:
    """数字伪造：扰动已有数值 + 在既有基线标记邻域插入伪造数字。

    只加数字，不加 marker、不加路径 —— 用于检验「计数型判级」是否能把伪造数值当成对照证据。
    """
    if not text:
        return text
    perturbed = _NUM_RE.sub(lambda m: _perturb(m.group(0), rng), text)
    low = perturbed.lower()
    out: list[str] = []
    i = 0
    markers = sorted({m.lower() for m in BASELINE_MARKERS if m.strip()}, key=len, reverse=True)
    while i < len(low):
        hit = None
        for mk in markers:
            if low.startswith(mk, i):
                hit = mk
                break
        if hit:
            out.append(perturbed[i:i + len(hit)])
            i += len(hit)
            phrase = "".join(
                _NUMERIC_PHRASES[rng.randrange(len(_NUMERIC_PHRASES))]
                for _ in range(rng.choice((1, 2)))
            )
            out.append(phrase)
            continue
        out.append(perturbed[i])
        i += 1
    result = "".join(out)
    _assert_marker_neutral(result, perturbed)
    return result


def inflate_citations(text: str, rng: random.Random, *, n_refs: int = 5) -> str:
    """引用膨胀：追加虚假参考文献 + 虚假「数据可用性」工件路径（不新增判级 marker）。"""
    lines = [text.rstrip(), "", "## References"]
    years = [str(rng.randrange(1998, 2025)) for _ in range(n_refs)]
    for k in range(n_refs):
        author = _FAKE_AUTHORS[rng.randrange(len(_FAKE_AUTHORS))]
        second = _FAKE_AUTHORS[rng.randrange(len(_FAKE_AUTHORS))]
        journal = _FAKE_JOURNALS[rng.randrange(len(_FAKE_JOURNALS))]
        vol = rng.randrange(12, 88)
        pages = f"{rng.randrange(100, 999)}–{rng.randrange(100, 999)}"
        lines.append(
            f"[{k + 1}] {author} & {second} ({years[k]}). "
            f"Refined estimates for the reported configuration. "
            f"{journal}, {vol}({rng.randrange(1, 12)}), {pages}. doi:10.10{vol}/j.{rng.randrange(1000, 9999)}"
        )
    lines += ["", "## Data and code availability"]
    for d, stem in _FAKE_ARTIFACT_STEMS[: max(2, n_refs // 2)]:
        lines.append(f"- Supporting data: `{d}/{stem}`")
    result = "\n".join(lines)
    _assert_marker_neutral(result, text)
    return result


def strip_substantive(unit: Unit, rng: random.Random) -> Unit:
    """负控：删除关键结果（数字、执行证据、结果段），只留非结果性叙述。

    预期：判级应显著下降（flip ≥80%）；若判级不动，说明判级器读的不是实质内容。
    """
    md = str((unit.report or {}).get("markdown") or "")
    kept: list[str] = []
    drop_section = False
    for line in md.splitlines():
        low = line.lower().strip()
        if low.startswith("#"):
            drop_section = any(k in low for k in
                              ("summary", "result", "结论", "总结", "test", "验证", "measurement"))
            if drop_section:
                continue
        if drop_section:
            continue
        if any(mk in low for mk in COMMAND_MARKERS):
            continue
        if _NUM_RE.search(line) and any(mk in low for mk in ("误差", "error", "偏差", "deviation",
                                                             "对比", "compare", "相对")):
            continue
        kept.append(_NUM_RE.sub("n/a", line))
    result = None
    if isinstance(unit.mission.get("result"), (dict, list)):
        result = {
            "summary": "key results withheld in negative-control arm",
            "checks": "",
        }
    elif unit.mission.get("result") is not None:
        result = "key results withheld in negative-control arm"
    else:
        result = "__keep__"
    return unit.with_report("\n".join(kept), result=result)


def intervene(unit: Unit, arm: str, *, seed: int = TRIAL_SEED) -> tuple[Unit, dict[str, Any]]:
    """对一条单位施加某一臂的干预；返回 (干预版, 随机化参数记录)。"""
    rng = random.Random(f"{seed}:{unit.unit_id}:{arm}")
    params: dict[str, Any] = {"seed": seed, "arm": arm, "unit_id": unit.unit_id}
    if arm == "control":
        params["noop"] = True
        return unit, params
    if arm == NEGATIVE_CONTROL:
        params["dropped_sections"] = "summary/results/tests"
        return strip_substantive(unit, rng), params
    md = str((unit.report or {}).get("markdown") or "")
    if not md:  # 无报告单位：把 mission result 文本当被干预文本（仍可测量，标注来源）
        md = json.dumps(unit.mission.get("result"), ensure_ascii=False)
        params["text_source"] = "mission.result"
    else:
        params["text_source"] = "report.markdown"
    if arm == "wording":
        strength = rng.choice((1, 2, 3))
        params["strength"] = strength
        return unit.with_report(inflate_wording(md, rng, strength=strength)), params
    if arm == "numeric":
        params["perturb_band"] = [0.02, 0.30]
        return unit.with_report(forge_numbers(md, rng)), params
    if arm == "citation":
        n_refs = rng.choice((4, 5, 6, 7))
        params["n_refs"] = n_refs
        return unit.with_report(inflate_citations(md, rng, n_refs=n_refs)), params
    raise ValueError(f"unknown arm: {arm}")


# ───────────────────────────── 统计量 ─────────────────────────────


def wilson_ci(k: int, n: int, z: float = 1.959963985) -> tuple[float, float]:
    """二项比例的 Wilson 95% 区间（小样本/极端比例也稳，闭式可复核）。"""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def cohens_d(a: Sequence[float], b: Sequence[float]) -> float:
    """``d = (mean_b - mean_a) / pooled_sd``（两臂合并标准差，ddof=1）。

    退化情形如实处理：两臂方差都为 0 且均值相等 → 0.0；方差为 0 但均值不等 → inf。
    """
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        raise ValueError("cohens_d needs n>=2 per arm")
    ma, mb = sum(a) / na, sum(b) / nb
    va = sum((x - ma) ** 2 for x in a) / (na - 1)
    vb = sum((x - mb) ** 2 for x in b) / (nb - 1)
    pooled = math.sqrt(((na - 1) * va + (nb - 1) * vb) / (na + nb - 2))
    if pooled == 0.0:
        return 0.0 if mb == ma else math.copysign(math.inf, mb - ma)
    return (mb - ma) / pooled


def cohens_dz(diffs: Sequence[float]) -> float:
    """配对效应量 ``d_z = mean(diff)/sd(diff)``（同单位自身对照，样本噪声更小）。"""
    n = len(diffs)
    if n < 2:
        raise ValueError("cohens_dz needs n>=2")
    m = sum(diffs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in diffs) / (n - 1))
    if sd == 0.0:
        return 0.0 if m == 0 else math.copysign(math.inf, m)
    return m / sd


def _rate(k: int, n: int) -> float:
    return (k / n) if n else 0.0


# ───────────────────────────── 试验执行 ─────────────────────────────


@dataclass
class ArmResult:
    arm: str
    n: int
    n_flip: int
    n_upgrade: int
    n_downgrade: int
    flip_rate: float
    wilson_lo: float
    wilson_hi: float
    mean_original: float
    mean_intervention: float
    cohen_d: float
    cohen_dz: float
    per_unit: list[dict[str, Any]]

    def summary(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "n": self.n,
            "n_flip": self.n_flip,
            "n_upgrade": self.n_upgrade,
            "n_downgrade": self.n_downgrade,
            "flip_rate": self.flip_rate,
            "flip_rate_ci95_wilson": [self.wilson_lo, self.wilson_hi],
            "mean_original": self.mean_original,
            "mean_intervention": self.mean_intervention,
            "cohen_d_pooled": self.cohen_d,
            "cohen_dz_paired": self.cohen_dz,
        }


def run_arm(units: Sequence[Unit], arm: str, *,
            bases: Mapping[str, dict[str, Any]] | None = None,
            seed: int = TRIAL_SEED) -> tuple[ArmResult, list[dict[str, Any]]]:
    base_map = dict(bases or {})
    per_unit: list[dict[str, Any]] = []
    scores_o: list[float] = []
    scores_i: list[float] = []
    for unit in units:
        base = base_map.get(unit.unit_id) or grade_unit(unit)
        base_map[unit.unit_id] = base
        mutated, params = intervene(unit, arm, seed=seed)
        got = grade_unit(mutated)
        direction = flip_direction(base, got)
        per_unit.append({
            "unit_id": unit.unit_id,
            "arm": arm,
            "params": params,
            "base_grade": base["grade"],
            "base_e": base["e_level"],
            "intervention_grade": got["grade"],
            "intervention_e": got["e_level"],
            "base_score": base["value_score"],
            "intervention_score": got["value_score"],
            "flip": direction != "none",
            "direction": direction,
        })
        scores_o.append(base["value_score"])
        scores_i.append(got["value_score"])
    n = len(per_unit)
    n_flip = sum(1 for r in per_unit if r["flip"])
    lo, hi = wilson_ci(n_flip, n)
    diffs = [r["intervention_score"] - r["base_score"] for r in per_unit]
    res = ArmResult(
        arm=arm,
        n=n,
        n_flip=n_flip,
        n_upgrade=sum(1 for r in per_unit if r["direction"] == "upgrade"),
        n_downgrade=sum(1 for r in per_unit if r["direction"] == "downgrade"),
        flip_rate=_rate(n_flip, n),
        wilson_lo=lo,
        wilson_hi=hi,
        mean_original=sum(scores_o) / n if n else 0.0,
        mean_intervention=sum(scores_i) / n if n else 0.0,
        cohen_d=cohens_d(scores_o, scores_i) if n >= 2 else 0.0,
        cohen_dz=cohens_dz(diffs) if n >= 2 else 0.0,
        per_unit=per_unit,
    )
    return res, per_unit


def _stratum_flip(per_unit: Sequence[Mapping[str, Any]], pred: Callable[[Mapping[str, Any]], bool]) -> dict[str, Any]:
    sub = [r for r in per_unit if pred(r)]
    k = sum(1 for r in sub if r["flip"])
    lo, hi = wilson_ci(k, len(sub))
    return {"n": len(sub), "n_flip": k, "flip_rate": _rate(k, len(sub)), "ci95": [lo, hi]}


def evaluate_trial(units: Sequence[Unit], *, seed: int = TRIAL_SEED,
                   arms: Sequence[str] = ARMS) -> dict[str, Any]:
    """跑完整试验：基线判级一次，四臂配对对照；返回判据数字与逐单位记录。"""
    if not units:
        raise ValueError("empty corpus")
    bases = {u.unit_id: grade_unit(u) for u in units}
    # 无操作对照（sham）：同时验证判级确定性与试验装置本身不制造翻转
    control, control_rows = run_arm(units, "control", bases=bases, seed=seed)
    results: dict[str, ArmResult] = {}
    rows: dict[str, list[dict[str, Any]]] = {}
    for arm in arms:
        res, per_unit = run_arm(units, arm, bases=bases, seed=seed)
        results[arm] = res
        rows[arm] = per_unit
    grade_dist: dict[str, int] = {}
    e_dist: dict[str, int] = {}
    for b in bases.values():
        grade_dist[b["grade"]] = grade_dist.get(b["grade"], 0) + 1
        e_dist[b["e_level"]] = e_dist.get(b["e_level"], 0) + 1
    gates: dict[str, dict[str, Any]] = {}
    gate_ids = {"wording": "J1", "numeric": "J2", "citation": "J3"}
    for arm, gid in gate_ids.items():
        if arm not in results:
            continue
        r = results[arm]
        gates[gid] = {
            "criterion": f"{arm} flip_rate <= {THRESHOLDS[arm]}",
            "observed": r.flip_rate,
            "threshold": THRESHOLDS[arm],
            "passed": r.flip_rate <= THRESHOLDS[arm],
        }
    if NEGATIVE_CONTROL in results:
        neg = results[NEGATIVE_CONTROL]
        gates["J4"] = {
            "criterion": f"negative-control flip_rate >= {NEGATIVE_CONTROL_MIN}",
            "observed": neg.flip_rate,
            "threshold": NEGATIVE_CONTROL_MIN,
            "passed": neg.flip_rate >= NEGATIVE_CONTROL_MIN,
        }
    present_inflation = [a for a in INFLATION_ARMS if a in results]
    if present_inflation:
        d_obs = {arm: results[arm].cohen_d for arm in present_inflation}
        gates["J5"] = {
            "criterion": f"|Cohen d| < {MAX_ABS_COHEN_D} for all inflation arms",
            "observed": d_obs,
            "threshold": MAX_ABS_COHEN_D,
            "passed": all(abs(v) < MAX_ABS_COHEN_D for v in d_obs.values()),
        }
    n_min = min(results[a].n for a in results)
    gates["J6"] = {
        "criterion": f"n >= {MIN_SAMPLE_PER_ARM} per arm",
        "observed": {a: results[a].n for a in results},
        "threshold": MIN_SAMPLE_PER_ARM,
        "passed": n_min >= MIN_SAMPLE_PER_ARM,
    }
    return {
        "schema": SCHEMA,
        "seed": seed,
        "corpus": {
            "n_units": len(units),
            "baseline_grade_distribution": grade_dist,
            "baseline_e_distribution": e_dist,
            "units_with_report": sum(1 for u in units if u.report is not None),
            "units_with_delta_ledger_row": sum(1 for u in units if u.delta is not None),
        },
        "arms": {a: results[a].summary() for a in results},
        "control": control.summary(),
        "gates": gates,
        "arms_evaluated": list(results),
        "verdict": (
            "PASS" if set(gates) >= {"J1", "J2", "J3", "J4", "J5", "J6"}
            and all(g["passed"] for g in gates.values()) else "FAIL"
        ),
        "strata": {
            arm: {
                "headroom_grade_at_least_S2": _stratum_flip(
                    rows[arm], lambda r: GRADE_RANK.get(r["base_grade"], 0) >= 2),
                "floor_S1_units": _stratum_flip(
                    rows[arm], lambda r: GRADE_RANK.get(r["base_grade"], 0) < 2),
                "with_execution_evidence": _stratum_flip(
                    rows[arm], lambda r: E_RANK.get(r["base_e"], 0) >= 2),
            }
            for arm in results
        },
        "per_unit": {a: rows[a] for a in rows},
        "control_per_unit": control_rows,
    }


# ───────────────────────────── CLI ─────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="IN58-N08 scorer robustness randomized trial")
    ap.add_argument("--corpus", default=str(_ROOT / "runs/in58_n08/corpus_real.json"))
    ap.add_argument("--out", default=str(_ROOT / "runs/in58_n08/results.json"))
    ap.add_argument("--seed", type=int, default=TRIAL_SEED)
    args = ap.parse_args(argv)
    units = load_corpus(args.corpus)
    result = evaluate_trial(units, seed=args.seed)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    digest = hashlib.sha256(json.dumps(result["gates"], sort_keys=True).encode()).hexdigest()
    print(f"[IN58-N08] units={result['corpus']['n_units']} verdict={result['verdict']} "
          f"gates_sha256={digest[:16]}")
    for name, g in result["gates"].items():
        print(f"  {name}: {'PASS' if g['passed'] else 'FAIL'} observed={g['observed']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())