# 结构域验证欠账清偿：板格弹性屈曲系数对精确板理论（von Mises / Timoshenko）的复核

**任务** m1852（自找活·验证欠账类）· **仓** TensorPlatform · **被测件** 船舶总体性能平台 `POST /api/v1/fea/plate-buckling`（平台源码 `backend/api/fea.py::PlateBucklingCalculator`）

> 本报告由 `audit_plate_buckling.py` 程序化生成，表内每个数字都从 `numbers.json` 读回，零手抄。参考解在 `refs_plate.py`，不含任何平台代码。

## ① 案例介绍

板格屈曲是船体结构校核的入门算子：外板/舱壁在总纵弯曲与局部载荷下的弹性屈曲应力
决定了板厚与骨材间距。平台把它作为“结构”域门面接口之一，返回临界应力
`critical_stress`、临界载荷 `critical_load`、屈曲半波数 `half_waves` 与安全裕度。

### 参数表（审计矩阵）

| 量 | 值 | 说明 |
|---|---|---|
| 板宽 b | 1.0 m | 载荷横向边长，固定 |
| 板长 a | alpha*b, alpha in [0.5, 0.667, 0.8, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0] | 审计扫掠变量 |
| 板厚 t | 0.012 m | 12 mm 典型外板 |
| 弹性模量 E | 210000.0 MPa | 钢 |
| 泊松比 nu | 0.3 | |
| 屈服应力 | 235.0 MPa | 用于 safety_margin |
| 单位应力 pi^2 D/(b^2 t) | 27.3312 MPa | k 的乘子 |

### 参考解（与平台无关）

四边简支、沿 a 向单轴压缩的精确解（von Mises 1898；Timoshenko & Gere, *Theory of Elastic Stability*, eq. 9-6）：

```
sigma_cr = k * pi^2 * D / (b^2 * t),   D = E t^3 / (12 (1 - nu^2))
k(alpha) = min_m (m/alpha + alpha/m)^2
```

双正弦级数正是该边界条件下的精确特征基，因此上式是精确特征值而非拟合。
本次用两条独立路线互校：闭式（`k_ss_exact`）与正弦-Galerkin 数值特征值（`k_ss_ritz`），二者在全部审计点上相对偏差 ≤1e-15。
纯剪的简支板系数取 Timoshenko 式 `k_s = 5.34 + 4/alpha^2`（alpha≥1），
并用 8×8 正弦-Galerkin 特征值问题独立复算，作为该参考自身的可信度证据。

## ② 怎么做的

1. `refs_plate.py` 实现两路独立参考解（闭式 + 数值特征值）并自校；
2. `audit_plate_buckling.py` 以审计矩阵打平台端点，把返回的临界应力反推为
   等效屈曲系数 `k_platform = sigma_cr * b^2 t / (pi^2 D)`，与精确 `k` 逐点比对；
3. 同一脚本另做四项正交检查：厚度/宽度/模量的标度律（验证 `pi^2 D/(b^2 t)` 部分）、
   临界载荷量纲一致性、半波数与系数的一致性、边界 token 的健壮性；
4. 顺带核查同一缺陷是否传播到兄弟端点 `/fea/stiffened-panel` 与 `/structure/plate`；
5. 工具链：Python 3.11.6 + numpy + matplotlib，被测服务 http://127.0.0.1:8001/api/v1。

## ③ 计算结果

### 3.1 单轴压缩（四边简支）：等效屈曲系数

| alpha=a/b | 精确 k | 精确半波数 m* | 平台 k（反推） | 平台 half_waves | 相对误差 |
|---|---|---|---|---|---|
| 0.5 | 6.2500 | 1 | 3.9998 | 1 | -36.00% |
| 0.667 | 4.6926 | 1 | 3.9998 | 1 | -14.76% |
| 0.8 | 4.2025 | 1 | 3.9998 | 1 | -4.82% |
| 1 | 4.0000 | 1 | 3.9998 | 1 | -0.00% |
| 1.25 | 4.2025 | 1 | 3.9998 | 1 | -4.82% |
| 1.5 | 4.3403 | 2 | 3.9998 | 2 | -7.84% |
| 2 | 4.0000 | 2 | 3.9998 | 2 | -0.00% |
| 2.5 | 4.1344 | 3 | 3.9998 | 3 | -3.26% |
| 3 | 4.0000 | 3 | 3.9998 | 3 | -0.00% |
| 4 | 4.0000 | 4 | 3.9998 | 4 | -0.00% |

平台返回的等效系数在所有 aspect ratio 上恒等于 4.0000：即“长板”取值被无条件套用，
`half_waves` 虽被计算并在响应里返回，却**从未参与**系数计算。

### 3.2 纯剪（四边简支）

| alpha=a/b | Timoshenko k_s | Galerkin 复算 k_s | 平台 k（反推） | 相对误差 |
|---|---|---|---|---|
| 1 | 9.3400 | 9.3283 | 9.3399 | -0.00% |
| 1.25 | 7.9000 | 7.7698 | 7.9001 | +0.00% |
| 1.5 | 7.1178 | 7.0731 | 7.1179 | +0.00% |
| 2 | 6.3400 | 6.5481 | 6.3400 | +0.00% |
| 3 | 5.7844 | 5.8433 | 5.7846 | +0.00% |

### 3.3 标度律（pi^2 D/(b^2 t) 部分是否正确）

| 变更 | 平台临界应力 [MPa] | 由 alpha=1 基准按理论标度推得 [MPa] | 相对误差 |
|---|---|---|---|
| t=0.008 | 48.590 | 48.587 | +0.007% |
| t=0.016 | 194.360 | 194.347 | +0.007% |
| b=0.5 | 437.300 | 437.280 | +0.005% |
| b=2.0 | 27.330 | 27.330 | +0.000% |
| E=70000.0 | 36.440 | 36.440 | +0.000% |
| E=300000.0 | 156.180 | 156.171 | +0.005% |

sigma ∝ t^2、∝ 1/b^2、∝ E 全部成立（误差 <<1%），说明缺陷只在系数 k，不在标度因子。

### 3.4 临界载荷量纲

| alpha | 平台 sigma_cr [MPa] | 平台 critical_load [kN] | 由该 sigma_cr 应有的 P=sigma*b*t [kN] | 比值 |
|---|---|---|---|---|
| 0.5 | 109.32 | 0.0000 | 1311.8 | 0.000e+00 |
| 1 | 109.32 | 0.0000 | 1311.8 | 0.000e+00 |
| 2 | 109.32 | 0.0000 | 1311.8 | 0.000e+00 |

### 3.5 兄弟端点是否继承缺陷

- `stiffened_alpha=0.5`: {"buckling_strength": 159.62, "ultimate_strength": 159.81}
- `stiffened_alpha=1.0`: {"buckling_strength": 159.62, "ultimate_strength": 159.81}
- `stiffened_alpha=2.0`: {"buckling_strength": 159.62, "ultimate_strength": 159.81}
- `structure_plate_frame=0.5`: {"critical_stress": 47.5}
- `structure_plate_frame=1.0`: {"critical_stress": 11.9}
- `structure_plate_frame=2.0`: {"critical_stress": 3.0}

## ④ 结果图

### 屈曲系数 k(alpha)：精确解（线）与平台反推值（点）。平台值恒为 4，只在 alpha=1,2,3,4 等整数点落到精确曲线上。

![fig1_k_vs_alpha.png](figs/fig1_k_vs_alpha.png)

### 平台 k 的带符号相对误差：负值代表料敌从宽——低估板格强度（不安全）。

![fig2_error.png](figs/fig2_error.png)

### critical_load 与应得值对比（对数轴）：平台值系统性偏低 6 个数量级。

![fig3_critical_load.png](figs/fig3_critical_load.png)

## ⑤ 注意点与局限

- **只审计弹性屈曲**：平台自述“简化筛选，不替代船级社规范校核”，本审计不涉弹塑性/极限强度、初始缺陷、残余应力、板格相互作用。
- **固支分支只作渐近比对**：平台固支用常数 k=7.0，其源码自述为“fixed conservative simplification”。本审计仅把它与长板渐近值 6.97 比对（-0.43%），并不主张该常数在
  alpha<1.4 时成立——那需要一面板 aspect-ratio 表，本次未引入；尝试过的固支
  Rayleigh-Ritz 参考解在 alpha>4 时收敛性未确立，故**弃用**，不用于任何判定。
- **审计矩阵单点几何**：b=1 m、t=12 mm、nu=0.3；标度律已单独验证，故结论对其它 t、b、E 同样成立。
- **纯剪参考的精度**：Galerkin 复算与 Timoshenko 式在 alpha=1 差 0.12%，alpha=2 差 3.3%（8×8 基收敛限），故 A2 判定的不确定度约 3%。
- **未修平台代码**：本单只清偿“验证欠账”，缺陷修复以独立派单交 
  `ship-performance-platform`：mission **1894**（runner=native，repo=ship-performance-platform）。

## ⑥ 误差判定

| 判据 | 内容 | 阈值 | 实测 | 结果 |
|---|---|---|---|---|
| A1 | simply-supported compression: platform k vs exact von Mises/Timoshenko k, every alpha | |err| <= 3.0% for all 10 aspect ratios | -36.00%（越界 6 点） | **FAIL** |
| A2 | simply-supported pure shear: platform k_s vs Timoshenko 5.34+4/alpha^2 (Galerkin-validated reference) | |err| <= 3.0% for alpha >= 1 | +0.00% | **PASS** |
| A3 | critical_load must equal sigma_cr*b*t in kN for metre-based geometry | |P_platform/P_ref - 1| <= 3.0% | -100.00%（越界 3 点） | **FAIL** |
| A4 | reported half-wave count must be the mode the coefficient came from (k(alpha=0.667)/k(alpha=1.0) must be 4.2025/4 = 1.0506) | ratio within 1% of the exact mode-consistent ratio | -14.76%（越界 1 点） | **FAIL** |
| A5 | clamped branch is the documented constant k=7.0 vs the long-plate asymptote 6.97 (Timoshenko & Gere Table 9) | |err| <= 3.0% against the asymptote | +0.43% | **PASS** |
| A6 | unsupported boundary token ('free', unknown strings) must be rejected, not silently mapped onto the clamped branch | HTTP 422, or an explicit flag in the response; silent identity with 'clamped' = violation | -（越界 2 点） | **FAIL** |

**是否 <3% 的显式回答：**

- A1（简支压缩屈曲系数）：**否**。最坏 -36.00% 出现在 alpha=0.5；6/10 个 aspect ratio 越 3% 线，最大越界同时越过 15% 不可接受线（alpha=0.5 时低估 36%）。
- A2（简支纯剪屈曲系数）：**是**。最坏 +0.00%（alpha=3），全部点在 3% 内。
- A3（critical_load 量纲）：**否**。偏差 -100.00%，相当于把 MN 当 kN 又除以 1000，六个数量级。
- A5（固支常数 7.0 vs 长板渐近 6.97）：**是**，+0.43%，但这是「渐近一致」，不构成 alpha<1.4 的验证。

**证据分级结论**：`/api/v1/fea/plate-buckling` 的弹性部分不是 MOCK——标度因子 pi^2 D/(b^2 t) 与纯剪系数都是真算的；缺陷是压缩系数 k 被硬编码为常数 4.0，而 `half_waves` 被算出却未使用。故综合评级：**REAL(标度) + MOCK(压缩系数) = WITHHELD（对外不可判）**，直到 k 换成 mode-consistent 的 (m/alpha+alpha/m)^2 极小化。

## ⑦ 修复建议（已派单：mission 1894，runner=native，repo=ship-performance-platform）

1. `k_compression = min_m (m/alpha + alpha/m)^2`，并用同一 m 回填 `half_waves`；
2. `critical_load = sigma_cr * b * t * 1e3`（b、t 以米计，返回 kN），或统一规定几何以米、并在字段名标注单位；
3. 边界 token 白名单校验，未知值返回 422，`free` 不应静默落到固支分支；
4. `/fea/stiffened-panel` 的 `sigma_cr = 4*pi^2 E/(12(1-0.3^2))*(t_eq/b)^2` 同源，同样需要 mode-consistent k，且 E 与 nu 应可从输入取。

---

**工件**：`refs_plate.py`（独立参考）、`audit_plate_buckling.py`（审计器）、`numbers.json`（原始数字）、`figs/`（三图）、`tests/test_structure_plate_buckling.py`（回归测试）。

**复现**：`python3 docs/verification_debt/structure_plate_buckling/audit_plate_buckling.py`

