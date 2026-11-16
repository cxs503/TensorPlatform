#!/usr/bin/env python3
"""能力证据台账单一真源（SSoT）验收测试（m22585 / r27-WP3）。

判据映射
--------
C1: ``artifacts/capability_ledger/ssot_matrix.json`` 存在，21 行 × 两源等级 + basis + 一致位。
C2: 工具侧载荷与 canonical 产物逐项比对，差异项全部带 basis + sha256（0 条「无说明差异」）。
C3: 4 条已知分歧被钉死；≥4 个方向的负对照（改一级/改 basis/改汇总/改工具侧）必须报红。
C4: ``with_evidence=3 / without_evidence=18`` 可由产物复算（独立重算，不经 ssot 汇总函数）。
C5: 源码中不含按 capability_id 硬编码的等级表（grep 负对照）。
C6: 矩阵给出「REAL 可核验计数」口径（产物 REAL 且 evidence_count>0）。

全程离线：只读工作树内产物与落盘快照，不访问 :8001/:8010。
"""
from __future__ import annotations

import copy
import json
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.capability_ledger import ssot  # noqa: E402

MATRIX = ROOT / ssot.MATRIX_REL
CANON = ROOT / ssot.CANONICAL_REL
SNAPSHOT = ROOT / ssot.SNAPSHOT_REL


# ────────────────────────────────────────────── 夹具
@pytest.fixture(scope="module")
def canonical_payload() -> dict:
    return json.loads(CANON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def canonical_rows(canonical_payload) -> dict:
    return ssot.parse_resolved(canonical_payload)


@pytest.fixture(scope="module")
def tool_capture() -> dict:
    """工具侧读数：落盘快照（带 sha256 provenance），离线可复算。"""
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    return {
        "provenance": {"kind": "snapshot", "path": str(SNAPSHOT),
                       "sha256": ssot.sha256_bytes(SNAPSHOT.read_bytes()),
                       "source": snap.get("source"),
                       "source_sha256": snap.get("source_sha256"),
                       "stale": True, "tried": []},
        "levels": ssot.parse_harness({"capabilities": snap["capabilities"]}),
    }


@pytest.fixture(scope="module")
def matrix(tool_capture) -> dict:
    return ssot.build_matrix(ROOT, tool_capture=tool_capture)


def _row(matrix: dict, cid: str) -> dict:
    for r in matrix["rows"]:
        if r["capability_id"] == cid:
            return r
    raise AssertionError(f"矩阵缺 {cid}")


HEX16 = re.compile(r"^[0-9a-f]{16}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


# ────────────────────────────────────────────── C1 矩阵形态
def test_c1_matrix_artifact_shape():
    assert MATRIX.is_file(), f"缺少产物 {MATRIX}"
    disk = json.loads(MATRIX.read_text(encoding="utf-8"))
    assert disk["schema_version"] == "capability-ledger-ssot-matrix/1.0"
    rows = disk["rows"]
    assert len(rows) == 21
    assert len({r["capability_id"] for r in rows}) == 21, "能力项重复"
    for r in rows:
        assert set(r) >= {"capability_id", "artifact", "tool", "agree"}
        assert r["artifact"]["level"] in ssot.LEVELS
        assert r["tool"]["level"] in ssot.LEVELS
        assert r["artifact"]["basis"] is not None
        assert HEX64.match(r["artifact"]["row_sha256"]), r["capability_id"]
        assert HEX64.match(r["tool"]["row_sha256"]), r["capability_id"]
    assert HEX64.match(disk["sources"]["artifact"]["sha256"])
    assert HEX64.match(disk["sources"]["tool"]["sha256"])


def test_c1_matrix_on_disk_matches_fresh_build(matrix):
    disk = json.loads(MATRIX.read_text(encoding="utf-8"))
    disk_rows = {r["capability_id"]: r for r in disk["rows"]}
    for r in matrix["rows"]:
        d = disk_rows[r["capability_id"]]
        assert d["artifact"]["level"] == r["artifact"]["level"], r["capability_id"]
        assert d["tool"]["level"] == r["tool"]["level"], r["capability_id"]
        assert d["agree"] == r["agree"], r["capability_id"]
    assert disk["counts"]["disagree"] == matrix["counts"]["disagree"]


# ────────────────────────────────────────────── 第一层：产物内部自洽
def test_layer1_artifact_self_consistent(matrix):
    assert matrix["layer1"]["status"] == "ok", matrix["layer1"]["violations"]
    assert matrix["layer1"]["violations"] == []
    assert matrix["counts"]["unexplained"] == 0, matrix["divergence_records"]


def test_layer1_divergence_derivable_each_row(canonical_rows):
    """claimed / reconciled / evidence_count 三者必须能互推出 divergence 标签。"""
    for cid, r in canonical_rows.items():
        exp = ssot.expected_divergence(r["claimed_level"], r["reconciled_level"],
                                       int(r["evidence_count"] or 0))
        assert exp == r["divergence"], (cid, exp, r["divergence"])


# ────────────────────────────────────────────── C4 口径可复算（独立重算）
def test_c4_counts_recomputable_independently(canonical_payload):
    caps = canonical_payload["capabilities"]
    with_ev = sum(1 for c in caps if (c.get("evidence_count") or 0) > 0)
    tally = {}
    for c in caps:
        tally[c["divergence"]] = tally.get(c["divergence"], 0) + 1
    for k in ("OK", "OVERCLAIM", "REVERIFY", "UNVERIFIED"):  # schema 枚举补零
        tally.setdefault(k, 0)
    declared = canonical_payload["summary"]
    assert with_ev == declared["with_evidence"] == 3
    assert len(caps) - with_ev == declared["without_evidence"] == 18
    assert tally == declared["divergence_tally"] == {
        "OK": 4, "OVERCLAIM": 2, "REVERIFY": 0, "UNVERIFIED": 15}
    disk = json.loads(MATRIX.read_text(encoding="utf-8"))
    assert disk["summary_recompute"]["with_evidence"] == 3
    assert disk["summary_recompute"]["without_evidence"] == 18
    assert disk["summary_recompute"]["match"] is True


# ────────────────────────────────────────────── C3 四条已知分歧钉死
def test_c3_geometry_hydro_curves_pinned(matrix):
    r = _row(matrix, "geometry.hydro_curves")
    assert r["artifact"]["level"] == "WITHHELD"
    assert r["artifact"]["claimed_level"] == "REAL"
    assert r["tool"]["level"] == "REAL"
    assert r["agree"] is False
    d = r["divergence_record"]
    assert d["artifact_level"] == "WITHHELD" and d["tool_level"] == "REAL"
    assert d["direction"] == "tool>artifact(工具高判)"
    assert ssot.NO_EVIDENCE_BASIS in d["artifact_basis"]
    assert d["artifact_evidence_count"] == 0
    assert HEX64.match(d["artifact_row_sha256"])
    assert HEX64.match(d["artifact_basis_sha256"])
    assert HEX64.match(d["tool_source_sha256"])


def test_c3_calc_stability_pinned(matrix):
    r = _row(matrix, "calc.stability")
    assert r["artifact"]["level"] == "REAL"
    assert r["tool"]["level"] == "MOCK"
    assert r["agree"] is False
    d = r["divergence_record"]
    assert d["direction"] == "tool<artifact(工具低判)"
    assert "m1564_stability_adjudication" in d["artifact_basis"]
    assert d["artifact_basis_line_sha256"] == "1e8f05848c597bdf"
    assert r["artifact"]["internal_divergence"] == "OK"
    assert r["artifact"]["evidence_line_sha256"]


def test_c3_calc_resistance_pinned(matrix):
    r = _row(matrix, "calc.resistance")
    assert r["artifact"]["level"] == "WITHHELD"
    assert r["artifact"]["claimed_level"] == "REAL"
    assert r["artifact"]["internal_divergence"] == "OVERCLAIM"
    assert r["tool"]["level"] == "MOCK"
    assert r["agree"] is False
    d = r["divergence_record"]
    assert "inline_resistance_audit_2026-11-01" in d["artifact_basis"]
    assert HEX16.match(d["artifact_basis_line_sha256"])
    assert d["artifact_evidence_count"] == 4


def test_c3_propeller_open_water_pinned(matrix):
    """工具与产物同判 MOCK，但自述 REAL → 分歧在产物内部（OVERCLAIM），同样必须钉死。"""
    r = _row(matrix, "propeller.open_water")
    assert r["artifact"]["level"] == "MOCK"
    assert r["tool"]["level"] == "MOCK"
    assert r["agree"] is True
    assert r["artifact"]["claimed_level"] == "REAL"
    assert r["artifact"]["internal_divergence"] == "OVERCLAIM"
    assert "PROPULSION_OPENWATER_M1584" in r["artifact"]["basis"]
    assert HEX16.match(r["artifact"]["evidence_line_sha256"][-1])


# ────────────────────────────────────────────── 负对照（≥4 个方向）
def test_neg1_tamper_reconciled_level_turns_red(canonical_payload):
    """方向 A：把产物某行等级改一级（不改 divergence）→ 第一层必须报红。"""
    bad = copy.deepcopy(canonical_payload)
    for c in bad["capabilities"]:
        if c["id"] == "calc.stability":
            c["reconciled_level"] = "MOCK"       # REAL → MOCK
    rows = ssot.parse_resolved(bad)
    rep = ssot.layer1_check(rows, bad["summary"])
    assert rep["status"] == "fail"
    kinds = {v["check"] for v in rep["violations"]}
    assert "divergence_derivable" in kinds, rep["violations"]
    assert any(v.get("capability_id") == "calc.stability" for v in rep["violations"])


def test_neg2_tamper_basis_or_evidence_turns_red(canonical_payload):
    """方向 B：抹掉 basis / 抽掉证据行 → 第一层必须报红（等级无法被证据支持）。"""
    bad = copy.deepcopy(canonical_payload)
    for c in bad["capabilities"]:
        if c["id"] == "calc.resistance":
            c["basis"] = ""
            c["evidence_refs"] = c["evidence_refs"][:1]  # 4 → 1，与 evidence_count 不符
    rep = ssot.layer1_check(ssot.parse_resolved(bad), bad["summary"])
    assert rep["status"] == "fail"
    kinds = {v["check"] for v in rep["violations"]}
    assert "basis_non_empty" in kinds
    assert "evidence_count_matches_refs" in kinds


def test_neg3_tamper_summary_turns_red(canonical_payload):
    """方向 C：改汇总口径（with_evidence 3→5）→ 复算必须报红。"""
    bad = copy.deepcopy(canonical_payload)
    bad["summary"]["with_evidence"] = 5
    bad["summary"]["without_evidence"] = 16
    rep = ssot.layer1_check(ssot.parse_resolved(bad), bad["summary"])
    assert rep["status"] == "fail"
    kinds = {v["check"] for v in rep["violations"]}
    assert "summary_evidence_counts_recomputable" in kinds
    bad2 = copy.deepcopy(canonical_payload)
    bad2["summary"]["divergence_tally"]["OK"] = 5
    rep2 = ssot.layer1_check(ssot.parse_resolved(bad2), bad2["summary"])
    assert rep2["status"] == "fail"
    assert "summary_divergence_tally_recomputable" in {
        v["check"] for v in rep2["violations"]}


def test_neg4_tamper_tool_side_level_detected(matrix, canonical_rows):
    """方向 D：把工具侧某一项改一级 → 第二层判读必须随之改变（单级敏感）。"""
    base = matrix
    prov = {"path": base["sources"]["tool"].get("path"),
            "sha256": base["sources"]["tool"].get("sha256")}
    levels = {r["capability_id"]: {"level": r["tool"]["level"], "detail": {},
                                   "row_sha256": r["tool"]["row_sha256"]}
              for r in base["rows"]}
    assert levels["geometry.hydro_curves"]["level"] == "REAL"
    levels["geometry.hydro_curves"]["level"] = "WITHHELD"     # REAL → WITHHELD
    rows = ssot.layer2_matrix(canonical_rows, levels, prov)
    got = {r["capability_id"]: r for r in rows}
    assert got["geometry.hydro_curves"]["agree"] is True      # 改侧后变「一致」
    assert got["geometry.hydro_curves"]["divergence_record"] is None
    assert sum(1 for r in rows if not r["agree"]) == base["counts"]["disagree"] - 1
    levels2 = {cid: dict(v) for cid, v in levels.items()}
    levels2["calc.stability"]["level"] = "REAL"
    rows2 = ssot.layer2_matrix(canonical_rows, levels2, prov)
    got2 = {r["capability_id"]: r for r in rows2}
    assert got2["calc.stability"]["agree"] is True
    assert sum(1 for r in rows2 if not r["agree"]) == base["counts"]["disagree"] - 2


def test_neg5_tool_snapshot_matches_live_source_when_available(tool_capture):
    """工具侧快照必须与其声明来源 sha256 一致（防快照被手改/漂移）。"""
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    src = snap.get("source")
    assert src and snap.get("source_sha256") == tool_capture["provenance"]["source_sha256"]
    live = pathlib.Path(src)
    if live.is_file():
        assert ssot.sha256_bytes(live.read_bytes()) == snap["source_sha256"], \
            "快照与其来源已漂移：需重跑 --capture 刷新"
        live_levels = ssot.parse_harness(json.loads(live.read_text(encoding="utf-8")))
        assert {c: v["level"] for c, v in live_levels.items()} == \
               {c: v["level"] for c, v in tool_capture["levels"].items()}


# ────────────────────────────────────────────── C5 禁硬编码等级表
def test_c5_no_hardcoded_level_table():
    """grep 负对照：源码里不许出现 ``"<capability_id>": "<LEVEL>"`` 形式的等级表。"""
    lv = "|".join(ssot.LEVELS)
    pat = re.compile(r"[\"'][A-Za-z0-9_.]+[\"']\s*:\s*[\"'](" + lv + r")[\"']")
    files = [ROOT / "tools/capability_ledger/ssot.py",
             ROOT / "tools/audit/ledger_consumer_audit_tool.py"]
    hits = []
    for f in files:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if pat.search(line):
                hits.append(f"{f.name}:{i}: {line.strip()}")
    assert not hits, "出现疑似硬编码等级表：\n" + "\n".join(hits)
    src = (ROOT / "tools/capability_ledger/ssot.py").read_text(encoding="utf-8")
    canon = json.loads(CANON.read_text(encoding="utf-8"))
    ids = [c["id"] for c in canon["capabilities"]]
    code_lines = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
    leaked = [cid for cid in ids if any(cid in ln for ln in code_lines)]
    assert not leaked, f"ssot.py 里写死了 capability_id：{leaked}"


# ────────────────────────────────────────────── 覆盖缺口只读清单 + REAL 口径
def test_unregistered_domains_readonly(matrix, canonical_payload):
    gaps = matrix["unregistered_domains"]
    assert len(gaps) == 5
    assert gaps == canonical_payload["unregistered_domains"]
    assert "只读" in matrix["unregistered_domains_note"]
    assert not hasattr(ssot, "write_registry")


def test_real_verifiable_count_and_scope(matrix):
    assert matrix["real_verifiable_count"] == 1
    assert matrix["tool_only_real"] == ["geometry.hydro_curves"]
    r = _row(matrix, "calc.stability")
    assert r["artifact"]["level"] == "REAL" and r["artifact"]["evidence_line_sha256"]
    assert "evidence_count>0" in matrix["real_verifiable_note"]


# ────────────────────────────────────────────── 工具渲染层共用同一解析器
def test_tool_renderer_shares_parser_tool_side_payload(tool_capture):
    """编排侧工具的 dict 型载荷渲染与矩阵用的是同一解析器 → 等级逐项一致。"""
    from tools.audit.ledger_consumer_audit_tool import render_caps

    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    payload = {"capabilities": snap["capabilities"]}
    text = render_caps(payload)
    for cid, v in tool_capture["levels"].items():
        line = [ln for ln in text.splitlines() if ln.startswith(cid)]
        assert line, f"渲染缺 {cid}"
        assert v["level"] in line[0], (cid, line[0], v["level"])


def test_cli_check_passes():
    assert ssot.main(["--check"]) == 0


def test_third_source_recorded_not_silent(matrix):
    """第三源（消费者契约行数字）必须被记录并与 canonical 口径显式区分开。"""
    lc = matrix.get("ledger_contract_source") or {}
    assert lc, "矩阵缺第三源记录"
    if "unavailable" in lc:
        pytest.skip(f"消费者契约门不可用（fail-soft）：{lc['unavailable']}")
    assert lc["path"] and HEX64.match(lc["sha256"])
    assert isinstance(lc["unverified"], int) and isinstance(lc["overclaim"], int)
    assert "不可直接比数" in lc["note"]
    # 与 canonical tally 不同是事实，必须被矩阵如实带出（不得抹平）
    canon_tally = matrix["summary_recompute"]["divergence_tally"]
    differs = (lc["unverified"] != canon_tally.get("UNVERIFIED")
               or lc["overclaim"] != canon_tally.get("OVERCLAIM"))
    assert differs or lc["note"], "口径不同却无说明"
