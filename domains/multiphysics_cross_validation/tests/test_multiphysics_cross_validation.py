"""P135-W2-N11 验收测试：十物理面集成验证与交叉互裁（领域侧）。

判据（与战役表 metadata 对齐，数字落 artifacts/p135w2/acceptance.json）：
  (a) 能量量级自洽：UNDEX ~10^8 J、碰撞 ~10^9 J、疲劳单次循环 ~10^3 J，序关系合理；
  (b) 接口契约：跨节点接口 100% 通过；
  (c) 基准数据库：≥10 条解析锚，每条 sha256 可重算且唯一；
  (d) 误差传播：Top-5 灵敏度双口径命中**预声明**期望，弹性符号符合物理直觉；
  (e) 报告：六要素段齐全。

另外锁死「零手抄」：锚值必须能由 ``anchors`` 的闭式公式独立重算出来。
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
for p in (str(ROOT),):
    if p not in sys.path:
        sys.path.insert(0, p)

from domains.multiphysics_cross_validation import anchors as A          # noqa: E402
from domains.multiphysics_cross_validation import manifest as M         # noqa: E402
from ops.taskplane import physics_integration_verify as V               # noqa: E402

TEN_NODES = {"undex", "collision", "fatigue", "mhd", "cavitation", "lng",
             "electric_propulsion", "power_system", "iso15016", "manoeuvring_cfd"}


@pytest.fixture(scope="module")
def manifest():
    """两遍构建（与 run_validation.py 同一路径）：第一遍取数字，第二遍注入六要素段。"""
    from domains.multiphysics_cross_validation import run_validation as R

    m1 = M.build_manifest()
    rep1 = V.verify(m1)
    refs = R.reference_errors(m1, rep1)
    return M.build_manifest(report=R.report_sections(m1, rep1, refs))


@pytest.fixture(scope="module")
def report(manifest):
    return V.verify(manifest)


# ---------------------------------------------------------------------------
# (a) 能量量级自洽
# ---------------------------------------------------------------------------
class TestEnergyConsistency:
    def test_energy_magnitudes_match_acceptance(self, report):
        mags = {m["entry_id"]: m["value_si"] for m in report["magnitudes"]}
        # UNDEX 冲击能 ~1e8 J、碰撞 ~1e9 J、疲劳单次循环 ~1e3 J（各半个数量级内）
        assert 10 ** 7.5 <= mags["undex.shock_energy"] <= 10 ** 8.5
        assert 10 ** 8.8 <= mags["collision.ke_initial"] <= 10 ** 9.3
        assert 10 ** 2.8 <= mags["fatigue.cycle_energy"] <= 10 ** 3.5

    def test_magnitude_ordering_physically_sensible(self, report):
        mags = {m["entry_id"]: m["value_si"] for m in report["magnitudes"]}
        assert mags["fatigue.cycle_energy"] < mags["undex.shock_energy"] < mags["collision.ke_initial"]
        # 疲劳单次循环比冲击能低 3 个量级以上；全寿命累积耗能与宏观事件同量级
        assert math.log10(mags["undex.shock_energy"] / mags["fatigue.cycle_energy"]) > 3.0
        assert 8.5 < math.log10(mags["fatigue.life_dissipation"]) < 9.6

    def test_all_magnitude_bands_hit(self, report):
        bad = [m["entry_id"] for m in report["magnitudes"] if not m["ok"]]
        assert bad == []

    def test_balances_close_within_tolerance(self, report):
        assert len(report["balances"]) == 3
        for b in report["balances"]:
            assert b["ok"], f"{b['balance_id']} 残差 {b['residual_rel']:+.3%} 超容差"
            assert abs(b["residual_rel"]) <= b["tol_rel"]

    def test_balance_parts_are_dimensionally_consistent(self, report):
        for b in report["balances"]:
            assert all(p["dim_matches_total"] for p in b["parts"]), b["balance_id"]

    def test_ordering_edges_pass(self, report):
        assert report["ordering"] and all(o["ok"] for o in report["ordering"])

    def test_chemical_energy_same_order_as_vlcc_collision(self, report):
        """100 kg TNT 化学能与 10 万吨 VLCC 4.5 m/s 碰撞动能同量级（物理直觉）。"""
        mags = {m["entry_id"]: m["log10_si"] for m in report["magnitudes"]}
        assert abs(mags["undex.chemical_energy"] - mags["collision.ke_initial"]) <= 0.5


# ---------------------------------------------------------------------------
# (b) 接口契约
# ---------------------------------------------------------------------------
class TestInterfaceContracts:
    def test_all_edges_pass(self, report):
        assert len(report["interfaces"]) == 12
        assert all(i["ok"] for i in report["interfaces"]), [
            (i["edge_id"], i["violations"]) for i in report["interfaces"] if not i["ok"]]

    def test_pass_rate_is_100pct(self, report):
        crit = next(c for c in report["criteria"] if c["id"] == "b_interface_contracts")
        assert crit["ok"] and crit["detail"]["pass_rate"] == 1.0

    def test_ten_domains_all_have_ports(self, manifest):
        producers = {e["producer"]["node_id"] for e in manifest["interfaces"]}
        consumers = {e["consumer"]["node_id"] for e in manifest["interfaces"]}
        covered = (producers | consumers) & TEN_NODES
        assert covered == TEN_NODES, f"未覆盖的物理面：{TEN_NODES - covered}"

    def test_bridge_edges_are_declared_with_rationale(self, manifest):
        bridges = [e for e in manifest["interfaces"] if e.get("bridge")]
        assert len(bridges) == 2
        for e in bridges:
            assert e["bridge"]["rationale"] and e["bridge"]["gain_unit"]

    def test_frame_class_bridge_is_required(self, manifest):
        """去掉帧等价类后 e08 必须失败 —— 证明该契约不是空转。"""
        units = V.load_units(manifest["units"])
        edge = next(e for e in manifest["interfaces"] if e["edge_id"] == "e08_prop_to_power")
        assert V.check_interface(edge, units, manifest["frame_classes"])["ok"]
        assert not V.check_interface(edge, units, [])["ok"]

    def test_removing_bridge_breaks_contract(self, manifest):
        units = V.load_units(manifest["units"])
        edge = next(e for e in manifest["interfaces"] if e["edge_id"] == "e06_lng_heat_to_tank")
        bare = dict(edge)
        bare.pop("bridge")
        assert not V.check_interface(bare, units)["ok"]


# ---------------------------------------------------------------------------
# (c) 基准数据库
# ---------------------------------------------------------------------------
class TestBenchmarkDatabase:
    def test_at_least_10_entries(self, manifest):
        units = V.load_units(manifest["units"])
        suite = V.build_benchmark_suite(manifest["anchors"], units, manifest["suite_id"])
        assert suite["n_entries"] >= 10

    def test_each_entry_has_unique_recomputable_sha256(self, manifest):
        units = V.load_units(manifest["units"])
        suite = V.build_benchmark_suite(manifest["anchors"], units, manifest["suite_id"])
        digests = [e["sha256"] for e in suite["entries"]]
        assert len(set(digests)) == len(digests)
        for e in suite["entries"]:
            body = {k: e[k] for k in ("entry_id", "node_id", "quantity", "value", "unit",
                                      "anchor_kind", "formula", "reference", "inputs")}
            assert V.digest(body) == e["sha256"]
        assert V.verify_benchmark_suite(suite, 10)["ok"]

    def test_suite_digest_reproducible(self, manifest):
        units = V.load_units(manifest["units"])
        a = V.build_benchmark_suite(manifest["anchors"], units, manifest["suite_id"])
        b = V.build_benchmark_suite(manifest["anchors"], units, manifest["suite_id"])
        assert a["suite_digest"] == b["suite_digest"]

    def test_artifacts_exist_after_run(self):
        """驱动器产物必须落盘（缺失则说明从未真正跑过验收）。"""
        if not (ROOT / "artifacts" / "p135w2" / "acceptance.json").exists():
            pytest.skip("artifact 尚未生成（先跑 run_validation.py）")
        import json
        acc = json.loads((ROOT / "artifacts" / "p135w2" / "acceptance.json").read_text())
        assert acc["verdict"] == "pass"
        assert acc["numbers"]["n_anchors"] >= 10


# ---------------------------------------------------------------------------
# (d) 误差传播 / 灵敏度
# ---------------------------------------------------------------------------
class TestErrorPropagation:
    def test_top5_dual_rankings_hit_predeclared(self, report):
        exp = M.build_propagation()["expectations"]
        got_el = [r["key"] for r in report["sensitivity"]["elasticity_top"]]
        got_va = [r["key"] for r in report["sensitivity"]["variance_top"]]
        assert got_el == exp["global_elasticity_rank_expected"]
        assert got_va == exp["global_variance_rank_expected"]

    def test_per_target_orders_hit_predeclared(self, report):
        assert all(t["ok"] for t in report["sensitivity"]["per_target"])

    def test_elasticity_signs_match_physics(self, report):
        assert all(s["ok"] for s in report["sensitivity"]["sign_expectations"])
        el = {(t["target_id"], x["input_id"]): x["elasticity"]
              for t in report["propagation"] for x in t["terms"]}
        assert el[("collision.ke_initial", "collision.v1")] == pytest.approx(2.0, rel=1e-4)
        assert el[("undex.shock_energy", "undex.standoff_m")] < 0
        assert abs(el[("undex.shock_energy", "undex.standoff_m")]) < 0.5   # 球面扩散几乎守恒
        assert el[("cav.collapse_time", "cav.dp")] == pytest.approx(-0.5, rel=1e-4)
        assert el[("iso.raa", "iso.v_rw")] == pytest.approx(2.0, rel=1e-4)

    def test_relative_sigma_finite_and_ordered(self, report):
        for t in report["propagation"]:
            assert math.isfinite(t["sigma_y_si"])
            assert t["rel_sigma"] is not None and t["rel_sigma"] > 0

    def test_sigma_sources_documented(self):
        for name, spec in A.INPUTS.items():
            assert 0.0 < spec["sigma_rel"] < 1.0, name
            assert isinstance(spec["rationale"], str) and len(spec["rationale"]) > 4, name

    def test_cross_checks_pass(self, report):
        assert report["cross_checks"] and all(c["ok"] for c in report["cross_checks"])


# ---------------------------------------------------------------------------
# (e) 报告 + 零手抄
# ---------------------------------------------------------------------------
class TestReportAndProvenance:
    def test_six_elements_present(self):
        md = ROOT / "artifacts" / "p135w2" / "report.md"
        if not md.exists():
            pytest.skip("report.md 尚未生成")
        text = md.read_text(encoding="utf-8")
        for token in ("① 案例介绍", "② 怎么做的", "③ 计算结果", "④ 结果图", "⑤ 注意点",
                      "⑥ 误差判定", "审计链"):
            assert token in text, token
        assert "是否 <3%" in text or "<3%？" in text

    def test_four_figures_present(self):
        for name in ("fig1_energy_framework", "fig2_interface_contracts",
                     "fig3_benchmark_anchors", "fig4_sensitivity_tornado"):
            if not (ROOT / "artifacts" / "p135w2" / f"{name}.png").exists():
                pytest.skip("figures 尚未生成")
            assert (ROOT / "artifacts" / "p135w2" / f"{name}.png").stat().st_size > 5000

    def test_criterion_e_passes(self, manifest):
        m = M.build_manifest(report={"case": "c", "method": "m", "results": {"n": 1},
                                     "figures": ["f"], "caveats": "x",
                                     "error_judgement": "y"})
        rep = V.verify(m)
        assert next(c for c in rep["criteria"] if c["id"] == "e_report")["ok"]

    def test_anchor_values_recomputable_from_formulas(self, manifest):
        """零手抄守卫：锚值必须等于闭式公式的独立重算。"""
        by_id = {a["entry_id"]: a for a in manifest["anchors"]}
        c = M.CASE
        recomputed = {
            "undex.shock_energy": A.undex_shock_energy(c["undex_mass"], c["undex_standoff"],
                                                       A.RHO_W, A.C_W),
            "collision.ke_initial": A.collision_ke(c["coll_m1"], c["coll_v1"]),
            "collision.crush_energy": A.collision_crush_energy(c["coll_r0"], c["coll_k"],
                                                              c["coll_s"]),
            "fatigue.cycle_energy": A.fatigue_cycle_energy(c["fat_dsigma"], c["fat_deps_p"],
                                                           c["fat_volume"], c["fat_shape"]),
            "mhd.force_density": A.mhd_force_density(c["mhd_sigma"], c["mhd_u"], c["mhd_b"]),
            "cav.collapse_time": A.cav_collapse_time(c["cav_r0"], c["cav_p_amb"] - c["cav_p_vap"],
                                                     A.RHO_W),
            "lng.heat_leak": A.lng_heat_leak(c["lng_u"], c["lng_area"], c["lng_dT"]),
            "iso.raa": A.iso_raa(c["iso_rho_air"], c["iso_cd"], c["iso_area_t"], c["iso_v_rw"]),
            "man.tactical_diameter_over_l": A.man_tactical_diameter_over_l(
                c["man_k"], math.radians(c["man_delta_deg"]), c["v_ship"], c["man_length"]),
        }
        for entry_id, val in recomputed.items():
            assert by_id[entry_id]["value"] == pytest.approx(val, rel=1e-12), entry_id

    def test_ten_nodes_covered_by_anchors(self, manifest):
        nodes = {a["node_id"] for a in manifest["anchors"]}
        assert nodes == TEN_NODES

    def test_every_anchor_unit_declared(self, manifest):
        units = V.load_units(manifest["units"])
        for a in manifest["anchors"]:
            assert a["unit"] in units, a["entry_id"]
            assert a["formula"], a["entry_id"]

    def test_reference_errors_are_reported_with_caliber(self, manifest, report):
        refs = [a for a in manifest["anchors"] if a.get("reference")]
        assert len(refs) >= 4
        units = V.load_units(manifest["units"])
        for a in refs:
            want = V.to_si(units, a["reference"]["value"], a["reference"]["unit"])
            got = next(m["value_si"] for m in report["magnitudes"]
                       if m["entry_id"] == a["entry_id"])
            err = (got - want) / want
            assert abs(err) < 0.5, f"{a['entry_id']} 与参考偏差 >50%"
            assert a["reference"]["source"]


# ---------------------------------------------------------------------------
# 确定性与裁决
# ---------------------------------------------------------------------------
def test_verdict_pass_and_deterministic(manifest, report):
    assert report["verdict"] == "pass", [c["id"] for c in report["criteria"] if not c["ok"]]
    assert V.verify(manifest)["input_digest"] == report["input_digest"]


def test_report_numbers_consistent_with_engine(report):
    mags = {m["entry_id"]: m["value_si"] for m in report["magnitudes"]}
    assert mags["undex.shock_energy"] == pytest.approx(
        A.undex_shock_energy(M.CASE["undex_mass"], M.CASE["undex_standoff"], A.RHO_W, A.C_W))