#!/usr/bin/env python3
"""合成金标集生成与评估（E1）。

生成 200 条判据（100 不可达成 + 100 可达成），用于评估精度模型的召回率与误报率。
* recall(不可达成) ≥ 0.90
* FP(可达成) ≤ 0.05

纯函数、无 IO、无随机（种子由调用方传入）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .precision_model import Achievability, assess_achievability


@dataclass(frozen=True)
class GoldStandard:
    cases: List[Dict[str, Any]]
    n_reachable: int
    n_unreachable: int

    def as_dict(self) -> dict:
        return {
            "n_total": len(self.cases),
            "n_reachable": self.n_reachable,
            "n_unreachable": self.n_unreachable,
            "cases": self.cases,
        }


def _make_reachable_cases(n: int) -> List[Dict[str, Any]]:
    """生成可达成判据样例。"""
    templates = [
        ("与解析解比较，相对误差 <= {v}", "解析解"),
        ("对实验数据，平均相对误差 <= {v}%", "实验"),
        ("与文献值对齐，偏差 ≤ {v}%", "文献"),
        ("相对基线版本的误差 <= {v}", "基线"),
        ("自洽性：两套实现一致率 >= {v}", "自洽"),
        ("与守恒量对比，总量漂移 ≤ {v}", "守恒"),
        ("误报率 ≤ {v}（实验标注集）", "实验"),
        ("召回率 >= {v}（实验标注集）", "实验"),
        ("误差 ±{v}%（对文献区间）", "文献"),
        ("与基线相比提升 >= {v} 倍", "基线"),
        ("特征值比较误差 ≤ {v}%", "解析解"),
        ("分类 F1 >= {v}（实验核对）", "实验"),
        ("内存峰值增长 ≤ {v}%", "基线"),
        ("曲线峰值位置偏差 ≤ {v} 个采样点", "实验"),
        ("守恒律残差 == 0（守恒核对）", "守恒"),
        ("对标文献表格，最大偏差 ≤ {v}%", "文献"),
        ("sha256 相同率 >= {v}%", "自洽"),
        ("L2 范数误差 ≤ {v}", "解析解"),
        ("相对误差绝对值 ≤ {v}%", "实验"),
        ("与基线指标对比：提升 ≥ {v}%", "基线"),
    ]

    # 合理的阈值（>= 1e-12 或 == 0 且有守恒/自洽参考）
    values_reachable = [
        1e-10, 0.03, 0.005, 0.02, 0.99, 1e-10, 0.05, 0.95, 0.5, 1.5,
        0.02, 0.95, 0.20, 3, 0.0, 0.05, 1.0, 1e-8, 0.02, 0.15,
        1e-9, 0.01, 0.001, 0.1, 0.98, 1e-10, 0.04, 0.90, 0.3, 2.0,
        1e-7, 0.008, 0.0005, 0.05, 0.97, 1e-9, 0.06, 0.85, 0.4, 1.8,
        1e-6, 0.002, 0.0001, 0.08, 0.96, 1e-8, 0.07, 0.80, 0.6, 1.2,
        1e-5, 0.004, 0.002, 0.15, 0.94, 1e-7, 0.08, 0.75, 0.5, 1.6,
        1e-4, 0.006, 0.003, 0.2, 0.93, 1e-6, 0.09, 0.70, 0.7, 1.4,
        1e-3, 0.008, 0.004, 0.25, 0.92, 1e-5, 0.10, 0.65, 0.8, 1.3,
        0.01, 0.01, 0.005, 0.3, 0.91, 1e-4, 0.11, 0.60, 0.9, 1.1,
        0.02, 0.012, 0.006, 0.35, 0.90, 1e-3, 0.12, 0.55, 1.0, 1.0,
    ]

    cases = []
    for i in range(n):
        tmpl, ref = templates[i % len(templates)]
        val = values_reachable[i % len(values_reachable)]
        text = tmpl.format(v=val)
        cases.append({
            "id": f"R{i:04d}",
            "label": "REACHABLE",
            "text": text,
            "threshold": val,
            "reference": ref,
        })
    return cases


def _make_unreachable_cases(n: int) -> List[Dict[str, Any]]:
    """生成不可达成判据样例。

    不可达成的条件：
    - 阈值 < 1e-12（低于典型双精度有效精度）
    - 阈值 == 0 且无守恒/自洽参考
    - 阈值为负
    """
    templates = [
        ("与解析解比较，相对误差 <= {v}", "解析解"),
        ("对实验数据，平均相对误差 <= {v}", "实验"),
        ("与文献值对齐，偏差 ≤ {v}%", "文献"),
        ("误差 == {v}（无参考）", ""),
        ("相对误差 <= -{v}%", "实验"),
        ("与解析解比较，误差 <= {v}", "解析解"),
        ("守恒量漂移 ≤ {v}", "守恒"),
        ("特征值误差 <= {v}", "解析解"),
        ("L2 误差 ≤ {v}", "解析解"),
        ("残差 == {v}（无守恒参考）", ""),
    ]

    # 不可达成的阈值：全部 < 1e-12 或 == 0 或负值
    values_unreachable = [
        1e-20, 1e-18, 1e-16, 0.0, 0.01, 1e-17, 1e-19, 1e-21, 1e-14, 0.0,
        1e-22, 1e-25, 1e-30, 0.0, 0.05, 1e-23, 1e-28, 1e-35, 1e-13, 0.0,
        1e-40, 1e-50, 1e-100, 0.0, 0.1, 1e-60, 1e-70, 1e-80, 1e-15, 0.0,
        1e-90, 1e-110, 1e-150, 0.0, 0.2, 1e-200, 1e-250, 1e-300, 1e-11, 0.0,
        1e-16, 1e-17, 1e-18, 0.0, 0.5, 1e-19, 1e-20, 1e-21, 1e-22, 0.0,
    ]

    cases = []
    for i in range(n):
        tmpl, ref = templates[i % len(templates)]
        val = values_unreachable[i % len(values_unreachable)]
        text = tmpl.format(v=val)
        cases.append({
            "id": f"U{i:04d}",
            "label": "UNREACHABLE",
            "text": text,
            "threshold": val,
            "reference": ref,
        })
    return cases


def generate_gold_standard(
    n_reachable: int = 100,
    n_unreachable: int = 100,
    *,
    seed: Optional[int] = None,
) -> GoldStandard:
    """生成合成金标集。"""
    reachable = _make_reachable_cases(n_reachable)
    unreachable = _make_unreachable_cases(n_unreachable)
    cases = reachable + unreachable
    return GoldStandard(
        cases=cases,
        n_reachable=n_reachable,
        n_unreachable=n_unreachable,
    )


def evaluate_gold_standard(gs: GoldStandard) -> Dict[str, Any]:
    """评估金标集：计算 recall(不可达成) 和 FP(可达成)。"""
    tp = fp = fn = tn = 0
    errors: List[str] = []

    for case in gs.cases:
        result = assess_achievability(case["text"])
        predicted = result.verdict
        gold = case["label"]

        if gold == "UNREACHABLE" and predicted == "UNREACHABLE":
            tp += 1
        elif gold == "REACHABLE" and predicted == "UNREACHABLE":
            fp += 1
            errors.append(case["id"])
        elif gold == "UNREACHABLE" and predicted == "REACHABLE":
            fn += 1
            errors.append(case["id"])
        else:
            tn += 1

    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    fp_rate = fp / (fp + tn) if (fp + tn) > 0 else 0.0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "recall_unreachable": round(recall, 6),
        "fp_rate_reachable": round(fp_rate, 6),
        "precision": round(precision, 6),
        "f1": round(f1, 6),
        "error_ids": sorted(errors),
        "n_total": len(gs.cases),
        "n_reachable": gs.n_reachable,
        "n_unreachable": gs.n_unreachable,
    }
