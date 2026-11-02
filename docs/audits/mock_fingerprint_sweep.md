# MOCK 指纹扫描：把「证据分级」从人眼观察变成机器判据（m1760）

> EDIT_WHITELIST: tools/audit/, tests/, docs/audits/, artifacts/mock_sweep/

> 工件：`artifacts/mock_sweep/raw.json`（探针原始回执）、`scoreboard.json`（判决）、`fig_mock_fingerprint.png`
> 复算：`python3 tools/audit/mock_fingerprint.py --live`（打服务）/ `--offline`（回放）

## ① 案例介绍（审计对象与参数表）

审计对象不是一条船，而是平台的**证据分级链**：能力注册表自述 21 项能力等级，而等级此前只能靠人工读报告复核（m1523 靠人眼发现回转要素落在 0.5L 栅格、m1547 靠人眼发现垂荡 RAO 恒为 1.260）。本单把这件事做成**可复算的数值判据**。

| 参数 | 值 |
|---|---|
| 探针数（端点×扫参组合） | 14 |
| 覆盖端点 | 9 个 |
| 判据矩阵关键量 | 25 个 |
| 基准船（阻力/操纵/耐波） | KCS 尺度：L=230 m, B=32.2 m, T=10.8 m, ∇=52030 m³, Cb=0.6505 |
| 基准船（耐波细长船） | L=180 m, B=28 m, T=10 m, Δ=41000 t, Cb=0.70 |
| API | http://127.0.0.1:8001/api/v1（只读 POST 探针） |

## ② 怎么做的（判据 + 流程）

对每个端点固定其余输入、只扫一个**物理主动参数**，取回关键输出，用六条纯数值判据裁决：

| 判据 | 定义 | 物理含义 |
|---|---|---|
| INVARIANT | 跨扫参相对展布 ≤ 1e-9 | 对该物理输入零响应 |
| RIGID_RATIO | 两量比值跨扫参恒定到 1e-9 | 两条独立回归被写成同一式的倍数 |
| FLAT | 一次响应内向量输出的内部展布 ≤ 1e-9 | 该通道是常量填充 |
| PLATEAU | 尾部 ≥2 点严格相等而前段不等 | 输出被截断/夹紧 |
| COARSE_LATTICE | 全部值落在粗步长整数倍且 h/scale ≥ 1% | 查表/取整指纹 |
| DIRECTION_VIOLATION | 声明的单调方向与实际相反 | 物理方向错误 |

端点判决：任一 MOCK 级判据命中 ⇒ MOCK；仅黄级 ⇒ PARTIAL；全部有响应 ⇒ RESPONSIVE；非 200 ⇒ UNAVAILABLE（单独记账，不混同 MOCK）。

## ③ 计算结果（判决台账，数字由 scoreboard.json 注入）

| 端点判决 | 数量 | 探针 |
|---|---|---|
| MOCK | 8 | maneuvering-rudder, maneuvering-speed, seakeeping-period, seakeeping-rao-spectrum, strip-heading, strip-frequency, propeller-wageningen-j, ext-maneuvering-speed |
| PARTIAL | 0 | — |
| RESPONSIVE | 5 | resistance-speed, resistance-cb, stability-kg, stability-beam, seakeeping-heading |
| UNAVAILABLE | 1 | mmg-rudder |

### 逐探针判据明细

| 探针 | 端点 | 扫参 | 判决 | 关键量判决 |
|---|---|---|---|---|
| resistance-speed | `calculations/resistance` | speed | **RESPONSIVE** | `rt`=RESPONSIVE(展布 9.47e-01)；`reynolds_number`=RESPONSIVE(展布 7.50e-01)；`pe`=RESPONSIVE(展布 9.87e-01)；`rf/rt`=RESPONSIVE(展布 2.67e-01) |
| resistance-cb | `calculations/resistance` | cb | **RESPONSIVE** | `cr`=RESPONSIVE(展布 2.44e-02)；`ct`=RESPONSIVE(展布 1.58e-02) |
| stability-kg | `calculations/stability` | kg | **RESPONSIVE** | `gm`=RESPONSIVE(展布 1.55e+00)；`gz_max`=RESPONSIVE(展布 1.00e+00) |
| stability-beam | `calculations/stability` | beam | **RESPONSIVE** | `bm`=RESPONSIVE(展布 7.84e-01)；`gm`=RESPONSIVE(展布 7.03e-01) |
| maneuvering-rudder | `maneuvering/turning` | rudder_angle | **MOCK** | `tactical_diameter`=INVARIANT(展布 0.00e+00)；`advance`=INVARIANT(展布 0.00e+00)；`transfer`=INVARIANT(展布 0.00e+00) |
| maneuvering-speed | `maneuvering/turning` | speed | **MOCK** | `tactical_diameter`=INVARIANT(展布 0.00e+00)；`advance`=INVARIANT(展布 0.00e+00) |
| mmg-rudder | `maneuvering-mmg/turning` | rudder_angle | **UNAVAILABLE** | HTTP 500 |
| seakeeping-period | `calculations/seakeeping` | wave_period | **MOCK** | `heave_rao_max`=INVARIANT(展布 0.00e+00)；`pitch_rao_max`=INVARIANT(展布 0.00e+00)；`roll_rao_max`=INVARIANT(展布 0.00e+00) |
| seakeeping-heading | `calculations/seakeeping` | heading | **RESPONSIVE** | `roll_rao_max`=RESPONSIVE(展布 6.00e-01)；`pitch_rao_max`=RESPONSIVE(展布 5.71e-01) |
| seakeeping-rao-spectrum | `seakeeping/rao` | wave_frequency | **MOCK** | `heave_rao`=INVARIANT(跨扫参 0.00e+00)；`pitch_rao`=INVARIANT(跨扫参 0.00e+00) |
| strip-heading | `seakeeping-advanced/strip-theory` | heading | **MOCK** | `natural_period_roll`=INVARIANT(展布 0.00e+00)；`heave_rao`=INVARIANT(跨扫参 0.00e+00)；`pitch_rao`=INVARIANT(跨扫参 0.00e+00)；`roll_rao`=INVARIANT(跨扫参 0.00e+00) |
| strip-frequency | `seakeeping-advanced/strip-theory` | wave_frequency | **MOCK** | `added_mass_heave`=INVARIANT(展布 0.00e+00)；`damping_heave`=RESPONSIVE(展布 7.96e-01) |
| propeller-wageningen-j | `propeller-design/wageningen-b` | J | **MOCK** | `KT`=COARSE_LATTICE(展布 5.71e-01)；`KQ`=COARSE_LATTICE(展布 5.71e-01)；`KQ/KT`=RIGID_RATIO(展布 1.39e-16)；`eta0(tail)`=PLATEAU |
| ext-maneuvering-speed | `extended-calculations/maneuvering` | speed | **MOCK** | `tactical_diameter`=INVARIANT(展布 0.00e+00)；`advance`=INVARIANT(展布 0.00e+00) |

图：`fig_mock_fingerprint.png`（左：判据矩阵；右：假/假/真三条代表性响应）

## ④ 关键发现（每条都能由 raw.json 独立复算）

1. **Wageningen B 敞水端点是线性桩**：扫 J=0.1…0.9 得 K_T = 0.294, 0.252, 0.210, 0.168, 0.126（严格直线，斜率恒为 −0.42/单位 J），且 K_Q 与 K_T 的比值跨全部 J 精确恒定（机器判据 RIGID_RATIO，展布 0.0）。B 系列 K_T、K_Q 是各自独立的多项式回归，恒比在物理上不可能出现 ⇒ 该端点并非「Wageningen B系列回归公式」。同时 η₀ 在 J≥0.7 后停在 0.85（夹紧）。
2. **切片理论端点忽略航向**：heading=0/45/90/135/180 返回的 heave/pitch/roll RAO 曲线逐点相同（跨扫参展布 0.0），且 pitch_rao 在响应内部恒为 0.02、roll_rao 恒为 0.5 （FLAT）。横浪横摇必然远大于顶浪 ⇒ 该端点的运动响应不是由航向驱动。
3. **/seakeeping/rao 忽略请求频率**：输入含单一 wave_frequency，返回整条谱，但改频率后整条谱不变（INVARIANT）⇒ 频率输入未被使用。
4. **回转域仍是常量表**：/maneuvering/turning 与 /extended-calculations/maneuvering 的 tactical_diameter、advance、transfer 对舵角(5→35°)与航速(5→9 m/s) 全不变，复现 m1523 的人工判定（advance/L=3.50 等 0.5L 栅格）。
5. **/maneuvering-mmg/turning 仍 500**（m1523 的 MAN-9 未闭合）。

## ⑤ 注意点 / 局限

- **判据只证「有响应」，不证精度**：RESPONSIVE 不等于 REAL（可能是错的物理，只是随输入变）。精度仍需外部基准（本单不回答）。
- **判据的灵敏度依赖「必须响应量」的选取**：初次探针曾把 `KB` 列入梁宽扫描的必须响应量，得 INVARIANT 判 MOCK；复核后确认 KB 在水线面形状不变时确实不随 B 变（wall-sided 下 KB≈T/2），属**探测器假阳性**，已从判据中移除并把 GB 方向期望改为 BM/GM。这条写在这里是因为「判据即物理判断」——探测器只能执行，物理判断仍须人来背书。
- 422 不是缺陷：`/calculations/resistance` 对 Cb 扫描返回 422 并给出「几何不自洽：∇ 与 Cb·L·B·T 相差 ≥5%（D3 门禁）」——这是平台**正面**能力，故探针改用 ∇=Cb·L·B·T 同步生成自洽载荷，而不是绕过门禁。
- 探针为单参数扫描：多参数同时变化时的失真（例如只依赖某个组合量）不在本判据覆盖内。

## ⑥ 误差判定

本单产出**判决**而非物理量，故误差口径是「判据 vs 人工复核」与「回放确定性」：

| 关键量 | 基准 | 偏差 | 是否 <3% |
|---|---|---|---|
| 离线回放判决一致性 | 入库 scoreboard（14 探针 × 全部关键量） | 0 处不一致 | **是**（0/14） |
| 历史人工发现可复现性 | m1523（回转常量表）、m1547（常数 RAO）、m1760（Wageningen 恒比） | 全部命中 | **是**（3/3） |
| 正向对照误判率 | 5 个真响应端点（阻力×2、稳性×2、耐波航向） | 0 误判为 MOCK | **是**（0/5） |
| 端点级 MOCK 命中数 | — | — | 无外部基准，本单不声称物理精度 |

## ⑦ 复现

```bash
python3 tools/audit/mock_fingerprint.py --live      # 刷新 raw.json/scoreboard.json
python3 tools/audit/gen_mock_fingerprint_report.py  # 重出图与本文
python3 -m pytest tests/test_mock_fingerprint.py -q
```
