# 破舱稳性域修复 F1~F5（SOLAS 2009 Reg.6 / Reg.7.2）

**承接**：验证报告 id=1093（mission 1503，判决 **MOCK**，8 判据 2 过 6 败）、工件
`docs/verif/damage_stability_verification.md`、`artifacts/verif/damage_stability/{evidence,verdict}.json`。
**本单**：mission 1507（FIX-DAMAGE，runner=native）。
**交付件**：`backend/api/damage_stability.py`（替换件）、`tests/test_damage_stability_solas.py`（23 项测试）、
`artifacts/verif/damage_stability_repair/`（evidence.json + 3 图 + 生成脚本）。

---

## 0. 一句话结论

把破舱稳性三个端点从「经验式 GZ + 硬编码 R + 用户直传 s + 二值生存概率」改成
「损失浮力法几何求解 + Reg.6 的 R + 由 GZ 曲线算 Reg.7.2 的 s + Reg.7.2 衡准集」。
**R 偏差 0%、终吃水对解析解偏差 6.9e-5（<3%）、s 与 Reg.7.2 闭式解逐点一致、破损范围 x10 改变 8/9 项输出**；
23/23 测试通过。

## 1. 缺陷 → 修复（逐条对照验证报告）

| 编号 | 缺陷（验证报告 §2） | 修复实现 | 证据（程序化取值） |
|---|---|---|---|
| F1 | `R = min(0.5Ls/100+0.5, 0.9)`，货船 Ls≥80 m 恒 0.9，Ls=100 时高 +82.9% | `solas2009_required_index()`：货船 Ls≥80 → `1−128/(Ls+152)`；Ls<80 → `1−152/(Ls+152)` | `F1_required_index.max_rel_dev = 0.0`；旧值最坏高估 `+0.829` |
| F2 | `A = Σ p·s`，s 由请求体直传（纯回声，换船不变） | `calculate_A()` 逐区用该区破损几何重算 GZ 曲线 → `s = solas2009_s_index(GZmax, Range)`；请求体 `survival` 一律忽略并置 `input_s_ignored=True` | `F2_no_echo.pass = True`（survival=0/0.5/1 三次 A 完全相同且 = p·s(GZ)）；`F2_s_function.matches_reference = True` |
| F3 | 破损几何未进入计算（`damage_extent ×10` 7 项输出逐项不变）；终吃水 6.301 vs 解析 7.1287（−11.6%） | 损失浮力法：水线面损失 `Σμ·A_breach`（A_breach 由舱室几何 ∩ damage_extent 的 length/width/height 决定）、平行下沉 `t' = ∇_ws/(Aw − ΣμA_breach)`、纵倾 `τ = −δx·∇_ws/I_L`、横倾取 `GZ(θe)=0` 的根 | `F3_draft_vs_analytic.max_rel_dev = 6.9e-5`；`F3_extent_sensitivity` x10 后 **8/9** 项改变 |
| F4 | `survival_probability = 1.0 if imo_compliant else 0.5`（二值伪造） | 删除该逻辑；输出 `s_factor` 为物理 s，`survival_probability` 保留为**弃用别名**（值恒等 `s_factor`） | `F2_F4_s_vs_KG`：同船扰动 KG 得 s ∈ {1.0, 0.9978, 0.9598, 0.0}（4 个不同取值，无 0.5）；且存在「不合规但 s=1.0」工况证明 s 不由 `imo_compliant` 驱动 |
| F5 | `freeboard > 0.3 m` 等非 SOLAS 判据参与合规 | 衡准集 = Reg.7.2 五项（θe≤7°、GZmax≥0.10 m、Range≥16°、开口不浸没/残存干舷、s>0），每项标注 `source`；旧 freeboard/GM 判据降级为 `_diagnostics`（不参与合规） | `F5_criteria.pass = True`；`/imo-criteria` 同步给出 `deprecated_non_solas` |

## 2. 物理模型（可复算）

**几何（等宽壁式等效箱体）**：`Cw=(1+2Cb)/3`，水线宽 `B_wp=Cw·B`，`Aw=Cw·L·B`，
等效排水体积 `∇_ws = Aw·T`（无损平衡吃水恒为输入 T；Cb=1 驳船时 `∇_ws=∇0=Cb·L·B·T`，与解析解同源）。

**破损几何**：舱室水平截面积 `Ac = l·b`（有显式几何）或 `V/h`；破口覆盖因子
`fl=min(1,l_d/l_c)`、`fb=min(1,b_d/b_c)`、`fh=min(1,h_d/h_c)`；
进水体积 `V=μ·min(A_breach·h_eff, V_c)`；水线面损失 `Σμ·A_breach`。

**平衡**：
- 平行下沉 `t' = ∇_ws/(Aw − ΣμA_breach)`（= 验证报告 R3 闭式解，Cb=1 时逐位一致）；
- 纵倾 `δx = A_loss(x_F−x_flood)/(Aw−A_loss)`，`τ = −δx·∇_ws/I_L`，`trim = τ·L`；
- 横倾：壁式截面逐站积分求浮心
  `A(d,θ)=∫[h−g]dy`、`y_B=∫y(h−g)dy/A`、`z_B=∫[h²/2−(z_b g+g²/2)]dy/A`，
  `GZ(θ)=y_B cosθ+(z_B−KG)sinθ − GG0 sinθ`，`GG0=Σi_x/∇_ws`；
  θe 取 `GZ(θ)=0` 的 |θ| 最小根（对称注水时为 0）。

**Reg.7.2 s**：`s=min(1,((GZmax/0.12)(Range/16))^0.25)`；GZmax<0.10 m 或 Range<16° → 0；
GZmax≥0.20 m 且 Range≥20° → 1。Range 自 θe 起算、上限为甲板边（限界开口）浸没角 `θ_cap=atan(2(D−t')/B_wp)`。

**初稳性**：`KB=T/2`、`BM_d=(I_T0 − A_loss·y² − L·b_f³/12)/∇_ws`、`GM_d=KB+BM_d−KG−GG0`。

## 3. 计算结果（全部来自 `artifacts/verif/damage_stability_repair/evidence.json`）

### 3.1 F1 R(Ls)（SOLAS Reg.6）

| Ls (m) | 修复后 R | SOLAS R | 相对偏差 | 旧实现 | 旧值高估 |
|---|---|---|---|---|---|
| 60 | 0.283019 | 0.283019 | 0 | 0.800 | +182.7% |
| 100 | 0.492063 | 0.492063 | 0 | 0.900 | +82.9% |
| 150 | 0.576159 | 0.576159 | 0 | 0.900 | +56.2% |
| 200 | 0.636364 | 0.636364 | 0 | 0.900 | +41.4% |
| 250 | 0.681592 | 0.681592 | 0 | 0.900 | +32.0% |

### 3.2 F3 终吃水对解析解（盒式驳船 L100/B20/T6，舱 V=2000 m³、H=6、TCG=+8 m）

| μ | 平台终吃水 (m) | 解析 `∇0/(Aw−μAc)` (m) | 相对偏差 | 横倾 (°) | 纵倾 (m) | GM_d (m) |
|---|---|---|---|---|---|---|
| 0.95 | 7.129 | 7.1287 | **4.0e-5** | 19.47 | −2.709 | 1.965 |
| 0.50 | 6.545 | 6.5455 | **6.9e-5** | 8.95 | −1.309 | 3.200 |

对照旧实现：同一工况终吃水 6.301 m（偏差 −11.6%），且 `damage_extent ×10` 七项输出逐项不变。

### 3.3 F3 破损范围敏感性（同一驳船，逐项）

| 破损范围 (m) | 终吃水 (m) | 进水体积 (m³) | GM_d (m) | GZmax (m) | Range (°) | s |
|---|---|---|---|---|---|---|
| 5×5×2 | 6.072 | 47.5 | 4.425 | 1.261 | 15.07 | 0.0000 |
| 10×5×6 | 6.146 | 285.0 | 4.294 | 1.167 | 13.86 | 0.0000 |
| 16.7×10×6 | 6.516 | 950.0 | 3.598 | 0.560 | 6.36 | 0.0000 |
| 16.7×20×6（全破） | 7.129 | 1900.0 | 1.965 | 0.000 | 0.00 | 0.0000 |
| 100×20×60（×10） | 7.129 | 1900.0 | 1.965 | 0.000 | 0.00 | 0.0000 |

（`10×5×6 → 100×20×60` 共 8/9 项改变，唯一不变的是 `s_factor`：两者横倾相差 17°，但均使 Range 低于 Reg.7.2 的 16° 阈值 → s=0，属规范阈值行为，而非「几何未进入计算」。）

### 3.4 F2/F4 s 的物理性

- `s(GZmax, Range)` 与独立复算在 0.08–0.23 m × {16,18,20}° 网格上 **逐点相等**（93 例，其中 6 例落在 0<s<1 中间带）。
- 中间带（`0<s<1`）模型层实测：L120/B12/T6/D7.70/Cb0.70，KG 3.90/4.00/4.05/4.10 →
  s = 1.0000 / 0.9978 / **0.9598** / 0.0000（Reg.7.2 的中间带很窄：只有 GZmax∈[0.10,0.12) m 且 Range∈[16,19.2)° 才落入）。
- 请求体直传 `survival ∈ {0, 0.5, 1}`：A 完全不变且等于 `p·s(GZ)` → 回声缺陷已消除。

## 4. 图

1. `fig_F1_required_index.png`：R(Ls) 修复前后 + 旧值相对误差曲线。
2. `fig_F2_GZ_s.png`：左=残存 GZ 曲线族（KG 2→6，止于甲板边浸没角）；右=s(GZmax) 与独立复算叠合。
3. `fig_F3_geometry.png`：左=破损范围 → 终吃水/进水体积/GM_d；右=终吃水 vs 解析解（标注相对偏差）。

## 5. 注意点 / 局限（如实）

1. **等宽壁式等效近似**：`B_wp=Cw·B`，`Cb` 仅通过 `Cw=(1+2Cb)/3` 进入；对矩形驳船（本域验收基准）GZ 积分精确，
   对一般船型不含舭部/球鼻/剖面形状效应，**BM 与 GZmax 存在系统偏差**（壁式截面高估大倾角右臂）。
   因此 s 的绝对值只作「量级/趋势/合规判定」用，不作为对某条实船的定量结论。
2. **Range 上限取甲板边浸没角**（`tanθ=2(D−t')/B_wp`），未建模具体开口（通风筒/门）高度；
   实际衡准应按最低限界开口逐一校核。
3. **2D 等效沿船长平均**：破损水线面损失按 `b_f=A_loss/L` 折算到壁式截面，纵倾用损失水线面对 LCF 的一阶矩；
   对长度远小于船长（l_c ≪ L）的舱室，纵倾量为近似（Ω 量级误差，无独立基准可裁，故未列入误差判定）。

4. **Reg.7.2 的中间带极窄**：按题目给定的 s 公式，`s<1` 只在 `GZmax·Range < 1.92 m·deg` 时出现，
   故「A 随 GZ 连续变化」在模型层只能在很窄的工况带内直接观察（本单给出 0.9598 一例 + 函数层连续性与单调性网格证据）。
5. **几何预检**：输出 `geometry_check` 对排水量与 Cb·L·B·T 的一致性做 >15% 告警（平台精度标准），
   仅告警、不参与 SOLAS 合规判定。

## 6. 误差判定（显式回答「是否 <3%」）

| 关键量 | 基准 | 相对偏差 | 是否 <3% |
|---|---|---|---|
| R(Ls)（Ls=40…300 共 9 点） | SOLAS 2009 Reg.6 闭式 | 0（最大 0.0） | **是** |
| 终吃水 t'（μ=0.95） | 损失浮力解析 7.1287 m | 4.0e-5（0.004%） | **是** |
| 终吃水 t'（μ=0.50） | 损失浮力解析 6.5455 m | 6.9e-5（0.007%） | **是** |
| s | Reg.7.2 闭式（由本次 GZmax/Range 复算） | 逐点相等（90/90） | **是** |
| GZmax / Range / 横倾 | 无独立基准（等宽壁式近似，见 §5.1） | — | **否（无基准，不作定量判定）** |

## 7. 交付与复现

```
backend/api/damage_stability.py                      # 修复实现（drop-in 替换）
tests/test_damage_stability_solas.py                 # 23 项验收测试（F1~F5 + 几何预检）
artifacts/verif/damage_stability_repair/
  generate_evidence.py       证据/图生成器（零手抄）
  evidence.json              全部数字 + 每项 pass 布尔
  fig_F1_required_index.png  fig_F2_GZ_s.png  fig_F3_geometry.png
```

```
python3 artifacts/verif/damage_stability_repair/generate_evidence.py
python3 -m pytest tests/test_damage_stability_solas.py -q      # 23 passed
```

## 8. 落地说明（重要，如实）

本单由调度器指派仓库为 **TensorPlatform**（worktree `/srv/tensor/agent/work/m1507`），
而缺陷实体在 **ship-performance-platform** 的 `backend/api/damage_stability.py`。
交付件按白名单写在 TensorPlatform 工作树内（`backend/api/damage_stability.py` 等路径），
**与 ship 侧文件同构、可直接覆盖**：

```
cp backend/api/damage_stability.py <ship-performance-platform>/backend/api/damage_stability.py
# 重启 :8001 服务后，/api/v1/damage-stability/{analyze,probabilistic,imo-criteria} 即生效
```

未覆盖 ship 侧文件的原因是「只改工作树内文件」的硬约束；`:8001` 现网仍为旧实现（MOCK），
需要在 ship 仓派一张同步/落地单才能让线上端点转为 REAL。