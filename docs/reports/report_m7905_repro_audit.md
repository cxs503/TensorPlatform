# P70W2-N09 历史验收声明抽样式重跑复证（成熟化单）

## ①案例介绍
对 `campaign_nodes` 中 `status=done` 的 **918 条战役节点**（done=474）做
repo×年 分层抽样重跑复证，目标是把 `tensor_guide_tracker.repro_rate` 从占位 0 换成**可审计实测数字**。
本轮抽样 N=28，覆盖 **11 个仓**、分层 11 个，候选池（带可执行工件）=483。

## ②怎么做的
1. 节点快照走平台 API（plans→nodes），零直连 DB；候选判据是结构性的（worktree 存在 ∧ `git ls-tree` 里有测试 ∧ 有数值判据）。
2. **分层确定性抽样**：层=repo×年，层内按 `sha256(seed|node)` 定序、轮转取满 N（小仓不被大仓淹没；同输入同输出）。
3. **干净检出**：`git archive HEAD | tar -x` 到临时目录执行，不写被审计仓工作树、不改 .git（只读纪律）。
4. 执行 `pytest <测试文件> -q`，单节点墙钟上限 600s；非通过且非 blocked 征兆 → **二次重跑**比失败集签名（drift 可复现性 ≥2 次）。
5. 判据口径：observed 相对容差 1e-6 或报告自带容差（`<5%`→0.05，`<1e-9` 直取）；diagnostic 近零量（|x|≤1e-8）用绝对容差 1e-12。
6. 归因四分类：source_change / dependency_version / float_path / environment（源码>依赖>浮点>环境，全部基于可观测差异）。
7. KPI = `pass/(pass+drift+fail)`，**blocked 不进分母**；KPI 必须由 JSON 逐项加总复算（|KPI−复算|≤1e-9）。

## ③计算结果（程序化注入，零手抄）
| 指标 | 数值 |
|---|---|
| 抽样 N | 28 |
| 覆盖仓数 | 11 |
| pass / drift / fail / blocked | 0 / 0 / 0 / 28 |
| 通过率分母（pass+drift+fail） | 0 |
| **repro_rate** | **None**（分母为 0 → 未定义，拒绝写 0） |
| blocked 占比 | 1.0 |
| blocked 归因分布 | {'timeout': 21, 'dependency_or_collection_error': 7} |
| KPI 落库 | refused_undefined_rate（no countable items: repro_rate undefined (refuse to write 0)） |

## ④结果图
见附图：左=blocked 归因分布，右=单节点墙钟分布（上限 600s）。

## ⑤注意点（踩坑与局限）
- **诚实 FAIL**：本轮 repro_rate **未定义**（28/28 采样节点均 blocked：{'timeout': 21, 'dependency_or_collection_error': 7}），
  因此判据 (b)「复证通过率落库且与 JSON 逐项可加总一致」**未达成** —— 不写 0、不写任何替代数字。
- blocked 主因是**执行预算与依赖**：TensorLBM/TensorDEM 等仓的测试套件重依赖（torch/SDAA）不可得 → collection error；
  其余为单节点墙钟超预算（211 次超时来自首轮 20s/50s 收紧预算的smoke，说明预算设置本身是主要敏感项）。
- 沙箱**无 PG 凭据**（psycopg2 为 stub、psql 无口令）且本分支新增的 `POST /tensor/repro/audit-kpi` 端点尚未部署，
  故 KPI 落库为 `pending`：`artifacts/repro_audit/kpi_upsert.sql` + `kpi_pending.json` 已就位，可由值守席位 `psql -f` 重放。
- 数值复算层（工件 mtime 越界判「重新生成」）已实现并有单测，但本轮无节点跑到该层（全部在测试执行层 blocked）。

## ⑥误差判定
本轮**无可用基准**（无 pass/drift 项），关键量误差无法逐项列出 —— 这是 blocked 而非误差：
若把 blocked 折算进通过率即为造假（判据 d 明令禁止）。下一波次的正确修法是把单节点预算恢复为 600s、
剔除依赖不可得的仓、并把测试选择收窄到「提到本节点工件/编号」的文件（本工具已实现 `_select_tests` 优先热文件）。
