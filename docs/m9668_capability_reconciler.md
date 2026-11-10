# 能力台账 × 工件树双向对账器：成熟化与机器判据证据（p176w1-n03）

**节点**：`p176w1-n03-capability-reconciler-maturity`（自主战役，物理域节点，层级 L1）
**仓库**：TensorPlatform
**交付物**：

| 路径 | 作用 |
|---|---|
| `backend/tools/capability_reconciler.py` | 对账器实现（只读；939 行） |
| `backend/tests/test_capability_reconciler.py` | 29 例 pytest（判据 C1–C5） |
| `runs/m9668/reconcile_report.json` | 真实工作树对账报告（审计工件） |
| `runs/m9668/criteria.json` | 判据原始数字（程序化写出，零手抄） |
| `runs/m9668/maturity_evidence.json` | `contracts/maturity-evidence.v1.schema.json` 格式的证据 |
| `runs/m9668/figures/detection.png` | 注入失配检出图 + 各场景失配率图 |
| `runs/m9668/build_evidence.py` | 上述工件的一键重跑脚本 |
| `runs/m9668/deltas_snapshot.json` / `reports_snapshot.json` | 对账数据源快照（API 拉取，见下） |

---

## 1. 案例介绍：为什么能力账需要双向对账

组织能力账 `tensor_capability_deltas` 每个 succeeded mission 落一行：`artifacts`
声明交付的工件路径，`evidence` 携带 `head_commit`。这份台账同时驱动能力中心聚合读面
（`/tensor/capability/summary`）、报告对账门与 S×E 判级，但它的正确性此前**只能靠自述**：

* 账上有、树上无：工件被删/改名/从未提交，能力声明成为空头支票；
* 树上有、账上无：成果产出但没进能力账，不可发现、不可复用；
* 账上 commit 不在 head：验收证据指向被改写/未合并的历史。

本节点实现三方交叉对账（磁盘工作树、git head 树、DB 台账），把三类失配变成
机器可计数、可审计的指标。

### 数据源与量测口径

| 项 | 值 | 来源 |
|---|---|---|
| 台账总行数 | 4901 | `GET /tensor/capability/summary` |
| 其中实质增量（非 `no_substantive_delta`） | **0** | 同上 |
| succeeded missions / 已入账 | 7547 / 4812（63.76%） | 同上 |
| 本次对账取用流水 | 200（API 单次上限） | `GET /tensor/capability/deltas?limit=200` |
| 其中 TensorPlatform mission 行 | 46（全部 `no_substantive_delta`，`artifacts=[]`） | 快照 `deltas_snapshot.json` |
| 工件树扫描范围 | `artifacts/`（忽略 `.gitkeep`/`__pycache__`/`.recycle` 等） | 工作树 |
| 扫描到工件文件 | 94 | `reconcile_report.json.scope.present_total` |
| 审计时点 git head | `247439c160eff781ff611fb6256ed5b54e8dfb64` | `git rev-parse HEAD` |

> 口径声明：资料源为 200 行样本（API 上限），不是全量 4901 行；`report_owed`
> 的"已报告"集合来自最近 100 条报告。结论按样本表述，不外推为全量精确值。

---

## 2. 怎么做的（方法 / 流程 / 关键设置）

### 2.1 三类失配的判定规则（全部机器可复算）

| 类别 | 判定 | 证据 |
|---|---|---|
| `declared_missing` | 声明路径在工作树无任何匹配（含 glob 无匹配） | 磁盘 `Path.exists`/`glob` |
| `undeclared_present` | `artifact_roots` 下文件不被任何声明覆盖（精确/目录前缀/glob） | 磁盘遍历 vs 声明集 |
| `not_in_head` (commit) | 记录 `evidence.head_commit` 非当前 ref 祖先（不存在/不可解析同样计入，`reason` 区分） | `git merge-base --is-ancestor` |
| `not_in_head` (file) | 声明工件在磁盘存在但不在 ref 的 git 树（未提交/只在别的分支） | `git ls-tree -r HEAD` |

### 2.2 可审计指标公式

```
declared_total = 去重声明路径数（多 mission 声明同一路径只算一次检查）
present_total  = artifact_roots 下扫描到的文件数
commit_total   = 去重 head_commit 引用数（git 不可用时置 0 并标注 warning）
total_checks   = declared_total + present_total + commit_total        # 失配率分母
mismatch_rate  = 0.0  if total_checks == 0 else mismatch_count / total_checks
report_owed    = 有实质 delta 声明但无报告记录的 mission 集合
```

### 2.3 工具链与流程

1. 登录 `$TENSOR_API_BASE`，拉取 `deltas_snapshot.json`（200 行）与 `reports_snapshot.json`（100 条）；
2. `python backend/tools/capability_reconciler.py --repo-root . --artifact-root artifacts \
   --deltas-json runs/m9668/deltas_snapshot.json --reports-json runs/m9668/reports_snapshot.json \
   --mission-repo TensorPlatform --out runs/m9668/reconcile_report.json`；
3. `python runs/m9668/build_evidence.py` 跑 C1–C4 控制场景 + 解析 pytest 计数 + 出图；
4. 全程只读：不改 DB、不动 git 配置、不写工作树之外路径。

### 2.4 关键设置

* **仓隔离**：台账多仓共享，声明路径相对各自仓库；`--mission-repo` 过滤避免跨仓路径全部误判（模块内含 `mission_repo` 解析与 DB LEFT JOIN）。
* **JSONB 兼容**：sqlite/psycopg 下 JSONB 可能以字符串返回，`_coerce_json` 统一解析。
* **零领域语义**：模块/测试不含任何船型、公式、域词条；tmp 目录合成数据。
* **只读与幂等**：无状态，可重复执行；输出 JSON 键集固定（`schema_version` + `metrics`）。

---

## 3. 计算结果（判据数字，程序化注入）

### 3.1 机器判据（来源 `runs/m9668/criteria.json`）

| 判据 | 指标 | 目标 | 实测 | 判定 |
|---|---|---|---|---|
| C1 | 注入 10 条已知失配 → 检出数 / 召回 | 10 / 1.00 | 10 / 1.00（4 缺失 + 3 未声明 + 3 非 head） | PASS |
| C2 | 零失配场景 `mismatch_rate` | = 0 | 0.0（total_checks = 4，分母非平凡） | PASS |
| C3 | `report_owed` 与真实欠账集合 | 集合相等 | `[2,3]` vs `[2,3]`；`check_available=True` | PASS |
| C4 | 空台账 `mismatch_count` / `mismatch_rate` | 0 / 0 | 0 / 0.0（total_checks = 0，优雅返回） | PASS |
| C5 | pytest 用例数 / 失败 | ≥15 / 0 | **29 passed / 0 failed** | PASS |

C1 明细分项（`by_kind`）：`declared_missing=4`、`undeclared_present=3`、`not_in_head=3`，
与注入完全一致；每条失配均携带 `mission_ids` 归属，可回到具体 mission。

### 3.2 真实工作树对账（来源 `runs/m9668/reconcile_report.json`）

| 指标 | 值 |
|---|---|
| `scope.declared_total` | 0 |
| `scope.present_total` | 94 |
| `scope.commit_total` | 0 |
| `scope.total_checks` | 94 |
| `mismatch_count` | 94 |
| `mismatch_rate` | 1.0 |
| `by_kind` | `declared_missing=0` / `undeclared_present=94` / `not_in_head=0` |
| `report_owed.count` | 0（46 行全为 `no_substantive_delta`，不构成实质声明） |
| `git_available` / head | True / `247439c1…` |

**解读（诚实口径）**：`mismatch_rate=1.0` 不是"工件树腐化"，而是**声明覆盖缺口**：
当前 200 行样本（以及平台 summary 的 4901/4901）全部是 `no_substantive_delta`、
`artifacts=[]`、`evidence={}`，台账侧没有任何路径或 commit 可对。因此
`artifacts/` 下 94 个文件 100% "存在但没声明"。这正是本节点要暴露的问题：
能力台账的 `artifacts`/`evidence` 字段在 mission 完成流里没有被填充，双向对账
目前只能在"空账 vs 满树"的极端下给出上界，不能给出"某 mission 声明漂移"的细粒度结论。

---

## 4. 结果图

![detection](../runs/m9668/figures/detection.png)

`runs/m9668/figures/detection.png`（由 `build_evidence.py` 程序化生成，非手绘）：

* 左图 C1：三类失配的注入量 vs 检出量（4/4、3/3、3/3，召回 1.00）；
* 右图失配率跨场景：零失配 0.0000、注入场景 0.8333（10/12）、空台账 0.0000、
  真实工作树 1.0000。

本节点是平台后端机制任务，无物理场量云图；不生成 CFD/FEM 类等值线图，理由是
对账对象为账本/文件/commit 三元组，可比量只有计数与比率（见上表）。

---

## 5. 注意点（踩坑 / 局限 / 适用范围）

**踩坑**

1. `PYTHONPATH=backend python -m tools.capability_reconciler` 会被仓库根同名 `tools/`
   包遮蔽（先命中根 `tools`，报 `No module named tools.capability_reconciler`）。
   直接 `python backend/tools/capability_reconciler.py` 或测试内
   `sys.path.insert(0, backend)` 才稳定。
2. `git commit-tree` 同树同 message 同秒会产出**相同 sha**，注入多个"非祖先 commit"
   时必须换 message，否则三条注入塌缩成一条（本节点第一版就踩了，C1 只检出 8）。
3. JSONB 在 sqlite/psycopg 可能返回字符串；不解析会把 `artifacts` 当单字符迭代。
4. SQLAlchemy 2.x `execute` 只吃 `TextClause`，而裸 sqlite3 只吃 `str`；
   异常回退若不分派会掩盖 `no such table`，负控 C4 会变成 TypeError。

**局限**

* 数据源样本 200 行（API 上限），非全量 4901 行；`report_owed` 的已报告集合为最近
  100 条报告，欠账结论是**上界**而非精确全集。
* 台账当前 0 条实质声明，真实数据上"三类失配"的细粒度判别尚未被真实样本检验；
  C1 的 100% 检出是合成注入证据，不代表真实台账的召回。
* `not_in_head`(file) 对目录声明的归属是目录级标记，不展开到逐文件明细。
* 跨仓工件路径只在本仓工作树可比；跨仓对账需要按 `mission_repo` 分别执行。

**适用范围**：任意 git 工作树 + 任意 `artifact_roots`；只读，可安全接入巡检/看板。

---

## 6. 误差判定

| 关键量 | 基准 | 实测 | 误差 | 是否 <3% |
|---|---|---|---|---|
| C1 检出召回 | 注入 10 条 | 10 条 | 0% | **是** |
| C1 分类计数一致性 | 4/3/3 | 4/3/3 | 0 | **是** |
| C2 失配率 | 0 | 0.0 | 0 | **是** |
| C3 欠账集合 | `{2,3}` | `{2,3}` | 0（集合相等） | **是** |
| C4 空台账失配 | 0 | 0 | 0 | **是** |
| C5 用例失败数 | 0 | 0（29 passed） | 0 | **是** |

无实验/文献基准：本节点是平台机制，判据为**逻辑恒等式**（注入量=检出量、
零失配=0、集合相等、空表=0），不是物理量测量，故误差以精确相等判定而非
百分比容差；真实数据的 `mismatch_rate=1.0` 属"声明覆盖缺口"而非数值误差，
已在 §3.2 显式声明，不做粉饰。

---

## 附：复现命令

```bash
cd TensorPlatform
python -m pytest backend/tests/test_capability_reconciler.py -q          # 29 passed
python backend/tools/capability_reconciler.py --repo-root . \
  --artifact-root artifacts \
  --deltas-json runs/m9668/deltas_snapshot.json \
  --reports-json runs/m9668/reports_snapshot.json \
  --mission-repo TensorPlatform --out runs/m9668/reconcile_report.json
python runs/m9668/build_evidence.py                                      # C1–C5 + 图
```