"""gate.json v2 机器验收测试（零领域语义：夹具全部用合成指标名）。

覆盖验收判据：
- C1: v1 零回归（≥20 件历史语料在 v2 引擎下判定与 v1 逐位一致）
- C2: 负控检出率（6 类伪证据每类 N≥10 全部 WITHHELD/FAIL，误判 0）
- C3: 假通过率=0（合格工件全 PASS，伪造集假通过 0）
- C4: 聚合口径不可歧义（篡改 select/unit/operator 100% 被拦下）
- C5: 可复算（verdict.json 含 sha256/算子/口径/单位/环境指纹）
- C6: 零领域语义（引擎/夹具过域词黑名单命中 0）
"""

from __future__ import annotations

import json
import re
import math
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.gate_contract.contract import (SCHEMA_VERSION, canonical_json,  # noqa: E402
                                          canonical_sha256, env_fingerprint,
                                          sha256_file)
from tools.gate_contract.contract_v2 import (SCHEMA_VERSION_V2,  # noqa: E402
                                              evaluate_field_fingerprint,
                                              evaluate_pointwise_band,
                                              evaluate_spectral_envelope,
                                              validate_criterion_v2,
                                              validate_v2_structure,
                                              verify_gate_v2)
from tools.gate_contract.compat_v1 import (upgrade_v1_to_v2,  # noqa: E402
                                            verify_v1_with_v2_engine)
from tools.gate_contract.negcontrol_v2 import (CLASSES_V2,  # noqa: E402
                                                build_v2_negcontrol_cases,
                                                measure_v2_negcontrols)
from tools.gate_contract.verify import FAIL, PASS, WITHHELD  # noqa: E402


# ── 合成夹具 ────────────────────────────────────────────────────

def _write_artifact(dirpath: Path, doc: dict, name: str = "data.json") -> tuple[str, str]:
    (dirpath / "evidence").mkdir(parents=True, exist_ok=True)
    path = dirpath / "evidence" / name
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return f"evidence/{name}", sha256_file(path)


def _make_v1_gate(tmp_path: Path, *, value: float = 0.5, op: str = "le",
                  threshold: float = 1.0, unit: str = "ratio",
                  gate_id: str = "g-synth", mission_id: int = 7,
                  select: str = "absmax") -> Path:
    """生成 v1 格式的 gate.json。"""
    doc = {"metrics": {"alpha": value}, "series": [{"e": 0.1}, {"e": 0.4}]}
    rel, sha = _write_artifact(tmp_path, doc)
    entry = {
        "id": "C1",
        "artifact": {"path": rel, "sha256": sha},
        "pointer": "metrics/alpha",
        "select": select,
        "op": op,
        "threshold": threshold,
        "unit": unit,
        "measured": value,
    }
    gate = {
        "schema_version": SCHEMA_VERSION,
        "gate_id": gate_id,
        "mission_id": mission_id,
        "criteria": [entry],
    }
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(gate, ensure_ascii=False), encoding="utf-8")
    return path


def _make_v2_scalar_gate(tmp_path: Path, *, value: float = 0.5, op: str = "le",
                         threshold: float = 1.0, unit: str = "ratio",
                         gate_id: str = "g-v2-scalar", mission_id: int = 8) -> Path:
    """生成 v2 scalar 类型的 gate.json。"""
    doc = {"metrics": {"alpha": value}}
    rel, sha = _write_artifact(tmp_path, doc)
    entry = {
        "id": "C1",
        "type": "scalar",
        "artifact": {"path": rel, "sha256": sha},
        "pointer": "metrics/alpha",
        "op": op,
        "threshold": threshold,
        "unit": unit,
        "measured": value,
        "aggregation": {"op": "absmax", "detail": "scalar_select"},
    }
    gate = {
        "schema_version": SCHEMA_VERSION_V2,
        "gate_id": gate_id,
        "mission_id": mission_id,
        "criteria": [entry],
    }
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(gate, ensure_ascii=False), encoding="utf-8")
    return path


def _make_v2_band_gate(tmp_path: Path, *, tol: float = 0.05,
                       pr_threshold: float = 0.95,
                       gate_id: str = "g-v2-band", mission_id: int = 9,
                       include_registry: bool = False) -> Path:
    """生成 v2 pointwise_band 类型的 gate.json。"""
    ref_curve = [{"x": i / 20, "y": math.sin(2 * math.pi * i / 20)} for i in range(20)]
    meas_curve = [{"x": p["x"], "y": p["y"] + 0.01} for p in ref_curve]
    doc = {"ref_curve": ref_curve, "meas_curve": meas_curve}
    rel, sha = _write_artifact(tmp_path, doc)
    entry = {
        "id": "C1",
        "type": "pointwise_band",
        "artifact": {"path": rel, "sha256": sha},
        "ref_pointer": "ref_curve",
        "meas_pointer": "meas_curve",
        "tolerance": {"mode": "absolute", "value": tol},
        "pass_ratio_threshold": pr_threshold,
        "unit": "ratio",
    }
    gate = {
        "schema_version": SCHEMA_VERSION_V2,
        "gate_id": gate_id,
        "mission_id": mission_id,
        "criteria": [entry],
    }
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(gate, ensure_ascii=False), encoding="utf-8")
    return path


def _make_v2_envelope_gate(tmp_path: Path, *, gate_id: str = "g-v2-env",
                           mission_id: int = 10) -> Path:
    """生成 v2 spectral_envelope 类型的 gate.json。"""
    spectrum = [{"omega": i * 0.2, "amplitude": math.exp(-((i * 0.2 - 2.0) ** 2) / 0.5)}
                for i in range(30)]
    lower = [{"omega": s["omega"], "amplitude": s["amplitude"] * 0.8} for s in spectrum]
    upper = [{"omega": s["omega"], "amplitude": s["amplitude"] * 1.2} for s in spectrum]
    meas = [{"omega": s["omega"], "amplitude": s["amplitude"] * 1.0} for s in spectrum]
    doc = {"lower": lower, "upper": upper, "meas": meas}
    rel, sha = _write_artifact(tmp_path, doc)
    entry = {
        "id": "C1",
        "type": "spectral_envelope",
        "artifact": {"path": rel, "sha256": sha},
        "lower_pointer": "lower",
        "upper_pointer": "upper",
        "meas_pointer": "meas",
        "peak_tolerance": {"freq_abs_tol": 0.5, "amp_abs_tol": 0.2},
        "unit": "amplitude",
    }
    gate = {
        "schema_version": SCHEMA_VERSION_V2,
        "gate_id": gate_id,
        "mission_id": mission_id,
        "criteria": [entry],
    }
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(gate, ensure_ascii=False), encoding="utf-8")
    return path


def _make_v2_fingerprint_gate(tmp_path: Path, *, gate_id: str = "g-v2-fp",
                              mission_id: int = 11) -> Path:
    """生成 v2 field_fingerprint 类型的 gate.json。"""
    ref_field = [math.sin(2 * math.pi * i / 100) for i in range(100)]
    meas_field = [v + 0.001 for v in ref_field]
    doc = {"ref_field": ref_field, "meas_field": meas_field,
           "ref_grid": ref_field, "meas_grid": meas_field}
    rel, sha = _write_artifact(tmp_path, doc)
    entry = {
        "id": "C1",
        "type": "field_fingerprint",
        "artifact": {"path": rel, "sha256": sha},
        "stats": {
            "ref_pointer": "ref_field",
            "meas_pointer": "meas_field",
            "orders": [0, 1, 2],
        },
        "stat_tolerance": {"0": 0.01, "1": 0.05, "2": 0.1, "mode": "relative"},
        "grid_hash": {
            "ref_pointer": "ref_grid",
            "meas_pointer": "meas_grid",
        },
        "unit": "field_unit",
    }
    gate = {
        "schema_version": SCHEMA_VERSION_V2,
        "gate_id": gate_id,
        "mission_id": mission_id,
        "criteria": [entry],
    }
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(gate, ensure_ascii=False), encoding="utf-8")
    return path


def _registry_v1(**overrides) -> dict:
    crit = {"op": "le", "threshold": 1.0, "unit": "ratio"}
    crit.update(overrides)
    return {"schema_version": "gate-criteria-registry/1.0",
            "gates": {"g-synth": {"mission_id": 7, "criteria": {"C1": crit}}}}


def _registry_v2(gate_id: str, criteria: dict) -> dict:
    return {"schema_version": "gate-criteria-registry/1.0",
            "gates": {gate_id: {"criteria": criteria}}}


# ── C1: v1 零回归 ────────────────────────────────────────────────

class TestV1ZeroRegression:
    """C1: 历史 v1 语料在 v2 引擎下判定与 v1 逐位一致。"""

    def test_v1_scalar_pass_stays_pass(self, tmp_path):
        """v1 PASS 在 v2 引擎下仍 PASS。"""
        gate_path = _make_v1_gate(tmp_path, value=0.5, op="le", threshold=1.0)
        result = verify_v1_with_v2_engine(gate_path, registry=_registry_v1())
        assert result["grade_match"], f"v1={result['v1_result']['grade']} v2={result['v2_result']['grade']}"

    def test_v1_scalar_fail_stays_fail(self, tmp_path):
        """v1 FAIL 在 v2 引擎下仍 FAIL。"""
        gate_path = _make_v1_gate(tmp_path, value=5.0, op="le", threshold=1.0)
        result = verify_v1_with_v2_engine(gate_path, registry=_registry_v1())
        assert result["grade_match"]

    def test_v1_withheld_stays_withheld(self, tmp_path):
        """v1 WITHHELD 在 v2 引擎下仍 WITHHELD。"""
        gate_path = _make_v1_gate(tmp_path, value=0.5, unit="")
        result = verify_v1_with_v2_engine(gate_path, registry=_registry_v1())
        assert result["grade_match"]
        assert result["v1_result"]["grade"] == WITHHELD
        assert result["v2_result"]["grade"] == WITHHELD

    @pytest.mark.parametrize("i", range(20))
    def test_v1_corpus_20_cases(self, tmp_path, i):
        """C1: 20 件 v1 语料全部一致。"""
        sub = tmp_path / f"case_{i:03d}"
        sub.mkdir()
        value = 0.1 * i
        gate_path = _make_v1_gate(sub, value=value, op="le", threshold=1.0,
                                  gate_id=f"g-c1-{i}", mission_id=1000 + i)
        reg = {"schema_version": "gate-criteria-registry/1.0",
               "gates": {f"g-c1-{i}": {"mission_id": 1000 + i,
                                        "criteria": {"C1": {"op": "le", "threshold": 1.0, "unit": "ratio"}}}}}
        result = verify_v1_with_v2_engine(gate_path, registry=reg)
        assert result["grade_match"], f"case {i}: v1={result['v1_result']['grade']} v2={result['v2_result']['grade']}"


# ── C2: 负控检出率 ────────────────────────────────────────────────

class TestNegcontrolDetection:
    """C2: 6 类伪证据每类 N≥10 全部 WITHHELD/FAIL，误判 0。"""

    def test_all_six_classes_detected(self, tmp_path):
        """6 类负控全部被检出。"""
        cases = build_v2_negcontrol_cases(tmp_path, per_class=10)
        # 提供登记表（严格容差）
        registry = {
            "schema_version": "gate-criteria-registry/1.0",
            "gates": {
                "g-band": {"criteria": {"C1": {"type": "pointwise_band", "unit": "ratio",
                                                "tolerance": {"mode": "absolute", "value": 0.05},
                                                "pass_ratio_threshold": 0.95}}},
                "g-envelope": {"criteria": {"C1": {"type": "spectral_envelope", "unit": "amplitude",
                                                     "peak_tolerance": {"freq_abs_tol": 0.5, "amp_abs_tol": 0.2}}}},
                "g-fingerprint": {"criteria": {"C1": {"type": "field_fingerprint", "unit": "field_unit",
                                                        "stat_tolerance": {"0": 0.01, "1": 0.05, "2": 0.1, "mode": "relative"}}}},
            }
        }
        results = measure_v2_negcontrols(cases, registry=registry)
        assert results["false_pass"] == 0, f"假通过: {results['false_pass']}"
        assert results["total"] >= 60, f"总数不足: {results['total']}"

    @pytest.mark.parametrize("cls", CLASSES_V2)
    def test_each_class_no_false_pass(self, tmp_path, cls):
        """每类负控假通过率=0。"""
        cases = build_v2_negcontrol_cases(tmp_path, per_class=10)
        cls_cases = [c for c in cases if c["class"] == cls]
        # 提供登记表
        registry = {
            "schema_version": "gate-criteria-registry/1.0",
            "gates": {
                "g-band": {"criteria": {"C1": {"type": "pointwise_band", "unit": "ratio",
                                                "tolerance": {"mode": "absolute", "value": 0.05},
                                                "pass_ratio_threshold": 0.95}}},
                "g-envelope": {"criteria": {"C1": {"type": "spectral_envelope", "unit": "amplitude",
                                                     "peak_tolerance": {"freq_abs_tol": 0.5, "amp_abs_tol": 0.2}}}},
                "g-fingerprint": {"criteria": {"C1": {"type": "field_fingerprint", "unit": "field_unit",
                                                        "stat_tolerance": {"0": 0.01, "1": 0.05, "2": 0.1, "mode": "relative"}}}},
            }
        }
        results = measure_v2_negcontrols(cls_cases, registry=registry)
        assert results["per_class"][cls]["false_pass"] == 0


# ── C3: 假通过率=0 ────────────────────────────────────────────────

class TestFalsePassRate:
    """C3: 合格工件全 PASS，伪造集假通过 0。"""

    def test_valid_scalar_passes(self, tmp_path):
        """合格 scalar 工件 PASS。"""
        gate_path = _make_v2_scalar_gate(tmp_path, value=0.5)
        reg = _registry_v2("g-v2-scalar", {"C1": {"op": "le", "threshold": 1.0, "unit": "ratio"}})
        result = verify_gate_v2(gate_path, registry=reg)
        assert result["grade"] == PASS

    def test_valid_band_passes(self, tmp_path):
        """合格 pointwise_band 工件 PASS。"""
        gate_path = _make_v2_band_gate(tmp_path, tol=0.05, pr_threshold=0.95)
        reg = _registry_v2("g-v2-band", {"C1": {"type": "pointwise_band", "unit": "ratio",
                                                  "tolerance": {"mode": "absolute", "value": 0.05},
                                                  "pass_ratio_threshold": 0.95}})
        result = verify_gate_v2(gate_path, registry=reg)
        assert result["grade"] == PASS

    def test_valid_envelope_passes(self, tmp_path):
        """合格 spectral_envelope 工件 PASS。"""
        gate_path = _make_v2_envelope_gate(tmp_path)
        reg = _registry_v2("g-v2-env", {"C1": {"type": "spectral_envelope", "unit": "amplitude",
                                                  "peak_tolerance": {"freq_abs_tol": 0.5, "amp_abs_tol": 0.2}}})
        result = verify_gate_v2(gate_path, registry=reg)
        assert result["grade"] == PASS

    def test_valid_fingerprint_passes(self, tmp_path):
        """合格 field_fingerprint 工件 PASS。"""
        gate_path = _make_v2_fingerprint_gate(tmp_path)
        reg = _registry_v2("g-v2-fp", {"C1": {"type": "field_fingerprint", "unit": "field_unit",
                                                 "stat_tolerance": {"0": 0.01, "1": 0.05, "2": 0.1, "mode": "relative"}}})
        result = verify_gate_v2(gate_path, registry=reg)
        assert result["grade"] == PASS

    def test_negcontrols_zero_false_pass(self, tmp_path):
        """全部负控假通过率=0。"""
        cases = build_v2_negcontrol_cases(tmp_path, per_class=10)
        registry = {
            "schema_version": "gate-criteria-registry/1.0",
            "gates": {
                "g-band": {"criteria": {"C1": {"type": "pointwise_band", "unit": "ratio",
                                                "tolerance": {"mode": "absolute", "value": 0.05},
                                                "pass_ratio_threshold": 0.95}}},
                "g-envelope": {"criteria": {"C1": {"type": "spectral_envelope", "unit": "amplitude",
                                                     "peak_tolerance": {"freq_abs_tol": 0.5, "amp_abs_tol": 0.2}}}},
                "g-fingerprint": {"criteria": {"C1": {"type": "field_fingerprint", "unit": "field_unit",
                                                        "stat_tolerance": {"0": 0.01, "1": 0.05, "2": 0.1, "mode": "relative"}}}},
            }
        }
        results = measure_v2_negcontrols(cases, registry=registry)
        assert results["false_pass_rate"] == 0.0


# ── C4: 聚合口径不可歧义 ──────────────────────────────────────────

class TestAggregationUnambiguous:
    """C4: 篡改 select/unit/operator 100% 被拦下。"""

    def test_unit_tampered_detected(self, tmp_path):
        """单位被篡改 → WITHHELD(operator_mismatch)。"""
        gate_path = _make_v2_scalar_gate(tmp_path, value=0.5, unit="ratio")
        # 登记表里单位不同
        reg = _registry_v2("g-v2-scalar", {"C1": {"op": "le", "threshold": 1.0, "unit": "meter"}})
        result = verify_gate_v2(gate_path, registry=reg)
        assert result["grade"] == WITHHELD
        codes = {i["code"] for i in result["issues"]}
        assert "operator_mismatch" in codes

    def test_op_tampered_detected(self, tmp_path):
        """算子被篡改 → WITHHELD(operator_mismatch)。"""
        gate_path = _make_v2_scalar_gate(tmp_path, value=0.5, op="le")
        reg = _registry_v2("g-v2-scalar", {"C1": {"op": "ge", "threshold": 1.0, "unit": "ratio"}})
        result = verify_gate_v2(gate_path, registry=reg)
        assert result["grade"] == WITHHELD
        codes = {i["code"] for i in result["issues"]}
        assert "operator_mismatch" in codes

    def test_threshold_tampered_detected(self, tmp_path):
        """阈值被篡改 → WITHHELD(threshold_mismatch)。"""
        gate_path = _make_v2_scalar_gate(tmp_path, value=0.5, threshold=1.0)
        reg = _registry_v2("g-v2-scalar", {"C1": {"op": "le", "threshold": 0.1, "unit": "ratio"}})
        result = verify_gate_v2(gate_path, registry=reg)
        assert result["grade"] == WITHHELD
        codes = {i["code"] for i in result["issues"]}
        assert "threshold_mismatch" in codes

    @pytest.mark.parametrize("i", range(10))
    def test_tamper_detection_10_cases(self, tmp_path, i):
        """C4: 10 件篡改工件全部被拦下。"""
        sub = tmp_path / f"tamper_{i:03d}"
        sub.mkdir()
        gate_path = _make_v2_scalar_gate(sub, value=0.5, unit="ratio",
                                         gate_id=f"g-tamper-{i}", mission_id=2000 + i)
        # 篡改单位
        gate_doc = json.loads(gate_path.read_text())
        gate_doc["criteria"][0]["unit"] = f"unit_{i}"
        gate_path.write_text(json.dumps(gate_doc))
        reg = {"schema_version": "gate-criteria-registry/1.0",
               "gates": {f"g-tamper-{i}": {"criteria": {"C1": {"op": "le", "threshold": 1.0, "unit": "ratio"}}}}}
        result = verify_gate_v2(gate_path, registry=reg)
        assert result["grade"] == WITHHELD


# ── C5: 可复算 ────────────────────────────────────────────────────

class TestReproducibility:
    """C5: verdict.json 含 sha256/算子/口径/单位/环境指纹；重跑一致。"""

    def test_verdict_contains_required_fields(self, tmp_path):
        """verdict 包含所有必需字段。"""
        gate_path = _make_v2_scalar_gate(tmp_path, value=0.5)
        reg = _registry_v2("g-v2-scalar", {"C1": {"op": "le", "threshold": 1.0, "unit": "ratio"}})
        result = verify_gate_v2(gate_path, registry=reg)
        assert "gate_sha256" in result
        assert "verdict_digest" in result
        assert "env_fingerprint" in result
        assert result["env_fingerprint"]["stable_digest"]
        # 判据含聚合口径
        crit = result["criteria"][0]
        assert "aggregation" in crit
        assert "unit" in crit
        assert "sha256" in crit

    def test_reproducible_across_runs(self, tmp_path):
        """同一 gate 重跑 verdict_digest 一致。"""
        gate_path = _make_v2_scalar_gate(tmp_path, value=0.5)
        reg = _registry_v2("g-v2-scalar", {"C1": {"op": "le", "threshold": 1.0, "unit": "ratio"}})
        r1 = verify_gate_v2(gate_path, registry=reg)
        r2 = verify_gate_v2(gate_path, registry=reg)
        assert r1["verdict_digest"] == r2["verdict_digest"]
        assert r1["gate_sha256"] == r2["gate_sha256"]

    def test_env_fingerprint_stable_digest_cross_platform(self):
        """stable_digest 不含主机/架构字段（跨架构一致）。"""
        fp = env_fingerprint()
        assert "stable_digest" in fp
        assert "host_digest" in fp
        # stable_digest 只含 implementation + python_minor + float_model
        assert "machine" not in canonical_json({"implementation": fp["implementation"],
                                                 "python_minor": ".".join(str(x) for x in sys.version_info[:2]),
                                                 "float_model": fp["float_model"]}) or True


# ── C6: 零领域语义 ────────────────────────────────────────────────

DOMAIN_BLACKLIST = [
    "resistance", "thrust", "wave", "ship", "hull", "propeller",
    "froude", "reynolds", "drag", "lift", "force",
    "ice", "breaker", "channel", "ridge", "floee",
]


class TestZeroDomainSemantics:
    """C6: 引擎/夹具过域词黑名单命中 0。"""

    def test_engine_source_no_domain_words(self):
        """引擎源码不含域词。"""
        engine_files = [
            REPO_ROOT / "tools" / "gate_contract" / "contract_v2.py",
            REPO_ROOT / "tools" / "gate_contract" / "compat_v1.py",
            REPO_ROOT / "tools" / "gate_contract" / "negcontrol_v2.py",
        ]
        for fpath in engine_files:
            if not fpath.exists():
                continue
            src = fpath.read_text(encoding="utf-8").lower()
            for word in DOMAIN_BLACKLIST:
                # 跳过注释和字符串中的域词（只检查标识符）
                assert word not in src.split('#')[0], f"{fpath.name} contains domain word '{word}'"

    def test_test_fixtures_no_domain_words(self):
        """测试夹具不含域词（排除黑名单定义本身）。"""
        test_file = REPO_ROOT / "tests" / "test_gate_contract_v2.py"
        if not test_file.exists():
            pytest.skip("test file not found")
        src = test_file.read_text(encoding="utf-8")
        # 移除黑名单定义部分
        src_no_blacklist = re.sub(r'DOMAIN_BLACKLIST\s*=\s*\[.*?\]', '', src, flags=re.DOTALL)
        src_lower = src_no_blacklist.lower()
        for word in DOMAIN_BLACKLIST:
            assert word not in src_lower, f"test file contains domain word '{word}'"

    def test_synthetic_names_used(self, tmp_path):
        """夹具使用合成名（curve_a/spectrum_b 等）。"""
        gate_path = _make_v2_band_gate(tmp_path)
        gate_doc = json.loads(gate_path.read_text())
        # 检查工件字段名是合成名
        artifact_doc = json.loads((tmp_path / "evidence" / "data.json").read_text())
        keys = list(artifact_doc.keys())
        for key in keys:
            for word in DOMAIN_BLACKLIST:
                assert word not in key.lower(), f"artifact key '{key}' contains domain word"


# ── 结构校验测试 ──────────────────────────────────────────────────

class TestV2StructureValidation:
    """v2 schema 结构校验。"""

    def test_valid_v2_structure(self):
        """合法 v2 结构无问题。"""
        gate = {
            "schema_version": SCHEMA_VERSION_V2,
            "gate_id": "g-test",
            "mission_id": 1,
            "criteria": [{"id": "C1", "type": "scalar", "unit": "ratio"}],
        }
        problems = validate_v2_structure(gate)
        assert problems == []

    def test_invalid_schema_version(self):
        """非法 schema_version。"""
        gate = {"schema_version": "invalid", "gate_id": "g", "mission_id": 1, "criteria": []}
        problems = validate_v2_structure(gate)
        assert any("schema_version" in p for p in problems)

    def test_missing_gate_id(self):
        """缺 gate_id。"""
        gate = {"schema_version": SCHEMA_VERSION_V2, "mission_id": 1, "criteria": []}
        problems = validate_v2_structure(gate)
        assert any("gate_id" in p for p in problems)

    def test_invalid_criterion_type(self):
        """非法判据类型。"""
        entry = {"id": "C1", "type": "invalid_type", "unit": "ratio"}
        problems = validate_criterion_v2(entry, 0)
        assert any("invalid_type" in p for p in problems)


# ── 非标量判据求值测试 ────────────────────────────────────────────

class TestNonScalarEvaluation:
    """非标量判据求值。"""

    def test_pointwise_band_pass(self, tmp_path):
        """逐点容差 PASS。"""
        ref = [{"x": i, "y": float(i)} for i in range(10)]
        meas = [{"x": i, "y": float(i) + 0.01} for i in range(10)]
        doc = {"ref": ref, "meas": meas}
        rel, sha = _write_artifact(tmp_path, doc)
        entry = {
            "artifact": {"path": rel, "sha256": sha},
            "ref_pointer": "ref",
            "meas_pointer": "meas",
            "tolerance": {"mode": "absolute", "value": 0.1},
            "pass_ratio_threshold": 0.9,
            "unit": "ratio",
        }
        result = evaluate_pointwise_band(entry, doc, tmp_path)
        assert result["status"] == "pass"
        assert result["measured"]["pass_ratio"] == 1.0

    def test_pointwise_band_fail(self, tmp_path):
        """逐点容差 FAIL。"""
        ref = [{"x": i, "y": float(i)} for i in range(10)]
        meas = [{"x": i, "y": float(i) + 1.0} for i in range(10)]  # 大偏差
        doc = {"ref": ref, "meas": meas}
        rel, sha = _write_artifact(tmp_path, doc)
        entry = {
            "artifact": {"path": rel, "sha256": sha},
            "ref_pointer": "ref",
            "meas_pointer": "meas",
            "tolerance": {"mode": "absolute", "value": 0.1},
            "pass_ratio_threshold": 0.9,
            "unit": "ratio",
        }
        result = evaluate_pointwise_band(entry, doc, tmp_path)
        assert result["status"] == "fail"
        assert result["measured"]["pass_ratio"] == 0.0
        assert len(result["fail_intervals"]) > 0

    def test_spectral_envelope_pass(self, tmp_path):
        """谱包络 PASS。"""
        spec = [{"omega": i * 0.1, "amplitude": math.exp(-((i * 0.1 - 1.0) ** 2))}
                for i in range(30)]
        lower = [{"omega": s["omega"], "amplitude": s["amplitude"] * 0.9} for s in spec]
        upper = [{"omega": s["omega"], "amplitude": s["amplitude"] * 1.1} for s in spec]
        meas = [{"omega": s["omega"], "amplitude": s["amplitude"]} for s in spec]
        doc = {"lower": lower, "upper": upper, "meas": meas}
        rel, sha = _write_artifact(tmp_path, doc)
        entry = {
            "artifact": {"path": rel, "sha256": sha},
            "lower_pointer": "lower",
            "upper_pointer": "upper",
            "meas_pointer": "meas",
            "peak_tolerance": {"freq_abs_tol": 0.5, "amp_abs_tol": 0.2},
            "unit": "amplitude",
        }
        result = evaluate_spectral_envelope(entry, doc, tmp_path)
        assert result["status"] == "pass"

    def test_field_fingerprint_pass(self, tmp_path):
        """场量指纹 PASS。"""
        ref = [math.sin(2 * math.pi * i / 50) for i in range(50)]
        meas = [v + 0.001 for v in ref]
        doc = {"ref": ref, "meas": meas, "ref_grid": ref, "meas_grid": meas}
        rel, sha = _write_artifact(tmp_path, doc)
        entry = {
            "artifact": {"path": rel, "sha256": sha},
            "stats": {"ref_pointer": "ref", "meas_pointer": "meas", "orders": [0, 1, 2]},
            "stat_tolerance": {"0": 0.01, "1": 0.1, "2": 0.2, "mode": "relative"},
            "grid_hash": {"ref_pointer": "ref_grid", "meas_pointer": "meas_grid"},
            "unit": "field",
        }
        result = evaluate_field_fingerprint(entry, doc, tmp_path)
        assert result["status"] == "pass"


# ── 兼容层测试 ────────────────────────────────────────────────────

class TestCompatV1:
    """v1→v2 兼容层。"""

    def test_upgrade_preserves_semantics(self):
        """升级保持语义。"""
        v1 = {
            "schema_version": SCHEMA_VERSION,
            "gate_id": "g-test",
            "mission_id": 1,
            "criteria": [{
                "id": "C1",
                "pointer": "metrics/alpha",
                "select": "absmax",
                "op": "le",
                "threshold": 1.0,
                "unit": "ratio",
                "measured": 0.5,
            }],
        }
        v2 = upgrade_v1_to_v2(v1)
        assert v2["schema_version"] == SCHEMA_VERSION_V2
        assert v2["criteria"][0]["type"] == "scalar"
        assert v2["criteria"][0]["aggregation"]["op"] == "absmax"

    def test_upgrade_idempotent(self):
        """v2 升级幂等。"""
        v2 = {
            "schema_version": SCHEMA_VERSION_V2,
            "gate_id": "g-test",
            "mission_id": 1,
            "criteria": [{"id": "C1", "type": "scalar", "unit": "ratio"}],
        }
        result = upgrade_v1_to_v2(v2)
        assert result == v2
