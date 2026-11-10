"""P135-W2-N11 驱动器：装配 manifest → 平台引擎裁决 → 落工件 + 验收判据汇总。

跑法::

    python3 domains/multiphysics_cross_validation/run_validation.py

产物（全部程序化生成，零手抄）::

    artifacts/p135w2/physics_manifest.json          注入文档（引擎输入，含预声明期望）
    artifacts/p135w2/integration_validation.json    引擎完整报告（判据 + 逐项明细）
    artifacts/p135w2/benchmark_suite.json           解析锚基准库（26 条，每条 sha256）
    artifacts/p135w2/integration_validation.md      人读版裁决表
    artifacts/p135w2/acceptance.json                机器验收判据 (a)–(e) + 基准比对误差表

纪律：两遍构建 —— 第一遍跑出数字，第二遍把数字写进报告六要素段（criterion e 需要
report 段落非空），再跑一遍得到最终工件。两遍输入除 report 段外完全一致，
故 ``input_digest`` 的差异仅来自报告段，可审计。
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from domains.multiphysics_cross_validation import manifest as M          # noqa: E402
from ops.taskplane import physics_integration_verify as V               # noqa: E402

ART = os.path.join(ROOT, "artifacts", "p135w2")


def _pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


def reference_errors(manifest: Dict[str, Any], rep: Dict[str, Any]) -> List[Dict[str, Any]]:
    """带 reference 的锚逐条比对（相对误差 = (计算 − 参考)/参考）。"""
    by_id = {m["entry_id"]: m for m in rep["magnitudes"]}
    rows: List[Dict[str, Any]] = []
    for a in manifest["anchors"]:
        ref = a.get("reference")
        if not ref:
            continue
        got = by_id[a["entry_id"]]["value_si"]
        want = V.to_si(V.load_units(manifest["units"]), ref["value"], ref["unit"])
        err = (got - want) / want
        rows.append({
            "entry_id": a["entry_id"],
            "quantity": a["quantity"],
            "computed": got,
            "reference": want,
            "reference_source": ref["source"],
            "rel_error": err,
            "tol_rel": ref.get("tol_rel"),
            "within_tol": abs(err) <= float(ref.get("tol_rel", 0.0)),
            "within_3pct": abs(err) < 0.03,
        })
    return rows


def report_sections(manifest: Dict[str, Any], rep: Dict[str, Any],
                    refs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """六要素段（数字程序化注入）。"""
    bal = {b["balance_id"]: b for b in rep["balances"]}
    el = rep["sensitivity"]["elasticity_top"]
    va = rep["sensitivity"]["variance_top"]
    worst = max(refs, key=lambda r: abs(r["rel_error"])) if refs else None
    return {
        "case": (
            "十物理面代表性工况：100 kg TNT 当量/15 m 爆距水下爆炸；10 万吨撞击船 4.5 m/s "
            "撞 20 万吨被撞船；焊趾 Δσ=120 MPa/Δε_p=1.67e-3 疲劳细节；海水 MHD 通道 "
            "σ=4 S/m、u=10 m/s、B=4 T；R0=1 mm 空泡在 Δp=97.7 kPa 下溃灭；13.8 万 m3 薄膜 "
            "LNG 舱 U=0.13 W/m2K/ΔT=203 K；20 MW 电力推进效率链；4×7.5 MW N+1 电力系统；"
            "A_T=800 m2/C_D=0.8/V_RW=12 m/s 风修正；L=200 m 船 35° 舵角回转。"
        ),
        "method": (
            "三层：①领域侧闭式锚模型（Arons 峰压+指数衰减能量通量、动量守恒+压溃曲线、"
            "Masing 滞回+Basquin、Lorentz/Hartmann、Rayleigh 溃灭+水锤、Q=UAΔT+BOR、"
            "效率链、SFOC、ISO15016 风阻、Nomoto）；②域无关引擎 "
            "ops/taskplane/physics_integration_verify.py 做量纲代数/守恒闭合/接口契约/"
            "sha256 基准库/一阶误差传播五类机械判定；③预声明期望（弹性结构推导的排序，"
            "平局规则固定）在跑前写入 manifest，引擎只比对不调参。"
        ),
        "results": {
            "n_anchors": rep["benchmark_suite"]["n_entries"],
            "suite_digest": rep["benchmark_suite"]["suite_digest"],
            "balance_residuals": {k: v.get("residual_rel") for k, v in bal.items()},
            "interface_edges": len(rep["interfaces"]),
            "interface_pass_rate": rep["criteria"][1]["detail"]["pass_rate"],
            "elasticity_top5": [r["key"] for r in el],
            "variance_top5": [r["key"] for r in va],
            "undex_shock_energy": next(m["value_si"] for m in rep["magnitudes"]
                                       if m["entry_id"] == "undex.shock_energy"),
            "collision_ke": next(m["value_si"] for m in rep["magnitudes"]
                                 if m["entry_id"] == "collision.ke_initial"),
            "fatigue_cycle_energy": next(m["value_si"] for m in rep["magnitudes"]
                                         if m["entry_id"] == "fatigue.cycle_energy"),
            "verdict": rep["verdict"],
        },
        "figures": [
            "fig1_energy_framework.png", "fig2_interface_contracts.png",
            "fig3_benchmark_anchors.png", "fig4_sensitivity_tornado.png",
        ],
        "caveats": (
            "①气泡脉动/热能份额（42%/35%）、疲劳耗能分配（93%/5%）取文献经验值，"
            "故该两条守恒式的意义是'残差在容差内'而非独立验证份额本身；"
            "②大量级带 band_log10 为量级判据（±半个数量级），不是精度判据；"
            "③热应力为全约束上界，实际结构通过柔性/滑动支座释放，不能直接判失效；"
            "④σ 全部来自仪器/文献（见 anchors.INPUTS.rationale），全局排序会随 σ 注入变化，"
            "故判据 (d) 采用双口径：弹性口径（纯物理结构，稳健）+ 方差口径（工程预算）；"
            "⑤Nomoto 一阶模型不含回转速降与横倾耦合，战术直径偏乐观；"
            "⑥桥接边 e06/e08 的增益是 section 降维/效率反算的声明式约定，需下游求解器确认。"
        ),
        "error_judgement": {
            "worst_reference": (None if worst is None else {
                "entry_id": worst["entry_id"], "rel_error": worst["rel_error"],
                "within_3pct": worst["within_3pct"]}),
            "per_reference": [{k: r[k] for k in ("entry_id", "rel_error", "tol_rel",
                                                 "within_tol", "within_3pct")} for r in refs],
            "statement": (
                "带参考值的 4 条锚中，满足 |误差|<3% 的条数 = "
                f"{sum(1 for r in refs if r['within_3pct'])}/{len(refs)}；"
                "其余为量级/区间比对（band_log10 与文献区间），不适用 3% 精度判据，"
                "原因：参考值本身是文献区间/工程经验点值而非单一实验真值。"
            ),
        },
    }


def main() -> int:
    os.makedirs(ART, exist_ok=True)

    # 第一遍：无 report 段 → 拿数字
    m1 = M.build_manifest()
    rep1 = V.verify(m1)
    refs = reference_errors(m1, rep1)

    # 第二遍：注入六要素段（数字来自第一遍，非手抄）
    m2 = M.build_manifest(report=report_sections(m1, rep1, refs))
    rep2 = V.verify(m2)
    refs = reference_errors(m2, rep2)

    units = V.load_units(m2["units"])
    suite = V.build_benchmark_suite(m2["anchors"], units, m2["suite_id"])

    V._write_json(os.path.join(ART, "physics_manifest.json"), m2)
    V._write_json(os.path.join(ART, "integration_validation.json"), rep2)
    V._write_json(os.path.join(ART, "benchmark_suite.json"), suite)
    with open(os.path.join(ART, "integration_validation.md"), "w", encoding="utf-8") as fh:
        fh.write(V.render_markdown(rep2))

    acceptance = {
        "node": "p135w2-n11-maturity-cross-validation",
        "suite_id": m2["suite_id"],
        "verdict": rep2["verdict"],
        "criteria": rep2["criteria"],
        "evidence_paths": {
            "manifest": "artifacts/p135w2/physics_manifest.json",
            "validation": "artifacts/p135w2/integration_validation.json",
            "benchmark_suite": "artifacts/p135w2/benchmark_suite.json",
            "markdown": "artifacts/p135w2/integration_validation.md",
            "acceptance": "artifacts/p135w2/acceptance.json",
        },
        "reference_errors": refs,
        "numbers": {
            "n_anchors": suite["n_entries"],
            "n_interface_edges": len(rep2["interfaces"]),
            "interface_pass_rate": rep2["criteria"][1]["detail"]["pass_rate"],
            "max_balance_residual": max(abs(b["residual_rel"]) for b in rep2["balances"]),
            "undex_shock_energy_J": next(m["value_si"] for m in rep2["magnitudes"]
                                         if m["entry_id"] == "undex.shock_energy"),
            "collision_ke_J": next(m["value_si"] for m in rep2["magnitudes"]
                                   if m["entry_id"] == "collision.ke_initial"),
            "fatigue_cycle_energy_J": next(m["value_si"] for m in rep2["magnitudes"]
                                           if m["entry_id"] == "fatigue.cycle_energy"),
            "elasticity_top5": [r["key"] for r in rep2["sensitivity"]["elasticity_top"]],
            "variance_top5": [r["key"] for r in rep2["sensitivity"]["variance_top"]],
            "suite_digest": suite["suite_digest"],
            "input_digest": rep2["input_digest"],
        },
    }
    V._write_json(os.path.join(ART, "acceptance.json"), acceptance)

    for c in rep2["criteria"]:
        print(("[PASS] " if c["ok"] else "[FAIL] ") + c["id"])
    print("verdict =", rep2["verdict"])
    for r in refs:
        print(f"  ref {r['entry_id']:28s} err={_pct(r['rel_error']):>9s} "
              f"tol={r['tol_rel']:.0%} within3%={r['within_3pct']}")
    return 0 if rep2["verdict"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())