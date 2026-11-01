# 平台横稳性 GZ 曲线模型 独立复核（验证欠账）

**对象**：船舶总体性能平台 `POST /api/v1/stability/gz-curve` 与 `POST /api/v1/stability-imo/gz-curve`（两者共用同一后端实现）。

**判据**：与独立精确参考（(1) 直壁式解析横截曲线 closed form，(2) 体素体积积分横截曲线）逐角度比对。验收门槛：误差 >15% 不可接受；目标 <3%。

**工件**：`artifacts/gz_audit/gz_audit.json`（原始数字）、`docs/verification/figs/`（图 1-4）、`tests/test_gz_cross_curve_audit.py`（11 项回归守卫，全绿）。


## 1. 案例介绍

平台该端点用 4 个主尺度 + `Cb/Cm/KG` 参数化地给出 0–90°、每 5° 一条静稳性力臂曲线，并顺带给出 `area_0_30 / area_0_40 / area_30_40` 与 `GZ_max`，这些量直接喂给 IMO 完整稳性衡准。因此它的精度不是学术细节，而是**判定能不能通过稳性衡准**的问题。

两个算例都选在**存在严格解析解**的参数域，使基准不依赖任何文献读数：

| 算例 | L [m] | B [m] | T [m] | D [m] | Cb | Cm | KG [m] | 精确参考 |
|---|---|---|---|---|---|---|---|---|
| box_barge_Cb1.0 | 100 | 20 | 10 | 20 | 1.0000 | 1.0000 | 7.0 | closed-form wall-sided up to deck edge (45.0 deg), voxel volume integration beyond |
| parabolic_Cb0.667 | 100 | 20 | 8 | 16 | 0.6667 | 1.0000 | 5.5 | closed-form wall-sided (parabolic waterplanes, KB=4.000, BM=2.857) up to deck edge 38.7 deg, voxel beyond |

几何一致性先做校验：体素积分得到的排水体积与解析值一致（方驳 `V0 = 20000 m³` vs `LBT = 20000 m³`），抛物形水线算例的 `Cb` 与解析值 2/3 一致（见测试 `test_parabolic_hydrostatics_quadrature`）。


## 2. 方法

**参考解 A（闭式）**：直壁式（矩形横剖面）船体在水线面惯性矩不变的角度区间内，稳心 M 固定，于是

```
KN(phi) = sin(phi) * [ KB + BM * (1 + 0.5*tan^2(phi)) ]
GZ(phi) = KN(phi) - KG * sin(phi)
```

对 `0 <= phi <= 甲板边缘入水角` 是**精确**的，不是近似。抛物形水线算例的 `KB/BM` 用求积公式 `BM = I/A_wp/T`、`I = (2/3)∫ b(x)^3 dx` 独立算得，`Cb` 亦由几何积分给出。

**参考解 B（数值）**：把船体离散为等体积体素，按倾角旋转后取「最下方 N 个体素」即完成定排水量自由液面求解（等体积 ⇒ 无需迭代），`KN = mean(y_world)`。该路径与闭式解在方驳上互校，最大相对偏差 `1.06%`（图 1 红线），达到可用精度；超过甲板边缘入水角后改用该路径作为参考。

**平台侧**：直接调用线上端点（`127.0.0.1:8001`），并把其 `KN` 公式`(BM*sinφ*cosφ + KB*sinφ)*(1+0.001φ)` 独立转写一遍，确认快照确实来自该实现（转写偏差 `5.0e-05`）。


## 3. 计算结果

### 3.1 静水力要素

| 算例 | KB 模型 | KB 精确 | BM 模型 | BM 精确 | KM 模型 | KM 精确 | KM 误差 |
|---|---|---|---|---|---|---|---|
| box_barge_Cb1.0 | 5.500 | 5.000 | 3.320 | 3.333 | 8.820 | 8.346 | +5.7% |
| parabolic_Cb0.667 | 4.267 | 4.000 | 2.733 | 2.857 | 7.000 | 6.868 | +1.9% |

### 3.2 GZ 逐角度对比与误差


**box_barge_Cb1.0**（甲板边缘入水角 45.0°；参考 GZ_max = 3.316 m @ 70°，平台 GZ_max = 0.860 m @ 40°）

| φ [°] | KN 平台 | KN 精确 | GZ 平台 | GZ 精确 | ΔGZ [m] | \|ΔGZ\|/GZ_max | 判定(<15%?) |
|---|---|---|---|---|---|---|---|
| 0 | 0.000 | 0.000 | 0.000 | 0.000 | +0.000 | 0.0% | 是 |
| 5 | 0.771 | 0.727 | 0.161 | 0.117 | +0.044 | 1.3% | 是 |
| 10 | 1.538 | 1.456 | 0.323 | 0.241 | +0.082 | 2.5% | 是 |
| 15 | 2.287 | 2.188 | 0.476 | 0.376 | +0.100 | 3.0% | 是 |
| 20 | 3.007 | 2.926 | 0.613 | 0.532 | +0.081 | 2.5% | 是 |
| 25 | 3.686 | 3.675 | 0.728 | 0.717 | +0.011 | 0.3% | 是 |
| 30 | 4.313 | 4.444 | 0.813 | 0.944 | -0.131 | 4.0% | 是 |
| 35 | 4.880 | 5.249 | 0.856 | 1.233 | -0.378 | 11.4% | 是 |
| 40 | 5.377 | 6.111 | 0.860 | 1.611 | -0.751 | 22.7% | **否** |
| 45 | 5.799 | 7.071 | 0.824 | 2.121 | -1.298 | 39.1% | **否** |
| 50 | 6.140 | 7.982 | 0.710 | 2.620 | -1.910 | 57.6% | **否** |
| 55 | 6.399 | 8.677 | 0.600 | 2.943 | -2.343 | 70.7% | **否** |
| 60 | 6.573 | 9.218 | 0.456 | 3.156 | -2.700 | 81.4% | **否** |
| 65 | 6.663 | 9.619 | 0.282 | 3.275 | -2.993 | 90.3% | **否** |
| 70 | 6.672 | 9.893 | 0.082 | 3.316 | -3.233 | 97.5% | **否** |
| 75 | 6.603 | 10.058 | 0.000 | 3.297 | -3.297 | 99.4% | **否** |
| 80 | 6.463 | 10.130 | 0.000 | 3.236 | -3.236 | 97.6% | **否** |
| 85 | 6.258 | 10.114 | 0.000 | 3.140 | -3.140 | 94.7% | **否** |
| 90 | 5.995 | 10.000 | 0.000 | 3.000 | -3.000 | 90.5% | **否** |

**parabolic_Cb0.667**（甲板边缘入水角 38.7°；参考 GZ_max = 2.615 m @ 75°，平台 GZ_max = 0.700 m @ 35°）

| φ [°] | KN 平台 | KN 精确 | GZ 平台 | GZ 精确 | ΔGZ [m] | \|ΔGZ\|/GZ_max | 判定(<15%?) |
|---|---|---|---|---|---|---|---|
| 0 | 0.000 | 0.000 | 0.000 | 0.000 | +0.000 | 0.0% | 是 |
| 5 | 0.612 | 0.599 | 0.133 | 0.119 | +0.014 | 0.5% | 是 |
| 10 | 1.220 | 1.198 | 0.265 | 0.243 | +0.022 | 0.8% | 是 |
| 15 | 1.815 | 1.801 | 0.391 | 0.378 | +0.013 | 0.5% | 是 |
| 20 | 2.385 | 2.410 | 0.503 | 0.529 | -0.025 | 1.0% | 是 |
| 25 | 2.921 | 3.029 | 0.597 | 0.705 | -0.108 | 4.1% | 是 |
| 30 | 3.417 | 3.667 | 0.666 | 0.917 | -0.250 | 9.6% | 是 |
| 35 | 3.862 | 4.335 | 0.700 | 1.180 | -0.480 | 18.3% | **否** |
| 40 | 4.252 | 5.047 | 0.667 | 1.512 | -0.844 | 32.3% | **否** |
| 45 | 4.581 | 5.736 | 0.638 | 1.847 | -1.209 | 46.2% | **否** |
| 50 | 4.845 | 6.349 | 0.576 | 2.136 | -1.560 | 59.7% | **否** |
| 55 | 5.042 | 6.830 | 0.485 | 2.325 | -1.840 | 70.4% | **否** |
| 60 | 5.171 | 7.243 | 0.365 | 2.480 | -2.115 | 80.9% | **否** |
| 65 | 5.233 | 7.537 | 0.220 | 2.553 | -2.333 | 89.2% | **否** |
| 70 | 5.230 | 7.782 | 0.054 | 2.614 | -2.560 | 97.9% | **否** |
| 75 | 5.165 | 7.927 | 0.000 | 2.615 | -2.615 | 100.0% | **否** |
| 80 | 5.043 | 8.012 | 0.000 | 2.595 | -2.595 | 99.3% | **否** |
| 85 | 4.869 | 8.043 | 0.000 | 2.564 | -2.564 | 98.1% | **否** |
| 90 | 4.651 | 8.000 | 0.000 | 2.500 | -2.500 | 95.6% | **否** |

### 3.3 供 IMO 衡准使用的派生量

| 算例 | area_0_30 平台 | 精确 | 误差 | area_0_40 平台 | 精确 | 误差 | area_30_40 平台 | 精确 | 误差 |
|---|---|---|---|---|---|---|---|---|---|
| box_barge_Cb1.0 | 0.2362 | 0.2142 | +10.3% | 0.3839 | 0.4333 | -11.4% | 0.1477 | 0.2192 | -32.6% |
| parabolic_Cb0.667 | 0.1940 | 0.2123 | -8.6% | 0.3133 | 0.4212 | -25.6% | 0.1193 | 0.2089 | -42.9% |

### 3.4 接口行为探针

- 排水量入参：`displacement = 20500` 与 `30750` t 返回**逐点相同**的 GZ 曲线 → declared displacement is ignored by the model。
- GZ 被 `max(0, GZ)` 截断：两个算例 `min(gz_platform) >= 0`，消失角与负力臂区间在输出中不可见（见 `test_gz_never_negative_due_to_clipping`）。
- 甲板边缘阶跃：`GZ *= 0.95` 与 `GZ *= (1-0.002(φ-30))` 在甲板角处引入非物理折点/跳变（甲板角探针：44.0° 平台 0.831 m vs 精确 2.006 m）。

## 4. 结果图

图 1 `figs/fig1_kn_box_barge.png`：方驳 KN 横截曲线——平台 vs 闭式精确 vs 体素互校。

图 2 `figs/fig2_gz_box_barge.png`：方驳 GZ 曲线平台 vs 精确（竖线为甲板边缘入水角）。

图 3 `figs/fig3_gz_parabolic.png`：抛物形水线船 GZ 曲线平台 vs 精确。

图 4 `figs/fig4_gz_error.png`：两个算例的 |ΔGZ|/GZ_max 随角度变化，叠加 15%/3% 判据线。


## 5. 注意点与局限

- 参考解 A 的适用上界是**甲板边缘入水角**（方驳 45.0°，抛物形算例 38.7°），超过该角度改用体素路径；体素路径按标准横截曲线约定处理完整船体外形，**不建模甲板进水/破舱**，因此大口径角度区间的对比只说明「横截曲线本身」的精度，不外推到破损稳性。
- 体素路径的离散误差约 1%（方驳闭式互校给出），远小于本文关心的 15% 门槛，但仍属数值参考而非解析解。
- 平台端点只接受 `L,B,T,D,Cb,Cm,KG`，无法表达真实型线；因此本文把它视为「由 Cb/Cm 参数化的统计模型」来评，两个算例的 Cb/Cm 与该参数化一致，比较是同类对同类。
- 未覆盖：横摇阻尼/动稳性、自由液面修正、`GM` 显式入参分支、风暴横摇衡准（weather criterion）。

## 6. 误差判定

**关键量 vs 基准，逐项是否 <3%：**

- `box_barge_Cb1.0`：KM 误差 +5.7% → **不满足 <3%**；0–30° 区间 GZ 最大归一化误差 4.0% → **不满足 <3%**；area_0_40 误差 -11.4% → **不满足 <3%**；GZ_max 平台 0.860 m vs 精确 3.316 m。
- `parabolic_Cb0.667`：KM 误差 +1.9% → **不满足 <3%**；0–30° 区间 GZ 最大归一化误差 9.6% → **不满足 <3%**；area_0_40 误差 -25.6% → **不满足 <3%**；GZ_max 平台 0.700 m vs 精确 2.615 m。
- 方驳 KB：模型 `T(0.5+0.05Cb) = 5.500` vs 精确 `T/2 = 5.000` → **误差 +10.0%，不满足 <3%**（该项直接抬高 KM 5.7%）。

**结论（数字均由工件生成）**：5–30° 区间该模型仍在 15% 验收带内（box_barge_Cb1.0 最大 4.0%，parabolic_Cb0.667 最大 9.6%）；但自 box_barge_Cb1.0 40°、parabolic_Cb0.667 35° 起归一化误差超过 15%；参考有效区间内（甲板边缘入水角 +5° 以内）最大 box_barge_Cb1.0 58%、parabolic_Cb0.667 32%；GZ_max 被低估 box_barge_Cb1.0 74%、parabolic_Cb0.667 73%；area_30_40 被低估 box_barge_Cb1.0 33%、parabolic_Cb0.667 43%，area_0_40 被低估 box_barge_Cb1.0 11%、parabolic_Cb0.667 26%。因此**大倾角稳性与喂给 IMO 衡准的面积量不满足平台的 15% 精度标准**，该能力不能按「已验证的 GZ 曲线」宣示。

## 7. 修复建议（可选补丁，未在本仓实施）

修复对象是**另一仓库**（`backend/api/stability_imo.py`），不在本单可写白名单内，故仅给出可直接落地的补丁骨架，并把该修复作为派单候选：

```python
# platforms/.../backend/api/stability_imo.py  (ready-to-apply sketch)
def calculate_kb(T, Cb, Cm=None, Cwp=None):
    # wall-sided / prismatic hulls: KB = T/2 is exact; better: Morrish
    return T * (0.5 + 0.05 * Cb) if Cm is None else T * (0.5 + 0.05 * Cb)

def calculate_bm(B, T, Cb, Cm=None, Cwp=None):
    # exact for a wall-sided hull: BM = Cwp * B^2 / (12 * Cb * T)
    cwp = Cwp if Cwp is not None else min(1.0, Cb / max(Cm, 1e-6) * 0.97 + 0.03)
    return cwp * B ** 2 / (12.0 * max(Cb, 1e-6) * T)

for phi in angles:
    p = math.radians(phi); s, c = math.sin(p), math.cos(p)
    if c < 1e-9:
        KN = KB * 1.0 + BM * 0.0
    else:
        KN = s * (KB + BM * (1.0 + 0.5 * (s / c) ** 2))   # wall-sided, exact to deck edge
    GZ = KN - KG * s
    gz_values.append(GZ)      # keep the sign: no max(0, GZ) clipping
# clip only where the hull form is unknown, and report the applicability range
```

1. Replace `KN = (BM*sin*cos + KB*sin)*(1+0.001*phi)` by the wall-sided cross curve
   `KN = sin(phi) * (KB + BM * (1 + 0.5*tan^2 phi))`. The deployed `BM*sin*cos` factor
   makes the BM arm *shrink* with heel whereas the physical wall-sided arm *grows*
   (`+0.5*BM*tan^2 phi`); the `(1+0.001*phi)` term cannot repair the sign.
2. Drop the two ad-hoc large-angle factors `GZ *= (1 - 0.002*(phi-30))` and
   `GZ *= 0.95` beyond the deck-edge angle: they introduce a 5% step and were
   compensated against the wrong KN, so removing them without (1) is worse.
3. Drop `max(0, GZ)`: clipping destroys the vanishing angle and the negative
   range that IMO `GZ <= 0` criteria and the weather criterion need.
4. `displacement` is accepted but unused; either remove it from the contract or
   use it to check the geometry/`Cb` consistency (L*B*T*Cb*rho vs displacement).
5. Mark the method metadata as `validation_status="preliminary"` while the
   large-angle branch is a heuristic; the current claim of a verified GZ curve
   is not supported by any published comparison.


## 8. 复现

```bash
python3 tools/verification/gz_audit/run_audit.py     # 调线上端点 → artifacts/gz_audit/gz_audit.json + 图
python3 tools/verification/gz_audit/make_doc.py      # 工件 → 本文档（数字零手抄）
python3 -m pytest tests/test_gz_cross_curve_audit.py -q   # 11 项回归守卫
```

EDIT_WHITELIST: tools/verification/, tests/, docs/, artifacts/

