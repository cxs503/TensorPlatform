"""C1/C2：marker 格式识别 + 能力特征提取的正确性。

判据
----
* C1：能正确识别 ≥5 种 capability marker 格式（本仓静态支持 6 种 + 1 种运行期）；
* C2：能从已知测试套件正确提取能力特征（id / 等级 / 锚点 / 参数区间 / 修饰符）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.capability_discovery.extract import extract_features
from tools.capability_discovery.markers import MarkerScanner, derive_bare_id, marker_name
from tools.capability_discovery.model import MARKER_FORMATS

SUITE = ROOT / "tools" / "capability_discovery" / "fixtures" / "synthetic_suite"
STATIC_FORMATS = {
    "function_kwargs",
    "function_positional_id",
    "function_bare",
    "class_kwargs",
    "module_pytestmark",
    "param_marks",
}


@pytest.fixture(scope="module")
def scanner() -> MarkerScanner:
    return MarkerScanner(ROOT)


@pytest.fixture(scope="module")
def records(scanner: MarkerScanner):
    return scanner.scan_paths([SUITE])


def by_cap(records, cap_id: str):
    return [r for r in records if r.capability_id == cap_id]


class TestMarkerFormats:
    def test_at_least_five_formats_recognised(self, records):
        """C1：识别格式数 ≥5。"""
        found = {r.marker_format for r in records}
        assert len(STATIC_FORMATS) >= 5
        assert STATIC_FORMATS <= found, f"缺少格式: {STATIC_FORMATS - found}"
        assert found <= set(MARKER_FORMATS)

    def test_function_kwargs_metadata(self, records):
        rec = by_cap(records, "synth.grid.trapezoid")[0]
        assert rec.marker_format == "function_kwargs"
        assert rec.declared_level == "REAL"
        assert rec.anchors == ["ref:closed_form:quadratic"]
        assert rec.params["n_samples"] == {"min": 32, "max": 4096}
        assert rec.tags == ["numeric"]
        assert len(rec.source_sha256) == 64
        assert rec.scope == "function"

    def test_positional_id_format(self, records):
        recs = by_cap(records, "synth.grid.trapezoid")
        fmts = {r.marker_format for r in recs}
        assert "function_positional_id" in fmts
        pos = [r for r in recs if r.marker_format == "function_positional_id"][0]
        assert pos.nodeid.endswith("::test_trapezoid_second_source")

    def test_bare_marker_derives_id_from_node(self, records):
        recs = [r for r in records if r.capability_id.startswith("derived.")]
        assert len(recs) == 1
        assert recs[0].marker_format == "function_bare"
        assert recs[0].capability_id == derive_bare_id(recs[0].nodeid)
        assert recs[0].declared_level == ""

    def test_class_scope_inherited_by_methods(self, records):
        recs = by_cap(records, "synth.algebra.vector_norm")
        assert len(recs) == 2
        assert all(r.scope == "class" for r in recs)
        assert all(r.marker_format == "class_kwargs" for r in recs)
        assert {r.nodeid.split("::")[-1] for r in recs} == {"test_axis_aligned", "test_scaled"}

    def test_module_pytestmark_scope(self, records):
        recs = by_cap(records, "synth.family.module_scope")
        assert recs and all(r.scope == "module" for r in recs)
        assert all(r.marker_format == "module_pytestmark" for r in recs)
        assert all(r.declared_level == "WITHHELD" for r in recs)

    def test_param_marks_format(self, records):
        recs = by_cap(records, "synth.grid.richardson")
        assert recs
        assert all(r.marker_format == "param_marks" for r in recs)

    def test_skip_and_xfail_modifiers_captured(self, records):
        skipped = by_cap(records, "synth.case.skipped")[0]
        xfailed = by_cap(records, "synth.case.xfail")[0]
        assert "skip" in skipped.modifiers
        assert "xfail" in xfailed.modifiers


class TestRobustness:
    def test_non_literal_metadata_is_reported_not_guessed(self, scanner):
        """非字面量 id → 诊断，不臆造能力，不崩溃。"""
        sc = MarkerScanner(ROOT)
        recs = sc.scan_paths([SUITE])
        diag = sc.diagnostics.to_list()
        assert not any(r.capability_id.startswith("synth.dynamic") for r in recs)
        assert any("字面量" in d["message"] for d in diag)
        assert all(d["severity"] in {"warning", "error"} for d in diag)

    def test_invalid_metadata_type_is_diagnosed(self, tmp_path):
        src = (
            "import pytest\n"
            "@pytest.mark.capability(id=123)\n"
            "def test_bad():\n"
            "    assert True\n"
        )
        f = tmp_path / "test_bad.py"
        f.write_text(src, encoding="utf-8")
        sc = MarkerScanner(tmp_path)
        recs = sc.scan_file(f)
        assert recs == []
        assert sc.diagnostics.to_list(), "非法元数据必须留下诊断"

    def test_syntax_error_does_not_raise(self, tmp_path):
        f = tmp_path / "test_broken.py"
        f.write_text("def broken(:\n    pass\n", encoding="utf-8")
        sc = MarkerScanner(tmp_path)
        assert sc.scan_file(f) == []
        assert sc.diagnostics.to_list()[0]["severity"] == "error"

    def test_unmarked_tests_produce_no_records(self, tmp_path):
        f = tmp_path / "test_plain.py"
        f.write_text("def test_plain():\n    assert 1\n", encoding="utf-8")
        sc = MarkerScanner(tmp_path)
        assert sc.scan_file(f) == []
        assert sc.diagnostics.to_list() == []

    def test_marker_name_helper(self):
        import ast

        for src, expected in [
            ("pytest.mark.capability", "capability"),
            ("pytest.mark.skip", "skip"),
            ("mark.xfail", "xfail"),
            ("pytest.mark.parametrize", "parametrize"),
            ("func", ""),
        ]:
            assert marker_name(ast.parse(src, mode="eval").body) == expected


class TestFeatureExtraction:
    """C2：从已知测试套件正确提取能力特征。"""

    def test_feature_aggregation(self, records):
        feats = {f.capability_id: f for f in extract_features(records)}
        trap = feats["synth.grid.trapezoid"]
        # 两条记录（kwargs + positional）→ 一个能力、两个测试节点
        assert len(trap.tests) == 2
        assert trap.declared_level == "REAL"
        assert trap.anchors == ["ref:closed_form:cubic", "ref:closed_form:quadratic"]
        assert set(trap.marker_formats) == {"function_kwargs", "function_positional_id"}
        assert {s["path"].split("/")[-1] for s in trap.sources} == {"test_synthetic_core.py"}
        assert all(len(s["sha256"]) == 64 for s in trap.sources)

    def test_without_outcomes_everything_is_not_run(self, records):
        feats = extract_features(records)
        for f in feats:
            assert f.outcomes == {"not_run": f.total_tests}

    def test_outcome_index_matches_parametrised_nodes(self, records):
        """运行期 nodeid 带 [param]，静态 nodeid 不带 → 后缀匹配必须命中。"""
        outcomes = {
            "pkg/test_synthetic_core.py::test_richardson_param[8]": "passed",
            "pkg/test_synthetic_core.py::test_richardson_param[16]": "passed",
        }
        feats = {f.capability_id: f for f in extract_features(records, outcomes)}
        assert feats["synth.grid.richardson"].outcomes == {"passed": 1}

    def test_conflicting_outcomes_take_worst(self, records):
        outcomes = {
            "a/test_synthetic_core.py::test_richardson_param[8]": "passed",
            "a/test_synthetic_core.py::test_richardson_param[16]": "failed",
        }
        feats = {f.capability_id: f for f in extract_features(records, outcomes)}
        assert feats["synth.grid.richardson"].outcomes == {"failed": 1}

    def test_param_range_merge_takes_outer_envelope(self, records):
        from tools.capability_discovery.model import DiscoveryRecord

        mk = lambda lo, hi: DiscoveryRecord(  # noqa: E731
            capability_id="synth.range.merge",
            nodeid=f"t.py::test_{lo}",
            source_file="t.py",
            source_sha256="0" * 64,
            lineno=1,
            marker_format="function_kwargs",
            declared_level="REAL",
            params={"n": {"min": lo, "max": hi}},
        )
        feat = extract_features([mk(64, 512), mk(16, 1024)])[0]
        assert feat.params["n"] == {"min": 16, "max": 1024}

    def test_nondomain_vocabulary_only(self, records):
        """C5 结构性证据：能力身份全部来自 marker，扫描器本身不含能力名。"""
        pkg = ROOT / "tools" / "capability_discovery"
        words = {r.capability_id.split(".")[0] for r in records}
        assert words, "夹具应产出能力"
        for path in pkg.glob("*.py"):
            if path.name in {"__init__.py", "cli.py"}:
                continue  # 文档字符串/帮助文本允许示例
            text = path.read_text(encoding="utf-8")
            for cap in {r.capability_id for r in records}:
                assert cap not in text, f"{path.name} 硬编码了能力标识 {cap}"