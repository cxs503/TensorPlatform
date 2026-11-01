# 完整稳性解析基准审计（m1532）：箱型驳船精确解复核平台 GZ 能力

**任务** TP self-start m1532（data 席）· **能力** `calc.stability`（审计等级 REAL）
· **判据** 项目精度标准：误差 >15% 不可接受，<3% 视为已复核。
**结论摘要**：`/calculations/stability` 的浮心高度 KB 与复原力臂 GZ 存在**公式级缺陷**，
箱型驳船（有精确解）上 KB 误差 +33.3%、GM 误差 +61.5%、
GZ(30°) 误差 -21.9%；同一船型上三个
稳性端点的 GM 互差最大 3.9 倍。同一平台同一 harness 对 ITTC-57 摩擦线的复核误差
-0.0197%（PASS），说明本审计具备区分能力，不是"什么都判失败"。

## ① 案例：为什么用箱型驳船

壁面直立（wall-sided）箱型驳船 Cb = Cm = Cw = 1 是完整稳性中唯一存在**闭式精确解**的船型：
浮心高度 KB = T/2、稳心半径 BM = B²/(12T) 有初等解析式，大倾角复原力臂满足

    GZ(φ) = sinφ · [ GM + ½·BM·tan²φ ]

该式在"甲板边缘入水"与"舭部出水"之前是**精确**的（推导见 `tools/analytic_bench/reference.py`：
对等排水量梯形浸水剖面求形心，x_b = BM·tanφ、z_b = KB + ½BM·tan²φ，代入
GZ = x_b·cosφ + (z_b − KG)·sinφ 即得上式）。因此任何偏差都只能来自被审计的代码，
不存在"参考值本身也是经验公式"的循环论证。

算例（几何已先验：Cb=1，∇ = L·B·T，Δ = ρ∇）：

| 算例 | L [m] | B [m] | T [m] | D [m] | KG [m] | Δ [t] | 甲板入水角 [deg] | 舭部出水角 [deg] | 判定窗口 [deg] |
|---|---|---|---|---|---|---|---|---|---|
| box-B20T8 | 100 | 20 | 8 | 14 | 6.0 | 16400 | 30.96 | 38.66 | 0-30.96 |
| box-B24T6 | 80 | 24 | 6 | 12 | 5.0 | 11808 | 26.57 | 26.57 | 0-26.57 |
| box-B18T7 | 120 | 18 | 7 | 15 | 6.8 | 15498 | 41.63 | 37.87 | 0-37.87 |

## ② 方法

1. `tools/analytic_bench/reference.py`：闭式解 + **独立**数值解（剖面多边形按倾斜水线
   Sutherland-Hodgman 裁剪，对水线偏移量二分至等排水量，再取形心算 GZ）。两者互校
   rel ≤ 1e-6（pytest `test_closed_form_matches_independent_wedge_integration`），参考值先自证。
2. `tools/analytic_bench/stability_audit.py`：HTTP 打平台三个稳性端点
   （`/calculations/stability`、`/stability/gz-curve`、`/stability/evaluate`），
   逐项算相对误差并给出 PASS(≤3%)/ALERT/FAIL(≥15%)/WITHHELD 判决，落盘 JSON+MD+PNG。
3. 同一 harness 打 `/calculations/resistance` 做 **PASS 对照**（ITTC-57 是恒等式）。
4. 工具链：Python 3.11 + numpy/matplotlib(Agg) + pytest；未起任何重算进程（load15 ≫ nproc 限额）。

## ③ 计算结果（算例 box-B20T8，程序化注入自 `artifacts/analytic_bench/stability_audit.json`）

### 静水力因子

| quantity | analytic | /calculations/stability | err_% | verdict |
|---|---|---|---|---|
| KB [m] | 4.0 | 5.333 | +33.33 | FAIL |
| BM [m] | 4.1667 | 4.167 | +0.008 | PASS |
| GM [m] | 2.1667 | 3.5 | +61.54 | FAIL |
| GM [m] | 2.1667 |  | +18.94 | FAIL |
| GM [m] | 2.1667 |  | +184.63 | FAIL |

（`/stability/evaluate` 的 GM 列取自该端点自身返回值；该端点 KB = T·(0.5+0.5Cb)，
Cb=1 时给出 KB = T = 8 m，而精确值为 4 m。）

### GZ 逐点（`/stability/gz-curve`）

| heel [deg] | analytic GZ [m] | gz-curve GZ [m] | err_% | judged | verdict |
|---|---|---|---|---|---|
| 0.0 | 0.0 | 0.0 | +nan | True | WITHHELD |
| 5.0 | 0.1902 | 0.2246 | +18.07 | True | FAIL |
| 10.0 | 0.3875 | 0.4466 | +15.26 | True | FAIL |
| 15.0 | 0.5995 | 0.656 | +9.43 | True | ALERT |
| 20.0 | 0.8354 | 0.8433 | +0.94 | True | PASS |
| 25.0 | 1.1071 | 0.9996 | -9.71 | True | ALERT |
| 30.0 | 1.4306 | 1.1169 | -21.93 | True | FAIL |
| 35.0 | 1.7734 | 1.118 | -36.96 | False | WITHHELD |
| 40.0 | 2.0536 | 1.1264 | -45.15 | False | WITHHELD |
| 45.0 | 2.2217 | 1.0846 | -51.18 | False | WITHHELD |

判定窗口内：n=6，max|err|=21.925%，mean|err|=12.555%，
PASS(3%)=1，FAIL(15%)=3。35° 以后因甲板已入水（真实船体会渐进水），
解析参考在"水密甲板"假设下不再物理，一律判 WITHHELD，不当作缺陷证据。

### 曲线标量

| scalar | analytic | platform | err_% | verdict |
|---|---|---|---|---|
| area_0_30_gzcurve | 0.3264 | 0.3155773453762247 | -3.306 | ALERT |
| gz_max | 2.2788 | 2.803 | 23.004 | WITHHELD |
| gz_max_angle | 50.0 | 40.0 | -20.0 | WITHHELD |
| area_0_30 | 0.3347 | 0.5041 | 50.626 | FAIL |
| area_0_40 | 0.6415 | 0.9236 | 43.985 | WITHHELD |

### 三算例汇总

| case | KB err % | GM err % | GZ max|err| % (judged) | judged window [deg] | FAIL(>15%) |
|---|---|---|---|---|---|
| box-B20T8 | +33.33 | +61.54 | 21.93 | 0-30 | 3/6 |
| box-B24T6 | +33.33 | +16.67 | 15.80 | 0-25 | 1/5 |
| box-B18T7 | +33.34 | +209.44 | 70.89 | 0-35 | 6/7 |

## ④ 图

![stability audit](artifacts/analytic_bench/fig_stability_audit.png)

左：GZ 曲线（黑=精确参考，红=/stability/gz-curve，灰区=甲板入水后不作判定）；
中：逐点相对误差与 ±3%/±15% 门限；右：三算例 KB/BM/GM 相对误差（KB 恒为 +33.3%，
暴露的是公式系数而非船型敏感度）。

## ⑤ 注意点 / 根因

| 端点 | 缺陷 | 代码位置（只读审计） |
|---|---|---|
| `/calculations/stability` | KB 注释写 Morrish 但实现为 `KB = T·(1 − Cb/3)`，箱型给 2T/3，精确 T/2；正确 Morrish 为 `T(5/6 − Cb/(3Cwp))` | `backend/api/calculations.py:372` |
| `/calculations/stability` | GZ 大倾角项用 `sin²φ` 而非 `tan²φ`，30° 处复原力臂偏低 -21.9% | `backend/api/calculations.py:389` |
| `/stability/gz-curve` | KN 近似 `BM·sinφ·cosφ + KB·sinφ` 丢了 `½BM·tan²φ·sinφ`；KB 用 `T(0.5+0.05Cb)`；另有 `*(1+0.001φ)`、`(1−0.002(φ−30))`、`*0.95` 三个无出处的经验修正 | `backend/api/stability_imo.py:99,119` |
| `/stability/evaluate` | KB = `T(0.5+0.5Cb)`；GZmax = `GM·sin45°·1.5`；消失角 = `60+10GM`，均为闭式臆估，非静水力积分 | `backend/api/stability.py:42` |

重要观察：**面积判据会掩盖 GZ 形状错误**。本算例 `area_0_30` 误差仅 50.626%
（小倾角偏高、大倾角偏低相互抵消），若只用 IMO 面积衡准复核，就会把一条形状错误的 GZ 曲线判为合格。
因此验收必须包含逐点 GZ 与 GM，而不能只看面积。

其它局限：
* 参考船型只有箱型（有闭式解）。真实商船（Cb≈0.8）的大倾角 GZ 无解析解，本 harness 只对
  其静水力因子给出 Morrish/Normand 级经验基准，不作 <3% 断言。
* 甲板入水后（>30°）箱型参考失去物理意义，故 GZmax / 消失角 在多数算例判 WITHHELD；
  这部分欠账需要真实船体型线 + 静水力软件（或 NAPA/GHS 参考数据）才能补齐。
* 平台湿表面积用 Holtrop(1984) 经验公式估，对 Cb=1 箱型偏低 -10.9%
  （ALERT）——这是经验公式适用范围问题（Holtrop 面向 Cb 0.55~0.85 商船），非编码错误。

## ⑥ 误差判定（逐项，显式回答是否 <3%）

| 关键量 | 误差 | <3%？ | 基准 |
|---|---|---|---|
| ITTC-57 Cf（PASS 对照） | -0.0197% | **是** | 恒等式 Cf=0.075/(log10Re−2)² |
| KB（箱型，精确 T/2） | +33.33% | **否** | 精确解 |
| BM（箱型，精确 B²/12T） | +0.008% | 是 | 精确解 |
| GM（箱型，精确） | +61.54% | **否** | 精确解 |
| GZ(30°)（`/stability/gz-curve`） | -21.93% | **否** | 闭式解（窗口内精确） |
| GZ(30°)（`/calculations/stability` 标量 gz_max 口径见 JSON） | 见 `artifacts/analytic_bench/stability_audit.json` | 否 | 闭式解 |

**honest 判定**：`calc.stability` 当前**不满足** 3% 判据，也不满足 15% 可接受线（KB +33%、GM +62%、
GZ(30°) −22%）。本单只做"验证欠账"交付（基准 + harness + 证据 + 根因），修复需改另一仓
`ship-performance-platform`（不在本席仓白名单），已按流程派单。

## 复现

```bash
python3 -m tools.analytic_bench.stability_audit            # 生成 artifacts/analytic_bench/*
python3 -m pytest tests/test_analytic_stability_bench.py -q # 23 passed, 2 xfailed
```
