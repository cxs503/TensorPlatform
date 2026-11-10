# 交付语义冻结验收证据（p92w2b-n08 / 重开 PR#873）

本目录是交付语义分类器（`backend/api/domain/delivery_state.py`）的**冻结验收证据**，
由 `backend/tests/test_delivery_semantics_eval.py` 直接读取并做 sha256 钉住复核。

## 为什么在 `quality/` 而不在 `runs/`

`ops/taskplane/tp_pusher.py` 的推送黑名单 `_EXCLUDE = ^(artifacts?/|runs/|results[-/]|REPORT_GATE)`
把所有 `runs/` 下的文件当「纯运行记录」滤除，永不上 GitHub。因此历史上 4 个把证据提交到
`runs/p92w2b-n08/` 的提交（`41cafb9d` / `ab5b653f` / `3bf1629f` / `52f53333`，含 PR#880、#907 的重开）
**在 main 上都不存在**，
语料测试只能永久 skip（或如本轮：presence 测试直接红）。证据搬到 `quality/` 后
（与 `quality/maturity/evidence/` 同源先例）哈希钉住才可执行，守卫才不是装饰品。
`backend/tests/test_delivery_semantics_eval.py::TestEvidenceHomeIsPushable` 机器守住
这条路径规则：证据目录一旦被挪回黑名单前缀，测试立刻红。

## 文件

| 文件 | 内容 | sha256（`metrics.json` 内钉住） |
|---|---|---|
| `evidence/preregistration.json` | 预注册：阈值/口径/停止规则（动手前写定） | `91573cc9…5eef` |
| `evidence/corpus_population.json` | 1000 单成功任务的抽样元数据与分层计数 | — |
| `evidence/corpus_eval.json` | 60 单评估集（派生事实，标注单元） | `75e0f9b8…409` |
| `evidence/corpus_eval_raw.json` | 60 单原始 mission result 载荷（只读 API 抓取） | — |
| `evidence/labels_human.json` | 人工标注（细则 v1.1，逐单理由） | `5071cc5b…2bf` |
| `evidence/metrics.json` | 全部被引用数字的唯一来源（脚本产出，零手抄） | — |
| `tools/evaluate.py` | 纯测量脚本：读冻结工件 → 重算 `metrics.json` | — |

## 复现

```bash
python3 quality/delivery_semantics/tools/evaluate.py      # 重算并覆盖 evidence/metrics.json
cd backend && python3 -m pytest tests/test_delivery_semantics_eval.py tests/test_delivery_state.py -q
```

`evaluate.py` 只读冻结工件（不访问网络、不改分类器），因此对当前 main 重跑应逐位
复现 `metrics.json` 的**全部指标**；唯一会变的是 `git_head`（测量时的 HEAD 版本戳，
重跑即更新，属预期而非数据漂移——判据只钉 corpus/labels 的 sha256）；若 corpus/labels 被改动，`test_metrics_pins_match_committed_artifacts`
会红（提示重跑本脚本），这正是「数字不得手抄」的机器执行面。

## 结论（本轮在 main `2f15b10a` 上重算）

| 变体 | macro-F1 | 准确率 | complete 召回 / 精确 |
|---|---|---|---|
| A0 上线版（修复前） | 0.2154 | 0.2500 | 0.000 / 0.000 |
| A1 平凡基线（非空即 complete） | 0.2271 | 0.5167 | 1.000 / 0.5167 |
| A2 预注册冻结版 | 0.8667 | 0.9333 | 0.9032 / 0.9655 |
| **A2 当前版** | **0.9226** | **0.9667** | **0.9677 / 0.9677** |

判据 a–e 全部达成（`metrics.json: all_criteria_met = true`，fail-closed 8 项负控
0 违例，领域词命中 0）。详细论述见 `docs/delivery-semantics/REPORT-p92w2b-n08.md`。