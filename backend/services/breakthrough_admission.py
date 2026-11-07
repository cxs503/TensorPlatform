"""breakthrough_problems 准入门（admission gate）—— 纯逻辑，零领域语义。

Purpose
-------
S4/S5 突破/创造类任务（难度≥3 或 research 类）进入编码环前，必须三要素齐备：

    1. problem_definition  问题定义（问题树 ≥3 子问题）
    2. baseline            基线（现状最好方案 + 差距）
    3. acceptance_criteria 验收标准（可机检处必须机检化）

缺任一 → 422 并列缺件清单。门必须可失败：三要素不全的攻关任务必须被拒
（红队负例）。普通工程单可显式声明豁免（exemption_declared 声明位），但
科研任务不得伪装成工程单绕门（exemption_guard 强制）。

设计约束（与 mission_gradient / guidance_loop 同源）：
* 零领域语义：本模块只认识「难度 / 任务类别 / 三要素 / 七环阶段」这些
  治理词，不硬编码任何物理/船舶词汇（机器检查在测试里强制）。
* 无 I/O、无 DB、无网络：每个函数纯且确定，派发器、API 层与验收 harness
  可共享同一判据。
* 七环状态机：problem_definition→investigation→hypothesis→experiment→
  challenge→acceptance→distillation，只允许沿环前进（可回退到上一环，
  但不可跳环），保证「阶段推进」可机检。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "PHASES",
    "PHASE_ORDER",
    "REQUIRED_ELEMENTS",
    "ELEMENT_LABELS",
    "ADMISSION_THRESHOLD_DIFFICULTY",
    "RESEARCH_CLASS",
    "ENGINEERING_CLASS",
    "MIN_PROBLEM_TREE_SUBPROBLEMS",
    "MIN_ACCEPTANCE_CRITERIA",
    "MIN_BASELINE_CHARS",
    "MIN_GAP_CHARS",
    "AdmissionError",
    "ExemptionAbuseError",
    "PhaseError",
    "normalise_task_class",
    "requires_admission",
    "validate_problem_tree",
    "validate_acceptance_criteria",
    "validate_baseline",
    "admission_decision",
    "exemption_guard",
    "phase_transition",
    "can_advance_phase",
    "missing_elements",
]

# ─────────────────────────────── constants ───────────────────────────────

#: 七环状态机（顺序即推进方向）。
PHASES: tuple[str, ...] = (
    "problem_definition",
    "investigation",
    "hypothesis",
    "experiment",
    "challenge",
    "acceptance",
    "distillation",
)

PHASE_ORDER: dict[str, int] = {phase: index for index, phase in enumerate(PHASES)}

#: 准入门三要素（缺任一 → 422 并列缺件清单）。
REQUIRED_ELEMENTS: tuple[str, ...] = (
    "problem_definition",
    "baseline",
    "acceptance_criteria",
)

ELEMENT_LABELS: dict[str, str] = {
    "problem_definition": "问题定义（问题树 ≥3 子问题）",
    "baseline": "基线（现状最好方案 + 差距）",
    "acceptance_criteria": "验收标准（可机检判据）",
}

#: 难度 ≥ 此值 → 强制走准入门（research 类任务同样强制）。
ADMISSION_THRESHOLD_DIFFICULTY = 3

RESEARCH_CLASS = "research"
ENGINEERING_CLASS = "engineering"

#: 问题树最少子问题数（机检判据）。
MIN_PROBLEM_TREE_SUBPROBLEMS = 3

#: 验收标准最少条数（可机检判据）。
MIN_ACCEPTANCE_CRITERIA = 1

#: 基线描述 / 差距描述最少字符（防空壳）。
MIN_BASELINE_CHARS = 8
MIN_GAP_CHARS = 8

#: 可机检验收判据的形态（每条须含 metric + target + tolerance）。
_ACCEPTANCE_CRITERION_KEYS = ("metric", "target", "tolerance")


class AdmissionError(ValueError):
    """准入门输入/输出不合法。"""


class ExemptionAbuseError(AdmissionError):
    """科研任务伪装成工程单声明豁免（绕门）—— 必须被拒。"""


class PhaseError(ValueError):
    """七环状态机推进不合法（跳环 / 未知环）。"""


# ──────────────────────────────── helpers ─────────────────────────────────


def _is_json_array(value: Any) -> bool:
    """True 当 value 是 JSON 数组（list 或可解析的 JSON 字符串）。"""
    if isinstance(value, list):
        return True
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return False
        return isinstance(parsed, list)
    return False


def _json_array_items(value: Any) -> list[Any]:
    """把 JSON 数组（list 或字符串）解成元素列表。"""
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return []
        return list(parsed) if isinstance(parsed, list) else []
    return []


def normalise_task_class(task_class: Any) -> str:
    """规范化任务类别：``research`` / ``engineering``（大小写不敏感）。"""
    if isinstance(task_class, str):
        cls = task_class.strip().lower()
        if cls in (RESEARCH_CLASS, ENGINEERING_CLASS):
            return cls
    raise AdmissionError(f"未知任务类别: {task_class!r}")


def requires_admission(difficulty: Any, task_class: Any) -> bool:
    """是否强制走准入门：难度≥3 或 research 类任务。

    普通工程单（difficulty<3 且 engineering）不强制 —— 可显式声明豁免。
    """
    if isinstance(difficulty, bool) or not isinstance(difficulty, (int, float)):
        raise AdmissionError(f"difficulty 必须是 1..5 的数字，got {difficulty!r}")
    d = int(difficulty)
    if d < 1 or d > 5:
        raise AdmissionError(f"difficulty 越界 1..5: {d}")
    cls = normalise_task_class(task_class)
    return d >= ADMISSION_THRESHOLD_DIFFICULTY or cls == RESEARCH_CLASS


# ──────────────────────────────── 三要素校验 ─────────────────────────────


def validate_problem_tree(problem_tree: Any) -> dict[str, Any]:
    """问题定义：问题树必须是 ≥3 子问题的 JSON 数组。

    返回 ``{"ok", "subproblems", "reason"}``；不满足时 ``ok=False``。
    """
    if not _is_json_array(problem_tree):
        return {"ok": False, "subproblems": 0, "reason": "problem_tree 必须是 JSON 数组"}
    items = _json_array_items(problem_tree)
    # 子问题须是非空字符串（防空壳）。
    real = [s for s in items if isinstance(s, str) and s.strip()]
    if len(real) < MIN_PROBLEM_TREE_SUBPROBLEMS:
        return {
            "ok": False,
            "subproblems": len(real),
            "reason": f"问题树子问题数 < {MIN_PROBLEM_TREE_SUBPROBLEMS}（缺 {MIN_PROBLEM_TREE_SUBPROBLEMS - len(real)} 个）",
        }
    return {"ok": True, "subproblems": len(real), "reason": None}


def validate_acceptance_criteria(acceptance_criteria: Any) -> dict[str, Any]:
    """验收标准：可机检判据清单（每条含 metric/target/tolerance）。

    返回 ``{"ok", "criteria", "machine_checkable", "reason"}``。
    """
    if not _is_json_array(acceptance_criteria):
        return {"ok": False, "criteria": 0, "machine_checkable": False,
                "reason": "acceptance_criteria 必须是 JSON 数组"}
    items = _json_array_items(acceptance_criteria)
    if len(items) < MIN_ACCEPTANCE_CRITERIA:
        return {"ok": False, "criteria": len(items), "machine_checkable": False,
                "reason": f"验收标准条数 < {MIN_ACCEPTANCE_CRITERIA}"}
    # 每条判据须含 metric/target/tolerance 三键（可机检形态）。
    machine_checkable = 0
    for item in items:
        if isinstance(item, Mapping) and all(k in item for k in _ACCEPTANCE_CRITERION_KEYS):
            machine_checkable += 1
    if machine_checkable < MIN_ACCEPTANCE_CRITERIA:
        return {"ok": False, "criteria": len(items), "machine_checkable": machine_checkable,
                "reason": f"可机检判据 < {MIN_ACCEPTANCE_CRITERIA}（每条须含 metric/target/tolerance）"}
    return {"ok": True, "criteria": len(items), "machine_checkable": machine_checkable, "reason": None}


def validate_baseline(baseline_summary: Any, baseline_gap: Any) -> dict[str, Any]:
    """基线：现状最好方案（baseline_summary）+ 差距（baseline_gap）。

    返回 ``{"ok", "reason"}``。
    """
    if not isinstance(baseline_summary, str) or len(baseline_summary.strip()) < MIN_BASELINE_CHARS:
        return {"ok": False, "reason": f"基线描述过短（< {MIN_BASELINE_CHARS} 字符）"}
    if not isinstance(baseline_gap, str) or len(baseline_gap.strip()) < MIN_GAP_CHARS:
        return {"ok": False, "reason": f"差距描述过短（< {MIN_GAP_CHARS} 字符）"}
    return {"ok": True, "reason": None}


# ──────────────────────────────── 准入门决策 ─────────────────────────────


def missing_elements(
    *,
    problem_tree: Any,
    baseline_summary: Any,
    baseline_gap: Any,
    acceptance_criteria: Any,
) -> list[str]:
    """并列列出缺件（三要素中不满足的项）。"""
    missing: list[str] = []
    tree = validate_problem_tree(problem_tree)
    if not tree["ok"]:
        missing.append("problem_definition")
    base = validate_baseline(baseline_summary, baseline_gap)
    if not base["ok"]:
        missing.append("baseline")
    acc = validate_acceptance_criteria(acceptance_criteria)
    if not acc["ok"]:
        missing.append("acceptance_criteria")
    return missing


def admission_decision(
    *,
    difficulty: Any,
    task_class: Any,
    problem_tree: Any,
    baseline_summary: Any,
    baseline_gap: Any,
    acceptance_criteria: Any,
    exemption_declared: bool = False,
) -> dict[str, Any]:
    """准入门决策（纯函数，不抛异常）。

    返回 ``{"admitted", "status", "missing", "reason", "exemption_used"}``。

    规则：
      1. 难度<3 且 engineering → 不强制（可豁免）；若显式声明豁免 → exempted。
      2. 难度≥3 或 research → 强制三要素；缺任一 → rejected（并列缺件）。
      3. 科研任务（research）即使声明豁免也**不得**绕门（exemption_guard）。
    """
    try:
        cls = normalise_task_class(task_class)
    except AdmissionError:
        return {"admitted": False, "status": "rejected", "missing": ["task_class"],
                "reason": "任务类别不合法", "exemption_used": False}
    try:
        d = int(difficulty)
        if d < 1 or d > 5:
            raise AdmissionError("difficulty 越界")
    except (AdmissionError, TypeError, ValueError):
        return {"admitted": False, "status": "rejected", "missing": ["difficulty"],
                "reason": "难度不合法", "exemption_used": False}

    forced = requires_admission(d, cls)

    # 科研任务伪装成工程单声明豁免 → 绕门，必须被拒。
    if cls == RESEARCH_CLASS and exemption_declared:
        return {"admitted": False, "status": "rejected", "missing": ["exemption_abuse"],
                "reason": "科研任务不得声明豁免绕门（exemption_guard）", "exemption_used": True}

    # 普通工程单 + 显式豁免 → 放行（exempted）。
    if not forced and exemption_declared:
        return {"admitted": True, "status": "exempted", "missing": [],
                "reason": "普通工程单显式声明豁免", "exemption_used": True}

    # 不强制（普通工程单，未声明豁免）→ 直接放行。
    if not forced:
        return {"admitted": True, "status": "admitted", "missing": [],
                "reason": "普通工程单（难度<3）不强制准入门", "exemption_used": False}

    # 强制 → 三要素校验。
    missing = missing_elements(
        problem_tree=problem_tree,
        baseline_summary=baseline_summary,
        baseline_gap=baseline_gap,
        acceptance_criteria=acceptance_criteria,
    )
    if missing:
        return {"admitted": False, "status": "rejected", "missing": missing,
                "reason": "三要素不全（缺件并列列出）", "exemption_used": False}
    return {"admitted": True, "status": "admitted", "missing": [],
            "reason": "三要素齐备，准入门通过", "exemption_used": False}


def exemption_guard(
    *,
    task_class: Any,
    exemption_declared: bool,
    difficulty: Any,
) -> dict[str, Any]:
    """豁免声明位守卫：科研任务不得借工程单豁免绕门。

    返回 ``{"ok", "reason"}``；科研任务声明豁免 → ``ok=False``（必须被拒）。
    """
    try:
        cls = normalise_task_class(task_class)
    except AdmissionError:
        return {"ok": False, "reason": "任务类别不合法"}
    if cls == RESEARCH_CLASS and exemption_declared:
        return {"ok": False, "reason": "科研任务不得声明豁免绕门（exemption_guard）"}
    return {"ok": True, "reason": None}


# ──────────────────────────────── 七环状态机 ─────────────────────────────


def can_advance_phase(current: str, target: str) -> bool:
    """七环状态机：只允许沿环前进（或回退到上一环），不可跳环。

    ``current -> target`` 合法当且仅当 ``target`` 是 ``current`` 的下一环
    （前进）或上一环（回退）。``current == target`` 视为无操作（合法）。
    """
    if current not in PHASE_ORDER or target not in PHASE_ORDER:
        return False
    delta = PHASE_ORDER[target] - PHASE_ORDER[current]
    return delta in (-1, 0, 1)


def phase_transition(current: str, target: str) -> dict[str, Any]:
    """七环状态机推进（不抛异常）。返回 ``{"ok", "from", "to", "reason"}``。"""
    if current not in PHASE_ORDER:
        return {"ok": False, "from": current, "to": target, "reason": f"未知当前环: {current!r}"}
    if target not in PHASE_ORDER:
        return {"ok": False, "from": current, "to": target, "reason": f"未知目标环: {target!r}"}
    if not can_advance_phase(current, target):
        return {"ok": False, "from": current, "to": target,
                "reason": f"跳环：{current} -> {target} 只允许相邻环（前进/回退一步）"}
    return {"ok": True, "from": current, "to": target, "reason": None}
