# P491-W2-N01 验证面活性巡检与能力台账证据驱动升账

- 战役节点：`p491w2-n01-ledger-liveness-maturity`（mission `13928`）
- 代码：`src/verification_vitality/`（10 个模块）；测试：`tests/verification_vitality/`（56 个测试函数，参数化后共 63 用例）
- 工件：`artifacts/verification_vitality/`（巡检报告、交叉建议、事故重放、负控、升账决策、判据 JSON）
- 生成：本文件由 `scripts/p491w2_n01_render_doc.py` 从工件渲染，文中数字无手抄
- 巡检基线 commit：`0df62547fb18e8c2b79b51417932c1873bc4216a`

## 1 案例介绍：台账说 REAL，验证面可能早就死了

平台用能力台账（`capabilities[]` + `evidence[]`）登记"这条能力被证据支持到什么等级"。
台账的致命盲区不在判据写得松，而在**判据所依赖的验证面本身可能已经静默失效**：

- 测试文件还在、CI 还是绿的，但 `pytest` 根本没把它们收集起来（导入失败）；
- 符号复盘/模块改名后，`from pkg.old import X` 已经在运行时抛 `ImportError`，
  而汇总行只显示"0 failed"；
- `--continue-on-collection-errors` 之类的宽容开关让收集错误不致命，绿得更彻底。

本仓历史里就有现成的事故：`tests/capability_ledger_auto/` 的 5 个测试文件曾经**全部**
是 collection error（src-layout 下按顶层包名导入），53 个用例一个都没跑过，CI 因宽容
开关静默放过（见 `docs/capability_ledger_auto/N02_capability_ledger_auto.md`）。
修复提交是 `3f05b56b60da`（Merge #1048 (m9750) via merge-master），父提交
`6ca5872ca9cd` 即事故状态——这就是本节点的天然负控。

本节点要建的是机制而非一次性检查：**三态活性巡检**（alive / dead / unmapped）×「台账
交叉器」×「证据驱动升账引擎（带 rollback guard）」，全部机器可裁、可复现、可回滚。

## 2 怎么做的

### 2.1 三态巡检：静态解析 + 真实收集，缺一不可

| 层 | 做什么 | 能抓到什么 | 抓不到什么 |
|---|---|---|---|
| 静态（`imports.py`，AST） | 把每个测试模块的 `import/from-import` 解析到仓库内文件，并检查**符号是否存在**（含 `__all__` 再导出契约、子模块导入、`from pkg import submod`） | 模块改名、符号改名、再导出被删 | conftest 缺路径、数据文件缺失、插件未注册 |
| 动态（`collector.py`） | 真跑一次 `pytest --collect-only --continue-on-collection-errors`，解析**全幅** `ERROR <file> - <reason>` 与每个文件的用例数 | conftest/夹具/数据缺失、导入失败、session 被 skip 掐掉 | pytest 没被执行时的语义 |
| 合成（`patrol.py`） | 三态：`dead`＝收集失败或非可选导入不可解析；`unmapped`＝活性无碍但无任何已登记能力认领；`alive`＝其余 | 登记边缺失（验证面孤岛） | — |

零领域语义：能力↔验证面的对应关系全部来自 `config/capabilities.json` 的
`test_patterns` 正则，包内不出现任何域词条。

误报控制（每一条都对应一个真实踩过的坑）：

- `from pkg import submod` 是**子模块**导入，不是包内符号——不判这一条会把真仓库的
  `from capability_ledger_auto import ac_verify` 误判 dead（本节点第一版就犯了）；
- `try: ... except ImportError` 的可选依赖、`if TYPE_CHECKING:` 的类型导入不判死；
- 外部依赖（`numpy` 等）不判——否则装没装包会污染活性结论；
- 星导入模块跳过符号检查（静态不可知，宁漏不误）。

### 2.2 收集期退化必须被识别

真仓库里一个 conftest 的模块级 `pytest.skip("...")` 会让**整次收集**以 exit≠0、
零错误、零用例收场。若巡检只看 exit code 或只看错误行，就会得到"全绿空转"。
`collector.is_degenerate()` 识别这种退化，`run_pytest_collect_chunked()` 自动按目录
分块重跑并合并（零领域语义的故障隔离），落不进块的目录记 `session_aborted`。

### 2.3 台账交叉器：dead + REAL ⇒ fail-closed

台账的"当前可声称等级"取**台账自称与反证裁决里更保守的那个**：一条独立审计说 MOCK，
就不该继续按 REAL 记账（`ledger_cross.effective_level`）。交叉结果四态：

- `FAIL_CLOSED`：台账 REAL 且被认领的验证面 dead ⇒ 输出 `DOWNGRADE`（REAL→WITHHELD）建议；
- `DOWNGRADE_RECOMMENDED`：非 REAL 但验证面已死 ⇒ 建议同步下修；
- `REVIEW_NO_SURFACE`：台账 REAL 但**没有任何验证面认领**（边缺失，不是死面）；
- `OK`：其余。

交叉器**只出建议不改台账**：fail-closed 的语义是"不许继续声称"，落地执行必须留痕可回滚。

另外两路增量流水交叉：delta 显式带 `capability_id`（硬关联）、或 delta 的 `artifacts`
里列出的测试文件正好落在 dead 面上（更硬的证据——"你宣称新增的验证面现在收集不起来"）。

### 2.4 升账引擎：三条件齐才升，且可回滚

| 判据 | 阈值 | 证据来源 |
|---|---|---|
| 执行通过率 | ≥ 0.90 | pytest 真跑的 `passed/(passed+failed)`；**收集期错误不计入分母，静态推断不算证据** |
| 基准误差 | < 15%（严格小于） | 台账 `evidence[].benchmark.error_pct`，或由 `benchmark_error_sources.json` 从既有基准工件抽取（取最差工况） |
| 验证面活性 | alive | 才 2.1 的巡检结论（dead/unmapped 一律不升） |

**接口对齐说明（诚实）**：节点任务书要求"与既有 FEAT-LEDGER-GATE 接口对齐"，但该名字的
门在 TensorPlatform 仓内**不存在**（全仓 grep `FEAT-LEDGER` 0 命中）。因此本节点对齐的是
仓内两个真实存在的接口：`config/capabilities.json` 的等级枚举
（`NOT_SUPPORTED/STUB/MOCK/WITHHELD/REAL` + 单调 rank）与
`backend/services/capability_governance/grading.py` 的证据链三元
（`verdict` / `decided_level` / `chain_digest`）；决策记录同时带这三项，故任何按此三字段
消费台账的门都可以直接吃本引擎的产出。

Rollback guard：升账写 append-only journal（`level_before / evidence_digest /
rollback_token`）；`rollback` 需 token 匹配，期间证据变了返回 `ROLLBACK_CONFLICT` 并拒绝；
同一 `(capability, evidence_digest)` 重复应用幂等（不产生第二条流水、不重复计数）。

## 3 计算结果（实测）

### 3.1 全仓巡检（TensorPlatform，730 个验证面）

| 量 | 值 |
|---|---|
| 验证面单位（tests/**/*.py，除 conftest/__init__） | 730 |
| alive / dead / unmapped | 57 / 76 / 597 |
| dead 比例 | 10.41% |
| pytest 收集用例总数 | 12198 |
| 收集期错误条目 | 44 |
| 已登记能力（注册表） | 21 |
| 能力 alive / dead / unmapped | 10 / 3 / 8 |
| 单仓巡检耗时 | 95.14 s |

dead 面成因分解（同一模块可同时命中多因）：

| 成因 | 模块数 |
|---|---|
| collect:import_error | 35 |
| static:symbol_missing | 27 |
| static:module_missing | 8 |
| collect:collection_error | 6 |
| collect:data_file_missing | 3 |

### 3.2 台账交叉：3 条 REAL 能力被 fail-closed

| 能力 | 台账等级（有效） | 巡检状态 | 建议 |
|---|---|---|---|
| dev.gate_5415 | REAL | dead | FAIL_CLOSED（6 个 dead 面） |
| geometry.generate | REAL | dead | FAIL_CLOSED（1 个 dead 面） |
| gov.stats | REAL | dead | FAIL_CLOSED（5 个 dead 面） |

另有 7 条 REAL 能力在注册表里没有任何验证面认领（边缺失，需补映射）：

dev.git、dev.logs、dev.nginx_audit、dev.restart、dev.services、geometry.verify_official、shiphull.generate

交叉汇总：{"n_capabilities": 21, "n_deltas_exposed": 0, "n_downgrade_recommended": 0, "n_fail_closed": 3, "n_ok": 11, "n_review_no_surface": 7, "n_suggestions": 10}

### 3.3 升账决策（真实数据，未注入任何度量）

| 能力 | 当前等级 | 决策 | 原因 | 通过率 | 基准误差% | 验证面 |
|---|---|---|---|---|---|---|
| ai.predict | NOT_SUPPORTED | HOLD | pass_rate_below_bar, benchmark_error_pct_missing | 0.8571 | None | alive |
| calc.resistance | MOCK | HOLD | benchmark_error_pct_below_bar | 1.0 | 68.89 | alive |
| calc.stability | MOCK | HOLD | pass_rate_missing, benchmark_error_pct_missing | None | None | alive |
| dev.gate_5415 | REAL | HOLD | already_real, surface_alive_below_bar | None | None | dead |
| dev.git | REAL | HOLD | already_real, surface_alive_below_bar | None | None | unmapped |
| dev.logs | REAL | HOLD | already_real, surface_alive_below_bar | None | None | unmapped |
| dev.nginx_audit | REAL | HOLD | already_real, surface_alive_below_bar | None | None | unmapped |
| dev.probe | REAL | NOOP | already_real | 1.0 | None | alive |
| dev.restart | REAL | HOLD | already_real, surface_alive_below_bar | None | None | unmapped |
| dev.services | REAL | HOLD | already_real, surface_alive_below_bar | None | None | unmapped |
| geometry.generate | REAL | HOLD | already_real, surface_alive_below_bar | None | None | dead |
| geometry.hydro_curves | REAL | NOOP | already_real | None | None | alive |
| geometry.list_models | REAL | NOOP | already_real | 1.0 | None | alive |
| geometry.verify_official | REAL | HOLD | already_real, surface_alive_below_bar | None | None | unmapped |
| gov.stats | REAL | HOLD | already_real, surface_alive_below_bar | None | None | dead |
| knowledge.docs | REAL | NOOP | already_real | None | None | alive |
| methods.computation | NOT_SUPPORTED | HOLD | pass_rate_missing, benchmark_error_pct_missing | None | None | alive |
| misc.stubs | STUB | HOLD | pass_rate_missing, benchmark_error_pct_missing, surface_alive_below_bar | None | None | unmapped |
| propeller.open_water | MOCK | HOLD | pass_rate_missing, benchmark_error_pct_missing | None | None | alive |
| shiphull.generate | REAL | HOLD | already_real, surface_alive_below_bar | None | None | unmapped |
| stdmodels.lookup | REAL | NOOP | already_real | 1.0 | None | alive |

- 决策分布：{"HOLD": 16, "NOOP": 5, "UPGRADE": 0}
- 实测通过率来源（真跑 pytest）：{"ai.predict": 0.8571, "calc.resistance": 1.0, "dev.probe": 1.0, "geometry.generate": null, "geometry.list_models": 1.0, "stdmodels.lookup": 1.0}
- 基准误差来源：{"calc.resistance": {"artifact": "artifacts/benchmark_validation.json", "n": 12, "reduce": "max", "value": 68.89}}

**真实数据上 0 条升账**，原因是可解释的：唯一有机器可读基准误差的能力
（`calc.resistance`）最差工况误差 68.89%
远超 15% 门 ⇒ 正确拒绝；其余能力台账里根本没有基准误差字段 ⇒ `*_missing` 而 HOLD。
这正是引擎该有的行为（宁可 HOLD，不许无证据自升），同时也暴露了台账的 schema 缺口。

### 3.4 闭环落地演示（合成度量，显式标注）

真实台账缺基准误差，故另用**同一套引擎 + 真实台账 schema + 注入的合成度量**跑完整链：
升账 → journal → 幂等复跑 → 回滚 → 回滚冲突探针。

```json
{
 "capability_id": "calc.resistance",
 "injected_metrics": {
  "calc.resistance": {
   "benchmark_error_pct": 8.0,
   "pass_rate": 0.95
  }
 },
 "label": "SYNTHETIC METRICS injected into the real ledger schema — 仅证明链路可落地，不计入真实升账",
 "idempotent_second_apply": {
  "applied": [],
  "dry_run": false,
  "n_applied": 0,
  "n_noop": 1,
  "noop": [
   "calc.resistance"
  ]
 },
 "rollback": {
  "capability_id": "calc.resistance",
  "event": {
   "at": "2026-11-11T06:56:16.664354+00:00",
   "capability_id": "calc.resistance",
   "event": "rollback",
   "evidence_digest": "42234f70270e87ac20c4904fc0b0f865e0d76c8cd3d30d28055731a898fb56e0",
   "level_after": "MOCK",
   "level_before": "REAL"
  },
  "status": "ROLLED_BACK",
  "to": "MOCK"
 },
 "passed": true
}
```

### 3.5 事故重放与合成负控

| 重放对象 | dead | exit | 用例数 |
|---|---|---|---|
| 修复前 6ca5872ca9cd | 5 | 1 | 5 |
| 修复后 3f05b56b60da | 0 | 0 | 6 |

修复前 5 个模块全部 `import_error`（`ModuleNotFoundError: No module named
'capability_ledger_auto.registry'`），修复后 0 dead。

| 合成案例 | 检出 | 静态判定 | 收集错误类 | 退出码 |
|---|---|---|---|---|
| module_rename | True | module_missing | import_error | 1 |
| symbol_rename | True | symbol_missing | import_error | 1 |
| reexport_removed | True | symbol_missing | import_error | 1 |

## 4 结果图

![fig1_vitality_three_state](../artifacts/verification_vitality/fig1_vitality_three_state.png)

*Figure 1 三态巡检：左=验证面单位（测试模块），右=已登记能力*

![fig2_dead_cause_breakdown](../artifacts/verification_vitality/fig2_dead_cause_breakdown.png)

*Figure 2 dead 验证面成因分解（收集期 vs 静态导入）*

![fig3_incident_replay](../artifacts/verification_vitality/fig3_incident_replay.png)

*Figure 3 git 历史事故重放：修复前 dead、修复后 alive*

![fig4_negative_control](../artifacts/verification_vitality/fig4_negative_control.png)

*Figure 4 三种符号迁移的合成负控检出结果*

![fig5_upgrade_decisions](../artifacts/verification_vitality/fig5_upgrade_decisions.png)

*Figure 5 真实台账上的升账决策分布（三条件齐才升）*

## 5 注意点与局限

1. **`unmapped` 不是"坏"**：它标记"这条验证面没有被任何已登记能力认领"，是登记边的
   缺口，不是代码缺陷。本仓该比例高达 597/730，本身就是一条结论：
   能力注册表的 `test_patterns` 覆盖面远小于测试面。
2. **静态检查有天然上限**：动态 `importlib`、星导入、运行期拼装的名字无法静态解析，
   一律按"不判"处理（宁可漏报不误报）；这类失效只能靠动态收集兜。
3. **收集期错误 ≠ 全部失效**：`pytest --collect-only` 看不到夹具解析失败（那是
   setup 期）。要覆盖 setup 期需真跑用例（成本高一个量级），本节点只做收集期。
4. **巡检不复刻 CI 的 PYTHONPATH 兜底**：巡检主动丢弃父进程 `PYTHONPATH`、只给仓库根，
   否则会替被试仓库把 `src/` 塞进导入路径、把它自己的 import 缺陷治好（第一版正是因此
   事故重放全绿，属于"工具治好了病灶"的假阴性）。
5. **真实数据 0 升账**：这是引擎的设计行为而非失败，但也说明"自动升账闭环"在本平台
   目前**没有可升的对象**——瓶颈是台账缺机器可读的基准误差字段，不是引擎逻辑。
6. **交叉器只出建议**：不自动改台账/注册表（改台账是不可逆动作，须走审查与留痕）。
7. 巡检范围仅本仓 `tests/`；跨仓（TensorLBM/FEM/...）的验证面不在本节点覆盖内，
   增量流水里跨仓 delta 因而不可能被判 EXPOSED（本节点实测 0 条）。

## 6 误差判定（判据 vs 基准）

本节点是平台机制节点，没有物理场量，故**不存在"计算值 vs 实验值"误差**；相应地，
我按机器判据逐项对表（判据即基准），并显式回答"是否 <3%"：

| 判据 | 实测 | 要求 | 结论 |
|---|---|---|---|
| (a) 事故重放 | 修复前 dead=5 / exit=1；修复后 dead=0 / exit=0 | 修复前 dead>0 且 exit≠0，修复后 dead=0 且 exit=0 | PASS |
| (b) 合成负控 | 3/3 检出，检出率 1.0 | 3 种符号迁移 3/3 | PASS |
| (c) 卫生（健康仓误报） | 合成健康仓 dead=0 | 0 误报 | PASS |
| (d) ≥30 已登记面三态分类 | 已登记（被能力认领）验证面 69 个，全部面 730 个；能力条目 21 条 | ≥30 且三态加和等于总数 | PASS |
| (e) 单仓巡检预算 | 95.14 s | ≤120 s | PASS |
| (f) 升账/交叉接口对齐 | 决策 {'HOLD': 16, 'NOOP': 5, 'UPGRADE': 0}；降级建议 10 条；fail-closed 3 条 | 与既有等级枚举/证据链三元同构，输出降级建议 JSON | PASS |

- 阈值判据是**离散判定**（dead/alive、detected/not、≤120 s），不存在百分比误差；
  唯一连续量是巡检耗时 95.14 s vs 门 120 s，
  相对余量 20.7%。
- 升账阈值（通过率 0.90、基准误差 15%）与生态既有精度纪律（误差 >15% 不可接受）一致；
  引擎对 `calc.resistance` 的最差工况误差 68.89%
  给出 HOLD，与"N02 台账裁决 MOCK"方向一致，无粉饰。

## 7 复现

```bash
PYTHONPATH=src python3 -m pytest tests/verification_vitality -q          # 56 个测试函数
PYTHONPATH=src python3 scripts/p491w2_n01_vitality_artifacts.py          # 全部工件（约 221.1 s）
PYTHONPATH=src python3 scripts/p491w2_n01_render_doc.py                  # 重渲本文档
PYTHONPATH=src python3 -m verification_vitality patrol --repo . --out /tmp/patrol.json
PYTHONPATH=src python3 -m verification_vitality cross --report /tmp/patrol.json
```

巡检/交叉/升账也可作为 CLI 独立跑：`python -m verification_vitality {{patrol,cross,upgrade,downgrade,rollback}}`，
默认 dry-run，只有 `--apply` 才写本引擎自己的 state/journal（从不改台账文件）。
