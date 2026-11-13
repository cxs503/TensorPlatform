"""judge —— 锚质量判据（纯函数，零 IO），v2 用于**事后**审计。

三条判据（v1 沿用，判据本身不因门位后移而放松）
------------------------------------------------
A1 **slug 规范**：段数 ≥2、段长 2..16、A-Z0-9、至少一个 ≥3 字母实词段
   （排除 ``A1-A2`` 这类步骤编号）；
A2 **量化验收非空**：acceptance 非空、≥8 字符，且同时含数字与比较符/百分号/计量词
   （只有形容词的验收条款机器裁不了）；
A3 **不与既有锚重复**：slug 撞任何状态的既有行 → ``duplicate_slug``；或与
   open/in_progress/planned/done/verified 锚的标题 token Jaccard ≥ 0.85 →
   ``duplicate_title``（近义改写有漏检，这是诚实边界，见 parser 的同类声明）。

为什么 v2 把裁决搬到事后还不放松判据
------------------------------------
事前门（v1）的代价是**建议被扣在 ``pending_review`` 里，等不到人就成了幽灵**——锚
不进供给面、单子不产生，agent 的独立思考在帐上等价于零。v2 的取舍是「先上车、后查票」：
铸造阶段只做机器可原子裁决的两件事（帽、slug 唯一），质量判据放在事后抽样审计里，
不过则 ``dropped`` + 关联未终态单 ``revoked``。所以判据必须**更**严而不是更宽——
否则审计帽（20/日）撑不起覆盖，噪声锚会直接占领可领面。

判定只有两种：``pass``（保持 open）/ ``fail``（dropped，带原因码）。原因码是周报与
红队的共用语汇，故全部 ASCII snake_case，禁止在代码里出现任何域词条。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .slugs import slug_problem, title_similarity

#: 量化验收判据
QUANT_NUM_RE = re.compile(r"\d")
QUANT_MARK_RE = re.compile(
    r"[<>≤≥=%]|以上|以下|不低于|不超过|达到|误差|偏差|通过率|覆盖率|条数|项数|小时|分钟|秒"
)

MIN_ACCEPTANCE_CHARS = 8
TITLE_DUP_THRESHOLD = 0.85

#: 参与「重复检测」的既有锚状态（planned 也算：已排期即已入榜）
DUP_STATUSES = ("open", "in_progress", "planned", "done", "verified")

PASS, FAIL = "pass", "fail"


@dataclass
class Decision:
    slug: str
    verdict: str                      # pass | fail
    reasons: List[str] = field(default_factory=list)   # 空 = 过审
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.verdict == PASS


def acceptance_problem(acceptance: Any) -> str:
    """量化验收判据；返回空串=合格。"""
    text = "" if acceptance is None else str(acceptance).strip()
    if not text:
        return "acceptance_empty"
    if len(text) < MIN_ACCEPTANCE_CHARS:
        return f"acceptance_too_short({len(text)})"
    if not QUANT_NUM_RE.search(text):
        return "acceptance_no_number"
    if not QUANT_MARK_RE.search(text):
        return "acceptance_no_threshold_marker"
    return ""


def evaluate(candidate: Dict[str, Any], existing: Sequence[Dict[str, Any]]) -> Decision:
    """单条审计裁决。``existing`` = 既有锚 ``[{slug,title,status}]``（含本条自身行）。"""
    slug = str(candidate.get("slug") or "")
    title = str(candidate.get("title") or "")
    acceptance = candidate.get("acceptance") or ""
    reasons: List[str] = []

    problem = slug_problem(slug) if slug else "slug_empty"
    if problem:
        reasons.append(problem)

    acc_problem = acceptance_problem(acceptance)
    if acc_problem:
        reasons.append(acc_problem)

    best_sim, best_slug = 0.0, ""
    for row in existing:
        eslug = str(row.get("slug") or "")
        if not eslug or eslug == slug:
            continue
        if str(row.get("status") or "") not in DUP_STATUSES:
            continue
        sim = title_similarity(title, row.get("title"))
        if sim > best_sim:
            best_sim, best_slug = sim, eslug
    if slug and any(str(r.get("slug") or "") == slug for r in existing):
        # 铸造期已被唯一约束挡住；走到这里只可能是历史遗留行，仍按重复判
        reasons.append("duplicate_slug")
    elif best_sim >= TITLE_DUP_THRESHOLD:
        reasons.append(f"duplicate_title({best_slug},{best_sim:.2f})")

    verdict = PASS if not reasons else FAIL
    return Decision(slug=slug, verdict=verdict, reasons=reasons,
                    evidence={"title": title,
                              "acceptance_len": len(str(acceptance)),
                              "best_title_similarity": round(best_sim, 4),
                              "similar_to": best_slug,
                              "slug_ok": not problem,
                              "acceptance_ok": not acc_problem})


def reason_codes(decisions: Iterable[Decision]) -> Dict[str, int]:
    """原因码分布（周报/红队共用口径）。"""
    out: Dict[str, int] = {}
    for d in decisions:
        for r in d.reasons or ["PASS"]:
            code = r.split("(")[0]
            out[code] = out.get(code, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def mint_precheck(candidate: Dict[str, Any]) -> str:
    """铸造期的**可机器先行**拦截面：只拦 slug 空/形状坏（其余留到事后审计）。

    为什么不一并拦验收条款：帽与唯一性是「原子可裁」的硬账，验收是「语义可议」的
    判据；把可议的东西塞进铸造同步路径，等于让 agent 的建议在路上等裁决——正是 v2
    要拆掉的那道门。故铸造期只拦「连账本键都不成立」的行。
    """
    slug = str(candidate.get("slug") or "")
    if not slug:
        return "slug_empty"
    return ""