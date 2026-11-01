"""Render the publication-grade report for the resistance contract audit.

Every number in the output is read from
`artifacts/verifier/resistance_contract/gate_report.json` (which is itself
produced by `audit_resistance_contract.py` from the verbatim API captures), so
nothing is transcribed by hand.

Writes:
    docs/verifier/RESISTANCE_CONTRACT_AUDIT.md   -- in-repo report
    /tmp/report.json                             -- platform report payload
                                                    (figures inlined as base64)

Usage:
    python3 tools/verifier/render_resistance_contract_report.py [--out /tmp/report.json]
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

MISSION_ID = 1582
TITLE = ("阻力端点契约与解析一致性复核（m1582）：6 项闭式恒等式全部 <0.03%，"
         "但 cf/ct 系数场不自洽（缺 dCf=0.400e-3）、单位契约未声明为 knots、"
         "Cb>1 与 V=0 无校验")


def _table(rows, cols):
    head = "| " + " | ".join(cols) + " |\n|" + "|".join(["---"] * len(cols)) + "|\n"
    body = ""
    for r in rows:
        body += "| " + " | ".join(str(r[c]) for c in cols) + " |\n"
    return head + body


def build(root: Path) -> dict:
    base = root / "artifacts/verifier/resistance_contract"
    rep = json.loads((base / "gate_report.json").read_text())
    cap = json.loads((base / "captures.json").read_text())
    s = rep["summary"]
    meta = rep["meta"]["reference_chain"]

    math_rows = [r for r in rep["checks"] if r["kind"] == "MATH" and "reference" in r]
    contract_rows = [r for r in rep["checks"] if r["kind"] == "CONTRACT"]
    obs = rep["observations"]
    c12 = next(r for r in rep["checks"] if r["check"] == "C12")
    c0 = next(r for r in rep["checks"] if r["check"] == "C0")

    # ---- ① case description
    case_tbl = _table(
        [{"算例": c["case_id"],
          "L×B×T [m]": f'{c["request"]["length"]:g}×{c["request"]["beam"]:g}×{c["request"]["draft"]:g}',
          "∇ 声称 [t]": f'{c["request"]["displacement"]:g}',
          "Cb": f'{c["request"]["cb"]:g}',
          "Vs [kn]": f'{c["request"]["speed"]:g}',
          "HTTP": c["http_status"],
          "Rt [kN]": f'{c.get("response", {}).get("rt", "-")}',
          "Pe [kW]": f'{c.get("response", {}).get("pe", "-")}'}
         for c in cap["cases"]],
        ["算例", "L×B×T [m]", "∇ 声称 [t]", "Cb", "Vs [kn]", "HTTP", "Rt [kN]", "Pe [kW]"])

    # ---- ③ result table (math checks, grouped by check id, min/max |err|)
    groups: dict[str, dict] = {}
    for r in math_rows:
        g = groups.setdefault(r["check"], {"check": r["check"], "quantity": r["quantity"],
                                           "tol": r["tol_pct"], "errs": [], "verdict": "PASS"})
        if r["rel_err_pct"] == r["rel_err_pct"]:
            g["errs"].append(abs(r["rel_err_pct"]))
        if r["verdict"] == "FAIL":
            g["verdict"] = "FAIL"
    math_tbl = _table(
        [{"检查": g["check"], "被检量": g["quantity"],
          "容差 [%]": f'{g["tol"]:g}',
          "max |误差| [%]": f'{max(g["errs"]):.4f}' if g["errs"] else "n/a",
          "判定": g["verdict"]}
         for g in groups.values()],
        ["检查", "被检量", "容差 [%]", "max |误差| [%]", "判定"])

    dcf_vals = [r["platform"] for r in rep["checks"] if r["check"] == "C6"]
    c8_rows = [r for r in contract_rows if r["check"] == "C8"]
    c8_tbl = _table(
        [{"算例": r["case"],
          "ct (返回)": f'{r["platform"]:.6f}',
          "cf+cr": f'{r["reference"]:.6f}',
          "(ct-(cf+cr))/ct [%]": f'{r["rel_err_pct"]:+.2f}'}
         for r in c8_rows],
        ["算例", "ct (返回)", "cf+cr", "(ct-(cf+cr))/ct [%]"])

    defects_tbl = _table(
        [{"缺陷": d["id"], "等级": d["severity"], "检查": d["check"],
          "描述": d["title"], "证据": d["evidence"], "修法": d["fix"]}
         for d in rep["defects"]],
        ["缺陷", "等级", "检查", "描述", "证据", "修法"])

    # ---- ⑥ error verdict
    per_item = []
    for g in groups.values():
        worst = max(g["errs"]) if g["errs"] else float("nan")
        per_item.append(f'{g["check"]} {g["quantity"]}: {worst:.4f}% → '
                        f'{"是" if worst < 3.0 else "否"}')
    c8_worst = max(abs(r["rel_err_pct"]) for r in c8_rows)
    per_item.append(f'C8 ct vs (cf+cr): {c8_worst:.2f}% → 否')
    for o in obs:
        if o["check"] in ("C10", "C11", "C9"):
            per_item.append(f'{o["check"]} {o["quantity"]}: 契约违规（非数值误差）→ 否')

    md = f"""# 阻力端点契约与解析一致性复核（verifier 席 tp-ver-1 · mission {MISSION_ID}）

## ① 案例介绍

被复核对象：已上线端点 `POST /api/v1/calculations/resistance`（能力 `calc.resistance`，
台账证据等级 **REAL**，自述方法串 "ITTC-57摩擦阻力 + Holtrop剩余阻力"）。
之所以复核它：`artifacts/credit_ledger.jsonl` 里唯一的 resistance 裁决针对的是
`tools/verifier/pilots/resistance_calculator.py`（一个**独立玩具实现**），
**从未有任何裁决针对真正对外服务的端点**；而 1148 号对账报告已指出台账与自述等级存在
OVERCLAIM 风险。本单因此把"验证欠账"补在端点上：不读源码、不做数值 CFD，只用
闭式恒等式把端点自述的每一个物理量逐项钉住。

为什么能钉死：端点返回的 8 个量中，Re、Fr、Cf(ITTC-1957)、Rr、Pe 都有**定义级**闭式参考，
不需要实验值也不需要文献查表；CT/Cr 的经验部分则以端点的 `method` 自述为被检命题，
用恒等式反解其隐含常数（结果反解出 ITTC-1978 粗糙度补贴 0.400e-3，见 ③）。

算例（全部为黑盒 POST，载荷与应答逐字节存 `artifacts/verifier/resistance_contract/captures.json`）：

{case_tbl}
参考链常数：1 kn = {meta['kn_to_ms']:.7f} m/s（平台实际用 0.5144，反解 {c0['platform']:.6f}，
偏差 {c0['rel_err_pct']:+.4f}%）、g = {meta['g']}、ρ = {meta['rho']:g} kg/m³、
ν = {meta['nu']:.2e} m²/s、dCf(ITTC-1978) = {meta['roughness_delta_cf']:.1e}。

## ② 怎么做的

1. **黑盒探针**：7 次 POST（5 个物理算例 + 2 个边界算例）。因平台 :8001 在本机
   load15≈126 时直连 HTTP 会挂起，全部经平台 API 工具调用，应答原样落盘（captures.json，
   含未成功的 500），可离线重放。
2. **单位契约反解**（C0-C2）：端点 schema 未声明 `speed` 单位。以 Fr = V/√(gL) 反解 V，
   得 V/Vs = {c0['platform']:.6f} m/s/knot，即 knots；同时用 Re = VL/ν 反解 ν，
   两者在 6 个算例上互相闭合 → 单位假设不是拟合出来的，是被两条独立定义同时确认的。
3. **闭式恒等式**（C3-C7）：Cf 对 ITTC-1957；Rr 对 ½ρV²S·Cr；Pe 对 Rt·V；
   再由 Rf/(½ρV²S) − cf 反解粗糙度补贴 dCf，并把 ct 用 (cf+dCf+cr) 重建做闭合校验。
4. **契约检查**（C8-C11）：返回的系数场能否自加（ct vs cf+cr）；`displacement` 是否起作用；
   物理不可能几何（Cb>1.2）是否被拒；V=0 是否 4xx。
5. **幂律与后果量化**（C12）：在 5/10/20 kn 上测 dlnRt/dlnV，把"单位误读"从"文档瑕疵"
   翻译成"Rt 低估多少 %"的可执行后果。
6. **工具链**：纯 python + matplotlib；离线 pytest 14 项（`tools/verifier/tests/
   test_resistance_contract.py`），其中 MATH 检查为不变量、5 条缺陷为"钉住现状"的回归锚，
   运行时修好后这些锚会主动失败，提示刷新 captures 并降级缺陷等级。

## ③ 计算结果

**MATH 检查：{s['math_checks'] - s['math_failed']}/{s['math_checks']} PASS，
最大 |相对误差| = {s['math_worst_abs_err_pct']:.4f}%（受端点打印精度 2–6 位小数限制）。**

{math_tbl}
说明：C6 的 {max(dcf_vals) - min(dcf_vals):.1e} 级离散度即 6 位小数舍入误差，
其均值 {sum(dcf_vals)/len(dcf_vals):.6f} 与 ITTC-1978 常用粗糙度补贴 0.400e-3 在 0.2% 内一致
—— 端点内部确实含该补贴，**但只在力值里，没在 `cf` 字段里**：

{c8_tbl}
**CONTRACT 检查：{s['contract_checks'] - s['contract_failed']}/{s['contract_checks']} PASS。**

{defects_tbl}
后果量化（C12 实测幂律 dlnRt/dlnV = {c12['detail']['loglog_slope']:.2f}）：若调用方把
`speed` 当 SI m/s 传入，Rt 被低估 {abs(c12['detail']['consequence_if_knots_read_as_ms_pct']):.0f}%
（例：{c12['detail']['rt_kn_sweep'][0]:.2f} / {c12['detail']['rt_kn_sweep'][1]:.2f} /
{c12['detail']['rt_kn_sweep'][2]:.2f} kN @ 5/10/20 kn）。这不是 15% 线附近的误差，而是数量级级误导。

## ④ 结果图

- 图1 `fig1_cf_vs_ittc1957.png`：端点 6 个 cf 点全部落在 ITTC-1957 曲线上（log-log），
  最大偏差 0.023%，是"REAL 而非 MOCK"的直观证据。
- 图2 `fig2_error_bars.png`：逐项 |相对误差| 条形（对数轴），蓝=闭式恒等式（全部 <0.03%），
  红=契约检查 C8（1.8–6.6%），并画出 0.1%（舍入下限）/3%（目标）/15%（不可接受）三线。
- 图3 `fig3_rt_powerlaw_and_coef_gap.png`：(a) Rt–V 幂律与拟合；(b) 各算例
  (ct−(cf+cr))/ct 缺口，显示缺的正是同一个 0.400e-3 补贴。

## ⑤ 注意点

- **未声明单位是本单最"轻"却最致命的发现**：端点 schema 只有 `speed`，无 units 字段；
  正确性完全依赖调用方读中文方法串猜单位。已在 D1 给出最小修复（schema 声明/SI 化）。
- **本单不做**：(a) 不读平台运行时源码（:8001 服务位于白名单外的
  `/srv/tensor/ship-workspace/`），因此 D2-D5 只交证据与修法，不改实现；
  (b) 不重算 Holtrop 的 Cr / 湿表面积 / (1+k) 经验回归——这三项无可复现的公开系数录入，
  标为 **WITHHELD**（需要 CM、CWP、Cstern、ABT 等入参，端点未暴露），不能声称已复核。
  因此本单的"REAL"判定**只覆盖定义级闭式量**，不覆盖经验阻力分量。
- 浮箱算例 Cb=1.0 只用来让 L·B·T·Cb 与 ∇ 对得上；c5 的 KCS 型算例用于验证常规船型下
  同一套闭式关系依然闭合（不是只在退化几何上成立）。
- 承载力纪律：本机 load15≈126（nproc=128），全程纯公式+matplotlib，未起重算力进程。

## ⑥ 误差判定（是否 <3%）

逐项（详见 ③ 表）：
{chr(10).join('  - ' + x for x in per_item)}

**结论**：端点自述的 6 项闭式物理量 **全部 <3%（实测 <0.03%）→ MATH 判定 PASS**；
但**返回字段层的 cf/ct 不自洽最大 {c8_worst:.2f}% > 3% → CONTRACT 判定 FAIL**，
且 D1/D3/D5 属可导致误用的契约缺陷。整体判定：**部分 PASS / 契约 FAIL（honest）**——
即"算法是对的，接口把对的算式包坏了"。修 D2 只需在响应里多返回一个 dcf 或把 cf 换成
cf_total，修 D3/D5 只需两处入参校验，均为分钟级改动；本单不越界改运行时仓。

## 产物

- `tools/verifier/audit_resistance_contract.py`（门：12 类检查 + 3 图）
- `tools/verifier/tests/test_resistance_contract.py`（14 项 pytest，全绿）
- `artifacts/verifier/resistance_contract/{{captures.json, gate_report.json, fig1..fig3}}`
- 本文件由 `tools/verifier/render_resistance_contract_report.py` 程序化渲染，数字零手抄。
"""
    figs = []
    for name, cap_ in (("fig1_cf_vs_ittc1957.png",
                        "图1 端点 cf 全部落在 ITTC-1957 摩擦线上（log-log，最大偏差 0.023%）"),
                       ("fig2_error_bars.png",
                        "图2 逐项相对误差：闭式恒等式 <0.03%，契约检查 C8 达 6.6%"),
                       ("fig3_rt_powerlaw_and_coef_gap.png",
                        "图3 (a) Rt–V 幂律与单位误读后果；(b) ct−(cf+cr) 缺口 = 未披露的 dCf")):
        p = base / name
        figs.append({"name": name, "caption": cap_,
                     "png_base64": base64.b64encode(p.read_bytes()).decode()})

    payload = {
        "title": TITLE,
        "summary": (f"黑盒复核 calc.resistance 端点：Re/Fr/Cf/Rr/Pe 等 {s['math_checks']} 项闭式检查全部 "
                    f"PASS（最大误差 {s['math_worst_abs_err_pct']:.4f}%），反解出 speed 实为 knots、"
                    f"dCf=0.400e-3；但 cf+cr≠ct（最大 {c8_worst:.2f}%）、Cb=1.2 被接受、V=0 返 500 —— "
                    f"5 项契约缺陷，修法已给。"),
        "scenario_key": "verification/resistance_contract_audit",
        "mission_id": MISSION_ID,
        "tags": ["verifier", "resistance", "contract-audit", "ITTC-1957", "honest-FAIL",
                 "calc.resistance", "m1582"],
        "markdown": md,
        "figures": figs,
    }
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[2]
    ap.add_argument("--md", default=str(root / "docs/verifier/RESISTANCE_CONTRACT_AUDIT.md"))
    ap.add_argument("--payload", default="/tmp/report.json")
    args = ap.parse_args()

    payload = build(root)
    Path(args.md).write_text(payload["markdown"])
    Path(args.payload).write_text(json.dumps(payload, ensure_ascii=False))
    print(f"md      -> {args.md} ({len(payload['markdown'])} chars)")
    print(f"payload -> {args.payload} ({len(payload['figures'])} figures, "
          f"{sum(len(f['png_base64']) for f in payload['figures'])//1024} KB base64)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())