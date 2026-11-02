# Michell/Wigley 基准尺修复 + 平台对精确解析基准复核（m1813）

> 生成时间（UTC）：2026-11-02T00:21:48+00:00 ｜ 探针 API：`http://127.0.0.1:8001/api/v1`
> 工件：快照 `docs/verification_debt/wigley_michell/artifacts/reaudit_2026-11-02.json`；图 `docs/verification/figs/michell_wigley_yardstick_2026-11-02.png`
> 复算：`python3 -m src.ship_verification.wigley_reaudit --replay`（离线，不碰网络）
> 判决：PASS 4 / FAIL 12 / WITHHELD 4 / ERROR 0

## ① 案例介绍

薄船理论（Michell 1898）的 Wigley 抛物线船型是船舶阻力领域唯一的**闭式可积**
基准：半宽 $y=\frac{B}{2}\left(1-(2x/L)^2\right)\left(1-(z/T)^2\right)$，排水体积 $=4LBT/9$、$C_b=4/9$ 均为精确闭式，因此兴波阻力可以脱离任何经验回归独立复算。

| 参数 | 值 | 精确参考 |
|---|---|---|
| L / B / T | 100.0 / 10.0 / 6.25 m（L/B=10, B/T=1.6） | — |
| Cb | 由 $\nabla/(LBT)$ 反算 | **4/9 = 0.444444** |
| 排水体积 | — | **2777.7778 m³** |
| 湿表面积 | — | **1487.906 m²**（谱收敛求积）|
| 浮箱稳性算例 | L=100.0, B=20.0, T=8.0, Cb=Cm=1 | KM = T/2 + B²/(12T) = 8.1667 m |

## ② 怎么做的

1. **先修基准尺，再判平台。** 仓内唯一解析兴波基准（m1568 的 `docs/verification_debt/wigley_michell`）的第二条独立路线 `rw_double_quad` 从未被测试门
   覆盖，实测为坏：复振幅被 `float()` 强转丢掉虚部（`ComplexWarning`），而 $x$ 向积分核关于 $x$ 反对称，
   实部完全抵消 ⇒ 返回 $R_w\sim10^{-28}\,\mathrm{N}$；另有一处多余的镜像因子 2（$\Rightarrow 4\times$）
   与一行算完即弃的 `phase = ...; del phase` 占位代码。**故 m1568 台账所依赖的“双路线互检”是空的。**
2. **修复 + 双路线互检**：路线 A（内层闭式积分 + θ 人工求积）与路线 B（二维面求积、复振幅累加、
   单侧半宽幅值与 prefactor 口径统一）现给出一致结果（下表）。
3. **加两条尺子无法自证的精确结构律**（与 prefactor 常数无关，故不受归一化争议影响）：
   $R_w\propto B^2$（固定 $L,T,Fn$）与 Froude 缩放 $R_w(\lambda L,\lambda B,\lambda T)=\lambda^3 R_w$。
4. **一次探针 → 冻结快照 → 全部判决由快照程序化重算**（`--replay`），台账里没有手抄数字；
   判据只用精确闭式或精确恒等式，不使用任何经验回归当参考。

### 基准尺自检（本次修复后）

| Fn | 路线 A $R_w$ [N] | 路线 B $R_w$ [N] | 相对差 |
|---|---|---|---|
| 0.15 | 1.212544e+04 | 1.212544e+04 | 3.60e-13% |
| 0.25 | 9.934256e+04 | 9.934256e+04 | 4.95e-12% |

θ 求积加密（600→1200）相对增量最大 4.57e-12%，即参考曲线本身已达求积极限。

### 归一化诚实声明
- 代码 prefactor = 8ρg²/(πU²)（单侧半宽幅值）；经典写法 4ρg²/(πU²)。**绝对量级存在 2 倍口径不确定性（WITHHELD）**，
  离线无可引用的绝对 Wigley $R_w$ 表可裁；故本单对平台兴波阻力只做**无量纲/标度型判决**
  （RES-6 Froude 不变性、RES-7 比值区间），不宣称绝对 $C_w$ 精度。

## ③ 计算结果（判决台账，全部由快照程序化注入）

### 几何/浮性（精确闭式）

| 编号 | 判据 | 参考值 | 实测值 | 相对误差 | 容差 | 判决 | 证据 |
|---|---|---|---|---|---|---|---|
| GEO-1 Cb of the parabolic hull | exact Cb = 4/9 for y = B/2 (1-(2x/L)^2)(1-(z/T)^2) | 0.4444 | 0.7274 | 63.7% | 2% | **FAIL** | REAL(参考)/FAIL |
| GEO-2 displaced volume [m^3] | exact nabla = 4 L B T / 9 on the dimensions the endpoint itself reports | 0.002778 | 0 | 100% | 2% | **FAIL** | REAL(参考)/FAIL |
| GEO-3 wetted surface [m^2] | spectrally converged quadrature of the exact Wigley surface | 0.1488 | 0.2 | 34.4% | 10% | **FAIL** | REAL(参考)/FAIL |
| GEO-4 dimensional fidelity (L returned / L asked) | requested model L must survive the hydrostatics call | 100 | 1 | 99% | 1% | **FAIL** | REAL(参考)/FAIL |

- `GEO-2 displaced volume [m^3]`：endpoint meta dims = {'L': 1.0, 'B': 0.1, 'T': 0.0625, 'Cb': 0.444}
- `GEO-4 dimensional fidelity (L returned / L asked)`：unit-hull rescaling is itself a defect if dims are silently changed

### 阻力原型（精确恒等式 + 标度律）

| 编号 | 判据 | 参考值 | 实测值 | 相对误差 | 容差 | 判决 | 证据 |
|---|---|---|---|---|---|---|---|
| RES-1 Reynolds number identity | Re = U L / nu (exact) | 0 | — | 0.00864% | 0.5% | **PASS** | REAL |
| RES-2 Froude number identity | Fn = U / sqrt(g L) (exact) | 0 | — | 0.0504% | 0.5% | **PASS** | REAL |
| RES-3 closure Rf + Rw = Rt | exact identity | 0 | — | 12.4% | 1% | **FAIL** | REAL(参考)/FAIL |
| RES-4 implied wetted area is speed independent | ITTC-57 Cf(Re) is exact, so Rf/(0.5 rho Cf U^2) = S = const | 0 | — | 0.0656% | 2% | **PASS** | REAL |
| RES-5 implied wetted area vs exact Wigley S | exact spectral quadrature of the Wigley surface (15% platform standard) | 1488 | 1285 | 13.6% | 15% | **PASS** | REAL |
| RES-6 Froude invariance of Cw (full vs half scale) | Cw = Rw/(0.5 rho S U^2) must depend on Fn only (geometric similarity) | 0.003529 | 0.003793 | 7.5% | 5% | **FAIL** | REAL(参考)/FAIL |
| RES-7 Michell Cw(Fn) absolute level | Michell thin-ship integral (routes A/B agreed) | — | — | — | — | **WITHHELD** | WITHHELD |

- `RES-1 Reynolds number identity`：max over speed sweep
- `RES-2 Froude number identity`：max over speed sweep
- `RES-3 closure Rf + Rw = Rt`：max over speed sweep
- `RES-4 implied wetted area is speed independent`：max |S - mean| / mean, mean S = 1285.3 m^2
- `RES-6 Froude invariance of Cw (full vs half scale)`：half-scale Fn = 0.2787 matched to full-scale Fn = 0.2628
- `RES-7 Michell Cw(Fn) absolute level`：absolute prefactor convention unresolved -> WITHHELD; ratio Rw_platform/Rw_Michell = 1.42..8.13

### 浮箱稳性（精确解析）

| 编号 | 判据 | 参考值 | 实测值 | 相对误差 | 容差 | 判决 | 证据 |
|---|---|---|---|---|---|---|---|
| STB-KM KG=3.0 | exact pontoon KM = T/2 + B^2/(12T) | 8.167 | 12.17 | 49% | 2% | **FAIL** | REAL(参考)/FAIL |
| STB-GM KG=3.0 | exact pontoon GM = KM - KG | 5.167 | 9.167 | 77.4% | 5% | **FAIL** | REAL(参考)/FAIL |
| STB-GZmax KG=3.0 | wall-sided GZ valid only below the deck-edge dip | — | 9.723 | — | — | **WITHHELD** | WITHHELD |
| STB-KM KG=6.0 | exact pontoon KM = T/2 + B^2/(12T) | 8.167 | 12.17 | 49% | 2% | **FAIL** | REAL(参考)/FAIL |
| STB-GM KG=6.0 | exact pontoon GM = KM - KG | 2.167 | 6.167 | 185% | 5% | **FAIL** | REAL(参考)/FAIL |
| STB-GZmax KG=6.0 | wall-sided GZ valid only below the deck-edge dip | — | 6.541 | — | — | **WITHHELD** | WITHHELD |
| STB-KM KG=9.0 | exact pontoon KM = T/2 + B^2/(12T) | 8.167 | 12.17 | 49% | 2% | **FAIL** | REAL(参考)/FAIL |
| STB-GM KG=9.0 | exact pontoon GM = KM - KG | -0.8333 | 3.167 | 480% | 5% | **FAIL** | REAL(参考)/FAIL |
| STB-GZmax KG=9.0 | wall-sided GZ valid only below the deck-edge dip | — | 3.359 | — | — | **WITHHELD** | WITHHELD |

- `STB-KM KG=3.0`：KM rebuilt as GM + KG
- `STB-GZmax KG=3.0`：platform reports GZ_max at 45.0 deg > validity limit 38.66 deg -> not comparable
- `STB-KM KG=6.0`：KM rebuilt as GM + KG
- `STB-GZmax KG=6.0`：platform reports GZ_max at 45.0 deg > validity limit 38.66 deg -> not comparable
- `STB-KM KG=9.0`：KM rebuilt as GM + KG
- `STB-GZmax KG=9.0`：platform reports GZ_max at 45.0 deg > validity limit 38.66 deg -> not comparable

### 平台兴波阻力 vs 参考（比值，非绝对）

| 航速 [kn] | Fn | $R_w$ 平台 [N] | $R_w$ 参考 [N] | 比值 |
|---|---|---|---|---|
| 4.0 | 0.0657 | 4440 | 1471 | 3.017 |
| 6.0 | 0.0985 | 1.196e+04 | 1471 | 8.128 |
| 8.0 | 0.1314 | 2.478e+04 | 4245 | 5.837 |
| 10.0 | 0.1642 | 4.422e+04 | 1.324e+04 | 3.340 |
| 12.0 | 0.1971 | 7.159e+04 | 2.556e+04 | 2.801 |
| 14.0 | 0.2299 | 1.18e+05 | 4.721e+04 | 2.500 |
| 16.0 | 0.2628 | 1.823e+05 | 9.934e+04 | 1.835 |
| 18.0 | 0.2956 | 2.664e+05 | 1.878e+05 | 1.418 |
| 20.0 | 0.3285 | 3.919e+05 | 2.619e+05 | 1.496 |

## ④ 结果图

![yardstick](figs/michell_wigley_yardstick_2026-11-02.png)

左：Michell 参考 $C_w(Fn)$ 形状（黑）与平台 $C_w$（全尺度圆点、半尺度空心方块——同一 Fn 必须重合）；
右：各精确判据的相对误差（对数轴，5%/15% 两条阈值线）。

## ⑤ 注意点与局限

1. **m1568 台账的“双路线互检”结论不可信**：第二条路线当时返回 $\sim10^{-28}$ N。已修复并被测试钉住；
   若有人引用 m1568 的“两条独立路线一致”作为证据，应改引本文件。
2. **绝对归一化未裁**（见上）：任何以本参考做绝对 $C_w$ 对比的下游，误差界为 $\pm$ 2 倍口径。
3. **Wigley 是薄船算例**：$B/L=0.1$、$T/L=0.0625$，Michell 一阶薄船理论适用；实船（$B/L\sim0.15$）误差会更大，
   且本理论不含破波/粘性/自由面非线性。
4. **浮箱 GZ 比较只在甲板边缘入水角以内**成立（本算例 38.66°）；平台在 45° 报告 GZ_max 已越界，故记 WITHHELD。
5. 平台侧只做**读**（探针），本单未改任何船舶生产代码；修复只落在基准尺（验证侧）。

## ⑥ 误差判定（是否 <3%）

| 关键量 | 基准 | 相对误差 | <3% ? |
|---|---|---|---|
| GEO-1 Cb of the parabolic hull | exact Cb = 4/9 for y = B/2 (1-(2x/L)^2)(1-(z/T)^2) | 63.7% | **否** |
| GEO-2 displaced volume [m^3] | exact nabla = 4 L B T / 9 on the dimensions the endpoint itself reports | 100% | **否** |
| GEO-3 wetted surface [m^2] | spectrally converged quadrature of the exact Wigley surface | 34.4% | **否** |
| GEO-4 dimensional fidelity (L returned / L asked) | requested model L must survive the hydrostatics call | 99% | **否** |
| RES-1 Reynolds number identity | Re = U L / nu (exact) | 0.00864% | 是 |
| RES-2 Froude number identity | Fn = U / sqrt(g L) (exact) | 0.0504% | 是 |
| RES-3 closure Rf + Rw = Rt | exact identity | 12.4% | **否** |
| RES-4 implied wetted area is speed independent | ITTC-57 Cf(Re) is exact, so Rf/(0.5 rho Cf U^2) = S = const | 0.0656% | 是 |
| RES-5 implied wetted area vs exact Wigley S | exact spectral quadrature of the Wigley surface (15% platform standard) | 13.6% | **否** |
| RES-6 Froude invariance of Cw (full vs half scale) | Cw = Rw/(0.5 rho S U^2) must depend on Fn only (geometric similarity) | 7.5% | **否** |
| STB-KM KG=3.0 | exact pontoon KM = T/2 + B^2/(12T) | 49% | **否** |
| STB-GM KG=3.0 | exact pontoon GM = KM - KG | 77.4% | **否** |
| STB-KM KG=6.0 | exact pontoon KM = T/2 + B^2/(12T) | 49% | **否** |
| STB-GM KG=6.0 | exact pontoon GM = KM - KG | 185% | **否** |
| STB-KM KG=9.0 | exact pontoon KM = T/2 + B^2/(12T) | 49% | **否** |
| STB-GM KG=9.0 | exact pontoon GM = KM - KG | 480% | **否** |

**RES-7（兴波阻力绝对量级）无可用基准**：绝对 prefactor 口径离线不可裁，故记 WITHHELD，
不宣称任何 $<3\%$ 结论；RES-6（Froude 不变性）是无量纲判据，其误差即表中数值。
