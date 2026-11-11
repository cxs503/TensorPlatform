"""数值方法交叉验证框架

本模块提供统一的交叉验证接口，协调：
  1. SPH vs DG AMR 毛细波色散关系对比
  2. FVM 仿生减阻 vs FVM 势流-粘性混合 摩擦阻力对比
  3. 能力台账自动更新
  4. 回归测试（确保新能力不破坏已有能力）

验收判据：
  C1: SPH毛细波vs DG AMR毛细波色散误差<10%
  C2: FVM仿生减阻vs混合方法摩擦阻力误差<10%
  C3: 能力台账自动更新≥5条新能力
  C4: 回归测试全绿
  C5: pytest≥10用例全绿
"""

from __future__ import annotations

import json
import numpy as np
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

from .capillary_waves import (
    CapillaryWaveParams, cross_validate_capillary,
    SPHCapillaryWave, DGAMRCapillaryWave,
    analytical_dispersion, pure_capillary_dispersion,
)
from .drag_reduction import (
    HullFormParams, FlowConditions, cross_validate_drag,
    FVMBiomimeticDrag, FVMPotentialViscousHybrid,
    ittc57_cf, schoenherr_cf,
)
from .capability_updater import CapabilityLedgerUpdater

class NumpyEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy types"""
    def default(self, obj):
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)




@dataclass
class CrossValidationResult:
    """交叉验证结果"""
    # 毛细波交叉验证
    capillary_cross_error: float
    sph_dispersion_error: float
    dg_dispersion_error: float
    capillary_pass: bool
    
    # 减阻交叉验证
    drag_cross_error: float
    biomimetic_vs_reference: float
    hybrid_vs_reference: float
    drag_pass: bool
    
    # 能力台账
    new_capabilities_count: int
    capability_entries: List[Dict]
    ledger_pass: bool
    
    # 总体
    all_pass: bool
    timestamp: str = ""
    content_hash: str = ""
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()
        content = json.dumps({
            'capillary_cross_error': self.capillary_cross_error,
            'drag_cross_error': self.drag_cross_error,
            'new_capabilities_count': self.new_capabilities_count,
        }, sort_keys=True)
        self.content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]


class NumericalMethodsCrossValidator:
    """数值方法交叉验证器"""
    
    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path("artifacts/p97w4_n12")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
    def run_capillary_validation(self, 
                                  params: CapillaryWaveParams = None,
                                  k_init: float = 50.0,
                                  sph_particles: int = 200,
                                  dg_elements: int = 50) -> Dict:
        """运行毛细波交叉验证"""
        return cross_validate_capillary(params, k_init, sph_particles, dg_elements)
    
    def run_drag_validation(self,
                            hull: HullFormParams = None,
                            flow: FlowConditions = None,
                            riblet_s_plus: float = 15.0) -> Dict:
        """运行减阻交叉验证"""
        return cross_validate_drag(hull, flow, riblet_s_plus)
    
    def run_full_validation(self) -> CrossValidationResult:
        """运行完整交叉验证流程"""
        # 1. 毛细波交叉验证
        cap_result = self.run_capillary_validation()
        
        # 2. 减阻交叉验证
        drag_result = self.run_drag_validation()
        
        # 3. 能力台账自动更新
        updater = CapabilityLedgerUpdater(
            ledger_path=self.output_dir / "capability_ledger"
        )
        entries = updater.auto_register_from_cross_validation(cap_result, drag_result)
        ledger_path = updater.save_ledger()
        
        # 4. 汇总结果
        result = CrossValidationResult(
            capillary_cross_error=cap_result['cross_validation_error'],
            sph_dispersion_error=cap_result['sph_dispersion_error'],
            dg_dispersion_error=cap_result['dg_dispersion_error'],
            capillary_pass=cap_result['pass_c1'],
            drag_cross_error=drag_result['cross_validation_error'],
            biomimetic_vs_reference=drag_result['biomimetic_vs_reference'],
            hybrid_vs_reference=drag_result['hybrid_vs_reference'],
            drag_pass=drag_result['pass_c2'],
            new_capabilities_count=len(entries),
            capability_entries=[asdict(e) for e in entries],
            ledger_pass=len(entries) >= 5,
            all_pass=(cap_result['pass_c1'] and 
                     drag_result['pass_c2'] and 
                     len(entries) >= 5),
        )
        
        # 5. 保存结果
        self._save_results(result, cap_result, drag_result, ledger_path)
        
        return result
    
    def _save_results(self, result: CrossValidationResult,
                      cap_result: Dict, drag_result: Dict,
                      ledger_path: Path):
        """保存验证结果到文件"""
        # 主结果 JSON
        output = {
            'schema_version': '1.0',
            'title': 'P97W4-N12 数值方法交叉验证结果',
            'timestamp': result.timestamp,
            'content_hash': result.content_hash,
            'summary': {
                'all_pass': result.all_pass,
                'C1_capillary_pass': result.capillary_pass,
                'C2_drag_pass': result.drag_pass,
                'C3_ledger_pass': result.ledger_pass,
            },
            'capillary_validation': {
                'cross_error': result.capillary_cross_error,
                'sph_error': result.sph_dispersion_error,
                'dg_error': result.dg_dispersion_error,
                'threshold': 0.10,
                'pass': result.capillary_pass,
            },
            'drag_validation': {
                'cross_error': result.drag_cross_error,
                'biomimetic_vs_ref': result.biomimetic_vs_reference,
                'hybrid_vs_ref': result.hybrid_vs_reference,
                'threshold': 0.10,
                'pass': result.drag_pass,
            },
            'capability_ledger': {
                'new_count': result.new_capabilities_count,
                'entries': result.capability_entries,
                'ledger_file': str(ledger_path),
            },
        }
        
        output_path = self.output_dir / "cross_validation_result.json"
        output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False, cls=NumpyEncoder))
        
        # sha256
        content_hash = hashlib.sha256(output_path.read_bytes()).hexdigest()
        (self.output_dir / "cross_validation_result.json.sha256").write_text(content_hash)
        
        # Markdown 报告
        md_report = self._generate_markdown_report(result, cap_result, drag_result)
        (self.output_dir / "cross_validation_report.md").write_text(md_report)
    
    def _generate_markdown_report(self, result: CrossValidationResult,
                                   cap_result: Dict, drag_result: Dict) -> str:
        """生成 Markdown 报告"""
        lines = [
            "# P97W4-N12 数值方法交叉验证报告",
            "",
            f"生成时间：{result.timestamp}",
            f"内容哈希：{result.content_hash}",
            "",
            "## 1. 案例介绍",
            "",
            "本节点验证 M2 阶段 5 个数值方法新前沿节点的交叉一致性：",
            "",
            "1. **SPH 毛细波 vs DG AMR 毛细波**：同一解析色散关系，两种不同数值方法",
            "2. **FVM 仿生减阻 vs FVM 势流-粘性混合**：同一船型，两种不同物理模型",
            "",
            "### 物理参数",
            "",
            "| 参数 | 值 | 单位 |",
            "|------|-----|------|",
            f"| 表面张力系数 σ | {cap_result['params'].sigma} | N/m |",
            f"| 密度 ρ | {cap_result['params'].rho} | kg/m³ |",
            f"| 水深 h | {cap_result['params'].depth} | m |",
            f"| 船长 L | {drag_result['biomimetic']['S_wet']:.1f} | m² (湿面积) |",
            f"| 来流速度 U | 5.0 | m/s |",
            f"| 雷诺数 Re | {drag_result['biomimetic']['Re']:.2e} | - |",
            "",
            "## 2. 怎么做的",
            "",
            "### 2.1 毛细波色散关系",
            "",
            "解析色散关系：ω² = (σ/ρ) k³ tanh(kh)",
            "",
            "- **SPH 方法**：200 粒子，cubic spline 核，CSF 表面张力模型",
            "- **DG AMR 方法**：50 单元，p=3 阶多项式，2 层 AMR",
            "- 两种方法独立求解，然后比较色散误差",
            "",
            "### 2.2 摩擦阻力",
            "",
            "- **仿生减阻**：RANS + riblet 等效粗糙度模型，s⁺=15",
            "- **势流-粘性混合**：Rankine 源势流 + Thwaites 积分 BL",
            "- 参考线：ITTC-57 模型-相关线",
            "",
            "## 3. 计算结果",
            "",
            "### 3.1 验收判据",
            "",
            "| 判据 | 指标 | 实测值 | 阈值 | 判定 |",
            "|------|------|--------|------|------|",
            f"| C1 | 毛细波交叉验证误差 | {result.capillary_cross_error:.4f} | <0.10 | {'PASS' if result.capillary_pass else 'FAIL'} |",
            f"| C2 | 减阻交叉验证误差 | {result.drag_cross_error:.4f} | <0.10 | {'PASS' if result.drag_pass else 'FAIL'} |",
            f"| C3 | 新能力登记数 | {result.new_capabilities_count} | ≥5 | {'PASS' if result.ledger_pass else 'FAIL'} |",
            f"| 总体 | 全部通过 | - | - | {'PASS' if result.all_pass else 'FAIL'} |",
            "",
            "### 3.2 毛细波色散误差",
            "",
            f"- SPH 色散误差：{result.sph_dispersion_error:.4f}",
            f"- DG AMR 色散误差：{result.dg_dispersion_error:.4f}",
            f"- 交叉验证误差：{result.capillary_cross_error:.4f}",
            "",
            "### 3.3 摩擦阻力对比",
            "",
            f"- 仿生减阻 Cf（光滑）：{drag_result['cf_smooth_biomimetic']:.6f}",
            f"- 势流-粘性混合 Cf：{drag_result['cf_smooth_hybrid']:.6f}",
            f"- ITTC-57 参考 Cf：{drag_result['cf_reference_ittc57']:.6f}",
            f"- 交叉验证误差：{result.drag_cross_error:.4f}",
            "",
            "## 4. 能力台账",
            "",
            f"新登记能力数：{result.new_capabilities_count}",
            "",
            "| ext_id | 标签 | 证据等级 |",
            "|--------|------|----------|",
        ]
        
        for entry in result.capability_entries:
            lines.append(f"| {entry['ext_id']} | {entry['label']} | {entry['evidence_level']} |")
        
        lines.extend([
            "",
            "## 5. 注意点",
            "",
            "1. 本验证使用简化 1D 模型，未包含完整 3D 效应",
            "2. SPH 粒子数和 DG 单元数影响精度，需收敛性研究",
            "3. 仿生减阻模型基于经验公式，高 Re 下需实验验证",
            "4. 势流-粘性混合方法假设弱耦合，强分离流不适用",
            "",
            "## 6. 误差判定",
            "",
            f"- C1 毛细波交叉验证误差 {result.capillary_cross_error:.4f} < 0.10 → **{'是' if result.capillary_pass else '否'}**",
            f"- C2 减阻交叉验证误差 {result.drag_cross_error:.4f} < 0.10 → **{'是' if result.drag_pass else '否'}**",
            "",
        ])
        
        return "\n".join(lines)


def run_cross_validation(output_dir: Optional[Path] = None) -> CrossValidationResult:
    """运行完整交叉验证（便捷入口）"""
    validator = NumericalMethodsCrossValidator(output_dir)
    return validator.run_full_validation()
