"""根因分类器（四类，可解释 cue 打分；无 ML 模型，可离线复算）。

为什么规则而不是模型
--------------------
* 语料只有几十条欠账行，训练集量级不够；报告是「机器可读的判词」，cue 词本身就是
  作者写下的理由，规则比小样本模型更可靠且**逐条可解释**（评测要求 macro-F1 ≥ 0.8）。
* 每条结果的 ``evidence`` 保留命中的 cue，审计时能直接看到「凭哪几个词定这一类」。

四类与典型 cue
--------------
* ``reference_limited``：二次引用 / 二手转录 / 不确定带 / 风洞与模型试验口径 / 经验式
* ``discretization_resolution``：网格 / GCI / 分辨率 / 档间差 / 收敛 / 阶 / 面板 / 体素
* ``implementation_defect``：实现 / 代码 / 契约 / 回归 / 未实现 / NaN / 模型差异 / 参数化型线
* ``reference_missing``：真值未知 / 无基准 / 无数据 / 仓内无实测

权重：强 cue 3 分、弱 cue 1 分；同分按 ``PRIORITY`` tie-break（参考类优先：若根因是
参考值质量，修实现属无效再派——这是任务书「再派判据」的纪律）。

默认值（无任何 cue 命中时）
--------------------------
* 误差 3%–6% 的**轻微超差** → ``discretization_resolution``（报告里这类超差几乎总是网格/
  回归分辨率所致，比默认「实现缺陷」更贴近作者原意）；
* 其余 ``WITHHELD`` → ``reference_limited``，``FAIL`` → ``implementation_defect``。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .schema import ROOT_CAUSES

CUES: dict[str, dict[str, tuple[str, ...]]] = {
    "reference_limited": {
        "strong": (
            "二次引用",
            "二手",
            "二手转录",
            "不确定带",
            "±33%",
            "±20%",
            "来源不足",
            "参考值来源",
            "带宽",
            "recommendation",
            "uncertainty band",
            "secondary citation",
            "scatter",
            "散布",
            "实验点稀疏",
            "已到参考精度极限",
            "reference limited",
            "模型试验",
            "风洞",
            "数字化线",
            "推荐式",
            "经验式",
            "经验判据",
            "参考图读数",
        ),
        "weak": (
            "参考",
            "recommended",
            "dnv",
            "chuang",
            "buchner",
            "文献",
            "实验值",
            "band",
            "估计量",
            "置信区间",
            "区间 [",
        ),
    },
    "discretization_resolution": {
        "strong": (
            "网格",
            "gci",
            "分辨率",
            "格间差",
            "档间差",
            "加密",
            "h_mean",
            "panels",
            "面板数",
            "面元数",
            "cells",
            "discretization",
            "收敛阶",
            "观测阶",
            "阶数",
            "单调",
            "收敛",
            "体素",
        ),
        "weak": (
            "mesh",
            "lattice",
            "阶",
            "coarse",
            "fine",
            "refine",
            "dx",
            "δx",
            "网格无关",
            "station",
            "测压片",
        ),
    },
    "implementation_defect": {
        "strong": (
            "实现",
            "代码",
            "bug",
            "契约",
            "回归",
            "未实现",
            "stub",
            "逻辑",
            "defect",
            "regression",
            "不收敛",
            "nan",
            "崩溃",
            "错位",
            "口径不一致",
            "接线",
            "模型差异",
            "bemt",
            "输运模型",
            "参数化",
            "型线",
        ),
        "weak": (
            "修复",
            "fix",
            "分支",
            "接口",
            "contract",
            "错误处理",
            "assert",
            "断言",
            "越界",
        ),
    },
    "reference_missing": {
        "strong": (
            "真值未知",
            "无基准",
            "缺参考",
            "无实验",
            "无解析解",
            "不可对标",
            "reference missing",
            "无一手参考",
            "没有参考",
            "reference unavailable",
            "无数据",
            "无实测",
            "仓内无",
            "无该实测",
        ),
        "weak": (
            "未知",
            "无对标",
            "未测",
            "缺失",
            "missing",
            "估计量",
        ),
    },
}

PRIORITY = (
    "reference_missing",
    "reference_limited",
    "discretization_resolution",
    "implementation_defect",
)


@dataclass
class Cause:
    label: str
    score: dict[str, int] = field(default_factory=dict)
    evidence: tuple[str, ...] = field(default=())
    defaulted: bool = False

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "score": self.score,
            "evidence": list(self.evidence),
            "defaulted": self.defaulted,
        }


def _count(text: str, cues: tuple[str, ...]) -> tuple[int, list[str]]:
    hits: list[str] = []
    total = 0
    low = text.casefold()
    for cue in cues:
        if cue.casefold() in low:
            hits.append(cue)
            total += 1
    return total, hits


def classify(text: str, *, verdict: str = "FAIL", small_overshoot: bool = False) -> Cause:
    """按 cue 打分分类。``text`` 应含行文本 + 小节上下文（调用方拼接）。"""
    score: dict[str, int] = {}
    evidence: list[str] = []
    for label in ROOT_CAUSES:
        strong_n, strong_hits = _count(text, CUES[label]["strong"])
        weak_n, weak_hits = _count(text, CUES[label]["weak"])
        score[label] = 3 * strong_n + weak_n
        evidence.extend(strong_hits[:3])
    best = max(score.values()) if score else 0
    if best == 0:
        if small_overshoot:
            return Cause(
                label="discretization_resolution",
                score=score,
                evidence=(),
                defaulted=True,
            )
        label = "reference_limited" if verdict == "WITHHELD" else "implementation_defect"
        return Cause(label=label, score=score, evidence=(), defaulted=True)
    winners = [lab for lab in PRIORITY if score[lab] == best]
    return Cause(label=winners[0], score=score, evidence=tuple(dict.fromkeys(evidence)))


REQUEUE_TEMPLATES: dict[str, str] = {
    "reference_limited": "补齐一手参考（原始实验表/规范原文）并给出不确定带后复测；未补齐前不再派同口径复算",
    "reference_missing": "先做参考值审计（入 benchmarks/references + 出处），再派精度单；当前无基准不得判 FAIL",
    "discretization_resolution": "网格/面元加密到 GCI<3%（或阶数达标）后复测，附收敛表",
    "implementation_defect": "定位实现缺陷并附最小复现用例（pytest）修复后复测",
}