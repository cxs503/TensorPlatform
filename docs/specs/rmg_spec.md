# RMG 科研任务生成器规范（Research Mission Generator Specification）

> PL-SEM-2 · proposal-only 原型。生成器只产 proposal 文件，绝不自动派发 mission——
> owner 审后经现有 dispatch_helper 晋级。不新建平行流水线，复用 mission 表/收割/审批现网。

## 1. 背景与边界

- **铁律边界**：RMG 只产出 `research_proposal.json` + `digest.md`，不触发任何
  dispatch/auto_approver/gates_check 行为。
- **不改现网**：不修改 cron、auto_approver、gates_check、semantic_gate 判定逻辑。
- **复用现网**：proposal 晋级后走现有 dispatch_helper → mission 表 → 收割 → 审批链。

## 2. 证据库清单

RMG 扫描以下证据源（只读，不修改）：

### 2.1 实验运行记录

| 源 | 路径模式 | 内容 |
|---|---|---|
| verdict.json | `/srv/tensor/shared/runs/*/**/verdict.json` | 实验判决（arm 状态、门判定、tripwire） |
| prereg.md | `/srv/tensor/shared/runs/*/**/prereg.md` | 预注册（假设、判据、冻结收据，append-only） |
| arm_result.json | `/srv/tensor/shared/runs/*/**/arm_result.json` | 单臂结果 |
| run.log | `/srv/tensor/shared/runs/*/**/run.log` | 运行日志 |
| report.md | `/srv/tensor/shared/runs/*/**/report.md` | 实验报告 |

### 2.2 基准验证库

| 源 | 路径 | 内容 |
|---|---|---|
| gates_verified.json | `benchmarks/verified/<case>/gates.json` | 已验证案例的门判决 |
| verdict.json | `benchmarks/verified/<case>/verdict.json` | 验证案例判决 |

### 2.3 Mission 数据库

| 源 | 内容 |
|---|---|
| honest-FAIL 记录 | mission 表中状态为 failed 且有诚实失败报告的记录 |
| 决策债记录 | 终裁链尾标注「交 owner 裁决」的未决事项 |

### 2.4 运营日志

| 源 | 路径 | 内容 |
|---|---|---|
| operator_log | `operator_log/verify_*.py` | 巡检记录、发现事项 |

### 2.5 平台报告

| 源 | 内容 |
|---|---|
| tensor_reports | 平台生成的综合报告 |

## 3. 缺口识别规则（v0=机械规则优先）

**v0 原则**：禁 LLM 自由发挥定题。所有候选必须由机械规则从证据缺口命中产生。

### 3.1 规则清单

| 规则 ID | 规则名 | 触发条件 | 优先级 |
|---|---|---|---|
| R1 | honest-FAIL 无后续单 | verdict 中有 arm status=fail，且同 run 目录下无后续修复 run | P0 |
| R2 | FAIL 归档根因待验证 | verdict 中 fail 的 reason 标注「待验证」或等价标记 | P1 |
| R3 | 终裁链尾决策债 | report.md 中「分枝建议」「后续」「待裁决」段落引用的未决事项 | P1 |
| R4 | prereg 假设未测 | prereg.md 中声明的判据/假设在对应 verdict 中无判决结果 | P0 |
| R5 | 跨库契约缺验证 | 两个实验引用同一物理量/配置但数值不一致或缺交叉验证 | P2 |

### 3.2 规则命中逻辑

```python
def scan_gaps(evidence_sources):
    candidates = []
    for run_dir in find_run_dirs():
        verdict = load_verdict(run_dir)
        prereg = load_prereg(run_dir)
        report = load_report(run_dir)
        
        # R1: honest-FAIL 无后续
        if has_failed_arms(verdict) and not has_followup_run(run_dir):
            candidates.append(make_candidate(R1, run_dir, verdict))
        
        # R4: prereg 假设未测
        if prereg:
            untested = find_untested_hypotheses(prereg, verdict)
            if untested:
                candidates.append(make_candidate(R4, run_dir, untested))
        
        # R3: 决策债
        if report:
            debts = extract_decision_debts(report)
            if debts:
                candidates.append(make_candidate(R3, run_dir, debts))
    
    return rank_by_priority(candidates)
```

## 4. Proposal 机器格式

### 4.1 research_proposal.json

```json
{
  "proposal_id": "20261025-tau-uniform-replication",
  "title": "均匀网格 τ 阶梯复现实验",
  "hypothesis": "τ×壁耦合效应在均匀网格 BGK 碰撞下同样存在",
  "evidence_refs": [
    {
      "file": "/srv/tensor/shared/runs/tau_ladder_uniform_20261025/verdict.json",
      "claim": "5 臂全部 fail（result.json not found）",
      "machine_check": {"type": "file_exists", "path": "/srv/tensor/shared/runs/tau_ladder_uniform_20261025/verdict.json"},
      "verified": true
    },
    {
      "file": "/srv/tensor/shared/runs/tau_axis_20261015/verdict.json",
      "claim": "TG1=RELAXATION_COUPLING，D_QW 对 τ 强响应",
      "machine_check": {"type": "json_value", "path": "/srv/tensor/shared/runs/tau_axis_20261015/verdict.json", "key": "gates.TG1.verdict", "expected_contains": "RELAXATION_COUPLING"},
      "verified": true
    }
  ],
  "why_now": "tau_ladder_uniform 全 FAIL 无后续，tau_axis 已证 AMR 系统 τ 耦合，均匀网格待验证",
  "falsification_criteria": [
    "均匀网格 τ 阶梯下 D_QW 对 τ 无响应（|ΔD| < 族半宽）",
    "BGK 碰撞下 τ 效应与 MRT 无显著差异"
  ],
  "controls": [
    "固定 Re_res=1e5，只变 τ",
    "与 tau_axis 同 checkpoint 基线",
    "分辨率阶梯（80/120/160）"
  ],
  "budget_hint": {
    "gpu_hours": 8,
    "storage_gb": 20,
    "estimated_wall_clock": "4h"
  },
  "expected_artifacts": [
    "verdict.json（含 TG1 τ 响应判决）",
    "report.md（均匀 vs AMR 对比）",
    "prereg.md（冻结判据）"
  ],
  "gap_rule": "R1+R4",
  "triggered_by": {
    "rule_id": "R1",
    "evidence_dir": "/srv/tensor/shared/runs/tau_ladder_uniform_20261025/",
    "detail": "5 臂全 FAIL 无后续修复 run"
  },
  "generated_at": "2026-10-25T12:00:00Z",
  "generator_version": "rmg-v0"
}
```

### 4.2 证据引用机器核验

**核心原则**：evidence_refs 指向的文件必须存在且引用值可复读复算。
语义门思想反作用于选题层：LLM 可起草措辞，引用必须机器核过才出库。

核验流程：
1. **文件存在性**：`evidence_ref.file` 路径必须存在（`os.path.exists`）。
2. **值可复算**：`machine_check` 指定的核验必须通过：
   - `file_exists`：文件存在。
   - `json_value`：JSON 文件中指定键路径的值包含/等于预期。
   - `grep_match`：文件中可 grep 到指定模式。
   - `numeric_range`：数值在指定范围内。
3. **核验失败 = proposal 拒绝**：任一 evidence_ref 核验失败，整条 proposal 不输出。

```python
def verify_evidence_refs(proposal):
    for ref in proposal["evidence_refs"]:
        # 1. 文件存在
        if not os.path.exists(ref["file"]):
            return False, f"文件不存在: {ref['file']}"
        
        # 2. 值可复算
        check = ref["machine_check"]
        if check["type"] == "json_value":
            with open(check["path"]) as f:
                data = json.load(f)
            value = walk_json(data, check["key"])
            if "expected_contains" in check:
                if check["expected_contains"] not in str(value):
                    return False, f"值不匹配: {check['key']}"
            elif "expected_equals" in check:
                if value != check["expected_equals"]:
                    return False, f"值不相等: {check['key']}"
        
        ref["verified"] = True
    
    return True, "all refs verified"
```

## 5. 输出规范

### 5.1 目录结构

```
agent_work/rmg/
├── proposals/
│   ├── 20261025-tau-uniform-replication.json
│   ├── 20261025-tau-uniform-replication.digest.md
│   ├── 20261025-amr-collision-attribution.json
│   └── ...
└── run_log.json  # 生成器运行日志
```

### 5.2 digest.md（给 owner 的摘要）

```markdown
# RMG Proposal Digest — 2026-10-25

生成时间：2026-10-25T12:00:00Z
扫描证据源：32 verdict.json, 15 prereg.md, 3 tau_* runs

## 候选 Proposal 摘要

### 1. 20261025-tau-uniform-replication
- **假设**：τ×壁耦合效应在均匀网格 BGK 碰撞下同样存在
- **触发规则**：R1（honest-FAIL 无后续）+ R4（prereg 假设未测）
- **证据**：tau_ladder_uniform 5 臂全 FAIL；tau_axis 已证 AMR τ 耦合
- **证伪判据**：均匀网格 τ 阶梯下 D_QW 对 τ 无响应
- **预算**：8 GPU·h, 4h 墙钟
- **状态**：待 owner 审

### 2. ...

## 未命中规则的缺口（信息性）

- ...

## 纪律声明

- 未修改任何现网文件（cron/auto_approver/gates_check/semantic_gate）
- 未自动派发任何 mission
- 所有 evidence_refs 经机器核验
```

## 6. 纪律

1. **proposal-only**：生成器只产 proposal，不触发 dispatch。
2. **不改现网**：不修改 cron、auto_approver、gates_check、semantic_gate。
3. **证据核验**：所有 evidence_refs 必须机器核验通过。
4. **规则命中**：v0 阶段只接受机械规则命中的候选，禁 LLM 自由发挥。
5. **owner 裁决**：proposal 是否晋级为 mission 由 owner 审。
