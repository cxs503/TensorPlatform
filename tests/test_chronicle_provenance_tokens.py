#!/usr/bin/env python3
"""Tests for tools/chronicle/provenance/tokens.py (W18-N10).

Covers:
* every basic numeric form (integer/decimal/signed/thousands/percent/
  permille/scientific e&E/unit);
* the eight composite forms (table/figure_caption/formula/error_bar/
  range/ratio/percent/scientific) against an independent hand-written
  expectation list (the oracle is not the extractor);
* extraction totality (100% of manuscript digits covered);
* the five-tuple contract (line, col, raw, value, context);
* the machine-auditable exemption mechanism (hash-anchored rules, tamper
  rejection, rate gate);
* the binding gate (bound / dangling / fabricated / unbound / rate).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.chronicle.provenance.tokens import (  # noqa: E402
    EIGHT_FORMS,
    check_coverage,
    extract_numeric_tokens,
    extraction_coverage,
    load_exemptions,
    rule_hash,
)

CORPUS_PATH = REPO_ROOT / "runs" / "m5298" / "corpus_8forms.md"
EXEMPTIONS_PATH = REPO_ROOT / "tools" / "chronicle" / "provenance" / "exemptions.yaml"

CORPUS = CORPUS_PATH.read_text(encoding="utf-8")

# Independent oracle: (line, raw, value, value2, kind, context)
EXPECTED = [
    (4, "42", 42.0, None, "integer", "prose"),
    (4, "3.14", 3.14, None, "decimal", "prose"),
    (4, "-7", -7.0, None, "signed", "prose"),
    (4, "+8", 8.0, None, "signed", "prose"),
    (5, "1,234,567.89", 1234567.89, None, "thousands", "prose"),
    (5, "12,345", 12345.0, None, "thousands", "prose"),
    (6, "85.7%", 85.7, None, "percent", "prose"),
    (6, "3.5‰", 3.5, None, "permille", "prose"),
    (7, "1.23e-4", 1.23e-4, None, "scientific", "prose"),
    (7, "6.02E+23", 6.02e23, None, "scientific", "prose"),
    (7, "2e3", 2000.0, None, "scientific", "prose"),
    (8, "25m/s", 25.0, None, "unit", "prose"),
    (8, "100MPa", 100.0, None, "unit", "prose"),
    (11, "10-15", 10.0, 15.0, "range", "prose"),
    (11, "20–25", 20.0, 25.0, "range", "prose"),
    (11, "30—40", 30.0, 40.0, "range", "prose"),
    (12, "3:2", 1.5, 2.0, "ratio", "prose"),
    (12, "1:4", 0.25, 4.0, "ratio", "prose"),
    (15, "0.0123 ± 0.0004", 0.0123, 0.0004, "error_bar", "prose"),
    (15, "14.2 ± 0.1", 14.2, 0.1, "error_bar", "prose"),
    (20, "0.65", 0.65, None, "decimal", "table_cell"),
    (20, "14.2", 14.2, None, "decimal", "table_cell"),
    (21, "0.68", 0.68, None, "decimal", "table_cell"),
    (21, "13.8", 13.8, None, "decimal", "table_cell"),
    (24, "0.55–0.72", 0.55, 0.72, "range", "figure_caption"),
    (27, "0.5", 0.5, None, "decimal", "formula"),
    (27, "2", 2.0, None, "integer", "formula"),
    (27, "14.2", 14.2, None, "decimal", "formula"),
]


def _tokens():
    return extract_numeric_tokens(CORPUS)


# ---------------------------------------------------------------------------
# extraction: basic forms
# ---------------------------------------------------------------------------
def test_basic_forms_values():
    toks = {t.raw: t for t in _tokens()}
    assert toks["42"].value == 42.0 and toks["42"].kind == "integer"
    assert toks["3.14"].value == pytest.approx(3.14) and toks["3.14"].kind == "decimal"
    assert toks["-7"].value == -7.0 and toks["-7"].kind == "signed"
    assert toks["+8"].value == 8.0 and toks["+8"].kind == "signed"
    assert toks["1,234,567.89"].value == pytest.approx(1234567.89)
    assert toks["1,234,567.89"].kind == "thousands"
    assert toks["85.7%"].value == pytest.approx(85.7) and toks["85.7%"].unit == "%"
    assert toks["3.5‰"].value == pytest.approx(3.5) and toks["3.5‰"].unit == "‰"
    assert toks["1.23e-4"].value == pytest.approx(1.23e-4)
    assert toks["6.02E+23"].value == pytest.approx(6.02e23)
    assert toks["2e3"].value == pytest.approx(2000.0)
    assert toks["25m/s"].value == 25.0 and toks["25m/s"].unit == "m/s"
    assert toks["100MPa"].value == 100.0 and toks["100MPa"].unit == "MPa"


def test_scientific_notation_lowercase_e():
    toks = {t.raw: t for t in extract_numeric_tokens("x = 3.5e2 与 1E-2。")}
    assert toks["3.5e2"].value == pytest.approx(350.0)
    assert toks["1E-2"].value == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# extraction: eight composite forms
# ---------------------------------------------------------------------------
def test_eight_forms_all_present():
    toks = _tokens()
    by_form = {}
    for form in EIGHT_FORMS:
        if form == "table":
            by_form[form] = sum(1 for t in toks if t.context == "table_cell")
        elif form in ("figure_caption", "formula"):
            by_form[form] = sum(1 for t in toks if t.context == form)
        else:
            by_form[form] = sum(1 for t in toks if t.kind == form)
    assert all(n >= 1 for n in by_form.values()), by_form
    assert by_form == {"table": 4, "figure_caption": 1, "formula": 3,
                      "error_bar": 2, "range": 4, "ratio": 2,
                      "percent": 1, "scientific": 3}


def test_range_dashes_all_three():
    toks = {t.raw: t for t in _tokens()}
    assert (toks["10-15"].value, toks["10-15"].value2) == (10.0, 15.0)
    assert (toks["20–25"].value, toks["20–25"].value2) == (20.0, 25.0)  # en dash
    assert (toks["30—40"].value, toks["30—40"].value2) == (30.0, 40.0)  # em dash


def test_ratio_and_error_bar():
    toks = {t.raw: t for t in _tokens()}
    assert toks["3:2"].value == pytest.approx(1.5) and toks["3:2"].value2 == 2.0
    assert toks["1:4"].value == pytest.approx(0.25)
    eb = toks["0.0123 ± 0.0004"]
    assert (eb.value, eb.value2) == (pytest.approx(0.0123), pytest.approx(0.0004))


def test_spaced_hyphen_is_not_a_range():
    # declared scope: "a - b" with spaces stays two separate numbers
    toks = extract_numeric_tokens("差值 10 - 15 与 10-15。")
    raws = [t.raw for t in toks]
    assert "10-15" in raws
    assert "10" in raws and "15" in raws
    assert "-15" not in raws


def test_five_tuple_contract():
    for t in _tokens():
        line, col, raw, value, context = t.five_tuple()
        assert isinstance(line, int) and line >= 1
        assert isinstance(col, int) and col >= 0
        assert isinstance(raw, str) and raw
        assert value is None or isinstance(value, float)
        assert context in ("prose", "table_cell", "figure_caption", "formula",
                           "code", "heading")


def test_identifier_digits_not_extracted():
    toks = extract_numeric_tokens("任务 m5298 与哈希 sha256:ab12cd 及 n-007。")
    assert toks == []  # all digits are identifier tails


def test_prov_token_digits_masked():
    text = "覆盖率 85.7% {{prov:n-007}} 结束"
    toks = extract_numeric_tokens(text)
    assert [t.raw for t in toks] == ["85.7%"]  # 007 inside prov token is masked


def test_html_comment_digits_masked():
    text = "数值 42 <!-- 内部 999 --> 结束"
    toks = extract_numeric_tokens(text)
    assert [t.raw for t in toks] == ["42"]


# ---------------------------------------------------------------------------
# extraction totality
# ---------------------------------------------------------------------------
def test_extraction_coverage_100_percent_on_corpus():
    cov, total, covered = extraction_coverage(CORPUS)
    assert total > 0
    assert covered == total
    assert cov == 1.0


def test_extraction_coverage_catches_unextracted_digit():
    # a digit the master regex cannot start (identifier tail) is excluded,
    # but a plain digit in prose is always extractable -> coverage stays 1.0
    cov, total, covered = extraction_coverage("裸数字 7 在文中。")
    assert cov == 1.0 and total == 1 and covered == 1


# ---------------------------------------------------------------------------
# corpus oracle: every token matches the hand-written expectation
# ---------------------------------------------------------------------------
def test_corpus_tokens_match_oracle():
    toks = _tokens()
    assert len(toks) == len(EXPECTED), (
        f"got {len(toks)} tokens: " + ", ".join(t.raw for t in toks))
    for tok, (line, raw, value, value2, kind, context) in zip(toks, EXPECTED):
        assert tok.line == line, f"line mismatch for {raw!r}: {tok.line} != {line}"
        assert tok.raw == raw
        if value is None:
            assert tok.value is None
        else:
            assert tok.value == pytest.approx(value), f"{raw!r}: {tok.value} != {value}"
        if value2 is not None:
            assert tok.value2 == pytest.approx(value2)
        assert tok.kind == kind, f"{raw!r}: {tok.kind} != {kind}"
        assert tok.context == context, f"{raw!r}: {tok.context} != {context}"


def test_corpus_per_form_accuracy_100_percent():
    toks = _tokens()
    n = len(EXPECTED)
    ok = sum(
        1 for tok, (line, raw, value, value2, kind, context) in zip(toks, EXPECTED)
        if tok.line == line and tok.raw == raw and tok.kind == kind
        and tok.context == context
        and (value is None or tok.value == pytest.approx(value))
    )
    assert ok / n == 1.0


# ---------------------------------------------------------------------------
# exemptions: machine-auditable
# ---------------------------------------------------------------------------
def test_exemptions_yaml_loads_and_hashes_verify():
    rules, findings = load_exemptions(EXEMPTIONS_PATH)
    assert findings == []
    assert all(r.valid for r in rules)
    assert {r.rule_id for r in rules} == {"EX-001", "EX-002", "EX-003", "EX-004"}
    for r in rules:
        assert r.hash == rule_hash(r.rule_id, r.exact_regex, r.reason)


def test_tampered_rule_rejected(tmp_path):
    rules, _ = load_exemptions(EXEMPTIONS_PATH)
    bad = {
        "schema": "chronicle-provenance-exemptions/1",
        "rules": [{
            "rule_id": "EX-001",
            "exact_regex": "^(?:表|图)\\s*\\d+$",
            "reason": "structural cross-reference label (table/figure/equation/section), not a measured value",
            "hash": "sha256:" + "0" * 64,  # tampered
        }],
    }
    p = tmp_path / "ex.yaml"
    p.write_text(json.dumps(bad), encoding="utf-8")
    rules2, findings2 = load_exemptions(p)
    assert not rules2[0].valid
    assert any(f["code"] == "EXEMPTION_HASH_MISMATCH" for f in findings2)
    # a rejected rule must never exempt anything
    assert not rules2[0].matches("表 1")


def test_label_exemption_never_fires_on_bare_integer():
    rules, _ = load_exemptions(EXEMPTIONS_PATH)
    label_rule = next(r for r in rules if r.rule_id == "EX-001")
    assert label_rule.matches("表 1")
    assert label_rule.matches("图 2–3")
    assert not label_rule.matches("1")  # bare integer is never exempted


def test_version_dotted_identifier():
    toks = {t.raw: t for t in extract_numeric_tokens("环境 Python 3.11.6 与 2.4.6。")}
    assert toks["3.11.6"].kind == "version" and toks["3.11.6"].value is None
    assert toks["2.4.6"].kind == "version"


def test_inline_code_digits_out_of_scope():
    text = "哈希 `374f9f572` 与路径 `runs/m5298/`。"
    cov, total, covered = extraction_coverage(text)
    assert cov == 1.0
    toks = extract_numeric_tokens(text)
    assert all(t.context == "code" for t in toks)


def test_hex_hash_in_prose_leads_to_token():
    # a bare hex id in prose: the leading digit run is a token (must be
    # bound or exempted); the letter-adjacent tails are identifier tails
    toks = extract_numeric_tokens("裸哈希 374f9f572 结束。")
    assert [t.raw for t in toks] == ["374"]


# ---------------------------------------------------------------------------
# coverage gate
# ---------------------------------------------------------------------------
def _records(pairs):
    return {pid: {"pid": pid, "kind": "number", "value": v, "decimals": d}
            for pid, v, d in pairs}


def test_bound_number_passes():
    text = "覆盖率 85.7% {{prov:n-001}} 达标。"
    rep = check_coverage(text, _records([("n-001", 85.7, 1)]), [])
    assert rep.status == "PASS"
    assert rep.bound == 1 and rep.unbound == 0
    assert rep.extraction_coverage == 1.0


def test_unbound_number_fails():
    rep = check_coverage("覆盖率 85.7% 达标。", {}, [])
    assert rep.status == "FAIL"
    assert rep.unbound == 1
    assert any(f["code"] == "UNBOUND_NUMBER" for f in rep.findings)


def test_fabricated_number_fails():
    text = "覆盖率 99.9% {{prov:n-001}} 达标。"
    rep = check_coverage(text, _records([("n-001", 85.7, 1)]), [])
    assert rep.status == "FAIL"
    assert any(f["code"] == "FABRICATED_NUMBER" for f in rep.findings)


def test_dangling_token_fails():
    text = "覆盖率 85.7% {{prov:n-999}} 达标。"
    rep = check_coverage(text, {}, [])
    assert rep.status == "FAIL"
    assert any(f["code"] == "DANGLING_TOKEN" for f in rep.findings)


def test_rounding_budget_allows_last_digit_rounding():
    # printed 85.7 vs recorded 85.71 with decimals=1 -> within half a unit
    text = "覆盖率 85.7% {{prov:n-001}} 达标。"
    rep = check_coverage(text, _records([("n-001", 85.71, 1)]), [])
    assert rep.status == "PASS"


def test_exemption_rate_gate():
    # 1 bound + 1 exempted label + 1 unbound -> unbound fails anyway;
    # rate check: 2 exempted of 2 tokens = 100% >= 5%
    text = "见 表 1 与 图 2。"
    rules, _ = load_exemptions(EXEMPTIONS_PATH)
    rep = check_coverage(text, {}, rules)
    assert rep.exempted == 2 and rep.exemption_rate == 1.0
    assert any(f["code"] == "EXEMPTION_RATE_HIGH" for f in rep.findings)


def test_exemption_rate_below_threshold_passes():
    # 1 exempted label among 20 bound numbers -> 1/21 < 5%
    nums = " ".join(f"{i}.0 {{{{prov:n-{i:03d}}}}}" for i in range(1, 21))
    text = f"见 表 1。数据：{nums}。"
    recs = _records([(f"n-{i:03d}", float(i), 1) for i in range(1, 21)])
    rules, _ = load_exemptions(EXEMPTIONS_PATH)
    rep = check_coverage(text, recs, rules)
    assert rep.status == "PASS"
    assert rep.exempted == 1 and rep.bound == 20
    assert rep.exemption_rate < 0.05


def test_date_exempted():
    # 1 exempted date among 21 tokens -> 1/21 < 5%
    nums = " ".join(f"{i}.0 {{{{prov:n-{i:03d}}}}}" for i in range(1, 21))
    text = f"运行于 2026-11-07。数据：{nums}。"
    recs = _records([(f"n-{i:03d}", float(i), 1) for i in range(1, 21)])
    rules, _ = load_exemptions(EXEMPTIONS_PATH)
    rep = check_coverage(text, recs, rules)
    assert rep.status == "PASS"
    assert rep.exempted == 1
    assert rep.unbound == 0


def test_empty_text_passes():
    rep = check_coverage("", {}, [])
    assert rep.status == "PASS"
    assert rep.extraction_coverage == 1.0
