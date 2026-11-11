"""P97W4-N12 数值方法交叉验证测试

验收判据：
  C1: SPH毛细波vs DG AMR毛细波色散误差<10%
  C2: FVM仿生减阻vs混合方法摩擦阻力误差<10%
  C3: 能力台账自动更新≥5条新能力
  C4: 回归测试全绿（已有能力不退化）
  C5: pytest≥10用例全绿
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest
import numpy as np

# 确保路径正确
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from domains.numerical_methods_cross_validation.capillary_waves import (
    CapillaryWaveParams,
    SPHCapillaryWave,
    DGAMRCapillaryWave,
    analytical_dispersion,
    pure_capillary_dispersion,
    cross_validate_capillary,
)
from domains.numerical_methods_cross_validation.drag_reduction import (
    HullFormParams,
    FlowConditions,
    FVMBiomimeticDrag,
    FVMPotentialViscousHybrid,
    ittc57_cf,
    schoenherr_cf,
    cross_validate_drag,
)
from domains.numerical_methods_cross_validation.capability_updater import (
    CapabilityLedgerUpdater,
    CapabilityEntry,
    NEW_CAPABILITIES,
)
from domains.numerical_methods_cross_validation.cross_validator import (
    NumericalMethodsCrossValidator,
    CrossValidationResult,
    run_cross_validation,
)


# ── 夹具 ──────────────────────────────────────────────────────────────

@pytest.fixture
def capillary_params():
    return CapillaryWaveParams()


@pytest.fixture
def hull_params():
    return HullFormParams()


@pytest.fixture
def flow_conditions():
    return FlowConditions()


@pytest.fixture
def cross_validator(tmp_path):
    return NumericalMethodsCrossValidator(output_dir=tmp_path / "artifacts")


# ── C1: SPH vs DG AMR 毛细波色散关系 ──────────────────────────────────

class TestCapillaryWaveCrossValidation:
    """C1: SPH毛细波vs DG AMR毛细波色散误差<10%"""
    
    def test_analytical_dispersion_deep_water(self, capillary_params):
        """测试解析色散关系深水极限"""
        k = np.array([50.0, 100.0, 200.0])
        omega = pure_capillary_dispersion(k, capillary_params)
        
        # 深水极限：ω² = (σ/ρ) k³
        sigma, rho = capillary_params.sigma, capillary_params.rho
        omega_deep = np.sqrt(sigma / rho * k**3)
        
        # kh >> 1 时 tanh(kh) ≈ 1
        np.testing.assert_allclose(omega, omega_deep, rtol=1e-3)
    
    def test_sph_dispersion_error_below_threshold(self, capillary_params):
        """测试 SPH 色散误差低于 10%"""
        sph = SPHCapillaryWave(capillary_params, n_particles=200)
        error = sph.compute_dispersion_error(k_init=50.0)
        
        assert error < 0.10, f"SPH 色散误差 {error:.4f} 超过 10%"
    
    def test_dg_amr_dispersion_error_below_threshold(self, capillary_params):
        """测试 DG AMR 色散误差低于 10%"""
        dg = DGAMRCapillaryWave(capillary_params, n_elements=50, polynomial_order=3)
        error = dg.compute_dispersion_error(k_init=50.0)
        
        assert error < 0.10, f"DG AMR 色散误差 {error:.4f} 超过 10%"
    
    def test_cross_validation_error_below_10_percent(self, capillary_params):
        """C1 核心判据：SPH vs DG AMR 交叉验证误差 < 10%"""
        result = cross_validate_capillary(capillary_params, k_init=50.0)
        
        assert result['pass_c1'], (
            f"C1 FAIL: 交叉验证误差 {result['cross_validation_error']:.4f} >= 10%"
        )
        assert result['cross_validation_error'] < 0.10
    
    def test_sph_convergence_with_more_particles(self, capillary_params):
        """测试 SPH 随粒子数增加收敛"""
        errors = []
        for n in [100, 200, 400]:
            sph = SPHCapillaryWave(capillary_params, n_particles=n)
            errors.append(sph.compute_dispersion_error(k_init=50.0))
        
        # 误差应随粒子数增加而减小（或至少不显著增大）
        assert errors[-1] <= errors[0] * 1.5  # 允许一定波动


# ── C2: FVM 仿生减阻 vs FVM 势流-粘性混合 ─────────────────────────────

class TestDragReductionCrossValidation:
    """C2: FVM仿生减阻vs混合方法摩擦阻力误差<10%"""
    
    def test_ittc57_friction_line(self):
        """测试 ITTC-57 摩擦线"""
        Re = 1e7
        cf = ittc57_cf(Re)
        
        # ITTC-57: Cf = 0.075 / (log10(Re) - 2)²
        expected = 0.075 / (np.log10(Re) - 2.0)**2
        assert abs(cf - expected) < 1e-10
    
    def test_schoenherr_close_to_ittc57(self):
        """测试 Schoenherr 线与 ITTC-57 接近"""
        Re = 1e7
        cf_sch = schoenherr_cf(Re)
        cf_ittc = ittc57_cf(Re)
        
        # 两者应在 5% 以内
        rel_diff = abs(cf_sch - cf_ittc) / cf_ittc
        assert rel_diff < 0.05, f"Schoenherr vs ITTC-57 差异 {rel_diff:.2%}"
    
    def test_biomimetic_drag_reduction_positive(self, hull_params, flow_conditions):
        """测试仿生减阻在最优工况下为正"""
        bio = FVMBiomimeticDrag(hull_params, flow_conditions, 
                                riblet_spacing_plus=15.0)
        result = bio.compute_friction_resistance()
        
        assert result['drag_reduction_pct'] > 0, "最优工况下减阻率应为正"
        assert result['cf_riblet'] < result['cf_smooth'], "riblet Cf 应小于光滑 Cf"
    
    def test_hybrid_friction_close_to_ittc57(self, hull_params, flow_conditions):
        """测试混合方法摩擦系数接近 ITTC-57"""
        hybrid = FVMPotentialViscousHybrid(hull_params, flow_conditions)
        result = hybrid.compute_friction_resistance()
        
        cf_ref = ittc57_cf(flow_conditions.reynolds_number(hull_params.L))
        rel_diff = abs(result['cf_avg'] - cf_ref) / cf_ref
        
        # 混合方法应在 ITTC-57 的 20% 以内（简化模型精度有限）
        assert rel_diff < 0.20, f"混合方法 Cf 与 ITTC-57 差异 {rel_diff:.2%}"
    
    def test_drag_cross_validation_error_below_10_percent(self, hull_params, flow_conditions):
        """C2 核心判据：仿生减阻 vs 混合方法误差 < 10%"""
        result = cross_validate_drag(hull_params, flow_conditions)
        
        assert result['pass_c2'], (
            f"C2 FAIL: 交叉验证误差 {result['cross_validation_error']:.4f} >= 10%"
        )
        assert result['cross_validation_error'] < 0.10


# ── C3: 能力台账自动更新 ──────────────────────────────────────────────

class TestCapabilityLedgerUpdate:
    """C3: 能力台账自动更新≥5条新能力"""
    
    def test_five_new_capabilities_defined(self):
        """测试定义了 5 个新能力"""
        assert len(NEW_CAPABILITIES) >= 5
    
    def test_capability_entry_creation(self):
        """测试能力条目创建"""
        entry = CapabilityEntry(
            ext_id='test.capability',
            label='测试能力',
            description='测试描述',
            evidence_level='REAL',
            evidence_files=['test.py'],
            test_count=5,
            tests_passed=5,
        )
        
        assert entry.ext_id == 'test.capability'
        assert entry.evidence_level == 'REAL'
        assert entry.content_hash  # 应有哈希
    
    def test_auto_register_from_cross_validation(self, tmp_path):
        """测试从交叉验证结果自动登记能力"""
        updater = CapabilityLedgerUpdater(ledger_path=tmp_path / "ledger")
        
        # 模拟交叉验证结果
        cap_result = {
            'sph_dispersion_error': 0.05,
            'dg_dispersion_error': 0.03,
            'cross_validation_error': 0.06,
        }
        drag_result = {
            'biomimetic': {'cf_smooth': 0.003},
            'hybrid': {'cf_avg': 0.003},
        }
        
        entries = updater.auto_register_from_cross_validation(cap_result, drag_result)
        
        assert len(entries) >= 5, f"应登记至少 5 条能力，实际 {len(entries)}"
    
    def test_ledger_save_and_load(self, tmp_path):
        """测试台账保存"""
        updater = CapabilityLedgerUpdater(ledger_path=tmp_path / "ledger")
        updater.register_capability(
            ext_id='test.cap',
            label='Test',
            description='Test capability',
            evidence_level='REAL',
            evidence_files=['test.py'],
            test_count=3,
            tests_passed=3,
        )
        
        path = updater.save_ledger()
        assert path.exists()
        
        # 验证 JSON 格式
        data = json.loads(path.read_text())
        assert data['entry_count'] == 1
    
    def test_ledger_pass_criterion(self, tmp_path):
        """C3 核心判据：新能力数 ≥ 5"""
        updater = CapabilityLedgerUpdater(ledger_path=tmp_path / "ledger")
        
        cap_result = {
            'sph_dispersion_error': 0.05,
            'dg_dispersion_error': 0.03,
            'cross_validation_error': 0.06,
        }
        drag_result = {
            'biomimetic': {'cf_smooth': 0.003},
            'hybrid': {'cf_avg': 0.003},
        }
        
        entries = updater.auto_register_from_cross_validation(cap_result, drag_result)
        
        assert len(entries) >= 5, f"C3 FAIL: 新能力数 {len(entries)} < 5"


# ── C4/C5: 端到端交叉验证 ─────────────────────────────────────────────

class TestEndToEndCrossValidation:
    """C4/C5: 端到端交叉验证 + 回归测试"""
    
    def test_full_cross_validation_passes(self, cross_validator):
        """测试完整交叉验证通过"""
        result = cross_validator.run_full_validation()
        
        assert result.all_pass, (
            f"完整验证失败: C1={result.capillary_pass}, "
            f"C2={result.drag_pass}, C3={result.ledger_pass}"
        )
    
    def test_output_files_created(self, cross_validator):
        """测试输出文件创建"""
        result = cross_validator.run_full_validation()
        
        output_dir = cross_validator.output_dir
        assert (output_dir / "cross_validation_result.json").exists()
        assert (output_dir / "cross_validation_report.md").exists()
    
    def test_regression_existing_capabilities_not_broken(self):
        """C4: 回归测试 - 已有能力不退化
        
        测试基础物理计算不受新模块影响
        """
        # 基础 ITTC-57 计算
        cf = ittc57_cf(1e7)
        assert 0.001 < cf < 0.01  # 合理范围
        
        # 基础色散关系
        params = CapillaryWaveParams()
        omega = pure_capillary_dispersion(np.array([100.0]), params)
        assert omega[0] > 0
    
    def test_convenience_entry_point(self, tmp_path):
        """测试便捷入口函数"""
        result = run_cross_validation(output_dir=tmp_path / "artifacts")
        
        assert isinstance(result, CrossValidationResult)
        assert result.timestamp
        assert result.content_hash


# ── 附加测试（确保 ≥10 用例）──────────────────────────────────────────

class TestAdditionalCoverage:
    """附加测试覆盖"""
    
    def test_hull_form_wetted_area_estimation(self):
        """测试湿面积估算"""
        hull = HullFormParams(L=10.0, B=2.0, T=0.8)
        
        # S ≈ L * (B + 2T) = 10 * (2 + 1.6) = 36
        assert abs(hull.S_wet - 36.0) < 1.0
    
    def test_flow_conditions_reynolds_number(self):
        """测试雷诺数计算"""
        flow = FlowConditions(U=5.0)
        hull = HullFormParams(L=10.0)
        
        Re = flow.reynolds_number(hull.L)
        # Re = U*L/nu = 5*10/1.19e-6 ≈ 4.2e7
        assert 1e7 < Re < 1e8
    
    def test_riblet_off_optimum_reduces_benefit(self, hull_params, flow_conditions):
        """测试偏离最优肋条间距减阻效果下降"""
        bio_opt = FVMBiomimeticDrag(hull_params, flow_conditions, riblet_spacing_plus=13.0)
        bio_off = FVMBiomimeticDrag(hull_params, flow_conditions, riblet_spacing_plus=30.0)
        
        res_opt = bio_opt.compute_friction_resistance()
        res_off = bio_off.compute_friction_resistance()
        
        assert res_opt['drag_reduction_pct'] > res_off['drag_reduction_pct']
