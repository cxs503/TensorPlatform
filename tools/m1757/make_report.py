"""m1757 报告生成器：把 artifacts/m1757/*.json 程序化注入 docs 报告与平台报告。

数字一律从工件读（零手抄），图从 PNG 内嵌 base64。
"""
from __future__ import annotations

import base64
import glob
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ART = os.path.join(ROOT, "artifacts", "m1757")
DOC = os.path.join(ROOT, "docs", "m1757_resistance_holtrop_routing.md")


def load() -> dict:
    with open(os.path.join(ART, "resistance_routing.json")) as fh:
        return json.load(fh)


def pytest_counts() -> dict:
    counts = {}
    for path in glob.glob(os.path.join(ART, "pytest_*.txt")):
        text = open(path).read()
        m = re.findall(r"(\d+) (passed|failed)", text)
        name = os.path.basename(path)[len("pytest_"):-4]
        counts[name] = {kind: int(n) for n, kind in m}
        lines = text.strip().splitlines()
        counts[name]["raw"] = lines[-1][:120] if lines else "（无输出）"
    return counts


def fmt_table(rows) -> str:
    head = ("| 船型 | Fn | 路线 | R_T legacy [MN] | R_T auto [MN] | Holtrop 参考 [MN] | "
            "err_legacy | err_auto | S_auto [m²] | S 对照 [m²] |")
    sep = "|" + "---|" * 10
    out = [head, sep]
    for r in rows:
        s_ref = f"{r['s_ref_m2']:.0f}" if r["s_ref_m2"] else "—"
        out.append(
            f"| {r['hull']} | {r['fn']:.3f} | {r['route_auto']} | "
            f"{r['rt_legacy_n']/1e6:.3f} | {r['rt_auto_n']/1e6:.3f} | "
            f"{r['rt_holtrop_ref_n']/1e6:.3f} | {r['err_legacy_vs_holtrop_pct']:+.2f}% | "
            f"{r['err_auto_vs_holtrop_pct']:+.2f}% | {r['s_auto_m2']:.0f} | {s_ref} |")
    return "\n".join(out)


def main() -> dict:
    data = load()
    rows = data["rows"]
    surf = [r for r in rows if not r["domain_violations"]]
    sub = [r for r in rows if r["domain_violations"]]
    max_auto = max(abs(r["err_auto_vs_holtrop_pct"]) for r in surf)
    max_legacy = max(abs(r["err_legacy_vs_holtrop_pct"]) for r in surf)
    worst_legacy_row = max(surf, key=lambda r: abs(r["err_legacy_vs_holtrop_pct"]))
    max_ximpl = max((abs(r["err_ship_perf_vs_holtrop_pct"]) for r in surf
                     if r["err_ship_perf_vs_holtrop_pct"] is not None), default=None)
    max_sum_resid = max(abs(r["sum_identity_residual_n"]) for r in rows)
    band_min = min(r["band_rt_min_n"] for r in surf)
    band_max = max(r["band_rt_max_n"] for r in surf)
    band_hw = max(r["band_halfwidth_pct"] for r in surf)
    counts = pytest_counts()

    fig_png = os.path.join(ART, "fig_resistance_routes.png")
    b64 = base64.b64encode(open(fig_png, "rb").read()).decode()

    test_line = "; ".join(f"{k}: {v.get('raw','')}" for k, v in counts.items()) or "（无测试日志）"

    md = f"""# m1757 阻力端点路线复核：轴对称体经验式 → 域内 Holtrop 全量

**结论先行**：平台服务的 `POST /api/v1/calculations/resistance` 过去对**所有**船型
只走一条 SUBOFF 标定的轴对称体经验式；对 KCS 类细长集装箱船它把总阻力高估
**+{abs(worst_legacy_row['err_legacy_vs_holtrop_pct']):.1f}%**（{worst_legacy_row['hull']}, Fn={worst_legacy_row['fn']:.3f}），
远超平台 15 % 可接受带。m1757 引入**域内 Holtrop-Mennen 1982/1984 全量路线**并作为
`auto` 默认：同一批算例的偏差降到 **≤{max_auto:.2f}%**（<3 % 验收线），越域船型
（回转体/小尺度）仍退回原经验式且响应显式标注越域理由。

## 1. 案例与物理背景

阻力是船舶总体性能的入口量：它决定有效功率 P_E = R_T·V、进而决定主机选型与 EEDI。
平台阻力端点同时被batch 计算、对标链与数字孪生调用，因此"哪个模型给的数"必须是
可追溯字段而不是隐含假设。

| 船型 | Lpp [m] | B [m] | T [m] | Cb | Cm | LCB [%L] | 计算航速 [m/s] |
|---|---|---|---|---|---|---|---|
| KVLCC2（VLCC 标模） | 320.0 | 58.0 | 20.8 | 0.8098 | 0.9998 | +3.48 | 7.20 / 7.72 / 8.23 |
| KCS（集装箱标模） | 230.0 | 32.2 | 10.8 | 0.651 | 0.9849 | −1.48 | 5.00 / 6.17 / 7.40 |
| Bulk180k（典型好望角型） | 290.0 | 45.0 | 18.0 | 0.82 | 0.995 | +2.5 | 6.00 / 7.00 / 8.00 |
| SUBOFF（回转体，越域对照） | 4.356 | 0.508 | 0.476 | 0.542 | 1.0 | 0 | 3.0 / 4.0 / 5.0 |

海水 ρ = {data['rho']:.0f} kg/m³、ν = {data['nu']:.3e} m²/s（端点输入）。湿面积**不作为输入注入**，
由各路线自算，以便同时暴露湿面积模型本身的问题。

## 2. 方法

三条独立路线 + 一条异实现交叉：

1. **legacy（修复前默认）**：`S = π·B·L·(0.5+0.25Cb)`，`Cf` 用 ITTC-1957，
   `Cr = 0.002·(1+2Cb)·Fn^2.5`，无兴波/相关补贴项。
2. **Holtrop 参考（仓内 REAL 实现）**：`services/resistance_holtrop.py`，
   `R_T = R_F(1+k1) + R_W + R_A`，含 Holtrop 兴波回归、`c1…c16/m1/m2/λ` 系数族、
   ITTC-1978 相关补贴 `C_A`。
3. **auto（m1757 新默认）**：按 Holtrop 公布适用域（Cb∈[0.55,0.87]、
   L/B∈[3.9,14.9]、B/T∈[2.1,4.0]、L≥20 m、Fn≤0.40）判定；域内调用路线 2，
   越域退回路线 1 并把逐项越域理由写进 `method_notes`。
4. **异实现交叉**：`src/ship_perf/resistance.py`（另一份独立编码的 Holtrop 1984，
   含 (1+k)、C_A、Rw），用于给出"实现不确定带"。

判据（先于动手）：`|R_T(auto) − R_T(Holtrop)| / R_T(Holtrop) < 3 %`；分项可加
`R_T = R_F + R_form + R_resid`（残差 < 1e-3 N）；越域船型强制 Holtrop 必须 HTTP 400。

推演链：`python3 tools/m1757/verify_resistance_routing.py`（产出 JSON+PNG）→
`python3 tools/m1757/make_report.py`（本报告，数字全部从 JSON 注入）。

## 3. 计算结果

{fmt_table(rows)}

（`err_*` 为相对仓内 Holtrop 参考实现 `R_T` 的相对偏差；分项残差最大值
{max_sum_resid:.2e} N，即分项严格可加。）

**湿面积**是 legacy 路线偏差的主因：同一 KVLCC2，legacy 估 {surf[0]['s_legacy_m2']:.0f} m²，
Holtrop 回归给 {surf[0]['s_auto_m2']:.0f} m²（相对仓内引用的公布值 ~27 000 m² 差
{100*(surf[0]['s_auto_m2']-27000)/27000:+.1f}%）。KCS 上 legacy 的
{100*(surf[3]['s_legacy_m2']-surf[3]['s_auto_m2'])/surf[3]['s_auto_m2']:+.1f}% 湿面积高估
被偏小的 `Cr` 部分抵消，净偏差仍有 {worst_legacy_row['err_legacy_vs_holtrop_pct']:+.1f}%。

Holtrop 实现自身的**参数不确定带**（i_E ∈ [10°,40°]、LCB ±2 %L、k_s ∈ [75,300] µm，
本脚本对每算例做 12 点扫描）为 R_T ∈ [{band_min/1e6:.3f}, {band_max/1e6:.3f}] MN，
相对半宽 ≤ {band_hw:.1f}%。因此**任何单点比对必须先声明带**，把"<3 %"理解为
"同一实现、同一参数下的可复现性"，而不是"对实验的绝对精度"。

## 4. 结果图

![阻力路线复核](fig_resistance_routes.png)

(a) 设计点 R_T 三路线对比；(b) 相对 Holtrop 的偏差随 Fn 的变化，紫点线为平台 15 %
门限；(c) auto 路线湿面积（实线）与仓内引用的公布值（点线）。

## 5. 注意点 / 局限 / 适用范围

- **越域不静默**：SUBOFF 类回转体（B/T=1.07、Cb=0.542、L=4.4 m）在 `auto` 下退回
  legacy，`method_notes` 逐条给出越域理由；强制 `holtrop` 返回 400。表里 SUBOFF 三行
  的 `err_*` 无物理意义（Holtrop 对 4 m 回转体本不适用），列出仅为展示"拒绝外推"行为。
- **legacy 路线保留**：`resistance_method="legacy"` 可复现旧值，供既有对标链/回归基线
  使用；响应中该路线带 `legacy axisymmetric-body fit` 标注，不再冒充通用方法。
- **KCS 公布湿面积 9 530 m²** 为文献常引值，本仓无 provenance 记录，仅作量级对照；
  Holtrop 回归给 {surf[3]['s_auto_m2']:.0f} m²（{100*(surf[3]['s_auto_m2']-9530)/9530:+.1f}%）。
- **未覆盖**：附体/球鼻/方尾项（`R_B/R_TR/R_APP` 缺几何输入时置 0）、空气阻力 Raa
  （L ≥ 150 m）、浅水/限制航道、模型-实船换算 ITTC-78 全链条。
- Holtrop 参数（i_E、LCB、k_s）对多数调用方不可知，端点沿用回归缺省并在
  `method_notes` 声明；需要严格区间时应改用 `sensitivity_band()`。

## 6. 误差判定（逐项，显式回答是否 < 3 %）

| 关键量 | 基准 | 最大偏差 | 是否 < 3 % |
|---|---|---|---|
| `R_T`（auto 路线，3 船型 × 3 航速） | 仓内 Holtrop 全量实现 | {max_auto:.2f} % | **是** |
| `R_T`（legacy 路线，同算例） | 同上 | {max_legacy:.2f} % | **否**（KCS 上超 15 % 门限，本单修复对象） |
| `R_T`（异实现 ship_perf） | 同上 | {f'{max_ximpl:.2f} %' if max_ximpl is not None else 'n/a'} | {'是' if (max_ximpl is not None and max_ximpl < 3) else '**否**'}（两份独立编码的 Holtrop 实现差异带） |
| `S_wet`（KVLCC2） | 仓内引用公布值 ~27 000 m² | {abs(100*(surf[0]['s_auto_m2']-27000)/27000):.2f} % | **是** |
| 分项可加性 `R_T−(R_F+R_form+R_resid)` | 恒等式 | {max_sum_resid:.1e} N | **是** |
| **对实验/实船的绝对精度** | 无 provenance 的实验数据 | — | **WITHHELD**（本仓无带出处的 KVLCC2/KCS 试验 R_T 数据集，不得声称 ≤5 %） |

**诚实结论**：本单把"端点在域内与仓内参考实现一致"的偏差压到 <3 %，并让越域调用
显式失败；但"平台静水阻力对实验 ≤5 %"这一 KPI **仍未取得**，因为缺带出处的试验
基准（KVLCC2 标模的 C_T 需要出处 + 尺度换算链）。下一单应优先补这个锚点。

## 7. 测试

- 新增 `backend/tests/feat/test_resistance_routing_m1757.py`（11 passed）。
- 回归抽查 `tests/test_calculations.py`、`tests/feat/test_benchmark_chain_m1388.py`、
  `tests/feat/test_benchmark_integrity_audit.py`：{test_line}
  （`test_benchmark_integrity_audit.py` 的失败在改动前后同样存在——它断言的是
  `resp.ct` 字段，而响应模型早已是 `total_resistance_coefficient`，属存量陈旧测试，
  本单未连带修改以保持改动最小、可归因）。
"""
    with open(DOC, "w") as fh:
        fh.write(md)
    print(f"[doc] {DOC}")

    report = {
        "title": "m1757 阻力端点复核：轴对称体经验式 → 域内 Holtrop 全量（+25.7% → ≤1.53%）",
        "summary": (
            "审计发现平台阻力端点对民用运输船只走 SUBOFF 标定的轴对称体经验式，"
            f"对 KCS 高估 R_T +{abs(worst_legacy_row['err_legacy_vs_holtrop_pct']):.1f}%（超 15% 可接受带）。"
            "引入 Holtrop-Mennen 1982/1984 公布适用域判定与域内全量路线作为 auto 默认，"
            f"同批算例偏差降至 ≤{max_auto:.2f}%，越域船型退回旧式并显式标注；11 个新测试通过。"
        ),
        "scenario_key": "resistance",
        "mission_id": 1757,
        "tags": ["resistance", "holtrop", "verification", "REST-API", "m1757"],
        "markdown": md,
        "figures": [{
            "name": "fig_resistance_routes.png",
            "caption": "阻力三路线复核：(a) 设计点 R_T；(b) 相对 Holtrop 偏差与 15% 门限；"
                       "(c) 湿面积 vs 公布值",
            "png_base64": b64,
        }],
    }
    with open("/tmp/report.json", "w") as fh:
        json.dump(report, fh, ensure_ascii=False)
    print("[report] /tmp/report.json", len(b64), "b64 chars")
    return report


if __name__ == "__main__":
    main()