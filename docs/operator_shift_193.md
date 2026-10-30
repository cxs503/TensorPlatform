# 值班长班次 #193 分析报告

## 班次信息

- **班次号**: 193
- **执行时间**: 2026-10-29
- **操作员**: eco_admin (ADMIN)
- **平台**: http://127.0.0.1:8010/api/v1

## 1. 平台登录与数据拉取

- POST /api/v1/auth/login (eco_admin) → 获取 access_token ✅
- GET /api/v1/tensor/missions?limit=100&offset=0 → 100 条 ✅
- GET /api/v1/tensor/missions?limit=100&offset=100 → 93 条 ✅
- 总计拉取: 193 条 mission

## 2. 扫荡线分析

### 筛选条件
- goal 含「SDAA 验证案例扫荡」
- 排除值班长班次报告、补报单

### 统计结果

| 指标 | 数值 |
|------|------|
| SDAA 扫荡 mission 总数 | 69 |
| mission 级 succeeded | 51 |
| mission 级 failed | 18 |
| 独立案例数 | 43 |
| 最新状态 PASS | 38 |
| 最新状态 FAIL | 5 |

### 失败原因归类

| 原因类别 | 计数 | 占比 |
|---------|------|------|
| agent未调用finish | 17 | 94.4% |
| 工作树无改动 | 1 | 5.6% |

### FAIL 案例清单（最新状态）

| 案例 | 最新 mission | 失败原因 | 累计尝试 |
|------|-------------|---------|---------|
| cavity_natural_convection | m192 | agent未调用finish | 4 |
| cylinder_3d | m188 | agent未调用finish | 3 |
| sphere_re100 | m191 | agent未调用finish | 5 |
| suboff_re1000 | m190 | agent未调用finish | 5 |
| womersley | m187 | agent未调用finish | 4 |

### 与上轮报告（m189）对比

| 指标 | m189 报告 | m193 报告 | 变化 |
|------|----------|----------|------|
| 独立案例 | 43 | 43 | - |
| PASS | 42 | 38 | -4 |
| FAIL | 1 | 5 | +4 |

变化原因：m189 后新派发的 5 单（m187/m188/m190/m191/m192）均因 agent 未调用 finish 失败，导致对应案例最新状态由 PASS 变为 FAIL。

## 3. 派发清单

本班次派发 3 单（硬上限），均为 TensorLBM 扫荡类「诊断+修复绕行」：

| 新 mission | 目标案例 | 类型 | 说明 |
|-----------|---------|------|------|
| m194 | suboff_re1000 | 诊断+修复绕行 | 第6轮，连续5轮finish缺失，需极简策略 |
| m195 | cavity_natural_convection | 诊断+修复绕行 | 第2轮诊断，m182已成功过 |
| m196 | cylinder_3d | 诊断+修复绕行 | 第4轮，口径问题已定位但agent未finish |

## 4. 合并报告

- POST /api/v1/tensor/reports → **report id=112** (status=published)
- 标题：【值班长·m193】SDAA 扫荡线合并报告 — 43 案例 38 PASS / 5 FAIL（论文级）
- 含逐案例判据表、FAIL 如实记录、设备证据汇总、审计与局限

## 5. 升级事项（留给 owner）

1. **系统性 agent finish 缺失**：17/18 次失败均为 agent 未调用 finish 工具。这是平台调度/agent 行为问题，非案例物理/算法问题。建议检查：
   - agent prompt 中 finish 调用指令是否足够明确
   - 预算配置（max_tool_calls/max_wall_seconds）是否导致 agent 在 finish 前超时
   - 是否需要增加 finish 调用的强制检查机制

2. **suboff_re1000 专项**：5 轮连续失败（m62/m108/m145/m172/m190），从未完成有效计算。建议 owner 评估：
   - 是否需要人工介入执行
   - 案例配置是否需要调整（计算量/内存需求）
   - 是否考虑暂时标记为 skip

3. **in-flight 队列管理**：当前 in-flight=1（仅 m193 自身），队列较空。建议后续班次关注新案例下探。

## 6. 下一班建议

1. 跟踪 m194/m195/m196 完工状态，若再次因 finish 缺失失败，建议 owner 介入
2. 若 in-flight < 4，可按字母序派未覆盖案例（当前 43 案例已全部覆盖）
3. 关注 suboff_re1000 是否需要特殊处理策略
4. 考虑增加 agent finish 调用的自动化检查/重试机制
