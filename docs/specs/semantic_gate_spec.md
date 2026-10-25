# 语义判据规范（Semantic Criteria Specification）

> PL-SEM 线 · owner 2026-10-25 指令「缺真正理解意义的评估闭环，不能以形式闭环
> 作为验收标准，需要内容真正符合」的落地规范。

## 1. 背景与动机

现有形式门（`gates_check.py` 四门/六门、`pr_hygiene`、`auto_approver`）验证的是
**内部自洽**：值-文件-键三元组复算、测试全绿、守恒账本闭合。但内部自洽≠目标语义
满足。

**铁证案例（SI-4 全链演示）**：
- goal 写「SUBOFF 顶破冰板完整演化」
- mission 判 succeeded、双账本门全绿、34/34 测试过
- 实际内容：合成载荷（`generate_synthetic_lbm_snapshot(seed=42)`）、禁用重力、
  1m 板 4×4 网格、12 颗粒 0 键断、物理时间 5ms

形式门无法发现「goal 语义未被内容满足」，因为形式门不读 goal 语义。

## 2. 设计原则

1. **语义门是增量，不是替代**：与形式门并存，形式门验内部自洽，语义门验目标语义。
2. **机器可裁**：每条判据必须是可机裁断言，不依赖人工解读。
3. **三类判据**：溯源、物理开关、规模现实性——覆盖「内容是否符合目标」的核心维度。
4. **合成标记可配置**：扫描模式用清单，不硬编码单库词汇。
5. **语义门失败≠否决交付**：阻断「succeeded=目标达成」表述，差距入报告交 owner。

## 3. Goal 模板扩展：`semantic-criteria` 块

goal 模板新增 `semantic-criteria` 块，每条判据=可机裁断言：

```yaml
semantic-criteria:
  - id: provenance_lbm_flow
    category: provenance          # 溯源判据
    description: "LBM 流场必须来自真实模拟，非合成/占位/种子函数"
    assertion:
      type: no_synthetic_marker
      scan_targets:               # 扫描哪些工件
        - "outputs/**/lbm_*.npz"
        - "outputs/**/flow_*.json"
      markers:                    # 合成标记（可配置）
        - "synthetic"
        - "placeholder"
        - "seed="
        - "generate_synthetic"
      evidence_files:             # 证据引用
        - "run_demo.py"
        - "full_chain_orchestrator.py"

  - id: physics_gravity_enabled
    category: physics_switch      # 物理开关判据
    description: "goal 声明含重力/破冰演化，重力必须开启"
    assertion:
      type: config_key_true
      config_files:
        - "outputs/**/full_chain_summary.json"
        - "outputs/**/dem_snapshot_*.json"
      key_path: "gravity_enabled"  # 或 grep 源码
      expected: true
      grep_fallback:              # 若 JSON 无此键，grep 源码
        files: ["scenarios/**/*.py"]
        pattern: "gravity\\s*=\\s*None"
        match_means: "fail"       # 匹配到 gravity=None 则判不符

  - id: scale_physical_duration
    category: scale_realism       # 规模现实性判据
    description: "物理时长必须达到 goal 声明的量级下限"
    assertion:
      type: numeric_lower_bound
      source:
        file: "outputs/**/full_chain_summary.json"
        key_path: "total_physical_time_s"
      lower_bound: 1.0            # 至少 1 秒（破冰演化量级）
      unit: "s"
```

## 4. 三类判据详解

### 4.1 溯源判据（Provenance）

**目的**：确保关键输入来自真实物理源，非合成/占位/种子函数。

**机裁方法**：
1. 扫描 goal 声明的关键输入类型（流场/几何/材料参数）。
2. 在工件与源码中搜索合成标记（`synthetic`/`placeholder`/`seed=`/`generate_*`）。
3. 若匹配到合成标记 → 判「不符」；若无标记且有真实数据文件 → 判「符合」。

**合成标记清单**（可配置，不硬编码）：
```yaml
synthetic_markers:
  - "synthetic"
  - "placeholder"
  - "seed="
  - "generate_synthetic"
  - "mock_"
  - "dummy_"
  - "fake_"
  - "random_state"
```

### 4.2 物理开关判据（Physics Switch）

**目的**：确保 goal 声明的物理效应在配置工件中实际开启。

**机裁方法**：
1. 从 goal 文本提取物理关键词（重力/两相/接触/运动/热/电磁…）。
2. 在配置工件（JSON/YAML）中检查对应开关键。
3. 若配置无此键，grep 源码检查是否显式禁用（如 `gravity=None`）。
4. 「为过门而禁用的物理」必须显式披露且默认判不符。

**物理关键词→配置键映射**（可配置）：
```yaml
physics_keywords:
  gravity:
    config_keys: ["gravity_enabled", "gravity", "g"]
    disable_patterns: ["gravity\\s*=\\s*None", "gravity\\s*=\\s*0"]
  two_phase:
    config_keys: ["two_phase", "phase_field", "volume_fraction"]
    disable_patterns: ["single_phase"]
  contact:
    config_keys: ["contact_model", "contact_enabled"]
    disable_patterns: ["no_contact", "contact\\s*=\\s*None"]
  fracture:
    config_keys: ["fracture_enabled", "cohesive_model"]
    disable_patterns: ["no_fracture"]
```

### 4.3 规模现实性判据（Scale Realism）

**目的**：确保物理时长/网格规模/粒子数达到 goal 声明的量级下限。

**机裁方法**：
1. 从 goal 文本或 `semantic-criteria` 提取规模下限。
2. 在工件中提取实际数值。
3. 比较：实际 ≥ 下限 → 符合；实际 < 下限 → 不符。

**规模维度**（可配置）：
```yaml
scale_dimensions:
  physical_duration:
    key_path: "total_physical_time_s"
    unit: "s"
  grid_resolution:
    key_path: "n_elements"
    unit: "count"
  particle_count:
    key_path: "n_particles"
    unit: "count"
  bond_count:
    key_path: "n_bonds_broken"
    unit: "count"
```

## 5. 总判定逻辑

- **内容符合**：全部判据符合。
- **部分符合**：部分判据不符或无法判定，但无致命差距（如溯源不符）。
- **不符合**：任一溯源判据不符，或 ≥2 类判据不符。

## 6. 输出格式

```json
{
  "goal_id": "si4_full_chain",
  "goal_text": "SUBOFF 顶破冰板完整演化",
  "overall_verdict": "non_compliant",
  "criteria_results": [
    {
      "id": "provenance_lbm_flow",
      "category": "provenance",
      "verdict": "non_compliant",
      "evidence": [
        {"file": "run_demo.py", "line": 42,
         "match": "generate_synthetic_lbm_snapshot(seed=42)"}
      ],
      "gap": "LBM 流场来自合成函数，非真实模拟"
    },
    ...
  ],
  "gap_summary": {
    "provenance_gaps": 1,
    "physics_switch_gaps": 1,
    "scale_realism_gaps": 2
  }
}
```

## 7. 与审批链集成（方案，不改现网）

- **双门并行**：形式门（`gates_check.py`）+ 语义门（`semantic_gate.py`）。
- **语义门失败**：阻断 `succeeded=目标达成` 表述，转差距报告。
- **裁决留 owner**：语义门不自动否决，差距入 backlog 交 owner/新战役承接。

## 8. 锚案例：SI-4 全链演示

语义门对 SI-4 工件必须判「不符合」，逐条指出：
1. **溯源差距**：`generate_synthetic_lbm_snapshot(seed=42)` 合成载荷。
2. **物理开关差距**：重力禁用（`gravity=None`，`potential_energy_J=-0.0`）。
3. **规模差距**：4×4 网格、12 颗粒、5ms 物理时间——远低于「完整演化」量级。

此锚案例固化入单测，防回退。

## 9. 四级验证映射（PL-SEM-2 新增）

> 语义门思想反作用于选题层：LLM 可起草措辞，引用必须机器核过才出库。
> 四级验证 = 从代码正确性到工程可信度的逐级递进，每级对应平台现有门。

### 9.1 四级定义

| 层级 | 名称 | 含义 | 核心问题 |
|------|------|------|----------|
| L1 | 代码正确性 | 代码逻辑正确、无 bug | 程序能跑、不崩溃 |
| L2 | 数值正确性 | 数值结果与参考解一致 | 数字对不对 |
| L3 | 物理正确性 | 物理效应被正确捕获 | 物理对不对 |
| L4 | 工程可信度 | 端到端可复现、跨库契约一致 | 系统可信吗 |

### 9.2 四级 ↔ 平台现有门对照表

| 层级 | 平台现有门 | 覆盖内容 | 当前覆盖度 | 缺口 |
|------|-----------|----------|-----------|------|
| **L1 代码正确性** | CI + 测试套件门 | 语法检查、单元测试、集成测试 | ✅ 全覆盖 | 无（成熟） |
| **L2 数值正确性** | 回归 + 机器判据复算 | gates_check.py 四门/六门：值-文件-键三元组复算、单调收敛、参考锁定 | ✅ 全覆盖 | 无（成熟） |
| **L3 物理正确性** | semantic_gate 三判据 + 外部锚 | 溯源/物理开关/规模现实性 + Blasius/Benchmark 外部参考 | 🟡 部分覆盖 | ①物理开关映射不全（仅重力/两相/接触）②外部锚库有限 |
| **L4 工程可信度** | 跨库契约 + 端到端复现 | 跨任务一致性、冻结回执、tripwire、端到端复现 | 🟡 部分覆盖 | ①跨库契约自动验证缺失 ②端到端复现无机器门 |

### 9.3 逐级详细说明

#### L1 代码正确性 → CI + 测试套件门

**现有实现**：
- `pytest` 全绿（tests/ 目录）
- `pr_hygiene.py`：PR 卫生检查
- CI 流水线：lint + test + build

**覆盖度**：✅ 全覆盖。代码层面的正确性由测试套件保证。

**缺口**：无。

#### L2 数值正确性 → 回归 + 机器判据复算

**现有实现**：
- `gates_check.py`：四门（classic）/六门（paper）机械复算
  - `library_entry`：库入口正确
  - `direct_observable`：直接观测量在参考范围内
  - `monotone_convergence`：单调收敛
  - `reference_locked_first`：参考锁定
  - `case_readme`：案例文档质量（paper 档）
  - `figures`：图表质量（paper 档）
- `suite_check.py`：套件检查
- `benchmark_dispatch.py`：基准派发

**覆盖度**：✅ 全覆盖。数值层面的正确性由 gates_check 保证。

**缺口**：无。

#### L3 物理正确性 → semantic_gate 三判据 + 外部锚

**现有实现**：
- `semantic_gate.py`：三类判据
  - 溯源判据（provenance）：检测合成/占位标记
  - 物理开关判据（physics_switch）：检测物理效应是否开启
  - 规模现实性判据（scale_realism）：检测物理量级是否合理
- 外部锚：Blasius 参考、benchmark verified 案例

**覆盖度**：🟡 部分覆盖。

**缺口**：
1. **物理开关映射不全**：当前仅覆盖重力/两相/接触，缺：
   - 湍流模型（Smagorinsky/BGK/MRT）
   - 壁面处理（BFL/leaf_wall/no_slip）
   - 碰撞算子（BGK/MRT/TRT）
   - 热效应（能量方程/被动标量）
2. **外部锚库有限**：当前仅 Blasius 平板，缺：
   - Taylor-Green 涡（衰减率参考）
   - 方柱绕流（Strouhal 数参考）
   - 渗透率（Gebart 参考）

#### L4 工程可信度 → 跨库契约 + 端到端复现

**现有实现**：
- 冻结回执（freeze_receipt）：checkpoint/树 sha 校验
- Tripwire：实验过程中关键文件未被篡改
- `verdict.json`：实验判决（含 tripwire 回执）
- `prereg.md`：预注册（判据冻结）

**覆盖度**：🟡 部分覆盖。

**缺口**：
1. **跨库契约自动验证缺失**：
   - 两个实验引用同一物理量（如 Re、τ），数值应一致
   - 当前靠人工核对，无机器门
   - RMG R5 规则初步覆盖，但未集成到 gates_check
2. **端到端复现无机器门**：
   - 同一实验在不同时间/环境重跑，结果应一致
   - 当前靠 tripwire 保证过程中文件未变，但未验证最终结果可复现
   - 需增加「复现门」：同一 case 两次跑，关键数值 |Δ| < ε

### 9.4 四级验证在审批链中的位置

```
mission 提交
    ↓
[L1] CI + 测试套件门 → 不绿则阻
    ↓
[L2] gates_check 四门/六门 → 不过则阻
    ↓
[L3] semantic_gate 三判据 → 不符则标记差距（不自动阻）
    ↓
[L4] 跨库契约 + 端到端复现 → 不一致则标记差距（不自动阻）
    ↓
auto_approver → 全过则 approved
    ↓
owner 审 → L3/L4 差距由 owner 裁决
```

**纪律**：L3/L4 当前为「标记差距、不自动阻」，因覆盖度不完整。
待覆盖度提升后，由 owner 裁决是否升级为自动阻。

### 9.5 RMG 与四级验证的关系

RMG（科研任务生成器）处于四级验证的**上游**：
- RMG 扫描 L2/L3/L4 的缺口（FAIL/未测/决策债/跨库不一致）
- 生成 proposal 交 owner 审
- owner 批准后，新 mission 补充证据，填补 L2/L3/L4 缺口

**证据引用核验 = L2 思想反作用于选题层**：
- proposal 中的 evidence_refs 必须机器核验（文件存在、值可复算）
- 语义门思想：LLM 可起草措辞，引用必须机器核过才出库
- 这是 L2 数值正确性的「选题层投影」
