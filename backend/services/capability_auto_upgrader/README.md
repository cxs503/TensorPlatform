# services/capability_auto_upgrader — 证据链驱动的能力等级自动升级/降级

把"能力证据等级"从**一次性的静态判据**变成**证据时间序列上的断言**：一个能力位只有在最近
`promotion_window`（默认 3）次**互不相同**的运行里，每次都同时满足 `pytest 通过率 ≥ 90%`
且 `对标误差 < 15%`，才被自动抬到 `REAL`；一次结论性回归失败（红测试或误差越界）就按台阶下退
一级（下限 `MOCK`）。每次升降都是一条带 `sha256` 哈希链的 `feature_ledger` 追加事件。

节点：**P362-W2-N01**（mission 13850）。报告见 `docs/p362w2_n01_capability_auto_upgrader.md`，
判据工件见 `runs/p362w2_n01_capability_auto_upgrader/acceptance.json`。

## 为什么不是又一套"规则表"

既有 `tools/capability_upgrade`（P348-W3-N01）判的是**证据集合的静态完整性**（锚点数/最大误差/
独立复核），降级只由缺陷注入触发。本模块补的是时间维度：

| 缺口 | 本模块的做法 |
|---|---|
| 同一份报告重放三次 = "跑了三次" | run digest 只覆盖证据内容（不含墙钟），重放返回 `duplicate_run`，streak 不动 |
| 判级没有持续性要求 | 连续 `promotion_window` 次不同运行全过才升级；抖动（好坏交替）永不升级 |
| 证据退化无人自动回退 | 结论性失败 → 逐级下退，事件带 `level_before` 与失败 run digest；历史只追加 |
| 外部审计与台账不一致时谁说了算 | 审计 API 的变化记成 `observed`/`mismatch` 事件，**从不改等级**；等级由台账重放推导 |
| 缺证被误当反证 | `insufficient`（无执行测试/样本不足）断 streak 但**不降级** |

等级阶梯与平台一致：`NOT_SUPPORTED < STUB < MOCK < WITHHELD < REAL`。

## 结构

| 文件 | 职责 |
|---|---|
| `model.py` | 内容寻址证据模型（junit 摘要、对标误差**现场推导**、run digest、去重） |
| `policy.py` | 阈值数据类 + 策略 digest（窗口/通过率/误差/样本数/降级步数/下限） |
| `streak.py` | `judge_run`（单次判定）、`analyse`（尾部连续计数）、`decide`（纯函数决策） |
| `ledger.py` | `feature_ledger` 追加写（JSONL + 平台同构 SQLite 表）、sha256 哈希链、重放推导、篡改检出 |
| `store.py` | 证据 run 存储 + 评估日志（每次决策都留痕，含 hold） |
| `watcher.py` | `audit_capabilities` 负载解析 + 快照 diff（added / level_changed / removed） |
| `upgrader.py` | 编排：入账 → 计数 → 决策 → 追加事件 → 校验 |
| `scanner.py` | 零领域语义自检（词表在 `config/capability_auto_upgrader/deny_list.json`） |
| `api.py` | FastAPI 适配层（`/status` `/events` `/verify` `/runs` `/audit-snapshot`），不含规则 |
| `figures.py` / `acceptance.py` / `cli.py` | 4 张图 + 论文级报告 + 一键验收 |

## 用法

```python
from services.capability_auto_upgrader import CapabilityAutoUpgrader, UpgradePolicy
from services.capability_auto_upgrader.model import EvidenceRun, TestEvidence, BenchmarkEvidence

with CapabilityAutoUpgrader("runs/auto_upgrader") as up:
    up.ingest_audit_snapshot(api_payload)          # 监听 audit_capabilities
    run = EvidenceRun(
        capability_id="synth.widget_alpha",
        seq=1,
        tests=TestEvidence.from_junit_xml("junit.xml"),     # 报告 sha256 自动入证
        benchmark=BenchmarkEvidence(measured=1.02, reference=1.0, n_samples=32),
    )
    print(up.record_run(run)["decision"])          # promote / demote / hold
    print(up.levels(), up.verify())
```

命令行：

```bash
python -m services.capability_auto_upgrader.cli accept      # 全量验收（跑 pytest + 出图 + 出报告）
python -m services.capability_auto_upgrader.cli status --dir runs/auto_upgrader
```

## 验证

```bash
cd backend && python -m pytest tests/test_capability_auto_upgrader.py -q
```

39 条用例覆盖：窗口边界（2 vs 3 次）、通过率 90%/89%、误差 15.0%/15.01%、抖动、重放、证据不足、
降级窗口可配、降级下限、审计监听与 mismatch、哈希链与两处篡改（JSONL / SQLite）、策略校验、
junit 解析、HTTP 适配层（200/422/409）、零领域语义扫描。

## 边界

* 本模块**不产生证据**，只管理等级随证据演化；它信任上游 junit/对标记录的真实性（由
  `report_sha256`/`source_sha256` 与平台审计链承担）。
* 单进程写入假设（JSONL `fsync` + 只追加，SQLite 镜像）；多实例部署前需加锁或改平台数据库事务。
* 只做平台共性机制：零领域语义，所有能力名/阈值/词表都由数据与配置注入。
## 可复用规则卡（knowledge distillation）

**规则（Title/rule）**：能力等级只能由"**互不相同的连续运行序列**"升级，且"缺证 ≠ 反证"——
证据不足断 streak 但不降级；任何等级变化必须是**带内容哈希的新行**，历史只追加不修改。

**适用边界（context and applicability）**：适用于"有可重复执行的测试 + 可与参考值对比的数值"
的能力位（端点、算法、内核）。不适用于：只有一次性验收的能力、无法机器复算的证据（纯文档/纯人工
评审结论）、需要跨进程并发写入的多实例台账（本模块单进程假设，见边界节）。

**证据与出处（evidence/provenance）**：节点 P362-W2-N01（mission 13850），仓 TensorPlatform，
分支 `agent/m13850`；判据工件 `runs/p362w2_n01_capability_auto_upgrader/acceptance.json`
（sha256 见报告"工件与审计链"节）；升级事件 sha256、策略 digest、junit 报告 sha256 均落报告；
复算命令 `python -m services.capability_auto_upgrader.cli accept`（一次性重放全部数字）。

**机制（mechanism）**：run digest 覆盖证据内容（不含墙钟）→ 重放退化为 `duplicate_run`；
`analyse` 只数尾部连续通过次数；`decide` 是纯函数；`ledger` 用 `prev_sha256`/`sha256` 链住每一行，
等级由重放推导，因此外部审计只能"记录"不能"覆盖"。

**推荐流程（recommended procedure）**：① junit 报告进 `TestEvidence.from_junit_xml`（自动带
报告 sha256）；② 数值对标进 `BenchmarkEvidence(measured, reference, n_samples)`（error 现场算）；
③ `record_run` 交给机制决策；④ 周期 `verify()`；⑤ 阈值要改就改 `UpgradePolicy`（digest 随事件
入账，历史可追）。

**反例与局限（counterexamples/limitations）**：把 `recorded_at` 计入 digest → 重放可刷 streak
（本节点实际踩坑，见报告 ⑤-1）；把缺测试当失败 → 一条未跑的回归就能把 `REAL` 打下去（用
`insufficient` 类别挡住）；期望"一次通过即升级" → 窗口 ≥3 会拒绝，这是刻意设计；自动化判成
`STUB`/`NOT_SUPPORTED` → 不做，红测试无法证明实现不存在。

**回归护栏（regression guard）**：
`backend/tests/test_capability_auto_upgrader.py` 39 条用例把上述约束钉死（窗口边界、90%/89%、
15.0%/15.01%、抖动、重放、缺证、降级窗口、降级下限、哈希链、两处篡改、HTTP 200/422/409、
零领域语义扫描）；`acceptance.py` 在报告里逐项声明 True/False，任何一项退化都会使
`all_passed=false` 并在报告中保留原值（不修饰）。

**评审状态 / 最近核验**：本节点自查（acceptance all_passed=true，2026-11-11 核验）；
待独立席位按 S×E 复判。
