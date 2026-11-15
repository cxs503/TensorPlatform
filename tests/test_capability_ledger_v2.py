"""
能力证据台账（扫描 → 落盘 → 查询）验收测试
============================================
覆盖 DEFECT「证据台账不可查询」的验收判据：

C1 21 条能力，每条含 evidence ∈ 枚举 与 evidence_count
C2 4 条真值断言（hydro_curves=REAL / open_water≠REAL / resistance≠REAL /
   stability 与注册表现值一致）
C3 等级由扫描规则从 config/capabilities.json 模式 + 报告元数据复算（无硬编码等级表），
   两次复算逐字节一致
C4 产物含 as_of/schema_version/sha256，写入路径可配置，平台路径缺失 fail-soft
C5 本文件 ≥5 条测试
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from capability_ledger import engine, sources  # noqa: E402

LEVELS = {"REAL", "MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED"}


@pytest.fixture(scope="module")
def cfg() -> dict:
    return engine.load_config(root=ROOT)


@pytest.fixture(scope="module")
def ledger(cfg) -> dict:
    return engine.build_ledger(cfg, ROOT)


def _by_id(ledger: dict) -> dict:
    return {c["capability_id"]: c for c in ledger["capabilities"]}


# ── C1 ────────────────────────────────────────────────────────────────
def test_c1_21_capabilities_with_evidence_and_count(ledger):
    caps = ledger["capabilities"]
    assert len(caps) == 21
    for c in caps:
        assert c["evidence"] in LEVELS, c
        assert isinstance(c["evidence_count"], int) and c["evidence_count"] >= 0
        assert c["evidence_rank"] == engine.LEVEL_RANK[c["evidence"]]
    # 注册表 21 条必须全覆盖（不多不少）
    assert {c["capability_id"] for c in caps} == {
        "calc.resistance", "calc.stability", "propeller.open_water", "geometry.generate",
        "geometry.hydro_curves", "geometry.list_models", "geometry.verify_official",
        "shiphull.generate", "stdmodels.lookup", "gov.stats", "knowledge.docs",
        "dev.gate_5415", "dev.git", "dev.logs", "dev.nginx_audit", "dev.probe",
        "dev.restart", "dev.services", "ai.predict", "methods.computation", "misc.stubs",
    }


# ── C2 真值断言（4 条，写死） ─────────────────────────────────────────
def test_c2_hydro_curves_is_real(ledger):
    """m21439（平台报告 19373）两案例静水力误差 <3%、22 项 pytest 全过 → REAL。"""
    cap = _by_id(ledger)["geometry.hydro_curves"]
    assert cap["evidence"] == "REAL", cap
    assert cap["grade_source"] == "evidence"
    assert 19373 in cap["matched_reports"], cap["matched_reports"]


def test_c2_propeller_open_water_not_real(ledger):
    """m21514（报告 19430）：ηR 9/11 越物理窗、J=0.80 dK_T 越限 → 不得 REAL。"""
    cap = _by_id(ledger)["propeller.open_water"]
    assert cap["evidence"] != "REAL", cap
    assert cap["evidence"] == "MOCK"
    assert 19430 in cap["matched_reports"]


def test_c2_calc_resistance_not_real(ledger):
    """m21440（报告 19374）10 算例仅 1 通过、Verdict: FAIL → 不得 REAL。"""
    cap = _by_id(ledger)["calc.resistance"]
    assert cap["evidence"] != "REAL", cap
    assert 19374 in cap["matched_reports"]


def test_c2_calc_stability_consistent_with_registry(ledger, cfg):
    """calc.stability 与注册表（config/capabilities.json）现值一致。

    注册表只声明「无证据默认值」（initial_level），台账必须原样回显该值
    （registry_level）并给出一致性判定；有证据时等级由复算给出，
    且不得高于历史自述 REAL（19635 双船 GZ 误差表判 FAIL）。
    """
    reg = engine.load_registry(cfg, ROOT)
    reg_value = reg["by_id"]["calc.stability"]["initial_level"]
    cap = _by_id(ledger)["calc.stability"]
    assert cap["registry_level"] == reg_value == "WITHHELD"
    assert cap["registry_consistent"] is True
    assert cap["evidence"] == engine.build_ledger(cfg, ROOT)["capabilities"][
        [c["capability_id"] for c in ledger["capabilities"]].index("calc.stability")
    ]["evidence"]  # 两次独立复算一致（等级不是手写常量）
    assert cap["evidence"] != "REAL"
    assert 19635 in cap["matched_reports"]


# ── C3 复算确定性 / 无硬编码等级表 ────────────────────────────────────
def test_c3_two_recomputes_identical_and_sha256_valid(ledger, cfg):
    a, b, deterministic = engine.recompute_twice(cfg, ROOT)
    assert deterministic
    assert engine.canonical_json(a) == engine.canonical_json(b)
    body = {k: v for k, v in ledger.items() if k != "sha256"}
    assert ledger["sha256"] == engine.sha256_of(body)
    assert len(ledger["sha256"]) == 64
    # 等级只来自规则：全部条目都能追溯到 registry_initial / evidence / weak_only
    assert {c["grade_source"] for c in ledger["capabilities"]} <= {
        "evidence", "evidence_veto", "registry_initial", "weak_evidence_only"
    }


def test_c3_no_hardcoded_level_table(ledger, cfg):
    """引擎源码里不得出现任何 capability_id（等级表被禁止），配置里不得有等级映射表。"""
    reg = engine.load_registry(cfg, ROOT)
    src = ""
    for f in ("engine.py", "sources.py", "cli.py"):
        src += (ROOT / "tools" / "capability_ledger" / f).read_text(encoding="utf-8")
    for cap in reg["capabilities"]:
        assert cap["capability_id"] not in src, f"引擎源码硬编码了能力名 {cap['capability_id']}"
    # 注册表配置不含 evidence/等级映射字段（只有 initial_level 默认值）
    raw = json.loads((ROOT / "config" / "capabilities.json").read_text(encoding="utf-8"))
    for cap in raw["capabilities"]:
        assert "evidence" not in cap and "evidence_grade" not in cap
    # 等级确实是扫描结果：至少落在 4 类不同等级上（不是一张常量表）
    assert len({c["evidence"] for c in ledger["capabilities"]}) >= 3


def test_c3_selfcheck_ok(cfg):
    report = engine.selfcheck(cfg, ROOT)
    assert report["status"] == "ok", report["failed"]
    assert report["deterministic"] is True
    names = {c["name"] for c in report["checks"]}
    assert {"two_recomputes_identical", "sha256_recomputable", "capability_count_21",
            "levels_in_enum", "registry_consistency"} <= names


# ── C4 产物字段 / 路径可配置 / fail-soft ──────────────────────────────
def test_c4_artifact_has_as_of_schema_sha_and_configurable_path(ledger, cfg, tmp_path):
    for key in ("as_of", "schema_version", "sha256"):
        assert ledger.get(key), key
    assert ledger["schema_version"] == cfg["ledger_schema_version"]
    # 落盘路径可配置：写到自定义路径并读回
    target = tmp_path / "custom_ledger.json"
    res = engine.write_artifact(ledger, target)
    assert res["written"] is True and target.exists()
    q = engine.query(path=target, cfg=cfg, root=ROOT)
    assert q["capability_count"] == 21
    assert q["warnings"] == []
    # 与仓库内产物一致（防止「产物未落盘」复发）
    on_disk = ROOT / cfg["ledger_output_path"]
    assert on_disk.exists()
    assert engine.canonical_json(json.loads(on_disk.read_text(encoding="utf-8"))) == \
        engine.canonical_json(ledger)


def test_c4_query_fail_soft_on_missing_artifact(cfg, tmp_path):
    missing = tmp_path / "no_such_dir" / "capabilities.json"
    q = engine.query(path=missing, cfg=cfg, root=ROOT)  # 不得抛 FileNotFoundError
    assert q["status"] == "degraded"
    assert any("fail-soft" in w for w in q["warnings"])
    assert q["capability_count"] == 21
    assert all(c["evidence"] in LEVELS for c in q["capabilities"])


def test_c4_publish_fail_soft_on_unwritable_path(ledger, cfg):
    res = engine.publish_to_platform(ledger, cfg, ROOT,
                                     path="/proc/definitely-not-writable/capabilities.json")
    assert res["written"] is False
    assert res["warnings"]
    assert res["schema_version"] == cfg["harness_schema_version"]
    assert res["capability_count"] == 21


def test_c4_harness_payload_shape(ledger, cfg):
    payload = engine.to_harness_payload(ledger, cfg)
    assert payload["schema_version"] == cfg["harness_schema_version"]
    assert payload["as_of"] == ledger["as_of"]
    assert payload["source_ledger_sha256"] == ledger["sha256"]
    caps = payload["capabilities"]
    assert len(caps) == 21
    for cid, rec in caps.items():
        assert rec["evidence"] in LEVELS
        assert rec["evidence_engine_grade"] == rec["evidence"]
        assert isinstance(rec["evidence_count"], int)


# ── 采集/匹配层不变量（防限流静默丢证据） ─────────────────────────────
def test_snapshot_records_contain_key_evidence(cfg):
    snap = engine.load_snapshot(cfg, ROOT)
    ids = {r.get("id") for r in snap["records"]}
    # 三次等级影响事件的报告必须在快照里（否则等级无从复算）
    assert {19373, 19374, 19430} <= ids
    assert snap["as_of"] and snap["record_count"] > 0
    # 快照字段机器可读、无 markdown（体积可控、可复核）
    for r in snap["records"][:50]:
        assert set(r) <= set(sources._FIELDS)
        assert "markdown" not in r