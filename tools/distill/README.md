# tools/distill —— 战役波次成果汇总与知识蒸馏（域无关）

把"波次收口"从**手抄式汇总**改成**机器可复核的蒸馏**：输入是外部数据包
（`campaign_bundle.json`，域内容经工件注入），输出是四份 JSON 账本 + 一份
机器自校验的 markdown 报告 + sha256 归档。

```
plan_nodes.json  →  collect_evidence.py  →  campaign_bundle.json  证据包
   + 各节点工作树                                              （外部注入）
                                                                  ↓
                            campaign_distill.py  →  coverage_matrix.json   判据覆盖矩阵
                                                  →  knowledge_graph.json   实体/关系图
                                                  →  gaps.json               缺口 + 提案草案骨架
                                                  →  distill_summary.json    判据数字 + 违规清单
                                                  →  report.md               报告（零违规才退出 0）
                                                  →  verified/<case>/        工件 + manifest.json + SHA256SUMS
                                                                  ↓
                            make_figures.py      →  fig1_coverage_and_evidence.png
                                                  →  fig2_knowledge_graph.png
                                                  →  fig3_criteria_vs_threshold.png
```

## 本目录当前收录范围

本 PR（TensorPlatform#836 重开第 1 轮，单一功能点＝**蒸馏管线三件**）收录
完整管线三件：

| 段 | 只做一件事 | 状态 |
|---|---|---|
| `collect_evidence.py` | 搬运证据：git 未跟踪/未提交变更逐件 sha256 + 新增测试的 pytest 探针结果；**不写结论** | **本 PR** |
| `campaign_distill.py` | 机裁：判据状态只按"结论 + 来源"推导；图谱/缺口/报告/归档全部由数据算出 | **本 PR** |
| `make_figures.py` | 把账本画成可核验的图，不重算任何数字 | 已合并 |
| `run_case.py` | 一键四阶段 + **归档事后独立复核** + 确定性边界记账 | **本 PR（m8707）** |

## 一键跑案例 + 事后独立复核（`run_case.py`）

三段命令手工串有两个漏子：**没有单一失败语义**（哪步挂了只能看日志）、**归档事后不可复核**
（`verify_manifest` 与归档同进程，且只遍历 manifest 自己列的条目——归档里多出来的文件、
`SHA256SUMS` 与 `manifest.json` 互不一致、manifest 条目被改而文件没动，全检不出；
`sha256sum -c` 也不查多余文件）。`run_case.py` 把它们收成一个入口：

```bash
python3 tools/distill/run_case.py --case tests/fixtures/distill_case/case.json --workdir runs/<case>
python3 tools/distill/run_case.py --case tests/fixtures/distill_case/case.json --workdir runs/<case> --clean  # 重跑
python3 tools/distill/run_case.py --verify-only verified/<case>                  # 只复核已落盘归档
```

`case.json`（域内容经工件注入，代码零域语义）：

```json
{"id": "my_case", "bundle": "bundle.json", "spec": {...内联或路径...},
 "narrative": {...}, "workdir": "out", "archive_subdir": "verified"}
```

四阶段与判定：

| 阶段 | 做什么 | 红在哪 |
|---|---|---|
| `distill` | 调 `campaign_distill.main`（账本+报告+归档） | 退出码≠0 / 报告违规 / **bundle 里的证据解析不到** |
| `figures` | 调 `make_figures.main` | 退出码≠0 / 一张图都没出 / 缺 matplotlib·networkx |
| `verify_archive` | **独立复核**：只信 `SHA256SUMS`+磁盘，逐条复算并与 manifest 双向对账 | 缺文件 / 篡改 / **多余文件（陈旧件）** / sums↔manifest 不一致 / manifest 条目摘要被改 |
| `determinism` | 同 bundle 复跑一遍 `distill`，比对账本与报告 | 账本非逐字节相同 / 复跑产物缺失 |

产出 `workdir/case_run.json`：逐阶段 ok、每个产物的 `sha256`+字节数、问题清单（`rule` 可机器判）。
退出码 `0` 全通 ｜ `1` 有阶段红（记录照写，失败可定位）｜ `2` 案例契约不满足。

**两处必须知道的设计点**：

* **脏 workdir 拒绝复用**：上一版 bundle 的归档/账本留在盘上会被本次复用，本次归档于是夹带陈旧
  工件（实测：换 bundle 后残留旧件 → 独立复核报 `unlisted_file`，而蒸馏自身的 C5 自检仍绿）。
  所以默认拒绝，必须显式 `--clean`（只清 workdir 内**已知**产物路径，不做通配删除）或 `--force`。
* `bundle` 里的证据相对路径按 **bundle 所在目录** 解析，解析不到逐条报
  `evidence_unresolvable`——原链路对缺失证据是 `if p.exists()` 静默跳过，会让"证据丢了"
  的案例照样归档成功（假绿）。
* 确定性有边界，边界写进记录：`content_stable_outputs` 是跨 workdir 逐字节相同的产物；
  `path_embedding_outputs`（`distill_summary.json` / `report.md` / `manifest.json`）内嵌绝对
  路径，跨 workdir 会变——`determinism` 阶段比对 md 时做路径规范化，报告里的路径不是内容。

## 用法

### 1. 采集证据

```bash
python3 tools/distill/collect_evidence.py \
  --plan-nodes artifacts/<case>/sources/plan272_nodes.json \
  --missions artifacts/<case>/sources/missions_raw.json \
  --work-root work \
  --out artifacts/<case>/inputs/campaign_bundle.json \
  --probe
```

### 2. 蒸馏机裁

```bash
python3 tools/distill/campaign_distill.py \
  --bundle artifacts/<case>/inputs/campaign_bundle.json \
  --narrative artifacts/<case>/inputs/narrative.json \
  --spec artifacts/<case>/inputs/report_spec.json \
  --out artifacts/<case>/out \
  --archive verified/<case> \
  --report docs/reports/<case>.md
```

### 3. 出图

```bash
python3 tools/distill/make_figures.py \
  --out-dir artifacts/<case>/out \
  --bundle artifacts/<case>/inputs/campaign_bundle.json \
  --fig-dir artifacts/<case>/figures
```

退出码：`0` = 报告/图谱/归档校验全通过；`1` = 有违规（违规清单落
`distill_summary.json.violations`）；`2` = 输入缺失。

## 输入契约（bundle JSON）

```json
{
  "bundle_version": "1.0",
  "campaign": {"plan_id": 272, "title": "...", "wave": 2},
  "snapshot_utc": "2026-11-08T13:30:00Z",
  "nodes": [
    {"node_key": "N01", "mission_id": 7293, "repo": "repo-a", "title": "...",
     "status": "in-flight", "depends_on": [],
     "criteria": [{"id": "C1", "text": "..."}],
     "evidence": {
       "files":  [{"path": "...", "sha256": "...", "bytes": 1234, "kind": "impl"}],
       "probes": [{"name": "pytest::x", "exit_code": 0, "summary": "3 passed"}],
       "claims": [{"criterion": "C1", "metric": "...", "value": 1.2, "ok": true,
                   "grade": "REAL", "source": "path#anchor"}]
     }}
  ]
}
```

## 数据契约要点

* 判据状态四档：`PASS_CLAIMED` / `FAIL_CLAIMED` / `EVIDENCE_ONLY` / `NO_EVIDENCE`。
  **结论缺来源一律 `FAIL_CLAIMED`**（不可采信），有工件但无逐判据结论映射记 `EVIDENCE_ONLY`。
* 节点级 `VERIFIED` 要求该节点全部判据 `PASS_CLAIMED`；否则 `PARTIAL` /
  `UNVERIFIED_EVIDENCE` / `WITHHELD`——节点级结论不外推。
* 缺口五规则：`unverified_criteria` / `stale_inflight` / `failed_node` /
  `dependency_open` / `claim_without_source`；每条缺口都挂提案草案骨架
  （title/repo/physics_domain/why_frontier/goal/acceptance_criteria/dependencies/metric），
  字段值取自节点自身数据，零手抄。
* 报告硬校验：必需章节、`[E#]` 引用完整性（正文引用 ↔ 工件清单双向）、
  正文数字 = JSON 实算值、覆盖矩阵逐行存在、无占位符（TBD/TODO/???/FIXME）。
* 归档：复制前后 sha256 必须一致；同名不同内容直接拒绝覆盖；`SHA256SUMS` 可
  `sha256sum -c` 复核。

## 纪律

* **零领域语义**：本目录代码与测试不含任何物理域词条，域词表当数据
  （`config/domain_word_denylist.json`），域内容一律走 bundle/工件/`--narrative` 注入；
  扫描路径已登记在 denylist `scan_paths`（`tools/distill`），由
  `tools/audit/domain_leak_scan.py` 机器裁决。
* **阈值走 `--spec`**：图谱规模、在途超时、必需章节名都是数据，不写死在逻辑里。
* **确定性无墙钟**：`snapshot_utc` 由 bundle 提供；实体/边/缺口/覆盖行确定性排序，
  同一 bundle 连跑两次输出逐字节相同。

## 测试

```bash
python3 -m pytest tests/test_campaign_distill.py tests/test_distill_run_case.py -q   # 21 + 17 passed
```

覆盖：覆盖矩阵四档 + 「结论缺来源」/「等级越界」变异体判负、图谱规模与悬空边、
五类缺口规则齐发、报告校验五类变异体（删章节/插占位符/悬空引用/数字不符/少一行覆盖）、
归档篡改检测、CLI 端到端确定性、域词零命中。

域中性用例额外带**反空转断言**：`domain_leak_scan` 对不存在的路径是静默跳过
（只打印"扫描 N 个文件"），所以断言 `files_missing == []` 且
`len(files_scanned) == len(paths)`——路径写错/文件被删时必须变红，不许假绿。
