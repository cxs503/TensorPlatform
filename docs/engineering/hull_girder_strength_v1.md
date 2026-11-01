# 总纵强度（船体梁纵向强度）能力落地 v1 —— 结构域最后空白补全与解析基准验证

**日期**：2026-11-01 　**分支**：`agent/m1598` 　**mission**：1598（tp-doc-3 自找活）
**新增代码**：`backend/services/hull_girder_strength.py`、`backend/api/calculations.py`
（`POST /calculations/hull-girder-strength`）、`backend/tests/test_hull_girder_strength.py`、
`scripts/validate_hull_girder_strength.py`　**工件**：`artifacts/hull_girder/`

## 1. 为什么做这一件

平台 21 项能力台账里，结构学科（结构强度）只有 `plate-stability`（板格屈曲，报告 1049/1091）
一项，**船体梁总纵强度（hull girder longitudinal strength）完全没有实现**——`backend/` 全仓
grep `girder|longitudinal_strength|section_modulus|bending_moment` 零命中。这是九学科里
唯一一处「连桩都没有」的核心能力空洞：阻力/推进/耐波/操纵/稳性/绿航/冰区都已有多轮
审计报告（1061/1085/1124/1134/1118 等），结构只有板格层。

因此本单不是「验证欠账」，而是**功能补全 + 自带验证判据**：先造可机器裁的解析基准，再交付
实现，避免重演「先合入后判 MOCK」的老路。

## 2. 做了什么（三层，逐层标注证据等级）

| 层 | 内容 | 证据等级 | 判据来源 |
|---|---|---|---|
| L1 剖面特性 | 由板厚构件（甲板/船底/内底/舷侧/纵舱壁）用平行轴定理求 `A, z_NA, I, W_deck, W_bottom` | **REAL** | 闭式解 + 几何数值积分双基准（<1e-3） |
| L2 静水剪力/弯矩 | 载荷曲线 `q(x)=b(x)-w(x)` 双积分，自由端边界条件，显式输出闭合残差与静水平衡残差（超差**抛错**，不塞补正系数） | **REAL** | 抛物载重闭式解 `BM_mid=ΔL/32`、`BM(x)` 四次多项式全profile |
| L3 波浪弯矩/最小剖面模数 | IACS UR S11 §3.2 波浪弯矩包络 + UR S7 最小剖面模数 + `σ=M/W ≤ 175/k` 屈服校核 | **REAL(公式层)** | 手工算术复算（非自比） |

诚实边界（写进函数 docstring 与报告）：
- 仅梁理论：无剪切滞后、无翘曲、无扭转、**无极限强度/疲劳/局部载荷**；
- UR S11 波浪弯矩公式适用 `90 m ≤ L ≤ 500 m`，`L<90 m` 用 `C=0.0412L+4.0` 并置
  `within_s11_window=0.0` 标记外推；
- 静水工况用**抛物载重解析基准**（不是真实配载工况），这一点在返回值 `notes` 里明写；
  `hull_girder_assessment` 不臆造货舱/压载重量分布，调用方必须给真实重量曲线。

## 3. 计算结果（全部由 `scripts/validate_hull_girder_strength.py` 程序化写入
`artifacts/hull_girder/validation_summary.json`，报告零手抄）

### 3.1 解析基准误差（关键量）

| 判据 | 平台值 vs 基准 | 相对误差 | <3%？ |
|---|---|---|---|
| 中剖面静水弯矩 `BM_mid` vs `ΔL/32` | 3.6770 GN·m vs 3.6770 GN·m | **6.48e-16** | **是** |
| 全 profile `BM(x)` vs 四次多项式（21 采样点最大偏差） | — | <1e-5（测试门 <1e-5） | **是** |
| 剪力闭合 `SF(L)/Δg` | 6.94e-7 | — | 是 |
| 弯矩闭合 `BM(L)/(ΔgL)` | 3.47e-7 | — | 是 |
| 静水平衡残差 `(B-Δ)/Δ` | 6.94e-7 | — | 是 |
| T 型剖面 `I` vs 几何双重积分（20000×2000 网格） | — | <1e-3 | **是** |
| 对称箱型剖面 `z_NA` vs `D/2` | 9.000000000 m | <1e-12 | **是** |
| UR S11/S7 公式 vs 手工算术 | 一致 | <1e-12 | 是（公式复现，非独立物理） |

### 3.2 三个算例的判决（不是全部 PASS —— 如实给出）

| 算例 | 剖面 | σ_total [MPa] | σ_perm [MPa] | W_actual [m³] | W_required [m³] | 判决 |
|---|---|---|---|---|---|---|
| 120 m coaster, 20 mm 单壳箱型 | 单壳 | 167.1 | 175 | 3.94 | 3.31 | **PASS** |
| 120 m coaster, 8 mm 单壳箱型 | 单壳 | 416.3 | 175 | 1.58 | 3.31 | **FAIL** |
| 200 m bulker, 双壳（甲板 24 / 底 26 / 舷 19 mm + 内底 20 / 内舷 15 mm） | 双壳 | 324.0 | 175 | 17.79 | 18.97 | **FAIL** |

第三个 FAIL 是**物理正确的诚实结论**，不是 bug：200 m 船在抛物载重基准（`ΔL/32` =
3.68 GN·m）叠加 UR S11 波浪弯矩（1.94 GN·m 中拱 / 2.09 GN·m 中垂）下，仅计纵向板厚的
双壳剖面（`I=181.7 m⁴`，`W_deck=17.8 m³`）既达不到 UR S7 的 `W_min=19.0 m³`，应力也超
限。真实船体靠纵骨/舷侧桁/甲板纵桁/舱口围板等纵向构件承担，本模块**不计这些**，因此
200 m 量级必须把这些构件显式建成构件再评估——这一点写进了 `double_hull_section` 的
docstring（误差方向：低估 I → 保守判 FAIL，不会给出假 PASS）。

## 4. 图

`artifacts/hull_girder/`：
- `fig1_swbm_closure.png`：L=200 m 静水剪力与弯矩分布 + 四次多项式解析解对照曲线（两条线
  完全重合，残差 3.5e-7）；
- `fig2_section_and_stress.png`：三算例 `W_actual` vs `W_required`（UR S7）柱状 + 应力利用率
  （1.0 为限值，红/绿区分）；
- `fig3_rule_envelope.png`：UR S11 波浪弯矩包络与 UR S7 `W_min` 随 L 的曲线（L=90–500 m），
  两个算例船位置打点。

## 5. 注意点 / 踩过的坑

1. **200 m 单壳箱型永远过不了 UR S7**（20/22/16 mm 只有 W=13.6 m³ < 19.0 m³）。一开始拿它
   当 PASS 演示例，测试直接红——这不是实现错，是我的算例选错。改为 120 m coaster（W_min
   3.31 m³）才既紧张又可满足，`test_single_skin_box_at_200m_fails_ur_s7` 反而把这个
   "过不了"固化成回归门。
2. **梯形积分的截断误差不能强行 <1e-9**：抛物载重下 `SF(L)`、`BM(L)` 闭合残差是 O(h²)，
   n=1201 时实测 6.9e-7 / 3.5e-7。测试把它当**报告量**并给同量级门限，而不是把残差抹平。
3. **不平衡的浮态不能算弯矩**：`still_water_shear_bending` 对 `|B-Δ|/Δ > tol` 直接抛
   `ValueError`（测试 `test_equilibrium_violation_is_rejected_not_patched` 用 5% 超浮验证）。
   老毛病是塞一个线性的"补正载荷"把闭合强行做成 0，那样静水弯矩就没有物理意义了。
4. **UR S11 的 C 在 L=90 m 两侧分支差 2e-4**（7.708 vs 7.707）——测试把它当连续性判据，
   同时 s=90 以下标 `extrapolated`，避免在适用窗外假装合规。
5. 端点 `POST /calculations/hull-girder-strength` 是**新路由**：本单只在 worktree 里交付代码
   + 路由级测试（`TestClient` 挂在最小 `FastAPI()` 上），线上 :8001 需要重启才可探测；
   本单不重启生产进程（容量纪律：load15 125 ≥ 100，只做轻活）。

## 6. 误差判定汇总

关键量全部对标**独立基准**（闭式解 / 几何积分 / 手工算术），最差相对误差 1e-3（T 型剖面
几何积分，网格离散所致），其余 ≤ 6.9e-7。**逐项均 <3%**。
唯一无独立基准的是 UR S11/S7 公式层——它按定义就是规则公式的复现，校核方式是手工算术
对拍（<1e-12），并**不**声称具备规范认证效力。

## 7. 后续线索（给下一次自找活）

- 把纵骨/纵桁/舱口围板作为纵向构件纳入 `double_hull_section`（需要 `SectionElement` 支持
  带自身偏心的纵向加强筋）→ 200 m 量级才能给出有工程意义的 PASS/FAIL；
- 加波浪剪力（UR S11 §3.3）与许用剪应力 `τ_perm = 110/k`，以及货舱/压载真实配载曲线输入；
- 加极限强度（Smith 法逐步破坏）——那是真正的 `ultimate strength`，本模块明确不含。