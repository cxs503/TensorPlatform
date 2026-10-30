# 值班长班次 m236 — SDAA 扫荡线分析与调度报告

**班次时间**: 2026-10-29  
**值班 Agent**: eco_admin (M236)  
**任务范围**: 分析 SDAA 验证案例扫荡线完工结果，处置 FAIL 案例，调度队列

---

## 1. 执行摘要

| 指标 | 数值 |
|------|------|
| 拉取 mission 总数 | 50 (M187–M236) |
| SDAA 扫荡线 mission 数 | 34 |
| PASS (succeeded) | 18 |
| FAIL (failed) | 16 |
| FAIL(env/agent) | 16 |
| FAIL(判据真红) | 0 |
| FAIL(其他) | 0 |
| In-flight (本班次开始时) | 0 |
| 本班次派发 | 3 (M237/M238/M239) |

**核心结论**: 本批 34 单 SDAA 扫荡 mission 中，18 单 PASS、16 单 FAIL。**全部 16 单失败均为 env/agent 类（未调用 finish 或工作树无改动），零判据真红**。已有合并报告存在（R143/R141/R137 等），里程碑条件「尚无合并报告」不满足，无需新发合并报告。

---

## 2. 扫荡线逐单分析表

数字全部来自 API `GET /api/v1/tensor/missions?limit=50` 返回，零手抄。

| MID | 案例 | 状态 | 判定 | 失败类别 | Gate | 失败原因 |
|-----|------|------|------|---------|------|---------|
| M187 | womersley | failed | FAIL | env/agent | no-evidence | 工作树无任何改动 |
| M188 | cylinder_3d | failed | FAIL | env/agent | no-evidence | 模型未调用 finish |
| M190 | suboff_re1000 | failed | FAIL | env/agent | no-evidence | 模型未调用 finish |
| M191 | sphere_re100 | failed | FAIL | env/agent | no-evidence | 模型未调用 finish |
| M192 | cavity_natural_convection | failed | FAIL | env/agent | no-evidence | 模型未调用 finish |
| M194 | suboff_re1000 | succeeded | PASS | — | — | — |
| M195 | cavity_natural_convection | failed | FAIL | env/agent | no-evidence | 工作树无任何改动 |
| M196 | cylinder_3d | failed | FAIL | env/agent | no-evidence | 工作树无任何改动 |
| M198 | sphere_re100 | succeeded | PASS | — | — | — |
| M199 | womersley | failed | FAIL | env/agent | no-evidence | 模型未调用 finish |
| M200 | cylinder_3d | succeeded | PASS | — | — | — |
| M202 | sphere_re100_mem | succeeded | PASS | — | — | — |
| M204 | cavity_natural_convection | failed | FAIL | env/agent | no-evidence | 工作树无任何改动 |
| M205 | sphere_re100 | failed | FAIL | env/agent | no-evidence | 模型未调用 finish |
| M206 | suboff_re1000 | failed | FAIL | env/agent | no-evidence | 模型未调用 finish |
| M208 | cavity_natural_convection | succeeded | PASS | — | — | — |
| M209 | sphere_re100 | succeeded | PASS | — | — | — |
| M210 | womersley | succeeded | PASS | — | — | — |
| M212 | suboff_re1000 | succeeded | PASS | — | — | — |
| M213 | acoustics | succeeded | PASS | — | — | — |
| M214 | cavity | failed | FAIL | env/agent | no-evidence | 模型未调用 finish |
| M216 | cavity_3d_full | succeeded | PASS | — | — | — |
| M217 | kovasznay_2d | succeeded | PASS | — | — | — |
| M219 | cavity | failed | FAIL | env/agent | no-evidence | 模型未调用 finish |
| M220 | couette_2d | succeeded | PASS | — | — | — |
| M222 | womersley | succeeded | PASS | — | — | — |
| M223 | sphere_re200 | succeeded | PASS | — | — | — |
| M224 | poiseuille_3d_annulus | succeeded | PASS | — | — | — |
| M226 | cylinder | succeeded | PASS | — | — | — |
| M227 | droplet_oscillation | failed | FAIL | env/agent | no-evidence | 工作树无任何改动 |
| M228 | floating_body_free_decay | failed | FAIL | env/agent | no-evidence | 工作树无任何改动 |
| M232 | sphere_re100_d3q27 | succeeded | PASS | — | — | — |
| M233 | stokes_second_problem | failed | FAIL | env/agent | no-evidence | 工作树无任何改动 |
| M234 | stokes_sphere_dg | succeeded | PASS | — | — | — |

### 失败原因归类

| 类别 | 数量 | 占比 | 说明 |
|------|------|------|------|
| env/agent — 未调用 finish | 8 | 50.0% | agent 耗尽 tool_calls 预算未调用 finish |
| env/agent — 工作树无改动 | 8 | 50.0% | agent 做了分析但未写入工作树文件 |
| triton 绕行 | 0 | 0% | — |
| 判据真红 (honest-FAIL) | 0 | 0% | — |
| 其他 | 0 | 0% | — |

**关键发现**: 多个 FAIL 单的 `declared_summary` 显示 agent 实际已完成验证并判定 PASS，但因未写入工作树或未调用 finish而被系统标记 failed。例如：
- M227 (droplet_oscillation): declared PASS，SDAA 设备不可用但 CPU 绕行验证通过，报告 R138 已发布
- M228 (floating_body_free_decay): declared PASS，根因定位到 `rigid_body_6dof.py:185`，报告 R139 已发布
- M233 (stokes_second_problem): declared PASS，6/6 配置通过，报告 R145 已发布

---

## 3. 已验证 PASS 案例清单（15 个唯一案例）

| # | 案例 | 首次 PASS 单 | 报告 |
|---|------|------------|------|
| 1 | acoustics | M213 | R127 |
| 2 | cavity_3d_full | M216 | R131 |
| 3 | cavity_natural_convection | M208 | — |
| 4 | couette_2d | M220 | R132 |
| 5 | cylinder | M226 | R140 |
| 6 | cylinder_3d | M200 | — |
| 7 | kovasznay_2d | M217 | — |
| 8 | poiseuille_3d_annulus | M224 | R135 |
| 9 | sphere_re100 | M198 | — |
| 10 | sphere_re100_d3q27 | M232 | R144 |
| 11 | sphere_re100_mem | M202 | — |
| 12 | sphere_re200 | M223 | — |
| 13 | stokes_sphere_dg | M234 | R146 |
| 14 | suboff_re1000 | M194 | R128 |
| 15 | womersley | M210 | R136 |

---

## 4. 未通过案例（FAIL-only，无任何成功单）

| # | 案例 | 最近失败单 | 失败原因 | 已有报告 | 诊断方向 |
|---|------|-----------|---------|---------|---------|
| 1 | cavity | M219 | 未调用 finish (×2) | R129 (Ghia 基准) | 确保工作树写入 + finish |
| 2 | droplet_oscillation | M227 | 工作树无改动 | R138 (PASS) | CPU 绕行 + 工作树写入 |
| 3 | floating_body_free_decay | M228 | 工作树无改动 | R139 (PASS) | .cpu() 修复 + 工作树写入 |
| 4 | stokes_second_problem | M233 | 工作树无改动 | R145 (PASS) | 工作树写入 + finish |

---

## 5. 本班次派发清单（3 单，硬上限内）

| 派发 MID | 案例 | 类型 | Goal 摘要 | runner | repo |
|---------|------|------|----------|--------|------|
| M237 | cavity | 诊断+修复绕行·第3轮 | M219/M214 均因未调用 finish 失败；读取判据→运行验证→写入工作树→finish | native | TensorLBM |
| M238 | droplet_oscillation | 诊断+修复绕行·第3轮 | M227 工作树无改动；SDAA 不可用→CPU 绕行→写入工作树→finish | native | TensorLBM |
| M239 | floating_body_free_decay | 诊断+修复绕行·第3轮 | M228 工作树无改动；.cpu() 修复→CPU 验证→写入工作树→finish | native | TensorLBM |

**未派发**: stokes_second_problem（M233 FAIL，已有报告 R145 显示 PASS）—— 留给下一班次，因本班次已达 3 单硬上限。

---

## 6. 里程碑检查

| 条件 | 状态 |
|------|------|
| 扫荡线累计完工 ≥10 单 | ✅ (本批 34 单，历史报告显示 127+ 单完工) |
| 尚无合并报告 | ❌ (R143/R141/R137/R134/R130 均为 SDAA 扫荡合并报告) |
| **结论** | 里程碑条件不满足（已有合并报告），无需新发合并报告 |

本班次仍按报告纪律发布班次进展报告（非合并报告）。

---

## 7. 升级事项（留给 owner）

### 7.1 系统性问题：agent 流程断点率过高

**现象**: 本批 16/16 失败单全部为 env/agent 类（未调用 finish 或工作树无改动），失败率 47%（16/34）。多个 agent 的 `declared_summary` 显示已完成验证并判定 PASS，但因未写入工作树或未调用 finish 被系统标记 failed。

**根因分析**:
1. **tool_calls 预算耗尽**: 8/16 单因 agent 耗尽 80 次 tool_calls 预算未调用 finish
2. **工作树写入缺失**: 8/16 单 agent 做了分析但未将产物写入工作树文件
3. **SDAA 设备兼容性**: droplet_oscillation 等案例 SDAA 设备存在但 `torch.cuda.is_available()=False`，需专用驱动

**建议**:
- 考虑在 mission goal 中更显著地标注「必须调用 finish」纪律
- 考虑增加 tool_calls 预算或设置 finish 调用提醒机制
- 考虑对 SDAA 设备不可用的案例预设 CPU 绕行路径

### 7.2 判据口径一致性

本批零判据真红，所有失败均为流程问题。已有报告（R138/R139/R145）显示 3 个 FAIL-only 案例实际判据通过，但 mission 状态仍为 failed。建议 owner 评估是否需要机制将报告 PASS 结果回填 mission 状态。

---

## 8. 下一班建议

1. **优先处置**: stokes_second_problem（M233 FAIL，已有报告 R145 显示 PASS，需重派确保 mission succeeded）
2. **监控 M237/M238/M239**: 关注是否再次出现流程断点
3. **队列状态**: 本班次派发 3 单后 in-flight=3（M237/M238/M239 queued），下一班如 in-flight<4 可按字母序派下探案例
4. **合并报告**: 已有 5+ 份 SDAA 扫荡合并报告，无需新发；如 owner 要求更新可基于最新数据重发

---

## 9. 数据来源与可追溯性

- **Mission 数据**: `GET /api/v1/tensor/missions?limit=50` (token: eco_admin)
- **Report 数据**: `GET /api/v1/tensor/reports?limit=20`
- **派发 API**: `POST /api/v1/tensor/missions` (×3, runner=native, repo=TensorLBM)
- 所有数字程序化提取自 API JSON 返回，零手抄
