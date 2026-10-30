# 值班长班次 m348 分析报告

**班次时间**: 2026-10-30  
**班次编号**: m348  
**职责**: 分析与调度（不改业务代码）

---

## 一、扫荡线完工结果分析

### 1.1 数据源
- API: `GET /api/v1/tensor/missions?limit=50`
- 过滤条件: goal 含「SDAA」且含「扫荡」，排除值班长班次自身
- 样本量: 31 单 SDAA 非值班长 mission（数字全部来自 API 返回，零手抄）

### 1.2 状态统计

| 状态 | 数量 | 占比 |
|------|------|------|
| succeeded | 17 | 54.8% |
| failed | 14 | 45.2% |
| **合计** | **31** | **100%** |

### 1.3 失败原因归类

| 类别 | 数量 | 占比 | 说明 |
|------|------|------|------|
| 环境 | 0 | 0% | 无环境相关失败 |
| triton 绕行 | 0 | 0% | 无 triton/CUDA 相关失败 |
| 判据真红 | 0 | 0% | 无真实判据失败 |
| **其他（agent 基础设施）** | **14** | **100%** | 工作树无改动 / 模型未调用 finish |

**关键发现**: 所有 14 单失败均为 agent 基础设施问题，非物理/算法/判据问题。

### 1.4 失败明细（14 单，逐单来自 API result）

| Mission ID | 案例 | 模型 | 失败原因 |
|------------|------|------|----------|
| #300 | poiseuille_3d_pipe | — | 模型未调用 finish |
| #301 | sphere_re100 | — | 工作树无任何改动 |
| #310 | sphere_re100 | — | 工作树无任何改动 |
| #311 | poiseuille_3d_pipe | MiniMax-M2.5-AWQ | 模型未调用 finish (30 tool calls) |
| #316 | permeability3d | — | 工作树无任何改动 |
| #320 | sphere_re100 | — | 工作树无任何改动 |
| #323 | permeability3d | — | 工作树无任何改动 |
| #330 | suboff_re1000 | — | 工作树无任何改动 |
| #333 | laplace_cac_phasefield | MiniMax-M2.5-AWQ | 模型未调用 finish (80 tool calls) |
| #334 | thermal_cavity_3d | — | 工作树无任何改动 |
| #335 | womersley | — | 工作树无任何改动 |
| #338 | thermal_cavity_3d | — | 工作树无任何改动 |
| #339 | (goal 解析异常) | — | 工作树无任何改动 |
| #342 | womersley | qwen3.7-plus | 工作树无任何改动 (37 tool calls) |

**模式分析**:
- "工作树无任何改动" (gate=no-evidence): 12 单 (85.7%)
- "模型未调用 finish": 2 单 (14.3%)
- 高频失败案例: sphere_re100 (3次), permeability3d (2次), thermal_cavity_3d (2次), womersley (2次), poiseuille_3d_pipe (2次)

### 1.5 案例覆盖状态（15 案例，全部 PASS）

| 案例 | 最终状态 | 成功次数 | 失败次数 | Mission IDs |
|------|----------|----------|----------|-------------|
| acoustics | ✅ PASS | 1 | 0 | 343 |
| aircraft_icing_naca0012_rime | ✅ PASS | 1 | 0 | 347 |
| laplace_cac_phasefield | ✅ PASS | 1 | 1 | 345, 333 |
| maneuvering_steady_turn | ✅ PASS | 1 | 0 | 314 |
| permeability | ✅ PASS | 1 | 0 | 315 |
| permeability3d | ✅ PASS | 1 | 2 | 327, 323, 316 |
| poiseuille_3d_duct | ✅ PASS | — | — | (前序班次覆盖) |
| poiseuille_3d_pipe | ✅ PASS | 1 | 2 | 321, 311, 300 |
| sphere_re100 | ✅ PASS | 1 | 3 | 329, 320, 310, 301 |
| stokes_second_problem | ✅ PASS | 2 | 0 | 324, 302 |
| suboff_re1000 | ✅ PASS | 2 | 1 | 337, 330, 319 |
| taylor_green_3d | ✅ PASS | 1 | 0 | 312 |
| thermal_cavity | ✅ PASS | 1 | 0 | 325 |
| thermal_cavity_3d | ✅ PASS | 1 | 2 | 341, 338, 334 |
| womersley | ✅ PASS | 2 | 2 | 346, 342, 335, 331 |

---

## 二、队列状态与处置

### 2.1 当前队列
- **In-flight**: 1 单（本班次 m348）
- **阈值**: in-flight < 4 → 需补充派发

### 2.2 派发决策
**硬上限**: 最多 3 单，只准派 TensorLBM 扫荡类 goal

**决策逻辑**:
1. 所有 15 案例均已 PASS → 无需派诊断修复单
2. In-flight=1 < 4 → 按规则派下探案例
3. 派发 3 单通用扫荡任务，由 agent 自行发现未覆盖案例

### 2.3 派发清单（3 单，已确认 queued）

| 序号 | Mission ID | 目标 | Repo | Runner | Status |
|------|-----------|------|------|--------|--------|
| 1 | **#349** | SDAA 扫荡——继续下探未覆盖案例 | TensorLBM | native | queued |
| 2 | **#350** | SDAA 扫荡——继续下探未覆盖案例 | TensorLBM | native | queued |
| 3 | **#351** | SDAA 扫荡——继续下探未覆盖案例 | TensorLBM | native | queued |

---

## 三、里程碑检查

### 3.1 合并报告状态
- **扫荡线累计完工**: 31 单（≥10 单阈值 ✅）
- **已有合并报告**: 11 份（最新为 m344 班次，report ID=263）
- **结论**: 已有合并报告，本班次不重复发布

---

## 四、升级事项（留给 owner）

### 4.1 🔴 系统性问题：Agent 基础设施失败率 45.2%
- **现象**: 14/31 单失败，全部为 agent 基础设施问题（非物理/算法）
- **根因分类**:
  - 85.7%: 工作树无任何改动 → agent 执行了工具调用但未产生 git diff
  - 14.3%: 模型未调用 finish → agent 流程中断或达到 tool_calls 上限
- **影响**: 浪费计算资源，扫荡周期延长约 1.8x
- **建议**:
  1. 在 agent runner 层增加"工作树 diff 非空"闸门
  2. 对 finish 调用增加强制校验（超时前提醒）
  3. 考虑对高频失败模型（MiniMax-M2.5-AWQ, qwen3.7-plus）降低优先级或增加重试

### 4.2 案例覆盖完整性待确认
- 当前已覆盖 15 个案例，全部 PASS
- 需 owner 确认 TensorLBM benchmarks/verified 目录是否还有未覆盖案例
- 如案例池已耗尽，建议调整扫荡线策略（如增加网格收敛性测试、多精度对比等）

---

## 五、下一班建议

1. **监控派发任务**: 关注 #349/#350/#351 执行情况，如再次出现"工作树无改动"失败，建议暂停扫荡线
2. **agent 基础设施**: 优先解决 45.2% 失败率问题，否则扫荡线效率持续低下
3. **案例池确认**: 与 owner 确认完整案例列表，避免无效派发
4. **性能基线**: 为已 PASS 案例建立性能基线，用于回归检测
5. **模型选择**: 考虑对扫荡任务使用更稳定的模型（如 qwen3.6-plus 在 m302 表现良好）

---

## 六、产物清单

1. ✅ **本文档**: `docs/operator_shift_m348.md`
2. ✅ **派发任务**: 3 单 TensorLBM 扫荡 mission（ID: 349, 350, 351，status=queued）
3. ✅ **进展报告**: 通过 `POST /api/v1/tensor/reports` 发布（论文级，mission_id=348）

---

**班次结论**: 
- 扫荡线整体健康（15 案例全 PASS，无判据真红）
- 主要风险为 agent 基础设施失败率（45.2%，已写入升级事项）
- 已派发 3 单补充队列（#349/#350/#351）
- 无需发布新合并报告（已有 11 份）

**值班长**: tp-arch-4  
**完成时间**: 2026-10-30
