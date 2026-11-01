# 静水力曲线族（`GET /api/v1/geometry/hydro-curves/{ship}`）独立复核

模板：TensorPlatform · mission 1576 · 分支 `agent/m1576`
工件：`artifacts/verifier/hydro_curves_audit.json`、`artifacts/verifier/hydro_curves_kcs_snapshot.json`、
`artifacts/verifier/fig_hydro_curves_kcs.png`

## 0. 一句话结论

**FAIL —— 2/10 条判据通过。** 端点对 KCS 在设计吃水返回 **Cb = 1.1949 > 1**
（物理不可能）与排水量 **97964.1 t**（已发表基准 53331 t，
偏差 **+83.69%**）；且排水体积随吃水**严格线性**，说明每换一个吃水就重建了一条
几何相似的新船，而不是对固定船型切水线。该能力此前在证据台账中为 UNVERIFIED（无任何证据），本单给出首份定量证据。

## 1. 为什么选这条欠账

平台规则「几何必须先验证 Cb/L/B」——任何阻力/稳性/推进数字都以静水力为前提。报告 1148 的台账中
`geometry.hydro_curves` 是 REAL 自述但**零证据**（15 条 UNVERIFIED 之一）。本条可用纯闭式数学裁定，
不需要文献查表，也不可能被"自洽 PASS"糊弄过去。

## 2. 方法（第二套独立实现，不 import 被审代码）

`tools/verifier/geometry/hydrostatics_reference.py`：给定 `b(x,z)` 半宽场，
以 801x201 网格梯形积分 ∇、Awp、IT、VCB、LCB、湿表面积（含曲舷弧长项 `sqrt(1+(db/dx)^2)`）。
**积分器自身先标定**：

| 标定对象 | 判据 | 结果 |
|---|---|---|
| 箱形浮体（Cb=1, KB=T/2, BM=B²/12T, Sw=LB+2LT） | 闭式 | 全部 < 1e-3 相对误差 |
| 抛物水线直立舷（Cb=2/3, BM=2B²/35T, IT=4B³L/105） | 闭式 | 全部 < 1e-3 |
| 曲舷湿表面积 | `scipy.integrate.quad` | 9969.50 vs 9969.51 m² |
| 网格收敛（401x101 → 801x201） | 自比 | < 1e-3 |

黑箱探针：`GET http://127.0.0.1:8001/api/v1/geometry/hydro-curves/KCS?n_drafts=5`，快照落盘。
根因定位为**只读源码检查**（运行仓 `ship-performance-platform/backend/api/hydrostatics.py`）。

## 3. 端点返回的曲线（原样）

| 吃水 d [m] | 排水量 [t] | Cb [-] | Awp [m²] | VCB [m] | LCB(AP) [m] | 湿面积 [m²] |
|---|---|---|---|---|---|---|
| 1.08 | 9796.4 | 1.1949 | 6061.5 | 0.984 | 115.918 | 6555.0 |
| 3.51 | 31838.2 | 1.1949 | 6061.4 | 3.197 | 115.918 | 7625.7 |
| 5.94 | 53880.2 | 1.1949 | 6061.5 | 5.410 | 115.918 | 8998.5 |
| 8.37 | 75922.1 | 1.1949 | 6061.5 | 7.624 | 115.918 | 10497.9 |
| 10.80 | 97964.1 | 1.1949 | 6061.5 | 9.837 | 115.918 | 12065.5 |

`meta` = {"L": 230.0, "B": 32.2, "T": 10.8, "Cb": 0.651}；设计吃水返回体积 ∇ = 95574.7 m³。

## 4. 判据与裁决（机器可裁，含定义级恒等式）

| 判据 | 命题 | 观测 | 基准 | 相对误差 | 裁决 |
|---|---|---|---|---|---|
| I1 | Cb <= 1 at every draft (Cb = nabla/(L B d), definition) | 1.1949 | 1.0 | +19.49% | **FAIL** |
| I2 | TPC = 1.025*Awp/100 (definition) | 62.13 | 62.13 | +0.00% | PASS |
| I3 | d(nabla)/dT = Awp(T) (fundamental theorem of calculus) | 8849.5 | 6061.5 | +46.00% | **FAIL** |
| I4 | VCB/d <= 0.65 for ship forms | 0.9111 | 0.65 | +26.11% | **FAIL** |
| I5 | Awp and wet_surface non-decreasing in draft | 6061.5 / 6061.5 | monotone | — | **FAIL** |
| I6 | reported Cb == displacement/(L B d) (internal consistency) | 1.1949 | 1.1949 | -0.00% | PASS |
| I7 | reported Cb (curves) == meta.Cb (same endpoint) | 1.1949 | 0.651 | +83.55% | **FAIL** |
| B1 | nabla(T) == published KCS 52030.3 m3 | 95574.7 | 52030.3 | +83.69% | **FAIL** |
| B2 | Cb(T) == published KCS 0.6505 | 1.1949 | 0.6505 | +83.69% | **FAIL** |
| B3 | wet_surface(T) == published KCS 9424 m2 | 12065.5 | 9424.0 | +28.03% | **FAIL** |

I2/I6 通过，说明端点**在它自己的定义链里是自洽的**；失败全部发生在物理侧——这正是"真代码、错物理"，
而非格式或单位错配。

## 5. 根因（只读源码检查，不改运行仓）

`backend/api/hydrostatics.py`：

1. `hydrostatics_at_draft()` 对每个吃水调用 `generate_hull_sections(L, B, d, Cb, ship_name=...)`——
   **用该吃水重新生成船型**并把同一目标 Cb 再标定一次。结果是每个吃水一条几何相似的新船：∇ ∝ d（实测线性度
   0.000034%），Awp 与 LCB 与吃水无关，
   Cb 恒为 1.1949（= 0.6505 × 1.8369，双重 Cb 标定的放大因子）。
2. `_section_props()` 中 `mom_half += y_avg*dz*((z1+z2)/2 + draft)`：截面型线的 z 已是水线相对坐标，
   再加一次 `draft` 即**下限重复计入**，故 VCB ≈ 0.9111·d（应约 0.5–0.55·d）。
3. `MTC = 0.01·Awp·L/100` 是占位公式（无纵稳心半径 I_L），应视为 STUB 级输出。

正确做法：船型**只在设计吃水生成一次**，各吃水仅积分 z ≤ d 的部分（本单参考实现即如此）。

## 6. 判据集非退化（负对照）

若判据只会否决则毫无价值。用本单参考实现对鼓形（`b ∝ sqrt(z/T)`，Cb 随吃水变化）生成 21 点静水力表，
喂给同一裁决函数：**I1–I7 全部 PASS**（`tests/test_hydrostatics_reference.py::test_criteria_pass_on_a_physically_correct_table`）。
即判据能把"对的表"和"错的表"分开。

## 7. 局限与适用范围

- 外部基准用公开 KCS 主尺度/排水量/湿表面积（SNAME/Gothenburg 2010 通行值），其中湿面积与 LCB 的文献值置信度弱于 ∇ 与 Cb；
  故 B3、LCB 只作旁证，**不单独作为裁决依据**，裁决主体是 I1/I3/I4/I7/B1/B2。
- 未做真实船型（非 KCS）的端到端复核；端点每个吃水重建船型，其"船型"本身也不等于 KCS 型线，故 B1/B2 的偏差
  同时含"船型不对"与"Cb 标定错"两个来源，本单不拆分二者权重。
- 运行仓不在本工作树白名单内，**本单只交证据与判据，未修端点**；修复单需另派（`runner=native`）。
- 端点响应慢（单次 5 吃水约 30 s），不适合在线校验路径。

## 8. 复现

```bash
python3 tools/verifier/geometry/audit_hydro_curves.py --live   # 重新探针（需 :8001）
python3 tools/verifier/geometry/audit_hydro_curves.py          # 回放快照（离线确定性）
python3 -m pytest tests/test_hydrostatics_reference.py -q       # 9 passed
```
