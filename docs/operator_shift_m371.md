# 值班长班次 m371 — SDAA 扫荡线分析与调度

- **班次**：m371 | **mission**：371 | **角色**：值班长（分析/调度，**未改任何业务代码**）
- **数据源**：`POST /api/v1/auth/login`（eco_admin）→ `GET /api/v1/tensor/missions?limit=50`（落盘 `/tmp/missions.json`）、`GET /tensor/reports?limit=100`、`GET /tensor/missions?limit=20`（in-flight 计数）
- **程序化流水线**：`agent_work/m371/analyze.py` → `agent_work/m371/ledger.json` → `agent_work/m371/figs.py`（图）→ `agent_work/m371/docgen.py`（本文件）。本文件内所有数字均为脚本注入，零手抄。

---

## 1 扫荡线总体（本窗口 50 单内的 goal 含「SDAA 验证案例扫荡」mission）

| 指标 | 数值 |
|---|---|
| 扫荡线 mission（含本班 371 自身 running、含值班长单） | 40 |
| 其中案例类 mission（diag + probe，不含值班长单） | 28 |
| status 分布 | {"succeeded": 27, "failed": 12, "running": 1} |
| 案例类判定分布（mission 级） | {"GATE_FAIL": 10, "PASS": 13, "HONEST_FAIL": 5} |
| 唯一案例数 | 14 |
| 案例最新判定分布 | {"PASS": 9, "HONEST_FAIL": 4, "GATE_FAIL": 1} |
| 案例最新归因分布 | {"通过": 9, "triton 绕行+判据真红": 1, "判据真红": 3, "其他(agent 基础设施)": 1} |

**最新判定非 PASS 的案例**（真红/基础设施缺口，共 5 个）：`aircraft_icing_rg15_ipw2`、`sphere_re100_mem`、`startup_poiseuille`、`suboff_re1000`、`womersley`
**最新判定 PASS 的案例**（9 个）：`acoustics`、`aircraft_icing_naca0012_rime`、`cavity`、`laplace_cac_phasefield`、`permeability3d`、`sphere_re100`、`stokes_second_problem`、`thermal_cavity`、`thermal_cavity_3d`

---

## 2 逐案例判定表（案例级，最新完工 mission 为准）

| 案例 | 窗口内尝试 | 最新 mission | 最新判定 | 归因类别 | API 证据摘录（零手抄） |
|---|---|---|---|---|---|
| `acoustics` | 3 | m355 | **PASS** | 通过 | SDAA device portability verified: acoustics benchmark (D2Q9 plane wave) — 3 grid sizes, bitwise identical CPU  |
| `aircraft_icing_naca0012_rime` | 1 | m347 | **PASS** | 通过 | 1. 读取result.json + 9案evidence JSON，程序化提取所有门判据数字（零手抄）；2. 生成4张matplotlib图(beta曲线/网格收敛/LWC线性/确定性)；3. POST /api/v1 |
| `aircraft_icing_rg15_ipw2` | 2 | m357 | **HONEST_FAIL** | 判据真红 | SDAA device validation: torch.sdaa.is_available()=True, device=/dev/tcaicard14, torch_sdaa=3.1.1a3+gite3971b9. |
| `cavity` | 3 | m351 | **PASS** | 通过 | cavity_re100: nx=32 max_abs_dev=2.25%<3% PASS; nx=48 max_abs_dev=1.90%<3% PASS; 收敛序列 2.25%→1.90%→0.75%→0.73% 单 |
| `laplace_cac_phasefield` | 2 | m345 | **PASS** | 通过 | result.json 5/5 臂 PASS; run.py 语法 OK; 网格收敛功能验证通过; 报告已提交 (ID:265) |
| `permeability3d` | 2 | m327 | **PASS** | 通过 | python3 scripts/sdaa_sweep_permeability3d.py (plumbing OK, archived extraction OK); curl POST /tensor/reports  |
| `sphere_re100` | 2 | m359 | **PASS** | 通过 | verify.py 运行通过：ladder within_gate&steady=True, monotone/err/decrease=True, PASS=True |
| `sphere_re100_mem` | 1 | m358 | **GATE_FAIL** | 其他(agent 基础设施) | ## 任务完成：SDAA 验证案例扫荡——sphere_re100_mem 覆盖补全 ### 执行摘要 完成班次 m358 派发的 SDAA 验证案例扫荡任务（下探单 A）。通过程序化集合差分析，识别出 benchmar |
| `startup_poiseuille` | 1 | m353 | **HONEST_FAIL** | 判据真红 | SDAA 实机双网格扫荡 `python run.py scan sdaa_out --device sdaa`（H=59: 52,235 步 / H=119: 212,435 步）；判据复算 `python colle |
| `stokes_second_problem` | 1 | m324 | **PASS** | 通过 | ✅ 读取 README.md 和 result.json 确认判据 ✅ 写独立脚本 scripts/sdaa_sweep_stokes_second_problem.py ✅ TRITON_INTERPRET=1 绕行（ |
| `suboff_re1000` | 2 | m337 | **HONEST_FAIL** | triton 绕行+判据真红 | 1) `python -m pytest tests/test_suboff_re1000_acceptance_gate.py -q` → 10 passed（覆盖 PASS / FAIL_ACCURACY / COA |
| `thermal_cavity` | 1 | m325 | **PASS** | 通过 | ✅ 读取 README.md 和 result.json 确认判据口径 ✅ 编写扫荡脚本（每工况一进程 + 墙钟预算 + 增量落盘） ✅ 程序化提取结果数字（零手抄） ✅ 与基准比较，给出 PASS/FAIL 判定 ✅  |
| `thermal_cavity_3d` | 3 | m341 | **PASS** | 通过 | Python语法检查通过；配置文件JSON验证通过；稳态检测函数单元测试通过；11案结果程序化提取；报告已发布(id:256) |
| `womersley` | 4 | m346 | **HONEST_FAIL** | 判据真红 | - H=29,α=8 complex_rel=4.890% > 3% → HONEST_FAIL 确认 - H=29,α=4 complex_rel=2.934% ≈ 3% → 临界通过 - H=59,119 全部 <3 |

## 3 逐 mission 明细（程序化分类，便于复核）

| mission | 类型 | 案例 | status | 判定 | gate | 归因 |
|---|---|---|---|---|---|---|
| m323 | diag | `permeability3d` | failed | GATE_FAIL | no-evidence | 其他(agent 基础设施) |
| m324 | diag | `stokes_second_problem` | succeeded | PASS | — | 通过 |
| m325 | diag | `thermal_cavity` | succeeded | PASS | — | 通过 |
| m327 | diag | `permeability3d` | succeeded | PASS | — | 通过 |
| m329 | diag | `sphere_re100` | succeeded | PASS | — | 通过 |
| m330 | diag | `suboff_re1000` | failed | GATE_FAIL | no-evidence | 其他(agent 基础设施) |
| m331 | diag | `womersley` | succeeded | HONEST_FAIL | — | 判据真红 |
| m333 | diag | `laplace_cac_phasefield` | failed | GATE_FAIL | no-evidence | 其他(agent 基础设施) |
| m334 | diag | `thermal_cavity_3d` | failed | GATE_FAIL | no-evidence | 其他(agent 基础设施) |
| m335 | diag | `womersley` | failed | GATE_FAIL | no-evidence | 其他(agent 基础设施) |
| m337 | diag | `suboff_re1000` | succeeded | HONEST_FAIL | — | triton 绕行+判据真红 |
| m338 | diag | `thermal_cavity_3d` | failed | GATE_FAIL | no-evidence | 其他(agent 基础设施) |
| m339 | probe | `acoustics` | failed | GATE_FAIL | no-evidence | 其他(agent 基础设施) |
| m341 | diag | `thermal_cavity_3d` | succeeded | PASS | — | 通过 |
| m342 | diag | `womersley` | failed | GATE_FAIL | no-evidence | 其他(agent 基础设施) |
| m343 | diag | `acoustics` | succeeded | PASS | — | 通过 |
| m345 | diag | `laplace_cac_phasefield` | succeeded | PASS | — | 通过 |
| m346 | diag | `womersley` | succeeded | HONEST_FAIL | — | 判据真红 |
| m347 | probe | `aircraft_icing_naca0012_rime` | succeeded | PASS | — | 通过 |
| m349 | probe | `cavity` | succeeded | PASS | — | 通过 |
| m350 | probe | `cavity` | succeeded | PASS | — | 通过 |
| m351 | probe | `cavity` | succeeded | PASS | — | 通过 |
| m353 | diag | `startup_poiseuille` | succeeded | HONEST_FAIL | — | 判据真红 |
| m354 | probe | `aircraft_icing_rg15_ipw2` | failed | GATE_FAIL | no-evidence | 其他(agent 基础设施) |
| m355 | probe | `acoustics` | succeeded | PASS | — | 通过 |
| m357 | diag | `aircraft_icing_rg15_ipw2` | succeeded | HONEST_FAIL | — | 判据真红 |
| m358 | probe | `sphere_re100_mem` | failed | GATE_FAIL | no-evidence | 其他(agent 基础设施) |
| m359 | probe | `sphere_re100` | succeeded | PASS | — | 通过 |

## 4 失败原因归类（mission 级）

| 类别 | mission 数 | 说明 |
|---|---|---|
| 通过（含 PASS，含已解决的历史 HONEST_FAIL） | 13 | 判据全绿 |
| 判据真红 | 4 | 数值/收敛判据超门限，未改口径 |
| triton 绕行 + 判据真红 | 1 | eager_fallback/teco_inductor 下 Cd 偏离基准 46.43% |
| 其他（agent 基础设施） | 10 | `gate=no-evidence`：工作树无改动 / 模型未调用 finish |
| RUNNING | 0 | 在飞 |

> 归类为**解释层**（规则见 `analyze.py`）：先用 mission 自身输出（checks/headline/declared_summary）判 gate 与判定；含其他 mission 编号的"历史句"被剔除，避免 m345 引用 m293 HONEST_FAIL 这类污染。`suboff_re1000` 单独沿用 OVERRIDES，理由见 4.1。

### 4.1 suboff_re1000 的判定说明（不改口径，只如实记录）

- m319：`Cd_tot=0.061495`（+46.43% vs 基准），`compile_status=eager_fallback` → 作为平台 15% 红线**不可接受**；
- m337（最近一次）：10 项验收闸门 pytest 全过，`--stages neutrality` max|Δf|=0.0，`--stages crossdevice` Cd 相对差 2.0e-5，`--stages sweep` L=24 窗均值 0.061424 = m319 −0.12%；
- 结论：m337 证明的是**可复现性与闸门正确性**，并未把 Cd 红线压下去 → 本班记为 `HONEST_FAIL / triton 绕行+判据真红`（保留原口径，不做任何放宽）。

---

## 5 误差判定（逐项 vs 基准，是否 <3%）

| 案例 | 判据量 | 数值 | 是否 <3% | 来源 mission |
|---|---|---|---|---|
| `acoustics` | 衰减率相对误差 max（波速 0.2190%） | 1.3145 | 是（1.3145% vs 3%） | m343 |
| `aircraft_icing_rg15_ipw2` | G3 per-bin Eulerian closure（门限 1e-6 绝对值） | 3.03e-2 | 否（绝对门限 1e-6，实测 3.03e-2；3% 相对口径不适用，已显式声明） | m357 |
| `cavity` | nx=32 max|u| dev（最差档） | 2.25 | 是（2.25% vs 3%） | m351 |
| `sphere_re100` | ladder within_gate / monotone（布尔全绿，无数值百分比） | — | 无百分比可引（见来源报告） | m359 |
| `startup_poiseuille` | 中心线 max 误差（H=119 档） | 0.476 | 是（0.476% vs 3%）**但 H→2H 单调收敛判据红 → 案例判定 FAIL** | m275 |
| `suboff_re1000` | Cd_tot vs 基准（m319 报价，经 m330 goal 引用） | 46.43 | 否（46.43% vs 3%） | m330 |
| `thermal_cavity_3d` | 三条 Ra 阶梯 finest Nu 误差 | 0.55 / 1.47 / 2.37 | 是（最差 2.37% vs 3%） | m334 |
| `womersley` | H=29,α=8 complex_rel（最差档） | 4.890 | 否（4.890% vs 3%） | m346 |

> 提取规则：数值由正则从 API 返回的 mission 文本中抽取（脚本内断言非空），"是否 <3%" 由脚本做数值比较得出。`aircraft_icing_rg15_ipw2` 的 G3 门限是 **1e-6 绝对量**，3% 相对口径不适用，已显式声明，不参与百分比比较。

---

## 6 派发清单（硬上限 3 单，全部 TensorLBM 扫荡类，runner=native）

| 新单 | 案例 | repo / runner | 类型 | 派发依据（API 引文） |
|---|---|---|---|---|
| m372 | `suboff_re1000` | TensorLBM / native | 诊断+修复绕行 | m319 Cd_tot=0.061495（+46.43% vs 基准）；m337 复现 0.061424（−0.12%）仅硬化闸门，红线未解除 |
| m373 | `startup_poiseuille` | TensorLBM / native | 诊断+修复绕行 | m353 `verdict=FAIL(阈值3条全过, err_decreased_H_to_2H=false)`，最差 0.476% < 3% |
| m374 | `aircraft_icing_rg15_ipw2` | TensorLBM / native | 诊断+修复绕行 | m357 `HONEST-FAIL`：G3 per-bin Eulerian closure 3.03e-2 vs ≤1e-6 门限；G11a FAIL |

- 派发后实测：`GET /tensor/missions?limit=20` → [374, 373, 372, 371, 370, 369, 368, 366, 365, 364, 363]（in-flight 11 单）。三单均 `repo=TensorLBM, runner=native, origin=agent`。
- **未派满的逻辑**：本班 3 单已用满上限；**不派下探单**，因为 in-flight=11 ≥ 4，队列不空（触发条件不成立）。
- **未派但在红名单的案例**：`womersley`（m346 已 4 次尝试，H=29/α=8 的 4.890% 属 Stokes 层分辨率物理极限，继续派单边际收益低 → 升级 owner 裁决是否把 H=29 档从判据集中剔除或改用 δ/Δx 自适应档）、`sphere_re100_mem`（唯一 mission m358 为 `gate=no-evidence`，43/43 覆盖结论缺受检证据 → 见升级事项 3）。

---

## 7 下一班建议（按优先级）

1. **盯 m372**（suboff_re1000）：要求给出 CPU L=16/24/32/48 的 Cd(L) 阶梯与单调性；若 L=48 仍 >15%，必须给出"所需 L 与墙钟成本"的量化 honest-FAIL，而不是再复述"coarse grid"。
2. **盯 m373**（startup_poiseuille）：原始判据 `err_decreased_H_to_2H` 与修正窗口两套数字必须并列出现；只报通过的那套视为不合格。
3. **盯 m374**（aircraft_icing_rg15_ipw2）：G3 门限 1e-6 是否与物理量纲匹配 → 若判定门限不合理，只能交证据给 owner，**不得自行改门限**。
4. **补证 `sphere_re100_mem`**：43/43 覆盖的关键一环只有一单 no-evidence 证据，建议下一班在配额内派一单"覆盖复核"（只读 result.json + 复算判据，产出受检工件）。
5. **不要把 43/43 覆盖当作"全 PASS"**：当前案例最新判定 = PASS 9 / 非 PASS 5；合并报告口径应写"覆盖 14 案例（窗口内），其中 5 例仍红"。
6. **口径一致性巡检**：m360 合并报告（r278）把 `suboff_re1000` 记为 PASS 且 HONEST_FAIL=是，与本窗口 m319 的 Cd +46.43% 红线不一致，下一班应统一为"honest-FAIL（真红未解除）"。

---

## 8 升级事项（留给 owner，本班未自行改判据/门限/审批）

1. **agent 基础设施失败率仍是主因**：本窗口 10 单案例 mission 为 `gate=no-evidence`（工作树无改动/未 finish），失败原因归类里占 10/28。建议平台侧在 finish 前强制 `git status --porcelain` 非空校验（现已有文案但不阻断）并对"未 finish"单自动重派。
2. **`suboff_re1000` 红线长期化**：单案例累计尝试次数已高（report 269：11 次），当前结论依赖"eager_fallback + 粗网格"双重归因，缺少 L 收敛阶梯。建议 owner 裁定：要么给卡上更长预算跑 L≥64，要么明确把该案例降级为"成本受限 honest-FAIL"并冻结尝试次数。
3. **`sphere_re100_mem` 覆盖证据缺口**：m358 声明 43/43 全覆盖但 `gate=no-evidence`，`benchmarks/verified` 清单与 result.json 的集合差结论尚无受检工件支撑 → 覆盖基数（43）应视为待复核。
4. **判据口径冲突（不改口径，只报告）**：`startup_poiseuille` 的三条绝对误差阈值全过（最差 0.476%）而单调性判据红；`womersley` H=29/α=8 的 4.890% 属物理分辨率极限。建议 owner 明确"门限集"是否应随分辨率档位分级，避免同一案例反复派单。
5. **在飞 P2 单与扫荡线争卡**：in-flight 11 单中 m363–m370 为 r3-plan P2 上卡任务（cylinder_3d / dam break / 相场 / triton 等价 / 跨卡域分解 / SUBOFF S0 / FVM-BEM），与扫荡诊断单共享 tcaicard，计时类判据（≥3× 加速、≥2.8× 并行效率）可能被 co-tenancy 污染，建议调度侧加设备互斥窗口。

---

## 9 复现

```
cd /srv/tensor/agent/work/m371
curl -s -X POST "$TENSOR_API_BASE/auth/login" -H 'Content-Type: application/json' \
  -d '{"username":"eco_admin","password":"<TENSOR_AGENT_PASS>"}' -o /tmp/login.json
TOKEN=$(sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p' /tmp/login.json)
curl -s "$TENSOR_API_BASE/tensor/missions?limit=50" -H "Authorization: Bearer $TOKEN" -o /tmp/missions.json
python3 agent_work/m371/analyze.py     # -> agent_work/m371/ledger.json
python3 agent_work/m371/figs.py        # -> agent_work/m371/fig/*.png
python3 agent_work/m371/docgen.py      # -> docs/operator_shift_m371.md + /tmp/report.json
```
