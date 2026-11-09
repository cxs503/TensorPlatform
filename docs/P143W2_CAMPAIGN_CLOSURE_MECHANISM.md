# P143-W2 战役闭环机制（跨域集成验证门）· 可复用规范 v1

> 节点：P143W2-N12（跨域集成验证与战役验收门）
> 实现：`ops/taskplane/campaign_integration_verify.py`（集成门）、`ops/taskplane/interface_probe.py`（在线接口探针）
> 复用前提：`ops/taskplane/inheritance_gate.py`（单节点验收）、`ops/taskplane/campaign_gate_snapshot.py`（快照/审计叠层）
> 验收测试：`tests/test_campaign_integration_verify.py`（36 条）、`tests/test_interface_probe.py`（10 条）
> 首次实裁：plan 357 / P143-W2（`artifacts/p143w2/`）

## 1. 问题的形状

跨域战役（求解器仓 × 船舶仓 × 平台仓）验收有三个**互相独立**的命题，缺一条就有假通过通道：

| 命题 | 失败长什么样 | 谁裁 |
|---|---|---|
| A. 单节点是否真验收 | 节点 `done` 但没有测量值；判据没测过就报完成 | `inheritance_gate.evaluate_inheritance_gate` |
| B. 域内数值是否互裁一致 | 两家报同一量，差 10 倍没人管 | `inheritance_gate.cross_consistency`（`quantities` 通道） |
| **C. 跨域成果是否可组合** | 每个节点各说各话通过，下游拿到的字段上游根本没产 | **本机制（新增）** |

本机制只做 C，并把 A/B 的结果**原样引用**（不重算、不覆盖），因此不存在"两套判据互相打掩护"。

## 2. 三层结构

```
campaign_nodes/metadata ──┐
tensor_reports(inheritance_evidence) ──┼─► inheritance_gate（A/B：逐节点 accepted + 互裁）
snapshot(plan/milestones/nodes/missions/reports) ──┘
                              │
integration_contract.json ──► campaign_integration_verify（C：形状契约 a + 拓扑契约 b + 汇总 c~f）
probe_config.json + 各域 openapi ──► interface_probe（在线接口在位/漂移）
```

### 2.1 形状契约（判据 a）

两条发布通道，都必须是**真东西**：

1. **平台报告证据包**：报告 `inheritance_evidence` 块按 `schemas/campaign_node_evidence.schema.json`
   校验（必含 `acceptance` 非空、`artifacts` 非空）——跨仓战役里唯一能随快照全量到手的通道；
2. **合约工件**：`integration_contract.json` 里声明的 `artifacts[].path` 必须可解析且过其 `schema`。

缺失 = `missing`（不计通过）；schema 违规 = `invalid`（节点降级为 `unproven`，已 accepted 也压回）。

### 2.2 拓扑契约（判据 b）

DAG 每条边的需求侧 `requires` 必须被**上游已发布证据里真出现的量名**覆盖，做键名/类型/量纲三重比对。

**关键设计：不采信上游自填的 `produces`。** 若允许用合约里自填的 `produces` 去满足需求侧，
需求方写什么都能自洽，检查退化为恒真（循环论证）。所以：

- 供给只来自 `report:evidence` 与工件 JSON 里的量名（`node_supply`）；
- 只有声明没有实物 → 边判 `consistent_declared_only`，**默认不计通过**
  （`allow_declared_only_edges=true` 可显式放开，并在报告里留痕）。

### 2.3 汇总与四分类（判据 c~f）

| 分类 | 定义 | 对门的作用 |
|---|---|---|
| `pass` | 继承门 `accepted` 且形状契约全绿 | 计入 `min_pass_nodes` |
| `honest_fail` | 判据实测越限/未达，但 config `honest_fail` 台账给了 `reason`+`evidence` | 计入 `max_honest_fail_nodes` 预算；**不粉饰、不可覆盖** |
| `unproven` | 在飞 / 无证据 / 判据未测 / 证据形状不合规 | 默认零容忍（`allow_unproven=false`）→ 阻塞 |
| `fail` | 判据实测越限但**未申报**；mission 终态失败；honest_fail 无 evidence 指针 | 一律阻塞（`e_no_undocumented_fail`） |

`unproven` 与 `fail` 分开计数是这套机制的要点：**"还没做完"不等于"失败"**，但两者都不能当通过。

## 3. 六条门判据（机器可算，逐条落 `criteria[]`）

| id | 判据 | 通过条件 |
|---|---|---|
| `a_schema_all_valid` | 形状契约 | 全部合约节点 `artifact_status=valid` |
| `b_edges_consistent` | 拓扑契约 | 无边 `inconsistent` / `unverifiable` / `declared_only` |
| `c_contract_coverage` | 覆盖 | 计划内每个节点都有合约条目 |
| `d_acceptance_budget` | 验收预算 | `pass >= min_pass_nodes` 且 `honest_fail <= max_honest_fail_nodes` |
| `e_no_undocumented_fail` | 诚实纪律 | `fail == 0` |
| `f_no_unproven` | 完成度 | `unproven == 0`（除非显式 `allow_unproven`） |

`verdict = pass` 当且仅当六条全过；否则 `not_ready`。**退出码 0/2/3，未就绪绝不返回 0**
（`2` = not_ready，`3` = 输入/网络错误）—— 这条是拒绝"绿皮报告"的机器闸。

## 4. 本战役实裁结果（2026-11-09 快照，input_digest `c4ee154d4a45…`；离线复算两次逐字节相同，见 §5）

产物：`artifacts/p143w2/integration_gate.json` / `.md`、`interface_probes.json`、
`plan357_snapshot.json`、`solver_repo_probe.json`、`figures/`。

| 判据 | 结果 | 数字 |
|---|---|---|
| a_schema_all_valid | **BLOCK** | valid 0 / 11 节点（全部 `incomplete`：无报告证据包亦无本地工件） |
| b_edges_consistent | **BLOCK** | 2 条边（N05→N07、11 域→N12）均 `consistent_declared_only`：需求侧量名由合约声明，上游实物未发布 |
| c_contract_coverage | PASS | contracted 12 / plan 12，uncovered 0 |
| d_acceptance_budget | **BLOCK** | pass 0 / min 9；honest_fail 0 / 预算 2 |
| e_no_undocumented_fail | PASS | fail 0（诚实纪律未破） |
| f_no_unproven | **BLOCK** | unproven 11 |

结局分布：`pass=0, honest_fail=0, unproven=11, fail=0`（N12 自身按配置排除，防自证）。
在线接口探针：`ok 11/11`（platform openapi v2.2.0 五条、ship openapi v2.0.0 六条，含
`calculations/seakeeping`、`dynamic-stability/analyze`、`maneuvering/zigzag`、
`extended-calculations/vibration`、`calculations/propulsion`、`damage-stability/analyze`）。
求解器仓（TensorLBM/TensorDEM）侧：`unverifiable` —— `run_cluster` 两节点均报
`Failed to connect to github.com port 443`（job `c1794185528_4` / `c1794185681_732`），
**记为 WITHHELD，不推定通过**。

**诚实结论：本战役此刻不满足验收门（not_ready）。** 结构性原因是 11 个前置节点与门节点
被同波次派发、全部在飞（`status=in-flight`，其中 N03/N06 已 `done` 但缺
`reproduction_verdict`，按红队规则不算验收）。门不粉饰、也不替它们判通过；
一旦证据落地，同一命令可复算升级（见 §5）。

## 5. 复用手册（后续谱系波直接照抄）

```bash
# 0) 拿 token（只读快照 + 发报告）
export TENSOR_TOKEN=$(curl -s -X POST $TENSOR_API_BASE/auth/login \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$TENSOR_AGENT_USER\",\"password\":\"$TENSOR_AGENT_PASS\"}" \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')

# 1) 派生合约（从战役表 ext_id/dependencies 自动生成，域内容写在 build_contract.py 的数据表里）
python3 artifacts/p143w2/build_contract.py --snapshot artifacts/p143w2/plan357_snapshot.json \
    --out artifacts/p143w2/integration_contract.json

# 2) 跑集成门（--fetch-api 现拉快照；也可 --snapshot 离线复算）
python3 ops/taskplane/campaign_integration_verify.py --fetch-api --plan 357 \
    --base-url $TENSOR_API_BASE --token "$TENSOR_TOKEN" \
    --contract artifacts/p143w2/integration_contract.json \
    --config   artifacts/p143w2/gate_config.json \
    --snapshot-out artifacts/p143w2/plan357_snapshot.json \
    --json-out artifacts/p143w2/integration_gate.json \
    --md-out   artifacts/p143w2/integration_gate.md      # exit 0=pass 2=not_ready 3=input

# 3) 在线接口探针（各域 openapi 当契约快照）
python3 ops/taskplane/interface_probe.py \
    --document platform=$TENSOR_API_BASE/openapi.json \
    --document ship=http://127.0.0.1:8001/openapi.json --token "$TENSOR_TOKEN" \
    --probes artifacts/p143w2/probe_config.json \
    --json-out artifacts/p143w2/interface_probes.json     # exit 0=全在位 2=缺失/漂移

# 4) 图 + 报告
python3 artifacts/p143w2/gen_figures.py
```

一键：`bash artifacts/p143w2/rerun_closure.sh`（幂等，重跑不改变未变化输入的 `input_digest`）。

新战役只需替换 `/tmp` 之外的三份**数据**：`build_contract.py` 的 `PRODUCES/REQUIRES` 表、
`gate_config.json`（预算/阈值）、`probe_config.json`（接口路径与字段）。
**代码零改动** —— 这是"零领域语义"铁律的直接收益。

## 6. 局限与适用范围（诚实边界）

1. **同波次派发的门节点结构性问题**：DAG 语义要求前置节点先完成，但调度器把整波同时派出，
   门节点必然先看到"全在飞"。本机制的正解不是放宽判据，而是**可复算重跑**：门报告带
   `input_digest`，证据落地后重跑即得新裁决；主动作是"把门做成连续函数"，不是"把门做成一次性事件"。
2. **跨仓工件不可解析**：`ship-performance-platform`/`TensorLBM` 的本地路径在本仓 worktree 里不存在，
   故形状契约主通道用平台报告证据包；本地工件通道保留给平台仓内节点。
3. **求解器仓侧当前 WITHHELD**：集群节点到 GitHub 不通（证据见 `solver_repo_probe.json`），
   故 TensorLBM/TensorDEM 的"main 是否已含交付面"未验；网络恢复后用同一探针重跑即可。
4. **`declared_only` 边的语义**：合约声明的 `produces` 只影响 `b` 判据的降级标记，不影响供给——
   供给只认实物。因此 `b` 判据对上游"说了没做"极其敏感，误报形态是偏保守（BLOCK 而非误放行）。
5. **图内文字为英文**：容器无 CJK 字体（DejaVu 只有拉丁字形），中文标签会渲染成方框；
   图注（caption）在报告侧用中文给出。
6. **`f_no_unproven` 的适用范围**：它度量"本波是否做完"，不是质量判据；历史波次复盘时若只关心
   质量，应以 `a/b/d/e` 四条为准（config 可关掉 `f`）。

## 7. 与平台既有机制的关系（不重复建设）

| 机制 | 管什么 | 与本机制边界 |
|---|---|---|
| `inheritance_gate` | 单节点 accepted + `quantities` 互裁 | 本机制**调用**它，不重写判据求值 |
| `campaign_gate_snapshot` | DB/API → 快照 + 继承门任务书四条审计 | 本机制用同一个快照结构，加了形状/拓扑/四分类 |
| `gate_auto_release` / `wave_cycle` | 放门与波次调度 | 本机制输出 `verdict`+退出码，供放门器直接消费 |
| `report_qa` / `chronicle` | 报告质量与编年 | 本机制只看证据包**形状**与接口**在位**，不评文风 |