"""W18-N09 能力台账证伪仪的回归测试。

覆盖三层：
  1. 判据层：三把刀在**已知真值**的合成序列上的判决（刀本身的单元测试 + 任务判据数字）；
  2. 仪器层：变异体自检（检出率 100% / 误报率 0%）——证明刀具不是恒真装置；
  3. 台账层：capabilities.json 覆盖 audit_capabilities 的 21 项（id 对称差为空）、
     必填字段齐全、混淆矩阵与判决文件自洽、判据数字落 report_data.json。

全部测试**离线**运行（不依赖 :8001 在线；需要的响应证据已由 run_audit.py 落盘到
artifacts/capability_falsification/probe_trace.json）。
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit.falsification import grade, knives, runner, selftest, spec  # noqa: E402

OUT = ROOT / spec.OUT_DIR_REL


# --------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def ledger():
    return json.loads((OUT / "capabilities.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def trace():
    return json.loads((OUT / "probe_trace.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def verdicts():
    return json.loads((OUT / "verdicts.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def matrix():
    return json.loads((OUT / "confusion_matrix.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def report():
    return json.loads((OUT / "report_data.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------- 1. 判据层
def test_mission_criteria_numbers_are_encoded():
    """任务判据数字必须写死在代码里：不变性 1e-12、解析误差 15%。"""
    assert knives.INVARIANCE_TOL == 1e-12
    assert knives.ANALYTIC_TOL == 0.15
    assert knives.IDENTITY_TOL < knives.ANALYTIC_TOL


def test_knife_a_kills_bitwise_invariant_output():
    samples = [{"label": "x1.0", "response": {"rt": 500.0}},
               {"label": "x0.7", "response": {"rt": 500.0}},
               {"label": "x1.3", "response": {"rt": 500.0}}]
    res = knives.perturbation_invariance(samples, require=["rt"])
    assert res["verdict"] == knives.KILL
    assert "rt" in res["invariant_keys"]


def test_knife_a_passes_on_responding_output():
    samples = [{"label": "x1.0", "response": {"rt": 500.0}},
               {"label": "x0.7", "response": {"rt": 260.0}},
               {"label": "x1.3", "response": {"rt": 830.0}}]
    assert knives.perturbation_invariance(samples, require=["rt"])["verdict"] == knives.PASS


def test_knife_a_does_not_kill_when_widened_sweep_moves():
    """舍入假阳性保护：主集不变、加宽集变化 → INCONCLUSIVE，不得判 MOCK。"""
    primary = [{"label": "a", "response": {"cf": 0.0016}},
               {"label": "b", "response": {"cf": 0.0016}},
               {"label": "c", "response": {"cf": 0.0016}}]
    confirm = [{"label": "d", "response": {"cf": 0.0016}},
               {"label": "e", "response": {"cf": 0.0019}}]
    res = knives.perturbation_invariance(primary, confirm, require=["cf"])
    assert res["verdict"] == knives.INCONCLUSIVE
    assert res["resolution_limit"]["key"] == "cf"


def test_knife_b_fifteen_percent_threshold():
    ok = knives.analytic_crosscheck([{"id": "c", "observed": 1.10, "reference": 1.0}])
    bad = knives.analytic_crosscheck([{"id": "c", "observed": 1.20, "reference": 1.0}])
    assert ok["verdict"] == knives.PASS
    assert bad["verdict"] == knives.KILL


def test_knife_b_identity_tolerance_is_tighter():
    res = knives.analytic_crosscheck([{"id": "ident", "observed": 1.01, "reference": 1.0,
                                       "kind": "identity"}])
    assert res["verdict"] == knives.KILL      # 1% 违反恒等式（容差 1e-3）


def test_knife_a2_determinism():
    """刀 A2：同一请求重复调用必须逐位相同；随机漂移判 MOCK。"""
    stable = [{"label": "r1", "response": {"rt": 500.0}},
              {"label": "r2", "response": {"rt": 500.0}},
              {"label": "r3", "response": {"rt": 500.0}}]
    noisy = [{"label": "r1", "response": {"rt": 500.0}},
             {"label": "r2", "response": {"rt": 505.0}},
             {"label": "r3", "response": {"rt": 497.0}}]
    assert knives.input_output_determinism(stable)["verdict"] == knives.PASS
    res = knives.input_output_determinism(noisy)
    assert res["verdict"] == knives.KILL
    assert res["nondeterministic_keys"] == ["rt"]


def test_knife_c_direction_and_stall():
    inc = [{"x": 1, "y": 1.0}, {"x": 2, "y": 2.0}, {"x": 3, "y": 3.0}]
    dip = [{"x": 1, "y": 1.0}, {"x": 2, "y": 0.9}, {"x": 3, "y": 3.0}]
    flat = [{"x": 1, "y": 1.0}, {"x": 2, "y": 1.0}, {"x": 3, "y": 1.0}]
    assert knives.monotonicity(inc, "increasing")["verdict"] == knives.PASS
    assert knives.monotonicity(dip, "increasing")["verdict"] == knives.KILL
    # 不变性归刀 A 判：刀 C 只记 STALL，不重复计罪
    assert knives.monotonicity(flat, "increasing")["verdict"] == knives.PASS
    assert knives.monotonicity(inc, "decreasing")["verdict"] == knives.KILL


def test_analytic_references_closed_form():
    # ITTC-57: Cf 随 Re 单调降
    assert knives.ittc57_cf(1e7) > knives.ittc57_cf(1e9)
    assert knives.ittc57_cf(knives.reynolds(7.202222, 100.0)) == pytest.approx(0.0016306, rel=1e-4)
    # 敞水效率恒等式 / 排水体积恒等式 / 初稳性恒等式
    assert knives.eta0_identity(0.4, 0.33724, 0.06469) == pytest.approx(0.33188, rel=2e-5)
    assert knives.box_volume(100, 20, 8, 0.6) == pytest.approx(9600.0)
    assert knives.gm_identity(4.4848, 5.0926, 8.0) == pytest.approx(1.5774, rel=1e-9)
    gz = knives.gz_wallsided_max(1.5774, 5.0926, phi_max_deg=35.0)
    assert 1.0 < gz["gz_max"] < 3.0


# --------------------------------------------------------------- 2. 仪器层
@pytest.fixture(scope="module")
def mutants():
    return selftest.run_mutants()


def test_mutant_selftest_detects_every_mutant(mutants):
    """设计预期：6 个变异体全部被检出（1.0），2 个阴性对照零误报。"""
    assert mutants["n_expected_flagged"] == 6
    assert mutants["n_detected"] == 6
    assert mutants["detection_rate"] == 1.0


def test_mutant_selftest_no_false_alarm(mutants):
    assert mutants["n_controls"] == 2
    assert mutants["n_control_false_alarm"] == 0
    assert mutants["false_alarm_rate"] == 0.0


def test_each_knife_has_kill_power(mutants):
    """四把刀（A / A2 / B / C）各自至少独立击杀一个变异体（防止某刀退化为恒真）。"""
    killed = {k for r in mutants["rows"] for k in r["killed_by"]}
    assert {"A", "A2", "B", "C"} <= killed


# --------------------------------------------------------------- 3. 台账层
def test_ledger_covers_audit_tool_ids_exactly(ledger):
    """单一真值台账必须与 audit_capabilities 的 21 项 id 集合**对称差为空**。"""
    resolved = json.loads((ROOT / spec.LEDGER_REL).read_text(encoding="utf-8"))
    tool_ids = {c["id"] for c in resolved["capabilities"]}
    snap_path = ROOT / spec.SNAPSHOT_REL
    ledger_ids = {c["id"] for c in ledger["capabilities"]}
    assert len(tool_ids) == 21
    assert ledger_ids ^ tool_ids == set()
    if snap_path.is_file():
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
        caps = snap.get("capabilities")
        snap_ids = set(caps) if isinstance(caps, dict) else {c["id"] for c in caps}
        assert ledger_ids ^ snap_ids == set()


def test_ledger_rows_have_required_fields(ledger):
    required = ("id", "name", "entrypoint", "evidence_grade", "evidence_ref", "callable_spec")
    assert ledger["n_capabilities"] == 21 == len(ledger["capabilities"])
    ids = [c["id"] for c in ledger["capabilities"]]
    assert len(ids) == len(set(ids)), "id 重复"
    for row in ledger["capabilities"]:
        for key in required:
            assert key in row, f"{row['id']} 缺字段 {key}"
        assert row["evidence_grade"] in {"REAL", "MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED"}
        json.dumps(row["callable_spec"])          # 必须可序列化（机器可执行前提）
        assert row["evidence_ref"]["ledger"] == spec.LEDGER_REL


def test_numeric_capabilities_have_executable_specs(ledger):
    numeric = [c for c in ledger["capabilities"]
               if c["callable_spec"].get("kind") == "http_json"]
    assert len(numeric) >= 5
    for c in numeric:
        sp = c["callable_spec"]
        assert sp["applicable_knives"] == ["A", "B", "C"]
        assert sp["perturbation_groups"] and sp["analytic_checks"] and sp["monotone_series"]
        for grp in sp["perturbation_groups"]:
            assert len(grp["samples"]) >= 3, f"{c['id']}/{grp['driver']} 主扰动集不足 3 点"
            assert len(grp.get("confirm") or []) >= 2, f"{c['id']}/{grp['driver']} 加宽确认集不足 2 点"


def test_ledger_geometry_perturbations_stay_in_physics_envelope(ledger):
    """任务判据：几何扰动必须落在 Cb∈[0.35,0.90]、L/B∈[3,12]、B/T∈[1.5,6.5] 内。"""
    checked = 0
    for row in ledger["capabilities"]:
        for body in [s["body"] for g in row["callable_spec"].get("perturbation_groups") or []
                     for s in g["samples"] + (g.get("confirm") or [])]:
            cb = body.get("cb", body.get("Cb"))
            L = body.get("length", body.get("Lpp"))
            B, T = body.get("beam", body.get("B")), body.get("draft", body.get("T"))
            if cb is not None:
                assert 0.35 <= cb <= 0.90 + 1e-9, (row["id"], cb)
                checked += 1
            if L and B and T:
                assert 3.0 - 1e-9 <= L / B <= 12.0 + 1e-9, (row["id"], L / B)
                assert 1.5 - 1e-9 <= B / T <= 6.5 + 1e-9, (row["id"], B / T)
    assert checked >= 10


def test_verdicts_cover_all_capabilities_and_matrix_is_consistent(verdicts, matrix):
    assert len(verdicts["rows"]) == 21
    assert {r["id"] for r in verdicts["rows"]} == {c["id"] for c in
                                                  spec.build_ledger()["capabilities"]}
    total = sum(sum(r.values()) for r in matrix["matrix"].values())
    assert total == 21
    recomputed = grade.confusion_matrix(verdicts)
    assert recomputed["matrix"] == matrix["matrix"]
    assert recomputed["overclaim_rate"] == matrix["overclaim_rate"]


def test_verdicts_are_reproducible_from_trace(trace, verdicts, ledger):
    """离线重放：从落盘 trace 重新判决必须逐条一致（判决不含随机性/在线状态）。"""
    replay = grade.grade_traces(ledger, {**trace, "capabilities": trace["capabilities"]})
    got = {r["id"]: r["falsified_grade"] for r in replay["rows"]}
    want = {r["id"]: r["falsified_grade"] for r in verdicts["rows"]}
    assert got == want


def test_known_findings_are_stable(trace):
    """钉住本单的实测结论：两个被推翻、两个确证 REAL（防止回归时静默漂移）。"""
    by = {t["capability"]: t for t in trace["capabilities"]}
    prop = {kr.get("series"): kr["verdict"] for kr in by["propeller.open_water"]["knife_results"]
            if kr["knife"] == "C"}
    assert prop["KQ_vs_J"] == knives.KILL          # 低 J 端 KQ 非单调（+2.1%）
    assert prop["KQ_vs_J_operating"] == knives.PASS  # 工作区 J∈[0.2,0.8] 单调
    hull_a = [kr for kr in by["shiphull.generate"]["knife_results"]
              if kr["knife"] == "A" and kr.get("driver") == "Cb"][0]
    assert hull_a["verdict"] == knives.KILL        # 湿表面积对 Cb 零敏感
    ai_a2 = [kr for kr in by["ai.predict"]["knife_results"] if kr["knife"] == "A2"][0]
    assert ai_a2["verdict"] == knives.KILL           # 同一请求重复调用输出漂移
    assert ai_a2["per_key"]["predicted_rt"]["max_rel_spread"] > knives.INVARIANCE_TOL
    res_b = [kr for kr in by["calc.resistance"]["knife_results"] if kr["knife"] == "B"][0]
    res_ids = {c["id"] for c in res_b["checks"]}
    assert {"ittc57_cf", "rf_identity", "cr_froude_similarity"} == res_ids
    assert all(c["verdict"] == knives.PASS for c in res_b["checks"])


def test_tamper_detection_gate(matrix, verdicts):
    """阴性对照：篡改一条判决，重算矩阵必须与原矩阵不一致（闸门非恒真）。"""
    bad = json.loads(json.dumps(verdicts))
    for row in bad["rows"]:
        if row["id"] == "propeller.open_water":
            row["falsified_grade"] = "REAL"
            break
    assert grade.confusion_matrix(bad)["matrix"] != matrix["matrix"]


def test_report_criteria_flags(report):
    c = report["criteria"]
    assert c["C1_ledger_single_truth"]["id_set_diff_empty"] is True
    assert c["C1_ledger_single_truth"]["n_ledger_capabilities"] == 21
    assert c["C1_ledger_single_truth"]["required_fields_present"] is True
    assert c["C2_three_knives_executable"]["all_three_exercised"] is True
    assert c["C2_three_knives_executable"]["knife_A2_repeatability"] >= 5
    assert c["C3_confusion_matrix"]["n_capabilities"] == 21
    assert c["C5_instrument_selftest"]["detection_rate"] == 1.0
    assert c["C5_instrument_selftest"]["false_alarm_rate"] == 0.0
    for key in ("ledger", "probe_trace", "verdicts", "confusion_matrix",
                "mutant_selftest", "report_data"):
        assert (ROOT / report["artifacts"][key]).is_file()


def test_ledger_renders_in_agent_tool(ledger):
    """工具层兼容：ops/tp_worker.py::_render_caps 必须能直接渲染本公司台账。

    （本会话 audit_capabilities 工具因硬编码路径失败；这里证明台账 schema 与工具
    `Capabilities = list` 分支兼容，且 _cap_sources 已把它列为兜底候选。）
    """
    import importlib.util
    tp = ROOT / "ops" / "tp_worker.py"
    spec_ = importlib.util.spec_from_file_location("tp_worker_under_test", tp)
    mod = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(mod)
    text = mod._render_caps(ledger)
    assert text.splitlines()[0].startswith("能力清单 (21 个")
    assert len(text.splitlines()) == 22
    cands = [p.relative_to(ROOT).as_posix() for p in mod._cap_sources()]
    assert "artifacts/capability_falsification/capabilities.json" in cands


def test_figures_exist_and_are_non_trivial(report):
    for key in ("fig_confusion_matrix", "fig_knife_profiles", "fig_mutant_selftest"):
        p = ROOT / report["artifacts"][key]
        assert p.is_file() and p.stat().st_size > 20000, key


def test_geometry_self_consistency_is_verified_first(trace):
    """纪律：几何必须先验证 Cb/L/B——每条阻力样本的 cb_from_dims 与排水自洽度都要被记录。"""
    t = [x for x in trace["capabilities"] if x["capability"] == "calc.resistance"][0]
    for rec in t["calls"]:
        e = rec["extracted"]
        if e.get("cb_from_dims") is None:
            continue
        body = rec["body"]
        assert e["cb_from_dims"] == pytest.approx(body["cb"], rel=1e-9)
        assert abs(e["displacement_consistency_pct"]) <= 5.0     # 平台 D3 门禁


def test_resistance_identity_units_are_kn(trace):
    """恒等式 rf = 0.5·ρV²S·Cf_total 的口径（kN）必须与平台一致，否则误判 MOCK。"""
    t = [x for x in trace["capabilities"] if x["capability"] == "calc.resistance"][0]
    base = [c for c in t["calls"] if c["label"] == "base"][0]
    e = base["extracted"]
    ref = 0.5 * 1025.0 * (14.0 * knives.KNOT_TO_MS) ** 2 * e["wetted_surface"] * e["cf_total"] / 1000.0
    assert ref == pytest.approx(e["rf"], rel=2e-3)
    assert e["cf_total"] > e["cf"]            # 粗糙度补贴 dcf 必须并入摩擦项