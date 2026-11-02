# m1814 — 终态单归因：把「物理答错」与「worker 死掉」从同一个成功率里拆出来

**席位** data（tp-doc-2）· **日期** 2026-11-02 · **仓** TensorPlatform（分支 `agent/m1814`）
**EDIT_WHITELIST**: `tools/`, `tests/`, `docs/`, `artifacts/`
**产物**: `tools/validation/mission_attribution.py` · `tests/test_mission_attribution.py`（21 条）·
`artifacts/m1814/{mission_attribution.json, mission_attribution.md, mission_attribution.png, missions_snapshot.jsonl}`

---

## 1. 案例背景：平台量的是哪个「成功」

舰队所有治理信号都从一张表的 `status` 列派生：`guide.success_rate`（target 0.9）、
guide_tracker 的「案例连续失败≥3 即升级」、以及「无人值守案例」计数。但 `status`
把三类互不相干的事件压进同一格：

1. **真的把活干成了**（有证据闸 + 有落盘改动）；
2. **物理/判据答错**（这是唯一的「技术质量」信号，也是唯一值得升级的信号）；
3. **worker 根本没干活**（llm_error / agent_lost / 工作树零改动 / 仓路径不存在 / owner 批量去重停单）。

第 3 类占多大比例，平台此前无人测量。本单把它测出来，并给出可机器裁决的替代口径。

**数据与字段**（只读，`SELECT ... WHERE status IN ('succeeded','failed','revoked')`）：

| 项 | 值 |
|---|---|
| 终态单 N | **1428** |
| 数据源 | `tensor_dev_missions`（只读快照 `artifacts/m1814/missions_snapshot.jsonl`，已剔除自由文本与绝对路径） |
| 判定所用机器字段 | `result.evidence.checks.gate`、`evidence.checks.reason`、`result.stop_condition`、`result.fail_stage`、`result.revoked(_reason)`、`result.accepted`、`result.decision_note`、`result.on_disk_changes/changed_files` |
| 明确**不用**的字段 | `result.checks`（自由文本自述）、`result.headline`、`evidence.summary.headline` —— 有专门测试钉住「自述不能改变分类」 |

## 2. 怎么做的

**分类器**（`tools/validation/mission_attribution.py`，纯标准库）：9 个互斥类，按固定优先级首次命中，
每条判定都返回 `basis`（命中的机器字段）以便复核：

| 优先级 | 类 | 判据（机器字段） | 语义 |
|---:|---|---|---|
| 1 | `admin_dedupe` | `revoked_reason ~ dup-dispatch/批量停重复` | 行政停单，不计质量 |
| 2 | `infra_crash` | `gate == worker-crash` | 仓缺失/worker 异常 |
| 3 | `infra_runtime` | `stop_condition == agent_lost` 或 `fail_stage ∈ {llm_error, wall/budget_exhausted, no_progress, timeout}` | 运行期丢失 |
| 4 | `evidence_absent` | `gate == no-evidence` 且非 succeeded | 工作树零改动/未 finish |
| 5 | `claim_falsified` | `revoked` 且非去重理由 | 声明被独立复核证伪 |
| 6 | `acceptance_rejected` | `accepted == false` 或 `decision_note` 非空 | 验收判据拒绝 |
| 7 | `failed_other` | `status == failed` 且无标记 | 兜底 |
| 8 | `succeeded_unverified` | `status == succeeded` 但 `gate == no-evidence` 或零改动 | 报成功但无证据 |
| 9 | `succeeded_verified` | `status == succeeded` + 证据闸 + 落盘改动 | 真交付 |

**四口径**（取代单一 blended 成功率）：
`raw_status_success_rate` = succeeded/终态；
`raw_success_rate_excl_admin` = 剔除行政停单；
`verified_delivery_rate` = 真交付/终态；
`technical_pass_rate` = 真交付/(真交付 + 技术性失败)；
`infra_loss_share` = 非技术性丢失/终态。

**可复现性**：快照（364 KB）只保留分类所需字段，重跑 `--jsonl` 得到与 `--out-json` **逐字段相同**的统计；
`tests/test_mission_attribution.py::test_published_numbers_reproduce_from_committed_snapshot` 把这条钉成回归测试。

```bash
# 采集（只读）
TP_PG_PASSWORD=*** python3 tools/validation/mission_attribution.py --from-db \
  --snapshot artifacts/m1814/missions_snapshot.jsonl \
  --out-json artifacts/m1814/mission_attribution.json \
  --out-md   artifacts/m1814/mission_attribution.md \
  --out-fig  artifacts/m1814/mission_attribution.png
# 复现（无需数据库）
python3 tools/validation/mission_attribution.py --jsonl artifacts/m1814/missions_snapshot.jsonl
python3 -m pytest tests/test_mission_attribution.py -q     # 21 passed
```

## 3. 计算结果（全部由工具程序化产出，零手抄）

### 3.1 同一条「成功率」其实横跨三类事件

| 口径 | 数值 |
|---|---:|
| raw_status_success_rate（现行 KPI 口径） | **72.62%** |
| raw_success_rate_excl_admin（剔除行政去重停单） | **82.43%** |
| verified_delivery_rate（真交付） | **72.34%** |
| technical_pass_rate（真交付/（真交付+技术失败）） | **99.71%** |
| infra_loss_share（非技术性丢失） | **15.27%** |
| admin_share（行政停单占比） | **11.90%** |

读法：现行口径 72.62% 与 guide 看板 2026-11-01 07:00 快照的
0.7994 之差，**几乎全部由 10-31 的 170 张 owner 批量去重停单贡献**（11.90%），
而非质量下降；把行政停单剔除后是 82.43%。
真正把活干成并留下证据的是 1033/1428 = 72.34%；
而在「真交付 + 技术性失败」这个技术分母上，通过率是 **99.71%**
（技术性失败仅 3 例）。

### 3.2 逐类计数

## 终态单归因（机器字段判定，程序化产出）

数据源：tensor_dev_missions 只读快照（artifacts/m1814/missions_snapshot.jsonl） · 终态单 N = **1428** · 未归因 = **0**

| 类别 | 含义 | 计数 | 占终态单 |
|---|---|---:|---:|
| `admin_dedupe` | 行政去重停单（不计质量） | 170 | 11.90% |
| `infra_crash` | 基础设施崩溃（仓缺失/异常退出） | 21 | 1.47% |
| `infra_runtime` | 运行期丢失（llm_error/agent_lost/预算耗尽） | 87 | 6.09% |
| `evidence_absent` | 无证据（工作树零改动/未 finish） | 110 | 7.70% |
| `claim_falsified` | 声明被独立复核证伪 | 2 | 0.14% |
| `acceptance_rejected` | 验收判据拒绝 | 1 | 0.07% |
| `failed_other` | 失败（无机器标记） | 0 | 0.00% |
| `succeeded_unverified` | 报成功但无证据闸 | 4 | 0.28% |
| `succeeded_verified` | 成功且有证据+落盘改动 | 1033 | 72.34% |

| 口径 | 定义 | 数值 |
|---|---|---:|
| raw_status_success_rate | status=succeeded / 终态单（现行 KPI 口径） | 72.62% |
| raw_success_rate_excl_admin | 剔除行政去重停单后的 status 成功率 | 82.43% |
| verified_delivery_rate | 成功且有证据+落盘改动 / 终态单 | 72.34% |
| technical_pass_rate | 已验证交付 / (已验证交付 + 技术性失败) | 99.71% |
| infra_loss_share | 非技术性丢失 / 终态单 | 15.27% |

### 按模型

| model | n | verified | technical_fail | infra_loss | infra_loss 占比 |
|---|---:|---:|---:|---:|---:|
| MiniMax-M2.5-AWQ | 41 | 14 | 0 | 19 | 46.34% |
| Qwen3.8-27B | 24 | 10 | 0 | 9 | 37.50% |
| deepseek-v4-flash | 464 | 404 | 0 | 37 | 7.97% |
| glm-5.1 | 72 | 44 | 0 | 22 | 30.56% |
| glm-5.2 | 73 | 27 | 1 | 25 | 34.25% |
| qwen3.6-plus | 368 | 278 | 1 | 34 | 9.24% |
| qwen3.7-plus | 354 | 256 | 1 | 45 | 12.71% |
| unknown | 32 | 0 | 0 | 27 | 84.38% |

### 按仓

| repo | n | verified | technical_fail | infra_loss | infra_loss 占比 |
|---|---:|---:|---:|---:|---:|
| Ghost-Repo-XYZ | 2 | 0 | 0 | 2 | 100.00% |
| TensorBEM | 11 | 7 | 0 | 4 | 36.36% |
| TensorCAD | 9 | 9 | 0 | 0 | 0.00% |
| TensorDEM | 31 | 24 | 0 | 7 | 22.58% |
| TensorDG | 4 | 4 | 0 | 0 | 0.00% |
| TensorFEM | 2 | 2 | 0 | 0 | 0.00% |
| TensorFVM | 5 | 5 | 0 | 0 | 0.00% |
| TensorLBM | 634 | 451 | 0 | 121 | 19.09% |
| TensorPapers | 19 | 18 | 0 | 1 | 5.26% |
| TensorPhysics | 66 | 47 | 1 | 18 | 27.27% |
| TensorPlatform | 558 | 408 | 2 | 36 | 6.45% |
| TensorSPH | 5 | 5 | 0 | 0 | 0.00% |
| ship-performance-platform | 24 | 5 | 0 | 19 | 79.17% |
| ship-platform | 58 | 48 | 0 | 10 | 17.24% |

### 升级信号回放（guide_tracker 标记为 escalated 的案例）

| case | n | 技术性失败 | 非技术性 | 现行规则触发升级 | 仅技术失败触发升级 |
|---|---:|---:|---:|---|---|
| lid_driven_cavity_3d | 5 | 0 | 5 | 是 | 否 |
| dam_break_sc | 12 | 0 | 12 | 是 | 否 |
| forchheimer | 5 | 0 | 5 | 是 | 否 |
| kelvin_helmholtz | 5 | 0 | 5 | 是 | 否 |


### 3.3 infra_crash 的机器理由（21 例）

| 机器理由（截断 60 字） | 例数 |
|---|---:|
| 仓库不存在或不是 git: /srv/tensor/workspace/ship-performance-platfo | 19 |
| 仓库不存在或不是 git: /srv/tensor/workspace/Ghost-Repo-XYZ | 2 |

结论：基础设施崩溃不是随机噪声，样本集中在 **worker 拿到的仓路径不存在**——
即派单指向的工作树与实际克隆位置不一致。这是可直接修的单点缺陷，不是「模型不行」。

### 3.4 升级信号回放：4 个 escalated 案例全部是假阳性

guide_tracker 于 2026-10-30/31 将 `lid_driven_cavity_3d`、`dam_break_sc`、`forchheimer`、
`kelvin_helmholtz` 标为升级（连续失败≥3，`guide_tracker_state.json` 至今未撤）。把它们的终态单
逐张归因后：

| case | n | 技术性失败 | 非技术性 | 现行规则触发升级 | 仅技术失败才升级 |
|---|---:|---:|---:|---|---|
| lid_driven_cavity_3d | 5 | 0 | 5 | 是 | 否 |
| dam_break_sc | 12 | 0 | 12 | 是 | 否 |
| forchheimer | 5 | 0 | 5 | 是 | 否 |
| kelvin_helmholtz | 5 | 0 | 5 | 是 | 否 |

**四个案例合计 27 张终态单，技术性失败 0 张**
（`dam_break_sc` 甚至已有 2 张 `succeeded_verified`）。也就是说：升级信号反映的是
调度/worker 存活率，与 LBM 求解器、与这些案例的文献判据**没有任何关系**；
若以技术失败为门，四个案例一个都不该升级，其根因单应当派给 infra 而非 solver。

## 4. 图

`artifacts/m1814/mission_attribution.png`（三联图）：
(a) 终态单逐类计数（灰=行政、红=技术失败、橙=非技术丢失、蓝=交付）；
(b) 四口径并排（raw / raw-excl-admin / verified / technical）；
(c) 逐模型堆叠（交付 vs 技术失败 vs 非技术丢失 vs 行政停单）。
图内数字由 `plot()` 从同一 stats 对象渲染，与本节表格同源。

## 5. 注意点与局限

1. **自由文本一律不采信**：`result.checks` 是 agent 自述（历史上出现过 3 张被复核证伪的单），
   分类器只吃机器闸字段；有测试专钉此约束。
2. **行政停单必须单列**：`revoked` 里 170/172 是 owner 批量去重，
   把它们算进「失败」会同时污染成功率与升级信号（本单两处都看到了）。
3. **`succeeded` 不等于有证据**：仍有 4 张单报成功但 `gate=no-evidence`/零改动；
   这类应视为**证据闸漏放**的 bug（本单只做测量，未改闸门逻辑，避免越界动平台代码）。
4. **快照必须带表列 `model`**：`result.model` 在部分行缺失，仅从 result 推断会让 ~2% 的单归入 unknown
   并使逐模型表漂移（这正是本单复现第一次不通过的原因，已修）。
5. **数字带时间戳**：DB 是活的，本单三次采集分别得到 N=1417/1425/1426/1428，故一切结论绑定
   `artifacts/m1814/missions_snapshot.jsonl` 这一份快照；报告里所有数都从该快照重算。
6. **规则式分类的边界**：「技术性失败」判据是 `accepted=false/decision_note/revoked(非去重)`，
   若某张单实际答错却被 worker 崩溃掩盖（先崩后判），会归入 infra —— 这类掩盖无法从 status 面区分，
   属于本方法的已知盲区（估计上界即 218 张单）。

## 6. 误差判定

| 关键量 | 基准 | 结果 | 是否 <3% |
|---|---|---|---|
| 未归因率（分区穷尽性） | 0，机器判据「每张终态单必须落在 9 类之一」 | 0/1428 = 0.00% | **是（0.00%）** |
| 快照复现一致性 | 与 DB 直采逐字段相同 | 全部键一致（含 by_class/rates/by_model/by_repo/flag_counts） | **是（0.00% 偏差）** |
| 升级信号误报率（4 个 escalated 案例） | 技术性失败 ≥1 才应升级 | 4/4 案例技术失败 0 —— 误报 100% | **否（这是本单主发现）** |
| 成功率口径偏差（raw vs 剔行政） | — | 72.62% vs 82.43%，差 9.81 个百分点 | 否（>3%，故 KPI 必须换口径） |

**声明**：本单是平台治理度量，其数值**没有外部文献基准可比**（不存在「客观正确的成功率」）；
因此判据取自洽的两条机器可裁决项——分区穷尽率与快照复现一致性——并把「误报率 100%」如实报告为未通过项。
按平台规章，如实回答「是否 <3%」：穷尽性与复现性 **是**；升级信号准确性与 KPI 稳定性 **否**，并在报告中点名。

## 7. 产出与建议

- 工具：`tools/validation/mission_attribution.py`（`--from-db/--jsonl/--snapshot/--out-*`，未归因>0 时退出码 1，可直接做 CI 闸）
- 测试：`tests/test_mission_attribution.py`（21 条：逐类、优先级、自述不可采信、分区穷尽 216 组合、快照往返、已发布数字复现、md 与 json 数字同源）
- 建议交付给治理线（本单不改平台代码）：
  1. `guide.success_rate` 改用 `verified_delivery_rate` + `raw_success_rate_excl_admin` 双报；
  2. 案例升级判据由「连续 failed≥3」改为「连续 `technical_fail`≥2」，否则升级单永远派错工种；
  3. 补 `succeeded` + `gate=no-evidence` 的证据闸漏洞（4 例在案）；
  4. 修 worker 的仓路径分辨率（`infra_crash` 21 例的共同理由）。
