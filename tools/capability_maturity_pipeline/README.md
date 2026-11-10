# 能力成熟度自治管线（p156w2-n07）

把「人工升账」变成机器可判的自治闭环：**证据扫描 → 矛盾检测 → 自动判定 → 台账更新
→ 自动回滚 → 哈希链审计**。全程无人工裁量点，任一环节的结论都能被重算与被追溯。

```
artifacts/ runs/ tensor_reports/ docs/
        │  EvidenceScanner（复用 capability_ledger_maturity 的解析器）
        ▼
   证据项 EvidenceItem ──► detect_conflicts / detect_supersessions
        │                         │（后续证据推翻先前基座）
        ▼                         ▼
   AdjudicationEngine ──────► MaturityLedger.apply_decision
   REAL/WITHHELD/MOCK/…       INIT / UPGRADE / DOWNGRADE /
                              ROLLBACK / REBASIS / HOLD
                                   │
                                   ▼
                             AuditChain（每条事件 sha256 + prev 链接）
                             台账旁挂 <ledger>.head 锚文件（防尾部截断）
```

## 与既有模块的分工（不重复实现）

| 模块 | 职责 | 本模块的关系 |
|---|---|---|
| `tools/capability_ledger_maturity` | 证据采集器、裁定引擎、阈值契约、快照式升级工作流 | **直接复用**采集器与裁定引擎；该模块的 `UpgradeWorkflow` 是「只升不降 + 快照回滚」 |
| `tools/capability_auto_audit` | 分类器 + 台账更新 + 审计日志 | 互补：本模块的审计链是**前驱链接**式（可证明历史未被改写），并把回滚做成**证据驱动** |
| **本模块** | 补齐缺失闭环段：证据驱动**自动回滚**（含变更链）、防篡改审计链、锚点、幂等、确定性 | — |

## 五段闭环的实现要点

1. **证据扫描**（`pipeline.EvidenceScanner`）
   受限根目录 + 文件大小上限 + 排除自身输出（`artifacts/capability_maturity/**`）；
   单文件解析异常不中断整轮（记入工件 `scan_errors`）；证据按 `sha256` 去重。
   能力归属：工件内显式 `capability_id` 优先，其次是**数据化归因规则**
   （`--attribution` 指向 glob→capability_id 的 JSON，代码内零能力名）；
   未归属证据进入独立桶，**不参与升账**（宁可不判，不可乱判）。
2. **矛盾检测**（`contradiction`）
   「同一个量」的身份 = (能力, 指标名, 参照值)；无参照值的指标只在**同一工件被重写**
   时可比。推翻需同时满足：同量 + 新证据时间不早 + 相对偏离 > 预声明阈值（默认 1%）。
3. **自动判定**（复用 `AdjudicationEngine`）
   阈值在运行前声明并计算指纹（`thresholds_sha256`）写入链，事后不可改判。
4. **台账更新**（`ledger.MaturityLedger`）
   每个能力记录 `level / decided_at / event_seq / evidence_refs / rule_fired /
   thresholds_sha256 / superseded_refs / rollback_of / history`——
   等级变更是「可追溯到具体一次裁定 + 具体证据 sha256」的。
5. **回滚 + 审计**（`ledger` + `chain`）
   * 基座证据被推翻且等级下降 → `ROLLBACK`，`rollback_of` 指回确立旧等级的事件 seq；
     非矛盾驱动的下降 → `DOWNGRADE`（两者在链上可区分，避免把「新证据更差」误记成「推翻」）。
   * 回滚是**追加事件**，历史只增不改；世代回放 `rollback_to_generation` 亦追加事件。
   * `AuditChain`：`event.sha256 = H(seq, type, cap, payload, prev_sha256, ts)`；
     台账落盘同时写 `<ledger>.head` 锚（链头+链长+台账 sha256）。
     **尾部截断只有锚点能发现**——仅有 prev 链接时，删掉末尾事件后的链仍然自洽。

## 用法

```bash
# 一轮闭环（写台账 + 运行工件）
python3 -m tools.capability_maturity_pipeline.cli run \
  --worktree . \
  --ledger artifacts/capability_maturity/ledger.json \
  --out artifacts/capability_maturity/run.json \
  --roots artifacts runs tensor_reports docs \
  --attribution artifacts/capability_maturity/attribution.json

# 校验（链 + 锚 + 台账↔链一致性）；失败退出码 1
python3 -m tools.capability_maturity_pipeline.cli verify \
  --ledger artifacts/capability_maturity/ledger.json

# 查看等级与判定依据
python3 -m tools.capability_maturity_pipeline.cli levels \
  --ledger artifacts/capability_maturity/ledger.json

# 世代回放（追加 MANUAL_ROLLBACK 事件，不删历史）
python3 -m tools.capability_maturity_pipeline.cli rollback \
  --ledger artifacts/capability_maturity/ledger.json --to-generation 0
```

程序化调用：

```python
from tools.capability_maturity_pipeline import MaturityPipeline, PipelineConfig

run = MaturityPipeline(worktree, config=PipelineConfig(
    roots=(Path("artifacts"), Path("runs")),
    attribution=PipelineConfig.load_attribution(cfg_path),
)).run(persist=True, ledger_path=ledger_path)
print(run.artifact["levels"], run.artifact["core_sha256"])
```

## 自治运行（定期扫描）

`ops/capability_maturity_loop.sh` 是纯壳（无业务判断）：按节拍跑 `cli run`，
`flock` 防重入，每轮工件落 `runs/capability_maturity/<ts>.json`，失败只记日志不退出。

```bash
CAP_MATURITY_INTERVAL=1800 nohup ops/capability_maturity_loop.sh \
  >> logs/daemon_capability_maturity.log 2>&1 &
```

## 可复现性与验收

* **确定性**：同输入 + 同注入时钟 → `core_sha256` / 链头 / 等级完全一致
  （证据观测时间统一记为本轮扫描时刻，避免文件解析时刻抖动污染指纹）。
* **幂等**：同批证据重复扫描 → `HOLD`，**不写链**（链长与链头不变）。
* 实跑与判据数字见 `artifacts/capability_maturity/report.json`，
  图文报告见 `docs/p156w2_n07_capability_maturity_pipeline.md`。

测试：

```bash
python3 -m pytest tests/capability_maturity_pipeline/ -q     # 29 项
python3 scripts/p156w2_n07_maturity_pipeline_report.py       # 重算全部判据+图
```

## 已知局限（诚实清单）

1. **归属是前提**：真实工件普遍不带 `capability_id`（实跑：672 条归属证据中显式
   标注 0 条，全靠归因规则）。没有归属就没有升账，管线只能报告缺口。
2. **可比性极稀**：实跑中带参照值（可与独立复算对照）的证据仅 1 条，
   跨来源同量对照 0 组 → 矛盾检测在真实数据上几乎无事可做（这不是检测器弱，
   是证据未按「同一个量」组织）。
3. **裁定按能力聚合最大误差**：异构指标混在同一能力下会偏保守（文本报告里
   正则抓到的数字可把能力压到 MOCK，实跑 `cap.platform.documentation`
   max_error 226415%）。消融实验显示：仅用结构化证据时该能力组的判定会变化，
   因此报告同时给出「全证据 / 仅结构化」两套等级。
4. **文本证据不可信**：`.md` 报告的数字是散文抽取，只应作为线索；
   结构化字段（`error_pct` + `reference` + `tolerance`）才是可判证据。
5. **锚点是单点**：`<ledger>.head` 与台账同目录，若攻击者同时改两者则锚失效；
   上线时应把锚随提交/外部存证固定（平台侧 `chain_head_sha256` 亦记录在运行工件中）。