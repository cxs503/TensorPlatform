# 阻力链路验证欠账清偿：Holtrop–Mennen 1982 真实现 + 基准数据集恒等式审计

**任务**：tp-arch-1（strategy 席）自找活 · mission 1398 · 2026-11-01
**仓**：TensorPlatform · 分支 `agent/m1398`
**EDIT_WHITELIST**：`backend/`、`tools/verify/`、`docs/`、`artifacts/`

---

## 1. 问题从哪来（验证欠账，不是新功能）

平台 21 能力清单把 `calc.resistance` 标为 **REAL**，`POST /api/v1/calculations/resistance`
的 docstring 与响应字段都声称使用 **Holtrop-Mennen** 法。实际代码（`backend/api/calculations.py`）：

| 环节 | 声称 | 实际 | 证据等级 |
|---|---|---|---|
| 湿表面积 | Holtrop 回归 | `S = π B L (0.5 + 0.25 Cb)`——无量纲项与船型回归无关 | MOCK |
| 摩擦阻力 | ITTC-1957 | `0.075/(log10Re-2)^2` | REAL |
| 残阻力 | Holtrop 兴波/形状项 | `Cr = 0.002 (1+2Cb) Fn^2.5`——手搓拟合，无文献出处 | MOCK |
| 相关津贴 | — | 未实现 | STUB |

同一仓另有 `optimizer/interfaces/resistance_eval.py`（自称 Holtrop，实为
`Cr_base = 0.2 exp(-1.5/Fn^1.2)·…`、`Rw_base = 0.5ρV²S·0.002 exp(-2/Fn²)`），以及
`tools/verifier/pilots/resistance_calculator.py` 的 `1+k`（注释自承 "Scaled for pilot"，
末尾 `* 0.1`）。**三处都叫 Holtrop，三处都不是 Holtrop。**

而唯一的外部基准 `backend/data/benchmark_reference.json` 被判据门
（`/calculations/benchmark_validate`）用于 pass/fail —— 所以先把基准本身按物理恒等式验一遍。

## 2. 做了什么

### 2.1 基准数据集恒等式审计（`tools/verify/resistance_benchmark_audit.py`）

一个条目要当基准，必须满足**定义自身字段**的三条恒等式（不是模型假设，是数学）：

```
(I1)  Fn     = V / sqrt(g L)                 容差 ±2%
(I2)  Rt     = 0.5 rho V^2 S Ct              容差 ±2%   (rho: 模型=998.5 淡水 / 实船=1025 海水)
(I3)  Delta  = rho Cb L B T                  容差 ±5%   (附体/余量差异)
```

判据机器化、逐条落 JSON，结论不依赖任何文献取值。

### 2.2 Holtrop–Mennen 1982(+1984) 真实现（`backend/services/holtrop_mennen.py`）

纯标准库、无依赖，按论文式实现：ITTC-57 `Cf`、湿表面积回归、形状因子 `1+k`（含
`c14/Cstern`、`LR`）、兴波阻力全套系数 `c1,c2,c5,c7,λ,m1,m2,c15,c16,iE`、以及 1984
增补的球鼻艏 `RB`、方尾浸没 `RTR`、相关津贴 `RA`、附体 `RAPP`。域外（`Cb`、`L/B`、
`B/T`、`Fn>0.45`）不静默外推，返回 `warnings` 与 `in_domain()`，供证据分级用。

## 3. 结果

### 3.1 审计：12/12 算例被判 REJECT（数值见 `artifacts/resistance_benchmark_audit/audit.json`）

| 判据 | 命中算例 | 典型偏差 |
|---|---|---|
| (I1) Fn 定义 | DTMB5415 4 例 | 列值 0.061 vs 定义值 0.343（**偏差 82%**），末例 `Fn=2.06` 物理不可能 |
| (I2) Ct↔Rt | SUBOFF 4 例 + DTMB 4 例 | SUBOFF-01：Ct=0.0038 但 Rt=12.5 N → Ct 只有 **0.00051**（7.4 倍） |
| (I2) Ct↔Rt | KVLCC2 4 例 | 列 Rt 550 kN 与列 Ct 0.0028 反推 Rt≈2.0 MN（**3.6 倍**） |
| (I3) 排水量 | KVLCC2 4 例 | 307,000 t vs `ρCbLBT`=320,289 t（4.2%，容差内） |

数据集结论：**REJECT_AS_BASELINE**。任何"对标实验误差 3%/5%/15%"的结论若建立在它上面都无效。

### 3.2 Holtrop–Mennen 实现的自洽与交叉核对

| 检查项 | 结果 | 是否 <3% |
|---|---|---|
| 湿表面积：Holtrop-S vs Denny–Mumford（`S=1.7LT+∇/T`）两独立回归互校 | KVLCC2 26,717 vs 26,345 m²（+1.41%）；DTMB5415 4.5 vs 4.6 m²（−1.86%） | **是** |
| `c16` 两分支在 `Cp=0.8` 处连续 | 1.1583 vs 1.1574（0.08%） | 是 |
| 形状因子 `1+k`（KVLCC2 实船） | 1.337（肥大型油轮合理带 1.30–1.40） | 无基准 |
| KVLCC2 实船 15.5 kn 总阻力 | 1.858 MN，`Ct=2.136e-3` | 无基准（文献 WIGLEY/EFD 引用本次离线不可读） |
| 兴波项 `RW` | 33.0 kN，仅占总阻力 1.8% | **WITHHELD**（见 §4） |
| Holtrop vs 数据集列值 Ct（KVLCC2） | −23.7% / −28.9% / −31.1% / −33.0% | 否（且基准已被 §3.1 判废，差值同时反映基准错误） |

## 4. 注意点与局限（honest）

1. **兴波项是 WITHHELD，不是 REAL**。`c1..c16` 中 `c16` 两分支在 `CP=0.8` 的自洽（0.08%）
   与 `c15` 分段形式互为佐证，说明系数回忆方向正确；但 `c7` 的**中段在 `B/L=0.11` 处
   本身不连续**（线性支给 −0.068，三次根支给 0.110），而 `c1 ∝ c7^3.786`。实现对
   `c7<=0` 截断并报警（`c7_flagged`），但这说明该段系数无法凭记忆担保；`RW` 的绝对值
   在拿到论文原表（Holtrop & Mennen 1982, Table 2）前**不得作为 REAL 证据**。KVLCC2
   `Fn=0.143` 给出 `RW=1.8%·RT` 偏小，怀疑即源于此，已在报告里显式标注。
2. **`CA` 只实现主项**（`0.006(L+100)^-0.16 - 0.00205`）。1984 的 `TF/LWL>0.04` 次级修正
   未实现，KVLCC2（`TF/L=0.065`）会命中该分支，实现里主动发 warning，不假装完整。
3. **主尺度与船型系数来源**：审计只用数据集自带的一次参数（L/B/T/Cb）；`Cm`、`Cwp`、
   `lcb`、`iE` 由模块内的回归/公式**派生并在 `result.derived` 里声明**，不是从文献抄的数字。
   这是刻意的：宁可承认派生，也不引入无法复核的"文献值"。
4. **仍然没有真正的外部实验对标**。本单的判定是"基准不可用"（数学上确凿）+"实现自洽/
   交叉回归 1.4%"，不宣称"阻力误差 <3%"。真正的对标需要重读 Roddy 1990 / SIMMAN 2008
   原表并把数据集按同一尺度、同一 `ρ`、同一 `S` 重建 —— 那是下一单（§5）。
5. **未接线**：`/calculations/resistance` 默认行为未改动（避免打断在飞调用方与既有断言），
   新模块以独立 service 提供，接线需单列变更单并同步 `benchmark_validate` 的判据。

## 5. 给下一单的线索

- 用 Roddy 1990（SUBOFF）与 SIMMAN 2008（KVLCC2）**原表**重建 `benchmark_reference.json`：
  统一尺度（模型/实船分文件）、统一 `ρ`、必带 `S` 与 `Cm/Cwp/lcb`，并让审计脚本进 CI
  （当前 12/12 REJECT 就是回归测试的锚点）。
- Holtrop 兴波项需要论文 Table 2 原值做逐系数单测（`c1`、`m1`、`λ` 至少三个点）。
- `1+k` 的独立基准：Prohaska 试验法需要 `Ct(Fn)` 曲线族，可与 ITTC-78 说明书的样例船配对。

## 6. 复现

```bash
python3 tools/verify/resistance_benchmark_audit.py      # 生成 audit.json / table.md / 4 张 png
python3 -m pytest backend/tests/test_holtrop_mennen.py -q   # 22 passed
```