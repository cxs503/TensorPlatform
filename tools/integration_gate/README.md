# tools/integration_gate — 跨域集成与互裁门（平台机制，零领域语义）

把一个战役里多个验证面的产出**折算成同一套证据等级**、把跨域口径冲突**显式检出**、
把缺口**结构化**交给下一波仲裁者。代码里没有任何物理域词条：域数据（节点自证、
互裁对、量测轨迹、判据阈值、叙述）全部从 `data/campaigns/<campaign>/` 注入。

## 组成

| 模块 | 职责 |
|---|---|
| `model.py` | 证据等级代数（REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED + cap/weaker）、判据/自证/轨迹/检查/互裁对数据模型 |
| `schema.py` | 工件 schema 校验（fail-closed）+ **由数字重算判据结论**（自述 PASS 但数字越界 → FAIL） |
| `provenance.py` | 确定性 JSON / sha256 / 只读 git 事实（fork 点、领先数、main 漂移、是否已合并） |
| `sampler.py` | 声明式量测：`callable` / `script` / `expression` 三种轨迹，provenance 钉住被测模块 sha256 |
| `compare.py` | 七类一致性检查：`trace_agreement`、`endpoint_agreement`（极限端）、`jump_ratio`（切换点不连续）、`metric_below`、`metric_agreement`、`profile_deviation`、`degenerate`（驱动非零而响应恒零） |
| `baseline.py` | 基线对齐：MERGED / BRANCH_ONLY / EXTERNAL_REPO / UNKNOWN；非 MERGED 强制 `interface_contract` |
| `ledger.py` | 能力台账合并 + 定级规则 R1–R5 + honest FAIL 保护 + 内容 sha256 |
| `report.py` | 播种报告 JSON（节点/互裁/台账/基线/缺口/下一波 seed/I1–I5 自检）+ markdown |
| `ingest.py` | 报告 markdown / `acceptance.json` → 节点自证（按配置取数、重算结论） |
| `docgen.py` | 六要素论文级报告（数字全部从 seed_report.json 注入） |
| `figures.py` | 轨迹对照 / 互裁矩阵 / 台账等级三张图 |
| `cli.py` | 一条命令跑完整门 |

## 定级规则（写进台账产物，可独立复算）

- **R1** `level = min(claim_level, 判据上限, 互裁上限)`
- **R2** honest FAIL 不可覆盖：`capability_gate` 判据 FAIL ⇒ 等级 ≤ MOCK；基础台账 honest_fail 条目不得抬高
- **R3** 未合并分支/外部仓证据 ⇒ `status=PROVISIONAL`（等级不虚高，注明不可从 main 复现）
- **R4** 条目排序 + 台账内容 sha256 确定性
- **R5** 无证据 ⇒ WITHHELD；无自证节点也登记为 `:unattested`（不许"漏登记"当"没能力"）

判据角色：`capability_gate`（验证实现本身，FAIL 压等级）vs `case_verdict`（对算例的
工程判决，FAIL 是结论不是缺陷，不压等级）。

## 用法

```bash
# 1) 从原始工件摄取出节点自证
python3 -c "from pathlib import Path; from tools.integration_gate.ingest import ingest_campaign; ingest_campaign(Path('data/campaigns/p158w2'))"
# 2) 跑门（轨迹 → 互裁 → 台账 → 报告 → 图）；--strict 时 I1–I5 未全满足退出码 3
python3 -m tools.integration_gate.cli --campaign p158w2 --data data/campaigns/p158w2 \
    --out artifacts/campaigns/p158w2 --repo-root . --main main --self-repo TensorPlatform \
    --base-ledger artifacts/capability_ledger/capability_ledger.json \
    --pytest-summary artifacts/campaigns/p158w2/pytest_summary.json --figures --strict
# 3) 论文级报告
python3 -m tools.integration_gate.docgen --report artifacts/campaigns/p158w2/seed_report.json \
    --prose data/campaigns/p158w2/doc_prose.json --out docs/P158W2-N08_INTEGRATION_GATE.md \
    --figures artifacts/campaigns/p158w2/figures
# 4) 测试（合成域名，零领域语义）
python3 -m pytest tests/test_integration_gate.py -q
```

## fail-closed 行为

| 情况 | 行为 |
|---|---|
| 工件 schema 违约（缺字段/未知检查/非法等级/坏 sha256） | 退出码 2，写 `gate_error.json`，不产出报告 |
| 轨迹导入失败 / 脚本非零退出 / 超时 | `status=error`，相关检查 `WITHHELD`（绝不 AGREE） |
| 对照侧缺失 / 指标不可用 | `WITHHELD` + `detail.missing` |
| 表达式含函数外调用/属性访问/未知名字 | 抛错（AST 白名单，防注入） |

## 战役实例

`p158w2`（plan 440）的实例配置与产物：
`data/campaigns/p158w2/`（nodes/traces/pairs + raw 工件快照 + 算例 runner + 域词表 + 叙述）、
`artifacts/campaigns/p158w2/`（traces/ledger/seed_report/figures）、
`docs/P158W2-N08_INTEGRATION_GATE.md`（论文级报告）。