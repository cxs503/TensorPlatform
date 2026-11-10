# WITHHELD 三态清偿闭环成熟化（IN50-W4-N09）

**节点** IN50-W4-N09 · **仓** TensorPlatform · **域** 平台共性机制（零领域语义）
**合成场景** SYN-* 虚拟域（SYN-Repo-A/B/C、SYN-DS-00x、SYN-QUANTITY-ALPHA、12 天战役）
**产物** `tools/vv/withheld_gates.py`（959 行）· `backend/migrations/113_withheld_gates.sql`
· `tests/test_withheld_gate_clearing.py`（26 用例）· `artifacts/withheld_gates/`

## ① 案例介绍（问题与设定）

平台正典判决为三态 `PASS < WITHHELD < WARN < FAIL`，其中 **WITHHELD = 证据不足、尚不可判**。
真实危害不是"判错"，而是 WITHHELD **永不结算**：门写着"缺参考数据"，但没人知道缺哪张数据集、
容差多少、欠了几天、该由哪个仓还。本节点把 WITHHELD 做成**可清偿债务**（债务三要素：
债权人=门、标的=数据集、账龄=天数），并用合成域名覆盖全流程，避免任何领域语义进入平台仓。

设定（全部合成，表内数值可程序化复算）：

| 项 | 设定 |
|---|---|
| 门数 | 8 门，分布于 SYN-Repo-A/B/C |
| 数据集 | SYN-DS-001（u_dig=1%）、SYN-DS-002（u_dig=5%）、SYN-DS-003（永不登记）、SYN-DS-004（出处不全） |
| 门工件契约 | `requires_reference{dataset_id, tolerance, k_factor, measurement{at,value,u_exp}}` |
| 判决式 | `E=(m−r)/D`，`band=max(tol, k·sqrt(u_exp²+u_dig²)/D)`，`D=max(|r|,0.05·y_scale)` |
| 战役时长 | 12 天（T0=2026-11-01T00:00:00Z） |

## ② 怎么做的（方法 / 流程 / 工具链）

1. **数据模型**：`tensor_withheld_gates`（门账本，含 `reason_code/tolerance/k_factor/
   artifact_sha256/cleared_at/verdict`）、`tensor_reference_datasets`（登记册：DOI、
   原始文件 sha256、数字化方法、`u_dig`、`usable_as_gate`）、`tensor_withheld_audit`
   （哈希链审计）。门工件可声明 `requires_reference`；契约字段（tol/k/数据集绑定/实测值/
   声明时刻）规范化序列化后取 sha256 作为**不可变基线**。
2. **绑定事件**：`register_reference()` 校验出处（缺 DOI/url、缺原始文件 sha256、缺数字化方法、
   缺 `u_dig` ⇒ `usable_as_gate=False`）→ 落登记行 → 写 `DATASET_REGISTERED` 审计 →
   **自动重判**所有绑定门（无需人工触发）。
3. **重判**：从门工件取实测值 m，按 `measurement.at` 在数字化表上插值取 r 与逐点 σ（超范围
   **不外推**，保持 WITHHELD）；不确定度预算 `k·sqrt(u_exp²+u_dig²)` 与容差 tol 取大者为 band。
4. **反作弊**（三重防线）：① 工件契约指纹与落库基线不符 ⇒ 判决 FAIL + `TAMPER_BLOCKED`；
   ② 即使绕过核对，生效容差取 `min(基线, 现行)`，只可收紧；
   ③ 库层触发器禁止 UPDATE 契约列、禁止 UPDATE/DELETE 审计行（`113_withheld_gates.sql`）。
5. **KPI + 派单**：`withheld_gate_share`、`withheld_mean_age_days` 按小时桶幂等 upsert 进
   `tensor_guide_tracker`（targets 0 / 7 d）；未清偿门按 `(repo, dataset_id, age)` 合并派单，
   工单号 `WO-sha256(repo|dataset_id)[:12]` 幂等。
6. **验证**：`pytest tests/test_withheld_gate_clearing.py` 26 用例（三态、不确定度预算、
   反作弊、触发器、审计链伪造检测、KPI 数值、派单排序/幂等、迁移列 parity）全绿。

## ③ 计算结果（终态表 + KPI 序列）

终态判决（逐门）：

| gate | repo | 数据集 | tol | 终态 | 判据摘要 |
|---|---|---|---|---|---|
| SYN-G1 | SYN-Repo-A | SYN-DS-001 | 0.150 | **PASS** | WITHIN_BAND: E=+0.0071 band=0.1500 (tol=0.150, k·u/D=0.0200) 判定 PASS |
| SYN-G2 | SYN-Repo-A | SYN-DS-001 | 0.150 | **FAIL** | OUT_OF_BAND: E=+0.2143 band=0.1500 (tol=0.150, k·u/D=0.0200) 判定 FAIL |
| SYN-G3 | SYN-Repo-B | SYN-DS-002 | 0.050 | **PASS** | WITHIN_UNCERTAINTY: E=+0.0714 band=0.1010 (tol=0.050, k·u/D=0.1010) 判定 PASS |
| SYN-G4 | SYN-Repo-B | SYN-DS-002 | 0.050 | **FAIL** | OUT_OF_BAND: E=+0.3929 band=0.1010 (tol=0.050, k·u/D=0.1010) 判定 FAIL |
| SYN-G5 | SYN-Repo-C | SYN-DS-003 | 0.150 | **WITHHELD** | NO_REFERENCE: 数据集 SYN-DS-003 尚未登记（门保持 WITHHELD） |
| SYN-G6 | SYN-Repo-C | SYN-DS-004 | 0.150 | **WITHHELD** | REFERENCE_INCOMPLETE: 数据集不可作判据：validate.blocking=['provenance'], registration_missing=['doi|url' |
| SYN-G7 | SYN-Repo-A | SYN-DS-001 | 0.150 | **WITHHELD** | REFERENCE_OUT_OF_RANGE: 测量自变量 at=9.0 超出数字化表覆盖范围：不做外推，无法判 PASS/FAIL |
| SYN-G8 | SYN-Repo-C | SYN-DS-001 | 0.150 | **FAIL** | OUT_OF_BAND: E=+0.2143 band=0.1500 (tol=0.150, k·u/D=0.0200) 判定 FAIL |

KPI 逐日序列：

| day | 门总数 | 未清偿 | withheld_gate_share | withheld_mean_age_days | PASS | FAIL |
|---|---|---|---|---|---|---|
| 0 | 4 | 4 | 100.0% | 0.00 | 0 | 0 |
| 1 | 6 | 6 | 100.0% | 0.67 | 0 | 0 |
| 2 | 6 | 4 | 66.7% | 1.50 | 1 | 1 |
| 3 | 8 | 5 | 62.5% | 2.00 | 1 | 2 |
| 4 | 8 | 5 | 62.5% | 3.00 | 1 | 2 |
| 5 | 8 | 3 | 37.5% | 4.00 | 2 | 3 |
| 6 | 8 | 3 | 37.5% | 5.00 | 2 | 3 |
| 7 | 8 | 3 | 37.5% | 6.00 | 2 | 3 |
| 8 | 8 | 3 | 37.5% | 7.00 | 2 | 3 |
| 9 | 8 | 3 | 37.5% | 8.00 | 2 | 3 |
| 10 | 8 | 3 | 37.5% | 9.00 | 2 | 3 |
| 11 | 8 | 3 | 37.5% | 10.00 | 2 | 3 |
| 12 | 8 | 3 | 37.5% | 11.00 | 2 | 3 |

终态 KPI：门总数 **8**，未清偿 **3**，
`withheld_gate_share = 0.3750`（目标 0），
`withheld_mean_age_days = 11.0000` d（目标 7.0 d），
平均清偿时长 `3.0000` d，未清偿原因分布
`{"NO_REFERENCE": 1, "REFERENCE_OUT_OF_RANGE": 1, "REFERENCE_INCOMPLETE": 1}`。

自动派单（未清偿项，按 (repo, dataset_id, age)）：

| 工单 | repo | dataset_id | 门数 | max age(d) | 优先级 | 原因码 |
|---|---|---|---|---|---|---|
| WO-7fb821a8e315 | SYN-Repo-A | SYN-DS-001 | 1 | 12.00 | P2 | REFERENCE_OUT_OF_RANGE |
| WO-481876f7f08e | SYN-Repo-C | SYN-DS-003 | 1 | 12.00 | P2 | NO_REFERENCE |
| WO-7707ca925f98 | SYN-Repo-C | SYN-DS-004 | 1 | 9.00 | P2 | REFERENCE_INCOMPLETE |

审计链：29 行 / 8 条链，
`ok=True`，断裂 0 行；
事件分布 `{"DATASET_REGISTERED": 6, "GATE_DECLARED": 8, "REJUDGED": 14, "TAMPER_BLOCKED": 1}`。

## ④ 结果图

![fig1_kpi_trajectory.png](fig1_kpi_trajectory.png)

*图1 withheld_gate_share 与 withheld_mean_age_days 逐日轨迹；红线为未清偿份额，蓝线为平均账龄（目标 7 天）*  
sha256 `9290ce2a3a01f8a4e5cd483a532785427ceddc9a641100e23c32b9407f8f4164`
![fig2_error_vs_band.png](fig2_error_vs_band.png)

*图2 各门最终 |E| 与容差 tol（橙实线）、判决带 max(tol,k·u/D)（紫虚线）对照；绿柱=PASS、红柱=FAIL*  
sha256 `e6665deed6115c3e4ba7b71d27982cd375ccb6f4790cb22caf8c2ea9ff3b7ae0`
![fig3_audit_events.png](fig3_audit_events.png)

*图3 审计事件按日分布：四种事件全部入哈希链，篡改尝试触发 TAMPER_BLOCKED 且链仍自洽*  
sha256 `b4ad8ebd24b1a584946f17759f71eb3b0031863fa6dd3d857451213f81858f49`

## ⑤ 注意点（坑 / 局限 / 适用范围）

* **坑1 触发器与 SQLite 的兼容**：`BEFORE UPDATE OF <列>` 里不能出现不存在的列，否则建表期
  即报错；PG 侧改成整行 `BEFORE UPDATE` + 列比较（`113_withheld_gates.sql`），两侧语义一致，
  列级 parity 由测试钉住。
* **坑2 不规范化的哈希**：直接把整个工件 JSON 取哈希会把注释/空白算进契约，正常运行也会
  误报篡改；因此基线只覆盖 `CONTRACT_FIELDS`（12 个契约字段），非契约注释改动不影响指纹
  （`test_artifact_fingerprint_ignores_non_contract_noise`）。
* **坑3 不确定度预算不是免罪符**：`k·u` 带宽必须由登记册的 `u_dig` 与工件 `u_exp` 供给，
  没有声明就退化为纯 tol（`test_uncertainty_budget_is_what_clears_the_gate` 用同一门两种
  数据集对照，证明 PASS 来自登记的不确定度而非放松判据）；39% 误差仍 FAIL。
* **局限1**：不确定度按相对量线性折算（`u_dig_kind=relative`），强非线性量的数字化误差
  宜用 `absolute` + 逐点 σ，本节点只支持这两种口径。
* **局限2**：插值线性、**禁止外推**；外推需求以 WITHHELD（`REFERENCE_OUT_OF_RANGE`）显式挂账。
* **局限3**：`withheld_gate_share` 的分母是"全部门"，对早期门很少的战役波动大，需与
  `withheld_mean_age_days` 同看；KPI 已按小时桶幂等，跨仓汇总需另加 organization 维度过滤。
* **坑4 演示脚本的静默降级**：`ds()` 曾用 `doi or <fallback>` 给"故意缺出处"的数据集
  兜底了一个假 DOI，结果 8 门全部退化为 `REFERENCE_INCOMPLETE`、`withheld_gate_share`
  假报 100%——**合成工件也会骗人**。修法：显式 `Optional[str]` 默认 + 演示脚本自检
  （三态必须同时出现，否则 assert 失败），并新增 pytest 用例钉住该自检。
* **适用范围**：任何"判据依赖外部参考数据"的门（benchmark 对标、解析解比对、复现校验）；
  不适用于纯自洽性检查（守恒律/量纲），后者无需参考数据集，不该入本账本。

## ⑥ 误差判定

本节点为**平台机制**任务，判据不是物理量误差，而是机制正确性；逐项对照见下表
（"是否 <3%" 列对物理量误差不适用，故显式标注口径）：

| 检查项 | 判据 | 结果 |
|---|---|---|
| 门→数据集绑定可机器读 | requires_reference.dataset_id + UNIQUE 约束 | PASS |
| 登记即自动重判（无需人工触发） | register_reference → rejudge_gate 链式调用 | PASS（6 个事件步） |
| 三态保留（WITHHELD 不被压成二态） | verdict ∈ {PASS,FAIL,WITHHELD} CHECK 约束 | PASS（终态分布 {'PASS': 2, 'FAIL': 3, 'WITHHELD': 3}） |
| 审计链自洽 | verify_audit_chain 逐行重算 prev_hash/row_hash | PASS（29 行 / 8 链） |
| 事后放宽 tol 被拦 | 契约指纹哈希核对 + 触发器 + min(基线,现行) | PASS（TAMPER_BLOCKED 1 行） |
| KPI 落 tensor_guide_tracker | withheld_gate_share / withheld_mean_age_days | PASS（26 行，13 天 × 2 key） |
| 未清偿项自动派单 | (repo, dataset_id, age) 分桶 + 幂等工单号 | PASS（3 张工单） |
| 数值复核：E 与手算相对误差 | 门 SYN-G1：m=1.41, r=1.40 ⇒ E=+0.714% | 机器值与手算一致（差值 0，pytest 断言 `approx`） |
| KPI 复算 | day12 share = 未清偿/总门数 | 程序化一致（`compute_kpis` 与 scenario.json 同源） |

**关于"是否 <3%"**：本节点无物理基准量，故不作 <3% 声明；替代判据是"机制断言 100% 通过"
（26/26 pytest）与"审计链 0 断裂"（`ok=True`）。物理量误差判据属消费方（如基准对标门），
本模块提供的是口径与证据链，不产生物理结论。

## 交付物与复现

```
tools/vv/withheld_gates.py             # 核心库 + CLI（--declare/--register/--rejudge/--kpi/--dispatch/--verify）
backend/migrations/113_withheld_gates.sql   # PG 迁移（表/触发器/视图/KPI 索引）
tests/test_withheld_gate_clearing.py   # 26 用例
artifacts/withheld_gates/scenario.json # 事件流 + 终态 + KPI + 派单 + 审计校验
artifacts/withheld_gates/kpi_series.csv
artifacts/withheld_gates/fig1..3.png
```

复现：`python3 tools/vv/withheld_gates_demo.py --out-dir artifacts/withheld_gates --report docs/reports/IN50-W4-N09_withheld_gates.md`

**偏离声明**：任务书写 `backend/migrations/xxxx_withheld_gates.py`，但本仓迁移 runner
（`backend/migrations/run_migration.py`）只执行编号 `*.sql` 并记录 checksum；为让迁移真正被
应用，交付 `.sql`（编号 113），并在测试里加 PG 迁移 ↔ SQLite DDL 列级 parity 断言防漂移。
