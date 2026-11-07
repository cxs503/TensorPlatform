#!/usr/bin/env python3
"""Gate tests for the chronicle report line (IN18-T3).

Five of these are **negative controls**: they inject a specific falsification
into an otherwise PASSing report and require the verifier to return FAIL with
the matching code. A gate that cannot say NO is not a gate, so if any negative
control here turns green on a PASS verdict, the test suite goes red.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.chronicle.report import demo  # noqa: E402
from tools.chronicle.report.provenance import ProvenanceStore, bare_numbers  # noqa: E402
from tools.chronicle.report.skeleton import (  # noqa: E402
    SECTION_ORDER,
    SkeletonMeta,
    render_skeleton,
    validate_skeleton,
)
from tools.chronicle.report.snapshot import Snapshot, build_snapshot, sha256_file  # noqa: E402
from tools.chronicle.report.verify import verify_report  # noqa: E402

TS = "2026-11-07T00:00:00+00:00"


@pytest.fixture(scope="session")
def bundle(tmp_path_factory) -> dict:
    """One real build of the first issue, shared read-only by all tests."""
    root = tmp_path_factory.mktemp("chronicle")
    art = root / "artifacts"
    docs = root / "docs"
    report = demo.build(art, docs)
    assert report["acceptance"]["verifier_status"] == "PASS", report["verification"]["findings"]
    return {
        "root": root,
        "art": art,
        "docs": docs,
        "manuscript": Path(report["artifacts"]["manuscript"]),
        "provenance": Path(report["artifacts"]["provenance"]),
        "snapshot": Path(report["artifacts"]["snapshot"]),
        "report": report,
    }


@pytest.fixture()
def clone(bundle, tmp_path) -> dict:
    """A private, mutable copy of the bundle for one negative control."""
    dst = tmp_path / "bundle"
    shutil.copytree(bundle["root"], dst)
    return {
        "root": dst,
        "manuscript": (dst / "docs/manuscript.md"),
        "provenance": (dst / "artifacts/provenance.json"),
        "snapshot": (dst / "artifacts/snapshot"),
        "artifact_root": dst / "docs",
    }


def _verify(cl: dict) -> dict:
    return verify_report(cl["manuscript"], cl["provenance"], cl["snapshot"], artifact_root=cl["artifact_root"])


def _codes(result: dict) -> set[str]:
    return {f["code"] for f in result["findings"] if f["severity"] == "FAIL"}


# ---------------------------------------------------------------------------
# 1. snapshot: deterministic, hash-anchored
# ---------------------------------------------------------------------------
def test_snapshot_is_deterministic_and_hash_anchored(tmp_path):
    a = build_snapshot(out_dir=tmp_path / "s1", ts=TS)
    b = build_snapshot(out_dir=tmp_path / "s2", ts=TS)
    assert a.snapshot_hash == b.snapshot_hash, "same source must yield byte-identical snapshot"
    assert a.manifest["rowcounts"]["capability_rows"] == 21
    assert a.manifest["rowcounts"]["evidence_rows"] > 0
    assert a.integrity()["ok"] is True
    # every recorded number is a real SQL query over the frozen rows
    assert a.scalar("SELECT COUNT(*) FROM capability") == 21
    assert a.scalar("SELECT COUNT(*) FROM capability WHERE reconciled_level = 'REAL'") >= 0


# ---------------------------------------------------------------------------
# 2. skeleton: ten sections, ordered, structurally validated
# ---------------------------------------------------------------------------
def test_skeleton_has_ten_sections_and_rejects_damage(bundle):
    md = bundle["manuscript"].read_text(encoding="utf-8")
    check = validate_skeleton(md)
    assert check["ok"], check["findings"]
    assert check["sections_found"] == SECTION_ORDER
    assert len(SECTION_ORDER) == 10

    meta = SkeletonMeta("T", "S", "A", "0" * 64, TS, "2026-11-07")
    stub = render_skeleton(meta)
    assert validate_skeleton(stub)["ok"]

    damaged = md.replace("## 6. Results", "## 6. Resultz")
    bad = validate_skeleton(damaged)
    assert not bad["ok"]
    assert "SKELETON_SECTION_MISSING" in {f["code"] for f in bad["findings"]}

    # a true positional swap of two section bodies: all ten names survive, order breaks
    swapped = (
        md.replace("## 5. Experimental Setup", "## SWAP5")
        .replace("## 6. Results", "## 5. Experimental Setup")
        .replace("## SWAP5", "## 6. Results")
    )
    order_bad = validate_skeleton(swapped)
    assert not order_bad["ok"]
    assert "SKELETON_SECTION_ORDER" in {f["code"] for f in order_bad["findings"]}


# ---------------------------------------------------------------------------
# 3. figures: three figures, PDF + PNG, hashed into provenance
# ---------------------------------------------------------------------------
def test_figure_pipeline_emits_three_figures_dual_format(bundle):
    fig_dir = bundle["art"] / "figures"
    pngs = sorted(fig_dir.glob("*.png"))
    pdfs = sorted(fig_dir.glob("*.pdf"))
    assert len(pngs) == 3 and len(pdfs) == 3
    for f in pngs + pdfs:
        assert f.stat().st_size > 2000, f
    store = ProvenanceStore.load(bundle["provenance"])
    fig_records = [r for r in store.records.values() if r.kind == "figure"]
    assert len(fig_records) == 3
    for rec in fig_records:
        assert len(rec.artifacts) == 2
        for art in rec.artifacts:
            assert sha256_file(art["path"]) == art["sha256"]
        assert rec.source["sql"] and rec.source["snapshot_hash"]


# ---------------------------------------------------------------------------
# 4. honest report: PASS
# ---------------------------------------------------------------------------
def test_honest_report_passes(bundle):
    result = _verify(
        {
            "manuscript": bundle["manuscript"],
            "provenance": bundle["provenance"],
            "snapshot": bundle["snapshot"],
            "artifact_root": bundle["docs"],
        }
    )
    assert result["status"] == "PASS", result["findings"]
    assert result["counts"]["queries_reexecuted"] == result["counts"]["number_records"] >= 15
    assert result["counts"]["unmarked_numbers_in_blocks"] == 0
    assert result["counts"]["figure_files_checked"] == 6


# ---------------------------------------------------------------------------
# 5. NEGATIVE: a fabricated digit must FAIL
# ---------------------------------------------------------------------------
def test_fabricated_number_must_fail(clone):
    md = clone["manuscript"].read_text(encoding="utf-8")
    store = ProvenanceStore.load(clone["provenance"])
    rec = next(r for r in store.records.values() if r.kind == "number" and r.decimals == 0)
    truth = f"{rec.value:g}"
    clone["manuscript"].write_text(md.replace(truth + " {{prov:", f"{int(rec.value) + 1} {{{{prov:", 1), encoding="utf-8")
    result = _verify(clone)
    assert result["status"] == "FAIL"
    assert "FABRICATED_NUMBER" in _codes(result)


# ---------------------------------------------------------------------------
# 6. NEGATIVE: re-pointing a provenance record must FAIL (source drift)
# ---------------------------------------------------------------------------
def test_record_value_tamper_must_fail(clone):
    payload = json.loads(clone["provenance"].read_text(encoding="utf-8"))
    rec = next(r for r in payload["records"] if r["kind"] == "number")
    rec["value"] = float(rec["value"]) + 7.0
    clone["provenance"].write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    result = _verify(clone)
    assert result["status"] == "FAIL"
    assert {"SOURCE_DRIFT", "FABRICATED_NUMBER"} <= _codes(result)


# ---------------------------------------------------------------------------
# 7. NEGATIVE: editing the frozen snapshot must FAIL
# ---------------------------------------------------------------------------
def test_snapshot_tamper_must_fail(clone):
    db = clone["snapshot"] / "report.sqlite"
    con = sqlite3.connect(db)
    con.execute("INSERT INTO capability (id, domain, evidence_count) VALUES ('ghost.cap','ghost',0)")
    con.commit()
    con.close()
    result = _verify(clone)
    assert result["status"] == "FAIL"
    assert "SNAP_HASH_MISMATCH" in _codes(result)


# ---------------------------------------------------------------------------
# 8. NEGATIVE: replacing a figure file must FAIL
# ---------------------------------------------------------------------------
def test_figure_tamper_must_fail(clone):
    store = ProvenanceStore.load(clone["provenance"])
    rec = next(r for r in store.records.values() if r.kind == "figure")
    png = Path(rec.artifacts[0]["path"])
    png.write_bytes(png.read_bytes() + b"\n% tampered\n")
    result = _verify(clone)
    assert result["status"] == "FAIL"
    assert "FIGURE_HASH_MISMATCH" in _codes(result)


# ---------------------------------------------------------------------------
# 9. NEGATIVE: an unmarked number inside an enforced block must FAIL
# ---------------------------------------------------------------------------
def test_unmarked_number_must_fail(clone):
    md = clone["manuscript"].read_text(encoding="utf-8")
    marker = "**表 1 快照一览（全部数值由 SQL 注入，零手抄）**"
    assert marker in md
    clone["manuscript"].write_text(md.replace(marker, marker + "\n\n覆盖率 99.9 % 未经查询。", 1), encoding="utf-8")
    result = _verify(clone)
    assert result["status"] == "FAIL"
    assert "UNMARKED_NUMBER" in _codes(result)


# ---------------------------------------------------------------------------
# 10. NEGATIVE: a token with no record must FAIL
# ---------------------------------------------------------------------------
def test_dangling_token_must_fail(clone):
    payload = json.loads(clone["provenance"].read_text(encoding="utf-8"))
    payload["records"] = [r for r in payload["records"] if r["kind"] != "figure"]
    clone["provenance"].write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    result = _verify(clone)
    assert result["status"] == "FAIL"
    assert "DANGLING_TOKEN" in _codes(result)


# ---------------------------------------------------------------------------
# 11. structural numerals are exempt, measured numerals are not
# ---------------------------------------------------------------------------
def test_label_numerals_exempt_but_measurements_not():
    assert bare_numbers("见图 1–3 与表 2、Fig. 4") == []
    assert [b["literal"] for b in bare_numbers("准确率 87.5 %")] == ["87.5"]