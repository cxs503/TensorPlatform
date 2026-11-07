# IN24-E4：评价体系自身脉搏 —— chronicle_snapshots 的评价面、阈值告警与周期质量报表

节点 IN24-E4 · 仓 **TensorPlatform** · 分支 `agent/m5179` · 采集时刻 tick1 = `2026-11-07T06:33:20+00:00`，tick2 = `2026-11-07T06:33:24+00:00` · 数据源：平台生产库（PostgreSQL，只读 SQL + 追加写快照行）。

---

## 1. 案例介绍

**被观测对象不是物理场，而是评价体系本身。** 平台的 `outcome_evaluations` 表（S1–S5×E0–E4 分层评价实体，见 `tools/outcome_evaluations.py`）已在记录成果评价，但评价体系没有任何自观测：
覆盖率是涨是跌、pending 积压躺了多久、独立复核是否退化成 0（全部由机器扫描自证）、S/E 分布是否塌缩到单一格，这些都不进编年史、也没有阈值告警。缺自观测的评价体系，其「可信」是自述，不是证据。

因此本节点把评价体系当成一个被测量的系统，定义六个可观测量并每拍钉进 `chronicle_snapshots`（评价面 facet）：

| 观测量 | 定义 | 口径 |
|---|---|---|
| 覆盖率 | `confirmed / succeeded` | 窗口比（该主体最近 N 张 succeeded mission 中有多少张已有 confirmed 评价） |
| S 分布 | S1–S5 + S-N 分层计数 | **不加权、不排名、不合成单一分数**；candidate/pending 不计入，S-N 单列 |
| E 级分布 | E0–E4 分层计数 | 仅 confirmed 记录 |
| 独立复核占比 | `independent / confirmed records` | 分母为 0 时记 **无定义**（`None`），不按 0% 处理 |
| auto_scan 占比 | `auto_scan / confirmed records` | 机器证据扫描，明确不等同于同行独立评审 |
| pending 积压年龄 | 条数 / 最老 / 中位 / P90 | 以记录 `created_at` 起算到 `as_of`，是下界 |

参数表（全部可注入，模块内无领域词）：

| 参数 | 值 | 来源 |
|---|---|---|
| `subject`（被观测主体键） | `TensorPlatform` | `--subject` 数据注入 |
| `window` | 100 | `--window` |
| `coverage_min` | 0.6 | 工程默认，可覆盖 |
| `pending_max` | 200 | 工程默认，可覆盖 |
| `independent_share_min` | 0.5 | 工程默认，可覆盖 |

## 2. 怎么做的

**工具链**：Python 3.11 · SQLAlchemy 2.0.51 + `pg8000`（生产 PG 驱动，本机无 psycopg2）· pytest 14 用例 · matplotlib（图全部由工件 JSON 渲染）。

**四层结构**（`tools/chronicle/eval_pulse.py`，1073 行）：

1. **facet 采集** `capture()`：7 条参数化 SQL（窗口 CTE + 覆盖率 + 记录状态 + S 分布 + E 分布 + 复核结构 + 积压聚合/逐条）算出评价面；SQL 原文与参数随 facet 一起落盘（`facet['sql']` / `facet['sql_params']`），任何第三方不看本模块也能重跑。
2. **阈值判定** `evaluate_thresholds()`：三条阈值给 `ok / breach / undefined` 三态。分母为 0 的占比进 `undefined_metrics`，**不**转成 0%（否则「还没开始评价」会被误报成「独立复核缺失」）。
3. **digest 告警** `emit_alerts()`：**上升沿**语义——同键持续越界只发一次，恢复后再次越界重新发。告警同时追加到 JSONL 台账（不覆盖历史）。
4. **快照入编年史** `write_snapshot()`：生产表 `chronicle_snapshots` 只有 `(ts timestamptz, payload jsonb)` 两列，**不加列、零迁移**：评价面作为带 schema 版本的块写入 payload，并带 `snapshot_id / prev_snapshot_id` 快照链。老读取方按 `schema` 忽略未知块。

**负控设计**：`run_drill()` 合成一份三条阈值全越界的数据（`source=synthetic_drill`，显式标注非实测），首拍要求「每键恰一条告警」、重拍要求「0 条」，两次判定都写进工件。

**审计链**：facet 内容指纹（`facet_fingerprint`，SHA-256）→ 快照 payload 指纹 → 生产库 `chronicle_snapshots` 行（可按 `payload->>'snapshot_id'` 回查）→ 告警 JSONL 台账。指纹只证明内容未变，不是签名（无签名密钥，如实声明）。

## 3. 计算结果

以下数字由 `tools/chronicle/eval_pulse_report.py` 从 `artifacts/chronicle_eval_pulse/*.json` 程序化注入，零手抄。

### 3.1 评价面（tick2，实测）

| 观测量 | 数值 |
|---|---|
| 窗口 succeeded mission | 100 |
| 已有 confirmed 评价 | 30 |
| **覆盖率** | **30.0%** |
| 独立复核占比 | 0.0% |
| auto_scan 占比 | 100.0% |
| pending 积压条数 | 60 |
| 积压最老年龄 | 4.3064 小时 |
| 积压中位年龄 | 4.3064 小时 |
| S 分布 | S-N=0 · S1=30 · S2=0 · S3=0 · S4=0 · S5=0 |
| E 级分布 | E0=0 · E1=3 · E2=27 · E3=0 · E4=0 |
| 阈值状态 | coverage_low=breach · independent_share_low=breach · pending_backlog_high=ok |

### 3.2 两拍快照的告警边沿

| 拍 | as_of | 越界键 | 发出告警 | 抑制（上一拍已在告警） |
|---|---|---|---|---|
| tick1 | `2026-11-07T06:33:20+00:00` | coverage_low, independent_share_low | coverage_low, independent_share_low | 0 |
| tick2 | `2026-11-07T06:33:24+00:00` | coverage_low, independent_share_low | （无） | 2 |

### 3.3 判据数字（机器可裁）

| 判据 | 结果 | 数字 | 工件 |
|---|---|---|---|
| ① 连续两拍含评价面全字段 | PASS | 快照 2 拍 · 字段 [22, 22]/22 · 链相连=True | `artifacts/chronicle_eval_pulse/consecutive_check.json` |
| ② 阈值负例一次演练告警 | PASS | 越界 3 条 · 首拍发出 3 条 · 重拍发出 0 条 | `artifacts/chronicle_eval_pulse/drill_alert.json` |
| ③ SQL 可复算 | PASS | 12 个字段逐一比对 · 差异 0 项 · 8 条 SQL | `artifacts/chronicle_eval_pulse/recompute_2.json` |
| ④ pytest ≥ 6 | PASS | 14 passed（要求 ≥ 6） | `tests/test_chronicle_eval_pulse.py` |

### 3.4 窗口灵敏度（同一主体、同一 SQL、只改 `:window`）

| window | 覆盖率 | confirmed | independent 占比 | pending |
|---|---|---|---|---|
| 30 | 0.0% | 0 | 无定义 | 60 |
| 100 | 30.0% | 30 | 0.0% | 60 |
| 300 | 10.0% | 30 | 0.0% | 60 |
| 600 | 5.0% | 30 | 0.0% | 60 |

### 3.5 落库证据（生产库回查）

| 项 | 值 |
|---|---|
| 本次写入评价面快照行数 | 2 |
| 写入后 `chronicle_snapshots` 总行数 | 7 |
| 行 `072c0a19e6644bbc` | ts=`2026-11-07 06:33:24+00:00` · prev=`d06131f6acc149c9` · 覆盖率=0.3 · 越界告警=2 |
| 行 `d06131f6acc149c9` | ts=`2026-11-07 06:33:20+00:00` · prev=`None` · 覆盖率=0.3 · 越界告警=2 |

回查 SQL：`SELECT ts, payload->>'snapshot_id', payload->>'prev_snapshot_id' FROM chronicle_snapshots WHERE payload->>'schema'='chronicle-eval-pulse/1' ORDER BY ts DESC`

## 4. 结果图

图全部由 `tools/chronicle/plot_eval_pulse.py` 从工件 JSON 渲染（无手抄数据）：

| 图 | 内容 | 文件 |
|---|---|---|
| 图 1 | 三条阈值面板：覆盖率（含 60% 阈值线、confirmed/succeeded 标注）、复核结构（独立 vs auto_scan，50% 阈值线）、pending 积压与年龄（200 条阈值线） | `artifacts/chronicle_eval_pulse/figures/fig1_thresholds.png` |
| 图 2 | S 分布与 E 级分布（confirmed，分层计数，图题注明 no weighting / no ranking） | `artifacts/chronicle_eval_pulse/figures/fig2_distributions.png` |
| 图 3 | 两连拍对比 + 告警上升沿/抑制状态矩阵（tick1 发出、tick2 抑制） | `artifacts/chronicle_eval_pulse/figures/fig3_two_ticks_alerts.png` |
| 图 4 | 窗口灵敏度曲线：覆盖率与独立占比随 `window` 变化，无定义点单独标注 | `artifacts/chronicle_eval_pulse/figures/fig4_window_sensitivity.png` |

## 5. 注意点（坑、局限、适用范围）

1. **覆盖率对窗口极敏感**（表 3.4）。近期 mission 的评价记录大多是 pending，因此小窗口覆盖率会掉到 0，同时独立占比退化为「无定义」。这不是 bug，是「评价流水线滞后于产出」的直接读数——但也意味着**任何覆盖率结论必须同时报窗口**，否则不可比。
2. **无定义 ≠ 0%**。分母为 0 时占比记 `None` 并单列；若粗暴按 0% 处理，「还没开始评价」会被误报成「独立复核缺失」，告警就失去信息量。
3. **积压年龄是下界**：以记录 `created_at` 起算，不含 mission 进入评价队列前的时间；生产库当前 60 行 pending 的 `created_at` 是同一时刻（批量生成），因此最老=中位=P90。
4. **阈值是工程默认值**（60% / 200 / 50%），不是学科判据；改阈值会改告警，报告必须写明当次阈值来源。
5. **快照只证明「当时的数是什么」**，不证明「数对」。评价本身的正确性由 `outcome_evaluations` 的判据（无理由必拒 / 无证据必拒 / 独立审查 / S-N 门槛）负责，本节点不复审。
6. **指纹不是签名**：SHA-256 只证明内容未变，不证明作者；工作树内无签名密钥。
7. **auto_scan 占比高不等于质量高**：机器证据扫描不能替代同行独立评审，两者在 facet 里始终分层。
8. **零领域语义**：模块内无任何学科/业务词（有测试 `test_module_has_no_domain_vocabulary` 守着），被观测主体全部经 `--subject` 注入；同一套代码可以对任何主体取脉搏。

## 6. 误差判定（逐项、显式回答「是否 < 3%」）

本节点是**平台可观测性工程**，不对应任何物理量，因此**不存在解析解/实验基准**（诚实声明：无物理基准，故不做「工程精度 15%」一类声称）。可机器裁的量用等价判据给出：

| 量 | 基准/判据 | 偏差 | 是否 < 3% |
|---|---|---|---|
| facet 全部计数与占比 | 重跑 facet 自带 SQL 原文（8 条）逐字段比对 | 0 项差异（相对偏差 0.000%） | **是**（精确相等） |
| 覆盖率恒等式 | `confirmed / succeeded` vs 报表值 | 0.000% | **是** |
| S 分布自洽 | Σ(分层计数) vs confirmed 记录数 = 30 | 0 | **是** |
| 积压年龄 | 逐条 `created_at` 重算 vs 报表 | 0.000% | **是** |
| 告警恰好一次 | 首拍每键 1 条 + 重拍 0 条 = `exactly_once_ok` | 布尔判据 = True | **是**（非数值量） |
| 连续两拍字段齐备 | 22 字段 × 2 拍全在 | 缺失 0 项 | **是** |

**结论**：对可精确裁定的量，偏差恰为 0（严格 < 3%）；对覆盖率/积压年龄这类「读数」本身，不存在参考解，因此不给误差百分比——只声明口径与下界性质（见 §5.3）。任何把读数当「真值精度」的解读都超出本节点证据范围。

## 7. 复现步骤

```bash
# 1) 采一拍（--write 才写快照行；本报告为 tick1/tick2 两拍）
DSN="postgresql://<user>:<pw>@localhost:5432/tensorplatform"
python3 tools/chronicle/eval_pulse.py capture --dsn "$DSN" --subject <subject> --window 100 \
    --write --json-out artifacts/chronicle_eval_pulse/snapshot_1.json --digest-out artifacts/chronicle_eval_pulse/quality_report_1.md
# 2) 第二拍（带链）
python3 tools/chronicle/eval_pulse.py capture --dsn "$DSN" --subject <subject> --window 100 \
    --write --prev-snapshot-id <tick1_snapshot_id> --json-out artifacts/chronicle_eval_pulse/snapshot_2.json
# 3) 三项机器判据
python3 tools/chronicle/eval_pulse.py check  --dsn "$DSN" --json-out artifacts/chronicle_eval_pulse/consecutive_check.json
python3 tools/chronicle/eval_pulse.py verify --dsn "$DSN" --facet artifacts/chronicle_eval_pulse/snapshot_2.json \
    --json-out artifacts/chronicle_eval_pulse/recompute_2.json
python3 tools/chronicle/eval_pulse.py drill  --json-out artifacts/chronicle_eval_pulse/drill_alert.json
# 4) 图与报告
python3 tools/chronicle/plot_eval_pulse.py
python3 tools/chronicle/eval_pulse_report.py
python3 -m pytest tests/test_chronicle_eval_pulse.py -q
```

## 8. 诚实声明

- 演练告警用的是**合成负控数据**（`source=synthetic_drill`），它证明告警通路与恰好一次语义，**不**证明任何真实异常已被处置。真实两拍的告警来自生产读数（覆盖率与独立占比越界），但本节点只负责「看得见」，不负责「修好」——覆盖率和独立占比为什么低，是评价流水线的治理问题，不在本节点范围内。
- 阈值、窗口、告警语义均为工程实现选择，非学科结论。
- 未做：告警外发通道（邮件/IM webhook）未接；只写 JSONL 台账 + 编年史 payload。periodic 调度（cron/守护）未在本节点接线，`capture` 可被任何调度器调用。

---

**工件清单**：`alert_ledger.jsonl` · `consecutive_check.json` · `drill_alert.json` · `drill_ledger.jsonl` · `quality_report_1.md` · `quality_report_2.md` · `recompute_2.json` · `snapshot_1.json` · `snapshot_2.json` · `snapshot_probe.json` · `window_100.json` · `window_30.json` · `window_300.json` · `window_600.json` · `window_sensitivity.json` · `tests/test_chronicle_eval_pulse.py`

