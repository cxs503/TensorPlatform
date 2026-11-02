# 耐波「切片理论」能力判决：标签为 strip-theory 的端点是硬编码表（MOCK）

M1796 · platform 席自找活 · 2026-11-01 · 只读审计，未改动 `ship-performance-platform` 源码

## 1. 案例（为什么查这一项）

耐波（seakeeping）是船舶总体性能九大学科之一，平台对外暴露 6 个耐波端点，其中
`POST /api/v1/seakeeping-advanced/strip-theory`（摘要字段「切片理论计算」）是唯一一个
*按名自称为物理模型*的耐波端点。报告 1431 已把操纵域与 BEM/RAO 子系统冻结为机裁判据，
但耐波族整体仍记为 WITHHELD——原因是仓内参考核 `tools/verifier/seakeeping_reference.py`
自述「**不是**切片理论求解器……不能用于判定真实 RAO 曲线的细节误差 <3%」。
本单清偿的就是这一段**可清偿的部分**：不引入第三方数字化试验数据，只用
(i) 输入扰动指纹、(ii) 两条精确极限、(iii) 频率轴完整性，把「是否有物理内容」变成
可机器裁判的三态结论。

基准船（KVLCC2 级母型，与平台正典数据集同源）：
L=320 m, B=58 m, T=20.8 m, D=30 m, ∇=312622 m³, Cb=0.8098, Cm=0.998, Cwp=0.87,
KG=18 m, GM=1.5 m, Vs=7.7 m/s, 浪高对应的波幅 1.0 m, 顶浪（heading=180°）。

## 2. 怎么做（方法/工具链）

新增 `tools/verifier/seakeeping_strip_verdict.py`（纯只读探针 + 机裁判据生成器）：

1. **输入扰动指纹**：对同一母型施加 7 组物理上必须改变答案的扰动
   （ω=0.3/0.5/0.8/1.2 rad/s、Vs=14 m/s、heading=120°、L=180 m 半尺寸船），
   对每个返回字段单独取 SHA-256 摘要。**按字段**而非整流做指纹是关键：
   平台确实回传随输入变化的标量，但 RAO 序列逐字节不变——整流指纹会漏判。
2. **精确极限裁决**（极限本身由仓内参考核证明，无自由参数）：
   λ/L→0 时 |RAO₃|→0（船体滤掉短波）；λ/L→∞ 时 |RAO₃|→1（船随波面）。阈值沿用平台
   已吸收的 15% 偏差闸门（ITTC 7.5-03-01/02 的 U_V 双判据在此之前）。
3. **频率轴完整性**：没有频率轴的 RAO 序列无法与任何极限对齐，属结构性缺陷。
4. **特异性对照**：同一裁判器必须能把「随频率变化的 SDOF 合成序列」判为
   REAL_UNVERIFIED 而不是 MOCK——否则它就是橡皮图章（见 `tests/verifier/` 的对照测试）。

## 3. 计算结果

表由 `tools/verifier/seakeeping_strip_verdict.py` 程序化生成（零手抄）：
见 `docs/benchmarks/seakeeping_strip_verdict_tables.md`，原始证据
`artifacts/seakeeping_strip_verdicts.json`，冻结响应 `artifacts/seakeeping_strip_responses.json`。

| 端点 | 证据等级 | RAO 序列是否被输入锁死 |
|---|---|---|
| `/seakeeping-advanced/strip-theory` | **MOCK** | heave_rao / pitch_rao / roll_rao 三条全部逐字节不变 |
| `/seakeeping/rao` | **MOCK** | heave_rao / pitch_rao / roll_rao 三条全部逐字节不变 |

- 前者 20 点序列，`heave_rao` 摘要 `6df8a44b467ef1a8` 在 ω=0.3/0.5/0.8/1.2 下**完全相同**；
  首点 0.500、尾点 0.500（常数尾巴）、峰值 4.764、2 个局部极值、**不带频率轴**。
  `pitch_rao` 恒为 0.0200（20 点标准差 0）。
- 后者 30 点序列（频率轴 0.2–3.1 rad/s），`heave_rao` 与 `pitch_rao` 摘要**相同**
  `8fa0dedec1d5b65d`——垂荡与纵摇共用同一张表；首点 1.070、尾点 0.066、峰值 4.561。

图 `docs/benchmarks/figs/seakeeping_strip_verdict.png`：
左图为 ω=0.3/0.5/0.8 三条输入对应的 heave RAO 曲线重合（三线一影，MOCK 的直观证据）；
右图为 `/seakeeping/rao` 表对两条精确极限的对照。

## 4. 注意点（坑与局限）

- **整流指纹会漏判**：首轮实现按整个 JSON 取摘要，两条端点都显示「输入敏感」——
  因为平台确实算了随输入变化的标量（如给定 ω 处的取值）。必须按序列字段做指纹。
- **不能拿本单做精度声明**：本单判的是「有无物理内容」，不是「算得准不准」。
  真实 RAO 的细节精度仍需切片理论/BEM 参考解或数字化船模试验，仓内均无。
- 峰值 4.764 本身无法判死：阻尼比未知时尚无普适上界（1/(2ζ) 需 ζ）。
  因此峰值只作描述量，不作判据。
- 两处缺陷位于 `ship-performance-platform`（本地私有仓），本单未改其源码，
  仅产出机裁判据资产；修复单应落该仓。

## 5. 误差判定（逐项回答「是否 <3%」）

| 关键量 | 平台值 | 基准（精确极限） | 相对误差 | <3%？ |
|---|---|---|---|---|
| strip-theory 端点 首点 RAO₃ | 0.500 | 1.000（λ/L→∞ 精确极限） | 50.0 % | **否** |
| strip-theory 端点 尾点 RAO₃ | 0.500 | 0.000（λ/L→0 精确极限） | 绝对偏差 0.500 | **否** |
| `/seakeeping/rao` 首点 RAO₃ | 1.070 | 1.000 | 7.0 % | **否**（>3%） |
| `/seakeeping/rao` 尾点 RAO₃ | 0.066 | 0.000 | 绝对偏差 0.066 | 接近但非零 |
| 输入不变性（RAO 序列摘要数） | 1（跨 4 个 ω） | ≥4（每个 ω 应不同） | 0 个不同摘要 | **否**（零容忍判据） |

**结论（如实）**：没有任何关键量满足 <3%。本单不宣称平台耐波精度合格；
宣称的是证据等级从 WITHHELD 降为 MOCK，判据为「RAO 序列对物理输入逐字节不变」
这一结构性事实，与精度无关。细节精度判定仍为 WITHHELD，缺口原因已写明：
仓内无切片理论/BEM 耐波参考解，也无数字化船模试验数据。

复现：
```bash
python3 tools/verifier/seakeeping_strip_verdict.py --fig      # 现场探针 + 图
python3 tools/verifier/seakeeping_strip_verdict.py --offline  # 用冻结 fixture 复算
python3 -m pytest tests/verifier/test_seakeeping_strip_verdict.py -q   # 7 passed
```