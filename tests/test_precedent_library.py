"""IN24-E3 precedent library acceptance tests.

Criteria (machine-checkable, fixed before implementation):
1. **Size & shape** — >= 8 cases, ids unique, >= 1 anti-case, S4 track acknowledged
   (vacant allowed but must explain the gap).
2. **Grade bars are code** — S1 cannot carry a comparison baseline, S2 must carry
   before/after/metric/delta with numbers, S3 must ship inspectable artifacts,
   S4 needs independent review or an explicit vacant status, anti-cases must cite
   the observed failure.
3. **Injection channel** — every line <= 120 chars, unique, gate checklist first;
   injection is append-only and idempotent (re-run inserts nothing, prior bytes are
   preserved in order), and a missing anchor is a hard error (never silent append).
4. **Goal-quality comparison** — real historical goals (quoted as data) score no
   worse after the injection block; the channel shows a positive mean uplift; the
   band taxonomy matches the documented thresholds.
5. **Zero domain semantics in code** — the module sources carry none of the domain
   terms that live (as data) in the repo's display vocabulary file.
6. **Evidence resolves** — every repo-relative evidence ref exists on disk, so a case
   cannot cite a fabricated artifact path.

Fixtures that need a foreign vocabulary use data files, never literals in code.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from tools.precedent_library import goal_quality as gq
from tools.precedent_library import library as lib

REPO_ROOT = Path(__file__).resolve().parents[1]
DOMAIN_VOCAB_PATH = REPO_ROOT / "tools" / "chronicle" / "report" / "domain_display.json"


@pytest.fixture(scope="module")
def store():
    return lib.load_cases()


@pytest.fixture(scope="module")
def cases(store):
    return store[1]


# ─────────────────────── 1. size & shape ───────────────────────

def test_library_has_minimum_cases_and_anti_case(cases):
    assert len(cases) >= 8
    assert lib.validate_cases(cases) == []
    assert any(c.grade == "anti" for c in cases)
    assert len({c.id for c in cases}) == len(cases)


def test_positive_grades_cover_s1_s2_s3_and_s4_track(cases):
    grades = {c.grade for c in cases}
    assert {"S1", "S2", "S3", "S4"} <= grades
    s4 = [c for c in cases if c.grade == "S4"]
    for case in s4:
        # Either independently reviewed, or honestly declared vacant with a reason.
        assert case.status == "vacant" or any(
            "independent" in json.dumps(dict(e), ensure_ascii=False).lower()
            for e in case.evidence
        )
        if case.status == "vacant":
            assert len(case.problem.strip()) >= 20


def test_summary_is_layered_only(cases):
    summary = lib.summarize(cases)
    assert summary["weighted_score"] is None and summary["ranking"] is None
    assert summary["total"] == len(cases)
    assert summary["anti"] >= 1
    assert summary["positive"] == summary["total"] - summary["anti"]


# ─────────────────────── 2. grade bars ───────────────────────

def test_schema_rejects_case_without_evidence(cases):
    base = next(c for c in cases if c.grade == "S1")
    broken = replace(base, evidence=())
    assert any("evidence must not be empty" in e for e in lib.validate_case(broken))


def test_s2_without_baseline_is_rejected(cases):
    s2 = next(c for c in cases if c.grade == "S2")
    broken = replace(s2, baseline=None)
    assert any("S2 requires a baseline" in e for e in lib.validate_case(broken))
    # ... and a baseline missing the delta cannot pass either.
    partial = replace(s2, baseline={"before": "a", "after": "b", "metric": "m"})
    assert any("missing keys" in e for e in lib.validate_case(partial))


def test_s1_cannot_claim_improvement_baseline(cases):
    s1 = next(c for c in cases if c.grade == "S1")
    upgraded = replace(s1, baseline={"before": "a", "after": "b", "metric": "m", "delta": "-1%"})
    assert any("S1 must not claim a comparison baseline" in e
               for e in lib.validate_case(upgraded))


def test_s3_needs_inspectable_artifacts(cases):
    s3 = next(c for c in cases if c.grade == "S3")
    only_api = replace(s3, evidence=({"kind": "api", "ref": "tensor/missions/1"},))
    errors = lib.validate_case(only_api)
    assert any("S3 needs artifact/run/commit evidence" in e for e in errors)
    assert any("S3 needs >= 2 independent evidence refs" in e for e in errors)


def test_s4_without_independent_review_must_be_vacant(cases):
    s4 = next(c for c in cases if c.grade == "S4")
    promoted = replace(s4, status="active",
                       evidence=({"kind": "run", "ref": "python3 demo.py"},))
    assert any("S4 needs independent review evidence" in e
               for e in lib.validate_case(promoted))


def test_anti_case_must_cite_failure(cases):
    anti = next(c for c in cases if c.grade == "anti")
    assert anti.missions or anti.evidence
    stripped = replace(anti, evidence=(), missions=())
    assert any("anti-case needs" in e for e in lib.validate_case(stripped))


def test_why_grade_must_quote_numbers(cases):
    for case in cases:
        assert re.search(r"\d", case.why_grade), case.id
        assert re.search(r"\d", case.delta), case.id


# ─────────────────────── 3. injection channel ───────────────────────

def test_injection_lines_within_budget_and_unique(store):
    meta, cases = store
    lines = lib.render_playbook_block(cases, gate_line=meta.get("injection_gate_line"))
    assert lines, "injection block must not be empty"
    assert len(set(lines)) == len(lines)
    for line in lines:
        assert line.startswith("- ")
        assert len(line) <= lib.MAX_LINE_CHARS, (len(line), line)
    # Gate checklist is first so a reader hits the 8 properties before case detail.
    assert lines[0].startswith("- E3-GATE")


def test_injection_is_append_only_and_idempotent(store, tmp_path):
    meta, cases = store
    lines = lib.render_playbook_block(cases, gate_line=meta.get("injection_gate_line"))
    bootstrap = tmp_path / "BOOTSTRAP.md"
    prior = (
        "# head\n\n## PLAYBOOK（append-only）\n"
        "- 历史条目甲：既有内容必须原样保留。\n"
        "- 历史条目乙：不得被改写或重排。\n\n"
        "## 逃生规则\n- 尾部条目。\n"
    )
    bootstrap.write_text(prior, encoding="utf-8")

    dry = lib.inject_playbook(bootstrap, lines)
    assert dry["applied"] is False and bootstrap.read_text(encoding="utf-8") == prior

    first = lib.inject_playbook(bootstrap, lines, apply=True)
    assert first["inserted_count"] == len(lines)
    text1 = bootstrap.read_text(encoding="utf-8")
    for old in ("历史条目甲", "历史条目乙", "尾部条目"):
        assert old in text1
    assert text1.index("历史条目甲") < text1.index(lines[0]) < text1.index("## 逃生规则")

    second = lib.inject_playbook(bootstrap, lines, apply=True)
    assert second["inserted_count"] == 0 and second["skipped_count"] == len(lines)
    assert bootstrap.read_text(encoding="utf-8") == text1


def test_injection_refuses_missing_anchor(store, tmp_path):
    meta, cases = store
    lines = lib.render_playbook_block(cases, gate_line=meta.get("injection_gate_line"))
    target = tmp_path / "no_anchor.md"
    target.write_text("# only a head\n", encoding="utf-8")
    with pytest.raises(lib.PrecedentError):
        lib.inject_playbook(target, lines, apply=True)
    assert target.read_text(encoding="utf-8") == "# only a head\n"


# ─────────────────────── 4. goal-quality comparison ───────────────────────

def test_goal_quality_uplift_on_real_samples(store):
    meta, cases = store
    lines = lib.render_playbook_block(cases, gate_line=meta.get("injection_gate_line"))
    samples = gq.load_samples()
    assert len(samples) >= 3
    comparison = gq.build_comparison(samples, lines)
    rows = comparison["rows"]
    assert all(r["after"]["score"] >= r["before"]["score"] for r in rows)
    assert comparison["summary"]["mean_delta"] > 0
    improved = [r for r in rows if r["delta"] > 0]
    assert len(improved) * 2 >= len(rows), "injection must move at least half the samples"
    for row in improved:
        assert row["recovered_checks"], row["sample_id"]


def test_scorer_bands_follow_documented_thresholds():
    weak = gq.score_goal("做事")
    assert weak["band"] == "weak" and weak["score"] < 0.625
    good = gq.score_goal(
        "基线对照 5%，误差<3%；pytest tests/x.py；工件 artifacts/x.json；"
        "honest FAIL 不可覆盖；worktree 分支；报告；全新前沿"
    )
    assert good["band"] == "excellent" and good["passed"] >= 7


def test_scorer_is_monotone_under_injection(store):
    meta, cases = store
    lines = lib.render_playbook_block(cases, gate_line=meta.get("injection_gate_line"))
    sample = gq.load_samples()[0]["goal"]
    assert gq.score_goal(sample + gq.injection_suffix(lines))["score"] >= gq.score_goal(sample)["score"]
    assert "开工前必读" in gq.injection_suffix(lines)


# ─────────────────────── 5. zero domain semantics in code ───────────────────────

def test_module_sources_carry_no_domain_vocabulary():
    terms = list(json.loads(DOMAIN_VOCAB_PATH.read_text(encoding="utf-8")))
    assert len(terms) >= 10
    sources = sorted(Path(lib.__file__).parent.glob("*.py"))
    assert sources
    leaks = {}
    for path in sources:
        text = path.read_text(encoding="utf-8")
        hits = [t for t in terms if t in text]
        if hits:
            leaks[path.name] = hits
    assert leaks == {}, f"domain vocabulary must be injected as data, not code: {leaks}"


# ─────────────────────── 6. evidence resolves ───────────────────────

def test_repo_relative_evidence_refs_exist(cases):
    prefixes = ("artifacts/", "docs/", "tests/", "tools/", "src/", "config/", "scripts/")
    checked = 0
    for case in cases:
        for item in case.evidence:
            ref = str(item["ref"])
            if item.get("external"):
                continue  # delivered on another mission branch; anchored via mission checks
            if ref.startswith(prefixes):
                assert (REPO_ROOT / ref).exists(), f"{case.id}: missing artifact {ref}"
                checked += 1
    assert checked >= 8, "expected several repo-local evidence refs to be verified"


def test_domain_display_fallback_is_identity(tmp_path, monkeypatch):
    """S2 case property: a missing vocabulary file degrades to identity, never crash."""
    from tools.chronicle.report import figures

    missing = tmp_path / "absent.json"
    monkeypatch.setattr(figures.Path, "with_name", lambda self, name: missing)
    assert figures._load_domain_display() == {}

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(figures.Path, "with_name", lambda self, name: corrupt)
    assert figures._load_domain_display() == {}