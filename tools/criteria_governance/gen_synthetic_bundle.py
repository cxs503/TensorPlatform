"""合成阶次判据工件生成器：把"阈值是 h 的函数"写成数据工件（程序化注入，零手抄）。

生成的 3 条判据全部基于纯数学合成算例（周期正弦的中心差分 / 复合梯形求积），
声明 ``threshold(h) = SF·C·h^p``：``C`` 由解析误差常数给出（探针现场重算），
``p`` 是声明阶，``SF`` 是显式裕度。领域语义为零；工件里只有数学与本生成器出处。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, List

from .probes_linear import SYNTHETIC_PROBES

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "data" / "criteria_governance" / "synthetic_orders.criteria.json"
REVIEW_DATE = "2026-11-09"
SAFETY_FACTOR = 1.2

SPECS: List[Dict] = [
    {
        "criterion_id": "SYN-C1",
        "title": "2 点中心差分导数算子的二阶误差界",
        "statement": "周期正弦的一阶导数用 2 点中心差分逼近，最细网格实测最大误差必须落在 "
                     "threshold(h)=SF·C·h^2 的界内。",
        "quantity": "max_abs_derivative_error",
        "probe": {"name": "linear_operator_refinement",
                  "params": {"stencil": 2, "wavenumber": 1.0,
                             "resolutions": [32, 64, 128, 256]}},
        "declared_exponent": 2.0,
        "expression": "SF*C*h**2",
    },
    {
        "criterion_id": "SYN-C2",
        "title": "5 点差分导数算子的四阶误差界",
        "statement": "周期正弦的一阶导数用 5 点 stencil 逼近，最细网格实测最大误差必须落在 "
                     "threshold(h)=SF·C·h^4 的界内（阶次由探针序列自行重算）。",
        "quantity": "max_abs_derivative_error",
        "probe": {"name": "linear_operator_refinement",
                  "params": {"stencil": 4, "wavenumber": 1.0,
                             "resolutions": [32, 64, 128, 256]}},
        "declared_exponent": 4.0,
        "expression": "SF*C*h**4",
    },
    {
        "criterion_id": "SYN-C3",
        "title": "复合梯形求积的二阶误差界",
        "statement": "指数函数在单位区间上的复合梯形积分误差必须落在 "
                     "threshold(h)=SF·C·h^2 的界内（C 取解析误差常数 (e-1)/12）。",
        "quantity": "abs_integration_error",
        "probe": {"name": "integration_refinement",
                  "params": {"lower": 0.0, "upper": 1.0, "resolutions": [16, 32, 64, 128]}},
        "declared_exponent": 2.0,
        "expression": "SF*C*h**2",
    },
]


def build_entries() -> List[Dict]:
    entries: List[Dict] = []
    for spec in SPECS:
        observation = SYNTHETIC_PROBES[spec["probe"]["name"]](dict(spec["probe"]["params"]))
        detail = observation["detail"]
        constant = float(detail["analytic_error_constant"])
        exponent = float(spec["declared_exponent"])
        h_ref = float(observation["hs"][-1])
        threshold = SAFETY_FACTOR * constant * h_ref ** exponent
        entries.append({
            "criterion_id": spec["criterion_id"],
            "title": spec["title"],
            "statement": spec["statement"],
            "quantity": spec["quantity"],
            "comparator": "<=",
            "threshold": {"op": "<=", "value": threshold, "unit": "1"},
            "source_type": "analytic",
            "source_ref": {
                "kind": "analytic_identity",
                "identifier": f"synthetic_order_analytic:{spec['probe']['name']}"
                              f"::stencil={spec['probe']['params'].get('stencil', 'n/a')}",
                "locator": "data/criteria_governance/synthetic_orders.criteria.json",
            },
            "depends_on": [{"param": "h", "exponent": exponent}],
            "derivation": {
                "method": "refinement_scan",
                "reference": "离散算子主误差项解析式（Taylor 展开首项），常数由探针 detail 现场计算",
                "parents": [],
                "expression": spec["expression"],
                "declared_exponent": exponent,
                "tolerance": 0.3,
                "safety_factor": SAFETY_FACTOR,
                "note": f"threshold(h)=SF*C*h^{exponent:g}，SF={SAFETY_FACTOR}，"
                        f"C={constant:.12g}，h_ref={h_ref:.12g}",
            },
            "probe": spec["probe"],
            "review_date": REVIEW_DATE,
        })
    return entries


def build_bundle() -> Dict:
    entries = build_entries()
    payload = {
        "bundle_id": "synthetic-orders",
        "bundle_role": "synthetic_order_reference",
        "physical_summary": "纯数学合成算例：阈值随分辨率按 h^p 收缩，用于验证重推导闭环机制。",
        "provenance": {
            "generator": "tools/criteria_governance/gen_synthetic_bundle.py",
            "safety_factor": SAFETY_FACTOR,
            "note": "阈值由生成器在探针 detail 上程序化计算，无手抄数字。",
        },
        "criteria": entries,
    }
    payload["provenance"]["entries_sha256"] = hashlib.sha256(
        json.dumps(entries, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return payload


def write_bundle(path: Path = BUNDLE) -> Path:
    payload = build_bundle()
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return Path(path)


if __name__ == "__main__":  # pragma: no cover
    print(write_bundle())