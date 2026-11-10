# P162-N10 · 结构面迁移账本（程序化生成）

> 由 `tools/audit/m9079_artifacts.py` 从 `config/domain_surface_manifest.json` 生成，禁止手改；改清单请走 `domain_surface_manifest.py generate|confirm-removal|bless`（带 owner 审批）。

模块总数 **38**；依赖豁免 **41** 对 / **45** 处 import 点（到期 2027-03-31）。

## 1. 模块归属（谁该接管、迁到哪、什么时候必须迁完）

| # | 模块 | owner_repo | target_layer | status | expiry | migration_ref |
|---|---|---|---|---|---|---|
| 1 | `__init__.py` | TensorPlatform | `backend/api/core` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 2 | `benchmark_audit.py` | ship-performance-platform | `domains/ship_performance` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 3 | `coupling_clock_sync.py` | TensorPhysics | `domains/physics_providers` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 4 | `data.py` | TensorPlatform | `backend/api/core` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 5 | `delivery_state.py` | TensorPlatform | `backend/api/core` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 6 | `deployment.py` | TensorPlatform | `backend/api/core` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 7 | `holtrop_mennen.py` | ship-performance-platform | `domains/ship_performance` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 8 | `hull_geometry.py` | ship-performance-platform | `domains/ship_performance` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 9 | `iam.py` | TensorPlatform | `backend/api/core` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 10 | `ice_uq.py` | TensorPhysics | `domains/physics_providers` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 11 | `iot_edge.py` | TensorPlatform | `backend/api/core` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 12 | `manoeuvring.py` | ship-performance-platform | `domains/ship_performance` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 13 | `misc.py` | TensorPlatform | `backend/api/core` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 14 | `mission_state_machine.py` | TensorPlatform | `backend/api/core` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 15 | `model.py` | TensorPlatform | `backend/api/core` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 16 | `mvd_checkpoint.py` | TensorPlatform | `backend/api/core` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 17 | `observability.py` | TensorPlatform | `backend/api/core` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 18 | `ops.py` | TensorPlatform | `backend/api/core` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 19 | `physics_providers.py` | TensorPhysics | `domains/physics_providers` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 20 | `platform.py` | TensorPlatform | `backend/api/core` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 21 | `polar.py` | ship-performance-platform | `domains/ship_performance` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 22 | `propeller_open_water.py` | ship-performance-platform | `domains/ship_performance` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 23 | `resistance_core.py` | ship-performance-platform | `domains/ship_performance` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 24 | `resistance_core_service.py` | ship-performance-platform | `domains/ship_performance` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 25 | `resistance_holtrop.py` | ship-performance-platform | `domains/ship_performance` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 26 | `resistance_model.py` | ship-performance-platform | `domains/ship_performance` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 27 | `ship_batch.py` | ship-performance-platform | `domains/ship_performance` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 28 | `ship_benchmark.py` | ship-performance-platform | `domains/ship_performance` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 29 | `ship_green.py` | ship-performance-platform | `domains/ship_performance` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 30 | `ship_icing.py` | ship-performance-platform | `domains/ship_performance` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 31 | `ship_resistance.py` | ship-performance-platform | `domains/ship_performance` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 32 | `ship_samples.py` | ship-performance-platform | `domains/ship_performance` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 33 | `ship_seakeeping.py` | ship-performance-platform | `domains/ship_performance` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 34 | `ship_vibration.py` | ship-performance-platform | `domains/ship_performance` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 35 | `status_contracts.py` | TensorPlatform | `backend/api/core` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 36 | `tensor.py` | TensorPlatform | `backend/api/core` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 37 | `tensor_common.py` | TensorPlatform | `backend/api/core` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |
| 38 | `tools.py` | TensorPlatform | `backend/api/core` | DECLARED-exempt | 2027-03-31 | P162W1-N10 |

未迁移理由（同一政策口径，按 owner 分组的代表理由）：

- **TensorPlatform**：平台机制（会话/任务/交付/台账/运维面），不属于任何物理域；应沉到 core 层并脱离 api.domain 包。
- **ship-performance-platform**：船舶总体性能域实现（阻力/推进/耐波/操纵/稳性/结构/振动噪声/绿色航行/冰区），按仓地图归 ship-performance-platform。
- **TensorPhysics**：求解器耦合/冰区不确定度等科研流程语义，按 ECOSYSTEM-MAP 归 TensorPhysics。

## 2. 依赖方向豁免账（核心/路由/工具层 → api.domain）

| # | 文件 | 模块 | 形态 | sites | 到期 |
|---|---|---|---|---|---|
| 1 | `backend/api/tensor_approvals.py` | `api.domain.tensor_common` | from | 2 | 2027-03-31 |
| 2 | `backend/api/tensor_missions.py` | `api.domain.mvd_checkpoint` | from | 2 | 2027-03-31 |
| 3 | `tools/p92w3_n10/adjudicate.py` | `api.domain.delivery_state` | from | 2 | 2027-03-31 |
| 4 | `tools/verif/geometry/std_geometry_gate.py` | `api.domain.ship_resistance` | from | 2 | 2027-03-31 |
| 5 | `backend/api/auth.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 6 | `backend/api/breakthrough_problems.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 7 | `backend/api/calculations.py` | `api.domain.ship_resistance` | from | 1 | 2027-03-31 |
| 8 | `backend/api/campaign_plans.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 9 | `backend/api/campaign_routing.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 10 | `backend/api/factory_residents.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 11 | `backend/api/factory_strategy.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 12 | `backend/api/kpi_delivery.py` | `api.domain.delivery_state` | from | 1 | 2027-03-31 |
| 13 | `backend/api/outcome_evaluations.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 14 | `backend/api/soul_runtime.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 15 | `backend/api/tensor_activity.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 16 | `backend/api/tensor_capability.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 17 | `backend/api/tensor_centers.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 18 | `backend/api/tensor_executions.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 19 | `backend/api/tensor_llm.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 20 | `backend/api/tensor_mission_lifecycle.py` | `api.domain.mission_state_machine` | from | 1 | 2027-03-31 |
| 21 | `backend/api/tensor_mission_lifecycle.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 22 | `backend/api/tensor_missions.py` | `api.domain.status_contracts` | from | 1 | 2027-03-31 |
| 23 | `backend/api/tensor_missions.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 24 | `backend/api/tensor_reconcile.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 25 | `backend/api/tensor_reports.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 26 | `backend/api/tensor_repro.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 27 | `backend/api/tensor_scenarios.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 28 | `backend/api/tensor_sessions.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 29 | `backend/api/tensor_system.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 30 | `backend/api/tensorphysics_runs.py` | `api.domain.status_contracts` | from | 1 | 2027-03-31 |
| 31 | `backend/api/users.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 32 | `scripts/audit_benchmark_chain_m1388.py` | `api.domain.ship_benchmark` | from | 1 | 2027-03-31 |
| 33 | `scripts/audit_benchmark_chain_m1388.py` | `api.domain.ship_resistance` | from | 1 | 2027-03-31 |
| 34 | `scripts/generate_m1772_figures.py` | `api.domain.ship_resistance` | from | 1 | 2027-03-31 |
| 35 | `tools/benchmark_audit_report.py` | `api.domain` | from | 1 | 2027-03-31 |
| 36 | `tools/contracts/vocab_contract_audit.py` | `api.domain.tensor_common` | from | 1 | 2027-03-31 |
| 37 | `tools/p92w3_n10/domain_scan.py` | `api.domain.delivery_state` | from | 1 | 2027-03-31 |
| 38 | `tools/p92w3_n10/evaluate.py` | `api.domain.delivery_state` | from | 1 | 2027-03-31 |
| 39 | `tools/p92w3_n10/shadow_golden.py` | `api.domain` | from | 1 | 2027-03-31 |
| 40 | `tools/p92w3_n10/shadow_golden.py` | `api.domain.delivery_state` | from | 1 | 2027-03-31 |
| 41 | `tools/verify/resistance_benchmark_audit.py` | `api.domain.holtrop_mennen` | from | 1 | 2027-03-31 |

## 3. 迁移动作（怎么把上表清零）

1. 域模块从 `backend/api/domain/` 迁到归属仓 / `domains/<domain>/`，路由层用 `provide_domain('<name>')` 解析（`backend/api/core/domain_provider.py`）。
2. 迁移后在目标仓引用新路径，本仓 `confirm-removal --approval-by owner --approval-ref <单号>` → 基线 −1。
3. 依赖豁免随之消失（`generate` 重签），`dependency_census.json` 的 `sites/pairs` 只降不升。
4. 到期（2027-03-31）未完成 → `check --today` 判 `exemption_expired` 红，不允许静默续期。

