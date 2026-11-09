# 谱系继承契约机检成熟化：把「继承局限节」从文字声明变成可复算门（m8058 / P27W7-N10）

**结论先行**：新增 `ops/taskplane/inheritance_contract.py`（纯函数机检 + 播种门），并接进
`campaign_dispatcher._validate_new_plan` 成为播种前标准检查项。真实谱系回放（plan 173 → 388）：
父波 11 个节点的【已知局限】**11/11 全部提取**（提取率 100%），子波判据覆盖 **0/11**，
其中 2 条来自 blocked 节点、升格为阻塞级（晃荡耦合、下水三阶段），**播种拦截触发** →
若当时有门，plan 388 的播种会被拦截并要求补判据。复算两次逐字节一致（`matrix_digest`
`ab4ec385bb4d2c57` 前 16 位）。在飞 draft 计划 438 走同一入口：`verdict=warn 缺口=3 拦截=0`
（父波 70 的 3 个 blocked 节点"欠账未记账"被揪出，但不阻塞）。

---

## ① 案例介绍（问题背景 / 输入 / 参数表）

**工程背景**：自主战役以 DAG 波次自持推进。子波任务书里有一句固定承接语——
"本波继承父波局限节与 KPI 缺口"。这句话此前是**纯文字声明**：

- 没有任何机器知道父波到底欠了几笔账；
- 子波可以在零判据、零书面理由的情况下被播种；
- 父波的 honest FAIL（failed/blocked 节点）与报告「已知局限」**静默蒸发**，
  指挥链上只留下"已继承"三个字。

**本节点的物理目标（元层面）**：把该声明变成门。判据形态是**账目型**（缺口数/覆盖数/
拦截率/digest），不产生水动力量。

**输入（只读，字节镜像落盘）**：

| 输入 | 大小 | sha256（前 24 位） | 说明 |
|---|---|---|---|
| `artifacts/m8058/parent_p173_payload.json` | 11 节点 | `ceec1d2a3fc61cac9671d058…` | 父波 plan 173（P27W6，11 节点：done 9 / blocked 2） |
| `artifacts/m8058/child_p388_payload.json` | 10 节点 | `f151263495386965304d878f…` | 子波 plan 388（P27W7，本波） |
| 平台库 `campaign_plans`/`campaign_nodes` | — | 只读 SELECT | 载荷由 `row_to_json(t)::text` 单列取回 |

参数表（门配置，全部运行输入注入，不写死在代码里）：

| 参数 | 默认 | 本回放取值 | 含义 |
|---|---|---|---|
| `min_token_coverage` | 0.5 | 0.5 | 纯文字缺口的词覆盖下限 |
| `min_shared_terms` | 2 | 2 | 最少共享词数 |
| `anchor_coverage` | 0.5 | 0.5 | 指标锚点被命中的比例下限（且至少 1 个） |
| `require_machine_checkable` | true | true | 判据须含算符/数值锚，散文不算覆盖 |
| `include_goal` | false | false | 子节点任务书散文不顶判据 |
| `severity_by_kind` | honest_fail/kpi_gap=block；limitation/status_*=warn | 同默认 | kind→严重度 |
| `escalate_unaccepted_gaps` | true | true | 未验收（failed/blocked）节点的局限升格为 block |
| `blocking_severities` | ["block"] | ["block"] | 未覆盖且属此集合 ⇒ 拦截 |

## ② 怎么做的（方法 / 流程 / 工具链）

- **实现**：`ops/taskplane/inheritance_contract.py`（633 行）——纯函数、零领域语义
  （不认识任何船型/物理量名，缺口键/严重度/阈值全部 config 注入）。
- **四路提取**：A 计划 metadata 缺口键；B 节点 metadata 缺口键；C 节点 goal 正文按
  `【标题】` 切段的「局限节」（`【已知局限】/【继承局限】/【未完成】/【欠账】`，标题含
  FAIL/未完成 → honest_fail）；D 派生：`status ∈ {failed, blocked}` 且无书面局限声明 →
  「欠账未记账」缺口。去重按 `(kind, 归一化文本)` 合并并保留全部溯源 pointer，
  `gap_id = kind-sha256(kind|text)[:10]`。
- **覆盖判定**（机械可解释）：① explicit（子节点 `covers_gaps` 点名 gap_id/alias）；
  ② lexical（判据词袋 vs 缺口词袋：有指标锚点时锚点必须命中，纯散文缺口按中文二元组覆盖率；
  且**判据必须机器可算**——"已充分理解"这类散文记 `criterion_not_machine_checkable` 近失，不算覆盖）。
- **升格政策**：failed/blocked 父节点的局限 = 未偿欠账 → 升格 `block` 并留痕
  `escalated_from`（否则真实数据里 100% 局限是 warn 级，门永远不响）。
- **确定性**：行按 `(severity!=block, kind, gap_id)`、列按 `(ext_id, node_id)` 排序；
  三层 digest（gaps/matrix/gate）= 规范 JSON sha256；与输入顺序无关。
- **播种接线**：`_validate_new_plan(pid)` 在原机器门后追加 `seeding_gate_from_db`；
  只读、无父计划即跳过、异常降级 WARN、`TP_INHERITANCE_CONTRACT_OFF=1` 可关、
  `TP_INHERITANCE_CONTRACT_CONFIG=<json>` 注配置。
- **工具链**：`tools/lineage_contract_audit.py`（真实谱系只读回放 + run1/run2 复算比对 + 出图）；
  `python -m ops.taskplane.inheritance_contract`（离线快照裁决，block→退出码 2）。
- **测试**：`tests/test_inheritance_contract.py` **28 条**（含 2 条显式负控、确定性/顺序无关/
  digest 敏感、DB 载荷解析（含 0x02 分隔炸点）、CLI 退出码、dispatcher 接线静态回归）。

## ③ 计算结果（程序化注入，零手抄）

### 表 T1 · 真实谱系回放（plan 173 → plan 388）覆盖面

| 项 | 数值 |
|---|---|
| 父计划 / 子计划 | 173 / 388 |
| 父节点数 / 子节点数 | 11 / 10 |
| 父节点含【已知局限】节 / 提取 gap 数（提取率） | 11 / 11（11/11 = 100 %） |
| 覆盖 / 未覆盖 | 0 / 11 |
| 阻塞级未覆盖（拦截） | 2 |
| 覆盖率 | 0.0 |
| 裁决 / intercepted | block / True |
| 复算一致（run1==run2 逐字节） | True |
| matrix_digest | `ab4ec385bb4d2c57786cb2ef53a02a8df3aa585a912a15991e2538064d6903c0` |
| gaps_digest | `d120ec17875eb2131919babbd4e3c88d063c42872d0fe67d764f1e6b0e8cafc7` |
| gate_digest | `6e660db906553ea33e127e83b1d10b3bd658fdd4af6c2850909283d054b41256` |
| payload sha256 (parent) | `ceec1d2a3fc61cac9671d058…` |
| payload sha256 (child) | `f151263495386965304d878f…` |
| 模块 sha256 | `62859e5f5feffb354112c168…` |

### 表 T2 · 逐缺口矩阵（父 173 已知局限 → 子 388 判据）

| gap_id | kind | severity | 状态 | 拦截 | 溯源 | 缺口文本 |
|---|---|---|---|---|---|---|
| `limitation-93d4e83045` | limitation | block | uncovered | YES | node:1078.metadata.goal#【已知局限】 | 已知局限：仅矩形舱；线性理论不含大幅晃荡非线性。 |
| `limitation-c0bfaba4a5` | limitation | block | uncovered | YES | node:1085.metadata.goal#【已知局限】 | 已知局限：二维纵剖面；不含水动力附加质量。 |
| `limitation-04a9095cd9` | limitation | warn | uncovered | no | node:1077.metadata.goal#【已知局限】 | 已知局限：KVLCC2单一船型；三象限近似不含完整四象限。 |
| `limitation-089cdfb9be` | limitation | warn | uncovered | no | node:1083.metadata.goal#【已知局限】 | 已知局限：远场近似；不含非线性传播效应。 |
| `limitation-0f1e6b024d` | limitation | warn | uncovered | no | node:1076.metadata.goal#【已知局限】 | 已知局限：四象限为多项式回归非CFD；不含浅水/风浪影响。 |
| `limitation-171cabb6c3` | limitation | warn | uncovered | no | node:1084.metadata.goal#【已知局限】 | 已知局限：单向耦合；线性Stokes区。 |
| `limitation-25a68f5e84` | limitation | warn | uncovered | no | node:1081.metadata.goal#【已知局限】 | 已知局限：气腔效率为经验值；微气泡分布均匀性假设。 |
| `limitation-971365c8a9` | limitation | warn | uncovered | no | node:1075.metadata.goal#【已知局限】 | 已知局限：仅覆盖单桨单舵；η_R需实验标定。 |
| `limitation-dc4e3036af` | limitation | warn | uncovered | no | node:1079.metadata.goal#【已知局限】 | 已知局限：线性相对运动假设；浅水波近似不含3D效应。 |
| `limitation-f2987a374e` | limitation | warn | uncovered | no | node:1080.metadata.goal#【已知局限】 | 已知局限：仅覆盖稳态风力；Flettner端部效应忽略。 |
| `limitation-f7da1ded5f` | limitation | warn | uncovered | no | node:1082.metadata.goal#【已知局限】 | 已知局限：电池模型简化（库仑计数）；仅覆盖并联构型。 |

### 表 T3 · 播种路径端到端（plan 438 draft，父 plan 70，走 dispatcher 门）

| 项 | 数值 |
|---|---|
| verdict | warn |
| gaps / covered / intercepted | 3 / 0 / 0 |
| by_kind | {"status_blocked": {"n": 3, "covered": 0}} |
| 风险标记（不拦截） | 3 条 |
| matrix_digest | `c8ba6d741b4539c8b991f40566a2a2887212fb7632d7b77fae3abf1bee1844f5` |
| 实际接线日志（campaign_dispatcher.log） | `继承契约机检 plan 438←70: verdict=warn 缺口=3 拦截=0 digest=c8ba6d741b45` |


## ④ 结果图

![coverage matrix](../../artifacts/m8058/coverage_matrix_p173_p388.png)

**图 1**（`artifacts/m8058/coverage_matrix_p173_p388.png`，2295×1091 px，matplotlib Agg 生成，
数据来源 = `artifacts/m8058/gate_p173_to_p388.json`，非手绘）：左图 = 父缺口（行）× 子节点判据（列）
的覆盖度热力图，红框 = 未覆盖的阻塞级缺口（本次 2 条，均来自 blocked 父节点）；
右图 = 按 kind 的覆盖/未覆盖堆叠（本回放 11 条全在 `limitation`，覆盖 0 条）。

**为什么没有"场量云图 / 与解析解比较曲线"**：本节点产出的是**机检门**，不求解任何物理场，
没有场量、没有实验/解析基准曲线可比。替代可比面是账目层（表 T1/T2）：

- 提取完备性：11/11 = 100%（父节点凡有【已知局限】节，必出一条 gap）；
- 拦截完备性：阻塞级未覆盖 2/2 = 100% 触发拦截（`n_intercepted == n_uncovered` 在本回放成立）；
- 复算确定性：run1 vs run2 逐字节一致 = True。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **覆盖 ≠ 质量**：门只证明"子波有判据点名该缺口且有阈值"，不证明阈值合理、更不证明重测做过；
   实测值仍归节点级继承门（`inheritance_evidence_contract`）裁决。
2. **真实数据的缺口形态和设计预期不同**：生产库 `campaign_nodes.metadata` 里**没有任何**
   `honest_fail/kpi_gaps` 键（`jsonb_object_keys` 全表统计：ext_id/goal/repo/acceptance_criteria…
   无缺口键）——欠账只存在于 goal 正文的【已知局限】与 blocked 状态里。所以 C/D 两路
   （goal 局限节 + 状态派生）不是兜底而是**主路**，A/B 两路是给未来播种者写的"机器可读欠账"接口。
3. **升格规则是组织政策**：把 blocked 节点的局限当欠账，是"未验收即未偿"的组织判断；
   改政策必须改 config 并留痕，不能在代码里偷偷改。
4. **词覆盖的假阴/假阳**：同义改写（"骤加质量"vs"附加质量"）会漏，改名（`err_a` vs `error_a`）
   也会漏；复杂谱系建议子节点显式写 `covers_gaps: ["<gap_id>"]`（explicit 路径不依赖词面）。
5. **回放是审计不是重判**：plan 388 已 active，门不会回溯逮捕；回放只回答"若当时有门会不会拦"。
6. **门自身不得成为单点故障**：接线处任何异常降级 WARN，先放行再让守望线查——自主体制里
   一个坏门把整个播种链冻住比漏一次更糟。

## ⑥ 误差判定（逐项，显式回答是否 <3%）

| 关键量 | 基准 | 本实现 | 相对偏差 | 是否 <3% |
|---|---|---|---|---|
| 缺口提取完备性 | 父波书面局限条目 11（11 节点 × 1 条【已知局限】） | 提取 11 | 0.0 % | **是**（100% 提取） |
| 播种拦截完备性 | 阻塞级未覆盖缺口 2 | 触发拦截 2 | 0.0 % | **是**（100% 触发） |
| 复算一致性 | run1 结果 | run2 结果 | 0 字节差异 | **是**（逐字节相同） |
| 负控 A（无缺口父计划） | rows 应为空 | rows = []，verdict=pass | — | **是**（pytest 断言） |
| 负控 B（全覆盖子计划） | blockers 应为空 | blockers = []，不拦截 | — | **是**（pytest 断言） |

**物理量误差是否 <3%**：**不适用并声明理由**——本节点不求解物理场、不产出任何水动力/结构量，
上述五项是本机制的全部可判口，均已逐项达标（0% 偏差或布尔真）。任何把"门通过"读成
"物理精度达标"的解读都是误读：门的输出是**账目**，物理精度由被判据重测的节点自己负责。

---

## 审计链（工件 → 数字）

| 工件 | 内容 | sha256/摘要 |
|---|---|---|
| `artifacts/m8058/parent_p173_payload.json` | 父波载荷（11 节点） | `ceec1d2a3fc61cac9671d058…` |
| `artifacts/m8058/child_p388_payload.json` | 子波载荷（10 节点） | `f151263495386965304d878f…` |
| `artifacts/m8058/gate_p173_to_p388.json` | 裁决 + 矩阵 + run1/run2 digest | gate_digest `6e660db90655…` |
| `artifacts/m8058/gate_p173_to_p388.md` | 人读裁决摘要 | — |
| `artifacts/m8058/seeding_gate_p438.json` | 播种路径端到端（draft 438←70） | matrix_digest `c8ba6d741b45…` |
| `artifacts/m8058/coverage_matrix_p173_p388.png` | 图 1 | 见文件 |
| `artifacts/m8058/report_tables.md` | 表 T1/T2/T3 生成脚本输出 | — |
| `artifacts/m8058/pytest_adjacent.log` | 相邻回归（28+48 条） | 76 passed |
| `ops/taskplane/inheritance_contract.py` | 实现 | `62859e5f5feffb354112c1…` |
| `tests/test_inheritance_contract.py` | 28 条验收测试 | 28 passed |
| `docs/specs/inheritance_gap_contract.md` | 契约文档 v1 | — |

复算入口（第三方一条命令复现表 T1/T2 全部数字）：

```bash
python tools/lineage_contract_audit.py --parent 173 --child 388 \
    --out-dir /tmp/replay --figure /tmp/replay/cov.png
```

## 回归与接线证据

- `python -m pytest tests/test_inheritance_contract.py -q` → **28 passed**；
- `python -m pytest tests/test_inheritance_contract.py tests/test_inheritance_gate.py \
  tests/test_campaign_dispatcher_autonomy_state.py -q` → **76 passed**（无回归）；
- `tests/test_campaign_nodes.py` → 16 passed / 5 skipped（既有跳过项，未变）；
- 接线回归 `test_dispatcher_seeding_flow_is_wired` 在基线（HEAD 版 dispatcher）**失败**
  （`git show HEAD:ops/taskplane/campaign_dispatcher.py | grep -c inheritance_contract` = 0），
  接线后通过——即"改前红、改后绿"；
- 进程内直调 `_validate_new_plan(438)`（与守护同一代码路径，只读）在 `campaign_dispatcher.log` 留痕：
  `继承契约机检 plan 438←70: verdict=warn 缺口=3 拦截=0 digest=c8ba6d741b45`。

## 局限（未完成/未做）

1. 存量 229 个计划的缺口键（A/B 路）为空，机器可读欠账接口尚无真实使用者——需要播种模板
   在仲裁者 goal 里要求写 `kpi_gaps`，本节点只提供门与契约，未改提案模板（下一节点可接）。
2. 词覆盖未做同义词/向量归一（有意：词面向量会引入不可解释的相似度，违背"可复算"纪律）；
   代价是假阴，靠 explicit 路径兜。
3. 未做"拦截后的自动补判据"闭环（拦截 → 回写仲裁者要求补 → 重播）；当前只 fail-closed 拦截 + 留痕。