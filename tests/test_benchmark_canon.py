"""m1561 六分支裁决的正典回归测试。

判据不是"文件里写着什么"，而是**解析恒等式**：每个断言都独立重算一遍再比对，
所以文件被改坏、或密度被换回海水，测试就会红。

运行（沙箱缺 cryptography，无法加载 backend/tests/conftest.py）：

    PYTHONPATH=backend python3 -m pytest tests/test_benchmark_canon.py --noconftest -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services import benchmark_canon as bc
from services import benchmark_dataset_audit as legacy_audit
from services import benchmark_integrity as bi

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "backend" / "data" / "benchmark_reference.json"
SOURCES = REPO / "backend" / "data" / "benchmark_reference_sources_v2.json"
SUPERSEDED = REPO / "docs" / "superseded"


def _canon_doc():
    return json.loads(DATA.read_text(encoding="utf-8"))


def _cases():
    return {c["case_id"]: c for c in _canon_doc()["cases"]}


# --- R1/R2：阻力恒等式 + 密度溯源（SUBOFF 争议的根）------------------------

def test_suboff01_rt_under_fresh_water():
    """SUBOFF-01 在淡水 rho=998.2 下 Rt = Ct*0.5*rho*V^2*S = 92.52 N。"""
    rt = bc.derive_rt(0.0038, 3.0, 5.42, bc.FLUIDS["fresh_water_20c"]["rho"])
    assert rt == pytest.approx(92.52, abs=0.01)


def test_suboff01_sea_water_value_is_the_superseded_one():
    """基线 v2 的 95.0 N 恰好等于海水密度复算值；两者差 2.68%，不是 7.6 倍。"""
    fresh = bc.derive_rt(0.0038, 3.0, 5.42, bc.FLUIDS["fresh_water_20c"]["rho"])
    sea = bc.derive_rt(0.0038, 3.0, 5.42, bc.FLUIDS["sea_water_15c"]["rho"])
    assert sea == pytest.approx(95.0, abs=0.05)
    assert bc.rel_error(sea, fresh) == pytest.approx(1025.0 / 998.2 - 1.0, rel=1e-6)
    assert bc.rel_error(sea, fresh) < 0.03


def test_v1_value_12_5N_is_rejected_by_identity():
    """v1 的 12.5 N 反解湿面积只有 0.73 m2，是记录值 5.42 m2 的 13%，物理不可能。"""
    s_imp = bc.implied_wetted_surface(0.0038, 3.0, 12.5, bc.FLUIDS["fresh_water_20c"]["rho"])
    assert s_imp == pytest.approx(0.73, abs=0.01)
    assert s_imp / 5.42 < 0.2
    assert bc.derive_rt(0.0038, 3.0, 5.42, 998.2) / 12.5 == pytest.approx(7.40, abs=0.05)


def test_all_suboff_rows_close_under_fresh_water():
    cases = _cases()
    for case_id in ("SUBOFF-01", "SUBOFF-02", "SUBOFF-03", "SUBOFF-04"):
        c = cases[case_id]
        rho = c["reference_density_kg_m3"]
        rt = bc.derive_rt(c["experimental_ct"], c["speed_ms"],
                          c["geometry"]["wetted_surface_m2"], rho)
        assert c["experimental_rt_n"] == pytest.approx(rt, abs=0.005), case_id
        assert rho == 998.2, case_id


def test_fluid_follows_scale_and_explicit_field():
    assert bc.fluid_for_case({"scale": "model"})[1] == 998.2
    assert bc.fluid_for_case({"scale": "full"})[1] == 1025.0
    assert bc.fluid_for_case({"scale": "model", "reference_fluid": "sea_water_15c"})[1] == 1025.0


def test_platform_physics_core_uses_fresh_water():
    """平台物理核默认介质是淡水——参考数据若用海水密度就是契约漂移。"""
    from services.ship_resistance import DEFAULT_WATER_DENSITY
    assert DEFAULT_WATER_DENSITY == pytest.approx(998.0, abs=0.5)


# --- R3：DTMB-5415 的 Fr/航速互不自洽 ---------------------------------------

def test_dtmb_filed_speed_gives_impossible_froude():
    """10.289 m/s（20 kn 全尺度）配 L=5.72 m 模型 -> Fr=1.37，物理不可能。"""
    fr = bc.derive_froude(10.289, 5.72)
    assert fr == pytest.approx(1.3735, abs=1e-3)
    assert fr > 1.0


def test_dtmb_filed_froude_implies_181m_hull():
    """记录 Fr=0.244 的隐含船长是 181 m，不是数据集声明的 5.72 m。"""
    assert bc.implied_length(10.289, 0.244) == pytest.approx(181.3, abs=0.5)


def test_dtmb_withheld_not_fabricated():
    cases = _cases()
    for case_id in ("DTMB5415-01", "DTMB5415-02", "DTMB5415-03", "DTMB5415-04"):
        c = cases[case_id]
        assert c["experimental_rt_n"] is None
        assert c["rt_status"] == "WITHHELD"
        assert c["froude_status"] == "WITHHELD"
        assert c["reference_status"] == "WITHHELD"
        assert "froude_provenance_withheld" in c["audit_flags"]


# --- R4：排水量闭合 ---------------------------------------------------------

def test_dtmb_filed_displacement_fails_closure():
    derived_kg = 0.508 * 5.72 * 0.759 * 0.246 * 998.2
    assert derived_kg == pytest.approx(541.6, abs=0.5)
    assert bc.rel_error(derived_kg, 466.3) > 0.15


def test_kvlcc2_displacement_derived_from_geometry():
    derived_t = 0.8098 * 320.0 * 58.0 * 20.8 * 1025.0 / 1000.0
    assert derived_t == pytest.approx(320437.2, abs=1.0)
    assert bc.rel_error(derived_t, 307000.0) == pytest.approx(0.0438, abs=1e-3)


def test_canon_displacements_are_the_derived_ones():
    cases = _cases()
    assert cases["DTMB5415-03"]["geometry"]["displacement_kg"] == pytest.approx(541.6, abs=0.2)
    assert cases["KVLCC2-03"]["geometry"]["displacement_t"] == pytest.approx(320437.2, abs=0.2)


# --- R5：摩擦下限 -----------------------------------------------------------

def test_every_canon_row_sits_above_friction_line():
    for c in _canon_doc()["cases"]:
        assert c["reference_density_kg_m3"] in (998.2, 1025.0)
        re = bc.reynolds_number(c["speed_ms"], c["geometry"]["length_m"],
                                c["reference_kinematic_viscosity_m2_s"])
        assert c["experimental_ct"] >= bc.ittc57_cf(re), c["case_id"]


# --- 正典文件本身 ----------------------------------------------------------

def test_canon_file_is_reproducible_from_frozen_sources():
    ok, problems = bc.check_canon(DATA)
    assert ok, problems


def test_canon_document_is_deterministic():
    assert bc.canon_json() == DATA.read_text(encoding="utf-8")


def test_frozen_sources_are_the_v2_baseline():
    assert json.loads(SOURCES.read_text(encoding="utf-8"))["version"] == "2.0"


def test_canon_self_audit_has_no_rejected_row():
    audit = bc.adjudicate_dataset(_canon_doc(), competitors={})
    assert audit["summary"]["REJECTED"] == 0
    verdicts = {r["case_id"]: r["verdict"] for r in audit["cases"]}
    assert sum(1 for v in verdicts.values() if v == bc.DERIVED) == 4
    assert all(verdicts["SUBOFF-0%d" % i] == bc.DERIVED for i in range(1, 5))


def test_canon_gate_agrees_with_integrity_engine():
    """正典引擎与闸门引擎不得互相对立：闸门 PASS 的行必须是 DERIVED 行。"""
    report = bi.audit_dataset(DATA)
    assert report["verdict_counts"][bi.FAIL] == 0
    for row in report["rows"]:
        if row["verdict"] == bi.PASS:
            assert _cases()[row["case_id"]]["reference_status"] == "DERIVED", row["case_id"]


# --- 三栏对照表（基线 / m1395 / 裁决）与降级 facade -------------------------

def test_three_column_table_covers_every_case_and_quantity():
    table = bc.competitor_table()
    assert len(table) == len(_canon_doc()["cases"]) * 4
    suboff = [r for r in table if r["case_id"] == "SUBOFF-01"
              and r["quantity"] == "experimental_rt_n"][0]
    assert suboff["canon"] == pytest.approx(92.52, abs=0.01)
    assert suboff["basis"]
    if suboff["baseline_v2"] is not None:
        assert suboff["baseline_v2"] == pytest.approx(95.0, abs=0.05)
    if suboff["m1395"] is not None:
        assert suboff["m1395"] == pytest.approx(suboff["canon"], rel=1e-4)


def test_deprecated_facade_delegates_to_canon_gate():
    canon = legacy_audit.load_and_assess(_canon_doc())
    assert canon["status_counts"][legacy_audit.CONSISTENT] == 4
    assert canon["verifiable_accuracy_claim"] is True
    assert canon["delegated_to"] == "services.benchmark_integrity"
    before = legacy_audit.load_and_assess(json.loads(SOURCES.read_text(encoding="utf-8")))
    assert before["verified_cases"] == 0
    assert before["verifiable_accuracy_claim"] is False


def test_superseded_notes_exist_for_every_losing_branch():
    assert (SUPERSEDED / "README.md").exists()
    for branch in ("m1395", "m1396", "m1397", "m1400", "m1402", "m1404"):
        assert list(SUPERSEDED.glob("%s_*.md" % branch)), branch


def test_report_artifacts_present():
    doc_dir = REPO / "docs" / "reports" / "m1561"
    table = json.loads((doc_dir / "adjudication_table.json").read_text(encoding="utf-8"))
    assert table and all("basis" in row for row in table)
    audit = json.loads((doc_dir / "canon_audit.json").read_text(encoding="utf-8"))
    assert audit["summary"]["REJECTED"] == 0
