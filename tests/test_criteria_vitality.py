#!/usr/bin/env python3
"""``criteria_vitality`` 验收测试（全程合成数据，零领域词条 —— 平台铁律）。

判据（先于实现，机器可裁）：
  A1 变异体生成：每条判据 >=3 个变异体、>=2 类算子（默认 6 个 / 6 类全覆盖）；
  A2 杀死率语义：边界被证据钉住的判据 MS=1.0；阈值悬空的判据 MS<0.5 且封 REAL；
     判据自身判错样本 -> DEFECTIVE/BLOCKED，不给活力分数；
  A3 kill 矩阵完整性：判据 x 变异体逐格有裁决，缺格即违规；
  A4 survived 清单：每个存活变异体有合法原因码 + 数值明细 + 审查提示；
  A5 分数换算：gate_vitality == MS*100，逐判据 vitality_score == ms*100；
  A6 台账：写 vitality_score、未体检能力 real_claim_allowed=False、除体力字段外逐字节不动；
  A7 零领域语义：模块/演示/本测试经 ``tools.evidence_domain_scan`` 扫描 0 命中；
  A8 预注册链：变异库与门定义绑定（digest/gate_sha256），篡改或被改即拒跑。

运行：``python3 -m pytest tests/test_criteria_vitality.py -q``
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.taskplane import criteria_vitality as CV  # noqa: E402


# ── 合成夹具 ────────────────────────────────────────────────────────────
def echo_impl(case):
    """合成实现入口：y = 2x（不含任何领域语义）。"""
    x = float(case["x"])
    return {"m": {"x": x, "y": 2.0 * x}}


def _gate(gate_id, criteria, subjects, impl=None):
    gate = {"gate_id": gate_id, "criteria": criteria, "subjects": subjects}
    if impl is not None:
        gate["implementation"] = impl
    return CV.parse_gate(gate)


def _subjects(pairs):
    return [
        {"subject_id": sid, "case": {"x": value}, "expect": expect, "provenance": f"synth:{sid}"}
        for sid, value, expect in pairs
    ]


def vital_gate():
    """边界两侧各有近界样本 + 侧翼远界样本 => 判据参数被证据钉住。"""
    return _gate(
        "g.vital",
        [{"criterion_id": "c.hi", "observed": "/m/x", "op": ">=",
          "threshold": 10.0, "unit": "1"}],
        _subjects([("s1", 12.0, "PASS"), ("s2", 10.5, "PASS"),
                   ("s3", 9.5, "FAIL"), ("s4", 6.0, "FAIL")]),
    )


def inert_gate():
    """样本全在边界一侧（阈值悬空）=> 扰动不改变判决。"""
    return _gate(
        "g.inert",
        [{"criterion_id": "c.lo", "observed": "/m/x", "op": "<=",
          "threshold": 0.05, "unit": "1"}],
        _subjects([("t1", 0.0003, "PASS"), ("t2", 0.0009, "PASS"),
                   ("t3", 0.0015, "PASS"), ("t4", 0.08, "FAIL")]),
    )


def defective_gate():
    """判据自身漏写容差，把预注册 PASS 样本判成 FAIL => DEFECTIVE。"""
    return _gate(
        "g.defective",
        [{"criterion_id": "c.flag", "observed": "/m/x", "op": ">=",
          "threshold": 1.0, "unit": "1"}],
        _subjects([("u1", 1.0, "PASS"), ("u2", 0.98, "PASS"), ("u3", 0.5, "FAIL")]),
    )


def overfit_gate():
    """全部样本紧贴边界（距边界 < 10%）且 MS=1.0 => 过拟合嫌疑。"""
    return _gate(
        "g.overfit",
        [{"criterion_id": "c.tight", "observed": "/m/x", "op": ">=",
          "threshold": 10.0, "unit": "1"}],
        _subjects([("v1", 10.2, "PASS"), ("v2", 9.8, "FAIL"), ("v3", 10.4, "PASS")]),
    )


def zero_threshold_gate():
    """阈值为 0：阈值类变异退化（判据逐字段相同的变异体）。"""
    return _gate(
        "g.zero",
        [{"criterion_id": "c.zero", "observed": "/m/x", "op": ">=",
          "threshold": 0.0, "unit": "1"}],
        _subjects([("w1", 5.0, "PASS"), ("w2", -5.0, "FAIL")]),
    )


def deadband_gate():
    """相等算子 + 容差：阈值扰动被容差吃掉（DEADBAND_ABSORBED）。"""
    return _gate(
        "g.deadband",
        [{"criterion_id": "c.eq", "observed": "/m/x", "op": "==",
          "threshold": 10.0, "tolerance": 1.0, "unit": "1"}],
        _subjects([("d1", 10.5, "PASS"), ("d2", 5.0, "FAIL")]),
    )


@pytest.fixture()
def library_of():
    def _make(gate, stamp="2026-11-10T00:00:00+00:00"):
        return CV.build_library(gate, generated_at=stamp)

    return _make


# ── A1 变异体生成 ───────────────────────────────────────────────────────
def test_mutants_cover_all_operators_and_floor():
    """每条判据默认 6 个变异体、6 类算子（远超 A1 下界 3 个 / 2 类）。"""
    gate = vital_gate()
    mutants = CV.generate_mutants(gate["criteria"][0])
    assert len(mutants) == len(CV.OPERATORS) == 6
    assert {m.operator for m in mutants} == set(CV.OPERATORS)
    assert len(mutants) >= CV.MIN_MUTANTS_PER_CRITERION
    assert len({m.operator for m in mutants}) >= CV.MIN_OPERATOR_CLASSES


def test_mutant_ids_are_criterion_scoped_and_unique():
    gate = vital_gate()
    ids = [m.mutant_id for m in CV.generate_mutants(gate["criteria"][0])]
    assert len(ids) == len(set(ids))
    assert all(i.startswith("c.hi::") for i in ids)


def test_mutant_digests_are_stable_and_spec_sensitive():
    spec = vital_gate()["criteria"][0]
    a = {m.operator: m.spec.digest() for m in CV.generate_mutants(spec)}
    b = {m.operator: m.spec.digest() for m in CV.generate_mutants(spec)}
    assert a == b, "同规格必须逐位产生同一变异体"
    spec2 = spec.replace(threshold=11.0)
    c = {m.operator: m.spec.digest() for m in CV.generate_mutants(spec2)}
    assert a != c, "判据变了，变异体指纹必须变"


def test_threshold_and_offset_magnitudes_are_10_percent():
    spec = vital_gate()["criteria"][0]
    by_op = {m.operator: m.spec for m in CV.generate_mutants(spec)}
    assert by_op["THRESHOLD_PLUS_10"].threshold == pytest.approx(11.0)
    assert by_op["THRESHOLD_MINUS_10"].threshold == pytest.approx(9.0)
    assert by_op["OBSERVED_OFFSET_PLUS"].offset == pytest.approx(1.0)
    assert by_op["OBSERVED_OFFSET_MINUS"].offset == pytest.approx(-1.0)
    assert by_op["OP_FLIP"].op == "<="
    # 单位错位：dimensionless("1") 按百分数口径 x100
    assert by_op["UNIT_ERROR"].threshold == pytest.approx(1000.0)


def test_unknown_operator_rejected():
    with pytest.raises(CV.VitalityInputError):
        CV.generate_mutants(vital_gate()["criteria"][0], operators=["NO_SUCH_OP"])


# ── 判据规格 / 观测式校验 ───────────────────────────────────────────────
@pytest.mark.parametrize(
    "payload",
    [
        {"criterion_id": "c", "observed": "m/x", "op": ">=", "threshold": 1.0},
        {"criterion_id": "c", "observed": "/m/x", "op": ">>", "threshold": 1.0},
        {"criterion_id": "", "observed": "/m/x", "op": ">=", "threshold": 1.0},
        {"criterion_id": "c", "observed": "/m/x", "op": ">=", "threshold": float("nan")},
        {"criterion_id": "c", "observed": "/m/x", "op": ">="},
    ],
)
def test_criterion_spec_rejects_invalid(payload):
    with pytest.raises(CV.VitalityInputError):
        CV.CriterionSpec.from_dict(payload)


def test_resolve_observation_errors_are_input_errors():
    spec = CV.CriterionSpec(cid="c", observed="/m/missing", op=">=", threshold=1.0)
    with pytest.raises(CV.VitalityInputError):
        CV.resolve_observation({"m": {"x": 1.0}}, "/m/missing")
    with pytest.raises(CV.VitalityInputError):
        CV.resolve_observation({"m": {"x": "text"}}, "/m/x")
    with pytest.raises(CV.VitalityInputError):
        CV.resolve_observation({"m": {"x": float("inf")}}, "/m/x")
    with pytest.raises(CV.VitalityInputError):
        CV.resolve_observation({"m": {"x": 1.0}}, "m/x")
    assert CV.resolve_observation({"m": {"x": 2.5}}, "/m/x") == pytest.approx(2.5)
    # 指针转义与数组索引
    assert CV.resolve_observation({"a/b": [0, 7.0]}, "/a~1b/1") == pytest.approx(7.0)


@pytest.mark.parametrize("op,value,expected", [
    (">=", 10.0, True), (">", 10.0, False), ("<=", 10.0, True),
    ("<", 10.0, False), ("==", 10.0, True), ("!=", 10.0, False),
])
def test_compare_operator_table(op, value, expected):
    assert CV.compare(op, value, 10.0, 0.0) is expected


def test_tolerance_is_a_deadband():
    assert CV.compare("==", 10.5, 10.0, 1.0) is True
    assert CV.compare("==", 12.0, 10.0, 1.0) is False
    assert CV.compare(">=", 9.5, 10.0, 1.0) is True


# ── A8 预注册链 ─────────────────────────────────────────────────────────
def test_library_preregistration_roundtrip(tmp_path):
    gate = vital_gate()
    lib = CV.build_library(gate, generated_at="2026-11-10T00:00:00+00:00")
    assert CV.verify_library(gate, lib) == []
    path = CV.write_library(lib, tmp_path / "lib.json")
    loaded = CV.load_json(path)
    assert CV.verify_library(gate, loaded) == []
    assert loaded["coverage"]["coverage_ok"] is True
    assert loaded["coverage"]["mutants_per_criterion_min"] >= CV.MIN_MUTANTS_PER_CRITERION


def test_library_detects_mutant_tampering(library_of):
    gate = vital_gate()
    # a) 篡改 mutated 载荷但保留旧 digest -> 载荷与指纹不自洽
    lib_a = library_of(gate)
    lib_a["mutants"][0]["mutated"]["threshold"] = 123.0
    assert any("与其 digest 不符" in p for p in CV.verify_library(gate, lib_a))
    # b) 同时改写 digest 与载荷 -> 与门定义重算结果不符
    lib_b = library_of(gate)
    forged_spec = CV.CriterionSpec.from_dict(lib_b["mutants"][0]["mutated"]).replace(
        threshold=123.0)
    lib_b["mutants"][0]["mutated"] = forged_spec.to_dict()
    lib_b["mutants"][0]["digest"] = forged_spec.digest()
    assert any("篡改" in p for p in CV.verify_library(gate, lib_b))


def test_library_detects_gate_edit_after_preregistration(library_of):
    gate = vital_gate()
    lib = library_of(gate)
    edited = _gate(
        "g.vital",
        [{"criterion_id": "c.hi", "observed": "/m/x", "op": ">=",
          "threshold": 10.5, "unit": "1"}],
        gate["subjects"],
    )
    problems = CV.verify_library(edited, lib)
    assert any("预注册后被改动" in p for p in problems)


def test_library_detects_missing_and_extra_mutants(library_of):
    gate = vital_gate()
    lib = library_of(gate)
    dropped = json.loads(json.dumps(lib))
    dropped["mutants"] = dropped["mutants"][:-1]
    assert any("缺注册变异体" in p for p in CV.verify_library(gate, dropped))

    extra = json.loads(json.dumps(lib))
    extra["mutants"].append({**extra["mutants"][0], "mutant_id": "c.hi::FORGED"})
    assert any("未授权变异体" in p for p in CV.verify_library(gate, extra))


def test_run_gate_refuses_mismatched_library(library_of):
    gate = vital_gate()
    lib = library_of(gate)
    lib["gate_sha256"] = "0" * 64
    with pytest.raises(CV.VitalityInputError):
        CV.run_gate(gate, echo_impl, lib)


# ── A2 杀死率语义 ───────────────────────────────────────────────────────
def test_vital_gate_kills_every_mutant(library_of):
    gate = vital_gate()
    result = CV.run_gate(gate, echo_impl, library_of(gate))
    assert result["criteria"][0]["base_accuracy"] == 1.0
    assert result["criteria"][0]["killed"] == result["criteria"][0]["mutants_total"] == 6
    assert result["ms"] == pytest.approx(1.0)
    assert result["verdict"] == CV.VERDICT_VITAL
    assert result["real_claim_allowed"] is True
    assert result["blockers"] == []


def test_inert_gate_blocks_real_claim(library_of):
    gate = inert_gate()
    result = CV.run_gate(gate, echo_impl, library_of(gate))
    row = result["criteria"][0]
    assert row["ms"] < CV.MS_FLOOR
    assert result["verdict"] == CV.VERDICT_INERT
    assert result["real_claim_allowed"] is False
    assert any("达标线" in b for b in result["blockers"])
    reasons = {item["reason_code"] for item in result["survived"]}
    assert "SLACK_EXCEEDS_PERTURBATION" in reasons


def test_defective_criterion_blocks_gate(library_of):
    gate = defective_gate()
    result = CV.run_gate(gate, echo_impl, library_of(gate))
    row = result["criteria"][0]
    assert row["status"] == CV.STATUS_DEFECTIVE
    assert row["base_accuracy"] < 1.0
    assert row["base_mismatches"]
    assert result["verdict"] == CV.VERDICT_BLOCKED
    assert result["real_claim_allowed"] is False
    assert any("判据自身不可用" in b for b in result["blockers"])
    # 缺陷判据下的「存活」必须被标成缺陷副产物，而不是活力证据
    reasons = {item["reason_code"] for item in result["survived"]}
    assert "DEFECTIVE_BASE_MASKED" in reasons


def test_zero_threshold_mutant_is_degenerate(library_of):
    gate = zero_threshold_gate()
    result = CV.run_gate(gate, echo_impl, library_of(gate))
    by_id = {item["mutant_id"]: item for item in result["survived"]}
    assert by_id["c.zero::THRESHOLD_PLUS_10"]["reason_code"] == "DEGENERATE_MUTANT"
    assert by_id["c.zero::THRESHOLD_MINUS_10"]["reason_code"] == "DEGENERATE_MUTANT"


def test_deadband_absorbs_threshold_perturbation(library_of):
    """容差死区把 +10% 阈值扰动吃掉：判决不变 => 存活，且原因码必须是 DEADBAND_ABSORBED。"""
    gate = deadband_gate()
    result = CV.run_gate(gate, echo_impl, library_of(gate))
    codes = {item["mutant_id"]: item["reason_code"] for item in result["survived"]}
    assert codes["c.eq::THRESHOLD_PLUS_10"] == "DEADBAND_ABSORBED"
    cells = result["kill_matrix"]["c.eq"]
    assert cells["c.eq::THRESHOLD_MINUS_10"]["result"] == "KILLED"
    assert cells["c.eq::THRESHOLD_PLUS_10"]["requires_review"] is True


def test_overfit_diagnostic_flags_knife_edge_samples(library_of):
    gate = overfit_gate()
    result = CV.run_gate(gate, echo_impl, library_of(gate))
    assert result["ms"] == pytest.approx(1.0)
    assert result["diagnostics"]["overfit_suspect"] is True
    assert result["diagnostics"]["overfit_note"]


def test_missing_observation_is_input_error(library_of):
    gate = _gate(
        "g.bad-pointer",
        [{"criterion_id": "c.bad", "observed": "/m/absent", "op": ">=",
          "threshold": 1.0, "unit": "1"}],
        _subjects([("z1", 1.0, "PASS")]),
    )
    with pytest.raises(CV.VitalityInputError):
        CV.run_gate(gate, echo_impl, library_of(gate))


def test_implementation_errors_are_input_errors(library_of):
    gate = vital_gate()

    def boom(case):
        raise RuntimeError("引擎崩了")

    with pytest.raises(CV.VitalityInputError):
        CV.run_gate(gate, boom, library_of(gate))
    with pytest.raises(CV.VitalityInputError):
        CV.run_gate(gate, lambda case: 42, library_of(gate))
    with pytest.raises(CV.VitalityInputError):
        CV.run_gate(gate, "no_such_module:fn", library_of(gate))


def test_module_function_implementation_resolution(library_of):
    fn = CV.resolve_implementation(
        "ops.taskplane.criteria_vitality_demo:synthetic_observations")
    assert callable(fn)
    doc = fn({"num": 95, "den": 100, "length": 4.5, "drift": 0.0004, "flag": 1.5})
    assert doc["metrics"]["ratio"] == pytest.approx(0.95)
    assert doc["metrics"]["span"] == pytest.approx(9.0)
    with pytest.raises(CV.VitalityInputError):
        CV.resolve_implementation("ops.taskplane.criteria_vitality:no_such_function")
    with pytest.raises(CV.VitalityInputError):
        CV.resolve_implementation("no_such_module_xyz:fn")
    # 实现吃 num/den/... 而本门 case 只有 x => 必须报输入错误（不是判据不合格）
    with pytest.raises(CV.VitalityInputError):
        CV.run_gate(vital_gate(),
                    "ops.taskplane.criteria_vitality_demo:synthetic_observations",
                    library_of(vital_gate()))


# ── A3 矩阵完整性 ───────────────────────────────────────────────────────
def test_kill_matrix_has_no_missing_cell(library_of):
    gate = vital_gate()
    lib = library_of(gate)
    result = CV.run_gate(gate, echo_impl, lib)
    registered = [m["mutant_id"] for m in lib["mutants"]]
    cells = result["kill_matrix"]["c.hi"]
    assert set(cells) == set(registered)
    assert all(cells[mid]["result"] in ("KILLED", "SURVIVED") for mid in registered)
    assert result["matrix_stats"]["actual_cells"] == result["matrix_stats"]["expected_cells"]
    assert result["matrix_stats"]["complete"] is True
    run = CV.run_bundle([gate], {"gates": {"g.vital": lib}},
                        implementations={"g.vital": echo_impl})
    ok, violations = CV.check_run(run)
    assert ok, violations


# ── A4 survived 清单 ────────────────────────────────────────────────────
def test_survived_items_are_fully_annotated(library_of):
    gate = inert_gate()
    result = CV.run_gate(gate, echo_impl, library_of(gate))
    assert result["survived"], "阈值悬空的判据必然有存活变异体"
    for item in result["survived"]:
        assert item["reason_code"] in CV.SURVIVAL_REASONS
        assert item["detail"].strip()
        assert item["review_hint"].strip()
        assert item["reason_text"] == CV.SURVIVAL_REASONS[item["reason_code"]]


def test_survived_reason_detail_carries_numbers(library_of):
    gate = inert_gate()
    result = CV.run_gate(gate, echo_impl, library_of(gate))
    detail = result["survived"][0]["detail"]
    assert any(ch.isdigit() for ch in detail), f"存活原因必须带数值明细：{detail}"


# ── A5 分数换算 + 机裁 ──────────────────────────────────────────────────
def test_scores_are_ms_times_hundred(library_of):
    run = CV.run_bundle(
        [vital_gate(), inert_gate(), defective_gate()],
        {"gates": {
            "g.vital": CV.build_library(vital_gate()),
            "g.inert": CV.build_library(inert_gate()),
            "g.defective": CV.build_library(defective_gate()),
        }},
        implementations={"g.vital": echo_impl, "g.inert": echo_impl,
                         "g.defective": echo_impl},
    )
    for gate in run["gates"]:
        assert gate["gate_vitality"] == pytest.approx(gate["ms"] * 100.0, abs=1e-6)
        for row in gate["criteria"]:
            assert row["vitality_score"] == pytest.approx(row["ms"] * 100.0, abs=1e-6)
    ok, violations = CV.check_run(run)
    assert ok, violations


def test_check_run_catches_forged_real_claim(library_of):
    gate = inert_gate()
    run = CV.run_bundle([gate], {"gates": {"g.inert": library_of(gate)}},
                        implementations={"g.inert": echo_impl})
    run["gates"][0]["real_claim_allowed"] = True  # 伪造：MS<floor 却自报 REAL
    ok, violations = CV.check_run(run)
    assert ok is False
    assert any("A2" in v for v in violations)


def test_check_run_catches_incomplete_matrix(library_of):
    gate = vital_gate()
    run = CV.run_bundle([gate], {"gates": {"g.vital": library_of(gate)}},
                        implementations={"g.vital": echo_impl})
    victim = next(iter(run["gates"][0]["kill_matrix"]["c.hi"]))
    run["gates"][0]["kill_matrix"]["c.hi"][victim] = None
    run["gates"][0]["matrix_stats"]["actual_cells"] -= 1
    ok, violations = CV.check_run(run)
    assert ok is False
    assert any(v.startswith("A3") for v in violations)


def test_run_is_deterministic(library_of):
    gate = vital_gate()
    lib = library_of(gate)
    first = CV.run_bundle([gate], {"gates": {"g.vital": lib}},
                          implementations={"g.vital": echo_impl},
                          generated_at="2026-11-10T00:00:00+00:00")
    second = CV.run_bundle([gate], {"gates": {"g.vital": lib}},
                           implementations={"g.vital": echo_impl},
                           generated_at="2026-11-10T00:00:00+00:00")
    assert CV.canonical_json(first) == CV.canonical_json(second)


# ── A6 台账入账 ─────────────────────────────────────────────────────────
def _synthetic_ledger():
    return {
        "schema_version": "2.0",
        "title": "synthetic ledger",
        "level_rank": {"NOT_SUPPORTED": 0, "STUB": 1, "MOCK": 2, "WITHHELD": 3, "REAL": 4},
        "capabilities": [
            {"id": "synth.alpha", "claimed_level": "REAL", "evidence": [], "note": "n1"},
            {"id": "synth.beta", "claimed_level": "REAL", "evidence": [], "note": "n2"},
        ],
    }


def test_ledger_stamp_writes_vitality_score(tmp_path, library_of):
    gate = vital_gate()
    run = CV.run_bundle([gate], {"gates": {"g.vital": library_of(gate)}},
                        implementations={"g.vital": echo_impl})
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(_synthetic_ledger()), encoding="utf-8")

    block, updated, before = CV.stamp_capability_ledger(
        run, ledger_path, capability_map={"g.vital": ["synth.alpha"]},
        embed_capability_fields=True)

    caps = {cap["id"]: cap for cap in updated["capabilities"]}
    assert caps["synth.alpha"]["vitality_score"] == pytest.approx(100.0)
    assert caps["synth.alpha"]["vitality_status"] == CV.STATUS_EXAMINED
    assert caps["synth.alpha"]["vitality_real_claim_allowed"] is True
    # 未体检能力：不得自报 REAL
    assert caps["synth.beta"]["vitality_score"] is None
    assert caps["synth.beta"]["vitality_status"] == CV.STATUS_UNEXAMINED
    assert caps["synth.beta"]["vitality_real_claim_allowed"] is False
    assert block["summary"]["capabilities_examined"] == 1
    assert block["summary"]["capabilities_unexamined"] == 1
    assert block["gates"]["g.vital"]["gate_vitality"] == pytest.approx(100.0)
    # 写盘后其余字段逐字节不动
    on_disk = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert on_disk["title"] == "synthetic ledger"
    assert before == CV._vitality_stripped_fingerprint(on_disk)


def test_ledger_stamp_blocks_real_for_inert_gate(tmp_path, library_of):
    gate = inert_gate()
    run = CV.run_bundle([gate], {"gates": {"g.inert": library_of(gate)}},
                        implementations={"g.inert": echo_impl})
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(_synthetic_ledger()), encoding="utf-8")
    block, updated, _ = CV.stamp_capability_ledger(
        run, ledger_path, capability_map={"g.inert": ["synth.beta"]})
    cap = next(c for c in updated["capabilities"] if c["id"] == "synth.beta")
    assert block["capabilities"]["synth.beta"]["real_claim_allowed"] is False
    assert block["capabilities"]["synth.beta"]["vitality_score"] < CV.MS_FLOOR * 100.0
    assert "不得自报 REAL" in block["capabilities"]["synth.beta"]["reason"]
    assert cap.get("vitality_score") is None  # 未开 embed 时不改原条目


def test_ledger_stamp_is_idempotent(tmp_path, library_of):
    gate = vital_gate()
    run = CV.run_bundle([gate], {"gates": {"g.vital": library_of(gate)}},
                        implementations={"g.vital": echo_impl},
                        generated_at="2026-11-10T00:00:00+00:00")
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(_synthetic_ledger()), encoding="utf-8")
    CV.stamp_capability_ledger(run, ledger_path, embed_capability_fields=True)
    first = ledger_path.read_text(encoding="utf-8")
    CV.stamp_capability_ledger(run, ledger_path, embed_capability_fields=True)
    assert ledger_path.read_text(encoding="utf-8") == first


# ── CLI 退出码 ──────────────────────────────────────────────────────────
def _cli_gate_dict():
    return {
        "schema": CV.SCHEMA_GATE,
        "gate_id": "cli.gate",
        "implementation": {"ref": "ops.taskplane.criteria_vitality_demo:synthetic_observations"},
        "criteria": [{"criterion_id": "cli.ratio", "observed": "/metrics/ratio",
                      "op": ">=", "threshold": 0.8, "unit": "1"}],
        "subjects": [
            {"subject_id": "c1", "case": {"num": 95, "den": 100, "length": 4.5,
                                          "drift": 0.0004, "flag": 1.5}, "expect": "PASS"},
            {"subject_id": "c2", "case": {"num": 83, "den": 100, "length": 5.75,
                                          "drift": 0.0011, "flag": 1.2}, "expect": "PASS"},
            {"subject_id": "c3", "case": {"num": 87, "den": 100, "length": 6.4,
                                          "drift": 0.0008, "flag": 0.9,
                                          "inject": "ratio_bias"}, "expect": "FAIL"},
        ],
    }


def test_cli_end_to_end_exit_codes(tmp_path):
    gate_path = tmp_path / "gate.json"
    gate_path.write_text(json.dumps({"gates": [_cli_gate_dict()]}), encoding="utf-8")
    lib_path = tmp_path / "lib.json"
    run_path = tmp_path / "run.json"
    md_path = tmp_path / "kill.md"

    assert CV.main(["preregister", "--gate", str(gate_path), "--out", str(lib_path)]) == 0
    assert lib_path.exists()
    assert CV.main(["run", "--gate", str(gate_path), "--library", str(lib_path),
                    "--out", str(run_path), "--markdown", str(md_path)]) == 0
    assert run_path.exists() and md_path.exists()
    assert CV.main(["check", "--run", str(run_path)]) == 0

    # 篡改运行记录（伪造 REAL 自报）-> check 必须返回 2
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["gates"][0]["ms"] = 0.2  # 伪造：MS 掉到达标线下但裁决/自报不动
    run["gates"][0]["real_claim_allowed"] = True
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps(run), encoding="utf-8")
    assert CV.main(["check", "--run", str(forged)]) == 2

    # 输入缺失 -> 3
    assert CV.main(["check", "--run", str(tmp_path / "absent.json")]) == 3
    assert CV.main(["preregister", "--gate", str(tmp_path / "absent.json"),
                    "--out", str(tmp_path / "x.json")]) == 3


# ── 报告渲染 ────────────────────────────────────────────────────────────
def test_markdown_report_contains_matrix_and_survivors(library_of):
    run = CV.run_bundle([inert_gate()], {"gates": {"g.inert": library_of(inert_gate())}},
                        implementations={"g.inert": echo_impl})
    md = CV.render_markdown(run)
    assert "kill 矩阵" in md
    assert "survived 清单" in md
    assert "g.inert" in md
    assert "SLACK_EXCEEDS_PERTURBATION" in md
    assert "gate_vitality" in md


# ── A7 零领域语义 ───────────────────────────────────────────────────────
def test_zero_domain_semantics_in_mechanism_sources():
    from tools.evidence_domain_scan import load_lexicon, scan_paths

    result = scan_paths(
        ["ops/taskplane/criteria_vitality.py",
         "ops/taskplane/criteria_vitality_demo.py",
         "tests/test_criteria_vitality.py"],
        lexicon=load_lexicon(),
    )
    assert result["hits"] == [], result["hits"]
    assert result["scanned_files"] >= 3


def test_operators_do_not_depend_on_domain_knowledge():
    """变异算子只依赖判据字段（阈值/算子/单位/偏移），不引入任何领域参数。"""
    spec = CV.CriterionSpec(cid="c", observed="/m/x", op="<=", threshold=7.5, unit="s")
    mutants = {m.operator: m.spec for m in CV.generate_mutants(spec)}
    for op, mutated in mutants.items():
        assert mutated.observed == spec.observed
        assert mutated.unit == spec.unit
    assert mutants["UNIT_ERROR"].threshold == pytest.approx(7.5 / 3600.0)
    assert set(CV.UNIT_LADDER) <= {
        "m", "s", "g", "kg", "N", "J", "W", "Pa", "Hz", "rad", "deg", "1"
    }


def test_ledger_stamp_preserves_existing_serialization(tmp_path, library_of):
    """入账必须沿用台账既有的缩进惯例：除追加行外，原有每一行都在原位。"""
    ledger = _synthetic_ledger()
    original = json.dumps(ledger, ensure_ascii=False, indent=CV.LEDGER_JSON_INDENT) + "\n"
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(original, encoding="utf-8")

    gate = vital_gate()
    run = CV.run_bundle([gate], {"gates": {"g.vital": library_of(gate)}},
                        implementations={"g.vital": echo_impl})
    CV.stamp_capability_ledger(run, ledger_path, embed_capability_fields=True)

    stamped = ledger_path.read_text(encoding="utf-8")
    old_lines = original.splitlines()
    new_lines = stamped.splitlines()
    cursor = 0
    for line in old_lines:
        while cursor < len(new_lines) and new_lines[cursor] != line and \
                new_lines[cursor] != line + ",":
            cursor += 1
        assert cursor < len(new_lines), f"原有行被改写或丢失：{line!r}"
        cursor += 1
    assert stamped.endswith("\n")


def test_cli_exits_two_when_gate_is_not_vital(tmp_path):
    """单门 MS < 达标线 => CLI run/check 必须 exit 2（不得 0 绿）。"""
    gate = {
        "schema": CV.SCHEMA_GATE,
        "gate_id": "cli.inert",
        "implementation": {"ref": "ops.taskplane.criteria_vitality_demo:synthetic_observations"},
        "criteria": [{"criterion_id": "cli.drift", "observed": "/metrics/drift",
                      "op": "<=", "threshold": 0.05, "unit": "1"}],
        "subjects": [
            {"subject_id": "n1", "case": {"num": 95, "den": 100, "length": 4.5,
                                          "drift": 0.0003, "flag": 1.5}, "expect": "PASS"},
            {"subject_id": "n2", "case": {"num": 95, "den": 100, "length": 4.5,
                                          "drift": 0.0800, "flag": 1.5}, "expect": "FAIL"},
        ],
    }
    gate_path = tmp_path / "gate.json"
    gate_path.write_text(json.dumps(gate), encoding="utf-8")
    lib_path = tmp_path / "lib.json"
    run_path = tmp_path / "run.json"
    assert CV.main(["preregister", "--gate", str(gate_path), "--out", str(lib_path)]) == 0
    assert CV.main(["run", "--gate", str(gate_path), "--library", str(lib_path),
                    "--out", str(run_path)]) == 2
    assert CV.main(["check", "--run", str(run_path)]) == 2
