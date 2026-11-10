#!/usr/bin/env python3
"""KPI 分母敏感性表（AC5）：对 ratio 型 KPI 给 ±10% 分母扰动下的 KPI 变动，输出带 sha256 的可复算表。

用法：
  from tools.metrics.sensitivity_table import compute_sensitivity_table, render_sensitivity_table
  table = compute_sensitivity_table()
  print(render_sensitivity_table(table))
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from tools.metrics.kpi_recompute import compute_denominator_sensitivity


@dataclass
class SensitivityEntry:
    """敏感性表条目"""
    kpi: str
    baseline_num: float
    baseline_den: float
    baseline_kpi: float
    perturbation_pct: float
    den_plus: float
    den_minus: float
    kpi_plus: Optional[float]
    kpi_minus: Optional[float]
    delta_plus: Optional[float]
    delta_minus: Optional[float]
    delta_plus_pct: Optional[float]
    delta_minus_pct: Optional[float]
    sha256: str = ""

    def __post_init__(self):
        if not self.sha256:
            content = json.dumps({
                "kpi": self.kpi,
                "baseline_num": self.baseline_num,
                "baseline_den": self.baseline_den,
                "baseline_kpi": self.baseline_kpi,
                "perturbation_pct": self.perturbation_pct,
                "den_plus": self.den_plus,
                "den_minus": self.den_minus,
                "kpi_plus": self.kpi_plus,
                "kpi_minus": self.kpi_minus,
                "delta_plus": self.delta_plus,
                "delta_minus": self.delta_minus,
            }, sort_keys=True)
            self.sha256 = hashlib.sha256(content.encode()).hexdigest()


# 默认敏感性分析参数
DEFAULT_RATIO_KPIS = [
    {"kpi": "success_rate", "num": 8, "den": 12, "perturbation_pct": 10},
    {"kpi": "lineage_coverage", "num": 10, "den": 12, "perturbation_pct": 10},
    {"kpi": "repro_rate", "num": 19, "den": 20, "perturbation_pct": 10},
]


def compute_sensitivity_table(
    kpis: Optional[List[Dict[str, Any]]] = None,
    perturbation_pct: float = 10.0,
) -> List[SensitivityEntry]:
    """计算分母敏感性表。

    Args:
        kpis: KPI 列表，每项包含 {kpi, num, den, perturbation_pct}
        perturbation_pct: 默认扰动百分比

    Returns:
        敏感性表条目列表
    """
    if kpis is None:
        kpis = DEFAULT_RATIO_KPIS

    entries = []
    for kpi_spec in kpis:
        kpi_name = kpi_spec["kpi"]
        num = kpi_spec["num"]
        den = kpi_spec["den"]
        pct = kpi_spec.get("perturbation_pct", perturbation_pct)

        result = compute_denominator_sensitivity(kpi_name, num, den, pct)

        entry = SensitivityEntry(
            kpi=kpi_name,
            baseline_num=num,
            baseline_den=den,
            baseline_kpi=result["baseline"] if result["baseline"] is not None else 0.0,
            perturbation_pct=pct,
            den_plus=result.get("den_plus_10pct", 0.0) or 0.0,
            den_minus=result.get("den_minus_10pct", 0.0) or 0.0,
            kpi_plus=result.get("kpi_plus_10pct"),
            kpi_minus=result.get("kpi_minus_10pct"),
            delta_plus=result.get("delta_plus"),
            delta_minus=result.get("delta_minus"),
            delta_plus_pct=result.get("delta_plus_pct"),
            delta_minus_pct=result.get("delta_minus_pct"),
        )
        entries.append(entry)

    return entries


def render_sensitivity_table(entries: List[SensitivityEntry]) -> str:
    """渲染敏感性表为可读字符串。

    Args:
        entries: 敏感性表条目列表

    Returns:
        格式化字符串
    """
    lines = []
    lines.append("=" * 80)
    lines.append("KPI 分母敏感性表（±10% 扰动）")
    lines.append("=" * 80)
    lines.append("")

    for e in entries:
        lines.append(f"KPI: {e.kpi}")
        lines.append(f"  基线: {e.baseline_num}/{e.baseline_den} = {e.baseline_kpi:.4f}")
        lines.append(f"  分母 +10%: {e.den_plus:.1f} → KPI = {e.kpi_plus:.4f} (Δ = {e.delta_plus:+.4f}, {e.delta_plus_pct:+.2f}%)")
        lines.append(f"  分母 -10%: {e.den_minus:.1f} → KPI = {e.kpi_minus:.4f} (Δ = {e.delta_minus:+.4f}, {e.delta_minus_pct:+.2f}%)")
        lines.append(f"  SHA256: {e.sha256}")
        lines.append("")

    return "\n".join(lines)


def sensitivity_table_to_json(entries: List[SensitivityEntry]) -> Dict[str, Any]:
    """将敏感性表转为 JSON 可序列化字典。

    Args:
        entries: 敏感性表条目列表

    Returns:
        JSON 可序列化字典
    """
    table_sha = hashlib.sha256()
    records = []
    for e in entries:
        record = {
            "kpi": e.kpi,
            "baseline_num": e.baseline_num,
            "baseline_den": e.baseline_den,
            "baseline_kpi": e.baseline_kpi,
            "perturbation_pct": e.perturbation_pct,
            "den_plus": e.den_plus,
            "den_minus": e.den_minus,
            "kpi_plus": e.kpi_plus,
            "kpi_minus": e.kpi_minus,
            "delta_plus": e.delta_plus,
            "delta_minus": e.delta_minus,
            "delta_plus_pct": e.delta_plus_pct,
            "delta_minus_pct": e.delta_minus_pct,
            "sha256": e.sha256,
        }
        table_sha.update(e.sha256.encode())
        records.append(record)

    return {
        "table_sha256": table_sha.hexdigest(),
        "entry_count": len(records),
        "records": records,
    }
