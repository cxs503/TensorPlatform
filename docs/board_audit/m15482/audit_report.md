# m15482 交差核验报告 · m15444 / GAP-PP-5-gpu-cluster

> 审计单 m15482（board-audit）· 被审单 m15444（TensorPlatform, PR #1266, commit f97aed771, status=succeeded）
> 判级：**L2**（独立功能实现）· 落表 tensor_maturity_verdicts#327 · 基准裁判：`tensor_benchmark_cases` 该 anchor **0 行**

## 0 结论先行（TL;DR）

| 项 | 结论 | 证据 |
|---|---|---|
| 三件套·run_artifacts | **真**（10/10 文件在，非空） | runs/m15482_audit/triple_check.json |
| 三件套·benchmark ref-vs-got | **数字在容差内**（τ 0.89971 vs ref 0.9，误差 0.0322% < 15%）| 本单独立复跑逐位再现 |
| 三件套·benchmark 正式裁判 | **缺位**：`tensor_benchmark_cases` anchor_slug='GAP-PP-5-gpu-cluster' 查得 0 行（表内 16 例） | triple_check.json benchmark_referee |
| 三件套·repro_cmd | **部分不可复现**：文档命令矩阵 {'grids': '64,128', 'steps': '10,50,200', 'reps': 2} ≠ 工件矩阵 {'configs': 1, 'reps': 1}；且依赖 TensorLBM 未提交补丁 | triple_check.json repro_cmd |
| 判级 | **L2**（非 L3：无平台调用方；非 L4：基准缺位+repro 不符；非 L5：无实验/文献误差对照） | 本报告 §4/§6 |

## 1 案例介绍（①物理背景/几何/参数）

锚点 `GAP-PP-5-gpu-cluster` 解决的是**可微 LBM 路径的设备口径**缺口：JOUR-DIFFLBM 成本表需要一条可复核的设备基线（GPU 节点，或文档化 CPU 基线）。
物理载体=二维 BGK 剪切波衰减：u=U₀·sin(ky)·exp(−νk²t)，ν=(τ−0.5)/3；反问题为已知 τ* 的前向模型造目标场后反解 τ（τ*=0.9 由构造精确已知）。

| 参数 | 值 |
|---|---|
| grid | 64 |
| steps | 50 |
| iters | 60 |
| tau_star | 0.9 |
| tau_got | 0.89971 |
| error_pct | 0.03 |
| wall_s | 33.5047 |
| dtype / device | float64 / CPU（无 CUDA 设备） |
| TensorLBM HEAD | `7f44cd53662c` |

## 2 怎么做的（②方法/流程/工具链）

审计流程（可复跑，脚本即证据）：

1. `docs/board_audit/m15482/verify_triple.py` — 三件套核验：工件存在性/大小、基准裁判 SQL、ref-vs-got 复算、repro_cmd 矩阵解析、pytest 实跑、集成调用方 grep、报告溯源、手抄数字扫描 → `runs/m15482_audit/triple_check.json`
2. `docs/board_audit/m15482/repro_check.py` — **独立复跑**（不采信原单 stdout）：同配置跑两遍 —— A=交付时 TensorLBM 工作树状态；B=把 `input_contract.py` 还原成 `git show HEAD:` 的干净检出状态 → `runs/m15482_audit/repro_check.json`
3. `docs/board_audit/m15482/make_figures.py` — 图读工件生成 → `runs/m15482_audit/fig_tau_recovery.png`
4. `docs/board_audit/m15482/write_verdict.py` — 落表 tensor_maturity_verdicts（evidence 全指针）

## 3 计算结果（③数字表）

### 3.1 交付记录 vs 本单独立复跑

| 状态 | τ_got | ref | 误差 [%] | 15% 判据 | 墙钟 [s] |
|---|---|---|---|---|---|
| 交付记录（results.json） | 0.89971 | 0.9 | 0.0322 | PASS | 33.5047 |
| 本单复跑 A：交付工作树状态 | 0.89971 | 0.9 | 0.03 | PASS | 41.991 |
| 本单复跑 B：TensorLBM 干净 HEAD | 0.55 | 0.9 | 38.89 | FAIL | 17.944 |

→ A 与记录**逐位一致**（τ_got=0.89971 完全相同），说明数字非伪造；B 掉回初始值 τ_init=0.55（误差 38.89%），说明**修复本身未落库**（TensorLBM `git status`: `M src/tensorlbm/input_contract.py`）。

### 3.2 基准裁判记录

```sql
SELECT case_name,metric,tolerance FROM tensor_benchmark_cases WHERE anchor_slug='GAP-PP-5-gpu-cluster'
```
返回 **0 行**。表内 anchor_slug 共 16 例：`H2-FRAZIL-RHEOLOGY`, `GAP-FEM-3D-COHESIVE`, `CASE-EULER-VORTEX`, `H3-ICE-PLATE-FEM`, `GAP-DEM-CLUMP`, `REPRO-FVM-SOD1978`, `XVAL-FEM-TPB1976`, `H6-MATURITY-GATE`, `GAP-FEM-WINKLER`, `H1-BROKEN-ICE-DEM`, `GAP-LBM-MCMP-POISEUILLE`, `GAP-FVM-TWOPHASE`, `CAP-LEDGER-EVIDENCE`, `GAP-DEM-FRICTION`, `GAP-FEM-ELASTOPLASTIC`, `GAP-LBM-SPHERE`。
→ 正式基准裁判**不存在**该锚点的判据行，L4 无法机器判；本轮以 results.json 的 ref-vs-got 复算作为替代裁判（并如实标注非正式基准）。

### 3.3 测试与集成

- `cd /root/tp-agent/work/m15444 && python3 -m pytest tests/test_gap_pp5_cpu_baseline.py -q` → exit=0, `11 passed, 2 warnings in 15.81s`（本单实跑，非引用原单）
- 集成调用方 grep（backend/api）：`（空）` → 不达 L3
- 报告：`tensor_dev_missions.report_id`=NULL；`tensor_reports WHERE mission_id=15444` → 0 行
- 文档段：1 Problem setting & baseline provenance / 2 Method & implementation / 3 Parameters / 4 ref vs got / 5 Wall-clock / throughput table / 6 Failure modes & scope / repro / 7 Verdict

## 4 结果图（④）

![fig_tau_recovery](../../../runs/m15482_audit/fig_tau_recovery.png)

(a) m15444 真实运行工件 `rep_0/convergence_tau.csv` 的 loss 收敛；(b) τ 恢复三方对照（ref / 交付态 / 干净 HEAD，15% 容差带内/外一目了然）；(c) 墙钟复现性：记录 vs 本单复跑。

## 5 注意点（⑤踩坑/局限/适用范围）

| # | 发现 | 严重度 | 证据 |
|---|---|---|---|
| F1 | `tensor_benchmark_cases` 无 `GAP-PP-5-gpu-cluster` 行 → 基准裁判缺位，L4 无法机器判 | 中（阻塞 L4，不阻塞 L2） | triple_check.json benchmark_referee |
| F2 | docs §6.4 repro_cmd 矩阵 {'grids': '64,128', 'steps': '10,50,200', 'reps': 2} ≠ 工件 configs=1, reps=1 → 照抄文档命令复现不出所报工件 | 中 | repro_cmd.matrix_match=False |
| F3 | 核心修复只存在于 TensorLBM 工作树未提交改动；干净检出复跑 τ=0.55/38.89% FAIL | 高（交付价值依赖未入库外部改动） | repro_check.json runs[1] |
| F4 | m15444 未发报告（report_id=NULL，tensor_reports 0 行） | 中（报告纪律） | triple_check.json report |
| F5 | docs §4.3 剪切波三个 ν 数字（0.03496/0.13397/0.29575）全仓只出现在文档里，无产出源 → 手抄，与『所有数字由 results.json 程序化产出』不符 | 中 | handtyped_scan |
| F6 | `bench_diff_lbm_cpu.py` loss 解析正则 `[\deE+-]+` 不含小数点 → `loss_final` 恒 null（`re.search` 实测 None） | 低 | 本报告 §5 复现命令 |
| F7 | `wallclock.csv` 字段名 `median_wall_s` 但 reps=1（无 median）；本单复跑 41.991s vs 记录 33.5047s（1.25×） | 低 | repro_check.json |
| F8 | GPU 节点/集群半边未达成（m15359 cj1081 queued）；按 owner 允许的第二选项『文档化 CPU 基线』口径满足，如实记录 | 需知悉 | docs §6.2/6.3 |
| F9 | **maturity_gate 机器门漏洞（平台侧缺陷）**：`callers_verified(['none: …'], repo)` 取第一段 `none` 去 grep，必然命中 → 审计者如实写 “none: 无调用方” 反被判 L3/L4 通过 | 高（门可被如实陈述绕过） | 本单元测试：`callers_verified(['none: 平台侧无调用方'],'TensorPlatform') == True`；`grep -rl -F none` 命中 agent_work/*.py |

适用范围：单配置（grid=64, steps=50）CPU 基线，仅证明可微路径端到端可跑通且 τ 反解在 15% 容差内；
**不**证明 GPU 设备口径、**不**证明集群吞吐、**不**证明多网格/多步数下的稳定性（扩展矩阵未跑）。

## 6 误差判定（⑥ vs 基准，显式回答是否 <3%）

| 关键量 | ref | got | 误差 | <3%? | 裁判性质 |
|---|---|---|---|---|---|
| τ 恢复（主判据） | 0.9 | 0.89971 | 0.0322% | **是** | ref 由构造精确已知（非实验/文献），容差 15%（平台判据），远优于 3% |
| 本单独立复跑 A | 0.9 | 0.89971 | 0.03% | **是** | 同上，逐位一致 → 无随机性 |
| 本单复跑 B（干净 HEAD） | 0.9 | 0.55 | 38.89% | **否**（38.89%） | 如实报 FAIL：修复未落库 |
| 墙钟吞吐 | 33.5047s | 41.991s | 1.25× 差 | **否** | 单 rep 无方差；不满足 <3% 复现性 |

结论：**功能判据（τ 恢复 0.03% ≪ 3%）达标；可复现性判据（墙钟 1.25×、干净检出 FAIL）不达标** → 故判 L2 而非 L4。

## 7 判级与落表（⑦）

| 级别 | 判据 | 本单裁定 |
|---|---|---|
| L0 文档 | 仅文档 | 否（有真代码+真工件） |
| L1 数学/单元 | 测试非空 | ✅ 11 passed（含 `_as_float` 梯度保护） |
| L2 独立功能 | 真代码 input→compute→output + 工件 + 入口 | ✅ bench→analyze→results.json/csv/png 全链可跑，本单复跑逐位再现 |
| L3 系统集成 | 平台/主求解器调用方 | ❌ grep 空（harness 是独立诊断脚本） |
| L4 基准验证 | 正式 benchmark case + 完整算例 + 可复现 | ❌ 无注册 case；repro_cmd 矩阵不符；依赖未提交外部补丁 |
| L5 科研工程 | 外部锚 + 前后对照问题记录 | ❌ 仅物理锚（BGK 解析），无实验/文献误差对照 |

**声明级别 = 2**；`maturity_gate.judge(2, evidence)` 本单预演返回 `(2, [])`（即机器复核通过 L2）。

## 8 处置（disposition）

- **approve with notes**：m15444 的 L2 实质成立（真代码+真工件+可复现的 τ 恢复），可消账；
- 但须登记三项非阻塞欠账：F2（repro_cmd 标注修正）、F4（原单报告欠账）、F5（§4.3 手抄数字改为程序化或删除）；
- F3 需跨仓跟进（TensorLBM 补丁落库），本单无权改 TensorLBM 仓，仅记录；
- F9 属平台门自身缺陷，建议修复 `callers_verified`：`none`/`n/a`/空 前缀应直接返回 False，并要求 grep 命中路径不得位于 `agent_work/`、`docs/`、`tests/`。

## 附：复现命令

```bash
cd /root/tp-agent/work/m15482
python3 docs/board_audit/m15482/verify_triple.py    # 三件套 → runs/m15482_audit/triple_check.json
python3 docs/board_audit/m15482/repro_check.py      # 独立复跑 A/B → runs/m15482_audit/repro_check.json
python3 docs/board_audit/m15482/make_figures.py     # 图 → fig_tau_recovery.png
python3 docs/board_audit/m15482/write_verdict.py    # 落表 tensor_maturity_verdicts
```

