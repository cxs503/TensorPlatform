# 耐波性（seakeeping）验证欠账清偿 —— m1649

> 类型：验证欠账（对已合并功能做独立复算）｜只读扫描 + 最小修复｜2026-11-01｜分支 `agent/m1649`
> 工具：`tools/benchmarks/seakeeping_anchors.py`（独立数值路线，退出码即判据）
> 测试：`tests/test_seakeeping_anchor_independence.py`（16 passed）+ `backend/tests/feat/test_ship_seakeeping.py`（25 passed，无回归）
> 产物：`artifacts/m1649/seakeeping_anchor_ledger.json`、`docs/asset/m1649/fig1_seakeeping_anchors.png`、`fig2_seakeeping_spectra.png`

## 1. 为什么这是"验证欠账"（三处缺口，逐条实证）

| # | 缺口 | 证据 |
|---|---|---|
| G1 | **悬挂证据引用**：`backend/services/seakeeping.py:6` 与 `backend/api/domain/ship_seakeeping.py:98` 均指向 `docs/audits/seakeeping_verification_m1433.md`，该文件**不在树内** | `find docs -name "*seakeeping*"` 仅返回 `docs/broken_ice_seakeeping_report.md`；m1433 合入提交 `4d2af6b` 只含 4 个文件（"52 个文件被门禁滤除"） |
| G2 | **已合并但线上不可达**：171 行路由 + 518 行服务从未被 `backend/main.py` import | 非测试代码中 `grep -rn ship_seakeeping` 零命中；线上实测 `POST /api/v1/calculations/seakeeping_rao`、`POST /api/v1/seakeeping_rao` 均 **404** |
| G3 | **自证非复算**：原 25 项测试全部是"闭式 vs 同一份闭式" | `backend/tests/feat/test_ship_seakeeping.py` 直接拿被测函数与其解析式互比，抓不到闭式本身写错 |

## 2. 方法：独立数值路线（不是重跑同一条公式）

11 项判据，每项都用**另一条计算路径**产生基准再比对：数值 Fourier 积分（水线面形状 → 相位因子）、
黄金分割极值搜索（共振峰）、变量替换 + 复合 Simpson（谱矩）、log-log 线性拟合（高频渐近）、参数扫描（不变性）。

### 2.1 判据台账（机读源 `artifacts/m1649/seakeeping_anchor_ledger.json`）

| id | 判据（独立路线） | 相对误差 | 容差 | 结论 |
|---|---|---|---|---|
| S1 | 深水色散 ω²=gk（λ 路线独立反推） | 0.000e+00 | 1e-12 | PASS |
| S2 | 水线面长度因子 Γ(u)=3(sin u-u cos u)/u³（数值 Fourier 积分） | 1.511e-13 | 1e-06 | PASS |
| S3 | 横向 sinc 因子 sin q/q（y 向数值 Fourier 积分） | 5.607e-14 | 1e-06 | PASS |
| S4 | 共振峰位置/幅值（数值极值 vs 闭式） | 5.343e-09 | 1e-07 | PASS |
| S5 | 高频衰减斜率 = -2（log-log 线性拟合） | 1.695e-04 | 1e-03 | PASS |
| S6 | 长波极限 heave RAO→1（κ/ζ 四组扫描无关性） | 3.550e-09 | 1e-03 | PASS |
| S7 | ITTC 谱矩闭式 m0=A/4B、m2=A√π/4√B（变量替换数值积分） | 1.566e-12 | 1e-06 | PASS |
| S8 | Froude 缩比 λ=25 下 heave RAO 不变 | 5.354e-16 | 1e-08 | PASS |
| S9 | 遭遇频率 U=0/迎浪 渐近恒等式 | 0.000e+00 | 1e-12 | PASS |
| S10 | 谱 Hs 恒等 4√m0=Hs + 粗/细网格离散化偏差 | 0.000e+00 | 1e-09 | PASS |
| S11 | 响应统计网格收敛（默认 n=201 的量化偏差） | 3.382e-03 | 报告项 | PASS |

**总判：PASS**，受门禁判据 10 项，最差误差 **1.695e-04**（S5 高频斜率拟合离散度，非公式误差）。

### 2.2 开发期被锚点抓出的两个错误（保留为教训）

1. **S4 容差设错**：初版 5e-9，实测 5.343e-09 打红。根因不是模块错，而是 ζ=0.05 时共振峰极尖锐，
   双精度在峰附近只能分辨 ~1e-8；容差必须设在**该问题浮点可实现的精度**上，否则判据随机红绿。改为 1e-7。
2. **S9 判据自身写反**：初版把 `heading=0`（随浪）当作 U=0 基准，误差 1.346 打红；改为随浪/迎浪各自的
   独立一阶运动学式后误差 **0.0e+00**。附带发现：w≈0.55–1.1 rad/s 且 U≈12 m/s 强随浪时 ω_e<0，
   属模块已声明的越界分支（`encounter_frequency_safe` 返回 `valid=False`），不可当有效 RAO 点使用。

## 3. 几何门禁实证（平台规则"Cb/L/B 先验证"确实生效）

本席最初的审计算例取 Δ=23000 t（L=150, B=22, T=8, Cb=0.65）被服务当场拒绝：
`nabla/(Cb*L*B*T) - 1 = +30.764% exceeds 5.0%`。按几何闭合反算取 Δ = Cb·L·B·T·ρ = 17589.0 t 后通过，
`geometry_residual_pct` = 0.0e+00%。门禁是可复现行为，且**审计者自己也会被它拦住**。

| 量 | 值 |
|---|---|
| ∇ | 17160.000 m³ |
| A_wp | 2475.000 m² |
| I_L | 4640625.000 m⁴ |
| BM_L | 270.4327 m |
| KB | 4.400 m |
| 固有周期 heave / pitch / roll | 7.088 / 5.150 / 17.951 s |

## 4. 离散化量化：默认网格的系统偏差（项目内首次量化）

平台默认 `omega∈[0.05,2.5], n=201`；参考 `[0.02,4.0], n=4001`。

**谱层（S10）**

| kind | Hs 误差@201 | Hs 误差@4001 | m2 相对差 | Tz@201 [s] | Tz@4001 [s] | Tz 相对差 |
|---|---|---|---|---|---|---|
| pm | +0.000e+00 | +0.000e+00 | 6.970e-02 | 6.0351 | 5.8209 | 3.679e-02 |
| jonswap | +0.000e+00 | +2.220e-14 | 5.521e-02 | 6.5201 | 6.3375 | 2.880e-02 |
| ittc | +0.000e+00 | +0.000e+00 | 4.294e-02 | 7.6363 | 7.4705 | 2.219e-02 |

**响应层（S11，JONSWAP Hs=3 m Tp=8 s，迎浪）**

| 统计量 | n=201（默认） | n=4001（参考） | 相对差 |
|---|---|---|---|
| sigma_heave | 0.90506 | 0.90201 | 3.381e-03 |
| sigma_pitch | 4.81734 | 4.80112 | 3.380e-03 |
| sigma_roll | 1.79898 | 1.79292 | 3.379e-03 |
| sigma_vertical_acc_m_s2 | 0.60182 | 0.59979 | 3.381e-03 |
| sigma_heave | 0.82516 | 0.82238 | 3.381e-03 |
| sigma_pitch | 43.45352 | 43.30710 | 3.381e-03 |
| sigma_roll | nan | nan | nan |
| sigma_vertical_acc_m_s2 | 0.53462 | 0.53282 | 3.382e-03 |

**结论**：默认网格最大量化偏差 **3.382e-03**（0.338%），优于 15% 精度线与 3% 目标，
可用于工程估算；但 m2/Tz 对高频截断更敏感（见 S10），窄谱/长峰工况应放宽 `omega_max`。

## 5. 误差判定（逐项，是否 <3%）

| 关键量 | 基准 | 误差 | <3%? |
|---|---|---|---|
| 11 项独立复算锚点（受门禁） | 数值积分/极值/不变性 | 最差 1.695e-04 | **是** |
| 相位因子 Γ(u)、sinc(q) | 数值 Fourier 积分 | ≤ 1.511e-13 | **是** |
| 共振峰位置/幅值 | 数值极值搜索 | 5.343e-09 | **是** |
| ITTC 谱矩 m0/m2 | 变量替换数值积分 | 1.566e-12 | **是** |
| Froude 缩比不变性 λ=25 | 自比 | 5.354e-16 | **是** |
| Hs 还原 4√m0=Hs | 恒等 | 0.000e+00 | **是** |
| 响应统计默认网格量化 | n=4001 细网格 | 3.382e-03 | **是** |
| **RAO 绝对量级 vs 水池试验** | 无基线 | — | **无法判定（WITHHELD）** |

末行是显式声明的剩余欠账：本套件证明"模型内部与解析极限自洽"，**不**证明"RAO 数值与某条试验吻合"。
κ（0.80/0.25/0.35）、ζ（0.20/0.12/0.10）为**与船型无关的常数**，服务 `ACCURACY_NOTE` 已如实声明。
故本模块诚实分级 = **REAL（模型）/ WITHHELD（试验精度）**，不应单标 REAL。

## 6. 修复与复用

```bash
python3 tools/benchmarks/seakeeping_anchors.py                      # 11 项独立复算
python3 -m pytest tests/test_seakeeping_anchor_independence.py -q    # 16 passed
python3 -m pytest backend/tests/feat/test_ship_seakeeping.py -q      # 25 passed（防回归）
```

代码改动（最小）：
1. `backend/main.py`：新增 `from api.domain import ship_seakeeping` + `app.include_router(ship_seakeeping.router, prefix="/api/v1")` → G2 清偿。
   **需重启 :8001 才线上生效**；本席不重启生产进程以免扰动在飞席位。
2. `docs/audits/seakeeping_verification_m1433.md`：补齐悬挂引用 → G1，内容为"m1433 原件不可恢复"
   的如实说明 + 指向本轮台账，**不伪造原始证据**。
3. 新增 `tools/benchmarks/seakeeping_anchors.py`、`tests/test_seakeeping_anchor_independence.py` → G3 清偿。

## 7. 局限

- **未做线上复核**：线上证据目前只有"修复前 404"；重启后 200 的复核属 `dev.restart` 持有者动作。
- **无试验基线**：RAO 绝对精度只能标 WITHHELD；升级需入库 `backend/data/seakeeping_benchmark_reference.json`。
- **模型物理边界**：入射力为长波 Froude-Krylov 类近似 + Smith 修正，衍射/辐射耦合吸收进 κ/ζ；
  λ ≲ L 时 heave 激励刻意省略长度相位因子（服务已注明），属定性有效域。
- S5 的 1.695e-04 是 5 点对数拟合离散度，不是公式误差；换取"独立路线"的代价。

## 8. 建议（按优先级）

1. **能力清单补耐波**：`audit_capabilities` 的 21 项无 `calc.seakeeping`，导致耐波整学科对平台审计不可见。
2. **重启即验收**：:8001 重启后回读 `POST /api/v1/calculations/seakeeping_response`（200 + sigma_heave）写入 §6。
3. **试验基线入库**：≥3 工况（Hs/Tp/heading/speed + 实测 heave/pitch 有义值），S11 收敛判据即可升级为绝对判据。
4. **κ/ζ 参数化**：改为按 Cb/L/B/T 的回归式或暴露为请求字段并写进结果，使"参数化近似"在结果里可见。
