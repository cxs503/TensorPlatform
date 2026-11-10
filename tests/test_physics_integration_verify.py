"""``ops/taskplane/physics_integration_verify`` 引擎测试（**合成分**夹具，零领域语义）。

夹具域名为 ``N-A`` / ``N-B``、端口名 ``port_x``、量 ``q1`` 等，与任何物理域无关，
用于锁死引擎的五类机械判据：量纲代数、守恒闭合、接口契约、摘要可复算、误差传播排序。
"""

from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.taskplane import physics_integration_verify as V  # noqa: E402


UNITS = {
    "1": {"dim": [0, 0, 0, 0, 0, 0, 0], "scale": 1.0},
    "kg": {"dim": [1, 0, 0, 0, 0, 0, 0], "scale": 1.0},
    "t": {"dim": [1, 0, 0, 0, 0, 0, 0], "scale": 1e3},
    "m": {"dim": [0, 1, 0, 0, 0, 0, 0], "scale": 1.0},
    "m2": {"dim": [0, 2, 0, 0, 0, 0, 0], "scale": 1.0},
    "1/m2": {"dim": [0, -2, 0, 0, 0, 0, 0], "scale": 1.0},
    "s": {"dim": [0, 0, 1, 0, 0, 0, 0], "scale": 1.0},
    "J": {"dim": [1, 2, -2, 0, 0, 0, 0], "scale": 1.0},
    "N": {"dim": [1, 1, -2, 0, 0, 0, 0], "scale": 1.0},
    "MN": {"dim": [1, 1, -2, 0, 0, 0, 0], "scale": 1e6},
    "W": {"dim": [1, 2, -3, 0, 0, 0, 0], "scale": 1.0},
    "W/m2": {"dim": [1, 0, -3, 0, 0, 0, 0], "scale": 1.0},
    "m/s": {"dim": [0, 1, -1, 0, 0, 0, 0], "scale": 1.0},
}


def _anchor(entry_id, value, unit, band, node="N-A"):
    return {"entry_id": entry_id, "node_id": node, "quantity": f"q_{entry_id}",
            "value": value, "unit": unit, "band_log10": list(band), "anchor_kind": "analytic",
            "formula": "synthetic", "reference": None, "inputs": {}}


def _base_manifest():
    """最小可裁 manifest：2 锚 + 1 守恒式 + 1 接口 + 1 传播目标 + 1 交叉检查。"""
    return {
        "suite_id": "synthetic-suite",
        "units": UNITS,
        "frame_classes": [["frame_a", "frame_a_prime"]],
        "anchors": [_anchor("alpha", 1.0e3, "J", (2.5, 3.5)),
                    _anchor("beta", 2.0e3, "J", (3.0, 3.5), node="N-B")],
        "balances": [{"balance_id": "bal1", "node_id": "N-A",
                      "total": {"value": 3.0e3, "unit": "J"},
                      "parts": [{"label": "p1", "value": 1.0e3, "unit": "J", "sign": "+"},
                                {"label": "p2", "value": 2.0e3, "unit": "J", "sign": "+"}],
                      "tol_rel": 0.01}],
        "ordering": [{"edge_id": "o1", "a": "alpha", "b": "beta", "relation": "a<b",
                      "min_gap_log10": 0.1}],
        "interfaces": [{"edge_id": "e1",
                        "producer": {"node_id": "N-A", "port": "port_x", "unit": "J",
                                     "frame": "frame_a", "shape": [], "dtype": "float64"},
                        "consumer": {"node_id": "N-B", "port": "port_y", "unit": "J",
                                     "frame": "frame_a_prime", "shape": [],
                                     "dtype": "float64"}}],
        "propagation": {
            "targets": [{"target_id": "T1", "node_id": "N-A", "output": "q_out", "unit": "J",
                         "value": 1.0e3,
                         "terms": [
                             {"input_id": "i_x", "value": 10.0, "sigma": 1.0, "derivative": 100.0},
                             {"input_id": "i_y", "value": 5.0, "sigma": 0.5, "derivative": 0.0},
                         ]}],
            "expectations": {"top_k": 2,
                             "per_target_order": {"T1": ["i_x", "i_y"]},
                             "global_elasticity_rank_expected": ["T1:i_x", "T1:i_y"],
                             "global_variance_rank_expected": ["T1:i_x", "T1:i_y"],
                             "sign_expectations": [
                                 {"target_id": "T1", "input_id": "i_x", "sign": "+",
                                  "abs_elasticity": 1.0, "tol_rel": 0.01}]},
        },
        "cross_checks": [{"check_id": "c1", "expr": "beta - alpha", "relation": "gte",
                          "rhs": 0.0, "note": "synthetic"}],
        "report": {"case": "c", "method": "m", "results": {"n": 1}, "figures": ["f"],
                   "caveats": "x", "error_judgement": "y"},
    }


# ---------------------------------------------------------------------------
# 单位 / 量纲
# ---------------------------------------------------------------------------
class TestUnits:
    def test_dim_of_joule(self):
        units = V.load_units(UNITS)
        assert V.unit_dim(units, "J") == [1, 2, -2, 0, 0, 0, 0]
        assert V.dim_label(V.unit_dim(units, "J")) == "M·L^2·T^-2"

    def test_scale_conversion(self):
        units = V.load_units(UNITS)
        assert V.to_si(units, 2.0, "t") == pytest.approx(2000.0)

    def test_unknown_unit_raises(self):
        units = V.load_units(UNITS)
        with pytest.raises(KeyError):
            V.unit_dim(units, "furlong")

    def test_bad_dim_length_raises(self):
        with pytest.raises(ValueError):
            V.load_units({"bad": {"dim": [1, 2], "scale": 1.0}})

    def test_bad_scale_raises(self):
        with pytest.raises(ValueError):
            V.load_units({"bad": {"dim": [0] * 7, "scale": 0.0}})

    def test_dim_mul_and_equal(self):
        assert V.dim_mul([1, 2, -3, 0, 0, 0, 0], [0, -2, 0, 0, 0, 0, 0]) == [1, 0, -3, 0, 0, 0, 0]
        assert V.dim_equal([1, 0, -3, 0, 0, 0, 0], V.unit_dim(V.load_units(UNITS), "W/m2"))


# ---------------------------------------------------------------------------
# (1) 量级 / 序 / 守恒
# ---------------------------------------------------------------------------
class TestMagnitudeOrderingBalance:
    def test_magnitude_in_band(self):
        units = V.load_units(UNITS)
        rec = V.check_magnitude(_anchor("a", 1.0e3, "J", (2.5, 3.5)), units)
        assert rec["ok"] and rec["log10_si"] == pytest.approx(3.0)

    def test_magnitude_out_of_band(self):
        units = V.load_units(UNITS)
        rec = V.check_magnitude(_anchor("a", 1.0e5, "J", (2.5, 3.5)), units)
        assert not rec["ok"] and "越出带" in rec["reason"]

    def test_magnitude_uses_si_scale(self):
        units = V.load_units(UNITS)
        # 1 t = 1e3 kg → log10(SI)=3，而不是 log10(1)=0
        rec = V.check_magnitude(_anchor("m", 1.0, "t", (2.5, 3.5)), units)
        assert rec["ok"]

    def test_magnitude_missing_band_fails(self):
        units = V.load_units(UNITS)
        a = _anchor("a", 1.0e3, "J", (2.5, 3.5))
        a.pop("band_log10")
        assert not V.check_magnitude(a, units)["ok"]

    def test_ordering_relations(self):
        units = V.load_units(UNITS)
        mags = {a["entry_id"]: V.check_magnitude(a, units)
                for a in [_anchor("alpha", 1.0e3, "J", (2, 4)), _anchor("beta", 2.0e3, "J", (2, 4))]}
        edges = [
            {"a": "alpha", "b": "beta", "relation": "a<b", "min_gap_log10": 0.1},
            {"a": "beta", "b": "alpha", "relation": "a>b", "min_gap_log10": 0.1},
            {"a": "alpha", "b": "beta", "relation": "same_magnitude", "min_gap_log10": 0.5},
            {"a": "alpha", "b": "beta", "relation": "a>b", "min_gap_log10": 0.0},
        ]
        ok = [e["ok"] for e in V.check_ordering(edges, mags, units)]
        assert ok == [True, True, True, False]

    def test_ordering_gap_enforced(self):
        units = V.load_units(UNITS)
        mags = {a["entry_id"]: V.check_magnitude(a, units)
                for a in [_anchor("alpha", 1.0e3, "J", (2, 4)), _anchor("beta", 1.2e3, "J", (2, 4))]}
        rec = V.check_ordering([{"a": "alpha", "b": "beta", "relation": "a<b",
                                 "min_gap_log10": 0.5}], mags, units)[0]
        assert not rec["ok"]

    def test_balance_closes(self):
        units = V.load_units(UNITS)
        rec = V.check_balance(_base_manifest()["balances"][0], units)
        assert rec["ok"] and rec["residual_rel"] == pytest.approx(0.0, abs=1e-12)

    def test_balance_residual_over_tol_fails(self):
        units = V.load_units(UNITS)
        bal = copy.deepcopy(_base_manifest()["balances"][0])
        bal["parts"][1]["value"] = 1.0e3          # Σ=2e3 vs total 3e3 → 残差 33%
        rec = V.check_balance(bal, units)
        assert not rec["ok"] and abs(rec["residual_rel"]) > bal["tol_rel"]

    def test_balance_dim_mismatch_fails(self):
        units = V.load_units(UNITS)
        bal = copy.deepcopy(_base_manifest()["balances"][0])
        bal["parts"][0]["unit"] = "N"
        rec = V.check_balance(bal, units)
        assert not rec["ok"] and "量纲" in rec["reason"]

    def test_balance_negative_part_supported(self):
        units = V.load_units(UNITS)
        bal = {"balance_id": "b", "total": {"value": 5.0, "unit": "J"},
               "parts": [{"label": "in", "value": 8.0, "unit": "J", "sign": "+"},
                         {"label": "out", "value": 3.0, "unit": "J", "sign": "-"}],
               "tol_rel": 0.01}
        assert V.check_balance(bal, units)["ok"]


# ---------------------------------------------------------------------------
# (2) 接口契约
# ---------------------------------------------------------------------------
class TestInterfaceContract:
    def test_frame_class_makes_compatible(self):
        units = V.load_units(UNITS)
        edge = _base_manifest()["interfaces"][0]
        assert V.check_interface(edge, units, [["frame_a", "frame_a_prime"]])["ok"]
        assert not V.check_interface(edge, units, [])["ok"]      # 无等价类 → 帧不兼容

    def test_dim_mismatch_fails(self):
        units = V.load_units(UNITS)
        edge = copy.deepcopy(_base_manifest()["interfaces"][0])
        edge["consumer"]["unit"] = "m/s"
        rec = V.check_interface(edge, units)
        assert not rec["ok"] and any("量纲" in v for v in rec["violations"])

    def test_declared_bridge_fixes_dimensions(self):
        units = V.load_units(UNITS)
        edge = {"edge_id": "e", "producer": {"node_id": "N-A", "port": "p", "unit": "W",
                                             "frame": "f", "shape": [], "dtype": "float64"},
                "consumer": {"node_id": "N-B", "port": "c", "unit": "W/m2", "frame": "f",
                             "shape": [], "dtype": "float64"},
                "bridge": {"gain": 0.01, "gain_unit": "1/m2", "rationale": "synthetic section"}}
        assert V.check_interface(edge, units)["ok"]
        edge["bridge"]["gain_unit"] = "1"     # 归一化为无量纲 → 量纲仍不一致
        assert not V.check_interface(edge, units)["ok"]

    def test_shape_and_sign_and_dtype_mismatch(self):
        units = V.load_units(UNITS)
        edge = copy.deepcopy(_base_manifest()["interfaces"][0])
        edge["producer"].update({"shape": [3], "dtype": "float32", "sign_convention": "a"})
        edge["consumer"].update({"shape": [], "dtype": "float64", "sign_convention": "b"})
        viol = V.check_interface(edge, units)["violations"]
        # 帧（无等价类）+ 形状 + dtype + 符号约定 → 4 条违规同报
        assert len(viol) == 4 and any("形状" in v for v in viol)

    def test_all_edges_must_pass(self):
        rep = V.verify(_base_manifest())
        crit = next(c for c in rep["criteria"] if c["id"] == "b_interface_contracts")
        assert crit["ok"] and crit["detail"]["pass_rate"] == 1.0


# ---------------------------------------------------------------------------
# (3) 基准库 / 摘要
# ---------------------------------------------------------------------------
class TestBenchmarkSuite:
    def _suite(self):
        units = V.load_units(UNITS)
        return V.build_benchmark_suite([_anchor("alpha", 1.0e3, "J", (2, 4)),
                                        _anchor("beta", 2.0e3, "J", (2, 4))], units, "s")

    def test_digest_reproducible(self):
        assert self._suite()["suite_digest"] == self._suite()["suite_digest"]

    def test_per_entry_sha256_matches_recompute(self):
        suite = self._suite()
        for e in suite["entries"]:
            body = {k: e[k] for k in ("entry_id", "node_id", "quantity", "value", "unit",
                                      "anchor_kind", "formula", "reference", "inputs")}
            assert V.digest(body) == e["sha256"]

    def test_tamper_detected(self):
        suite = self._suite()
        suite["entries"][0]["value"] = 9.9e3          # 改值不改摘要
        rec = V.verify_benchmark_suite(suite, min_entries=2)
        assert not rec["ok"] and "alpha" in rec["broken_entries"]

    def test_duplicate_detected(self):
        units = V.load_units(UNITS)
        suite = V.build_benchmark_suite([_anchor("alpha", 1.0e3, "J", (2, 4)),
                                         _anchor("alpha", 1.0e3, "J", (2, 4))], units, "s")
        rec = V.verify_benchmark_suite(suite, min_entries=1)
        assert not rec["ok"] and rec["duplicate_entries"]

    def test_min_entries_enforced(self):
        rec = V.verify_benchmark_suite(self._suite(), min_entries=10)
        assert not rec["ok"] and any("下限" in r for r in rec["reasons"])

    def test_suite_digest_tamper_detected(self):
        suite = self._suite()
        suite["suite_digest"] = "0" * 64
        assert not V.verify_benchmark_suite(suite, min_entries=1)["ok"]


# ---------------------------------------------------------------------------
# (4) 误差传播 / 排序
# ---------------------------------------------------------------------------
class TestPropagation:
    def test_sigma_and_elasticity(self):
        units = V.load_units(UNITS)
        tgt = _base_manifest()["propagation"]["targets"][0]
        out = V.propagate(tgt, units)
        # σ_y = |d_x·σ_x| = 100·1 = 100（i_y 的偏导为 0）
        assert out["sigma_y_si"] == pytest.approx(100.0)
        assert out["rel_sigma"] == pytest.approx(0.1)
        term = out["terms"][0]
        assert term["elasticity"] == pytest.approx(1.0)          # 100·10/1000
        assert term["variance_share"] == pytest.approx(1.0)

    def test_covariance_term(self):
        units = V.load_units(UNITS)
        tgt = copy.deepcopy(_base_manifest()["propagation"]["targets"][0])
        tgt["terms"] = [{"input_id": "a", "value": 1.0, "sigma": 1.0, "derivative": 1.0},
                        {"input_id": "b", "value": 1.0, "sigma": 1.0, "derivative": 1.0}]
        tgt["correlations"] = {"a|b": 1.0}
        out = V.propagate(tgt, units)
        assert out["sigma_y_si"] == pytest.approx(2.0)           # 完全正相关：σ=1+1
        tgt["correlations"] = {"a|b": -1.0}
        assert V.propagate(tgt, units)["sigma_y_si"] == pytest.approx(0.0, abs=1e-12)

    def test_ranking_tie_rules(self):
        targets = [{"target_id": "T", "terms": [
            {"input_id": "i_big_sigma", "elasticity": 1.0, "sigma_rel": 0.50},
            {"input_id": "i_big_elastic", "elasticity": 3.0, "sigma_rel": 0.01},
            {"input_id": "i_mid", "elasticity": 2.0, "sigma_rel": 0.02},
        ]}]
        for t in targets:
            for term in t["terms"]:
                term["abs_elasticity_sorted"] = round(abs(term["elasticity"]), 6)
                term["weight_sorted"] = round(abs(term["elasticity"]) * term["sigma_rel"], 6)
        ranks = V.rank_sensitivities(targets)
        assert [r["input_id"] for r in ranks["elasticity_rank"][:2]] == ["i_big_elastic", "i_mid"]
        assert ranks["variance_rank"][0]["input_id"] == "i_big_sigma"

    def test_expectation_mismatch_fails_criterion(self):
        m = _base_manifest()
        m["propagation"]["expectations"]["global_elasticity_rank_expected"] = ["T1:i_y", "T1:i_x"]
        rep = V.verify(m, min_benchmark_entries=2)
        crit = next(c for c in rep["criteria"] if c["id"] == "d_error_propagation")
        assert not crit["ok"] and rep["verdict"] == "not_ready"

    def test_wrong_sign_expectation_fails(self):
        m = _base_manifest()
        m["propagation"]["expectations"]["sign_expectations"][0]["sign"] = "-"
        crit = next(c for c in V.verify(m, min_benchmark_entries=2)["criteria"]
                    if c["id"] == "d_error_propagation")
        assert not crit["ok"]


# ---------------------------------------------------------------------------
# 表达式交叉检查（白名单 AST）
# ---------------------------------------------------------------------------
class TestCrossChecks:
    @staticmethod
    def _q():
        return {"alpha": (1.0e3, "J"), "beta": (2.0e3, "J")}

    def test_relations(self):
        rows = V.check_cross_checks([
            {"check_id": "a", "expr": "beta - alpha", "relation": "gte", "rhs": 0.0},
            {"check_id": "b", "expr": "beta / alpha", "relation": "eq", "rhs": 2.0, "tol_rel": 1e-9},
            {"check_id": "c", "expr": "alpha * 2", "relation": "lte", "rhs": 2.0e3},
        ], self._q())
        assert [r["ok"] for r in rows] == [True, True, True]

    def test_failing_relation_reports_reason(self):
        row = V.check_cross_checks([{"check_id": "a", "expr": "alpha", "relation": "gt",
                                     "rhs": 5.0e3}], self._q())[0]
        assert not row["ok"] and "不满足" in row["reason"]

    def test_unknown_symbol_rejected(self):
        row = V.check_cross_checks([{"check_id": "a", "expr": "gamma + 1", "relation": "gte",
                                     "rhs": 0.0}], self._q())[0]
        assert not row["ok"] and "求值失败" in row["reason"]

    @pytest.mark.parametrize("expr", [
        "__import__('os').system('true')",
        "(lambda: 1)()",
        "alpha.__class__",
        "[1,2,3]",
    ])
    def test_injection_rejected(self, expr):
        with pytest.raises((ValueError, SyntaxError)):
            V.safe_eval(expr, {"alpha": 1.0})


# ---------------------------------------------------------------------------
# 总裁决 / 确定性
# ---------------------------------------------------------------------------
class TestVerifyVerdict:
    def test_pass_on_clean_fixture(self):
        rep = V.verify(_base_manifest(), min_benchmark_entries=2)
        assert rep["verdict"] == "pass", [c["id"] for c in rep["criteria"] if not c["ok"]]
        assert all(c["ok"] for c in rep["criteria"])

    def test_missing_report_section_blocks(self):
        m = _base_manifest()
        m["report"].pop("caveats")
        rep = V.verify(m, min_benchmark_entries=2)
        crit = next(c for c in rep["criteria"] if c["id"] == "e_report")
        assert not crit["ok"] and rep["verdict"] == "not_ready"

    def test_deterministic_report(self):
        a = json.dumps(V.verify(_base_manifest(), min_benchmark_entries=2), sort_keys=True)
        b = json.dumps(V.verify(_base_manifest(), min_benchmark_entries=2), sort_keys=True)
        assert a == b

    def test_input_digest_changes_with_input(self):
        m1 = _base_manifest()
        m2 = copy.deepcopy(m1)
        m2["anchors"][0]["value"] = 1.1e3
        assert V.verify(m1)["input_digest"] != V.verify(m2)["input_digest"]

    def test_markdown_contains_criteria_and_numbers(self):
        md = V.render_markdown(V.verify(_base_manifest(), min_benchmark_entries=2))
        for token in ("a_energy_self_consistent", "b_interface_contracts", "c_benchmark_db",
                      "d_error_propagation", "e_report", "verdict" if False else "裁决"):
            assert token in md

    def test_cli_exit_codes(self, tmp_path):
        good = tmp_path / "m.json"
        good.write_text(json.dumps(_base_manifest()), encoding="utf-8")
        assert V.main(["--manifest", str(good), "--min-benchmark-entries", "2"]) == 0
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert V.main(["--manifest", str(bad)]) == 3

    def test_cli_writes_artifacts(self, tmp_path):
        m = tmp_path / "m.json"
        m.write_text(json.dumps(_base_manifest()), encoding="utf-8")
        js = tmp_path / "out.json"
        bj = tmp_path / "bench.json"
        md = tmp_path / "out.md"
        assert V.main(["--manifest", str(m), "--json-out", str(js), "--bench-out", str(bj),
                       "--md-out", str(md), "--min-benchmark-entries", "2"]) == 0
        assert json.loads(js.read_text())["verdict"] == "pass"
        assert json.loads(bj.read_text())["n_entries"] == 2
        assert "裁决" in md.read_text()