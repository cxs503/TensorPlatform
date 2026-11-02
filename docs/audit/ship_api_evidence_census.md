# 船舶总体性能 API 面证据普查（机器生成）

> 由 `tools/audit/ship_api_evidence_census.py` 生成，勿手改。数字全部来自 AST 扫描，零手抄。

- 全 API 路由：**1427**；其中船舶总体性能域：**44**
- `audit_capabilities` 手工登记能力：**21** → 覆盖本域端点的 **47.73%**
- 证据等级：ANCHORED **30** / WITHHELD **14** / MOCK_RISK **0**
- 外部证据链：ANCHORED **30** / SMOKE **2** / UNCOVERED **12**
- 带容差判据覆盖率：**68.18%**

## MOCK 风险端点（常量返回或随机数 + 无带容差判据）

| 端点 | 处理器 | 模块 | 常量返回 | 随机 | 申报 |
|---|---|---|---|---|---|

> 全部 MOCK 风险端点均已在 `docs/audit/ship_api_mock_allowlist.json` 申报。

## WITHHELD 端点（有计算体但证据不足，不得称 REAL）

| 端点 | 处理器 | 模块 | 外部证据 |
|---|---|---|---|
| `POST /ice-uq/resistance` | `calc_ice_resistance` | `backend/api/domain/ice_uq.py:90` | UNCOVERED |
| `GET /ice-uq/ships` | `list_available_ships` | `backend/api/domain/ice_uq.py:201` | UNCOVERED |
| `POST /ice-uq/uq` | `run_uq_analysis` | `backend/api/domain/ice_uq.py:117` | UNCOVERED |
| `POST /ice-uq/uq/all` | `run_all_ships_uq_analysis` | `backend/api/domain/ice_uq.py:158` | UNCOVERED |
| `GET /ice_optimization/constraints` | `get_optimization_constraints` | `backend/api/ice_optimization.py:72` | UNCOVERED |
| `GET /ice_optimization/health` | `health_check` | `backend/api/ice_optimization.py:90` | SMOKE |
| `POST /ice_optimization/run` | `run_ice_optimization` | `backend/api/ice_optimization.py:37` | UNCOVERED |
| `GET /polar/acceptance/channel` | `acceptance_channel` | `backend/api/domain/polar.py:504` | UNCOVERED |
| `GET /polar/acceptance/route` | `acceptance_route` | `backend/api/domain/polar.py:495` | UNCOVERED |
| `GET /reports/quick/energy-consumption` | `quick_energy_report` | `backend/api/report.py:159` | UNCOVERED |
| `POST /ship/batch_sweep` | `batch_sweep` | `backend/api/domain/ship_samples.py:114` | SMOKE |
| `POST /ship/benchmark_validate` | `benchmark_validate_endpoint` | `backend/api/domain/ship_samples.py:155` | UNCOVERED |
| `GET /ship/samples` | `list_samples` | `backend/api/domain/ship_samples.py:217` | UNCOVERED |
| `POST /ship/samples/bulk` | `bulk_insert_samples` | `backend/api/domain/ship_samples.py:189` | UNCOVERED |

## ANCHORED 端点（有带容差判据）

| 端点 | 处理器 | 判据文件 |
|---|---|---|
| `POST /calculations/hydrostatics` | `calc_hydrostatics` | backend/tests/test_ship_hydrostatics.py, tools/build_hydrostatics_report.py |
| `POST /calculations/ice-load-inversion` | `ice_load_inversion` | tests/test_ice_load_inversion.py |
| `GET /calculations/ice-load-inversion/info` | `ice_load_inversion_info` | tests/test_ice_load_inversion.py |
| `POST /calculations/propulsion/actuator_disk` | `calc_actuator_disk` | backend/tests/test_propulsion_api.py, tools/verify_propulsion.py |
| `POST /calculations/propulsion/self_propulsion` | `calc_self_propulsion` | backend/tests/test_propulsion_api.py, tools/verify_propulsion.py |
| `POST /calculations/resistance` | `calc_resistance` | backend/tests/feat/test_benchmark_chain_m1388.py, backend/tests/test_benchmark_audit_endpoint.py, backend/tests/test_propulsion.py |
| `POST /calculations/seakeeping_rao` | `seakeeping_rao` | backend/tests/feat/test_ship_seakeeping.py |
| `POST /calculations/seakeeping_response` | `seakeeping_response` | backend/tests/feat/test_ship_seakeeping.py |
| `POST /calculations/seakeeping_spectrum` | `seakeeping_spectrum` | backend/tests/feat/test_ship_seakeeping.py |
| `POST /calculations/stability` | `calc_stability` | backend/tests/test_propulsion_api.py, tests/test_m1386_stability_gz_validation.py, tests/test_m1564_stability_canon.py |
| `GET /polar/acceptance` | `acceptance` | tests/physics/test_marine_icing.py, tests/test_clock_sync.py, tests/test_ice_load_inversion.py |
| `GET /polar/capabilities` | `capabilities` | backend/tests/polar/test_polar_api.py, tools/audit/ship_api_evidence_census.py, tools/benchmarks/propulsion_anchors.py |
| `POST /polar/cii` | `cii` | backend/tests/polar/test_polar_api.py, backend/tests/polar/test_polar_physics.py, tests/e2e1_b1/build_cases.py |
| `POST /polar/convoy/curve` | `convoy_curve` | tools/audit/ship_api_evidence_census.py |
| `POST /polar/convoy/spacing` | `convoy_spacing` | backend/tests/polar/test_polar_api.py |
| `GET /polar/ice-field/scenario` | `ice_field_scenario` | backend/tests/polar/test_polar_api.py |
| `POST /polar/ice-field/summary` | `ice_field_summary` | backend/tests/polar/test_polar_api.py, backend/tests/polar/test_polar_physics.py, tools/polar/render_report.py |
| `POST /polar/resistance/channel` | `channel` | backend/tests/polar/test_polar_api.py, backend/tests/polar/test_polar_physics.py, backend/tests/test_digital_twin_estimator_predictor.py |
| `POST /polar/resistance/level-ice` | `level_ice` | backend/tests/polar/test_polar_api.py, backend/tests/polar/test_polar_physics.py, tools/polar/run_acceptance.py |
| `POST /polar/resistance/level-ice/grid` | `level_ice_grid` | backend/tests/polar/test_polar_api.py |
| `POST /polar/route/optimize` | `route_optimize` | backend/tests/polar/test_polar_api.py |
| `GET /polar/ships` | `ships` | backend/tests/polar/test_polar_api.py, backend/tests/polar/test_polar_physics.py, backend/tests/test_digital_twin_physics.py |
| `POST /polar/ships/validate` | `ships_validate` | backend/tests/polar/test_polar_api.py |
| `POST /polar/voyage/optimize` | `voyage_optimize` | backend/tests/polar/test_polar_api.py |
| `POST /ship/icing/accumulate` | `accumulate` | tools/m796_report_gen.py |
| `POST /ship/icing/benchmark` | `benchmark` | backend/tests/feat/test_benchmark_chain_m1388.py, backend/tests/test_benchmark_audit_endpoint.py, backend/tests/test_benchmark_reference_integrity.py |
| `GET /ship/icing/capability` | `capability` | tests/test_m1386_stability_gz_validation.py, tests/test_propeller_open_water.py, tools/audit/ship_api_evidence_census.py |
| `POST /ship/icing/heat_balance` | `heat_balance` | tests/physics/test_marine_icing.py, tools/m796_marine_icing_validation.py, tools/m796_report_gen.py |
| `POST /ship/icing/spray` | `spray` | tests/physics/test_marine_icing.py, tools/m796_marine_icing_validation.py, tools/m796_report_gen.py |
| `GET /ship/timing_baseline` | `timing_baseline` | backend/tests/test_ship_hydrostatics.py |
