# AlphaEvolve-Style Verifier/Generator Separation — Design Document

## 1. 背景与动机

### 1.1 问题陈述

当前平台的验收流存在"橡皮章"问题：
- **自测即验收**：worker 自己跑测试、自己报告通过，没有独立裁决
- **同源验证**：验收逻辑与实现逻辑在同一进程/同一权限下，无法防止"既当运动员又当裁判"
- **无 held-out 题库**：没有不可见的测试集，worker 可以过拟合已知测试

### 1.2 AlphaEvolve/FunSearch 核心结构

DeepMind 的 FunSearch/AlphaEvolve 系统采用严格的 **生成器/验证器分离**：
1. **候选生成器（Generator/Worker）**：产生候选方案
2. **独立验证器（Verifier/Evaluator）**：用 held-out 测试评估候选
3. **权限隔离**：验证器持有的考卷对生成器不可见
4. **只有过验证的候选存活**：自测结果不计入验收

## 2. 架构设计

### 2.1 组件概览

```
┌─────────────────────────────────────────────────────────┐
│                    VERIFIER ROLE                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ Question     │  │ Independent  │  │ Credit       │  │
│  │ Bank         │  │ Verifier     │  │ Ledger       │  │
│  │ (SQLite,     │  │ (loads exam, │  │ (records     │  │
│  │  chmod 600)  │  │  runs test,  │  │  verdicts)   │  │
│  │              │  │  judges)     │  │              │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
│         ▲                 │                    │        │
│         │                 ▼                    ▼        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ Question     │  │ tp-ver-*     │  │ credit_      │  │
│  │ Generator    │  │ runner       │  │ ledger.jsonl │  │
│  │ (from specs/ │  │              │  │              │  │
│  │  literature) │  │              │  │              │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────────┘
                          │
                    ┌─────┴─────┐
                    │ Isolation │  (file permissions,
                    │ Boundary  │   separate process,
                    │           │   no shared memory)
                    └─────┬─────┘
                          │
┌─────────────────────────┼───────────────────────────────┐
│                    WORKER ROLE                           │
│  ┌──────────────────────┴────────────────────────────┐  │
│  │ Candidate Implementation                          │  │
│  │ - Reads spec from stdin                           │  │
│  │ - Computes result                                 │  │
│  │ - Writes result to stdout                         │  │
│  │ - CANNOT access question bank                     │  │
│  │ - Self-test results are INFORMATIONAL ONLY        │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### 2.2 权限隔离机制

| 层级 | 机制 | 说明 |
|------|------|------|
| 文件系统 | `chmod 600` on questions.db | 仅 owner 可读写 |
| 目录权限 | `chmod 700` on question_bank/ | 仅 owner 可进入 |
| 进程隔离 | subprocess verifier | worker 在独立进程中运行 |
| 环境变量 | `TP_VERIFIER_MODE=1` | 标识验证器模式 |
| 角色分离 | verifier vs worker | 不同角色不同权限 |

### 2.3 数据流

```
1. [Verifier] generate_questions.py → questions.db (commit/freeze)
2. [Worker]   candidate.py (implements calculation, self-tests)
3. [Verifier] tp-ver-* loads exam from questions.db
4. [Verifier] runs candidate in subprocess with held-out inputs
5. [Verifier] compares actual vs expected, computes deviations
6. [Verifier] issues verdict (pass/fail/error)
7. [Verifier] records in credit_ledger.jsonl
8. [Platform] credit_ledger entry → mission acceptance decision
```

## 3. 题库机制

### 3.1 出题规范

每道题目包含：
- **domain**: 学科域（resistance, stability, propulsion, ...）
- **difficulty**: basic / intermediate / advanced
- **specification**: 输入参数（几何、工况、无量纲数）
- **expected_result**: 参考值（来自解析解/文献/基准数据集）
- **tolerances**: 各指标的容许偏差
- **provenance**: 出处（文献、公式、数据集）
- **commit_hash**: SHA-256 冻结哈希（防止篡改）

### 3.2 出题工具

```python
# 从解析解出题
generate_ittc57_questions()      # ITTC-1957 摩擦线
generate_flat_plate_questions()  # 平板边界层

# 从经验公式出题
generate_holtrop_questions()     # Holtrop-Mennen 方法

# 填充题库
populate_question_bank(path)     # 生成 + 冻结
```

### 3.3 题库安全

- 题库文件 `questions.db` 权限 `0o600`
- 题目一旦 commit 即不可修改（immutable）
- 每次 commit 生成 SHA-256 哈希，可验证完整性
- Worker 进程无法读取题库（权限隔离）

## 4. 验收流

### 4.1 验收流程

```
Mission Complete
      │
      ▼
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│ Worker      │     │ tp-ver-*     │     │ Credit       │
│ self-test   │────▶│ loads exam   │────▶│ Ledger       │
│ (info only) │     │ runs indep.  │     │ records      │
│             │     │ verdict      │     │ verdict      │
└─────────────┘     └──────────────┘     └──────────────┘
                                                │
                                                ▼
                                         ┌──────────────┐
                                         │ Mission      │
                                         │ Acceptance   │
                                         │ Decision     │
                                         └──────────────┘
```

### 4.2 关键原则

1. **自测不计入验收**：worker 的自测结果仅作为参考信息记录在 credit_ledger 中，但不影响验收判定
2. **独立裁决**：tp-ver-* 加载考卷、独立运行候选、独立计算偏差
3. **只有过验证的候选存活**：verdict=pass 才计入验收
4. **可回放**：所有验证记录（考卷、结果、偏差、判定）都持久化存储

## 5. 试点案例

### 5.1 阻力计算试点

- **考卷**：15 道题（5 ITTC-1957 + 3 Blasius + 4 Prandtl-Schlichting + 3 Holtrop-Mennen）
- **候选**：`pilots/resistance_calculator.py`
- **验证器**：`tp-ver-resistance`
- **结果**：15/15 通过，verdict=pass

### 5.2 台账记录

```json
{
  "candidate_id": "pilot-resistance-v1",
  "verifier_id": "tp-ver-resistance",
  "domain": "resistance",
  "verdict": "pass",
  "questions_passed": 15,
  "questions_total": 15,
  "pass_rate": 1.0,
  "evidence_hash": "8ffcbb67...",
  "self_test_result": "pass",
  "self_test_note": "Self-test results are informational only"
}
```

## 6. 机器判据验证

| 判据 | 验证方式 | 结果 |
|------|----------|------|
| 题库对 worker 不可读 | `check_worker_access()` + 权限测试 | ✓ secure=true, mode=0o600 |
| 试点台账可回放 | `credit_ledger.jsonl` 持久化 | ✓ 15/15 pass recorded |
| verifier 裁决落 credit_ledger | `record_from_report()` | ✓ entry with evidence_hash |
| 自测不替代验收 | `self_test_note` 显式声明 | ✓ "informational only" |

## 7. 后续扩展

- [ ] 更多学科域的题库（stability, propulsion, seakeeping, ...）
- [ ] 与 tensor_missions 的 complete 端点集成
- [ ] 多 verifier 交叉验证
- [ ] 题库版本管理与演化
- [ ] 基于 LLM 的自动出题（从论文提取参考值）
