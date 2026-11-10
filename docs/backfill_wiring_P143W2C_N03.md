# P143W2C-N03：run-manifest 接进回填器（五元证据链从「只验形态」升到「内容可追溯」）

> 上游节点 IN143-W1-N01 交付了 `tools/evidence_chain/backfill_wiring.py`（目录 → E1/E2/E3 分级）。
> 本节点做**成熟化**：把 manifest 接进回填器，让历史 run 的五元证据链可被机器复算，
> 而不是靠人工填 / 靠「形态合法」放行。所有数字由
> `python3 -m tools.evidence_chain.gen_report_p143w2c_n03` 从实际运行程序化注入（零手抄）。

## ① 案例介绍：问题在哪

历史 run（`runs/`、`artifacts/` 下的 240 条合成语料，形态与真实历史目录同构）大多有三种残缺：
工件散落无 manifest、manifest 声明了但工件已被删、工件被后续改动而 manifest 未更新。
上游 Layer 1 对这三种都只做**形态**判定，且有两个结构性缺口：

| 缺口 | 后果 | 本节点处置 |
|---|---|---|
| `_try_build_manifest` 在 manifest 缺失时**现场生成**一份（摘要由它自己算） | 「run 自己给自己发证」，任何历史目录都能刷到 E3 | `verify_run_content` **不自动生成** manifest；缺失即 `BACKFILL_MANIFEST_MISSING` 封顶 |
| 只校验 manifest 结构与工件「存在」 | 自签、跨 run 混用、伪锚、缺 sha 全能过 | 引入 **独立登记锚**（`registry.local.jsonl` 的 `manifest_sha256`）+ 逐件严格复算 + `data_anchor`/`commit_sha` 绑定 |

**物理/工程对应**：一条结论要「可复算」，必须能回答三个问题——算的是哪份数据（data 工件摘要）、
用的哪版代码（`producer.revision`，即 commit sha）、运行描述有没有被事后改过（manifest 摘要与
外部登记是否一致）。三问答不全 → 证据只能停 E1，不许按申报等级入信用账。

## ② 怎么做的：方法与判据

```
历史评价（outcome_evaluations × 240）
   │  resolve_run_dir（目录布局由调用方注入，回填器不猜）
   ▼
verify_run_content：manifest 存在？→ 契约结构 → digest=sha256(canonical_json)
   │                   → 与独立登记锚比对 → 逐件 sha256+size 复算（含越界拦截）
   │                   → 挑 data 工件 → data_anchor=实算摘要
   ▼
五元形态复核（复用 evidence_chain_link.analyze_chain，同一把尺）
   theory/report 来自评价声明；code←producer.revision；config←configuration_sha256；data←data_anchor
   ▼
三分类 fail-closed：full_chain_granted / capped_missing / verify_failed
   ▼
outcome_evidence_chains（chain_status=complete 才解封）+ outcome_chain_backfill（逐条裁决留痕）
   ▼
双闸：结算面 chain_blocked、提级闸（回填前后评价表行级指纹不变）
```

处置矩阵（`240` 条语料，比例见仓库夹具 `tools/evidence_chain/backfill_fixture.py`）：

| 形态 kind | 条数 | 回填判定 |
|---|---|---|
| `intact` | 146 | 完整链授权 |
| `missing_manifest` | 30 | 缺件封顶（无 manifest） |
| `missing_artifact` | 25 | 缺件封顶（data 工件被删） |
| `tampered` | 20 | 校验失败（单字节篡改） |
| `self_signed` | 15 | 校验失败（无独立登记锚＝自签） |
| `redteam_no_sha` | 1 | 校验失败（缺 checksum_sha256） |
| `redteam_wrong_digest` | 1 | 校验失败（登记后改 manifest） |
| `redteam_fake_anchor` | 1 | 校验失败（伪造 data 摘要） |
| `redteam_cross_run` | 1 | 校验失败（跨 run 混用） |

## ③ 计算结果（判据 (a)~(e)，全部实测）

### 判据 (a) 三分类 + 结算面

| 分类 | 条数 | 占比 |
|---|---|---|
| `full_chain_granted` | 146 | 60.8% |
| `capped_missing` | 55 | 22.9% |
| `verify_failed` | 39 | 16.2% |
| 合计 | 240 | 100% |

- 与夹具口径（每种形态的期望归属）**精确相等**：`True`；
- 完整链授权行在结算面 `chain_blocked`：**0**（须 0）；审计视图缺行 **0**；
- 结构性清偿：回填前被拦 **160** 行 → 回填后 **62** 行，
  **清偿 98 行**（只清偿内容可追溯者，其余照旧拦付）。

结构化 reason 分布（fail-closed 词表，逐条可查）：

| code | 条数 |
|---|---|
| `BACKFILL_CONTENT_VERIFIED` | 146 |
| `CHAIN_CAPS_E_TO_E1` | 32 |
| `CHAIN_MISSING_CODE` | 32 |
| `CHAIN_MISSING_CONFIG` | 32 |
| `BACKFILL_MANIFEST_MISSING` | 30 |
| `BACKFILL_ARTIFACT_MISSING` | 25 |
| `BACKFILL_ARTIFACT_HASH_MISMATCH` | 20 |
| `BACKFILL_SELF_SIGNED_NO_ATTESTATION` | 15 |
| `BACKFILL_CROSS_RUN_MIXED` | 1 |
| `BACKFILL_DATA_ANCHOR_MISMATCH` | 1 |
| `BACKFILL_DATA_ANCHOR_MISSING` | 1 |
| `BACKFILL_MANIFEST_DIGEST_MISMATCH` | 1 |
| `BACKFILL_MANIFEST_STRUCT_INVALID` | 1 |

### 判据 (b) 篡改必失败

| 项 | 值 |
|---|---|
| 受害 run | `eval-bf-0008` |
| 改动 | `result.json` 第 1 字节翻转 1 bit（1 字节） |
| sha256 | `34f8c7f4bb5d69ff…` → `09d81b7ffc23e0a8…` |
| 授权数 | 146 → 145 |
| 该 run 判定 | `verify_failed`，codes=['BACKFILL_ARTIFACT_HASH_MISMATCH'] |
| 是否检出 | **True** |

### 判据 (c) 幂等

| 项 | 值 |
|---|---|
| 两表内容指纹（首跑 vs 二跑） | `2bd8730a19f05633…` / `2bd8730a19f05633…` → 相等 **True** |
| 报表逐字节一致 | **True** |
| 二跑行变动数 | **0** |
| 首跑写入 | `{"chain": {"inserted": 240}, "backfill": {"inserted": 240}}` |
| 二跑写入 | `{"chain": {"unchanged": 240}, "backfill": {"unchanged": 240}}` |
| 篡改后再跑一次仍不变 | **True** |

实现要点：upsert 按**内容**比对（忽略时间戳列），内容相同则完全不写 —— 不是「删了重插」的假幂等。

### 判据 (d) 与手写 SQL 精确相等

| 口径 | 值 |
|---|---|
| 手写 SQL 三分类 | `{"capped_missing": 55, "full_chain_granted": 146, "verify_failed": 39}` |
| 回填器 Python 计数 | `{"full_chain_granted": 146, "capped_missing": 55, "verify_failed": 39}` |
| 手写 SQL `chain_status='complete'` | 146 |
| Python `granted` | 146 |
| 精确相等 | **True** |

### 判据 (e) 不得借回填提级

| 项 | 回填前 | 回填后 |
|---|---|---|
| `confirmed` 且 S3+/S4/S5/S-N 行数 | 160 | 160 |
| 评价表行级指纹 | `d5b24247e2721628…` | `d5b24247e2721628…` |
| delta | — | **0** |
| 提级闸 | — | **True** |

回填只写 `outcome_evidence_chains` 与 `outcome_chain_backfill`，**不 UPDATE `outcome_evaluations`**：
链完整只解封结算拦付，不改 grade/status/e_level。反向（内容不可追溯）时**只降**：旧链 complete → partial + `capped=TRUE` + `e_level_granted=E1`。

## ④ 结果图

- `figures/fig1_three_class_and_codes.png`：三分类计数与 reason code 分布（fail-closed 词表实际命中）
- `figures/fig2_tamper_and_idempotency.png`：单字节篡改后授权数下降；首跑/二跑写入行数（0 变动）
- `figures/fig3_settlement_clearing.png`：结算面拦付行数 160 → 62，
  其中 98 行由「内容可追溯」解封

## ⑤ 注意点与局限

1. **独立锚是信任根**：`registry.local.jsonl` 必须由回填**之前**的登记流程写出（`evidence_chain.py ingest`）。
   若把回填器自己产出的摘要当锚，等于自签——本实现刻意不做自动生成、不做自证。
2. **data 工件识别靠三条规则**（评价声明 → `role` → 唯一工件）；manifest 未声明 `role` 且多工件时，
   宁可封顶也不猜（`BACKFILL_DATA_ANCHOR_MISSING`）。
3. **theory/report 仍是形态判据**：这两元无法从工件内容推出（doi/报告 id 属外部声明），
   本节点只把它们与内容锚一起要求「齐」，未升级为内容核验。
4. **代码版本只认 `producer.revision`**（7–40 位 hex）。历史 manifest 若无该字段 → 封顶，
   这是有意的：无 commit 的 run 不可复算。
5. **不回写评价表**：因此「链补全」不会让任何结论升格；若某结论曾因缺链被判 `candidate`，
   本节点也不会把它改成 `confirmed`（判据 e 的硬边界）。
6. 语料为**合成夹具**（240 条，确定性生成、零随机、零学科语义）：它量的是回填器判定逻辑与闸门的
   正确性，不代表真实历史目录的通过率；真实通过率须在本平台真实 `outcome_evaluations` 上复跑
   `python3 -m tools.evidence_chain.backfill_wiring chain-backfill --db-url … --run-map …` 得到。
   **已补测**：真实库只读扩样结果见 `docs/backfill_wiring_P143W2C_N03_real_db.md`
   （`outcome_evaluations` 全量只读体检：真实历史评价 0 条可内容追溯，原因是 run 引用是
   `mission:N#result.field` 的 DB 指针而非工件目录——结构性欠账，非回填器缺陷）。

## ⑥ 误差判定

本节点是**判定逻辑**类任务，关键量是「分类计数与闸门状态」，基准是**夹具自带的期望归属**（构造时即确定）：

| 关键量 | 基准 | 实测 | 误差 |
|---|---|---|---|
| 完整链授权数 | 146（intact 条数） | 146 | **0%** |
| 缺件封顶数 | 55（missing_manifest+missing_artifact） | 55 | **0%** |
| 校验失败数 | 39（tampered+self_signed+红队 4） | 39 | **0%** |
| 授权行 `chain_blocked` | 0 | 0 | **0%** |
| 二跑行变动 | 0 | 0 | **0%** |
| confirmed 高等级行数 delta | 0 | 0 | **0%** |

**是否 <3%**：是——上表各项实测与基准**逐项精确相等（误差 0）**。无基准项仅「真实历史目录通过率」，
该量本节点不声明（须在真实库复跑，见注意点 6，不给含糊数字）。

## 审计链

| 产物 | 路径 |
|---|---|
| 回填器（Layer1+Layer2） | `tools/evidence_chain/backfill_wiring.py` |
| 合成语料夹具 | `tools/evidence_chain/backfill_fixture.py` |
| 测试（35 项，含红队 5 例） | `tests/evidence_chain/test_backfill_chain_wiring.py` |
| 回填报表（240 行逐条裁决） | `artifacts/backfill_wiring/p143w2c_n03/chain_backfill_report.json` |
| 判据数字 | `artifacts/backfill_wiring/p143w2c_n03/metrics.json` |
| 图 | `artifacts/backfill_wiring/p143w2c_n03/figures/*.png` |
| 库（本地复算用，*.sqlite 不入库） | `artifacts/backfill_wiring/p143w2c_n03/chain_backfill.sqlite` |
| 语料指纹 | `891722d394afbf2d0c225bf0b630fa962f6ba20d28effc49f765b80a0a8be037`（确定性可重建） |
