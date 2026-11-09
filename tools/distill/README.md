# tools/distill —— 战役波次成果汇总与知识蒸馏（域无关）

三个可复用件，串起来就是一条"波次收口"管线：

```
战役节点账本 (JSON)  ─┐
各节点工作树证据切片  ─┴→ collect_evidence.py → campaign_bundle.json
                                                   │
                                    campaign_distill.py  → coverage_matrix.json
                                                         → knowledge_graph.json
                                                         → gaps.json（含提案草案）
                                                         → distill_summary.json（判据数字+产物路径）
                                                         → report.md（机器校验零违规）
                                                         → verified/<case>/{manifest.json,SHA256SUMS}
                                                   │
                                    make_figures.py → fig1 覆盖+证据盘点 / fig2 图谱 / fig3 阈值对比
```

## 为什么拆成三段

| 段 | 只做一件事 | 不做的事 |
|---|---|---|
| `collect_evidence` | 搬运证据：git 未跟踪/未提交变更逐件 sha256 + 新增测试的 pytest 探针结果 | **不写结论**（`claims` 留空）——避免采集器替节点自评 |
| `campaign_distill` | 机裁：判据状态只按"结论 + 来源"推导；图谱/缺口/报告/归档全部由数据算出 | 不含任何领域语义；阈值走 `--spec`，叙述走 `--narrative` |
| `make_figures` | 把账本画成可核验的图 | 不重算任何数字，只读 JSON |

## 用法

```bash
# 1) 采集（--probe 会跑一次 pytest，禁用字节码与缓存，不污染对方工作树）
python3 tools/distill/collect_evidence.py \
  --plan-nodes plan_nodes.json --missions missions_snapshot.json \
  --work-root /path/to/work --probe --out inputs/campaign_bundle.json

# 2) 蒸馏（--spec/--narrative 都是数据文件；域内容全部在这里注入）
python3 tools/distill/campaign_distill.py \
  --bundle inputs/campaign_bundle.json --narrative inputs/narrative.json \
  --out out --archive verified/<case> --report docs/reports/<case>.md

# 3) 图
python3 tools/distill/make_figures.py \
  --out-dir out --bundle inputs/campaign_bundle.json --fig-dir figures
```

退出码：`0` = 报告/图谱/归档校验全通过；`1` = 有违规（违规清单落 `distill_summary.json`）；`2` = 输入缺失。

## 数据契约要点

* 判据状态四档：`PASS_CLAIMED` / `FAIL_CLAIMED` / `EVIDENCE_ONLY` / `NO_EVIDENCE`。
  **结论缺来源一律 `FAIL_CLAIMED`**（不可采信），有工件无结论映射记 `EVIDENCE_ONLY`。
* 节点级 `VERIFIED` 要求该节点全部判据 `PASS_CLAIMED`；否则 `PARTIAL` /
  `UNVERIFIED_EVIDENCE` / `WITHHELD`。
* 缺口五规则：`unverified_criteria` / `stale_inflight` / `failed_node` /
  `dependency_open` / `claim_without_source`；每条缺口都挂提案草案骨架
  （title/repo/physics_domain/why_frontier/goal/acceptance_criteria/dependencies/metric），
  字段值取自节点自身数据。
* 报告硬校验：必需章节、`[E#]` 引用完整性、正文数字 = JSON 实算值、覆盖矩阵逐行存在、
  无占位符（TBD/TODO/???/FIXME）。
* 归档：复制前后 sha256 必须一致；同名不同内容直接拒绝覆盖；`SHA256SUMS` 可
  `sha256sum -c` 复核。

## 确定性

无墙钟：`snapshot_utc` 由 bundle 提供；实体/边/缺口/覆盖行全部确定性排序。同一 bundle
连跑两次输出逐字节相同（`tests/test_campaign_distill.py::test_cli_end_to_end_and_determinism`）。

## 测试

```bash
python3 -m pytest tests/test_campaign_distill.py -q
```

21 个用例：覆盖矩阵四档 + 缺来源/越界等级变异体、图谱规模与悬空边、五类缺口规则、
报告校验五类变异体、归档篡改检测、CLI 端到端确定性、域词零命中
（`tools/audit/domain_leak_scan.py`，扫描路径已登记在本目录）。