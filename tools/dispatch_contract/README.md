# dispatch_contract —— 派单前契约就绪门（fail-closed，零领域语义）

## 解决什么问题

战役 DAG 节点的 `blocked` 常常是「上游没交付 / 交付口径不符」，但历史上这件事
只能靠人肉发现：节点被派下去，agent 花完预算才发现要用的工件不存在或单位不对。
本模块把这件事变成**派单前的机器判定**：

1. **契约声明**（`dispatch-contract/v1`）：每个节点声明 `input_contract` /
   `output_contract`，每条需求写清 artifact 类别、必需字段、单位/口径、版本下界。
2. **就绪检查器**（`readiness.assess`）：拿派单时刻的上游注册表快照逐条核对，
   任一 `required` 需求未满足 → `BLOCK`（fail-closed）。判定只用 schema/配置，
   **不看任何领域词条**。
3. **降级语义**：节点没声明契约 → `READY + degraded=CONTRACT_MISSING`（恒等放行，
   不崩、不拦）；检查器自身任何异常 → 恒等放行并在返回结构里留痕。
4. **历史回测**（`backtest`）：对历史终态派单做回测，出混淆矩阵 / PR 曲线 /
   打乱标签负控，判据数字落 `artifacts/dispatch_contract/backtest_report.json`。

## 目录

| 文件 | 职责 |
|---|---|
| `contract_schema.py` | 契约 v1 静态校验、单位归一、版本兼容 |
| `readiness.py` | 就绪判定（fail-closed + 降级 + 永不抛异常） |
| `corpus.py` | 从历史任务快照推导回测语料（契约推断 + 派单时刻回放，无未来信息） |
| `backtest.py` | 混淆矩阵 / PR / AUC / 打乱标签负控 / 平衡设计 |
| `lint.py` | 契约缺陷体检（无契约 / 无输出声明 / 无判据 / 孤立输入工件） |
| `dispatch_hook.py` | 派单链路挂钩（off/report/enforce 三模式，永不抛异常） |
| `figures.py` | 报告图（数字全部从 JSON 读，零手抄） |
| `cli.py` | 命令行：`check` / `build-corpus` / `backtest` / `lint` |

配置：`config/dispatch_contract.v1.json`（阈值、状态集、单位别名、语料正则全在此，
代码里零硬编码）。

## 用法

```bash
# 1) 判定一份派单声明
python3 -m tools.dispatch_contract.cli check --dispatch artifacts/dispatch_contract/example_dispatch.json

# 2) 从历史任务快照推导回测语料
python3 -m tools.dispatch_contract.cli build-corpus --source <missions_snapshot.json> \
    --out artifacts/dispatch_contract/corpus_v1.jsonl

# 3) 回测 + 出图
python3 -m tools.dispatch_contract.cli backtest \
    --corpus artifacts/dispatch_contract/corpus_v1.jsonl \
    --out artifacts/dispatch_contract/backtest_report.json
python3 -m tools.dispatch_contract.figures \
    --report artifacts/dispatch_contract/backtest_report.json \
    --outdir artifacts/dispatch_contract

# 4) 契约缺陷体检
python3 -m tools.dispatch_contract.cli lint --corpus artifacts/dispatch_contract/corpus_v1.jsonl
```

## 派单链路接入

```python
from tools.dispatch_contract.dispatch_hook import pre_dispatch
res = pre_dispatch(node_id=node, contract=contract_doc, upstream=registry_snapshot)
# 环境变量 DISPATCH_CONTRACT_MODE=off|report|enforce 控制拦不拦（默认 report）
```

## 结论摘要（见 `docs/P233W1-N09_dispatch_contract_gate.md`）

* 机制正确性：契约语义、口径（单位/版本）校验、降级放行、永不抛异常，40 条 pytest 全覆盖。
* 契约覆盖率是硬上限：2113 个历史终态战役节点中只有 **203 个（9.6%）** 自带机器
  可解析的 `input_contract`，其余 1910 个只能降级放行。
* 结局预测能力 **honest FAIL**：以「节点最终 failed/stopped」为标签，门在可判子集上
  precision 0.173 / recall 0.487，AUC 0.475（平衡设计 0.503）≈ 随机。
  输入不可得的节点 blocked 率（0.173）甚至**低于**输入可得的节点（0.215）——
  「上游没交付是 blocked 主因」这一假设被 203 例回测否定。
* 因此本门的定位是**契约符合性闸门（元数据正确性）**，不是交付结局预测器；
  要让它具备预测力，平台侧必须先补两件事：强制节点声明契约、在 finish 里记录
  `blocked_cause` 归因字段（历史 303 个 blocked 节点里 111 个 `fail_stage` 为 None）。