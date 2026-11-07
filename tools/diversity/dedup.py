"""同族查重：方案摘要相似度门（fail-closed）+ 反同族人口约束。

**要防的东西**：锦标赛里最常见的伪多样性不是"复读同一句"，而是**换词不换招**——
同一个算法骨架上改形容词、换常数、调顺序，交上来当作新方案。令牌级 Jaccard 对
这类改写不敏感（同义词把交集吃掉），因此本模块用两层表征 + 一层人口约束：

1. **表层（lexical）**：NFKC 归一小写 → 拉丁词 + CJK 二元组集合；
2. **骨架（skeleton）**：在表层之前先把"连接词近义词表"归一到代表词、把数字/量纲
   替换为 `<n>`，再做集合——顺序与措辞被抹平，**方法骨架**被留下；
3. **人口约束（anti-monoculture）**：同一 clade 的席位占比超过上限即拒绝新来者，
   这是"反同族"在群体层面（而不是两两相似度层面）的实现。

**fail-closed 来源**：与 `ops/taskplane/campaign_dispatcher.py::_hollow()` 同一打法
（复用的正是那套"台账查不了就不放行"的硬门）：摘要过短、缺交付物、缺 clade 标识、
令牌为空——一律**拒**，绝不因为"看不出来"而放行。门是机器门：下游无法用一句
"这是新方案"绕过。

零平台域语义：输入只是 (方案摘要文本, clade_id, 交付物标记)。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

MIN_SUMMARY_CHARS = 40
# 判定阈值（由 tools/diversity/run_node.py 在标注语料上核实并写进 artifacts：
# 独立方案对最大骨架相似度 << T_CLONE_SKELETON << 克隆对最小骨架相似度）
T_CLONE_SKELETON = 0.72
T_CLONE_CONTAINMENT = 0.85
T_CLONE_LEXICAL = 0.80
T_SAME_CLADE = 0.45          # 同族更严：同族相近即视作同骰子
T_NUMBER_IDENTICAL = 0.50    # 数字集合完全相同 + 骨架过半 = 只调参的克隆
T_SKELETON_FOR_NUMBERS = 0.50
CLADE_SHARE_CAP = 0.60       # 任一族占已收方案比例上限

# 近义词归一表（只收方法中立的连接/动作词；不含任何学科词，保持域无关）。
_SYNONYMS: Tuple[Tuple[str, str], ...] = tuple(sorted({
    ("给出", "提供"), ("提供", "给出"), ("采用", "使用"), ("使用", "采用"),
    ("运用", "使用"), ("通过", "经由"), ("利用", "使用"), ("构建", "构造"),
    ("构造", "构建"), ("建立", "构造"), ("验证", "核验"), ("核验", "验证"),
    ("检验", "验证"), ("说明", "阐述"), ("阐述", "说明"), ("需要", "必须"),
    ("应当", "必须"), ("应", "必须"), ("同时", "并且"), ("而且", "并且"),
    ("并", "并且"), ("以及", "与"), ("和", "与"), ("首先", "第一"),
    ("其次", "第二"), ("最后", "第三"), ("改进", "优化"), ("提升", "优化"),
    ("提高", "优化"), ("计算", "求解"), ("求解", "计算"), ("估计", "估算"),
    ("估算", "估计"), ("误差", "偏差"), ("偏差", "误差"), ("步骤", "环节"),
    ("环节", "步骤"), ("方案", "思路"), ("思路", "方案"),
}, key=lambda kv: -len(kv[0])))

_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?%?")
_LATIN = re.compile(r"[a-z][a-z0-9_./-]{1,}")
_CJK = re.compile(r"[\u3400-\u9fff]")


class GateReject(ValueError):
    """门在"看不清"时的拒收（fail-closed 的显式类型）。"""


@dataclass
class Decision:
    accepted: bool
    reasons: List[str]
    nearest: Optional[dict] = None
    sims: List[dict] = field(default_factory=list)
    candidate_clade: str = ""

    def as_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "reasons": list(self.reasons),
            "nearest": self.nearest,
            "candidate_clade": self.candidate_clade,
        }


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def numbers(text: str) -> List[str]:
    """数字指纹：格式归一（0.30 → 0.3），保序去重，用于识别"只改常数"的克隆。"""
    out: List[str] = []
    for m in _NUM.findall(_norm(text)):
        tok = m.rstrip("%")
        try:
            tok = f"{float(tok):.6g}"
        except ValueError:
            pass
        if tok not in out:
            out.append(tok)
    return out


def skeleton(text: str) -> str:
    """骨架归一：近义词 → 代表词，数字/量纲 → <n>，保留拉丁标识符（方法名是骨架的一部分）。"""
    s = _norm(text)
    for a, b in _SYNONYMS:
        if a == b:
            continue
        s = s.replace(a, b)
    # 只把"裸数字"抹平；附着在标识符上的数字（rk4、tau0.5）保留，因为它们区分方法。
    s = re.sub(r"(?<![a-z0-9_.])[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?%?(?![a-z0-9_])", " <n> ", s)
    return s


def tokens(text: str) -> set:
    """表层令牌：拉丁词（len≥2）+ CJK 二元组（单字丢弃，避免中文虚词撑起交集）。"""
    s = _norm(text)
    toks = set(_LATIN.findall(s))
    chars = [c for c in s if _CJK.match(c)]
    for i in range(len(chars) - 1):
        toks.add(chars[i] + chars[i + 1])
    for n in numbers(text):
        toks.add("num:" + n)
    return toks


def measures(a: str, b: str) -> dict:
    """四路相似度：表层 Jaccard / 包含度 / 骨架 Jaccard / 数字集合一致度。"""
    ta, tb = tokens(a), tokens(b)
    ka, kb = tokens(skeleton(a)), tokens(skeleton(b))
    inter_t = len(ta & tb)
    inter_k = len(ka & kb)
    lex = inter_t / len(ta | tb) if (ta | tb) else 0.0
    cont = inter_t / min(len(ta), len(tb)) if min(len(ta), len(tb)) else 0.0
    skel = inter_k / len(ka | kb) if (ka | kb) else 0.0
    na, nb = set(numbers(a)), set(numbers(b))
    num = inter_t and (len(na & nb) / len(na | nb) if (na | nb) else 0.0) or 0.0
    return {
        "lexical": round(lex, 4),
        "containment": round(cont, 4),
        "skeleton": round(skel, 4),
        "numbers": round(num, 4),
    }


def _clone_reasons(sim: dict, same_clade: bool) -> List[str]:
    reasons: List[str] = []
    if sim["skeleton"] >= T_CLONE_SKELETON:
        reasons.append(f"CLONE_SKELETON({sim['skeleton']:.2f}>={T_CLONE_SKELETON})")
    if sim["containment"] >= T_CLONE_CONTAINMENT:
        reasons.append(f"CLONE_CONTAINMENT({sim['containment']:.2f}>={T_CLONE_CONTAINMENT})")
    if sim["lexical"] >= T_CLONE_LEXICAL:
        reasons.append(f"CLONE_LEXICAL({sim['lexical']:.2f}>={T_CLONE_LEXICAL})")
    if same_clade and sim["skeleton"] >= T_SAME_CLADE:
        reasons.append(f"SAME_CLADE_REPEAT({sim['skeleton']:.2f}>={T_SAME_CLADE})")
    if sim["numbers"] >= 0.999 and sim["skeleton"] >= T_SKELETON_FOR_NUMBERS:
        reasons.append(f"RETUNE_ONLY(numbers=1.0,skeleton={sim['skeleton']:.2f})")
    return reasons


def admit(
    candidate: str,
    accepted: Sequence[dict],
    *,
    candidate_clade: str = "",
    deliverable: str = "",
) -> Decision:
    """相似度门：返回 Decision。`accepted` 每项至少含 {summary, clade_id}。

    fail-closed：摘要过短 / 无交付物 / 无 clade / 令牌为空 → 拒（理由显式）。
    """
    reasons: List[str] = []
    text = (candidate or "").strip()
    if len(text) < MIN_SUMMARY_CHARS:
        return Decision(False, [f"SUMMARY_TOO_SHORT({len(text)}<{MIN_SUMMARY_CHARS})"],
                        None, [], candidate_clade)
    if not (deliverable or "").strip():
        return Decision(False, ["NO_DELIVERABLE"], None, [], candidate_clade)
    if not candidate_clade.strip():
        return Decision(False, ["NO_CLADE_ID"], None, [], candidate_clade)
    if not tokens(text):
        return Decision(False, ["EMPTY_TOKEN_SET"], None, [], candidate_clade)

    sims = []
    for ref in accepted:
        sim = measures(text, str(ref.get("summary", "")))
        sim.update({"ref": ref.get("id") or ref.get("seat_id") or "", "clade": ref.get("clade_id", "")})
        sims.append(sim)
    worst = None
    for sim in sims:
        r = _clone_reasons(sim, same_clade=(sim["clade"] == candidate_clade))
        if r:
            sim = dict(sim, reasons=r)
            if worst is None or sim["skeleton"] > worst["skeleton"]:
                worst = sim
            reasons.extend(r)
    sims.sort(key=lambda s: -s["skeleton"])

    # 人口约束：某一族已占满上限 → 该族新方案需要显著更低相似度才放行（反同族）。
    n = len(accepted)
    if n >= 3:
        share = sum(1 for r in accepted if r.get("clade_id") == candidate_clade) / n
        if share >= CLADE_SHARE_CAP:
            reasons.append(f"CLADE_SHARE_CAP({candidate_clade}:{share:.2f}>={CLADE_SHARE_CAP})")

    return Decision(not reasons, reasons, worst or (sims[0] if sims else None), sims, candidate_clade)


def population_report(accepted: Sequence[dict], clades: Sequence[str]) -> dict:
    """已收方案的人口结构：各族占比、前两族占比（防单族垄断）、最相似对。"""
    n = len(accepted)
    counts = {c: sum(1 for r in accepted if r.get("clade_id") == c) for c in clades}
    shares = {c: (round(v / n, 4) if n else 0.0) for c, v in counts.items()}
    top2 = sorted(shares.values(), reverse=True)[:2]
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            sim = measures(str(accepted[i].get("summary", "")), str(accepted[j].get("summary", "")))
            pairs.append({
                "a": accepted[i].get("id"), "b": accepted[j].get("id"),
                **sim,
            })
    pairs.sort(key=lambda p: -p["skeleton"])
    return {
        "n_accepted": n,
        "family_counts": counts,
        "family_shares": shares,
        "top2_share": round(sum(top2), 4),
        "max_pairwise_skeleton": pairs[0]["skeleton"] if pairs else None,
        "pairs": pairs[:10],
    }