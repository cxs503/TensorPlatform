#!/usr/bin/env python3
"""Tests for the number-level provenance gate (``tools/prov_gate``, P251-W2-N04).

The oracle is *independent of the implementation*: every fixture is a small
synthetic report (table ``sample`` with rows ``alpha``/``beta``) whose expected
numbers are written out by hand in the assertions, so a change in the gate
cannot silently move the expectation.

Three negative-control classes are exercised, and the suite ends with an
aggregate test that measures the **interception rate** of all three:

* NC-1 placeholder value rewritten by hand (in the body, or in the sidecar
  after rendering);
* NC-2 sidecar missing the key the template references;
* NC-3 recorded ``query_hash`` no longer equal to the recomputed fingerprint
  (query re-pointed) or the re-executed query returning a different value.

Zero domain semantics: no measurement name, unit, equipment or physics vocabulary
appears in this file (see ``config/domain_word_denylist.json``).
"""
from __future__ import annotations

import copy
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.prov_gate import (  # noqa: E402
    GateConfig,
    MissingKeyError,
    Sidecar,
    canonical_query,
    gate_report,
    query_hash,
    render,
    sha256_file,
    sha256_text,
    validate_sidecar,
)
from tools.prov_gate.gate import main as gate_main  # noqa: E402
from tools.prov_gate.render import main as render_main  # noqa: E402
from tools.prov_gate.sidecar import ArtifactRecord, NumberRecord  # noqa: E402

SQL_ALPHA = "SELECT v FROM sample WHERE id = 'alpha'"
SQL_BETA = "SELECT v FROM sample WHERE id = 'beta'"
TEMPLATE = (
    "# Synthetic report\n"
    "\n"
    "## 2.1 Headline\n"
    "\n"
    "Coverage reached {{prov:cov}} % of rows, with residual {{prov:res}} m.\n"
    "\n"
    "| metric | value |\n"
    "|---|---|\n"
    "| coverage | {{prov:cov}} |\n"
    "| residual | {{prov:res}} |\n"
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def _db(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sample (id TEXT PRIMARY KEY, v REAL NOT NULL)")
    conn.executemany("INSERT INTO sample VALUES (?, ?)", [("alpha", 85.71), ("beta", 0.4123)])
    conn.commit()
    conn.close()
    return path


def _sidecar(tmp_path: Path) -> Sidecar:
    sc = Sidecar()
    sc.add_number("cov", 85.71, SQL_ALPHA, [], decimals=1, label="row coverage")
    sc.add_number("res", 0.4123, SQL_BETA, [], decimals=3, label="residual")
    art = tmp_path / "figs" / "curve.png"
    art.parent.mkdir(parents=True, exist_ok=True)
    art.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic-bytes")
    sc.add_artifact("fig-curve", "figs/curve.png", artifact_root=tmp_path)
    return sc


def _rendered(tmp_path: Path) -> dict:
    sc = _sidecar(tmp_path)
    res = render(TEMPLATE, sc)
    return {"sidecar": sc, "rendered": res.markdown, "manifest": res.to_manifest(),
            "template": TEMPLATE, "db": _db(tmp_path), "root": tmp_path}


def _codes(result: dict) -> list[str]:
    return [f["code"] for f in result["findings"]]


def _rekey(sc: Sidecar, mutate) -> Sidecar:
    recs = [copy.deepcopy(r) for r in sc.records.values()]
    mutate(recs)
    return Sidecar(recs)


# ---------------------------------------------------------------------------
# 1-4: sidecar contract
# ---------------------------------------------------------------------------
def test_query_hash_format_insensitive_but_identity_sensitive() -> None:
    a = query_hash("SELECT v FROM sample WHERE id='alpha'", [])
    b = query_hash("  SELECT v\n  FROM sample\n  WHERE id='alpha'  ", [])
    c = query_hash("SELECT v FROM sample WHERE id='beta'", [])
    d = query_hash("SELECT v FROM sample WHERE id=?", ["alpha"])
    assert a == b, "formatting must not change the fingerprint"
    assert a != c
    assert a != d, "parameters are part of the query identity"
    assert a.startswith("sha256:") and len(a) == len("sha256:") + 64
    assert canonical_query("SELECT 1;", []) == '{"params":[],"sql":"SELECT 1"}'


def test_sidecar_roundtrip_and_validation(tmp_path: Path) -> None:
    sc = _sidecar(tmp_path)
    path = sc.save(tmp_path / "sidecar.json")
    back = Sidecar.load(path)
    assert len(back) == len(sc) == 3
    assert validate_sidecar(back) == []
    rec = back.get("cov")
    assert isinstance(rec, NumberRecord) and rec.value == pytest.approx(85.71)
    assert rec.computed_at.endswith("Z")
    assert isinstance(back.get("fig-curve"), ArtifactRecord)


def test_sidecar_validation_rejects_repointed_query_and_bad_artifact(tmp_path: Path) -> None:
    sc = _sidecar(tmp_path)
    rec = sc.get("cov")
    rec.source["params"] = ["beta"]  # query re-pointed after fingerprinting
    art = sc.get("fig-curve")
    art.artifact_sha256 = "not-a-digest"
    codes = [f["code"] for f in validate_sidecar(sc)]
    assert "QUERY_HASH_MISMATCH" in codes
    assert "SIDECAR_SCHEMA_INVALID" in codes


# ---------------------------------------------------------------------------
# 5-7: render contract
# ---------------------------------------------------------------------------
def test_render_substitutes_values_from_sidecar_only(tmp_path: Path) -> None:
    sc = _sidecar(tmp_path)
    res = render(TEMPLATE, sc)
    assert "{{prov:" not in res.markdown
    assert "85.7" in res.markdown and "0.412" in res.markdown
    assert res.rendered_sha256 == sha256_text(res.markdown)
    # every span the manifest records really holds the recorded text
    for e in res.entries:
        assert res.markdown[e.start:e.end] == e.text
    assert [e.key for e in res.entries].count("cov") == 2
    assert res.missing == []


def test_render_strict_refuses_missing_key_but_never_blanks(tmp_path: Path) -> None:
    sc = _sidecar(tmp_path)
    del sc.records["res"]
    with pytest.raises(MissingKeyError) as exc:
        render(TEMPLATE, sc)
    assert exc.value.missing == ["res"]
    lenient = render(TEMPLATE, sc, strict=False)
    assert lenient.missing == ["res"]
    assert "{{prov:res}}" in lenient.markdown, "a missing value must never render as empty"


def test_render_artifact_renders_digest(tmp_path: Path) -> None:
    sc = _sidecar(tmp_path)
    digest = sha256_file(tmp_path / "figs" / "curve.png")
    res = render("figure digest {{prov:fig-curve}}", sc)
    assert res.markdown == f"figure digest {digest}"
    assert len(digest) == 64


# ---------------------------------------------------------------------------
# 8-14: gate layers
# ---------------------------------------------------------------------------
def test_gate_clean_report_passes_with_zero_bare_ratio(tmp_path: Path) -> None:
    f = _rendered(tmp_path)
    res = gate_report(f["rendered"], f["sidecar"], template=f["template"],
                      manifest=f["manifest"], db_path=f["db"], artifact_root=f["root"],
                      config=GateConfig(require_manifest=True))
    assert res["status"] == "PASS", _codes(res)
    assert res["disposition"] == "pass"
    assert res["bare_number_ratio"] == 0.0
    assert res["binding_mode"] == "manifest"
    assert res["counts"]["in_scope"] == 4  # 2 prose + 2 table cells
    assert res["counts"]["queries_reexecuted"] == 2
    assert res["excluded_by_reason"].get("section_number") == 1
    assert res["extraction"]["coverage"] == 1.0


def test_gate_rejects_handwritten_number_outside_placeholders(tmp_path: Path) -> None:
    f = _rendered(tmp_path)
    body = f["rendered"] + "\nAn extra claim of 12.34 appears here.\n"
    res = gate_report(body, f["sidecar"], config=GateConfig())
    assert res["status"] == "FAIL"
    assert "BARE_NUMBER_RATIO_EXCEEDED" in _codes(res)
    assert res["bare_number_ratio"] > 0.0
    assert any(t["raw"] == "12.34" for t in res["bare_tokens"])


def test_nc1_rewritten_rendered_value_is_intercepted(tmp_path: Path) -> None:
    f = _rendered(tmp_path)
    tampered = f["rendered"].replace("85.7", "95.7")
    res = gate_report(tampered, f["sidecar"], template=f["template"], manifest=f["manifest"])
    codes = _codes(res)
    assert res["status"] == "FAIL"
    assert "RENDER_DRIFT" in codes, "canonical render mismatch must be caught"
    assert "DOC_SHA_MISMATCH" in codes, "manifest digest mismatch must be caught"
    assert res["bare_number_ratio"] > 0.0


def test_nc1_sidecar_value_edited_after_render_is_intercepted(tmp_path: Path) -> None:
    f = _rendered(tmp_path)

    def bump(recs):
        for r in recs:
            if getattr(r, "key", None) == "cov":
                r.value = 95.71

    sc_edit = _rekey(f["sidecar"], bump)
    res = gate_report(f["rendered"], sc_edit, template=f["template"], manifest=f["manifest"])
    codes = _codes(res)
    assert res["status"] == "FAIL"
    assert "SIDECAR_VALUE_DRIFT" in codes
    assert "RENDER_DRIFT" in codes


def test_nc2_missing_sidecar_key_is_intercepted(tmp_path: Path) -> None:
    f = _rendered(tmp_path)

    def drop(recs):
        recs[:] = [r for r in recs if getattr(r, "key", None) != "res"]

    sc_missing = _rekey(f["sidecar"], drop)
    res = gate_report(f["rendered"], sc_missing, template=f["template"], manifest=f["manifest"])
    codes = _codes(res)
    assert res["status"] == "FAIL"
    assert "MISSING_KEY" in codes
    assert any("res" == f.get("key") for f in res["findings"] if f["code"] == "MISSING_KEY") or \
        "res" in json.dumps([f for f in res["findings"] if f["code"] == "MISSING_KEY"])
    # an unrendered placeholder in the body is equally fatal
    res2 = gate_report("{{prov:cov}} 85.7 0.412", sc_missing)
    assert "UNRENDERED_PLACEHOLDER" in _codes(res2)


def test_nc3_query_hash_mismatch_is_intercepted(tmp_path: Path) -> None:
    f = _rendered(tmp_path)

    def repoint(recs):
        for r in recs:
            if getattr(r, "key", None) == "cov":
                r.source["sql"] = SQL_BETA  # record now claims a different query

    sc_bad = _rekey(f["sidecar"], repoint)
    res = gate_report(f["rendered"], sc_bad, template=f["template"], manifest=f["manifest"])
    assert res["status"] == "FAIL"
    assert "QUERY_HASH_MISMATCH" in _codes(res)


def test_nc3_recomputed_query_disagreeing_with_record_is_intercepted(tmp_path: Path) -> None:
    f = _rendered(tmp_path)
    conn = sqlite3.connect(f["db"])
    conn.execute("UPDATE sample SET v = 95.71 WHERE id = 'alpha'")
    conn.commit()
    conn.close()
    res = gate_report(f["rendered"], f["sidecar"], template=f["template"],
                      manifest=f["manifest"], db_path=f["db"])
    assert res["status"] == "FAIL"
    assert "SOURCE_DRIFT" in _codes(res)


def test_gate_unverifiable_query_is_never_a_pass(tmp_path: Path) -> None:
    f = _rendered(tmp_path)

    def break_sql(recs):
        for r in recs:
            if getattr(r, "key", None) == "cov":
                r.source["sql"] = "SELECT missing_column FROM sample"
                r.query_hash = query_hash(r.source["sql"], [])

    sc_bad = _rekey(f["sidecar"], break_sql)
    res = gate_report(f["rendered"], sc_bad, template=f["template"],
                      manifest=f["manifest"], db_path=f["db"])
    assert res["status"] == "FAIL"
    assert "SOURCE_ERROR" in _codes(res)


def test_gate_artifact_hash_mismatch_is_intercepted(tmp_path: Path) -> None:
    f = _rendered(tmp_path)
    (tmp_path / "figs" / "curve.png").write_bytes(b"\x89PNG\r\n\x1a\nDIFFERENT-bytes")
    res = gate_report(f["rendered"], f["sidecar"], template=f["template"],
                      manifest=f["manifest"], artifact_root=f["root"])
    assert res["status"] == "FAIL"
    assert "ARTIFACT_HASH_MISMATCH" in _codes(res)

    (tmp_path / "figs" / "curve.png").unlink()
    res2 = gate_report(f["rendered"], f["sidecar"], template=f["template"],
                       manifest=f["manifest"], artifact_root=f["root"])
    assert "ARTIFACT_MISSING" in _codes(res2)


# ---------------------------------------------------------------------------
# 15-18: scope declarations and policy
# ---------------------------------------------------------------------------
def test_declared_and_literature_regions_are_excluded_and_counted(tmp_path: Path) -> None:
    f = _rendered(tmp_path)
    body = (
        f["rendered"]
        + "\n<!-- prov:scope:literature -->\n"
        + "Earlier work reported 12.34 percent over 7 of 9 runs.\n"
        + "<!-- prov:scope:end -->\n"
        + "\n<!-- prov:scope:declared -->\n"
        + "Declared design target is 3.5 units.\n"
        + "<!-- prov:scope:end -->\n"
        + "\nA line-declared bound of 4.25 holds. <!-- declared -->\n"
    )
    res = gate_report(body, f["sidecar"], artifact_root=f["root"],
                      config=GateConfig(value_equality_binding=True))
    # no template/manifest -> value-equality path; declared numbers must not be bare
    assert res["excluded_by_reason"].get("literature_region", 0) >= 3
    assert res["excluded_by_reason"].get("declared_region", 0) >= 1
    assert res["excluded_by_reason"].get("declared_line", 0) >= 1
    assert res["bare_number_ratio"] == 0.0
    assert res["status"] == "PASS", _codes(res)


def test_unterminated_scope_region_is_a_failure(tmp_path: Path) -> None:
    f = _rendered(tmp_path)
    body = f["rendered"] + "\n<!-- prov:scope:declared -->\nsome 9.99 value\n"
    res = gate_report(body, f["sidecar"])
    assert res["status"] == "FAIL"
    assert "SCOPE_REGION_UNTERMINATED" in _codes(res)


def test_structural_numerals_are_excluded_by_declared_scope(tmp_path: Path) -> None:
    sc = Sidecar()
    sc.add_number("cov", 85.71, SQL_ALPHA, [], decimals=1)
    body = (
        "See Table 2 and Fig. 3 for the layout.\n"
        "Snapshot taken 2026-11-10; schema version 3.11.6.\n"
        "Inline `0.5` in code is machine content.\n"
        "\n```\nvalue = 42\n```\n"
        "\n## 4.1 Details\n"
        "\nOnly {{prov:cov}} % here.\n"
    )
    sc.add_number("unused", 1.0, "SELECT 1", [])
    doc = render(body, sc, strict=False).markdown
    # a toy document is mostly code, so lift the escape-hatch cap for this case
    res = gate_report(doc, sc, config=GateConfig(max_code_ratio=1.0))
    reasons = res["excluded_by_reason"]
    assert reasons.get("label_numeral", 0) >= 2
    assert reasons.get("structural_date_or_version", 0) >= 2
    assert reasons.get("code_context", 0) >= 2
    assert reasons.get("section_number", 0) >= 1
    assert res["bare_number_ratio"] == 0.0
    assert res["status"] == "PASS", _codes(res)


def test_ordered_list_markers_are_excluded_structurally(tmp_path: Path) -> None:
    sc = Sidecar()
    sc.add_number("cov", 85.71, SQL_ALPHA, [], decimals=1)
    body = (
        "1. First step, coverage {{prov:cov}} %.\n"
        "2. Second step with no measurement.\n"
        "12) Twelfth step.\n"
    )
    res = gate_report(render(body, sc).markdown, sc, artifact_root=tmp_path)
    assert res["excluded_by_reason"].get("list_marker", 0) >= 3
    assert res["status"] == "PASS", _codes(res)


def test_region_marker_quoted_in_code_is_inert(tmp_path: Path) -> None:
    sc = Sidecar()
    sc.add_number("cov", 85.71, SQL_ALPHA, [], decimals=1)
    body = (
        "Documentation of the marker syntax: `<!-- prov:scope:declared -->` "
        "and `<!-- prov:scope:literature -->`.\n"
        "\n"
        "```\n<!-- prov:scope:declared -->\n```\n"
        "\n"
        "Coverage {{prov:cov}} %.\n"
    )
    res = gate_report(render(body, sc).markdown, sc,
                      config=GateConfig(max_code_ratio=1.0))
    assert "SCOPE_REGION_UNTERMINATED" not in _codes(res)
    assert res["status"] == "PASS", _codes(res)


def test_result_number_in_a_heading_stays_in_scope(tmp_path: Path) -> None:
    sc = Sidecar()
    sc.add_number("cov", 85.71, SQL_ALPHA, [], decimals=1)
    # section numbering is structural, but a result printed in a heading is not
    fabricated = "## 7.1 Coverage was 91.25 %\n"
    true_doc = "## 7.1 Coverage was {{prov:cov}} %\n"
    res_bad = gate_report(render(fabricated, sc, strict=False).markdown, sc)
    assert res_bad["bare_number_ratio"] == 1.0
    assert res_bad["status"] == "FAIL"
    assert res_bad["excluded_by_reason"].get("section_number", 0) == 1
    res_ok = gate_report(render(true_doc, sc).markdown, sc)
    assert res_ok["status"] == "PASS", _codes(res_ok)
    assert res_ok["counts"]["bound"] == 1


def test_ratio_threshold_is_exclusive(tmp_path: Path) -> None:
    sc = Sidecar()
    for key, value in (("a", 1.5), ("b", 2.5), ("c", 3.5)):
        sc.add_number(key, value, f"SELECT {value}", [], decimals=1)
    doc = render("{{prov:a}} {{prov:b}} {{prov:c}}", sc).markdown + " stray 9.75\n"
    at_threshold = gate_report(doc, sc, config=GateConfig(max_bare_ratio=0.25))
    below = gate_report(doc, sc, config=GateConfig(max_bare_ratio=0.2499))
    assert at_threshold["bare_number_ratio"] == pytest.approx(0.25)
    assert at_threshold["status"] == "PASS", "ratio == threshold is not an exceedance"
    assert below["status"] == "FAIL"


def test_policy_reject_vs_flag_at_same_ratio(tmp_path: Path) -> None:
    sc = Sidecar()
    for key, value, sql in (("a", 1.5, "SELECT 1"), ("b", 2.5, "SELECT 2"),
                            ("c", 3.5, "SELECT 3"), ("d", 4.5, "SELECT 4")):
        sc.add_number(key, value, sql, [], decimals=1)
    doc = render("{{prov:a}} {{prov:b}} {{prov:c}}", sc).markdown + " stray 9.75\n"
    reject = gate_report(doc, sc, config=GateConfig(max_bare_ratio=0.0, on_exceed="reject"))
    flag = gate_report(doc, sc, config=GateConfig(max_bare_ratio=0.0, on_exceed="flag"))
    tolerant = gate_report(doc, sc, config=GateConfig(max_bare_ratio=0.5, on_exceed="reject"))
    assert reject["status"] == "FAIL" and reject["disposition"] == "reject"
    assert flag["status"] == "FLAG" and flag["disposition"] == "flag"
    assert tolerant["status"] == "PASS"
    assert reject["bare_number_ratio"] == pytest.approx(0.25)
    assert flag["bare_number_ratio"] == pytest.approx(reject["bare_number_ratio"])


def test_value_equality_binding_is_declared_weak(tmp_path: Path) -> None:
    sc = Sidecar()
    sc.add_number("cov", 85.71, SQL_ALPHA, [], decimals=1)
    hand_written = "Coverage reached 85.7 % (typed by hand, same digits).\n"
    weak = gate_report(hand_written, sc, config=GateConfig(value_equality_binding=True))
    strict = gate_report(hand_written, sc, config=GateConfig(value_equality_binding=False))
    assert weak["binding_mode"] == "value_equality"
    assert weak["status"] == "PASS", _codes(weak)
    assert strict["status"] == "FAIL" and strict["bare_number_ratio"] == 1.0
    # require_manifest removes the weak mode from the policy entirely
    res = gate_report(hand_written, sc, config=GateConfig(require_manifest=True))
    assert "MANIFEST_REQUIRED" in _codes(res)


def test_code_context_escape_hatch_is_capped(tmp_path: Path) -> None:
    sc = Sidecar()
    sc.add_number("cov", 85.71, SQL_ALPHA, [], decimals=1)
    doc = render("Coverage {{prov:cov}} %.\n", sc).markdown
    doc += " ".join(f"`{v}`" for v in ("11.1", "22.2", "33.3", "44.4"))
    res = gate_report(doc, sc, config=GateConfig(max_code_ratio=0.10))
    assert "CODE_CONTEXT_RATIO_HIGH" in _codes(res)
    assert res["status"] == "FLAG"
    assert res["code_context_ratio"] > 0.10


def test_declared_region_escape_hatch_is_capped(tmp_path: Path) -> None:
    sc = Sidecar()
    sc.add_number("cov", 85.71, SQL_ALPHA, [], decimals=1)
    region = ("<!-- prov:scope:declared -->\n"
              + " ".join(str(v) for v in (1.25, 2.5, 3.75, 4.125, 5.5, 6.75, 7.25, 8.5))
              + "\n<!-- prov:scope:end -->\n")
    doc = render("Coverage {{prov:cov}} %.\n", sc).markdown + region
    res = gate_report(doc, sc, config=GateConfig(max_region_excluded_ratio=0.5))
    assert "REGION_EXCLUDED_RATIO_HIGH" in _codes(res)
    assert res["status"] == "FLAG"
    # and with the documented cap the same document is a PASS
    res_ok = gate_report(doc, sc, config=GateConfig(max_region_excluded_ratio=0.95))
    assert res_ok["status"] == "PASS", _codes(res_ok)


def test_glued_percent_placeholder_is_still_bound(tmp_path: Path) -> None:
    sc = Sidecar()
    sc.add_number("cov", 85.71, SQL_ALPHA, [], decimals=1)
    template = "Coverage {{prov:cov}}% of rows.\n"
    res = render(template, sc)
    assert res.ambiguous_joins == []
    gate = gate_report(res.markdown, sc, template=template, manifest=res.to_manifest())
    assert gate["status"] == "PASS", _codes(gate)
    assert gate["counts"]["bound"] == 1 and gate["bare_number_ratio"] == 0.0


def test_render_refuses_ambiguous_digit_join(tmp_path: Path) -> None:
    from tools.prov_gate.render import AmbiguousJoinError

    sc = Sidecar()
    sc.add_number("a", 1.5, "SELECT 1", [], decimals=1)
    sc.add_number("b", 2.5, "SELECT 2", [], decimals=1)
    with pytest.raises(AmbiguousJoinError):
        render("{{prov:a}}{{prov:b}}\n", sc)
    lenient = render("{{prov:a}}{{prov:b}}\n", sc, strict=False)
    assert lenient.ambiguous_joins == ["a", "b"], "both sides of the join are ambiguous"


def test_sdk_publish_renders_writes_and_gates_disk_bytes(tmp_path: Path) -> None:
    from tools.prov_gate.sdk import render_and_gate

    sc = _sidecar(tmp_path)
    tpl = tmp_path / "template.md"
    tpl.write_text(TEMPLATE, encoding="utf-8")
    out = render_and_gate(tpl, sc, out=tmp_path / "published.md",
                          manifest_out=tmp_path / "manifest.json",
                          db_path=_db(tmp_path), artifact_root=tmp_path,
                          config=GateConfig(require_manifest=True))
    assert out.status == "PASS", _codes(out.gate)
    published = (tmp_path / "published.md").read_text(encoding="utf-8")
    assert out.gate["document_sha256"] == sha256_text(published)


def test_sdk_review_side_catches_post_publish_edit(tmp_path: Path) -> None:
    from tools.prov_gate.sdk import render_and_gate, verify_published

    sc = _sidecar(tmp_path)
    tpl = tmp_path / "template.md"
    tpl.write_text(TEMPLATE, encoding="utf-8")
    db = _db(tmp_path)
    out = render_and_gate(tpl, sc, out=tmp_path / "published.md",
                          manifest_out=tmp_path / "manifest.json",
                          db_path=db, artifact_root=tmp_path)
    assert out.status == "PASS"
    # an editor rewrites the published file after the fact
    path = tmp_path / "published.md"
    path.write_text(path.read_text(encoding="utf-8").replace("0.412", "0.421"),
                    encoding="utf-8")
    verdict = verify_published(path, sc, template=tpl,
                               manifest=tmp_path / "manifest.json",
                               db_path=db, artifact_root=tmp_path)
    assert verdict["status"] == "FAIL"
    assert "DOC_SHA_MISMATCH" in _codes(verdict)


# ---------------------------------------------------------------------------
# 19-21: aggregate negative controls + CLI
# ---------------------------------------------------------------------------
def test_all_three_negative_controls_intercepted_100_percent(tmp_path: Path) -> None:
    f = _rendered(tmp_path)
    outcomes: dict[str, list[bool]] = {"NC-1 body rewrite": [], "NC-1 sidecar rewrite": [],
                                       "NC-2 missing key": [], "NC-3 query hash": [],
                                       "NC-3 value drift": []}

    for offset in (0.1, 1.0, 7.5, 12.34):  # several tamper magnitudes
        tampered = f["rendered"].replace("85.7", f"{85.7 + offset:.1f}")
        outcomes["NC-1 body rewrite"].append(
            gate_report(tampered, f["sidecar"], template=f["template"],
                        manifest=f["manifest"])["status"] == "FAIL")

        def bump(recs, off=offset):
            for r in recs:
                if getattr(r, "key", None) == "cov":
                    r.value = 85.71 + off

        outcomes["NC-1 sidecar rewrite"].append(
            gate_report(f["rendered"], _rekey(f["sidecar"], bump), template=f["template"],
                        manifest=f["manifest"])["status"] == "FAIL")

        def drop_key(recs, name=("cov", "res")[int(offset) % 2]):
            recs[:] = [r for r in recs if getattr(r, "key", None) != name]

        outcomes["NC-2 missing key"].append(
            gate_report(f["rendered"], _rekey(f["sidecar"], drop_key), template=f["template"],
                        manifest=f["manifest"])["status"] == "FAIL")

        def repoint(recs, sql=("SELECT v FROM sample WHERE id = 'beta'" if offset > 1
                               else "SELECT v FROM sample WHERE id = 'alpha' /*moved*/")):
            for r in recs:
                if getattr(r, "key", None) == "cov":
                    r.source["sql"] = sql

        outcomes["NC-3 query hash"].append(
            gate_report(f["rendered"], _rekey(f["sidecar"], repoint), template=f["template"],
                        manifest=f["manifest"])["status"] == "FAIL")

        db = tmp_path / f"drift_{int(offset * 100)}.sqlite"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE sample (id TEXT PRIMARY KEY, v REAL NOT NULL)")
        conn.executemany("INSERT INTO sample VALUES (?, ?)",
                         [("alpha", 85.71 + offset), ("beta", 0.4123)])
        conn.commit()
        conn.close()
        outcomes["NC-3 value drift"].append(
            gate_report(f["rendered"], f["sidecar"], template=f["template"],
                        manifest=f["manifest"], db_path=db)["status"] == "FAIL")

    rates = {k: sum(v) / len(v) for k, v in outcomes.items()}
    assert all(r == 1.0 for r in rates.values()), rates


def test_cli_render_and_gate_exit_codes(tmp_path: Path, capsys) -> None:
    sc = _sidecar(tmp_path)
    sidecar_path = sc.save(tmp_path / "sidecar.json")
    tmpl = tmp_path / "template.md"
    tmpl.write_text(TEMPLATE, encoding="utf-8")
    out = tmp_path / "published.md"
    man = tmp_path / "manifest.json"
    assert render_main(["--template", str(tmpl), "--sidecar", str(sidecar_path),
                        "--out", str(out), "--manifest", str(man)]) == 0
    json_out = tmp_path / "gate.json"
    code = gate_main(["--published", str(out), "--sidecar", str(sidecar_path),
                      "--template", str(tmpl), "--manifest", str(man),
                      "--db", str(_db(tmp_path)), "--artifact-root", str(tmp_path),
                      "--json-out", str(json_out), "-q"])
    assert code == 0, out.read_text(encoding="utf-8")
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"
    # a template with a key the sidecar lacks must make the strict render refuse
    bad = tmp_path / "bad.md"
    bad.write_text("value {{prov:absent}}\n", encoding="utf-8")
    assert render_main(["--template", str(bad), "--sidecar", str(sidecar_path),
                        "--out", str(tmp_path / "bad_out.md")]) == 1
    capsys.readouterr()