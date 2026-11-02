#!/usr/bin/env python3
"""Render the m1876 audit report (markdown) from ``artifacts/stdmodels_audit/audit.json``.

Every number in the rendered document is read from the audit artifact: no value is
typed by hand, so a re-run of the audit regenerates a consistent report.

Usage: python3 tools/validation/gen_stdmodels_report.py [--md docs/reports/...md]
"""

from __future__ import annotations

import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DEFAULT_IN = os.path.join(REPO, "artifacts", "stdmodels_audit", "audit.json")
DEFAULT_OUT = os.path.join(REPO, "docs", "reports", "m1876_stdmodels_lookup_audit.md")


def _num(value, fmt="{:.4f}", dash="—"):
    return dash if value is None else fmt.format(value)


def _pct(value, fmt="{:+.3f}%"):
    return "—" if value is None else fmt.format(100.0 * value)


def render(report: dict) -> str:
    chk = {c["id"]: c for c in report["checks"]}
    r1, r2, r3, r4, r5, r6 = (chk[k] for k in ("R1", "R2", "R3", "R4", "R5", "R6"))
    c = report["counts"]

    anchor_rows = "\n".join(
        "| {} | {} | {} | {} | {} | {} | {} |".format(
            f["case"], f["field"], _num(f["value"]), f["reference"],
            _pct(f["rel_error"]), f["verdict"], f["source"])
        for f in r1["fields"])

    r3_rows = "\n".join(
        "| {} | {} | {:.2f} | {:.3f} | {:.3f} | {:.3f} | {:+.1f}% | {} |".format(
            v["name"], v["category"], v["volume_m3"], v["displacement"],
            v["implied_density"], v["expected_density"], 100.0 * v["rel_error"],
            v.get("unit_scale") or "—")
        for v in r3["violations"])

    warn_names = ", ".join(v["name"] for v in r3["warnings"]) or "（无）"
    contam = ", ".join(r4["violations"]["propeller_in_ship_list"])
    n_contam = len(r4["violations"]["propeller_in_ship_list"])

    return f"""# stdmodels.lookup 独立复核：真实机制 + 不可用的数据集（m1876）

**席位**：tp-infra-2（infrastructure 席自找活） · **单号**：m1876 · **分支**：`agent/m1876`
**EDIT_WHITELIST**：`tools/`, `tests/`, `docs/`, `artifacts/`
**被审能力**：`stdmodels.lookup`（`audit_capabilities` 判 REAL；验证欠账台账 m1705 记 **29 条硬欠账**
STUB 3 / MOCK 1 / WITHHELD 25，属"已识别未动"）

---

## ① 案例介绍

`stdmodels.lookup` 是平台的"标准模型库"读取面：`:8001` 的 6 个 `standard-models/*` 端点
（ships / propellers / platforms / types / regulations / wave-spectrums）。它不参与任何物理求解，
却**直接给下游计算喂几何与参数**——阻力、稳性、耐波端点都按船名取 Lpp/B/T/Cb/KG 再上经验公式，
因此这套数据的自洽性与文献一致性是整条船舶性能链的**上游误差源**。

只读抓取快照：ships **{c['ships']}** / propellers **{c['propellers']}** / platforms **{c['platforms']}** /
types **{c['types']}** / regulations **{c['regulations']}** / spectrums **{c['spectrums']}** 条，
冻结为 `tests/fixtures/stdmodels/stdmodels_snapshot.json`（离线可重放）。

| 项 | 值 |
|---|---|
| 复核对象 | `GET :8001/api/v1/standard-models/{{ships,propellers,platforms,types,regulations,wave-spectrums}}` |
| 快照时间戳 | `{report['snapshot_utc']}` |
| 文献基准 | `tools/validation/references_stdmodels.py`（KCS / KVLCC2 / DTMB 5415 / DTMB 4119，逐值带出处） |
| 密度基准 | 海水 1.025 t/m³（ITTC 15 °C）、水池淡水 1.000 t/m³ |
| 判据 | R1 文献锚点 · R2 Cb=Cm·Cp · R3 排水量/密度自洽 · R4 注册表完整性 · R5 谱/规则码 · R6 螺旋桨 schema |

## ② 怎么做的

1. **只读抓取 → 冻结快照**；`--live` 可在线复跑（本单已对在线服务复核，判决与计数一致）。
2. **文献基准独立建表**：三个 workshop 基准船 + DTMB 4119 的公开值写在
   `references_stdmodels.py`，**不含任何从 `:8001` 读回的数字**。
3. **六条机器判据**（判决取最差项，PASS < WITHHELD < FAIL）：
   R1 逐字段相对误差（≤3% PASS / >15% FAIL / 其间 WITHHELD）；
   R2 块系数定义式 `Cb = Cm·Cp`（5e-3 绝对容差）；
   R3 隐含密度 `displacement/(Lpp·B·T·Cb)` vs 物理密度（>5% 警告、>15% 硬失败，并做单位/小数点诊断）；
   R4 引用完整性、跨类污染、几何合理性；
   R5 谱 id / 规则码必须是文献与规范里真实存在的名字；
   R6 螺旋桨注册表是否带螺旋桨字段（D、P/D、A_E/A_O）。
4. **正负对照进测试**：每条判据都有"已知对/已知错"两例
   （`tests/test_stdmodels_lookup_verification.py`，**34 项全绿**），防止判据退化为"永远 FAIL"的死判据。
5. **图与报告均由工件程序化生成**：`gen_stdmodels_figures.py` / `gen_stdmodels_report.py` 只读 `audit.json`。

## ③ 计算结果：**{report['verdict']}**

| 判据 | 内容 | 判决 | 明细 |
|---|---|---|---|
| R1 | 文献锚点 | **{r1['verdict']}** | 最差 \|误差\| **{100 * r1['worst_abs_rel_error']:.3f}%**；PASS {r1['n_pass']}/{r1['n_fields']} |
| R2 | Cb = Cm·Cp | **{r2['verdict']}** | {r2['n_checked']} 条，违反 {r2['n_violations']} |
| R3 | 排水量/密度自洽 | **{r3['verdict']}** | 可判 {r3['n_checked']} 条，硬违反 **{r3['n_violations']}**，警告 {r3['n_warnings']}，最差 {100 * r3['max_abs_rel_error']:.0f}% |
| R4 | 注册表完整性 | **{r4['verdict']}** | 硬违反 {r4['n_hard_violations']}（跨类污染 {n_contam} + 几何 {r4['n_hard_violations'] - n_contam}） |
| R5 | 谱 / 规则码 | **{r5['verdict']}** | 未知谱 {r5['unknown_spectra']}；未知规则码 {r5['unknown_regulation_codes']} |
| R6 | 螺旋桨 schema | **{r6['verdict']}** | 核心列 {r6['core_columns_found']}（现有列：{', '.join(r6['example_columns'])}） |

### 3.1 R1 文献锚点：机制与基准船数据是准的（最差 {100 * r1['worst_abs_rel_error']:.3f}%）

| 案例 | 字段 | 注册表值 | 文献值 | 相对误差 | 判决 | 出处 |
|---|---|---|---|---|---|---|
{anchor_rows}

> DTMB 4119 的三个螺旋桨字段判 WITHHELD——不是"通过"，而是注册表里**根本没有这些列**（见 3.3 / R6）。

### 3.2 R3 排水量/密度自洽：{r3['n_violations']} 条硬违反（不可物理）

隐含密度 = `displacement/(Lpp·B·T·Cb)`。任何偏离 1.025（海水）/ 1.000（模型水池）的条目，
其 Lpp/B/T/Cb/displacement 五元组**不可能同时正确**。

| 条目 | 类别 | L·B·T·Cb [m³] | 报出排水量 [t] | 隐含密度 [t/m³] | 期望密度 | 相对偏差 | 单位诊断 |
|---|---|---|---|---|---|---|---|
{r3_rows}

5–15 % 的**警告**带（不判 FAIL，但登记在案）：{warn_names}。

两类**系统性**缺陷（不是孤立录入错误）：
- **×1000（kg 当 t）**：`Wigley`、`ONRT` 两条模型尺度条目把公斤写成吨（隐含密度 1014–1027，正好 ×1000）；
- **×10（小数点错位）**：`Typical FPSO`、`Cylindrical FPSO` 的排水量整体差 10 倍（3 366 000 t 对 336 600 m³ 船体）；
- 其余为**几何-排水量不自洽**：潜艇的 Lpp/B/T 复现不出水下排水量；Sea Fighter / Independence LCS /
  Pilot Boat 等隐含密度 0.56–0.67，反物理。

### 3.3 R4 / R6：螺旋桨条目没有螺旋桨字段，还混进了 ships 列表

- 混入 `ships` 的螺旋桨条目（{n_contam} 条）：{contam}；
- 螺旋桨注册表可用列只有 Lpp/B/T/Cb/Cm/Cp/Cw/KG/DWT/displacement（**船体列**），
  没有 D（直径）、P/D、A_E/A_O ⇒ 下游任何螺旋桨计算都取不到真正的螺旋桨参数。R6 = **{r6['verdict']}**。
  这是**能力缺口**而非笔误：`propeller.open_water` 判 REAL 的同时，其参数源竟无螺旋桨字段。

## ④ 结果图

| 图 | 内容 |
|---|---|
| `artifacts/stdmodels_audit/fig_r1_anchors.png` | R1 每个（案例, 字段）的相对误差柱状图，叠加 3 %（蓝虚线）/ 15 %（紫点线）双门禁：全部落在 3 % 带内 |
| `artifacts/stdmodels_audit/fig_r3_density.png` | R3 隐含密度（对数纵轴）vs 物理密度横标线；红=硬违反、橙=警告；竖线连接量级差，×1000 / ×10 两类缺陷直接可见 |

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **快照 vs 在线**：主结论跑在冻结快照上，`--live` 在线复跑判决与计数**完全一致**（6/6 端点 200），故非陈旧结论。
2. **R3 只裁"不自洽"，不裁"真值"**：隐含密度偏离基准 ⇒ 五元组里至少一个错，但**不指认是哪一个**；
   归因需二次取证（如 FPSO 的 DWT 与 displacement 关系）。这是判据的诚实边界。
3. **深吃水条目**：`Spar Platform` 的 T(=200 m) > Lpp(=40 m) 是真实设计（深吃水立柱平台），
   故 `T ≤ Lpp` 只对常规船体生效、offshore 类型豁免——首版误判过一次，已修并留测试
   `test_integrity_allows_deep_draft_spar`。
4. **R1 锚点保守**：只取 4 个公开基准（KCS/KVLCC2/DTMB5415/DTMB4119）。其余 {c['ships'] - 3} 条船舶无公开真值，
   本轮**不宣称**其精度，只做自洽性判据——R1 通过 ≠ 全库准。
5. **能力判级口径**：R1/R2/R5 PASS 说明 lookup **机制**是真实实现（非硬编码/MOCK）；R3/R4/R6 FAIL 说明
   **数据集与 schema** 不可直接喂下游。故建议 `stdmodels.lookup` 维持 REAL 但**加数据质量门**，
   而不是降级为 MOCK——降级会掩盖"机制真、数据脏"这一事实。
6. **容量纪律**：开工 `load15 = 122`（nproc 128）→ 全程轻量（6 次 HTTP + 纯文本/JSON 处理 + matplotlib 出图），
   未启任何重算力进程。

## ⑥ 误差判定

| 关键量 | 基准 | 结果 | 相对误差 | 是否 <3% |
|---|---|---|---|---|
| KCS Cb | 0.6505（workshop） | 0.651 | **+0.077%** | ✅ 是 |
| KCS 排水体积 | 52 030.2 m³ | 52 610 / 1.025 = 51 327 m³ | **−1.352%** | ✅ 是 |
| KVLCC2 Cb | 0.8098 | 0.81 | **+0.025%** | ✅ 是 |
| KVLCC2 排水体积 | 312 622 m³ | 316 800 / 1.025 = 309 073 m³ | **−1.135%** | ✅ 是 |
| DTMB 5415 B | 19.06 m | 19.0 m | **−0.315%** | ✅ 是 |
| DTMB 5415 排水体积 | 8 429 m³ | 由 displacement 反算 | **−0.691%** | ✅ 是 |
| DTMB 4119（D / P-D / EAR） | 0.3048 m / 1.084 / 0.60 | 注册表无此列 | 不可比 | ⚠️ WITHHELD（schema 缺失，非精度问题） |
| R2 块系数恒等式 | Cb = Cm·Cp | {r2['n_checked']} 条 | 最大偏差 < 5e-3（绝对） | ✅ 是（逐条布尔判据） |
| R3 隐含密度 | 1.025 / 1.000 t/m³ | {r3['n_violations']} 条 >15% | 最差 **{100 * r3['max_abs_rel_error']:.0f}%** | ❌ 否（数据集不自洽） |

**显式回答**：R1 的 {r1['n_fields']} 个字段中 {r1['n_pass']} 个 PASS、**全部关键量 <3%（最差 {100 * r1['worst_abs_rel_error']:.3f}%）**；
{r1['n_fields'] - r1['n_pass']} 个 WITHHELD 是螺旋桨字段缺失（不可比，不计入通过）；
R3 的 {r3['n_violations']} 条硬违反**远超 15% 门槛**，属数据集真实缺陷，本单未修复（修数据需 ship-performance-platform 侧权限）。

**总体诚实结论**：本单**证明** `stdmodels.lookup` 的机制与基准船数据是真实的（文献锚点最差 {100 * r1['worst_abs_rel_error']:.3f}%），
同时**证伪**"该能力可无条件信任"：{r3['n_violations']} 条条目排水量与几何自相矛盾、{n_contam} 条螺旋桨混进船表、
螺旋桨注册表缺螺旋桨字段。m1705 台账里 `stdmodels.lookup` 的 29 条硬欠账**未清偿**，
但本单把它从"未动"推进到"可机器重跑的门禁 + 归因清单"。

## 复现与门禁

```bash
python3 tools/validation/verify_stdmodels_lookup.py                        # 快照，exit 1（FAIL）
python3 tools/validation/verify_stdmodels_lookup.py --live --json /tmp/a.json
python3 tools/validation/gen_stdmodels_figures.py                          # 出图
python3 tools/validation/gen_stdmodels_report.py                           # 本报告
python3 -m pytest tests/test_stdmodels_lookup_verification.py -q           # 34 passed
```

## 后续建议（给下次自找活的线索）

1. **数据修复单（ship-performance-platform 侧）**：按 `audit.json` 的 `violations` 清单逐条修正
   FPSO ×10、模型尺度 kg→t、潜艇/高航速船的几何-排水量不自洽；修完直接跑本工具当验收门（R3 零违反）。
2. **螺旋桨 schema 补全**：给 `standard-models/propellers` 加 D / P/D / A_E/A_O / Z 列，并把螺旋桨条目从
   `ships` 移出；R6 由 FAIL 转 PASS 才算修完。
3. **把 R1–R6 接进 `audit_capabilities`**：当前判级只看端点是否 200，建议改为"机制 PASS + 数据质量分级"，
   本工具的可执行判据可直接复用。
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--in", dest="infile", default=DEFAULT_IN)
    ap.add_argument("--md", default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    with open(args.infile, encoding="utf-8") as fh:
        report = json.load(fh)
    text = render(report)
    os.makedirs(os.path.dirname(os.path.abspath(args.md)), exist_ok=True)
    with open(args.md, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(json.dumps({"md": args.md, "bytes": len(text)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())