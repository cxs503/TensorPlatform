# 规划师权限阶梯文档

## 概述

规划师权限分为 4 级，每级升权需 owner 审批。

## 权限等级

### advisory

**描述**: 纯建议模式：规划师输出决策建议，不实际派单

**需 owner 审批**: 否

---

### shadow

**描述**: 影子模式：并行出决策，对照机器门+人工裁决

**升级条件**:

- `min_decisions`: 30
- `min_accuracy`: 0.7
- `min_held_out_accuracy`: 0.65

**需 owner 审批**: 是

---

### low_risk_auto

**描述**: 低风险自动派：仅机械修+返工类可自动派单

**升级条件**:

- `min_decisions`: 100
- `min_accuracy`: 0.8
- `min_held_out_accuracy`: 0.75
- `min_mechanical_accuracy`: 0.9
- `zero_false_dispatch`: True

**需 owner 审批**: 是

---

### campaign_auto

**描述**: 战役自动拆单：可自动拆解战役级任务

**升级条件**:

- `min_decisions`: 300
- `min_accuracy`: 0.85
- `min_held_out_accuracy`: 0.8
- `min_mechanical_accuracy`: 0.95
- `zero_false_dispatch`: True
- `cost_calibration_error`: 0.2

**需 owner 审批**: 是

---

## 培养回路

1. **决策记录**：每次决策入 credit_ledger
2. **结果回填**：后续结果（succeeded/revoked/返工）回填
3. **信用评分**：基于正确性和置信度计算
4. **错判案例**：写入 playbooks 规划分册
5. **升权评估**：定期检查是否满足升级条件

## 当前状态

- 当前权限等级: advisory
- 信用分: 100.0（初始）
- 决策总数: 0
- 正确数: 0
- 错误数: 0

## 升权流程

1. 规划师达到目标等级的所有条件
2. 提交升权请求
3. Owner 审核跑分报告和典型案例
4. Owner 批准后升级

## 降级机制

- 连续 3 次误判 → 降一级
- 严重误判（如 false dispatch 导致资源浪费）→ 降两级
- 信用分 < 50 → 回到 advisory
