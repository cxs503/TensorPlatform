# Mission 1925 补报：船舶平台 ANCHORED 证据等级验证

> **补报说明**：本单为 mission 1925 的补报单。原单已成功交付但缺论文级报告。
> 原单工件目录未留档，本报告数字全部从仓库内现存台账（`docs/audit/ship_api_evidence_ledger.json`、`docs/audit/ship_api_evidence_census.md`）程序化提取，**零手抄**。
> 无原始运行日志/截图/npz 可回溯，标注"未留档"。

---

## 1. 案例介绍

### 1.1 物理/工程背景

船舶总体性能平台（TensorPlatform）对外暴露 1449 条 API 路由，其中船舶域（ship domain）57 条端点覆盖阻力、推进、耐波、操纵、稳性、结构、振动噪声、绿色航行、冰区航行等学科。平台能力台账要求每个计算端点必须有**可证伪的数值判据**（带容差的测试断言或解析基准对比），才能从 WITHHELD 升级为 ANCHORED（等价于"有真实算法实现且经过数值验证"）。

### 1.2 验证目标

Mission 1925 的任务（tp-app-1 applications 席自找活）：
- **扫描**提案池与报告局限/待办节，找到可做的验证欠账类任务
- **验证**平台 ANCHORED 判据的可靠性：43 个 ANCHORED 端点是否真的有带容差判据？14 个 WITHHELD 端点的降级理由是否成立？
- **产出**证据台账（ledger）与审计摘要（census）

### 1.3 参数/规模表

| 参数 | 值 | 来源 |
|---|---|---|
| 全平台 API 路由总数 | 1449 | ledger.totals.api_routes_all |
| 船舶域端点数 | 57 | ledger.totals.ship_domain |
| 审计工具覆盖能力数 | 21 | ledger.totals.curated_capabilities_in_audit_tool |
| ANCHORED 端点数 | 43 | ledger.grades.ANCHORED |
| WITHHELD 端点数 | 14 | ledger.grades.WITHHELD |
| MOCK_RISK 端点数 | 0 | ledger.grades.MOCK_RISK |
| 带容差判据覆盖率 | 75.44% | ledger.coverage_pct |
| 未确认 MOCK 风险 | 0 | ledger.unacknowledged_mock_risk |

---

## 2. 方法/流程

### 2.1 审计管线

采用两阶段审计：

1. **静态结构扫描**（v1 管线 `tools/capability_audit_gate`）：
   - AST 扫描每个端点的处理器实现
   - 检测空壳占比（`constant_return`、`uses_random`、`has_binop`）
   - 匹配基准工件中的 `worst_error_pct` vs 容差
   - 输出结构信号：REAL / WITHHELD / STUB / NOT_SUPPORTED

2. **证据链枚举**（`tools/audit/ship_api_evidence_census.py`）：
   - 遍历 57 个船舶域端点
   - 对每个端点查找关联的测试文件（`evidence_files`）
   - 分类外部证据等级：ANCHORED（有带容差判据的测试）/ SMOKE（仅冒烟测试）/ UNCOVERED（无测试覆盖）
   - 生成 `ship_api_evidence_census.md` 与 `ship_api_evidence_ledger.json`

### 2.2 独立复核（m1862）

对 ANCHORED 判据进行独立复核，发现 2 项需降级：
- `GET /polar/acceptance/channel`：判据 (b) hold-out 残差是混合密度比恒等式（判别力仅 2/8），且仅在单一 brash 层厚 0.8 m 上成立，厚度 hold-out 最大偏差 69.98% → 降级为 WITHHELD
- `GET /polar/acceptance/route`：仅文档覆盖、未做独立复核（判据 (e) 仍是优化器与自身基线族对比）→ 降级为 WITHHELD

### 2.3 工具链

- Python 3 + JSON 台账
- `tools/audit/ship_api_evidence_census.py`（枚举脚本）
- `tools/capability_audit_v2/cli`（v2 审计管线，含 pytest 执行 + 不变量探针）

---

## 3. 计算结果

### 3.1 证据等级分布总表

| 证据等级 | 端点数 | 占比 | 含义 |
|---|---|---|---|
| ANCHORED | 43 | 75.44% | 有带容差判据的测试/基准验证 |
| WITHHELD | 14 | 24.56% | 有计算体但证据不足，不得称 REAL |
| MOCK_RISK | 0 | 0% | 无未确认的 MOCK 风险 |

### 3.2 外部证据链分布

| 外部证据 | 端点数 | 说明 |
|---|---|---|
| ANCHORED | 43 | 有独立测试文件 + 容差判据 |
| SMOKE | 4 | 仅有冒烟测试（`/ice_optimization/health`、`/polar/acceptance/channel`、`/polar/acceptance/route`、`/ship/batch_sweep`） |
| UNCOVERED | 10 | 无任何测试覆盖 |

### 3.3 WITHHELD 端点明细

| 端点 | 处理器 | 模块 | 外部证据 | 独立复核降级 |
|---|---|---|---|---|
| `POST /ice-uq/resistance` | `calc_ice_resistance` | `ice_uq.py:90` | UNCOVERED | — |
| `GET /ice-uq/ships` | `list_available_ships` | `ice_uq.py:201` | UNCOVERED | — |
| `POST /ice-uq/uq` | `run_uq_analysis` | `ice_uq.py:117` | UNCOVERED | — |
| `POST /ice-uq/uq/all` | `run_all_ships_uq_analysis` | `ice_uq.py:158` | UNCOVERED | — |
| `GET /ice_optimization/constraints` | `get_optimization_constraints` | `ice_optimization.py:72` | UNCOVERED | — |
| `GET /ice_optimization/health` | `health_check` | `ice_optimization.py:90` | SMOKE | — |
| `POST /ice_optimization/run` | `run_ice_optimization` | `ice_optimization.py:37` | UNCOVERED | — |
| `GET /polar/acceptance/channel` | `acceptance_channel` | `polar.py:504` | SMOKE | ✅ m1862 |
| `GET /polar/acceptance/route` | `acceptance_route` | `polar.py:495` | SMOKE | ✅ m1862 |
| `GET /reports/quick/energy-consumption` | `quick_energy_report` | `report.py:159` | UNCOVERED | — |
| `POST /ship/batch_sweep` | `batch_sweep` | `ship_samples.py:114` | SMOKE | — |
| `POST /ship/benchmark_validate` | `benchmark_validate_endpoint` | `ship_samples.py:155` | UNCOVERED | — |
| `GET /ship/samples` | `list_samples` | `ship_samples.py:217` | UNCOVERED | — |
| `POST /ship/samples/bulk` | `bulk_insert_samples` | `ship_samples.py:189` | UNCOVERED | — |

### 3.4 ANCHORED 端点代表性样本（43 个中前 10 个）

| 端点 | 处理器 | 判据文件 |
|---|---|---|
| `POST /calculations/green/cii` | `calc_cii` | `tests/test_green_navigation.py` |
| `POST /calculations/hydrostatics` | `calc_hydrostatics` | `test_ship_hydrostatics.py`, `build_hydrostatics_report.py` |
| `POST /calculations/ice-load-inversion` | `ice_load_inversion` | `tests/test_ice_load_inversion.py` |
| `POST /calculations/maneuvering/mmg` | `calc_maneuvering_mmg` | `tests/test_mmg_input_schema.py` |
| `POST /calculations/noise/levels` | `api_levels` | `test_ship_vibration.py` |
| `POST /calculations/propulsion/actuator_disk` | `calc_actuator_disk` | `test_propulsion_api.py`, `verify_propulsion.py` |
| `POST /calculations/resistance` | `calc_resistance` | `test_benchmark_chain_m1388.py` 等 |
| `POST /calculations/seakeeping_rao` | `seakeeping_rao` | `test_ship_seakeeping.py` 等 |
| `POST /calculations/stability` | `calc_stability` | `test_hull_geometry_validation.py` 等 |
| `POST /polar/resistance/level-ice` | `level_ice` | `test_polar_api.py`, `test_polar_physics.py` |

---

## 4. 结果图

**本单无图**。理由：
- 原 mission 1925 工件目录未留档（无 npz/png 可回溯）
- 本单为补报单，仅从现存台账程序化提取数字，未执行新的计算/可视化
- 审计结果为分类统计表，非场量/曲线类数据，matplotlib 图不增加信息量

---

## 5. 注意点

### 5.1 关键发现

1. **覆盖率 75.44% 尚可但有缺口**：14 个 WITHHELD 端点中，10 个完全无测试覆盖（UNCOVERED），4 个仅有冒烟测试。冰区 UQ（`/ice-uq/*`）和冰优化（`/ice_optimization/*`）两个模块共 7 个端点全部 WITHHELD，是最大欠账区。

2. **独立复核有效拦截**：m1862 独立复核将 2 个 `polar/acceptance` 端点从 ANCHORED 降级为 WITHHELD，理由充分：
   - `channel`：hold-out 残差是混合密度比恒等式（判别力仅 2/8），且仅在单一工况（brash 层厚 0.8 m）成立，厚度 hold-out 偏差 69.98%
   - `route`：仅文档覆盖，判据是优化器与自身基线族对比（循环论证）

3. **零未确认 MOCK 风险**：`unacknowledged_mock_risk = []`，说明所有疑似 MOCK 的端点都已被显式标记或降级，无遗漏。

### 5.2 局限

- **原单工件缺失**：无法回溯原始运行日志、pytest 输出、探针数值。本报告数字全部来自仓库内现存台账文件，假设台账未被后续任务篡改。
- **未执行 v2 管线**：本补报单未重跑 `tools/capability_audit_v2/cli`，无法验证当前代码是否仍满足 ANCHORED 判据。
- **21 能力 vs 57 端点**：审计工具（`audit_capabilities`）覆盖 21 个高层能力，与 57 个端点不是一一映射。本报告聚焦端点级证据。

### 5.3 适用范围

- 本审计仅覆盖船舶域（ship domain）57 个端点
- 不覆盖平台其他域（如通用计算、认证、报告等）
- ANCHORED 判据的时效性取决于代码是否被后续修改——台账是快照，非实时监控

---

## 6. 误差判定

### 6.1 关键量 vs 基准

| 关键量 | 本单值 | 基准/预期 | 误差 | 是否 <3% |
|---|---|---|---|---|
| ANCHORED 端点数 | 43 | 台账记录 43 | 0% | ✅ 一致（无基准偏差） |
| WITHHELD 端点数 | 14 | 台账记录 14 | 0% | ✅ 一致 |
| 覆盖率 | 75.44% | 43/57 = 75.44% | 0% | ✅ 算术自洽 |
| MOCK_RISK | 0 | 台账记录 0 | 0% | ✅ 一致 |

### 6.2 无基准声明

- **原单运行数据**：未留档。无法对比原单执行时的 pytest 通过率、探针残差等运行时数字。
- **v2 管线复跑结果**：未执行。无法确认当前代码是否仍满足 ANCHORED 判据。
- **原因**：本单为补报单，任务定义为"从现存台账程序化提取数字"，不涉及重新执行审计管线。

### 6.3 结论

本补报单的数字与仓库内现存台账**完全一致**（零偏差），算术自洽。但由于原单工件缺失且未重跑审计管线，**无法证明当前代码仍满足 ANCHORED 判据**——这是诚实的局限声明，不是误差。

---

## 附录：数据来源

| 文件 | 用途 |
|---|---|
| `docs/audit/ship_api_evidence_ledger.json` | 57 端点完整台账（grade/evidence/override） |
| `docs/audit/ship_api_evidence_census.md` | 人类可读审计摘要 |
| `docs/CAPABILITY_AUDIT_V2.md` | v2 审计管线方法学文档 |

---

*报告生成时间：补报单 m18690 执行期间*
*原单：mission 1925（TensorPlatform，tp-app-1 applications 席）*
*补报原因：原单成功交付但缺论文级报告*
