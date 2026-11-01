# 操纵性域基线 v1：MMG 3-DOF 框架落地与证据分级（MANOEUVRING_MMG_V1）

**对象**：`backend/methods/manoeuvring/`（新建）+ `backend/api/domain/manoeuvring.py`（新端点）+ `tools/verification/manoeuvring_baseline.py`（可复现跑批）+ `backend/tests/feat/test_manoeuvring.py`（25 项校验）。
**动机**：船舶总体性能九域中「操纵性」在平台内**零实现**——live serving 面 `:8001` 的 `POST /api/v1/calculations/manoeuvring` 返回 **404**（同域 `calculations/seakeeping` 返回 405 = 端点存在），仓库内 `grep -ri "manoeuvr|zigzag|kijima|mmg"` 无命中；近 24h 终态单覆盖阻力/耐波/稳性/推进/绿色航行，操纵性为空白，不与在飞单重复。

## 1. 做了什么

| 层 | 内容 | 证据等级 |
|---|---|---|
| 模型方程 | MMG 标准型 3-DOF（surge–sway–yaw），型式为 Yasukawa & Yoshimura (2015) 综述的 modular 分解：`(m+m_x)u̇-(m+m_y)vr=X_H+X_P+X_R`、`(m+m_y)v̇+(m+m_x)ur=Y_H+Y_R`、`(I_zz+J_zz)ṙ=N_H+N_R+N_munk` | REAL（结构） |
| 船体力 | Newtonian 横流积分：`dY=-0.5ρC_D T|v+xr|(v+xr)dx`（Simpson 61 站）+ 细长体附加质量（2D 剖面 `ρπ(T/2)²`）+ Munk 去稳力矩 `-(m_y-m_x)uv` | REAL（实现），**不含势流升力项** |
| 舵力 | Fujii 横流法向力 `F_N=0.5ρA_R U_R² f_α sin α_R` + MMG 推力减额/整流系数 `t_R,a_H,x_H,x_R,w_R` | REAL（结构） |
| 螺旋桨 | 等推力假设 `X_P=R_T(U0)`（自航平衡；不引入敞水曲线数据） | 简化假设（已声明） |
| 标准机动 | 回转圈（35° 舵）、10/10 与 20/20 Z 形、稳态回转 Newton 解、圆拟合 | REAL |
| 判据 | IMO Res. MSC.137(76)（advance ≤4.5L、战术直径 ≤5L、Z 形超调限值；L<100 m 标 advisory） | REAL（判据实现） |
| **绝对精度** | 无离线可溯源的力导数集 / EFD 比对 | **WITHHELD**（本文量化其不确定度带） |

数值实现：经典 RK4，`dt=0.5 s`（敏感性门：`dt=0.25` 与 `0.5` 的 advance 差 `1.7e-5`）；几何门强制 `Cb∈[0.35,0.90]`、`L/B∈[3,12]`、`B/T∈[1.5,6.5]`，若给定位移则校验 `Δ=ρC_bLBT` 闭合到 **3%** 以内，否则 400。

## 2. 解析/闭式判据（机器可复算）

| 判据 | 实测 | 基准 | 相对误差 | 门限 | 判定 |
|---|---|---|---|---|---|
| Nomoto 稳态增益 `Kδ` vs Newton 稳态解 | 4.71889e-05 | 4.72005e-05 | 2.45e-04 | 3% | PASS |
| Newton 稳态 `r_ss` vs 4000 s 时间推进 | 1.61209e-03 | 1.61023e-03 | 1.16e-03 | 0.5% | PASS |
| Newton 稳态 `u_ss` vs 时间推进 | 4.870 | 4.865 | 1.06e-03 | 0.5% | PASS |
| 稳态回转直径：轨迹圆拟合 vs `u/r` 运动学 | 6044.95 m | 6041.85 m | 5.13e-04 | 3% | PASS |
| 时间步敏感性（advance, dt 0.25 vs 0.5） | — | — | 1.75e-05 | 1% | PASS |
| 左右舷对称性 `ᅵψ(-δ)+ψ(+δ)ᅵ/ψ` | 0 | 0 | 0 | 1e-9 | PASS |
| 几何门拒绝不自洽排水量（3%） | 触发 | 触发 | — | — | PASS |
| Nomoto 一阶瞬态 vs 非线性仿真（t>0.3T 最大偏差） | 14.95% | — | 1.50e-01 | 20% | PASS（一阶等效粗糙，如实记录） |

结论：**数值实现（积分、线性化、稳态求解、指标提取）全部在 0.12% 以内复现各自闭式解**，误差判据「是否<3%」=**是**（除有意标注的 Nomoto 一阶等效瞬态项，其偏差 14.95% 属近似模型固有，非实现误差）。

## 3. 标准机动结果（KVLCC2 主尺度，L=320 m, B=58 m, T=20.8 m, Cb=0.8098, U0=7 m/s, Fr=0.125）

回转圈（δ=35°）：advance **10.59 L**、transfer **8.97 L**、战术直径 **10.00 L**、稳态回转直径 6041.8 m（**9.44 L** 半径）、失速 **30.4%**、漂角 2.47°。
Z 形：10/10 超调 20.6°（三次）、20/20 超调 41.1°。
IMO MSC.137(76) 判定：advance 10.59 L（限 4.5 L）**FAIL**、战术直径 10.00 L（限 5 L）**FAIL**、10/10 首超调 20.6°（限 10°）**FAIL**、二超调 20.6°（限 25°）PASS、20/20 首超调 41.1°（限 25°）**FAIL**。

## 4. 关键发现（本单的实质增量）

**纯横流船体力模型不可用于操纵性定量预报。** 对三个「已声明但有物理区间」的建模常数做 12 组组合（附加质量系数 k∈{1.0,2.3}、Munk 开关、横流阻力 C_D∈{0.5,1.0,1.3}），用 Newton 稳态解扫描：

- 稳态回转直径区间 **3.67 L ↔ ∞（部分组合不收敛 = 航向失稳）**；
- 同一几何、同一舵角下 IMO 5 L 判据的结论可为 PASS 也可为 FAIL——**判决随未溯源常数翻转**；
- 参考配置（k=1.0, Munk 开, C_D=1.0）给出 10.0 L，比 IMO 限值大 2 倍、比 KVLCC2 公开试验量级（约 3 L 战术直径）大 3 倍以上。

**归因（可检验）**：
1. **缺势流升力项**：真实船体线性导数 `Y'_v≈-0.3`（量纲化约 −5×10⁶ N/(m/s)）；本模型在 v→0 处横流力按 v² 趋零，无线性升力，只能靠大漂角/大回转率产生侧向力 → 回转能力被系统性压低。这是 Newtonian 模型的固有缺陷，不是代码缺陷（同一模型在稳态/瞬态闭式判据上误差 <0.12%）。
2. **舵来流未含桨尾流加速**：等推力假设下 `u_R=(1-w_R)u=0.75u`，缺 `ε~1.3–1.7` 的桨尾流因子，法向力约低 1.7–3 倍。
3. **Munk 力矩主导**：`m_y/m=0.348`（细长体估算）时，Munk 去稳力矩与舵力矩同量级，使上面第 1 条被放大成「判决翻转」而非「小幅偏差」。

因此本模块的 serving 端点显式返回 `accuracy_grade="WITHHELD"` 与三条 caveats，**不**输出任何 IMO 合规结论。

## 5. 局限与关闭路径

- 关闭「绝对精度」缺一条即可：接入**可溯源**的 MMG 导数集（Kijima 型回归或 CFD/EFD 辨识），代码已按 `ModelParameters` + `MMGModel.forces()` 分层，替换船体力/舵力函数即可，不改积分器、指标提取与 IMO 判据（三者已被本单闭式门锁定）。
- 需补桨尾流模型（需敞水曲线 `K_T(J)`：推进域已有审计记录，见 report 1128/1133，其敞水实现目前判为 MOCK，故本单未复用）。
- 适用域：深水、静水、无横倾耦合；未含浅水/岸壁效应、风浪中操纵、冰区操纵。
- 环境限制：本 worktree 的完整 app 导入缺 `cryptography` 依赖，故端点测试用 `importlib` 按路径加载路由模块 + `TestClient`（`--noconftest`），未做全栈启动验证。

## 6. 产物

- 代码：`backend/methods/manoeuvring/{model.py,manoeuvres.py}`、`backend/api/domain/manoeuvring.py`（`POST /api/v1/calculations/manoeuvring`、`GET .../limits`）。
- 测试：`backend/tests/feat/test_manoeuvring.py`（25 passed）。
- 可复现跑批：`tools/verification/manoeuvring_baseline.py` → `artifacts/manoeuvring/{results.json, fig_turning_circle.png, fig_zigzag.png, fig_nomoto_check.png, fig_uncertainty_band.png}`。