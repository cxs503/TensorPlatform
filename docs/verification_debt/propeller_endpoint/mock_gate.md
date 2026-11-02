# 敞水螺旋桨端点 MOCK 指纹闸（propeller-design/wageningen-b）

作者：tp-impl-1（platform 席，mission 1791） · 全部数字由脚本从 `artifacts/mock_fingerprint/propeller_endpoint_mock_gate.json` 注入，零手抄

## ① 案例介绍

被测端点：`POST http://127.0.0.1:8001/api/v1/propeller-design/wageningen-b` —— 船舶总体性能平台的敞水螺旋桨正门，平台自述能力 `propeller.open_water = REAL`，
响应里 `method` 字段回填「Wageningen B系列回归公式」。

扫描矩阵：4 个设计点 × 13 个进速比 J∈[0,1.2]：

| 设计点 | Z | A_e/A_0 | P/D |
|---|---|---|---|
| Z4_Ae0.40_PD1.0 | 4 | 0.40 | 1.0 |
| Z4_Ae0.70_PD1.0 | 4 | 0.70 | 1.0 |
| Z5_Ae0.75_PD1.4 | 5 | 0.75 | 1.4 |
| Z4_Ae0.70_PD0.8 | 4 | 0.70 | 0.8 |

被测量：`K_T`、`K_Q`、`η_0`。敞水特性是螺旋桨设计的第一性曲线：`K_T(J)`、`K_Q(J)` 由 39/47 项
Wageningen B 系列回归给出（Oosterveld & van Oossanen 1975，Re=2×10⁶），`η_0=(J/2π)·K_T/K_Q` 必须 <1，
且必须随 P/D、Z、A_e/A_0 变化。这些是**不需要实验基线**的物理约束。

## ② 怎么做的

新增 `tools/mock_fingerprint/`：一组纯函数「物理不变量」指纹探测器 + 裁决器，离线可跑、无外部依赖。

| 探测器 | 判据 | 阈值 |
|---|---|---|
| `ratio_lock` | 两输出在整条扫描上比值恒定 | 相对散布 ≤1e-12 |
| `parameter_blindness` | 输出对必须依赖的设计参数不敏感 | 相对散布 ≤1e-12 |
| `physical_bound` | 违反硬物理界（η_0<1、无推力时 η_0=0） | 硬界 |
| `clamp_plateau` | 上段扫描被钉成常数（程序截断而非物理） | 相对散布 ≤1e-12 |
| `reference_deviation` | 对独立参考（`tools/ref_anchor_lib.py` 的 B4-70 39/47 项回归）的相对误差 | >15% 判失败 |

裁决（`judge()`）：任一**结构性**指纹命中 → MOCK；仅基准偏差超限 → WITHHELD；全静默且基准吻合 → REAL。
探测器先用合成响应自证（缺陷响应必须触发、健康响应必须静默），再用同一套代码裁决真端点——
先用 B 系列回归造「健康端点」喂给探测器，`ratio_lock` 静默（真实序列 K_Q/K_T 散布 >5%），说明阈值不是空转。

## ③ 计算结果

### 3.1 端点输出摘录

| 设计点 | K_T(J=0) | K_Q(J=0) | K_T(J=0.5) | K_Q(J=0.5) | η_0(J=0.5) | η_0(J=0.8) |
|---|---|---|---|---|---|---|
| Z4_Ae0.40_PD1.0 | 0.2700 | 0.0270 | 0.1800 | 0.0180 | 0.7958 | 0.8500 |
| Z4_Ae0.70_PD1.0 | 0.3600 | 0.0360 | 0.2400 | 0.0240 | 0.7958 | 0.8500 |
| Z5_Ae0.75_PD1.4 | 0.5000 | 0.0500 | 0.4080 | 0.0408 | 0.7958 | 0.8500 |
| Z4_Ae0.70_PD0.8 | 0.2880 | 0.0288 | 0.1680 | 0.0168 | 0.7958 | 0.8500 |

### 3.2 指纹判定

| 探测器 | 结果 | 证据 |
|---|---|---|
| `ratio_lock` K_Q/K_T | **触发** | K_Q/K_T = 0.100000000000 恒定，相对散布 2.78e-16（48 点，跨 4 个设计点） |
| `parameter_blindness` η_0 vs P/D | **触发** | J=0.5 处 4 个设计点 η_0 完全相同（散布 0.00e+00） |
| `parameter_blindness` K_T vs P/D | 未触发 | K_T 散布 0.588（K_T 走的是另一条硬编码曲线，对 P/D 才有响应） |
| `clamp_plateau` η_0 | **触发** | J≥0.80 的 20 个点 η_0 全 = 0.85（散布 0.00e+00） |
| `physical_bound` η_0<1 | 未触发（**被截断掩盖**） | 无截断时 η_0=10J/2π 在 J>0.628 即破界，但 0.85 截断先行，故该界自身不报 |
| `reference_deviation` vs B4-70 | **触发** | 全域 22/26 点超 15%；物理区间 J≤0.8 内 15/18 点超 15% |

综合裁决：**MOCK** —— structural fingerprint fired: clamp_plateau, parameter_blindness, ratio_lock。
与平台自述 `propeller.open_water = REAL` **矛盾**（OVERCLAIM）。

### 3.3 与独立基准的逐点误差（B4-70，P/D=1.0，A_e/A_0=0.70，Z=4）

物理区间 J∈[0,0.8]（在回归的零推力交点 J₀=1.0618 之前）：

| 量 | 最差点 | 平台 | B4-70 回归 | 相对误差 |
|---|---|---|---|---|
| K_T | J=0.8 | 0.1680 | 0.1297 | **+29.5%** |
| K_Q | J=0.2 | 0.03120 | 0.05942 | **-47.5%** |

J>0.8 的点（8 个）跨越参考曲线的零推力交点，相对误差失去意义（分母→0，最极端可达数倍），
故误差表只用物理区间；越界点本身已由 `ratio_lock`/`clamp_plateau` 定性钉死。

### 3.4 图

`artifacts/mock_fingerprint/propeller_endpoint_mock_gate.png`
左：端点 K_T 与 10K_Q 对 B4-70 回归；右：4 个设计点的 η_0 塌缩到同一条 η_0=min(10J/2π, 0.85) 直线（附 η_0<1 参考线）。

## ④ 注意点与局限

1. **η_0 截断掩盖了 η_0>1 越界**：单靠「η_0<1」这条物理界查不出缺陷，必须配 `clamp_plateau`
   同时钉住「线性律 + 上端截断」两件套；本单为此新增该探测器。
2. **K_T 有 P/D 依赖、K_Q 没有**：端点对 K_Q 直接取 `K_T/10`，因此「K_T 单调 / 随参数变化」这类常规
   自洽测试会放过它，只有比值锁定判据抓得住。
3. **适用范围**：本闸只裁决敞水端点**前门**。批量执行器侧螺旋桨已在 mission 1770 升级为 BEM 理论，
   本结论不外推到执行器路径；也不外推到回归库本身（`tools/ref_anchor_lib.py` 已与 Bernitsas 独立转写逐项互校）。
4. **修复归属**：缺陷在船舶平台 serving 层（ship-performance-platform 仓），不在本仓改造范围，按纪律派单而非热修。
5. 阈值 1e-12 是「逐位相同」级别；真实回归极难出现这种巧合（B 系列 K_Q/K_T 实测散布 >5%，见测试）。

## ⑤ 误差判定（显式回答是否 <3%）

| 关键量 | 基准 | 相对误差（最差点） | 是否 <3% |
|---|---|---|---|
| K_T（B4-70，P/D=1.0） | Oosterveld & van Oossanen 1975，39 项回归 | +29.5% | **否** |
| K_Q（B4-70，P/D=1.0） | 同上，47 项回归 | -47.5% | **否** |
| η_0 设计参数敏感性 | 物理要求（随 P/D、Z、A_e/A_0 变） | 0%（完全不敏感） | **否** |

结论：**MOCK**。`method` 自述「Wageningen B系列回归公式」与实测指纹不符，属自述—实现背离。

## ⑥ 复现

```bash
python3 tools/mock_fingerprint/__init__.py --probe                       # 出 ledger + 图
python3 -m pytest tests/test_mock_fingerprint_propeller_gate.py -q       # 8 passed, 1 xfailed
```

测试层：`tests/test_mock_fingerprint_propeller_gate.py` 先在合成响应上验证探测器（缺陷触发、健康静默），
再用 `xfail(strict=True)` 钉住目标态「端点应吻合 B 系列回归」——修复落地时该测试变 XPASS（硬失败）强制更新本闸；
另一条测试把「缺陷存在」钉死，防止修复被静默回退。
