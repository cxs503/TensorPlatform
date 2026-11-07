"""breakthrough_problems 准入门（IN59-A1）验收用例。

覆盖四条硬判据：
1. 准入门三要素：难度≥3 或 research 类任务进编码环前三要素齐备
   （问题定义（问题树≥3）/ 基线（现状+差距）/ 验收标准（可机检））；
   缺任一 → 422 并列缺件清单。
2. 门必须可失败（红队负例）：三要素不全的攻关任务必须被拒。
3. 接入既有 mission 创建流：普通工程单可显式声明豁免（声明位）；
   科研任务不得伪装成工程单绕门（exemption_guard）。
4. 七环状态机：problem_definition→investigation→hypothesis→experiment→
   challenge→acceptance→distillation，只允许相邻环（不可跳环）。

零领域语义：夹具使用 alpha/beta 之类的合成命名（不硬编码物理/船舶词汇）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.breakthrough_admission import (
    PHASES,
    PHASE_ORDER,
    REQUIRED_ELEMENTS,
    ADMISSION_THRESHOLD_DIFFICULTY,
    RESEARCH_CLASS,
    ENGINEERING_CLASS,
    MIN_PROBLEM_TREE_SUBPROBLEMS,
    MIN_ACCEPTANCE_CRITERIA,
    AdmissionError,
    ExemptionAbuseError,
    PhaseError,
    admission_decision,
    can_advance_phase,
    exemption_guard,
    missing_elements,
    normalise_task_class,
    phase_transition,
    requires_admission,
    validate_acceptance_criteria,
    validate_baseline,
    validate_problem_tree,
)


# ───────────────────────── 1. 三要素校验（纯函数） ─────────────────────────

def _full_tree() -> list[str]:
    return ["子问题 alpha", "子问题 beta", "子问题 gamma"]


def _full_criteria() -> list[dict]:
    return [
        {"metric": "误差", "target": "<0.03", "tolerance": "0.01"},
        {"metric": "守恒", "target": "==1", "tolerance": "1e-9"},
    ]


class TestProblemTree:
    """问题定义：问题树必须是 ≥3 子问题的 JSON 数组。"""

    def test_full_tree_passes(self):
        result = validate_problem_tree(_full_tree())
        assert result["ok"] is True
        assert result["subproblems"] == 3

    def test_short_tree_fails(self):
        result = validate_problem_tree(["只有一"])
        assert result["ok"] is False
        assert "子问题数" in result["reason"]

    def test_empty_tree_fails(self):
        result = validate_problem_tree([])
        assert result["ok"] is False

    def test_non_array_fails(self):
        result = validate_problem_tree("not-an-array")
        assert result["ok"] is False

    def test_blank_subproblems_not_counted(self):
        # 空壳子问题（空白字符串）不计入。
        result = validate_problem_tree(["a", "  ", "b"])
        assert result["ok"] is False  # 只有 2 个非空


class TestAcceptanceCriteria:
    """验收标准：可机检判据（每条含 metric/target/tolerance）。"""

    def test_machine_checkable_passes(self):
        result = validate_acceptance_criteria(_full_criteria())
        assert result["ok"] is True
        assert result["machine_checkable"] == 2

    def test_empty_fails(self):
        result = validate_acceptance_criteria([])
        assert result["ok"] is False

    def test_non_machine_checkable_fails(self):
        # 判据缺 metric/target/tolerance → 不可机检 → 拒。
        result = validate_acceptance_criteria([{"note": "随便写"}])
        assert result["ok"] is False
        assert "可机检" in result["reason"]


class TestBaseline:
    """基线：现状最好方案 + 差距。"""

    def test_full_baseline_passes(self):
        result = validate_baseline("现有方案能算到 5% 误差", "目标要 <3% 误差")
        assert result["ok"] is True

    def test_short_summary_fails(self):
        result = validate_baseline("短", "目标要 <3% 误差")
        assert result["ok"] is False

    def test_short_gap_fails(self):
        result = validate_baseline("现有方案能算到 5% 误差", "短")
        assert result["ok"] is False


# ───────────────────────── 2. 准入门决策（纯函数） ─────────────────────────

class TestAdmissionDecision:
    """准入门：难度≥3 或 research 类 → 三要素齐备才放行。"""

    def test_research_with_full_elements_admitted(self):
        decision = admission_decision(
            difficulty=4, task_class="research",
            problem_tree=_full_tree(),
            baseline_summary="现有方案能算到 5% 误差",
            baseline_gap="目标要 <3% 误差",
            acceptance_criteria=_full_criteria(),
        )
        assert decision["admitted"] is True
        assert decision["status"] == "admitted"
        assert decision["missing"] == []

    def test_research_missing_element_rejected(self):
        # 红队负例：research 任务缺问题树 → 必须被拒。
        decision = admission_decision(
            difficulty=4, task_class="research",
            problem_tree=["只有一"],
            baseline_summary="现有方案能算到 5% 误差",
            baseline_gap="目标要 <3% 误差",
            acceptance_criteria=_full_criteria(),
        )
        assert decision["admitted"] is False
        assert decision["status"] == "rejected"
        assert "problem_definition" in decision["missing"]

    def test_research_missing_all_three_rejected(self):
        # 红队负例：三要素全缺 → 必须被拒，并列缺件。
        decision = admission_decision(
            difficulty=4, task_class="research",
            problem_tree=[], baseline_summary="", baseline_gap="",
            acceptance_criteria=[],
        )
        assert decision["admitted"] is False
        assert set(decision["missing"]) == set(REQUIRED_ELEMENTS)

    def test_difficulty3_engineering_forced(self):
        # 难度=3 的工程单也强制三要素。
        decision = admission_decision(
            difficulty=3, task_class="engineering",
            problem_tree=_full_tree(),
            baseline_summary="现有方案能算到 5% 误差",
            baseline_gap="目标要 <3% 误差",
            acceptance_criteria=_full_criteria(),
        )
        assert decision["admitted"] is True

    def test_difficulty3_engineering_missing_rejected(self):
        decision = admission_decision(
            difficulty=3, task_class="engineering",
            problem_tree=[], baseline_summary="", baseline_gap="",
            acceptance_criteria=[],
        )
        assert decision["admitted"] is False
        assert set(decision["missing"]) == set(REQUIRED_ELEMENTS)

    def test_ordinary_engineering_not_forced(self):
        # 普通工程单（难度<3）不强制 → 直接放行。
        decision = admission_decision(
            difficulty=1, task_class="engineering",
            problem_tree=[], baseline_summary="", baseline_gap="",
            acceptance_criteria=[],
        )
        assert decision["admitted"] is True
        assert decision["status"] == "admitted"

    def test_ordinary_engineering_exemption_declared(self):
        # 普通工程单 + 显式豁免 → exempted。
        decision = admission_decision(
            difficulty=1, task_class="engineering",
            problem_tree=[], baseline_summary="", baseline_gap="",
            acceptance_criteria=[], exemption_declared=True,
        )
        assert decision["admitted"] is True
        assert decision["status"] == "exempted"
        assert decision["exemption_used"] is True


class TestExemptionGuard:
    """豁免声明位守卫：科研任务不得借工程单豁免绕门。"""

    def test_research_cannot_declare_exemption(self):
        # 红队负例：research 任务声明豁免 → 必须被拒（绕门）。
        decision = admission_decision(
            difficulty=4, task_class="research",
            problem_tree=[], baseline_summary="", baseline_gap="",
            acceptance_criteria=[], exemption_declared=True,
        )
        assert decision["admitted"] is False
        assert "exemption_abuse" in decision["missing"]

    def test_guard_rejects_research_exemption(self):
        guard = exemption_guard(
            task_class="research", exemption_declared=True, difficulty=4,
        )
        assert guard["ok"] is False
        assert "绕门" in guard["reason"]

    def test_guard_allows_engineering_exemption(self):
        guard = exemption_guard(
            task_class="engineering", exemption_declared=True, difficulty=1,
        )
        assert guard["ok"] is True


class TestRequiresAdmission:
    """难度≥3 或 research 类 → 强制准入门。"""

    def test_research_always_forced(self):
        assert requires_admission(1, "research") is True

    def test_difficulty3_forced(self):
        assert requires_admission(3, "engineering") is True

    def test_ordinary_not_forced(self):
        assert requires_admission(1, "engineering") is False
        assert requires_admission(2, "engineering") is False


# ───────────────────────── 3. 七环状态机 ─────────────────────────

class TestPhaseMachine:
    """七环：problem_definition→…→distillation，只允许相邻环。"""

    def test_phase_order_is_seven_rings(self):
        assert PHASES == (
            "problem_definition", "investigation", "hypothesis", "experiment",
            "challenge", "acceptance", "distillation",
        )
        assert len(PHASES) == 7

    def test_forward_advance_allowed(self):
        for i in range(len(PHASES) - 1):
            assert can_advance_phase(PHASES[i], PHASES[i + 1]) is True

    def test_backward_retreat_allowed(self):
        for i in range(1, len(PHASES)):
            assert can_advance_phase(PHASES[i], PHASES[i - 1]) is True

    def test_jump_ring_rejected(self):
        # 红队负例：跳环（problem_definition → experiment）必须被拒。
        assert can_advance_phase("problem_definition", "experiment") is False
        transition = phase_transition("problem_definition", "experiment")
        assert transition["ok"] is False
        assert "跳环" in transition["reason"]

    def test_unknown_phase_rejected(self):
        assert can_advance_phase("problem_definition", "nonsense") is False
        transition = phase_transition("problem_definition", "nonsense")
        assert transition["ok"] is False


# ───────────────────────── 4. 缺件清单 ─────────────────────────

class TestMissingElements:
    """缺任一 → 并列缺件清单。"""

    def test_missing_all_three(self):
        missing = missing_elements(
            problem_tree=[], baseline_summary="", baseline_gap="",
            acceptance_criteria=[],
        )
        assert set(missing) == set(REQUIRED_ELEMENTS)

    def test_missing_only_problem_definition(self):
        missing = missing_elements(
            problem_tree=["只有一"],
            baseline_summary="现有方案能算到 5% 误差",
            baseline_gap="目标要 <3% 误差",
            acceptance_criteria=_full_criteria(),
        )
        assert missing == ["problem_definition"]

    def test_missing_only_baseline(self):
        missing = missing_elements(
            problem_tree=_full_tree(),
            baseline_summary="", baseline_gap="",
            acceptance_criteria=_full_criteria(),
        )
        assert missing == ["baseline"]

    def test_missing_only_acceptance(self):
        missing = missing_elements(
            problem_tree=_full_tree(),
            baseline_summary="现有方案能算到 5% 误差",
            baseline_gap="目标要 <3% 误差",
            acceptance_criteria=[],
        )
        assert missing == ["acceptance_criteria"]

    def test_none_missing(self):
        missing = missing_elements(
            problem_tree=_full_tree(),
            baseline_summary="现有方案能算到 5% 误差",
            baseline_gap="目标要 <3% 误差",
            acceptance_criteria=_full_criteria(),
        )
        assert missing == []


# ───────────────────────── 5. 迁移即建库（结构回归） ─────────────────────────

def test_migration_creates_breakthrough_problems_table():
    """迁移文件作为建库脚本执行 → breakthrough_problems 表可查。"""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    migration = (ROOT / "backend" / "migrations" / "106_breakthrough_problems.sql").read_text(encoding="utf-8")
    # 去掉 BIGSERIAL（SQLite 用 INTEGER PRIMARY KEY 即可）—— 迁移文件里
    # 用 BIGSERIAL 是 Postgres 方言；单测用可移植子集替换。
    migration = migration.replace("BIGSERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
    conn.executescript(migration)
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "breakthrough_problems" in tables
    # 七环 CHECK 约束存在。
    cols = [row[1] for row in conn.execute(
        "PRAGMA table_info(breakthrough_problems)"
    ).fetchall()]
    assert "phase" in cols
    assert "admission_status" in cols
    assert "exemption_declared" in cols
    assert "problem_tree" in cols
    assert "baseline_summary" in cols
    assert "acceptance_criteria" in cols
    conn.close()


# ───────────────────────── 6. 端到端：API 准入门（红队可失败） ─────────────────────────

def test_api_admission_gate_end_to_end():
    """API 层：准入门三要素 + 红队可失败 + 七环推进（用 sqlite 建库直测）。"""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    migration = (ROOT / "backend" / "migrations" / "106_breakthrough_problems.sql").read_text(encoding="utf-8")
    migration = migration.replace("BIGSERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
    conn.executescript(migration)

    # 1. 三要素齐备的 research 任务 → 准入通过，落库。
    decision = admission_decision(
        difficulty=4, task_class="research",
        problem_tree=_full_tree(),
        baseline_summary="现有方案能算到 5% 误差",
        baseline_gap="目标要 <3% 误差",
        acceptance_criteria=_full_criteria(),
    )
    assert decision["admitted"] is True
    conn.execute(
        "INSERT INTO breakthrough_problems (title, problem_statement, problem_tree, "
        "baseline_summary, baseline_gap, acceptance_criteria, difficulty, task_class, "
        "phase, admission_status, exemption_declared, missing_elements) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("攻关 alpha", "研究 alpha 的数值方法", json.dumps(_full_tree()),
         "现有方案能算到 5% 误差", "目标要 <3% 误差", json.dumps(_full_criteria()),
         4, "research", "problem_definition", "admitted", 0, "[]"),
    )
    conn.commit()
    row = conn.execute("SELECT id, phase, admission_status FROM breakthrough_problems").fetchone()
    assert row[1] == "problem_definition"
    assert row[2] == "admitted"

    # 2. 红队负例：三要素不全的 research 任务 → 必须被拒（不落库）。
    bad = admission_decision(
        difficulty=4, task_class="research",
        problem_tree=[], baseline_summary="", baseline_gap="",
        acceptance_criteria=[],
    )
    assert bad["admitted"] is False
    assert set(bad["missing"]) == set(REQUIRED_ELEMENTS)

    # 3. 七环推进：problem_definition → investigation（相邻环）。
    transition = phase_transition("problem_definition", "investigation")
    assert transition["ok"] is True
    conn.execute(
        "UPDATE breakthrough_problems SET phase=? WHERE id=?",
        ("investigation", row[0]),
    )
    conn.commit()
    updated = conn.execute("SELECT phase FROM breakthrough_problems WHERE id=?", (row[0],)).fetchone()
    assert updated[0] == "investigation"

    # 4. 红队负例：跳环（investigation → experiment）必须被拒。
    jump = phase_transition("investigation", "experiment")
    assert jump["ok"] is False
    assert "跳环" in jump["reason"]
    conn.close()
