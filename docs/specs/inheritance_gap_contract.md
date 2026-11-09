# 谱系继承缺口契约 `inheritance_gap` v1

> 适用对象：**战役播种**（父计划 → 子计划）。父波以「本波继承父波局限节与 KPI 缺口」一句话
> 承接未偿欠账；本契约把这句话变成**可复算机器门**。
> 实现：`ops/taskplane/inheritance_contract.py`（纯函数 `evaluate_seeding`）
> 接线：`ops/taskplane/campaign_dispatcher.py::_validate_new_plan`（播种前硬校验）
> 验收测试：`tests/test_inheritance_contract.py`（28 条）
> 真实回放：`artifacts/m8058/gate_p173_to_p388.json`（plan 173 → 388，裁决 `block`）
> 姊妹契约：`docs/specs/inheritance_evidence_contract.md`（**节点级**证据块，管"节点是否验收"）；
> 本契约管**计划级**欠账传承（"父波欠的账，子波有没有判据接住"）。两者互补，不重叠。

## 1. 为什么要这条门

历史失败的形状：父波终态里有 failed/blocked 节点、报告里有「已知局限」，子波只写一句
"继承局限节"，然后**没有任何判据去度量那些局限**——父波的 honest FAIL 静默蒸发，
指挥链上看不见。门的判据：

> 父波每条未偿欠账，必须在子波里被**判据点名重测**（lexical）、或被**显式引用**
> （explicit）、或由播种者显式降级并留痕（severity=warn），否则播种 fail-closed 拦截。

## 2. 输入载荷（离线快照，纯 JSON，与库解耦）

```json
{"plan":  {"id": 173, "status": "done", "metadata": {"honest_fail": ["..."], "kpi_gaps": [{"text": "...", "metric": "..."}]}},
 "nodes": [{"id": 1078, "status": "blocked", "ext_id": "P27W6-S1", "milestone_id": 5,
            "metadata": {"goal": "【物理目标】…【已知局限】…",
                         "acceptance_criteria": ["err_x < 3 %"],
                         "covers_gaps": ["honest_fail-1a2b3c4d5e"],
                         "limitations": ["…"]}}]}
```

线上取载荷用 `fetch_plan_payload(plan_id, q)`：`q(sql)` 返回 `row_to_json(t)::text` 单列行
（**不**用 `\x02` 多列分隔解包——goal/metadata 文本可含任意字符）。

## 3. 缺口提取（四路，一条不漏）

| 路 | 位置 | kind | 默认 severity |
|---|---|---|---|
| A | `plan.metadata` 的 `honest_fail` / `honest_fails` / `kpi_gap` / `kpi_gaps` / `inheritance_limitations` / `known_limitations` / `limitations` / `debts` | 按键映射 | 见 §5 |
| B | 每个 `node.metadata` 的同名键 | 按键映射 | 见 §5 |
| C | `node.metadata.goal` 正文的**局限节**：`【已知局限】` / `【继承局限】` / `【未完成】` / `【欠账】`… （标题含 `局限/未偿/欠账/未完成/缺口/FAIL`；含 `FAIL/未完成` 记 `honest_fail`，否则 `limitation`） | `limitation`/`honest_fail` | warn / block |
| D | 派生：`node.status ∈ {failed, blocked}` 且该节点**没有任何书面局限声明** → 记「欠账未记账」 | `status_failed`/`status_blocked` | warn |

条目写法：字符串，或 `{"text"/"description"/"statement", "metric"/"quantity", "id"}` 字典
（字典可给 `id` 当作 alias，子节点 `covers_gaps` 里点名它即算显式覆盖）。

**去重**：`(kind, 归一化文本)` 相同 → 合并一条，`sources` 记录全部溯源 pointer 并排序
（不静默丢弃，评审可逐条回查）。`gap_id = "<kind>-<sha256(kind|text)[:10]>"`，
与来源数量/顺序无关 → 可跨波复算对齐。

`config.derive_from_status=false` 可关掉 D 路；`config.severity_by_kind` 可改 kind→severity。

## 4. 覆盖判定（机械、可解释、无人工判断）

对每个 (缺口, 子节点) 组合，逐条 `acceptance_criteria` 求证据：

1. **explicit**：子节点 `metadata.covers_gaps`/`inherits_gaps`/`covered_gaps` 里出现该
   `gap_id`（或该缺口的 `id` alias）→ 覆盖，`coverage=1.0`，不依赖词面。
2. **lexical**：取判据文本与缺口文本的词袋（latin 词 ≥3 字符 + 中文二元组）：
   - 缺口带**指标锚点**（latin 指标名/变量名 ≥4 字符，`pytest/api/json` 等通用词除外）
     → 判据必须命中至少 1 个锚点且锚点覆盖 ≥ `anchor_coverage`(0.5)；
   - 缺口是**纯中文散文**（无锚点）→ 词覆盖 ≥ `min_token_coverage`(0.5) 且共享词 ≥ `min_shared_terms`(2)；
   - **判据必须机器可算**（含 `<,<=,>,>=,=,±` 等算符 + 数值，或 `% / 误差 / 残差 / 准确率 / 覆盖率 / 偏差` + 数值）；
     不可算 → 记 `criterion_not_machine_checkable` 近失，**不算覆盖**（"已充分理解"不是判据）。
3. 其余 → `uncovered`；最好的近失写入 `near_misses[0].reason`
   （`token_coverage_below_threshold` / `anchor_not_covered` / `criterion_not_machine_checkable` / `no_shared_terms`），
   供播种者定向补判据。

`config.include_goal=true` 时子节点 goal 散文也参与词覆盖（默认关：任务书散文不顶判据）。

## 5. 严重度与升格（门的牙齿）

`severity_by_kind` 默认：`honest_fail=block`、`kpi_gap=block`、`limitation=warn`、
`status_failed=warn`、`status_blocked=warn`。

**升格规则**（`escalate_unaccepted_gaps=true`，默认开）：缺口来自 `failed/blocked` 父节点时，
它的"局限"不是适用范围声明而是**未偿欠账** → 升格为 `block`，并在 gap 上留痕
`escalated_from={"severity":"warn","reason":"node_not_accepted:<status>"}`。
没有这条规则，真实数据里 100% 的局限都是 warn 级 → 门永远不拦（等于没门）。

`blocking_severities` 默认 `["block"]`：只有该集合里的 severity 未覆盖才拦截播种。

## 6. 裁决

| 裁决 | 条件 | 播种 | CLI 退出码 |
|---|---|---|---|
| `pass` | 所有缺口 covered，或根本没有缺口（**空矩阵**） | 放行 | 0 |
| `warn` | 只有非阻塞级缺口未覆盖 | 放行 + 风险标记 | 0 |
| `block` | 至少 1 条阻塞级缺口未覆盖 | **拦截**（播种者补判据后重播） | 2 |

负控两条（机器判据）：**无缺口的父计划 ⇒ rows 空、verdict=pass、不拦截**；
**全部缺口覆盖的子计划 ⇒ blockers 空、不拦截**。

## 7. 可复算与确定性

- 矩阵行按 `(severity!=block, kind, gap_id)` 排序、列按 `(ext_id, node_id)` 排序
  → **与输入节点顺序无关**；
- `matrix_digest = sha256(canonical_json(matrix))`、`gaps_digest`、`gate_digest` 三层，
  canonical 形式 = `sort_keys=True, ensure_ascii=False, separators=(",",":")`；
- 同输入两次裁决**逐字节相同**（`tools/lineage_contract_audit.py` 内置 run1/run2 比对）；
- 改一条缺口文本 → digest 必变（digest 不是恒真装饰）。

## 8. 播种流程接线

`campaign_dispatcher._validate_new_plan(pid)` 在原机器门（节点数/ext_id 唯一/repo 白名单/
DAG 无环/里程碑/成熟化单）之后追加：

```
child = fetch_plan_payload(pid); parent_plan_id = child.plan.metadata.parent_plan
seeding_gate_from_db(pid, q, config, parent_plan_id)  → (ok, reasons, report)
ok=False ⇒ reasons 并入机器门 reasons ⇒ 计划不激活、重派仲裁者
```

纪律：

- 只读（SELECT `row_to_json`），不写任何表；
- 无 `parent_plan` 或父计划不存在 ⇒ 跳过（空矩阵，放行）；
- 任何异常 ⇒ `WARN 继承契约机检跳过`（门自身不得成为播种单点故障）；
- `TP_INHERITANCE_CONTRACT_OFF=1` 关闭；`TP_INHERITANCE_CONTRACT_CONFIG=<json>` 注入配置；
- 每次裁决在 `campaign_dispatcher.log` 留一行 `继承契约机检 plan X←Y: verdict=… 缺口=… 拦截=… digest=…`。

## 9. 工装

```bash
# 真实谱系回放（只读；落载荷/矩阵/裁决/MD/图/digest 到 artifacts/）
python tools/lineage_contract_audit.py --parent 173 --child 388 \
    --out-dir artifacts/m8058 --figure artifacts/m8058/coverage_matrix_p173_p388.png
# 播种路径端到端（与 dispatcher 同一入口）
python tools/lineage_contract_audit.py --seeding-check 438 --out-dir artifacts/m8058
# 离线裁决（快照两文件）
python -m ops.taskplane.inheritance_contract --parent p.json --child c.json --json-out g.json --md-out g.md
```

## 10. 局限与适用范围（honest）

1. **覆盖 ≠ 质量**：门只证明"子波有判据点名该缺口"，不证明判据阈值合理、不证明重测真做过。
   判据的实测值仍由节点级继承门（`inheritance_evidence_contract`）裁决。
2. **词覆盖有假阳/假阴可能**：中文二元组对同义改写不敏感（"骤加质量"vs"附加质量"），
   指标锚点对改名不敏感（`err_a` vs `error_a`）→ 复杂谱系建议子节点显式写 `covers_gaps`。
3. **升格规则是政策不是物理**：`escalate_unaccepted_gaps` 反映了"未验收节点的局限=欠账"的
   组织判断；政策变更需改 config 并留痕，不可写在代码里偷偷改。
4. **本题不产生船舶物理量**：真实回放里的数字是**账目数字**（缺口数/覆盖数/digest），
   不是水动力量；无实验/解析基准可比，故 3% 误差判据在报告 ⑥ 中显式声明不适用并给替代可判口。
5. **存量计划的回溯是审计不是重判**：已 active 的子波（如 388）不会被门回溯拦截，
   回放只回答"如果当时有门，会不会拦"。