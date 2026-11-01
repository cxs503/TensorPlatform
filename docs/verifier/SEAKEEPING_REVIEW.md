# 耐波域验证欠账清偿 v1：D5 证据分级判决与独立参考核

- 任务：m1534（自找活，data 席）｜仓：TensorPlatform｜分支：agent/m1534
- 判决对象：平台 `:8001` 的耐波（seakeeping）相关端点
- 参考核：`tools/verifier/seakeeping_reference.py`
- 审计器：`tools/verifier/seakeeping_audit.py`（每次探测原始响应全量落盘）
- 数字表：`docs/verifier/SEAKEEPING_TABLES.md`（由 `tools/verifier/seakeeping_figs.py` 程序化注入，零手抄）
- 证据：`runs/m1534_sk/seakeeping_audit.json`、`runs/m1534_sk/figs/fig{1,2,3}_*.png`
- 回归：`backend/tests/feat/test_seakeeping_reference.py`（18 passed）

## 1. 案例介绍（物理背景与算例）

耐波性给出船在波浪中的六自由度运动与加速度，是失速/上浪/砰击/晕船/作业窗口（operability）
与结构疲劳载荷谱的上游输入。平台对外宣称的耐波能力分布在四个端点：

| 端点 | 入参 | 关键输出 |
|---|---|---|
| `/calculations/seakeeping` | L,B,T,Δ,V,Cb,H,T_z,heading | roll/pitch/heave RAO 与幅值、横向/垂向加速度、附加质量、横摇固有周期 |
| `/dynamic-stability/roll-motion` | 上述 + **GM**, KB, roll_damping, 波幅/周期/艏向 | 横摇运动响应 |
| `/model-test/generate/seakeeping` | 船体主尺度 + 模型缩尺 | 模型试验工况表（波高/周期的遭遇频率、RAO、相位） |
| `/ai-models/predict/seakeeping` | **仅船体主尺度** | roll/pitch/heave 幅值、operability、晕船指数 |

母型船（贯穿全部判据）：L=180 m，B=28 m，T=10 m，Δ=41000 t，Cb=0.7，V=10 kn，H=4 m。
波浪为深水规则波，色散关系 ω²=gk、λ=gT²/2π。

## 2. 方法与判据设计

原则：**判据必须能判真也能判假**。每条判据都配"正向对照"（正向对照不过则该判据作废），
避免退化成恒真的否决器（这是报告 1083「证据闸门 v2」记录的教训）。

判据分三层严格性：

- **L1 精确解析**（无自由参数，可作 hard criterion）
  深水色散；遭遇频率 ω_e=|ω−kU cosβ|；垂荡 RAO 两端极限（λ/L→0 ⇒ 0，λ/L→∞ ⇒ 1）；
  横摇 RAO 两端极限（ω→0 ⇒ 1，ω→∞ ⇒ 0）与共振放大 1/(2ζ)；a_z=ω_e²·η_3a。
- **L2 教科书 SDOF 线性响应核**：|H|=ω_n²/√((ω_n²−ω²)²+(2ζω_nω)²)，垂荡/纵摇再乘
  Froude–Krylov 吃水衰减 e^{−kT}（自动满足 L1 两端极限）。
- **L3 几何工程估计**：C_wp=(1+2Cb)/3，ω_n3、ω_n5，横摇 T₄=2π k_xx/√(gGM)（k_xx=0.35B）。
  L3 只用于判"量级/结构是否可辨识"，绝对精度记 **WITHHELD**（未对船模试验标定）。

诚实边界：本参考核**不是**切片理论求解器。它能判死"违反精确极限 / 无频率依赖 / 无艏向耦合 /
量纲与结构不通"这类缺陷；它不能用来判定真实 RAO 细节是否 <3%。

## 3. 判决结果（数字见 SEAKEEPING_TABLES.md）

| 判据 | 内容 | 判决 | 正向对照 |
|---|---|---|---|
| K1 | 遭遇频率与色散（model-test 端点） | MOCK | 色散 PASS（相对偏差 <1e-3） |
| K2 | 垂荡 RAO 两端精确极限 | MOCK | 参考核满足两端极限 PASS |
| K3 | 横摇频响与共振 | MOCK | 1/(2ζ)=5.0 精确 PASS |
| K4 | 纵摇顶浪频响 | MOCK | 参考核峰位 λ/L∈[0.5,2] PASS |
| K5 | 艏向-遭遇频率耦合 | MOCK | \|sin β\| 形状吻合 PASS |
| K6 | 波高线性（正向对照本身） | REAL | 幅值比恒 0.5 PASS |
| K7 | 横摇固有周期结构可辨识性 | MOCK | 参考核 T₄ 随 Cb 变化 PASS |
| K8 | `/dynamic-stability/roll-motion` 可用性 | STUB | — |
| K9 | AI 耐波端点输入完备性 | MOCK | — |
| K10 | `method` 字段声明真实性 | MOCK | — |

域判决：**MOCK**（8 项 MOCK / 1 项 STUB / 1 项 REAL，正向对照 10/10 通过）。

关键事实（全部可由 `seakeeping_audit.json` 复算）：

1. **三个 RAO 对波浪周期完全没有依赖**：T=2→25 s（λ/L=0.03→5.42）内垂荡恒 1.26、
   顶浪纵摇恒 3.5 deg/m、横浪横摇恒 5.0，跨度 0。
2. 平台自报 `roll_natural_period=15.92 s` 却**在 T₄ 附近没有任何峰值结构**；
   且常值 5.0 恰等于 ζ=0.1 的共振放大 1/(2ζ)——即"把共振响应当成全频响应"。
3. **垂荡 RAO 同时违反两个精确极限**：短波端应为 0 而给 1.26；长波端应为 1 而给 1.26（+26%）。
4. **无遭遇频率耦合**：艏向 0°→270° 垂向加速度完全不变（跨度 0），而 ω_e 在 0.44→0.95 rad/s
   之间变化，按 a_z=ω_e²η_3a 应相差约 4.65 倍。横摇对艏向的形状**是对的**（精确等于
   2.0+3.0·|sin β|），说明作者知道横浪最危险，但没有把幅值交给频响函数。
5. **横摇固有周期没有物理通道**：端点不接受 GM/KG；同 L/B/T 下排水量 20.5k/41k/49.2k t
   T₄ 恒为 15.92 s。反推隐含 GM=1.5255 m=0.0545·B（无出处的常数），而参考核在同样几何下
   T₄ 跨度 4.21 s——即用户无法表达"这条船的 GM 是多少"。
6. **跨端点艏向符号约定相反**：`/model-test/generate/seakeeping` 用 ω+kU·cos(heading)
   （heading=0 视为顶浪），而 `/calculations/seakeeping` 以 heading=180 为顶浪（横摇 90° 最大）。
   两个端点拼起来用会把顶浪算成顺浪。同一 wave_length 下船速 0→2.12 m/s，RAO 恒为 1.303/0.391。
7. **`/dynamic-stability/roll-motion` 恒定 HTTP 500**：这是唯一显式接受 GM 与 roll_damping、
   结构上能承载 T₄=2πk_xx/√(gGM) 的通道，当前不可用（STUB）。
8. **`/ai-models/predict/seakeeping` 无波高/周期/艏向入参**，却返回运动幅值与晕船指数：
   线性理论下运动幅值 ∝ 波幅，该端点在结构上不可能随海况变化。
9. `method` 字段声明"切片理论 + 经验公式"：切片理论必然输出随遭遇频率变化的 RAO 与相位，
   常数 RAO 不可能是其输出——声明与实际不符（K10）。

图：

- `fig1_rao_vs_period.png`：垂荡/横摇/纵摇 RAO vs 波周期，平台（点线，常数）对比独立参考核（曲线），
  横轴顶端为 λ/L 副坐标轴。
- `fig2_heading_polar.png`：T=9 s 下横摇 RAO 的艏向极图，与 |sin β| 形状对照——形状对、幅值恒定。
- `fig3_heave_limits.png`：垂荡 RAO 与两条精确极限（短波→0、长波→1）的对照。

## 4. 误差判定（关键量 vs 基准，逐项）

| 量 | 基准 | 平台 | 相对偏差 | <3%？ |
|---|---|---|---|---|
| 色散关系 λ vs gT²/2π | 深水精确 | model-test 端点 | <1e-3 | **是** |
| 垂荡 RAO @λ/L=5.42（长波） | 1.0（精确极限） | 1.26 | +26% | 否 |
| 垂荡 RAO @λ/L=0.035（短波） | →0（精确极限） | 1.26 | 量级错误（>10⁵×） | 否 |
| 横摇 RAO @T=2 s（横浪） | 0.016（ζ=0.1，T₄=15.92 s） | 5.0 | +31100% | 否 |
| 横摇 RAO @T=16 s（共振附近） | 5.02 | 5.0 | −0.4%（巧合） | 偶然"是"，但峰值结构缺失 |
| 纵摇 RAO 顶浪峰位 | 存在 λ/L≈1 的峰 | 无峰（跨度 0） | 结构性错误 | 否 |
| 垂向加速度 vs 艏向 | 应按 ω_e² 变化 4.65 倍 | 完全不变 | 结构性错误 | 否 |
| 横摇固有周期 vs 用户 GM | T₄∝GM^(−1/2) | 无 GM 通道 | 不可调 | 否 |
| 波高线性（K6） | 幅值 ∝ H | 比值 0.5 | 0% | **是** |

结论：**除色散关系与波高线性外，耐波端点关键量均不满足 <3%**；其中 5 项为结构性错误
（无频率依赖/无艏向耦合/无 GM 通道/无波浪入参），不能靠调系数修复。
绝对 RAO 曲线层面，本报告**不做** <3% 判定（参考核为 SDOF 工程核，绝对精度记 WITHHELD）。

## 5. 注意点与局限

- **平台源码不在本工作树**（`:8001` 服务运行于 `/srv/tensor/ship-workspace/`，
  工作树写权限规则禁止跨仓修改），因此本单交付**判决 + 可复算判据 + 参考核 + 回归测试**，
  不含对 `:8001` 实现的补丁。修复方向明确（见下节），但落地需在 ship 平台仓开单。
- 参考核的 L3 固有频率依赖 Bhattacharyya/Journée 量级系数（a33=0.5、a55=1.0、k_xx=0.35B、
  KG=0.70D），仅用于判结构；`WITHHELD` 项不得当基准引用。
- K3 的"共振处 −0.4%"是巧合（常值 5.0 恰好等于 ζ=0.1 的放大倍数），不构成"该点准确"的证据；
  判定依据是短波端 311 倍与长波端 4 倍偏差及峰值结构缺失。
- 判据不依赖平台自报的 T₄ 正确与否：K2（两端精确极限）与 K5（ω_e 平方耦合）只用参考核的
  精确恒等式，与 L3 参数无关。

## 6. 修复契约（供 ship 平台开单时验收）

新实现必须同时满足（可直接用 `tools/verifier/seakeeping_audit.py` 回归）：

1. 端点接受 `wave_period` 扫描时 RAO 必须具备频率结构：垂荡/横摇/纵摇 RAO 跨度 > 0 且
   峰位分别靠近 ω_n3、ω_n4（=2π/T₄）、λ/L≈1 附近。
2. 垂荡 RAO 满足两端极限：T=1.5 s 时 <1e-3，T=40 s 时 |RAO−1|<2%。
3. 垂荡/纵摇/横摇 RAO 必须通过遭遇频率 ω_e 依赖船速与艏向：顶浪与顺浪的垂向加速度比应
   与 (ω_e,head/ω_e,follow)² 一致（本例约 4.65）。
4. 横摇固有周期必须由用户 GM/KG 驱动：`GM` 加倍 ⇒ T₄ 除以 √2（精确）。
5. 跨端点统一艏向约定（建议：heading=180° 为顶浪，与 `/calculations/seakeeping` 现状一致）。
6. `/ai-models/predict/seakeeping` 必须增加 wave_height/wave_period/heading 入参，否则改为
   返回"给定海况下的 RAO"而非幅值。
7. `/dynamic-stability/roll-motion` 修复 500，并用 |H₄|=1/√((1−ν²)²+(2ζν)²) 做契约测试。

## 7. 复跑方式

```bash
python tools/verifier/seakeeping_audit.py            # 判决 → runs/m1534_sk/seakeeping_audit.json
python tools/verifier/seakeeping_figs.py             # 图 + 数字表
pytest backend/tests/feat/test_seakeeping_reference.py -q   # 参考核 18 例回归
```