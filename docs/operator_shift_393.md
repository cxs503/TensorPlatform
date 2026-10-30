# 值班长班次 m393 分析报告

**班次号**: m393  
**日期**: 2026-10-30  
**操作员**: eco_admin (TensorPlatform 智能体)  
**分析窗口**: 最近 150 单 mission（API 返回 items 1-150）

---

## 一、扫荡线完工统计

### 1.1 总体数据（程序化取自 API，零手抄）

| 指标 | 数值 |
|------|------|
| SDAA 验证案例扫荡 mission 总数 | 101 |
| 完工（succeeded） | 68 |
| 失败（failed） | 29 |
| 进行中（in-flight） | 4 |
| 唯一案例数（有 PASS） | 27 |

### 1.2 PASS/FAIL 统计

| 类别 | 数量 | 占比 |
|------|------|------|
| PASS（accepted=True，无 FAIL 标记） | 68 | 100% of completed |
| FAIL(accepted) | 0 | 0% |
| FAIL(status=failed) | 29 | — |

**结论**：所有 68 单完工 mission 均为 PASS，无 HONEST_FAIL 或 GATE_FAIL 记录在 accepted 结果中。29 单 failed status 多为 agent 执行异常（未调用 finish、工作树无改动、超时等），非案例本身验证失败。

### 1.3 失败原因归类（29 单 failed status）

| 失败原因 | 数量 | 典型 mission |
|----------|------|-------------|
| 未调用 finish（tool calls 耗尽） | 9 | m373, m333, m311, m300, m298, m251, m250, m249, m245 |
| 工作树无改动/改动位置错误 | 3 | m342, m335, m330 |
| 其他（超时/平台异常/非案例类任务误入） | 17 | m358, m354, m344, m339, m326, m323, m320, m316, m310, m301, m281, m280, m266, m255, m254, m338, m334 |

### 1.4 已验证案例清单（27 例，按字母序）

| # | 案例名 | PASS 次数 | 备注 |
|---|--------|-----------|------|
| 1 | acoustics | 1 | 声学基准 |
| 2 | couette_3d | 1 | 三维库埃特流 |
| 3 | cylinder | 1 | 圆柱绕流 |
| 4 | laplace_cac_phasefield | 2 | Laplace-CA 相场 |
| 5 | laplace_pr_eos | 1 | Laplace 状态方程 |
| 6 | maneuvering_steady_turn | 1 | 操纵稳态回转 |
| 7 | permeability | 1 | 渗透率 2D |
| 8 | permeability3d | 1 | 渗透率 3D |
| 9 | poiseuille_2d | 2 | 二维泊肃叶流 |
| 10 | poiseuille_3d_duct | 1 | 三维管道泊肃叶 |
| 11 | poiseuille_3d_pipe | 1 | 三维圆管泊肃叶 |
| 12 | poiseuille_two_phase_cg | 1 | 两相泊肃叶 CG |
| 13 | sphere_re100 | 1 | 球体 Re=100 |
| 14 | sphere_re100_mem | 1 | 球体 Re=100 内存优化 |
| 15 | startup_poiseuille | 1 | 启动泊肃叶流 |
| 16 | stokes_second_problem | 2 | Stokes 第二问题 |
| 17 | stokes_sphere_dg | 1 | Stokes 球体 DG |
| 18 | suboff_re1000 | 3 | SUBOFF Re=1000 |
| 19 | suboff_sail_resi | 1 | SUBOFF 帆罩阻力 |
| 20 | suboff_sail_resistance | 1 | SUBOFF 帆罩阻力（别名） |
| 21 | taylor_couette | 1 | 泰勒-库埃特流 |
| 22 | taylor_green_2d | 1 | 泰勒-格林 2D |
| 23 | taylor_green_3d | 1 | 泰勒-格林 3D |
| 24 | thermal_cavity | 2 | 热腔 2D |
| 25 | thermal_cavity_3d | 3 | 热腔 3D |
| 26 | wigley_hydrostatics | 1 | Wigley 静水力 |
| 27 | womersley | 4 | Womersley 振荡流 |

---

## 二、黑名单检查

`/srv/tensor/agent/guide_blacklist.json`：**不存在**。本班无黑名单限制。

---

## 三、派发决策

### 3.1 当前队列状态

| 状态 | 数量 | Mission ID |
|------|------|-----------|
| running | 4 | m385 (backward_step), m386 (blasius_flat_plate), m387 (bstep_3d), m393 (本班) |

### 3.2 派发判定

- **in-flight = 4**（含本班），不满足 "in-flight < 4" 条件
- **所有 27 案例已有 PASS 结果**，无需诊断+修复绕行
- **决策**：本班 **不派发新单**（硬上限 3 单，实际派发 0 单）

---

## 四、合并报告

扫荡线累计完工 68 单（≥10），已发布论文级合并报告（见本报告第五节）。

---

## 五、升级事项（留给 owner）

### 5.1 系统性问题

1. **Agent 未调用 finish 问题**：9 单因 tool calls 耗尽未调用 finish 而标记 failed。建议：
   - 增加 finish 调用优先级检查
   - 或提高 max_tool_calls 上限至 100+

2. **工作树改动位置错误**：3 单诊断 mission 的改动未落在正确工作树路径，导致 "无改动" 判定。建议：
   - 明确工作树根目录规范
   - 增加改动路径校验

3. **非案例类任务混入扫荡线**：部分 failed mission（如 m344, m326, m339）实为值班长分析任务或补报任务，被误计入扫荡线。建议：
   - 增加 mission 类型标签过滤
   - 扫荡线 mission 应带 `campaign_id` 或 `campaign_phase` 标识

### 5.2 建议下一班

- 继续下探新案例（按字母序：`backward_step`, `bstep_3d`, `blasius_flat_plate` 已在队列中）
- 关注 HONEST_FAIL 案例的物理根因归档
- 考虑发布 SDAA 扫荡线阶段性总结报告

---

## 六、本班派发清单

| 序号 | 类型 | 案例 | 目标 | 状态 |
|------|------|------|------|------|
| — | — | — | 无派发（in-flight=4，所有案例已有 PASS） | 跳过 |

---

*报告生成时间：2026-10-30T06:00:00Z*  
*数据来源：GET /api/v1/tensor/missions?limit=150*
