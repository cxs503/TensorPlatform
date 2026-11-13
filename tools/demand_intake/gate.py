"""gate —— 审计门：pending_review 锚的轻量裁决（纯函数，零 IO）。

三条判据（规格即代码，逐条可打靶）
----------------------------------
G1 **slug 规范**：``slugs.is_valid_slug``（段数≥2、段长 2..16、A-Z0-9、至少一个 ≥3 字母
   实词段——排除 ``A1-A2`` 这类步骤编号）；
G2 **量化验收非空**：acceptance 非空、≥4 字符，且含「数字 + 比较符/百分号/计量词」
   （只有形容词的验收条款不算判据，机器裁不了）；
G3 **不与既有锚重复**：slug 精确撞已有行（任何状态）→ ``duplicate_slug``；
   或与既有 ``open/in_progress/planned/done/verified`` 锚标题 token Jaccard ≥ 0.85
   → ``duplicate_title``（近义改写仍可能漏检，见 parser 的诚实边界）。

裁决只有三种：``open``（过门，进 feeder/board-claim 供给面）/ ``dropped``（不过门，留痕）
/ ``duplicate``（不新写行，只记日志）。**未过门绝不静默丢弃**：每条都带原因码落
``demand_intake_log``，红队与周报都对着这张表打。

为什么门要严而不是宽
--------------------
实测（2026-11-13，10879 张 succeeded 单跑本包 parser）：文本兜底路径上「锚」语境行
里 slug 形状 token 的噪声极大（``A1-A2``/``AG26W2-M1`` 这类步骤编号占多数）。宽口径
放行会把榜变成噪声榜——所以 G1/G2 是硬闸，宁可 dropped 也不污染可领面；被 dropped
的条目**同时**进 ``demand_backlog``（报告待办候选表），供 coordinator 周批人工采认，
需求不丢、只是不占可领面。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .slugs import is_valid_slug, slug_problem, title_similarity

#: 量化验收判据（与 parser 的抽取前置条件同一套正则，避免「抽得到却判不过」的漂移）
QUANT_NUM_RE = re.compile(r"\d")
QUANT_MARK_RE = re.compile(
    r"[<>≤≥=%]|以上|以下|不低于|不超过|达到|误差|偏差|通过率|覆盖率|条数|项数|小时|分钟|秒"
)

MIN_ACCEPTANCE_CHARS = 4
TITLE_DUP_THRESHOLD = 0.85

#: 参与「重复检测」的既有锚状态（planned 也算：已排期即已入榜）
DUP_STATUSES = ("open", "in_progress", "planned", "done", "verified", "pending_review")

PASS, DROPPED, DUPLICATE = "open", "dropped", "duplicate"


@dataclass
class Decision:
    slug: str
    verdict: str                 # open | dropped | duplicate
    reasons: List[str]           # 空=过门
    evidence: Dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.verdict == PASS


def acceptance_problem(acceptance: Any,
                       min_chars: int = MIN_ACCEPTANCE_CHARS) -> str:
    """量化验收判据；返回空串=合格。

    ``min_chars`` 由调用方指定：事前门（:func:`evaluate` 缺省 4）与事后审计
    （:mod:`tools.demand_intake.audit` 传 8）**共用同一套判据代码**——只调门槛不复制
    判据，避免两条规则漂移（「抽得到却判不过」/「门松审计紧」这类历史病）。
    """
    text = "" if acceptance is None else str(acceptance).strip()
    if not text:
        return "acceptance_empty"
    if len(text) < int(min_chars):
        return f"acceptance_too_short({len(text)})"
    if not QUANT_NUM_RE.search(text):
        return "acceptance_no_number"
    if not QUANT_MARK_RE.search(text):
        return "acceptance_no_threshold_marker"
    return ""


def evaluate(candidate: Dict[str, Any], existing: Sequence[Dict[str, Any]],
             *, min_acceptance_chars: int = MIN_ACCEPTANCE_CHARS) -> Decision:
    """单条候选裁决。``existing`` = 既有锚 ``[{slug,title,status}]``（含本次待审行）。

    ``min_acceptance_chars`` 透传给 :func:`acceptance_problem`（事后审计用 8）。
    """
    slug = str(candidate.get("slug") or "")
    title = str(candidate.get("title") or "")
    acceptance = candidate.get("acceptance") or ""
    reasons: List[str] = []

    problem = slug_problem(slug) if slug else "slug_empty"
    if problem:
        reasons.append(problem)

    acc_problem = acceptance_problem(acceptance,
                                     min_chars=min_acceptance_chars)
    if acc_problem:
        reasons.append(acc_problem)

    dup_of: Optional[Dict[str, Any]] = None
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
        dup_of = next(r for r in existing if str(r.get("slug") or "") == slug)
        reasons.append(f"duplicate_slug({dup_of.get('status')})")
    elif best_sim >= TITLE_DUP_THRESHOLD:
        reasons.append(f"duplicate_title({best_slug},{best_sim:.2f})")

    hard = [r for r in reasons if not r.startswith("duplicate_slug(")]
    if not hard and reasons:
        verdict = DUPLICATE          # 只撞 slug、其余合格 → 不新写行
    elif reasons:
        verdict = DROPPED
    else:
        verdict = PASS
    return Decision(slug=slug, verdict=verdict, reasons=reasons,
                    evidence={"title": title, "acceptance_len": len(str(acceptance)),
                              "best_title_similarity": round(best_sim, 4),
                              "similar_to": best_slug, "slug_ok": not problem,
                              "acceptance_ok": not acc_problem})


def gate_reason_codes(decisions: Iterable[Decision]) -> Dict[str, int]:
    """原因码分布（周报/红队共用口径）。"""
    out: Dict[str, int] = {}
    for d in decisions:
        for r in d.reasons or ["PASS"]:
            code = r.split("(")[0]
            out[code] = out.get(code, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
