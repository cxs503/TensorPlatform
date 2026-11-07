"""generator —— 质询使命生成器。

职责：
  * 从候选池里选出当期 3-5 条（I1），跨族覆盖 + 严重度优先 + 7 周期去重（I2）；
  * 生成可直接 ``POST /tensor/missions`` 的 mission 参数：
      - goal ≥80 字，含题目、证据数字（带 source）、≥3 个解释分支、机器可判判据、
        honest FAIL 纪律、转化要求；
      - ``task_key = interrogator:<fingerprint>`` 让平台侧创建幂等（重复周期不重复立单）；
      - ``origin = agent``（平台 CHECK 只允许 owner/agent），生成者身份记在 goal 标记里。

设计取舍：题目文本由 question_bank 决定，本模块只做**排版与配额**，
因此改文案不会影响判据；改配额只需动 ``MIN/MAX_PER_CYCLE``。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Iterable

from .ledger import MAX_PER_CYCLE, MIN_PER_CYCLE
from .question_bank import FAMILIES, FAMILY_RATIONALE, FAMILY_TITLES, Question, candidates
from .probes import Signal

GENERATOR_TAG = "interrogator"
GOAL_MARKER = "【战略质询 IN27-H4】"
DEDUPE_WINDOW_CYCLES = 7
DEFAULT_BUDGET = {"max_tool_calls": 60, "max_wall_seconds": 3600, "max_tokens": 600_000}


def cycle_target_count(cycle_date: str, lo: int = MIN_PER_CYCLE, hi: int = MAX_PER_CYCLE) -> int:
    """当期题数：3-5，按日期确定（可复跑、可预期，不做随机）。"""
    digest = int(hashlib.sha256(f"IN27-H4:{cycle_date}".encode()).hexdigest()[:8], 16)
    return lo + digest % (hi - lo + 1)


def _family_rotation_rank(cycle_date: str) -> dict[str, int]:
    digest = int(hashlib.sha256(f"rot:{cycle_date}".encode()).hexdigest()[:8], 16)
    start = digest % len(FAMILIES)
    return {FAMILIES[(start + i) % len(FAMILIES)]: i for i in range(len(FAMILIES))}


def select_questions(
    signals: dict[str, Signal],
    ledger_stats: dict[str, Any] | None,
    recent_fingerprints: Iterable[str],
    cycle_date: str,
    target: int | None = None,
) -> list[Question]:
    """选当期题目：先去重，再跨族轮转 + 严重度优先，不足时按最旧补位（保证 ≥3）。"""
    pool = candidates(signals, ledger_stats)
    if not pool:
        raise RuntimeError("候选池为空：信号与账本同时不可用，拒绝生成空质询")
    recent = set(recent_fingerprints)
    rotation = _family_rotation_rank(cycle_date)
    n = target or cycle_target_count(cycle_date)

    fresh = [q for q in pool if q.fingerprint not in recent]
    fresh.sort(key=lambda q: (-q.severity, rotation.get(q.family, 99), q.fingerprint))

    chosen: list[Question] = []
    for q in fresh:
        if len(chosen) >= n:
            break
        chosen.append(q)
    # 跨族覆盖：当选中的族数 < 2 且池里有别的族，换入别的族最高分题
    if len({q.family for q in chosen}) < 2:
        for q in fresh:
            if q.family not in {c.family for c in chosen}:
                chosen.append(q)
                break
    # 不足下限（题目总数少）：允许复用最久未问的题（recent 之外已无货）
    if len(chosen) < MIN_PER_CYCLE:
        for q in pool:
            if q.fingerprint in {c.fingerprint for c in chosen}:
                continue
            chosen.append(q)
            if len(chosen) >= MIN_PER_CYCLE:
                break
    if len(chosen) > MAX_PER_CYCLE:
        chosen = chosen[:MAX_PER_CYCLE]
    if len(chosen) < MIN_PER_CYCLE:
        raise RuntimeError(f"可选题目不足 {MIN_PER_CYCLE} 条（实得 {len(chosen)}）")
    return chosen


def _evidence_block(q: Question) -> str:
    lines = []
    for ev in q.evidence:
        lines.append(f"  - {ev.get('signal')} = {json.dumps(ev.get('value'), ensure_ascii=False)}"
                     f"  [status={ev.get('status')}]  来源: {ev.get('source')}")
    return "\n".join(lines) if lines else "  - （本期无可用信号，度量缺失本身即待判定对象）"


def build_goal(q: Question, cycle_date: str, signals: dict[str, Signal]) -> str:
    """生成 mission goal（≥80 字，含判据/honest FAIL/转化要求/幂等键）。"""
    branches = "\n".join(f"  {i+1}. {b}" for i, b in enumerate(q.branches))
    criteria = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(q.criteria))
    statuses = {name: sig.status for name, sig in signals.items()}
    goal = f"""{GOAL_MARKER}质询族 {q.family}（{FAMILY_TITLES.get(q.family, q.family)}）· 周期 {cycle_date}

质询题（问对不问错）:
{q.text}

为什么问这个:
  {FAMILY_RATIONALE.get(q.family, '')}

本期事实（数字全部来自探针，可回溯到同期快照 signals.json）:
{_evidence_block(q)}

必须排除的互斥解释分支（择一为主因，其余注明排除证据）:
{branches}

验收判据（机器可判）:
{criteria}
  末项. 结论必须落到「是否需要立修补/改进单」的二元判断；需要则写明 {q.convert_rule}

纪律:
- 只读审计：本单不改产品代码，只产出判定与证据（修补单另立）。
- honest FAIL 不可覆盖：证据不足以判定就如实写 FAIL 与缺口，禁止猜测式定论。
- 数字必须来自平台表/端点/文件，注明路径；禁止手抄或估算。
- 报告须含判据数字与产物路径，产物落 docs/ 或 runs/ 内。

转化要求:
  {q.convert_rule}

幂等键: task_key=interrogator:{q.fingerprint.replace(':', '-').lower()}（重复周期不重复立单）
信号源状态: {json.dumps(statuses, ensure_ascii=False)}"""
    if len(goal) < 80:  # 理论上不会触发，保留为防御
        goal += f"\n\n（本单由 {GENERATOR_TAG} 于 {cycle_date} 自动生成）"
    return goal.strip()


def to_mission_params(q: Question, cycle_date: str, signals: dict[str, Signal],
                      repo: str | None = None, base_ref: str = "main") -> dict[str, Any]:
    return {
        "goal": build_goal(q, cycle_date, signals),
        "repo": repo or q.target_repo,
        "base_ref": base_ref,
        "origin": "agent",
        "task_key": f"interrogator:{q.fingerprint.replace(':', '-').lower()}",
        "budget": dict(DEFAULT_BUDGET),
    }


def build_cycle(
    signals: dict[str, Signal],
    ledger_stats: dict[str, Any] | None,
    recent_fingerprints: Iterable[str],
    cycle_date: str,
    repo: str = "TensorPlatform",
) -> list[dict[str, Any]]:
    """一期完整产物：每项含 question dict + mission 参数（可直接下发）。"""
    questions = select_questions(signals, ledger_stats, recent_fingerprints, cycle_date)
    return [{"question": q.to_dict(),
             "mission": to_mission_params(q, cycle_date, signals, repo=repo)}
            for q in questions]


def summarize(questions: list[Question]) -> dict[str, Any]:
    cov: dict[str, int] = {}
    for q in questions:
        cov[q.family] = cov.get(q.family, 0) + 1
    return {"count": len(questions), "families": cov,
            "fingerprints": [q.fingerprint for q in questions],
            "max_severity": max((q.severity for q in questions), default=0)}