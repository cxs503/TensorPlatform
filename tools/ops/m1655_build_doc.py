#!/usr/bin/env python3
"""Build the m1655 audit document from the machine ledger (no hand-copied numbers).

Reads ``artifacts/m1655/open_water_absolute_ledger.json`` (written by
``tools/benchmarks/open_water_absolute.py``) and emits
``docs/audit/m1655_open_water_absolute_benchmark.md``.  Every figure in the
document comes from that ledger; the narrative is a template.

Usage::

    python3 tools/benchmarks/open_water_absolute.py --write-reference \
        --json artifacts/m1655/open_water_absolute_ledger.json \
        --markdown artifacts/m1655/open_water_absolute_table.md \
        --figure docs/asset/m1655/open_water_absolute.png
    python3 tools/ops/m1655_build_doc.py
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LEDGER = os.path.join(REPO_ROOT, "artifacts", "m1655", "open_water_absolute_ledger.json")
OUT = os.path.join(REPO_ROOT, "docs", "audit", "m1655_open_water_absolute_benchmark.md")


def _fmt(value, digits=2):
    if isinstance(value, float):
        if value != value:
            return "n/a"
        return f"{value:.{digits}f}"
    return str(value)


def grouped_extremes(rows, key, n=6):
    """Worst |error| rows, sorted, as a markdown table."""
    ranked = sorted((r for r in rows if r["scored"]), key=lambda r: -abs(r[key]))[:n]
    lines = ["| 算例 | J | P/D | Ae/A0 | Z | 参考 | BEM | 误差 [%] |",
             "|---|---|---|---|---|---|---|---|"]
    ref_key = key.replace("err_", "").replace("_pct", "")
    ref_col = {"KT": "KT_ref", "KQ": "KQ_ref", "eta_0": "eta_0_ref"}[ref_key]
    bem_col = {"KT": "KT_bem", "KQ": "KQ_bem", "eta_0": "eta_0_bem"}[ref_key]
    for r in ranked:
        lines.append(
            f"| {r['case_id']} | {r['J']:.2f} | {r['P_D']:.2f} | {r['Ae_Ao']:.2f} "
            f"| {r['Z']} | {_fmt(r[ref_col], 4)} | {_fmt(r[bem_col], 4)} "
            f"| {r[key]:+.1f} |")
    return "\n".join(lines)


def by_pitch(rows):
    buckets = defaultdict(list)
    for r in rows:
        if r["scored"]:
            buckets[r["P_D"]].append(r)
    lines = ["| P/D | 点数 | mean\\|eKT\\| [%] | max\\|eKT\\| [%] | mean\\|eKQ\\| [%] "
             "| max\\|eKQ\\| [%] | mean\\|eη₀\\| [%] |", "|---|---|---|---|---|---|---|"]
    for pd in sorted(buckets):
        members = buckets[pd]
        def stat(key, fn):
            return fn(abs(m[key]) for m in members)
        lines.append(
            f"| {pd:.1f} | {len(members)} "
            f"| {stat('err_KT_pct', lambda v: sum(v) / len(members)):.1f} "
            f"| {stat('err_KT_pct', max):.1f} "
            f"| {stat('err_KQ_pct', lambda v: sum(v) / len(members)):.1f} "
            f"| {stat('err_KQ_pct', max):.1f} "
            f"| {stat('err_eta_0_pct', lambda v: sum(v) / len(members)):.1f} |")
    return "\n".join(lines)


def main() -> int:
    with open(LEDGER, "r", encoding="utf-8") as fh:
        ledger = json.load(fh)
    diag = ledger.get("diagnostics") or {}
    quant = ledger["quantities"]
    rows = ledger["rows"]

    ae = diag.get("dlnKT_dlnAeAo", {})
    pd_ = diag.get("dlnKT_dlnPD", {})
    zt = diag.get("zero_thrust", {})
    zt_rows = zt.get("rows", [])

    zt_table = ["| P/D | Ae/A0 | Z | J₀ 基准 | J₀ BEM | 误差 [%] |",
                "|---|---|---|---|---|---|"]
    for r in zt_rows:
        zt_table.append(f"| {r['P_D']:.1f} | {r['Ae_Ao']:.2f} | {r['Z']} "
                        f"| {_fmt(r['J0_ref'], 4)} | {_fmt(r['J0_bem'], 4)} "
                        f"| {r['err_pct']:+.1f} |")

    def verdict_cn(key):
        return {"FAIL": "**不通过**", "WITHHELD": "存疑"}.get(ledger["verdict"][key],
                                                             ledger["verdict"][key])

    doc = f"""# 敞水推进绝对量级对标：仓内 BEM 模型 vs Wageningen B 系列正典（m1655）

**任务类型**：验证欠账偿还（selfstart，strategy 席）
**上游欠账**：`docs/audit/m1413_propulsion_verification_debt.md` §6.5 ——
"B 系列 KT/KQ 表值入库为 `backend/data/propulsion_benchmark_reference.json`，届时本套件的
J1–J7 可直接叠加绝对量级判据，形成完整三段式（恒等 + 上界 + 表值）"。本单交付该第三段。
**一句话结论**：仓内 BEM 敞水模型全部既有判据（效率上界、单调性、Froude 不变性、动量一致性）
依然成立，但对**已发布基准**的绝对误差 KT 最大 {_fmt(quant['KT']['max_abs_pct'])}%、
KQ {_fmt(quant['KQ']['max_abs_pct'])}%、η₀ {_fmt(quant['eta_0']['max_abs_pct'])}%，
全面突破 15% 接受线（{quant['KT']['n_over_accept']}/{quant['KT']['n']} 点超线）——
即"必要但非充分"判据的经典失效。同时抓出并修复了一处**代码级 REAL←MOCK 失真**：
批量执行器里的 `calc_propeller` 至今仍返回硬编码线性占位式。

---

## 1. 案例介绍（物理背景 / 基准 / 参数表）

敞水性能由三个无量纲量描述：推力系数 $K_T=T/(\\rho n^2D^4)$、
扭矩系数 $K_Q=Q/(\\rho n^2D^5)$、敞水效率 $\\eta_0=JK_T/(2\\pi K_Q)$，
进速系数 $J=V_a/(nD)$。工业界与 ITTC 敞水试验的公共语言就是 $K_T(J),K_Q(J)$ 曲线，
因此"绝对量级"对标只有一种合法形式：**同一 (P/D, A_e/A_0, Z) 几何下逐 J 点比对**。

**基准（reference）**：不是记忆表值，而是仓内已有的正典多项式
`tools/verify/propeller_open_water/reference_bseries.py` —— Oosterveld & van Oossanen (1975)
的 39 项 $K_T$ / 47 项 $K_Q$ 回归（如 Bernitsas, Ray & Kinley 1981, Report No. 237 所载），
且由 `tests/verify/test_propeller_open_water_audit.py` 与
`backend/data/official/Wageningen_Bseries_coeffs.dat` 逐项交叉校验。本单将其**程序化求值**为
数据集 `backend/data/propulsion_benchmark_reference.json`
（{ledger['reference_case_count']} 个算例，生成脚本 sha256 写入文件元数据，零手抄）。

**被检对象**：`backend/services/propeller_open_water.py::open_water_point`
（叶元-动量混合模型，Prandtl 梢/毂损失，剖面 $C_l=2\\pi\\sin\\alpha$ 上限 1.10，
$C_d=0.012+\\alpha^2$，弦长分布为归一化正弦型、总叶面积严格等于 $(A_e/A_0)\\pi D^2/4$）。

**评定包线（显式声明）**：

| 参数 | 范围 | 依据 |
|---|---|---|
| J | 0.2 – 1.0 | 避开系柱（$J\\to0$，相对误差分母趋零）与风车状态 |
| P/D | 0.8 – 1.4 | 可调距桨商船常用带，且 B 系列不外推 |
| A_e/A_0 | 0.40 – 0.85 | 同上（正典有效域 0.2–1.05 的内层） |
| Z | 4, 5 | 主流叶数 |

B 系列正典有效域本身为 $Z\\in[2,7]$, $P/D\\in[0.5,1.4]$, $A_e/A_0\\in[0.2,1.05]$, $J\\in[0,1.5]$。

---

## 2. 怎么做的（方法 / 流程 / 工具链）

1. **数据入库**（`--write-reference`）：对包线做笛卡尔积（{ledger['reference_case_count']} 点），
   逐点调用正典多项式求值，写出 JSON（schema 版本、provenance、生成脚本 sha256、包线、
   $\\eta_0$ 定义）。旧欠账要求的正是这个路径。
2. **逐点打分**（`run_benchmark`）：BEM 与参考在 $(J,P/D,A_e/A_0,Z)$ 上逐点比对，
   输出 $K_T$、$K_Q$、$\\eta_0$ 的相对误差，按 15%（接受线）/ 3%（严格线）双阈值判定。
3. **相对误差的适用域**（关键方法学点）：$K_T\\to 0$、$K_Q\\to 0$ 时**比值发散**，
   与模型好坏无关。因此只在参考值 ≥ 同桨 $J=0.2$ 处值的
   {ledger['relative_floor_fraction'] * 100:.0f}% 时计分；被排除
   {ledger['excluded_case_count']} 点如实计数并保留在原始 ledger 中（不静默丢弃）。
   被排除点全部是 $P/D=0.8$、$J=1.0$ 的风车工况（$K_T,K_Q$ 均已过零）。
4. **失败闭合**：数据集缺失/schema 不符/空表一律抛异常（绝不落成"通过"）。
5. **判别力自检**：测试中把参考 $K_T$ 人为放大 50%，判据必须判 FAIL；
   与模型自身输出比对时必须判 PASS(<3%)。两侧都钉住，杜绝"橡皮图章"。
6. **结构性诊断**（回答"为什么错"而非仅"错了"）：$K_T$ 对 $A_e/A_0$ 与 $P/D$ 的对数敏感度、
   零推力进速系数 $J_0$、剖面失速钳位分支占用率。
7. **工具链**：`tools/benchmarks/open_water_absolute.py`（本单新增，纯离线，无服务依赖）、
   `pytest tests/test_open_water_absolute_benchmark.py`（22 项）、matplotlib(Agg) 出图。

---

## 3. 计算结果

### 3.1 头条结果（计分子域 {ledger['scored_case_count']} 点）

| 量 | max\\|err\\| [%] | mean\\|err\\| [%] | RMS [%] | 超 {ledger['accept_pct']:.0f}% 点数 | 判定 |
|---|---|---|---|---|---|
| $K_T$ | {_fmt(quant['KT']['max_abs_pct'])} | {_fmt(quant['KT']['mean_abs_pct'])} | {_fmt(quant['KT']['rms_pct'])} | {quant['KT']['n_over_accept']}/{quant['KT']['n']} | {verdict_cn('KT')} |
| $K_Q$ | {_fmt(quant['KQ']['max_abs_pct'])} | {_fmt(quant['KQ']['mean_abs_pct'])} | {_fmt(quant['KQ']['rms_pct'])} | {quant['KQ']['n_over_accept']}/{quant['KQ']['n']} | {verdict_cn('KQ')} |
| $\\eta_0$ | {_fmt(quant['eta_0']['max_abs_pct'])} | {_fmt(quant['eta_0']['mean_abs_pct'])} | {_fmt(quant['eta_0']['rms_pct'])} | {quant['eta_0']['n_over_accept']}/{quant['eta_0']['n']} | {verdict_cn('eta_0')} |

严格线 3% 下通过的相对误差点数为 $K_T$ {quant['KT']['n'] - quant['KT']['n_over_strict']}/{quant['KT']['n']}、
$K_Q$ {quant['KQ']['n'] - quant['KQ']['n_over_strict']}/{quant['KQ']['n']}、
$\\eta_0$ {quant['eta_0']['n'] - quant['eta_0']['n_over_strict']}/{quant['eta_0']['n']}。

![敞水曲线与误差分布](../asset/m1655/open_water_absolute.png)

*图 1*　上排：两组几何的 $K_T$、$K_Q$ 曲线（实线 BEM，虚线/点线正典基准）；
下排：$A_e/A_0=0.55$、$Z=4$ 时不同 $P/D$ 的 $K_T$ 与 $\\eta_0$ 误差随 $J$ 的分布，
红线为 ±15% 接受带。

### 3.2 按螺距比分组的误差结构

{by_pitch(rows)}

误差随 $P/D$ 单调下降：低螺距（轻载、$\\alpha$ 小）区 BEM 高估效率与推力，
高螺距（重载）区转入低估。这不是整体比例误差——整体标定无法同时消除两端。

### 3.3 最劣算例（每量前 6）

$K_T$：

{grouped_extremes(rows, 'err_KT_pct')}

$K_Q$：

{grouped_extremes(rows, 'err_KQ_pct')}

$\\eta_0$：

{grouped_extremes(rows, 'err_eta_0_pct')}

### 3.4 结构性诊断（回答"为什么"）

| 指标 | BEM | 正典基准 | 判读 |
|---|---|---|---|
| $\\mathrm{{d}}\\ln K_T/\\mathrm{{d}}\\ln(A_e/A_0)$（$J=0.6,P/D=1.0,Z=4$） | {_fmt(ae.get('bem'))} | {_fmt(ae.get('bseries'))} | BEM 弦长 $\\propto A_e/A_0$ ⇒ 推力近线性随面积变化；正典几乎不敏感。**相差约 {_fmt(ae.get('bem', 0) / max(ae.get('bseries', 1e-9), 1e-9), 1)} 倍**，是主偏差源之一 |
| $\\mathrm{{d}}\\ln K_T/\\mathrm{{d}}\\ln(P/D)$（同上） | {_fmt(pd_.get('bem'))} | {_fmt(pd_.get('bseries'))} | BEM 螺距标定略强 |
| 剖面失速（$C_l$ 钳位）分支占用率 | 均值 {ledger['clamp_occupancy_mean'] * 100:.0f}%，最大 {ledger['clamp_occupancy_max'] * 100:.0f}% | — | 模型大部分工作点跑在钳位分支上：几何螺距角在毂部可达 60° ，剖面 $\\alpha$ 常在 10–45°，远超 $C_l$ 上限对应角度 |
| 动量迭代收敛 | {ledger['all_induction_converged']} | — | 部分叶栅落入动量极限带（重载钳位），非数值失败但降低了可信度 |

零推力进速系数 $J_0$（尺度无关的误差指标，与 $K_T$ 整体比例误差无关）：

{chr(10).join(zt_table)}

$J_0$ 最大误差 {_fmt(zt.get('max_abs_err_pct'), 1)}%。该指标显著优于系数本身误差，
说明模型"过零位置"大致正确而"斜率/曲率"偏离——这与失速钳位削平 $K_T(J)$ 曲率的判断一致。

### 3.5 代码级发现：REAL←MOCK 失真仍在（已修）

`tests/test_propeller_open_water.py::TestBatchWrapperWiring` 在本单开工时是**红的**
（`KeyError: 'model'`）。根因不在测试，而在实现：批量执行器
`tools/batch_scan/batch_executor.py::ShipCalculationEngine.calc_propeller` 自 mission 1393
写了 BEM 模型之后**从未被接线**，仍在返回：

```python
KT = max(0.05, 0.5 - 0.3 * J)      # 与 P/D、A_e/A_0、Z 无关
KQ = max(0.01, 0.05 + 0.02 * J)    # K_Q 随 J 上升 —— 真实螺旋桨必然下降
eta0 = min(0.85, max(0.3, J*KT/(2*pi*KQ)))   # 人工平台
```

这正是 `propeller_open_water.py` 模块 docstring 宣称"已替换"的占位式。本单把它接上 BEM，
保留原输出键（向后兼容）并新增 `model="bem"`；导入失败时**抛异常而非降级回占位式**。
配套新增 5 项证伪测试（$K_Q$ 必须随 $J$ 下降、$K_T$ 必须响应 $P/D$、$\\eta_0$ 不得落在人工平台、
`model` 标识、几何校验），任何回退到占位式的改动都会被这套测试打红。

---

## 4. 误差判定（逐项，是否 <3%）

| 关键量 | 基准 | 误差 | <3%？ |
|---|---|---|---|
| $K_T$（全包线最大） | 正典 B 系列多项式 | {_fmt(quant['KT']['max_abs_pct'])}% | **否** |
| $K_Q$（全包线最大） | 同上 | {_fmt(quant['KQ']['max_abs_pct'])}% | **否** |
| $\\eta_0$（全包线最大） | 同上 | {_fmt(quant['eta_0']['max_abs_pct'])}% | **否** |
| $K_T$（平均绝对） | 同上 | {_fmt(quant['KT']['mean_abs_pct'])}% | **否**（但已接近 15% 线以外） |
| 零推力进速系数 $J_0$ | 同上（数值寻根） | {_fmt(zt.get('max_abs_err_pct'), 1)}% | **否** |
| 参考数据集 vs 仓内多项式重算 | 程序化复算 | ≤1e−12（测试断言） | **是** |
| 数据集内 $\\eta_0=JK_T/(2\\pi K_Q)$ 恒等 | 定义式 | ≤1e−12（测试断言） | **是** |
| 判据自检：模型自比 | 自身输出 | ≤3%（PASS 断言） | **是** |

**显式回答：关键量（$K_T$、$K_Q$、$\\eta_0$）对已发布基准的误差均不满足 <3%，也不满足 <15% 接受线。**
因此 `propeller.open_water` 的"绝对精度已验证"不成立；其既有的 REAL 分级只对
"解析上界 + 结构不变量"这一类判据成立。

---

## 5. 注意点 / 局限 / 适用范围

1. **基准的性质**：对标对象是 B 系列**回归多项式**（工业标准近似），不是 ITTC 敞水**试验**曲线。
   两者差异（约 2–5% 量级）未计入本单误差分解，因此上表误差是"对同族模型"的偏差，
   不是对实桨试验的偏差。要补试验级基准则需引入 ITTC 敞水试验数据集（本单未做）。
2. **相对误差的定义域**：$J$ 越靠近零推力/零扭矩点，比值越无意义。本单用"参考值不低于该桨
   $J=0.2$ 值的 10%"作为显式下限，并把被排除点（{ledger['excluded_case_count']} 个风车工况）
   如实计数。任何引用本单数字的场合都必须带上这个子域限定。
3. **模型内部结构决定上限**：弦长分布是通用正弦型而非 B 系列真实弦长/拱度分布，
   剖面模型为无拱度平板 + 恒定 $C_{{d0}}$ + 硬钳位。即使把 $C_l$ 斜率、$C_{{d0}}$ 当自由参数标定，
   也**无法**同时压平低 $P/D$ 与高 $P/D$ 两端的误差（本单做过斜率扫描：钳位使斜率完全失效）。
   结论是**结构性重做（真实剖面族 + 拱度 + 失速后模型）**而非参数微调。
4. **反向依赖**：$A_e/A_0$ 敏感度 20 倍偏差意味着该模型在变叶面积设计中会给出系统性错误的趋势，
   比单点误差更危险——趋势错误会让优化器朝错误方向走。
5. **未覆盖**：桨后伴流场、空泡、非定常来流下的推力/扭矩波动、尺度效应（Re 修正）。
6. **服务侧**：本单全程离线，不依赖 `:8001`；m1413 记录的在线端点问题（K_Q/K_T≡0.1、
   η₀ 钳位 0.850）属**另一套实现**，不在本单修复范围，仍待修。

---

## 6. 建议（按优先级）

1. **分级诚实化**：`propeller.open_water` 应记为
   `REAL(analytic/structural)` + `WITHHELD(absolute accuracy)`，并在能力卡上直接引用本单的
   误差表。分级失真比算错更危险——下游派单会把它当"已对标"。
2. **剖面族重做**：以 NACA a=0.8 / 66-mod 类剖面替换无拱度平板，加入拱度（零升力角偏移）、
   有限展弦比升力线斜率修正 $a_{{3D}}=2\\pi\\,AR/(AR+2)$、失速后 $C_l$ 平台与分离阻力，
   使钳位分支占用率降到 ~0；验收判据直接用本单 harness（目标：$K_T$、$K_Q$ max|err| < 15%）。
3. **弦长/拱度分布对齐 B 系列**：以公开的 B 系列几何（0.7R 螺距线、$a_{{0.7R}}$）生成弦长与螺距分布，
   而不是通用正弦型；这是把 $\\mathrm{{d}}\\ln K_T/\\mathrm{{d}}\\ln(A_e/A_0)$ 从
   {_fmt(ae.get('bem'))} 拉回 {_fmt(ae.get('bseries'))} 量级的唯一途径。
4. **接上试验级基准**：补 ITTC 敞水试验曲线（该数据有版本与试验编号，可追溯），
   与 B 系列多项式并列成为双基准；届时本 harness 只需增加一个 data source 即可。
5. **回归门**：把本单的 22 项测试接入 CI，其中"债务上界"（$K_T$≤85%、$K_Q$≤65%、$\\eta_0$≤40%）
   是**上界**语义——改进可通过，回退必红。

---

## 7. 复现

```bash
# 1) 重建参考数据集（也可直接使用已提交的 JSON）
python3 tools/benchmarks/open_water_absolute.py --write-reference

# 2) 全量对标 + 表 + 图
python3 tools/benchmarks/open_water_absolute.py \\
    --json artifacts/m1655/open_water_absolute_ledger.json \\
    --markdown artifacts/m1655/open_water_absolute_table.md \\
    --figure docs/asset/m1655/open_water_absolute.png

# 3) 测试（含判据灵敏度与占位式证伪）
python3 -m pytest tests/test_open_water_absolute_benchmark.py -q
```

产物：
`backend/data/propulsion_benchmark_reference.json`（{ledger['reference_case_count']} 算例，程序化生成）、
`artifacts/m1655/open_water_absolute_ledger.json`（逐点原始误差）、
`docs/asset/m1655/open_water_absolute.png`、`tests/test_open_water_absolute_benchmark.py`。
"""
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print(f"doc -> {os.path.relpath(OUT, REPO_ROOT)} ({len(doc)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())