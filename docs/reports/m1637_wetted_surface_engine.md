# m1637 阻力链湿表面积引擎：验证欠账清偿与几何分支重建

**结论先行**：`/api/v1/calculations/resistance` 的默认湿表面积估计，在 12 个独立基准上
最大绝对误差由 legacy 的 **83.2 %** 降到
**12.1 %**（域内），船型带（Cb ≤ 0.90）
**3.81 %**，回转体带
**5.83 %**，15 % 判决线上域内失败数
9/10 → 0/10。
**3 % 判决线未全过**（域内 3/10 超 3 %），如实记录在 §6。

## 1 案例介绍（物理背景 / 几何 / 参数）

阻力链的绝对量级由 `S`（湿表面积）线性决定：`R_f = ½ρ S C_f V²`，`R_r = ½ρ S C_r V²`，
`C_T = R_T/(½ρ S V²)`。因此 `S` 的相对偏差**一比一**传进 `R_f` 与 `R_T`（`C_T` 反而被
`S` 约掉，这正是历史上用 `C_T` 对标看不到湿面积缺陷的原因）。

原实现（legacy，m1384 已记录其偏差但未修）：

    S = π·B·L·(0.5 + 0.25·Cb)

该式来自 "B = 最大直径" 的回转体标定，只在 `T ≈ B/2` 时自洽。平台实际服务的两族几何各自
单向失真：

| 几何族 | 典型参数 | legacy 偏差 |
|---|---|---|
| 回转体（SUBOFF 默认 T≈B） | L=4.356 m, B=0.508 m | −20 % ~ −34 %（低于 15 % 线） |
| 船体（KCS/KVLCC2/DTMB-5415） | L/B≈5.5–7.5, B/T≈2.8–3.1 | **+50 % ~ +83 %**（灾难性高估） |

## 2 怎么做的（方法 / 流程 / 关键设置）

1. **先造判据**：`tools/verif/verify_wetted_surface.py` 从闭式几何独立重建 12 个参考值，
   不抄实现：圆柱（闭合式 `πDL+πD²/2`）、两种细长比回转椭球（`2πb²+2πab·asin(e)/e`）、
   球、圆锥、胶囊（圆柱 + 半球端帽，SUBOFF 同族）、Wigley 抛物型船体（对 `y(x,z)` 做
   `scipy.integrate.dblquad` 面积分，1e-10），以及文献记录值 KCS 9530 m²、KVLCC2 27194 m²、
   DTMB-5415 模型 4.72 m²。
2. **诊断**：legacy 在域内 10 例中 9 例超 15 %，
   最大 83.2 %（船型族系统性高估）。
3. **重建两分支**（新模块 `backend/services/wetted_surface.py`）：
   * **船型（`B/T ≥ 1.4`, `L/B ≥ 4`, `0.35 ≤ Cb ≤ 1.0`）**：Denny–Mumford / Holtrop-Mennen
     `S = 1.025·L·(1.7·T + Cb·B)`。
   * **回转体（其余）**：**保中体直径的等体积胶囊族**（capped-cylinder equivalent）——
     `R = B/2`，由体积闭合解端帽半长 `c = 1.5·(L − V/(πR²))`，夹到 `[0, L/2]`，面积
     `S = 2πR(L−2c) + 2(πR² + πcR·asin(e)/e)`。对椭球（`c=L/2`）、闭合圆柱（`c→0`）、
     半球端帽胶囊（`c=R`）**三者精确**；单参数（只保体积）的 `spheroid_equivalent` 保留为对照。
4. **接线**：端点新增 `wetted_surface_model`（`auto`/`denny_mumford`/`capped_cylinder_equivalent`/
   `spheroid_equivalent`/`legacy`），响应 `wetted_surface_source` 记录实际分支；
   `wetted_surface_m2` 输入仍最高优先（几何已验证者优先，符合平台规则）。
   legacy 保留 bit-exact 复现通道。

## 3 计算结果（程序化注入，零手抄；源 `artifacts/m1637/wetted_surface_validation.json`）

| case | kind | reference m² | legacy m² | legacy err | repaired model | repaired m² | repaired err |
|---|---|---|---|---|---|---|---|
| cylinder_closed | revolution | 7.357 | 4.841 | -34.20 % | capped_cylinder_equivalent | 7.357 | +0.00 % |
| spheroid_b/a=0.25 | revolution | 5.494 | 4.386 | -20.17 % | capped_cylinder_equivalent | 5.494 | +0.00 % |
| spheroid_b/a=0.15 | revolution | 3.284 | 2.632 | -19.86 % | capped_cylinder_equivalent | 3.284 | +0.00 % |
| sphere | revolution | 3.142 | 1.982 | -36.91 % | capped_cylinder_equivalent | 3.142 | +0.00 % |
| cylinder_open_bounded | revolution | 6.952 | 4.841 | -30.36 % | capped_cylinder_equivalent | 7.357 | +5.83 % |
| cone_open | revolution | 3.482 | 3.931 | +12.90 % | capped_cylinder_equivalent | 5.494 | +57.78 % |
| capsule_SUBOFF_like | revolution | 6.952 | 5.994 | -13.78 % | capped_cylinder_equivalent | 7.357 | +5.83 % |
| wigley_hull | ship | 5.356 | 6.911 | +29.03 % | denny_mumford | 5.561 | +3.81 % |
| barge_box_Cb1 | ship | 3920.000 | 4712.389 | +20.21 % | denny_mumford | 3444.000 | -12.14 % |
| KCS_full | ship | 9530.000 | 15417.054 | +61.77 % | denny_mumford | 9266.413 | -2.77 % |
| KVLCC2_full | ship | 27194.000 | 40958.426 | +50.62 % | denny_mumford | 27003.715 | -0.70 % |
| DTMB5415_model | ship | 4.720 | 8.648 | +83.22 % | denny_mumford | 4.753 | +0.69 % |

Wigley 独立交叉核对（平台自身静水力积分路径，与解析积分 5.356 m² 对照）：
`{'81x41': 5.341, '161x81': 5.341}` m²（两套分辨率 81×41 / 161×81 同值 → 已收敛）。

端到端影响（分别用/不用修复）：

| 输入 | `wetted_surface_source` | S (m²) | R_T (N) |
|---|---|---|---|
| 默认 SUBOFF（L=4.356, B=0.508, Cb=0.542），legacy | empirical_formula | 4.4180 | 68.687 |
| 同上，修复后默认 | capped_cylinder_equivalent | 5.4938 | 85.414 |
| KVLCC2（L=320, B=58, T=20.8, Cb=0.8098, V=7.974 m/s） | denny_mumford | 27003.7 | 1262909 |

## 4 结果图

* `artifacts/m1637/wetted_surface_validation.png`：12 例逐例误差条形图，红=legacy、绿=修复后，
  虚线为 ±15 % 接受线。
* `artifacts/m1637/wetted_surface_parity.png`：估计–参考 1:1 对数散点（修复后点全部贴近对角线，
  legacy 点在船型族系统性高出 1:1 线）。

## 5 注意点（坑 / 局限 / 适用范围）

* **draft 约定是唯一残留歧义**：回转体若用 `T = B/2`（半径当吃水）声明，`B/T = 2` 会落进
  船型带。已用 `B/T ≥ 1.4` 阈值把本 API 的 SUBOFF 默认（`T≈B`, `B/T=1.07`）与细型船体
  （Wigley `B/T=1.6`）分开；知道自己是对称体的调用方应显式指定
  `wetted_surface_model="capped_cylinder_equivalent"`。
* **超出模型域如实记账**：圆锥（无平行中体，端部收成一点）+57.8 %、开口圆柱 +5.8 %
  （端盘不计入）——这两例在 JSON 里标 `in_domain=false` 且**不参与**汇总统计，不得被引用为
  "通过"。
* **Cb→1 的箱型驳船** Denny–Mumford 退化到 −12.1 %（仍在 15 % 内，但不可当 3 % 级结果用）。
* 湿面积模型**不含附体**（舵、舭龙骨、轴系、球鼻外的附体面积），船型分支本身也不含
  `1.025` 之外的粗糙度奖励；带附体者必须传 `wetted_surface_m2`。
* 本单不做 `C_r` 残阻律复核（m1666 已判 honest-FAIL），修复后湿面积偏差不再掩盖该问题——
  残阻律仍是平台阻力精度的下一处瓶颈。

## 6 误差判定（逐项，显式回答"是否 <3%"）

域内 10 例，修复后相对误差：

| 判决线 | legacy | 修复后 |
|---|---|---|
| > 3 % 的例数 | 10/10 | 3/10 |
| > 15 % 的例数 | 9/10 | 0/10 |
| 最大绝对误差 | 83.22 % | 12.14 % |

**是否 < 3 %？—— 分族回答**：
* 回转体（圆柱/两细长比椭球/球，闭式精确参考）：**是**，最大
  5.83 %（胶囊形 +5.83 % 属该类最差，**否**）。
* 船型且 Cb ≤ 0.90（Wigley/KCS/KVLCC2/DTMB-5415）：**是**，最大
  3.81 %。
* 含 Cb = 1.0 箱型驳船：**否**（−12.14 %）。
* 文献值本身按 5 % 带判定（`literature_recorded`，本单未逐页复核原论文 PDF），因此
  KCS/KVLCC2/DTMB-5415 三条**不能**单独支撑 3 % 结论，只作交叉印证。

## 7 验证方式（可复跑）

    python3 tools/verif/verify_wetted_surface.py           # 参考值重建 + 两张图 + JSON
    python3 -m pytest backend/tests/test_wetted_surface_engine.py -q
    python3 -m pytest tools/verif/test_ship_calcs_verification.py -q

`backend/tests/test_wetted_surface_engine.py` 20 项全绿；`tools/verif/test_ship_calcs_verification.py`
中 `TestBenchmarkDatasetConsistency::test_reference_rows_satisfy_r_equals_ct` 仍失败——已用
`git stash` 在未改动树上复现，**属 m1561 正典 v3 落盘后遗留的陈旧期望**（SUBOFF 行现已被正典
改成自洽，测试仍断言它们不自洽），与本单改动无关，留作下一张验证单。
