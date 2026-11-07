# 证据链批量入账 CLI（evidence_chain）：把战役入账从手工变工具

**节点** IN17-B3 · 判例 #610 清洗 · 仓库 TensorPlatform · 分支 `agent/m5013` ·
报告生成于程序化注入（`tools/evidence_chain/gen_report_m4960.py`，数字零手抄）。

## ① 案例介绍

### 背景

战役（campaign）每推进一步都会产出一批**数值证据**：参数扫描曲线、一致性核对 JSON、图。
这些证据若要能被后续自回归循环复用，必须先进入平台证据链（`tensorphysics_runs` 登记表）。
平台的登记契约存在，但它只存元数据：

> *The endpoint does not itself ingest files, verify remote object existence, enforce the
> complete nested JSON Schema, or claim solver correctness.* (`docs/tensorphysics-run-registry.md`)

于是「工件 → 登记」之间留下一条必须由人补的缝：算哈希、抄摘要、粘贴请求、坏件回滚、
中断后不知道登到哪。本节点交付把这条缝封上的工具 —— **证据链批量入账 CLI**。

演示用的证据本体是**合成解析函数数据**（纯数学函数生成，不依赖任何物理域 API）：

### 参数表

| 参数 | 符号 | 值 | 来源 |
|---|---|---|---|
| 主项系数 | α | 2.5 | 合成参数 |
| 幂指数 | β | 0.8 | 合成参数 |
| 振荡幅值 | γ | 1.2 | 合成参数 |
| 振荡频率 | δ | 0.15 | 合成参数 |
| 扫描范围 | x | 10–90（9 点） | 扫描设置 |
| 设计工况 | x_d | 50：y_total=58.2887，dy/dx=0.9770 | 端点输出 |
| 函数形式 | — | y = α·x^β + γ·sin(δ·x) | 解析函数 |

证据形态（被入账的工件）：

| run_id | 工件 | 角色 | validation_status |
|---|---|---|---|
| `m4960-r1-synthetic-sweep` | `sweep.csv`、`sweep.json`、`README.md` | scan-curve / raw-payload / case-note | `not_run`（如实：该 run 未做数值基准验证） |
| `m4960-r2-design-point-x50` | `design_point.json`、`check_consistency.json` | design-point / internal-check | `not_run` |
| `m4960-r3-tampered` | 篡改后的 `design_point.json` | 负控 | — |
| `m4960-r4-broken` | 截断的 `run_manifest.json` | 负控 | — |
| `m4960-r5-consistency-note` | `note.json` | 判据声明 | `not_run` |

## ② 怎么做的

### 工具链与流程

```
tools/evidence_chain/evidence_chain.py   （1 个文件，argparse 双子命令 build / ingest）
    build  : 扫工件目录 → 逐文件 sha256+size → 产出 RunManifest（契约 v1，词表取自
             backend/contracts/research_run_status_vocab.v1.json，只读）
    ingest : 发现 manifest（≤2 层）→ 结构校验 → 逐文件复算 → 对账 → 登记（sink）→ 落账
```

四个设计决定，正对应四条硬判据：

1. **状态台账 + 结算语义（断点续传）**：`state.json` 记录每条 run 的
   `manifest_sha256` 与结算 outcome。重跑时若摘要相同且已结算，直接判
   `already_registered` 并**跳过 sink 调用** —— 中断/断网后重跑只补缺口，不重复登记。
2. **隔离而不阻断**：单条 run 的坏件只把**它自己**推进 `quarantine.json`（含逐工件
   declared/actual sha256 与 size），同批其它 run 照常登记，进程退出码 2 作为信号。
3. **报告双视图**：`ingest_report.json` 分 `evidence_state`（证据状态，幂等视图）与
   `rows/counts`（执行视图）。幂等判据作用在前者与落盘文件字节上 —— 否则任何「本轮做了
   什么」的报告天然每轮不同，度量会变成假门。
4. **不假装已验证**：`status`/`validation_status` 必须由生产者显式声明；远端 `s3://`
   URI 无法本地复算哈希 → 标 `artifact_uri_unsupported` 隔离，绝不写 `verified`。

### 关键设置

* 摘要规范化与端点一致：`json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)`
  → sha256，跨机可复现（与 `backend/api/tensorphysics_runs.py::_canonical_json` 同式）。
* 登记落点三选一：`local`（JSONL，端点语义的本地等价实现，幂等 + 409）、
  `http`（真端点 `POST /api/v1/tensorphysics/runs`，只读使用，Bearer 从环境变量取）、
  `none`（只校验）。
* 敏感信息零落盘：token 只从 `--token-env` 指定的环境变量读，不进台账、不进报告。

### 四相实验（演示编排，`tools/evidence_chain/demo_m4960.py`）

| 相 | 输入 | 目的 |
|---|---|---|
| A | 2 个干净 run | 首轮登记必须成功 |
| B | 同一批再跑 | 幂等重入 diff=0 |
| C | 篡改件 + 非法 JSON + 新干净件 | 坏件隔离、好件不被阻断、结构拒收 |
| D | 同 C，`--dry-run` | 零副作用 + 预测等于实做 |

## ③ 计算结果

### 3.1 四相入账结果（数字取自 `artifacts/m4960/report_metrics.json`）

| 相 | registered | already | quarantined | rejected | conflict | failed | 工件复算 | 退出码 |
|---|---|---|---|---|---|---|---|---|
| A 全新批（2 干净 run） | 2 | 0 | 0 | 0 | 0 | 0 | 5/5 | 0 |
| B 幂等重跑 | 0 | 2 | 0 | 0 | 0 | 0 | 5/5 | 0 |
| C 混合批（篡改 + 非法 JSON + 新干净件） | 1 | 0 | 1 | 1 | 0 | 0 | 1/2 | 2 |
| D dry-run | 0 | 1 | 1 | 1 | 0 | 0 | 1/2 | 2 |

### 3.2 幂等重入逐字节比对（判据 I1）

| 落盘文件 | A 轮 sha256 | B 轮 sha256 | 差异 |
|---|---|---|---|
| `registry.local.jsonl` | `050b68da4f9bf36f…` | `050b68da4f9bf36f…` | 0 字节 |
| `state.json` | `a74522895c085f25…` | `a74522895c085f25…` | 0 字节 |
| `quarantine.json` | `0a2e6c1461753792…` | `0a2e6c1461753792…` | 0 字节 |
| `ledger.jsonl` | `6fad929ca203002d…` | `6fad929ca203002d…` | 0 字节 |

`diff=0` = **True**；`evidence_state` 段一致 =
**True**；B 轮 `registered` =
0（期望 0）；B 轮 `already_registered` =
2（期望 2）。dry-run 零副作用 =
**True**。

### 3.3 隔离报告（判据 I2/I3）

| run_id | outcome | reason | declared sha256 | actual sha256 |
|---|---|---|---|---|
| `（manifest 不可解析，无 run_id）` | `rejected` | `manifest_unparsable` | — | — |
| `m4960-r3-tampered` | `quarantined` | `artifact_hash_mismatch` | `4c8eb763288c7d9a…` | `ad81dbed4ad42fc9…` |

篡改件故意做成**等长改写**（`123.456 → 123.457`），size 不变 —— 只有哈希能抓住它，
这排除了「靠大小蒙对」的伪验证。

### 3.4 独立复核：工具算的 sha256 vs coreutils `sha256sum`（负控，防自证）

| 工件 | bytes | 工具 sha256 | sha256sum | 一致 |
|---|---|---|---|---|
| `batch_clean/r1_synthetic_sweep/README.md` | 165 | `a8d3b2108eea1cb9…` | `a8d3b2108eea1cb9…` | ✔ 一致 |
| `batch_clean/r1_synthetic_sweep/sweep.csv` | 422 | `3057563e2a881aa1…` | `3057563e2a881aa1…` | ✔ 一致 |
| `batch_clean/r1_synthetic_sweep/sweep.json` | 1182 | `c34ed55aecea6ba2…` | `c34ed55aecea6ba2…` | ✔ 一致 |
| `batch_clean/r2_design_point/check_consistency.json` | 298 | `365d1f050a25a48b…` | `365d1f050a25a48b…` | ✔ 一致 |
| `batch_clean/r2_design_point/design_point.json` | 135 | `aa8bb0ffe52c3eda…` | `aa8bb0ffe52c3eda…` | ✔ 一致 |
| `batch_mixed/r3_tampered_artifact/design_point.json` | 46 | `ad81dbed4ad42fc9…` | `ad81dbed4ad42fc9…` | ✔ 一致 |
| `batch_mixed/r5_consistency_note/note.json` | 283 | `85951b368ac44ad7…` | `85951b368ac44ad7…` | ✔ 一致 |

### 3.5 已入账状态台账（`artifacts/m4960/chain/state.json`）

| run_id | 工件数 | manifest_sha256 | outcome | validation_status |
|---|---|---|---|---|
| `m4960-r1-synthetic-sweep` | 3 | `1bcb90b14069e163…` | `registered` | `not_run` |
| `m4960-r2-design-point-x50` | 2 | `2a0d2dcc5595d715…` | `registered` | `not_run` |
| `m4960-r5-consistency-note` | 1 | `2c447cfbafb748cc…` | `registered` | `not_run` |

### 3.6 被入账证据的内容（合成数据扫描，`sweep.csv`）

| x | y_main | y_osc | y_total | dy/dx |
|---|---|---|---|---|
| 10 | 15.7739 | 1.1970 | 16.9709 | 1.2746 |
| 20 | 27.4640 | 0.1693 | 27.6334 | 0.9204 |
| 30 | 37.9872 | -1.1730 | 36.8141 | 0.9750 |
| 40 | 47.8176 | -0.3353 | 47.4823 | 1.1292 |
| 50 | 57.1631 | 1.1256 | 58.2887 | 0.9770 |
| 60 | 66.1395 | 0.4945 | 66.6341 | 0.7179 |
| 70 | 74.8201 | -1.0556 | 73.7645 | 0.7695 |
| 80 | 83.2553 | -0.6439 | 82.6114 | 0.9844 |
| 90 | 91.4817 | 0.9645 | 92.4462 | 0.9203 |

## ④ 结果图

**图 1 四相入账结果：每次调用各 outcome 计数（A 全新批 / B 幂等重跑 / C 混合批 / D dry-run）。B 相对 A 的 registered 归零、C 里坏件被隔离而好件照常入账，是 I1/I2 判据的可视化。**

![fig1_ingest_outcomes](artifacts/m4960/fig1_ingest_outcomes.png)


**图 2 每个已入账 run 的工件复算数与 manifest 摘要前 8 位。入账前的 sha256 逐文件复算覆盖率为 100%，隔离件不计入。**

![fig2_artifact_verification](artifacts/m4960/fig2_artifact_verification.png)


**图 3 被入账的证据本体：合成解析函数扫描（y = α·x^β + γ·sin(δ·x)），数据由纯数学函数生成，不依赖任何物理域 API。**

![fig3_synthetic_sweep](artifacts/m4960/fig3_synthetic_sweep.png)

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **「报告 diff=0」是个陷阱**：第一版把幂等判据压在整份 `ingest_report.json` 上，
   必然失败 —— 报告如实记录「本轮 registered=2 / 本轮 already=2」，两轮天然不同。
   改为双视图（`evidence_state` 幂等视图 + 执行视图）后判据才既可证又非恒假。
   同源坑：`ledger.jsonl` 首版为每次 resumed 跳转也追加一行 → 台账膨胀、diff 不为 0；
   现改为**只记状态跃迁**，重入零写入。
2. **dry-run 必须读台账**：首版 dry-run 不读 `state.json`，导致它把「已入账」的 run 预测成
   「将登记」，预测与实做对不上（假阴性演练）。现 dry-run 只读台账、不写盘，预测=实做
   （已由 `test_dry_run_prediction_matches_subsequent_apply` 钉住）。
3. **远端 URI 的诚实性**：`s3://` 工件在本工具里**无法**验证，只能隔离。若为了「批量成功」
   把它当已验证，就等于把 WITHHELD 洗成 REAL —— 这条红线写进了测试
   （`test_verify_artifacts_detects_hash_size_missing_and_remote`）。
4. **端到端真登记未打通（WITHHELD）**：本容器内 TensorPlatform 后端未挂载
   `/api/v1/tensorphysics/runs`（`:8000` 该路径 404），故 `--sink http` 只在本地 stub 服务上
   验证了 200（created/created=false）、409（冲突）、503（可重试）三条路径。真后端 + Postgres
   联调列为本节点遗留项（不改冻结面、不绕路直连数据库）。
5. **并发范围**：同 `--out-dir` 无文件锁，仅串行重入安全；要多实例并行需各自 out-dir。
6. **适用范围**：本工具是「元数据+工件一致性」闸门，不替代各域数值验证器；manifest 的
   `validation_status` 是生产者声明，工具照抄不背书。
7. **域无关性**：本演示使用纯合成数据（解析函数），证明证据链机制不依赖任何物理域。
   域特定数据应通过 API/配置/工件注入，不应硬编码进 backend/tools/tests。

## ⑥ 误差判定（逐项，显式回答「是否 <3%」）

| 关键量 | 基准 | 实测差异 | 是否 <3% |
|---|---|---|---|
| 逐文件 sha256（全部 7 个工件） | coreutils `sha256sum` | 0 个不符（0 bit） | **是（0%）** |
| 幂等重跑证据字节（4 个落盘文件） | A 轮字节 | 0 字节差异 | **是（0%）** |
| 坏件检出力（1 个篡改件 + 1 个非法 JSON） | 应检 2 件 | 检出 2/2 = 100% | **是（漏检率 0%）** |
| dry-run 预测 vs apply 实做 | apply 的 outcome 计数 | 各 outcome 计数完全一致 | **是（0 偏差）** |
| 设计点 y_total（x=50） | 解析函数复算 | 58.2887 vs 解析值（0 偏差，同源回放） | **不适用于精度声明** |
| y_total 对实验/文献基准 | — | **未评估（WITHHELD）** | **否 —— 无基准（合成数据）** |

最后两行是本案的诚实边界：入账工具证明的是「工件与声明一致、登记幂等」，**不证明数值
精度**；演示证据的 `validation_status` 因此如实写 `not_run`（各域验证器才有权裁绝对精度）。
本节点不主张任何物理量精度指标，也不把同源回放的一致当成对实验基准的验证。

## 审计链

| 项 | 值 |
|---|---|
| 工具 | `tools/evidence_chain/evidence_chain.py`（sha256 `18832f3b3727b0d1beeb332229856a83…`） |
| 演示编排 | `tools/evidence_chain/demo_m4960.py`（sha256 `5e277ac0335f6af59e3ba248f55b48a4…`） |
| 报告生成 | `tools/evidence_chain/gen_report_m4960.py`（sha256 `5010d28985f57ab68bdfc8fb92b591bf…`） |
| 回归 | `tests/test_evidence_chain_m4960.py` —— 21 passed（含空目录/非法 JSON/契约违规/冲突/远端 URI/HTTP 409+503/跨批台账负例） |
| 判据数字 | `artifacts/m4960/report_metrics.json`（`criteria.all_passed = True`） |
| 账本 | `artifacts/m4960/chain/{registry.local.jsonl, state.json, quarantine.json, ledger.jsonl, ingest_report.json}` |
| 证据工件 | `artifacts/m4960/evidence/batch_clean/`、`artifacts/m4960/evidence/batch_mixed/` |
| 数据来源 | synthetic-data-generator（generated synthetic data: 9 scan points + design point at x=50.0） |
| git 分支 / HEAD | `agent/m5013` / `e5cfd4aa` |

**判据总表**：corrupted_file_quarantined=True, dry_run_zero_side_effects=True, hash_cross_checked_by_coreutils=True, idempotent_rerun_diff_zero=True, idempotent_rerun_evidence_state_identical=True, invalid_json_rejected=True, rerun_registered_is_zero=True, valid_row_not_blocked_by_bad_row=True

冻结面纪律：本次改动**只新增** `tools/evidence_chain/`、`tests/test_evidence_chain_m4960.py`、
`artifacts/m4960/`、`docs/reports/m4960_evidence_chain_report.md`；
未修改 `backend/`、未改表结构、未新增/变更端点、未动既有测试。
