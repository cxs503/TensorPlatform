# 阻力验证链欠账清偿：参考数据集一致性审计与校验器契约修复

任务：tp-impl-3 / mission 1452（TensorPlatform，分支 `agent/m1452`）
日期：2026-11-01
相关代码：`backend/data/benchmark_reference.json`、`backend/api/domain/ship_benchmark.py`、
`backend/api/calculations.py`、`backend/services/ship_resistance_core.py`
工具：`tools/audit_resistance_validation.py`、`tools/fix_benchmark_reference_dataset.py`、
`tools/figures_resistance_audit.py`
论文工件：`artifacts/resistance_validation_audit/{baseline,after_fix}.json` 与同名 `figures/`

---

## 1. 案例背景：这条链为什么值得审计

平台对外宣称 `calc.resistance` 为 **REAL**，其"验证证据"是
`POST /api/v1/calculations/benchmark_validate`：把 Holtrop/ITTC-57 阻力计算结果
与 `backend/data/benchmark_reference.json` 中的实验参考值比较，误差 ≤15% 判 pass。
这条链是整个船舶阻力能力的**唯一量化证据来源**——参考值一旦不可信，
pass/fail 判决就没有物理含义，REAL 等级也失去支撑。

参考数据集覆盖三类标模，共 12 个工况：

| 标模 | L (m) | B/T (m) | Cb | 工况数 | 引用来源 |
|---|---|---|---|---|---|
| DARPA SUBOFF（回转体） | 4.356 | D=0.508 | 由几何反推 | 4 | Roddy 1990, DTRC-90/012 |
| DTMB-5415 | 5.720 | 0.759 / 0.246 | 0.508（表列） | 4 | ITTC V&V Workshop 2010 |
| KVLCC2 | 320.0 | 58.0 / 20.8 | 0.8098 | 4 | SIMMAN 2008 |

## 2. 方法：用不变量而不是"看起来合理"来判真伪

无量纲化把阻力问题变成一条恒等式链，任何一个记录只要违背基本定义即可判定为不可信。
审计工具对每个工况检验 5 条不变量（`tools/audit_resistance_validation.py`）：

| 编号 | 不变量 | 判据 |
|---|---|---|
| I1 | Froude 数 | `Fn = V/√(gL)`，绝对容差 0.01 |
| I2 | 力的定义式 | `Rt = ½ρSV²Ct`，相对容差 2%（S 有文献值时） |
| I3 | 隐含湿表面积 | S/(L·B) ∈ [0.6, 3.0]（船）；S/(πDL) ∈ [0.5, 1.05]（回转体） |
| I4 | 系数下限 | `Ct/Cf(Re)` ∈ [1.1, 3.0]，Cf 用 ITTC-57 |
| I5 | 方形系数 | `Cb` 与 `∇/(LBT)` 之差 ≤0.02 |

同时对**部署中**的 `:8001` 阻力服务做闭式对照（L1 Re、L2 Fn、L3 力闭合），
防止线上实现与工作树代码无声漂移。

## 3. 结果：12/12 工况不合格（修复前）

`artifacts/resistance_validation_audit/baseline.json`：

| 工况族 | 失败不变量 | 量化 |
|---|---|---|
| SUBOFF-01…04 | I2 力闭合 | 表列 Rt 比自身 Ct 隐含值小 **7.1×–9.1×**（如 12.5 N vs 92.5 N） |
| DTMB5415-01…04 | I1 + I3 + I5 | 表列 Fn 0.061/0.122/0.244/0.366，按 `V/√(gL)` 应为 0.343/0.687/1.374/2.060；反推湿表面积 0.46–0.57 m²（5.72 m 船体应为 ~4.5 m²，差 8×）；Cb=0.508 与 displacement=466.3 kg 互斥（后者对应 Cb=0.437） |
| KVLCC2-01…04 | I3 | 反推湿表面积 7025–7589 m²，SIMMAN 值 27 194 m²，**Rt 偏小约 3.7×** |

**判定：修复前 12/12 记录至少违背一条物理恒等式，该数据集不能作为判据。**
I4 全部通过（Ct/Cf 落在 1.2–1.6 的合理带内），说明**无量纲 Ct 列本身是可信的**，
劣化集中在其派生量（力、Fn、排水量）——这也决定了修复策略。

同一轮审计对部署服务（`:8001`）的对照结果（`baseline.json → live`）：

| 检查 | 服务返回 | 闭式期望 | 比值 |
|---|---|---|---|
| Re（3 m/s, L=4.356, ν=1.004e-6） | 5 648 890 | 13 015 936 | 0.434 |
| Fn（同工况） | 0.2361 | 0.4589 | 偏小 1.94× |
| Rt（用服务自己的 S、Ct） | 0.06 N | 228.78 N | 2.6e-4 |

线上服务把力输出写成 0.02/0.04/0.06 N 量级（应为百 N 量级），即**力未乘 ½ρSV²**；
Re 与 Fn 也与自身几何不自洽。工作树代码与线上实现是两套不同实现（线上 schema 仍收
`speed`/`displacement`，工作树收 `speed_ms`），此前无人交叉核对。

## 4. 修复：不造数，只把定义式补回去

`tools/fix_benchmark_reference_dataset.py`（幂等，二次运行 0 变更）执行 24 处修正：

1. **Fn 重算**（I1）：4 条 5415 记录按 `V/√(gL)` 重算；
2. **Rt 重导**（I2）：有文献湿表面积的 SUBOFF（5.42 m², Roddy 1990）与 KVLCC2
   （27 194 m², SIMMAN 2008）由保留的 Ct 重新导出 `Rt = ½ρSV²Ct`；
   DTMB-5415 无文献湿表面积 → `experimental_rt_n = null`，**不编造**；
3. **排水量重导**（I5）：5415 的 466.3 kg 与表列 Cb=0.508 矛盾，按 `Cb·L·B·T·ρ`
   改为 541.5 kg；
4. **适用域标注**：5415 的 10/20/30 kn 工况 Fn 达 0.687–2.060，远超模型试验包络
   （Fn≤0.45），`validation_eligible=false` 并写明排除理由（疑为实船节速与模型几何混记），
   保留记录但不再参与 pass/fail 计分；
5. 每处改动写入该工况的 `dataset_fixes`（原值/新值/理由），顶层 `corrections` 记录
   工具、日期、策略与累计修正数。

**校验器契约修复**（`ship_benchmark.py`）：
- 原代码读 `response["ct"]`，但阻力端点从不发布 `ct` → 每次比较都是 `0.0` vs 参考值，
  造成"全体 100% 误差、全体 fail"的假判决；新增 `extract_ct()` 兜底（服务自带 ct →
  由力与湿表面积反推），并在 `ResistanceResponse` 中补 `ct` 与 `wetted_surface_m2`；
- payload 同时发送 `speed` 与 `speed_ms` 两种拼写，兼容线上/工作树两套 schema；
- 几何改从**工况记录本身**取（`geometry_from_case`），不再用按船型硬编码的
  `SHIP_GEOMETRY`（其中 SUBOFF 的 `displacement=0.508`、`cb=0.508` 是把 5415 的
  Cb 串到回转体上）；
- 不适航工况列入 `summary.excluded_cases`，不计入 pass 率。

物理核心抽到无依赖模块 `backend/services/ship_resistance_core.py`，
使审计与测试在无 psycopg2/cryptography 的环境下也能运行。

## 5. 修复后结果

`artifacts/resistance_validation_audit/after_fix.json`：

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 违背 ≥1 不变量的工况 | 12/12 | **0/12** |
| 可计分工况 | 12 | 9（3 条超包络被标注排除） |
| SUBOFF Rt（3 m/s） | 12.5 N（虚假） | 92.5 N（=½ρSV²Ct） |
| KVLCC2 Rt（7.202 m/s） | 550 kN | 2.024 MN |
| 校验器是否可能给出有效判决 | 否（ct 恒为 0） | 是 |

图见 `artifacts/resistance_validation_audit/figures/`：
fig1 表列 Rt 与自身 Ct 隐含 Rt 的对数柱对比（修复前差距 3.7–9.1×）；
fig2 Ct/Cf 合理性带（保留列全通过，说明修复策略正确）；
fig3 部署服务三项不变量偏差。

## 6. 误差判定（对基准逐项）

本研究是"验证链审计"，基准是物理恒等式而非实验值，因此逐项给出**闭合误差**：

| 关键量 | 修复前偏差 | 修复后 | 是否 <3% |
|---|---|---|---|
| SUBOFF Rt vs ½ρSV²Ct | 86%–89%（即差 7–9 倍） | 0（构造闭合，容差 2%） | 是 |
| KVLCC2 Rt 同上 | 73% | 0 | 是 |
| 5415 Fn vs V/√(gL) | 82%（0.061 vs 0.343） | 0 | 是 |
| 5415 Cb vs ∇/(LBT) | 0.071 绝对差（14%） | 0 | 是 |
| 部署服务 Re vs VL/ν | 57% | 未修（工作树外的部署件） | **否** |
| 部署服务 Rt 力闭合 | 99.97% | 未修 | **否** |

算法本身对实验值的误差（ITTC-57 + 经验 Cr 对 SUBOFF/5415/KVLCC2 的 ≥15% 风险）
**本轮不声称改善**：这是方法学问题（Holtrop 类方法对 Cb<0.55 细长体与 Fn>0.4 不适用），
本轮的贡献是让这条链第一次有能力给出**可信**的判决，而不是让判决变好看。

## 7. 注意点与局限

- 修复保留原值审计轨迹，可逐条回溯，未删除任何工况；
- KVLCC2 的 Ct 由文献几何反推的 Rt（2.0 MN @14 kn）与实船功率量级（≈20 MW 轴功率）
  自洽，可作为该族记录的独立旁证；
- 5415 的 3 条超包络工况是**数据录入性质问题**（模型几何 + 实船节速），
  正确做法是补全尺度信息后单列，而不是按 15% 门槛判算法失败；
- 部署在 `:8001` 的阻力实现与工作树代码不一致，且其 Re/Fn/力闭合三项均错。
  **这是本单未修的欠账**（部署件不在本仓 diff 范围内），已用 `--live-url` 审计留证，
  建议下一张单以"线上实现对齐工作树 + 部署后再审计"为主题；
- 审计工具的 I3/I4 带是工程判据（非标准强制），用于拦"数量级错误"，不用于精细判等。

## 8. 复现

```bash
python3 tools/audit_resistance_validation.py                    # 审计数据集（退出码 0=全部合格）
python3 tools/audit_resistance_validation.py \
        --live-url http://127.0.0.1:8001/api/v1 --json artifacts/resistance_validation_audit/baseline.json
python3 tools/fix_benchmark_reference_dataset.py                # 幂等修复
python3 tools/figures_resistance_audit.py                       # 出图
cd backend && python3 -m pytest tests/feat/test_benchmark_reference_consistency.py -q --noconftest
```

测试覆盖（22 项，全绿）：数据集 I1–I5 逐工况、修正轨迹完整性、ITTC-57 闭式对照、
阻力核心力/系数闭合、速度平方标度、Cb 非法输入、`extract_ct` 反推、
校验器端到端计分、不适航工况标注、几何取自工况记录。