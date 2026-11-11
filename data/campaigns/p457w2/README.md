# p457w2 战役集成门数据包（n09）

本目录是 `p457w2-n09-integration-gate` 的**声明与证据数据包**。门代码在
`tools/integration_gate/`（零领域语义），本目录承载全部域语义与证据出处。

## 文件

| 文件 | 作用 |
|---|---|
| `nodes.json` | 8 个交付节点的仓/mission/分支/兄弟 worktree，以及每个节点的 `ingest` 配置（从验收工件按路径取数，判据由 `op`/`threshold` 重算） |
| `attestations/*.json` | 由 `ingest` 从 `raw/` 机器产出的节点自证（含来源工件 sha256）；**不要手改** |
| `raw/` | 节点验收工件快照（n02 criteria.json、n04 acceptance.json、n07 scan_report.json），sha256 记入 attestation |
| `assumptions.json` | 跨节点共享假设登记表（标准版本、常数、双段幂律参数 + 律自洽表达式）；由 `extract_assumptions.py` 从被测源码机器抽取 |
| `interfaces.json` | 5 条数据交换接口契约（载荷/单位/认证区间/需求区间/降级护栏/文档引用） |
| `pairs.json` | 3 对互裁面及其检查（逐点轨迹比对、标量比对、标量阈值） |
| `traces.json` | 5 条可复算轨迹（4 条 runner 脚本 + 1 条同节点双路径） |
| `runners/` | 轨迹 runner：只读加载兄弟 worktree 实现，输出 `{"rows": [...], "meta": {...}}`，meta 里记录被测模块 sha256 |
| `archive_spec.json` | 工件快照路径与排除规则 |
| `platform_observed_levels.json` | n07 标注器对平台 21 能力的独立观测（供台账增量比对） |
| `doc_prose.json` | 综述文档的六要素叙述（数字一律由 `seed_report.json` 注入，不写死在叙述里） |
| `extract_assumptions.py` | 机器抽取共享假设（含继续性条件表达式与源文件 sha256） |
| `publish_ledger_delta.py` | 把台账增量落成**追加式**平台台账增量文件（不改基础台账） |

## 复现

```bash
# 时间戳固定到基线提交，保证同一输入逐字节可复现（工件快照才能稳定校验）
export SOURCE_DATE_EPOCH=$(git log -1 --format=%ct main)
TP_SIBLING_ROOT=/root/tp-agent/work python3 data/campaigns/p457w2/extract_assumptions.py
python3 -c "from pathlib import Path; from tools.integration_gate.ingest import ingest_campaign; ingest_campaign(Path('data/campaigns/p457w2'))"
python3 -m tools.integration_gate.cli --campaign p457w2 --data data/campaigns/p457w2 \
  --out artifacts/campaigns/p457w2 --repo-root . --main main --self-repo TensorPlatform \
  --base-ledger artifacts/capability_ledger/capability_ledger.json \
  --interfaces data/campaigns/p457w2/interfaces.json \
  --assumptions data/campaigns/p457w2/assumptions.json \
  --archive-spec data/campaigns/p457w2/archive_spec.json \
  --platform-observed data/campaigns/p457w2/platform_observed_levels.json \
  --pytest-summary artifacts/campaigns/p457w2/pytest_summary.json --figures --strict
python3 data/campaigns/p457w2/publish_ledger_delta.py
python3 -m tools.integration_gate.docgen --report artifacts/campaigns/p457w2/seed_report.json \
  --prose data/campaigns/p457w2/doc_prose.json --out docs/P457W2-N09_INTEGRATION_GATE.md \
  --figures artifacts/campaigns/p457w2/figures
```

## 依赖外部状态（诚实声明）

- `runners/*` 与被抽参数的源码位于兄弟 worktree（`/root/tp-agent/work/m13986|m13988|m13989|m13991`），
  `TP_SIBLING_ROOT` 可覆盖。这些 worktree 未并入各自 main 时，节点自证一律标 `PROVISIONAL`。
- 兄弟 worktree 里若有**未提交**改动，本门只按文件 sha256 记录当时内容，不保证可重现；
  报告 §3.3 的 git 事实（领先/漂移）是运行时快照。