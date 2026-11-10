"""pytest suite for the evidence debt compiler (P519-N01).

All tests use **synthetic** domain names and units only (zero domain semantics).
Maps to campaign node acceptance criteria:

C1  classifier accuracy on 10 synthetic WITHHELD declarations ... test_c1_*
C2  each declaration generates ≥1 verification task ............. test_c2_*
C3  verification execution updates evidence level ............... test_c3_*
C4  zero domain semantics (grep domain words = 0 hits) .......... test_c4_*
C5  idempotency (repeated run doesn't duplicate) ................ test_c5_*
C6  KPI invariant: n_upgraded+n_failed+n_remaining=n_initial .... test_c6_*
C7  pytest ≥15 cases all green .................................. this file
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

WORKTREE = Path(__file__).resolve().parents[3]
if str(WORKTREE) not in sys.path:
    sys.path.insert(0, str(WORKTREE))

from tools.evidence_debt_compiler import (  # noqa: E402
    BaselineClassifier,
    BaselineType,
    EvidenceDebtCompiler,
    EvidenceDebtExtractor,
    KPICalculator,
    VerificationOutcome,
    VerificationResult,
    VerificationTask,
    VerificationTaskGenerator,
)
from tools.evidence_debt_compiler.model import (  # noqa: E402
    ClassifiedDebt,
    ExtractedDebt,
    LevelUpdate,
    PipelineKPI,
)


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def extractor():
    return EvidenceDebtExtractor()


@pytest.fixture
def classifier():
    return BaselineClassifier()


@pytest.fixture
def task_generator():
    return VerificationTaskGenerator()


@pytest.fixture
def compiler():
    return EvidenceDebtCompiler(db_path=":memory:")


def make_debt(cap_id: str, text: str, debt_id: str = "") -> ExtractedDebt:
    """Helper to create a synthetic ExtractedDebt."""
    import uuid
    return ExtractedDebt(
        debt_id=debt_id or f"debt-{uuid.uuid4().hex[:8]}",
        capability_id=cap_id,
        source_report_id="synth-report-001",
        declaration_text=text,
    )


# ── C1: Classifier accuracy on 10 synthetic WITHHELD declarations ────────

def test_c1_classifier_analytical_solution():
    """Classifier correctly identifies analytical solution baseline need."""
    clf = BaselineClassifier()
    debt = make_debt("synth.cap.01", "capability synth.cap.01 is WITHHELD: needs analytical solution comparison")
    result = clf.classify(debt)
    assert result.baseline_type == BaselineType.ANALYTICAL_SOLUTION
    assert result.confidence > 0


def test_c1_classifier_experimental():
    """Classifier correctly identifies experimental baseline need."""
    clf = BaselineClassifier()
    debt = make_debt("synth.cap.02", "capability synth.cap.02 WITHHELD: lacks experimental validation data")
    result = clf.classify(debt)
    assert result.baseline_type == BaselineType.EXPERIMENTAL
    assert result.confidence > 0


def test_c1_classifier_mesh_convergence():
    """Classifier correctly identifies mesh convergence baseline need."""
    clf = BaselineClassifier()
    debt = make_debt("synth.cap.03", "capability synth.cap.03 is WITHHELD: no mesh convergence study performed")
    result = clf.classify(debt)
    assert result.baseline_type == BaselineType.MESH_CONVERGENCE
    assert result.confidence > 0


def test_c1_classifier_physical_consistency():
    """Classifier correctly identifies physical consistency baseline need."""
    clf = BaselineClassifier()
    debt = make_debt("synth.cap.04", "capability synth.cap.04 WITHHELD: conservation law check pending")
    result = clf.classify(debt)
    assert result.baseline_type == BaselineType.PHYSICAL_CONSISTENCY
    assert result.confidence > 0


def test_c1_classifier_cross_validation():
    """Classifier correctly identifies cross-validation baseline need."""
    clf = BaselineClassifier()
    debt = make_debt("synth.cap.05", "capability synth.cap.05 is WITHHELD: cross-validation with another method needed")
    result = clf.classify(debt)
    assert result.baseline_type == BaselineType.CROSS_VALIDATION
    assert result.confidence > 0


def test_c1_classifier_batch_10_declarations():
    """Classifier achieves 100% accuracy on 10 synthetic declarations."""
    clf = BaselineClassifier()
    declarations = [
        ("synth.c01", "WITHHELD: needs analytical solution", BaselineType.ANALYTICAL_SOLUTION),
        ("synth.c02", "WITHHELD: lacks experimental data", BaselineType.EXPERIMENTAL),
        ("synth.c03", "WITHHELD: no mesh convergence", BaselineType.MESH_CONVERGENCE),
        ("synth.c04", "WITHHELD: conservation check pending", BaselineType.PHYSICAL_CONSISTENCY),
        ("synth.c05", "WITHHELD: cross-validation needed", BaselineType.CROSS_VALIDATION),
        ("synth.c06", "WITHHELD: exact solution comparison", BaselineType.ANALYTICAL_SOLUTION),
        ("synth.c07", "WITHHELD: model test data missing", BaselineType.EXPERIMENTAL),
        ("synth.c08", "WITHHELD: grid independence study", BaselineType.MESH_CONVERGENCE),
        ("synth.c09", "WITHHELD: symmetry check needed", BaselineType.PHYSICAL_CONSISTENCY),
        ("synth.c10", "WITHHELD: code-to-code comparison", BaselineType.CROSS_VALIDATION),
    ]
    correct = 0
    for cap_id, text, expected in declarations:
        debt = make_debt(cap_id, text)
        result = clf.classify(debt)
        if result.baseline_type == expected:
            correct += 1
    accuracy = correct / len(declarations)
    assert accuracy == 1.0, f"Expected 100% accuracy, got {accuracy*100}%"


def test_c1_classifier_unknown_fallback():
    """Classifier returns UNKNOWN for unclassifiable text."""
    clf = BaselineClassifier()
    debt = make_debt("synth.cap.99", "capability synth.cap.99 is WITHHELD: no specific baseline mentioned")
    result = clf.classify(debt)
    assert result.baseline_type == BaselineType.UNKNOWN
    assert result.confidence == 0.0


# ── C2: Each declaration generates ≥1 verification task ──────────────────

def test_c2_each_debt_generates_task():
    """Each classified debt generates at least one verification task."""
    clf = BaselineClassifier()
    gen = VerificationTaskGenerator()
    debts = [
        make_debt("synth.t01", "WITHHELD: needs analytical solution"),
        make_debt("synth.t02", "WITHHELD: lacks experimental data"),
        make_debt("synth.t03", "WITHHELD: no mesh convergence"),
    ]
    classified = clf.classify_batch(debts)
    tasks = gen.generate_batch(classified)
    assert len(tasks) >= len(debts), "Each debt should generate at least one task"
    for task in tasks:
        assert task.task_id
        assert task.capability_id
        assert task.spec


def test_c2_task_spec_has_required_fields():
    """Generated task specs contain required fields."""
    clf = BaselineClassifier()
    gen = VerificationTaskGenerator()
    debt = make_debt("synth.t04", "WITHHELD: needs analytical solution")
    classified = clf.classify(debt)
    task = gen.generate(classified)
    assert "verification_kind" in task.spec
    assert "required_evidence" in task.spec
    assert "acceptance_criteria" in task.spec
    assert task.spec["capability_id"] == "synth.t04"


# ── C3: Verification execution updates evidence level ────────────────────

def test_c3_pass_verification_upgrades_to_real():
    """PASS verification result upgrades WITHHELD to REAL."""
    def mock_executor(task: VerificationTask) -> VerificationResult:
        return VerificationResult(
            task_id=task.task_id,
            debt_id=task.debt_id,
            capability_id=task.capability_id,
            outcome=VerificationOutcome.PASS,
            metrics={"error_pct": 1.5},
        )

    compiler = EvidenceDebtCompiler(
        db_path=":memory:",
        verification_executor=mock_executor,
    )
    reports = [{
        "report_id": "synth-rpt-01",
        "text": "capability synth.up.01 is WITHHELD: needs analytical solution comparison",
    }]
    result = compiler.run(reports)
    updates = result["level_updates"]
    assert len(updates) == 1
    assert updates[0]["before_level"] == "WITHHELD"
    assert updates[0]["after_level"] == "REAL"
    compiler.close()


def test_c3_fail_verification_stays_withheld():
    """FAIL verification result keeps WITHHELD."""
    def mock_executor(task: VerificationTask) -> VerificationResult:
        return VerificationResult(
            task_id=task.task_id,
            debt_id=task.debt_id,
            capability_id=task.capability_id,
            outcome=VerificationOutcome.FAIL,
            metrics={"error_pct": 15.0},
        )

    compiler = EvidenceDebtCompiler(
        db_path=":memory:",
        verification_executor=mock_executor,
    )
    reports = [{
        "report_id": "synth-rpt-02",
        "text": "capability synth.up.02 is WITHHELD: needs analytical solution",
    }]
    result = compiler.run(reports)
    updates = result["level_updates"]
    assert len(updates) == 1
    assert updates[0]["before_level"] == "WITHHELD"
    assert updates[0]["after_level"] == "WITHHELD"
    compiler.close()


# ── C4: Zero domain semantics ────────────────────────────────────────────

def test_c4_no_domain_words_in_source():
    """Source code contains no domain-specific vocabulary."""
    import re
    src_dir = Path(__file__).resolve().parents[1]
    domain_words = [
        r"\bship\b", r"\bhull\b", r"\bresistance\b", r"\bpropeller\b",
        r"\bwave\b", r"\bice\b", r"\bfroude\b", r"\breynolds\b",
        r"\bhydrostatic\b", r"\bseakeeping\b", r"\bmanoeuvr",
    ]
    domain_pattern = re.compile("|".join(domain_words), re.IGNORECASE)
    hits = []
    for py_file in src_dir.glob("*.py"):
        if py_file.name == "test_evidence_debt_compiler.py":
            continue  # skip test file itself
        content = py_file.read_text()
        matches = domain_pattern.findall(content)
        if matches:
            hits.append((py_file.name, matches))
    assert len(hits) == 0, f"Domain words found in source: {hits}"


# ── C5: Idempotency ──────────────────────────────────────────────────────

def test_c5_repeated_run_skips_duplicates():
    """Repeated pipeline run skips already-processed debts."""
    call_count = 0

    def mock_executor(task: VerificationTask) -> VerificationResult:
        nonlocal call_count
        call_count += 1
        return VerificationResult(
            task_id=task.task_id,
            debt_id=task.debt_id,
            capability_id=task.capability_id,
            outcome=VerificationOutcome.PASS,
        )

    compiler = EvidenceDebtCompiler(
        db_path=":memory:",
        verification_executor=mock_executor,
    )
    reports = [{
        "report_id": "synth-rpt-idem",
        "text": "capability synth.idem.01 is WITHHELD: needs analytical solution",
    }]

    # First run
    result1 = compiler.run(reports)
    assert call_count == 1
    assert result1["kpi"]["n_upgraded"] == 1

    # Second run — should skip the already-processed debt
    result2 = compiler.run(reports)
    assert call_count == 1, "Executor should not be called again"
    assert result2["skipped_duplicate"] == 1
    assert result2["kpi"]["n_withheld_initial"] == 0
    compiler.close()


# ── C6: KPI invariant ────────────────────────────────────────────────────

def test_c6_kpi_invariant_holds():
    """KPI invariant: n_upgraded + n_failed + n_remaining = n_WITHHELD_initial."""
    kpi = PipelineKPI(
        n_withheld_initial=10,
        n_upgraded=4,
        n_failed=3,
        n_remaining=3,
    )
    kpi.compute()
    assert kpi.n_upgraded + kpi.n_failed + kpi.n_remaining == kpi.n_withheld_initial
    assert kpi.closure_rate == 0.4


def test_c6_kpi_invariant_violation_raises():
    """KPI invariant violation raises ValueError."""
    kpi = PipelineKPI(
        n_withheld_initial=10,
        n_upgraded=4,
        n_failed=3,
        n_remaining=2,  # 4+3+2=9 != 10
    )
    with pytest.raises(ValueError, match="invariant violated"):
        kpi.compute()


def test_c6_kpi_closure_rate_calculation():
    """Closure rate = n_upgraded / n_WITHHELD_initial."""
    kpi = PipelineKPI(
        n_withheld_initial=20,
        n_upgraded=15,
        n_failed=3,
        n_remaining=2,
    )
    kpi.compute()
    assert kpi.closure_rate == 0.75


def test_c6_kpi_zero_initial():
    """KPI handles zero initial WITHHELD count."""
    kpi = PipelineKPI(n_withheld_initial=0)
    kpi.compute()
    assert kpi.closure_rate == 0.0


# ── C7: Additional coverage ──────────────────────────────────────────────

def test_c7_extractor_from_structured_report():
    """Extractor works on structured report dicts."""
    ext = EvidenceDebtExtractor()
    report = {
        "report_id": "synth-struct-01",
        "capabilities": {
            "synth.str.01": {"level": "WITHHELD", "label": "test cap 1"},
            "synth.str.02": {"level": "REAL", "label": "test cap 2"},
            "synth.str.03": {"level": "WITHHELD", "label": "test cap 3"},
        },
    }
    debts = ext.extract_from_structured(report)
    assert len(debts) == 2
    cap_ids = {d.capability_id for d in debts}
    assert cap_ids == {"synth.str.01", "synth.str.03"}


def test_c7_extractor_empty_report():
    """Extractor handles empty reports gracefully."""
    ext = EvidenceDebtExtractor()
    debts = ext.extract("", "empty-rpt")
    assert debts == []


def test_c7_full_pipeline_dry_run():
    """Full pipeline in dry-run mode generates tasks without execution."""
    compiler = EvidenceDebtCompiler(db_path=":memory:")
    reports = [
        {
            "report_id": "synth-dry-01",
            "text": "capability synth.dry.01 is WITHHELD: needs analytical solution",
        },
        {
            "report_id": "synth-dry-02",
            "text": "capability synth.dry.02 is WITHHELD: lacks experimental data",
        },
    ]
    result = compiler.run(reports, dry_run=True)
    assert result["kpi"]["n_withheld_initial"] == 2
    assert len(result["verification_tasks"]) == 2
    assert all(r["outcome"] == "skipped" for r in result["verification_results"])
    compiler.close()


def test_c7_multiple_reports_aggregated():
    """Pipeline aggregates debts from multiple reports."""
    compiler = EvidenceDebtCompiler(db_path=":memory:")
    reports = [
        {"report_id": "r1", "text": "capability synth.multi.01 is WITHHELD: analytical solution needed"},
        {"report_id": "r2", "text": "capability synth.multi.02 is WITHHELD: experimental data missing"},
        {"report_id": "r3", "text": "capability synth.multi.03 is WITHHELD: mesh convergence study required"},
    ]
    result = compiler.run(reports, dry_run=True)
    assert result["kpi"]["n_withheld_initial"] == 3
    assert len(result["classified_debts"]) == 3
    compiler.close()


def test_c7_level_updater_called_on_upgrade():
    """Level updater callback is called when upgrade occurs."""
    updated_caps = []

    def mock_updater(cap_id: str, new_level: str, reason: str) -> bool:
        updated_caps.append((cap_id, new_level, reason))
        return True

    def mock_executor(task: VerificationTask) -> VerificationResult:
        return VerificationResult(
            task_id=task.task_id,
            debt_id=task.debt_id,
            capability_id=task.capability_id,
            outcome=VerificationOutcome.PASS,
        )

    compiler = EvidenceDebtCompiler(
        db_path=":memory:",
        verification_executor=mock_executor,
        level_updater=mock_updater,
    )
    reports = [{"report_id": "r1", "text": "capability synth.cb.01 is WITHHELD: analytical solution"}]
    compiler.run(reports)
    assert len(updated_caps) == 1
    assert updated_caps[0][0] == "synth.cb.01"
    assert updated_caps[0][1] == "REAL"
    compiler.close()


# ── Run all tests ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
