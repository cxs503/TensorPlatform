#!/usr/bin/env python3
"""KPI 可复算替代模块（m9358）：把 ledger 源 KPI 改为 artifact-scan 派生。

背景（prop-7619 静默改写事故）：
  * benchmark_cases / tool_categories / ip_assets 原为 ledger 字面量，可被直改。
  * 本模块提供可复算替代：从文件系统扫描带 reference_value + sha256 的产物。

用法：
  from tools.metrics.kpi_recompute import recompute_benchmark_cases
  count = recompute_benchmark_cases()
"""
from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, List, Optional, Set

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def recompute_benchmark_cases(
    scan_paths: Optional[List[str]] = None,
    required_fields: Optional[List[str]] = None,
    dedup_key: str = "sha256",
) -> int:
    """复算 benchmark_cases KPI：扫描 benchmarks/ 下带 reference_value + sha256 的对标产物并去重。
    
    Args:
        scan_paths: 扫描路径列表（相对 ROOT）
        required_fields: 必需字段列表
        dedup_key: 去重键
    
    Returns:
        去重后的合格算例数
    """
    if scan_paths is None:
        scan_paths = [
            "artifacts/benchmark_validation.json",
            "artifacts/benchmark_settlement/audit.json",
        ]
    if required_fields is None:
        required_fields = ["reference_value", "sha256"]
    
    unique_keys: Set[str] = set()
    
    for rel_path in scan_paths:
        full_path = ROOT / rel_path
        if not full_path.exists():
            continue
        
        try:
            with open(full_path) as fp:
                data = json.load(fp)
        except (json.JSONDecodeError, IOError):
            continue
        
        # 处理不同 JSON 结构
        entries = []
        if isinstance(data, dict):
            # benchmark_settlement/audit.json 结构
            if "cases" in data:
                entries = data["cases"]
            elif "results" in data:
                entries = data["results"]
            else:
                entries = [data]
        elif isinstance(data, list):
            entries = data
        
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            
            # 检查必需字段
            if all(field in entry for field in required_fields):
                # 检查 reference_status（如果有）
                ref_status = entry.get("reference_status")
                if ref_status and ref_status == "WITHHELD":
                    continue  # WITHHELD 行不计入
                
                # 检查误差（如果有）
                error_pct = entry.get("error_pct")
                if error_pct is not None and abs(error_pct) > 15.0:
                    continue  # 误差 >15% 不合格
                
                # 去重
                key = str(entry.get(dedup_key, ""))
                if key:
                    unique_keys.add(key)
    
    return len(unique_keys)


def recompute_tool_categories(
    scan_root: str = "tools/",
    min_files_per_category: int = 3,
) -> int:
    """复算 tool_categories KPI：扫描 tools/ 下子目录，计数有 __init__.py 且含至少 N 个 .py 文件的类别。
    
    Args:
        scan_root: 扫描根目录（相对 ROOT）
        min_files_per_category: 每类别最少 .py 文件数
    
    Returns:
        满足条件的类别数
    """
    tools_dir = ROOT / scan_root
    if not tools_dir.exists():
        return 0
    
    count = 0
    for subdir in tools_dir.iterdir():
        if not subdir.is_dir():
            continue
        if subdir.name.startswith("_") or subdir.name.startswith("."):
            continue
        
        init_file = subdir / "__init__.py"
        if not init_file.exists():
            continue
        
        # 计数 .py 文件（排除 __init__.py）
        py_files = [f for f in subdir.glob("*.py") if f.name != "__init__.py"]
        
        # 检查每个 .py 文件是否有 >10 行非空代码（防 M3 空文件凑数）
        valid_files = []
        for py_file in py_files:
            try:
                with open(py_file) as fp:
                    lines = [line.strip() for line in fp if line.strip() and not line.strip().startswith("#")]
                    if len(lines) > 10:
                        valid_files.append(py_file)
            except IOError:
                continue
        
        if len(valid_files) >= min_files_per_category:
            count += 1
    
    return count


def recompute_ip_assets(
    scan_dirs: Optional[List[str]] = None,
    metadata_required_keys: Optional[List[str]] = None,
) -> int:
    """复算 ip_assets KPI：扫描 docs/papers/, docs/standards/ 等下带 metadata.json 的条目。
    
    Args:
        scan_dirs: 扫描目录列表（相对 ROOT）
        metadata_required_keys: metadata.json 必需键
    
    Returns:
        带完整 metadata 的唯一条目数
    """
    if scan_dirs is None:
        scan_dirs = [
            "docs/papers/",
            "docs/standards/",
            "docs/patents/",
            "docs/software_copyrights/",
        ]
    if metadata_required_keys is None:
        metadata_required_keys = ["title", "authors", "date", "status"]
    
    unique_assets: Set[str] = set()
    
    for rel_dir in scan_dirs:
        full_dir = ROOT / rel_dir
        if not full_dir.exists():
            continue
        
        for metadata_file in full_dir.rglob("metadata.json"):
            try:
                with open(metadata_file) as fp:
                    metadata = json.load(fp)
            except (json.JSONDecodeError, IOError):
                continue
            
            if not isinstance(metadata, dict):
                continue
            
            # 检查必需键
            if not all(key in metadata for key in metadata_required_keys):
                continue
            
            # 检查 status（防 M1 空壳）
            status = metadata.get("status", "")
            if status not in ("published", "submitted", "accepted"):
                continue
            
            # 去重（用文件路径或 sha256）
            asset_key = str(metadata_file.relative_to(ROOT))
            unique_assets.add(asset_key)
    
    return len(unique_assets)


def compute_denominator_sensitivity(
    kpi_name: str,
    baseline_num: float,
    baseline_den: float,
    perturbation_pct: float = 10.0,
) -> Dict[str, Any]:
    """计算分母敏感性：对 ratio 型 KPI 给 ±10% 分母扰动下的 KPI 变动。
    
    Args:
        kpi_name: KPI 名称
        baseline_num: 基线分子
        baseline_den: 基线分母
        perturbation_pct: 扰动百分比
    
    Returns:
        敏感性分析结果
    """
    if baseline_den == 0:
        return {
            "kpi": kpi_name,
            "baseline": None,
            "perturbation_pct": perturbation_pct,
            "den_plus_10pct": None,
            "den_minus_10pct": None,
            "delta_plus": None,
            "delta_minus": None,
            "note": "分母为 0，无法计算",
        }
    
    baseline_kpi = baseline_num / baseline_den
    den_plus = baseline_den * (1 + perturbation_pct / 100)
    den_minus = baseline_den * (1 - perturbation_pct / 100)
    
    kpi_plus = baseline_num / den_plus if den_plus != 0 else None
    kpi_minus = baseline_num / den_minus if den_minus != 0 else None
    
    delta_plus = (kpi_plus - baseline_kpi) if (kpi_plus is not None) else None
    delta_minus = (kpi_minus - baseline_kpi) if (kpi_minus is not None) else None
    
    return {
        "kpi": kpi_name,
        "baseline": baseline_kpi,
        "perturbation_pct": perturbation_pct,
        "den_plus_10pct": den_plus,
        "den_minus_10pct": den_minus,
        "kpi_plus_10pct": kpi_plus,
        "kpi_minus_10pct": kpi_minus,
        "delta_plus": delta_plus,
        "delta_minus": delta_minus,
        "delta_plus_pct": (delta_plus / baseline_kpi * 100) if (delta_plus is not None and baseline_kpi != 0) else None,
        "delta_minus_pct": (delta_minus / baseline_kpi * 100) if (delta_minus is not None and baseline_kpi != 0) else None,
    }


def main():
    """主入口：演示可复算替代"""
    print("=== KPI 可复算替代演示 ===\n")
    
    # benchmark_cases
    bc = recompute_benchmark_cases()
    print(f"benchmark_cases (artifact-scan): {bc}")
    
    # tool_categories
    tc = recompute_tool_categories()
    print(f"tool_categories (artifact-scan): {tc}")
    
    # ip_assets
    ip = recompute_ip_assets()
    print(f"ip_assets (artifact-scan): {ip}")
    
    # 分母敏感性示例
    print("\n=== 分母敏感性分析（示例） ===\n")
    
    # success_rate 示例：8 succeeded + 4 failed = 0.6667
    sens_sr = compute_denominator_sensitivity("success_rate", 8, 12, 10)
    print(f"success_rate 基线: {sens_sr['baseline']:.4f}")
    print(f"  分母 +10%: KPI = {sens_sr['kpi_plus_10pct']:.4f}, Δ = {sens_sr['delta_plus']:+.4f} ({sens_sr['delta_plus_pct']:+.2f}%)")
    print(f"  分母 -10%: KPI = {sens_sr['kpi_minus_10pct']:.4f}, Δ = {sens_sr['delta_minus']:+.4f} ({sens_sr['delta_minus_pct']:+.2f}%)")
    
    # lineage_coverage 示例：10 covered / 12 terminal = 0.8333
    sens_lc = compute_denominator_sensitivity("lineage_coverage", 10, 12, 10)
    print(f"\nlineage_coverage 基线: {sens_lc['baseline']:.4f}")
    print(f"  分母 +10%: KPI = {sens_lc['kpi_plus_10pct']:.4f}, Δ = {sens_lc['delta_plus']:+.4f} ({sens_lc['delta_plus_pct']:+.2f}%)")
    print(f"  分母 -10%: KPI = {sens_lc['kpi_minus_10pct']:.4f}, Δ = {sens_lc['delta_minus']:+.4f} ({sens_lc['delta_minus_pct']:+.2f}%)")
    
    # repro_rate 示例：19 ok / 20 total = 0.95
    sens_rr = compute_denominator_sensitivity("repro_rate", 19, 20, 10)
    print(f"\nrepro_rate 基线: {sens_rr['baseline']:.4f}")
    print(f"  分母 +10%: KPI = {sens_rr['kpi_plus_10pct']:.4f}, Δ = {sens_rr['delta_plus']:+.4f} ({sens_rr['delta_plus_pct']:+.2f}%)")
    print(f"  分母 -10%: KPI = {sens_rr['kpi_minus_10pct']:.4f}, Δ = {sens_rr['delta_minus']:+.4f} ({sens_rr['delta_minus_pct']:+.2f}%)")


if __name__ == "__main__":
    main()
