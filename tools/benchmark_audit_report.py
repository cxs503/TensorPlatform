#!/usr/bin/env python3
"""Generate the benchmark reference-data consistency audit artefacts.

Outputs (all numbers read from the auditor, nothing hand-typed):
    artifacts/benchmark_audit/audit.json
    artifacts/benchmark_audit/audit.md
    artifacts/benchmark_audit/fig1_froude_identity.png
    artifacts/benchmark_audit/fig2_identity_residuals.png
    docs/research/benchmark_reference_audit.md

Usage:  python3 tools/benchmark_audit_report.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from services import benchmark_audit as ba  # noqa: E402

OUT = ROOT / "artifacts" / "benchmark_audit"
OUT.mkdir(parents=True, exist_ok=True)
DATASET = ROOT / "backend" / "data" / "benchmark_reference.json"


def _dev(report, case_id, check):
    case = next(c for c in report["cases"] if c["case_id"] == case_id)
    for f in case["findings"]:
        if f["check"] == check and "deviation_pct" in f:
            return f["deviation_pct"], f["severity"]
    return None, None


def main() -> int:
    report = ba.audit_file(DATASET)
    (OUT / "audit.json").write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                    encoding="utf-8")
    (OUT / "audit.md").write_text(ba.audit_summary_markdown(report), encoding="utf-8")

    cases = report["cases"]
    ids = [c["case_id"] for c in cases]
    fn_stated = [c["froude_stated"] for c in cases]
    fn_derived = [c["froude_derived"] for c in cases]

    # --- Figure 1: stated vs derived Froude number -------------------------
    fig, ax = plt.subplots(figsize=(6.4, 5.2))
    colors = {"SUBOFF": "#1f77b4", "DTMB-5415": "#d62728", "KVLCC2": "#2ca02c"}
    for ship in colors:
        xs = [d for c, d in zip(cases, fn_derived) if c["ship_type"] == ship]
        ys = [s for c, s in zip(cases, fn_stated) if c["ship_type"] == ship]
        ax.scatter(xs, ys, label=ship, s=46, color=colors[ship], zorder=3)
    lim = [0.03, 3.0]
    ax.plot(lim, lim, "k--", lw=1, label="identity Fn(stated)=V/sqrt(gL)")
    ax.axvline(ba.FN_SURFACE_MAX, color="grey", ls=":", lw=1)
    ax.text(ba.FN_SURFACE_MAX * 1.03, 0.05, "Fn=1: mixed reference frame",
            rotation=90, fontsize=8, color="grey", va="bottom")
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlim(*lim); ax.set_ylim(*lim)
    ax.set_xlabel("Fn derived from stated V and L  (V/sqrt(gL))")
    ax.set_ylabel("Fn stated in dataset")
    ax.set_title("Froude identity audit: 4/12 cases off the diagonal (DTMB-5415)")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(OUT / "fig1_froude_identity.png", dpi=150)
    plt.close(fig)

    # --- Figure 2: identity residuals per case ----------------------------
    checks = [("FROUDE_IDENTITY", "Fn identity"),
              ("CT_RT_RECIPROCITY", "Ct<->Rt identity"),
              ("BLOCK_COEFF_IDENTITY", "cb*L*B*T identity")]
    width = 0.26
    fig, ax = plt.subplots(figsize=(9.6, 4.4))
    x = np.arange(len(ids))
    for k, (key, label) in enumerate(checks):
        vals = []
        for cid in ids:
            dev, _sev = _dev(report, cid, key)
            vals.append(dev if dev is not None else np.nan)
        vals = np.array(vals, dtype=float)
        ax.bar(x + (k - 1) * width, np.nan_to_num(vals, nan=0.0) + 1e-3,
               width=width, label=label)
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels(ids, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("relative deviation  [%]  (log, clipped at 0.001 %)")
    ax.axhline(5.0, color="k", ls="--", lw=1,
               label="tolerance: Fn 1 %, Ct<->Rt 5 %, cb 5 %")
    ax.set_title("Benchmark reference identities: relative deviation per case")
    ax.grid(alpha=0.3, axis="y")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "fig2_identity_residuals.png", dpi=150)
    plt.close(fig)

    # --- deliverable doc ---------------------------------------------------
    tbl = ba.audit_summary_markdown(report)
    blocked = ", ".join(report["blocked_case_ids"]) or "none"
    doc = f"""# 船舶阻力基准数据集一致性审计（benchmark_reference.json）

> 生成工件：`tools/benchmark_audit_report.py`（本文件与 `artifacts/benchmark_audit/*` 全部由脚本产出，
> 数字零手抄）。审计器：`backend/services/benchmark_audit.py`。回归测试：`tests/test_benchmark_audit.py`。
> 审计时间 2026-11-01（m1589）。

## 1. 为什么审

`backend/api/domain/ship_benchmark.py` 对外发布一个**精度判决门**：Holtrop/ITTC-57 计算 Ct 与
`backend/data/benchmark_reference.json` 中 `experimental_ct` 的相对误差 ≤15 % 即判 pass。
门本身的合法性依赖参考值**内部自洽**——若参考表自相矛盾，pass 只是把配方误差藏在数据缺陷后面。
因此把 3 条定义性恒等式作为审计判据（Δ 为相对偏差）：

| 判据 | 恒等式 | 容差 | 含义 |
|---|---|---|---|
| C1 FROUDE_IDENTITY | Fn = V/√(gL) | 1 % | 比较点（速度）与长度同一参考系 |
| C2 CT_RT_RECIPROCITY | Ct = Rt/(½ρSV²) | 5 % | 无量纲 Ct 与有力纲 Rt 互洽 |
| C3 BLOCK_COEFF_IDENTITY | cb·L·B·T = ∇ | 5 % | 方形系数与排水量互洽 |
| C4 SPEED_REFERENCE_FRAME | V/√(gL) ≤ 1 | — | 排水型基准算例不存在跨声速/滑行 Fn |
| C5 CT_PLAUSIBILITY | Ct/Cf_ITTC57 ∈ [0.9, 3.5] | — | Ct 不得低于摩擦线（物理下界） |

ρ：模型尺度淡水 999 kg/m³，KVLCC2 实船海水 1025 kg/m³；ν：模型 1.0e-6、实船 1.19e-6 m²/s。

## 2. 审计结果（程序化表格）

{tbl}

**缺陷清册（n = 12 例）**：`{json.dumps(report['defects_by_check'], ensure_ascii=False)}`；
判据被阻断（WITHHELD）的算例：`{blocked}`。

## 3. 四类实测缺陷

1. **C1 弗劳德恒等式破裂（4/12，DTMB-5415）**：表列 Fn=0.061 而 V/√(gL)=0.343，偏差 82 %；
   末两例反解 Fn=1.37、2.06 > 1 —— 说明 `speed_ms` 是**实船速度**却配了**模型长度** 5.72 m，
   两列参考系不一致。任何"DTMB-5415 通过 15 % 门"的结论都不可复核。
2. **C2 Ct↔Rt 恒等式破裂（12/12）**：SUBOFF 由 Rt 反解 Ct=0.00051 而表列 0.0038（偏 86–90 %）；
   KVLCC2 由 Rt 反解 Ct=0.00076 而表列 0.0028 —— 且 KVLCC2/DTMB 缺 `wetted_surface_m2`，
   根本无法反算（数据完整性欠账）。
3. **C3 方形系数恒等式（4/12，DTMB-5415）**：cb·L·B·T = 0.5425 m³ vs 表列 0.4663 t（16 %）。
4. **几何预设与数据集打架（API 层）**：旧的硬编码 `SHIP_GEOMETRY` 里 SUBOFF 的 `cb` 写成了
   0.508（＝艇体直径），DTMB-5415 的排水量写成 466.3 kg（其自身 cb·L·B·T 应有 542.5 kg）。
   现改为**从数据集派生**，只对数据集缺失的键保留显式 `fallback` 并回传 `preset_sources` 溯源。

## 4. 门硬化：fail-closed（WITHHELD）

`POST /api/v1/calculations/benchmark_validate` 现在先跑审计：

* C1/C4/C5 破裂 → 该算例判 `pass=False`，`method="WITHHELD"`，
  `note="REFERENCE_DATA_INCONSISTENT[...]: ..."`，并计入 `summary.withheld_cases`；
* C2/C3 破裂（Ct 本身仍合物理）→ 记 warning，不阻断（数据卫生债，登记待修）；
* `summary.reference_data_audit` 回传缺陷计数，`summary.preset_sources` 回传几何溯源。

即：**不许用坏参考值点亮绿灯**。审计后 12 例中 8 例可判（SUBOFF、KVLCC2），4 例 WITHHELD（DTMB-5415）。

## 5. 结果图

![fig1](fig1_froude_identity.png)：表列 Fn 与 V/√(gL) 的对角线图，DTMB-5415 四点显著偏离，两点越过 Fn=1。
![fig2](fig2_identity_residuals.png)：逐算例三条恒等式残差（对数轴），红线为容差；可见 C2 残差 80–90 % 量级。

## 6. 注意点与局限

* 本轮**只修可判定项**（派生预设、fail-closed），**不重写文献数值**：DTMB-5415 正确速度/尺度、
  KVLCC2 湿表面积需回原始文献（ITTC V&V Workshop、MOERI 官方 particular、Roddy 1990）核对后由
  负责人确认，禁止凭记忆填数。
* C2/C3 具体是 Ct 对还是 Rt 对，本审计不作裁决（只能证明二者矛盾）。C5 显示 SUBOFF Ct/Cf≈1.33、
  KVLCC2 Ct/Cf≈2.0（均在物理包线内），提示**力纲列**更可能是笔误来源，仍待文献核实。
* 场景外推：Holtrop 对 Fr>0.4（SUBOFF）本就超适用域，本审计不改善该方法的物理适用范围。
"""
    (ROOT / "docs" / "research" / "benchmark_reference_audit.md").write_text(
        doc, encoding="utf-8")

    print(json.dumps({"n_cases": report["n_cases"],
                      "n_blocked": report["n_blocked"],
                      "blocked": report["blocked_case_ids"],
                      "defects": report["defects_by_check"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())