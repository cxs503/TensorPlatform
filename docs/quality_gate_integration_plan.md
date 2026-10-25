# 质量门集成方案（m311 PL-SEM-3）

**状态**：方案已设计，待 owner 批准后接线  
**日期**：2026-10-25  
**作者**：tensor-agent (m311)

---

## 1. 概述

本文档描述 `quality_gate.py` 在收割线中的接入点设计。**当前不接线**，仅作为方案供 owner 审批后执行。

### 1.1 核心原则

- **阻断语义**：`overall=FAIL` → 不推 PR，不合并
- **异常处理**：工具异常 → `overall=UNDETERMINED`（fail-open），但标记人工位
- **不改变现网行为**：仅交付工具+测试+方案，不修改现有 cron/收割逻辑

---

## 2. 接入点设计

### 2.1 expand_follow 线（任务派发）

**接入时机**：任务完成后、派发 PR 前

**接入逻辑**：
```python
# tools/ops/expand_follow.py（示意，未实现）
from quality_gate import run_quality_gate

def dispatch_pr(repo, branch, worktree):
    # 1. 跑质量门
    result = run_quality_gate(
        repo=repo,
        branch=branch,
        worktree=worktree,
        skip_r4=False,  # expand_follow 线应跑完整套件
    )
    
    # 2. 判定
    if result.overall == "FAIL":
        log(f"质量门 FAIL，不派发 PR: {result.summary()}")
        return False  # 阻断
    
    if result.overall == "UNDETERMINED":
        log(f"质量门异常，标记人工位: {result.tool_error}")
        # 继续派发，但标记需要人工复核
        mark_needs_human_review(repo, branch)
    
    # 3. 派发 PR
    create_pr(repo, branch)
    return True
```

**阻断点**：
- `overall=FAIL` → 不创建 PR
- `overall=UNDETERMINED` → 创建 PR 但标记 `needs_human_review`

---

### 2.2 benchmark 线（基准复算）

**接入时机**：`gates_check.py` 复算通过后、提交 PR 前

**接入逻辑**：
```python
# tools/ops/benchmark/benchmark_dispatch.py（示意，未实现）
from quality_gate import run_quality_gate

def submit_benchmark_pr(case_dir, worktree):
    # 1. 先跑 gates_check（现有逻辑）
    if not gates_check(case_dir):
        return False
    
    # 2. 再跑质量门
    result = run_quality_gate(
        repo="TensorPlatform",
        branch=get_current_branch(worktree),
        worktree=worktree,
        skip_r4=True,  # benchmark 线不跑完整套件（CI 环境限制）
    )
    
    # 3. 判定
    if result.overall == "FAIL":
        log(f"质量门 FAIL，不提交基准 PR: {result.summary()}")
        return False
    
    # 4. 提交 PR
    create_pr("TensorPlatform", get_current_branch(worktree))
    return True
```

**阻断点**：
- `overall=FAIL` → 不提交基准 PR
- `overall=UNDETERMINED` → 继续提交（benchmark 线容忍异常）

---

### 2.3 si_harvest 线（语义闭环收割）

**接入时机**：语义门判定后、发布报告前

**接入逻辑**：
```python
# tools/ops/si_harvest.py（示意，未实现）
from quality_gate import run_quality_gate
from semantic_gate import SemanticGate

def harvest_semantic_closure(mission_id, worktree):
    # 1. 先跑语义门（现有逻辑）
    sem_gate = SemanticGate(worktree)
    sem_result = sem_gate.check_all()
    
    if sem_result["verdict"] == "non_compliant":
        log(f"语义门不通过，不收割: {sem_result['gaps']}")
        return False
    
    # 2. 再跑质量门
    result = run_quality_gate(
        repo="TensorPlatform",
        branch=get_current_branch(worktree),
        worktree=worktree,
        skip_r4=False,  # si_harvest 线应跑完整套件
    )
    
    # 3. 判定
    if result.overall == "FAIL":
        log(f"质量门 FAIL，不发布报告: {result.summary()}")
        return False
    
    if result.overval == "UNDETERMINED":
        log(f"质量门异常，标记人工位: {result.tool_error}")
        mark_needs_human_review("TensorPlatform", get_current_branch(worktree))
    
    # 4. 发布报告
    publish_report(mission_id)
    return True
```

**阻断点**：
- `overall=FAIL` → 不发布报告
- `overall=UNDETERMINED` → 发布报告但标记 `needs_human_review`

---

## 3. PR 机器质量评论

### 3.1 评论格式

当 `quality_gate.py --pr <number>` 时，自动在 PR 下发表评论：

```markdown
<!-- quality-gate-bot -->
# 质量门判定（QUALITY_BAR.md）

**总判定**: `PASS` / `FAIL` / `PASS_WITH_UNDETERMINED` / `UNDETERMINED`

## 逐条规则判定

### R1 ✅ PASS
**理由**: 无门/判据变更，R1 不适用

### R2 ✅ PASS
**理由**: 2 个 validation_report 的 reference_provenance 有效
**证据**:
- `validation_report_m293.json`
- `validation_report_m300.json`

### R3 ⚠️ UNDETERMINED
**理由**: 1 个物理开关无法静态判定方程路径: ['gravity']
**证据**:
- undetermined: gravity

### R4 ✅ PASS
**理由**: 无新增红测试（基线 5 → 分支 5，修复 0）

### R5 ✅ PASS
**理由**: 无判决文件变更

---
*此评论由 quality_gate.py 自动生成，owner 合并前必读。*
```

### 3.2 评论触发时机

- **expand_follow 线**：PR 创建后自动评论
- **benchmark 线**：PR 创建后自动评论
- **si_harvest 线**：报告发布后自动评论

### 3.3 评论凭证

复用 `review_bridge` 的 GitHub 令牌：
- 环境变量：`GH_PUSH_TOKEN`
- 或文件：`/tmp/gh_token`

---

## 4. 异常处理

### 4.1 工具异常（fail-open）

当 `quality_gate.py` 自身异常时（如 git 命令失败、JSON 解析错误）：

```python
result = QualityGateResult()
result.tool_error = f"{type(exc).__name__}: {exc}"
# overall = "UNDETERMINED"
```

**处理策略**：
- **不阻断**：`overall=UNDETERMINED` 不阻断推送
- **标记人工位**：在 PR 评论中标记 `⚠️ 工具异常（fail-open）`
- **日志记录**：记录异常详情供后续排查

### 4.2 规则判定异常

当单条规则判定异常时（如 R4 套件执行超时）：

```python
return RuleVerdict(
    "R4", RuleVerdict.UNDETERMINED,
    f"套件执行异常: timeout after 600s",
)
```

**处理策略**：
- **不假绿**：异常判 `UNDETERMINED`，不判 `PASS`
- **不阻断**：`UNDETERMINED` 不阻断推送（仅 `FAIL` 阻断）
- **证据记录**：在 `evidence` 字段记录异常原因

---

## 5. 接线步骤（待 owner 批准）

### 5.1 expand_follow 线接线

1. 修改 `tools/ops/expand_follow.py`
2. 在 `dispatch_pr()` 函数中调用 `run_quality_gate()`
3. 根据 `result.overall` 决定是否创建 PR
4. 测试：在测试仓库验证阻断逻辑

### 5.2 benchmark 线接线

1. 修改 `tools/ops/benchmark/benchmark_dispatch.py`
2. 在 `submit_benchmark_pr()` 函数中调用 `run_quality_gate()`
3. 根据 `result.overall` 决定是否提交 PR
4. 测试：在测试仓库验证阻断逻辑

### 5.3 si_harvest 线接线

1. 修改 `tools/ops/si_harvest.py`
2. 在 `harvest_semantic_closure()` 函数中调用 `run_quality_gate()`
3. 根据 `result.overall` 决定是否发布报告
4. 测试：在测试仓库验证阻断逻辑

### 5.4 PR 评论接线

1. 在三线的 PR 创建逻辑后调用 `post_pr_comment()`
2. 验证评论格式和触发时机
3. 验证 GitHub 令牌权限

---

## 6. 风险与缓解

### 6.1 风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| 质量门误判 FAIL | 阻断正常推送 | 人工复核 + 快速修复通道 |
| 质量门异常 UNDETERMINED | 漏检质量问题 | 标记人工位 + 日志告警 |
| R4 套件执行慢 | 延迟推送 | `--skip-r4` 选项 + 超时控制 |
| GitHub API 限流 | 评论失败 | 重试机制 + 降级处理 |

### 6.2 缓解措施

- **人工复核**：`UNDETERMINED` 标记人工位，owner 可手动覆盖
- **快速修复**：`FAIL` 后可快速修复并重新跑质量门
- **超时控制**：R4 套件设置 600s 超时，避免阻塞
- **降级处理**：GitHub API 失败时降级为日志记录

---

## 7. 验收标准

### 7.1 工具验收

- [x] `quality_gate.py` 实现五规则判定
- [x] 34 个单元测试全部通过
- [x] 杀伤性自测：坏工件 FAIL，好工件 PASS
- [x] PR 评论功能实现
- [x] 异常处理：fail-open + 人工位标记

### 7.2 集成验收（待接线后）

- [ ] expand_follow 线阻断逻辑验证
- [ ] benchmark 线阻断逻辑验证
- [ ] si_harvest 线阻断逻辑验证
- [ ] PR 评论格式和触发时机验证
- [ ] 异常场景验证（工具异常、规则异常）

---

## 8. 后续工作

1. **owner 审批**：审批本集成方案
2. **接线执行**：批准后执行接线步骤
3. **监控告警**：接线后监控质量门判定分布
4. **规则迭代**：根据实际运行情况迭代规则（如 R3 物理开关映射）
5. **性能优化**：优化 R4 套件执行速度（并行化、缓存）

---

**文档版本**：v1.0  
**最后更新**：2026-10-25  
**维护者**：tensor-agent (PL-SEM 线)
