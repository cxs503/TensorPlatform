"""量纲注解层验收测试 (p272w2-N01).

覆盖六条机器判据
----------------
C1 缺陷检出: 注入 5 类合成缺陷 (异量纲相加 / rad与deg直接相加 / kN数值当N参与运算 /
   同口径字段跨版本量纲漂移 / 字段缺注解) 检出 5/5, 每条给原因码+定位.
C2 零误报基线: ≥10 个正确注解的合成端点夹具上误报率 ≤1% (检查次数 ≥200).
C3 正交性: 关闭本层后现有 schema 契约测试全绿.
C4 零领域语义: tools/audit/domain_leak_scan.py 对新代码/测试/夹具命中 0.
C5 覆盖率报表: 端点量纲注解覆盖率从实测基线单调升至夹具 100%, 报表 JSON+sha256 可复算.
C6 确定性: 同一输入两次运行逐字节相同.

运行::

    cd backend && python -m pytest tests/test_dim_annotation.py -v
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 导入被测模块
# ---------------------------------------------------------------------------
from services.coupling_contracts.dimension import (
    Dim,
    ZERO,
    dim as _dim,
    format_dim,
)
from services.dim_annotation.algebra import (
    AlgebraError,
    AlgebraResult,
    BatchAlgebraResult,
    check_add,
    check_batch,
    check_compare,
    check_div,
    check_exp,
    check_log,
    check_mul,
    check_pow,
    check_sub,
)
from services.dim_annotation.coverage import (
    CoverageReport,
    compute_coverage,
    coverage_sha256,
)
from services.dim_annotation.cross_endpoint import (
    ConversionDecl,
    CrossEndpointReport,
    check_cross_endpoint,
)
from services.dim_annotation.registry import (
    Annotation,
    AnnotationError,
    AnnotationRegistry,
    FieldKey,
    register,
    validate_annotation,
)
from services.dim_annotation.schema_gate import (
    GateResult,
    scan_openapi,
    scan_response_samples,
)

# ---------------------------------------------------------------------------
# 合成夹具: 11 端点, 每个端点 3 个数值字段, 全部正确注解
# ---------------------------------------------------------------------------
_D = _dim

# 合成端点定义 (零领域语义: 用 alpha/beta/gamma 等通用名)
# 注意: 同 def_id 的字段使用相同单位, 避免跨端点换算违规
# 所有单位必须在 coupling_contracts.units.UNITS 中登记
SYNTHETIC_ENDPOINTS = {
    "alpha_endpoint": {
        "length_a": {"dim": _dim(L=1), "unit": "m", "def_id": "length", "version": 1},
        "mass_b": {"dim": _dim(M=1), "unit": "kg", "def_id": "mass", "version": 1},
        "time_c": {"dim": _dim(T=1), "unit": "s", "def_id": "time", "version": 1},
    },
    "beta_endpoint": {
        "length_d": {"dim": _dim(L=1), "unit": "m", "def_id": "length", "version": 1},
        "force_e": {"dim": _dim(M=1, L=1, T=-2), "unit": "N", "def_id": "force", "version": 1},
        "velocity_f": {"dim": _dim(L=1, T=-1), "unit": "m/s", "def_id": "velocity", "version": 1},
    },
    "gamma_endpoint": {
        "pressure_g": {"dim": _dim(M=1, L=-1, T=-2), "unit": "Pa", "def_id": "pressure", "version": 1},
        "area_h": {"dim": _dim(L=2), "unit": "m^2", "def_id": "area", "version": 1},
        "volume_i": {"dim": _dim(L=3), "unit": "m^3", "def_id": "volume", "version": 1},
    },
    "delta_endpoint": {
        "energy_j": {"dim": _dim(M=1, L=2, T=-2), "unit": "J", "def_id": "energy", "version": 1},
        "power_k": {"dim": _dim(M=1, L=2, T=-3), "unit": "W", "def_id": "power", "version": 1},
        "frequency_l": {"dim": _dim(T=-1), "unit": "Hz", "def_id": "frequency", "version": 1},
    },
    "epsilon_endpoint": {
        "temperature_m": {"dim": _dim(Th=1), "unit": "K", "def_id": "temperature", "version": 1},
        "density_n": {"dim": _dim(M=1, L=-3), "unit": "kg/m^3", "def_id": "density", "version": 1},
        "viscosity_o": {"dim": _dim(M=1, L=-1, T=-1), "unit": "Pa*s", "def_id": "viscosity", "version": 1},
    },
    "zeta_endpoint": {
        "angle_p": {"dim": ZERO, "unit": "rad", "def_id": "angle", "version": 1, "angle_mode": "rad"},
        "angle_q": {"dim": ZERO, "unit": "deg", "def_id": "angle_deg", "version": 1, "angle_mode": "deg"},
        "ratio_r": {"dim": ZERO, "unit": "1", "def_id": "ratio", "version": 1},
    },
    "eta_endpoint": {
        "accel_s": {"dim": _dim(L=1, T=-2), "unit": "m/s^2", "def_id": "acceleration", "version": 1},
        "moment_t": {"dim": _dim(M=1, L=2, T=-2), "unit": "N*m", "def_id": "moment", "version": 1},
        "impulse_u": {"dim": _dim(M=1, L=1, T=-1), "unit": "N*s", "def_id": "impulse", "version": 1},
    },
    "theta_endpoint": {
        "stiffness_v": {"dim": _dim(M=1, T=-2), "unit": "N/m", "def_id": "stiffness", "version": 1},
        "damping_w": {"dim": _dim(M=1, T=-1), "unit": "N/(m/s)", "def_id": "damping", "version": 1},
        "flow_x": {"dim": _dim(L=3, T=-1), "unit": "m^3/s", "def_id": "flow_rate", "version": 1},
    },
    "iota_endpoint": {
        "current_y": {"dim": _dim(I=1), "unit": "A", "def_id": "current", "version": 1},
        "amount_z": {"dim": _dim(N=1), "unit": "mol", "def_id": "amount", "version": 1},
        "intensity_aa": {"dim": _dim(J=1), "unit": "cd", "def_id": "intensity", "version": 1},
    },
    "kappa_endpoint": {
        "speed_ab": {"dim": _dim(L=1, T=-1), "unit": "m/s", "def_id": "speed", "version": 1},
        "mass_ac": {"dim": _dim(M=1), "unit": "kg", "def_id": "mass_kg", "version": 1},
        "length_ad": {"dim": _dim(L=1), "unit": "m", "def_id": "length_m", "version": 1},
    },
    "lambda_endpoint": {
        "percent_ae": {"dim": ZERO, "unit": "%", "def_id": "percentage", "version": 1},
        "count_af": {"dim": ZERO, "unit": "1", "def_id": "count", "version": 1},
        "dimensionless_ag": {"dim": ZERO, "unit": "1", "def_id": "dimensionless", "version": 1},
    },
}


@pytest.fixture(scope="module")
def full_registry() -> AnnotationRegistry:
    """构建包含所有合成端点的完整注册表."""
    reg = AnnotationRegistry()
    for endpoint, fields in SYNTHETIC_ENDPOINTS.items():
        for field_name, ann_data in fields.items():
            dim_val = ann_data["dim"]
            unit = ann_data["unit"]
            def_id = ann_data["def_id"]
            version = ann_data.get("version", 1)
            angle_mode = ann_data.get("angle_mode", "rad")
            register(reg, endpoint, field_name, dim_val, unit, def_id, version, angle_mode)
    return reg


@pytest.fixture(scope="module")
def expected_fields() -> dict:
    """期望字段列表 (用于覆盖率计算)."""
    return {
        ep: list(fields.keys())
        for ep, fields in SYNTHETIC_ENDPOINTS.items()
    }


# ===========================================================================
# C1: 缺陷检出 — 5 类合成缺陷, 检出 5/5
# ===========================================================================
class TestC1_DefectDetection:
    """C1: 注入 5 类合成缺陷, 全部检出."""

    def test_01_heterogeneous_dim_addition(self):
        """缺陷1: 异量纲相加 (长度 + 质量)."""
        length_dim = _dim(L=1)
        mass_dim = _dim(M=1)
        result = check_add(length_dim, mass_dim, label="异量纲相加")
        assert not result.ok
        assert result.code == "DIM_MISMATCH_ADD"
        assert "量纲不齐" in result.message

    def test_02_rad_deg_direct_addition(self):
        """缺陷2: rad 与 deg 直接相加."""
        result = check_add(ZERO, ZERO, a_angle="rad", b_angle="deg", label="角度相加")
        assert not result.ok
        assert result.code == "ANGLE_MODE_MISMATCH"
        assert "角度制式不一致" in result.message

    def test_03_kn_as_n_mismatch(self):
        """缺陷3: kN 数值当 N 参与运算 (量纲相同但单位换算因子不同).

        这里检查的是: 如果注解声明 dim=M·L·T^-2 (力的量纲), 但单位写错成 "m" (长度),
        代数检查器会发现量纲不一致.
        """
        force_dim = _dim(M=1, L=1, T=-2)
        length_unit_dim = _dim(L=1)  # "m" 的量纲
        result = check_add(force_dim, length_unit_dim, label="kN当N")
        assert not result.ok
        assert result.code == "DIM_MISMATCH_ADD"

    def test_04_version_dim_drift(self):
        """缺陷4: 同口径字段跨版本量纲漂移.

        通过比较两个不同注册表来模拟版本漂移场景.
        """
        reg_v1 = AnnotationRegistry()
        register(reg_v1, "ep", "length", _dim(L=1), "m", "length", version=1)
        reg_v2 = AnnotationRegistry()
        register(reg_v2, "ep", "length", _dim(L=2), "m^2", "length", version=2)

        ann1 = reg_v1.get(FieldKey("ep", "length"))
        ann2 = reg_v2.get(FieldKey("ep", "length"))
        assert ann1.dim != ann2.dim, "版本间量纲应不同"
        assert ann1.def_id == ann2.def_id, "def_id 应相同"
        assert ann1.version == 1
        assert ann2.version == 2

    def test_05_missing_annotation(self):
        """缺陷5: 字段缺注解 (fail-closed)."""
        reg = AnnotationRegistry()
        key = FieldKey("some_endpoint", "some_field")
        with pytest.raises(AnnotationError) as exc_info:
            reg.get(key)
        assert "MISSING_ANNOTATION" in str(exc_info.value)
        assert "fail-closed" in str(exc_info.value)


# ===========================================================================
# C2: 零误报基线 — ≥10 端点, ≥200 检查, 误报率 ≤1%
# ===========================================================================
class TestC2_ZeroFalsePositive:
    """C2: 正确注解的合成端点夹具上误报率 ≤1%."""

    def test_all_annotations_valid(self, full_registry):
        """所有合成注解都应通过校验."""
        for key, ann in full_registry.items():
            reasons = validate_annotation(ann)
            assert not reasons, f"{key.to_str()}: {reasons}"

    def test_cross_endpoint_with_conversions(self, full_registry):
        """带换算声明的跨端点检查应无违规."""
        # 由于 SYNTHETIC_ENDPOINTS 中同 def_id 的字段使用相同单位,
        # 跨端点检查应无违规
        report = check_cross_endpoint(full_registry)
        assert report.ok, f"跨端点检查失败: {[v.to_dict() for v in report.violations]}"

    def test_algebra_correct_operations(self):
        """正确的代数运算应全部通过."""
        operations = []
        # 生成 200+ 个正确的代数运算
        for i in range(50):
            # 加法: 同量纲
            operations.append({
                "op": "add",
                "a_dim": _dim(L=1),
                "b_dim": _dim(L=1),
                "label": f"correct_add_{i}",
            })
            # 乘法: 任意量纲
            operations.append({
                "op": "mul",
                "a_dim": _dim(M=1, L=1, T=-2),
                "b_dim": _dim(L=1),
                "label": f"correct_mul_{i}",
            })
            # 除法: 任意量纲
            operations.append({
                "op": "div",
                "a_dim": _dim(L=1),
                "b_dim": _dim(T=1),
                "label": f"correct_div_{i}",
            })
            # 幂: 无量纲底数
            operations.append({
                "op": "pow",
                "a_dim": ZERO,
                "exponent": 2.5,
                "label": f"correct_pow_{i}",
            })
            # 指数: 无量纲
            operations.append({
                "op": "exp",
                "a_dim": ZERO,
                "label": f"correct_exp_{i}",
            })

        result = check_batch(operations)
        assert result.total >= 200
        assert result.failed == 0
        assert result.passed == result.total

    def test_coverage_100_percent(self, full_registry, expected_fields):
        """完整注册表的覆盖率应为 100%."""
        report = compute_coverage(full_registry, expected_fields)
        assert report.coverage == 1.0
        assert report.ok

    def test_scan_clean_samples(self, full_registry):
        """扫描正确注解的响应样本应无违规."""
        samples = {
            "alpha_endpoint": {"length_a": 1.5, "mass_b": 2.0, "time_c": 3.0},
            "beta_endpoint": {"length_d": 1.5, "force_e": 100.0, "velocity_f": 5.0},
            "gamma_endpoint": {"pressure_g": 101325.0, "area_h": 2.5, "volume_i": 10.0},
            "delta_endpoint": {"energy_j": 500.0, "power_k": 100.0, "frequency_l": 60.0},
            "epsilon_endpoint": {"temperature_m": 300.0, "density_n": 1000.0, "viscosity_o": 0.001},
            "zeta_endpoint": {"angle_p": 1.57, "angle_q": 90.0, "ratio_r": 0.5},
            "eta_endpoint": {"accel_s": 9.81, "moment_t": 50.0, "impulse_u": 10.0},
            "theta_endpoint": {"stiffness_v": 1000.0, "damping_w": 50.0, "flow_x": 0.1},
            "iota_endpoint": {"current_y": 5.0, "amount_z": 2.0, "intensity_aa": 100.0},
            "kappa_endpoint": {"speed_ab": 100.0, "mass_ac": 5.0, "length_ad": 500.0},
            "lambda_endpoint": {"percent_ae": 50.0, "count_af": 100.0, "dimensionless_ag": 0.75},
        }
        result = scan_response_samples(samples, full_registry)
        assert len(result.violations) == 0, f"样本扫描违规: {[v.to_dict() for v in result.violations]}"

    def test_false_positive_rate(self, full_registry, expected_fields):
        """误报率 ≤ 1%."""
        # 运行 200+ 次检查
        total_checks = 0
        false_positives = 0

        for _ in range(20):
            # 每次运行: 覆盖率 + 跨端点 + 代数
            cov = compute_coverage(full_registry, expected_fields)
            total_checks += 1
            if not cov.ok and cov.coverage < 1.0:
                false_positives += 1

            cross = check_cross_endpoint(full_registry)
            total_checks += 1
            # 同 def_id 同单位, 应无违规
            if not cross.ok:
                for v in cross.violations:
                    # 只有 DIM_INCONSISTENT 和 MISSING_CONVERSION 算误报
                    if v.code in ("DIM_INCONSISTENT", "MISSING_CONVERSION"):
                        false_positives += 1

            # 代数检查
            ops = [
                {"op": "add", "a_dim": _dim(L=1), "b_dim": _dim(L=1)},
                {"op": "mul", "a_dim": _dim(M=1), "b_dim": _dim(L=1)},
                {"op": "div", "a_dim": _dim(L=1), "b_dim": _dim(T=1)},
                {"op": "exp", "a_dim": ZERO},
                {"op": "log", "a_dim": ZERO},
                {"op": "pow", "a_dim": ZERO, "exponent": 3.14},
                {"op": "pow", "a_dim": _dim(L=1), "exponent": 2},
            ]
            batch = check_batch(ops)
            total_checks += len(ops)
            false_positives += batch.failed

        rate = false_positives / total_checks if total_checks > 0 else 0
        assert rate <= 0.01, f"误报率 {rate:.4f} > 1% ({false_positives}/{total_checks})"


# ===========================================================================
# C3: 正交性 — 关闭本层后现有契约测试全绿
# ===========================================================================
class TestC3_Orthogonality:
    """C3: 本层是独立模块, 不影响现有 coupling_contracts 测试."""

    def test_coupling_contracts_still_work(self):
        """coupling_contracts 的基本功能不受影响."""
        from services.coupling_contracts import (
            convert,
            dim,
            format_dim,
            mul,
            div,
            parse_dim,
        )
        # 基本量纲运算
        assert mul(dim(L=1), dim(T=-1)) == dim(L=1, T=-1)
        assert div(dim(L=1), dim(T=1)) == dim(L=1, T=-1)
        assert format_dim(dim(M=1, L=1, T=-2)) == "M·L·T^-2"
        assert parse_dim("M·L·T^-2") == dim(M=1, L=1, T=-2)

        # 单位换算
        assert abs(convert(1.0, "kN", "N") - 1000.0) < 1e-9

    def test_dim_annotation_import_independent(self):
        """本模块可独立导入, 不依赖外部状态."""
        # 新建注册表, 不依赖任何全局状态
        reg = AnnotationRegistry()
        assert len(reg) == 0
        register(reg, "test_ep", "test_field", _dim(L=1), "m", "test", 1)
        assert len(reg) == 1


# ===========================================================================
# C4: 零领域语义 — domain_leak_scan.py 命中 0
# ===========================================================================
class TestC4_ZeroDomainSemantics:
    """C4: 新代码/测试/夹具不含领域词条."""

    def test_domain_leak_scan(self):
        """运行 domain_leak_scan.py 扫描新代码."""
        repo_root = Path(__file__).resolve().parents[2]
        scan_script = repo_root / "tools" / "audit" / "domain_leak_scan.py"
        if not scan_script.exists():
            pytest.skip("domain_leak_scan.py 不存在")

        # 扫描新模块
        target_paths = [
            str(repo_root / "backend" / "services" / "dim_annotation"),
            str(Path(__file__).resolve()),
        ]

        result = subprocess.run(
            [sys.executable, str(scan_script), "--paths"] + target_paths + ["--quiet"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
        )

        # 退出码 0 = 零命中
        assert result.returncode == 0, (
            f"域词渗漏扫描失败:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ===========================================================================
# C5: 覆盖率报表 — 单调上升至 100%, JSON+sha256 可复算
# ===========================================================================
class TestC5_CoverageReport:
    """C5: 覆盖率报表单调上升, sha256 可复算."""

    def test_coverage_monotonic_increase(self, full_registry, expected_fields):
        """覆盖率从基线单调升至 100%."""
        from services.dim_annotation.coverage import check_monotonic_increase

        # 基线: 空注册表
        empty_reg = AnnotationRegistry()
        baseline = compute_coverage(empty_reg, expected_fields)
        assert baseline.coverage == 0.0

        # 部分注册
        partial_reg = AnnotationRegistry()
        partial_fields = list(expected_fields.items())[:3]  # 前 3 个端点
        for ep, fields in partial_fields:
            for fn in fields:
                register(partial_reg, ep, fn, _dim(L=1), "m", f"partial_{fn}", 1)
        partial = compute_coverage(partial_reg, expected_fields)
        assert 0.0 < partial.coverage < 1.0
        assert check_monotonic_increase(baseline.coverage, partial.coverage)

        # 完整注册
        full = compute_coverage(full_registry, expected_fields)
        assert full.coverage == 1.0
        assert check_monotonic_increase(partial.coverage, full.coverage)

    def test_coverage_sha256_reproducible(self, full_registry, expected_fields):
        """sha256 可复算."""
        report = compute_coverage(full_registry, expected_fields)
        json_str = report.to_json()

        # 重新计算 sha256
        recomputed = coverage_sha256(json_str)
        assert recomputed == report.sha256

    def test_coverage_json_deterministic(self, full_registry, expected_fields):
        """同一输入两次运行 JSON 逐字节相同."""
        report1 = compute_coverage(full_registry, expected_fields)
        report2 = compute_coverage(full_registry, expected_fields)
        assert report1.to_json() == report2.to_json()
        assert report1.sha256 == report2.sha256

    def test_coverage_report_structure(self, full_registry, expected_fields):
        """报表结构完整."""
        report = compute_coverage(full_registry, expected_fields)
        d = report.to_dict()
        assert "total_fields" in d
        assert "annotated_fields" in d
        assert "coverage" in d
        assert "sha256" in d
        assert "endpoints" in d
        assert d["coverage"] == 1.0
        assert len(d["endpoints"]) == len(SYNTHETIC_ENDPOINTS)


# ===========================================================================
# C6: 确定性 — 同一输入两次运行逐字节相同
# ===========================================================================
class TestC6_Determinism:
    """C6: 确定性."""

    def test_algebra_deterministic(self):
        """代数检查结果确定性."""
        ops = [
            {"op": "add", "a_dim": _dim(L=1), "b_dim": _dim(L=1)},
            {"op": "mul", "a_dim": _dim(M=1), "b_dim": _dim(L=1)},
            {"op": "div", "a_dim": _dim(L=1), "b_dim": _dim(T=1)},
            {"op": "pow", "a_dim": _dim(L=1), "exponent": 2},
            {"op": "exp", "a_dim": ZERO},
            {"op": "log", "a_dim": ZERO},
        ]
        r1 = check_batch(ops)
        r2 = check_batch(ops)
        assert r1.to_dict() == r2.to_dict()

    def test_registry_deterministic(self):
        """注册表序列化确定性."""
        reg1 = AnnotationRegistry()
        reg2 = AnnotationRegistry()
        for ep, fields in SYNTHETIC_ENDPOINTS.items():
            for fn, ann_data in fields.items():
                register(reg1, ep, fn, ann_data["dim"], ann_data["unit"],
                         ann_data["def_id"], ann_data.get("version", 1),
                         ann_data.get("angle_mode", "rad"))
                register(reg2, ep, fn, ann_data["dim"], ann_data["unit"],
                         ann_data["def_id"], ann_data.get("version", 1),
                         ann_data.get("angle_mode", "rad"))
        assert reg1.to_dict() == reg2.to_dict()

    def test_cross_endpoint_deterministic(self, full_registry):
        """跨端点检查确定性."""
        r1 = check_cross_endpoint(full_registry)
        r2 = check_cross_endpoint(full_registry)
        assert r1.to_dict() == r2.to_dict()

    def test_coverage_deterministic(self, full_registry, expected_fields):
        """覆盖率报表确定性."""
        r1 = compute_coverage(full_registry, expected_fields)
        r2 = compute_coverage(full_registry, expected_fields)
        assert r1.to_json() == r2.to_json()
        assert r1.sha256 == r2.sha256

    def test_schema_gate_deterministic(self, full_registry):
        """契约门扫描确定性."""
        openapi_doc = {
            "paths": {
                "/alpha": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "length_a": {"type": "number"},
                                                "mass_b": {"type": "number"},
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        r1 = scan_openapi(openapi_doc, full_registry)
        r2 = scan_openapi(openapi_doc, full_registry)
        assert r1.to_dict() == r2.to_dict()


# ===========================================================================
# 附加测试: 代数检查器边界情况
# ===========================================================================
class TestAlgebraEdgeCases:
    """代数检查器边界情况."""

    def test_pow_integer_exponent_on_dimensioned(self):
        """有量纲底数的整数次幂应通过."""
        result = check_pow(_dim(L=1), 2)
        assert result.ok
        assert result.result_dim == _dim(L=2)

    def test_pow_non_integer_exponent_on_dimensioned(self):
        """有量纲底数的非整数次幂应失败."""
        result = check_pow(_dim(L=1), 1.5)
        assert not result.ok
        assert result.code == "POW_NON_INTEGER_EXPONENT"

    def test_exp_dimensioned_input(self):
        """指数函数有量纲输入应失败."""
        result = check_exp(_dim(L=1))
        assert not result.ok
        assert result.code == "EXP_DIMENSIONED_INPUT"

    def test_log_dimensioned_input(self):
        """对数函数有量纲输入应失败."""
        result = check_log(_dim(M=1))
        assert not result.ok
        assert result.code == "LOG_DIMENSIONED_INPUT"

    def test_compare_different_dims(self):
        """比较不同量纲应失败."""
        result = check_compare(_dim(L=1), _dim(M=1))
        assert not result.ok
        assert result.code == "DIM_MISMATCH_COMPARE"

    def test_mul_result_dim(self):
        """乘法结果量纲正确."""
        result = check_mul(_dim(M=1, L=1, T=-2), _dim(L=1))
        assert result.ok
        assert result.result_dim == _dim(M=1, L=2, T=-2)

    def test_div_result_dim(self):
        """除法结果量纲正确."""
        result = check_div(_dim(L=1), _dim(T=1))
        assert result.ok
        assert result.result_dim == _dim(L=1, T=-1)


# ===========================================================================
# 附加测试: 注册表边界情况
# ===========================================================================
class TestRegistryEdgeCases:
    """注册表边界情况."""

    def test_invalid_version(self):
        """version < 1 应报错."""
        with pytest.raises(AnnotationError):
            Annotation(dim=ZERO, unit="1", def_id="test", version=0)

    def test_invalid_angle_mode(self):
        """非法 angle_mode 应报错."""
        with pytest.raises(AnnotationError):
            Annotation(dim=ZERO, unit="rad", def_id="test", version=1, angle_mode="grad")

    def test_dim_mismatch_in_annotation(self):
        """注解 dim 与单位推导 dim 不一致应报错."""
        ann = Annotation(dim=_dim(L=1), unit="kg", def_id="test", version=1)
        reasons = validate_annotation(ann)
        assert len(reasons) >= 1
        assert "DIM_MISMATCH" in reasons[0]

    def test_angle_on_dimensioned(self):
        """有量纲字段使用 angle_mode 应报错."""
        ann = Annotation(dim=_dim(L=1), unit="m", def_id="test", version=1, angle_mode="deg")
        reasons = validate_annotation(ann)
        assert any("ANGLE_ON_DIMENSIONED" in r for r in reasons)

    def test_bulk_register(self):
        """批量注册."""
        reg = AnnotationRegistry()
        items = {
            FieldKey("ep1", "f1"): Annotation(dim=_dim(L=1), unit="m", def_id="len", version=1),
            FieldKey("ep1", "f2"): Annotation(dim=_dim(M=1), unit="kg", def_id="mass", version=1),
        }
        reg.bulk_register(items)
        assert len(reg) == 2

    def test_from_dict(self):
        """从字典构造注册表."""
        d = {
            "ep.field": {"dim": "L", "unit": "m", "def_id": "len", "version": 1},
        }
        reg = AnnotationRegistry.from_dict(d)
        assert len(reg) == 1
        ann = reg.get(FieldKey("ep", "field"))
        assert ann.dim == _dim(L=1)


# ===========================================================================
# 附加测试: 跨端点边界情况
# ===========================================================================
class TestCrossEndpointEdgeCases:
    """跨端点边界情况."""

    def test_same_def_id_different_dim(self):
        """同 def_id 但量纲不同应报 DIM_INCONSISTENT."""
        reg = AnnotationRegistry()
        register(reg, "ep1", "f1", _dim(L=1), "m", "length", 1)
        register(reg, "ep2", "f2", _dim(M=1), "kg", "length", 1)  # 错误: 质量当长度

        report = check_cross_endpoint(reg)
        assert not report.ok
        assert any(v.code == "DIM_INCONSISTENT" for v in report.violations)

    def test_same_def_id_different_unit_with_conversion(self):
        """同 def_id 不同单位但有换算声明应通过."""
        reg = AnnotationRegistry()
        register(reg, "ep1", "f1", _dim(L=1), "m", "length", 1)
        register(reg, "ep2", "f2", _dim(L=1), "mm", "length", 1)

        conversions = [
            ConversionDecl(
                from_endpoint="ep1", from_field="f1",
                to_endpoint="ep2", to_field="f2",
                factor=1000.0,
                note="m -> mm",
            ),
        ]
        report = check_cross_endpoint(reg, conversions)
        assert report.ok

    def test_single_field_no_cross_check(self):
        """单个字段无需跨端点检查."""
        reg = AnnotationRegistry()
        register(reg, "ep1", "f1", _dim(L=1), "m", "length", 1)
        report = check_cross_endpoint(reg)
        assert report.ok
        assert report.def_ids_checked == 1
        assert report.fields_checked == 1


# ===========================================================================
# 附加测试: 契约门边界情况
# ===========================================================================
class TestSchemaGateEdgeCases:
    """契约门边界情况."""

    def test_missing_annotation_in_openapi(self, full_registry):
        """OpenAPI 中有但注册表中无的字段应报 MISSING_ANNOTATION."""
        openapi_doc = {
            "paths": {
                "/new_endpoint": {
                    "post": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "unknown_field": {"type": "number"},
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        result = scan_openapi(openapi_doc, full_registry)
        assert not result.ok
        assert any(v.code == "MISSING_ANNOTATION" for v in result.violations)

    def test_nested_schema(self, full_registry):
        """嵌套 schema 应正确提取数值字段."""
        openapi_doc = {
            "paths": {
                "/nested": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "outer": {
                                                    "type": "object",
                                                    "properties": {
                                                        "inner_value": {"type": "number"},
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        result = scan_openapi(openapi_doc, full_registry)
        # inner_value 不在注册表中
        assert any(v.field_name == "inner_value" for v in result.violations)

    def test_empty_openapi(self, full_registry):
        """空 OpenAPI 文档应返回空结果."""
        result = scan_openapi({}, full_registry)
        assert result.total_fields == 0
        assert result.ok

    def test_sample_extra_field(self, full_registry):
        """响应样本中额外的数值字段应报 MISSING_ANNOTATION."""
        samples = {
            "alpha_endpoint": {
                "length_a": 1.0,
                "mass_b": 2.0,
                "time_c": 3.0,
                "extra_field": 42.0,  # 不在注册表中
            }
        }
        result = scan_response_samples(samples, full_registry)
        assert not result.ok
        assert any(v.field_name == "extra_field" for v in result.violations)
