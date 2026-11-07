# IN27-H3 — 协调官 harness 手术执行权（金丝雀门 + 备份 + 验证 + 自动回滚 + 红队演练）

## 1. 案例介绍（背景/对象/参数）

战役 DAG 节点 IN27-H3（依赖 H2）：授予协调官（coordinator）**门控内**的
harness 手术执行权。对象=一个真实可执行的 harness 探针文件
`harness_probe.py`（参考模型 `compute(x)=3x`，植入系数故障 `2x`）。
门链：`DIAGNOSE → CANARY → BACKUP → SURGERY → VERIFY → COMMIT | ROLLED_BACK`，
金丝雀门失败 → `REJECTED`（目标文件字节不动）。

| 参数 | 值 |
|---|---|
| 参考模型 | compute(x) = 3x |
| 植入故障 | compute(x) = 2x |
| 红队注入 | 系数 3 → 999（错误手术） |
| 审计 | append-only JSONL，事件含 actor/human 标志 |
| 备份 | 术前整文件拷贝 + sha256 一致性校验 |

## 2. 怎么做的（方法/流程/工具链）

新模块 `tools/autonomy/harness_surgery.py`（TensorPlatform 自治域，模块原先
不存在，按领域目录新建）：
- `AuditLog`：JSONL 审计链，事件=回执（seq/ts/stage/actor/human/detail）。
- `HarnessSurgery.execute(ticket)`：五段门链，任一门失败即止；回滚=从备份
  精确还原字节（sha256 相等为证）。
- `diagnose / make_repair_ticket / run_closed_loop`：真实诊断→修补单→执行
  的无人闭环；`red_team_drill`：注入错误手术，要求被回滚门拦截。
- 测试 `tests/test_harness_surgery_in27.py`（pytest，6 用例）。
- 产物脚本 `runs/in27_h3/make_artifacts.py`：程序化生成报告/审计/图（零手抄）。

## 3. 计算结果（判据数字，程序化注入）

pytest：`6 passed in 0.47s`（python3.11，本机）。

| 判据 | 结果 | 期望 | 判定 |
|---|---|---|---|
| C1 金丝雀门过→自动执行，无人工 | status=COMPLETED, human_events=0 | COMPLETED / 0 | PASS |
| C2 术前备份哈希=术前哈希 | 相等（loop_report.json） | 相等 | PASS |
| C3 术后验证探针 | [true, true, true] | 全 true | PASS |
| C4 失败手术自动回滚 | ROLLED_BACK，哈希还原 | ROLLED_BACK | PASS |
| C5 红队注入错误手术被拦 | verdict=PASS, blocked=true | PASS | PASS |
| C6 闭环审计链 | CANARY→BACKUP→SURGERY→VERIFY→COMMIT | 同左 | PASS |

`acceptance.json` 含全部判据数字与产物路径；`all_pass=true`。

## 4. 结果图

- `runs/in27_h3/fig1_closed_loop_timeline.png`：闭环审计事件时间线
  （5 事件，actor 全为 coordinator，human 全为 false）。
- `runs/in27_h3/fig2_gate_verdicts.png`：三场景门判定
  （REJECTED / ROLLED_BACK / COMPLETED）+ 红队判定 PASS。

## 5. 注意点与局限

- 锚点替换为精确文本匹配，非语义补丁；多行/重排补丁需升级 patch 引擎。
- 审计链为本地 JSONL（无 DB 依赖，可重放可 diff）；跨进程并发写需加锁。
- 验证探针=参考模型数值核对；非数值型 harness 需换探针集。
- 手术权严格限定在门链内：金丝雀门/验证门任一失败即止，无绕过路径。

## 6. 误差判定

本节点为门控/审计类能力，无连续物理量：关键量=门判定（布尔）与哈希一致性
（精确相等），误差 0；「<3%」判据不适用，原因=无连续解算量。
红队演练为可失败门：注入错误必须被拦，本次被拦（blocked=true，字节还原）。

## 7. 审计链

`runs/in27_h3/loop_audit.jsonl`（闭环 5 事件）、`rt_audit.jsonl`（红队 4 事件）、
`rej_audit.jsonl`（金丝雀拒绝 2 事件）；`acceptance.json` 汇总判据与产物路径。
