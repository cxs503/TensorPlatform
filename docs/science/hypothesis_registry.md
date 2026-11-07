# hypothesis_registry —— 预注册假设台账（盲预测不可改 + 开盲流 + 红队负例）

> 领域目录：`docs/science/`（科学方法论层） · 实现：`tools/hypothesis_registry.py` ·
> 验收用例：`tests/test_hypothesis_registry.py` · 真实任务示范：`runs/m6329_hypothesis_registry/`
> 产物：`artifacts/hypothesis_registry.db`、`artifacts/m6329/in59a2_acceptance.json`

## 1. 为什么需要这张表

科研循环里最容易被悄悄污染的环节不是计算，而是**判定**。同一条实验结果，在
「我先猜了 A，结果不是 A」与「我猜的就是 B」两种叙述下价值完全不同；没有登记
时间戳与预测承诺，两者在文本上无法区分，于是 falsified 被系统性改写成
confirmed，失败知识库（A4）永远收不到料。

`hypothesis_registry` 把「先登记、再实验、后判定」变成**数据库约束**：

| 环节 | 机器门 | 违反时的行为 |
|---|---|---|
| 登记 | 四个叙述字段非空；`prediction_before` 非空且（若带 `rule`）当场可执行；`evidence_at_registration` 必须为空；声明的 `result_paths` 必须尚不存在 | `RegistryError`，无半条写入 |
| 预测冻结 | `prediction_commitment = sha256(canonical_json(prediction_before))`；字段 UPDATE 由触发器 `trg_hr_immutable_core` 拒绝 | 裸 SQL 报 `ABORT`；若先 `DROP TRIGGER` 再改，`verify` 复算承诺报 `commitment_mismatch` |
| 实验记录 | `experiment_runs` 插入要求：假设已登记 / 仍 `pending` / `run_utc >= registered_utc` | `ABORT`：不登记先跑、倒填时间戳都写不进 |
| 结题 | 至少一条 run；`outcome` 终态单向；`verdict_basis` ≥20 字；`falsified` 另需 ≥40 字复盘 | `ABORT`：无实验不得裁决，结题后不得翻案 |
| 历史 | `experiment_runs` / `event_log` 禁 UPDATE、禁 DELETE；`event_log` 逐行哈希链 `entry_hash = sha256(prev|kind|hid|utc|payload_sha)` | 改任一行 → `verify` 报 `chain_broken` |

## 2. 表结构与取值域

```
hypothesis_registry(hypothesis_id PK, mission_id, domain,
                    hypothesis_statement, theory_basis, falsification_experiment_design,
                    prediction_before JSON, prediction_commitment,
                    registered_utc, registered_by, evidence_at_registration JSON, blind_guards JSON,
                    outcome ∈ {pending,confirmed,falsified,inconclusive}, outcome_utc,
                    verdict_basis, postmortem_note,
                    sn_track, sn_weight, failure_kb ∈ {0,1}, schema_version)
experiment_runs(hypothesis_id, run_seq, run_ref, run_utc, payload JSON, logged_utc)  -- 只增
event_log(seq, kind, hypothesis_id, event_utc, payload_sha, prev_hash, entry_hash)   -- 只增 + 哈希链
```

- `SQLite` 版即 `tools/hypothesis_registry.py::DDL`（本地可复算台账，含全部触发器）；
- `PostgreSQL` 版由 `pg_ddl()` 生成（CHECK 约束 + 同语义触发器），落盘
  `artifacts/m6329/hypothesis_registry_pg.sql`，供线上实例部署。

## 3. 冻结规则执行器（`evaluate_rule`）

`prediction_before` 里可以带一个**机器可执行判据**：

```json
{"rule": {"rule_id": "R1", "all": [
    {"metric": "n_humps",       "op": ">=",       "value": 3},
    {"metric": "first_hump_Fn", "op": "in_range", "range": [0.21, 0.245]}]},
 "predicted": {"n_humps": 3, "first_hump_Fn": 0.2303}}
```

`register` 会调 `validate_rule`：判据不合法（缺 metric、未知 op、range 非数值）直接拒
——**冻结不了的东西不许登记**。结题时 `evaluate_rule(rule, measurements)` 输出
`confirmed`（全通过）/ `falsified`（任一失败）/ `inconclusive`（有 metric 缺失）。
这样结题不是「再商量一次判据」，而是执行登记时的判据。

## 4. S-N 轨记账（bootstrap 权4）

| outcome | 轨 | 权重 | 计入失败率 | 失败知识库(A4) |
|---|---|---|---|---|
| `confirmed` | S-C | 1 | 是 | 否 |
| `falsified` | S-N | **4** | **否** | **是** |
| `inconclusive` | S-N | 1 | 否 | 否 |

`close` 自动写 `sn_track / sn_weight / failure_kb`；`summary()` 给出按轨汇总
（`sn_track`、`failure_kb_entries`）。falsified 因此是**有价值的正收益**，不是欠账。

## 5. 用法

```bash
# 登记（实验前！result_paths 必须是还没生成的文件）
python3 tools/hypothesis_registry.py init     --db artifacts/hypothesis_registry.db
python3 tools/hypothesis_registry.py register --db artifacts/hypothesis_registry.db --file prereg.json
# 实验执行后逐条挂钩
python3 tools/hypothesis_registry.py run      --db ... --hid M-H1 --ref "runs/x/execute.py -> artifacts/x/res.json"
# 按冻结规则结题
python3 tools/hypothesis_registry.py close    --db ... --hid M-H1 --outcome falsified \
        --basis-file basis.txt --postmortem-file pm.txt
# 审计 / 出报告
python3 tools/hypothesis_registry.py verify   --db ...            # 退出码非 0 = 审计失败
python3 tools/hypothesis_registry.py report   --db ... --out-json artifacts/m6329/hypothesis_registry_summary.json \
        --out-md artifacts/m6329/hypothesis_registry_summary.md
python3 tools/hypothesis_registry.py sql      --db ... --mission m6329-IN59-A2
```

编程接口：`register / log_run / close / get / list_hypotheses / verify_integrity /
summary / render_markdown / evaluate_rule / validate_rule / sn_ledger / pg_ddl`。

## 6. 能力边界（诚实声明）

**能证明**（`verify_integrity().blindness_basis`）：库内顺序（run 必在登记之后）、
预测未被改写（承诺复算）、结题单向、历史不可改（哈希链）、登记时结果文件不存在。

**不能证明**（`residual_risk`）：墙钟世界的真实性——若有人在库外先跑完实验再登记，
台账只能靠 `result_paths` 预声明与外部时间戳旁证逼近。**本模块不声称已完全杜绝事后
补登**，只把「库内可查的部分」做成不可争辩的事实，并在 `verify` 输出里明示残余风险。

## 7. 验收（IN59-A2）

| 判据 | 状态 | 证据 |
|---|---|---|
| 表存在且字段 = 任务书要求（含 `prediction_before`、`outcome` 取值域） | ✅ | `tools/hypothesis_registry.py::DDL`；`test_summary_counts_multiple` |
| 登记 / 不可改 / 开盲流 | ✅ | 13 个不可改/时序/终态/链 类触发器；`test_n2_*`、`test_n3_*`、`test_n4_*` |
| 红队负例 ≥3 全部被拦 | ✅ 15 条 | `test_n1_*…test_n6_*`（含裸 SQL 层 + `DROP TRIGGER` 篡改检测） |
| 一个真实任务全程使用示范 | ✅ | `runs/m6329_hypothesis_registry/preregister.py` → `execute.py`，3 条假设 2 falsified / 1 confirmed，`artifacts/m6329/` |