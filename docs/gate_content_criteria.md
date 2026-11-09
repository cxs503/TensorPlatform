# 门判据内容化（C2b）：从「工件存在」到「判据数字可复算」

**节点**：IN383-TEST01（TensorPlatform，服务层 `ops/taskplane/`）
**日期**：2026-11-09
**产物**：`artifacts/gate_content_criteria/`（`ab_matrix.json` / `ab_matrix.png` / `ab_matrix.md` /
`cli_selfcheck.json` / `cli_selfcheck.md`）

---

## 1. 案例介绍（物理背景 → 机制背景）

平台是 12 仓租户制、任务由战役 DAG（`campaign_plans` / `campaign_nodes`）驱动。DAG 上的
**门（gate）节点**决定下游节点能否放行；放门由常驻 daemon
`ops/taskplane/gate_auto_release.py` 以纯函数 `evaluate_gate()` 自动判定（owner 渐退令：
机制替身不依赖人类会话）。

放门判据三条：C1 上游全 `done`；C2 门 metadata 声明的判据工件「可解析」；C3 非治理门。
其中 **C2 的语义只到「存在」**：路径能解析到文件、字节数 > 0、`.json` 能被 `json.load`
解析。也就是说，本节点任务书里最常见的验收语句「判据数字与产物路径落报告 JSON」在旧门下
**完全不可机验**——下面这份空洞工件同样触发放行：

```bash
mkdir -p artifacts/x && echo '{}' > artifacts/x/verdict.json
# 旧门 → action=release, reason=satisfied
```

这不是假想缺陷：本轮 8 个合成场景里，旧门对 6 个场景放行，其中 5 个是**误放行**
（空洞工件 / 陈旧超标数字 / 图摘要漂移 / 路径逃逸 / 报告仍留 `TODO` 占位）。

## 2. 怎么做的（方法 / 流程 / 工具链）

### 2.1 设计约束

* **零领域语义**：不得出现任何船型/物理量/域判据硬编码；判据只经 `metadata` 工件注入。
* **向后兼容**：未声明 `checks` 的门，判定逐字段不变（老测试全绿）。
* **决策可离线复算**：纯函数 + 只读文件，无时钟、无网络、无 DB；同一输入同一结论。
* **证据纪律**：证据不足**绝不**折算成 PASS；已有反证不能被「别处证据不足」稀释。

### 2.2 新增模块 `ops/taskplane/gate_content_criteria.py`

门 metadata 增量声明：

```json
"gate_criteria": {
  "artifacts": ["artifacts/demo/verdict.json"],
  "checks": [
    {"id": "residual", "kind": "json_pointer",
     "path": "artifacts/demo/verdict.json", "pointer": "/checks/residual",
     "op": "<=", "value": 0.03},
    {"id": "fig_bytes", "kind": "min_bytes",
     "path": "artifacts/demo/field.png", "value": 4096}
  ]
}
```

* `kind`：`exists` / `min_bytes` / `max_bytes` / `sha256` / `json_pointer` /
  `json_count` / `text_contains` / `text_regex`；
* `op`：`== != < <= > >= ~=（带 tolerance） in contains`；
* JSON Pointer 按 RFC6901（`~0`/`~1` 转义、数组下标）；
* 路径解析复用放门既有语义（存在 + 非空 + 仓归属 `metadata.repo`），并**新增逃逸防护**
  （`..` 段 / realpath 落在 roots 之外 → 拒绝）。

### 2.3 三态裁决（关键语义）

| 裁决 | 触发 | 门的动作 |
|---|---|---|
| `PASS` | 每条声明判据都被实测值满足 | 可放行（＋C1/C3） |
| `FAIL` | 判据**可判且被违反**（阈值不达标 / 摘要不符 / 逃逸 roots / 类型冲突） | 继续等（`content_fail`），**绝不写 failed** |
| `WITHHELD` | 证据不足（文件缺失 / JSON 半写 / 指针不存在 / 值为 null） | 继续等（`content_withheld`） |
| `NOT_DECLARED` | 未声明 checks | 旧行为不变 |

合取优先级：**FAIL 优先于 WITHHELD**——已被实测否证的判据不会因为别处证据不足而被
「挂起来等」。声明本身不合法（未知 kind/op、缺必填字段、重复 id、非法 digest）→
`content_spec_invalid` 且**只等不放**，不静默跳过。

`digest` = 全部 check 结果的规范化 sha256，且**路径无关**（剔除 `evidence.resolved_path`，
保留 `size_bytes`/`sha256`），因此同一内容在任何检出位置得同一摘要，跨机复核有效。

### 2.4 接线（最小侵入）

`evaluate_gate()` 增加 C2b 段：`content` 子块进 `checks`（含 verdict/digest/逐条实测值），
仅当裁决阻断时改判 `wait`。审计表 `detail` 因此自带内容级证据链。

## 3. 计算结果（`artifacts/gate_content_criteria/ab_matrix.json`）

每个场景独立临时根目录 + 合成夹具（零领域语义），双路径同跑：

| 场景 | 说明 | 旧门 action | 新门 action | 内容裁决 | digest(前12) |
|---|---|---|---|---|---|
| content_ok | 数字齐备且达标 0.012 ≤ 0.03，图摘要相符 | release | **release** | PASS | 见产物 |
| hollow_object | 空洞工件 `{}` | **release（误）** | wait | WITHHELD | 见产物 |
| stale_number | 数字陈旧/超标 0.412 > 0.03 | **release（误）** | wait | FAIL | 见产物 |
| corrupt_json | JSON 半写 | wait | wait | WITHHELD | 见产物 |
| empty_file | 0 字节 | wait | wait | WITHHELD | 见产物 |
| digest_drift | 数字合规但图 sha256 不符 | **release（误）** | wait | FAIL | 见产物 |
| path_traversal | 判据路径 `../` 逃逸 roots | **release（误）** | wait | FAIL | 见产物 |
| placeholder_text | 报告仍留 `TODO` 占位 | **release（误）** | wait | FAIL | 见产物 |

汇总（机读，`ab_matrix.json:counts`）：

```json
{"scenarios": 8, "old_released": 6, "old_waited": 2,
 "new_released": 1, "new_waited": 7,
 "new_verdict_match": 8, "false_release_prevented": 5}
```

即：**旧门 6/8 放行、其中 5 例为误放行；新门仅放行 1 例（唯一内容齐备者），8/8 裁决与
预先声明的预期一致。**

图 `ab_matrix.png`：左=逐场景「旧门 / 新门」判定矩阵（release / wait(WITHHELD) / wait(FAIL)）；
右=三态裁决分布（PASS 1 / FAIL 4 / WITHHELD 3）。

CLI 自检（`cli_selfcheck.json`）：用本模块给自身产物下判 4 条 check
（`scenarios>=8`、`new_verdict_match==8`、`rows/1/new_reason=="content_withheld"`、
`图 >= 5000 B`）→ `PASS`，4/4 PASS。

## 4. 注意点（踩坑 / 局限 / 适用范围）

1. **相对导入**：`gate_auto_release.py` 历史上既能 `from ops.taskplane import ...`，也被测试
   以「顶层模块」（`sys.path` 含 `ops/taskplane`）导入。首次接线用 `from . import` 直接把
   `tests/test_autonomy_acceptance_gate.py` 打成 `ImportError`；已改为 try/except 双路导入。
   （教训：改老模块的 import 形态前先 `grep -rn "import <module>" tests/`。）
2. **digest 不能吃绝对路径**：初版把 `evidence.resolved_path` 纳入摘要，临时目录重跑即不
   一致；已剔除，改为内容级证据（size/sha256）+ 实测值。
3. **演示夹具会串味**：初版「先全部落盘、后统一求值」导致 8 个场景共享最后一份
   `verdict.json`（新门也误放行）。已改为每场景独立根目录 + eval 时 setup；并由
   `test_reproducible_in_tmp` 钉死（重算 digest 必须逐行一致）。
4. **适用范围**：本模块只判「工件里声明的值」，不做统计/物理正确性判断（那属于域仓的
   算法验收面）。门仍然只由 C1/C2/C2b/C3 决定，`failed` 状态永不由 daemon 写入。
5. **未做**（诚实边界）：未把 `checks` 回填进存量战役节点的 metadata（需策略决定哪些门值得
   内容化）；未接 DB 端到端演练（本环境无 psql，`PsqlStore` 路径未在此验证，仅
   `MemoryStore` 走通 `run_once`）。

## 5. 误差判定（vs 基准，逐项）

本任务是机制层，基准 = **同一批夹具下旧门的判定（A/B 对照组）**，不是物理参考量。

| 关键量 | 实测 | 基准/预期 | 偏差 | 是否 <3% |
|---|---|---|---|---|
| 误放行场景数（旧门） | 5 | 5（每个误放行场景设计上就是缺陷） | 0 | 是（0%） |
| 新门裁决与声明预期一致数 | 8 | 8 | 0 | 是（0%） |
| 复算 digest 一致性（临时目录重跑） | 8/8 逐行相同 | 8/8 | 0 | 是（0%） |
| 老门行为回归（未声明 checks） | 10 项老用例全绿 | 全绿 | 0 | 是（0%） |

唯一未绿项：`tests/test_gate_auto_release.py::test_committed_drill_artifact_holds_acceptance_invariants`
因 `artifacts/gate_auto_release_drill.json` 在本分支从未存在（`git ls-files` 计数 0，
`git log` 无记录）——**pre-existing，与本次改动无关**，未粉饰。

## 6. 复算

```bash
python3 ops/taskplane/gate_content_criteria_demo.py           # 重生成 A/B 产物 + 图
python3 -m pytest tests/test_gate_content_criteria.py \
                 tests/test_gate_content_criteria_demo.py -q  # 58 passed
python3 ops/taskplane/gate_content_criteria.py \
    --metadata /tmp/gcc_meta.json --roots . \
    --json-out artifacts/gate_content_criteria/cli_selfcheck.json \
    --md-out  artifacts/gate_content_criteria/cli_selfcheck.md
```