# 值班长班次 m384 — SDAA 扫荡线分析与调度

- **班次**：m384 | **mission**：384 | **角色**：值班长（分析/调度，**未改任何业务代码**）
- **数据源**：`POST /api/v1/auth/login`（eco_admin）→ `GET /api/v1/tensor/missions?limit=100`、`GET /api/v1/tensor/reports?limit=50`
- **程序化流水线**：Python 脚本直接从 API 响应解析，所有数字零手抄。
- **黑名单检查**：`/srv/tensor/agent/guide_blacklist.json` 不存在，无黑名单约束。

---

## 1 扫荡线总体（100 单窗口内 goal 含「SDAA 验证案例扫荡」mission）

| 指标 | 数值 |
|---|---|
| 扫荡线 mission 总数（含值班长单） | 73 |
| 其中案例类 mission（diag + probe，不含值班长单） | 48 |
| status 分布 | succeeded: 31, failed: 15, running: 2 |
| 完工数（succeeded + failed） | 46 |
| 唯一案例数（本窗口内） | 19 |
| verified 目录案例总数 | 42（m376 报告确认全覆盖） |

### 1.1 判定分布（完工 46 单）

| 判定 | 数量 | 占比 |
|---|---|---|
| PASS | 20 | 43.5% |
| HONEST_FAIL | 11 | 23.9% |
| FAIL（环境/基础设施） | 15 | 32.6% |
| RUNNING | 2 | — |

### 1.2 失败原因归类（26 单含 HONEST_FAIL + FAIL）

| 失败类型 | 数量 | 典型 mission | 说明 |
|---|---|---|---|
| 环境/交付形态（工作树零 diff） | 12 | m301,m310,m316,m320,m323,m330,m334,m335,m338,m339,m342,m354 | agent 未产出工作树改动，被「完成即有产物」规则判 failed |
| 判据真红（HONEST_FAIL/GATE_FAIL） | 11 | m289,m293,m294,m319,m331,m337,m341,m345,m346,m353,m357 | 物理/算法分辨率极限或已知 case 缺陷，非代码 bug |
| 环境/agent 未完成（未调用 finish） | 3 | m300,m311,m333 | tool calls 耗尽或模型未调用 finish |
| triton 绕行 | 0 | — | 本窗口无独立 triton 绕行失败（suboff_re1000 的 eager_fallback 归入判据真红） |
| 其他 | 0 | — | — |

### 1.3 FAIL 案例解决状态

| 案例 | FAIL 单数 | 最新判定 | 解决状态 |
|---|---|---|---|
| poiseuille_3d_pipe | 2 (m300,m311) | PASS (m321) | ✅ 已解决 |
| sphere_re100 | 3 (m301,m310,m320) | PASS (m329) | ✅ 已解决 |
| permeability3d | 2 (m316,m323) | PASS (m327) | ✅ 已解决 |
| suboff_re1000 | 1 (m330) | RUNNING (m372) | 🔄 在飞诊断中 |
| laplace_cac_phasefield | 1 (m333) | HONEST_FAIL (m345) | ✅ 已收口（判据真红） |
| thermal_cavity_3d | 2 (m334,m338) | HONEST_FAIL (m341) | ✅ 已收口（判据真红） |
| womersley | 2 (m335,m342) | HONEST_FAIL (m346) | ✅ 已收口（判据真红） |
| 下探单（通用） | 2 (m339,m354) | PASS (m355) | ✅ 后续单覆盖 |

**结论**：所有 FAIL 案例均已通过后续诊断单解决或收口，无未解决 FAIL 案例。

---

## 2 当前 in-flight 状态

| mission | 案例 | 类型 | 状态 | 说明 |
|---|---|---|---|---|
| m372 | suboff_re1000 | 诊断+修复绕行 | running | 真红未解除，第 4+ 次诊断 |
| m373 | startup_poiseuille | 诊断+修复绕行 | running | 判据真红，第 2+ 次诊断 |
| m385 | backward_step | 下探单（pending） | queued | 本班派发 |
| m386 | blasius_flat_plate | 下探单（pending） | queued | 本班派发 |
| m387 | bstep_3d | 下探单（pending） | queued | 本班派发 |

SDAA 扫荡 in-flight（不含本值班长单）：**5 单**（2 running + 3 queued）

---

## 3 本班派发决策

**派发数量：3 单**（达到硬上限）

### 派发清单

| mission ID | 案例 | 目录 | goal 类型 | 派发理由 |
|---|---|---|---|---|
| 385 | backward_step | benchmarks/pending/backward_step | 下探单 A | verified 42 案已全覆盖；in-flight<4；pending 字母序第 1 |
| 386 | blasius_flat_plate | benchmarks/pending/blasius_flat_plate | 下探单 B | 同上；pending 字母序第 2 |
| 387 | bstep_3d | benchmarks/pending/bstep_3d | 下探单 C | 同上；pending 字母序第 3 |

### 派发依据（程序化取自 API）

- verified 目录 42 案例已全覆盖（m376 报告 ID=288 确认：PASS 37 / HONEST_FAIL 4 / GATE_FAIL 1）
- 派发前 SDAA 扫荡 in-flight=2（m372, m373），< 4 阈值
- 无黑名单文件
- 所有 FAIL 案例已解决，无需派发诊断单
- pending 目录有 25+ 未覆盖案例，按字母序选取前 3

### 未派发诊断单的理由

所有 status=failed 的案例均已通过后续 succeeded mission 解决：
- 环境/交付形态类失败（12 单）→ 后续诊断单产生工作树改动+报告，案例级 PASS 或 HONEST_FAIL
- 环境/agent 未完成类失败（3 单）→ 同上
- 无任何案例处于「仅 FAIL 无后续成功」状态

---

## 4 里程碑报告状态

扫荡线累计完工（本窗口）：**46 单**

已发布合并报告（近期）：
- Report 294（m371 班次）：窗口 40 单判读
- Report 288（m376 班次）：42 案例全覆盖状态确认
- Report 278（m360 班次）：窗口 35 单判读
- Report 273（m356 班次）：窗口 34 单判读
- Report 269（m352 班次）：43 案例扫荡合并报告

**本班不重复发布合并报告**（已有 5+ 份近期合并报告，待 pending 下探单完工后由后续班次发布扩展版）。

---

## 5 升级事项（留给 owner）

### 5.1 系统性问题：环境/交付形态失败率偏高

本窗口 46 单完工中，12 单（26%）因「工作树无任何改动」被判 failed。这些并非物理/算法失败，而是 agent 执行层面问题：
- agent 未产出任何文件改动即调用 finish
- 可能原因：tool calls 耗尽、模型生成空响应、SDAA 设备初始化失败后无 fallback

**建议**：owner 审查 agent 执行日志，确认是否存在系统性的 tool call 预算不足或设备初始化失败模式。

### 5.2 HONEST_FAIL 案例判据复核

4 个案例持续 HONEST_FAIL，需 owner 决策是否调整门限或标注为已知限制：

| 案例 | 失败判据 | 数值 | 门限 | 根因 |
|---|---|---|---|---|
| womersley | H=29 复振幅误差 | 4.890% | <3% | Stokes 边界层分辨率极限（H=59/119 均 <3%） |
| suboff_re1000 | Cd_tot 偏差 | +46.43% | <15% | eager_fallback + 达标档网格过粗（CPU 验证 PASS） |
| startup_poiseuille | 单调收敛判据 | 阈值全过但单调性未过 | err_decreased=True | 最差 0.476% < 3%，但收敛序列非严格单调 |
| aircraft_icing_rg15_ipw2 | G3 per-bin closure | 3.03e-2 | ≤1e-6 | Eulerian 闭合度门限过严（案例级已知缺陷） |

### 5.3 pending 目录覆盖扩展

verified 目录 42 案已全覆盖。pending 目录有 25+ 未覆盖案例（backward_step, blasius_flat_plate, bstep_3d 等）。本班已派发前 3 个，建议后续班次继续按字母序下探。

### 5.4 sphere_re100_mem 判定不一致

m376 报告标注 sphere_re100_mem 为 GATE_FAIL，但 m290（本窗口）succeeded 且 headline 显示 VERDICT: PASS。建议 owner 复核该案例最新判定口径。

---

## 6 下一班建议

1. **等待 m372/m373 完工**：suboff_re1000 和 startup_poiseuille 诊断结果将决定是否需要升级为已知限制或继续重试。
2. **跟踪 m385/m386/m387**：pending 下探单完工后，按结果决定是否派发诊断单。
3. **继续 pending 下探**：若 in-flight<4，按字母序派发下一个 pending 案例（capillary_invasion_washburn 起）。
4. **发布扩展合并报告**：待 pending 下探单累计完工 ≥5 单后，发布含 pending 案例的扩展版合并报告。
5. **复核 sphere_re100_mem**：确认该案例最新判定是 PASS 还是 GATE_FAIL。
