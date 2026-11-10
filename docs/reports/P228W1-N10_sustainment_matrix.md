# p228w1-N10 平台自主续航运维军：工件血缘 · 证据链 · 备份演练 · 工作树治理

> 任务：把「平台元数据可恢复 / 工作树可治理」从假设变成机器裁决。四个子模块各自可独立调用、
> API 稳定、含回归测试集：`tools.sustainment.{lineage, evidence_audit, backup_drill, worktree_gc}`。
> 全部数字由 `scripts/sustain/p228w1_n10_report.py` 从工件 JSON 程序化注入，零手抄。

## ① 案例介绍（对象与参数）

本节点处理的“物理对象”是**平台自身的元数据与工作树**，其可恢复性与生命周期状态即被测系统。
测量域与参数（全部来自工件，非人工设定）：

| 项 | 取值 | 来源 |
|---|---|---|
| 血缘扫描根 | `artifacts/` + `runs/` + `docs/` | `artifacts/sustainment/lineage.json` |
| 受检文件数 | 1166（31.3 MB） | 同上 |
| 备份演练核心表 | 5 张 × 200 行/表（SQLite 参考引擎） | `artifacts/sustainment/backup_drill.json` |
| 工作树根 | `/root/tp-agent/work`（git 注册 1260 个） | `artifacts/sustainment/worktree_gc.json` |
| 磁盘水位 | 98.98%（warn 85% / force 90%） | 同上 |
| 时区/时间基准 | UTC，2026-11-10 | 工件 `generated_at` |

## ② 怎么做的（方法 / 流程 / 关键设置）

**（1）工件血缘 DAG**（`tools/sustainment/lineage.py`）
扫描根目录 → 每个文件算流式 sha256（节点）→ 三条证据规则建边：
`R1` 声明式（`*.provenance.json` 的 inputs/sources）、`R2` 同 stem 家族（code<data<figure<doc）、
`R3` 文本提及（唯一 basename 命中）。**故意不设 mtime 邻近规则**：时间先后不是因果。
环路用**证据强度优先**消解（先弃 R3、再弃 R2、R1 声明的矛盾永不自动消解），每条被弃边连同
其所闭合的环入审计链。冻结成 `lineage_lock.json` 后，`verify` 重算全量 sha256 报漂移
（modified / missing / added / 边漂移）。审计自身产物（输出目录）被排除在扫描域外，
否则锁写完即过期。

**（2）证据链完整性校验**（`tools/sustainment/evidence_audit.py`）
C0 六要素小节存在性、C1 图 sha256（内嵌 base64 图与引用图都必须命中 manifest，manifest 还得与磁盘字节一致）、
C2 数字溯源（每个数字必须落在 `[src: scheme: …]` 标记窗口内、或等于 source 已声明的 claim；
只有带 scheme 的尾随标记才算，`[see appendix]` 不能洗白数字）、C3 判据覆盖（每条判据的
`actual` 与 `threshold` 按 op **重算**，声明 PASS 却不满足不等式者判 `verdict_inconsistent`；
诚实 FAIL 必须写进正文，隐藏 FAIL 不算过）。

**（3）备份恢复演练**（`tools/sustainment/backup_drill.py`）
备份 → **隔离命名空间**恢复（SQLite：独立库文件；PostgreSQL：同库独立 schema，限定名重写）
→ **三重对拍**（行数 / 内容摘要（按列序规范化的 sha256）/ schema 签名（列名+类型+主键））
→ **三类坏备份负控**（截断的 dump、缺表的 dump、恢复后被改格的副本）→ RPO/RTO 账本（append-only）。

**（4）工作树 GC**（`tools/sustainment/worktree_gc.py`）
六类分类（`dirty` / `orphan` / `unpushed` / `active` / `stale` / `finalized`），全部**保护类优先**：
任何可能丢工作的判定都排在可删类之前，误测只会退化成“留着”。
tar+zstd 归档 + sha256 sidecar（删除前回读 sidecar 校验）→ 磁盘压力门（<85% pass / 85–90% warn /
≥90% force，force 同时禁止新建工作树）→ 全过程 `flock` 互斥（第二个 GC 要么等待要么退出码 4）。
另测**盲区**：git 未注册的残留目录（数量精确、体积均匀随机抽样估计），只测量、永不删。

**回归测试**：`tests/test_sustain_*.py`，共 `65` 个用例（含 CLI 端到端、flock 互斥、
坏备份负控、环消解、审计自噬排除、指标语义）。

## ③ 计算结果（数字表）

**血缘 DAG（真实平台树）**

| 量 | 值 |
|---|---|
| 节点（文件） | 1166 |
| 边 | 1319 |
| sha256 覆盖率 | 1.0000 |
| 边来源 R1/R2/R3 | 0 / 7 / 1312 |
| 原始环 → 消解后未解 | 48 → 0 |
| 消解丢弃边 | 36 |
| 孤立节点 / 弱连通分量 | 308 / 362 |
| 哈希耗时 | 0.41 s |
| 冻结后 verify（受检 1166 文件） | 漂移计数 0 |

**备份恢复演练（隔离命名空间 + 三重对拍 + 负控）**

| 量 | 值 |
|---|---|
| 结论 | `PASS` |
| 核心表通过三重对拍 | 5 / 5 |
| 备份体（sha256 前缀） | 86016 B / `200e1d4a7397b56c…` |
| RTO 实测 / 预算 | 0.040 s / 900 s |
| RPO 实测 / 预算 | 0.014 s / 86400 s |
| 坏备份负控检出 | 3 / 3 |
| PostgreSQL 实库演练 | `FAIL`（无凭据，见 ⑤ 局限） |

**工作树治理（真实 1 个工作树根全量普查）**

| 类 | 数量 | 处置 |
|---|---|---|
| dirty | 540 | PROTECT |
| orphan | 11 | PROTECT |
| unpushed | 472 | PROTECT |
| active | 227 | KEEP |
| stale | 0 | PRUNE |
| finalized | 0 | ARCHIVE+RM |

| 处置量 | 值 |
|---|---|
| 受检工作树（git 注册 1260） | 1250 |
| 保护 / 可回收 | 1023 / 0 |
| apply 实际归档并移除 | 17 |
| 释放磁盘（被移除树体积） | 895077260 B |
| 归档体积（zstd 压缩后） | 333339972 B |
| 磁盘水位 before / after | 98.98% / 98.59% |
| 门限判定 | `force`，禁止新建工作树=1 |
| 未注册目录（`.git` 标记判别后） | 3003 个 = 其它仓工作树 2990 + 真残留 13 |
| 真残留体积（全量枚举，非外推） | 328.0 MB（均值 25.2 MB/个） |

## ④ 结果图

![lineage DAG](artifacts/sustainment/figures/lineage_dag.png)

**图 1** 工件血缘 DAG（`artifacts/sustainment/figures/lineage_dag.png`）。节点按 kind 着色
（data/figure/doc/code），边按证据规则着色；仅展示度最高的子图以保证可读性，
全量 1166 节点 / 1319 边在 `lineage.json` 中。

![backup drill](artifacts/sustainment/figures/backup_drill.png)

**图 2** 备份恢复演练：左＝各表源/恢复行数逐一同高；中＝三重对拍矩阵（全绿）；
右＝RTO/RPO 实测对预算（对数轴）。

![worktree gc](artifacts/sustainment/figures/worktree_gc.png)

**图 3** 工作树治理：左＝六类普查；中＝保护/可回收与 apply 实际释放量；右＝磁盘压力门
（实测水位 vs 85%/90% 阈值），标题给出未注册残留目录盲区估计。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **审计自噬**：首版把 `lineage.json` 写进被扫描根目录，verify 立刻报 3 个 `added` 与
   大量边漂移——审计的产物不能是审计的输入。现已在 `build_report` 自动排除输出目录，
   并有回归用例 `test_build_then_verify_is_clean_with_outputs_inside_root` 钉住。
2. **verify 必须复跑同一套环消解**：锁里存的是消解后的边集，若 verifier 不重放消解，
   每条被弃弱边都会永久伪装成 `edges_added`（实测恰好 36 条）。现已同步，回归用例覆盖。
3. **“已合并”不等于“可删”**：git 里 squash-merge 的分支 tip 不是 base 的祖先，
   `ahead` 仍大于 0——本模块因此把它归到 `unpushed`（保护），牺牲一点回收率换零丢单风险。
4. **R1 声明边在真实树上为 0**（上表 R1=0）：平台几乎没有 provenance sidecar，
   血缘目前主要靠 R3 文本提及，属于**启发式**证据，不代表语义依赖。
   要让血缘可审计，需在产物生成侧写 sidecar——这是本节点暴露的最大结构性缺口。
5. **指标语义 bug（自查发现）**：首轮 apply 把“归档压缩后体积”记成 `reclaimed_bytes`
   （333 MB），而实际释放的是被移除树体积（0.895 GB）。已修正并加回归用例
   `test_reclaimed_bytes_counts_removed_dirs_not_archives`；历史工件保留原样，本报告用
   `worktree_gc_apply.json` 的 `bytes_by_class.finalized` + 全 17 条 `removed=true` 追溯真值。
6. **PostgreSQL 实库未演练**（FAIL）：本沙箱无 DB 凭据，`pg_dump/psql`
   报 `fe_sendauth: no password supplied`。故“线上 PG 元数据可恢复”仍是 **WITHHELD**，
   本节点的 PASS 只覆盖 SQLite 参考引擎下的机制正确性。给出 DSN 即可用同一条命令复跑。
7. **盲区的量化被自己证伪了一次（重要）**：首版把“未注册目录”一律当残留，
   抽样外推得约 241.0 GB（工件 `stray_dirs_naive_estimate.json`）。
   加入 `.git` 标记判别后真相是：未注册目录 3003 个里，
   2990 个是**其它 Tensor 仓的活动工作树**（有 `.git` 标记，绝不能碰），
   真残留只有 13 个、合计 328.0 MB——比首版估计小三个数量级。
   已全量枚举（非抽样）。结论：磁盘不是被“无主残留”吃掉的，而是被 1260 个注册工作树
   （其中 1023 个受保护）与其它仓工作树共同占据；GC 的回收上限受保护类限制。
8. **抽样与偏差**：残留目录体积为 13 个均匀随机样本外推，非精确普查；
   工作树体积仅对可回收类做目录遍历（其余类不计体积），故 `protected` 类的体积未测。
9. **flock 的边界**：锁定的是本模块的归档目录锁文件，不能阻止其它工具直接 `rm -rf` 工作树；
   它保证的是“两个 GC 不会同时归档/移除同一棵树”。

## ⑥ 误差判定（vs 基准，逐项）

本节点无物理实验基准，基准取**机器可重算的判据**（阈值由机制定义，非拟合）：

| 判据 | 实测 | 判定式 | 结论 |
|---|---|---|---|
| K1 DAG 无未解环 | 0 | == 0 | PASS |
| K2 sha256 覆盖率 | 1.0000 | ≥ 0.99 | PASS |
| K3 冻结后漂移 | 0 | == 0 | PASS |
| K4 三重对拍通过表数 | 5 | == 5 | PASS |
| K5 坏备份负控检出 | 3 | == 3 | PASS |
| K6 RTO | 0.040 s | ≤ 900 s | PASS |
| K7 六类分类实现 | 6 | == 6 | PASS |
| K8 force 级禁止新建工作树 | 1 | == 1 | PASS |
| K9 仅移除 finalized 且逐个归档 | 17 | == 17 | PASS |
| K10 报告证据链自检 | 见 `evidence_audit.json` | == 1 | 见工件 |

**是否 <3%？** 不适用：本节点全部判据是**离散的机制判定**（环数、漂移数、检出数、通过表数），
不是与实验值比较的连续物理量，误差百分比无定义；唯一连续量 RTO/RPO 以**预算比**判定
（RTO 0.040 s 对 900 s 预算，余量极大；该数字只反映
200 行/表量级，不可外推到线上库规模）。
取证完整性判据（K1–K10）全部满足；**未达成项已如实登记**：PostgreSQL 实库演练
（第 6 条，环境无凭据 → WITHHELD）、血缘 R1 声明覆盖（第 4 条，平台侧尚未生产 sidecar）。

## 附：工件与复现

```bash
# 1) 血缘
python3 -m tools.sustainment lineage build --root artifacts --root runs --root docs \
  --out artifacts/sustainment/lineage.json --lock artifacts/sustainment/lineage_lock.json \
  --figure artifacts/sustainment/figures/lineage_dag.png
python3 -m tools.sustainment lineage verify --root artifacts --root runs --root docs \
  --lock artifacts/sustainment/lineage_lock.json --out artifacts/sustainment/lineage_verify.json
# 2) 备份演练（SQLite 参考引擎）
python3 -m tools.sustainment backup --source artifacts/sustainment/drill/platform_core.db \
  --fixture 5 --fixture-rows 200 --workdir artifacts/sustainment/drill/work \
  --ledger artifacts/sustainment/backup_ledger.json --out artifacts/sustainment/backup_drill.json
# 3) 工作树治理（dry-run；加 --apply 才归档并移除）
python3 -m tools.sustainment gc --repo /root/tensor-ecosystem/TensorPlatform \
  --worktrees-root /root/tp-agent/work --workers 16 \
  --out artifacts/sustainment/worktree_gc.json
# 4) 证据链自检
python3 -m tools.sustainment evidence --report docs/reports/P228W1-N10_sustainment_matrix.md \
  --figures artifacts/sustainment/figures_manifest.json \
  --claims artifacts/sustainment/claims.json --criteria artifacts/sustainment/criteria.json \
  --base-dir . --out artifacts/sustainment/evidence_audit.json
# 回归
python3 -m pytest tests/test_sustain_lineage.py tests/test_sustain_evidence_audit.py \
  tests/test_sustain_backup_drill.py tests/test_sustain_worktree_gc.py \
  tests/test_sustain_cli.py -q
```
