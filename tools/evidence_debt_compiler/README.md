# Evidence Debt Compiler (P519-N01)

WITHHELD→REAL automatic upgrade pipeline and evidence debt compiler.

## Purpose

Automates the evidence governance lifecycle:
1. **Extract** WITHHELD declarations from completed node reports
2. **Classify** missing baseline types (analytical/experimental/mesh/physical/cross-validation)
3. **Generate** verification task specifications
4. **Execute** verification tasks (via cluster or mock executor)
5. **Update** evidence levels based on results
6. **Report** KPIs (closure_rate = n_upgraded / n_WITHHELD_initial)

## Design Principles

- **Zero domain semantics**: No domain vocabulary in code; all domain data via injection
- **Idempotent**: Repeated runs skip already-processed debts (SQLite tracking)
- **KPI invariant**: `n_upgraded + n_failed + n_remaining = n_WITHHELD_initial`
- **Deterministic**: Same inputs → same outputs (content-addressed deduplication)

## Usage

```python
from tools.evidence_debt_compiler import EvidenceDebtCompiler

compiler = EvidenceDebtCompiler(db_path="evidence_debts.db")
result = compiler.run(reports=[
    {"report_id": "rpt-001", "text": "capability synth.alpha is WITHHELD: needs analytical solution"},
])
print(result["kpi"]["closure_rate"])
```

## Modules

| Module | Purpose |
|--------|---------|
| `model.py` | Data models (ExtractedDebt, ClassifiedDebt, VerificationTask, etc.) |
| `extractor.py` | Extract WITHHELD declarations from report text |
| `classifier.py` | Classify missing baseline types |
| `task_generator.py` | Generate verification task specs |
| `orchestrator.py` | Main pipeline orchestrator |
| `kpi.py` | KPI calculation and invariant validation |

## Tests

```bash
pytest tools/evidence_debt_compiler/tests/ -v
```

22 test cases covering:
- C1: Classifier accuracy (100% on 10 synthetic declarations)
- C2: Task generation (≥1 task per declaration)
- C3: Level updates (WITHHELD→REAL on pass, stays WITHHELD on fail)
- C4: Zero domain semantics (grep domain words = 0 hits)
- C5: Idempotency (repeated runs skip duplicates)
- C6: KPI invariant (n_upgraded+n_failed+n_remaining=n_initial)
- C7: Additional coverage (structured reports, dry-run, callbacks)
