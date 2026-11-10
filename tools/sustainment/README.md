# 平台自主续航运维矩阵（p228w1-N10 / sustainment ops）

把四件“平台元数据大概是安全的”假设，变成四条**机器裁决**：

| 模块 | 一句话 | 入口 |
|---|---|---|
| `lineage.py` | 工件血缘 DAG：全量 sha256 + 证据分级的依赖边 + 冻结/篡改检测 | `build_report()` / `Lineage` / `verify_lock()` |
| `evidence_audit.py` | 报告证据链：图 sha256 / 数字溯源 / 判据覆盖 + 诚实 FAIL 披露 | `audit()` / `AuditResult.to_json()` |
| `backup_drill.py` | 隔离命名空间恢复 + 三重对拍 + 三类坏备份负控 + RPO/RTO 账本 | `run_drill()` / `append_ledger()` |
| `worktree_gc.py` | 工作树六类治理 + tar+zstd 归档 + 磁盘压力门 + flock | `run_gc()` / `discover()` / `disk_pressure()` |

**零领域语义**：本包不认识任何物理量、船型、域判据。表名、扫描根、判据阈值一律由
CLI/JSON 注入（`config/sustain_core_tables.json` 只列平台元数据表）。测试用合成域名。

## 安装/依赖

标准库 + `networkx`（DAG）；图渲染需 `matplotlib`；归档需 `zstd`+`tar`；
PostgreSQL 引擎需 `pg_dump`/`psql`（缺一则该引擎报 `engine_unavailable`/`WITHHELD`，绝不假装通过）。

## CLI（`python3 -m tools.sustainment <cmd>`）

```bash
# 1) 血缘：建图 + 冻结 + 图 + 追溯
python3 -m tools.sustainment lineage build --root artifacts --root docs \
  --out artifacts/sustainment/lineage.json \
  --lock artifacts/sustainment/lineage_lock.json \
  --figure artifacts/sustainment/figures/lineage_dag.png \
  --trace artifacts/demo/summary.md          # 可选：正向/反向追溯
python3 -m tools.sustainment lineage verify --root artifacts --root docs \
  --lock artifacts/sustainment/lineage_lock.json --out artifacts/sustainment/lineage_verify.json

# 2) 备份演练（SQLite 参考引擎，可无凭据自测；--engine postgres 走实库）
python3 -m tools.sustainment backup --source /tmp/platform.db --fixture 5 --fixture-rows 200 \
  --workdir /tmp/drill --ledger artifacts/sustainment/backup_ledger.json \
  --out artifacts/sustainment/backup_drill.json

# 3) 工作树治理（默认 dry-run；--apply 才归档并移除）
python3 -m tools.sustainment gc --repo /root/tensor-ecosystem/TensorPlatform \
  --worktrees-root /root/tp-agent/work --workers 16 --ttl-hours 24 \
  --out artifacts/sustainment/worktree_gc.json

# 4) 报告证据链自检
python3 -m tools.sustainment evidence --report docs/reports/x.md \
  --figures artifacts/sustainment/figures_manifest.json \
  --claims artifacts/sustainment/claims.json \
  --criteria artifacts/sustainment/criteria.json --base-dir . \
  --out artifacts/sustainment/evidence_audit.json

# 5) 四条一次跑完（能力矩阵）
python3 -m tools.sustainment matrix --out-dir artifacts/sustainment --report-md docs/reports/x.md
```

退出码：`0` 通过 · `1` 判定失败（含 honest FAIL 以外的失败）· `4` GC 锁被占用。

## 关键设计（为什么这么做）

### 血缘：证据分级 + 环消解
边只有三条规则，且**每条边都记录规则名**：
`R1_declared`（`*.provenance.json` 的 inputs/sources）> `R2_sibling`（同 stem 家族，code<data<figure<doc）
> `R3_mention`（唯一 basename 文本提及）。**故意没有 mtime 邻近规则**——时间先后不是因果。

真实树上 R2+R3 会闭环（脚本与它写出的 md 互相提及）。消解策略：按证据强度**先弃 R3、再弃 R2**，
R1 声明的矛盾**永不自动消解**（那是数据矛盾，必须暴露）。每次丢弃都连同闭合它的环入审计链
（`cycle_resolution.dropped_edges`）；verifier 必须重放同一套消解，否则被弃弱边会永久伪装成漂移。

审计自身产物（`--out/--lock/--figure` 及其目录）自动排除出扫描域：审计的产物不能是审计的输入。

### 备份演练：三重对拍 + 负控
- 隔离命名空间：SQLite → 独立库文件；PostgreSQL → 同库独立 schema（限定名重写）。
- 不变量：`row_count`、`content_digest`（按列序规范化的逐行 sha256）、`schema_signature`（列名+类型+主键）。
- 负控必须**全部被检出**，否则整场演练 FAIL：`NC1` 截断 dump、`NC2` 缺表 dump、`NC3` 恢复后改格。
  不能失败的校验器不是校验器。
- RPO = 被恢复快照的年龄；RTO = 从恢复开始到三重对拍通过的实际墙钟；账本 append-only（新在前）。
- 源库指纹前后比对（`source_untouched`），演练绝不动源。

### 工作树六类（保护类优先）
`dirty` / `orphan` / `unpushed` → PROTECT；`active` → KEEP；`stale`（git 报 prunable，目录已消失）
→ PRUNE；`finalized`（base 已含其全部提交且闲置 ≥ TTL）→ ARCHIVE+RM。
顺序刻意让“可能丢工作”的判定先于“可删”判定：**误测只会退化成留着**。
squash-merge 的分支 tip 不是 base 祖先、`ahead>0`，因此被归到 `unpushed`（保护）——牺牲回收率换零丢单。

归档 tar+zstd + sha256 sidecar，删前回读 sidecar；`flock` 保证两个 GC 不会并发处理同一棵树。

### 磁盘压力门 / 盲区测量
`<85%` pass（不删，仅可归档）· `85–90%` warn · `≥90%` force（可删 + **禁止新建工作树**；
其它工具可 `from tools.sustainment.worktree_gc import assert_can_create_worktree` 复用）。

盲区：`git worktree list` 只认识本仓工作树，目录下还有**其它仓的活动工作树**（有 `.git` 标记）。
`measure_stray_dirs()` 把二者分开计数，只测量真残留（无 `.git` 标记）体积，永不删除（无主即不动）。

## 测试

```bash
python3 -m pytest tests/test_sustain_lineage.py tests/test_sustain_evidence_audit.py \
  tests/test_sustain_backup_drill.py tests/test_sustain_worktree_gc.py \
  tests/test_sustain_cli.py -q
```

覆盖：sha256/边规则/追溯/环消解/锁漂移/自噬排除 · 六要素/图 sha/数字溯源（含尾随标记 scheme 门）/
判据重算与诚实 FAIL 披露 · 隔离恢复/三重对拍/三类负控/账本/源不可变 · 六类分类/归档校验/
门限三档/flock 互斥/指标语义/盲区判别 · CLI 退出码端到端。

## 已知局限（详见 docs/reports/P228W1-N10_sustainment_matrix.md ⑤）

1. 线上 PostgreSQL 演练需 DSN；无凭据时报 FAIL/WITHHELD，不假通过。
2. R1 声明边依赖产物侧写 provenance sidecar；平台目前几乎没有，血缘主要靠 R3 启发式。
3. `protected` 类工作树不测体积，故“可回收上限”只覆盖可回收类。
4. flock 只保证本模块互斥，不能阻止其它工具直接 `rm -rf`。