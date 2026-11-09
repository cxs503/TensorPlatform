# 补报：冰-水拖曳力系数标定（plan 1 / node 18 / mission 683）

- mission_id: **683**（补报单 m8102，reconcile_sha256=`e0609dabe3ca898a`）
- 战役节点：plan 1 node 18「冰-水拖曳力系数标定」（原 repo=TensorLBM，status=done，
  `finished_at=2026-10-31T03:57:05Z`）
- 本报告性质：**证据链重建 + 重新判级**。原始工作树已被回收，原始工件在全盘（含
  TensorLBM 三个镜像仓全分支）不可检索到，故本报告不复述原实现，而是重建其
  *证据结构*并交平台机器判级器 `tools/evidence_grade.py` 重新判定。

## ① 案例介绍

物理背景：冰颗粒/碎冰在流体中运动时，LBM/DEM 耦合的相间动量交换项取
`F = 0.5·Cd·ρ·A·|u_rel|·u_rel`，其中拖曳力系数 `Cd` 是 `Re` 的函数。原节点声称
"标定 Cd，对标 ITTC 推荐公式与实验数据"，验收判据：
`Cd_error_vs_ITTC < 0.15`（ratio）、`Re_range_coverage ⊇ [1, 1e5]`、
`pytest_test_drag_coefficient == PASS`。原结果记录 `Cd_error_vs_ITTC = 0.000000`、
pytest 30/30 PASS。

参数表：

| 项 | 值 |
|---|---|
| 验收判据 | `Cd_error_vs_ITTC < 0.15` |
| 声称 Re 覆盖 | [1, 100000] |
| 记录到的实测误差 | `0.000000` |
| 参考数据（本报告独立基准） | Clift, Grace & Weber (1978) standard drag curve (Lapple-Shepherd tabulation), smooth sphere |
| 独立解析基准 | Stokes 精确解 `Cd = 24/Re`（Re→0 极限） |
| 闭合族 | Schiller-Naumann `24/Re(1+0.15Re^0.687)`；Morrison (2013)；Newton 0.44 |

## ② 方法/流程

1. **溯源**：从平台库读回 mission 683 的 `result/evidence/on_disk_changes`，
   得到原产出的 5 个路径（`src/tensorlbm/ice_drag_coefficient.py`、
   `tests/test_ice_drag_coefficient.py`、`docs/ice_drag_coefficient_calibration_report.md`、
   `artifacts/ice_drag_coefficient_calibration.png`、`artifacts/ice_drag_force_vs_velocity.png`）。
2. **取证**：`/root/tp-agent/work/` 无 m683 工作树；对 TensorLBM 三处克隆
   （`/opt/mm-mirror/TensorLBM.git`、`/opt/mm-verify/TensorLBM`、
   `/root/TensorLBM_test_waic`）执行 `git log --all --name-status -- '*ice_drag*'`
   → 零命中。结论：**原始工件不可恢复**（诚实记录，不补造）。
3. **重建证据结构 + 机器判级**：把"实现输出 vs 同一条公式"写成
   `evidence-contract/1.0` 契约 A（as_reported），把"经验闭合 vs 解析 Stokes 极限"
   写成契约 B（independent），两者都交 `tools/evidence_grade.py::grade_contract`
   （R00–R13 规则 DAG）判级，判级只看字节级来源与可复算残差，不看作者声明。
4. **独立基准**：用同一脚本在声明的 Re 网格上计算三种闭合族与实验标准阻力曲线
   （Clift/Grace/Weber 1978 表格）的相对误差，量化"这条能力到底准到什么程度"。
5. **工具链**：Python 3 / matplotlib(Agg) / `tools/evidence_grade.py` /
   工件 sha256 与 provenance digest 全部由脚本生成，报告数字全部来自
   `runs/m8102/ice_drag_cd/artifacts/*.json`，无手抄。

## ③ 计算结果

### 3.1 机器判级（关键结论）

| 契约 | 规则链最终判定 | failed_rule | reason_code |
|---|---|---|---|
| A. as_reported（记录证据结构） | **WITHHELD** | `R10_INDEPENDENCE` | `SAME_SOURCE_COPY` |
| B. independent（SN vs 解析 Stokes） | **REAL** | `None` | `ALL_GATES_PASS` |

A 的失败详情：`[{"code": "SAME_SOURCE_COPY", "detail": "cd_implementation declares derivation from ['cd_reference']"}]`

A 通过 R00–R09（schema 合法、锚可读、残差 0.0 可复算、0.0 ≤ 0.15 达标），
但在 R10「路径独立性」被拦下：被判级器识别为 **同源推导**，即该 0 残差是
自比较的恒等式（对同一条公式两边都取该公式的值），**不构成验证证据**。
B 在同一判据下判为 **REAL**：解析锚与经验闭合来源不同、残差复算一致、
负控可失效、provenance digest 匹配。→ 说明判级器本身不偏袒：它给 B 放行，
说明 A 的失败不是口径问题，而是证据结构问题。

### 3.2 独立基准 Cd(Re)

| Re | Cd_exp | SN | err % | Morrison | err % |
|---|---|---|---|---|---|
| 0.01 | 2400.000 | 2415.2161 | +0.6 | 2400.1526 | +0.0 |
| 0.10 | 240.000 | 247.4012 | +3.1 | 240.2212 | +0.1 |
| 1.00 | 26.500 | 27.6000 | +4.2 | 24.6729 | +6.9 |
| 2.00 | 13.900 | 14.8979 | +7.2 | 13.0357 | +6.2 |
| 5.00 | 6.900 | 6.9753 | +1.1 | 6.3141 | +8.5 |
| 10.00 | 4.100 | 4.1511 | +1.2 | 3.9676 | +3.2 |
| 20.00 | 2.730 | 2.6095 | +4.4 | 2.5601 | +6.2 |
| 50.00 | 1.570 | 1.5381 | +2.0 | 1.4880 | +5.2 |
| 100.00 | 1.070 | 1.0917 | +2.0 | 1.0382 | +3.0 |
| 200.00 | 0.770 | 0.8056 | +4.6 | 0.7677 | +0.3 |
| 500.00 | 0.550 | 0.5627 | +2.3 | 0.5674 | +3.2 |
| 1000.00 | 0.450 | 0.4383 | +2.6 | 0.4841 | +7.6 |
| 2000.00 | 0.420 | 0.3455 | +17.7 | 0.4349 | +3.6 |
| 5000.00 | 0.400 | 0.2551 | +36.2 | 0.4024 | +0.6 |
| 10000.00 | 0.410 | 0.2039 | +50.3 | 0.3936 | +4.0 |
| 20000.00 | 0.430 | 0.1634 | +62.0 | 0.3941 | +8.3 |
| 50000.00 | 0.470 | 0.1222 | +74.0 | 0.4066 | +13.5 |
| 100000.00 | 0.470 | 0.0983 | +79.1 | 0.4247 | +9.6 |

汇总（相对误差，|·|）：

| 指标 | 值 |
|---|---|
| SN 最大误差 Re∈[1,800] | **7.18 %** @ Re=2 |
| SN 最大误差 Re∈[1,1e5] | **79.09 %** @ Re=100000 |
| Morrison 最大误差 Re∈[1,1e5] | **13.49 %** @ Re=50000 |
| SN vs 解析 Stokes @Re=0.1 | 3.08 % |
| Morrison vs 解析 Stokes @Re=0.1 | 0.09 % |

## ④ 结果图

- `fig1_cd_vs_re.png`：Cd(Re): Schiller-Naumann / Morrison / Stokes vs experimental standard drag curve; shaded band = claimed Re range beyond the closure's validity.
- `fig2_error_vs_re.png`：Relative error of each closure vs the experimental standard drag curve, with the 3% and 15% lines; dashed vertical = Re=800 validity edge.
- `fig3_evidence_routes.png`：Residual per evidence route (log scale). Route A is zero by construction and therefore carries no information; routes B-D are genuine disagreements.

图 1/2 是与实验标准阻力曲线/解析解的对比曲线；图 3 是四条证据路线的残差量级
（对数轴），直接展示"零残差不等于证据"。原任务本应提供的场量云图
（LBM 流场速度/涡量）**无法提供**——原始工作树与工件已灭失，重算需要重建
整个 TensorLBM 冰颗粒算例，超出补报单范围；此处如实声明缺口而非用示意图搪塞。

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **自比较陷阱（本单核心发现）**：验收量 `Cd_error_vs_ITTC` 的分子分母来自同一条
   公式时，残差恒为 0，与实现是否正确无关。记录的 `0.000000` 正是这种结构。
   任何"误差恰好为 0"的数值验收都应当先问：两条路径的字节来源是否不同？
2. **工件不可恢复**：原节点 `on_disk_changes` 列出的 5 个文件在平台工作树与 TensorLBM
   三处克隆的全部分支中均不存在（`git log --all -- '*ice_drag*'` 零命中）。
   TensorLBM 侧 `matured mission deliverables（N 个文件被门禁滤除）` 的合并信息说明
   域仓合并时确有文件被滤除，工件从此不在版本历史里。
3. **参考数据的精度天花板**：实验标准阻力曲线表格本身有 ~1–2% 的取整/数据散布，
   因此"误差 <3%"的判定在 1–2% 附近的分辨力有限；本报告的 15%/3% 结论只在
   表格分辨力之上才可信。
4. **适用范围**：Schiller-Naumann 仅 Re ≲ 800 有效；声明的 `Re∈[1,1e5]` 覆盖
   不能由单一 SN 闭合实现（见汇总表，1e5 处误差 79.1%）。
   若冰颗粒问题需要全 Re 覆盖，必须用分段/Morrison 类闭合，或把覆盖声明收窄。
5. **"ITTC 推荐公式"本身不可核**：原实现对标的究竟是哪一条 ITTC 条款式，随工件灭失
   无法确认；本报告因此把"对标 ITTC"降级为"对标闭合族 + 实验标准阻力曲线"这种
   可独立复算的口径。
6. **勘误**：本报告首版（report id 9447）的 §6 表中有两格 `<3%?` 为硬编码 `是`，与脚本算出的 7.2%/3.1% 不符；现已改为由生成器计算（`是/否` 由数值判定），正文数字未变。这正是本单反复强调的纪律：判定必须由数字生成，不能由作者声明。
8. **与既有补报 report 9349 的关系**：9349（2026-11-09T04:12Z，0 图）以「TensorLBM DEM Schiller-Naumann 模型 vs sphere Re=100 LBM 基准」报 T1/T2 误差 2.77%/2.54%（<3%）。本报告与之不矛盾：Re=100 处 SN 对实验标准阻力曲线的误差为 2.0%，与 2.5–3% 同量级；但 9349 未覆盖 Re>800，本报告指出全 Re 覆盖不成立（79.1% @Re=1e5）。两报合并后的正确结论是「Re≲800 合格、全 Re 覆盖不成立」，而非原单声称的 0 误差。
9. 冰区工程上更有意义的量是**非球形冰/碎冰群**的整体拖曳系数（Cd≈1–2 量级，
   依赖浓度/形状），与本文的球形颗粒闭合不在同一适用面，不能互推。

## ⑥ 误差判定（逐项，显式回答是否 <3%）

| 关键量 | 基准 | 误差 | 是否 <3% |
|---|---|---|---|
| 原文声称 `Cd_error_vs_ITTC` | 同一条公式（自比较） | 0.000000 | **不适用**——恒等式，非独立测量，判级 WITHHELD |
| SN 闭合 Cd(Re)，Re∈[1,800] | 实验标准阻力曲线 | 最大 7.18 % | **否** |
| SN 闭合 Cd(Re)，Re∈[1,1e5] | 实验标准阻力曲线 | 最大 79.09 % | **否** |
| Morrison Cd(Re)，Re∈[1,1e5] | 实验标准阻力曲线 | 最大 13.49 % | **否** |
| SN Cd @Re=0.1 | 解析 Stokes 24/Re | 3.08 % | **否** |
| Re 覆盖 | 声称 [1, 1e5] | 覆盖可达，但精度不达标（见上两行） | 覆盖达标 / 精度不达标 |

**结论（honest）**：node 18 的 `done` 状态**不由其记录的证据支撑**——唯一记录到的
验收数字 0.000000 是自比较恒等式，机器判级 WITHHELD（`SAME_SOURCE_COPY`）。
用独立基准重算，该闭合族在 Re∈[1,800] 内最大误差 7.2%（满足 15% 验收线，但未达 3%），
在声称的 Re 上限 1e5 处失控（79.1%），因此**"Cd 标定 + 全 Re 覆盖"
这一复合主张不成立**；成立的是较弱的主张："Re≲800 的球形冰颗粒拖曳系数闭合
与实验标准阻力曲线一致到 7.2%"。原单按本报告应改判为
**部分成立（PARTIAL）/ 证据 WITHHELD**，而非 REAL。

## 产物与复现

```
runs/m8102/ice_drag_cd/node18_reissue.py            # 本报告全部数字与图的生成器
runs/m8102/ice_drag_cd/artifacts/independent_benchmark_cd_vs_experiment.json
runs/m8102/ice_drag_cd/artifacts/contract_as_reported/{contract.json,grade_as_reported.json}
runs/m8102/ice_drag_cd/artifacts/contract_independent/{contract.json,grade_independent.json}
runs/m8102/ice_drag_cd/artifacts/fig1_cd_vs_re.png / fig2_error_vs_re.png / fig3_evidence_routes.png
```
复现：`python3 runs/m8102/ice_drag_cd/node18_reissue.py`
