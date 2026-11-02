# Truth Gates 独立性收敛判真机制自身的验证欠账审计（m2038）

> 对象：`backend/services/truth_gates_aggregator.py` + `backend/api/truth_gates.py` +
> `backend/middleware/truth_gates_meter.py`（m613「独立性收敛判真」三支柱）。
> 判据来源：模块自身 docstring 的宪法级声明——「两个不知情的人很难撒一样的谎；
> 正确答案唯一，独立结果必收敛」。审计问题：**这套判真机制自己是否被判过真？**

## 1. 结论（一句话）

机制的三条支柱里，**第 1 支柱的聚合规则有 5 条可证逃逸路径**（8 个对抗选票集
旧规则放行 6 个，逃逸率 75% → 修复后 0%），**第 2 支柱（平台侧独立计量）从未接线**
（判定 STUB），第 3 支柱（sidecar 物理检查器）经复核为 REAL 但只有 2 个检查器、
覆盖面窄。修复集中在第 1 支柱的规则与 API 契约；第 2 支柱的接线登记为 OPEN 欠账。

## 2. 缺陷清单（每条都有攻击用例与机器判据）

| 编号 | 缺陷 | 旧行为（可复现） | 判据 |
|---|---|---|---|
| D1 | 少数派打击 | 10 票里 2 票 cheat（份额 20%）→ `is_confirmed_cheat=True` | 份额须 ≥2/3 |
| D2 | 法定人数被绕过 | 只有 2 名投票者 `{cheat,cheat}` → 打击成立 | 须 ≥3 名不同投票者 |
| D3 | 平局顺序依赖 | `{cheat:2,pass:2}` 先插入 cheat 则裁决 `cheat`，换序则 `pass`，且 `is_split=False` | 同票集两序同解、平局须 split |
| D4 | 自我确认 | 同一 `voter_model` 投 2 票 cheat 被计数 2 次（纯函数路径无唯一性） | 一人一票，重复票取最严后只计 1 |
| D5 | 证据同源冒充独立复现 | 2 票 cheat 共用同一 `evidence_hash` → 打击成立 | 确认打击须 ≥2 个不同非空哈希 |
| D6 | 空哈希假通过 | `reconcile_hash("","")` → `match=True`（不上报证据哈希即通过） | 任一侧空 → anomaly |
| D7 | 提交路径丢弃证据 | `submit_vote` 重算时只取 `voter_model/verdict/confidence`，而 `GET /vote-groups/{id}` 取全部 → **同组两处聚合口径不一致** | 两处输入同构 |
| D8 | 重复票 500 | 违反 `uq_vote_group_voter` 时 `IntegrityError` 直接冒泡成 500 | 须 409 + 可读原因 |

## 3. 修复（最小改动）

1. `aggregate_votes`：新增 `QUORUM=3`、`CHEAT_MIN=2`、`CHEAT_SHARE=2/3`、`UNIQUE_PLURALITY=2`；
   投票人按 `voter_model`（去空白）去重、重复票取最严；非法 verdict 不入选票池而是记入
   `invalid_verdicts`；平局→`majority_verdict=None, is_split=True`（排序后判定，天然顺序无关）；
   确认打击 = 法定人数 + 票数下限 + 2/3 份额；证据独立性单独出
   `is_confirmed_cheat_independent` 与 `independence_blocks`（`correlated_evidence_hash` /
   `evidence_hash_missing` / `evidence_not_independent`）。
2. `reconcile_hash`：哈希规范化（trim/lower/去 `sha256:` 前缀），空侧判 `missing_hash` 异常。
3. `api/truth_gates.py`：`submit_vote` 先查重（→409）、再 `IntegrityError`→409；重算时
   **带上 `evidence_hash`**（口径与 GET 对齐）；split 事件只落一次；对外
   `strike_confirmed` 采用独立性确认口径，同时返回 `cheat_majority` 与 `confirmation_blocks`。
   （`repro_verdict_votes` / `split_verdict_events` / `metered_usage` / `integrity_events`
   四张表实测 0 行，语义收紧无历史数据影响。）

## 4. 结果（数字全部由 `artifacts/m2038/truth_gates_independence.json` 程序化取数）

对抗选票集 8 个、正确性基线 1 个（S1 split、S2 合法 2-of-3）：

| 场景 | 旧规则攻击是否得手 | 新规则 |
|---|---|---|
| S1 3 票各执一词 | 否（本来正确） | 否 |
| S2 2 cheat + 1 pass，哈希独立 | 是（合法打击） | 是（合法打击保留） |
| S3 2/10 少数派 | **是** | 否 |
| S4 2 人组 {cheat,cheat} | **是** | 否 |
| S5/S5b 平分 2:2（两种顺序） | **是**（且两序答案不同：cheat / pass） | 否（两序同为 split） |
| S6 同一投票人投 2 票 | **是** | 否 |
| S7 同哈希冒充双人复现 | **是** | 否（`correlated_evidence_hash`） |
| S8 非法 verdict ×2 | **是**（`majority_verdict="unknown"`） | 否（不入池） |

- 逃逸率：**75.0% → 0.0%**；合法打击保留：旧/新均 `True`。
- 顺序确定性：旧 `{"cheat","pass"}` 两序不一 → 新 `{None,None}` 一致。
- 空哈希假通过：旧 `True` → 新 `False`。
- 打击判定域（图 2）：红色区域 = 修复后不再打击（少数派 + 人数不足），蓝色 = 新增打击（无）。

## 5. 第 2 支柱：平台侧独立计量的静态判定 = STUB

`backend/middleware/truth_gates_meter.py` 定义了 `TruthGatesMeterMiddleware`，但
`backend/main.py` 中**没有任何 `add_middleware(TruthGatesMeterMiddleware)`**，且
`flush_mission_metrics()` 在模块外调用点为 **0**：指标只在进程内存里累加，永不落库。
即便接线，token 计量是 `content-length // 4` 的启发式（非真实 token），
`reconcile_usage` 的「ground truth」在运行时并不存在。**登记为 OPEN 欠账**（本单不改：
接中间件会改变全站请求路径与延迟，超出单干最小改动范围；建议单独派单，判据 =
中间件已挂载 + flush 有调用方 + `metered_usage` 行数随任务增长）。

## 6. 局限与适用范围

- 独立性仍是**声明式**的：`voter_model` 由请求体自报，平台不校验模型钉扎/工作树真实性；
  本次修复把「一人多票」「同一证据多票」这类**可在数据层看见的**伪造挡住，但挡不住
  三个不同模型名同一进程串通。真正的人-模型绑定额要引入平台侧身份（`voter_identity`
  列 + 认证主体），涉及 DDL，登记欠账。
- 2-of-3 推广为「≥3 人 + ≥2 票 + ≥2/3 份额」，是保守推广；若有 5 人以上投票族，
  建议改用「≥⌈2n/3⌉」而非份额（二者在 n=3 时重合，n=6 时份额法更松：4/6=0.667 通过，
  ⌈2n/3⌉=4 相同；n=7 时份额法 5/7=0.714 通过而 ⌈2n/3⌉=5 相同——边界一致，可接受）。
- sidecar 检查器只审 `holtrop_cd_re`、`wave_cw_fn` 两类输出，域覆盖窄；未审计其阈值合理性。

## 7. 复现

```bash
cd <worktree>
python3 -m pytest tests/test_truth_gates.py tests/test_truth_gates_independence.py -q   # 51 passed
python3 docs/audit/m2038_truth_gates_independence_audit.py                              # 前后对照 JSON + 2 图
```

图：`artifacts/m2038/fig_attack_matrix.png`（攻击矩阵前后对照）、
`artifacts/m2038/fig_strike_domain.png`（打击判定域变化）。