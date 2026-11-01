# 几何能力证据复核：静水力 / 几何生成 / 几何校验端点对解析恒等式与独立积分的判定（verifier 席 tp-ver-6, m1586）

> EDIT_WHITELIST: tools/verifier/geometry/, tests/, docs/verification/, artifacts/verifier/geometry/
>
> 本文全部数字由 `tools/verifier/geometry/audit_geometry.py` 从 `artifacts/verifier/geometry/api_captures/*.json`
> 程序化生成（零手抄），与 `artifacts/verifier/geometry/audit.json` 同源。

## ① 案例介绍

能力台账（报告 1148）把 `geometry.hydro_curves`、`geometry.generate`、`geometry.verify_official`、
`shiphull.generate`、`geometry.offsets`、`stdmodels.lookup` 六项列为 **UNVERIFIED**（自述 REAL、零审计证据），
而平台精度标准第一条正是「几何必须先验证 Cb/L/B」。此前这些端点只被端到端契约抽查过（报告 1061），
没有一次针对**几何自身物理量**的独立复核。本单以黑盒方式补这一层。

被复核对象为 :8001 运行时服务的 `/api/v1/geometry/*`、`/api/v1/ship-hull/*`、`/api/v1/standard-models/*`；
船型参数取自平台自家标模库（与 KRISO 公布值一致）：

| 船 | Lpp [m] | B [m] | T [m] | Cb（声明） | Cm（文献） | S_w（文献）[m²] |
|---|---|---|---|---|---|---|
| KCS | 230.0 | 32.2 | 10.8 | 0.651 | 0.985 | 9530 |
| KVLCC2 | 320.0 | 58.0 | 20.8 | 0.81 | 0.995 | 27194 |

## ② 怎么做的

1. **捕获即证据**：`capture_api.py` 把 19 个请求的原始应答（含 HTTP 状态码与耗时）落盘到 `api_captures/`
   （采集时间 2026-11-01T08:05:33），此后所有计算只读这些文件，报告可离线重放。
2. **三层独立基准**：
   - (i) **解析恒等式**（不需外部数据）：`Cb = ∇/(LBT) ≤ 1`、`dV/dd = Awp`、`Cb = Cp·Cm`、`TPC = Awp·ρ/100`、`d ≤ T`。
   - (ii) **平台自家型值表的独立积分**：`/geometry/offsets/{ship}` 返回 21 站 × 11 水线归一化半宽表，
     用自写复合 Simpson 从零积分 ∇、Cb、Cm、Cp、Awp、Cw、LCB、VCB；湿表面积用 `S=2∬√(1+b_x²+b_z²)dxdz`
     参数化积分并做 1/2/4/8 倍加密收敛研究。
   - (iii) **文献/标模库**：`standard-models/ships` 排水量与 KRISO 公布 Cm、S_w。
3. **网格体一致性**：`/geometry/generate` 返回三角网格，散度定理 `V=∮r·n dA/3` 求体积，同时统计边界边数
   （是否封闭）与包围盒（L/B/T 是否被忠实复现）。
4. **门语义检验（负对照）**：未知船名、缺 `model_id`、`draft > T` 各打一次看是否 fail-closed；
   并检查 `/geometry/verify` 是否存在**任何可失败的判决字段**——永远返回 200 的「校验器」不是校验器。
5. 计算栈 python3 + numpy + matplotlib(Agg)，无重型求解器；本机 load15≈126/128 核，全单只做轻量分析。

## ③ 计算结果

### 3.1 静水力：平台值 vs 独立基准（设计吃水）

| 量 | KCS 平台 | KCS 型值表积分 | KVLCC2 平台 | KVLCC2 型值表积分 |
|---|---|---|---|---|
| ∇ [m³] | 95574.7 | 56461.2 | 557487.5 | 301240.5 |
| Cb | 1.1949 | 0.7059 | 1.4441 | 0.7803 |
| Awp [m²] | 6061.5 | 6369.2 | 17804.1 | 17644.4 |
| LCB(FP→) [m] | 114.082 | 113.440 | 166.274 | 166.283 |
| S_w [m²] | 12065.5 | 9238.0 | 34395.1 | 25257.7 |

关键事实：

- **两条船的 Cb 都 > 1**（KCS 1.1949、KVLCC2 1.4441）：静水力端点给出的体积超过其自身 L·B·T，物理上不可能，
  也违反平台自己写的 `0<Cb≤1` 门。
- **同一响应内 LCB 与体积不同源**：LCB(FP→) 与型值表积分吻合到 0.57 %（KCS）/ 0.01 %（KVLCC2），
  说明端点确实读了型值表算 LCB，却没把同一张表用于体积。
- **湿表面积全线偏高**：vs 型值表积分 +30.6 %（KCS）/ +36.2 %（KVLCC2）；vs KRISO 文献 +26.6 % / +26.5 %。
  而型值表积分本身对文献只有 -3.1 % / -7.1 %，两条独立线索同向。

### 3.2 恒等式与吃水依赖（机器可判，无需外部基准）

| 恒等式 / 行为 | KCS | KVLCC2 | 判定 |
|---|---|---|---|
| `0 < Cb ≤ 1` | 1.1949 | 1.4441 | **违反** |
| Cb 随吃水跨度 [%] | 0.000 | 0.000 | 形状零变化 → 不是积出来的 |
| Awp 随吃水跨度 [%] | 0.000 | 0.000 | 形状零变化 → 不是积出来的 |
| LCB 随吃水跨度 [%] | 0.000 | 0.000 | 形状零变化 → 不是积出来的 |
| TPC 随吃水跨度 [%] | 0.000 | 0.000 | 形状零变化 → 不是积出来的 |
| `dV/dd = Awp` 最大偏差 [%] | 49.6 | 54.3 | **违反** |
| V ∝ d 幂指数 | 1.0000 | — | 线性单调 → 占位式 |
| `d > T` 行为 | True（返回 draft=10.8） | — | 静默钳位，不报错 |

### 3.3 型值表与生成网格

| 量 | KCS | KVLCC2 | 参考 |
|---|---|---|---|
| 型值表积分 Cb | 0.7059 | 0.7803 | 声明 0.651 / 0.81 |
| 型值表积分 Cm | 0.8208 | 0.8208 | 文献 0.985 / 0.995 |
| 生成网格 Cb（散度定理） | 0.8167 | 0.7697 | 声明 0.651 / 0.81 |
| 网格 B 跨度 [m] | 32.233 | 60.900 | 32.2 / 58.0 |
| 网格边界边数 | 102 | 102 | 0 = 封闭体 |

**型值表的指纹级证据**：两条完全不同的船，中横剖面系数给出同一个数 Cm=0.8208（KCS）== 0.8208（KVLCC2），
而文献为 0.985 / 0.995（偏差 -16.7 % / -17.5 %）。真实 CAD 提取的型线不可能让两条船的中横剖面肥满度
相同到小数点后四位——该表是**同一参数族批量生成**的，`source` 字段的「CAD 水线层提取」属不可核实声明。

### 3.4 校验器语义与负对照

| 检查 | 结果 |
|---|---|
| `/geometry/verify/KCS` | stations_checked=0, avg_error=None, max_error=None, 判决字段存在=False |
| `/geometry/verify/KVLCC2` | stations_checked=4, avg_error=23.3, max_error=141.1, 判决字段存在=False |
| 未知船 `verify/NOSUCHSHIP` | HTTP 404（fail-closed ✓） |
| 未知船 `hydro-at/NOSUCHSHIP` | HTTP 500（未处理异常 ✗） |
| `generate` 缺 model_id | HTTP 404 |

KVLCC2 的 CAD 比对结果本身也不自洽：`cad_halfbreadth` 在 x=0.3·L 处给出 89.8 m（该船 B=58 m，B/2=29 m），
而 `gen_halfbreadth` 在三个站位恒为 29.0 m（生成体是等宽柱），故 max_error=141.1 % 里混合了「CAD 抽取单位错误」
与「生成体过粗」两种缺陷，端点却既不区分也不判决。

## ④ 结果图

![fig1_draft_dependence.png](../artifacts/verifier/geometry/fig1_draft_dependence.png)

*Fig.1 (a) platform displacement is a straight ramp in draft, far from the offsets-derived displacement (star); (b) residual of the dV/dd = Awp identity for both hulls.*

![fig2_error_bars.png](../artifacts/verifier/geometry/fig2_error_bars.png)

*Fig.2 Relative error of each audited metric against its independent reference, with the platform's 3 % target and 15 % unacceptable lines.*

![fig3_offsets_profiles.png](../artifacts/verifier/geometry/fig3_offsets_profiles.png)

*Fig.3 Independent integration of the platform's exposed offset table: section-area and waterplane-area curves, i.e. the geometry the platform already has but the hydrostatic endpoints do not use.*

## ⑤ 注意点（踩坑 / 局限 / 适用范围）

- **型值表当基准的循环依赖风险**：用平台自己的型值表当几何基准等于「以被审对象为尺」。缓解靠三角互证：
  型值表积分 S_w 与 KRISO 文献只差 -3.1 % / -7.1 %，说明积分链可信；§3.2 的恒等式检验则完全不依赖该表。
- **生成网格体积不可当精确量**：网格有 102 条边界边（非封闭），散度定理只是近似，网格 Cb 仅用于定性判定
  （其离散偏差量级远超 3 %），不作精度基准。
- **型值表分辨率限制**：21×11 网格对艏艉陡变欠采样，Simpson 对 Cb 还有 1–2 % 量级不确定度；湿表面积做了
  1/2/4/8 倍加密，最后两级差 0.05 %（KCS）/ 0.05 %（KVLCC2），故 S 的比较结论在 ±1 % 内稳健。
- **`draft > T` 静默钳位**可能是有意保护，但静默返回设计值会污染上层阻力/稳性链路，应改 4xx 或显式标记。
- **未做的部分**：未审计 `geometry/hydro-curves` 的 TPC/MTC/VCB 逐项精度（缺独立基准）、未查 `list_models`
  （本构建 `/geometry/models/{category}` 返回 404，记 WITHHELD）、未对 `ship-hull/export-iges|step` 做格式合规校验。
- 本机 load15≈126/128 核，本单只做 API 探测 + numpy 积分 + 出图，未启动任何重算力作业；
  `/geometry/offsets/KCS` 首次调用曾 25 s 超时、复采立即返回，属冷启动/缓存，不计为缺陷。

## ⑥ 误差判定（关键量 vs 基准，逐项回答是否 <3%）

| 量 | 相对误差 | 基准 | <3% ? |
|---|---|---|---|
| KCS Cb platform vs offsets | +69.27 % | 型值表独立积分 Cb | **否** |
| KCS disp platform vs registry | +86.21 % | 标模库 KRISO 排水量 | **否** |
| KCS S platform vs offsets | +30.61 % | 型值表积分 S_w | **否** |
| KCS Cb offsets vs declared | +8.43 % | 平台声明 Cb | **否** |
| KCS Cm offsets vs lit | -16.67 % | KRISO 文献 Cm | **否** |
| KCS B mesh vs declared | +0.10 % | 声明 B | 是 |
| KCS Cb mesh vs declared | +25.45 % | 声明 Cb | **否** |
| KVLCC2 Cb platform vs offsets | +85.07 % | 型值表独立积分 Cb | **否** |
| KVLCC2 disp platform vs registry | +80.37 % | 标模库 KRISO 排水量 | **否** |
| KVLCC2 S platform vs offsets | +36.18 % | 型值表积分 S_w | **否** |
| KVLCC2 Cb offsets vs declared | -3.66 % | 平台声明 Cb | **否** |
| KVLCC2 Cm offsets vs lit | -17.51 % | KRISO 文献 Cm | **否** |
| KVLCC2 B mesh vs declared | +5.00 % | 声明 B | **否** |
| KVLCC2 Cb mesh vs declared | -4.97 % | 声明 Cb | **否** |

**结论（honest FAIL，不粉饰）**：

- 唯一达到 3 % 的是 `stdmodels.lookup` 的主尺度/排水量（KCS 排水量与标模库一致，KVLCC2 亦在 3 % 内）——它判定 REAL。
- 其余几何端点**没有任何一项关键量落在 3 %% 内**，且 `Cb>1`、`dV/dd≠Awp` 属恒等式级违反（不是精度问题而是
  模型不对）：`geometry.hydro_curves`=MOCK、`geometry.verify_official`=STUB、`geometry.generate`/`shiphull.generate`=MOCK、
  `geometry.offsets`=MOCK（合成型值表，provenance 声明不可核实）。
- 影响面：所有以「几何可信」为前提的下游能力（阻力 Cb、稳性 KB/BM、耐波湿表面）都建立在 Cb 偏差 +8.4 %%…+83.5 %%
  的几何上，这解释了报告 1136（稳性 KB +33 %%、GM +114 %）与 1126（阻力 MOCK）的同向偏差。

## ⑦ 复现

```
python3 tools/verifier/geometry/capture_api.py --out artifacts/verifier/geometry/api_captures
python3 tools/verifier/geometry/audit_geometry.py            # -> audit.json + fig1..3 + 本文
python3 -m pytest tests/test_m1586_geometry_gate.py -q       # 离线（只读 captures）
```
