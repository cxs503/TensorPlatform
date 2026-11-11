# 交差审计：m15121 KPI-REPRO-95（复现 ≥95%）

**审计单**：m15150（board-audit）· **对象**：m15121（TensorPlatform，succeeded，PR#1228 merged）
**基准裁判**：`tensor_benchmark_cases WHERE anchor_slug='KPI-REPRO-95'` → **0 行**（锚点未登记裁判表）
**结论**：三件套 2/3 实证成立（工件✓、repro_cmd✓），裁判表空 + KPI 未达标（74.39%<95%）→ **判级 L2**，verdict 已落表（id=238）。

## 1. 案例介绍

m15121 是「只计量不改进」的 KPI 计量单：把 KPI-REPRO-95（复现官记录通过率）现值从真实数据算出，
交付可复算的现值与缺口台账。口径 = 「复现任务 succeeded 且工件在」/ 复现任务总数，目标 95%。
mission goal 明示「KPI 消账门=测量值达标（非本单 succeeded）——本单交付的是真实现值，好看难看都要如实」。

## 2. 怎么做的（审计方法）

1. **取证**：读 m15121 工作树 `artifacts/kpi_repro_95/`（12 文件）、`tensor_reports` id=14250、
   `tensor_dev_missions` id=15121 result。
2. **独立复算**（审计核心，零手抄）：
   - 从冻结快照 `rows.jsonl`（87 行，T0=2026-11-11 19:29:52）独立重算：
     **P=82, succeeded=69, pass=61/82=74.3902%, S1=35/35, with_report=68** —— 与报告逐项一致（0.00% 偏差）。
   - 对 live DB 重跑 `measure.sql` Q1：因 verdicts join 未按 T0 截断，全集漂移（82→86/87），
     属报告已披露的在线系统漂移，非缺陷。
3. **repro_cmd 可执行性**：`python3 runs/m15121_kpi_repro/measure_kpi_repro.py --out <tmp>` → **exit=0**，
   新快照 75.58%（65/86），漂移符合预期；`verify_kpi_repro.py` 对新快照 14/19（5 项失败全为 measure/verify
   间隔内的 live 漂移：87vs86 / 74vs73 / 42vs41），原 T0 快照 19/19 有效。
4. **基准裁判**：`tensor_benchmark_cases` 对 KPI-REPRO-95 **0 行**（全表 13 个 anchor 无此 slug）——
   裁判表无法确认；报告 §6 的「0.00% 偏差」是对**独立 SQL 复算**（自指），非外部参考值。

## 3. 计算结果（审计复算，程序化）

| 量 | 报告值 | 审计复算 | 偏差 |
|---|---|---|---|
| 主口径通过率 | 74.39%（61/82） | 74.3902%（61/82） | 0.00% |
| 分母 | 82 | 82 | 0 |
| succeeded | 69 | 69 | 0 |
| S1 子集 | 35/35 | 35/35 | 0 |
| 上界（+报告通道） | 82.93%（68/82） | 68/82 | 0 |
| 缺口 | 20.61 pp | 20.6098 pp | 0.00% |
| verify 检查 | 19/19 | 19/19 @T0（新快照 14/19 漂移） | — |

## 4. 结果图

纯后端/计量审计任务，无场量云图；图=审计复算核对表（上表）+ 原报告 4 图
（`artifacts/kpi_repro_95/fig1..fig4.png`，已核存在且非空）。无新图理由：本单为数据管道审计，无物理场量。

## 5. 注意点

1. **裁判表空**：`tensor_benchmark_cases` 无 KPI-REPRO-95 锚点 → 「基准判据是唯一裁判」在此锚点**无裁判可裁**；
   审计改以冻结快照独立复算为准。报告自身已披露「口径指向的通道是空的」。
2. **KPI 未达标**：74.39% < 95%（缺口 20.61pp）。mission 标 succeeded 指「计量完成」，KPI 缺口如实披露，
   非虚标（goal 已声明消账门=测量值达标）。
3. **漂移**：在线系统，measure 与 verify 间隔内新增终态任务/判定 → 复跑 verify 14/19（非缺陷）。
4. **自指复算**：0.00% 偏差是对同一数据的 SQL 两写法交叉，非外部参考值验证。

## 6. 误差判定（逐项显式回答是否 <3%）

| 关键量 | 基准/源 | 实测 | 相对偏差 | 是否 <3% |
|---|---|---|---|---|
| 主口径通过率 | 报告 74.39% | 74.3902%（rows.jsonl 复算） | 0.00% | ✅ 是 |
| 分母 | 82 | 82 | 0 | ✅ 是 |
| succeeded | 69 | 69 | 0 | ✅ 是 |
| 缺口 | 20.61 pp | 20.6098 pp | 0.00% | ✅ 是 |
| KPI 达标 | 95% | 74.39% | -20.61 pp | ❌ 否（**未达标**，如实） |

**是否所有关键量 <3%**：对**计量精度**（通过率/分母/分子/缺口）是（0.00%）；对 **KPI 达标**否（未达标，非误差）。

## 7. 判级与落表

- **级别 = L2**（独立功能实现：输入 DB → 计算通过率 → 输出工件+报告）。
  - 非 L3：未接入平台主流程（无 integration_callers，独立只读脚本）。
  - 非 L4：裁判表 `tensor_benchmark_cases` 对此锚点 0 行（未登记）+ KPI 未达标 + 自指复算非外部参考。
  - 附百页报告不改变级别；级别=功能实质。
- **落表**：`INSERT tensor_maturity_verdicts (repo='TensorPlatform', scope='m15121 KPI-REPRO-95',
  mission_id=15150, level=2, evidence=<JSON 真实指针>, verdict_by='board-audit')` → **id=238, gate_state=pending**。
- **evidence 键**：impl_files / entry / run_artifacts / tests / integration_callers(none) /
  benchmark(0 行) / repro_cmd(exit=0) / external_anchor(95% vs 74.39%) —— 全部真实指针。
- **maturity_gate**：verdict 落表后由平台自动裁定（confirmed=消账 / reopened=返修）。
