"""Acceptance tests for the evidence second-implementation maturity layer.

Criteria covered (mission p256w1-n02, machine-checkable):

(a) injection detection: each of the three forgery classes caught on >= 2/3 targets,
    overall >= 2/3;
(b) false alarms: the untouched control population raises 0 alarms;
(c) every relation instance has a negative control that actually fails;
(d) the post-regrade self-reference share is queryable per capability *from SQL*,
    with evidence line numbers;
(e) the ledger write-back is reversible (backup + rollback drill) and takes < 5 s;
(f) zero domain vocabulary in the mechanism layer (scanner + synthetic handle ids).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tools.evidence_second_impl import (
    build_declarations,
    figures,
    handles as H,
    independence,
    injection,
    ledger,
    metamorphic,
    runner,
)
from tools.evidence_second_impl.model import (
    DeclarationRecord,
    EvidenceLocator,
    HandleSpec,
    InjectionType,
    ReferenceSource,
    RelationKind,
    RelationSpec,
    RelationStatus,
    line_sha256,
)

ROOT = runner.ROOT


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def population():
    return H.load_manifest(H.build_manifest(proxy_ids=["synth.proxy.1"]))


@pytest.fixture(scope="module")
def bench(population):
    return injection.run_bench(population)


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out = tmp_path_factory.mktemp("second_impl")
    return runner.run(ROOT, out)


# ---------------------------------------------------------------------------
# (a) injection detection
# ---------------------------------------------------------------------------

def test_injection_detection_per_type(bench):
    summary = bench["summary"]
    assert summary["controls"] == 20 and len(bench["injections"]) == 9
    for kind, stats in summary["per_injection"].items():
        assert stats["handles"] == 3, kind
        assert stats["detected"] / stats["handles"] >= 2 / 3, (kind, stats)
        assert stats["detected"] >= 2, (kind, stats)
    assert summary["injection_rate"] >= 2 / 3


def test_injection_detected_by_declared_relation(bench):
    """Detection must come from a relation, and the detecting kind must be plausible."""
    blind = {RelationKind.MIRROR_PARITY.value, RelationKind.SCALING.value,
             RelationKind.ZERO_POINT.value, RelationKind.ADDITIVITY.value}
    for row in bench["injections"]:
        assert row["detected"] is True
        assert row["detecting_kinds"], row
        if row["injection"] in (InjectionType.SCALE_1P05.value,
                                InjectionType.SIGN_FLIP.value):
            # scale-invariant relations are mathematically blind to a global factor
            assert set(row["detecting_kinds"]) - blind, row


def test_multiplier_injection_survives_ratio_relations_alone(population):
    """Honest negative result: a scale-invariant relation cannot see x1.05."""
    spec = next(s for s in population if s.family == "power")
    reference, second = H.build_oracles(spec)
    forged = injection.inject(second, InjectionType.SCALE_1P05, reference(spec.probes["base"]))
    scaling = next(r for r in spec.relations if r.kind is RelationKind.SCALING)
    res = metamorphic.evaluate(scaling, spec, forged, reference)
    assert res.status is RelationStatus.PASS          # blind by construction
    equiv = next(r for r in spec.relations if r.kind is RelationKind.EQUIVALENCE)
    assert metamorphic.evaluate(equiv, spec, forged, reference).status is RelationStatus.FAIL


# ---------------------------------------------------------------------------
# (b) false alarms
# ---------------------------------------------------------------------------

def test_no_false_alarms(bench):
    summary = bench["summary"]
    assert summary["false_alarm_count"] == 0, summary["false_alarms"]
    assert summary["false_alarm_rate"] == 0.0
    assert summary["clean_alarm_count"] == 0, summary["clean_alarms_all_handles"]
    assert all(not c["flagged"] for c in bench["controls"])


def test_clean_residuals_are_small_but_nonzero(bench):
    """The second implementation is a real second path, not a copy: tiny residual."""
    ratios = [c["max_residual_ratio"] for c in bench["clean"]]
    assert max(ratios) < 1e-2          # comfortably below the pass/fail boundary
    assert max(ratios) > 0.0           # not a bit-identical copy


# ---------------------------------------------------------------------------
# (c) negative controls
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", [k for k in RelationKind])
def test_negative_control_fails_for_every_relation_kind(kind, population):
    """Anti-vacuity: for every relation kind on a suitable handle, the mutant fails."""
    suitable = {
        RelationKind.EQUIVALENCE: "linear",
        RelationKind.SCALING: "quadratic",
        RelationKind.ZERO_POINT: "linear",
        RelationKind.ADDITIVITY: "superposition",
        RelationKind.MIRROR_PARITY: "cubic",
        RelationKind.CONSERVATION: "partition",
    }[kind]
    spec = next(s for s in population if s.family == suitable)
    reference, second = H.build_oracles(spec)
    rel = next(r for r in spec.relations if r.kind is kind)
    result = metamorphic.negative_control(rel, spec, second, reference)
    assert result["honest"]["status"] == "PASS"
    assert result["mutant_result"]["status"] == "FAIL"
    assert result["discriminating"] is True


def test_every_relation_instance_is_discriminating(population):
    n = discriminating = 0
    for spec in population:
        reference, second = H.build_oracles(spec)
        for rel in spec.relations:
            out = metamorphic.negative_control(rel, spec, second, reference)
            n += 1
            discriminating += int(out["discriminating"])
    assert n > 0
    assert discriminating == n


def test_relation_catalogue_is_complete():
    cat = metamorphic.catalogue()
    assert set(cat) == {k.value for k in RelationKind}
    for kind, entry in cat.items():
        assert entry["statement"] and entry["domain"] and entry["negative_control"], kind


# ---------------------------------------------------------------------------
# (d) independence re-grade
# ---------------------------------------------------------------------------

def _record(cid, grade, source, locators=(), corroboration=None, reason=""):
    return DeclarationRecord(capability_id=cid, graded_grade=grade,
                             reference_source=source, locators=tuple(locators),
                             independent_corroboration=corroboration, reason=reason)


def _loc(path: str, line: int, root: Path) -> EvidenceLocator:
    text = (root / path).read_text(encoding="utf-8").splitlines()[line - 1]
    return EvidenceLocator(path=path, line=line, line_sha256=line_sha256(text))


def test_real_with_verified_independent_locator_is_kept(tmp_path):
    f = tmp_path / "ev.txt"
    f.write_text("alpha\nreference value 12.5\n", encoding="utf-8")
    loc = EvidenceLocator("ev.txt", 2, line_sha256("reference value 12.5"))
    out = independence.regrade(_record("synth.cap.a", "REAL",
                                       ReferenceSource.ANALYTIC_CLOSED_FORM, [loc]), tmp_path)
    assert out["verdict"] == "KEEP" and out["regraded_grade"] == "REAL"
    assert out["self_reference_share"] == 0.0


def test_self_reference_is_downgraded(tmp_path):
    f = tmp_path / "ev.txt"
    f.write_text("residual against own solver\n", encoding="utf-8")
    loc = EvidenceLocator("ev.txt", 1, line_sha256("residual against own solver"))
    out = independence.regrade(_record("synth.cap.b", "REAL",
                                       ReferenceSource.SAME_CODE_SELF_REFERENCE, [loc]), tmp_path)
    assert out["verdict"] == "DOWNGRADE"
    assert out["regraded_grade"] == "WITHHELD"
    assert out["reason_code"] == independence.REASON_DOWNGRADE_SELF


def test_self_reference_with_independent_corroboration_is_retained_flagged(tmp_path):
    f = tmp_path / "ev.txt"
    f.write_text("self residual\nindependent anchor\n", encoding="utf-8")
    loc = EvidenceLocator("ev.txt", 1, line_sha256("self residual"))
    corr = EvidenceLocator("ev.txt", 2, line_sha256("independent anchor"))
    out = independence.regrade(_record("synth.cap.c", "REAL",
                                       ReferenceSource.SAME_CODE_SELF_REFERENCE,
                                       [loc], corr), tmp_path)
    assert out["verdict"] == "RETAIN_FLAGGED"
    assert out["regraded_grade"] == "REAL"
    assert out["self_reference_share"] == 1.0     # visible, not laundered


def test_forged_locator_is_downgraded(tmp_path):
    f = tmp_path / "ev.txt"
    f.write_text("reference value 12.5\n", encoding="utf-8")
    bad = EvidenceLocator("ev.txt", 1, "deadbeefdeadbeef")
    out = independence.regrade(_record("synth.cap.d", "REAL",
                                       ReferenceSource.EXTERNAL_DATA, [bad]), tmp_path)
    assert out["verdict"] == "DOWNGRADE"
    assert out["reason_code"] == independence.REASON_DOWNGRADE_NO_LOCATOR
    assert out["independence"] == "UNVERIFIED"


def test_missing_locator_line_is_downgraded(tmp_path):
    f = tmp_path / "ev.txt"
    f.write_text("one line\n", encoding="utf-8")
    out = independence.regrade(_record("synth.cap.e", "REAL",
                                       ReferenceSource.INDEPENDENT_ALGORITHM,
                                       [EvidenceLocator("ev.txt", 99, "0123456789abcdef")]),
                               tmp_path)
    assert out["verdict"] == "DOWNGRADE"
    assert out["locators"][0]["verified"] is False


def test_non_real_claim_is_not_applicable(tmp_path):
    out = independence.regrade(_record("synth.cap.f", "WITHHELD",
                                       ReferenceSource.NOT_ASSERTED), tmp_path)
    assert out["verdict"] == "NOT_APPLICABLE"
    assert out["regraded_grade"] == "WITHHELD"


def test_population_regrade_summary(report):
    ind = report["independence"]
    assert ind["n_capabilities"] == 12
    assert ind["real_before"] == 5
    assert ind["real_after"] == ind["real_before"] - ind["downgrade_count"]
    assert ind["downgrade_count"] >= 1
    assert ind["self_reference_share_after"] == 0.0
    assert ind["self_reference_share_before"] == pytest.approx(
        ind["self_reference_count"] / ind["real_before"])


def test_declarations_are_hash_pinned(tmp_path):
    out = tmp_path / "declarations.json"
    info = build_declarations.build(out_path=out)
    records = independence.load_declarations(out)
    assert info["n_declarations"] == len(records) == 12
    for rec in records:
        assert rec.locators, rec.capability_id
        for loc in rec.locators:
            assert loc.line_sha256 and loc.line >= 1
            assert independence.verify_locator(loc, ROOT).verified, (rec.capability_id, loc)


def test_ambiguous_needle_is_rejected(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("needle\nneedle\n", encoding="utf-8")
    with pytest.raises(build_declarations.LocatorResolutionError):
        build_declarations.resolve_locator({"path": str(src), "needle": "needle"}, tmp_path)


# ---------------------------------------------------------------------------
# (e) ledger write-back, rollback, timing
# ---------------------------------------------------------------------------

def test_ledger_write_and_rollback(tmp_path, report):
    db = tmp_path / "l.sqlite"
    caps = report["ledger"]["sql"] and json.loads(json.dumps(
        [_row_for_ledger(r) for r in independence.regrade_all(
            independence.load_declarations(ROOT / runner.DECLARATIONS_REL), ROOT)]))
    info = ledger.write_back(db, capabilities=caps, handle_results=[], negative_controls=[],
                             injections=[], meta={"k": 1})
    assert info["elapsed_seconds"] < 5.0
    assert info["backup"]["bytes"] > 0
    drill = ledger.rollback_drill(db, info["backup"], info["pre_digest"])
    assert drill["verified"] is True


def _row_for_ledger(row):
    keep = ("capability_id", "graded_grade", "regraded_grade", "reference_source",
            "independence", "verdict", "reason_code", "self_reference",
            "self_reference_share", "verified_locators", "reason", "locators",
            "corroboration")
    return {k: row[k] for k in keep if k in row}


def test_ledger_rollback_restores_previous_state(tmp_path):
    db = tmp_path / "l.sqlite"
    ledger.init_schema(db)
    pre = ledger.table_digest(db)
    caps = [{"capability_id": "synth.cap.z", "graded_grade": "REAL",
             "regraded_grade": "WITHHELD", "reference_source": "same_code_self_reference",
             "independence": "SELF_REFERENTIAL", "verdict": "DOWNGRADE",
             "reason_code": "SELF_REFERENCE_NO_INDEPENDENT_CORROBORATION",
             "self_reference": True, "self_reference_share": 0.0,
             "verified_locators": 1, "reason": "unit test",
             "locators": [{"path": "x", "line": 1, "declared_sha256": "a",
                           "actual_sha256": "a", "verified": True, "note": ""}]}]
    info = ledger.write_back(db, capabilities=caps, handle_results=[],
                             negative_controls=[], injections=[], meta={})
    assert ledger.table_digest(db) != pre
    drill = ledger.rollback_drill(db, info["backup"], pre)
    assert drill["verified"] and drill["restored_digest"] == pre


def test_sql_queries_expose_self_reference_and_evidence_lines(report):
    db = ROOT / runner.LEDGER_REL
    share = ledger.q_self_reference_share(db)[0]
    assert share["n_real"] == report["independence"]["real_after"]
    assert share["self_reference_share"] == 0.0
    rows = ledger.q_self_reference(db)
    assert all(r["regraded_grade"] == "REAL" for r in rows)
    lines = ledger.q_evidence_lines(db)
    assert lines and all(r["line"] >= 1 for r in lines)
    downgrades = ledger.q_downgrades(db)
    assert downgrades and all(r["evidence_line"] for r in downgrades)
    assert all(len(r["evidence_line_sha256"]) == 16 for r in downgrades)


def test_sql_queries_match_report_numbers(report):
    sql = report["ledger"]["sql"]
    db = ROOT / runner.LEDGER_REL
    assert sql["injection_total"][0]["detected"] == sql["injection_total"][0]["n"]
    assert ledger.q_injection_rates(db) == sql["injection_rates"]
    matrix = ledger.q_detection_matrix(db)
    caught = {(r["kind"], r["injection"]): r["n_caught"] for r in matrix}
    assert caught[("equivalence", "scale_1p05")] == 3
    assert caught[("equivalence", "sign_flip")] == 3
    assert caught[("equivalence", "offset")] == 3


# ---------------------------------------------------------------------------
# (f) domain neutrality
# ---------------------------------------------------------------------------

def test_mechanism_layer_has_no_domain_vocabulary():
    from tools import evidence_domain_scan as scan
    result = scan.scan_paths(["tools/evidence_second_impl"])
    assert (result.get("hits") or []) == []
    assert result.get("scanned_files", 0) >= 8


def test_handle_ids_are_synthetic(population):
    assert population and all(s.handle_id.startswith("synth.") for s in population)
    assert sum(1 for s in population if s.handle_class == "control") == 20
    assert sum(1 for s in population if s.handle_class == "target") == 3


def test_cli_style_run_is_reproducible(tmp_path):
    first = runner.run(ROOT, tmp_path / "a")
    second = runner.run(ROOT, tmp_path / "b")
    keys = ("injection_rate", "false_alarm_count")
    a, b = first["bench"]["summary"], second["bench"]["summary"]
    for k in keys:
        assert a[k] == b[k]
    assert first["acceptance"]["all_pass"] == second["acceptance"]["all_pass"] is True
    assert first["independence"]["downgraded"] == second["independence"]["downgraded"]


# ---------------------------------------------------------------------------
# artefacts / figures
# ---------------------------------------------------------------------------

def test_figures_are_generated(tmp_path, report):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    out = figures.build_all(path, tmp_path / "figs")
    for key, p in out.items():
        assert Path(p).is_file() and Path(p).stat().st_size > 5000, key


def test_run_report_artifacts_exist(report):
    out = ROOT / runner.OUT_DIR_REL
    for name in ("run_report.json", "acceptance.json", "bench_manifest.json",
                 "capability_declarations.json", "evidence_second_impl.sqlite",
                 "declaration_sources.json"):
        assert (out / name).is_file(), name
    acc = json.loads((out / "acceptance.json").read_text(encoding="utf-8"))
    assert acc["all_pass"] is True
    assert acc["n_checks"] == acc["n_pass"] == 6