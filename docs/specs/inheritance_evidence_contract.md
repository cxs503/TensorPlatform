# 继承门证据契约 `inheritance_evidence` v1

> 适用对象：战役 DAG 的**非门节点**（task/review/checkpoint）——上游要让自己被继承门
> 判为 `accepted`，必须在交付物里给出本契约声明的证据块。
> 实现：`ops/taskplane/inheritance_gate.py`（纯函数 `evaluate_inheritance_gate`）
> 验收测试：`tests/test_inheritance_gate.py`（35 条）
> 首次实裁：IN28-G25 / plan 83（`artifacts/in28_g25/inheritance_gate.json`）

## 1. 为什么要契约

继承门不读散文、不猜量纲、不替节点拍容差。它只认两样东西：

1. 平台库里的节点状态：`campaign_nodes.status`、`reproduction_verdict`、关联 mission 状态；
2. 节点交付物里**机器可解析**的证据块 `inheritance_evidence`。

没有第 2 项 → 声明的验收判据全部判为 `unproven`，节点不算验收（**判据缺口≠通过**）。
这堵住了历史上最常见的两种假通过：节点 `done` 但没有测量值；测量值量纲错位（6.1 vs 0.061）。

## 2. 证据块放在哪（三通道，任选其一，可叠加）

| 通道 | 位置 | 说明 |
|---|---|---|
| A. 报告正文围栏 | 报告 markdown 里 ```` ```inheritance-evidence ```` 围栏 | 推荐；报告与证据同源，评审可见 |
| B. 报告 JSON 块 | ```` ```json ```` 围栏，体内须含键 `inheritance_evidence` | 兼容已有 JSON 代码块 |
| C. 工件文件 | 节点 metadata `inheritance_evidence: "artifacts/.../ev.json"` | 路径须落在门配置 `roots` 内 |

多份证据并存时取**最新一份**（按报告 `created_at`），其余来源写入节点 `warnings`
（`multiple_evidence_sources:...`）——不静默丢弃。

## 3. 证据块结构

```json
{
  "inheritance_evidence": {
    "acceptance": {
      "c_t_lbm_vs_fvm": {"value": 0.061, "unit": "fraction", "tol": 0.08},
      "c_w_bem_vs_lbm": {"value": 6.1, "unit": "%"},
      "trend_consistency_rho": {"value": 1.0, "unit": "1"},
      "gci": {"value": 0.031, "unit": "fraction"}
    },
    "quantities": {
      "c_t_fn0_30": {"value": 4.31e-3, "tol": 0.08, "reference": 4.20e-3, "rtol": 0.05},
      "c_w_fn0_30": {"value": 4.10e-4, "tol": 0.10}
    },
    "artifacts": ["artifacts/in28_n01/ct_decomposition.json", "runs/in28_n01/pytest.log"],
    "notes": "自由文本，门不解析"
  }
}
```

### 3.1 `acceptance`：逐条验收判据的测量值

键 = 判据文本左侧的量名，按与门相同的归一化规则匹配（小写、空白→`_`、去非 `[0-9a-z_]`）。
例：判据 `"C_T_LBM_vs_FVM < 8%"` → 键 `c_t_lbm_vs_fvm`。

条目字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `value`（或 `measured`） | 是 | 测量值。缺 → `unproven:measurement_missing_value` |
| `unit` | 百分点判据必填 | `%`/`pct`/`percent` = 该值已按百分点给出；`fraction`/`rel`/`ratio`/`1` = 已是比值。**缺 unit → `unproven:pct_criterion_requires_unit`，绝不猜** |
| `tol` | 否 | 仅 `=`/`!=` 判据用（等值容差），缺省取门配置 `eq_abs_tol`（默认 1e-3） |

裁决：`passed` / `failed` / `unproven`。**只有 `passed` 计入通过率**；
`failed` 与 `unproven` 都作为阻塞项进 `blockers`。

### 3.2 `quantities`：跨节点互裁用的关键量

同一个键在 **≥2 个已验收节点**里出现才构成"交叉裁决"：

- `spread = (max-min)/|mean|`，与容差比（容差取 config `consistency.tolerance_by_key`
  > 各源条目自带 `tol` 的最小值 > config `consistency.tolerance`，默认 0.10）；
- 条目可带 `reference` + `rtol` → 额外做 `|value-reference|/|reference| ≤ rtol` 基准偏差裁决；
- 只有一个来源的量记为 `single_source_no_cross_judgement`（fail）——**只报一家数拿不到一致性分**。

### 3.3 `artifacts`：复算入口

门只核对"引用了什么"，不复算内容；但无 `artifacts` 且无报告时节点记 `no_evidence`。
建议每条判据至少对应一个工件（JSON 数值表 / pytest 日志 / 图），便于第三方复算。

## 4. 门判 `accepted` 的完整条件

| 代号 | 条件 | 缺省配置 |
|---|---|---|
| A1 | `node.status ∈ done_statuses` | `["done"]` |
| A2 | `reproduction_verdict` 不是 fail；缺省要求为 pass | `require_reproduction_verdict=true` |
| A3 | 关联 mission 状态不是失败终态；缺省要求存在 mission 链接 | `require_mission=true` |
| A4 | 有报告或证据工件 | `require_evidence=true` |
| A5 | 每条声明判据都 `passed` | 无开关（判据缺口永不计通过） |

四条硬门（决定 `verdict=ready`）：

1. `all_covered_milestones_complete`：被覆盖里程碑内节点全部 accepted；
2. `governance_nodes_accepted`：治理类节点全部 accepted（集合由 config/metadata 注入）；
3. `cross_consistency_within_tolerance`：无任何互裁越限、无缺互裁量；
4. `score_above_min`：综合评分 ≥ `min_score`（默认 0.70）。

评分 = Σ 分量×权重（`milestone 0.40 / criteria 0.25 / consistency 0.20 / governance 0.15`，
不适用的分量自动重归一化）。**门只有两种终态：`ready` / `not_ready`，绝不写 `failed`。**

## 5. 最小合规示例（一个节点）

````markdown
## 验收判据与测量值

```inheritance-evidence
{
  "acceptance": {"c_t_lbm_vs_fvm": {"value": 0.061, "unit": "fraction"}},
  "quantities": {"c_t_fn0_30": {"value": 0.00431, "tol": 0.08}},
  "artifacts": ["artifacts/demo/ct_table.json"]
}
```
````

对应配置（门侧，属于运行输入而非代码）：

```json
{"consistency": {"tolerance": 0.08, "min_shared_keys": 1}}
```

## 6. 复跑

```bash
# 1) 只读拉快照
python3 ops/taskplane/inheritance_gate.py --api http://127.0.0.1:8010/api/v1 \
    --plan 83 --token "$TOKEN" --snapshot-out artifacts/in28_g25/snapshot.json

# 2) 复裁（退出码 0=ready，2=not_ready）
python3 ops/taskplane/inheritance_gate.py \
    --snapshot artifacts/in28_g25/snapshot.json \
    --config artifacts/in28_g25/gate_config.json \
    --json-out artifacts/in28_g25/inheritance_gate.json \
    --md-out artifacts/in28_g25/inheritance_gate.md
```

同一 snapshot+config 必得逐字节相同报告（`input_digest` 内容派生），可进 CI 作回归门。