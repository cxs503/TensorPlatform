# 语义门集成审批链方案

> PL-SEM 线 · 2026-10-25  
> **状态**：方案（不改现网，留 owner 裁决）

## 1. 背景

现有审批链：
- **形式门**：`gates_check.py`（值-文件-键三元组复算）、`pr_hygiene`、`auto_approver`
- **验证**：测试全绿、守恒账本闭合、PR 卫生

问题：形式门验内部自洽，不验目标语义。SI-4 全链演示案例证明：形式门全绿但内容
不符合 goal 语义（合成载荷/禁用重力/规模不足）。

## 2. 双门并行方案

### 2.1 架构

```
goal 声明
  ↓
┌─────────────────────────────────────┐
│  形式门（gates_check.py）            │  验内部自洽
│  - 值-文件-键复算                    │
│  - 测试全绿                          │
│  - 守恒账本闭合                      │
└─────────────────────────────────────┘
  ↓ PASS
┌─────────────────────────────────────┐
│  语义门（semantic_gate.py）          │  验目标语义
│  - 溯源判据（合成标记扫描）          │
│  - 物理开关判据（配置/源码检查）     │
│  - 规模现实性判据（量级下限）        │
└─────────────────────────────────────┘
  ↓
┌─────────────────────────────────────┐
│  审批决策                            │
│  - 双门 PASS → succeeded=目标达成    │
│  - 语义门 FAIL → 阻断表述，转差距    │
└─────────────────────────────────────┘
```

### 2.2 语义门失败处理

**原则**：语义门失败 ≠ 否决交付，是阻断 `succeeded=目标达成` 表述。

处理流程：
1. **阻断表述**：mission 不能标记 `succeeded=目标达成`，改为 `succeeded=形式闭环`。
2. **差距报告**：语义门输出差距摘要（溯源/物理开关/规模），入 mission 报告。
3. **backlog 登记**：差距入 backlog，交 owner/新战役承接（SIR 线范式）。
4. **裁决留 owner**：语义门不自动否决，最终裁决留 owner。

### 2.3 集成点

**候选集成点**（不改现网，方案留 owner）：

1. **`auto_approver.py`**：在审批前调用语义门，失败时修改审批类型。
   ```python
   # 伪代码
   semantic_report = semantic_gate.evaluate(goal, worktree)
   if semantic_report["overall_verdict"] != "compliant":
       # 阻断 succeeded=目标达成
       decision_note += f"\n[语义门] {semantic_report['gap_summary']}"
       # 不自动否决，留 owner 裁决
   ```

2. **`gates_check.py`**：追加语义门为第七门（paper 档）。
   ```python
   # 伪代码
   SEMANTIC_GATE = "semantic_criteria"
   PAPER_GATES = {"case_readme", "figures", "semantic_criteria"}
   ```

3. **mission 报告模板**：新增 `semantic_gate_report` 字段。
   ```json
   {
     "mission_id": "m188",
     "status": "succeeded",
     "formal_closure": true,
     "semantic_closure": false,
     "semantic_gate_report": {
       "overall_verdict": "non_compliant",
       "gap_summary": {...}
     }
   }
   ```

## 3. 不改现网的纪律

1. **不改动 `auto_approver.py`**：方案文档化，留 owner 裁决。
2. **不改动 `gates_check.py`**：语义门独立，不追加为第七门。
3. **不改动审批流**：双门并行，语义门失败不自动否决。

## 4. 落地路径（建议）

1. **本任务**：语义门原型 + 单测 + SI-4 锚案例固化。
2. **下一任务**：owner 裁决集成方案，决定是否改动现网。
3. **SIR 线**：承接 SI-4 语义门差距，用真实物理重跑。

## 5. 锚案例：SI-4 翻案

语义门对 SI-4 工件的判定（单测固化）：
- **总判定**：`non_compliant`
- **溯源差距**：`generate_synthetic_lbm_snapshot(seed=42)` 合成载荷
- **物理开关差距**：`gravity=None` 禁用重力
- **规模差距**：5ms 物理时间、16 网格单元、12 颗粒——远低于「完整演化」量级

此锚案例防回退：任何对语义门的修改必须保持 SI-4 判不符合。

## 6. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 合成标记清单不全 | 可配置，不硬编码单库词汇 |
| 物理关键词映射遗漏 | 可配置，逐步完善 |
| 规模下限设定争议 | 由 goal 声明或 owner 裁决 |
| 语义门误判 | 单测覆盖，SI-4 锚案例防回退 |

## 7. 验收标准

- [x] 语义门原型实现（`tools/ops/semantic_gate.py`）
- [x] 单测覆盖三类判据（`tests/test_semantic_gate.py`）
- [x] SI-4 锚案例固化（判不符合）
- [x] 集成方案文档化（本文档）
- [x] 不改现网审批器
- [x] 既有测试全绿
